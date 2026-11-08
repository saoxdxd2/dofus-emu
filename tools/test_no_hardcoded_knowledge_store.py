"""Guard against hardcoded knowledge-store constants.

`dofus-network/agent/knowledge_store.py` bakes `DEFAULT_TTL_DAYS = 45`,
`DISPUTES_TO_DROP = 3`, `COMPACT_BYTES = 8 MiB`, `COMPACT_EVERY = 512` and the
default fact confidence `0.5` into source. These should be configurable per
instance or per deployment.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_knowledge_store.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STORE = REPO / "dofus-network" / "agent" / "knowledge_store.py"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = STORE.read_text(encoding="utf-8", errors="replace")

checks = [
    ("DEFAULT_TTL_DAYS = 45", "knowledge TTL days 45"),
    ("DISPUTES_TO_DROP = 3", "knowledge disputes-to-drop 3"),
    ("COMPACT_BYTES = 8 * 1024 * 1024", "knowledge compact bytes 8 MiB"),
    ("COMPACT_EVERY = 512", "knowledge compact every 512 writes"),
    ("confidence: float = 0.5", "fact default confidence 0.5"),
]
for literal, name in checks:
    if literal in text:
        fail(f"knowledge_store.py hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded knowledge-store constant(s) still present")
    sys.exit(1)
print("no hardcoded knowledge-store constants detected")
sys.exit(0)