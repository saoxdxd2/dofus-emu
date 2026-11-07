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


def heuristic_actions(state: Dict[str, jax.Array],
                      tables: Dict[str, jax.Array],
                      cfg: Config) -> Dict[str, jax.Array]:
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
    legal = masks["target"]
    value = jnp.full(legal.shape, -1.0e9, dtype=real_value.dtype)
    value = value.at[:, :, :cfg.spells, :].set(real_value)
    value = jnp.where(legal, value, -1.0e9)
    flat = value.reshape(value.shape[0], value.shape[1], -1)
    best = jnp.argmax(flat, axis=-1)
    n_targets = cfg.n_actors
    spell = best // n_targets
    target = best % n_targets
    has_real = jnp.any(legal[:, :, :cfg.spells, :], axis=(2, 3))
    spell = jnp.where(has_real, spell, cfg.spells)
    target = jnp.where(has_real, target, 0)

    dist = state["dist"]
    damage_range = tables["spell_rmax"]
    rmax = jnp.max(jnp.where(tables["spell_ally"], -1.0, damage_range), axis=2)
    rmin = jnp.min(jnp.where(tables["spell_ally"], 1.0e9,
                             tables["spell_rmin"]), axis=2)
    move = jnp.where(has_real, 1,
                     jnp.where(dist > rmax, 0,
                               jnp.where(dist < rmin, 2, 1)))
    move = jnp.where(acting_actor, move, 1)
    return {"move": move.astype(jnp.int32),
            "spell": jnp.where(acting_actor, spell, cfg.spells).astype(jnp.int32),
            "target": jnp.where(acting_actor, target, 0).astype(jnp.int32)}


def jax_one_hot(values: jax.Array, depth: int) -> jax.Array:
    """One-hot that tolerates the -1 padding element."""
    clipped = jnp.clip(values.astype(jnp.int32), 0, depth - 1)
    return jax.nn.one_hot(clipped, depth)
