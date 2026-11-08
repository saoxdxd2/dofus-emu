"""Guard against hardcoded constants in `desktop/farmengine.cs`.

`RegKeyPath`, `SysImgSubPath`, `WakeAndStartCmd`, `LiveScanCacheMs=12000`,
`maxBytes=64KiB`, `ValidGpuModes`, `GpuModeProbeOrder` are baked into source.
These should be configurable per deployment.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_farmengine_constants.py
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

checks = [
    ('RegKeyPath = @"HKEY_CURRENT_USER\\Software\\DofusFarm"', "registry key path"),
    ('SysImgSubPath = @"system-images\\', "system image subpath"),
    ('WakeAndStartCmd = "input keyevent', "wake and start command"),
    ("LiveScanCacheMs = 12000", "live scan cache ms 12000"),
    ('const int maxBytes = 64 * 1024', "max bytes 64 KiB"),
    ("ValidGpuModes = {", "valid GPU modes list"),
    ("GpuModeProbeOrder = {", "GPU mode probe order"),
]
for literal, name in checks:
    if literal in text:
        fail(f"farmengine.cs hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded farmengine constant(s) still present")
    sys.exit(1)
print("no hardcoded farmengine constants detected")
sys.exit(0)