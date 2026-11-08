"""Guard against hardcoded page origin and relay URLs in `desktop/WebView2ClientForm.cs`.

`PageOrigin`, relay URI format with `127.0.0.1`, CDN request timeouts, redirection
limits, and connection limits are baked into source. These should come from config.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_page_origin.py
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
    ('internal const string PageOrigin = "https://d', "PageOrigin constant"),
    ('req.Timeout = 30000', "CDN request timeout 30s"),
    ('req.ReadWriteTimeout = 30000', "CDN read/write timeout 30s"),
    ('req.MaximumAutomaticRedirections = 5', "CDN max redirections 5"),
    ('req.ServicePoint.ConnectionLimit = 24', "CDN connection limit 24"),
    ('http://127.0.0.1:{0}/__cdn/', "relay URI format with 127.0.0.1"),
]
for literal, name in checks:
    if literal in text:
        fail(f"WebView2ClientForm.cs hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded constant(s) still present")
    sys.exit(1)
print("no hardcoded constants detected")
sys.exit(0)