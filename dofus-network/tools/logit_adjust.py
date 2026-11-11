"""Does removing the label prior from the decision rule recover the read tools?

The corpus is 95.8% four action tools; `quest.list` is 0.40%, an 81x ratio against
`quest.advance`. A cross-entropy fit reproduces that prior, so the model almost never
emits a read tool even on the states where the read tool is the correct answer --
those four tools are 3.5% of labels but a third of all suboptimal decisions.

Menon et al.'s logit adjustment is the principled version of "fix the prior": subtract
tau * log pi(y) from the logits at decision time. It leaves the fitted function alone and
only moves the decision boundary, so unlike training-time class weighting it does not
distort the probabilities the model was calibrated on -- which matters here, because
temperature scaling was fitted on those probabilities.

tau = 0 is the shipped behaviour and is included as the control, so the sweep reports
the delta against the thing actually running.

Usage: python tools/logit_adjust.py [--model models/tool_policy.onnx]
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

from agent.weight_space import _onnx_logits, gain_of_row  # noqa: E402

TAUS = (0.0, 0.25, 0.5, 0.75, 1.0, 1.25)


def load(path: Path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def score(rows, tools, pred):
    acc = opt = 0
    gain = 0.0
    teacher_gain = 0.0
    oracle = 0.0
    for r, p in zip(rows, pred.tolist()):
        tool = tools[int(p)]
        gains = r.get("gains") or {}
        acc += tool == r.get("tool")
        gain += gain_of_row(r, tool)
        teacher_gain += gain_of_row(r, str(r.get("teacher") or ""))
        oracle += gain_of_row(r, str(r.get("tool") or ""))
        if gains:
            opt += abs(gain_of_row(r, tool) - max(gains.values())) < 1e-9
        else:
            opt += 1
    n = len(rows)
    span = (oracle - teacher_gain) / n
    return {"accuracy": acc / n, "optimal": opt / n,
            "mean_gain": gain / n,
            "capture": ((gain - teacher_gain) / (oracle - teacher_gain))
            if oracle != teacher_gain else 0.0}


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

    counts = Counter(r.get("tool") for r in train)
    n = sum(counts.values())
    # Floor at 1/n so an unseen tool cannot produce log(0); it would otherwise get an
    # infinite bonus and be emitted on every row.
    prior = np.asarray([max(counts.get(t, 0), 1) / n for t in tools])
    log_prior = np.log(prior)

    logits = np.asarray(_onnx_logits(Path(args.model), val, sidecar), dtype=np.float64)
    print(f"{'tau':>6}{'acc':>9}{'optimal':>9}{'mean_gain':>11}{'capture':>9}"
          f"{'read_emits':>12}")
    print("-" * 57)
    rows_out = []
    read = {"quest.list", "inv.list", "state.snapshot", "inv.sell"}
    for tau in TAUS:
        adj = logits - tau * log_prior[None, :]
        pred = np.argmax(adj, axis=1)
        s = score(val, tools, pred)
        read_emits = sum(1 for p in pred.tolist() if tools[int(p)] in read)
        rows_out.append({"tau": tau, **{k: round(v, 4) for k, v in s.items()},
                         "read_emits": read_emits})
        print(f"{tau:>6.2f}{s['accuracy']:>9.4f}{s['optimal']:>9.4f}"
              f"{s['mean_gain']:>11.4f}{s['capture']:>9.4f}{read_emits:>12}")

    best = max(rows_out, key=lambda r: r["optimal"])
    print(f"\nbest by optimality: tau={best['tau']} -> {best['optimal']:.4f} "
          f"(tau=0 is {rows_out[0]['optimal']:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
