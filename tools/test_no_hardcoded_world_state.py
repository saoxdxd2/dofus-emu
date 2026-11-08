"""Guard against hardcoded world-state constants in `dofus-network/agent/world_state.py`.

`SCHEMA_VERSION = 2`, `CELLS_PER_ROW = 14`, `SLOTS` dict, `GLOBAL_FEATURES`
tuple and all the scaling denominators are baked into source. These should be
configurable or derived from the observation schema.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_world_state.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WS = REPO / "dofus-network" / "agent" / "world_state.py"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = WS.read_text(encoding="utf-8", errors="replace")

checks = [
    ("SCHEMA_VERSION = 2", "world state schema version 2"),
    ("CELLS_PER_ROW = 14", "world state cells per row 14"),
    ('SLOTS = {"npc": 8, "interactive": 8, "group": 8, "exit": 8, "door": 12,',
     "world state SLOTS dict"),
]
for literal, name in checks:
    if literal in text:
        fail(f"world_state.py hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded world-state constant(s) still present")
    sys.exit(1)
print("no hardcoded world-state constants detected")
sys.exit(0)