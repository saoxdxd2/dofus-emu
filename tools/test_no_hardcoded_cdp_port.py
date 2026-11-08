"""Guard against hardcoded CDP port offset in `desktop/farmengine.cs`.

`cdpPort = port + 4000` is baked into source. The Chrome DevTools Protocol
port offset should be configurable to avoid conflicts with other services.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_cdp_port.py
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

if "int cdpPort = port + 4000" in text:
    fail("farmengine.cs hardcodes CDP port offset 4000 — make configurable")
else:
    ok("CDP port offset not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded CDP port offset(s) still present")
    sys.exit(1)
print("no hardcoded CDP port offsets detected")
sys.exit(0)