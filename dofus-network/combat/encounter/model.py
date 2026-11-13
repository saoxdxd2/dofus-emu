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

from combat.encounter.backend import (Array, XP, log_softmax, random, relu,
                                       rsqrt, softmax)

from combat.encounter.env import Config


Params = Dict[str, Any]
NEG = XP.float32(-1.0e9)


def _dense(key: Array, fan_in: int, fan_out: int,
           scale: float = 2.0) -> Tuple[Array, Array]:
    w = random.normal(key, (fan_in, fan_out)) * XP.sqrt(
        XP.float32(scale / max(1, fan_in)))
    return w, XP.zeros((fan_out,), dtype=XP.float32)


def _ln_init(key: Array, width: int) -> Tuple[Array, Array]:
    return XP.ones((width,)), XP.zeros((width,))


def init_params(key: Array, feat_dim: int, global_dim: int,
                cfg: Config, hidden: int = 96, heads: int = 4,
                layers: int = 2) -> Params:
    keys = iter(random.split(key, 64))
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
    p["move"] = _dense(next(keys), hidden, getattr(cfg, "n_moves", getattr(cfg, "moves", 3)))
    p["spell"] = _dense(next(keys), hidden, cfg.n_spells)
    p["t_q"] = _dense(next(keys), hidden, hidden)
    p["t_k"] = _dense(next(keys), hidden, hidden)
    p["t_bias"] = XP.zeros((cfg.n_spells, cfg.n_actors), dtype=XP.float32)
    p["value1"] = _dense(next(keys), 2 * hidden, hidden)
    p["value2"] = _dense(next(keys), hidden, 1, scale=1.0)
    return p


def _dense_apply(x: Array, wb: Tuple[Array, Array]) -> Array:
    w, b = wb
    return x @ w + b


def _ln(x: Array, wb: Tuple[Array, Array]) -> Array:
    g, b = wb
    centered = x - XP.mean(x, axis=-1, keepdims=True)
    var = XP.mean(XP.square(centered), axis=-1, keepdims=True)
    return centered * rsqrt(var + 1e-5) * g + b


def _attention(p: Dict[str, Any], x: Array, mask: Array,
               heads: int) -> Array:
    batch, tokens, width = x.shape
    qkv = _dense_apply(x, p["qkv"]).reshape(batch, tokens, 3, heads,
                                             width // heads)
    q, k, v = qkv[:, :, 0], qkv[:, :, 1], qkv[:, :, 2]
    q = q.transpose(0, 2, 1, 3)
    k = k.transpose(0, 2, 1, 3)
    v = v.transpose(0, 2, 1, 3)
    logits = XP.einsum("bhtd,bhsd->bhts", q, k) / XP.sqrt(
        XP.float32(width // heads))
    logits = XP.where(mask[:, None, None, :], logits, NEG)
    probs = softmax(logits, axis=-1)
    out = XP.einsum("bhts,bhsd->bhtd", probs, v)
    out = out.transpose(0, 2, 1, 3).reshape(batch, tokens, width)
    return _dense_apply(out, p["out"])


def trunk(params: Params, obs: Array, glob: Array, attn_mask: Array,
          heads: int = 4) -> Array:
    """`forward`'s trunk: the encoder, the global token and the transformer blocks.

    Returns the actor tokens [B,N,H] and the global token [B,H]. Split out because a weight-space
    edit on a head is solved from the activation that head reads, and the alternative is a second
    copy of this walk in a tool -- which would be a different network the moment this one changed.
    """
    x = relu(_dense_apply(obs, params["enc1"]))
    x = relu(_dense_apply(x, params["enc2"]))
    g = relu(_dense_apply(glob, params["glob"]))[:, None, :]
    tokens = XP.concatenate([g, x], axis=1)                 # [B, 1+N, H]
    mask = XP.concatenate(
        [XP.ones((obs.shape[0], 1), dtype=bool), attn_mask], axis=1)
    tokens = _ln(tokens, params["ln_in"])
    for block in params["blocks"]:
        h = _ln(tokens, block["ln1"])
        tokens = tokens + _attention(block, h, mask, heads)
        h = _ln(tokens, block["ln2"])
        h = relu(_dense_apply(h, block["mlp1"]))
        h = _dense_apply(h, block["mlp2"])
        tokens = tokens + h
    return tokens[:, 1:], tokens[:, 0]


def forward(params: Params, obs: Array, glob: Array,
            attn_mask: Array, heads: int = 4) -> Dict[str, Array]:
    """obs [B,N,F], glob [B,G], attn_mask [B,N] -> logits and value [B]."""
    hidden = params["enc1"][0].shape[1]
    actors, global_token = trunk(params, obs, glob, attn_mask, heads)
    move = _dense_apply(actors, params["move"])
    spell = _dense_apply(actors, params["spell"])
    q = _dense_apply(actors, params["t_q"])
    k = _dense_apply(actors, params["t_k"])
    att = XP.einsum("bih,bjh->bij", q, k) / XP.sqrt(
        XP.float32(hidden))
    target = att[:, :, None, :] + params["t_bias"][None, None, :, :]
    count = XP.maximum(XP.sum(attn_mask, axis=1, keepdims=True), 1.0)
    value, _ = value_readout(params, actors, global_token, attn_mask, count)
    return {"move": move, "spell": spell, "target": target, "value": value}


def value_readout(params: Params, actors: Array, global_token: Array,
                  attn_mask: Array, count: Array) -> Tuple[Array, Array]:
    """The value head's estimate and the activation its last layer reads.

    Split out of `forward` so there is one implementation of the pooling: `value2` is a linear
    read of the tensor returned here, so a weight-space edit on the value head is solvable in
    closed form from cached (activation, terminal outcome) pairs, and the tool solving it has to
    see exactly the tensor `forward` sees rather than re-deriving the mean over attended actors.
    """
    pooled = (XP.sum(XP.where(attn_mask[..., None], actors, 0.0), axis=1)
              / count)
    pooled = XP.concatenate([pooled, global_token], axis=-1)
    hidden = relu(_dense_apply(pooled, params["value1"]))
    value = XP.tanh(_dense_apply(hidden, params["value2"]))[:, 0]
    return value, hidden


def greedy_actions(logits: Dict[str, Array], masks: Dict[str, Array], cfg: Config):
    move = XP.argmax(XP.where(masks["move"], logits["move"], NEG), axis=-1)
    spell = XP.argmax(XP.where(masks["spell"], logits["spell"], NEG), axis=-1)
    target_logits = XP.take_along_axis(logits["target"], spell[..., None, None], axis=2)[:, :, 0]
    target_mask = XP.take_along_axis(masks["target"], spell[..., None, None], axis=2)[:, :, 0]
    target = XP.argmax(XP.where(target_mask, target_logits, NEG), axis=-1)
    return {"move": move.astype(XP.int32), "spell": spell.astype(XP.int32),
            "target": XP.where(spell < cfg.spells, target, 0).astype(XP.int32)}


def factor_logp(logits: Dict[str, Array], masks: Dict[str, Array],
                actions: Dict[str, Array],
                cfg: Config) -> Tuple[Array, Array]:
    """Per-actor log-probability and entropy of stored actions.

    The target distribution is conditioned on the sampled spell, matching the
    rollout. Pass has no target choice and contributes no target entropy.
    """
    move_logits = XP.where(masks["move"], logits["move"], NEG)
    spell_logits = XP.where(masks["spell"], logits["spell"], NEG)
    chosen = XP.take_along_axis(
        logits["target"], actions["spell"][..., None, None], axis=2)[:, :, 0, :]
    chosen = XP.where(
        XP.take_along_axis(masks["target"], actions["spell"][..., None, None],
                            axis=2)[:, :, 0, :], chosen, NEG)

    move_lp = log_softmax(move_logits, axis=-1)
    spell_lp = log_softmax(spell_logits, axis=-1)
    target_lp = log_softmax(chosen, axis=-1)
    lp = (XP.take_along_axis(move_lp, actions["move"][..., None], -1)[..., 0]
          + XP.take_along_axis(spell_lp, actions["spell"][..., None], -1)[..., 0]
          + XP.where(actions["spell"] < cfg.spells,
                      XP.take_along_axis(target_lp,
                                          actions["target"][..., None], -1)[..., 0],
                      0.0))
    ent = (-XP.sum(XP.exp(move_lp) * move_lp, axis=-1)
           - XP.sum(XP.exp(spell_lp) * spell_lp, axis=-1)
           - XP.where(actions["spell"] < cfg.spells,
                       -XP.sum(XP.exp(target_lp) * target_lp, axis=-1), 0.0))
    return lp, ent
