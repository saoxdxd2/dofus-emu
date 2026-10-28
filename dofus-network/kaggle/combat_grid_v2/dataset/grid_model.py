"""30M 560-cell combat encoder.

This is the spatial companion to ``combat/encounter/model.py``. The encounter
actor is a set-transformer over fighter tokens; it cannot see the board. This
model reads:

* 560 cell tokens (walkable/LoS flags, occupancy, distance, visibility),
* fighter tokens (HP/AP/MP, cell, team, resists, boss flags),
* plan tokens (the solver's stratified legal plans and their predicted effects),

and returns a score per plan plus a value. It ranks legal plans; it never invents
an action, spell id or cell.

No training is claimed here. The recorded corpus has no actionable board (see
``python -m combat.grid_probe``), so the trainer refuses pass-only rows. This
module exists to make the 30M shape and its parameter count executable before the
first live fight board arrives.
"""

from __future__ import annotations

import argparse
from typing import Any, Dict, Optional, Tuple

import jax
import jax.numpy as jnp

NEG = jnp.float32(-1.0e9)

CELL_FEATS = 12
FIGHTER_FEATS = 20
PLAN_FEATS = 16
MAX_FIGHTERS = 16
MAX_PLANS = 16
DEFAULT_HIDDEN = 768
DEFAULT_CELL_LAYERS = 3
DEFAULT_FIGHTER_LAYERS = 2
DEFAULT_PLAN_LAYERS = 1
DEFAULT_HEADS = 8


def _block(key: jax.Array, hidden: int) -> Dict[str, Any]:
    keys = iter(jax.random.split(key, 8))
    return {
        "ln1": _ln_init(next(keys), hidden),
        "qkv": _dense(next(keys), hidden, 3 * hidden),
        "out": _dense(next(keys), hidden, hidden),
        "ln2": _ln_init(next(keys), hidden),
        "mlp1": _dense(next(keys), hidden, 2 * hidden),
        "mlp2": _dense(next(keys), 2 * hidden, hidden),
    }


def _ln_init(key: jax.Array, width: int) -> Tuple[jax.Array, jax.Array]:
    return jnp.ones((width,)), jnp.zeros((width,))


def _dense(key: jax.Array, fan_in: int, fan_out: int,
           scale: float = 2.0) -> Tuple[jax.Array, jax.Array]:
    w = jax.random.normal(key, (fan_in, fan_out)) * jnp.sqrt(
        jnp.float32(scale / max(1, fan_in)))
    return w, jnp.zeros((fan_out,), dtype=jnp.float32)


def _ln(x: jax.Array, wb: Tuple[jax.Array, jax.Array]) -> jax.Array:
    g, b = wb
    mean = jnp.mean(x, axis=-1, keepdims=True)
    var = jnp.mean(jnp.square(x - mean), axis=-1, keepdims=True)
    return (x - mean) * jax.lax.rsqrt(var + 1e-5) * g + b


def _attention(p: Dict[str, Any], x: jax.Array, mask: jax.Array,
               heads: int) -> jax.Array:
    batch, tokens, width = x.shape
    qkv = _dense_apply(x, p["qkv"]).reshape(
        batch, tokens, 3, heads, width // heads)
    q, k, v = qkv[:, :, 0], qkv[:, :, 1], qkv[:, :, 2]
    q = q.transpose(0, 2, 1, 3)
    k = k.transpose(0, 2, 1, 3)
    v = v.transpose(0, 2, 1, 3)
    logits = jnp.einsum("bhtd,bhsd->bhts", q, k) / jnp.sqrt(
        jnp.float32(width // heads))
    logits = jnp.where(mask[:, None, None, :], logits, NEG)
    probs = jax.nn.softmax(logits, axis=-1)
    out = jnp.einsum("bhts,bhsd->bhtd", probs, v)
    out = out.transpose(0, 2, 1, 3).reshape(batch, tokens, width)
    return _dense_apply(out, p["out"])


def init_params(key: jax.Array,
                cell_feats: int = CELL_FEATS,
                fighter_feats: int = FIGHTER_FEATS,
                plan_feats: int = PLAN_FEATS,
                hidden: int = DEFAULT_HIDDEN,
                cell_layers: int = DEFAULT_CELL_LAYERS,
                fighter_layers: int = DEFAULT_FIGHTER_LAYERS,
                plan_layers: int = DEFAULT_PLAN_LAYERS,
                heads: int = DEFAULT_HEADS) -> Dict[str, Any]:
    keys = iter(jax.random.split(key, 256))
    p: Dict[str, Any] = {
        "cell_in": _dense(next(keys), cell_feats, hidden),
        "cell_type": jnp.zeros((hidden,), dtype=jnp.float32),
        "cell_blocks": [_block(next(keys), hidden) for _ in range(cell_layers)],
        "fighter_in": _dense(next(keys), fighter_feats, hidden),
        "fighter_type": jnp.zeros((hidden,), dtype=jnp.float32),
        "fighter_blocks": [_block(next(keys), hidden) for _ in range(fighter_layers)],
        "plan_in": _dense(next(keys), plan_feats, hidden),
        "plan_type": jnp.zeros((hidden,), dtype=jnp.float32),
        "plan_blocks": [_block(next(keys), hidden) for _ in range(plan_layers)],
        "grid_query": _dense(next(keys), hidden, hidden),
        "plan_query": _dense(next(keys), hidden, hidden),
        "value1": _dense(next(keys), 2 * hidden, hidden),
        "value2": _dense(next(keys), hidden, 1, scale=1.0),
    }
    return p


def _pool(x: jax.Array, mask: jax.Array) -> jax.Array:
    weights = mask[..., None].astype(jnp.float32)
    return jnp.sum(x * weights, axis=1) / jnp.maximum(
        jnp.sum(weights, axis=1), 1.0)


def _encode(x: jax.Array, blocks: list, mask: jax.Array, heads: int) -> jax.Array:
    for block in blocks:
        h = _ln(x, block["ln1"])
        x = x + _attention(block, h, mask, heads)
        h = _ln(x, block["ln2"])
        h = jax.nn.relu(_dense_apply(h, block["mlp1"]))
        h = _dense_apply(h, block["mlp2"])
        x = x + h
    return x


def _dense_apply(x: jax.Array, wb: Tuple[jax.Array, jax.Array]) -> jax.Array:
    return x @ wb[0] + wb[1]


def forward(params: Dict[str, Any],
            cells: jax.Array, cell_mask: jax.Array,
            fighters: jax.Array, fighter_mask: jax.Array,
            plans: jax.Array, plan_mask: jax.Array,
            heads: int = DEFAULT_HEADS) -> Dict[str, jax.Array]:
    """cells [B,560,Fc], fighters [B,16,Ff], plans [B,16,Fp] -> scores/value."""
    cell = jax.nn.relu(_dense_apply(cells, params["cell_in"]))
    cell = cell + params["cell_type"]
    cell = _encode(cell, params["cell_blocks"], cell_mask, heads)
    grid = _pool(cell, cell_mask)

    fighter = jax.nn.relu(_dense_apply(fighters, params["fighter_in"]))
    fighter = fighter + params["fighter_type"]
    fighter = _encode(fighter, params["fighter_blocks"], fighter_mask, heads)
    fighter_pool = _pool(fighter, fighter_mask)

    plan = jax.nn.relu(_dense_apply(plans, params["plan_in"]))
    plan = plan + params["plan_type"]
    plan = _encode(plan, params["plan_blocks"], plan_mask, heads)

    query = jax.nn.relu(_dense_apply(grid + fighter_pool, params["grid_query"]))
    query = query + jax.nn.relu(
        _dense_apply(fighter_pool, params["plan_query"]))
    scores = jnp.sum(plan * query[:, None, :], axis=-1) / jnp.sqrt(
        jnp.float32(plan.shape[-1]))
    scores = jnp.where(plan_mask, scores, NEG)
    pooled = jnp.concatenate([grid, fighter_pool], axis=-1)
    pooled = jax.nn.relu(_dense_apply(pooled, params["value1"]))
    value = jnp.tanh(_dense_apply(pooled, params["value2"]))[:, 0]
    return {"scores": scores, "value": value}


def param_count(params: Any) -> int:
    return int(sum(int(x.size) for x in jax.tree_util.tree_leaves(params)))


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check-params", action="store_true")
    parser.add_argument("--forward-check", action="store_true")
    parser.add_argument("--hidden", type=int, default=DEFAULT_HIDDEN)
    parser.add_argument("--cell-layers", type=int, default=DEFAULT_CELL_LAYERS)
    parser.add_argument("--fighter-layers", type=int, default=DEFAULT_FIGHTER_LAYERS)
    parser.add_argument("--plan-layers", type=int, default=DEFAULT_PLAN_LAYERS)
    args = parser.parse_args(argv)
    params = init_params(jax.random.PRNGKey(0), hidden=args.hidden,
                         cell_layers=args.cell_layers,
                         fighter_layers=args.fighter_layers,
                         plan_layers=args.plan_layers)
    count = param_count(params)
    print("params", count, "hidden", args.hidden,
          "layers", args.cell_layers, args.fighter_layers, args.plan_layers)
    if args.forward_check:
        cells = jnp.zeros((2, 560, CELL_FEATS), dtype=jnp.float32)
        cell_mask = jnp.ones((2, 560), dtype=bool)
        fighters = jnp.zeros((2, MAX_FIGHTERS, FIGHTER_FEATS), dtype=jnp.float32)
        fighter_mask = jnp.ones((2, MAX_FIGHTERS), dtype=bool)
        plans = jnp.zeros((2, MAX_PLANS, PLAN_FEATS), dtype=jnp.float32)
        plan_mask = jnp.ones((2, MAX_PLANS), dtype=bool)
        out = forward(params, cells, cell_mask, fighters, fighter_mask,
                      plans, plan_mask)
        print("scores", out["scores"].shape, "finite",
              bool(jnp.isfinite(out["scores"]).all()),
              "value", out["value"].shape,
              bool(jnp.isfinite(out["value"]).all()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
