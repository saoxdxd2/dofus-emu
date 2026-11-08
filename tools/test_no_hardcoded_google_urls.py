"""Guard against hardcoded Google/Play Store URLs in C# and Go source.

`DynamicVersionResolver.cs` hardcodes the Google Play Store URL for version
checks. `dofus-net-proxy.go` hardcodes Google domain allowlists. These should
be configurable or derived from config.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_google_urls.py
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


# DynamicVersionResolver.cs
dvr = REPO / "desktop" / "DynamicVersionResolver.cs"
text = dvr.read_text(encoding="utf-8", errors="replace")
if 'StoreUrl = "https://play.google.com' in text:
    fail("DynamicVersionResolver.cs hardcodes Google Play Store URL — make configurable")
else:
    ok("Google Play Store URL not hardcoded")

# dofus-net-proxy.go domain allowlist
proxy = REPO / "scripts" / "dofus-net-proxy.go"
ptext = proxy.read_text(encoding="utf-8", errors="replace")
for domain in (".google.com", ".gstatic.com", ".googleusercontent.com"):
    if domain in ptext:
        fail(f"dofus-net-proxy.go hardcodes domain allowlist {domain} — make configurable")
    else:
        ok(f"Domain allowlist {domain} not hardcoded")

# WebView2Backend.cs Google accounts check
wb = REPO / "desktop" / "WebView2Backend.cs"
wtext = wb.read_text(encoding="utf-8", errors="replace")
if "accounts.google" in wtext.lower():
    fail("WebView2Backend.cs hardcodes accounts.google.com check — make configurable")
else:
    ok("Google accounts check not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded Google URL(s) still present")
    sys.exit(1)
print("no hardcoded Google URLs detected")
sys.exit(0)