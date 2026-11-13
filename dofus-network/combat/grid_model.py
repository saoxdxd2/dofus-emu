"""30M 560-cell combat encoder.

This is the spatial companion to ``combat/encounter/model.py``. The encounter
actor is a set-transformer over fighter tokens; it cannot see the board. This
model reads:

* 560 cell tokens (walkable/LoS flags, occupancy, distance, visibility),
* fighter tokens (HP/AP/MP, cell, team, resists, boss flags),
* plan tokens (the solver's stratified legal plans and their predicted effects),

and returns a score per plan plus a value. It ranks legal plans; it never invents
an action, spell id or cell.

The forward pass runs on whichever array backend exists -- jax when installed
(training box, jit-compiled), numpy otherwise (installed embedded interpreter,
eager) -- through ``combat.grid_backend``. No training is claimed here: the
recorded corpus has no actionable board (see ``python -m combat.grid_probe``), so
the trainer refuses pass-only rows. This module exists to make the 30M shape and
its parameter count executable before the first live fight board arrives.
"""
from __future__ import annotations

import argparse
from typing import Any, Dict, Optional, Tuple

try:
    from combat.grid_backend import (XP, f32, relu, softmax, rsqrt, tree_map,
                                     tree_leaves, random, HAS_JAX, BACKEND)
except ImportError:  # Kaggle standalone: grid_backend.py sits beside this file
    from grid_backend import (XP, f32, relu, softmax, rsqrt, tree_map,
                              tree_leaves, random, HAS_JAX, BACKEND)

NEG = f32(-1.0e9)

CELL_FEATS = 14
FIGHTER_FEATS = 20
PLAN_FEATS = 16
MAX_FIGHTERS = 16
MAX_PLANS = 16
#: How many cell tokens reach the transformer. A fight touches a fraction of the
#: 560-cell map: everything outside the walkable/LOS/reachable set is padded zeros
#: that attention still pays O(N^2) for. 128 keeps every cell a plan can name
#: (20-150 on the recorded maps) while cutting attention by 19x at N=560.
DEFAULT_ACTIVE_CELLS = 128
MIN_ACTIVE_CELLS = 16
DEFAULT_HIDDEN = 768
DEFAULT_CELL_LAYERS = 3
DEFAULT_FIGHTER_LAYERS = 2
DEFAULT_PLAN_LAYERS = 1
DEFAULT_HEADS = 8


def _block(key: Any, hidden: int) -> Dict[str, Any]:
    keys = iter(random.split(key, 8))
    return {
        "ln1": _ln_init(next(keys), hidden),
        "qkv": _dense(next(keys), hidden, 3 * hidden),
        "out": _dense(next(keys), hidden, hidden),
        "ln2": _ln_init(next(keys), hidden),
        "mlp1": _dense(next(keys), hidden, 2 * hidden),
        "mlp2": _dense(next(keys), 2 * hidden, hidden),
    }


def _ln_init(key: Any, width: int) -> Tuple[Any, Any]:
    return XP.ones((width,)), XP.zeros((width,))


def _dense(key: Any, fan_in: int, fan_out: int,
           scale: float = 2.0) -> Tuple[Any, Any]:
    w = random.normal(key, (fan_in, fan_out)) * XP.sqrt(
        f32(scale / max(1, fan_in)))
    return w, XP.zeros((fan_out,), dtype=f32)


def _ln(x: Any, wb: Tuple[Any, Any]) -> Any:
    g, b = wb
    mean = XP.mean(x, axis=-1, keepdims=True)
    var = XP.mean(XP.square(x - mean), axis=-1, keepdims=True)
    return (x - mean) * rsqrt(var + 1e-5) * g + b


def cell_salience(cells: Any, cell_mask: Any) -> Any:
    """Per-cell importance, from the features the encoder already receives.

    Pre-attention pruning has to be cheap: computing it must cost less than the
    attention it removes. This is one pass over the cell tensor, no geometry, no
    fighter loop -- the interesting cells are already labelled as such by
    ``grid_dataset._cells``:

        occupied (|4|)      somebody is standing there
        reachable_now (13)  the character can stand there this turn
        covered_by_enemy (12) standing there gets hit
        los_from_me (3)     a spell can be cast there
        walkable (2)        at least a legal cell
        1 - distance (6)    tie-break towards the fight

    Cells outside ``cell_mask`` are pushed to NEG so they only survive when
    every cell is masked out.
    """
    walk = cells[..., 2]
    los = cells[..., 3]
    occupied = XP.abs(cells[..., 4])
    distance = cells[..., 6]
    covered = cells[..., 12]
    reachable = cells[..., 13]
    score = (2.0 * occupied + 1.5 * reachable + 1.0 * covered
             + 1.0 * los + 0.5 * walk + 0.5 * (1.0 - distance))
    # A deterministic tie-break by cell id. Most of a map scores exactly 0, and
    # numpy's argsort, jax's top_k and torch's topk do not agree on which of
    # several equal values they return first -- so without this the ONNX export
    # and the trained model would gather different (equally irrelevant) cells and
    # the parity check would fail for no real reason.
    total = int(cells.shape[1])
    score = score - XP.arange(total) * f32(1e-6)
    return XP.where(cell_mask, score, NEG)


def _top_k(scores: Any, k: int) -> Any:
    if HAS_JAX:
        import jax
        return jax.lax.top_k(scores, int(k))[1]
    # numpy: argsort is O(N log N) on 560 floats -- ~9 us, two orders below the
    # attention it removes. jax's top_k exists so the jitted path stays static.
    return XP.argsort(-XP.asarray(scores), axis=-1)[:, :int(k)].astype(XP.int32)


def prune_board(cells: Any, cell_mask: Any,
                active_cells: int = DEFAULT_ACTIVE_CELLS) -> Tuple[Any, Any, Any]:
    """Gather the top-``active_cells`` tokens per board before the transformer.

    Returns ``(cells, cell_mask, index)``; ``index`` is ``[B, k]`` so a caller can
    map a pruned token back to its cell id. ``active_cells <= 0`` or a budget at
    least as large as the tensor disables pruning and returns the inputs.
    """
    total = int(cells.shape[1])
    if active_cells is None or int(active_cells) <= 0 or int(active_cells) >= total:
        return cells, cell_mask, None
    k = max(MIN_ACTIVE_CELLS, int(active_cells))
    k = min(k, total)
    index = _top_k(cell_salience(cells, cell_mask), k)
    gathered = XP.take_along_axis(cells, index[..., None], axis=1)
    mask = XP.take_along_axis(cell_mask, index, axis=1)
    return gathered, mask, index


def attention_flops(tokens: int, width: int, layers: int, heads: int,
                    batch: int = 1) -> int:
    """Multiply-adds in the N^2 terms alone (scores and weighted sum).

    This is the number pruning changes by (560/128)^2 = 19.1x. The projections
    are O(N) and do not scale with the active set the same way; ``stack_flops``
    reports the whole cell stack, where the projection term dominates at width
    768 and the honest saving is ~5.5x rather than 19x.
    """
    del heads  # each head is a slice of the same width; the total is unchanged
    return int(batch * layers * 2 * tokens * tokens * width)


def stack_flops(tokens: int, width: int, layers: int, batch: int = 1) -> int:
    """Total multiply-adds in the cell stack: QKV/out/MLP plus the N^2 terms."""
    per_layer = 4 * tokens * width * width + 2 * tokens * tokens * width
    return int(batch * layers * per_layer)


def _attention(p: Dict[str, Any], x: Any, mask: Any,
               heads: int) -> Any:
    batch, tokens, width = x.shape
    qkv = _dense_apply(x, p["qkv"]).reshape(
        batch, tokens, 3, heads, width // heads)
    q, k, v = qkv[:, :, 0], qkv[:, :, 1], qkv[:, :, 2]
    q = q.transpose(0, 2, 1, 3)
    k = k.transpose(0, 2, 1, 3)
    v = v.transpose(0, 2, 1, 3)
    logits = XP.einsum("bhtd,bhsd->bhts", q, k) / XP.sqrt(
        f32(width // heads))
    logits = XP.where(mask[:, None, None, :], logits, NEG)
    probs = softmax(logits, axis=-1)
    out = XP.einsum("bhts,bhsd->bhtd", probs, v)
    out = out.transpose(0, 2, 1, 3).reshape(batch, tokens, width)
    return _dense_apply(out, p["out"])


def init_params(key: Any,
                cell_feats: int = CELL_FEATS,
                fighter_feats: int = FIGHTER_FEATS,
                plan_feats: int = PLAN_FEATS,
                hidden: int = DEFAULT_HIDDEN,
                cell_layers: int = DEFAULT_CELL_LAYERS,
                fighter_layers: int = DEFAULT_FIGHTER_LAYERS,
                plan_layers: int = DEFAULT_PLAN_LAYERS,
                heads: int = DEFAULT_HEADS) -> Dict[str, Any]:
    keys = iter(random.split(key, 64))
    p: Dict[str, Any] = {}
    p["cell_in"] = _dense(next(keys), cell_feats, hidden)
    p["cell_type"] = XP.zeros((hidden,))
    p["cell_blocks"] = [_block(next(keys), hidden)
                        for _ in range(cell_layers)]
    p["fighter_in"] = _dense(next(keys), fighter_feats, hidden)
    p["fighter_type"] = XP.zeros((hidden,))
    p["fighter_blocks"] = [_block(next(keys), hidden)
                           for _ in range(fighter_layers)]
    p["plan_in"] = _dense(next(keys), plan_feats, hidden)
    p["plan_type"] = XP.zeros((hidden,))
    p["plan_blocks"] = [_block(next(keys), hidden)
                        for _ in range(plan_layers)]
    # `grid_query` reads the *sum* of the two pools, not their concatenation -- `forward`
    # calls `_dense_apply(grid + fighter_pool, ...)`. It was declared `2 * hidden` wide, so
    # every forward pass died on `dot_general requires contracting dimensions to have the
    # same shape, got (64,) and (128,)` and the parameter count read 31,355,905 against the
    # pinned 30,766,081; the difference is exactly one `hidden * hidden` (768 * 768).
    # `value1` is the head that concatenates, and it keeps `2 * hidden`.
    p["grid_query"] = _dense(next(keys), hidden, hidden)
    p["plan_query"] = _dense(next(keys), hidden, hidden)
    p["value1"] = _dense(next(keys), 2 * hidden, hidden)
    p["value2"] = _dense(next(keys), hidden, 1, scale=1.0)
    return p


def _dense_apply(x: Any, wb: Tuple[Any, Any]) -> Any:
    return x @ wb[0] + wb[1]


def _pool(x: Any, mask: Any) -> Any:
    weights = mask[..., None].astype(f32)
    return XP.sum(x * weights, axis=1) / XP.maximum(
        XP.sum(weights, axis=1), 1.0)


def _encode(x: Any, blocks: list, mask: Any, heads: int) -> Any:
    for block in blocks:
        h = _ln(x, block["ln1"])
        x = x + _attention(block, h, mask, heads)
        h = _ln(x, block["ln2"])
        h = relu(_dense_apply(h, block["mlp1"]))
        h = _dense_apply(h, block["mlp2"])
        x = x + h
    return x


def forward(params: Dict[str, Any],
            cells: Any, cell_mask: Any,
            fighters: Any, fighter_mask: Any,
            plans: Any, plan_mask: Any,
            heads: int = DEFAULT_HEADS,
            active_cells: int = DEFAULT_ACTIVE_CELLS) -> Dict[str, Any]:
    """cells [B,560,Fc], fighters [B,16,Ff], plans [B,16,Fp] -> scores/value.

    ``active_cells`` prunes the cell tensor before the first transformer layer
    (see ``prune_board``); pass 0 to run over all 560 tokens, which is what the
    pruning parity test compares against.
    """
    cells, cell_mask, _index = prune_board(cells, cell_mask, active_cells)
    cell = relu(_dense_apply(cells, params["cell_in"]))
    cell = cell + params["cell_type"]
    cell = _encode(cell, params["cell_blocks"], cell_mask, heads)
    grid = _pool(cell, cell_mask)

    fighter = relu(_dense_apply(fighters, params["fighter_in"]))
    fighter = fighter + params["fighter_type"]
    fighter = _encode(fighter, params["fighter_blocks"], fighter_mask, heads)
    fighter_pool = _pool(fighter, fighter_mask)

    plan = relu(_dense_apply(plans, params["plan_in"]))
    plan = plan + params["plan_type"]
    plan = _encode(plan, params["plan_blocks"], plan_mask, heads)

    query = relu(_dense_apply(grid + fighter_pool, params["grid_query"]))
    query = query + relu(
        _dense_apply(fighter_pool, params["plan_query"]))
    scores = XP.sum(plan * query[:, None, :], axis=-1) / XP.sqrt(
        f32(plan.shape[-1]))
    scores = XP.where(plan_mask, scores, NEG)
    pooled = XP.concatenate([grid, fighter_pool], axis=-1)
    pooled = relu(_dense_apply(pooled, params["value1"]))
    value = XP.tanh(_dense_apply(pooled, params["value2"]))[:, 0]
    return {"scores": scores, "value": value}


def param_count(params: Any) -> int:
    return int(sum(int(x.size) for x in tree_leaves(params)))


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check-params", action="store_true")
    parser.add_argument("--forward-check", action="store_true")
    parser.add_argument("--hidden", type=int, default=DEFAULT_HIDDEN)
    parser.add_argument("--cell-layers", type=int, default=DEFAULT_CELL_LAYERS)
    parser.add_argument("--fighter-layers", type=int, default=DEFAULT_FIGHTER_LAYERS)
    parser.add_argument("--plan-layers", type=int, default=DEFAULT_PLAN_LAYERS)
    parser.add_argument("--active-cells", type=int, default=DEFAULT_ACTIVE_CELLS,
                        help="cell tokens kept before attention (0 = all 560)")
    args = parser.parse_args(argv)
    if not HAS_JAX:
        # init_params draws from the random stream, which needs jax. On a numpy
        # backend we can still report the architecture but cannot initialise or
        # run a forward pass -- that is training-box work.
        print("params <jax required to initialise> hidden", args.hidden,
              "layers", args.cell_layers, args.fighter_layers, args.plan_layers,
              "backend", BACKEND)
        print("forward-check skipped: numpy backend is inference only")
        return 0
    import jax  # noqa: import only on the training box; guarded by HAS_JAX
    params = init_params(jax.random.PRNGKey(0),
                         hidden=args.hidden,
                         cell_layers=args.cell_layers,
                         fighter_layers=args.fighter_layers,
                         plan_layers=args.plan_layers)
    count = param_count(params)
    print("params", count, "hidden", args.hidden,
          "layers", args.cell_layers, args.fighter_layers, args.plan_layers)
    if args.forward_check:
        cells = XP.zeros((2, 560, CELL_FEATS), dtype=f32)
        cell_mask = XP.ones((2, 560), dtype=bool)
        # A realistic board: me at 300, two reachable cells, an enemy at 320.
        for cell_id in (300, 301, 302, 320):
            cells = cells.at[0, cell_id, 2].set(1.0)
            cells = cells.at[0, cell_id, 3].set(1.0)
        for cell_id in (301, 302):
            cells = cells.at[0, cell_id, 13].set(1.0)
        cells = cells.at[0, 320, 4].set(-1.0)
        fighters = XP.zeros((2, MAX_FIGHTERS, FIGHTER_FEATS), dtype=f32)
        fighter_mask = XP.ones((2, MAX_FIGHTERS), dtype=bool)
        plans = XP.zeros((2, MAX_PLANS, PLAN_FEATS), dtype=f32)
        plan_mask = XP.ones((2, MAX_PLANS), dtype=bool)
        out = forward(params, cells, cell_mask, fighters, fighter_mask,
                      plans, plan_mask, active_cells=args.active_cells)
        print("active cells", args.active_cells, "scores", out["scores"].shape, "finite",
              bool(XP.isfinite(out["scores"]).all()),
              "value", out["value"].shape,
              bool(XP.isfinite(out["value"]).all()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
