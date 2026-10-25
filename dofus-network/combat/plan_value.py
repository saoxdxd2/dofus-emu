"""Score live cell-level plans with the learned encounter value head.

The encounter policy was trained on a coarse, five-actor abstraction. That is
enough to rank the battle solver's candidate turns: a candidate carries the
resulting cell and HP of every fighter, so we can project it into the same
observation the value head was trained on and ask for P(win).

This is a bridge, not a replacement for the solver. Enemy rows now come from the
enemy spell facts the board actually carries. When the client has not loaded
them yet the rows are zero and the observation contract marks the value ranker
unready; the synthetic ``enemy_loadout`` fallback is deliberately gone. The
solver still owns legality, line of sight, MP costs and damage.
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List, Optional, Sequence, Tuple

import jax
import jax.numpy as jnp
import numpy as np

from combat.battle_solver import BattleSolver, Board, Fighter, TurnPlan
from combat.damage_calculator import CharacterStats
from combat.encounter import model
from combat.encounter.env import Config, initial_state, observe

ELEMENTS = ("neutral", "earth", "fire", "water", "air")
ELEMENT_INDEX = {name: index for index, name in enumerate(ELEMENTS)}
TABLE_KEYS = ("spell_ap", "spell_rmin", "spell_rmax", "spell_damage",
              "spell_heal", "spell_element", "spell_ally")


def _resists(fighter: Fighter) -> List[float]:
    return [float(fighter._percent(name)) for name in ELEMENTS]


def _distance(board: Board, first: int, second: int) -> float:
    return float(max(1, board.cells.distance(first, second)))


def _player_spell_rows(board: Board, character, plan: TurnPlan
                       ) -> List[Dict[str, Any]]:
    """The live spells as the abstract table, with stats folded into damage."""
    dummy_foe = Fighter(
        id=-1, name="dummy", cell=board.me.cell if board.me else board.my_cell,
        team="enemy", hp=10_000_000, max_hp=10_000_000, ap=0, mp=0,
        shield=0, level=1, alive=True, by_me=False,
    )
    dummy_ally = Fighter(
        id=-2, name="dummy-ally", cell=dummy_foe.cell, team="player",
        hp=1, max_hp=10_000_000, ap=0, mp=0, shield=0, level=1,
        alive=True, by_me=False,
    )
    rows: List[Dict[str, Any]] = []
    for spell in board.spells:
        damage = 0.0
        heal = 0.0
        element = 0
        ally = False
        if spell.has_damage:
            result = BattleSolver._estimate(
                board, spell, character, dummy_foe, "damage")
            damage = float(result.expected_value) if result else 0.0
            damage_effects = spell.damage_effects
            if damage_effects:
                element = ELEMENT_INDEX.get(
                    (damage_effects[0].element or "NEUTRAL").lower(), 0)
        if spell.has_heal:
            result = BattleSolver._estimate(
                board, spell, character, dummy_ally, "heal")
            heal = float(result.expected_value) if result else 0.0
            ally = not spell.has_damage
        if damage <= 0 and heal <= 0:
            continue
        rows.append({
            "id": spell.id,
            "ap": float(max(1, spell.ap_cost)),
            "rmin": float(max(0, spell.min_range)),
            "rmax": float(max(0, spell.max_range)),
            "damage": damage,
            "heal": heal,
            "element": element,
            "ally": ally,
        })
    return rows


def _pick_player_spells(rows: List[Dict[str, Any]], plan: TurnPlan,
                        slots: int) -> List[Dict[str, Any]]:
    """Best four by impact, but never drop a spell the chosen plan casts."""
    by_id = {row["id"]: row for row in rows}
    ranked = sorted(rows, key=lambda row: max(row["damage"], row["heal"]),
                    reverse=True)
    picked = ranked[:slots]
    planned = [action.spell_id for action in plan.actions
               if action.kind == "cast"]
    for spell_id in planned:
        if any(row["id"] == spell_id for row in picked):
            continue
        if spell_id in by_id:
            if len(picked) >= slots:
                picked[-1] = by_id[spell_id]
            else:
                picked.append(by_id[spell_id])
    while len(picked) < slots:
        picked.append({"id": 0, "ap": 6.0, "rmin": 0.0, "rmax": 0.0,
                       "damage": 0.0, "heal": 0.0, "element": 0,
                       "ally": False})
    return picked[:slots]


def _empty_spell_row() -> Dict[str, Any]:
    return {"id": 0, "ap": 6.0, "rmin": 0.0, "rmax": 0.0,
            "damage": 0.0, "heal": 0.0, "element": 0, "ally": False}


def _enemy_character(fighter: Fighter) -> CharacterStats:
    return CharacterStats(
        name=fighter.name or str(fighter.id),
        level=max(1, int(fighter.level)),
        action_points=max(0, int(fighter.ap)),
        movement_points=max(0, int(fighter.mp)),
    )


def _enemy_rows(board: Board, fighter: Fighter, slots: int
                ) -> Tuple[List[Dict[str, Any]], bool]:
    """The enemy's own spells, or zeros when the client has not loaded them.

    The second value says whether any real damage/heal row was found. The
    caller must not rank plans with the value head when it is false: a synthetic
    monster loadout would be a guess, not an observation.
    """
    player = board.me
    if player is None or not fighter.spells:
        return [_empty_spell_row() for _ in range(slots)], False
    caster = _enemy_character(fighter)
    rows: List[Dict[str, Any]] = []
    for spell in fighter.spells:
        damage = 0.0
        heal = 0.0
        element = 0
        ally = False
        if spell.has_damage:
            result = BattleSolver._estimate(board, spell, caster, player, "damage")
            damage = float(result.expected_value) if result else 0.0
            damage_effects = spell.damage_effects
            if damage_effects:
                element = ELEMENT_INDEX.get(
                    (damage_effects[0].element or "NEUTRAL").lower(), 0)
        if spell.has_heal:
            result = BattleSolver._estimate(board, spell, caster, fighter, "heal")
            heal = float(result.expected_value) if result else 0.0
            ally = not spell.has_damage
        if damage <= 0 and heal <= 0:
            continue
        rows.append({
            "id": spell.id,
            "ap": float(max(1, spell.ap_cost)),
            "rmin": float(max(0, spell.min_range)),
            "rmax": float(max(0, spell.max_range)),
            "damage": damage,
            "heal": heal,
            "element": element,
            "ally": ally,
        })
    rows.sort(key=lambda row: max(row["damage"], row["heal"]), reverse=True)
    known = bool(rows)
    rows = rows[:slots]
    while len(rows) < slots:
        rows.append(_empty_spell_row())
    return rows, known


def _allocate_encounter(cfg: Config, batch: int
                        ) -> Tuple[Dict[str, np.ndarray], Dict[str, np.ndarray]]:
    n = cfg.n_actors
    tables = {key: np.zeros((batch, n, cfg.spells), dtype=np.float32)
              for key in TABLE_KEYS if key not in ("spell_element", "spell_ally")}
    tables["spell_element"] = np.zeros((batch, n, cfg.spells), dtype=np.int32)
    tables["spell_ally"] = np.zeros((batch, n, cfg.spells), dtype=bool)
    spec = {
        "hp": np.zeros((batch, n), dtype=np.float32),
        "max_hp": np.zeros((batch, n), dtype=np.float32),
        "ap_max": np.zeros((batch, n), dtype=np.float32),
        "mp_max": np.zeros((batch, n), dtype=np.float32),
        "power": np.zeros((batch, n), dtype=np.float32),
        "res": np.zeros((batch, n, 5), dtype=np.float32),
        "dist": np.full((batch, n), 12.0, dtype=np.float32),
        "initiative": np.zeros((batch, n), dtype=np.float32),
        "alive": np.zeros((batch, n), dtype=bool),
        "first_team": np.zeros((batch,), dtype=np.int32),
    }
    return tables, spec


def _fill_encounter(board: Board, plan: TurnPlan, cfg: Config,
                    tables: Dict[str, np.ndarray],
                    spec: Dict[str, np.ndarray], index: int, first_team: int,
                    player_rows: Optional[Sequence[Dict[str, Any]]],
                    player_candidates: Optional[Sequence[Dict[str, Any]]],
                    enemy_rows: Optional[Dict[int, Sequence[Dict[str, Any]]]]
                    ) -> None:
    enemies = board.enemies
    player = board.me
    hp = spec["hp"]
    max_hp = spec["max_hp"]
    ap_max = spec["ap_max"]
    mp_max = spec["mp_max"]
    power = spec["power"]
    res = spec["res"]
    dist = spec["dist"]
    initiative = spec["initiative"]
    alive = spec["alive"]

    def fill_rows(actor: int, rows: Sequence[Dict[str, Any]]) -> None:
        for slot, row in enumerate(rows[:cfg.spells]):
            tables["spell_ap"][index, actor, slot] = row["ap"]
            tables["spell_rmin"][index, actor, slot] = row["rmin"]
            tables["spell_rmax"][index, actor, slot] = row["rmax"]
            tables["spell_damage"][index, actor, slot] = row["damage"]
            tables["spell_heal"][index, actor, slot] = row["heal"]
            tables["spell_element"][index, actor, slot] = row["element"]
            tables["spell_ally"][index, actor, slot] = row["ally"]

    player_cell = int(plan.final_cell)
    if player is not None:
        hp[index, 0] = float(plan.final_hp.get(player.id, player.hp))
        max_hp[index, 0] = float(max(1, player.max_hp))
        ap_max[index, 0] = float(max(1, player.max_ap or board.my_ap))
        mp_max[index, 0] = float(max(1, player.max_mp or board.my_mp))
        res[index, 0] = _resists(player)
        initiative[index, 0] = float(player.level)
        power[index, 0] = 10.0 + float(player.level)
        alive[index, 0] = bool(player.alive and hp[index, 0] > 0)
        if player_rows is None:
            candidates = list(player_candidates) if player_candidates is not None \
                else _player_spell_rows(board, board.character_stats(), plan)
            rows = _pick_player_spells(candidates, plan, cfg.spells)
        else:
            rows = list(player_rows)[:cfg.spells]
            while len(rows) < cfg.spells:
                rows.append(_empty_spell_row())
        fill_rows(0, rows)

    for offset, fighter in enumerate(enemies[:cfg.enemies], start=1):
        hp[index, offset] = float(plan.final_hp.get(fighter.id, fighter.hp))
        max_hp[index, offset] = float(max(1, fighter.max_hp))
        ap_max[index, offset] = float(max(1, fighter.max_ap or fighter.ap))
        mp_max[index, offset] = float(max(1, fighter.max_mp or fighter.mp))
        res[index, offset] = _resists(fighter)
        initiative[index, offset] = float(fighter.level)
        power[index, offset] = (10.0 + (1.5 if fighter.is_boss else 1.0)
                                * float(fighter.level))
        alive[index, offset] = bool(fighter.alive and hp[index, offset] > 0)
        if enemy_rows is not None and fighter.id in enemy_rows:
            rows = list(enemy_rows[fighter.id])
        else:
            rows, _known = _enemy_rows(board, fighter, cfg.spells)
        fill_rows(offset, rows)

    live_enemies = [f for f, keep in zip(enemies[:cfg.enemies], alive[index, 1:])
                    if keep]
    if player is not None and player.alive:
        if live_enemies:
            dist[index, 0] = min(_distance(board, player_cell, f.cell)
                                 for f in live_enemies)
        for offset, fighter in enumerate(enemies[:cfg.enemies], start=1):
            if alive[index, offset]:
                dist[index, offset] = _distance(board, fighter.cell, player_cell)
    spec["first_team"][index] = int(first_team)


def board_encounter(board: Board, plan: TurnPlan, cfg: Config,
                    first_team: int = 1,
                    player_rows: Optional[Sequence[Dict[str, Any]]] = None,
                    player_candidates: Optional[Sequence[Dict[str, Any]]] = None,
                    enemy_rows: Optional[Dict[int, Sequence[Dict[str, Any]]]] = None,
                    as_jax: bool = True
                    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Project the live board after ``plan`` into the encounter tensors.

    ``first_team`` is 0 for the live actor (the player is acting now) and 1 for
    the value head after the player's plan (the monsters act next). ``player_rows``
    lets the live actor pin the exact four spells it bound to the policy slots;
    the value head keeps its own impact-based selection.

    ``as_jax=False`` keeps the tensors as NumPy when the caller immediately hands
    them to a jitted function; JAX converts the leaves during dispatch. Building
    seventeen individual JAX arrays here first was measurable in the live turn.
    """
    tables, spec = _allocate_encounter(cfg, 1)
    _fill_encounter(board, plan, cfg, tables, spec, 0, first_team,
                    player_rows, player_candidates, enemy_rows)
    if not as_jax:
        return tables, spec
    return ({key: jnp.asarray(value) for key, value in tables.items()},
            {key: jnp.asarray(value) for key, value in spec.items()})


def board_encounters(board: Board, plans: Sequence[TurnPlan], cfg: Config,
                     first_team: int = 1,
                     player_candidates: Optional[Sequence[Dict[str, Any]]] = None,
                     enemy_rows: Optional[Dict[int, Sequence[Dict[str, Any]]]] = None,
                     as_jax: bool = True
                     ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Project a whole candidate batch into one set of encounter tensors.

    The previous ranker built one 1-sample JAX tree per plan and concatenated
    seventeen arrays. Allocating the batch once removes those per-plan device
    transfers and the concatenate tree; the value head still sees exactly the
    same per-candidate rows.
    """
    plans = list(plans)
    tables, spec = _allocate_encounter(cfg, len(plans))
    for index, plan in enumerate(plans):
        _fill_encounter(board, plan, cfg, tables, spec, index, first_team,
                        None, player_candidates, enemy_rows)
    if not as_jax:
        return tables, spec
    return ({key: jnp.asarray(value) for key, value in tables.items()},
            {key: jnp.asarray(value) for key, value in spec.items()})


@partial(jax.jit, static_argnums=(3,))
def _batch_value(params: Dict[str, Any],
                 tables: Dict[str, jax.Array],
                 spec: Dict[str, jax.Array],
                 cfg: Config) -> jax.Array:
    """Value for a whole candidate batch in one compiled program."""
    state = initial_state(spec, tables, cfg)
    acting = jnp.ones((spec["hp"].shape[0],), dtype=jnp.int32)
    obs, glob, mask = observe(state, tables, cfg, acting_team=acting)
    return model.forward(params, obs, glob, mask)["value"]


def score_plan(params: Dict[str, Any], board: Board, plan: TurnPlan,
               cfg: Optional[Config] = None) -> float:
    """Player-perspective value-head estimate in [-1, 1]; +1 is a certain win.

    The value head predicts the *acting* team's result. After the player's
    plan the monsters act next, so the raw head is the monsters' value and the
    player's score is its negation. Draws sit at zero.
    """
    cfg = cfg or Config(max_rounds=24)
    player = board.me
    if player is not None and (not player.alive or player.hp <= 0):
        return -1.0
    if not board.enemies:
        return 1.0
    tables, spec = board_encounter(board, plan, cfg, as_jax=False)
    return -float(_batch_value(params, tables, spec, cfg)[0])


def _tactical_bonus(plan: TurnPlan) -> float:
    """Break value-head ties toward damage and kills.

    The value head was trained on coarse encounters and can saturate at +1 on
    a state that is already won. Within that plateau, the solver's damage and
    kill count are the better signal. The weight is tiny on purpose: it never
    overrides a meaningful difference in win probability.
    """
    return (1e-4 * min(float(plan.expected_damage), 1000.0)
            + 1e-3 * len(plan.kills))


def rank_plans(params: Dict[str, Any], board: Board,
               plans: Sequence[TurnPlan],
               cfg: Optional[Config] = None) -> List[Tuple[float, TurnPlan]]:
    """Candidates sorted by player win value, best first.

    Ties are broken by the solver's damage/kill estimate so a certain win
    picks the lethal plan rather than passing. The spell tables that do not
    depend on the candidate are built once per board, and all candidate values
    are produced by one compiled batch instead of one eager forward each.
    """
    cfg = cfg or Config(max_rounds=24)
    plans = list(plans)
    if not plans:
        return []
    player = board.me
    if player is not None and (not player.alive or player.hp <= 0):
        return sorted([(-1.0 + _tactical_bonus(plan), plan) for plan in plans],
                      key=lambda item: item[0], reverse=True)
    if not board.enemies:
        return sorted([(1.0 + _tactical_bonus(plan), plan) for plan in plans],
                      key=lambda item: item[0], reverse=True)

    player_candidates = _player_spell_rows(
        board, board.character_stats(), plans[0])
    enemy_cache = {fighter.id: _enemy_rows(board, fighter, cfg.spells)[0]
                   for fighter in board.enemies[:cfg.enemies]}
    # A varying batch size makes JAX recompile the value program for every
    # distinct candidate count. Pad to a small fixed ladder and score the
    # duplicates too, then keep only the real candidates.
    original = list(plans)
    batch = 8 if len(original) <= 8 else 1 << (len(original) - 1).bit_length()
    plans = original + [original[-1]] * (batch - len(original))
    tables, spec = board_encounters(
        board, plans, cfg, player_candidates=player_candidates,
        enemy_rows=enemy_cache, as_jax=False)
    values = np.asarray(_batch_value(params, tables, spec, cfg))
    scored = [(-float(value) + _tactical_bonus(plan), plan)
              for value, plan in zip(values[:len(original)], original)]
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored
