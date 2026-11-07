"""A deterministic opponent for the first training phase.

Self-play from a random policy mostly produces draws: neither side closes or
casts, and a 20-round cap turns the episode into a zero. That is a poor value
target and an even poorer policy gradient. The baseline here is deliberately
simple and readable — close if out of range, cast the best affordable spell at
the weakest valid target, heal the ally with the most missing HP — so the
learned policy has an opponent that actually finishes fights. Once the value
head has signal, the trainer can switch to self-play.

This is a training baseline, never a live policy: it has no map, no line of
sight and no spell cooldowns.
"""

from __future__ import annotations

from typing import Dict

import jax
import jax.numpy as jnp

from combat.encounter.env import Config, action_masks


def action_values(state: Dict[str, jax.Array], tables: Dict[str, jax.Array],
                  cfg: Config, attention: jax.Array = None, weight: float = 0.0):
    """The score tensor behind the scripted rule: [B, caster, spell, target], illegal at -1e9.

    Exposed because a channel that adds to a score has to be *priced against the score's own units*.
    A bid count of 0-4 added to a damage value in the hundreds is inert by arithmetic, not by
    design, and the only honest way to choose the weight is to look at the gap it must overturn.
    """
    masks = action_masks(state, tables, cfg)
    team = state["team"]
    acting = state["acting_team"][:, None]
    acting_actor = state["alive"] & (team == acting)

    power = (100.0 + state["power"]) / 100.0                  # [B,N]
    damage = tables["spell_damage"] * power[:, :, None]       # [B,N,S]
    heal = tables["spell_heal"] * power[:, :, None]           # [B,N,S]
    onehot = jax_one_hot(tables["spell_element"], 5)          # [B,N,S,5]
    # Resistance of each *target* to each caster's spell element:
    # state.res [B,T,E], onehot [B,C,S,E] -> [B,C,S,T].
    target_res = jnp.einsum("bte,bcse->bcst", state["res"], onehot)
    damage = damage[:, :, :, None] * (1.0 - target_res / 100.0)
    hp_ratio = state["hp"] / jnp.maximum(state["max_hp"], 1.0)
    heal = heal[:, :, :, None] * (1.0 - hp_ratio[:, None, None, :])
    real_value = jnp.where(tables["spell_ally"][:, :, :, None],
                           heal, jnp.maximum(damage, 0.0))    # [B,N,S,T]
    # An instant kill is not a large damage number: it ends an actor whatever the bar says, and an
    # opponent that ranks spells by damage alone walks past its own. Measured, not assumed: on
    # 1,024 real boss boards zeroing *every* enemy mechanic column changed not one final HP value,
    # while zeroing the player's changed 52 and flipped 26 outcomes -- the scripted side simply
    # never expressed the 162 kill slots it was dealt. Price the removal above any damage the same
    # cast could do, and only on a target this actor can actually remove.
    kill = tables.get("spell_kill")
    if kill is not None:
        removable = ((team[:, :, None] != team[:, None, :])
                     & state["alive"][:, None, :])            # [B,N,T]
        real_value = real_value + (1.0e6 * kill.astype(real_value.dtype)[:, :, :, None]
                                   * removable[:, :, None, :])
    legal = masks["target"]
    value = jnp.full(legal.shape, -1.0e9, dtype=real_value.dtype)
    value = value.at[:, :, :cfg.spells, :].set(real_value)
    value = jnp.where(legal, value, -1.0e9)
    if attention is not None:
        # no truth test on `weight`: inside a scan it can be a traced scalar, and `weight=0` must
        # stay exactly the shipped rule -- adding zero to a score is bit-identical, so the parity
        # holds without a Python branch
        value = value + weight * jnp.where(
            legal, jnp.asarray(attention, dtype=value.dtype)[:, None, None, :], 0.0)
    return value, legal, acting_actor


def heuristic_actions(state: Dict[str, jax.Array],
                      tables: Dict[str, jax.Array],
                      cfg: Config,
                      attention: jax.Array = None,
                      weight: float = 0.0) -> Dict[str, jax.Array]:
    """One team's turn. `attention` is the team's shared bid -- a [B,T] count of how many allies
    this actor's own first-hop plan already aimed at each target -- and `weight` prices it.

    With `weight=0`, which is every existing caller, this is the same arithmetic it always was. The
    wire is deliberately not a negotiation: one hop, computed from each actor's own uncoordinated
    preference, then read back by everyone. That is what makes it a *channel* rather than a planner,
    and what keeps it inside a `lax.scan`.

    The bid must be weighted against the units of the score it enters -- see `action_values`.
    """
    value, legal, acting_actor = action_values(state, tables, cfg, attention, weight)
    flat = value.reshape(value.shape[0], value.shape[1], -1)
    best = jnp.argmax(flat, axis=-1)
    n_targets = cfg.n_actors
    spell = best // n_targets
    target = best % n_targets
    has_real = jnp.any(legal[:, :, :cfg.spells, :], axis=(2, 3))
    spell = jnp.where(has_real, spell, cfg.spells)
    target = jnp.where(has_real, target, 0)

    dist = state["dist"]
    # The chosen spell is only worth casting from a distance that makes it legal, and
    # `action_masks` now admits every MP-reachable spell, so "I can cast something" is not a
    # reason to stand still: an actor that holds while its best spell is out of range spends
    # the turn doing nothing. Walk the gap that this very spell needs -- close it if we are too
    # far, open it if we are inside the minimum -- otherwise hold and cast from here.
    # `pass` has no column in the spell tables, and numpy's gather refuses an out-of-range
    # index, so clamp: these values are only read where `has_real` is true.
    chosen = jnp.minimum(spell, cfg.spells - 1)[..., None]
    too_far = dist > jnp.take_along_axis(tables["spell_rmax"], chosen, axis=2)[:, :, 0]
    too_close = ((dist < jnp.take_along_axis(tables["spell_rmin"], chosen, axis=2)[:, :, 0])
                 & ~jnp.take_along_axis(tables["spell_ally"], chosen, axis=2)[:, :, 0])
    move = jnp.where(has_real, jnp.where(too_far, 0, jnp.where(too_close, 2, 1)), 1)
    move = jnp.where(acting_actor, move, 1)
    return {"move": move.astype(jnp.int32),
            "spell": jnp.where(acting_actor, spell, cfg.spells).astype(jnp.int32),
            "target": jnp.where(acting_actor, target, 0).astype(jnp.int32)}


def jax_one_hot(values: jax.Array, depth: int) -> jax.Array:
    """One-hot that tolerates the -1 padding element."""
    clipped = jnp.clip(values.astype(jnp.int32), 0, depth - 1)
    return jax.nn.one_hot(clipped, depth)
