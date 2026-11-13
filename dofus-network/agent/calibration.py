"""Recalibrate the foresight move head so `move_prob` survives a change of era.

The problem
-----------
`agent/planner_nn.py:207` does not merely display `move_prob`; it decides with it:

    top_p = cells.get(top_tool, {}).get("move_prob", 1.0)
    max_p = max(... .get("move_prob", 0.0) ...)
    all_inert = (max_p < 0.20)
    if streak >= 1:
        if top_p < 0.20:
            for alt in ranked[1:]:
                if cells.get(alt_tool, {}).get("move_prob", 0.0) >= 0.25:
                    chosen = alt

Three hardcoded cuts on a probability: 0.20 twice, 0.25 once. They are only meaningful if
0.20 means "one in five". Measured on the real transitions, it does not.

What is actually wrong (measured 2026-11-13)
--------------------------------------------
The artifact already carries a fitted `move_temperature` (`models/foresight_v1`: 2.5002), so
this is *not* a missing-temperature problem, and adding another scalar does nothing. The
defect is that the calibration was fitted on the era it was trained on, and the world moved:

    split   n      base rate   mean predicted   ECE
    train   4099   0.220       0.251            0.103
    val     1087   0.642       0.418            0.241
    test    298    0.466       0.368            0.110

The three splits are strictly chronological (`at` ranges do not overlap), so the base rate of
"this action moved the world" went from 22% to 64% between train and val. A single scalar
fitted on the 22% era cannot express a 64% era; `mean predicted 0.418` against a true 0.642 is
the whole error. On the fit era the model is already excellent (ECE 0.0086); the residual is
entirely drift.

The drift has one cause, and it is not the model
------------------------------------------------
Per tool, on either side of the same boundary:

    tool            fit era (1,485 rows)      report era (730 rows)
    nav.goto        moved 0.173, pred 0.255   moved 0.722, pred 0.385
    quest.advance   moved 0.250, pred 0.249   moved 0.495, pred 0.437

`nav.goto` went from moving the character 17.3% of the time to 72.2% of the time. Navigation
started working; the model was trained while it did not, and it still believes it does not.
That one tool is 44% of the report window and accounts for the calibration collapse. The
"86% of actions are inert" figure in the reviews was largely a harness failure that has since
been fixed, not a model failure -- and the model is the last thing still acting as if it had
not been.

What recalibration buys, measured out of sample
-----------------------------------------------
Newest 1,100 actionable rows (base rate 0.645), calibrator fitted on the 1,100 before them:

    map                  ECE      AUC     log loss   rows below 0.25 that moved
    raw (T = 2.5)        0.2189   0.8534  0.5516     0.345
    temperature refit    0.2140   0.8534  0.5689     -
    Platt                0.1262   0.8603  0.4912     -
    isotonic             0.1966   0.8566  1.0401     -

Two things to read off that table. A refitted **temperature alone makes things worse**
(0.5516 -> 0.5689), because the error is a level shift and a slope cannot move a level; the
intercept is the part that matters, which is why Platt wins and temperature does not.
**Isotonic is the worst of the four out of sample** (log loss 1.04) even though it is perfect
in sample (ECE 0.0000): its blocks are fitted to the noise of the window and do not transfer.
That is the argument against the non-parametric option, made with a number instead of a
preference.

On the deployed artifact the shipped sidecar takes the newest era from ECE 0.2189 to 0.0802
and log loss 0.5516 to 0.4071, and `nav.goto` from a predicted 0.465 to 0.701 against a true
0.868. The number that matters for decisions is the last column: with the raw probability,
`alt_p >= 0.25` -- the preemption test in `planner_nn` -- rejects candidates that go on to
move 34.5% of the time; after calibration that is 9.2%.

Across era boundaries the gain is necessarily smaller, because no map fitted on the past can
know the next era's base rate. A four-fold rolling-origin backtest (`--backtest 4`) gives mean
ECE 0.0863 -> 0.0827 and worst-fold 0.2323 -> 0.1900. The honest claim is therefore narrow:
recalibration pays when it is refitted on the current era, which is what a session-close job
can do, and it does not substitute for retraining the net on current data.

Why Platt and not isotonic
--------------------------
Platt scaling `p = sigmoid(a * z + b)` is strictly monotone in the logit `z`, so it leaves the
**ranking untouched**: AUC is invariant by construction. Isotonic regression is the monotone
projection of a *non*-monotone curve, so it necessarily pools bins -- it can only coarsen the
ordering, and on this corpus it does, at a cost in log loss. Since `agent/ensemble.py` and
`planner_nn` both rank by `move_prob` and only *cut* on it, the transform that preserves rank
and fixes the level is the correct one. Isotonic is implemented and reported anyway, because
"we tried it and it lost by this much" is the only way to stop the next session re-adding it.
The one visible exception -- Platt's AUC reading 0.8603 against the raw 0.8534 -- is not a real
gain: `sigmoid` saturates below z = -60, the raw scores therefore contain ties, and a steeper
map breaks them in a favourable direction. Ties score 0.5, so breaking them can only help.

Per-tool (Mondrian) offsets
---------------------------
A single `(a, b)` assumes every tool's probability is wrong in the same direction. They are
not: `nav.goto` needs an intercept of +1.37 and `quest.advance` only +0.17. A per-tool pair is
fitted where the tool has at least `MIN_GROUP` rows and the global pair is used otherwise, the
same guard `ai/conformal.py` uses for class-conditional coverage. `safe.stop` (which never
moved in either era) gets its own pair and is driven to 0.000, which is the correct answer.

What this module does not do
----------------------------
It does not retrain the net, does not touch `foresight.json`, and does not change any
threshold in `planner_nn.py`. It produces a sidecar `calibration.json` next to the artifact;
`agent/foresight.py` applies it only when that file exists, and `DOFUS_FORESIGHT_CALIBRATION=0`
turns it off. Deleting the sidecar restores the previous behaviour exactly.

It also does not model drift. It is meant to be refitted on the newest rows at session close
(`tools/session_close.py`), which is the only defence against the shift measured above.

    python -m agent.calibration --model models/foresight_v1 --report
    python -m agent.calibration --model models/foresight_v1 --write
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import logging
logger = logging.getLogger(__name__)

METHODS = ("identity", "temperature", "platt", "isotonic")
DEFAULT_METHOD = "platt"
MIN_GROUP = 60
DEFAULT_RIDGE = 1e-3
# A calibrator may flatten a probability but must never reverse one. If the unconstrained fit
# wants `a <= 0` the model is anti-correlated with its own label, and honouring that would make
# `planner_nn` prefer the candidate it is most confident will not work. The objective is convex
# and the feasible set `a >= MIN_SLOPE` is convex, so stopping on that boundary is the
# constrained optimum rather than a hack.
MIN_SLOPE = 1e-3
EPS = 1e-12


# -- metrics ---------------------------------------------------------------

def _as_arrays(probs: Iterable[float], labels: Iterable[float]) -> Tuple[List[float], List[float]]:
    p = [float(v) for v in probs]
    y = [1.0 if v else 0.0 for v in labels]
    if len(p) != len(y):
        raise ValueError("probs and labels differ in length: %d vs %d" % (len(p), len(y)))
    return p, y


def ece(probs: Iterable[float], labels: Iterable[float], bins: int = 10) -> Optional[float]:
    """Expected calibration error, the number the review asked to minimise.

    Empty bins are skipped rather than counted as zero error -- counting them would make a
    degenerate all-0.5 predictor look perfect on a bimodal score distribution.
    """
    p, y = _as_arrays(probs, labels)
    if not p:
        return None
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        picked = [i for i, v in enumerate(p)
                  if (v >= lo and (v < hi if hi < 1.0 else v <= hi))]
        if not picked:
            continue
        mean_p = sum(p[i] for i in picked) / len(picked)
        mean_y = sum(y[i] for i in picked) / len(picked)
        total += (len(picked) / len(p)) * abs(mean_p - mean_y)
    return total


def mce(probs: Iterable[float], labels: Iterable[float], bins: int = 10) -> Optional[float]:
    p, y = _as_arrays(probs, labels)
    if not p:
        return None
    worst = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        picked = [i for i, v in enumerate(p)
                  if (v >= lo and (v < hi if hi < 1.0 else v <= hi))]
        if not picked:
            continue
        worst = max(worst, abs(sum(p[i] for i in picked) / len(picked)
                               - sum(y[i] for i in picked) / len(picked)))
    return worst


def brier(probs: Iterable[float], labels: Iterable[float]) -> Optional[float]:
    p, y = _as_arrays(probs, labels)
    if not p:
        return None
    return sum((a - b) ** 2 for a, b in zip(p, y)) / len(p)


def log_loss(probs: Iterable[float], labels: Iterable[float]) -> Optional[float]:
    """Proper scoring rule; used to *select* between calibrators, because ECE is bin-dependent
    and can be gamed by coarsening."""
    p, y = _as_arrays(probs, labels)
    if not p:
        return None
    total = 0.0
    for a, b in zip(p, y):
        a = min(1.0 - EPS, max(EPS, a))
        total -= b * math.log(a) + (1.0 - b) * math.log(1.0 - a)
    return total / len(p)


def auc(probs: Sequence[float], labels: Sequence[float]) -> Optional[float]:
    """Rank AUC with tie handling; None when one class is absent."""
    pairs = sorted(zip([float(v) for v in probs],
                       [1 if v else 0 for v in labels]))
    pos = sum(1 for _, y in pairs if y)
    neg = len(pairs) - pos
    if not pos or not neg:
        return None
    rank_sum = 0.0
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        average_rank = (i + j) / 2.0 + 1.0
        rank_sum += average_rank * sum(1 for k in range(i, j + 1) if pairs[k][1])
        i = j + 1
    return (rank_sum - pos * (pos + 1) / 2.0) / (pos * neg)


def reliability(probs: Iterable[float], labels: Iterable[float],
                bins: int = 10) -> List[Dict[str, Any]]:
    p, y = _as_arrays(probs, labels)
    rows: List[Dict[str, Any]] = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        picked = [i for i, v in enumerate(p)
                  if (v >= lo and (v < hi if hi < 1.0 else v <= hi))]
        if not picked:
            continue
        rows.append({
            "lo": round(lo, 3),
            "n": len(picked),
            "mean_pred": round(sum(p[i] for i in picked) / len(picked), 4),
            "frac_actual": round(sum(y[i] for i in picked) / len(picked), 4),
        })
    return rows


def evaluate(probs: Iterable[float], labels: Iterable[float]) -> Dict[str, Any]:
    p, y = _as_arrays(probs, labels)
    if not p:
        return {"n": 0}
    rank = auc(p, y)
    return {
        "n": len(p),
        "base_rate": round(sum(y) / len(y), 4),
        "mean_pred": round(sum(p) / len(p), 4),
        "ece": round(ece(p, y) or 0.0, 4),
        "mce": round(mce(p, y) or 0.0, 4),
        "brier": round(brier(p, y) or 0.0, 4),
        "log_loss": round(log_loss(p, y) or 0.0, 4),
        "auc": (round(rank, 4) if rank is not None else None),
    }


# -- transforms ------------------------------------------------------------

def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-min(z, 60.0)))
    e = math.exp(max(z, -60.0))
    return e / (1.0 + e)


def logit(p: float) -> float:
    p = min(1.0 - EPS, max(EPS, p))
    return math.log(p / (1.0 - p))


@dataclass
class Platt:
    """`p = sigmoid(a * z + b)`. Monotone in `z` for `a > 0`, so AUC is untouched."""

    a: float = 1.0
    b: float = 0.0

    def apply_logit(self, z: float) -> float:
        return sigmoid(self.a * z + self.b)

    def apply(self, p: float) -> float:
        return self.apply_logit(logit(p))

    def to_dict(self) -> Dict[str, float]:
        return {"a": round(self.a, 6), "b": round(self.b, 6)}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Platt":
        return cls(a=float(raw.get("a", 1.0)), b=float(raw.get("b", 0.0)))


def fit_platt(logits: Sequence[float], labels: Sequence[float],
              ridge: float = DEFAULT_RIDGE, iterations: int = 60) -> Platt:
    """Two-parameter logistic fit by Newton/IRLS with a backtracking line search.

    Convex in `(a, b)`, so Newton from the identity converges in a handful of steps -- *if*
    every step is accepted only when it lowers the objective. The first version of this
    accepted any step inside a box, and on the real logits (which reach -133) it oscillated and
    returned parameters whose in-sample log loss was 5.65 against 0.84 for the identity. A
    calibrator that is worse than doing nothing in-sample is not a calibrator, so the line
    search is what makes the guarantee "never worse than identity" true rather than hoped for.

    The ridge pulls `(a, b)` toward `(1, 0)` and keeps the fit finite under separation -- a
    tool that never moved has no finite MLE intercept, and returning `b = -800` would make the
    gate permanently inert on the strength of 8 rows.
    """
    z = [float(v) for v in logits]
    y = [1.0 if v else 0.0 for v in labels]
    n = len(z)
    if n == 0:
        return Platt()

    def objective(a: float, b: float) -> float:
        total = ridge * ((a - 1.0) ** 2 + b * b)
        for zi, yi in zip(z, y):
            u = a * zi + b
            # log(1 + e^u) computed stably; a naive -log(sigmoid) overflows at u = -800
            softplus = u if u > 0 else 0.0
            softplus += math.log1p(math.exp(-abs(u)))
            total += (softplus - yi * u) / n
        return total

    a, b = 1.0, 0.0
    current = objective(a, b)
    for _ in range(iterations):
        g0 = ridge * 2.0 * (a - 1.0)
        g1 = ridge * 2.0 * b
        h00 = ridge * 2.0
        h01 = 0.0
        h11 = ridge * 2.0
        for zi, yi in zip(z, y):
            p = sigmoid(a * zi + b)
            w = max(p * (1.0 - p), 1e-12)
            g0 += (p - yi) * zi / n
            g1 += (p - yi) / n
            h00 += w * zi * zi / n
            h01 += w * zi / n
            h11 += w / n
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-16:
            break
        da = (h11 * g0 - h01 * g1) / det
        db = (h00 * g1 - h01 * g0) / det
        if not (math.isfinite(da) and math.isfinite(db)):
            break
        step, accepted = 1.0, False
        for _ in range(30):
            na, nb = a - step * da, b - step * db
            # the slope constraint is enforced *inside* the search, so every accepted point is
            # feasible and the objective is still non-increasing -- clamping afterwards would
            # break the "never worse than identity" guarantee
            if math.isfinite(na) and math.isfinite(nb) and na >= MIN_SLOPE:
                candidate = objective(na, nb)
                if candidate <= current:
                    a, b, current = na, nb, candidate
                    accepted = True
                    break
            step *= 0.5
        if not accepted:
            break  # no descent direction left; the identity (or the last accepted point) stands
        if max(abs(step * da), abs(step * db)) < 1e-10:
            break
    return Platt(a=a, b=b)


def fit_temperature(logits: Sequence[float], labels: Sequence[float],
                    ridge: float = DEFAULT_RIDGE, iterations: int = 60) -> Platt:
    """`p = sigmoid(a * z)` with the intercept pinned at zero -- a pure temperature, `a = 1/T`.

    Kept separate from `fit_platt` because a method called "temperature" that quietly fits an
    intercept is not a temperature, and the whole reason to compare the two is to find out how
    much of the error is a level shift (the intercept) rather than a scale error (the slope).
    """
    z = [float(v) for v in logits]
    y = [1.0 if v else 0.0 for v in labels]
    n = len(z)
    if n == 0:
        return Platt()

    def objective(a: float) -> float:
        total = ridge * (a - 1.0) ** 2
        for zi, yi in zip(z, y):
            u = a * zi
            softplus = (u if u > 0 else 0.0) + math.log1p(math.exp(-abs(u)))
            total += (softplus - yi * u) / n
        return total

    a = 1.0
    current = objective(a)
    for _ in range(iterations):
        g = ridge * 2.0 * (a - 1.0)
        h = ridge * 2.0
        for zi, yi in zip(z, y):
            p = sigmoid(a * zi)
            g += (p - yi) * zi / n
            h += max(p * (1.0 - p), 1e-12) * zi * zi / n
        if abs(h) < 1e-16 or not math.isfinite(g):
            break
        step, accepted = 1.0, False
        for _ in range(30):
            na = a - step * g / h
            if math.isfinite(na) and na >= MIN_SLOPE and objective(na) <= current:
                a, current, accepted = na, objective(na), True
                break
            step *= 0.5
        if not accepted or abs(step * g / h) < 1e-10:
            break
    return Platt(a=a, b=0.0)


@dataclass
class Isotonic:
    """Pool-adjacent-violators step function over the probability, evaluated by block mean."""

    blocks: List[Tuple[float, float, float]] = field(default_factory=list)

    def apply(self, p: float) -> float:
        if not self.blocks:
            return p
        p = min(1.0, max(0.0, float(p)))
        for lo, hi, mean in self.blocks:
            if p <= hi:
                return mean
        return self.blocks[-1][2]

    def to_dict(self) -> Dict[str, Any]:
        return {"blocks": [[round(lo, 6), round(hi, 6), round(m, 6)] for lo, hi, m in self.blocks]}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Isotonic":
        return cls(blocks=[(float(a), float(b), float(c)) for a, b, c in raw.get("blocks") or []])


def fit_isotonic(probs: Sequence[float], labels: Sequence[float]) -> Isotonic:
    """PAV on (score, label) pairs. Ties are pooled first: two rows with the same score must
    share a fitted value or the result is not a function of the input."""
    pairs: Dict[float, List[float]] = {}
    for p, y in zip(probs, labels):
        cell = pairs.setdefault(min(1.0, max(0.0, float(p))), [0.0, 0.0])
        cell[0] += 1.0
        cell[1] += 1.0 if y else 0.0
    ordered = sorted(pairs.items())
    blocks: List[List[float]] = []  # [lo, hi, weight, positives]
    for score, (w, pos) in ordered:
        blocks.append([score, score, w, pos])
        while len(blocks) > 1:
            prev, last = blocks[-2], blocks[-1]
            if prev[3] / prev[2] > last[3] / last[2]:
                prev[1] = last[1]
                prev[2] += last[2]
                prev[3] += last[3]
                blocks.pop()
            else:
                break
    return Isotonic(blocks=[(lo, hi, pos / w) for lo, hi, w, pos in blocks])


# -- the fitted bundle -----------------------------------------------------

@dataclass
class Calibrator:
    """A global transform plus per-group overrides, applied on the logit scale."""

    method: str = "identity"
    global_: Optional[Platt] = None
    isotonic: Optional[Isotonic] = None
    groups: Dict[str, Platt] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    def apply_logit(self, z: float, group: Optional[str] = None) -> float:
        if self.method == "identity":
            return sigmoid(z)
        if self.method == "isotonic":
            base = sigmoid(z)
            return self.isotonic.apply(base) if self.isotonic else base
        model = self.groups.get(group) if group else None
        if model is None:
            model = self.global_
        if model is None:
            return sigmoid(z)
        return model.apply_logit(z)

    def apply(self, p: float, group: Optional[str] = None) -> float:
        return self.apply_logit(logit(p), group)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "method": self.method,
            "global": self.global_.to_dict() if self.global_ else None,
            "isotonic": self.isotonic.to_dict() if self.isotonic else None,
            "groups": {k: v.to_dict() for k, v in sorted(self.groups.items())},
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Calibrator":
        method = str(raw.get("method") or "identity")
        if method not in METHODS:
            method = "identity"
        return cls(
            method=method,
            global_=(Platt.from_dict(raw["global"]) if raw.get("global") else None),
            isotonic=(Isotonic.from_dict(raw["isotonic"]) if raw.get("isotonic") else None),
            groups={k: Platt.from_dict(v) for k, v in (raw.get("groups") or {}).items()},
            meta=dict(raw.get("meta") or {}),
        )


def fit(logits: Sequence[float], labels: Sequence[float],
        groups: Optional[Sequence[Optional[str]]] = None,
        method: str = DEFAULT_METHOD, min_group: int = MIN_GROUP,
        ridge: float = DEFAULT_RIDGE) -> Calibrator:
    """Fit one calibrator. `groups` is per-row (usually the tool name)."""
    z = [float(v) for v in logits]
    y = [1.0 if v else 0.0 for v in labels]
    if not z:
        return Calibrator(method="identity")
    if method == "identity":
        return Calibrator(method="identity")
    if method == "isotonic":
        probs = [sigmoid(v) for v in z]
        return Calibrator(method="isotonic", isotonic=fit_isotonic(probs, y),
                          meta={"rows": len(z)})
    if method == "temperature":
        return Calibrator(method="temperature", global_=fit_temperature(z, y, ridge=ridge),
                          meta={"rows": len(z), "intercept_pinned": True})
    if method != "platt":
        raise ValueError("unknown calibration method %r (known: %s)"
                         % (method, ", ".join(METHODS)))
    base = fit_platt(z, y, ridge=ridge)
    table: Dict[str, Platt] = {}
    if groups is not None:
        if len(groups) != len(z):
            raise ValueError("groups and logits differ in length")
        buckets: Dict[str, List[int]] = {}
        for i, g in enumerate(groups):
            if g is None:
                continue
            buckets.setdefault(str(g), []).append(i)
        for name, idx in buckets.items():
            if len(idx) < min_group:
                continue  # too few rows to say anything the global fit does not already say
            table[name] = fit_platt([z[i] for i in idx], [y[i] for i in idx], ridge=ridge)
    return Calibrator(method="platt", global_=base, groups=table,
                      meta={"rows": len(z), "min_group": min_group, "ridge": ridge})


def save(path: Path, cal: Calibrator) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cal.to_dict(), indent=1), encoding="utf-8")
    return path


def load(path: Path) -> Optional[Calibrator]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return Calibrator.from_dict(raw)
    except (TypeError, ValueError):
        return None


# -- CLI -------------------------------------------------------------------

def _score_rows(net: Any, rows: Sequence[Dict[str, Any]]
                ) -> Tuple[List[float], List[float], List[Optional[str]]]:
    """Raw logits, labels and tool names for the actionable rows of a transition corpus."""
    import numpy as np
    vectors, labels, groups = [], [], []
    for row in rows:
        vec = net.vector(row["x"], row["tool"])
        if vec is None:
            continue
        vectors.append(vec)
        labels.append(1.0 if row.get("moved") else 0.0)
        groups.append(row.get("tool"))
    if not vectors:
        return [], [], []
    logits = net.move_logits(np.asarray(vectors, dtype=np.float32))
    return [float(v) for v in logits], labels, groups


def backtest(logits: Sequence[float], labels: Sequence[float],
             groups: Sequence[Optional[str]], folds: int = 4, window: int = 2,
             min_group: int = MIN_GROUP) -> Dict[str, Any]:
    """Rolling-origin backtest: fit on the recent past, report on the next block.

    This is the only protocol that answers the question the deployment asks. A single
    fit/report split measures one shift; the daemon refits at every session close, so what
    matters is whether a calibrator fitted on the *immediately preceding* era holds up in the
    next one. The window slides rather than expands because the whole point is to forget the
    era whose behaviour no longer exists.
    """
    n = len(logits)
    if n < (folds + 1) * 50:
        return {"folds": 0, "note": "not enough rows for %d folds" % folds}
    size = n // (folds + 1)
    results: Dict[str, List[Dict[str, Any]]] = {m: [] for m in METHODS}
    per_fold: List[Dict[str, Any]] = []
    for i in range(1, folds + 1):
        lo = max(0, (i - window) * size)
        hi = i * size
        fit_slice = slice(lo, hi)
        rep_slice = slice(hi, (i + 1) * size)
        fz, fy, fg = list(logits[fit_slice]), list(labels[fit_slice]), list(groups[fit_slice])
        rz, ry, rg = list(logits[rep_slice]), list(labels[rep_slice]), list(groups[rep_slice])
        if not rz:
            continue
        fold: Dict[str, Any] = {
            "fold": i,
            "fit_rows": len(fz),
            "report_rows": len(rz),
            "report_base_rate": round(sum(ry) / len(ry), 4),
        }
        for method in METHODS:
            cal = fit(fz, fy, fg, method=method, min_group=min_group)
            p = [cal.apply_logit(v, g) for v, g in zip(rz, rg)]
            metrics = evaluate(p, ry)
            results[method].append(metrics)
            fold[method] = {"ece": metrics["ece"], "log_loss": metrics["log_loss"],
                            "auc": metrics["auc"]}
        per_fold.append(fold)
    summary: Dict[str, Any] = {}
    for method, cells in results.items():
        if not cells:
            continue
        summary[method] = {
            "mean_ece": round(sum(c["ece"] for c in cells) / len(cells), 4),
            "worst_ece": round(max(c["ece"] for c in cells), 4),
            "mean_log_loss": round(sum(c["log_loss"] for c in cells) / len(cells), 4),
            "mean_auc": round(sum(c["auc"] for c in cells if c["auc"] is not None)
                              / max(1, sum(1 for c in cells if c["auc"] is not None)), 4),
        }
    return {"folds": len(per_fold), "summary": summary, "per_fold": per_fold}


def _read_rows(paths: Sequence[Path]) -> List[Dict[str, Any]]:
    from ai.train_foresight import load_live
    return load_live(list(paths))["rows"]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", type=Path, default=Path("models") / "foresight_v1")
    ap.add_argument("--data", type=Path, default=Path("data") / "transitions")
    ap.add_argument("--split-at", type=float, default=0.7,
                    help="fraction of rows, in `at` order, used to fit the calibrator")
    ap.add_argument("--method", default=DEFAULT_METHOD, choices=list(METHODS))
    ap.add_argument("--min-group", type=int, default=MIN_GROUP)
    ap.add_argument("--backtest", type=int, default=0,
                    help="run a rolling-origin backtest with this many folds instead")
    ap.add_argument("--folds-window", type=int, default=2,
                    help="how many blocks of the past each backtest fold fits on")
    ap.add_argument("--recent", type=int, default=2500,
                    help="rows, counted backwards from the newest, that --write fits on")
    ap.add_argument("--write", action="store_true", help="write calibration.json beside the model")
    ap.add_argument("--report", action="store_true", help="print the fit/report comparison")
    args = ap.parse_args(argv)

    from agent.foresight import load as load_net
    net = load_net(args.model / "foresight.json")
    if net is None:
        logger.debug("no artifact at %s" % (args.model / "foresight.json"), file=sys.stderr)
        return 2
    net.attach_calibration(None)  # fit against the raw logits, never the sidecar's output

    rows = _read_rows([args.data / ("%s.jsonl" % name) for name in ("train", "val", "test")])
    rows = [r for r in rows if r.get("at")]
    rows.sort(key=lambda r: float(r["at"]))
    if len(rows) < 200:
        logger.debug("not enough actionable rows (%d) to calibrate" % len(rows), file=sys.stderr)
        return 2
    logits, labels, groups = _score_rows(net, rows)
    if not logits:
        logger.debug("the artifact scored no rows", file=sys.stderr)
        return 2

    if args.backtest:
        logger.debug(json.dumps(backtest(logits, labels, groups, folds=args.backtest,
                                  window=args.folds_window, min_group=args.min_group),
                         indent=1))
        return 0

    cut = int(len(logits) * args.split_at)
    fit_z, fit_y, fit_g = logits[:cut], labels[:cut], groups[:cut]
    rep_z, rep_y, rep_g = logits[cut:], labels[cut:], groups[cut:]

    base_fit = [sigmoid(v) for v in fit_z]
    base_rep = [sigmoid(v) for v in rep_z]
    out: Dict[str, Any] = {
        "model": str(args.model),
        "rows": {"fit": len(fit_z), "report": len(rep_z)},
        "baseline": {"fit": evaluate(base_fit, fit_y), "report": evaluate(base_rep, rep_y)},
    }
    fitted: Dict[str, Calibrator] = {}
    for method in METHODS:
        cal = fit(fit_z, fit_y, fit_g, method=method, min_group=args.min_group)
        fitted[method] = cal
        p_rep = [cal.apply_logit(v, g) for v, g in zip(rep_z, rep_g)]
        out.setdefault("candidates", {})[method] = {
            "report": evaluate(p_rep, rep_y),
            "groups": sorted(cal.groups),
            "global": cal.global_.to_dict() if cal.global_ else None,
        }
    # selection uses an inner temporal holdout *inside* the fit window. Selecting on the fit
    # window's own loss would always pick the most flexible method (isotonic), and selecting on
    # the report window would make the quoted number optimistic.
    inner = int(len(fit_z) * 0.8)
    best, best_loss = "identity", float("inf")
    for method in METHODS:
        cal = fit(fit_z[:inner], fit_y[:inner], fit_g[:inner],
                  method=method, min_group=args.min_group)
        loss = log_loss([cal.apply_logit(v, g) for v, g in zip(fit_z[inner:], fit_g[inner:])],
                        fit_y[inner:])
        if loss is not None and loss < best_loss:
            best, best_loss = method, loss
    out["selected_on_inner_holdout"] = best
    chosen = fitted[args.method]
    shipped_probs = [chosen.apply_logit(v, g) for v, g in zip(rep_z, rep_g)]
    out["shipped"] = {"method": args.method,
                      "report": evaluate(shipped_probs, rep_y),
                      "reliability_report": reliability(shipped_probs, rep_y)}
    logger.debug(json.dumps(out, indent=1))
    if args.write:
        # The shipped artifact must be fitted on the *recent* era, not the whole corpus. The
        # corpus contains an era in which `nav.goto` moved the character 17.3% of the time and a
        # later one in which it moved 72.2%; an unweighted fit over both lands between them and
        # ships a correction for a world that no longer exists.
        tail = min(args.recent, len(logits))
        rz, ry, rg = logits[-tail:], labels[-tail:], groups[-tail:]
        shipped = fit(rz, ry, rg, method=args.method, min_group=args.min_group)
        shipped.meta.update({
            "fitted_rows": tail,
            "window_start_at": round(float(rows[-tail]["at"]), 3),
            "window_end_at": round(float(rows[-1]["at"]), 3),
            "window_base_rate": round(sum(ry) / len(ry), 4),
            "groups": sorted(shipped.groups),
            "backtest": (backtest(logits, labels, groups, folds=4,
                                  window=args.folds_window, min_group=args.min_group)
                         .get("summary") if len(logits) >= 2500 else None),
        })
        path = save(args.model / "calibration.json", shipped)
        logger.debug("wrote %s (fitted on the last %d rows, base rate %.3f)"
              % (path, tail, sum(ry) / len(ry)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
