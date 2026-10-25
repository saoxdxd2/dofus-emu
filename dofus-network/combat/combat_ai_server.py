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


def _rotate_log() -> None:
    """Keep an unattended server from filling the disk with one JSONL file."""
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size >= LOG_MAX_BYTES:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            rotated = LOG_PATH.with_name(
                f"{LOG_PATH.stem}.{stamp}{LOG_PATH.suffix}")
            LOG_PATH.rename(rotated)
    except OSError:
        pass


def _log_decision(payload: Dict[str, Any], decision: Dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _rotate_log()
        record = {
            "time": time.time(),
            "fight_id": payload.get("fight_id"),
            "round": payload.get("round"),
            "my_id": payload.get("my_id"),
            "decision": decision,
        }
        with LOG_PATH.open("ab") as handle:
            handle.write(dumps_bytes(record) + b"\n")
    except OSError:
        pass


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
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length)
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
        LOG_PATH = Path(log_path)
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
