"""End-to-end proof that the retrained ranker actually serves a live board.

Four things are checked, in the order they can fail:

1. the checkpoint loads at the live encoder width;
2. `rank_plans` returns a ranking for a real recorded board (this seam was dead
   for the entire life of the 30M model: `_padded` called the training
   featuriser and unpacked five values from a seven-tuple);
3. the ranking is permutation-equivariant -- the same candidates in a different
   order get the same per-plan score, which is what self-attention guarantees and
   what a leftover shuffle would break;
4. zeroing a spatial channel changes the scores. If it does not, the model is
   not reading the input and "the encoder has a coverage channel" would be true
   of the code and false of the model.

    python scratch/verify_14ch_ranker.py
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from combat import grid_dataset, grid_infer, grid_model  # noqa: E402
from combat.battle_solver import BattleSolver, Board  # noqa: E402


def boards(limit: int = 400):
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
            board = row.get("board") if isinstance(row, dict) else None
            if isinstance(board, dict):
                out.append(board)
            if len(out) >= limit:
                break
    return out


def usable(payloads, want: int = 3):
    """Real boards with at least two legal plans, de-duplicated by state.

    `fight_boards.jsonl` records the same turn many times over. Without the
    de-duplication this check "passed" on three copies of one board and would
    have reported identical scores as agreement rather than as one sample.
    """
    solver = BattleSolver(beam_width=8, max_actions=2)
    found = []
    seen = set()
    for payload in payloads:
        try:
            board = Board.from_fight_state(payload)
        except Exception:
            continue
        if board.me is None or not board.me.ap:
            continue
        plans = solver.plan_candidates(board, 16)
        if len(plans) < 3:
            continue
        fingerprint = (tuple(sorted((f.id, f.cell, f.hp) for f in board.fighters)),
                       board.me.cell, board.me.ap, board.me.mp)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        found.append((board, plans))
        if len(found) >= want:
            break
    return found


def main() -> int:
    loaded = grid_infer.load_model()
    if loaded is None:
        print("FAIL  checkpoint refused at load time")
        return 1
    params, config = loaded
    width = int(np.asarray(params["cell_in"][0]).shape[0])
    print("ok    loaded: params %d, cell width %d, config %s"
          % (grid_model.param_count(params), width, config))
    if width != grid_model.CELL_FEATS:
        print("FAIL  width %d != CELL_FEATS %d" % (width, grid_model.CELL_FEATS))
        return 1

    cases = usable(boards())
    if not cases:
        print("FAIL  no recorded board had three legal plans")
        return 1
    tops = []

    for index, (board, plans) in enumerate(cases):
        ranked = grid_infer.rank_plans(board, plans)
        if ranked is None:
            print("FAIL  rank_plans returned None for board %d" % index)
            return 1
        order, meta = ranked
        tops.append(order[0][0])
        print("ok    board %d: %d plans ranked, top score %.3f, solver top %.3f"
              % (index, len(order), order[0][0], max(p.score for p in plans)))

        # (3) permutation equivariance
        rng = random.Random(index)
        shuffled = list(plans)
        rng.shuffle(shuffled)
        again = grid_infer.rank_plans(board, shuffled)
        if again is None:
            print("FAIL  rank_plans returned None on the reordered list")
            return 1
        first = {id(plan): score for score, plan in order}
        second = {id(plan): score for score, plan in again[0]}
        drift = max(abs(first[key] - second[key]) for key in first)
        if drift > 1e-3:
            print("FAIL  scores moved by %.4f when the candidate order changed" % drift)
            return 1
        print("ok    reordering the candidates moved no score (max drift %.2e)" % drift)

        # (4) does the model read the new channels?
        batch = grid_infer._padded(board, plans)
        base = np.asarray(grid_model.forward(
            params, batch["cells"], batch["cell_mask"], batch["fighters"],
            batch["fighter_mask"], batch["plans"], batch["plan_mask"])["scores"])[0]
        for name, channel in (("covered_by_enemy", 12), ("reachable_now", 13)):
            muted = {key: value for key, value in batch.items()}
            cells = np.array(batch["cells"])
            cells[0, :, channel] = 0.0
            muted["cells"] = cells
            after = np.asarray(grid_model.forward(
                params, muted["cells"], muted["cell_mask"], muted["fighters"],
                muted["fighter_mask"], muted["plans"], muted["plan_mask"])["scores"])[0]
            delta = float(np.abs(after[:len(plans)] - base[:len(plans)]).max())
            print("      zeroing %-17s changes scores by up to %.4f" % (name, delta))
            if delta == 0.0:
                print("FAIL  channel %d is not read by the model" % channel)
                return 1

    # (5) distinct boards must not score identically. This is the check the first
    # version of this script was missing: it ran on three duplicate recordings of
    # one state, so identical numbers looked like agreement.
    if len(set(round(score, 4) for score in tops)) == 1:
        print("FAIL  every distinct board scored %.4f; the model is not reading the board"
              % tops[0])
        return 1
    print("ok    %d distinct boards scored %s" % (len(tops), [round(t, 2) for t in tops]))

    print("PASS  the 14-channel ranker serves a real board and reads both new channels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
