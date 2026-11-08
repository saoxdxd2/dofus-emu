"""Guard against hardcoded device IDs in `desktop/farmengine.cs`.

`ca8ea76b0fe32f85` (UUID), `R58M45F41EFD` (serial), `SM-A515F` (model),
`samsung` (manufacturer), `ARM` (GL vendor), `Mali-G72 MP3` (GL renderer),
and `8192` (max texture size) are baked into source as defaults. Every
instance would share the same device fingerprint, making ban detection
trivial. These should come from `DeviceCatalog` profiles or be randomized.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_device_ids.py
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
    ('uuid ?? "ca8ea76b0fe32f85"', "default device UUID"),
    ('serial ?? "R58M45F41EFD"', "default device serial"),
    ('p.Model : "SM-A515F"', "default device model"),
    ('p.Manufacturer : "samsung"', "default device manufacturer"),
    ('p.GlVendor : "ARM"', "default GL vendor"),
    ('p.GlRenderer : "Mali-G72 MP3"', "default GL renderer"),
    ('p.MaxTextureSize > 0) ? p.MaxTextureSize : 8192', "default max texture size"),
]
for literal, name in checks:
    if literal in text:
        fail(f"farmengine.cs hardcodes {name} ({literal}) — use DeviceCatalog profiles or randomize")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded device ID(s) still present")
    sys.exit(1)
print("no hardcoded device IDs detected")
sys.exit(0)