"""Guard against hardcoded plan-stack constants in `dofus-network/agent/plan_stack.py`.

`MAX_GOALS = 16` and `MAX_BYTES = 2048` are baked into source. These should be
configurable per instance.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_plan_stack.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STACK = REPO / "dofus-network" / "agent" / "plan_stack.py"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = STACK.read_text(encoding="utf-8", errors="replace")

checks = [
    ("MAX_GOALS = 16", "plan stack max goals 16"),
    ("MAX_BYTES = 2048", "plan stack max bytes 2048"),
]
for literal, name in checks:
    if literal in text:
        fail(f"plan_stack.py hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded plan-stack constant(s) still present")
    sys.exit(1)
print("no hardcoded plan-stack constants detected")
sys.exit(0)