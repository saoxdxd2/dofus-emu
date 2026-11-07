"""A policy whose target choice depends on which spell it is casting.

`combat/encounter/model.py` reads a board as one flat 49-number vector per actor -- 13 status
numbers followed by 4 spells x 9 -- and then scores targets with

    target = att[actors, actors] + t_bias[spell, actor]

`att` is built from actor tokens only, so for a fixed board the *ordering* of targets is
identical for every spell and differs only by `t_bias`, a learned constant per (spell slot, actor
slot). That is not a tuning problem and no weight edit fixes it: the network is structurally
unable to represent "the dispel goes on whoever holds the state, the damage goes on whoever does
not". Every mechanic that decides a boss fight is exactly that kind of statement -- a relation
between a spell's semantics and a target's state. 108 of 519 boss species put a state on the
board and 138 summon, and the current head cannot route a spell by either.

So this module changes the layers, not the loss:

  * **typed tokens.** An actor token carries who is alive, hurt, buffed and summoning. A spell
    token carries the 79-number mechanic record from `combat.encounter.mechanics` -- what the
    cast puts on the board, on which kind of target, for how long, and with what limits.
  * **a relational target head.** `joint(spell, target) = q(spell) . k(target) / sqrt(d)`: the
    logit for hitting actor j with spell k is a bilinear function of the *spell's mechanics* and
    the *target's state*. It changes when a state moves between actors, which the old head cannot.
  * **spell choice reads the same joint.** A spell's score is its own prior plus how well it
    lands on the best available target, so "is this worth casting" is answered against the board
    rather than remembered as a constant.

Parameter count stays in the same class as the shipped head (~0.2M at the default width), so a
comparison between the two is a comparison of structure and not of capacity.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from combat.encounter.backend import (Array, XP, log_softmax, random, relu, rsqrt,
                                      softmax)
from combat.encounter.env import Config

Params = Dict[str, Any]
NEG = XP.float32(-1.0e9)


def _dense(key: Array, fan_in: int, fan_out: int,
           scale: float = 2.0) -> Tuple[Array, Array]:
    w = random.normal(key, (fan_in, fan_out)) * XP.sqrt(XP.float32(scale / max(1, fan_in)))
    return w, XP.zeros((fan_out,), dtype=XP.float32)


def init_params(key: Array, actor_dim: int, spell_dim: int, global_dim: int,
                cfg: Config, hidden: int = 96, heads: int = 4,
                layers: int = 2) -> Params:
    keys = iter(random.split(key, 64))
    p: Params = {}
    p["actor_enc"] = _dense(next(keys), actor_dim, hidden)
    p["spell_enc"] = _dense(next(keys), spell_dim, hidden)
    p["glob"] = _dense(next(keys), global_dim, hidden)
    p["ln_in"] = (XP.ones((hidden,)), XP.zeros((hidden,)))
    p["blocks"] = []
    for _ in range(layers):
        p["blocks"].append({
            "ln1": (XP.ones((hidden,)), XP.zeros((hidden,))),
            "qkv": _dense(next(keys), hidden, 3 * hidden),
            "out": _dense(next(keys), hidden, hidden),
            "ln2": (XP.ones((hidden,)), XP.zeros((hidden,))),
            "mlp1": _dense(next(keys), hidden, 2 * hidden),
            "mlp2": _dense(next(keys), 2 * hidden, hidden),
        })
    p["t_q"] = _dense(next(keys), hidden, hidden)
    p["t_k"] = _dense(next(keys), hidden, hidden)
    p["t_bias"] = XP.zeros((cfg.n_spells, cfg.n_actors), dtype=XP.float32)
    p["move"] = _dense(next(keys), hidden, 3)
    p["spell_prior"] = _dense(next(keys), hidden, cfg.n_spells)
    p["value1"] = _dense(next(keys), 2 * hidden, hidden)
    p["value2"] = _dense(next(keys), hidden, 1, scale=1.0)
    return p


def _apply(x: Array, wb: Tuple[Array, Array]) -> Array:
    w, b = wb
    return x @ w + b


def _ln(x: Array, wb: Tuple[Array, Array]) -> Array:
    g, b = wb
    mean = XP.mean(x, axis=-1, keepdims=True)
    var = XP.mean(XP.square(x - mean), axis=-1, keepdims=True)
    return (x - mean) * rsqrt(var + 1e-5) * g + b


def _block(p: Dict[str, Any], x: Array, mask: Array, heads: int) -> Array:
    batch, tokens, width = x.shape
    h = _ln(x, p["ln1"])
    qkv = _apply(h, p["qkv"]).reshape(batch, tokens, 3, heads, width // heads)
    q, k, v = qkv[:, :, 0], qkv[:, :, 1], qkv[:, :, 2]
    q, k, v = q.transpose(0, 2, 1, 3), k.transpose(0, 2, 1, 3), v.transpose(0, 2, 1, 3)
    logits = XP.einsum("bhtd,bhsd->bhts", q, k) / XP.sqrt(XP.float32(width // heads))
    logits = XP.where(mask[:, None, None, :], logits, NEG)
    out = XP.einsum("bhts,bhsd->bhtd", softmax(logits, axis=-1), v)
    x = x + _apply(out.transpose(0, 2, 1, 3).reshape(batch, tokens, width), p["out"])
    h = relu(_apply(_ln(x, p["ln2"]), p["mlp1"]))
    return x + _apply(h, p["mlp2"])


def trunk(params: Params, actor: Array, spell: Array, glob: Array,
          attn_mask: Array, heads: int = 4) -> Tuple[Array, Array, Array]:
    """Contextual actor tokens [B,N,H], spell tokens [B,N,S,H] and the global token [B,H].

    Split out because a weight-space edit on a head is solved from the activation that head
    reads, and a second copy of this walk in a tool would drift the first time the net changed.
    """
    x = relu(_apply(actor, params["actor_enc"]))
    s = relu(_apply(spell, params["spell_enc"]))
    g = relu(_apply(glob, params["glob"]))[:, None, :]
    tokens = XP.concatenate([g, x], axis=1)
    mask = XP.concatenate([XP.ones((actor.shape[0], 1), dtype=bool), attn_mask], axis=1)
    tokens = _ln(tokens, params["ln_in"])
    for block in params["blocks"]:
        tokens = _block(block, tokens, mask, heads)
    return tokens[:, 1:], s, tokens[:, 0]


def forward(params: Params, feats: Dict[str, Array], heads: int = 4) -> Dict[str, Array]:
    """feats: ``actor`` [B,N,Fa], ``spell`` [B,N,S,Fs], ``glob`` [B,G], ``mask`` [B,N].

    Returns the same logit shapes as `model.forward`, so the environment, the masks and the
    training loss do not change: ``move`` [B,N,3], ``spell`` [B,N,S+1], ``target`` [B,N,S+1,N],
    ``value`` [B].
    """
    attn_mask = feats["mask"]
    hidden = params["actor_enc"][0].shape[1]
    actors, spell_tokens, global_token = trunk(params, feats["actor"], feats["spell"],
                                               feats["glob"], attn_mask, heads)
    q = _apply(spell_tokens, params["t_q"])                  # [B,N,S,H]
    k = _apply(actors, params["t_k"])                        # [B,N,H]
    joint = XP.einsum("bikd,bjd->bikj", q, k) / XP.sqrt(XP.float32(hidden))
    target = joint + params["t_bias"][None, None, :-1, :]       # [B,N,S,N]
    target = XP.concatenate(                                     # pass casts at no target
        [target, XP.zeros(target.shape[:2] + (1, target.shape[3]),
                          dtype=target.dtype)], axis=2)         # [B,N,S+1,N]
    best = XP.max(XP.where(attn_mask[:, None, None, :], joint, NEG), axis=-1)
    spell = _apply(actors, params["spell_prior"]) + XP.concatenate(
        [best, XP.zeros((best.shape[0], best.shape[1], 1), dtype=XP.float32)], axis=-1)
    count = XP.maximum(XP.sum(attn_mask, axis=1, keepdims=True), 1.0)
    value, _ = value_readout(params, actors, global_token, attn_mask, count)
    return {"move": _apply(actors, params["move"]), "spell": spell,
            "target": target, "value": value}


def split_obs(obs: Array, cfg: Config, spell_feats: int = 9) -> Dict[str, Array]:
    """`observe`'s flat [B,N,F] vector into the typed tokens this head reads.

    The trainer, the rollouts and the live policy all pass the flat observation the environment
    already builds, so the architecture is swappable at one place instead of everywhere. Spell
    columns beyond `cfg.spells` are table padding and are not castable, so they are dropped here
    rather than silently becoming extra spells.
    """
    columns = (obs.shape[-1] - 13) // spell_feats
    return {"actor": obs[..., :13],
            "spell": obs[..., 13:].reshape(obs.shape[0], obs.shape[1], columns,
                                           spell_feats)[:, :, :cfg.spells],
            "glob": None, "mask": None}


def forward_obs(params: Params, obs: Array, glob: Array, attn_mask: Array,
                cfg: Config, heads: int = 4) -> Dict[str, Array]:
    feats = split_obs(obs, cfg)
    feats["glob"], feats["mask"] = glob, attn_mask
    return forward(params, feats, heads)


def init_params_obs(key: Array, feat_dim: int, global_dim: int, cfg: Config,
                    hidden: int = 96, heads: int = 4, layers: int = 2,
                    spell_feats: int = 9) -> Params:
    columns = (feat_dim - 13) // spell_feats
    return init_params(key, 13, spell_feats, global_dim, cfg, hidden, heads, layers)


def value_readout(params: Params, actors: Array, global_token: Array, attn_mask: Array,
                  count: Array) -> Tuple[Array, Array]:
    """The value estimate and the activation its last layer reads (one pooling, shared)."""
    pooled = XP.sum(XP.where(attn_mask[..., None], actors, 0.0), axis=1) / count
    pooled = XP.concatenate([pooled, global_token], axis=-1)
    hidden = relu(_apply(pooled, params["value1"]))
    return XP.tanh(_apply(hidden, params["value2"]))[:, 0], hidden


def install_relational(params: Params, M: Array, hidden: int) -> Params:
    """Author the relational head from a solved matrix instead of training it.

    `forward` scores a pair as `q(spell) . k(target) / sqrt(hidden)`, so installing `M` verbatim
    would scale every logit by 1/sqrt(hidden) -- the same ordering, but a different function than
    the one that was solved, and a written artifact nobody could reproduce. `t_q = sqrt(hidden) M`
    and `t_k = I` makes the network compute exactly `s^T M a`. The slot bias and the spell prior go
    to zero with it, because the write claims the pair score comes from the mechanics alone.
    """
    import math
    scale = math.sqrt(hidden)
    zeros = XP.zeros((hidden,), dtype=XP.float32)
    written = {k: (dict(v) if isinstance(v, dict) else v) for k, v in params.items()}
    written["t_q"] = (XP.asarray(scale, XP.float32) * XP.asarray(M, XP.float32), zeros)
    written["t_k"] = (XP.eye(hidden, dtype=XP.float32), zeros)
    written["t_bias"] = XP.zeros_like(params["t_bias"])
    written["spell_prior"] = (XP.zeros_like(params["spell_prior"][0]),
                              XP.zeros_like(params["spell_prior"][1]))
    return written


def greedy_actions(logits: Dict[str, Array], masks: Dict[str, Array],
                   cfg: Config) -> Dict[str, Array]:
    move = XP.argmax(XP.where(masks["move"], logits["move"], NEG), axis=-1)
    spell = XP.argmax(XP.where(masks["spell"], logits["spell"], NEG), axis=-1)
    target_logits = XP.take_along_axis(
        logits["target"], spell[..., None, None], axis=2)[:, :, 0]
    target_mask = XP.take_along_axis(
        masks["target"], spell[..., None, None], axis=2)[:, :, 0]
    target = XP.argmax(XP.where(target_mask, target_logits, NEG), axis=-1)
    return {"move": move.astype(XP.int32), "spell": spell.astype(XP.int32),
            "target": XP.where(spell < cfg.spells, target, 0).astype(XP.int32)}


def factor_logp(logits: Dict[str, Array], masks: Dict[str, Array],
                actions: Dict[str, Array], cfg: Config) -> Tuple[Array, Array]:
    """Per-actor log-probability and entropy, with the target law conditioned on the spell."""
    move_lp = log_softmax(XP.where(masks["move"], logits["move"], NEG), axis=-1)
    spell_lp = log_softmax(XP.where(masks["spell"], logits["spell"], NEG), axis=-1)
    chosen = XP.take_along_axis(
        logits["target"], actions["spell"][..., None, None], axis=2)[:, :, 0, :]
    chosen_mask = XP.take_along_axis(
        masks["target"], actions["spell"][..., None, None], axis=2)[:, :, 0, :]
    target_lp = log_softmax(XP.where(chosen_mask, chosen, NEG), axis=-1)
    lp = (XP.take_along_axis(move_lp, actions["move"][..., None], -1)[..., 0]
          + XP.take_along_axis(spell_lp, actions["spell"][..., None], -1)[..., 0]
          + XP.where(actions["spell"] < cfg.spells,
                     XP.take_along_axis(target_lp, actions["target"][..., None], -1)[..., 0],
                     0.0))
    ent = (-XP.sum(XP.exp(move_lp) * move_lp, axis=-1)
           - XP.sum(XP.exp(spell_lp) * spell_lp, axis=-1)
           - XP.where(actions["spell"] < cfg.spells,
                      -XP.sum(XP.exp(target_lp) * target_lp, axis=-1), 0.0))
    return lp, ent
