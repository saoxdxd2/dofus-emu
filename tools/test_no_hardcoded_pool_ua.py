"""Guard against hardcoded Chrome UA in `dofus-network/ai/pool_runner.py`.

`Chrome/112.0.5615.137` is hardcoded as the User-Agent for proxy scraping.
Ankama can fingerprint or block old clients based on UA. It should come from
config or be updated regularly.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_pool_ua.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POOL_RUNNER = REPO / "dofus-network" / "ai" / "pool_runner.py"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = POOL_RUNNER.read_text(encoding="utf-8", errors="replace")

if "Chrome/112.0.5615.137" in text:
    fail("pool_runner.py hardcodes Chrome/112.0.5615.137 as default UA version — "
         "make configurable or update regularly")
else:
    ok("Chrome UA version not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded Chrome UA version(s) still present")
    sys.exit(1)
print("no hardcoded Chrome UA versions detected")
sys.exit(0)