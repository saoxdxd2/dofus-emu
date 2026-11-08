"""Guard against hardcoded client effect IDs and combat constants in
`dofus-network/combat/battle_solver.py`.

These values were ported from client bundle 1.74.9. When Ankama updates the
bundle, effect IDs, grid dimensions, and cache sizes can change silently,
corrupting combat planning. They should be derived from the live client data
extractor (`data/game_data_extractor.py`) rather than hardcoded.

No client, no network, no torch.

Run: python dofus-network/combat/test_effect_id_hardcodes.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SOLVER = REPO / "dofus-network" / "combat" / "battle_solver.py"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = SOLVER.read_text(encoding="utf-8", errors="replace")

# --- Hardcoded effect IDs from client 1.74.9 ---------------------------------
effect_id_pattern = re.compile(
    r"^\s*\d+:\s*\(\"(?:steal|damage|heal)\",\s*\"(?:WATER|EARTH|AIR|FIRE|NEUTRAL|HEAL)\"\)",
    re.MULTILINE,
)
effect_ids = sorted({int(m.group(0).split(":")[0]) for m in effect_id_pattern.finditer(text)})
if effect_ids:
    fail(f"battle_solver.py hardcodes {len(effect_ids)} client effect IDs "
         f"(from bundle 1.74.9): {effect_ids}")
else:
    ok("no hardcoded effect IDs in battle_solver.py")

# --- Hardcoded grid size -----------------------------------------------------
if "NB_CELLS = 560" in text:
    fail("battle_solver.py hardcodes NB_CELLS = 560 (tactical grid size from "
         "client 1.74.9 — derive from live board instead)")
else:
    ok("NB_CELLS is not hardcoded")

# --- Hardcoded lru_cache sizes -----------------------------------------------
cache_sizes = re.findall(r"@lru_cache\(maxsize=(\d+)\)", text)
if cache_sizes:
    fail(f"battle_solver.py hardcodes lru_cache maxsize values: {cache_sizes} "
         "(make configurable)")
else:
    ok("no hardcoded lru_cache sizes")

# --- Hardcoded AP/MP weights -------------------------------------------------
weights = [
    ("AP_DENIAL_WEIGHT = 6.0", "AP denial weight"),
    ("MP_DENIAL_WEIGHT = 3.0", "MP denial weight"),
    ("AP_GAIN_WEIGHT = 5.0", "AP gain weight"),
    ("MP_GAIN_WEIGHT = 1.5", "MP gain weight"),
    ("PUSH_WEIGHT = 0.75", "push weight"),
]
for literal, name in weights:
    if literal in text:
        fail(f"battle_solver.py hardcodes {name} ({literal.split('=')[0].strip()} "
             "— tune from live data or make configurable)")
    else:
        ok(f"{name} not hardcoded")

# --- Hardcoded teleport positions --------------------------------------------
if "TELEPORT_POSITIONS = 8" in text:
    fail("battle_solver.py hardcodes TELEPORT_POSITIONS = 8")
else:
    ok("TELEPORT_POSITIONS not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} combat hardcode(s) still present")
    sys.exit(1)
print("no combat hardcodes detected (unexpected — review needed)")
sys.exit(0)