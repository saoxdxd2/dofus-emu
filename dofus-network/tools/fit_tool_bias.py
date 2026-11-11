"""Fit a per-tool additive bias on TRAIN, report it on VAL.

The diagnostics narrowed the remaining error to one shape: the model is systematically
conservative on stale observations and under-emits `nav.goto` (77 emissions against 155
rows where it is the best action), losing gain on 79 rows where it is strictly better
than what was chosen. A single vector of 21 numbers can express that, and `_choose_tool`
already applies one (`tool_penalty`, subtracted from the logits).

The discipline that matters here is where the numbers come from. Fitting a bias against
the val set would recover exactly the rows it was fitted on and report a gain that does
not exist; so the bias is fitted on train only and the val numbers are the first time it
is measured against data it has not seen. Coordinate ascent is used because the objective
-- total gain collected -- is piecewise constant in the bias and has no useful gradient.

The objective is gain, not accuracy and not the tie-aware `optimal`: gain is what the run
actually collects, and a bias that buys label agreement while losing gain is the exact
failure mode the consult gate already demonstrated.

Usage: python tools/fit_tool_bias.py [--passes 3]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from agent.weight_space import _onnx_logits, gain_of_row  # noqa: E402

GRID = np.round(np.arange(-1.5, 1.51, 0.1), 2)


def load(path: Path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def gain_matrix(rows, tools):
    """(n_rows, n_tools) of measured gain; a tool absent from the row scores 0.

    This mirrors `gain_of_row`, which is what the metric uses, so the fit optimises the
    same quantity the report measures.
    """
    out = np.zeros((len(rows), len(tools)), dtype=np.float64)
    for i, r in enumerate(rows):
        for j, t in enumerate(tools):
            out[i, j] = gain_of_row(r, t)
    return out


def totals(logits, gains, bias):
    return float(gains[np.arange(len(gains)), np.argmax(logits + bias[None, :], axis=1)].sum())


def fit(logits, gains, passes):
    bias = np.zeros(logits.shape[1], dtype=np.float64)
    best = totals(logits, gains, bias)
    for p in range(passes):
        moved = False
        for t in range(logits.shape[1]):
            keep = bias[t]
            local_best, local_val = best, keep
            for v in GRID:
                bias[t] = v
                s = totals(logits, gains, bias)
                if s > local_best + 1e-12:
                    local_best, local_val = s, v
            bias[t] = local_val
            if local_val != keep:
                moved = True
                best = local_best
        print(f"  pass {p + 1}: train gain {best:.2f}, moved={moved}")
        if not moved:
            break
    return bias, best


def report(name, rows, tools, pred):
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
    print(f"{name:<10}{acc / n:>9.4f}{opt / n:>9.4f}{gain / n:>11.4f}"
          f"{((gain - teacher) / (oracle - teacher)) if oracle != teacher else 0.0:>9.4f}")
    return np.asarray([gain_of_row(r, tools[int(p)]) for r, p in zip(rows, pred.tolist())]), \
        np.asarray([(not (r.get('gains') or {})) or
                    abs(gain_of_row(r, tools[int(p)]) - max((r.get('gains') or {0: 0}).values())) < 1e-9
                    for r, p in zip(rows, pred.tolist())])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/tool_policy.onnx")
    ap.add_argument("--sidecar", default="models/tool_policy.json")
    ap.add_argument("--train", default="data/tool_sft_v3/train.jsonl")
    ap.add_argument("--val", default="data/tool_sft_v3/val.jsonl")
    ap.add_argument("--passes", type=int, default=3)
    args = ap.parse_args()

    sidecar = json.loads(Path(args.sidecar).read_text(encoding="utf-8"))
    tools = list(sidecar["tools"])
    train = load(Path(args.train))
    val = load(Path(args.val))

    print("scoring train ...")
    tr_logits = np.asarray(_onnx_logits(Path(args.model), train, sidecar), dtype=np.float64)
    tr_gains = gain_matrix(train, tools)
    print("fitting per-tool bias on train only:")
    bias, tr_best = fit(tr_logits, tr_gains, args.passes)

    print("\nval (never seen by the fit):")
    print(f"{'':<10}{'acc':>9}{'optimal':>9}{'mean_gain':>11}{'capture':>9}")
    va_logits = np.asarray(_onnx_logits(Path(args.model), val, sidecar), dtype=np.float64)
    base_gain, base_opt = report("baseline", val, tools, np.argmax(va_logits, axis=1))
    adj_gain, adj_opt = report("biased", val, tools, np.argmax(va_logits + bias[None, :], axis=1))

    print("\nper-tool bias (non-zero only):")
    for t, b in sorted(zip(tools, bias), key=lambda x: -abs(x[1])):
        if abs(b) > 1e-9:
            print(f"  {t:<20}{b:+.2f}")

    # Paired on gain: a row counts as improved if the biased arm collected more.
    delta = adj_gain - base_gain
    print(f"\nper-row gain delta: mean {delta.mean():+.4f}, "
          f"improved {(delta > 1e-9).sum()}, worsened {(delta < -1e-9).sum()}, "
          f"unchanged {(np.abs(delta) <= 1e-9).sum()}")
    print(f"optimal delta: {adj_opt.mean() - base_opt.mean():+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
