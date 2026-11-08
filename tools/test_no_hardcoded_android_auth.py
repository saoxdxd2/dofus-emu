"""Guard against hardcoded Ankama URLs in `scripts/android-micro-auth.go`.

`https://auth.ankama.com/login/ankama-guest...` and `com.ankama.dofustouch`
headers are baked into source. If Ankama rotates endpoints, auth breaks. These
should come from config or flags.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_android_auth.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ANDROID_AUTH = REPO / "scripts" / "android-micro-auth.go"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = ANDROID_AUTH.read_text(encoding="utf-8", errors="replace")

checks = [
    ('https://auth.ankama.com/login/ankama-guest', "Ankama guest auth URL"),
    ('X-Requested-With", "com.ankama.dofus', "Ankama package header"),
]
for literal, name in checks:
    if literal in text:
        fail(f"android-micro-auth.go hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded auth constant(s) still present")
    sys.exit(1)
print("no hardcoded auth constants detected")
sys.exit(0)