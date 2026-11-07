"""Can the live log answer an off-policy question? Measure the overlap, not the claim.

The corpus is observational: at each state the teacher took one action. To ask
"what would another policy score" the standard estimator is self-normalised
importance sampling (SNIPS),

    V(pi_t) = sum_i w_i y_i / sum_i w_i,   w_i = pi_t(a_i|x_i) / pi_b(a_i|x_i)

with pi_b estimated from the log itself and cross-fitted by segment so no row is
scored by a model that saw it. The two numbers that decide whether V is worth
reading are the effective sample size,

    ESS = (sum_i w_i)^2 / sum_i w_i^2

and the tail of the weights. A policy that differs from the behaviour in states
where the behaviour is confident gets a handful of huge weights and an estimate
with no useful variance; a policy that asks to act where the teacher always read
gets no support at all.

This probe reports those numbers for three targets on the live corpus:

  * uniform      every tool equally likely (the weakest possible behaviour model)
  * uniform-action  every non-read tool equally likely
  * no-read      the teacher's own distribution, renormalised over non-read tools
                 (this is the "stop spending 44% of steps on reads" question)

It does not train anything and it never claims the estimate is causal: the
outcome model is assumed unchanged by the policy, which is exactly the assumption
the overlap numbers let a reader judge.

    python tools/ope_probe.py
    python tools/ope_probe.py --json --folds 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

READ_TOOLS = {"state.snapshot", "quest.list", "inv.list", "fight.state",
              "fight.plan", "fight.estimate", "hdv.scan", "hdv.prices",
              "knowledge.search", "fight.pass"}


def load_rows(paths: Sequence[Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row.get("x"), list) and len(row["x"]) >= 8:
                    rows.append(row)
    return rows


def fold_of(segment: Any, folds: int) -> int:
    digest = hashlib.sha1(str(segment).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % max(1, folds)


def cross_fit_propensity(rows: List[Dict[str, Any]], tools: List[str],
                         folds: int) -> Tuple[List[List[float]], Dict[str, Any]]:
    """Out-of-fold P(tool|x) for every row, plus the model's own accuracy."""
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    index = {name: i for i, name in enumerate(tools)}
    xs = np.asarray([row["x"] for row in rows], dtype=np.float64)
    labels = np.asarray([index.get(str(row.get("tool")), -1) for row in rows])
    known = labels >= 0
    proba = np.zeros((len(rows), len(tools)), dtype=np.float64)
    hits = 0
    scored = 0
    for fold in range(folds):
        test = np.asarray([fold_of(row.get("segment"), folds) == fold for row in rows])
        train = ~test & known
        if not train.any() or not test.any():
            continue
        model = make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=2000, C=1.0))
        model.fit(xs[train], labels[train])
        # A fold without one rare tool fits fewer columns; scatter them back so
        # every row is scored on the same tool axis. A tool absent from a fold's
        # training data gets a floor probability, which is the honest reading of
        # "no support" and keeps the importance weight finite.
        fold_proba = model.predict_proba(xs[test])
        block = np.full((int(test.sum()), len(tools)), 1e-6, dtype=np.float64)
        for column, label in enumerate(model.classes_):
            block[:, int(label)] = fold_proba[:, column]
        proba[test] = block
        picked = proba[test].argmax(axis=1)
        hits += int((picked == labels[test]).sum())
        scored += int(test.sum())
    return proba.tolist(), {"accuracy": round(hits / max(1, scored), 4),
                            "rows_scored": scored}


def snips(y: List[float], w: List[float]) -> Optional[float]:
    total = sum(w)
    if total <= 0:
        return None
    return sum(wi * yi for wi, yi in zip(w, y)) / total


def ess(w: List[float]) -> float:
    squared = sum(wi * wi for wi in w)
    return (sum(w) ** 2 / squared) if squared else 0.0


def bootstrap_segments(rows: List[Dict[str, Any]], y: List[float], w: List[float],
                       rounds: int = 300, seed: int = 0) -> Optional[Tuple[float, float]]:
    by_seg: Dict[Any, List[int]] = {}
    for i, row in enumerate(rows):
        by_seg.setdefault(row.get("segment"), []).append(i)
    segments = list(by_seg.values())
    if len(segments) < 3:
        return None
    rng = random.Random(seed)
    vals: List[float] = []
    for _ in range(rounds):
        picked = [rng.choice(segments) for _ in segments]
        idx = [i for group in picked for i in group]
        value = snips([y[i] for i in idx], [w[i] for i in idx])
        if value is not None:
            vals.append(value)
    if len(vals) < 30:
        return None
    vals.sort()
    return (round(vals[int(0.025 * len(vals))], 4),
            round(vals[int(0.975 * len(vals)) - 1], 4))


def cluster_stats(rows: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    """How much independent evidence a clustered binary label actually carries."""
    import math

    by_seg: Dict[Any, List[Dict[str, Any]]] = {}
    for row in rows:
        by_seg.setdefault(row.get("segment"), []).append(row)
    clusters = [group for group in by_seg.values() if len(group) > 1]
    n = sum(len(group) for group in clusters)
    if not clusters or not n:
        return {"n": 0}
    mean = sum(1 for group in clusters for row in group if row.get(key)) / n
    grand = mean
    ssb = sum(len(group) * (sum(1 for row in group if row.get(key)) / len(group)
                            - grand) ** 2 for group in clusters)
    ssw = sum(((1 if row.get(key) else 0)
               - sum(1 for other in group if other.get(key)) / len(group)) ** 2
              for group in clusters for row in group)
    k = len(clusters)
    msb = ssb / max(1, k - 1)
    msw = ssw / max(1, n - k)
    size = n / k
    icc = (msb - msw) / (msb + (size - 1) * msw) if (msb + (size - 1) * msw) else 0.0
    icc = max(0.0, min(1.0, icc))
    n_eff = n / (1 + (size - 1) * icc)
    # Minimum detectable relative change, two-proportion, 80% power, 5% two-sided,
    # with the effective (not nominal) sample size per arm.
    rel = None
    if 0 < mean < 1 and n_eff > 1:
        delta = (1.96 + 0.84) * math.sqrt(2 * mean * (1 - mean) / n_eff)
        rel = round(100.0 * delta / mean, 1)
    return {
        "n": n, "clusters": k, "mean_cluster": round(size, 1),
        "rate": round(mean, 5), "icc": round(icc, 4),
        "n_eff": round(n_eff, 1),
        "ci95_half_width": round(196.0 * math.sqrt(mean * (1 - mean) / n_eff), 3),
        "mde_relative_pct": rel,
    }


def target_weights(rows: List[Dict[str, Any]], proba: List[List[float]],
                   tools: List[str], target: str,
                   clip: Optional[float] = None) -> List[float]:
    """pi_t(a|x)/pi_b(a|x) for the row's taken tool under the named target."""
    read_index = {i for i, name in enumerate(tools) if name in READ_TOOLS}
    action_index = [i for i in range(len(tools)) if i not in read_index]
    weights: List[float] = []
    for row, probs in zip(rows, proba):
        taken = tools.index(str(row.get("tool")))
        p_b = max(1e-6, probs[taken])
        if target == "uniform":
            p_t = 1.0 / len(tools)
        elif target == "uniform-action":
            p_t = (1.0 / len(action_index)) if taken in action_index else 0.0
        elif target == "no-read":
            p_read = sum(probs[i] for i in read_index)
            p_t = 0.0 if taken in read_index else probs[taken] / max(1e-6, 1.0 - p_read)
        else:
            raise SystemExit("unknown target %s" % target)
        weight = p_t / p_b
        if clip is not None:
            weight = min(weight, clip)
        weights.append(weight)
    return weights


def probe(data: Path, folds: int = 5) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for name in ("train", "val", "test"):
        rows.extend(load_rows([data / ("%s.jsonl" % name)]))
    if not rows:
        raise SystemExit("no rows under %s" % data)
    tools = sorted({str(r.get("tool")) for r in rows if r.get("tool")})
    proba, model = cross_fit_propensity(rows, tools, folds)
    taken = [proba[i][tools.index(str(row.get("tool")))] for i, row in enumerate(rows)]
    top = [max(p) for p in proba]
    result: Dict[str, Any] = {
        "data": str(data), "rows": len(rows), "tools": tools,
        "propensity": dict(model, mean_taken_prob=round(sum(taken) / len(taken), 4),
                           mean_max_prob=round(sum(top) / len(top), 4),
                           share_confident_90=round(
                               sum(1 for p in top if p >= 0.9) / len(top), 4)),
        "targets": {},
    }
    result["power"] = {outcome: cluster_stats(rows, outcome)
                       for outcome in ("moved", "progress", "advance")}
    for outcome in ("moved", "progress", "advance"):
        y = [1.0 if row.get(outcome) else 0.0 for row in rows]
        base: Dict[str, Any] = {"observed_rate": round(sum(y) / len(y), 4)}
        for target in ("uniform", "uniform-action", "no-read"):
            for clip in (None, 20.0):
                w = target_weights(rows, proba, tools, target, clip)
                label = target if clip is None else "%s/clip20" % target
                nonzero = [wi for wi in w if wi > 0]
                base[label] = {
                    "snips": _r(snips(y, w)),
                    "ci95": bootstrap_segments(rows, y, w),
                    "ess": round(ess(w), 1),
                    "ess_pct": round(100.0 * ess(w) / len(rows), 2),
                    "max_weight": round(max(w), 1) if w else None,
                    "rows_with_support": len(nonzero),
                    "share_over_20": round(sum(1 for wi in w if wi > 20) / len(w), 4),
                }
        result["targets"][outcome] = base
    return result


def _r(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 4)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=REPO_ROOT / "data" / "transitions_v2")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    result = probe(args.data, args.folds)
    if args.json:
        print(json.dumps(result, indent=1))
        return 0
    prop = result["propensity"]
    print("rows %d  propensity out-of-fold accuracy %.3f  mean P(taken) %.3f  "
          "mean max P %.3f  confident(>=0.9) %.1f%%"
          % (result["rows"], prop["accuracy"], prop["mean_taken_prob"],
             prop["mean_max_prob"], 100 * prop["share_confident_90"]))
    print("power (cluster = segment; MDE = smallest change visible at 80% power):")
    for outcome, stat in result["power"].items():
        print("   %-9s n %d  segs %d  rate %.5f  ICC %.3f  n_eff %.0f  "
              "CI95 +/-%.3fpp  MDE %s%%"
              % (outcome, stat["n"], stat["clusters"], stat["rate"], stat["icc"],
                 stat["n_eff"], stat["ci95_half_width"], stat["mde_relative_pct"]))
    for outcome, targets in result["targets"].items():
        print("== %s (observed %.4f)" % (outcome, targets["observed_rate"]))
        for name, stat in targets.items():
            if name == "observed_rate":
                continue
            print("   %-22s SNIPS %-7s CI %-16s ESS %8.1f (%5.2f%%)  max w %7s  "
                  "w>20 %5.1f%%"
                  % (name, stat["snips"], str(stat["ci95"]), stat["ess"],
                     stat["ess_pct"], stat["max_weight"], 100 * stat["share_over_20"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
