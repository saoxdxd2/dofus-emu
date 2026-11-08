"""Guard against hardcoded login host in `desktop/netsettings.cs`.

`LoginHost = "dt-proxy-production-login.ankama-games.com"` is baked into source.
If Ankama rotates the login host, the proxy cannot remap sockets. It should
come from `network.ini` or env.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_netsettings.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NETSETTINGS = REPO / "desktop" / "netsettings.cs"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = NETSETTINGS.read_text(encoding="utf-8", errors="replace")

if 'LoginHost = "dt-proxy-production-login.ankama-games.com"' in text:
    fail("netsettings.cs hardcodes Ankama login host — make configurable via "
         "network.ini/login_host or env")
else:
    ok("Ankama login host not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded login host(s) still present")
    sys.exit(1)
print("no hardcoded login host detected")
sys.exit(0)