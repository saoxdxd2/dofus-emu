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
    BoardMark,
    BoardMarks,
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

    def test_own_ap_is_recovered_from_the_stat_and_the_turn_spend(self):
        """The client sends `ap: null` on our own fighter; `my_stats` has the budget.

        Measured 2026-11-10 on the captured corpus: 106 of 160 boards taken on our own
        turn read `ap: 0`. Re-measured 2026-11-12 over the same 333 boards: the same 106,
        and this derivation recovers 152 of them -- 8 boards still read no positive AP
        and every one of those had genuinely spent the turn (`action_points: 6`, two
        casts of a 3-AP spell). `data/combat_grid/manifest.json` records `no_positive_ap:
        8`, `actionable: 7`. The earlier sentence here said 198 of 333 were dropped; that
        was true before the derivation existed and has not been true since.
        """
        payload = self.payload()
        payload["my_stats"]["action_points"] = 6
        payload["my_stats"]["movement_points"] = 3
        payload["fighters"][0]["ap"] = None
        payload["fighters"][0]["mp"] = None
        board = Board.from_fight_state(payload)
        self.assertEqual(board.my_ap, 6, "the budget is the turn's, from my_stats")
        self.assertEqual(board.my_mp, 3)
        self.assertEqual(board.me.ap, 6, "the fighter row must agree with my_ap")

        # Two casts of a 3-AP spell spend the whole turn: the board is genuinely empty
        # and must say so instead of being rescued to full.
        payload["spell_cast_counts"] = {"10": 2}
        board = Board.from_fight_state(payload)
        self.assertEqual(board.my_ap, 0)

        # One cast leaves half the turn, and the fighter's own ap agrees.
        payload["spell_cast_counts"] = {"10": 1}
        board = Board.from_fight_state(payload)
        self.assertEqual(board.my_ap, 3)
        self.assertEqual(board.me.ap, 3)

        # A published AP is never overridden by the stat.
        payload["fighters"][0]["ap"] = 2
        payload["spell_cast_counts"] = {}
        board = Board.from_fight_state(payload)
        self.assertEqual(board.my_ap, 2, "the client's own number wins when it is there")

    def test_an_unchecked_spell_keeps_the_reason_it_was_unchecked(self):
        """`castable: null` is available, and the page also says *why* it could not tell.

        The page writes `castable_error` when `canCastThisSpell` threw (2026-11-12). The
        answer is already handled -- unknown is available, the executor re-checks before
        sending -- but the reason was dropped at parse time, so a broken client API looked
        exactly like a healthy board. Measured on the 333 recorded boards: `castable` is
        never null, so this path is untested live and the fixture is the only witness.
        """
        payload = self.payload()
        payload["spells"][0]["castable"] = None
        payload["spells"][0]["castable_error"] = "Cannot read property 'states' of null"
        board = Board.from_fight_state(payload)
        spell = board.spells[0]
        self.assertTrue(spell.castable, "unknown is available, not refused")
        self.assertEqual(spell.castable_error, "Cannot read property 'states' of null")

        # The round trip carries it: every offline measurement reads the payload back.
        row = board.to_fight_state()["spells"][0]
        self.assertEqual(row["castable_error"], "Cannot read property 'states' of null")

        # And the decision says it, so a live session reads as an unchecked spell rather
        # than as a turn the solver chose to spend.
        plan = BattleSolver().plan_turn(board)
        joined = " | ".join(plan.notes)
        self.assertIn("client could not check these spells", joined)
        self.assertIn("Hit", joined)

        # A spell the client *did* answer carries no error, and no note appears.
        payload["spells"][0]["castable"] = True
        payload["spells"][0].pop("castable_error")
        clean = Board.from_fight_state(payload)
        self.assertIsNone(clean.spells[0].castable_error)
        self.assertNotIn("client could not check these spells",
                         " | ".join(BattleSolver().plan_turn(clean).notes))

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


class BoardSerializationTests(unittest.TestCase):
    """`Board.to_fight_state()` is the inverse of `from_fight_state`, or it is useless.

    The live policy is only ever driven by the page's payload, so an offline measurement of the
    real decision path needs a board to be expressible as one. If a field is dropped or a default
    is relied on -- `requires_los` is True when the key is absent, so a silent omission turns a
    permissive spell into a line-of-sight one -- every number measured through it is about a
    board the client never sends.
    """

    def board(self) -> Board:
        spells = [
            live_spell(),
            live_spell(id=1002, name="Heal", effects=(LiveEffect(99, 4, 6),),
                       castable=False, max_range=6, requires_los=True,
                       max_cast_per_target=None, zone_size=2, zone_shape="circle"),
            live_spell(id=1003, name="Push", is_weapon=True,
                       effects=(LiveEffect(400, 0, 0, target_id=7, duration=3),),
                       min_cast_interval=2, critical_hit_probability=0.25),
        ]
        board = board_with(cell(20, 20), cell(23, 20), spells=spells, my_ap=7, my_mp=2,
                           stats={"strength": 51, "power": 40, "critical_hit": 5})
        board.round = 4
        board.fight_id = 99
        board.current_fighter_id = 1
        return board

    def test_a_board_survives_a_round_trip_through_the_pages_own_payload(self):
        board = self.board()
        again = Board.from_fight_state(board.to_fight_state())
        self.assertEqual(again.spells, board.spells)
        self.assertEqual(again.fighters, board.fighters)
        self.assertEqual(again.cells.flags, board.cells.flags)
        for field in ("my_id", "my_cell", "my_ap", "my_mp", "round", "fight_id",
                      "map_id", "in_fight", "is_my_turn", "in_preparation"):
            self.assertEqual(getattr(again, field), getattr(board, field), field)
        self.assertEqual(again.my_stats, board.my_stats)
        self.assertEqual(again.character_stats().__dict__, board.character_stats().__dict__)

    def test_the_reparse_is_not_vacuous(self):
        # A round trip that compares two objects that were never rebuilt proves nothing, so one
        # field is deliberately broken in the payload and must come back different.
        board = self.board()
        payload = board.to_fight_state()
        payload["spells"][0]["requires_los"] = not payload["spells"][0]["requires_los"]
        again = Board.from_fight_state(payload)
        self.assertNotEqual(again.spells[0], board.spells[0])

    def _castable_after(self, value, present=True):
        payload = self.board().to_fight_state()
        if present:
            payload["spells"][0]["castable"] = value
        else:
            payload["spells"][0].pop("castable", None)
        return Board.from_fight_state(payload).spells[0].castable

    def test_a_spell_the_client_refused_is_not_castable(self):
        self.assertIs(self._castable_after(False), False)

    def test_a_spell_whose_castability_check_threw_is_still_offered(self):
        # The page writes null when `canCastThisSpell` threw. Reading that as a
        # veto removes the spell from every candidate set, so the AI never gets to
        # consider it -- and the executor re-checks before sending anyway.
        self.assertIs(self._castable_after(None), True)

    def test_a_spell_row_without_the_field_is_still_offered(self):
        # All 1,341 spell rows in the recorded corpus predate `castable`.
        self.assertIs(self._castable_after(None, present=False), True)

    def test_a_duel_board_can_ask_the_live_policy(self):
        from combat.duel import make_duel, solver_policy  # noqa: F401
        from combat import duel as du
        import numpy as np
        import random

        data = np.load(str(du.DEFAULT_DATA))
        cells = du.load_map(du.DEFAULT_MAP)
        state = make_duel(data, 0, 1, cells, rng=random.Random(1))
        self.assertIsNotNone(state)
        board = du.board_for(state, state.actor)
        payload = board.to_fight_state()
        reparsed = Board.from_fight_state(payload)
        self.assertEqual([s.id for s in reparsed.spells], [s.id for s in board.spells])
        self.assertEqual(reparsed.my_cell, board.my_cell)
        self.assertTrue(reparsed.in_fight and reparsed.is_my_turn)


class ActionRealizationTests(unittest.TestCase):
    """`plan_for_action()` is the inverse of the page's action, priced by the same arithmetic.

    The live policy emits one action per request and replans from the new cell, so an offline
    measurement of the shipped rule has to build a turn out of those single actions. The beam
    cannot supply them -- it folds the walk into the cast and prunes every (spell, target) pair
    that is not top-scoring -- so the synthesis needs its own arithmetic. It gets the beam's:
    a single-cast turn here is compared against a depth-1 beam search over the same board, and
    every illegal action has to return None rather than invent a legal one.
    """

    def test_a_direct_cast_synthesizes_the_turn_the_beam_also_prices(self):
        board = board_with(cell(20, 20), cell(21, 20))
        solver = BattleSolver(max_actions=1)
        beam = solver.plan_turn(board)
        made = solver.plan_for_action(
            board, {"type": "cast", "spellId": 1001, "targetId": 2,
                    "cell": cell(21, 20)})
        self.assertEqual(beam.actions[0].kind, "cast")
        self.assertIsNotNone(made)
        first = made.actions[0]
        self.assertEqual(first.kind, "cast")
        self.assertEqual(first.from_cell, board.my_cell)
        self.assertIsNone(first.move_to)
        self.assertEqual(made.final_cell, board.my_cell)
        self.assertEqual(made.remaining_ap, beam.remaining_ap)
        self.assertEqual(made.remaining_mp, beam.remaining_mp)
        self.assertEqual(made.final_hp, beam.final_hp)
        self.assertEqual(made.expected_damage, beam.expected_damage)
        self.assertEqual(made.score, beam.score)
        # The concrete numbers, so a shared mistake in both paths cannot pass quietly.
        self.assertEqual(made.remaining_ap, 7)
        self.assertEqual(made.final_hp, {1: 1000, 2: 190})
        self.assertEqual(made.expected_damage, 10.0)

    def test_a_lethal_cast_is_capped_at_the_targets_hp_and_counts_the_kill(self):
        board = board_with(cell(20, 20), cell(21, 20), foe_hp=5, foe_max_hp=200)
        solver = BattleSolver(max_actions=1)
        beam = solver.plan_turn(board)
        made = solver.plan_for_action(
            board, {"type": "cast", "spellId": 1001, "targetId": 2,
                    "cell": cell(21, 20)})
        self.assertEqual(made.final_hp, beam.final_hp)
        self.assertEqual(made.score, beam.score)
        self.assertEqual(made.final_hp[2], 0)
        self.assertEqual(made.expected_damage, 5.0)
        self.assertEqual(made.kills, [2])

    def test_a_walk_spends_mp_and_hurts_nobody(self):
        board = board_with(cell(20, 20), cell(25, 20), my_mp=4)
        made = BattleSolver().plan_for_action(board, {"type": "move",
                                                      "cell": cell(21, 20)})
        self.assertIsNotNone(made)
        self.assertEqual(made.actions[0].kind, "move")
        self.assertEqual(made.final_cell, cell(21, 20))
        self.assertEqual(made.remaining_ap, 10)
        self.assertEqual(made.remaining_mp, 3.0)
        self.assertEqual(made.expected_damage, 0.0)
        self.assertEqual(made.final_hp, {1: 1000, 2: 200})

    def test_a_walk_the_turn_cannot_make_returns_none(self):
        solver = BattleSolver()
        standing = board_with(cell(20, 20), cell(25, 20), my_mp=4)
        self.assertIsNone(solver.plan_for_action(standing,
                                                 {"type": "move", "cell": cell(20, 20)}))
        self.assertIsNone(solver.plan_for_action(standing, {"type": "move", "cell": None}))
        no_mp = board_with(cell(20, 20), cell(25, 20), my_mp=0)
        self.assertIsNone(solver.plan_for_action(no_mp, {"type": "move",
                                                         "cell": cell(21, 20)}))
        # The foe's own cell is not enterable, whatever the page's path says.
        self.assertIsNone(solver.plan_for_action(standing, {"type": "move",
                                                            "cell": cell(25, 20)}))

    def test_a_cast_that_is_not_legal_from_here_returns_none(self):
        solver = BattleSolver()
        here = board_with(cell(20, 20), cell(21, 20))
        out_of_range = board_with(cell(20, 20), cell(25, 20), my_mp=0)
        cases = [
            (out_of_range, {"type": "cast", "spellId": 1001, "targetId": 2,
                            "cell": cell(25, 20)}, "distance 5, range 4"),
            (here, {"type": "cast", "spellId": 9999, "targetId": 2,
                    "cell": cell(21, 20)}, "no such spell"),
            (here, {"type": "cast", "spellId": 1001, "targetId": 1,
                    "cell": cell(20, 20)}, "a damage spell on the caster"),
            (board_with(cell(20, 20), cell(21, 20), my_ap=2),
             {"type": "cast", "spellId": 1001, "targetId": 2, "cell": cell(21, 20)},
             "the spell costs 3 AP"),
            (board_with(cell(20, 20), cell(21, 20), foe_hp=0),
             {"type": "cast", "spellId": 1001, "targetId": 2, "cell": cell(21, 20)},
             "the target is already dead"),
            (here, {"type": "cast", "spellId": None, "targetId": None,
                    "cell": None}, "nothing chosen at all"),
        ]
        for board, action, why in cases:
            with self.subTest(why):
                self.assertIsNone(solver.plan_for_action(board, action))
        # and the same action that fails out of range does work at range 1, so the
        # rejects above are about legality and not about the shape of the dict.
        self.assertIsNotNone(solver.plan_for_action(
            here, {"type": "cast", "spellId": 1001, "targetId": 2, "cell": cell(21, 20)}))

    def test_a_declared_pass_is_a_turn_of_its_own(self):
        board = board_with(cell(20, 20), cell(21, 20))
        made = BattleSolver().plan_for_action(board, {"type": "pass"})
        self.assertEqual(made.actions[0].kind, "pass")
        self.assertEqual(made.remaining_ap, board.my_ap)
        self.assertEqual(made.final_hp, {1: 1000, 2: 200})
        self.assertIsNone(BattleSolver().plan_for_action(board, {"type": "summon"}))


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
    def test_a_turn_with_nothing_in_range_walks_instead_of_passing(self):
        """The common turn: a target too far to cast at, MP to close some of it.

        Measured on the captured corpus (2026-11-10): 114 of 152 turns on our own side
        came back as a single `pass`, because the beam folds a walk into a cast and a
        turn with no reachable cast therefore had no candidate plan at all. The live
        policy hands those turns to the actor, which is the half measured 0.0875 worse.
        """
        board = board_with(cell(20, 20), cell(26, 20), my_mp=3, my_ap=6,
                           spells=[live_spell(max_range=2)])
        plans = BattleSolver().plan_candidates(board, 5)
        kinds = [p.actions[0].kind for p in plans if p.actions]
        self.assertIn("move", kinds, "a turn that can only walk must offer a walk")
        walk = next(p for p in plans if p.actions and p.actions[0].kind == "move")
        action = walk.actions[0]
        self.assertEqual(action.from_cell, cell(20, 20))
        self.assertLess(board.cells.distance(action.move_to, cell(26, 20)),
                        board.cells.distance(cell(20, 20), cell(26, 20)),
                        "the walk must close distance, not wander")
        self.assertGreater(walk.remaining_mp, -1)
        # The pass is still offered: walking is a choice, not a compulsion.
        self.assertIn("pass", kinds)

    def test_a_walk_never_outranks_a_cast_that_lands(self):
        """`APPROACH_VALUE` is below one point of damage for exactly this reason."""
        board = board_with(cell(20, 20), cell(22, 20), my_mp=3, my_ap=6,
                           spells=[live_spell(max_range=3)])
        plans = BattleSolver().plan_candidates(board, 5)
        cast = next(p for p in plans if p.actions and p.actions[0].kind == "cast")
        self.assertGreater(cast.score, 0.0)
        self.assertEqual(plans[0].actions[0].kind, "cast")

    def test_an_adjacent_target_with_no_castable_spell_is_a_real_pass(self):
        """Nothing to do is a verdict, not a failure: 30 of the 51 remaining passes."""
        board = board_with(cell(20, 20), cell(21, 20), my_mp=3, my_ap=6,
                           spells=[live_spell(max_range=1, castable=False)])
        plans = BattleSolver().plan_candidates(board, 5)
        self.assertEqual([p.actions[0].kind for p in plans if p.actions], ["pass"])

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


class SpellGradeTests(unittest.TestCase):
    """A spell level lists the same effect id once per grade; the client casts one.

    Measured 2026-11-10 on the live spell 61: three `effect_id 97` rows (18-20,
    21-23, 24-26) were summed into 66 base damage, the caster's stats folded that to
    117.65, and the live cast dealt 39. Every plan the solver ranked was built on a
    number three times too large, so it believed one cast finished a 120-hp monster
    and left three enemies standing.
    """

    def test_repeated_effect_ids_are_grades_not_extra_hits(self):
        spell = live_spell(effects=(LiveEffect(97, 18, 20), LiveEffect(97, 21, 23),
                                    LiveEffect(97, 24, 26)))
        lines = spell.damage_effects
        self.assertEqual(len(lines), 1)
        self.assertEqual((lines[0].min_roll, lines[0].max_roll), (24, 26))

    def test_the_highest_grade_wins_regardless_of_order(self):
        spell = live_spell(effects=(LiveEffect(97, 24, 26), LiveEffect(97, 18, 20)))
        self.assertEqual(spell.damage_effects[0].min_roll, 24)

    def test_different_effect_ids_stay_separate(self):
        spell = live_spell(effects=(LiveEffect(97, 18, 20), LiveEffect(99, 13, 16)))
        self.assertEqual(len(spell.damage_effects), 2)

    def test_an_unknown_effect_id_is_not_a_damage_line(self):
        # `kind` is derived from the effect id, so id 0 is "other" -- it is dropped
        # before the grade collapse rather than being merged into a real line.
        spell = live_spell(effects=(LiveEffect(0, 10, 10), LiveEffect(97, 4, 4)))
        self.assertEqual([e.effect_id for e in spell.damage_effects], [97])

    def test_the_estimate_drops_by_the_grade_factor(self):
        target = fighter(2, "Foe", cell(21, 20), "enemy")
        board = board_with(cell(20, 20), target.cell, spells=[])
        summed = live_spell(effects=(LiveEffect(97, 18, 20), LiveEffect(97, 21, 23),
                                     LiveEffect(97, 24, 26)))
        result = BattleSolver._estimate(
            board, summed, board.character_stats(), target, "damage")
        self.assertEqual(result.total_min, 24)
        self.assertEqual(result.total_max, 26)


class SelfCheckTests(unittest.TestCase):
    def test_self_check_passes(self):
        self_check()


class BoardMarksAndTrapChainsTests(unittest.TestCase):
    """Tests for BoardMarks layer, summon tracking, and Sram trap displacement chains."""

    def test_board_marks_parsing_and_roundtrip(self):
        payload = {
            "map_id": 1,
            "in_fight": True,
            "in_preparation": False,
            "is_my_turn": True,
            "my_id": 1,
            "my_cell": cell(20, 20),
            "cells": list(OPEN_FLAGS),
            "my_stats": {"strength": 100},
            "fighters": [
                {"id": 1, "name": "Me", "cell": cell(20, 20), "team": "player",
                 "hp": 1000, "max_hp": 1000, "ap": 10, "mp": 3, "alive": True, "is_me": True},
                {"id": 2, "name": "Foe", "cell": cell(22, 20), "team": "enemy",
                 "hp": 500, "max_hp": 500, "ap": 6, "mp": 3, "alive": True},
            ],
            "spells": [],
            "marks": [
                {"id": 101, "mark_type": "trap", "cell": cell(21, 20), "radius": 0,
                 "owner_id": 1, "team": "player", "spell_id": 80, "push_distance": 0,
                 "effects": [{"effect_id": 97, "dice_num": 50, "dice_side": 50}]},
            ],
            "traps": [
                {"id": 102, "cell": cell(23, 20), "radius": 1, "owner_id": 1,
                 "team": "player", "spell_id": 73, "push_distance": 2},
            ],
            "glyphs": [
                {"id": 103, "cell": cell(25, 20), "radius": 2, "owner_id": 2,
                 "team": "enemy", "spell_id": 15},
            ],
            "last_actions": [
                {"action_id": 310, "cell": cell(24, 20), "mark_type": 1, "radius": 0,
                 "caster_id": 1, "team": "player", "spell_id": 65},
            ],
        }
        board = Board.from_fight_state(payload)
        self.assertEqual(len(board.marks.marks), 4)
        self.assertEqual(len(board.marks.traps), 3)
        self.assertEqual(len(board.marks.glyphs), 1)

        traps_at_21 = board.marks.traps_at(cell(21, 20))
        self.assertEqual(len(traps_at_21), 1)
        self.assertEqual(traps_at_21[0].mark_id, 101)

        # Radius 1 trap at 23,20 covers cardinal neighbours like 23,19
        traps_near_23 = board.marks.traps_at(cell(23, 19))
        self.assertEqual(len(traps_near_23), 1)
        self.assertEqual(traps_near_23[0].mark_id, 102)

        # Roundtrip via to_fight_state
        state = board.to_fight_state()
        self.assertIn("marks", state)
        self.assertEqual(len(state["marks"]), 4)
        again = Board.from_fight_state(state)
        self.assertEqual(len(again.marks.marks), 4)

    def test_summon_slots_and_1_per_type_uniqueness(self):
        # Character has summonable_creatures_boost: 1 -> max_summons = 2
        chafer = Fighter(
            id=3, name="Chaferfu", cell=cell(21, 21), team="player",
            hp=100, max_hp=100, ap=4, mp=3, shield=0, level=50,
            alive=True, by_me=False, is_summon=True, summoner_id=1,
            creature_generic_id=282,
        )
        chafer_spell = live_spell(
            id=101, name="Chaferfu", ap_cost=3, min_range=1, max_range=4,
            need_free_cell=True, is_summon=True, summon_type=282,
            effects=(LiveEffect(180, 282, 282),),
        )
        double_spell = live_spell(
            id=102, name="Double", ap_cost=3, min_range=1, max_range=4,
            need_free_cell=True, is_summon=True, summon_type=237,
            effects=(LiveEffect(181, 237, 237),),
        )
        board = board_with(
            cell(20, 20), cell(25, 20),
            spells=[chafer_spell, double_spell],
            stats={"summonable_creatures_boost": 1},
            extra_fighters=[chafer],
        )

        self.assertEqual(board.max_summons, 2)
        self.assertEqual(board.active_my_summons, 1)
        self.assertEqual(board.available_summon_slots, 1)
        self.assertIn(282, board.active_summon_types)

        # When planning a turn, Double (type 237) is legal to summon, but Chaferfu (type 282) is illegal (1-per-type)
        plan = BattleSolver(max_actions=1).plan_turn(board)
        cast_actions = [a for a in plan.actions if a.kind == "cast"]
        self.assertTrue(len(cast_actions) > 0)
        self.assertEqual(cast_actions[0].spell_id, 102)  # Double was summoned!

        # Now test when summon limit is reached (0 slots available):
        board_full = board_with(
            cell(20, 20), cell(25, 20),
            spells=[chafer_spell, double_spell],
            stats={"summonable_creatures_boost": 0},  # max_summons = 1
            extra_fighters=[chafer],  # 1 active summon
        )
        self.assertEqual(board_full.available_summon_slots, 0)
        plan_full = BattleSolver(max_actions=1).plan_turn(board_full)
        # Should not cast any summon because 0 slots are available
        cast_summons = [a for a in plan_full.actions if a.kind == "cast" and a.spell_id in (101, 102)]
        self.assertEqual(len(cast_summons), 0)

    def test_trap_trigger_on_push_displacement(self):
        # Enemy at cell(20, 20), caster at cell(19, 20)
        # Trap at cell(21, 20) dealing 80 earth damage
        trap = BoardMark(
            mark_id=1, mark_type="trap", cell=cell(21, 20), radius=0,
            owner_id=1, team="player", spell_id=80, push_distance=0,
            effects=(LiveEffect(97, 80, 80),),
        )
        # Push spell: Fear (pushes 1 cell)
        fear = live_spell(
            id=67, name="Peur", ap_cost=2, min_range=1, max_range=2,
            cast_in_line=True, effects=(LiveEffect(5, 1, 1),),
        )
        board = board_with(
            cell(19, 20), cell(20, 20),
            spells=[fear], foe_hp=300, foe_max_hp=300,
        )
        board.marks = BoardMarks((trap,))

        plan = BattleSolver(max_actions=1).plan_turn(board)
        self.assertTrue(len(plan.actions) > 0)
        first = plan.actions[0]
        self.assertEqual(first.kind, "cast")
        self.assertEqual(first.spell_id, 67)
        # Moved enemy to trap cell (21, 20)
        self.assertEqual(first.moved_target, cell(21, 20))
        # Expected damage includes the trap damage!
        self.assertGreaterEqual(plan.expected_damage, 80.0)

    def test_sram_trap_chain_reaction(self):
        # Caster at cell(19, 20), enemy at cell(20, 20)
        # Trap A: Repelling Trap at cell(21, 20) -> pushes 2 cells away from trap center (towards 23, 20)
        # Trap B: Lethal Trap at cell(23, 20) -> deals 120 earth damage
        trap_a = BoardMark(
            mark_id=1, mark_type="trap", cell=cell(21, 20), radius=0,
            owner_id=1, team="player", spell_id=73, push_distance=2,
        )
        trap_b = BoardMark(
            mark_id=2, mark_type="trap", cell=cell(23, 20), radius=0,
            owner_id=1, team="player", spell_id=80, push_distance=0,
            effects=(LiveEffect(97, 120, 120),),
        )
        fear = live_spell(
            id=67, name="Peur", ap_cost=2, min_range=1, max_range=2,
            cast_in_line=True, effects=(LiveEffect(5, 1, 1),),
        )
        board = board_with(
            cell(19, 20), cell(20, 20),
            spells=[fear], foe_hp=400, foe_max_hp=400,
        )
        board.marks = BoardMarks((trap_a, trap_b))

        plan = BattleSolver(max_actions=1).plan_turn(board)
        self.assertTrue(len(plan.actions) > 0)
        first = plan.actions[0]
        self.assertEqual(first.kind, "cast")
        self.assertEqual(first.spell_id, 67)
        # Chain displacement: 20,20 -> Fear -> 21,20 (Trap A) -> Push 2 -> 23,20 (Trap B triggers!)
        self.assertEqual(first.moved_target, cell(23, 20))
        self.assertGreaterEqual(plan.expected_damage, 120.0)

    def test_single_turn_place_traps_and_push_chain(self):
        # Caster at cell(18, 20) with 10 AP
        # Enemy at cell(20, 20) with 200 HP
        # Caster has:
        # - Repelling Trap (spell 73, 2 AP, is_trap, push 2)
        # - Lethal Trap (spell 80, 3 AP, is_trap, deals 100 damage)
        # - Fear (spell 67, 2 AP, pushes 1 cell)
        repelling = live_spell(
            id=73, name="Piege Repulsif", ap_cost=2, min_range=1, max_range=6,
            need_free_cell=True, effects=(LiveEffect(5, 2, 2),),
        )
        lethal = live_spell(
            id=80, name="Piege Mortel", ap_cost=3, min_range=1, max_range=6,
            need_free_cell=True, effects=(LiveEffect(97, 100, 100),),
        )
        fear = live_spell(
            id=67, name="Peur", ap_cost=2, min_range=1, max_range=3,
            cast_in_line=True, effects=(LiveEffect(5, 1, 1),),
        )
        board = board_with(
            cell(18, 20), cell(20, 20),
            spells=[repelling, lethal, fear], my_ap=10, foe_hp=200, foe_max_hp=200,
        )

        plan = BattleSolver(max_actions=3).plan_turn(board)
        self.assertGreaterEqual(len(plan.actions), 2)
        # Beam search should find a plan that places trap(s) and pushes the enemy,
        # yielding substantial damage from the trap!
        self.assertGreaterEqual(plan.expected_damage, 100.0)

    def test_trap_chain_bounded_depth_prevents_infinite_ping_pong(self):
        # Two repelling traps facing each other
        trap_1 = BoardMark(
            mark_id=1, mark_type="trap", cell=cell(21, 20), radius=0,
            owner_id=1, team="player", spell_id=73, push_distance=1,
        )
        trap_2 = BoardMark(
            mark_id=2, mark_type="trap", cell=cell(22, 20), radius=0,
            owner_id=1, team="player", spell_id=73, push_distance=-1,
        )
        solver = BattleSolver()
        board = board_with(cell(19, 20), cell(20, 20))
        enemy = board.fighters[1]
        character = board.character_stats()

        # Simulate trap chain starting at 21, 20
        dest, dmg, ap_l, mp_l, remaining_marks, notes = solver._simulate_trap_chain(
            board=board,
            fighter=enemy,
            target_cell=cell(21, 20),
            entry_from_cell=cell(20, 20),
            marks=BoardMarks((trap_1, trap_2)),
            character=character,
            blocked=frozenset(),
            max_depth=8,
        )
        # Must finish without error and consume both traps
        self.assertEqual(len(remaining_marks.marks), 0)

    def test_walk_step_triggers_trap(self):
        # Enemy trap at cell(19, 21)
        trap = BoardMark(
            mark_id=99, mark_type="trap", cell=cell(19, 21), radius=0,
            owner_id=2, team="enemy", spell_id=80, push_distance=0,
            effects=(LiveEffect(97, 50, 50), LiveEffect(101, 2, 2)),
        )
        solver = BattleSolver()
        board = board_with(cell(19, 20), cell(25, 20), spells=[live_spell(min_range=1, max_range=2)])
        board.marks = BoardMarks((trap,))
        me = board.fighters[0]
        character = board.character_stats()

        # When caster walks from (19, 20) to (19, 21), trap triggers
        dest, dmg, ap_l, mp_l, remaining_marks, notes = solver._simulate_trap_chain(
            board=board,
            fighter=me,
            target_cell=cell(19, 21),
            entry_from_cell=cell(19, 20),
            marks=board.marks,
            character=character,
            blocked=frozenset(),
            max_depth=8,
        )
        self.assertEqual(dest, cell(19, 21))
        self.assertGreaterEqual(dmg, 40.0)
        self.assertEqual(ap_l, 2)
        self.assertEqual(len(remaining_marks.marks), 0)


if __name__ == "__main__":
    unittest.main()
