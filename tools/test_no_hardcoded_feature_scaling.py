"""Guard against hardcoded feature scaling denominators in
`dofus-network/agent/features.py`.

Values like quest_level/200, step_index/8, inventory/20, level/200,
objectives_completed/50, streak/5, travel_hops/3 are baked into the
featuriser. Changing the game's scale (max level, max objectives per
quest, etc.) silently changes feature magnitudes and breaks the model.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_feature_scaling.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FEATURES = REPO / "dofus-network" / "agent" / "features.py"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = FEATURES.read_text(encoding="utf-8", errors="replace")

# Hardcoded scaling denominators in feature computation.
scaling = [
    ("quest_level / 200.0", "quest_level scale 200"),
    ("step_index / 8.0", "step_index scale 8"),
    ("len(inv) / 20.0", "inventory scale 20"),
    ("travel_hops(state.get(\"map_id\"), target_map) / 3.0", "travel_hops scale 3"),
    ("(state.get(\"level\") or 1) / 200.0", "level scale 200"),
    ("(state.get(\"objectives_completed\") or 0) / 50.0", "objectives_completed scale 50"),
    ("(state.get(\"quests_completed\") or 0) / 20.0", "quests_completed scale 20"),
    ("len(state.get(\"quests_active\") or {}) / 5.0", "quests_active scale 5"),
    ("streak / 5.0", "streak scale 5"),
]
for literal, name in scaling:
    if literal in text:
        fail(f"features.py hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded feature scaling constant(s) still present")
    sys.exit(1)
print("no hardcoded feature scaling constants detected")
sys.exit(0)