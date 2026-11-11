"""Guard against hardcoded client effect IDs and combat constants in
`dofus-network/combat/battle_solver.py`.

These values were ported from client bundle 1.74.9. When Ankama updates the
bundle, effect IDs, grid dimensions, and cache sizes can change silently,
corrupting combat planning. They should be derived from the live client data
extractor (`data/game_data_extractor.py`) rather than hardcoded.

No client, no network, no torch.

Run as a script:  python dofus-network/combat/test_effect_id_hardcodes.py
Run under pytest: python -m pytest dofus-network/combat/test_effect_id_hardcodes.py

This module must not call sys.exit() at import time. The gate (`tools/gate.py`)
runs it as __main__, but pytest imports every test_*.py during collection; an
import-time exit used to kill the entire run with INTERNALERROR, taking every
other combat test down with it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SOLVER = REPO / "dofus-network" / "combat" / "battle_solver.py"

# Tuple of (label, literal-or-None, predicate). Keeping the checks as data means
# the script and pytest collect exactly the same set; neither can drift.
WEIGHTS = (
    ("AP_DENIAL_WEIGHT = 6.0", "AP denial weight"),
    ("MP_DENIAL_WEIGHT = 3.0", "MP denial weight"),
    ("AP_GAIN_WEIGHT = 5.0", "AP gain weight"),
    ("MP_GAIN_WEIGHT = 1.5", "MP gain weight"),
    ("PUSH_WEIGHT = 0.75", "push weight"),
)


def collect_failures(text: str) -> tuple[list[str], list[str]]:
    """Return (failure messages, ok messages) for one solver source."""
    failures: list[str] = []
    oks: list[str] = []

    # --- Hardcoded effect IDs from client 1.74.9 -----------------------------
    effect_id_pattern = re.compile(
        r"^\s*\d+:\s*\(\"(?:steal|damage|heal)\",\s*\"(?:WATER|EARTH|AIR|FIRE|NEUTRAL|HEAL)\"\)",
        re.MULTILINE,
    )
    effect_ids = sorted({int(m.group(0).split(":")[0])
                         for m in effect_id_pattern.finditer(text)})
    if effect_ids:
        failures.append(f"battle_solver.py hardcodes {len(effect_ids)} client effect IDs "
                        f"(from bundle 1.74.9): {effect_ids}")
    else:
        oks.append("no hardcoded effect IDs in battle_solver.py")

    # --- Hardcoded grid size -------------------------------------------------
    if "NB_CELLS = 560" in text:
        failures.append("battle_solver.py hardcodes NB_CELLS = 560 (tactical grid size "
                        "from client 1.74.9 — derive from live board instead)")
    else:
        oks.append("NB_CELLS is not hardcoded")

    # --- Hardcoded lru_cache sizes -------------------------------------------
    cache_sizes = re.findall(r"@lru_cache\(maxsize=(\d+)\)", text)
    if cache_sizes:
        failures.append(f"battle_solver.py hardcodes lru_cache maxsize values: "
                        f"{cache_sizes} (make configurable)")
    else:
        oks.append("no hardcoded lru_cache sizes")

    # --- Hardcoded AP/MP weights ---------------------------------------------
    for literal, name in WEIGHTS:
        if literal in text:
            failures.append(f"battle_solver.py hardcodes {name} "
                            f"({literal.split('=')[0].strip()} — tune from live data "
                            "or make configurable)")
        else:
            oks.append(f"{name} not hardcoded")

    # --- Hardcoded teleport positions ----------------------------------------
    if "TELEPORT_POSITIONS = 8" in text:
        failures.append("battle_solver.py hardcodes TELEPORT_POSITIONS = 8")
    else:
        oks.append("TELEPORT_POSITIONS not hardcoded")

    return failures, oks


def run_checks(verbose: bool = True) -> list[str]:
    """Read the solver, print the report when verbose, return the failures."""
    text = SOLVER.read_text(encoding="utf-8", errors="replace")
    failures, oks = collect_failures(text)
    if verbose:
        for msg in oks:
            print(f"ok   {msg}")
        for msg in failures:
            print(f"FAIL {msg}")
    return failures


def test_no_hardcoded_client_constants() -> None:
    """pytest entry point. A failed check is an assertion failure, never an
    import-time exit, so one red test cannot abort collection for the rest."""
    failures = run_checks(verbose=False)
    assert not failures, "\n".join(failures)


def main() -> int:
    failures = run_checks(verbose=True)
    print()
    if failures:
        print(f"{len(failures)} combat hardcode(s) still present")
        return 1
    print("no combat hardcodes detected (unexpected — review needed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
