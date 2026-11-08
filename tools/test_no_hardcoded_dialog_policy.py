"""Guard against hardcoded language word lists in `dofus-network/agent/dialog_policy.py`.

`_LEAVE_WORDS`, `_ENTER_WORDS`, `_CLOSING` and `_ELEMENT_KEYWORDS` are baked into
source with English/French/Spanish/German words. Adding a language requires editing
code. These should come from a data file.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_dialog_policy.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIALOG = REPO / "dofus-network" / "agent" / "dialog_policy.py"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = DIALOG.read_text(encoding="utf-8", errors="replace")

checks = [
    ('_LEAVE_WORDS = {"leave", "leaving", "exit", "out", "outside", "depart", "head",',
     "dialog leave words"),
    ('_ENTER_WORDS = {"enter", "entering", "inside", "entrer", "entrez",',
     "dialog enter words"),
    ('_CLOSING = ("good luck", "goodbye", "good bye", "farewell", "see you", "take care",',
     "dialog closing words"),
    ("_ELEMENT_KEYWORDS: Dict[str, set[str]] = {", "dialog element keywords"),
]
for literal, name in checks:
    if literal in text:
        fail(f"dialog_policy.py hardcodes {name} — move to a data file")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded dialog-policy constant(s) still present")
    sys.exit(1)
print("no hardcoded dialog-policy constants detected")
sys.exit(0)