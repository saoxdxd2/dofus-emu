"""Run a policy in the symbolic world and print what it achieved.

This is the scoring harness for agent 2: seconds instead of a live session, and a
number that can be compared across policies.

    python sim/run.py --policy planner --level 60 --steps 500
    python sim/run.py --policy random  --level 60 --steps 500
    python sim/run.py --policy rule    --level 60 --steps 500
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sim.world import World  # noqa: E402

GOALS = ["do all the quests"]


class RandomPolicy:
    """Lower bound: pick a plausible tool at random."""

    TOOLS = ["nav.goto", "quest.advance", "farm.start", "state.snapshot", "inv.list"]

    def __init__(self, seed: int = 7) -> None:
        self.rng = random.Random(seed)

    def choose(self, goal, observation, tools=None):
        from agent.runtime import ToolCall

        tool = self.rng.choice(self.TOOLS)
        args: Dict[str, Any] = {}
        obs = observation or {}
        if tool == "nav.goto":
            args = {"map_id": self.rng.choice([1, 2, 3, 4, 5]) * 1000}
        elif tool == "quest.advance":
            args = {"quest_id": self.rng.choice(list(range(1, 50)))}
        elif tool == "farm.start":
            args = {"monster": self.rng.choice(list(range(1, 50)))}
        return ToolCall(tool, args, goal)


class RulePolicy:
    """The policy the app runs today: one call per goal, no plan."""

    def choose(self, goal, observation, tools=None):
        from agent.runtime import ToolCall

        g = (goal or "").lower()
        if "quest" in g:
            return ToolCall("quest.list", {}, goal)
        if "inventory" in g or "inventaire" in g:
            return ToolCall("inv.list", {}, goal)
        if "farm" in g:
            return ToolCall("farm.start", {}, goal)
        return None


def run(policy: Any, level: int = 60, steps: int = 500, seed: int = 7,
        verbose: bool = False) -> Dict[str, Any]:
    world = World(level=level, seed=seed, max_steps=steps)
    goal = GOALS[0]
    while world.steps < steps:
        obs = world.observe()
        call = policy.choose(goal, obs, None)
        if call is None:
            break
        ok, note = world.step(call.tool, call.args)
        if verbose and world.steps % 50 == 0:
            print("  step %4d  %-14s %-5s %s" % (world.steps, call.tool,
                                                 "ok" if ok else "no", note[:50]))
    score = world.score()
    score["policy"] = type(policy).__name__
    score["level"] = level
    return score


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", choices=("planner", "random", "rule"), default="planner")
    ap.add_argument("--level", type=int, default=60)
    ap.add_argument("--steps", type=int, default=500)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.policy == "planner":
        from agent.planner import QuestPlanner
        policy: Any = QuestPlanner(level_hint=args.level)
    elif args.policy == "random":
        policy = RandomPolicy(args.seed)
    else:
        policy = RulePolicy()

    score = run(policy, args.level, args.steps, args.seed, args.verbose)
    print(json.dumps(score, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
