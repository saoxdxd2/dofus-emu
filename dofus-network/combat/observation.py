"""Versioned, build-agnostic combat observation read from the live board.

The policy is not allowed to remember a build. Level, HP, AP/MP, spell facts,
enemy spells, positions and effect ids all come from the board the client just
sent. This module normalizes that board (and the simulator's ``Board``) into one
dict, so training and live inference share a contract instead of two
distributions.

Missing facts are reported by :func:`validate_observation`; they are never
replaced with a level-200 default. Derived damage is computed here, in code,
from the fetched stats and spell effects, and is labelled with its basis.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from combat.battle_solver import BattleSolver, Board, Fighter, LiveSpell
from combat.damage_calculator import CharacterStats

OBSERVATION_VERSION = 1
ELEMENTS = ("neutral", "earth", "fire", "water", "air")
STAT_KEYS = (
    "strength", "intelligence", "chance", "agility", "power", "damage",
    "damage_neutral", "damage_earth", "damage_fire", "damage_water",
    "damage_air", "critical_hit", "damage_critical", "heals",
)
VALUE_CRITICAL_WARNINGS = frozenset({
    "self_stats_missing", "self_effects_missing", "self_dead",
    "enemy_spells_missing", "enemy_effects_missing",
})


def _num(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    return None


def _effect(effect: Any) -> Dict[str, Any]:
    return {
        "effect_id": int(effect.effect_id),
        "kind": effect.kind,
        "element": (effect.element or "").lower() or None,
        "min": int(effect.min_roll),
        "max": int(effect.max_roll),
        "target_id": effect.target_id,
        "duration": effect.duration,
    }


def _spell(spell: LiveSpell) -> Dict[str, Any]:
    effects = [_effect(effect) for effect in spell.effects]
    critical = [_effect(effect) for effect in spell.critical_effects]
    every = list(spell.effects) + list(spell.critical_effects)
    return {
        "id": spell.id,
        "name": spell.name,
        "ap_cost": spell.ap_cost,
        "min_range": spell.min_range,
        "max_range": spell.max_range,
        "cast_in_line": spell.cast_in_line,
        "cast_in_diagonal": spell.cast_in_diagonal,
        "requires_los": spell.requires_los,
        "need_free_cell": spell.need_free_cell,
        "need_taken_cell": spell.need_taken_cell,
        "max_cast_per_turn": spell.max_cast_per_turn,
        "max_cast_per_target": spell.max_cast_per_target,
        "min_cast_interval": spell.min_cast_interval,
        "casts_this_turn": spell.casts_this_turn,
        "castable": spell.castable,
        "critical_hit_probability": spell.critical_hit_probability,
        "is_weapon": spell.is_weapon,
        "has_damage": spell.has_damage,
        "has_heal": spell.has_heal,
        "zone_size": spell.zone_size,
        "zone_shape": spell.zone_shape,
        "mechanics": sorted({effect["kind"] for effect in effects + critical}),
        "unmodeled_effect_ids": sorted({
            effect.effect_id for effect in every if effect.kind == "other"}),
        "effects": effects,
        "critical_effects": critical,
    }


def _stats(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): _num(value) for key, value in sorted(raw.items())}


def _occupied(board: Board, *exclude: int) -> frozenset:
    return frozenset(
        fighter.cell for fighter in board.fighters
        if fighter.alive and fighter.cell is not None and fighter.cell not in exclude)


def _geometry(board: Board, origin: Optional[int], spell: LiveSpell,
              target: Optional[int]) -> Dict[str, Any]:
    """Current-cell distance / line of sight / range, or None when unknown."""
    if origin is None or target is None or not board.cells.flags:
        return {"distance": None, "has_los": None, "in_range": None}
    distance = board.cells.distance(origin, target)
    has_los = True
    if spell.requires_los:
        has_los = board.cells.has_los(
            origin, target, _occupied(board, origin, target))
    cells = board.cells.range_cells(
        origin, spell.min_range, spell.max_range,
        spell.cast_in_line, spell.cast_in_diagonal)
    return {
        "distance": distance,
        "has_los": bool(has_los),
        "in_range": bool(target in cells and (has_los or not spell.requires_los)),
    }


def _estimate(board: Board, spell: LiveSpell, caster: CharacterStats,
              target: Fighter, mode: str) -> Optional[Dict[str, Any]]:
    try:
        result = BattleSolver._estimate(board, spell, caster, target, mode)
    except Exception:
        return None
    if result is None:
        return None
    return {
        "expected": round(float(result.expected_value), 2),
        "min": int(result.total_min),
        "max": int(result.total_max),
        "crit_rate": round(float(result.crit_rate), 4),
    }


def _enemy_caster(fighter: Fighter) -> CharacterStats:
    # The client exposes enemy resistances but not their damage bonuses. This
    # sheet keeps the spell's base rolls; the observation says so explicitly.
    return CharacterStats(
        name=fighter.name or str(fighter.id),
        level=max(1, int(fighter.level)),
        action_points=max(0, int(fighter.ap)),
        movement_points=max(0, int(fighter.mp)),
    )


def _fighter_dict(board: Board, fighter: Optional[Fighter],
                  spells: List[LiveSpell], reference: Optional[int],
                  stats: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if fighter is None:
        return None
    entries = []
    for spell in spells:
        entry = _spell(spell)
        if reference is not None and fighter.id != board.my_id:
            entry["geometry"] = _geometry(board, reference, spell, fighter.cell)
        entries.append(entry)
    max_hp = int(fighter.max_hp)
    stat_values = _stats(stats)
    return {
        "id": fighter.id,
        "name": fighter.name,
        "team": fighter.team,
        "level": fighter.level,
        "hp": fighter.hp,
        "max_hp": max_hp,
        "hp_ratio": (round(float(fighter.hp) / max_hp, 4) if max_hp > 0 else None),
        "ap": fighter.ap,
        "max_ap": fighter.max_ap or None,
        "mp": fighter.mp,
        "max_mp": fighter.max_mp or None,
        "shield": fighter.shield,
        "cell": fighter.cell,
        "alive": bool(fighter.alive),
        "initiative": fighter.initiative or None,
        "is_boss": bool(fighter.is_boss),
        "is_summon": bool(fighter.is_summon),
        "is_companion": bool(fighter.is_companion),
        "summoner_id": fighter.summoner_id,
        "creature_generic_id": fighter.creature_generic_id,
        "states": list(fighter.states),
        "resist_percent": {name: fighter._percent(name) for name in ELEMENTS},
        "resist_fixed": {name: fighter._fixed(name) for name in ELEMENTS},
        "stats": stat_values,
        "stats_known": any(value is not None for value in stat_values.values()),
        "spells_known": bool(spells),
        "spells": entries,
    }


def _derived(board: Board, me: Optional[Fighter], self_spells: List[LiveSpell],
             enemies: List[Fighter]) -> Dict[str, Any]:
    caster = board.character_stats() if me is not None else None
    self_entries: Dict[str, Any] = {}
    for spell in self_spells:
        entry: Dict[str, Any] = {"targets": {}, "heal": None}
        if caster is not None and me is not None:
            if spell.has_heal:
                entry["heal"] = _estimate(board, spell, caster, me, "heal")
            for target in enemies:
                if not target.alive:
                    continue
                target_entry = _geometry(board, me.cell, spell, target.cell)
                if spell.has_damage:
                    damage = _estimate(board, spell, caster, target, "damage")
                    if damage is not None:
                        target_entry["damage"] = damage
                entry["targets"][str(target.id)] = target_entry
        self_entries[str(spell.id)] = entry

    incoming: Dict[str, Any] = {}
    for enemy in enemies:
        if not enemy.alive or me is None:
            continue
        caster_stats = _enemy_caster(enemy)
        entries: Dict[str, Any] = {}
        for spell in enemy.spells:
            spell_entry: Dict[str, Any] = {}
            if spell.has_damage:
                damage = _estimate(board, spell, caster_stats, me, "damage")
                if damage is not None:
                    spell_entry["damage"] = damage
            if spell.has_heal:
                heal = _estimate(board, spell, caster_stats, enemy, "heal")
                if heal is not None:
                    spell_entry["heal"] = heal
            if spell_entry:
                entries[str(spell.id)] = spell_entry
        if entries:
            incoming[str(enemy.id)] = entries
    return {
        "self": self_entries,
        "incoming": incoming,
        "basis": {
            "self": "live_stats_and_spell_effects",
            "enemy": "spell_effects_only_no_damage_bonuses",
        },
    }


def build_observation(board: Board, source: str = "live") -> Dict[str, Any]:
    """Normalize a parsed board. Pure: no network, no model, no defaults for facts."""
    me = board.me
    self_spells = list(board.spells) or (list(me.spells) if me is not None else [])
    enemies = [fighter for fighter in board.fighters
               if fighter.id != board.my_id and fighter.team != "player"]
    allies = [fighter for fighter in board.fighters
              if fighter.id != board.my_id and fighter.team == "player"]
    self_name = me.cell if me is not None else None

    everything = list(self_spells)
    for fighter in board.fighters:
        everything.extend(fighter.spells)
    unmodeled = sorted({effect.effect_id
                        for spell in everything
                        for effect in list(spell.effects) + list(spell.critical_effects)
                        if effect.kind == "other"})
    kinds = sorted({effect.kind
                    for spell in everything
                    for effect in list(spell.effects) + list(spell.critical_effects)})

    return {
        "schema_version": OBSERVATION_VERSION,
        "source": source,
        "units": {"hp": "points", "damage": "points", "distance": "cells"},
        "fight": {
            "in_fight": bool(board.in_fight),
            "in_preparation": bool(board.in_preparation),
            "is_my_turn": bool(board.is_my_turn),
            "my_id": board.my_id,
            "round": board.round,
            "fight_id": board.fight_id,
            "current_fighter_id": board.current_fighter_id,
            "map_id": board.map_id,
        },
        "board": {
            "my_cell": board.my_cell,
            "cell_count": len(board.cells.flags),
            "map_id": board.map_id,
            "occupied_cells": sorted({
                fighter.cell for fighter in board.fighters
                if fighter.alive and fighter.cell is not None}),
        },
        "self": _fighter_dict(
            board, me, self_spells, reference=None, stats=board.my_stats),
        "allies": [
            _fighter_dict(board, fighter, list(fighter.spells),
                          reference=self_name, stats=None)
            for fighter in allies
        ],
        "enemies": [
            _fighter_dict(board, fighter, list(fighter.spells),
                          reference=self_name, stats=None)
            for fighter in enemies
        ],
        "mechanics": {
            "kinds": kinds,
            "unmodeled_effect_ids": unmodeled,
        },
        "derived": _derived(board, me, self_spells, enemies),
        "observed": {
            "last_actions": [dict(action) for action in board.last_actions[-32:]],
        },
    }


def validate_observation(observation: Dict[str, Any]) -> Dict[str, Any]:
    """Return the contract for an observation: structural errors, warnings, gates."""
    errors: List[str] = []
    warnings: List[str] = []

    if observation.get("schema_version") != OBSERVATION_VERSION:
        errors.append("schema_version_mismatch")
    fight = observation.get("fight") or {}
    board_info = observation.get("board") or {}
    me = observation.get("self")
    if not isinstance(me, dict):
        errors.append("no_self")
    else:
        level = me.get("level")
        if level is None or level <= 0:
            errors.append("self_level_missing")
        max_hp = me.get("max_hp")
        if max_hp is None or max_hp <= 0:
            errors.append("self_max_hp_missing")
        if me.get("hp") is None:
            errors.append("self_hp_missing")
        spells = me.get("spells") or []
        if not spells:
            errors.append("self_spells_missing")
        elif not any(spell.get("effects") for spell in spells):
            warnings.append("self_effects_missing")
        stats = me.get("stats") or {}
        if not any(stats.get(key) is not None for key in STAT_KEYS):
            warnings.append("self_stats_missing")
        if me.get("initiative") is None:
            warnings.append("self_initiative_missing")
        if me.get("max_ap") is None:
            warnings.append("self_max_ap_missing")
        if me.get("max_mp") is None:
            warnings.append("self_max_mp_missing")
        for spell in spells:
            if not spell.get("effects"):
                warnings.append(f"self_spell_effects_missing:{spell.get('id')}")
            if spell.get("ap_cost") in (None, 0) and not spell.get("is_weapon"):
                warnings.append(f"self_spell_ap_missing:{spell.get('id')}")
        if me.get("cell") is None:
            errors.append("self_cell_missing")
        if not me.get("alive"):
            warnings.append("self_dead")

    if not fight.get("in_fight"):
        warnings.append("not_in_fight")
    if not fight.get("is_my_turn"):
        warnings.append("not_my_turn")
    if not board_info.get("cell_count"):
        errors.append("cells_missing")
    my_cell = board_info.get("my_cell")
    cell_count = board_info.get("cell_count") or 0
    if my_cell is not None and cell_count and not (0 <= my_cell < cell_count):
        warnings.append("my_cell_out_of_board")

    enemies = observation.get("enemies") or []
    live_enemies = [enemy for enemy in enemies if enemy.get("alive")]
    if not live_enemies and fight.get("in_fight"):
        errors.append("no_live_enemies")
    for enemy in live_enemies:
        ident = enemy.get("id")
        if enemy.get("max_hp") in (None, 0):
            errors.append(f"enemy_max_hp_missing:{ident}")
        if enemy.get("hp") is None:
            errors.append(f"enemy_hp_missing:{ident}")
        if enemy.get("level") in (None, 0):
            warnings.append(f"enemy_level_missing:{ident}")
        spells = enemy.get("spells") or []
        if not spells:
            warnings.append(f"enemy_spells_missing:{ident}")
        elif not any(spell.get("effects") for spell in spells):
            warnings.append(f"enemy_effects_missing:{ident}")
        for spell in spells:
            if not spell.get("effects"):
                warnings.append(f"enemy_spell_effects_missing:{ident}:{spell.get('id')}")
    if live_enemies:
        warnings.append("enemy_stats_not_exposed")

    unmodeled = (observation.get("mechanics") or {}).get("unmodeled_effect_ids") or []
    if unmodeled:
        warnings.append("unmodeled_effects:" + ",".join(str(value) for value in unmodeled))

    warnings = sorted(set(warnings))
    errors = sorted(set(errors))
    critical = any(
        warning in VALUE_CRITICAL_WARNINGS
        or warning.startswith("enemy_spells_missing:")
        or warning.startswith("enemy_effects_missing:")
        for warning in warnings)
    ok = not errors
    return {
        "version": OBSERVATION_VERSION,
        "ok": ok,
        "actionable": bool(
            ok and fight.get("in_fight") and fight.get("is_my_turn")
            and me and me.get("alive") and live_enemies),
        "value_ready": bool(ok and not critical),
        "errors": errors,
        "warnings": warnings,
    }


def observation_from_fight_state(payload: Dict[str, Any],
                                 source: str = "live"
                                 ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    board = Board.from_fight_state(payload)
    observation = build_observation(board, source=source)
    return observation, validate_observation(observation)


def observation_from_board(board: Board,
                           source: str = "sim"
                           ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    observation = build_observation(board, source=source)
    return observation, validate_observation(observation)


def observation_signature(observation: Dict[str, Any]) -> Dict[str, Any]:
    """Key layout only, for the live/sim parity test."""
    me = observation.get("self") or {}
    enemies = observation.get("enemies") or []
    enemy = enemies[0] if enemies else {}
    spells = me.get("spells") or []
    spell = spells[0] if spells else {}
    effects = spell.get("effects") or []
    effect = effects[0] if effects else {}
    enemy_spells = enemy.get("spells") or []
    enemy_spell = enemy_spells[0] if enemy_spells else {}
    enemy_effects = enemy_spell.get("effects") or []
    enemy_effect = enemy_effects[0] if enemy_effects else {}
    return {
        "schema_version": observation.get("schema_version"),
        "top": sorted(observation),
        "fight": sorted((observation.get("fight") or {})),
        "board": sorted((observation.get("board") or {})),
        "self": sorted(me),
        "enemy": sorted(enemy),
        "spell": sorted(spell),
        "effect": sorted(effect),
        "enemy_spell": sorted(enemy_spell),
        "enemy_effect": sorted(enemy_effect),
        "mechanics": sorted((observation.get("mechanics") or {})),
        "derived": sorted((observation.get("derived") or {})),
        "observed": sorted((observation.get("observed") or {})),
    }
