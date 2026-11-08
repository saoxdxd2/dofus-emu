"""Guard against hardcoded memory limits in the Go relay and C# host.

`scripts/dofus-net-proxy.go` calls `debug.SetMemoryLimit(384 << 20)` (384 MiB) — a
machine-specific GC ceiling that should scale with available RAM or be configurable.
`desktop/farmengine.cs` hardcodes `"384m"` as the Dalvik heap limit for low-RAM
devices and `1024m`/`512m` thresholds tied to `ramMb` brackets.

A real upgrade should derive these from the instance's RAM budget (~200 MB/instance
target) rather than compile them in.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_memory_limit.py
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


# --- Go relay ---------------------------------------------------------------
go = REPO / "scripts" / "dofus-net-proxy.go"
gtext = go.read_text(encoding="utf-8", errors="replace")
if "SetMemoryLimit(384 << 20)" in gtext or "SetMemoryLimit(384" in gtext:
    fail("dofus-net-proxy.go: hardcoded 384 MiB GC memory limit "
         "(debug.SetMemoryLimit(384 << 20)) — derive from instance RAM or make configurable")
else:
    ok("no hardcoded GC memory limit in Go relay")

# --- C# host ----------------------------------------------------------------
cs = REPO / "desktop" / "farmengine.cs"
ctext = cs.read_text(encoding="utf-8", errors="replace")
for token in ('"384m"', '"512m"', '"1024m"'):
    if token in ctext:
        fail(f"farmengine.cs: hardcoded heap limit {token} — derive from ramMb instead")
    else:
        ok(f"no hardcoded heap limit {token} in C# host")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded memory limit(s) still present")
    sys.exit(1)
print("no hardcoded memory limits detected")
sys.exit(0)