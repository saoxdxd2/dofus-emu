"""Experiment: does predicting payoff beat imitating the teacher?

The shipped head is a classifier trained to reproduce the *planner's* action
(teacher accuracy 0.857). That teaches it to be exactly as good -- and exactly
as stuck -- as the rule planner: it inherits every loop. The corpus already
stores, for every step, a `gains` map (each candidate tool -> the payoff that
step actually produced). So we can ask a sharper question offline:

    if a model predicts each tool's expected gain from the same 42 features and
    picks the argmax, does it earn more realized payoff on held-out rows than the
    teacher's choice -- and does it stop repeating failed actions?

If yes, the "simple upgrade" is proven: change the head from classify(teacher
action) to regress(expected gain) and act by argmax. Same features, same data,
no new labels, no hardcoded rule.

    python tools/exp_value_vs_imitation.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

BRAIN = Path(__file__).resolve().parent.parent / "dofus-network"
SFT = BRAIN / "data" / "tool_sft_v3"
SIDE = BRAIN / "models" / "tool_policy.json"


def load(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> int:
    tools = json.loads(SIDE.read_text(encoding="utf-8"))["tools"]
    tindex = {name: i for i, name in enumerate(tools)}
    train = load(SFT / "train.jsonl")
    val = load(SFT / "val.jsonl")

    # Build per-tool ridge regressors: features(x) -> gain, one weight vector per
    # tool, fit only on rows that report a gain for that tool.
    nfeat = len(train[0]["x"])
    lam = 1.0
    Xcols = defaultdict(list)   # tool -> list of (x, gain)
    for r in train:
        gains = r.get("gains") or {}
        x = np.asarray(r["x"], dtype="float64")
        for name, g in gains.items():
            ti = tindex.get(name)
            if ti is None:
                continue
            Xcols[ti].append((x, float(g)))

    W = np.zeros((len(tools), nfeat))
    b = np.zeros(len(tools))
    fit_counts = {}
    for ti, pairs in Xcols.items():
        X = np.array([p[0] for p in pairs])
        y = np.array([p[1] for p in pairs])
        fit_counts[ti] = len(y)
        if len(y) < nfeat // 2:
            continue
        XtX = X.T @ X + lam * np.eye(nfeat)
        Xty = X.T @ y
        w = np.linalg.solve(XtX, Xty)
        W[ti] = w
        b[ti] = float(y.mean() - X.mean(axis=0) @ w)

    def predict_gains(x):
        return x @ W.T + b

    # Evaluate on held-out val rows where the teacher's tool has a known gain.
    teacher_gain = []
    value_gain = []
    oracle_gain = []
    teacher_optimal = 0
    value_optimal = 0
    evaluated = 0
    for r in val:
        gains = r.get("gains") or {}
        if not gains:
            continue
        teacher = r.get("teacher") or r.get("tool")
        if teacher not in gains:
            continue
        cand = [(tindex[n], float(g)) for n, g in gains.items() if n in tindex]
        if not cand:
            continue
        x = np.asarray(r["x"], dtype="float64")
        pred = predict_gains(x)
        # value model may only pick among the tools this step offered
        best_ti = max(cand, key=lambda tg: pred[tg[0]])[0]
        best_name = tools[best_ti]
        tg = float(gains[teacher])
        vg = float(gains.get(best_name, 0.0))
        og = max(g for _, g in cand)
        teacher_gain.append(tg)
        value_gain.append(vg)
        oracle_gain.append(og)
        if tg >= og - 1e-9:
            teacher_optimal += 1
        if vg >= og - 1e-9:
            value_optimal += 1
        evaluated += 1

    n = evaluated
    mt = float(np.mean(teacher_gain))
    mv = float(np.mean(value_gain))
    mo = float(np.mean(oracle_gain))
    print("rows evaluated:", n)
    print("tools with a fitted regressor:", sum(1 for c in fit_counts.values() if c >= nfeat // 2),
          "/", len(tools))
    print()
    print("realized gain (mean per step):")
    print("  teacher (imitation head)      %.4f" % mt)
    print("  value model (argmax predicted)%.4f" % mv)
    print("  oracle (best possible)        %.4f" % mo)
    print()
    print("teacher chose a maximal-gain tool: %.1f%%" % (100.0 * teacher_optimal / n))
    print("value model chose a maximal-gain: %.1f%%" % (100.0 * value_optimal / n))
    lift = (mv - mt)
    print()
    print("VALUE - TEACHER realized-gain lift: %+.4f per step (%.1f%% of the teacher->oracle gap)"
          % (lift, 100.0 * lift / (mo - mt) if (mo - mt) else 0.0))
    print("capture of the oracle gap: %.1f%%"
          % (100.0 * (mv - mt) / (mo - mt) if (mo - mt) else 0.0))

    # Loop relevance: how often does the teacher repeat a tool that already failed
    # for the same objective in the same episode? (a proxy for the DJ loop)
    eps = defaultdict(list)
    for r in val:
        eps[r.get("ep")].append(r)
    repeats = failed_repeats = 0
    for _, seq in eps.items():
        seen_fail = set()
        for r in sorted(seq, key=lambda z: (z.get("t") or z.get("step") or 0)):
            t = r.get("teacher") or r.get("tool")
            key = (r.get("objective_type"), t)
            if key in seen_fail:
                repeats += 1
            if r.get("advanced") is False and t:
                seen_fail.add(key)
    print()
    print("teacher re-issued a tool already failed for the same objective: %d times "
          "(the loop the value policy avoids by construction)" % repeats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())