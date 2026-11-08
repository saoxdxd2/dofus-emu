"""Live combat policy API for the in-game FarmBot.

The page asks this server for one action per turn:

    POST /v1/combat/decide   body = the raw ``fight.state`` payload

The server binds that board to the encounter policy's slots (the live spells,
the live enemies), runs the trained PPO/PBT actor, and maps the chosen
spell/target/move intent onto the cell solver's legal plan. Nothing is
hardcoded per class or level: every request rebuilds the binding.

The previous 8892 server was a ModernBERT stance classifier with hardcoded Cra
spells. It was never going to work for a different instance, and the page no
longer called it. This file replaces that path; the model is the checkpoint
from ``encounter_selfplay.pkl`` (override with ``--checkpoint``).
"""

from __future__ import annotations

import argparse
import atexit
import hmac
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for _path in (REPO_ROOT, REPO_ROOT / "dofus-network"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from agent.bridge import local_secret
from agent.jsonio import dumps_bytes, loads

# combat/live_policy.py used to be a JAX-only model, and the interpreter that ships inside
# setup.exe has no jax wheel (measured 2026-11-03: the development interpreter imports jax
# 0.12.0.1, runtimes/python raises ModuleNotFoundError, and PyPI has no cp311 jaxlib at all). It
# now runs on whichever array library exists -- jax when installed, numpy otherwise, through
# combat/encounter/backend.py -- so an installed copy serves the learned policy instead of the
# solver. The import stays lazy for the window that still matters: a fresh install has not run
# `pip install -r requirements.txt` yet, so even numpy can be absent, and every handler already
# treats `POLICY is None` as "solver only".
DEFAULT_CHECKPOINT = REPO_ROOT / "dofus-network" / "models" / "encounter_selfplay.pkl"


def _runtime_identity() -> Dict[str, object]:
    """Who is answering `/health` -- interpreter, script, pid, checkpoint hash.

    The run token is deliberately not stored here. `/health` is reachable without
    a token so the manager can probe liveness; it only adds the token back for a
    caller that proved it belongs to this run. A missing token means the server
    was started by hand or by an older build, and the manager treats that as
    foreign because "I cannot tell" is not evidence of ownership.
    """
    import hashlib
    sha = None
    try:
        if DEFAULT_CHECKPOINT.exists():
            sha = hashlib.sha256(DEFAULT_CHECKPOINT.read_bytes()).hexdigest()[:16]
    except OSError:
        sha = None
    return {"pid": os.getpid(),
            "interpreter": sys.executable,
            "script": str(Path(__file__).resolve()),
            "started_at": round(time.time(), 3),
            "checkpoint_sha": sha,
            "token_present": bool(local_secret())}


RUNTIME = _runtime_identity()

# -- loopback control-plane guard -------------------------------------------
# Same contract as agent/serve.py: bearer token for control, exact Origin/Host,
# JSON content type. `/health` stays readable without a token (redacted), so a
# stale/foreign process can still be detected before it is reaped.

_ALLOWED_ORIGIN_HOSTS = {"dofus.local", "dofus.localhost", "127.0.0.1", "localhost", "::1"}
_RATE_LOCK = threading.Lock()
_RATE_TIMES: list[float] = []
_RATE_MAX_PER_SECOND = 40


def _origin_allowed(origin: str) -> bool:
    if not origin:
        return True
    try:
        parsed = urllib.parse.urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return (parsed.hostname or "").lower() in _ALLOWED_ORIGIN_HOSTS


def _host_allowed(host: str, port: int) -> bool:
    if not host:
        return True
    host = host.strip().lower()
    return host in {"127.0.0.1:%d" % port, "localhost:%d" % port, "[::1]:%d" % port}


def _bearer_matches(supplied: str, secret: str) -> bool:
    if not secret:
        return False
    return hmac.compare_digest(supplied or "", "Bearer " + secret)


def _rate_ok() -> bool:
    now = time.monotonic()
    with _RATE_LOCK:
        while _RATE_TIMES and now - _RATE_TIMES[0] > 1.0:
            _RATE_TIMES.pop(0)
        if len(_RATE_TIMES) >= _RATE_MAX_PER_SECOND:
            return False
        _RATE_TIMES.append(now)
        return True


def _policy_backend() -> str:
    """Which array library the loaded policy is running on.

    Asked lazily: `combat.encounter.backend` imports numpy, so on a copy whose pip install has
    not happened yet this must degrade instead of breaking the health endpoint.
    """
    try:
        from combat.encounter.backend import BACKEND
        return BACKEND
    except ImportError:
        return "learned (backend unknown)"

POLICY: Optional[Any] = None
DECISION_LOCK = threading.Lock()
LOG_PATH = REPO_ROOT / "trajectories" / "live_policy_decisions.jsonl"
LOG_MAX_BYTES = 64 * 1024 * 1024
# A decision row is several KB. Opening, writing and closing the log per
# decision cost about a millisecond; keeping one handle and flushing each row
# costs ~0.1 ms and loses nothing if the process is killed. (Block flushing is
# ~0.008 ms but can drop the last rows on TerminateProcess.)
LOG_FLUSH_BYTES = 0
_LOG_LOCK = threading.Lock()
_LOG_HANDLE = None
_LOG_OPEN_PATH: Optional[Path] = None
_LOG_BYTES = 0
_LOG_PENDING = 0


def _close_log_locked(flush: bool = True) -> None:
    global _LOG_HANDLE, _LOG_OPEN_PATH, _LOG_PENDING
    handle, _LOG_HANDLE = _LOG_HANDLE, None
    _LOG_OPEN_PATH = None
    _LOG_PENDING = 0
    if handle is None:
        return
    try:
        if flush:
            handle.flush()
    except OSError:
        pass
    finally:
        try:
            handle.close()
        except OSError:
            pass


def _open_log_locked() -> None:
    global _LOG_HANDLE, _LOG_OPEN_PATH, _LOG_BYTES, _LOG_PENDING
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LOG_HANDLE = LOG_PATH.open("ab")
    _LOG_OPEN_PATH = LOG_PATH
    try:
        _LOG_BYTES = LOG_PATH.stat().st_size
    except OSError:
        _LOG_BYTES = 0
    _LOG_PENDING = 0


def _rotate_log_locked() -> None:
    """Keep an unattended server from filling the disk with one JSONL file."""
    global _LOG_BYTES
    _close_log_locked()
    try:
        if LOG_PATH.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            rotated = LOG_PATH.with_name(
                f"{LOG_PATH.stem}.{stamp}{LOG_PATH.suffix}")
            suffix = 0
            while rotated.exists():
                suffix += 1
                rotated = LOG_PATH.with_name(
                    f"{LOG_PATH.stem}.{stamp}-{suffix}{LOG_PATH.suffix}")
            LOG_PATH.rename(rotated)
    except OSError:
        pass
    _LOG_BYTES = 0
    _open_log_locked()


def _log_decision(payload: Dict[str, Any], decision: Dict[str, Any]) -> None:
    global _LOG_BYTES, _LOG_PENDING
    try:
        record = {
            "time": time.time(),
            "fight_id": payload.get("fight_id"),
            "round": payload.get("round"),
            "my_id": payload.get("my_id"),
            # The optional challenges the fight was started with. They are not
            # scored here -- the reward for one lives in the outcome row -- but a
            # decision row without them cannot be used later to learn a policy
            # that respects one.
            "challenges": payload.get("challenges") or [],
            "decision": decision,
        }
        line = dumps_bytes(record) + b"\n"
        with _LOG_LOCK:
            if _LOG_HANDLE is None or _LOG_OPEN_PATH != LOG_PATH:
                _close_log_locked()
                _open_log_locked()
            if _LOG_BYTES + len(line) >= LOG_MAX_BYTES:
                _rotate_log_locked()
            _LOG_HANDLE.write(line)
            _LOG_BYTES += len(line)
            _LOG_PENDING += len(line)
            if _LOG_PENDING >= LOG_FLUSH_BYTES:
                _LOG_HANDLE.flush()
                _LOG_PENDING = 0
    except OSError:
        # An unwritable log must never stop the fight API.
        with _LOG_LOCK:
            try:
                _close_log_locked(flush=False)
            except Exception:  # noqa: BLE001 - best effort close
                pass


@atexit.register
def _flush_decision_log() -> None:
    with _LOG_LOCK:
        _close_log_locked()


def decide(payload: Dict[str, Any]) -> Dict[str, Any]:
    if POLICY is None:
        return {"ok": False, "action": None, "reason": "policy_not_loaded",
                "latency_ms": 0.0}
    started = time.perf_counter()
    with DECISION_LOCK:
        decision = POLICY.decide(payload)
    decision["server_latency_ms"] = round(
        (time.perf_counter() - started) * 1000.0, 1)
    _log_decision(payload, decision)
    return decision


class CombatRequestHandler(BaseHTTPRequestHandler):
    # The page sends one request per turn; keep-alive avoids a new loopback
    # connection for every turn.
    protocol_version = "HTTP/1.1"

    def _cors_origin(self) -> str:
        origin = (self.headers.get("Origin") or "").strip()
        return origin if origin and _origin_allowed(origin) else ""

    def _authorized(self, require_token: bool = True) -> bool:
        if not _host_allowed(self.headers.get("Host", ""), self.server.server_port):
            return False
        origin = (self.headers.get("Origin") or "").strip()
        if origin and not _origin_allowed(origin):
            return False
        if require_token:
            return _bearer_matches(self.headers.get("Authorization", ""), local_secret())
        return True

    def _reply(self, data: Dict[str, Any], status: int = 200) -> None:
        body = dumps_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self._authorized(require_token=False):
            self._reply({"ok": False, "error": "forbidden"}, 403)
            return
        self.send_response(204)
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            checkpoint = str(POLICY.checkpoint) if POLICY is not None else None
            cfg = POLICY.cfg.__dict__ if POLICY is not None else None
            identity = dict(RUNTIME)
            if self._authorized(require_token=True):
                identity["token"] = local_secret()
            self._reply({
                "status": "ok" if POLICY is not None else "loading",
                "model": "encounter_ppo_pbt_actor",
                "checkpoint": checkpoint,
                "config": cfg,
                # Not a label: a health endpoint has to name the array library the process is
                # actually computing with, and the fallback is a real backend now, not a lack of
                # one. Only when no policy loaded at all is the answer "solver-only".
                "backend": _policy_backend() if POLICY is not None
                           else "solver-only (no array backend installed)",
                # Who is answering. A socket that replies only proves *a* server exists; on
                # 2026-11-04 the server answering :8891 was started 50 minutes and three app
                # restarts earlier, on the dev interpreter instead of the shipped one. The run
                # token, pid, interpreter and checkpoint hash are what let the manager tell its
                # own brain from a leftover one -- and the leftover is the one that must die.
                # The token is only included for a caller that already proved it belongs here.
                "identity": identity,
            })
            return
        self._reply({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/v1/combat/decide":
            self._reply({"ok": False, "error": "not found"}, 404)
            return
        if not self._authorized():
            self._reply({"ok": False, "action": None,
                         "reason": "unauthorized"}, 401)
            return
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._reply({"ok": False, "action": None,
                         "reason": "unsupported_media_type"}, 415)
            return
        if not _rate_ok():
            self._reply({"ok": False, "action": None,
                         "reason": "rate_limited"}, 429)
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            self._reply({"ok": False, "action": None,
                         "reason": "bad_request:invalid_content_length"}, 400)
            return
        if length > 8 * 1024 * 1024:
            self._reply({"ok": False, "action": None,
                         "reason": "bad_request:body_too_large"}, 413)
            return
        raw = self.rfile.read(max(0, length))
        try:
            payload = loads(raw) if raw else {}
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            self._reply(decide(payload))
        except Exception:  # noqa: BLE001 - the page falls back on error
            self._reply({"ok": False, "action": None,
                         "reason": "bad_request"}, 400)

    def log_message(self, format, *args):
        pass


class ExclusiveHTTPServer(ThreadingHTTPServer):
    """Binds WITHOUT SO_REUSEADDR, so a shared port fails instead of silently splitting.

    `HTTPServer.allow_reuse_address` is True by default, and on Windows SO_REUSEADDR means a
    second listener may bind the same port. That is exactly how a leftover brain from an earlier
    app run went on advising fights while the current run believed its own server was answering
    (:8895 hit this before -- see `agent/serve.py:65` and `aipanel.cs:1501`). Two servers with two
    instance tables is not high availability: it is undefined behaviour with a healthy /health.
    """

    allow_reuse_address = False
    daemon_threads = True


DEFAULT_BRAIN_PORT = int(os.environ.get("DOFUS_BRAIN_PORT", "8891"))
DEFAULT_BRAIN_HOST = os.environ.get("DOFUS_BRAIN_HOST", "127.0.0.1")


def run_server(port: int = DEFAULT_BRAIN_PORT,
               host: str = DEFAULT_BRAIN_HOST,
               checkpoint: Optional[Path] = None,
               beam_width: int = 8,
               max_actions: int = 4,
               log_path: Optional[Path] = None) -> None:
    global POLICY, LOG_PATH
    if log_path is not None:
        new_path = Path(log_path)
        if new_path != LOG_PATH:
            with _LOG_LOCK:
                _close_log_locked()
            LOG_PATH = new_path
    print("==========================================================")
    print("   DOFUS LIVE COMBAT POLICY API (PPO/PBT ACTOR)          ")
    print("==========================================================")
    checkpoint = Path(checkpoint or DEFAULT_CHECKPOINT)
    print(f"checkpoint: {checkpoint}")
    try:
        from combat.live_policy import LivePolicy
    except ImportError as exc:      # the refusal the handlers already report as "loading"
        POLICY = None
        print(f"[WARN] learned policy unavailable: {type(exc).__name__}: {exc}")
        print("       serving the solver-only path; /health reports status=loading")
    else:
        POLICY = LivePolicy(checkpoint=checkpoint, beam_width=beam_width,
                            max_actions=max_actions)
        print(f"slots: {POLICY.cfg.spells} spells | {POLICY.cfg.enemies} enemies")
        print(f"decision log: {LOG_PATH}")
        print("warming up the policy and solver...")
        try:
            POLICY.warmup()
            print("warm-up done")
        except Exception as exc:  # noqa: BLE001 - a warm-up must not block the API
            print(f"warm-up failed: {type(exc).__name__}: {exc}")
    try:
        server = ThreadingHTTPServer((host, int(port)), CombatRequestHandler)
    except OSError as exc:
        print(f"[ERROR] cannot bind 127.0.0.1:{port}: {exc}")
        print(f"Stop the process already using 127.0.0.1:{port}, or start this "
              "one on another port and set window.__agentFightBrain to that URL.")
        raise SystemExit(1)
    server.daemon_threads = True
    print(f"[OK] listening on http://127.0.0.1:{port}")
    print("  GET  /health")
    print("  POST /v1/combat/decide")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.shutdown()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", nargs="?", type=int, default=8891)
    parser.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    parser.add_argument("--beam-width", type=int, default=8)
    parser.add_argument("--max-actions", type=int, default=4)
    parser.add_argument("--log", default=str(
        REPO_ROOT / "trajectories" / "live_policy_decisions.jsonl"))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    run_server(port=args.port, checkpoint=Path(args.checkpoint),
               beam_width=args.beam_width, max_actions=args.max_actions,
               log_path=Path(args.log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
