"""Does the consult gate still pay for itself on the dedicated model?

The gate was fitted for the 42-wide head, where the model was weak enough that
handing low-margin rows to the rule planner rescued more than it broke. The
dedicated model is stronger, so the same trade may have inverted: deferring to a
weaker advisor can raise agreement with that advisor while lowering the gain the
run actually collects.

Label accuracy and task optimality therefore have to be reported together, and
the delta between gated and ungated has to be paired -- the two readings are made
on the same rows, so unpaired intervals overstate the uncertainty and a 0.4pp
swing reads as noise or as signal depending on which one you pick.

Usage: python tools/gate_tradeoff.py [--floor 1.05] [--model models/tool_policy.onnx]
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

from agent.weight_space import (  # noqa: E402
    _onnx_logits, gain_of_row, paired_bootstrap,
)


def load_rows(path: Path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def correctness(rows, tools, pred_idx):
    """(label_correct, optimal_correct) for a vector of predicted tool indices."""
    label, optimal = [], []
    for row, p in zip(rows, pred_idx.tolist()):
        tool = tools[int(p)]
        label.append(tool == row.get("tool"))
        gains = row.get("gains") or {}
        if not gains:
            optimal.append(True)
            continue
        optimal.append(abs(gain_of_row(row, tool) - max(gains.values())) < 1e-9)
    return np.asarray(label, bool), np.asarray(optimal, bool)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/tool_policy.onnx")
    ap.add_argument("--sidecar", default="models/tool_policy.json")
    ap.add_argument("--data", default="data/tool_sft_v3/val.jsonl")
    ap.add_argument("--floor", type=float, default=None,
                    help="margin floor to test; default reads models/consult_gate.json")
    args = ap.parse_args()

    sidecar = json.loads(Path(args.sidecar).read_text(encoding="utf-8"))
    base = dict(sidecar)
    base.setdefault("tools", sidecar.get("tools"))
    tools = list(base["tools"])
    rows = load_rows(Path(args.data))

    logits = np.asarray(_onnx_logits(Path(args.model), rows, base), dtype=np.float64)
    order = np.argsort(-logits, axis=1)
    pred = order[:, 0]
    margin = logits[np.arange(len(rows)), order[:, 0]] - logits[np.arange(len(rows)), order[:, 1]]

    floor = args.floor
    if floor is None:
        gate = json.loads((REPO_ROOT / "models" / "consult_gate.json").read_text(encoding="utf-8"))
        floor = float(gate["floor"])

    teacher_idx = np.asarray([tools.index(r["teacher"]) if r.get("teacher") in tools else -1
                              for r in rows])
    defer = (margin < floor) & (teacher_idx >= 0)
    gated = np.where(defer, teacher_idx, pred)

    u_lab, u_opt = correctness(rows, tools, pred)
    g_lab, g_opt = correctness(rows, tools, gated)

    out = {
        "model": args.model,
        "rows": len(rows),
        "floor": floor,
        "deferred_rows": int(defer.sum()),
        "ungated": {"accuracy": round(float(u_lab.mean()), 4),
                    "optimal": round(float(u_opt.mean()), 4)},
        "gated": {"accuracy": round(float(g_lab.mean()), 4),
                  "optimal": round(float(g_opt.mean()), 4)},
        "delta_accuracy": paired_bootstrap(u_lab, g_lab),
        "delta_optimal": paired_bootstrap(u_opt, g_opt),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
