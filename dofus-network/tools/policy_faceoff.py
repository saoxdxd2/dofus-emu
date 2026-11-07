"""Matched-seed face-off: the deterministic QuestPlanner vs the learned tool policy.

The live default is `--policy rule`; the 49K `models/tool_policy.onnx` is opt-in
(`--policy planner` / `NNPlanner`). This runs both on the same symbolic worlds and
reports quests completed per seed, so "is the learned chooser better?" is a number.

    python tools/policy_faceoff.py --seeds 30 --steps 300
    python tools/policy_faceoff.py --seeds 20 --steps 600 --json

Caveat, stated by the tool as well: the symbolic observation does not report quests a
client could start, while the live G31 guard refuses any id the client never showed.
The harness therefore seeds each planner's client-confirmed set with the curriculum —
a simulation of "a client that offers these quests", not the live rule. It is a
relative policy measurement, not live proof (KNOWN_GAPS G7).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.planner import QuestPlanner  # noqa: E402
from agent.planner_nn import NNPlanner  # noqa: E402
from sim.run import run  # noqa: E402


def _policy(cls: Any, level: int, ordering: str) -> Any:
    policy = cls(level_hint=level, ordering=ordering)
    # G31 makes the live planner refuse ids the client never reported. The symbolic
    # world knows every quest but its observation has no startable list, so the harness
    # supplies the curriculum as the client's offer for both policies.
    policy._client_quests |= set(policy._curriculum(level))
    return policy


def faceoff(seeds: int, steps: int, level: int, ordering: str = "step",
            noise: bool = True, travel_cost: bool = True) -> Dict[str, Any]:
    nn = _policy(NNPlanner, level, ordering)
    nn._ensure_model()
    if nn.session is None:
        raise SystemExit("learned model unavailable (%s); refusing a rules-vs-rules "
                         "comparison" % nn.note)
    rows: List[Dict[str, Any]] = []
    for seed in range(1, seeds + 1):
        scores = {}
        for name, cls in (("rule", QuestPlanner), ("learned", NNPlanner)):
            policy = _policy(cls, level, ordering)
            score = run(policy, level=level, steps=steps, seed=seed, noise=noise,
                        travel_cost=travel_cost, ordering=ordering)
            scores[name] = score
        rows.append({
            "seed": seed,
            "rule_quests": scores["rule"]["quests"],
            "learned_quests": scores["learned"]["quests"],
            "rule_objectives": scores["rule"]["objectives"],
            "learned_objectives": scores["learned"]["objectives"],
        })
    wins = sum(1 for row in rows if row["learned_quests"] > row["rule_quests"])
    ties = sum(1 for row in rows if row["learned_quests"] == row["rule_quests"])
    losses = sum(1 for row in rows if row["learned_quests"] < row["rule_quests"])
    return {
        "model": str(nn.model_path),
        "model_note": nn.note,
        "level": level,
        "steps": steps,
        "ordering": ordering,
        "noise": noise,
        "travel_cost": travel_cost,
        "seeds": seeds,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "mean_rule_quests": round(statistics.mean(r["rule_quests"] for r in rows), 2),
        "mean_learned_quests": round(statistics.mean(r["learned_quests"] for r in rows), 2),
        "mean_delta": round(statistics.mean(
            r["learned_quests"] - r["rule_quests"] for r in rows), 2),
        "rows": rows,
        "caveat": ("the sim client offers the curriculum; the live G31 guard is "
                   "stricter. Simulation only, not live proof."),
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--steps", type=int, default=300)
    parser.add_argument("--level", type=int, default=60)
    parser.add_argument("--ordering", choices=("step", "nearest"), default="step")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = faceoff(args.seeds, args.steps, args.level, args.ordering)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("%s (%s)" % (result["model"], result["model_note"]))
        print("level %d, %d steps, %d seeds, noise=%s travel_cost=%s"
              % (result["level"], result["steps"], result["seeds"],
                 result["noise"], result["travel_cost"]))
        print("learned wins %d, ties %d, rule wins %d | mean rule %.2f, learned %.2f, "
              "delta %+.2f quests"
              % (result["wins"], result["ties"], result["losses"],
                 result["mean_rule_quests"], result["mean_learned_quests"],
                 result["mean_delta"]))
        print("caveat: %s" % result["caveat"])
    return 0 if result["losses"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
