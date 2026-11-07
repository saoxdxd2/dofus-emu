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

# `jax` and `jnp` come from the backend shim, not from the packages: the interpreter that ships
# (3.11.9) has no jax, and `initial_state` / `observe` / `action_masks` -- the three functions the
# live policy calls -- are dense array math that numpy does exactly. Importing jax here directly is
# what kept the whole trained path unloadable on an installed copy, through `model.py` and
# `live_policy.py`, which is the one thing combat/encounter/backend.py exists to prevent. The
# annotations below still say `jax.Array`; with `from __future__ import annotations` they are never
# evaluated, so they cost nothing and keep the shapes readable on the training box.
from combat.encounter.backend import XP as jnp, at, jax, log_softmax, random as jrandom


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


# The five columns that say what a cast does beyond its damage line. `step` applies them as physics
# and `observe` emits them as input, keyed off this one list, so a corpus cannot half-upgrade: the
# columns the network is told about are the columns the fight enforces.
MECH_TABLES = ("spell_kill", "spell_push", "spell_ap_delta", "spell_mp_delta",
               "spell_duration")

# The three columns that say what a summon puts on the board: how many bodies, the HP of each, and
# the strongest line of each body's own kit. They are physics *and* input off the same list, exactly
# like `MECH_TABLES`: a corpus that predates them gets the old fight, bit for bit.
SUMMON_TABLES = ("spell_summon_bodies", "spell_summon_hp", "spell_summon_damage")


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
    state = {
        "hp": hp,
        "max_hp": jnp.maximum(spec["max_hp"], 1.0),
        "ap_max": spec["ap_max"],
        "mp_max": spec["mp_max"],
        "ap": ap,
        "mp": mp,
        "ap_mod": jnp.zeros_like(ap),
        "mp_mod": jnp.zeros_like(ap),
        "mod_rounds": jnp.zeros_like(ap),
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
    if all(key in tables for key in SUMMON_TABLES):
        # One mean-field swarm per team rather than new actor slots. Actors would have to be
        # inserted into the initiative order, given kits, and carried through `observe`, and the
        # batch axis is fixed at build time -- 8 bodies per cast at 4 casts per turn has no slot
        # budget. What a swarm does to a fight is stand between the boss and the attacker and add
        # its own damage each round, and both of those are one number per team.
        state["pool_hp"] = jnp.zeros((batch, 2))
        state["pool_dmg"] = jnp.zeros((batch, 2))
    return state


def _take_actor_spell(tables: Dict[str, jax.Array], i: int,
                      spell: jax.Array) -> Dict[str, jax.Array]:
    """Gather spell columns for actor slot ``i`` at the chosen spell index.

    All tables are [B, N, S]; ``spell`` is [B]. The result is [B] per field. The four mechanic
    columns are optional: a corpus that predates them gets zeros, and zeros are exactly what the
    old arithmetic assumed, so the same boards must still play out identically.
    """
    b = jnp.arange(spell.shape[0])
    last = tables["spell_ap"].shape[2] - 1
    idx = jnp.minimum(spell, last)      # pass has no column; gather is harmless
    out = {
        "ap": tables["spell_ap"][b, i, idx],
        "rmin": tables["spell_rmin"][b, i, idx],
        "rmax": tables["spell_rmax"][b, i, idx],
        "damage": tables["spell_damage"][b, i, idx],
        "heal": tables["spell_heal"][b, i, idx],
        "element": tables["spell_element"][b, i, idx],
        "ally": tables["spell_ally"][b, i, idx],
    }
    for out_key, table_key, default in (
            ("ap_delta", "spell_ap_delta", 0.0), ("mp_delta", "spell_mp_delta", 0.0),
            ("push", "spell_push", 0.0), ("duration", "spell_duration", 0.0),
            ("bodies", "spell_summon_bodies", 0.0), ("summon_hp", "spell_summon_hp", 0.0),
            ("summon_damage", "spell_summon_damage", 0.0),
            ("kill", "spell_kill", False)):
        if table_key in tables:
            out[out_key] = tables[table_key][b, i, idx]
        else:
            out[out_key] = jnp.full((b.shape[0],), default)
    return out


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
    # Timed characteristic reductions. Absent from a state that predates them, and zero is exactly
    # the arithmetic the old refill assumed, so boards without the columns cannot drift.
    ap_mod = state.get("ap_mod", jnp.zeros_like(ap))
    mp_mod = state.get("mp_mod", jnp.zeros_like(mp))
    mod_rounds = state.get("mod_rounds", jnp.zeros_like(ap))

    has_summons = all(key in tables for key in SUMMON_TABLES)
    if has_summons:
        pool_hp = state.get("pool_hp", jnp.zeros((batch, 2)))
        pool_dmg = state.get("pool_dmg", jnp.zeros((batch, 2)))
        # A swarm cannot outbulk the team that summoned it: 4 casts per turn of an 8-body spell
        # would otherwise print an army with no ceiling. The cap is that team's own total HP, so
        # the pool is measured in the same units as the fighters it stands in front of.
        pool_cap = jnp.stack([jnp.sum(max_hp * (team == t), axis=1)
                              for t in range(2)], axis=1)          # [B, 2]

    move = actions["move"].astype(jnp.int32)
    spell = actions["spell"].astype(jnp.int32)
    target = actions["target"].astype(jnp.int32)

    # Slot order is the initiative order after sorting in the dataset builder.
    # A simple sequential loop is enough: each actor reads the state left by
    # the previous one, which is what makes a kill cancel the victim's action.
    still_running = ~state["done"]
    for i in range(cfg.n_actors):
        act = alive[:, i] & (team[:, i] == acting) & still_running
        if not isinstance(act, jnp.ndarray):  # trace sanity on either backend
            # `jnp`, not `jax`: the numpy branch of the backend shim sets `jax = None`, so
            # naming the package here made `step()` unloadable on exactly the interpreter
            # the shim exists to serve. Measured 2026-11-05 by the env suite the gate runs
            # as a subprocess: AttributeError 'NoneType' object has no attribute 'Array'.
            act = jnp.asarray(act)

        s = spell[:, i]
        f = _take_actor_spell(tables, i, jnp.minimum(s, pass_index))
        real = act & (s < pass_index)
        tgt = jnp.clip(target[:, i], 0, cfg.n_actors - 1)

        # -- movement first ------------------------------------------------
        # The client's turn is walk-then-cast, and `action_masks` admits a spell on
        # any MP-reachable distance, so MP is spent here. If the walked distance is
        # not legal for the chosen spell while some reachable one is, the actor lands
        # on the nearest legal reachable distance instead -- the "move to a cell you
        # can cast from" that `LivePolicy._cast_cell` already does on the live path.
        # This is also what the module docstring has always claimed ("pays MP once to
        # close or open the gap, then casts"); the order below is what makes it true.
        budget = jnp.where(act, mp[:, i], 0.0)
        direction = jnp.where(move[:, i] == 0, -1.0,
                              jnp.where(move[:, i] == 2, 1.0, 0.0))
        walked = jnp.clip(dist[:, i] + direction * budget, 1.0, float(cfg.max_dist))
        # The walk is one-directional and the whole bar is offered, so the distances the actor
        # can actually arrive at are the segment between where it stands and where the chosen
        # direction would take it. Stopping at the first legal distance *along that segment*
        # is what "walk in, then cast" means; it is also why a hold is not a free teleport --
        # holding collapses the segment to the current distance, so an out-of-range spell stays
        # out of range and the move head keeps its meaning.
        seg_lo = jnp.minimum(dist[:, i], walked)
        seg_hi = jnp.maximum(dist[:, i], walked)
        land_lo = jnp.maximum(seg_lo, f["rmin"])
        land_hi = jnp.minimum(seg_hi, f["rmax"])
        snap = (real & ~f["ally"]
                & ~((walked >= f["rmin"]) & (walked <= f["rmax"]))
                & (land_lo <= land_hi))
        new_dist = jnp.where(snap, jnp.clip(walked, land_lo, land_hi), walked)
        dist = at(dist)[:, i].set(jnp.where(act, new_dist, dist[:, i]))
        mp = at(mp)[:, i].set(jnp.where(act, 0.0, mp[:, i]))

        # -- cast (repeat while AP allows), at the distance just landed on --
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
            if has_summons:
                # The swarm stands in front of the fighter it was summoned for, so the hit lands on
                # bodies before it lands on the boss. `absorb` is 0 wherever there is no pool, and
                # `hit - 0.0` is exact, so a board with no summon columns plays out unchanged.
                tgt_pool = _gather_target(team, tgt)
                standing = _gather_target(pool_hp, tgt_pool)
                striking = _gather_target(pool_dmg, tgt_pool)
                absorb = jnp.minimum(hit, standing)
                hp = at(hp)[rows, tgt].add(mend - (hit - absorb))
                pool_hp = at(pool_hp)[rows, tgt_pool].add(-absorb)
                # Half a swarm hits for half: output travels with surviving bodies instead of
                # sitting on the board as a free attack after the bodies are gone.
                pool_dmg = at(pool_dmg)[rows, tgt_pool].set(
                    striking * ((standing - absorb) / jnp.maximum(standing, 1.0)))
            else:
                hp = at(hp)[rows, tgt].add(mend - hit)
            landed = legal & ~f["ally"]
            if has_summons:
                # The bodies arrive on every legal repetition of the cast, which is what the AP
                # economy already does for damage: a summoner that can afford the spell four times
                # this turn summons four times.
                called = legal & (f["bodies"] > 0)
                own = team[:, i]
                # The cap is applied to both team columns rather than only the caster's: the other
                # column is already inside it, so the extra `minimum` cannot change it, and a
                # per-column cap would need the scatter's own mask instead of its index.
                pool_hp = jnp.minimum(
                    at(pool_hp)[rows, own].add(
                        jnp.where(called, f["bodies"] * f["summon_hp"], 0.0)),
                    pool_cap)
                pool_dmg = jnp.minimum(
                    at(pool_dmg)[rows, own].add(
                        jnp.where(called, f["bodies"] * f["summon_damage"], 0.0)),
                    pool_cap)
            if "spell_kill" in tables:
                # "Tue la cible": the target dies whatever the health bar says. 46 boss species
                # carry it, and no amount of HP arithmetic on a damage column expresses it.
                hp_after = _gather_target(hp, tgt)
                hp = at(hp)[rows, tgt].set(
                    jnp.where(landed & (f["kill"] > 0), 0.0, hp_after))
            if "spell_push" in tables:
                # Pushback and attraction move the target, which is what makes a spell's range
                # window and the walk that reaches it one coupled problem instead of two.
                moved = jnp.clip(dist[rows, tgt] + f["push"], 1.0, float(cfg.max_dist))
                dist = at(dist)[rows, tgt].set(jnp.where(landed, moved, dist[rows, tgt]))
            if "spell_ap_delta" in tables or "spell_mp_delta" in tables:
                # A reduction in AP or MP is a timed modifier in these tables -- the client writes
                # "-#1 AP (2 turns)" -- so it bites on the victim's own turns, which is the only
                # place it can. A reduction with no stated duration lasts one turn. The old
                # immediate subtraction was not a simplification, it was a no-op: the victim's AP
                # is refilled at the start of its next turn, so a value subtracted during someone
                # else's turn never survived to be spent.
                landed_mod = landed & ((f["ap_delta"] != 0.0) | (f["mp_delta"] != 0.0))
                ap_mod = at(ap_mod)[rows, tgt].add(jnp.where(landed_mod, f["ap_delta"], 0.0))
                mp_mod = at(mp_mod)[rows, tgt].add(jnp.where(landed_mod, f["mp_delta"], 0.0))
                rounds = jnp.where(landed_mod, jnp.maximum(f["duration"], 1.0), 0.0)
                mod_rounds = at(mod_rounds)[rows, tgt].set(
                    jnp.where(landed_mod, jnp.maximum(mod_rounds[rows, tgt], rounds),
                              mod_rounds[rows, tgt]))
            ap = at(ap)[:, i].set(
                jnp.where(legal, ap[:, i] - f["ap"], ap[:, i]))
            ap = jnp.maximum(ap, 0.0)
            mp = jnp.maximum(mp, 0.0)
            alive = hp > 0.0

    if has_summons:
        # The swarm strikes at the end of its master's turn, spread evenly over whoever of the
        # opposing side is still standing -- no target head, no position, and therefore no new
        # decision for the policy to be blamed or credited for. The defending swarm intercepts
        # first, by the same rule as above, which is what keeps the two sides' arithmetic one rule
        # rather than two: the same weights play both, and an asymmetry here would be a bug that
        # reads as an advantage.
        foes = alive & (team != acting[:, None])
        n_foes = jnp.sum(foes, axis=1)
        swing = jnp.where(still_running & (n_foes > 0), pool_dmg[rows, acting], 0.0)
        each = jnp.floor(swing / jnp.maximum(n_foes, 1.0))
        guard = 1 - acting
        standing = pool_hp[rows, guard]
        intercept = jnp.minimum(swing, standing)
        dealt = jnp.maximum(each - jnp.floor(intercept / jnp.maximum(n_foes, 1.0)), 0.0)
        hp = hp - jnp.where(foes, dealt[:, None], 0.0)
        pool_hp = at(pool_hp)[rows, guard].add(-intercept)
        pool_dmg = at(pool_dmg)[rows, guard].set(
            pool_dmg[rows, guard] * ((standing - intercept) / jnp.maximum(standing, 1.0)))

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
    # A timed reduction is felt on the victim's own turns, which is where the client's
    # "-#1 AP (2 turns)" actually bites, and the counter runs down across those turns. A state
    # without the columns has zero rounds everywhere, so both branches below leave the refill at
    # `ap_max`, exactly as before.
    acted = (team == acting[:, None])
    mod_rounds = jnp.where(acted & (mod_rounds > 0), mod_rounds - 1, mod_rounds)
    expired = acted & (mod_rounds <= 0)
    ap_mod = jnp.where(expired, 0.0, ap_mod)
    mp_mod = jnp.where(expired, 0.0, mp_mod)
    ap = jnp.where(done[:, None], ap,
                   jnp.where(next_team_acts, jnp.maximum(state["ap_max"] + ap_mod, 0.0), 0.0))
    mp = jnp.where(done[:, None], mp,
                   jnp.where(next_team_acts, jnp.maximum(state["mp_max"] + mp_mod, 0.0), 0.0))

    out = {
        **state,
        "hp": hp,
        "ap": ap,
        "mp": mp,
        "ap_mod": ap_mod,
        "mp_mod": mp_mod,
        "mod_rounds": mod_rounds,
        "dist": dist,
        "alive": alive,
        "acting_team": next_acting,
        "round": jnp.where(done, state["round"], next_round),
        "done": done,
        "winner": jnp.where(done, winner, state["winner"]),
    }
    if has_summons:
        out["pool_hp"] = pool_hp
        out["pool_dmg"] = pool_dmg
    return out


def observe(state: Dict[str, jax.Array], tables: Dict[str, jax.Array],
            cfg: Config,
            acting_team: Optional[jax.Array] = None,
            blind: Tuple[str, ...] = ()) -> Tuple[jax.Array, jax.Array, jax.Array]:
    """Per-actor tokens [B,N,F], a global vector [B,G], and the attention mask.

    Everything is expressed relative to the acting team: the same weights play
    both sides, which is what makes self-play symmetric.

    `blind` zeroes named feature *blocks* while leaving the widths alone: `"mechanics"` clears the
    five kill/push/AP/MP/duration numbers per spell, `"summons"` the three body numbers, `"pool"` the
    four swarm globals. This is the input-ablation knob, and it is not the same thing as zeroing the
    table columns: `heuristic.py` prices an instant kill at 1e6 and `step` spends every one of those
    columns, so clearing the tables changes the world being played, while clearing the observation
    changes only what the network is told about it. Measured 2026-11-06: a corpus with the eight
    columns zeroed produced *different winners* on 64 boards under the scripted line, which is exactly
    why an ablation arm must be built here and not in the data.
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
    # What the cast puts on the board, as the client's own tables say it: an instant kill, the
    # cells pushed or pulled, the AP/MP it takes or gives and for how many rounds. Emitted only
    # when the corpus carries the columns, so a board built before them keeps the 9-number spell
    # token and every checkpoint trained on that width still reads its own observation.
    if all(key in tables for key in MECH_TABLES):
        mech = jnp.stack([
            tables["spell_kill"].astype(jnp.float32),
            tables["spell_push"] / float(cfg.max_dist),
            tables["spell_ap_delta"] / 6.0,
            tables["spell_mp_delta"] / 4.0,
            tables["spell_duration"] / 5.0,
        ], axis=-1)
        spell_feats = jnp.concatenate(
            [spell_feats, mech * (0.0 if "mechanics" in blind else 1.0)], axis=-1)  # [B,N,S,14]
    # What a cast adds to the board as bodies, and how strong each body is -- the same three numbers
    # the environment spends, in the same presence-gated style, so a policy that has to decide
    # whether to summon or to hit is told what summoning buys.
    if all(key in tables for key in SUMMON_TABLES):
        swarm = jnp.stack([
            jnp.clip(tables["spell_summon_bodies"] / 8.0, 0.0, 1.0),
            jnp.log1p(tables["spell_summon_hp"]) / jnp.log(3001.0),
            jnp.log1p(tables["spell_summon_damage"]) / jnp.log(301.0),
        ], axis=-1)
        spell_feats = jnp.concatenate(
            [spell_feats, swarm * (0.0 if "summons" in blind else 1.0)], axis=-1)  # [B,N,S,17]
    b, n, s_dim, f_dim = spell_feats.shape
    obs = jnp.concatenate(
        [base, spell_feats.reshape(b, n, s_dim * f_dim)], axis=-1)

    ours = alive & same
    theirs = alive & ~same
    def _mean(values, mask):
        count = jnp.maximum(jnp.sum(mask, axis=1), 1.0)
        return jnp.sum(jnp.where(mask, values, 0.0), axis=1) / count

    glob = [
        jnp.sum(ours, axis=1) / float(cfg.allies),
        jnp.sum(theirs, axis=1) / float(cfg.enemies),
        state["round"] / float(cfg.max_rounds),
        _mean(hp_ratio, ours),
        _mean(hp_ratio, theirs),
        _mean(state["dist"] / float(cfg.max_dist), ours),
        _mean(state["dist"] / float(cfg.max_dist), theirs),
        _mean(ap_ratio, ours),
        _mean(ap_ratio, theirs),
    ]
    if "pool_hp" in state:
        # Each side's swarm, as a share of the same ceiling `step` caps it with, so the network can
        # read "the boss is behind 1,200 HP of bodies" rather than infer it from an attack that
        # mysteriously did no damage.
        cap = jnp.stack([jnp.sum(state["max_hp"] * (team == t), axis=1)
                         for t in range(2)], axis=1)
        safe = jnp.maximum(cap, 1.0)
        glob += [state["pool_hp"][:, 0] / safe[:, 0], state["pool_hp"][:, 1] / safe[:, 1],
                 state["pool_dmg"][:, 0] / safe[:, 0], state["pool_dmg"][:, 1] / safe[:, 1]]
        if "pool" in blind:
            glob = glob[:-4] + [z * 0.0 for z in glob[-4:]]
    glob = jnp.stack(glob, axis=-1)
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
        # The client lets an actor walk and then cast in the same turn, so a spell is legal
        # when *some* distance the actor's MP can reach lies inside its range -- not only the
        # one it happens to stand at. `step` spends MP before resolving casts and lands the
        # actor on the nearest reachable legal distance, so this interval and that landing
        # rule are the same expression: mask and legality cannot disagree. Measured on the
        # Touch boss corpus, gating on the current distance alone leaves 1,651 of 2,048
        # opening boards with no castable real spell at all (ARCH_REASSESSMENT section N.11.8).
        reach_lo = jnp.maximum(state["dist"][:, :, None] - state["mp"][:, :, None], 1.0)
        reach_hi = jnp.minimum(state["dist"][:, :, None] + state["mp"][:, :, None],
                               float(cfg.max_dist))
        in_range = (reach_lo <= rmax[..., None]) & (rmin[..., None] <= reach_hi)
        target_ok = jnp.where(ally[..., None], same_target, ~same_target)
        range_gate = jnp.where(ally[..., None], jnp.ones_like(in_range), in_range)
        valid_target = alive[:, None, :] & target_ok & range_gate
        can_cast = act & (state["ap"] >= ap_cost) & jnp.any(valid_target, axis=-1)
        spell_mask = at(spell_mask)[:, :, s].set(can_cast)
        target_mask = at(target_mask)[:, :, s, :].set(
            valid_target & can_cast[..., None])

    # Pass: always legal, no target choice. Slot 0 is a harmless placeholder.
    pass_index = cfg.spells
    spell_mask = at(spell_mask)[:, :, pass_index].set(act)
    target_mask = at(target_mask)[:, :, pass_index, 0].set(act)

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
    k_move, k_spell, k_target = jrandom.split(key, 3)
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
    k_move, k_spell, k_target = jrandom.split(key, 3)
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
    move = log_softmax(move_logits, axis=-1)
    spell = log_softmax(spell_logits, axis=-1)
    target = log_softmax(target_logits, axis=-1)
    move_lp = jnp.take_along_axis(
        move, actions["move"][..., None], axis=-1)[..., 0]
    spell_lp = jnp.take_along_axis(
        spell, actions["spell"][..., None], axis=-1)[..., 0]
    target_lp = jnp.take_along_axis(
        target, actions["target"][..., None], axis=-1)[..., 0]
    target_lp = jnp.where(actions["spell"] < cfg.spells, target_lp, 0.0)
    return move_lp + spell_lp + target_lp
