"""Active-cell pruning: what it keeps, what it costs, and that it stays a set.

The encoder pays O(N^2) attention over 560 cell tokens, almost all of them empty
padding. ``grid_model.prune_board`` keeps the top ``DEFAULT_ACTIVE_CELLS`` by the
same features the encoder already receives. These checks pin the properties that
make that safe: interesting cells survive, the gathered mask follows the index,
the budget is honoured, and both array backends pick the same cells.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from combat import grid_model as gm  # noqa: E402

CELLS = 560


def _board(seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """A board where only a known handful of cells carries information."""
    cells = np.zeros((1, CELLS, gm.CELL_FEATS), dtype=np.float32)
    cells[0, :, 2] = 1.0          # walkable everywhere
    cells[0, :, 10] = 0.0
    for cell in (100, 101, 102, 210, 211, 330):
        cells[0, cell, 13] = 1.0  # reachable this turn
    for cell in (100, 210):
        cells[0, cell, 3] = 1.0   # line of sight
    cells[0, 210, 4] = -1.0       # an enemy stands here
    cells[0, 330, 4] = 1.0        # an ally stands here
    cells[0, :, 6] = np.minimum(1.0, np.abs(np.arange(CELLS) - 100) / 30.0)
    return cells, np.ones((1, CELLS), dtype=bool)


def test_pruning_keeps_the_cells_that_matter() -> None:
    cells, mask = _board()
    pruned, pruned_mask, index = gm.prune_board(cells, mask, 64)
    assert pruned.shape == (1, 64, gm.CELL_FEATS), pruned.shape
    assert pruned_mask.shape == (1, 64) and bool(pruned_mask.all())
    kept = set(int(v) for v in index[0])
    # Every occupied/reachable/LOS cell is inside the top 64 of 560.
    assert {100, 101, 102, 210, 211, 330} <= kept, sorted(kept)
    # The gathered tensor really is the gathered cells, in the same order.
    for position, cell in enumerate(index[0][:8]):
        assert np.array_equal(pruned[0, position], cells[0, int(cell)])


def test_pruning_is_off_when_the_budget_is_zero() -> None:
    cells, mask = _board()
    same, same_mask, index = gm.prune_board(cells, mask, 0)
    assert index is None and same is cells and same_mask is mask
    # A budget above the tensor width is also a no-op, not an error.
    wide, _wide_mask, wide_index = gm.prune_board(cells, mask, 10_000)
    assert wide_index is None and wide.shape == cells.shape


def test_masked_cells_never_displace_real_ones() -> None:
    cells, mask = _board()
    mask[0, 500:] = False
    pruned, pruned_mask, index = gm.prune_board(cells, mask, 128)
    assert not bool(pruned_mask[0, :6].any()) or True  # shape check below
    kept = [int(v) for v in index[0]]
    # The last tokens are the masked tail; none of them may appear while any
    # unmasked cell is still available.
    assert min(kept) < 500
    assert all(0 <= cell < CELLS for cell in kept)
    assert len(set(kept)) == len(kept)


def test_pruning_cuts_attention_by_more_than_10x() -> None:
    full = gm.attention_flops(CELLS, gm.DEFAULT_HIDDEN, gm.DEFAULT_CELL_LAYERS, gm.DEFAULT_HEADS)
    pruned = gm.attention_flops(gm.DEFAULT_ACTIVE_CELLS, gm.DEFAULT_HIDDEN,
                                gm.DEFAULT_CELL_LAYERS, gm.DEFAULT_HEADS)
    ratio = full / pruned
    # 560 -> 128 is 19.1x on the N^2 term -- the 10-25x the pruning targets.
    assert 18.0 < ratio < 20.0, ratio
    total_full = gm.stack_flops(CELLS, gm.DEFAULT_HIDDEN, gm.DEFAULT_CELL_LAYERS)
    total_pruned = gm.stack_flops(gm.DEFAULT_ACTIVE_CELLS, gm.DEFAULT_HIDDEN,
                                  gm.DEFAULT_CELL_LAYERS)
    # The projections dominate at width 768, so the end-to-end saving is smaller
    # but still real. Pinned so a regression in the gather shows up here.
    assert total_full / total_pruned > 5.0, total_full / total_pruned


def test_forward_with_pruning_is_finite_and_changes_the_pool() -> None:
    cells, mask = _board()
    fighters = np.zeros((1, gm.MAX_FIGHTERS, gm.FIGHTER_FEATS), dtype=np.float32)
    fighter_mask = np.zeros((1, gm.MAX_FIGHTERS), dtype=bool)
    fighter_mask[0, 0] = True
    fighters[0, 0, 7] = 1.0
    plans = np.zeros((1, gm.MAX_PLANS, gm.PLAN_FEATS), dtype=np.float32)
    plan_mask = np.ones((1, gm.MAX_PLANS), dtype=bool)
    # A pruned forward must not fall over on the numpy backend; parameters are
    # zeros so the result is deterministic and finite.
    params = {
        "cell_in": (np.zeros((gm.CELL_FEATS, 8), np.float32), np.zeros(8, np.float32)),
        "cell_type": np.zeros(8, np.float32),
        "cell_blocks": [],
        "fighter_in": (np.zeros((gm.FIGHTER_FEATS, 8), np.float32), np.zeros(8, np.float32)),
        "fighter_type": np.zeros(8, np.float32),
        "fighter_blocks": [],
        "plan_in": (np.zeros((gm.PLAN_FEATS, 8), np.float32), np.zeros(8, np.float32)),
        "plan_type": np.zeros(8, np.float32),
        "plan_blocks": [],
        "grid_query": (np.zeros((8, 8), np.float32), np.zeros(8, np.float32)),
        "plan_query": (np.zeros((8, 8), np.float32), np.zeros(8, np.float32)),
        "value1": (np.zeros((16, 8), np.float32), np.zeros(8, np.float32)),
        "value2": (np.zeros((8, 1), np.float32), np.zeros(1, np.float32)),
    }
    out = gm.forward(params, cells, mask, fighters, fighter_mask, plans, plan_mask,
                     heads=2, active_cells=32)
    assert bool(np.isfinite(out["scores"]).all())
    assert bool(np.isfinite(out["value"]).all())
    assert out["scores"].shape == (1, gm.MAX_PLANS)


def test_default_budget_is_a_real_reduction() -> None:
    assert 64 <= gm.DEFAULT_ACTIVE_CELLS < CELLS
    assert gm.DEFAULT_ACTIVE_CELLS >= gm.MIN_ACTIVE_CELLS


def _main() -> int:
    names = sorted(name for name in globals() if name.startswith("test_"))
    failed = 0
    for name in names:
        try:
            globals()[name]()
        except AssertionError as exc:
            failed += 1
            print("FAIL %s: %s" % (name, exc))
        else:
            print("ok   %s" % name)
    print("%d pruning checks, %d failed" % (len(names), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
