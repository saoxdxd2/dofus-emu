"""Head-to-head benchmark for quest policies on held-out worlds.

The promotion question is not "does the learned head agree with the teacher" —
that number is bounded by the teacher's own agreement — it is "does a policy
finish more quests than the rule planner on worlds it never trained on". This
tool answers that, and it also measures the ceiling of the corpus recipe:

  rule      QuestPlanner, the deterministic teacher.
  learned   NNPlanner with models/tool_policy.onnx (the shipped student).
  oracle    At every step, fork the world for each candidate tool and take the
            argmax of the same gain the corpus labels use, with the teacher as
            the continuation policy. This is the *recipe's* best possible
            behaviour, not a global optimum: it shows whether one-step
            improvement over the teacher is worth anything over a trajectory.
  random    Uniform over the same candidate pool. A floor.

Seeds default to a held-out range (20000+) because the corpus is generated from
seed 7 onwards (5 workers stride 977, 120 episodes each). Evaluating on 1..20
overlaps the training worlds for seeds 7..20, which is how a "+0.20 quests"
claim was measured on partially-seen worlds. If a requested seed falls inside a
corpus range this tool says so.

    python tools/bench_quest_policies.py --seeds 5 --steps 400
    python tools/bench_quest_policies.py --seeds 20 --steps 400 --json

Runtime is dominated by `oracle`: every environment step forks ~6-8 tools to
depth 5. Drop `--depth` or `--seeds` to make it cheaper; the rule/learned
comparison alone is fast.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.planner import QuestPlanner, args_for_tool  # noqa: E402
from agent.planner_nn import NNPlanner  # noqa: E402
from sim.tool_choice import (  # noqa: E402
    _fork_score, _perturb, _planned_objective, candidates,
)
from sim.world import World  # noqa: E402

GOAL = "do all the quests"


def corpus_seed_ranges(episodes: int = 120, workers: int = 5, stride: int = 977,
                       base: int = 7) -> List[Tuple[int, int]]:
    return [(base + i * stride, base + i * stride + episodes - 1)
            for i in range(workers)]


def overlaps_corpus(seeds: List[int]) -> List[int]:
    ranges = corpus_seed_ranges()
    return [s for s in seeds if any(lo <= s <= hi for lo, hi in ranges)]


class PlannerPolicy:
    """Adapt a QuestPlanner/NNPlanner to the world-shaped choose the bench uses."""

    def __init__(self, policy: Any) -> None:
        self.policy = policy

    def choose(self, world: World):
        return self.policy.choose(GOAL, world.observe(), None)

    def note_result(self, tool: str, ok: bool, note: str, obs: Dict[str, Any]) -> None:
        self.policy.note_result(tool, ok, note, obs)


class OraclePolicy:
    """Greedy argmax of the corpus gain, executed closed-loop.

    Deliberately stateless: the whole point is to measure the recipe's ceiling
    without a student in the way. Ties go to the teacher, as the corpus does.
    """

    def __init__(self, level: int, depth: int, score_mode: str, rng_seed: int) -> None:
        self.planner = QuestPlanner(level_hint=level, ordering="step")
        self.planner._client_quests |= set(self.planner._curriculum(level))
        self.level = level
        self.depth = depth
        self.score_mode = score_mode
        self.rng = random.Random(rng_seed)

    def choose(self, world: World):
        obs = world.observe()
        call = self.planner.choose(GOAL, obs, None)
        if call is None:
            return None
        current = _planned_objective(world, self.planner)
        if current is None:
            return call
        quest, _step_idx, _obj_idx, obj = current
        quest_row = self.planner.quests.get(quest.id) or {"id": quest.id}
        best_tool, best_args, best = None, None, None
        teacher_score = None
        for tool in candidates(call.tool, obj, self.rng):
            args = args_for_tool(tool, quest_row, obj, obs)
            score, _cum = _fork_score(world, self.planner, tool, args,
                                      self.depth, self.score_mode)
            if tool == call.tool:
                teacher_score = score
            if best is None or score > best:
                best_tool, best_args, best = tool, args, score
        if best is None or (teacher_score is not None and teacher_score >= best):
            return call
        from agent.runtime import ToolCall
        return ToolCall(best_tool, best_args or {}, GOAL)

    def note_result(self, tool: str, ok: bool, note: str, obs: Dict[str, Any]) -> None:
        self.planner.note_result(tool, ok, note, obs)


class RandomPolicy:
    def __init__(self, level: int, seed: int) -> None:
        self.planner = QuestPlanner(level_hint=level, ordering="step")
        self.rng = random.Random(seed)

    def choose(self, world: World):
        obs = world.observe()
        call = self.planner.choose(GOAL, obs, None)
        if call is None:
            return None
        from agent.runtime import ToolCall
        return ToolCall(self.rng.choice(self.tools_for(world)), {}, GOAL)

    def tools_for(self, world: World) -> List[str]:
        return ["state.snapshot", "nav.goto", "quest.advance", "fight.join", "inv.list"]

    def note_result(self, tool: str, ok: bool, note: str, obs: Dict[str, Any]) -> None:
        self.planner.note_result(tool, ok, note, obs)


READ_TOOLS = ("state.snapshot", "quest.list", "inv.list", "fight.state",
              "fight.plan", "fight.estimate", "hdv.scan", "knowledge.search")


def run_episode(policy: Any, level: int, steps: int, seed: int, noise: bool,
                travel_cost: bool, perturb: bool) -> Dict[str, Any]:
    world = World(level=level, seed=seed, max_steps=steps, noise=noise,
                  travel_cost=travel_cost)
    rng = random.Random(seed * 31 + 7)
    failed = 0
    while world.steps < steps:
        if perturb and rng.random() < 0.10:
            _perturb(world, rng)
        call = policy.choose(world)
        if call is None:
            break
        ok, note = world.step(call.tool, call.args)
        if not ok:
            failed += 1
        reporter = getattr(policy, "note_result", None)
        if reporter is not None:
            reporter(call.tool, ok, note, world.state())
    score = world.score()
    counts: Dict[str, int] = {}
    for row in world.history:
        counts[row["tool"]] = counts.get(row["tool"], 0) + 1
    total = max(1, len(world.history))
    score["tool_mix"] = {k: round(v / total, 3) for k, v in
                         sorted(counts.items(), key=lambda kv: -kv[1])[:8]}
    score["read_share"] = round(sum(v for k, v in counts.items() if k in READ_TOOLS) / total, 3)
    score["failed_share"] = round(failed / total, 3)
    score["steps_used"] = len(world.history)
    return score


def sign_test(wins: int, losses: int) -> float:
    """Two-sided exact sign test p-value (no scipy)."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def bench(seeds: List[int], steps: int, level: int, policies: List[str],
          noise: bool, travel_cost: bool, depth: int, score_mode: str,
          perturb: bool) -> Dict[str, Any]:
    out: Dict[str, List[Dict[str, Any]]] = {name: [] for name in policies}
    for seed in seeds:
        for name in policies:
            if name == "rule":
                planner = QuestPlanner(level_hint=level, ordering="step")
                planner._client_quests |= set(planner._curriculum(level))
                policy: Any = PlannerPolicy(planner)
            elif name == "learned":
                planner = NNPlanner(level_hint=level, ordering="step")
                planner._ensure_model()
                if planner.session is None:
                    raise SystemExit("learned model unavailable: %s" % planner.note)
                planner._client_quests |= set(planner._curriculum(level))
                policy = PlannerPolicy(planner)
            elif name == "oracle":
                policy = OraclePolicy(level, depth, score_mode, seed)
            elif name == "random":
                policy = RandomPolicy(level, seed)
            else:
                raise SystemExit("unknown policy %r" % name)
            score = run_episode(policy, level, steps, seed, noise, travel_cost, perturb)
            score["seed"] = seed
            out[name].append(score)
    summary: Dict[str, Any] = {"seeds": seeds, "steps": steps, "level": level,
                               "noise": noise, "travel_cost": travel_cost,
                               "depth": depth, "score_mode": score_mode,
                               "perturb": perturb, "policies": {}, "head_to_head": {}}
    for name, rows in out.items():
        summary["policies"][name] = {
            "quests_mean": round(statistics.mean(r["quests"] for r in rows), 3),
            "quests_per_1000_steps": round(statistics.mean(
                r["quests_per_1000_steps"] for r in rows), 2),
            "objectives_per_100_steps": round(statistics.mean(
                r["objectives_per_100_steps"] for r in rows), 2),
            "stall": round(statistics.mean(1.0 if r.get("stalled") else 0.0 for r in rows), 3),
            "read_share": round(statistics.mean(r.get("read_share", 0.0) for r in rows), 3),
            "failed_share": round(statistics.mean(r.get("failed_share", 0.0) for r in rows), 3),
            "tool_mix": rows[0].get("tool_mix") if rows else {},
            "rows": rows,
        }
    base = out.get("rule")
    if base is not None:
        for name, rows in out.items():
            if name == "rule":
                continue
            wins = sum(1 for a, b in zip(rows, base) if a["quests"] > b["quests"])
            ties = sum(1 for a, b in zip(rows, base) if a["quests"] == b["quests"])
            losses = sum(1 for a, b in zip(rows, base) if a["quests"] < b["quests"])
            deltas = [a["quests"] - b["quests"] for a, b in zip(rows, base)]
            summary["head_to_head"][name] = {
                "wins": wins, "ties": ties, "losses": losses,
                "mean_delta": round(statistics.mean(deltas), 3),
                "sign_test_p": round(sign_test(wins, losses), 4),
            }
    summary["corpus_overlap"] = overlaps_corpus(seeds)
    if summary["corpus_overlap"]:
        summary["warning"] = ("%d seed(s) are inside the corpus generator's range; "
                              "this is not a held-out measurement"
                              % len(summary["corpus_overlap"]))
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--seed-base", type=int, default=20000)
    ap.add_argument("--steps", type=int, default=400)
    ap.add_argument("--level", type=int, default=60)
    ap.add_argument("--policies", default="rule,learned,oracle")
    ap.add_argument("--depth", type=int, default=5)
    ap.add_argument("--score-mode", choices=("rate", "cummean"), default="cummean")
    ap.add_argument("--noise", action="store_true", default=True)
    ap.add_argument("--deterministic", dest="noise", action="store_false")
    ap.add_argument("--travel-cost", action="store_true", default=True)
    ap.add_argument("--no-travel-cost", dest="travel_cost", action="store_false")
    ap.add_argument("--no-perturb", dest="perturb", action="store_false", default=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    seeds = list(range(args.seed_base, args.seed_base + args.seeds))
    policies = [p.strip() for p in args.policies.split(",") if p.strip()]
    result = bench(seeds, args.steps, args.level, policies, args.noise,
                   args.travel_cost, args.depth, args.score_mode, args.perturb)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print("level %d, %d steps, %d seeds (%d..%d), noise=%s travel_cost=%s depth=%d %s perturb=%s"
              % (args.level, args.steps, len(seeds), seeds[0], seeds[-1],
                 args.noise, args.travel_cost, args.depth, args.score_mode,
                 args.perturb))
        for name, stat in result["policies"].items():
            print("%-8s quests %.2f  per1000 %.1f  objectives/100 %.1f  stall %.2f  "
                  "reads %.2f  failed %.2f  mix %s"
                  % (name, stat["quests_mean"], stat["quests_per_1000_steps"],
                     stat["objectives_per_100_steps"], stat["stall"],
                     stat["read_share"], stat["failed_share"],
                     json.dumps(stat["tool_mix"])))
        for name, h2h in result["head_to_head"].items():
            print("vs rule: %-8s wins %d ties %d losses %d  mean delta %+.2f  p=%.4f"
                  % (name, h2h["wins"], h2h["ties"], h2h["losses"],
                     h2h["mean_delta"], h2h["sign_test_p"]))
        if result.get("warning"):
            print("WARNING: %s" % result["warning"])
        print("oracle note: the oracle scores the same fork the corpus labels use, "
              "with the teacher as continuation; it is this recipe's ceiling, not a "
              "global optimum or live proof.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
