"""Fit a per-tool additive bias on TRAIN, report it on VAL.

The diagnostics narrowed the remaining error to one shape: the model is systematically
conservative on stale observations and under-emits `nav.goto` (77 emissions against 155
rows where it is the best action), losing gain on 79 rows where it is strictly better
than what was chosen. A single vector of 21 numbers can express that, and `_choose_tool`
already applies one (`tool_penalty`, subtracted from the logits).

Units. The graph this scores through already carries the calibration scale
(`ai/dedicated_policy.py` folds `logit_scale` = 1/T into the logits, and the
shipped sidecar says T = 0.25, so every logit is multiplied by 4). The bias is
therefore fitted in post-scale logit units and must be applied to the graph's own
output unchanged. Dividing it by T -- the natural-looking move, since the vector
"belongs to the softmax" -- would multiply it by four and overshoot the fitted
amount by 4x. `NNPlanner.tool_penalty` subtracts from exactly these logits, so the
numbers travel as they are; `--write` stamps the units and the temperature into
the sidecar so no later reader has to guess.

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
import math
import sys
from pathlib import Path
from typing import Dict

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


def sign_test_p(improved: int, worsened: int) -> float:
    """One-sided exact sign test: is `improved > worsened` more than chance?

    The per-row gain delta is mostly zeros (a bias only matters on rows whose
    ranking it changes), so a t-test over all rows would call a 57-up/73-down
    result significant on the strength of the zero mass. The paired sign test
    asks the question that matters -- among the rows it touched, did it help more
    often than it hurt -- and it needs no distributional assumption because the
    delta is a count.
    """
    n = int(improved) + int(worsened)
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(int(improved), n + 1)) / float(2 ** n)
    return min(1.0, tail)


def stable_rewards(logits, gains, bias, passes: int = 2,
                   min_abs: float = 0.2) -> tuple:
    """Zero the entries the two train halves disagree about.

    A coordinate ascent over a piecewise-constant objective can move an entry on
    noise: one row changing hands pays the whole grid step. Fitting each half of
    train separately and keeping only the signs all three agree on removes the
    entries that exist because of a handful of rows -- and `min_abs` removes the
    ones too small to change a decision at a logit scale of four.
    """
    half = len(gains) // 2
    left = fit(logits[:half], gains[:half], passes, verbose=False)[0]
    right = fit(logits[half:], gains[half:], passes, verbose=False)[0]
    out = np.array(bias, dtype=np.float64, copy=True)
    dropped = []
    for index in range(len(out)):
        signs = {float(np.sign(out[index])), float(np.sign(left[index])),
                 float(np.sign(right[index]))}
        if len(signs) != 1 or abs(out[index]) < min_abs:
            if abs(out[index]) > 1e-9:
                dropped.append(index)
            out[index] = 0.0
    return out, dropped


def fit(logits, gains, passes, verbose: bool = True):
    """Coordinate ascent on total gain; the vector is a *reward* per tool.

    Positive means "this tool should be chosen more often". The planner's
    `tool_penalty` is a *cost*, so the sidecar stores the negation -- see
    `bias_block`.
    """
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
        if verbose:
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


def bias_block(tools, costs, temperature: float, train_gain: float, delta,
               model: str, p_value: float = 1.0) -> Dict[str, object]:
    """The sidecar block, stamped with the units it was fitted in.

    `units: post_scale` is the load-bearing field: it says the numbers are already
    in the exported graph's logits (scaled by 1/T), so a consumer applies them
    directly. A reader that divides by `temperature` on top would multiply the
    bias by `graph_scale` again -- 4x for the shipped 0.25 -- which is the mistake
    this schema exists to make impossible to commit by accident.
    """
    delta = np.asarray(delta, dtype=np.float64)
    return {
        "units": "post_scale",
        "sign": "cost",
        "temperature": temperature,
        "graph_scale": round(1.0 / temperature, 6) if temperature else 1.0,
        "bias": {tool: round(float(b), 4) for tool, b in zip(tools, costs)
                 if abs(float(b)) > 1e-9},
        "sign_test_p": round(float(p_value), 4),
        "train_gain": round(float(train_gain), 4),
        "val_gain_delta": round(float(delta.mean()) if delta.size else 0.0, 6),
        "improved": int((delta > 1e-9).sum()),
        "worsened": int((delta < -1e-9).sum()),
        "fit_split": "train",
        "model": model,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/tool_policy.onnx")
    ap.add_argument("--sidecar", default="models/tool_policy.json")
    ap.add_argument("--train", default="data/tool_sft_v3/train.jsonl")
    ap.add_argument("--val", default="data/tool_sft_v3/val.jsonl")
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--write", action="store_true",
                    help="store the fitted bias in the sidecar the planner reads")
    ap.add_argument("--out", default=None,
                    help="sidecar to write (default: the one it was scored with)")
    ap.add_argument("--force", action="store_true",
                    help="write even when the val gain delta is negative")
    args = ap.parse_args()

    sidecar = json.loads(Path(args.sidecar).read_text(encoding="utf-8"))
    tools = list(sidecar["tools"])
    # The graph multiplies by 1/T (the calibration scale). Printed because it is
    # the number every later reader of the bias needs and the one that invites a
    # wrong division: the bias is already in these units.
    temperature = float(sidecar.get("temperature") or 1.0)
    graph_scale = 1.0 / temperature if temperature else 1.0
    print("logit units: post-calibration (graph scale 1/T = %.3f, T = %s); the bias "
          "is fitted and applied here, never divided by T" % (graph_scale, temperature))
    train = load(Path(args.train))
    val = load(Path(args.val))

    print("scoring train ...")
    tr_logits = np.asarray(_onnx_logits(Path(args.model), train, sidecar), dtype=np.float64)
    tr_gains = gain_matrix(train, tools)
    print("fitting per-tool bias on train only:")
    bias, tr_best = fit(tr_logits, tr_gains, args.passes)
    bias, dropped = stable_rewards(tr_logits, tr_gains, bias,
                                   passes=max(2, min(3, args.passes)))
    if dropped:
        print("  dropped %d unstable tool(s): %s"
              % (len(dropped), ", ".join(tools[i] for i in dropped)))

    print("\nval (never seen by the fit):")
    print(f"{'':<10}{'acc':>9}{'optimal':>9}{'mean_gain':>11}{'capture':>9}")
    va_logits = np.asarray(_onnx_logits(Path(args.model), val, sidecar), dtype=np.float64)
    # The fit produces rewards; the planner subtracts costs, so the shipped
    # numbers are the negation. `va_logits - costs` is `va_logits + rewards`.
    costs = -bias
    base_gain, base_opt = report("baseline", val, tools, np.argmax(va_logits, axis=1))
    adj_gain, adj_opt = report("biased", val, tools, np.argmax(va_logits - costs[None, :], axis=1))

    print("\nper-tool bias (non-zero only):")
    for t, b in sorted(zip(tools, bias), key=lambda x: -abs(x[1])):
        if abs(b) > 1e-9:
            print(f"  {t:<20}{b:+.2f}")

    # Paired on gain: a row counts as improved if the biased arm collected more.
    delta = adj_gain - base_gain
    improved = int((delta > 1e-9).sum())
    worsened = int((delta < -1e-9).sum())
    p_value = sign_test_p(improved, worsened)
    print(f"\nper-row gain delta: mean {delta.mean():+.4f}, "
          f"improved {improved}, worsened {worsened}, "
          f"unchanged {(np.abs(delta) <= 1e-9).sum()}")
    print(f"optimal delta: {adj_opt.mean() - base_opt.mean():+.4f}")
    print(f"paired sign test on the touched rows: p={p_value:.3f} "
          "(needs p<0.05 to be more than noise)")

    if not args.write:
        print("\n(not written; pass --write to store it in the sidecar)")
        return 0
    mean_delta = float(delta.mean())
    if mean_delta <= 0.0 and not args.force:
        # A bias that loses gain on the split it never saw is not worth shipping,
        # and overwriting the artifact with it would make every later run worse
        # for a reason nobody would find.
        print("\nrefusing to write: val mean gain delta %+.4f <= 0 "
              "(pass --force to override)" % mean_delta)
        return 3
    if p_value >= 0.05 and not args.force:
        # A positive mean delta is not enough on its own: this fit moved 57 rows
        # up and 73 down, which is noise. Shipping that would make the panel's
        # policy differ from its validation for no measured reason.
        print("\nrefusing to write: the gain delta is not distinguishable from "
              "zero (p=%.3f >= 0.05)" % p_value)
        return 3
    payload = dict(sidecar)
    payload["tool_bias"] = bias_block(tools, costs, temperature, tr_best, delta,
                                      str(args.model), p_value)
    out = Path(args.out or args.sidecar)
    out.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    print("\nwrote %s: %d non-zero tools, units post_scale" %
          (out, len(payload["tool_bias"]["bias"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
