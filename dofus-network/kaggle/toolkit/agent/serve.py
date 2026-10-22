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
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.executor import ToolExecutor
from agent.recorder import TrajectoryRecorder
from agent.runtime import AgentRuntime, RulePolicy
from agent.schema import TOOL_SCHEMA
from agent.tool_dataset import SYSTEM_PROMPT, SYSTEM_PROMPT_COMPACT

logger = logging.getLogger("dofus.agent.serve")


class AgentHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        runtime: AgentRuntime,
        recorder: TrajectoryRecorder,
    ) -> None:
        super().__init__(address, AgentRequestHandler)
        self.runtime = runtime
        self.recorder = recorder
        self.executor: ToolExecutor = runtime.executor


class AgentRequestHandler(BaseHTTPRequestHandler):
    server: AgentHTTPServer
    protocol_version = "HTTP/1.1"

    # -- plumbing ------------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - base signature
        logger.debug("%s - %s", self.address_string(), format % args)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Optional[Dict[str, Any]]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > 8 * 1024 * 1024:
            return None
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _query(self) -> Dict[str, str]:
        parsed = urlparse(self.path)
        return {key: values[0] for key, values in parse_qs(parsed.query).items()}

    # -- routes --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - base signature
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
        else:
            self._send_json(404, {"error": f"unknown GET path {path}"})

    def do_POST(self) -> None:  # noqa: N802 - base signature
        path = urlparse(self.path).path
        payload = self._read_json()
        if payload is None:
            self._send_json(400, {"error": "request body must be a JSON object"})
            return

        if path == "/agent/execute":
            tool = str(payload.get("tool", ""))
            args = payload.get("args") or {}
            if not tool:
                self._send_json(400, {"error": "missing 'tool'"})
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
    parser.add_argument("--directive-url", default="http://127.0.0.1:8893/directive")
    parser.add_argument("--policy", choices=["rule", "thx"], default="rule",
                        help="decision policy: rule baseline or THX-01 (loads lazily)")
    parser.add_argument("--thx-model", default=None,
                        help="THX-01 path or HF id (default: local cache or doofz/THX-01)")
    parser.add_argument("--thx-device", default="cpu")
    parser.add_argument("--thx-fallback", action="store_true",
                        help="fall back to RulePolicy when THX-01 is invalid or unsure")
    parser.add_argument("--schedule", action="store_true",
                        help="wire ScheduleManager.active_objective as policy context (§C)")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = build_parser().parse_args(argv)

    recorder = TrajectoryRecorder(root=args.recorder_dir, instance=args.instance)
    executor = ToolExecutor(
        sentinel_url=args.sentinel_url,
        directive_url=args.directive_url,
        recorder=recorder,
    )
    policy = None
    if args.policy == "thx":
        from ai.thx_policy import ThxPolicy

        policy = ThxPolicy(
            model=args.thx_model,
            device=args.thx_device,
            fallback=RulePolicy() if args.thx_fallback else None,
        )
    runtime = AgentRuntime(
        executor=executor, recorder=recorder, policy=policy, instance=args.instance)
    if args.schedule:
        try:
            from planning.schedule import ScheduleManager
            from planning.planner import Planner
            from planning.economy import Economy

            sm = ScheduleManager(Planner(), Economy())
            sm.restore()
            runtime.objective_provider = sm.active_objective
            logger.info("§C objective context wired from ScheduleManager")
        except Exception as exc:  # noqa: BLE001
            logger.warning("schedule wiring failed: %s; running without objective context", exc)

    server = AgentHTTPServer((args.host, args.port), runtime, recorder)
    logger.info(f"Dofus agent service listening on http://{args.host}:{args.port} "
                f"({len(TOOL_SCHEMA['tools'])} tools, policy {type(runtime.policy).__name__}, "
                f"trajectory {recorder.path})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping agent service.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
