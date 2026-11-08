"""Guard against hardcoded ports in `desktop/installer.cs`.

The installer writes `base_port=8880` and `socks_port=40000` into the generated
`network.ini`, and uses port `5554` as the ADB emulator base. These are machine
and environment specific and should come from `network.ini` or env vars.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_installer_ports.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "desktop" / "installer.cs"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = INSTALLER.read_text(encoding="utf-8", errors="replace")

checks = [
    ('"base_port=8880"', "installer writes base_port=8880"),
    ('"socks_port=40000"', "installer writes socks_port=40000"),
    ('5554 + 2 * (slotIdx', "installer uses ADB base 5554"),
    ('"emulator-"', "installer uses hardcoded emulator serial prefix"),
]
for literal, name in checks:
    if literal in text:
        fail(f"installer.cs hardcodes {name} ({literal}) — use network.ini/env")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded installer value(s) still present")
    sys.exit(1)
print("no hardcoded installer values detected")
sys.exit(0)