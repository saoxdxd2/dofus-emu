"""Guard against hardcoded constants in remaining C# host files.

Checks mainform.cs, localsecret.cs, pages.cs, prioritygovernor.cs, watchdog.cs
for hardcoded ports, timeouts, cache seconds, and other constants that should
be configurable.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_cs_constants.py
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


checks = [
    ("desktop/mainform.cs", 'private const int NetDaemonPort = 8895;', "NetDaemonPort 8895"),
    ("desktop/mainform.cs", "private const long LogMaxBytes = 64L * 1024 * 1024", "LogMaxBytes 64 MiB"),
    ("desktop/mainform.cs", "private const int WM_HOTKEY = 0x0312", "WM_HOTKEY 0x0312"),
    ("desktop/pages.cs", "const int block = 8192", "block size 8192"),
    ("desktop/prioritygovernor.cs", "private const double ParentCacheSeconds = 4.0", "parent cache seconds 4.0"),
    ("desktop/watchdog.cs", "private const int MaxRestartsPerHour = 3", "max restarts per hour 3"),
    ("desktop/watchdog.cs", "private const int RestartCooldownSeconds = 60", "restart cooldown 60s"),
    ("desktop/localsecret.cs", 'public const string FileName = "local_secret"', "local secret filename"),
]
for rel_path, literal, name in checks:
    path = REPO / rel_path
    if not path.exists():
        ok(f"{rel_path} absent (check skipped)")
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    if literal in text:
        fail(f"{rel_path} hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded in {rel_path}")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded C# constant(s) still present")
    sys.exit(1)
print("no hardcoded C# constants detected")
sys.exit(0)