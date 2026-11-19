"""Watch the models directory for new builds and auto-run the live test harness.

Usage:

    python tools/watch_build_and_test.py --baseline models/tool_policy.onnx

Polls `models/` and `models/candidates/` for new or modified `.onnx` files.
When a new ONNX appears, runs:

    python tools/live_test_after_build.py --model <new_onnx> \
        --baseline models/tool_policy.onnx --offline-only

and writes the JSON report to `data/live_test_results/`.

Press Ctrl+C to stop watching.

Runs offline-only (no Dofus client needed). Intended to run in a terminal
while you iterate on model weights in another terminal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

RESULTS_DIR = REPO_ROOT / "data" / "live_test_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

POLL_S = 2.0
DEBOUNCE_S = 10.0


def _sha256(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


class BuildWatcher:
    """Poll for new/changed ONNX model files and auto-test them."""

    def __init__(self, baseline: Path, watch_dirs: list[Path]) -> None:
        self.baseline = baseline
        self.watch_dirs = watch_dirs
        self._known: Dict[Path, float] = {}  # path -> mtime

    def _discover(self) -> list[Path]:
        found: list[Path] = []
        for d in self.watch_dirs:
            if not d.is_dir():
                continue
            for p in d.rglob("*.onnx"):
                if p.is_file():
                    found.append(p)
        return found

    def _should_trigger(self, path: Path, mtime: float) -> bool:
        known = self._known.get(path)
        if known is None:
            self._known[path] = mtime
            return False  # first sighting: just record, don't trigger
        if abs(mtime - known) < 0.1:
            return False
        self._known[path] = mtime
        return True

    def _run_test(self, model: Path) -> None:
        print(f"\n{'='*70}")
        print(f"[watch] New build detected: {model}")
        print(f"[watch] baseline: {self.baseline}")
        print(f"{'='*70}\n")

        cmd = [
            sys.executable, "tools/live_test_after_build.py",
            "--model", str(model),
            "--baseline", str(self.baseline),
            "--offline-only",
            "--report", str(RESULTS_DIR / f"watch_{model.stem}_{int(time.time())}.json"),
        ]
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT), text=True)
        status = "PASS" if proc.returncode == 0 else "FAIL"
        print(f"\n[watch] {status}: {model} (exit {proc.returncode})")

    def poll_once(self) -> None:
        for path in self._discover():
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if self._should_trigger(path, mtime):
                self._run_test(path)


def main() -> int:
    ap = argparse.ArgumentParser(description="Watch model builds and auto-test")
    ap.add_argument("--baseline", default="models/tool_policy.onnx",
                    help="Baseline ONNX for comparison")
    ap.add_argument("--watch-dir", default="models",
                    help="Directory to watch (can repeat)")
    args = ap.parse_args()

    baseline = REPO_ROOT / args.baseline
    watch_dirs = [REPO_ROOT / d for d in args.watch_dir.split(",")]

    if not baseline.is_file():
        print(f"[FATAL] baseline not found: {baseline}")
        return 1
    for d in watch_dirs:
        if not d.is_dir():
            print(f"[FATAL] watch dir not found: {d}")
            return 1

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    watcher = BuildWatcher(baseline, watch_dirs)

    print(f"[watch] Watching {', '.join(str(d) for d in watch_dirs)} for ONNX builds...")
    print(f"[watch] Baseline: {baseline}")
    print(f"[watch] Reports:  {RESULTS_DIR}")
    print("[watch] Polling every %.1fs. Press Ctrl+C to stop." % POLL_S)

    # Seed known files so we don't trigger on pre-existing models.
    for p in watcher._discover():
        try:
            watcher._known[p] = p.stat().st_mtime
        except OSError:
            pass

    try:
        while True:
            watcher.poll_once()
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        pass
    print("\n[watch] Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
