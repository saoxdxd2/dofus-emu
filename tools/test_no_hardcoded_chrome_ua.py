"""Guard against hardcoded Chrome UA version in `desktop/farmengine.cs`.

`DeviceCatalog.BuildUa` hardcodes `Chrome/112.0.5615.137` as the default Chrome
version when `DOFUS_CHROME_UA_VERSION` is not set. Ankama can fingerprint or block
old clients based on UA. The version should come from config or be updated regularly.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_chrome_ua.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FARMENGINE = REPO / "desktop" / "farmengine.cs"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = FARMENGINE.read_text(encoding="utf-8", errors="replace")

if 'chromeVer = "Chrome/112.0.5615.137"' in text:
    fail("farmengine.cs hardcodes Chrome/112.0.5615.137 as default UA version — "
         "make configurable or update regularly")
else:
    ok("Chrome UA version not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded Chrome UA version(s) still present")
    sys.exit(1)
print("no hardcoded Chrome UA versions detected")
sys.exit(0)