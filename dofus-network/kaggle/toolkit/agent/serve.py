"""HTTP service for the agent layer (stdlib only, default 127.0.0.1:8895).

Endpoints:

    GET  /agent/health          service + recorder stats
    GET  /agent/tools           full tool schema (the text-to-action contract)
    GET  /agent/prompt          full and compact system prompts (SFT/serving)
    GET  /agent/stats           recorder counters
    GET  /agent/trajectory      last N records (?limit=50)
    POST /agent/execute         {"tool": ..., "args": {...}, "instance", "goal",
                                 "source", "observation"?}
    POST /agent/goal            {"text"|"goal": ..., "instance", "observation"?}
    POST /agent/observe         {"observation": {...}, "instance", "goal"}

Observations are a stream, not a per-request field. ``/agent/observe`` pushes a
game-state snapshot and makes it current; every later ``/agent/execute`` and
``/agent/goal`` records that snapshot alongside the action, tagged with its age
and with ``observation_source`` = ``"carried"``. Passing ``observation`` in the
request body instead stamps it as ``"call"`` and age 0. Either way the step
record carries the state the decision was made against, which is what the
observation-conditioned SFT pairs need -- before this, all 2,759 recorded steps
had ``observation: null``.

The C# bridge (added next) will long-poll a directive queue here; until then
the service is useful on its own: it executes knowledge and navigation calls,
records goals, and exposes the schema for tooling.

Usage::

    python -m agent.serve --port 8895 --recorder-dir dofus-network/trajectories
"""

from __future__ import annotations

import argparse
import hmac
import logging
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlsplit, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.brain_daemon import BrainDaemon
from agent.bridge import local_secret
from agent.executor import ToolExecutor
from agent.jsonio import dumps_bytes, loads
from agent.recorder import TrajectoryRecorder
from agent.runtime import AgentRuntime
from agent.schema import TOOL_SCHEMA
from agent.tool_dataset import SYSTEM_PROMPT, SYSTEM_PROMPT_COMPACT

logger = logging.getLogger("dofus.agent.serve")

# Distinct from a crash: the host reads it and attaches to the running daemon.
EXIT_PORT_TAKEN = 3

# -- loopback control-plane guard -------------------------------------------
#
# A web page can POST to 127.0.0.1 without a CORS preflight by sending
# `Content-Type: text/plain`; the browser delivers the request and only hides
# the response. The page cannot read the per-user secret file, so a bearer
# token plus an exact Origin/Host check removes that whole class of drive-by
# requests. There is no login: every legitimate caller runs as this user and
# reads the same file (agent/bridge.py:local_headers()).

_ALLOWED_ORIGIN_HOSTS = {"dofus.local", "dofus.localhost", "127.0.0.1", "localhost", "::1"}


def _origin_allowed(origin: str) -> bool:
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return (parsed.hostname or "").lower() in _ALLOWED_ORIGIN_HOSTS


def _host_allowed(host: str, port: int) -> bool:
    """Reject DNS rebinding: a browser resolving evil.example to 127.0.0.1
    still sends `Host: evil.example`, which is not one of these."""
    if not host:
        return True
    host = host.strip().lower()
    return host in {"127.0.0.1:%d" % port, "localhost:%d" % port, "[::1]:%d" % port}


def _bearer_matches(supplied: str, secret: str) -> bool:
    if not secret:
        return False
    return hmac.compare_digest(supplied or "", "Bearer " + secret)


def service_present(host: str, port: int) -> bool:
    """Whether something already answers on this address.

    HTTPServer sets SO_REUSEADDR, which on Windows is close to SO_REUSEPORT:
    a second daemon binds an occupied port without an error and from then on
    the two answer an arbitrary share of the requests. Measured 2026-10-29:
    pids 17476 and 9996 both held :8895, each with its own instance table, so
    a stop or a status read could land on the daemon that knew nothing.
    """
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    try:
        with socket.create_connection((probe_host, port), timeout=0.6):
            return True
    except OSError:
        return False


class AgentHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    # TCPServer defaults the accept backlog to 5. 128 is the common Linux
    # default and costs nothing idle; a stdlib-depth queue is simply too short
    # for a burst of short connections. Note this did *not* change the ~4.8% of
    # requests that fail under the stress harness's sustained mixed load (the
    # ratio was the same before and after), so that failure is elsewhere --
    # recorded in the 2026-11-07 stress report as unconfirmed.
    request_queue_size = 128

    # Hard cap on handler threads. `ThreadingMixIn` spawns one thread per
    # accepted connection and the handler timeout only bounds how long each
    # *lives*: a client opening thousands of sockets at once still reserved
    # thousands of thread stacks before any of them timed out. With the cap,
    # excess connections wait in the accept backlog (128) and beyond that the
    # kernel drops the SYN, which is backpressure instead of an address-space
    # exhaustion.
    #
    # 512, not 64: taking a slot blocks the accept loop, so the cap is also the
    # number of connections the plane will answer while they are all stalled.
    # At 64 a 160-socket half-open flood starved `/agent/health` for the whole
    # handler timeout -- the stress harness caught it as "control plane stopped
    # answering" (measured 2026-11-07, health timed out after 8 s). 512 is far
    # above any real fan-out and above the flood size that test uses, so health
    # keeps answering, while a 10k-socket flood is still bounded at 512 threads.
    max_concurrent_requests = 512

    def __init__(
        self,
        address: tuple[str, int],
        runtime: AgentRuntime,
        recorder: TrajectoryRecorder,
        brain: Optional[BrainDaemon] = None,
    ) -> None:
        super().__init__(address, AgentRequestHandler)
        self.runtime = runtime
        self.recorder = recorder
        self.executor: ToolExecutor = runtime.executor
        self.brain = brain
        self._slots = threading.BoundedSemaphore(self.max_concurrent_requests)

    # -- concurrency ceiling -------------------------------------------------

    def process_request(self, request, client_address) -> None:
        """Take a slot before spawning the handler thread.

        Runs on the accept thread, so blocking here stops new connections from
        being accepted rather than refusing them: they queue in the backlog and
        are served as slots free. The slot is released in
        ``process_request_thread``'s ``finally``; if the thread cannot even be
        started it is released here.
        """
        self._slots.acquire()
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address) -> None:
        """A client that hangs up is not a server error.

        ``socketserver`` prints a full traceback for every exception raised
        while serving, including ``ConnectionResetError`` /
        ``ConnectionAbortedError`` when the peer simply went away. The 2026-11-07
        stress run logged 159 of them for force-closed test sockets, which buries
        the tracebacks that do matter. Those are demoted to debug; anything else
        still goes through the default handler.
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            logger.debug("client %s disconnected: %s: %s",
                         client_address, type(exc).__name__, exc)
            return
        super().handle_error(request, client_address)


class AgentRequestHandler(BaseHTTPRequestHandler):
    server: AgentHTTPServer
    protocol_version = "HTTP/1.1"

    # Without a per-connection timeout a client that sends a Content-Length
    # header and then stalls pins its handler thread forever: measured 2026-11-07,
    # 160 half-open bodies grew the process from 6 to 166 threads and the test
    # had to force-close every socket. StreamRequestHandler applies this to the
    # accepted socket, and BaseHTTPRequestHandler catches TimeoutError and
    # closes the connection, so a stalled body costs one thread for 15 s.
    timeout = 15

    # -- plumbing ------------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - base signature
        logger.debug("%s - %s", self.address_string(), format % args)

    def _cors_origin(self) -> Optional[str]:
        origin = (self.headers.get("Origin") or "").strip()
        return origin if origin and _origin_allowed(origin) else None

    def _authorized(self, require_token: bool = True) -> bool:
        if not _host_allowed(self.headers.get("Host", ""), self.server.server_port):
            return False
        origin = (self.headers.get("Origin") or "").strip()
        if origin and not _origin_allowed(origin):
            return False
        if require_token:
            return _bearer_matches(self.headers.get("Authorization", ""), local_secret())
        return True

    def _send_json(self, status: int, payload: Any) -> None:
        body = dumps_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _send_fail(self, status: int, payload: Any) -> None:
        """Send an error and close the connection.

        HTTP/1.1 keeps the connection alive, so when a body was rejected
        *before it was fully read* the unread bytes stay in the socket buffer
        and the next ``handle_one_request`` parses them as a fresh request.
        Measured 2026-11-07: an oversized POST followed by ``GET /agent/health``
        produced a second, attacker-influenced ``501`` on the same connection.
        Closing after an error removes the desync.
        """
        self.close_connection = True
        self._send_json(status, payload)

    def _read_json(self) -> Optional[Dict[str, Any]]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > 8 * 1024 * 1024:
            return None
        try:
            raw = self.rfile.read(length)
        except TimeoutError:
            # A body that stops arriving must not become a traceback: the
            # handler timeout (15 s) fires here and the caller gets a 400.
            return None
        try:
            payload = loads(raw)
        except (ValueError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _query(self) -> Dict[str, str]:
        parsed = urlparse(self.path)
        return {key: values[0] for key, values in parse_qs(parsed.query).items()}

    # -- routes --------------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802 - base signature
        # A preflight never carries the Authorization header, so it is answered
        # without one -- but only for an allowed Origin/Host pair.
        if not self._authorized(require_token=False):
            self._send_fail(403, {"error": "forbidden"})
            return
        origin = self._cors_origin()
        self.send_response(204)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - base signature
        if not self._authorized():
            self._send_fail(401, {"error": "unauthorized"})
            return
        path = urlparse(self.path).path
        if path == "/agent/health":
            self._send_json(200, {
                "status": "ok",
                "service": "dofus-agent",
                "tools": len(TOOL_SCHEMA["tools"]),
                "languages": TOOL_SCHEMA["languages"],
                "policy": type(self.server.runtime.policy).__name__,
                # Observations pushed since start, and how stale the current one
                # is. Zero observations with non-zero steps means the bridge is
                # not streaming state and every recorded pair is
                # observation-blind.
                "observations_seen": self.server.runtime.observations_seen,
                "observation_age_ms": self.server.runtime.current_observation()[1],
                "recorder": self.server.recorder.stats(),
            })
        elif path == "/agent/tools":
            self._send_json(200, TOOL_SCHEMA)
        elif path == "/agent/prompt":
            self._send_json(200, {
                "system_prompt": SYSTEM_PROMPT,
                "chars": len(SYSTEM_PROMPT),
                "student_system_prompt": SYSTEM_PROMPT_COMPACT,
                "student_chars": len(SYSTEM_PROMPT_COMPACT),
                "completion_format": '{"tool": "<name>", "args": {...}}',
            })
        elif path == "/agent/stats":
            self._send_json(200, self.server.recorder.stats())
        elif path == "/agent/trajectory":
            try:
                limit = int(self._query().get("limit", "50"))
            except ValueError:
                limit = 50
            self._send_json(200, {"records": self.server.recorder.tail(limit)})
        elif path == "/brain/health":
            if self.server.brain is None:
                self._send_json(503, {"status": "error", "error": "brain mode is off"})
            else:
                self._send_json(200, self.server.brain.health())
        elif path == "/net/status":
            # The network pipeline's own table: who owns each port, whether it is this run's, and
            # whether the relay can still serve a credentialed browser request. Read lazily and
            # import-guarded: the agent service must still answer if the network module is broken.
            try:
                from net.orchestrator import report_now
                self._send_json(200, report_now(refresh=bool(self._query().get("refresh"))))
            except Exception:  # noqa: BLE001 - a missing probe is reported, never hidden
                logger.exception("network status probe failed")
                self._send_json(200, {"error": "probe unavailable",
                                      "components": [], "problems": ["probe-unavailable"]})
        elif path == "/brain/status":
            if self.server.brain is None:
                self._send_json(503, {"status": "error", "error": "brain mode is off"})
            else:
                self._send_json(200, self.server.brain.status())
        else:
            self._send_json(404, {"error": f"unknown GET path {path}"})

    def do_POST(self) -> None:  # noqa: N802 - base signature
        if not self._authorized():
            self._send_fail(401, {"error": "unauthorized"})
            return
        # A JSON content type forces a CORS preflight in every browser. The
        # token is the primary control; this is the second lock, so a page
        # cannot reach the handler even if it somehow learns the token.
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._send_fail(415, {"error": "Content-Type must be application/json"})
            return
        path = urlparse(self.path).path
        payload = self._read_json()
        if payload is None:
            # The body was never fully consumed, so the connection cannot be
            # reused: the unread bytes would be parsed as the next request.
            self._send_fail(400, {"error": "request body must be a JSON object"})
            return

        try:
            self._dispatch_post(path, payload)
        except Exception:  # noqa: BLE001 - a handler bug is a 500, not a dropped socket
            logger.exception("unhandled error serving POST %s", path)
            self._send_fail(500, {"error": "internal error"})

    def _dispatch_post(self, path: str, payload: Dict[str, Any]) -> None:
        if path == "/agent/execute":
            tool = str(payload.get("tool", ""))
            args = payload.get("args") or {}
            if not tool:
                self._send_fail(400, {"error": "missing 'tool'"})
                return
            # ``observation`` is optional on purpose: the runtime keeps the
            # freshest snapshot pushed to /agent/observe and stamps it (with its
            # age and provenance) onto the step record, so a bridge that streams
            # state does not have to repeat it in every action body.
            result = self.server.runtime.execute_tool(
                tool,
                args,
                instance=payload.get("instance"),
                goal=payload.get("goal"),
                source=str(payload.get("source", "live")),
                observation=payload.get("observation"),
            )
            self._send_json(200, result.as_dict())
        elif path == "/agent/goal":
            text = str(payload.get("text") or payload.get("goal") or "").strip()
            if not text:
                self._send_json(400, {"error": "missing 'text' or 'goal'"})
                return
            result = self.server.runtime.handle_goal(
                text,
                instance=payload.get("instance"),
                observation=payload.get("observation"),
            )
            self._send_json(200, result.as_dict())
        elif path == "/agent/observe":
            observation = payload.get("observation")
            if not isinstance(observation, dict):
                self._send_json(400, {"error": "missing 'observation' object"})
                return
            # Routed through the runtime, not the recorder: the snapshot has to
            # become the runtime's *current* observation so later /agent/execute
            # and /agent/goal calls can be stamped with it.
            record = self.server.runtime.observe(
                observation,
                goal=payload.get("goal"),
                source=str(payload.get("source", "live")),
            )
            self._send_json(200, {"ok": True, "record": record})
        elif path == "/brain/start":
            if self.server.brain is None:
                self._send_json(503, {"ok": False, "error": "brain mode is off"})
                return
            self._send_json(200, self.server.brain.start(payload))
        elif path == "/brain/stop":
            if self.server.brain is None:
                self._send_json(503, {"ok": False, "error": "brain mode is off"})
                return
            instance = payload.get("instance")
            self._send_json(200, self.server.brain.stop(
                instance=str(instance) if instance else None,
                stop_all=bool(payload.get("all")),
                why=str(payload.get("why") or "stopped by the host"),
            ))
        else:
            self._send_json(404, {"error": f"unknown POST path {path}"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent.serve", description="Dofus agent HTTP service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8895)
    parser.add_argument("--instance", default="default")
    parser.add_argument("--recorder-dir", type=Path,
                        default=REPO_ROOT / "trajectories")
    parser.add_argument("--sentinel-url", default="http://127.0.0.1:8890")
    parser.add_argument("--directive-url", default=None,
                        help="default: the port the app published to bridge_port.txt")
    parser.add_argument("--schedule", action="store_true",
                        help="wire ScheduleManager.active_objective as policy context (§C)")
    parser.add_argument("--brain", action="store_true",
                        help="serve the multi-instance brain control plane "
                             "(POST /brain/start, /brain/stop; GET /brain/status)")
    # The default has to be a policy that can serve the default goal. "rule" cannot: RulePolicy
    # has no quest planner, so `/brain/start` with no explicit policy answers "do all the quests"
    # with None and no reason -- measured 2026-11-04, where the same live observation gave
    # `choose -> None, last_no_call: ''` under rule and a real (if unhappy) verdict under planner.
    # `_base_policy` already learned this lesson for the CLI path on 2026-10-29; the daemon
    # default quietly kept the broken value.
    parser.add_argument("--brain-policy", choices=["rule", "planner", "encoder"],
                        default="planner", help="default policy for /brain/start")
    parser.add_argument("--brain-log-dir", type=Path, default=None,
                        help="per-instance autopilot logs (default: LOCALAPPDATA/DofusFarm)")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = build_parser().parse_args(argv)

    if service_present(args.host, args.port):
        logger.error(
            "another dofus agent service already answers on %s:%s; "
            "refusing to start a second control plane on the same port",
            args.host, args.port)
        return EXIT_PORT_TAKEN

    recorder = TrajectoryRecorder(root=args.recorder_dir, instance=args.instance)
    executor = ToolExecutor(
        sentinel_url=args.sentinel_url,
        directive_url=args.directive_url,
        recorder=recorder,
    )
    runtime = AgentRuntime(
        executor=executor, recorder=recorder, instance=args.instance)
    if args.schedule:
        try:
            from planning.schedule import build_schedule_stack

            # One constructor order, in `planning.schedule`, because this block had it wrong twice
            # over (Economy() without a cost model, Planner() without kb/cost/economy) and the
            # broad except below turned both TypeErrors into "running without objective context".
            sm = build_schedule_stack()
            sm.restore()
            runtime.objective_provider = sm.active_objective
            store = sm.economy.experience
            logger.info("§C objective context wired from ScheduleManager "
                        "(experience store: %d params, %d events seen)",
                        len(store), store.events_seen)
        except Exception as exc:  # noqa: BLE001
            logger.warning("schedule wiring failed: %s; running without objective context", exc)

    server = AgentHTTPServer((args.host, args.port), runtime, recorder,
                             brain=BrainDaemon(
                                 log_dir=args.brain_log_dir,
                                 recorder_dir=args.recorder_dir,
                                 default_policy=args.brain_policy,
                             ) if args.brain else None)
    logger.info(f"Dofus agent service listening on http://{args.host}:{args.port} "
                f"({len(TOOL_SCHEMA['tools'])} tools, policy {type(runtime.policy).__name__}, "
                f"trajectory {recorder.path}"
                + (", brain daemon on" if args.brain else "") + ")")
    if args.brain:
        # The network watchdog rides the brain daemon because that process is already the long-lived
        # one the farm depends on, and it is python -- so this lands without rebuilding the host.
        # Failures here are logged and ignored: a network check must never stop the agent serving.
        try:
            from net.orchestrator import start_watchdog
            start_watchdog(interval_s=float(os.environ.get("DOFUS_NET_INTERVAL", "120")),
                           log=logger.info)
        except Exception as exc:  # noqa: BLE001
            logger.warning("net watchdog not started: %s: %s", type(exc).__name__, exc)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping agent service.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
