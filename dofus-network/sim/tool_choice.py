"""Counterfactual tool corpus: which tool *would* have worked, for every tool.

The corpus this replaces had two labels — `farm.start` and `quest.advance` — out of
eighteen registered tools, and its label was "what the teacher did". Both are fatal in
the same way: a model cannot learn to choose an action that never appears in its data,
and imitation cannot tell you that the teacher was wrong.

So this generator does what `sim/decisions.py` already proved works for the repair
decision, one level down: for a state, it forks the world once per candidate tool,
runs the teacher forward a few steps in each fork, and labels the tool whose fork
completed the most objectives. The label is a fact about the world, not an opinion
about the rules.

What that buys, concretely:

* **Label variance.** Eight candidates per state, drawn from the whole registry, so
  the action head is 18-wide and every class is populated.
* **Preconditions become learnable.** `farm.start` on a talk objective fails; the
  fork says so. A model trained on this learns "match the tool to the objective"
  rather than "the teacher emits farm.start a lot".
* **Reads get a value.** `inv.list` and `state.snapshot` clear a stale or partial
  observation, so when the observation is stale their fork genuinely gains more than
  acting on a lie. That is the only reason a read should ever be chosen, and it is
  now measurable instead of assumed.

Cost: |candidates| forks of DEPTH steps per row. A fork is dict copies — the quest
table is shared by reference — so this is cheap, but it is not free: expect roughly
an order of magnitude fewer rows per second than pure imitation.

    python sim/tool_choice.py --episodes 120 --steps 90 --noise --out data/tool_sft
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.features import (TOOLS, TOOL_INDEX, objective_features,  # noqa: E402
                            objective_type)
from agent.planner import QuestPlanner, args_for_tool  # noqa: E402
from sim.world import World  # noqa: E402

DEPTH = 5

# The plausible set per objective bucket. Every entry is a tool that a competent
# player might reach for; the point is to include the *wrong but tempting* ones,
# because a corpus of only-good-options teaches nothing about discrimination.
ALTERNATIVES: Dict[str, List[str]] = {
    "kill":   ["fight.join", "nav.goto", "inv.list", "state.snapshot"],
    "fight":  ["fight.join", "farm.start", "nav.goto", "fight.pass", "state.snapshot"],
    "talk":   ["quest.advance", "nav.goto", "quest.list", "state.snapshot"],
    "deliver": ["farm.start", "quest.advance", "nav.goto", "inv.list", "inv.sell"],
    "map":    ["nav.goto", "state.snapshot", "knowledge.search"],
    "craft":  ["inv.list", "knowledge.search", "state.snapshot", "nav.goto"],
    "area":   ["nav.goto", "knowledge.search", "state.snapshot", "quest.list"],
    "other":  ["quest.list", "state.snapshot", "knowledge.search", "nav.goto"],
}

BUCKET = {0: "kill", 6: "fight", 14: "kill", 16: "kill", 1: "talk", 9: "talk",
          2: "deliver", 3: "deliver", 12: "deliver", 4: "map", 17: "craft", 5: "area"}


def bucket_of(obj: Dict[str, Any]) -> str:
    return BUCKET.get(objective_type(obj), "other")


def candidates(teacher_tool: str, obj: Dict[str, Any], rng: random.Random,
               limit: int = 8, noise_pick: int = 2) -> List[str]:
    """Teacher's tool first (tie-break anchor), then the plausible set, then noise.

    `farm.start` is a standing farm order in the live agent. Quest mode only uses
    it to gather the item a delivery needs; on a kill objective the planner refuses
    it, because that would turn a quest into an open-ended farm session. The
    simulator happily completes a kill with it, so leaving it in the candidate set
    made it win two thirds of the rows -- the network spent its capacity learning
    an action the runtime gates. The candidate set is the *runtime's* action set,
    so it is excluded everywhere except the delivery bucket.
    """
    bucket = bucket_of(obj)
    allowed = lambda tool: tool != "farm.start" or bucket == "deliver"
    pool = ([teacher_tool] if allowed(teacher_tool) else [])
    pool += [tool for tool in ALTERNATIVES[bucket] if allowed(tool)]
    # Two tools from anywhere in the registry: this is how the model learns that
    # hdv.scan does not advance a quest, instead of never having heard of it.
    noise_pool = [tool for tool in TOOLS if allowed(tool)]
    pool += rng.sample(noise_pool, min(noise_pick, len(noise_pool)))
    seen, out = set(), []
    for tool in pool:
        if tool in seen or tool not in TOOL_INDEX:
            continue
        seen.add(tool)
        out.append(tool)
        if len(out) >= limit:
            break
    return out


def _fork_score(world: World, planner: QuestPlanner, tool: str, args: Dict[str, Any],
                depth: int, mode: str = "rate") -> Tuple[float, List[int]]:
    """Fork once, and score the candidate by progress *per step*.

    Total progress over a fixed horizon does not discriminate: with three steps
    available, a wasted read still finishes the objective, so every candidate ties and
    the label collapses back into "what the teacher did". What separates them is the
    rate — one objective in one step beats one objective in three.

    Two ways to compute it, and the difference matters:

    * ``rate`` — max(cumulative / steps). Coarse: it can only take the values 1, 1/2,
      1/3, 1/4, 1/5, so candidates land on the same number constantly and the
      tie-break, not the world, decides the label. Measured: 83% of soul-stone rows
      were ambiguous under this score.
    * ``cummean`` — mean cumulative gain over the horizon. Rewards speed and also
      rewards *not stalling*: finishing at step 2 scores 0.8, at step 3 scores 0.6.
      Continuous, so fewer ties and less of the label decided by a coin flip.
    """
    base = world.objectives_completed
    cum: List[int] = []
    twin = world.clone()
    guide = planner.fork()
    ok, _ = twin.step(tool, args)
    cum.append(twin.objectives_completed - base)
    for _ in range(max(0, depth - 1)):
        call = guide.choose("do all the quests", twin.observe(), None)
        if call is None:
            break
        twin.step(call.tool, call.args)
        cum.append(twin.objectives_completed - base)
    if mode == "cummean":
        return (sum(cum) / float(max(1, len(cum)))), cum
    best = 0.0
    for i, done in enumerate(cum, start=1):
        if done > 0:
            best = max(best, done / float(i))
    return best, cum


def _perturb(world: World, rng: random.Random) -> None:
    """The states where the obvious action is the wrong one.

    Note what is *not* here: `map_id = None`. An earlier version used it to mean
    "nowhere", and it produced a dead end — no map, no destination the objective
    names, so every tool failed and three quarters of the corpus was a tie between
    options that could not work. A failure the agent cannot recover from is not a
    decision, it is a hole in the world. Being on the *wrong* map is the real
    failure, and it has a real recovery.
    """
    roll = rng.random()
    if roll < 0.30:
        # A real map from the KB, not a made-up id: a wrong place has to be
        # reachable, or the recovery is a hole rather than a decision.
        places = [int(q.start_map) for q in world.quests.values() if q.start_map]
        if places:
            world.map_id = int(rng.choice(places))
            world.visited.add(world.map_id)
    elif roll < 0.60:
        world.inventory.clear()                                # nothing carried
    elif roll < 0.70:
        # A full bag: the only state in which selling is a precondition rather than
        # a waste of a step. Without it inv.sell can never win a fork.
        for i in range(world.capacity):
            world.inventory[900000 + i] = 1


def _planned_objective(world: World, planner: QuestPlanner
                       ) -> Optional[tuple]:
    """The objective the planner's last call is about.

    The world's `current_objective()` is the oldest active quest. The planner may
    deliberately work on a different one, and featurising the oldest while storing a
    label for another quest teaches the model a mapping that does not exist.
    """
    qid = planner.current_quest
    oid = planner.current_objective
    if qid is None or oid is None:
        return None
    quest = world.quests.get(int(qid))
    if quest is None:
        return None
    for step_idx, step in enumerate(quest.steps):
        for obj_idx, obj in enumerate(step.get("objectives") or []):
            if obj.get("id") == oid:
                return quest, step_idx, obj_idx, obj
    return None


def episode(level: int, steps: int, seed: int, rng: random.Random,
            noise: bool = True, depth: int = DEPTH,
            focus: Optional[int] = None,
            score_mode: str = "rate",
            travel_cost: bool = False,
            ordering: str = "step",
            behavior: str = "teacher",
            model: Optional[Path] = None) -> List[Dict[str, Any]]:
    world = World(level=level, seed=seed, max_steps=steps, noise=noise,
                  travel_cost=travel_cost)
    # Stand the character on an NPC that offers a quest of the focused type. G31 only lets the
    # planner start an id the client reported, and the harness reports an offer through the
    # dialog link, so an episode that begins in the middle of nowhere can never accept anything.
    if focus is not None:
        for quest in sorted(world.quests.values(), key=lambda q: q.id):
            if not quest.start_map or not world.can_start(quest):
                continue
            first = next((o for s in quest.steps
                          for o in (s.get("objectives") or []) if o), None)
            if first is not None and objective_type(first) == focus:
                world.place_at(int(quest.start_map))
                break
    if behavior == "nn":
        # A DAgger round: walk the states the deployed policy actually reaches,
        # then label every candidate by the same fork. The teacher corpus alone
        # only covers the teacher's trajectory, so the student never sees the
        # recovery states its own mistakes create.
        from agent.planner_nn import NNPlanner
        planner: Any = NNPlanner(level_hint=level, curriculum="random", seed=seed,
                                 focus=focus, ordering=ordering,
                                 model_path=model)
    else:
        planner = QuestPlanner(level_hint=level, curriculum="random", seed=seed,
                               focus=focus, ordering=ordering)
    rows: List[Dict[str, Any]] = []

    for _ in range(steps):
        if rng.random() < 0.10:
            _perturb(world, rng)

        obs = world.observe()
        call = planner.choose("do all the quests", obs, None)
        if call is None:
            break
        current = _planned_objective(world, planner)
        if current is None:
            # Accepting a quest: a real step, but there is no objective to featurise.
            ok, note = world.step(call.tool, call.args)
            planner.note_result(call.tool, ok, note, world.state())
            continue

        quest, step_idx, obj_idx, obj = current
        ctx = planner.tracker.ctx()
        x = objective_features(obj, int(quest.level or level), step_idx, obs, ctx)

        gains: Dict[str, float] = {}
        notes: Dict[str, str] = {}
        base = world.objectives_completed
        for tool in candidates(call.tool, obj, rng):
            args = args_for_tool(tool, planner.quests.get(quest.id) or {"id": quest.id}, obj, obs)
            score, cum = _fork_score(world, planner, tool, args, depth, score_mode)
            gains[tool] = round(score, 4)
            notes[tool] = ",".join(str(c) for c in cum)

        best = max(gains.values())
        # Margin matters: a row where the top two candidates are 0.02 apart carries
        # almost no signal, and the trainer should know that. Recorded per row so the
        # weight can be set from data rather than from a hunch.
        ordered = sorted(gains.values(), reverse=True)
        margin = ordered[0] - ordered[1] if len(ordered) > 1 else ordered[0]
        winner = next(t for t in gains if gains[t] == best)
        # Ties go to the teacher: when several tools work equally well, the corpus
        # should not invent a preference that the world does not have.
        if gains.get(call.tool) == best:
            winner = call.tool

        ok, note = world.step(call.tool, call.args)
        planner.note_result(call.tool, ok, note, world.state())
        advanced = world.objectives_completed > base

        rows.append({
            "ep": seed, "t": len(rows),
            "quest_id": quest.id, "step": step_idx,
            "objective_type": objective_type(obj),
            "x": x,
            "y": TOOL_INDEX[winner],
            "tool": winner,
            "teacher": call.tool,
            "agrees": winner == call.tool,
            # Every candidate scored zero: nothing could advance the objective within
            # the horizon, so the "winner" is only the tie-break. Such a row teaches
            # the preconditions but carries no counterfactual information, and the
            # trainer weights it down rather than pretending it is evidence.
            "trivial": best <= 0.0,
            "margin": round(margin, 4),
            "gains": gains,
            "curves": notes,
            "ok": bool(ok), "advanced": bool(advanced), "note": note[:60],
        })
    return rows


def _parallel(args: argparse.Namespace) -> int:
    """Children are waited on, not detached: a detached worker dies with the shell."""
    import subprocess
    import tempfile

    args.out.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + args.minutes * 60 if args.minutes else 0.0
    root = Path(tempfile.mkdtemp(prefix="toolchoice_"))
    procs = []
    for i in range(args.parallel):
        cmd = [sys.executable, str(Path(__file__).resolve()),
               "--episodes", str(args.episodes), "--steps", str(args.steps),
               "--seed", str(args.seed + i * 977), "--depth", str(args.depth),
               "--score", args.score,
               "--levels", *[str(x) for x in args.levels],
               "--focus", *[str(x) for x in args.focus],
               "--out", str(root / ("w%d" % i))]
        if args.noise:
            cmd.append("--noise")
        if args.travel_cost:
            cmd.append("--travel-cost")
        cmd += ["--ordering", args.ordering, "--val-every", str(args.val_every),
                "--behavior", args.behavior]
        if args.model:
            cmd += ["--model", str(args.model)]
        if deadline:
            cmd += ["--deadline-epoch", "%.0f" % deadline]
        procs.append(subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE))

    tails: List[str] = []
    for proc in procs:
        proc.wait()
        out = (proc.stdout.read() or b"").decode("utf-8", "replace").strip()
        tails.append(out)
        if proc.returncode != 0:
            # The first version discarded this stream, so a crashed worker showed
            # up only as "0 rows" and the real exception was lost.
            err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()
            print("worker failed (exit %d):\n%s" % (proc.returncode, err), file=sys.stderr)

    counts = {"train": 0, "val": 0}
    tools: Counter = Counter()
    with (args.out / "train.jsonl").open("w", encoding="utf-8") as tr, \
            (args.out / "val.jsonl").open("w", encoding="utf-8") as va:
        for i in range(args.parallel):
            for name, sink in (("train", tr), ("val", va)):
                part = root / ("w%d" % i) / (name + ".jsonl")
                if not part.exists():
                    continue
                with part.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        sink.write(line)
                        counts[name] += 1
                        try:
                            tools[json.loads(line)["tool"]] += 1
                        except Exception:  # noqa: BLE001
                            pass

    total = counts["train"] + counts["val"]
    if total == 0:
        # A generator that quietly yields nothing is worse than one that crashes: the corpus on
        # disk keeps looking authoritative while every future number is stale. This is exactly
        # how the offer/focus bug hid for five days.
        print("0 rows from %d workers; worker output:\n%s"
              % (args.parallel, "\n---\n".join((t or "<empty>")[-600:] for t in tails)),
              file=sys.stderr)
        return 1
    stats = {"workers": args.parallel, "rows": total, **counts,
             "minutes": args.minutes, "depth": args.depth,
             "score_mode": args.score, "travel_cost": args.travel_cost,
             "val_every": args.val_every, "behavior": args.behavior,
             "tools": {k: round(100.0 * v / max(1, total), 1) for k, v in tools.most_common()}}
    (args.out / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(json.dumps(stats, indent=1))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--episodes", type=int, default=120)
    ap.add_argument("--steps", type=int, default=90)
    ap.add_argument("--levels", type=int, nargs="+", default=[20, 40, 60, 80, 100])
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "tool_sft")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--depth", type=int, default=DEPTH)
    ap.add_argument("--noise", action="store_true",
                    help="stochastic actions, stale and partial observations")
    ap.add_argument("--deadline-epoch", type=float, default=0.0)
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--minutes", type=float, default=0.0)
    ap.add_argument("--travel-cost", action="store_true",
                    help="travel costs hops instead of one free step")
    ap.add_argument("--ordering", choices=("step", "nearest"), default="nearest",
                    help="pursue the closest open objective rather than the KB's order")
    ap.add_argument("--score", choices=("rate", "cummean"), default="rate",
                    help="how a fork is scored: max progress per step, or mean "
                         "cumulative progress (finer, fewer ties)")
    ap.add_argument("--focus", type=int, nargs="+", default=[0, 1, 3, 6, 9, 2, 12, 14, 16, 4],
                    help="objective types to steer episodes at, cycled; keeps the "
                         "corpus from collapsing onto whatever the shortest quests do")
    ap.add_argument("--behavior", choices=("teacher", "nn"), default="teacher",
                    help="policy that walks the episode. `nn` is a DAgger round: "
                         "states come from the deployed model, labels still come "
                         "from counterfactual forks")
    ap.add_argument("--model", type=Path, default=Path("models/tool_policy.onnx"),
                    help="tool policy used by --behavior nn")
    ap.add_argument("--val-every", type=int, default=5,
                    help="hold out every Nth complete episode. Splitting by row "
                         "instead puts the tail of a trajectory in val while its "
                         "head is in train, so the reported score measures memory "
                         "of that episode, not generalisation")
    args = ap.parse_args(argv)

    if args.parallel > 1:
        return _parallel(args)

    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    parts: Dict[str, List[Dict[str, Any]]] = {"train": [], "val": []}
    started = time.time()
    focus_cycle = args.focus
    for i in range(args.episodes):
        if args.deadline_epoch and time.time() > args.deadline_epoch:
            break
        level = args.levels[i % len(args.levels)]
        focus = focus_cycle[i % len(focus_cycle)] if focus_cycle else None
        rows = episode(level, args.steps, args.seed + i, rng, args.noise,
                       args.depth, focus, args.score, args.travel_cost,
                       args.ordering, args.behavior, args.model)
        name = "val" if (i + 1) % max(1, args.val_every) == 0 else "train"
        parts[name].extend(rows)

    for name, part in parts.items():
        with (args.out / (name + ".jsonl")).open("w", encoding="utf-8") as fh:
            for row in part:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    rows = parts["train"] + parts["val"]
    split = len(parts["train"])

    tools = Counter(r["tool"] for r in rows)
    total = max(1, len(rows))
    trivial = sum(1 for r in rows if r.get("trivial"))
    stats = {
        "rows": len(rows), "train": split, "val": len(rows) - split,
        "features": len(rows[0]["x"]) if rows else 0, "depth": args.depth,
        "noise": args.noise, "val_every": args.val_every,
        "seconds": round(time.time() - started, 1),
        "rows_per_s": round(len(rows) / max(0.001, time.time() - started), 1),
        "agreement_with_teacher": round(sum(1 for r in rows if r["agrees"]) / total, 3),
        "score_mode": args.score,
        "mean_margin": round(sum(r.get("margin") or 0 for r in rows) / total, 4),
        "near_ties": round(sum(1 for r in rows if (r.get("margin") or 0) <= 0.05) / total, 3),
        "trivial_rate": round(trivial / total, 3),
        "tools": {k: round(100.0 * v / total, 1) for k, v in tools.most_common()},
        "covered": sum(1 for v in tools.values() if v / total >= 0.05),
    }
    (args.out / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
