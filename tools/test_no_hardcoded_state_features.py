"""Guard against hardcoded state-feature constants in `dofus-network/agent/state_features.py`.

`ENTITY_DIM = 12`, `GLOBAL_DIM = 20`, `WORLD_KEYS`, the grid size `14`
(cell % 14), and the many scaling denominators (level/200, ap/12, mp/6,
kamas/100000, inventory/60, etc.) are baked into source. These should be
configurable per deployment or derived from the observation schema.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_state_features.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FEATS = REPO / "dofus-network" / "agent" / "state_features.py"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = FEATS.read_text(encoding="utf-8", errors="replace")

checks = [
    ("ENTITY_DIM = 12", "state feature entity dim 12"),
    ("GLOBAL_DIM = 20", "state feature global dim 20"),
    ("WORLD_KEYS = (\"map_id\", \"cell\", \"in_dialog\", \"in_fight\", \"quest_steps\",",
     "state feature world keys"),
    ("float(value % 14)", "state feature grid size 14"),
    ("_num(flat.get(\"level\")) / 200.0", "state feature level scale 200"),
    ("_num(hp.get(\"ap\")) / 12.0", "state feature AP scale 12"),
    ("_num(hp.get(\"mp\")) / 6.0", "state feature MP scale 6"),
    ("min(1.0, _num(obs.get(\"kamas\")) / 100000.0)", "state feature kamas scale 100000"),
    ("min(1.0, _size(obs.get(\"inventory\")) / 60.0)", "state feature inventory scale 60"),
]
for literal, name in checks:
    if literal in text:
        fail(f"state_features.py hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded state-feature constant(s) still present")
    sys.exit(1)
print("no hardcoded state-feature constants detected")
sys.exit(0)