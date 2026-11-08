"""Guard against hardcoded .NET/Windows SDK paths in regression.py.

regression.py assumes C-colon Program Files dotnet sdk, C-colon Program Files
(x86), C-colon Windows Microsoft.NET framework v4.0.30319 csc.exe, etc. These are
machine-specific facts: a different .NET SDK version, a non-default Windows drive,
or a Linux/macOS build machine breaks the host compile path. The correct approach
is which-style discovery or env overrides, not hardcoded install dirs.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_sdk_paths.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PATH = REPO / "dofus-network" / "agent" / "regression.py"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = PATH.read_text(encoding="utf-8", errors="replace")

# Hardcoded Windows install dirs that should be discovered instead.
hardcoded = [
    r'C:\Program Files\dotnet\sdk',
    r'C:\Program Files (x86)',
    r'C:\Windows',
    r'v4.0.30319',
]
for h in hardcoded:
    if h in text:
        fail(f"regression.py hardcodes {h!r} — use env/PATH discovery instead")
    else:
        ok(f"no hardcoded {h!r}")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded SDK path(s) still present")
    sys.exit(1)
print("no hardcoded SDK/Windows paths detected")
sys.exit(0)