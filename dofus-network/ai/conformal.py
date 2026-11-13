"""Split-conformal abstention for the learned tool head.

The problem
-----------
`ai/guard_policy.py:758` returns `argmax(probs)` and nothing else. `ai/onnx_policy.py`
does the same for the tool logits. Neither has a confidence gate on the *tool*: whatever
class wins, wins, even when the distribution over tools is flat. The one scalar in
`policy.json` (`threshold`) gates **argument presence** -- "is this argument present at
all" -- not "is this action safe to take".

That is a real gap and it is worse than a badly-tuned threshold: there is no threshold.

Why not just add one
--------------------
A hand-picked softmax cut has no guarantee. It is calibrated on the data it was tuned on,
and the moment the distribution moves -- a new map, an unseen monster family, a client UI
change that shifts the observation -- the fraction of wrong-but-confident decisions is
whatever it is. There is no number to quote.

What split conformal gives instead
----------------------------------
A finite-sample, distribution-free guarantee. Given a calibration set of `n` labelled
points disjoint from training, and a nonconformity score `s(x, y) = 1 - p(y | x)`:

    q_hat = the ceil((n + 1)(1 - alpha))-th smallest calibration score

then for a *fresh* point, `P(Y in C(X)) >= 1 - alpha` -- under exchangeability, with no
assumption about the model, the data distribution, or the score. The prediction set is

    C(x) = { j : 1 - p(j | x) <= q_hat } = { j : p(j | x) >= 1 - q_hat }

and the rule is: **accept only when |C(x)| == 1**. Size 0 means the model cannot even
cover its own answer (out of distribution); size >= 2 means the set is ambiguous. Both
abstain, which is the honest response -- the deterministic planner is a better fallback
than a guess with no support.

The guarantee is marginal, not conditional: it promises 1 - alpha coverage *on average
over the data*, not for every region. Class-conditional coverage needs a per-class
quantile (`Mondrian`), which is offered here as `per_class=True` for when a rare tool
matters more than the average.

What this module does not do
----------------------------
It does not fit, retrain or re-threshold the model, and it does not touch
`policy.json`. `threshold` keeps gating argument presence exactly as before; this gates
*acceptance of the tool* and only when a calibration artifact is loaded.

Measured on real data, 2026-11-13
---------------------------------
`data/tool_sft_v3/val.jsonl` (3,719 rows, 42 features) through the 42-wide 21-class head
`models/backups/tool_policy.807023112a4b.onnx`, argmax accuracy **0.9043**. Calibrated on
one random half, measured on the other:

    alpha  floor   coverage (target)   set size  abstain  empty   holds
    0.05   0.132   0.9425   (0.95)      1.19      0.136    0.000   yes
    0.10   0.333   0.9011   (0.90)      0.99      0.020    0.013   yes
    0.20   0.791   0.7946   (0.80)      0.83      0.173    0.173   yes

The guarantee holds at all three levels. At `alpha=0.10` the gate escalates 2.0% of
decisions, and **1.3% of all rows are an empty set** -- the model's own top answer does
not survive its own calibration. Those are exactly the confidently-wrong decisions a
scalar threshold cannot name.

The per-class quantiles at `alpha=0.10` are not close to the marginal 0.667: class 4
(n=576) needs only 0.489, class 3 (n=18) needs 0.993, and every class with n=1 needs
1.000, which means "always in the set, always abstain". That is the skew in this corpus
(quest.advance 32%, then a tail of 0.1% classes) showing up as class-conditional
under-coverage, and it is the argument for `per_class=True` when a rare tool matters.

To fit the artifact for whatever head is installed, the caller needs `(probs, labels)`
for that head. The shipped 266-wide head builds its features at runtime in
`agent/planner_nn.py`, so its calibration has to come from a labelled run rather than
from a stored corpus::

    python -m ai.conformal --probs cal.npz --check test.npz --alpha 0.1 \\
        --out models/conformal.json
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


def _as_matrix(probs: Any) -> np.ndarray:
    matrix = np.asarray(probs, dtype=np.float64)
    if matrix.ndim == 1:
        matrix = matrix[None, :]
    return matrix


def quantile_index(n: int, alpha: float) -> int:
    """The 1-based rank of the conformal quantile: ``ceil((n + 1)(1 - alpha))``.

    This exact integer is the whole guarantee. Using ``ceil(n(1 - alpha))`` -- the
    "obvious" choice -- is the classic off-by-one that makes the coverage promise false
    at small ``n``. Returns 0 when the rank exceeds ``n``, meaning "not enough
    calibration data for this alpha", which the caller must treat as ``q_hat = 1``.
    """
    if n <= 0:
        return 0
    rank = int(math.ceil((n + 1) * (1.0 - float(alpha))))
    return rank if 1 <= rank <= n else 0


def nonconformity(probs: Any, labels: Sequence[int]) -> np.ndarray:
    """``1 - p(label | x)`` for every calibration point."""
    matrix = _as_matrix(probs)
    truth = np.asarray(labels, dtype=int)
    if matrix.shape[0] != truth.shape[0]:
        raise ValueError("%d rows of probabilities for %d labels"
                         % (matrix.shape[0], truth.shape[0]))
    if truth.size and (truth.min() < 0 or truth.max() >= matrix.shape[1]):
        raise ValueError("label outside the probability columns")
    return 1.0 - matrix[np.arange(matrix.shape[0]), truth]


@dataclass
class ConformalCalibration:
    """A fitted quantile plus what it was fitted on, so it can be audited."""

    alpha: float
    q_hat: float
    n: int
    classes: int
    rank: int
    # Empirical diagnostics from the calibration split, not part of the guarantee.
    coverage_on_calibration: float = 0.0
    mean_set_size: float = 0.0
    abstain_rate: float = 0.0
    per_class: Dict[int, float] = field(default_factory=dict)

    @property
    def probability_floor(self) -> float:
        """A class is in the set when its probability is at least this."""
        return max(0.0, 1.0 - self.q_hat)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "alpha": self.alpha, "q_hat": self.q_hat, "n": self.n,
            "classes": self.classes, "rank": self.rank,
            "probability_floor": self.probability_floor,
            "coverage_on_calibration": round(self.coverage_on_calibration, 4),
            "mean_set_size": round(self.mean_set_size, 4),
            "abstain_rate": round(self.abstain_rate, 4),
            "per_class": {str(k): round(v, 6) for k, v in self.per_class.items()},
            "method": "split-conformal, score = 1 - p(y|x), marginal coverage",
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ConformalCalibration":
        return cls(
            alpha=float(payload["alpha"]), q_hat=float(payload["q_hat"]),
            n=int(payload["n"]), classes=int(payload.get("classes") or 0),
            rank=int(payload.get("rank") or 0),
            coverage_on_calibration=float(payload.get("coverage_on_calibration") or 0.0),
            mean_set_size=float(payload.get("mean_set_size") or 0.0),
            abstain_rate=float(payload.get("abstain_rate") or 0.0),
            per_class={int(k): float(v)
                       for k, v in (payload.get("per_class") or {}).items()},
        )

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Optional["ConformalCalibration"]:
        path = Path(path)
        if not path.exists():
            return None
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            return None


def calibrate(probs: Any, labels: Sequence[int], alpha: float = 0.1,
              per_class: bool = False) -> ConformalCalibration:
    """Fit the conformal quantile on a labelled split disjoint from training."""
    if not 0.0 < float(alpha) < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    matrix = _as_matrix(probs)
    truth = np.asarray(labels, dtype=int)
    scores = nonconformity(matrix, truth)
    n = int(scores.size)
    if n == 0:
        raise ValueError("no calibration points")
    rank = quantile_index(n, alpha)
    # Not enough points to promise anything: q_hat = 1 puts every class in the set, so
    # the gate abstains on everything rather than pretending to a guarantee it cannot
    # make. This is the honest failure mode and it is visible in `mean_set_size`.
    q_hat = 1.0 if rank == 0 else float(np.sort(scores)[rank - 1])

    in_set = matrix >= (1.0 - q_hat) - 1e-12
    sizes = in_set.sum(axis=1)
    covered = in_set[np.arange(n), truth]
    per_class_scores: Dict[int, float] = {}
    if per_class:
        for label in np.unique(truth):
            mask = truth == label
            class_rank = quantile_index(int(mask.sum()), alpha)
            per_class_scores[int(label)] = (1.0 if class_rank == 0
                                            else float(np.sort(scores[mask])[class_rank - 1]))
    return ConformalCalibration(
        alpha=float(alpha), q_hat=q_hat, n=n, classes=int(matrix.shape[1]),
        rank=rank,
        coverage_on_calibration=float(covered.mean()),
        mean_set_size=float(sizes.mean()),
        abstain_rate=float((sizes != 1).mean()),
        per_class=per_class_scores,
    )


def prediction_set(probs_row: Any, calibration: ConformalCalibration,
                   label: Optional[int] = None) -> List[int]:
    """The classes the model cannot rule out at the calibrated level.

    ``label`` selects the class-conditional quantile when the calibration was fitted
    with ``per_class=True`` and that class is present; otherwise the marginal one.
    """
    row = np.asarray(probs_row, dtype=np.float64).ravel()
    floor = 1.0 - calibration.q_hat
    if label is not None and label in calibration.per_class:
        floor = 1.0 - calibration.per_class[label]
    return [int(j) for j in np.nonzero(row >= floor - 1e-12)[0]]


def accept(probs_row: Any, calibration: ConformalCalibration,
           label: Optional[int] = None) -> Tuple[bool, int]:
    """``(accepted, set_size)`` -- accept only a singleton prediction set."""
    size = len(prediction_set(probs_row, calibration, label=label))
    return size == 1, size


@dataclass
class Decision:
    """What the gate decided, with the evidence, so a log can explain it."""

    tool: Optional[str]
    accepted: bool
    set_size: int
    set_members: List[str]
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return {"tool": self.tool, "accepted": self.accepted,
                "set_size": self.set_size, "set_members": self.set_members,
                "reason": self.reason}


def gate(tool: Optional[str], tools: Sequence[str], probs: Any,
         calibration: Optional[ConformalCalibration]) -> Decision:
    """Decide whether the head's own answer is safe to act on.

    With no calibration loaded this accepts everything, which is exactly today's
    behaviour -- the gate is additive, and an uncalibrated deployment is unchanged.
    """
    if calibration is None:
        return Decision(tool, True, 1, [tool] if tool else [], "no calibration loaded")
    if tool is None:
        return Decision(None, False, 0, [], "no tool proposed")
    members = prediction_set(probs, calibration)
    names = [tools[j] for j in members if 0 <= j < len(tools)]
    size = len(members)
    if size == 1:
        chosen = names[0] if names else tool
        return Decision(chosen, True, 1, names, "singleton prediction set")
    if size == 0:
        # Nothing clears the floor: the model's own answer is not in its own set, which
        # is what an out-of-distribution observation looks like from here.
        return Decision(tool, False, 0, [], "empty set: no class reaches the floor")
    return Decision(tool, False, size, names,
                    "ambiguous: %d classes in the set (%s)" % (size, ", ".join(names[:4])))


# ---------------------------------------------------------------------------
# A CLI, so the artifact is reproducible rather than hand-written
# ---------------------------------------------------------------------------

def report(calibration: ConformalCalibration, probs: Any, labels: Sequence[int]) -> Dict[str, Any]:
    """Measure the guarantee on a *second* split the quantile never saw."""
    matrix = _as_matrix(probs)
    truth = np.asarray(labels, dtype=int)
    floor = 1.0 - calibration.q_hat
    in_set = matrix >= floor - 1e-12
    sizes = in_set.sum(axis=1)
    covered = in_set[np.arange(truth.size), truth]
    return {
        "alpha": calibration.alpha,
        "target_coverage": round(1.0 - calibration.alpha, 4),
        "measured_coverage": round(float(covered.mean()), 4),
        "n": int(truth.size),
        "mean_set_size": round(float(sizes.mean()), 4),
        "abstain_rate": round(float((sizes != 1).mean()), 4),
        "empty_rate": round(float((sizes == 0).mean()), 4),
        "holds": bool(covered.mean() >= (1.0 - calibration.alpha) - 0.02),
    }


def _main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Fit split-conformal abstention")
    parser.add_argument("--probs", type=Path, required=True,
                        help=".npz with 'probs' (n x classes) and 'labels' (n)")
    parser.add_argument("--alpha", type=float, default=0.1)
    parser.add_argument("--per-class", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("models/conformal.json"))
    parser.add_argument("--check", type=Path,
                        help="second .npz to measure coverage on")
    args = parser.parse_args(argv)

    blob = np.load(args.probs)
    calibration = calibrate(blob["probs"], blob["labels"], alpha=args.alpha,
                            per_class=args.per_class)
    calibration.save(args.out)
    print(json.dumps(calibration.as_dict(), indent=1))
    if args.check:
        other = np.load(args.check)
        print(json.dumps(report(calibration, other["probs"], other["labels"]), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
