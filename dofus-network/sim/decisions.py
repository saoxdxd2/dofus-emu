"""Counterfactual repair corpus: what *would* have worked after a failure.

Every corpus that learns "the teacher would do X" is imitation, and imitation cannot
tell you the teacher was wrong. After a failure the teacher has no rule at all — it
plans objectives, not recoveries — so this is the one decision in the system that is
genuinely unmodelled. It is also the only place a learned model can still earn its
place, after the tool policy was measured to lose to the teacher's exact precondition
checks (now.md §3).

The simulator can do better than any teacher here: it forks the world four ways and
runs each decision to the same depth. The label is a fact about the world.

Four decisions, and what each means in the simulator:

* ``continue`` — let the planner take its next step.
* ``retry``    — replay the *same call with the same arguments*. Replaying the tool
  with empty arguments, which this file used to do, is not a retry: `farm.start` with
  no monster is a different action, and it made the retry arm look worse than it is.
* ``recover``  — refresh the observation (`state.snapshot`), which clears a stale or
  partial one.
* ``replan``   — drop the active quest and take the next.

    python sim/decisions.py --states 3000 --travel-cost --out data/decision_tc
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.features import (TOOL_INDEX, objective_type, required_map,  # noqa: E402
                            travel_hops)
from agent.planner import QuestPlanner  # noqa: E402
from sim.world import World  # noqa: E402

DECISIONS = ["continue", "retry", "recover", "replan"]
DEPTH = 3

ERROR_SLOTS = ["none", "structural", "transport", "action", "other"]


def _err_slot(note: Optional[str]) -> str:
    if not note:
        return "none"
    try:
        from agent.controller import error_class
        cls = error_class(note)
    except Exception:  # noqa: BLE001
        cls = "other"
    return cls if cls in ERROR_SLOTS else "other"


def features(world: World, streak: int, last_ok: Optional[bool],
             last_tool: Optional[str], last_note: Optional[str],
             obs_kind: str = "fresh") -> List[float]:
    """What a decider may see at the moment it has to decide: state and history.

    Deliberately not the 11 numbers this used to be. Those omitted the three things a
    recovery decision actually depends on — *why* the last call failed, *which* call it
    was, and *how far* the objective is — and the earlier verdict ("the decision is not
    learnable from these features") was a verdict about the features, not the decision.
    """
    obs = world.observe()
    current = world.current_objective()
    obj = current[3] if current else None
    t = objective_type(obj) if obj else -1
    inv = obs.get("inventory") or {}
    need = required_map(obj) if obj else None
    hops = travel_hops(obs.get("map_id"), need) if need else 0
    capacity = int(obs.get("inventory_capacity") or 0)

    vec: List[float] = [
        min(1.0, world.level / 200.0),
        1.0 if obs.get("map_id") else 0.0,
        min(1.0, len(inv) / 20.0),
        min(1.0, world.objectives_completed / 50.0),
        min(1.0, world.quests_completed / 20.0),
        1.0 if obj else 0.0,
        float(t) / 20.0,
        min(1.0, len(world.active) / 5.0),
        min(1.0, streak / 5.0),
        1.0 if last_ok else 0.0,
        min(1.0, world.steps / 200.0),
        (TOOL_INDEX.get(last_tool, 0) / 17.0) if last_tool else 0.0,
        min(1.0, hops / 3.0),
        1.0 if capacity and len(inv) >= capacity else 0.0,
    ]
    slot = _err_slot(last_note)
    for name in ERROR_SLOTS:
        vec.append(1.0 if slot == name else 0.0)
    vec.append(1.0 if obs_kind == "stale" else 0.0)
    vec.append(1.0 if obs_kind == "partial" else 0.0)
    has_needed = 0.0
    if obj is not None and t in (2, 3, 12):
        from agent.planner import _params
        p = _params(obj)
        item = p.get("parameter1") if t == 3 else p.get("parameter0")
        if item is not None:
            has_needed = 1.0 if (inv.get(item) or inv.get(str(item))) else 0.0
    vec.append(has_needed)
    vec.append(min(1.0, (obs.get("kamas") or 0) / 10000.0))
    vec.append(1.0 if need and obs.get("map_id") == need else 0.0)
    return vec


FEATURE_NAMES: List[str] = (
    ["level", "map_known", "inv_stacks", "objectives", "quests", "has_objective",
     "obj_type", "active", "streak", "last_ok", "steps", "last_tool", "hops",
     "inv_full"] + ["err_%s" % s for s in ERROR_SLOTS] + ["obs_stale", "obs_partial",
     "has_needed", "kamas", "on_target"]
)
FEATURE_COUNT = len(FEATURE_NAMES)


def _apply(world: World, decision: str, planner: QuestPlanner,
           last_tool: Optional[str], last_args: Optional[Dict[str, Any]]) -> None:
    """One step of the decision, then let the planner continue for DEPTH steps."""
    if decision == "retry" and last_tool:
        world.step(last_tool, dict(last_args or {}))
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
        call = planner.choose("do all the quests", world.observe(), None)
        if call is not None:
            world.step(call.tool, call.args)

    for _ in range(DEPTH - 1):
        nxt = planner.choose("do all the quests", world.observe(), None)
        if nxt is None:
            break
        world.step(nxt.tool, nxt.args)


def sample(world: World, planner: QuestPlanner, streak: int,
           last_ok: Optional[bool], last_tool: Optional[str],
           last_args: Optional[Dict[str, Any]], last_note: Optional[str],
           obs_kind: str) -> Tuple[List[float], int, List[float]]:
    """Fork the world four ways and keep the decision that actually progressed."""
    base = world.objectives_completed
    gains: List[float] = []
    for decision in DECISIONS:
        twin = world.clone()
        guide = planner.fork()
        _apply(twin, decision, guide, last_tool, last_args)
        gains.append(float(twin.objectives_completed - base))

    best = max(range(len(gains)), key=lambda i: gains[i])
    # If nothing helps, the honest label is "continue": doing nothing unusual is the
    # conservative choice when every option is equally useless.
    if all(g <= 0 for g in gains):
        best = DECISIONS.index("continue")
    return features(world, streak, last_ok, last_tool, last_note, obs_kind), best, gains


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--states", type=int, default=400)
    ap.add_argument("--levels", type=int, nargs="+", default=[20, 40, 60, 80])
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "decision_sft")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--noise", action="store_true", default=True)
    ap.add_argument("--travel-cost", action="store_true")
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    world = World(level=args.levels[0], seed=args.seed, max_steps=10 ** 6,
                  noise=args.noise, travel_cost=args.travel_cost)
    planner = QuestPlanner(level_hint=args.levels[0], curriculum="random",
                           seed=args.seed, ordering="nearest")
    streak, last_ok, last_tool, last_args, last_note = 0, None, None, None, None

    guard = 0
    while len(rows) < args.states and guard < args.states * 400:
        guard += 1
        if world.steps % 200 == 0 and world.steps:
            level = rng.choice(args.levels)
            world = World(level=level, seed=rng.randint(0, 10 ** 6),
                          max_steps=10 ** 6, noise=args.noise,
                          travel_cost=args.travel_cost)
            planner = QuestPlanner(level_hint=level, curriculum="random",
                                   seed=rng.randint(0, 10 ** 6), ordering="nearest")
            streak, last_ok, last_tool, last_args, last_note = 0, None, None, None, None

        # Only record states that follow a failure. When the last action worked,
        # "continue" wins trivially and the corpus fills with rows where the decision
        # does not matter -- which is how a decision dataset ends up teaching nothing.
        if streak >= 1:
            obs = world.observe()
            x, best, gains = sample(world, planner, streak, last_ok, last_tool,
                                    last_args, last_note, world.last_obs_kind)
            rows.append({"x": x, "y": best, "gains": gains,
                         "decision": DECISIONS[best], "teacher": DECISIONS[0],
                         "took_effect": gains[best] > 0,
                         "margin": round(sorted(gains, reverse=True)[0]
                                         - sorted(gains, reverse=True)[1], 4),
                         "note": (last_note or "")[:60]})

        call = planner.choose("do all the quests", world.observe(), None)
        if call is None:
            world.steps += 1
            continue
        ok, note = world.step(call.tool, call.args)
        planner.note_result(call.tool, ok, note, world.observe())
        last_ok, last_tool, last_args, last_note = ok, call.tool, dict(call.args), note
        streak = 0 if ok else streak + 1

    split = int(len(rows) * 0.85)
    for name, part in (("train", rows[:split]), ("val", rows[split:])):
        with (args.out / (name + ".jsonl")).open("w", encoding="utf-8") as fh:
            for row in part:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")

    counts = {d: sum(1 for r in rows if r["decision"] == d) for d in DECISIONS}
    gains_by = {d: round(sum(r["gains"][i] for r in rows) / max(1, len(rows)), 4)
                for i, d in enumerate(DECISIONS)}
    ties = sum(1 for r in rows if r["margin"] <= 0)
    stats = {"rows": len(rows), "train": split, "val": len(rows) - split,
             "decisions": counts, "mean_gain_by_constant": gains_by,
             "effective_rate": round(sum(1 for r in rows if r["took_effect"]) / max(1, len(rows)), 3),
             "exact_ties": round(ties / max(1, len(rows)), 3),
             "features": len(rows[0]["x"]) if rows else 0, "depth": DEPTH,
             "travel_cost": args.travel_cost}
    (args.out / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
