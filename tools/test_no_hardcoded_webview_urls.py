"""Guard against hardcoded Ankama URLs in `desktop/WebView2ClientForm.cs`.

Hardcoded URLs for CDN referer, static assets, login server, etc. break if
Ankama rotates endpoints. These should come from config or be derived.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_webview_urls.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEBVIEW = REPO / "desktop" / "WebView2ClientForm.cs"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = WEBVIEW.read_text(encoding="utf-8", errors="replace")

checks = [
    ('private const string CdnReferer = "https://www.', "CDN referer URL"),
    ("private const int CdnCookieTtlSeconds = 45", "CDN cookie TTL 45s"),
    ('"https://dofustouch.cdn.ankama.com/"', "Dofustouch CDN URL"),
    ('"https://auth.ankama.com/"', "Auth.ankama.com URL"),
    ('"https://dt-proxy-production-login.ankama-games.com/"', "DT proxy login URL"),
]
for literal, name in checks:
    if literal in text:
        fail(f"WebView2ClientForm.cs hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded URL constant(s) still present")
    sys.exit(1)
print("no hardcoded URL constants detected")
sys.exit(0)