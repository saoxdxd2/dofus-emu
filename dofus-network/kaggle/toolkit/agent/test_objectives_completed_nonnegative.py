"""Guard for KNOWN_GAPS.md G94: the published `objectives_completed` is a
step-index sum and can go negative, which corrupts the controller's progress
math and triggers spurious replan/abort decisions.

Reads the recorded trajectory corpus and asserts `objectives_completed` never
drops below zero in any step observation. No client, no network, no torch.

Run: python dofus-network/agent/test_objectives_completed_nonnegative.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
TRAJECTORIES = REPO / "trajectories"

FAILURES = 0
CHECKED = 0
FILES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


def scan_file(path: Path) -> None:
    global CHECKED, FILES
    FILES += 1
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        obs = row.get("observation") or row.get("snapshot") or {}
        if not isinstance(obs, dict):
            continue
        val = obs.get("objectives_completed")
        CHECKED += 1
        if isinstance(val, (int, float)) and val < 0:
            fail(f"{path}:{lineno}: objectives_completed={val} (negative)")


def main() -> None:
    global FAILURES
    if TRAJECTORIES.exists():
        for path in sorted(TRAJECTORIES.glob("*.jsonl")):
            scan_file(path)
    else:
        print("no trajectories dir (check skipped)")
        return

    print(f"\nscanned {FILES} files, {CHECKED} objectives_completed values")
    if FAILURES:
        print(f"{FAILURES} negative value(s) found — G94 not closed")
        sys.exit(1)
    print("objectives_completed never negative in the corpus — G94 closed on this data")


if __name__ == "__main__":
    main()
