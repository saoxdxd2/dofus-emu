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
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for _path in (REPO_ROOT, REPO_ROOT / "dofus-network"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from combat.live_policy import DEFAULT_CHECKPOINT, LivePolicy
from agent.jsonio import dumps_bytes, loads

POLICY: Optional[LivePolicy] = None
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

    def _reply(self, data: Dict[str, Any], status: int = 200) -> None:
        body = dumps_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            checkpoint = str(POLICY.checkpoint) if POLICY is not None else None
            cfg = POLICY.cfg.__dict__ if POLICY is not None else None
            self._reply({
                "status": "ok" if POLICY is not None else "loading",
                "model": "encounter_ppo_pbt_actor",
                "checkpoint": checkpoint,
                "config": cfg,
                "backend": "jax",
            })
            return
        self._reply({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/v1/combat/decide":
            self._reply({"ok": False, "error": "not found"}, 404)
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
        except Exception as exc:  # noqa: BLE001 - the page falls back on error
            self._reply({"ok": False, "action": None,
                         "reason": f"bad_request:{type(exc).__name__}: {exc}"}, 400)

    def log_message(self, format, *args):
        pass


def run_server(port: int = 8891,
               checkpoint: Path = DEFAULT_CHECKPOINT,
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
    print(f"checkpoint: {checkpoint}")
    POLICY = LivePolicy(checkpoint=Path(checkpoint), beam_width=beam_width,
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
        server = ThreadingHTTPServer(("127.0.0.1", int(port)), CombatRequestHandler)
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
