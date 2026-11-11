"""Does the learned value rank plans better than the solver's own score?

The duel benchmark cannot answer this cleanly. Two policies that disagree on a
minority of boards produce mostly-zero paired differences, so the effective
sample size is small and the noise is large -- the 40-board run of 2026-11-11
returned 20/20 and could not distinguish anything.

This asks the question directly, with ground truth. For each real board:

  1. take the solver's k candidate plans,
  2. for each plan, apply it and play the rest of the duel out with a fixed
     playout policy; that final HP differential is the *truth* about the plan,
  3. rank the plans three ways and compare the rankings against the truth.

The rankers:

  ``score``     the solver's one-ply score. The incumbent to beat.
  ``immediate`` the successor's HP differential -- the trivial reading of the
                state, which isolates how much of the value is just "who is
                ahead right now".
  ``value``     V(s') from the outcome-trained head.
  ``random``    calibration: the expected regret of picking blind.

If ``value`` cannot beat ``score`` at predicting the consequence of a plan, the
lookahead policy is a re-parameterisation of the solver and no amount of search
on top of it will help. That is the falsifiable claim, and this script is the
test of it.

Why the truth is played out with the solver rather than with the model: a ground
truth that depends on the thing being judged is not a ground truth. The value was
trained on games where 55% of turns followed the solver and the rest followed a
Gumbel draw over the solver's score, so pure-solver playout is close to
on-policy without being circular.

Usage::

    python scratch/rank_by_truth.py --model scratch/dv_v3.pkl --boards 60
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from combat import duel_value as dv  # noqa: E402


def ranks(values: Sequence[float]) -> np.ndarray:
    """Average ranks, so ties share a rank instead of being broken by index."""
    array = np.asarray(values, dtype=np.float64)
    order = np.argsort(array, kind="mergesort")
    out = np.empty(len(array), dtype=np.float64)
    position = 0
    while position < len(array):
        end = position
        while end + 1 < len(array) and array[order[end + 1]] == array[order[position]]:
            end += 1
        average = 0.5 * (position + end) + 1.0
        for slot in range(position, end + 1):
            out[order[slot]] = average
        position = end + 1
    return out


def spearman(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Rank correlation, or None when either side is constant (undefined)."""
    ra, rb = ranks(a), ranks(b)
    if ra.std() == 0.0 or rb.std() == 0.0:
        return None
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    return float((ra * rb).sum() / np.sqrt((ra * ra).sum() * (rb * rb).sum()))


def truth_of_plan(state: Any, plan: Any, actor_id: int, solver: Any,
                  rng: random.Random, max_rounds: int, k: int) -> float:
    """Final HP differential if this plan is played and the duel runs out."""
    from combat.duel import advance_turn, apply_plan, play_game, solver_policy

    clone = state.clone()
    apply_plan(clone, plan)
    if clone.done:
        return dv.outcome(clone, actor_id)
    advance_turn(clone)
    if clone.done:
        return dv.outcome(clone, actor_id)
    final, _ = play_game(clone, {1: solver_policy, 2: solver_policy}, solver, rng,
                         k=k)
    return dv.outcome(final, actor_id)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, action="append", default=None,
                        help="repeatable: every model is scored on the "
                             "same boards, so the columns are directly "
                             "comparable")
    parser.add_argument("--boards", type=int, default=60)
    parser.add_argument("--k", type=int, default=6)
    parser.add_argument("--beam", type=int, default=8,
                        help="solver beam width; raise it with --k to widen the "
                             "candidate set and test whether the space is the "
                             "bottleneck rather than the ranking")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--max-rounds", type=int, default=12)
    parser.add_argument("--rows", type=int, default=4096)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    from combat.battle_solver import BattleSolver
    from combat.duel import DEFAULT_DATA, make_duel, plans_for
    from combat.duel_benchmark import load_data, sample_pair
    from combat.normal_build_duel import DEFAULT_MAP

    models = []
    for path in (args.model or []):
        if not path.exists():
            print("no model at %s -- skipped" % path)
            continue
        import pickle
        with path.open("rb") as handle:
            payload = pickle.load(handle)
        report = payload.get("report", {})
        name = path.stem
        models.append((name, payload["params"]))
        print("model %-16s %-18s samples %-6s val_ce %-8s val_spearman %-6s"
              % (name, report.get("objective", "value"),
                 report.get("samples", "?"),
                 round(report["val_ce"], 4) if report.get("val_ce") else "-",
                 round(report["val_spearman"], 4) if report.get("val_spearman") else "-"))

    cells = dv._load_cells(DEFAULT_MAP)
    data, levels = load_data(DEFAULT_DATA)
    pool = list(range(min(args.rows, len(levels))))
    solver = BattleSolver(beam_width=args.beam, max_actions=2)
    rng = random.Random(args.seed)

    rows: List[Dict[str, Any]] = []
    skipped_single = skipped_flat = 0
    built = 0
    while len(rows) < args.boards:
        row_a, row_b = sample_pair(levels, pool, rng)
        state = make_duel(data, row_a, row_b, cells, rng,
                          max_rounds=args.max_rounds)
        if state is None:
            continue
        built += 1
        actor = state.actor
        plans = plans_for(state, solver, args.k)
        if len(plans) < 2:
            skipped_single += 1
            continue

        # One RNG per (board, plan) seeded identically: ties inside each rollout
        # break the same way at the first divergence. Beyond that the streams
        # diverge, so this is variance reduction, not a coupled construction.
        truths = [truth_of_plan(state, plan, actor.id, solver,
                                random.Random(args.seed * 7919 + built), args.max_rounds,
                                args.k)
                  for plan in plans]
        if len(set(round(t, 6) for t in truths)) < 2:
            skipped_flat += 1
            continue

        scores = [float(plan.score) for plan in plans]
        immediate = []
        for plan in plans:
            clone = state.clone()
            from combat.duel import apply_plan
            apply_plan(clone, plan)
            immediate.append(dv.outcome(clone, actor.id))

        entry: Dict[str, Any] = {
            "board": len(rows),
            "plans": len(plans),
            "truth": truths,
            "score": scores,
            "immediate": immediate,
            "rho_score": spearman(scores, truths),
            "rho_immediate": spearman(immediate, truths),
        }
        for name, params in models:
            feats = []
            for plan in plans:
                clone = state.clone()
                from combat.duel import apply_plan
                apply_plan(clone, plan)
                feats.append(dv.encode(clone, actor))
            key = "value:" + name
            entry[key] = [float(v) for v in
                          dv.predict(params, np.stack(feats))]
            entry["rho_" + key] = spearman(entry[key], truths)
        rows.append(entry)
        if len(rows) % 10 == 0:
            print("  %d/%d boards" % (len(rows), args.boards), flush=True)

    if not rows:
        print("no usable boards (%d single-plan, %d flat-outcome)"
              % (skipped_single, skipped_flat))
        return 1

    def regret(key: str) -> np.ndarray:
        out = []
        for row in rows:
            truth = np.asarray(row["truth"])
            pick = int(np.argmax(row[key]))
            out.append(float(truth.max() - truth[pick]))
        return np.asarray(out)

    def top1(key: str) -> float:
        hits = 0
        for row in rows:
            truth = np.asarray(row["truth"])
            pick = int(np.argmax(row[key]))
            hits += int(truth[pick] >= truth.max() - 1e-9)
        return hits / float(len(rows))

    def random_regret() -> np.ndarray:
        return np.asarray([float(np.max(row["truth"]) - np.mean(row["truth"]))
                           for row in rows])

    def rho(key: str) -> np.ndarray:
        return np.asarray([row[key] for row in rows
                           if row.get(key) is not None], dtype=np.float64)

    rankers = ["score", "immediate"] + ["value:" + n for n, _ in models]
    table: Dict[str, Any] = {}
    print("\n%-20s %10s %10s %10s %8s" %
          ("ranker", "regret", "top1", "mean_rho", "rho_n"))
    for key in rankers:
        reg = regret(key)
        table[key] = {
            "mean_regret": round(float(reg.mean()), 4),
            "median_regret": round(float(np.median(reg)), 4),
            "top1": round(top1(key), 4),
            "mean_spearman": round(float(rho("rho_" + key).mean()), 4)
            if len(rho("rho_" + key)) else None,
            "spearman_boards": int(len(rho("rho_" + key))),
        }
        print("%-20s %10.4f %10.3f %10s %8d"
              % (key, reg.mean(), top1(key),
                 "%.4f" % rho("rho_" + key).mean() if len(rho("rho_" + key)) else "n/a",
                 len(rho("rho_" + key))))
    blind = random_regret()
    table["random"] = {"mean_regret": round(float(blind.mean()), 4)}
    print("%-20s %10.4f %10s %10s %8s" % ("random", blind.mean(), "-", "-", "-"))

    # Paired comparison: the only number that decides the question.
    for name, _ in models:
        key = "value:" + name
        delta = regret("score") - regret(key)
        se = float(delta.std(ddof=1) / np.sqrt(len(delta))) if len(delta) > 1 else 0.0
        boot = np.random.default_rng(args.seed + 3)
        draws = boot.choice(delta, size=(4000, len(delta)), replace=True).mean(axis=1)
        lo, hi = np.percentile(draws, [2.5, 97.5])
        better = int((delta > 0).sum())
        worse = int((delta < 0).sum())
        table["%s_minus_score" % name] = {
            "mean_regret_reduction": round(float(delta.mean()), 4),
            "ci95": [round(float(lo), 4), round(float(hi), 4)],
            "boards_value_better": better,
            "boards_value_worse": worse,
            "boards_tied": int((delta == 0).sum()),
            "t_stat": round(float(delta.mean() / se), 3) if se > 0 else 0.0,
        }
        print("\n%s vs score, per-board regret reduction" % name)
        print("  mean %+.4f   ci95 [%+.4f, %+.4f]   better %d  worse %d  tied %d"
              % (delta.mean(), lo, hi, better, worse, int((delta == 0).sum())))

    print("\nboards built %d, used %d, skipped %d single-plan / %d flat-outcome"
          % (built, len(rows), skipped_single, skipped_flat))
    print("mean plans per board %.2f" % np.mean([row["plans"] for row in rows]))

    report = {"boards": len(rows), "skipped": {"single": skipped_single,
                                              "flat": skipped_flat},
              "mean_plans": round(float(np.mean([row["plans"] for row in rows])), 3),
              "table": table, "rows": rows}
    target = args.out or (REPO / "scratch" / "rank_by_truth.json")
    target.write_text(json.dumps(report, indent=1))
    print("wrote %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
