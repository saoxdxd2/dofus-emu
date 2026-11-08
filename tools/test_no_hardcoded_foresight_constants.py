"""Guard against hardcoded foresight constants in `dofus-network/agent/foresight.py`.

`DEPTH = 5`, `HIDDEN = 128` and the model path are baked into source. These
should be configurable per deployment.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_foresight_constants.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FORESIGHT = REPO / "dofus-network" / "agent" / "foresight.py"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = FORESIGHT.read_text(encoding="utf-8", errors="replace")

checks = [
    ("DEPTH = 5", "foresight depth 5"),
    ("HIDDEN = 128", "foresight hidden 128"),
    ('MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "foresight_v1"',
     "foresight model path"),
]
for literal, name in checks:
    if literal in text:
        fail(f"foresight.py hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded foresight constant(s) still present")
    sys.exit(1)
print("no hardcoded foresight constants detected")
sys.exit(0)