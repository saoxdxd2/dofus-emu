"""Guard against hardcoded ports in C# host (`desktop/*.cs`).

Ports 8895 (brain daemon), 8880 (relay base), 40000 (SOCKS) and others are
baked into C# source. They should come from `network.ini` or environment
variables so an operator can relocate services without recompiling.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_cs_ports.py
"""

from __future__ import annotations

import re
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


PORT_LITERAL = re.compile(r"\b(8880|8881|8882|8883|8884|8885|8886|8887|8888|8889|8890|8891|8893|8894|8895|40000|5554)\b")

for cs in (REPO / "desktop").rglob("*.cs"):
    text = cs.read_text(encoding="utf-8", errors="replace")
    for m in PORT_LITERAL.finditer(text):
        line_no = text[: m.start()].count("\n") + 1
        snippet = text[max(0, m.start() - 50): m.end() + 50].replace("\n", "\\n")
        fail(f"{cs.name}:{line_no}: hardcoded port {m.group(0)} in {snippet.encode('ascii', 'replace').decode()!r}")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded C# port(s) still present")
    sys.exit(1)
print("no hardcoded C# ports detected")
sys.exit(0)