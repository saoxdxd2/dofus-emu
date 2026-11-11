"""How good is the retrained ranker, measured against the solver it learned from.

The honest framing first: this model is *distilled* from the solver, so the
solver is its ceiling. It cannot be better than its teacher on the teacher's own
score. The only questions that matter are therefore:

1. **Agreement** -- when the ranker picks a plan, is it the plan the solver also
   called best? A ranker that disagrees with its teacher is making things worse,
   because the teacher is the thing that defines "better" here.
2. **Damage** -- when it disagrees, how much solver-score does it give up? Zero
   means it picked an equally-good (tied) plan; negative is a real regression.
3. **Correlation** -- does its ordering track the solver's ordering at all, or is
   it noise that happens to land on a winner? Measured as Spearman within board.
4. **Headroom** -- how much of the agreement is free? The solver calls 11 of 16
   plans equal, so a coin flip inside the winning set already agrees often.

    python scratch/measure_ranker_quality.py [boards]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from combat import grid_infer  # noqa: E402
from combat.battle_solver import BattleSolver, Board  # noqa: E402


def boards(limit: int = 600):
    path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm" / "fight_boards.jsonl"
    out = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            payload = row.get("board") if isinstance(row, dict) else None
            if isinstance(payload, dict):
                out.append(payload)
            if len(out) >= limit:
                break
    return out


SOLVER_MS = []


def distinct_cases(payloads, want: int):
    """Boards with >=2 legal plans, de-duplicated: the file repeats turns."""
    import time

    solver = BattleSolver(beam_width=8, max_actions=2)
    found, seen = [], set()
    for payload in payloads:
        try:
            board = Board.from_fight_state(payload)
        except Exception:
            continue
        if board.me is None or not board.me.ap:
            continue
        start = time.perf_counter()
        plans = solver.plan_candidates(board, 16)
        SOLVER_MS.append((time.perf_counter() - start) * 1000.0)
        if len(plans) < 2:
            continue
        key = (tuple(sorted((f.id, f.cell, f.hp) for f in board.fighters)),
               board.me.cell, board.me.ap, board.me.mp)
        if key in seen:
            continue
        seen.add(key)
        found.append((board, plans))
        if len(found) >= want:
            break
    return found


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2:
        return 1.0
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean()
    rb -= rb.mean()
    denom = np.sqrt((ra * ra).sum() * (rb * rb).sum())
    return float((ra * rb).sum() / denom) if denom else 0.0


def main() -> int:
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    cases = distinct_cases(boards(), want)
    if not cases:
        print("no usable boards")
        return 1

    agree = 0
    in_best = 0
    deltas = []
    strict_loss = 0
    rho = []
    best_sizes = []
    plan_counts = []
    scored = 0
    solver_pick_total = 0.0
    model_pick_total = 0.0
    solver_ms = []
    grid_ms = []
    import time

    for board, plans in cases:
        t0 = time.perf_counter()
        ranked = grid_infer.rank_plans(board, plans)
        grid_ms.append((time.perf_counter() - t0) * 1000.0)
        if ranked is None:
            continue
        order, _meta = ranked
        scored += 1
        picked = order[0][1]
        scores = np.asarray([p.score for p in plans], dtype=float)
        best = float(scores.max())
        winners = np.flatnonzero(np.isclose(scores, best))
        best_sizes.append(len(winners))
        plan_counts.append(len(plans))
        solver_pick_total += best
        model_pick_total += float(picked.score)

        if picked is plans[0]:
            agree += 1
        if picked.score >= best - 1e-9:
            in_best += 1
        delta = picked.score - best
        deltas.append(delta)
        if delta < -1e-9:
            strict_loss += 1
        model_scores = np.asarray([dict((id(p), s) for s, p in order)[id(p)]
                                   for p in plans], dtype=float)
        rho.append(spearman(model_scores, scores))

    deltas = np.asarray(deltas)
    rho = np.asarray(rho)
    best_sizes = np.asarray(best_sizes)
    plan_counts = np.asarray(plan_counts)

    print("boards scored            %d" % scored)
    print("mean plans per board     %.1f" % plan_counts.mean())
    print("mean tied-best set       %.2f" % best_sizes.mean())
    print()
    print("AGREEMENT")
    print("  picks the solver's top plan      %5.1f%%" % (100.0 * agree / scored))
    print("  picks any tied-best plan         %5.1f%%" % (100.0 * in_best / scored))
    # Random pick among the plans actually offered, not among 16 slots: the
    # previous version divided by MAX_PLANS and reported 13.6%, which flattered
    # the model by a factor of three.
    print("  free baseline (random in list)   %5.1f%%"
          % (100.0 * (best_sizes / plan_counts).mean()))
    print()
    print("DAMAGE when it disagrees")
    print("  mean solver-score given up       %+.4f" % deltas.mean())
    print("  worst case                       %+.4f" % deltas.min())
    print("  boards with a strictly worse pick %4d (%.1f%%)"
          % (strict_loss, 100.0 * strict_loss / scored))
    print("  total score: solver %.1f vs model %.1f  (%+.1f, %+.2f%%)"
          % (solver_pick_total, model_pick_total,
             model_pick_total - solver_pick_total,
             100.0 * (model_pick_total - solver_pick_total) / solver_pick_total))
    print()
    print("CORRELATION with the teacher (Spearman, within board)")
    print("  mean                             %+.3f" % rho.mean())
    print("  median                           %+.3f" % np.median(rho))
    print("  boards below zero                %4d (%.1f%%)"
          % (int((rho < 0).sum()), 100.0 * (rho < 0).mean()))
    print()
    print("COST (per board, this machine, one thread)")
    print("  grid rank_plans                  %6.1f ms" % np.median(grid_ms))
    print("  solver plan_candidates           %6.1f ms" % np.median(SOLVER_MS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
