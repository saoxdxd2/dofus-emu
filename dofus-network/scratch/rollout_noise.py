"""How much of a plan's measured outcome is noise?

`rollout_truth` plays the rest of the duel out with the solver on both sides,
which is deterministic: two rollouts of the same plan give bit-identical results
(measured, max |r1 - r2| = 0.0000 on 12 boards). That is not the same as the
plan's *expected* outcome. The duel is chaotic -- a tie broken differently on
turn 4 changes the final HP by a large fraction of a bar -- so one continuation
is a single sample from a wide distribution, and training on it means training on
the variance of that distribution.

This measures the width directly. A plan is rolled out R times under a stochastic
playout policy (the solver's plan 80% of the time, a Gumbel draw over its scores
otherwise), and the standard deviation across those R continuations is the noise
floor of the target.

The comparison that matters is against the quantity the ranker is supposed to
learn: the gap between the plan the solver picks and the best available one,
measured at 0.0177 HP-differential units over 60 boards. If the continuation
noise is several times that, no amount of model capacity can rank the plans,
because the differences it must resolve are smaller than the noise in the labels.

Usage::

    python scratch/rollout_noise.py --boards 20 --rollouts 6
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, List, Optional

import numpy as np

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from combat import duel_value as dv  # noqa: E402


def noisy_solver(state: Any, actor: Any, plans: List[Any],
                 rng: random.Random, model: Any = None) -> Any:
    """The solver's plan, 80% of the time; otherwise a Gumbel draw over scores."""
    if rng.random() < 0.8 or len(plans) == 1:
        return plans[0]
    logits = np.asarray([p.score for p in plans], dtype=np.float64)
    return plans[int(dv.gumbel_topk(logits, 1, rng)[0])]


def rollout(state: Any, actor_id: int, plan: Any, solver: Any,
            rng: random.Random, max_rounds: int, k: int) -> float:
    from combat.duel import advance_turn, apply_plan, play_game

    clone = state.clone()
    apply_plan(clone, plan)
    if clone.done:
        return dv.outcome(clone, actor_id)
    advance_turn(clone)
    if clone.done:
        return dv.outcome(clone, actor_id)
    final, _ = play_game(clone, {1: noisy_solver, 2: noisy_solver}, solver, rng,
                         k=k)
    return dv.outcome(final, actor_id)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--boards", type=int, default=20)
    parser.add_argument("--rollouts", type=int, default=6)
    parser.add_argument("--k", type=int, default=6)
    parser.add_argument("--seed", type=int, default=4242)
    args = parser.parse_args(argv)

    from combat.battle_solver import BattleSolver
    from combat.duel import DEFAULT_DATA, make_duel, plans_for
    from combat.duel_benchmark import load_data, sample_pair
    from combat.normal_build_duel import DEFAULT_MAP

    cells = dv._load_cells(DEFAULT_MAP)
    data, levels = load_data(DEFAULT_DATA)
    pool = list(range(min(4096, len(levels))))
    solver = BattleSolver(beam_width=8, max_actions=2)
    rng = random.Random(args.seed)

    per_plan_std: List[float] = []
    board_spread: List[float] = []
    board_means: List[np.ndarray] = []
    built = 0
    while len(board_means) < args.boards:
        row_a, row_b = sample_pair(levels, pool, rng)
        state = make_duel(data, row_a, row_b, cells, rng, max_rounds=12)
        if state is None:
            continue
        built += 1
        actor = state.actor
        plans = plans_for(state, solver, args.k)
        if len(plans) < 2:
            continue
        means = []
        for plan in plans:
            draws = [rollout(state, actor.id, plan, solver,
                             random.Random(args.seed * 31 + built * 7 + index),
                             12, args.k)
                     for index in range(args.rollouts)]
            per_plan_std.append(float(np.std(draws)))
            means.append(float(np.mean(draws)))
        board_means.append(np.asarray(means))
        board_spread.append(float(np.std(means)))
        if len(board_means) % 5 == 0:
            print("  %d/%d boards" % (len(board_means), args.boards), flush=True)

    noise = float(np.mean(per_plan_std))
    true_spread = float(np.mean(board_spread))
    print()
    print("rollouts per plan            : %d" % args.rollouts)
    print("boards                       : %d" % len(board_means))
    print("continuation std per plan    : %.4f   <- the label noise" % noise)
    print("std of plan means (a board)  : %.4f   <- signal + residual noise" % true_spread)
    print("noise of an R-rollout mean   : %.4f" % (noise / np.sqrt(args.rollouts)))
    print()
    print("decision gap to resolve      : 0.0177   (best-of-6 vs solver top-1)")
    print("rollouts needed per plan for the noise to fall below that: %.0f"
          % ((noise / 0.0177) ** 2))
    print()
    report = {"boards": len(board_means), "rollouts": args.rollouts,
              "continuation_std": round(noise, 4),
              "plan_mean_std": round(true_spread, 4),
              "noise_of_mean": round(noise / np.sqrt(args.rollouts), 4),
              "rollouts_for_00177": int(round((noise / 0.0177) ** 2)),
              "per_plan_std": [round(x, 4) for x in per_plan_std],
              "board_spread": [round(x, 4) for x in board_spread]}
    target = REPO / "scratch" / "rollout_noise.json"
    target.write_text(json.dumps(report, indent=1))
    print("wrote %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
