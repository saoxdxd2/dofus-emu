"""Guard against hardcoded gate constants in `dofus-network/agent/gate.py`.

`VETO_AT = 2`, `MAX_MISS_KEYS = 10_000` and `VOLATILE_ARGS` are baked into
source. These should be configurable per instance.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_gate_constants.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "dofus-network" / "agent" / "gate.py"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = GATE.read_text(encoding="utf-8", errors="replace")

checks = [
    ("VETO_AT = 2", "gate veto-at 2"),
    ("MAX_MISS_KEYS = 10_000", "gate max miss keys 10000"),
    ("VOLATILE_ARGS = {\"lang\", \"skill_uid\", \"cell\"}", "gate volatile args"),
]
for literal, name in checks:
    if literal in text:
        fail(f"gate.py hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded gate constant(s) still present")
    sys.exit(1)
print("no hardcoded gate constants detected")
sys.exit(0)