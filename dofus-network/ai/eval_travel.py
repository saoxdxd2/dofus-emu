"""Does the learned policy beat the teacher in a world where travel costs something?

This is the question the last four experiments were circling. Every previous score was
measured in a world where movement was free, and in that world the teacher's plan —
"one objective at a time, re-plan every step" — is provably optimal, so the only thing
a model could learn was how to react to noise. That is why capacity, data, recursion
and the featuriser all failed to move the number: they were all being asked to beat an
optimum that was optimal by construction.

With travel charged, the teacher is no longer optimal, so a policy that decides *when*
to travel and *what* to do first has room. This measures whether anything fills it.

    python ai/eval_travel.py --seeds 7 --steps 400
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.planner import QuestPlanner  # noqa: E402
from agent.planner_nn import NNPlanner  # noqa: E402
from sim.run import run  # noqa: E402


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=7)
    ap.add_argument("--steps", type=int, default=400)
    ap.add_argument("--level", type=int, default=60)
    ap.add_argument("--model", type=Path,
                    default=REPO_ROOT / "models" / "tool_policy.onnx")
    ap.add_argument("--no-travel-cost", dest="travel_cost", action="store_false",
                    help="the old free-travel world, for comparison")
    ap.set_defaults(travel_cost=True)
    args = ap.parse_args(argv)

    seeds = [7, 11, 23, 31, 47, 59, 71][: args.seeds]
    policies: Dict[str, Any] = {
        "teacher (step order)": lambda: QuestPlanner(level_hint=args.level,
                                                     ordering="step"),
        "teacher (nearest)": lambda: QuestPlanner(level_hint=args.level,
                                                  ordering="nearest"),
        "learned (nearest)": lambda: NNPlanner(level_hint=args.level,
                                               ordering="nearest",
                                               model_path=args.model),
    }

    rows = []
    for name, make in policies.items():
        q, o = [], []
        for seed in seeds:
            policy = make()
            r = run(policy, level=args.level, steps=args.steps, seed=seed,
                    noise=True, travel_cost=args.travel_cost,
                    ordering=getattr(policy, "ordering", "step"))
            q.append(r["quests"])
            o.append(r["objectives"])
        rows.append((name, statistics.mean(q), statistics.pstdev(q),
                     statistics.mean(o)))

    base = rows[0][1]
    print("world: --noise --travel-cost, %d steps, %d seeds"
          % (args.steps, len(seeds)))
    print("%-22s %8s %6s %10s %8s" % ("policy", "quests", "sd", "objectives", "vs base"))
    for name, mean_q, sd, mean_o in rows:
        print("%-22s %8.1f %6.1f %10.1f %+7.1f%%"
              % (name, mean_q, sd, mean_o, 100.0 * (mean_q - base) / max(1e-9, base)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
