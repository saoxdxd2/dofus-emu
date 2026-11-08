"""Guard against hardcoded constants in `dofus-network/advisor/`.

`MAX_PAGE = 20000`, `listing_limit = 500`, `timeout = 30.0`, `max_level = character.level + 10`
and similar values are baked into the advisor. They should be configurable per
environment or character level range.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_advisor_constants.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


# --- advisor/cli.py ---------------------------------------------------------
cli = REPO / "dofus-network" / "advisor" / "cli.py"
text = cli.read_text(encoding="utf-8", errors="replace")

checks = [
    ("MAX_PAGE = 20000", "advisor MAX_PAGE 20000"),
    ("timeout: float = 30.0", "advisor default timeout 30s"),
    ('"listing_limit": 500', "advisor listing_limit 500"),
    ('%%LOCALAPPDATA%%\\\\DofusFarm\\\\bridge_port.txt', "advisor hardcoded LOCALAPPDATA path in error msg"),
]
for literal, name in checks:
    if literal in text:
        fail(f"advisor/cli.py hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

# --- advisor/market.py ------------------------------------------------------
market = REPO / "dofus-network" / "advisor" / "market.py"
mtext = market.read_text(encoding="utf-8", errors="replace")

if "character.level + 10" in mtext:
    fail("advisor/market.py hardcodes max_level = character.level + 10 — make configurable")
else:
    ok("advisor max_level not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded advisor constant(s) still present")
    sys.exit(1)
print("no hardcoded advisor constants detected")
sys.exit(0)