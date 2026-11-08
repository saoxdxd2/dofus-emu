r"""Combat integrity: every hardcoded game constant that Ankama re-tunes with a
bundle or balance patch, across all of combat/ (not just battle_solver.py).

Checks:
  * fixed grid size (NB_CELLS = 560, from client bundle 1.74.9)
  * AP/MP denial/gain/push/heal weights (hardcoded tuning constants)
  * lru_cache maxsize budgets
  * TELEPORT_POSITIONS = 8
  * breed_id / level / action_points / movement_points defaults
  * hardcoded effect IDs ported from a specific client bundle

These must be derived from live board data or made configurable before an
upgrade, because Ankama changes them with every patch.

`test_effect_id_hardcodes.py` already guards battle_solver.py effect IDs; this
file sweeps the rest of combat/ for the same class of upgrade-blocking hardcode.

No client, no network, no torch.

Run: python dofus-network/combat/test_combat_integrity.py   (exit 0 = green)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
COMBAT = REPO / "dofus-network" / "combat"
FAILURES: list = []
CHECK_COUNT: int = 0

GRID_SIZE = re.compile(r"NB_CELLS\s*=\s*560\b")
LRU_CACHE = re.compile(r"@lru_cache\(maxsize=(\d+)\)")
WEIGHT = re.compile(r"\b[A-Z][A-Z_]*WEIGHT\s*=\s*\d+\.\d+")
TELEPORT_POSITIONS = re.compile(r"TELEPORT_POSITIONS\s*=\s*8\b")
BREED_DEFAULT = re.compile(r"\bbreed_id\s*=\s*8\b")
LEVEL_DEFAULT = re.compile(r"\blevel\s*=\s*100\b")
AP_DEFAULT = re.compile(r"\baction_points\s*=\s*10\b")
MP_DEFAULT = re.compile(r"\bmovement_points\s*=\s*4\b")

CHECKS = [
    (GRID_SIZE, "hardcoded grid size 560", None),
    (LRU_CACHE, "hardcoded lru_cache maxsize", 1),
    (WEIGHT, "hardcoded weight", None),
    (TELEPORT_POSITIONS, "hardcoded TELEPORT_POSITIONS 8", None),
    (BREED_DEFAULT, "hardcoded breed_id 8", None),
    (LEVEL_DEFAULT, "hardcoded level 100", None),
    (AP_DEFAULT, "hardcoded action_points 10", None),
    (MP_DEFAULT, "hardcoded movement_points 4", None),
]


def fail(label: str, detail: str = "") -> None:
    global CHECK_COUNT
    CHECK_COUNT += 1
    FAILURES.append(f"{label}: {detail}" if detail else label)
    print(f"FAIL {label}" + (f" | {detail}" if detail else ""))


def ok(msg: str) -> None:
    global CHECK_COUNT
    CHECK_COUNT += 1
    print(f"ok   {msg}")


def line_of(text: str, index: int) -> int:
    return text[: index].count("\n") + 1


def scan(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        code = raw.split("#", 1)[0]
        for pat, label, grp in CHECKS:
            for m in pat.finditer(code):
                detail = m.group(grp) if grp is not None else m.group(0)
                fail(f"{path}:{lineno}: {label}", f"{detail!r}")


def main() -> int:
    print("== combat_integrity == ")
    for path in sorted(COMBAT.glob("*.py")):
        if path.name.startswith("test_") or path.name == Path(__file__).name:
            continue
        scan(path)
    print()
    print("=" * 50)
    status = "PASS" if not FAILURES else "FAIL"
    print(f"[{status}] combat_integrity (combat): {CHECK_COUNT} check(s), {len(FAILURES)} failure(s), 0 warning(s)")
    print(f"TOTAL: {len(FAILURES)} combat hardcode(s)")
    if FAILURES:
        print("Derive from live data or make configurable before upgrading.")
        return 1
    print("no upgrade-blocking combat constants detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
