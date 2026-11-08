"""Guard against hardcoded ports in `dofus-network/combat/`.

`BRAIN_PORT = 8891` and hardcoded `127.0.0.1` in `brain_service.py` and
`combat_ai_server.py` should be configurable via env or args.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_combat_ports.py
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


# brain_service.py
bs = REPO / "dofus-network" / "combat" / "brain_service.py"
text = bs.read_text(encoding="utf-8", errors="replace")

checks = [
    ("BRAIN_PORT = 8891", "brain port 8891"),
    ('target = "http://127.0.0.1:%d/health" % int(port)', "health URL with 127.0.0.1"),
]
for literal, name in checks:
    if literal in text:
        fail(f"brain_service.py hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

# combat_ai_server.py
cai = REPO / "dofus-network" / "combat" / "combat_ai_server.py"
ctext = cai.read_text(encoding="utf-8", errors="replace")

checks2 = [
    ("def run_server(port: int = 8891,", "combat server default port 8891"),
    ('ThreadingHTTPServer(("127.', "combat server bind 127.0.0.1"),
]
for literal, name in checks2:
    if literal in ctext:
        fail(f"combat_ai_server.py hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded combat port(s) still present")
    sys.exit(1)
print("no hardcoded combat ports detected")
sys.exit(0)