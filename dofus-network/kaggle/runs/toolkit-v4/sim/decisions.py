"""Counterfactual decision corpus: what *would* have worked, not what the rules did.

Every previous corpus in this project learned "the teacher would do X". That is
imitation, and imitation cannot tell you that the teacher was wrong. The noisy world
made this urgent: 60% of actions fail, so the interesting question is no longer which
tool to use but **whether to retry, verify, recover or replan** — and the rule
controller answers that with hand-set thresholds nobody has ever validated.

The simulator can do better than any teacher: it can fork the world and try each
decision. So for a sampled state we run all four to the same depth and label the one
that completed the most objectives. That label is a *fact about the world*, not an
opinion about the rules.

    python sim/decisions.py --states 400 --out data/decision_sft

Cost: four short rollouts per state. The quest table is shared by reference, so a fork
is a few dict copies, not a re-read of the knowledge base.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.features import objective_type  # noqa: E402
from agent.planner import QuestPlanner  # noqa: E402
from sim.world import World  # noqa: E402

# The decision space. Order is part of the corpus format.
DECISIONS = ["continue", "retry", "recover", "replan"]
DEPTH = 3


def features(world: World, planner: QuestPlanner, streak: int,
             last_ok: bool) -> List[float]:
    """What the decider is allowed to see: state, history, and nothing hidden."""
    obs = world.observe()
    current = world.current_objective()
    t = objective_type(current[3]) if current else -1
    inv = obs.get("inventory") or {}
    return [
        min(1.0, world.level / 200.0),
        1.0 if obs.get("map_id") else 0.0,
        min(1.0, len(inv) / 20.0),
        min(1.0, world.objectives_completed / 50.0),
        min(1.0, world.quests_completed / 20.0),
        1.0 if current else 0.0,
        float(t) / 20.0,
        min(1.0, len(world.active) / 5.0),
        min(1.0, streak / 5.0),
        1.0 if last_ok else 0.0,
        min(1.0, world.steps / 200.0),
    ]


def _apply(world: World, decision: str, planner: QuestPlanner,
           last_tool: Optional[str]) -> None:
    """One step of the decision, then let the planner continue for DEPTH steps."""
    call = planner.choose("do all the quests", world.observe(), None)
    if decision == "retry" and last_tool:
        world.step(last_tool, {})
    elif decision == "recover":
        world.step("state.snapshot", {})
    elif decision == "replan":
        # Replanning in the sim = abandon the current quest and take the next one.
        if world.active:
            world.active.clear()
        call = planner.choose("do all the quests", world.observe(), None)
        if call is not None:
            world.step(call.tool, call.args)
    else:
        if call is not None:
            world.step(call.tool, call.args)

    for _ in range(DEPTH - 1):
        nxt = planner.choose("do all the quests", world.observe(), None)
        if nxt is None:
            break
        world.step(nxt.tool, nxt.args)


def sample(world: World, planner: QuestPlanner, rng, streak: int,
           last_ok: bool) -> Tuple[List[float], int, List[float]]:
    """Fork the world four ways and keep the decision that actually progressed."""
    base = world.objectives_completed
    gains: List[float] = []
    for decision in DECISIONS:
        twin = world.clone()
        _apply(twin, decision, planner, world.history[-1]["tool"] if world.history else None)
        gains.append(float(twin.objectives_completed - base))

    best = max(range(len(gains)), key=lambda i: gains[i])
    # If nothing helps, the honest label is "continue": doing nothing unusual is the
    # conservative choice when every option is equally useless.
    if all(g <= 0 for g in gains):
        best = DECISIONS.index("continue")
    return features(world, planner, streak, last_ok), best, gains


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--states", type=int, default=400)
    ap.add_argument("--levels", type=int, nargs="+", default=[20, 40, 60, 80])
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "decision_sft")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args(argv)

    import random
    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    world = World(level=args.levels[0], seed=args.seed, max_steps=10 ** 6, noise=True)
    planner = QuestPlanner(level_hint=args.levels[0])
    streak = 0
    last_ok = True

    while len(rows) < args.states:
        if world.steps % 200 == 0:
            level = rng.choice(args.levels)
            world = World(level=level, seed=rng.randint(0, 10 ** 6),
                          max_steps=10 ** 6, noise=True)
            planner = QuestPlanner(level_hint=level)
            streak, last_ok = 0, True

        # Only record states that follow a failure. When the last action worked,
        # "continue" wins trivially and the corpus fills with rows where the decision
        # does not matter -- which is how a decision dataset ends up teaching nothing.
        if streak >= 1:
            x, best, gains = sample(world, planner, rng, streak, last_ok)
            rows.append({"x": x, "y": best, "gains": gains,
                         "decision": DECISIONS[best],
                         "took_effect": gains[best] > 0})

        call = planner.choose("do all the quests", world.observe(), None)
        if call is None:
            continue
        ok, _ = world.step(call.tool, call.args)
        last_ok = ok
        streak = 0 if ok else streak + 1

    split = int(len(rows) * 0.85)
    for name, part in (("train", rows[:split]), ("val", rows[split:])):
        with (args.out / (name + ".jsonl")).open("w", encoding="utf-8") as fh:
            for row in part:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")

    counts = {d: sum(1 for r in rows if r["decision"] == d) for d in DECISIONS}
    stats = {"rows": len(rows), "train": split, "val": len(rows) - split,
             "decisions": counts,
             "effective_rate": round(sum(1 for r in rows if r["took_effect"]) / len(rows), 3),
             "features": len(rows[0]["x"]), "depth": DEPTH}
    (args.out / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
