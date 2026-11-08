"""Guard against hardcoded egress defaults in `dofus-network/ai/test_egress.py`.

`DEFAULT_HOST = "dt-proxy-production-login.ankama-games.com"` and
`DEFAULT_PORT = 443` are baked into source. If Ankama rotates the login host,
egress testing breaks. These should come from `network.ini` or env.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_egress_defaults.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EGRESS = REPO / "dofus-network" / "ai" / "test_egress.py"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = EGRESS.read_text(encoding="utf-8", errors="replace")

checks = [
    ('DEFAULT_HOST = "dt-proxy-production-login.ankama-games.com"', "egress default host"),
    ("DEFAULT_PORT = 443", "egress default port 443"),
]
for literal, name in checks:
    if literal in text:
        fail(f"test_egress.py hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded egress default(s) still present")
    sys.exit(1)
print("no hardcoded egress defaults detected")
sys.exit(0)