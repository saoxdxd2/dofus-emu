"""A/B the two fork scores: rate vs cummean.

The question is not which score produces a nicer label distribution. It is whether the
labels become *less ambiguous* (same features, same answer) and whether the resulting
policy completes more quests. Anything else is decoration.
"""

import collections
import json
import statistics
import sys

import numpy as np

sys.path.insert(0, ".")

from agent.planner import QuestPlanner  # noqa: E402
from agent.planner_nn import NNPlanner  # noqa: E402
from sim.run import run  # noqa: E402


def load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")]


def ambiguity(root):
    tr = load(root + "/train.jsonl")
    va = load(root + "/val.jsonl")
    table = collections.defaultdict(collections.Counter)
    for r in tr:
        table[tuple(np.round(r["x"], 3))][r["y"]] += 1
    n = a = 0
    for r in va:
        k = tuple(np.round(r["x"], 3))
        if k not in table:
            continue
        n += 1
        a += len(table[k]) > 1
    return a, n, tr, va


def main() -> int:
    for root in ("data/ab_rate", "data/ab_cummean"):
        a, n, tr, va = ambiguity(root)
        stats = json.load(open(root + "/stats.json", encoding="utf-8"))
        print("%-16s rows %6d  ambiguous %4d/%4d = %5.1f%%  margin %.3f  near-tie %.3f"
              % (root, len(tr) + len(va), a, n, 100 * a / max(1, n),
                 stats.get("mean_margin", 0), stats.get("near_ties", 0)))
        print("                 label mix:",
              {k: v for k, v in list(stats["tools"].items())[:5]})

    print("\nsimulator (7 seeds, --noise, 400 steps):")
    seeds = (7, 11, 23, 31, 47, 59, 71)
    for root in ("data/ab_rate", "data/ab_cummean"):
        model = "models/ab_%s.onnx" % root.split("_")[-1]
        import subprocess
        subprocess.run([sys.executable, "ai/train_tool_policy.py", "--data", root,
                        "--out", model, "--epochs", "60"],
                       capture_output=True, text=True)
        q, o = [], []
        for s in seeds:
            r = run(NNPlanner(level_hint=60, model_path=model), level=60, steps=400,
                    seed=s, noise=True)
            q.append(r["quests"])
            o.append(r["objectives"])
        print("  %-16s quests %.1f  objectives %.1f" % (root, statistics.mean(q), statistics.mean(o)))

    q, o = [], []
    for s in seeds:
        r = run(QuestPlanner(level_hint=60), level=60, steps=400, seed=s, noise=True)
        q.append(r["quests"])
        o.append(r["objectives"])
    print("  %-16s quests %.1f  objectives %.1f" % ("teacher", statistics.mean(q), statistics.mean(o)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
