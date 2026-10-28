"""Tests for the live-board battle solver.

The geometry under test is a port of the 1.74.9 client, so the fixtures here are
deliberately literal: cell flags, effect ids and roll encodings are written the
way the client sends them, not the way the solver would like them.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from combat.battle_solver import (
    NB_CELLS,
    BattleSolver,
    Board,
    CellMap,
    Fighter,
    LiveEffect,
    LiveSpell,
    _POINTS,
    _roll,
    cell_point,
    line_points,
    self_check,
)
from combat.damage_calculator import CharacterStats

MAP_FIXTURE = Path(__file__).resolve().parents[1] / "data" / "maps" / "100139008.json"
OPEN_FLAGS = tuple([3] * NB_CELLS)  # bit 0 walkable | bit 1 line of sight


def open_map() -> CellMap:
    return CellMap(OPEN_FLAGS)


def cell(x: int, y: int) -> int:
    return _POINTS[(x, y)]


def live_spell(**overrides) -> LiveSpell:
    fields = dict(
        id=1001,
        name="Test Strike",
        ap_cost=3,
        min_range=1,
        max_range=4,
        cast_in_line=False,
        cast_in_diagonal=False,
        requires_los=False,
        need_free_cell=False,
        need_taken_cell=False,
        max_cast_per_turn=2,
        max_cast_per_target=2,
        min_cast_interval=0,
        casts_this_turn=0,
        castable=True,
        critical_hit_probability=0.0,
        effects=(LiveEffect(100, 10, 10),),
        critical_effects=(),
        is_weapon=False,
        zone_size=None,
        zone_shape=None,
    )
    fields.update(overrides)
    return LiveSpell(**fields)


def fighter(fid: int, name: str, at: int, team: str, hp: int = 200,
            max_hp: int = 200, **overrides) -> Fighter:
    fields = dict(
        id=fid, name=name, cell=at, team=team, hp=hp, max_hp=max_hp,
        ap=10, mp=3, shield=0, level=100, alive=True,
        by_me=False, resist_percent=(), resist_fixed=(), critical_resistance=0,
    )
    fields.update(overrides)
    return Fighter(**fields)


def board_with(me_cell: int, foe_cell: int, *, spells=None, foe_hp: int = 200,
               foe_max_hp: int = 200, my_ap: int = 10, my_mp: int = 3,
               cells: CellMap = None, stats=None, extra_fighters=(),
               ally_cell: int = None, is_my_turn: bool = True) -> Board:
    my_stats = {"strength": 0, "intelligence": 0, "chance": 0, "agility": 0,
                "power": 0, "damage": 0, "critical_hit": 0, "heals": 0}
    if stats:
        my_stats.update(stats)
    fighters = [
        fighter(1, "Me", me_cell, "player", hp=1000, max_hp=1000,
                ap=my_ap, mp=my_mp, by_me=True),
        fighter(2, "Foe", foe_cell, "enemy", hp=foe_hp, max_hp=foe_max_hp),
    ]
    if ally_cell is not None:
        fighters.append(fighter(3, "Ally", ally_cell, "player",
                                hp=500, max_hp=1000))
    fighters.extend(extra_fighters)
    return Board(
        map_id=1,
        in_fight=True,
        in_preparation=False,
        is_my_turn=is_my_turn,
        my_id=1,
        my_cell=me_cell,
        my_ap=my_ap,
        my_mp=my_mp,
        fighters=fighters,
        spells=list(spells if spells is not None else [live_spell()]),
        my_stats=my_stats,
        cells=cells if cells is not None else open_map(),
    )


class GeometryTests(unittest.TestCase):
    def test_cell_points_are_unique_and_match_the_client_formula(self):
        self.assertEqual(len(_POINTS), NB_CELLS)
        self.assertEqual(len(set(_POINTS.values())), NB_CELLS)
        self.assertEqual(cell_point(0), (19, 0))
        self.assertEqual(cell_point(14), (19, 1))
        self.assertEqual(cell_point(28), (18, 1))
        self.assertEqual(cell_point(27), (32, 14))

    def test_cardinal_neighbours_are_symmetric(self):
        cell_map = open_map()
        centre = cell(20, 20)
        neighbours = cell_map.neighbours(centre)
        self.assertEqual(sorted(neighbours),
                         sorted([cell(21, 20), cell(19, 20), cell(20, 21), cell(20, 19)]))
        for neighbour in neighbours:
            self.assertIn(centre, cell_map.neighbours(neighbour))

    def test_range_rings_have_the_client_shapes(self):
        cell_map = open_map()
        origin = cell(20, 20)

        diamond = cell_map.range_cells(origin, 2, 4)
        self.assertEqual(len(diamond), 36)  # 4*radius per ring: 8 + 12 + 16
        for target in diamond:
            self.assertIn(cell_map.distance(origin, target), (2, 3, 4))

        self.assertIn(origin, cell_map.range_cells(origin, 0, 1))
        self.assertEqual(len(cell_map.range_cells(origin, 1, 1)), 4)

        cross = cell_map.range_cells(origin, 1, 3, cast_in_line=True)
        self.assertEqual(len(cross), 12)
        ox, oy = cell_point(origin)
        for target in cross:
            tx, ty = cell_point(target)
            self.assertTrue(tx == ox or ty == oy)

        diagonals = cell_map.range_cells(origin, 1, 3, cast_in_diagonal=True)
        self.assertEqual(len(diagonals), 12)
        for target in diagonals:
            tx, ty = cell_point(target)
            self.assertEqual(abs(tx - ox), abs(ty - oy))

        both = cell_map.range_cells(origin, 1, 1, cast_in_line=True,
                                    cast_in_diagonal=True)
        self.assertEqual(len(both), 8)

    def test_range_filters_points_off_the_map(self):
        cell_map = open_map()
        corner = cell(19, 0)
        ring = cell_map.range_cells(corner, 1, 1)
        self.assertTrue(ring)
        self.assertTrue(all(cell_map.valid(target) for target in ring))
        self.assertLess(len(ring), 4)

    def test_line_points_are_origin_exclusive_and_target_inclusive(self):
        points = line_points(20, 20, 23, 20)
        self.assertEqual(points, [(21, 20), (22, 20), (23, 20)])
        self.assertEqual(line_points(20, 20, 23, 23),
                         [(21, 21), (22, 22), (23, 23)])


class LineOfSightTests(unittest.TestCase):
    def test_open_map_has_line_of_sight(self):
        cell_map = open_map()
        self.assertTrue(cell_map.has_los(cell(20, 20), cell(23, 20)))

    def test_a_non_seeing_cell_blocks_the_line(self):
        flags = list(OPEN_FLAGS)
        flags[cell(22, 20)] = 1  # walkable, but bit 1 (the sight bit) is clear
        cell_map = CellMap(tuple(flags))
        self.assertFalse(cell_map.has_los(cell(20, 20), cell(23, 20)))

    def test_an_actor_blocks_every_line_cell_except_the_target(self):
        cell_map = open_map()
        self.assertFalse(cell_map.has_los(
            cell(20, 20), cell(23, 20), frozenset({cell(21, 20)})))
        self.assertTrue(cell_map.has_los(
            cell(20, 20), cell(23, 20), frozenset({cell(23, 20)})))

    def test_a_cell_sees_itself(self):
        self.assertTrue(open_map().has_los(cell(20, 20), cell(20, 20)))


class ReachabilityTests(unittest.TestCase):
    def test_one_mp_reaches_only_cardinal_neighbours(self):
        cell_map = open_map()
        start = cell(20, 20)
        reached = cell_map.reachable(start, 1)
        self.assertEqual(set(reached), {
            start, cell(21, 20), cell(19, 20), cell(20, 21), cell(20, 19)})
        self.assertNotIn(cell(21, 21), reached)

    def test_a_diagonal_cell_costs_two_mp_and_is_not_a_diagonal_step(self):
        cell_map = open_map()
        start = cell(20, 20)
        self.assertNotIn(cell(21, 21), cell_map.reachable(start, 1))
        self.assertEqual(cell_map.reachable(start, 2)[cell(21, 21)], 2.0)

    def test_occupied_cells_are_not_enterable(self):
        cell_map = open_map()
        start = cell(20, 20)
        reached = cell_map.reachable(start, 4, frozenset({cell(21, 20)}))
        self.assertNotIn(cell(21, 20), reached)
        self.assertEqual(reached[cell(22, 20)], 4.0)  # detour around the blocker

    def test_walls_block_and_zero_mp_stays_put(self):
        flags = list(OPEN_FLAGS)
        flags[cell(21, 20)] = 0
        cell_map = CellMap(tuple(flags))
        start = cell(20, 20)
        self.assertNotIn(cell(21, 20), cell_map.reachable(start, 3))
        self.assertEqual(cell_map.reachable(start, 0), {start: 0.0})
        self.assertEqual(cell_map.reachable(cell(21, 20), 3), {})


class PayloadParsingTests(unittest.TestCase):
    def payload(self):
        return {
            "map_id": 100139008,
            "in_fight": True,
            "in_preparation": False,
            "is_my_turn": True,
            "my_id": 1,
            "my_cell": cell(20, 20),
            "cells": list(OPEN_FLAGS),
            "my_stats": {
                "strength": 120, "intelligence": 80, "chance": 40, "agility": 60,
                "power": 7, "damage": 4, "damage_earth": 2, "critical_hit": 5,
                "damage_critical": 3, "heals": 11,
            },
            "fighters": [
                {"id": 1, "name": "Me", "cell": cell(20, 20), "team": "player",
                 "hp": 900, "max_hp": 1000, "ap": 10, "mp": 3, "shield": 0,
                 "level": 100, "alive": True, "is_me": True,
                 "resist_percent": {"fire": 10},
                 "resist_fixed": {"fire": 5}, "critical_resistance": 2},
                {"id": 2, "name": "Foe", "cell": cell(21, 20), "team": "enemy",
                 "hp": 200, "max_hp": 250, "ap": 4, "mp": 3, "shield": 12,
                 "level": 50, "alive": True,
                 "resist_percent": {"earth": 20},
                 "resist_fixed": {"earth": 3}, "critical_resistance": 7},
                {"id": 3, "name": "Ally", "cell": cell(19, 20), "team": "player",
                 "hp": 400, "max_hp": 1000, "ap": 6, "mp": 3, "shield": 0,
                 "level": 90, "alive": True},
                {"id": 4, "name": "Unplaced", "cell": None, "team": "enemy",
                 "hp": 10, "max_hp": 10, "alive": True},
            ],
            "spells": [
                {"id": 10, "name": "Hit", "ap_cost": 3, "min_range": 1,
                 "range": 5, "castable": True, "max_cast_per_turn": 2,
                 "max_cast_per_target": 2, "requires_los": True,
                 "critical_hit_probability": 5,
                 "effects": [{"effect_id": 97, "dice_num": 10, "dice_side": 15}],
                 "critical_effects": [{"effect_id": 97, "dice_num": 20,
                                       "dice_side": 25}]},
                {"id": 11, "name": "Fixed", "ap_cost": 2, "min_range": 1,
                 "range": 3, "castable": True,
                 "effects": [{"effect_id": 96, "dice_num": 20, "dice_side": 0}]},
                {"id": 12, "name": "ByValue", "ap_cost": 1, "min_range": 1,
                 "range": 3, "castable": True,
                 "effects": [{"effect_id": 143, "value": 30}]},
            ],
        }

    def test_board_parses_fighters_stats_and_spells(self):
        board = Board.from_fight_state(self.payload())
        self.assertTrue(board.in_fight)
        self.assertTrue(board.is_my_turn)
        self.assertEqual(board.my_id, 1)
        self.assertEqual(board.my_ap, 10)
        self.assertEqual(board.my_mp, 3)
        self.assertEqual(len(board.fighters), 3)  # the cell-less actor is dropped
        self.assertEqual([f.id for f in board.enemies], [2])
        self.assertEqual([f.id for f in board.allies], [1, 3])

        me = board.character_stats()
        self.assertEqual((me.strength, me.intelligence, me.chance, me.agility),
                         (120, 80, 40, 60))
        self.assertEqual((me.power, me.damage, me.damage_earth), (7, 4, 2))
        self.assertEqual((me.critical_hit, me.damage_critical, me.heals),
                         (5, 3, 11))

        foe = board.enemies[0]
        stats = foe.target_stats()
        self.assertEqual((stats.max_hp, stats.current_hp), (250, 200))
        self.assertEqual((stats.res_percent_earth, stats.res_fixed_earth), (20, 3))
        self.assertEqual((stats.armor_shield, stats.res_fixed_critical), (12, 7))
        self.assertFalse(stats.is_player)
        self.assertTrue(board.me.target_stats().is_player)

        self.assertEqual(len(board.spells), 3)
        hit = board.spells[0]
        self.assertEqual((hit.min_range, hit.max_range), (1, 5))
        self.assertTrue(hit.requires_los)
        self.assertEqual(hit.critical_hit_probability, 5.0)
        self.assertEqual(hit.effects, (LiveEffect(97, 10, 15),))
        self.assertEqual(hit.critical_effects, (LiveEffect(97, 20, 25),))
        self.assertEqual(board.spells[1].effects[0], LiveEffect(96, 20, 20))
        self.assertEqual(board.spells[2].effects[0], LiveEffect(143, 30, 30))
        self.assertEqual(hit.effects[0].kind, "damage")
        self.assertEqual(hit.effects[0].element, "EARTH")

    def test_roll_encoding_matches_the_client(self):
        self.assertEqual(_roll({"dice_num": 10, "dice_side": 15}), (10, 15))
        self.assertEqual(_roll({"dice_num": 20, "dice_side": 0}), (20, 20))
        self.assertEqual(_roll({"dice_num": 0, "dice_side": 0, "value": 7}), (7, 7))
        self.assertIsNone(_roll({"dice_num": 0, "value": 0}))
        self.assertIsNone(_roll({}))

    def test_effect_ids_map_to_elements_kinds_and_heals(self):
        expected = {
            91: ("steal", "WATER"), 92: ("steal", "EARTH"),
            93: ("steal", "AIR"), 94: ("steal", "FIRE"),
            95: ("steal", "NEUTRAL"), 96: ("damage", "WATER"),
            97: ("damage", "EARTH"), 98: ("damage", "AIR"),
            99: ("damage", "FIRE"), 100: ("damage", "NEUTRAL"),
            108: ("heal", "HEAL"), 143: ("heal", "HEAL"),
        }
        for effect_id, (kind, element) in expected.items():
            effect = LiveEffect(effect_id, 1, 1)
            self.assertEqual((effect.kind, effect.element), (kind, element))


class DamageMathTests(unittest.TestCase):
    def estimate(self, spell: LiveSpell, target: Fighter, stats=None):
        board = board_with(cell(20, 20), target.cell, spells=[], stats=stats)
        return BattleSolver._estimate(
            board, spell, board.character_stats(), target, "damage")

    def test_flat_damage_with_no_resistance(self):
        target = fighter(2, "Foe", cell(21, 20), "enemy")
        result = self.estimate(live_spell(effects=(LiveEffect(100, 10, 10),)), target)
        self.assertEqual(result.total_min, 10)
        self.assertEqual(result.expected_value, 10.0)

    def test_percentage_resistance_is_applied_after_flat_bonuses(self):
        target = fighter(2, "Foe", cell(21, 20), "enemy",
                         resist_percent=(("neutral", 20),))
        result = self.estimate(
            live_spell(effects=(LiveEffect(100, 10, 10),)),
            target, stats={"damage": 5})
        # (10 + 5) * (1 - 0.20) = 12
        self.assertEqual(result.expected_value, 12.0)

    def test_fixed_resistance_comes_before_percentage_resistance(self):
        target = fighter(2, "Foe", cell(21, 20), "enemy",
                         resist_fixed=(("neutral", 5),),
                         resist_percent=(("neutral", 20),))
        result = self.estimate(live_spell(effects=(LiveEffect(100, 20, 20),)), target)
        # max(0, 20 - 5) * 0.80 = 12
        self.assertEqual(result.expected_value, 12.0)

    def test_shield_absorbs_after_resistances(self):
        target = fighter(2, "Foe", cell(21, 20), "enemy",
                         resist_fixed=(("neutral", 5),),
                         resist_percent=(("neutral", 20),), shield=5)
        result = self.estimate(live_spell(effects=(LiveEffect(100, 20, 20),)), target)
        self.assertEqual(result.expected_value, 7.0)

    def test_player_percentage_resistance_is_capped_at_35(self):
        target = fighter(2, "Foe", cell(21, 20), "player",
                         resist_percent=(("neutral", 80),))
        result = self.estimate(live_spell(effects=(LiveEffect(100, 10, 10),)), target)
        # (10 * 65) // 100 = 6 per roll
        self.assertEqual(result.expected_value, 6.0)

    def test_stat_scaling_uses_the_element_primary(self):
        target = fighter(2, "Foe", cell(21, 20), "enemy")
        result = self.estimate(
            live_spell(effects=(LiveEffect(97, 10, 10),)),
            target, stats={"strength": 100})
        self.assertEqual(result.expected_value, 20.0)

    def test_critical_probability_and_critical_rolls(self):
        target = fighter(2, "Foe", cell(21, 20), "enemy")
        spell = live_spell(
            effects=(LiveEffect(100, 10, 10),),
            critical_effects=(LiveEffect(100, 20, 20),),
            critical_hit_probability=50.0)
        result = self.estimate(spell, target)
        self.assertEqual(result.crit_rate, 0.5)
        self.assertEqual(result.expected_value, 15.0)

    def test_guaranteed_critical_uses_only_the_critical_roll(self):
        target = fighter(2, "Foe", cell(21, 20), "enemy")
        spell = live_spell(
            effects=(LiveEffect(100, 10, 10),),
            critical_effects=(LiveEffect(100, 20, 20),),
            critical_hit_probability=100.0)
        result = self.estimate(spell, target)
        self.assertEqual(result.expected_value, 20.0)

    def test_healing_scales_with_intelligence_and_heals(self):
        target = fighter(3, "Ally", cell(21, 20), "player", hp=100, max_hp=1000)
        board = board_with(cell(20, 20), cell(25, 20), spells=[],
                           stats={"intelligence": 100, "heals": 10})
        spell = live_spell(
            effects=(LiveEffect(108, 20, 20),), max_cast_per_turn=1)
        result = BattleSolver._estimate(
            board, spell, board.character_stats(), target, "heal")
        self.assertEqual(result.expected_value, 50.0)

    def test_a_steal_effect_heals_the_caster_for_the_damage_dealt(self):
        spell = live_spell(effects=(LiveEffect(91, 10, 10),),
                           max_cast_per_turn=1)
        board = board_with(cell(20, 20), cell(21, 20), spells=[spell],
                           stats={"chance": 100})
        board.fighters[0] = fighter(1, "Me", cell(20, 20), "player",
                                    hp=50, max_hp=1000, ap=10, mp=3, by_me=True)
        plan = BattleSolver().plan_turn(board)
        self.assertEqual(plan.expected_damage, 20.0)
        self.assertEqual(plan.expected_healing, 20.0)


class LegalityTests(unittest.TestCase):
    def test_out_of_range_means_pass(self):
        board = board_with(cell(20, 20), cell(25, 20), my_mp=0)
        plan = BattleSolver().plan_turn(board)
        self.assertEqual(plan.actions[0].kind, "pass")
        self.assertEqual(plan.expected_damage, 0.0)

    def test_los_blocks_the_cast_unless_the_spell_ignores_los(self):
        flags = list(OPEN_FLAGS)
        flags[cell(22, 20)] = 1
        blocked_map = CellMap(tuple(flags))
        los_spell = live_spell(requires_los=True, max_range=5)
        plan = BattleSolver().plan_turn(
            board_with(cell(20, 20), cell(23, 20), spells=[los_spell],
                       cells=blocked_map, my_mp=0))
        self.assertEqual(plan.actions[0].kind, "pass")

        free_spell = live_spell(requires_los=False, max_range=5)
        plan = BattleSolver().plan_turn(
            board_with(cell(20, 20), cell(23, 20), spells=[free_spell],
                       cells=blocked_map, my_mp=0))
        self.assertEqual(plan.actions[0].kind, "cast")

    def test_a_teammate_on_the_line_blocks_the_cast(self):
        board = board_with(cell(20, 20), cell(23, 20),
                           spells=[live_spell(requires_los=True, max_range=5)],
                           ally_cell=cell(22, 20), my_mp=0)
        plan = BattleSolver().plan_turn(board)
        self.assertEqual(plan.actions[0].kind, "pass")

    def test_not_castable_and_need_free_cell_spells_are_skipped(self):
        for spell in (live_spell(castable=False),
                      live_spell(need_free_cell=True)):
            plan = BattleSolver().plan_turn(
                board_with(cell(20, 20), cell(21, 20), spells=[spell]))
            self.assertEqual(plan.actions[0].kind, "pass")

    def test_max_cast_per_turn_limits_the_number_of_casts(self):
        spell = live_spell(max_cast_per_turn=1, max_cast_per_target=None)
        board = board_with(cell(20, 20), cell(21, 20), spells=[spell],
                           foe_hp=1000, foe_max_hp=1000, my_ap=12)
        plan = BattleSolver().plan_turn(board)
        self.assertEqual(len([a for a in plan.actions if a.kind == "cast"]), 1)

    def test_casts_already_spent_this_turn_count_against_the_limit(self):
        spell = live_spell(max_cast_per_turn=2, casts_this_turn=2)
        plan = BattleSolver().plan_turn(
            board_with(cell(20, 20), cell(21, 20), spells=[spell]))
        self.assertEqual(plan.actions[0].kind, "pass")

    def test_max_cast_per_target_limits_repeats_on_one_target(self):
        spell = live_spell(max_cast_per_turn=2, max_cast_per_target=1)
        board = board_with(cell(20, 20), cell(21, 20), spells=[spell],
                           foe_hp=1000, foe_max_hp=1000, my_ap=12)
        plan = BattleSolver().plan_turn(board)
        self.assertEqual(len([a for a in plan.actions if a.kind == "cast"]), 1)

    def test_min_cast_interval_prevents_a_second_cast_in_the_same_turn(self):
        spell = live_spell(max_cast_per_turn=2, max_cast_per_target=None,
                           min_cast_interval=1)
        board = board_with(cell(20, 20), cell(21, 20), spells=[spell],
                           foe_hp=1000, foe_max_hp=1000, my_ap=12)
        plan = BattleSolver().plan_turn(board)
        self.assertEqual(len([a for a in plan.actions if a.kind == "cast"]), 1)

    def test_need_taken_cell_can_target_a_fighter(self):
        spell = live_spell(need_taken_cell=True)
        plan = BattleSolver().plan_turn(
            board_with(cell(20, 20), cell(21, 20), spells=[spell]))
        self.assertEqual(plan.actions[0].kind, "cast")

    def test_no_ap_or_not_my_turn_means_pass(self):
        plan = BattleSolver().plan_turn(
            board_with(cell(20, 20), cell(21, 20), my_ap=0))
        self.assertEqual(plan.actions[0].kind, "pass")
        plan = BattleSolver().plan_turn(
            board_with(cell(20, 20), cell(21, 20), is_my_turn=False))
        self.assertEqual(plan.actions[0].kind, "pass")

    def test_utility_only_spells_are_reported_not_guessed(self):
        utility = live_spell(
            effects=(LiveEffect(116, 5, 5),),  # AP removal, not modelled
            name="AP Sap")
        plan = BattleSolver().plan_turn(
            board_with(cell(20, 20), cell(21, 20), spells=[utility]))
        self.assertEqual(plan.actions[0].kind, "pass")
        self.assertTrue(any("AP Sap" in note for note in plan.notes))


class SearchTests(unittest.TestCase):
    def test_a_walk_into_range_then_cast_is_planned(self):
        board = board_with(cell(20, 20), cell(24, 20), my_mp=1, my_ap=3,
                           spells=[live_spell(max_range=3)])
        plan = BattleSolver().plan_turn(board)
        self.assertEqual(len(plan.actions), 1)
        action = plan.actions[0]
        self.assertEqual(action.kind, "cast")
        self.assertEqual(action.from_cell, cell(21, 20))
        self.assertEqual(action.mp_cost, 1.0)
        self.assertEqual(plan.final_cell, cell(21, 20))
        self.assertEqual(plan.expected_damage, 10.0)

    def test_two_casts_are_chained_and_lethal_damage_is_capped_at_hp(self):
        board = board_with(cell(20, 20), cell(21, 20), foe_hp=15,
                           foe_max_hp=100, my_ap=6)
        plan = BattleSolver().plan_turn(board)
        casts = [a for a in plan.actions if a.kind == "cast"]
        self.assertEqual(len(casts), 2)
        self.assertEqual(plan.expected_damage, 15.0)
        self.assertEqual(plan.kills, [2])
        self.assertTrue(casts[-1].kills)
        self.assertEqual(plan.remaining_ap, 0)

    def test_kill_bonus_rewards_finishing_a_target(self):
        board = board_with(cell(20, 20), cell(21, 20), foe_hp=10,
                           foe_max_hp=100, my_ap=3)
        plan = BattleSolver().plan_turn(board)
        # 10 damage + 0.35 * 100 kill bonus
        self.assertAlmostEqual(plan.score, 45.0, places=6)

    def test_spending_all_ap_beats_leaving_ap_unused(self):
        spell = live_spell(max_cast_per_turn=3, max_cast_per_target=None)
        board = board_with(cell(20, 20), cell(21, 20), foe_hp=1000,
                           foe_max_hp=1000, my_ap=9, spells=[spell])
        plan = BattleSolver().plan_turn(board)
        self.assertEqual(plan.remaining_ap, 0)
        self.assertEqual(len([a for a in plan.actions if a.kind == "cast"]), 3)

    def test_heal_plans_only_when_an_ally_is_hurt(self):
        heal = live_spell(
            effects=(LiveEffect(108, 20, 20),), critical_effects=(),
            max_cast_per_turn=1, name="Heal")
        board = board_with(cell(20, 20), cell(25, 20), spells=[heal],
                           ally_cell=cell(21, 20), stats={"intelligence": 0})
        # Ally at 500/1000 is worth healing.
        plan = BattleSolver().plan_turn(board)
        self.assertEqual(plan.actions[0].kind, "cast")
        self.assertEqual(plan.expected_healing, 20.0)

        full = board_with(cell(20, 20), cell(25, 20), spells=[heal],
                          ally_cell=cell(21, 20))
        full.fighters[2] = fighter(3, "Ally", cell(21, 20), "player",
                                   hp=1000, max_hp=1000)
        plan = BattleSolver().plan_turn(full)
        self.assertEqual(plan.actions[0].kind, "pass")

    def test_node_budget_still_returns_a_plan(self):
        board = board_with(cell(20, 20), cell(25, 20), foe_hp=1000,
                           foe_max_hp=1000, my_ap=20, my_mp=5)
        plan = BattleSolver(beam_width=4, max_nodes=1).plan_turn(board)
        self.assertIsNotNone(plan)
        self.assertTrue(plan.actions)

    def test_plan_candidates_are_distinct_and_include_pass(self):
        spell = live_spell(max_range=4, max_cast_per_turn=2,
                           max_cast_per_target=2)
        board = board_with(cell(20, 20), cell(24, 20), foe_hp=1000,
                           foe_max_hp=1000, my_ap=6, my_mp=2,
                           spells=[spell])
        solver = BattleSolver(beam_width=8, max_actions=2)
        plans = solver.plan_candidates(board, 6)
        self.assertGreaterEqual(len(plans), 2)
        signatures = [
            tuple((a.kind, a.move_to, a.spell_id, a.target_id)
                  for a in plan.actions)
            for plan in plans
        ]
        self.assertEqual(len(signatures), len(set(signatures)))
        self.assertTrue(any(plan.actions[0].kind == "pass" for plan in plans))
        self.assertIn("2", plans[0].as_dict()["final_hp"])


    def test_stratified_buckets_keep_legal_plans_within_the_beam(self):
        spell = live_spell(max_range=4, max_cast_per_turn=2,
                           max_cast_per_target=2)
        board = board_with(cell(20, 20), cell(24, 20), foe_hp=1000,
                           foe_max_hp=1000, my_ap=6, my_mp=2,
                           spells=[spell])
        solver = BattleSolver(beam_width=8, max_actions=2)
        plan = solver.plan_turn(board)
        allowed = {"damage", "displacement", "setup", "boss", "pass"}
        self.assertIn(plan.bucket, allowed)
        self.assertTrue(solver.last_candidates)
        self.assertTrue(all(p.bucket in allowed for p in solver.last_candidates))
        self.assertEqual(sum(solver.last_buckets.values()),
                         len(solver.last_candidates))
        self.assertLessEqual(len(solver.last_candidates), solver.beam_width + 1)
        self.assertEqual(solver.last_candidates[0].score,
                         max(p.score for p in solver.last_candidates))
        self.assertIn("bucket", plan.as_dict())


class RealMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = json.loads(MAP_FIXTURE.read_text(encoding="utf-8"))
        cls.flags = tuple(int(c.get("l", 0)) for c in raw["cells"])
        cls.cell_map = CellMap(cls.flags)
        cls.walkable = [c for c in range(NB_CELLS) if cls.cell_map.walkable(c)]

    def test_the_fixture_is_a_full_560_cell_map(self):
        self.assertEqual(len(self.flags), NB_CELLS)
        self.assertGreater(len(self.walkable), 100)

    def test_a_cell_always_sees_itself(self):
        for start in self.walkable[:50]:
            self.assertTrue(self.cell_map.has_los(start, start))

    def test_one_mp_reaches_exactly_the_walkable_cardinal_neighbours(self):
        start = self.walkable[0]
        expected = {start}
        for neighbour in self.cell_map.neighbours(start):
            if self.cell_map.walkable(neighbour):
                expected.add(neighbour)
        self.assertEqual(set(self.cell_map.reachable(start, 1)), expected)

    def test_reachable_cells_are_walkable_and_cost_at_least_manhattan(self):
        start = self.walkable[0]
        for target, cost in self.cell_map.reachable(start, 3).items():
            self.assertTrue(self.cell_map.walkable(target))
            self.assertGreaterEqual(cost, self.cell_map.distance(start, target))
            self.assertLessEqual(cost, 3)

    def test_line_of_sight_to_a_neighbour_follows_the_sight_bit(self):
        for start in self.walkable[:20]:
            for neighbour in self.cell_map.neighbours(start):
                self.assertEqual(self.cell_map.has_los(start, neighbour),
                                 self.cell_map.sees(neighbour))

    def test_range_rings_on_the_real_map_use_manhattan_radii(self):
        origin = self.walkable[0]
        ring = self.cell_map.range_cells(origin, 2, 3)
        self.assertTrue(ring)
        for target in ring:
            self.assertIn(self.cell_map.distance(origin, target), (2, 3))


class SelfCheckTests(unittest.TestCase):
    def test_self_check_passes(self):
        self_check()


if __name__ == "__main__":
    unittest.main()
