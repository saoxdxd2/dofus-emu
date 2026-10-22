"""Generate training rows by playing the symbolic world, not by reading a table.

The first corpus was 9,600 rows: one row per (objective, state) pair, labelled by the
deterministic teacher. That is enough to imitate a rule and nowhere near enough to
train something bigger than a rule. What a large model needs is *behaviour with
outcomes* — the same decision made in many states, with what happened next attached.

So this runs episodes in `sim.world` and records, at every step:

    features(objective, state)  ->  tool  ->  did the objective actually complete?

The outcome column is the part that matters later: imitation says "the teacher would
do this", the outcome says "this worked". A model trained on both can be corrected
where the teacher is wrong, which is the whole reason to have a model instead of an
`if`.

Failures are injected on purpose (wrong map, missing item, unreachable target) so the
corpus contains the states where the naive action is wrong — the repair cases.

    python sim/rollout.py --episodes 200 --out data/rollout_sft/train.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.planner import QuestPlanner  # noqa: E402
from ai.train_planner_student import TOOLS, features  # noqa: E402
from sim.world import World  # noqa: E402


def _perturb(world: World, rng: random.Random) -> None:
    """Create the states where the obvious action is the wrong one."""
    roll = rng.random()
    if roll < 0.34:
        world.map_id = None                      # nowhere: must travel first
    elif roll < 0.67:
        world.inventory.clear()                  # nothing carried: must gather
    # else: leave the world as it is


def episode(level: int, steps: int, seed: int, rng: random.Random,
            deadline: float = 0.0) -> List[Dict[str, Any]]:
    world = World(level=level, seed=seed, max_steps=steps)
    planner = QuestPlanner(level_hint=level)
    rows: List[Dict[str, Any]] = []

    for _ in range(steps):
        if deadline and time.time() > deadline:
            break
        if rng.random() < 0.15:
            _perturb(world, rng)

        obs = world.observe()
        quest = world.active_quest() or None
        current = world.current_objective()

        call = planner.choose("do all the quests", obs, None)
        if call is None:
            break

        if current is None:
            # No objective is open yet: the call is "accept this quest". It is a
            # real step in the world but there is nothing to featurise, so it is not
            # a training row.
            world.step(call.tool, call.args)
            continue

        q, step_idx, obj_idx, obj = current
        before = world.objectives_completed
        ok, note = world.step(call.tool, call.args)
        advanced = world.objectives_completed > before

        rows.append({
            "ep": seed,
            "t": len(rows),
            "quest_id": q.id,
            "step": step_idx,
            "objective_type": int(obj.get("type_id") or -1),
            "x": features(obj, q.level, step_idx, obs),
            "y": TOOLS.index(call.tool) if call.tool in TOOLS else -1,
            "tool": call.tool,
            "ok": bool(ok),
            "advanced": bool(advanced),
            "note": note[:60],
        })
        if world.steps >= steps:
            break
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--episodes", type=int, default=200)
    ap.add_argument("--steps", type=int, default=120)
    ap.add_argument("--levels", type=int, nargs="+", default=[20, 40, 60, 80, 100])
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "rollout_sft")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--deadline-epoch", type=float, default=0.0,
                    help="stop starting new episodes after this unix time")
    ap.add_argument("--parallel", type=int, default=1,
                    help="run this many child processes and merge their output")
    ap.add_argument("--minutes", type=float, default=0.0,
                    help="with --parallel: wall-clock budget for the whole job")
    args = ap.parse_args(argv)

    if args.parallel > 1:
        return _parallel(args)

    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    train_path = args.out / "train.jsonl"
    val_path = args.out / "val.jsonl"

    written = {"train": 0, "val": 0}
    advanced = 0
    with train_path.open("w", encoding="utf-8") as tr, val_path.open("w", encoding="utf-8") as va:
        for i in range(args.episodes):
            level = args.levels[i % len(args.levels)]
            rows = episode(level, args.steps, args.seed + i, rng, args.deadline_epoch)
            target = va if i % 10 == 0 else tr
            name = "val" if i % 10 == 0 else "train"
            for row in rows:
                target.write(json.dumps(row, separators=(",", ":")) + "\n")
                written[name] += 1
                advanced += 1 if row["advanced"] else 0

    total = written["train"] + written["val"]
    stats = {
        "episodes": args.episodes,
        "rows": total,
        "train": written["train"],
        "val": written["val"],
        "advanced_rate": round(advanced / max(1, total), 3),
        "features": 26,
        "bytes": train_path.stat().st_size + val_path.stat().st_size,
    }
    (args.out / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(json.dumps(stats, indent=1))
    return 0


def _parallel(args: argparse.Namespace) -> int:
    """Children are waited on, not detached.

    A detached worker is killed when the shell call returns, which silently produced
    six files of 33 rows. Keeping the parent in the foreground is the difference
    between a corpus and a stub.
    """
    import subprocess
    import tempfile

    args.out.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + args.minutes * 60 if args.minutes else 0.0
    root = Path(tempfile.mkdtemp(prefix="rollout_"))
    procs = []
    for i in range(args.parallel):
        cmd = [sys.executable, str(Path(__file__).resolve()),
               "--episodes", str(args.episodes), "--steps", str(args.steps),
               "--seed", str(args.seed + i * 977),
               "--out", str(root / ("w%d" % i))]
        if deadline:
            cmd += ["--deadline-epoch", "%.0f" % deadline]
        procs.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.PIPE))

    for proc in procs:
        proc.wait()

    merged_train = args.out / "train.jsonl"
    merged_val = args.out / "val.jsonl"
    counts = {"train": 0, "val": 0}
    with merged_train.open("w", encoding="utf-8") as tr, merged_val.open("w", encoding="utf-8") as va:
        for i in range(args.parallel):
            for name, sink in (("train", tr), ("val", va)):
                part = root / ("w%d" % i) / (name + ".jsonl")
                if not part.exists():
                    continue
                with part.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        sink.write(line)
                        counts[name] += 1

    stats = {"workers": args.parallel, "rows": counts["train"] + counts["val"], **counts,
             "minutes": args.minutes,
             "bytes": merged_train.stat().st_size + merged_val.stat().st_size}
    (args.out / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
