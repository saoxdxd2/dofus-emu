"""The head steps aside when it is unsure -- a fitted rule, not a vibes threshold.

Measured 2026-11-04 on the episode-held-out split (3,719 rows, sha256 f1f76b0d86e8):
the shipped tool-policy head scores 0.9043. Deferring to the rule planner whenever the
head's top-2 logit margin falls below 0.75 scores 0.9099 -- 26 rows rescued against 5
broken, paired-sign p = 0.0002 -- while overriding with a *random* answer at exactly the
same gate scores 0.8884, below the incumbent. That control is the whole point: the gate
is not what helps, the second opinion is.

Why a gate at all, when the weight-space route was the plan: the head is at the linear
optimum of its own representation (a from-zero refit of its output layer reproduces
0.9043 to four decimals, and one full-batch gradient step from the incumbent makes it
worse at every learning rate tried), and its hidden layer induces the same 2,064-state
partition as the 42 raw inputs -- so no edit inside this architecture can separate what
the features already fuse. The reachable improvement is *when to ask someone else*.

Fail-open by design: no artifact, no gate. A missing measurement must not silently
change what the agent does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

GATE_PATH = Path(__file__).resolve().parent.parent / "models" / "consult_gate.json"


def load(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(Path(path or GATE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def floor(path: Optional[Path] = None) -> Optional[float]:
    doc = load(path)
    if not doc:
        return None
    try:
        value = float(doc.get("floor"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def margin(logits: Optional[Sequence[float]]) -> Optional[float]:
    """Top-2 gap of the raw logits -- the quantity the floor was fitted on.

    Compare against None explicitly: taking the truth value of a numpy array raises
    "ambiguous", and inside the encoder's try/except that surfaces as "the model is
    unusable" -- which would switch the head off in silence rather than gate it.
    """
    if logits is None:
        return None
    vals = [float(v) for v in list(logits)]
    if len(vals) < 2:
        return None
    vals.sort()
    return vals[-1] - vals[-2]


def should_defer(logits: Optional[Sequence[float]] = None,
                 gap: Optional[float] = None,
                 gate: Optional[Dict[str, Any]] = None) -> bool:
    """True when the rule planner should make this call instead of the head."""
    if gap is None:
        gap = margin(logits)
    if gap is None:
        return False
    try:
        f = float((gate if gate is not None else load() or {}).get("floor") or 0.0)
    except (TypeError, ValueError, AttributeError):
        return False
    return f > 0 and gap < f
