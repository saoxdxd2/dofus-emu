"""Train while the machine is otherwise idle — the "free" half of the loop.

The loop produces data at ~100 steps/hour and cannot afford to train during play:
a bad update mid-run is unrecoverable and, on this account, expensive. So the
split is:

    playing  -> collect rows + memory (no gradients)
    idle     -> train on what was just collected, into a *candidate* artifact

"Idle" is a fact, not a guess: `agent/autopilot.py` writes `autopilot.lock` while
it runs and clears it in a `finally`. If the lock exists, this exits immediately.

Nothing is ever swapped in automatically. The candidate is written next to the
live artifact with its metrics; adopting it is a deliberate act, because a model
that silently replaces the one you tested is how you lose a week.

    python ai/idle_trainer.py --once --max-seconds 90
    python ai/idle_trainer.py --watch --idle-minutes 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.bridge import base_url as bridge_base_url, local_opener  # noqa: E402

STATE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm"
LOCK = STATE_DIR / "autopilot.lock"
LOG = STATE_DIR / "idle_trainer.log"
STEPS = STATE_DIR / "autopilot.jsonl"


def log(msg: str) -> None:
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def busy_reason() -> Optional[str]:
    """Why training must not start now, or None when it is safe."""
    if LOCK.exists():
        try:
            age = time.time() - LOCK.stat().st_mtime
        except OSError:
            age = 0
        return "a loop holds the lock (%ds old)" % age
    # A live bridge means the client is open and someone is playing.
    try:
        import urllib.request
        local_opener().open(bridge_base_url(), timeout=0.8)
        return "the client bridge is answering"
    except Exception:  # noqa: BLE001
        return None


def train_once(max_seconds: float = 90.0, epochs: int = 60) -> Dict[str, Any]:
    """Build a corpus from what was collected and train a candidate head."""
    started = time.time()
    out_dir = REPO_ROOT / "data" / "idle_sft"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. corpus: live rows if there are enough, else the deterministic teacher.
    cmd = [sys.executable, str(REPO_ROOT / "ai" / "build_verifier_sft.py"),
           "--out", str(out_dir)]
    if STEPS.exists():
        cmd += ["--input", str(STEPS)]
    else:
        cmd += ["--selfcheck"]
    import subprocess
    build = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=max(20, max_seconds / 2))
    if build.returncode != 0:
        return {"ok": False, "stage": "corpus", "detail": build.stderr[-300:]}

    train = out_dir / "train.jsonl"
    val = out_dir / "val.jsonl"
    if not train.exists() or not val.exists():
        return {"ok": False, "stage": "corpus", "detail": "no files produced"}

    # 2. candidate, never the live artifact.
    candidate = REPO_ROOT / "models" / "verifier_head.candidate.onnx"
    remaining = max(10, int(max_seconds - (time.time() - started)))
    train_cmd = [sys.executable, str(REPO_ROOT / "ai" / "train_verifier_head.py"),
                 "--train", str(train), "--val", str(val),
                 "--out", str(candidate), "--epochs", str(epochs),
                 "--no-int8"]
    run = subprocess.run(train_cmd, capture_output=True, text=True, timeout=remaining)
    if run.returncode != 0:
        return {"ok": False, "stage": "train", "detail": run.stderr[-300:]}

    metrics: Dict[str, Any] = {"ok": True, "candidate": str(candidate),
                               "seconds": round(time.time() - started, 1)}
    sidecar = candidate.with_suffix(".json")
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            val_metrics = (data.get("metrics") or {}).get("val") or {}
            metrics["val_accuracy"] = val_metrics.get("accuracy")
        except (OSError, ValueError):
            pass
    return metrics


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--idle-minutes", type=float, default=10.0)
    ap.add_argument("--max-seconds", type=float, default=90.0)
    ap.add_argument("--epochs", type=int, default=60)
    args = ap.parse_args(argv)

    if args.once:
        why = busy_reason()
        if why:
            log("skipped: " + why)
            return 0
        result = train_once(args.max_seconds, args.epochs)
        log(json.dumps(result))
        return 0 if result.get("ok") else 1

    if not args.watch:
        ap.print_help()
        return 2

    log("watching: train after %.0f idle minutes" % args.idle_minutes)
    idle_since = time.time()
    while True:
        time.sleep(30)
        if busy_reason():
            idle_since = time.time()
            continue
        if (time.time() - idle_since) / 60.0 < args.idle_minutes:
            continue
        result = train_once(args.max_seconds, args.epochs)
        log(json.dumps(result))
        idle_since = time.time()


if __name__ == "__main__":
    raise SystemExit(main())
