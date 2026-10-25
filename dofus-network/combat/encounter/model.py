"""Shared policy/value network for encounter self-play.

One set of weights plays both sides: actor tokens carry only information that
is relative to the acting team, so a policy that wins as the player also wins
as the monsters. That symmetry is what makes self-play a valid training signal
rather than two networks chasing each other.

Size is a deployment decision, not an accident: the host budget for a combat
head is under 1M parameters, and this pilot stays far below it (~0.2M at the
default width). The TPU is for rolling out many independent fights, not for a
large model.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

import jax
import jax.numpy as jnp

from combat.encounter.env import Config


Params = Dict[str, Any]
NEG = jnp.float32(-1.0e9)


def _dense(key: jax.Array, fan_in: int, fan_out: int,
           scale: float = 2.0) -> Tuple[jax.Array, jax.Array]:
    w = jax.random.normal(key, (fan_in, fan_out)) * jnp.sqrt(
        jnp.float32(scale / max(1, fan_in)))
    return w, jnp.zeros((fan_out,), dtype=jnp.float32)


def _ln_init(key: jax.Array, width: int) -> Tuple[jax.Array, jax.Array]:
    return jnp.ones((width,)), jnp.zeros((width,))


def init_params(key: jax.Array, feat_dim: int, global_dim: int,
                cfg: Config, hidden: int = 96, heads: int = 4,
                layers: int = 2) -> Params:
    keys = iter(jax.random.split(key, 64))
    p: Params = {}
    p["enc1"] = _dense(next(keys), feat_dim, hidden)
    p["enc2"] = _dense(next(keys), hidden, hidden)
    p["glob"] = _dense(next(keys), global_dim, hidden)
    p["ln_in"] = _ln_init(next(keys), hidden)
    p["blocks"] = []
    for _ in range(layers):
        p["blocks"].append({
            "ln1": _ln_init(next(keys), hidden),
            "qkv": _dense(next(keys), hidden, 3 * hidden),
            "out": _dense(next(keys), hidden, hidden),
            "ln2": _ln_init(next(keys), hidden),
            "mlp1": _dense(next(keys), hidden, 2 * hidden),
            "mlp2": _dense(next(keys), 2 * hidden, hidden),
        })
    p["move"] = _dense(next(keys), hidden, 3)
    p["spell"] = _dense(next(keys), hidden, cfg.n_spells)
    p["t_q"] = _dense(next(keys), hidden, hidden)
    p["t_k"] = _dense(next(keys), hidden, hidden)
    p["t_bias"] = jnp.zeros((cfg.n_spells, cfg.n_actors), dtype=jnp.float32)
    p["value1"] = _dense(next(keys), 2 * hidden, hidden)
    p["value2"] = _dense(next(keys), hidden, 1, scale=1.0)
    return p


def _dense_apply(x: jax.Array, wb: Tuple[jax.Array, jax.Array]) -> jax.Array:
    w, b = wb
    return x @ w + b


def _ln(x: jax.Array, wb: Tuple[jax.Array, jax.Array]) -> jax.Array:
    g, b = wb
    mean = jnp.mean(x, axis=-1, keepdims=True)
    var = jnp.mean(jnp.square(x - mean), axis=-1, keepdims=True)
    return (x - mean) * jax.lax.rsqrt(var + 1e-5) * g + b


def _attention(p: Dict[str, Any], x: jax.Array, mask: jax.Array,
               heads: int) -> jax.Array:
    batch, tokens, width = x.shape
    qkv = _dense_apply(x, p["qkv"]).reshape(batch, tokens, 3, heads,
                                             width // heads)
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


def forward(params: Params, obs: jax.Array, glob: jax.Array,
            attn_mask: jax.Array, heads: int = 4) -> Dict[str, jax.Array]:
    """obs [B,N,F], glob [B,G], attn_mask [B,N] -> logits and value [B]."""
    hidden = params["enc1"][0].shape[1]
    x = jax.nn.relu(_dense_apply(obs, params["enc1"]))
    x = jax.nn.relu(_dense_apply(x, params["enc2"]))
    g = jax.nn.relu(_dense_apply(glob, params["glob"]))[:, None, :]
    tokens = jnp.concatenate([g, x], axis=1)                 # [B, 1+N, H]
    mask = jnp.concatenate(
        [jnp.ones((obs.shape[0], 1), dtype=bool), attn_mask], axis=1)
    tokens = _ln(tokens, params["ln_in"])
    for block in params["blocks"]:
        h = _ln(tokens, block["ln1"])
        tokens = tokens + _attention(block, h, mask, heads)
        h = _ln(tokens, block["ln2"])
        h = jax.nn.relu(_dense_apply(h, block["mlp1"]))
        h = _dense_apply(h, block["mlp2"])
        tokens = tokens + h
    actors = tokens[:, 1:]
    global_token = tokens[:, 0]
    move = _dense_apply(actors, params["move"])
    spell = _dense_apply(actors, params["spell"])
    q = _dense_apply(actors, params["t_q"])
    k = _dense_apply(actors, params["t_k"])
    att = jnp.einsum("bih,bjh->bij", q, k) / jnp.sqrt(
        jnp.float32(hidden))
    target = att[:, :, None, :] + params["t_bias"][None, None, :, :]
    count = jnp.maximum(jnp.sum(attn_mask, axis=1, keepdims=True), 1.0)
    pooled = (jnp.sum(jnp.where(attn_mask[..., None], actors, 0.0), axis=1)
              / count)
    pooled = jnp.concatenate([pooled, global_token], axis=-1)
    pooled = jax.nn.relu(_dense_apply(pooled, params["value1"]))
    value = jnp.tanh(_dense_apply(pooled, params["value2"]))[:, 0]
    return {"move": move, "spell": spell, "target": target, "value": value}


def factor_logp(logits: Dict[str, jax.Array], masks: Dict[str, jax.Array],
                actions: Dict[str, jax.Array],
                cfg: Config) -> Tuple[jax.Array, jax.Array]:
    """Per-actor log-probability and entropy of stored actions.

    The target distribution is conditioned on the sampled spell, matching the
    rollout. Pass has no target choice and contributes no target entropy.
    """
    move_logits = jnp.where(masks["move"], logits["move"], NEG)
    spell_logits = jnp.where(masks["spell"], logits["spell"], NEG)
    chosen = jnp.take_along_axis(
        logits["target"], actions["spell"][..., None, None], axis=2)[:, :, 0, :]
    chosen = jnp.where(
        jnp.take_along_axis(masks["target"], actions["spell"][..., None, None],
                            axis=2)[:, :, 0, :], chosen, NEG)

    move_lp = jax.nn.log_softmax(move_logits, axis=-1)
    spell_lp = jax.nn.log_softmax(spell_logits, axis=-1)
    target_lp = jax.nn.log_softmax(chosen, axis=-1)
    lp = (jnp.take_along_axis(move_lp, actions["move"][..., None], -1)[..., 0]
          + jnp.take_along_axis(spell_lp, actions["spell"][..., None], -1)[..., 0]
          + jnp.where(actions["spell"] < cfg.spells,
                      jnp.take_along_axis(target_lp,
                                          actions["target"][..., None], -1)[..., 0],
                      0.0))
    ent = (-jnp.sum(jnp.exp(move_lp) * move_lp, axis=-1)
           - jnp.sum(jnp.exp(spell_lp) * spell_lp, axis=-1)
           - jnp.where(actions["spell"] < cfg.spells,
                       -jnp.sum(jnp.exp(target_lp) * target_lp, axis=-1), 0.0))
    return lp, ent
