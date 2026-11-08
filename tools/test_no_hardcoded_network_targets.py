"""Guard against hardcoded external network targets in C# host and Go relay.

`desktop/*.cs` probes `1.1.1.1:443` as the egress reachability test and falls back
to proxy `127.0.0.1:8880` when no instance proxy is set. Both are machine/network
specific: a blocked Cloudflare address or a relay on a different port breaks health
checks and instance routing. They should come from `network.ini` (base_port,
socks_port, upstream) or be configurable probe targets.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_network_targets.py
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


# --- C# host: hardcoded fallback proxy port ---------------------------------
for cs in (REPO / "desktop").rglob("*.cs"):
    text = cs.read_text(encoding="utf-8", errors="replace")
    if '"127.0.0.1:8880"' in text or "'127.0.0.1:8880'" in text:
        fail(f"{cs.name}: hardcoded fallback proxy 127.0.0.1:8880 — "
             "use network.ini base_port instead")
    if '1.1.1.1' in text and ':443' in text:
        # report the specific line
        for line in text.splitlines():
            if '1.1.1.1' in line and ':443' in line:
                fail(f"{cs.name}: hardcoded egress probe 1.1.1.1:443 — "
                     f"({line.strip()[:80]})")
                break

# --- Go relay: hardcoded upstream test target -------------------------------
go = REPO / "scripts" / "dofus-net-proxy.go"
gtext = go.read_text(encoding="utf-8", errors="replace")
for line in gtext.splitlines():
    if '1.1.1.1' in line:
        fail(f"dofus-net-proxy.go: hardcoded external probe target — "
             f"({line.strip()[:80]})")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded network target(s) still present")
    sys.exit(1)
print("no hardcoded external network targets detected")
sys.exit(0)