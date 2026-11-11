"""Why can the policy only ever emit 8 of its 21 tools?

"Unreachable" is ambiguous, and the three causes have completely different fixes:

  * never-simulated -- the counterfactual fork never evaluated the tool on any row,
    so it has no measured gain anywhere and can never be the argmax. Nothing the
    model does can change this; it is a property of the candidate set.
  * simulated-but-never-best -- the tool was evaluated and lost every time. The model
    is right to never emit it, and forcing it would be teaching a mistake.
  * labelled-but-not-emitted -- the tool is the correct answer on some rows and the
    model still never ranks it first. That is the only category that is a model bug.

Reporting them as one number hides which one you are looking at, and the fix for the
first is corpus work while the fix for the third is training work.

Usage: python tools/tool_coverage.py [--model models/tool_policy.onnx]
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

from agent.weight_space import _onnx_logits  # noqa: E402


def load(path: Path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


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

    # Corpus-side counts over BOTH splits: a tool missing from train but present in
    # val is a different problem from one missing from both.
    label = Counter(r.get("tool") for r in train + val)
    teacher = Counter(r.get("teacher") for r in train + val)
    simulated = Counter()
    best = Counter()
    for r in train + val:
        gains = r.get("gains") or {}
        for name in gains:
            simulated[name] += 1
        if gains:
            top = max(gains.values())
            for name, g in gains.items():
                if abs(g - top) < 1e-9:
                    best[name] += 1

    logits = np.asarray(_onnx_logits(Path(args.model), val, sidecar), dtype=np.float64)
    order = np.argsort(-logits, axis=1)
    emitted = Counter(tools[i] for i in order[:, 0].tolist())
    # Mean rank (0 = first) tells you whether an unemitted tool is close or nowhere.
    ranks = np.empty_like(order)
    np.put_along_axis(ranks, order, np.arange(order.shape[1])[None, :].repeat(len(val), 0), axis=1)
    mean_rank = {name: float(ranks[:, i].mean()) for i, name in enumerate(tools)}

    def classify(name):
        if emitted.get(name, 0) > 0:
            return "reachable"
        if label.get(name, 0) > 0:
            return "labelled-but-not-emitted"
        if simulated.get(name, 0) == 0:
            return "never-simulated"
        return "simulated-but-never-best"

    print(f"{'tool':<20}{'label':>7}{'teach':>7}{'sim':>7}{'best':>6}"
          f"{'emit':>6}{'rank':>7}  class")
    print("-" * 82)
    buckets = Counter()
    for i, name in enumerate(tools):
        cls = classify(name)
        buckets[cls] += 1
        print(f"{name:<20}{label.get(name,0):>7}{teacher.get(name,0):>7}"
              f"{simulated.get(name,0):>7}{best.get(name,0):>6}{emitted.get(name,0):>6}"
              f"{mean_rank[name]:>7.1f}  {cls}")

    print("\n" + ", ".join(f"{k}={v}" for k, v in sorted(buckets.items())))
    print(f"rows: train={len(train)} val={len(val)}; gain-set sizes "
          f"{dict(sorted(Counter(len(r.get('gains') or {}) for r in train + val).items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
