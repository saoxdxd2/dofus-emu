"""A batched, JAX-traceable encounter environment.

State is a dict of arrays with a leading batch dimension. Tables (spells) are
also batched: a sample can be a level-30 Iop with a fire spell in slot 0 and a
level-44 Eniripsa with a heal there, and the same environment code serves both.
Nothing branches on Python values inside the step; every conditional is a
``jnp.where`` so the whole rollout compiles to one device program.

Turn structure
--------------
One player side (``allies`` actors, matching the live solo character) and one
monster side (up to ``enemies`` actors). A turn is one whole team acting.
Actors act in slot order; an actor killed earlier in the same turn does not
act. A cast is legal only when AP, target team, target life and range all hold.
An actor repeats its chosen spell and target while AP remains, up to
``casts_per_turn``; that is the AP economy the live client has, and modelling
it as one cast per turn made the player's 10-12 AP mostly wasted. Movement is a
distance change along the line between the teams: each actor pays MP once to
close or open the gap, then casts.

The reward is the only thing outside this module: +1 for the acting team's win,
-1 for its loss, 0 for a draw. The value head therefore learns P(win | state,
acting team), which is exactly ``fight.estimate``'s missing number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import jax
import jax.numpy as jnp


@dataclass(frozen=True)
class Config:
    allies: int = 1
    enemies: int = 4
    spells: int = 4
    max_rounds: int = 16
    max_dist: int = 12
    casts_per_turn: int = 4

    @property
    def n_actors(self) -> int:
        return self.allies + self.enemies

    @property
    def n_spells(self) -> int:
        # The last index is "pass"; it always exists and always costs nothing.
        return self.spells + 1


def team_of(cfg: Config) -> jax.Array:
    return (jnp.arange(cfg.n_actors) >= cfg.allies).astype(jnp.int32)


def initial_state(spec: Dict[str, jax.Array],
                  tables: Dict[str, jax.Array],
                  cfg: Config) -> Dict[str, jax.Array]:
    """Build the first state from an encounter specification.

    Required spec keys (all with a leading batch axis unless noted):
    ``hp, max_hp, ap_max, mp_max, power, res, dist, initiative, alive,
    first_team``. ``alive`` is a boolean/int mask; dead slots must still carry
    finite values so gathers never touch a sentinel.
    """
    team = team_of(cfg)
    alive = spec["alive"].astype(bool)
    hp = jnp.where(alive, spec["hp"], 0.0)
    acting = spec["first_team"].astype(jnp.int32)
    ours = team[None, :] == acting[:, None]
    ap = jnp.where(ours & alive, spec["ap_max"], 0.0)
    mp = jnp.where(ours & alive, spec["mp_max"], 0.0)
    batch = hp.shape[0]
    return {
        "hp": hp,
        "max_hp": jnp.maximum(spec["max_hp"], 1.0),
        "ap_max": spec["ap_max"],
        "mp_max": spec["mp_max"],
        "ap": ap,
        "mp": mp,
        "power": spec["power"],
        "res": spec["res"],
        "dist": spec["dist"],
        "initiative": spec["initiative"],
        "alive": alive,
        "team": jnp.broadcast_to(team, (batch, cfg.n_actors)),
        "acting_team": acting,
        "round": jnp.zeros((batch,), dtype=jnp.int32),
        "done": jnp.zeros((batch,), dtype=bool),
        "winner": jnp.full((batch,), -1, dtype=jnp.int32),
    }


def _take_actor_spell(tables: Dict[str, jax.Array], i: int,
                      spell: jax.Array) -> Dict[str, jax.Array]:
    """Gather spell columns for actor slot ``i`` at the chosen spell index.

    All tables are [B, N, S]; ``spell`` is [B]. The result is [B] per field.
    """
    b = jnp.arange(spell.shape[0])
    last = tables["spell_ap"].shape[2] - 1
    idx = jnp.minimum(spell, last)      # pass has no column; gather is harmless
    return {
        "ap": tables["spell_ap"][b, i, idx],
        "rmin": tables["spell_rmin"][b, i, idx],
        "rmax": tables["spell_rmax"][b, i, idx],
        "damage": tables["spell_damage"][b, i, idx],
        "heal": tables["spell_heal"][b, i, idx],
        "element": tables["spell_element"][b, i, idx],
        "ally": tables["spell_ally"][b, i, idx],
    }


def _gather_target(field: jax.Array, target: jax.Array) -> jax.Array:
    """field [B, N, ...] -> [B, ...] at a per-row target index."""
    b = jnp.arange(field.shape[0])
    return field[b, target]


def _element_resist(res: jax.Array, target: jax.Array,
                    element: jax.Array) -> jax.Array:
    """Per-row resistance for the target and element (percent, may be negative)."""
    row = _gather_target(res, target)                 # [B, E]
    safe = jnp.clip(element, 0, row.shape[1] - 1)
    picked = jnp.take_along_axis(row, safe[:, None], axis=1)
    return picked[:, 0]


def step(state: Dict[str, jax.Array],
         actions: Dict[str, jax.Array],
         tables: Dict[str, jax.Array],
         cfg: Config) -> Dict[str, jax.Array]:
    """Apply one team's turn. ``actions`` has move [B,N], spell [B,N], target [B,N]."""
    hp = state["hp"]
    max_hp = state["max_hp"]
    ap = state["ap"]
    mp = state["mp"]
    dist = state["dist"]
    alive = state["alive"]
    team = state["team"]
    acting = state["acting_team"]
    power = state["power"]
    res = state["res"]
    batch = hp.shape[0]
    rows = jnp.arange(batch)
    pass_index = cfg.spells

    move = actions["move"].astype(jnp.int32)
    spell = actions["spell"].astype(jnp.int32)
    target = actions["target"].astype(jnp.int32)

    # Slot order is the initiative order after sorting in the dataset builder.
    # A simple sequential loop is enough: each actor reads the state left by
    # the previous one, which is what makes a kill cancel the victim's action.
    still_running = ~state["done"]
    for i in range(cfg.n_actors):
        act = alive[:, i] & (team[:, i] == acting) & still_running
        if not isinstance(act, jax.Array):  # pragma: no cover - trace sanity
            act = jnp.asarray(act)

        # -- cast (repeat while AP allows) -------------------------------
        # Casting uses the same distance the mask saw. Applying the move
        # first would silently drop any spell that the policy picked while
        # it was still in range, because the mask is computed pre-move.
        s = spell[:, i]
        f = _take_actor_spell(tables, i, jnp.minimum(s, pass_index))
        real = act & (s < pass_index)
        tgt = jnp.clip(target[:, i], 0, cfg.n_actors - 1)
        tgt_team = _gather_target(team, tgt)
        same_side = tgt_team == team[:, i]
        team_ok = jnp.where(f["ally"], same_side, ~same_side)
        in_range = (dist[:, i] >= f["rmin"]) & (dist[:, i] <= f["rmax"])
        range_ok = jnp.where(f["ally"], jnp.ones_like(in_range), in_range)

        # Power and resistance do not change inside a turn, so the per-cast
        # amount is computed once; only legality and the target's life are
        # re-read on each repetition.
        resist = _element_resist(res, tgt, f["element"])
        damage = jnp.floor(f["damage"] * (100.0 + power[:, i]) / 100.0
                           * (100.0 - resist) / 100.0)
        heal = jnp.floor(f["heal"] * (100.0 + power[:, i]) / 100.0)

        for _ in range(cfg.casts_per_turn):
            tgt_alive = _gather_target(alive, tgt)
            legal = (real & (ap[:, i] >= f["ap"]) & tgt_alive
                     & team_ok & range_ok)
            hit = jnp.where(legal & ~f["ally"],
                            jnp.maximum(damage, 0.0), 0.0)
            mend = jnp.where(legal & f["ally"], heal, 0.0)
            # A single scatter per cast: valid hits can overkill; healing caps
            # below. The next repetition sees the target's updated life.
            hp = hp.at[rows, tgt].add(mend - hit)
            ap = ap.at[:, i].set(
                jnp.where(legal, ap[:, i] - f["ap"], ap[:, i]))
            alive = hp > 0.0

        # -- movement ----------------------------------------------------
        budget = jnp.where(act, mp[:, i], 0.0)
        direction = jnp.where(move[:, i] == 0, -1.0,
                              jnp.where(move[:, i] == 2, 1.0, 0.0))
        new_dist = jnp.clip(dist[:, i] + direction * budget, 1.0,
                            float(cfg.max_dist))
        dist = dist.at[:, i].set(jnp.where(act, new_dist, dist[:, i]))
        mp = mp.at[:, i].set(jnp.where(act, 0.0, mp[:, i]))

    hp = jnp.clip(hp, 0.0, max_hp)
    alive = hp > 0.0

    team_alive = jnp.stack([
        jnp.any(alive & (team == t), axis=1) for t in range(2)
    ], axis=1)                                            # [B, 2]
    next_round = state["round"] + (acting == 1).astype(jnp.int32)
    terminal = (~team_alive[:, 0]) | (~team_alive[:, 1]) | (next_round >= cfg.max_rounds)
    both_dead = (~team_alive[:, 0]) & (~team_alive[:, 1])
    winner = jnp.where(
        terminal,
        jnp.where(both_dead, 2,
                  jnp.where(~team_alive[:, 0], 1,
                            jnp.where(~team_alive[:, 1], 0, 2))),
        -1).astype(jnp.int32)
    done = state["done"] | terminal

    # Teams alternate until the fight ends. On the terminal step the state is
    # frozen, so the caller can read the final HP for calibration features.
    next_acting = jnp.where(done, acting, 1 - acting)
    next_team_acts = (team == next_acting[:, None]) & alive
    ap = jnp.where(done[:, None], ap,
                   jnp.where(next_team_acts, state["ap_max"], 0.0))
    mp = jnp.where(done[:, None], mp,
                   jnp.where(next_team_acts, state["mp_max"], 0.0))

    return {
        **state,
        "hp": hp,
        "ap": ap,
        "mp": mp,
        "dist": dist,
        "alive": alive,
        "acting_team": next_acting,
        "round": jnp.where(done, state["round"], next_round),
        "done": done,
        "winner": jnp.where(done, winner, state["winner"]),
    }


def observe(state: Dict[str, jax.Array], tables: Dict[str, jax.Array],
            cfg: Config,
            acting_team: Optional[jax.Array] = None) -> Tuple[jax.Array, jax.Array, jax.Array]:
    """Per-actor tokens [B,N,F], a global vector [B,G], and the attention mask.

    Everything is expressed relative to the acting team: the same weights play
    both sides, which is what makes self-play symmetric.
    """
    team = state["team"]
    acting = state["acting_team"] if acting_team is None else acting_team
    same = (team == acting[:, None])
    alive = state["alive"]
    hp_ratio = state["hp"] / jnp.maximum(state["max_hp"], 1.0)
    ap_ratio = state["ap"] / jnp.maximum(state["ap_max"], 1.0)
    mp_ratio = state["mp"] / jnp.maximum(state["mp_max"], 1.0)

    base = jnp.stack([
        same.astype(jnp.float32),
        alive.astype(jnp.float32),
        hp_ratio,
        ap_ratio,
        mp_ratio,
        state["dist"] / float(cfg.max_dist),
        jnp.clip(state["initiative"] / 100.0, 0.0, 1.0),
        jnp.clip(state["power"] / 200.0, 0.0, 1.0),
    ] + [state["res"][..., k] / 100.0 for k in range(5)], axis=-1)

    # Spell columns are static per sample but batched: [B,N,S]. Damage and
    # heal are log-scaled: at level 60 the old linear clip saturated most
    # damage spells at the same value, so the policy could not rank them.
    ap_safe = jnp.maximum(tables["spell_ap"], 1.0)
    spell_feats = jnp.stack([
        tables["spell_ap"] / 12.0,
        tables["spell_rmin"] / float(cfg.max_dist),
        tables["spell_rmax"] / float(cfg.max_dist),
        jnp.log1p(tables["spell_damage"]) / jnp.log(301.0),
        jnp.log1p(tables["spell_heal"]) / jnp.log(301.0),
        jnp.clip(tables["spell_element"] / 5.0, 0.0, 1.0),
        tables["spell_ally"].astype(jnp.float32),
        jnp.log1p(tables["spell_damage"] / ap_safe) / jnp.log(61.0),
        jnp.log1p(tables["spell_heal"] / ap_safe) / jnp.log(61.0),
    ], axis=-1)                                           # [B,N,S,9]
    b, n, s_dim, f_dim = spell_feats.shape
    obs = jnp.concatenate(
        [base, spell_feats.reshape(b, n, s_dim * f_dim)], axis=-1)

    ours = alive & same
    theirs = alive & ~same
    def _mean(values, mask):
        count = jnp.maximum(jnp.sum(mask, axis=1), 1.0)
        return jnp.sum(jnp.where(mask, values, 0.0), axis=1) / count

    glob = jnp.stack([
        jnp.sum(ours, axis=1) / float(cfg.allies),
        jnp.sum(theirs, axis=1) / float(cfg.enemies),
        state["round"] / float(cfg.max_rounds),
        _mean(hp_ratio, ours),
        _mean(hp_ratio, theirs),
        _mean(state["dist"] / float(cfg.max_dist), ours),
        _mean(state["dist"] / float(cfg.max_dist), theirs),
        _mean(ap_ratio, ours),
        _mean(ap_ratio, theirs),
    ], axis=-1)
    attn_mask = alive
    return obs, glob, attn_mask


def action_masks(state: Dict[str, jax.Array], tables: Dict[str, jax.Array],
                 cfg: Config) -> Dict[str, jax.Array]:
    """Legal (move, spell, target-per-spell) actions for every actor.

    The target mask is per spell, so the policy cannot pick a heal on an enemy
    or a melee spell from across the map in the first place.
    """
    team = state["team"]
    alive = state["alive"]
    acting = state["acting_team"]
    act = alive & (team == acting[:, None])
    batch = alive.shape[0]
    n = cfg.n_actors
    s2 = cfg.n_spells

    move_mask = jnp.broadcast_to(act[:, :, None], (batch, n, 3))
    spell_mask = jnp.zeros((batch, n, s2), dtype=bool)
    target_mask = jnp.zeros((batch, n, s2, n), dtype=bool)

    for s in range(cfg.spells):
        ap_cost = tables["spell_ap"][:, :, s]
        rmin = tables["spell_rmin"][:, :, s]
        rmax = tables["spell_rmax"][:, :, s]
        ally = tables["spell_ally"][:, :, s]
        same_target = (team[:, None, :] == team[:, :, None])
        in_range = ((state["dist"][:, :, None] >= rmin[..., None])
                    & (state["dist"][:, :, None] <= rmax[..., None]))
        target_ok = jnp.where(ally[..., None], same_target, ~same_target)
        range_gate = jnp.where(ally[..., None], jnp.ones_like(in_range), in_range)
        valid_target = alive[:, None, :] & target_ok & range_gate
        can_cast = act & (state["ap"] >= ap_cost) & jnp.any(valid_target, axis=-1)
        spell_mask = spell_mask.at[:, :, s].set(can_cast)
        target_mask = target_mask.at[:, :, s, :].set(
            valid_target & can_cast[..., None])

    # Pass: always legal, no target choice. Slot 0 is a harmless placeholder.
    pass_index = cfg.spells
    spell_mask = spell_mask.at[:, :, pass_index].set(act)
    target_mask = target_mask.at[:, :, pass_index, 0].set(act)

    return {
        "move": move_mask,
        "spell": spell_mask,
        "target": target_mask,
        "actor": act,
    }


def terminal_reward(winner: jax.Array, acting_team: jax.Array) -> jax.Array:
    """+1 / -1 / 0 from the acting team's point of view."""
    return jnp.where(winner == acting_team, 1.0,
                     jnp.where(winner == 2, 0.0, -1.0)).astype(jnp.float32)


def sample_actions(key: jax.Array,
                   logits: Dict[str, jax.Array],
                   masks: Dict[str, jax.Array],
                   cfg: Config) -> Dict[str, jax.Array]:
    """Sample a legal factored action per actor and return its log-probability.

    ``logits`` keys: move [B,N,3], spell [B,N,S+1], target [B,N,S+1,N].
    """
    k_move, k_spell, k_target = jax.random.split(key, 3)
    neg = jnp.float32(-1.0e9)
    move_logits = jnp.where(masks["move"], logits["move"], neg)
    spell_logits = jnp.where(masks["spell"], logits["spell"], neg)
    move = jax.random.categorical(k_move, move_logits)
    spell = jax.random.categorical(k_spell, spell_logits)

    # Pick the target distribution belonging to the sampled spell.
    chosen = jnp.take_along_axis(
        logits["target"], spell[..., None, None], axis=2)[:, :, 0, :]  # [B,N,N]
    chosen_mask = jnp.take_along_axis(
        masks["target"], spell[..., None, None], axis=2)[:, :, 0, :]
    target_logits = jnp.where(chosen_mask, chosen, neg)
    target = jax.random.categorical(k_target, target_logits)
    pass_index = cfg.spells
    target = jnp.where(spell < pass_index, target, 0)

    logp = _action_logp(move_logits, spell_logits, target_logits,
                        {"move": move, "spell": spell, "target": target},
                        cfg)
    return {"move": move, "spell": spell, "target": target, "logp": logp}


def sample_random_actions(key: jax.Array, masks: Dict[str, jax.Array],
                          cfg: Config) -> Dict[str, jax.Array]:
    """Uniform legal actions, for an evaluation baseline and curriculum."""
    k_move, k_spell, k_target = jax.random.split(key, 3)
    neg = jnp.float32(-1.0e9)
    move = jax.random.categorical(k_move, jnp.where(masks["move"], 0.0, neg))
    spell = jax.random.categorical(k_spell, jnp.where(masks["spell"], 0.0, neg))
    chosen_mask = jnp.take_along_axis(
        masks["target"], spell[..., None, None], axis=2)[:, :, 0, :]
    target = jax.random.categorical(k_target, jnp.where(chosen_mask, 0.0, neg))
    target = jnp.where(spell < cfg.spells, target, 0)
    return {"move": move, "spell": spell, "target": target}


def _action_logp(move_logits: jax.Array, spell_logits: jax.Array,
                 target_logits: jax.Array, actions: Dict[str, jax.Array],
                 cfg: Config) -> jax.Array:
    """Sum of the factored log-probabilities for one actor: [B,N]."""
    move = jax.nn.log_softmax(move_logits, axis=-1)
    spell = jax.nn.log_softmax(spell_logits, axis=-1)
    target = jax.nn.log_softmax(target_logits, axis=-1)
    move_lp = jnp.take_along_axis(
        move, actions["move"][..., None], axis=-1)[..., 0]
    spell_lp = jnp.take_along_axis(
        spell, actions["spell"][..., None], axis=-1)[..., 0]
    target_lp = jnp.take_along_axis(
        target, actions["target"][..., None], axis=-1)[..., 0]
    target_lp = jnp.where(actions["spell"] < cfg.spells, target_lp, 0.0)
    return move_lp + spell_lp + target_lp
