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
import logging
logger = logging.getLogger(__name__)

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

from agent.features import objective_type  # noqa: E402
from agent.planner import QuestPlanner  # noqa: E402
from ai.train_planner_student import TOOLS, features  # noqa: E402
from sim.world import World  # noqa: E402

_STACK = None
_FORESIGHT = None
_MODELS_LOADED = False


def _ensure_ensemble_models():
    global _STACK, _FORESIGHT, _MODELS_LOADED
    if _MODELS_LOADED:
        return _STACK, _FORESIGHT
    _MODELS_LOADED = True
    try:
        from agent.ensemble import ToolStack
        from agent.foresight import load as load_foresight
        _STACK = ToolStack.load()
        _FORESIGHT = load_foresight()
    except Exception:
        _STACK = None
        _FORESIGHT = None
    return _STACK, _FORESIGHT


def compute_ensemble_uncertainty(
    x: List[float],
    teacher_tool: str,
    obs: Dict[str, Any],
) -> Tuple[float, bool]:
    """Computes active ensemble disagreement / margin uncertainty for DAgger sampling.

    Returns:
        (uncertainty: float in [0.0, 1.0], is_high_uncertainty: bool)
    """
    stack, foresight = _ensure_ensemble_models()
    if stack is not None and foresight is not None:
        try:
            width = getattr(foresight, "width", 42)
            x_state = x[:width] if len(x) > width else x
            cells = {row["tool"]: row for row in foresight.rank(x_state, TOOLS)}
            if len(cells) >= 2:
                # Mock uniform baseline logits for candidate ranking
                base_logits = [0.0] * len(TOOLS)
                context = stack.context(x) if hasattr(stack, "context") else None
                ranked = stack.rank(base_logits, cells, TOOLS, context=context)
                if ranked and len(ranked) >= 2:
                    top_tool = ranked[0]["tool"]
                    s0 = float(ranked[0]["score"])
                    s1 = float(ranked[1]["score"])
                    margin = max(0.0, s0 - s1)
                    # High uncertainty when top 2 candidate scores are close
                    margin_u = 1.0 / (1.0 + margin * 2.0)
                    # Disagreement bonus when stack disagrees with deterministic teacher
                    disagreement = 0.35 if top_tool != teacher_tool else 0.0
                    total_u = min(1.0, margin_u + disagreement)
                    return round(float(total_u), 4), bool(total_u >= 0.40)
        except Exception as exc:
            logger.debug("ignored %s", exc, exc_info=True)

    # Proxy uncertainty from feature state ambiguity (hops, inventory, level gap)
    # x indices: 0 is travel_hops (normalized), 1 is carried, 2 is map_match
    hops = x[0] if len(x) > 0 else 0.0
    carried = x[1] if len(x) > 1 else 0.0
    map_match = x[2] if len(x) > 2 else 0.0
    proxy_u = 0.20
    if hops > 0 and map_match == 0:
        proxy_u += 0.25
    if carried == 0:
        proxy_u += 0.15
    return round(float(min(1.0, proxy_u)), 4), bool(proxy_u >= 0.45)


def _perturb(world: World, rng: random.Random) -> None:
    """Create the states where the obvious action is the wrong one."""
    roll = rng.random()
    if roll < 0.34:
        world.map_id = None                      # nowhere: must travel first
    elif roll < 0.67:
        world.inventory.clear()                  # nothing carried: must gather
    # else: leave the world as it is


def episode(level: int, steps: int, seed: int, rng: random.Random,
            deadline: float = 0.0, noise: bool = False,
            calibrated: bool = False, dagger: bool = False,
            min_uncertainty: float = 0.40, dagger_only: bool = False) -> List[Dict[str, Any]]:
    world = World(level=level, seed=seed, max_steps=steps, noise=noise, calibrated=calibrated)
    planner = QuestPlanner(level_hint=level)
    planner._client_quests |= set(planner._curriculum(level))
    rows: List[Dict[str, Any]] = []

    for _ in range(steps):
        if deadline and time.time() > deadline:
            break
        if rng.random() < 0.15:
            _perturb(world, rng)

        obs = world.observe()
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
        feat_vec = features(obj, q.level, step_idx, obs)

        # Active uncertainty evaluation for DAgger
        u_score, is_dagger = compute_ensemble_uncertainty(feat_vec, call.tool, obs)
        if dagger_only and not is_dagger:
            # Skip low-uncertainty easy steps to focus exclusively on high-leverage samples
            world.step(call.tool, call.args)
            continue

        before = world.objectives_completed
        ok, note = world.step(call.tool, call.args)
        advanced = world.objectives_completed > before

        row = {
            "ep": seed,
            "t": len(rows),
            "quest_id": q.id,
            "step": step_idx,
            "objective_type": objective_type(obj),
            "x": feat_vec,
            "y": TOOLS.index(call.tool) if call.tool in TOOLS else -1,
            "tool": call.tool,
            "ok": bool(ok),
            "advanced": bool(advanced),
            "note": note[:60],
        }
        if dagger:
            row["uncertainty"] = u_score
            row["is_dagger"] = is_dagger
        if calibrated:
            row["latency_ms"] = getattr(world, "last_latency_ms", 0.0)

        rows.append(row)
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
    ap.add_argument("--noise", action="store_true",
                    help="stochastic actions, stale and partial observations")
    ap.add_argument("--calibrated", action="store_true",
                    help="use sim-to-real calibrated latency and failure distributions")
    ap.add_argument("--dagger", action="store_true",
                    help="enable active uncertainty-driven DAgger sampling")
    ap.add_argument("--dagger-only", action="store_true",
                    help="save only high-uncertainty DAgger query rows")
    ap.add_argument("--dagger-min-uncertainty", type=float, default=0.40,
                    help="minimum uncertainty threshold to flag a DAgger sample")
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
            rows = episode(
                level,
                args.steps,
                args.seed + i,
                rng,
                args.deadline_epoch,
                noise=args.noise,
                calibrated=args.calibrated,
                dagger=args.dagger or args.dagger_only,
                min_uncertainty=args.dagger_min_uncertainty,
                dagger_only=args.dagger_only,
            )
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
        if args.noise:
            cmd.append("--noise")
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


