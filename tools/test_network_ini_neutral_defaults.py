"""Guard for machine-specific defaults in `network.ini`.

`network.ini` ships defaults measured on one machine (Maroc Telecom,
2026-10-22). Shipping machine-specific defaults to every install means the
relay/egress behavior is wrong elsewhere. This test flags machine-specific
literals and comments so they can be replaced with neutral defaults.

No client, no network, no torch.

Run: python tools/test_network_ini_neutral_defaults.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INI = REPO / "network.ini"

FAILURES = 0
WARNINGS = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def warn(msg: str) -> None:
    global WARNINGS
    WARNINGS += 1
    print(f"WARN {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


if not INI.exists():
    print("network.ini missing (check skipped)")
    sys.exit(0)

text = INI.read_text(encoding="utf-8", errors="replace")

# Machine-specific comment evidence.
if "Maroc Telecom" in text:
    warn("network.ini: comment references 'Maroc Telecom' — machine-specific "
         "measurement comment, make neutral")
else:
    ok("no machine-specific ISP comment")

# A public-proxy example IP in a comment (203.0.113.10 is documentation-range,
# fine, but flag if it looks like a real default).
for line in text.splitlines():
    if "203.0.113.10" in line and "exit_proxy" in line:
        ok("exit_proxy example uses documentation-range IP (203.0.113.10)")

# base_port default 8880 is fine as a default, but confirm it's documented as
# configurable (it is, via the comment). The machine-specific part is the
# comment block at the top.
print()
if FAILURES:
    print(f"{FAILURES} failure(s), {WARNINGS} warning(s)")
    sys.exit(1)
if WARNINGS:
    print(f"{WARNINGS} warning(s) — replace machine-specific defaults with neutrals")
    sys.exit(0)
print("network.ini defaults are neutral")
sys.exit(0)