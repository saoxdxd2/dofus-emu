"""The live board and the simulator must produce one observation contract."""

from __future__ import annotations

import unittest

from agent.schema import TOOLS_BY_NAME
from combat.battle_solver import (
    NB_CELLS,
    Board,
    CellMap,
    Fighter,
    LiveEffect,
    LiveSpell,
    _POINTS,
)
from combat.observation import (
    build_observation,
    observation_from_fight_state,
    observation_signature,
    validate_observation,
)
from combat.plan_value import _enemy_rows


def cell(x: int, y: int) -> int:
    return _POINTS[(x, y)]


ME_CELL = cell(20, 20)
FOE_CELL = cell(22, 20)
OPEN_CELLS = tuple([3] * NB_CELLS)
STATS = {
    "strength": 50, "intelligence": 10, "chance": 100, "agility": 20,
    "power": 0, "damage": 4, "damage_neutral": 1, "damage_earth": 2,
    "damage_fire": 0, "damage_water": 6, "damage_air": 0,
    "critical_hit": 5, "damage_critical": 10, "heals": 0,
}
RESISTS = {"neutral": 5, "earth": 4, "fire": 3, "water": 2, "air": 1}
FIXED = {"neutral": 1, "earth": 0, "fire": 0, "water": 0, "air": 2}


def player_spell() -> LiveSpell:
    return LiveSpell(
        id=123, name="Water Jet", ap_cost=3, min_range=1, max_range=5,
        cast_in_line=False, cast_in_diagonal=False, requires_los=True,
        need_free_cell=False, need_taken_cell=False,
        max_cast_per_turn=2, max_cast_per_target=1, min_cast_interval=0,
        casts_this_turn=0, castable=True, critical_hit_probability=5.0,
        effects=(LiveEffect(96, 8, 12, target_id=1),), critical_effects=(),
    )


def enemy_spell() -> LiveSpell:
    return LiveSpell(
        id=900, name="Bite", ap_cost=3, min_range=1, max_range=2,
        cast_in_line=False, cast_in_diagonal=False, requires_los=True,
        need_free_cell=False, need_taken_cell=False,
        max_cast_per_turn=1, max_cast_per_target=1, min_cast_interval=0,
        casts_this_turn=0, castable=True, critical_hit_probability=0.0,
        effects=(LiveEffect(97, 4, 6),), critical_effects=(),
    )


def raw_spell(spell_id: int, name: str, range_: int, effect_id: int,
              low: int, high: int, **extra) -> dict:
    raw = {
        "id": spell_id, "name": name, "ap_cost": 3, "min_range": 1,
        "range": range_, "cast_in_line": False, "cast_in_diagonal": False,
        "requires_los": True, "max_cast_per_turn": 2, "max_cast_per_target": 1,
        "min_cast_interval": 0, "casts_this_turn": 0, "castable": True,
        "critical_hit_probability": 0.0,
        "effects": [{"effect_id": effect_id, "dice_num": low,
                     "dice_side": high, "target_id": 1}],
        "critical_effects": [],
    }
    raw.update(extra)
    return raw


def live_payload(level: int = 12, my_stats=None,
                 with_enemy_spells: bool = True) -> dict:
    return {
        "in_fight": True, "in_preparation": False, "is_my_turn": True,
        "fight_id": 7, "round": 3, "current_fighter_id": 1,
        "my_id": 1, "my_cell": ME_CELL, "map_id": 1,
        "cells": list(OPEN_CELLS),
        "my_stats": dict(STATS) if my_stats is None else my_stats,
        "spells": [raw_spell(123, "Water Jet", 5, 96, 8, 12)],
        "fighters": [
            {
                "id": 1, "name": "Me", "cell": ME_CELL, "team": "player",
                "is_me": True, "alive": True, "hp": 80, "max_hp": 100,
                "ap": 6, "max_ap": 6, "mp": 3, "max_mp": 3, "shield": 0,
                "level": level, "resist_percent": dict(RESISTS),
                "resist_fixed": dict(FIXED), "states": [], "spells": [],
            },
            {
                "id": 2, "name": "Foe", "cell": FOE_CELL, "team": "enemy",
                "is_me": False, "alive": True, "hp": 40, "max_hp": 60,
                "ap": 4, "max_ap": 4, "mp": 2, "max_mp": 2, "shield": 0,
                "level": 5, "resist_percent": dict(RESISTS),
                "resist_fixed": dict(FIXED), "states": [],
                "spells": [raw_spell(900, "Bite", 2, 97, 4, 6)]
                if with_enemy_spells else [],
            },
        ],
    }


def sim_board() -> Board:
    me = Fighter(
        id=1, name="Me", cell=ME_CELL, team="player", hp=80, max_hp=100,
        ap=6, mp=3, shield=0, level=12, alive=True, by_me=True,
        resist_percent=tuple(sorted(RESISTS.items())),
        resist_fixed=tuple(sorted(FIXED.items())),
        max_ap=6, max_mp=3,
    )
    foe = Fighter(
        id=2, name="Foe", cell=FOE_CELL, team="enemy", hp=40, max_hp=60,
        ap=4, mp=2, shield=0, level=5, alive=True, by_me=False,
        resist_percent=tuple(sorted(RESISTS.items())),
        resist_fixed=tuple(sorted(FIXED.items())),
        max_ap=4, max_mp=2, spells=(enemy_spell(),),
    )
    return Board(
        map_id=1, in_fight=True, in_preparation=False, is_my_turn=True,
        my_id=1, my_cell=ME_CELL, my_ap=6, my_mp=3,
        fighters=[me, foe], spells=[player_spell()], my_stats=dict(STATS),
        cells=CellMap(OPEN_CELLS), round=3, fight_id=7,
        current_fighter_id=1,
    )


class ObservationContractTests(unittest.TestCase):
    def test_live_level_and_spells_are_kept_not_assumed(self):
        observation, contract = observation_from_fight_state(live_payload(level=12))
        self.assertEqual(observation["self"]["level"], 12)
        self.assertEqual(observation["self"]["spells"][0]["id"], 123)
        self.assertEqual(observation["enemies"][0]["level"], 5)
        self.assertEqual(observation["enemies"][0]["spells"][0]["id"], 900)
        self.assertTrue(observation["self"]["stats_known"])
        self.assertFalse(observation["enemies"][0]["stats_known"])
        self.assertTrue(contract["ok"])
        self.assertTrue(contract["actionable"])
        self.assertTrue(contract["value_ready"])

    def test_missing_self_stats_blocks_the_ranker_not_the_solver(self):
        observation, contract = observation_from_fight_state(
            live_payload(my_stats={}))
        self.assertFalse(observation["self"]["stats_known"])
        self.assertIn("self_stats_missing", contract["warnings"])
        self.assertTrue(contract["ok"])
        self.assertTrue(contract["actionable"])
        self.assertFalse(contract["value_ready"])

    def test_missing_enemy_spells_are_zeros_not_invented_loadouts(self):
        payload = live_payload(with_enemy_spells=False)
        observation, contract = observation_from_fight_state(payload)
        enemy = observation["enemies"][0]
        self.assertFalse(enemy["spells_known"])
        self.assertEqual(enemy["spells"], [])
        self.assertIn("enemy_spells_missing:2", contract["warnings"])
        self.assertTrue(contract["ok"])
        self.assertFalse(contract["value_ready"])
        rows, known = _enemy_rows(Board.from_fight_state(payload),
                                  Board.from_fight_state(payload).enemies[0], 4)
        self.assertFalse(known)
        self.assertTrue(all(row["damage"] == 0.0 and row["heal"] == 0.0
                            for row in rows))

    def test_utility_effects_are_exposed_and_flagged(self):
        payload = live_payload()
        payload["spells"][0]["effects"].append(
            {"effect_id": 127, "duration": 2, "target_id": 1})
        observation, contract = observation_from_fight_state(payload)
        spell = observation["self"]["spells"][0]
        self.assertIn(127, spell["unmodeled_effect_ids"])
        self.assertIn("unmodeled_effects:127", contract["warnings"])
        self.assertTrue(contract["value_ready"])

    def test_observed_action_stream_is_kept(self):
        payload = live_payload()
        payload["last_actions"] = [{
            "type": "GameActionFightDamageMessage", "source_id": 2,
            "target_id": 1, "spell_id": 900, "value": 5, "effect_id": 97,
        }]
        observation, _contract = observation_from_fight_state(payload)
        self.assertEqual(observation["observed"]["last_actions"][0]["value"], 5)
        self.assertEqual(observation["observed"]["last_actions"][0]["effect_id"], 97)

    def test_live_and_sim_have_the_same_signature_and_derived_values(self):
        live, live_contract = observation_from_fight_state(live_payload())
        simulated = build_observation(sim_board(), source="sim")
        sim_contract = validate_observation(simulated)
        self.assertEqual(observation_signature(live),
                         observation_signature(simulated))
        self.assertEqual(simulated["self"]["level"], live["self"]["level"])
        self.assertEqual(simulated["enemies"][0]["spells"][0]["id"],
                         live["enemies"][0]["spells"][0]["id"])
        self.assertEqual(
            simulated["derived"]["self"]["123"]["targets"]["2"]["in_range"],
            live["derived"]["self"]["123"]["targets"]["2"]["in_range"])
        self.assertEqual(
            simulated["derived"]["incoming"]["2"]["900"]["damage"]["expected"],
            live["derived"]["incoming"]["2"]["900"]["damage"]["expected"])
        self.assertTrue(live_contract["value_ready"])
        self.assertTrue(sim_contract["value_ready"])

    def test_the_live_registry_exposes_the_plan_ranker(self):
        spec = TOOLS_BY_NAME["fight.plan"]
        for name in ("top_k", "rank_with_value", "ranker"):
            self.assertIn(name, spec.params)
        self.assertEqual(spec.params["ranker"]["enum"], ["solver", "value"])


if __name__ == "__main__":
    unittest.main()
