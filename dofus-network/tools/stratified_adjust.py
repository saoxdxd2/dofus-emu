"""Does correcting the prior *within* each observation stratum beat correcting it globally?

The global sweep (tools/logit_adjust.py) failed, and the reason is visible in the
strata: the label prior is not one distribution. On a fresh observation the top label is
nav.goto; on a stale one it is quest.advance at 53%. A single tau has to split the
difference and so fixes neither -- which is exactly what happened, with accuracy falling
1.6pp while optimality stayed flat.

Stratified adjustment fits a prior per `obs_stale` value and applies the matching one to
each row. That is the same estimator with the conditioning the data actually has. If this
also fails, the prior is not the mechanism and the remaining error is data or capacity,
not a decision rule -- which is worth knowing before spending a retrain on it.

tau = 0 is the shipped behaviour, included so the table reports against what runs.

Usage: python tools/stratified_adjust.py [--model models/tool_policy.onnx]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from agent.features import FEATURE_NAMES  # noqa: E402
from agent.weight_space import _onnx_logits, gain_of_row  # noqa: E402

STALE = FEATURE_NAMES.index("obs_stale")
TAUS = (0.0, 0.15, 0.25, 0.35, 0.5)


def load(path: Path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def stratum(row) -> int:
    return 1 if row["x"][STALE] > 0.5 else 0


def evaluate(rows, tools, pred):
    acc = opt = 0
    gain = teacher = oracle = 0.0
    for r, p in zip(rows, pred.tolist()):
        tool = tools[int(p)]
        gains = r.get("gains") or {}
        acc += tool == r.get("tool")
        gain += gain_of_row(r, tool)
        teacher += gain_of_row(r, str(r.get("teacher") or ""))
        oracle += gain_of_row(r, str(r.get("tool") or ""))
        opt += (not gains) or abs(gain_of_row(r, tool) - max(gains.values())) < 1e-9
    n = len(rows)
    return {"accuracy": acc / n, "optimal": opt / n, "mean_gain": gain / n,
            "capture": ((gain - teacher) / (oracle - teacher))
            if oracle != teacher else 0.0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/tool_policy.onnx")
    ap.add_argument("--sidecar", default="models/tool_policy.json")
    ap.add_argument("--train", default="data/tool_sft_v3/train.jsonl")
    ap.add_argument("--val", default="data/tool_sft_v3/val.jsonl")
    args = ap.parse_args()

    sidecar = json.loads(Path(args.sidecar).read_text(encoding="utf-8"))
    tools = list(sidecar["tools"])
    train = load(Path(args.train))
    val = load(Path(args.val))

    # One prior per stratum, floored at 1/n so an unseen tool cannot get an infinite
    # bonus and be emitted on every row of that stratum.
    log_prior = {}
    for s in (0, 1):
        sub = [r for r in train if stratum(r) == s]
        counts = Counter(r.get("tool") for r in sub)
        n = sum(counts.values())
        p = np.asarray([max(counts.get(t, 0), 1) / n for t in tools])
        log_prior[s] = np.log(p)
        print(f"stratum obs_stale={s}: train rows {n}, top "
              f"{', '.join(f'{t} {c / n:.0%}' for t, c in counts.most_common(3))}")

    logits = np.asarray(_onnx_logits(Path(args.model), val, sidecar), dtype=np.float64)
    strata = np.asarray([stratum(r) for r in val])

    print(f"\n{'tau':>6}{'acc':>9}{'optimal':>9}{'mean_gain':>11}{'capture':>9}")
    print("-" * 44)
    results = []
    for tau in TAUS:
        adj = logits.copy()
        for s in (0, 1):
            m = strata == s
            adj[m] -= tau * log_prior[s][None, :]
        s = evaluate(val, tools, np.argmax(adj, axis=1))
        results.append({"tau": tau, **{k: round(v, 4) for k, v in s.items()}})
        print(f"{tau:>6.2f}{s['accuracy']:>9.4f}{s['optimal']:>9.4f}"
              f"{s['mean_gain']:>11.4f}{s['capture']:>9.4f}")

    base = results[0]
    best = max(results, key=lambda r: r["optimal"])
    print(f"\nbaseline optimal {base['optimal']:.4f} -> best {best['optimal']:.4f} "
          f"at tau={best['tau']}")
    print("verdict:", "prior correction helps" if best["optimal"] > base["optimal"] + 1e-4
          else "prior is not the mechanism")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
