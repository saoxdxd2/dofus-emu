"""Guard against hardcoded thresholds in `dofus-network/agent/verifier.py`.

Confidence scores (0.9, 0.85, 0.8, 0.6), the model minimum confidence (0.5),
the veto threshold (0.0), and the latency boundary (2500 ms) are baked into
the rule engine. These were measured once but should be configurable per
environment, model version, or risk tolerance.

No client, no network, no torch.

Run: python tools/test_no_hardcoded_verifier_thresholds.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VERIFIER = REPO / "dofus-network" / "agent" / "verifier.py"

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES
    FAILURES += 1
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


text = VERIFIER.read_text(encoding="utf-8", errors="replace")

# Hardcoded confidence scores returned by rule verdicts.
confidence_literals = [
    ("confidence=1.0", "verdict confidence 1.0"),
    ("confidence=0.9", "verdict confidence 0.9"),
    ("confidence=0.85", "verdict confidence 0.85"),
    ("confidence=0.8", "verdict confidence 0.8"),
    ("confidence=0.6", "verdict confidence 0.6"),
]
for literal, name in confidence_literals:
    if literal in text:
        fail(f"verifier.py hardcodes {name} ({literal}) — make configurable")
    else:
        ok(f"{name} not hardcoded")

# Model minimum confidence gate.
if "self.model_min_confidence = 0.5" in text:
    fail("verifier.py hardcodes model_min_confidence = 0.5 — make configurable")
else:
    ok("model_min_confidence not hardcoded")

# Veto threshold default.
if "veto_threshold: float = 0.0" in text:
    fail("verifier.py hardcodes veto_threshold = 0.0 — make configurable")
else:
    ok("veto_threshold not hardcoded")

# Latency boundary for timeout/reset classification.
if "> 2500" in text and "latency_ms" in text:
    fail("verifier.py hardcodes latency threshold 2500 ms — make configurable")
else:
    ok("latency threshold not hardcoded")

print()
if FAILURES:
    print(f"{FAILURES} hardcoded verifier threshold(s) still present")
    sys.exit(1)
print("no hardcoded verifier thresholds detected")
sys.exit(0)