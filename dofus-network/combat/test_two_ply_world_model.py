"""Stage II module 1 gate: typed world-model mechanics and the 2-ply reply.

Four findings this file has to keep true:

* a push into a wall converts the untravelled cells into collision damage,
  and an open push does not;
* at equal one-turn damage, a plan that leaves the enemy out of reach or out
  of line of sight outranks one that stands in its face;
* removing 2 AP from an enemy whose only line costs 4 zeroes its reply --
  a published spellbook is evidence, so the level prior must not fill in;
* the whole path runs without `copy.deepcopy` and 1000 world-model steps
  stay under 20 ms on the shipped interpreter.
"""

from __future__ import annotations

import copy
import time
import unittest
from dataclasses import replace
from unittest import mock

from combat.battle_solver import (BattleSolver, Board, CellMap, LiveEffect,
                                  PlannedAction, TurnPlan)
from combat.test_battle_solver import (OPEN_FLAGS, cell, fighter, live_spell,
                                       open_map)
from combat.world_model import (CombatState, DofusWorldModel, pushback_damage)
from data.game_data_extractor import DamageEffect, SpellInfo

MY_STATS = {"strength": 0, "intelligence": 0, "chance": 0, "agility": 0,
            "power": 0, "damage": 0, "critical_hit": 0, "heals": 0}


def spell_info(sid: int, **overrides) -> SpellInfo:
    fields = dict(id=sid, name_fr="Test", name_en="Test", name_es="Test",
                  breed_id=0, ap_cost=3)
    fields.update(overrides)
    return SpellInfo(**fields)


def base_state(**overrides) -> CombatState:
    fields = dict(char_level=100, char_hp=1000, char_max_hp=1000,
                  action_points=10, movement_points=3, strength=0,
                  intelligence=0, chance=0, agility=0, power=0,
                  flat_damage=0, target_hp=500, target_max_hp=500)
    fields.update(overrides)
    return CombatState(**fields)


def duel_board(me_cell: int, foe_cell: int, foe_spells=(), foe_ap: int = 6,
               foe_mp: int = 3, cells: CellMap = None,
               my_spells=None) -> Board:
    return Board(
        map_id=1, in_fight=True, in_preparation=False, is_my_turn=True,
        my_id=1, my_cell=me_cell, my_ap=10, my_mp=3,
        fighters=[
            fighter(1, "Me", me_cell, "player", hp=1000, max_hp=1000,
                    by_me=True),
            fighter(2, "Foe", foe_cell, "enemy", hp=200, max_hp=200,
                    ap=foe_ap, mp=foe_mp, spells=tuple(foe_spells)),
        ],
        spells=list(my_spells if my_spells is not None else [live_spell()]),
        my_stats=dict(MY_STATS),
        cells=cells if cells is not None else open_map(),
    )


def cast_plan(final_cell: int, foe_cell: int, extra_actions=(),
              damage: float = 50.0) -> TurnPlan:
    actions = [PlannedAction(kind="cast", spell_id=1001, from_cell=final_cell,
                             target_id=2, target_cell=foe_cell, ap_cost=3,
                             damage=damage)]
    actions.extend(extra_actions)
    return TurnPlan(actions=actions, final_cell=final_cell, remaining_ap=7,
                    remaining_mp=3.0, expected_damage=damage,
                    expected_healing=0.0, kills=[], score=damage,
                    final_hp={1: 1000, 2: 150})


class PushbackTests(unittest.TestCase):
    def test_wall_collision_beats_an_open_push(self):
        self.assertEqual(pushback_damage(100, 0, 0, 4), 82)
        self.assertEqual(pushback_damage(100, 0, 0, 0), 0)
        push = spell_info(9001, push_cells=4)
        wall = DofusWorldModel.step(base_state(push_free_cells=0), push)
        open_ = DofusWorldModel.step(base_state(push_free_cells=4), push)
        self.assertGreater(wall.damage_dealt, open_.damage_dealt)
        self.assertEqual(wall.info["push_moved"], 0)
        self.assertEqual(open_.info["push_moved"], 4)
        self.assertEqual(wall.damage_dealt, 82.0)
        self.assertEqual(open_.damage_dealt, 0.0)
        self.assertEqual(wall.next_state.target_hp, 500 - 82)
        self.assertEqual(open_.next_state.target_hp, 500)


class TwoPlyReplyTests(unittest.TestCase):
    def setUp(self):
        self.solver = BattleSolver(beam_width=8, max_actions=4)

    def test_reach_and_los_beat_melee_at_equal_one_turn_damage(self):
        melee = live_spell(id=2001, ap_cost=3, min_range=1, max_range=1,
                           effects=(LiveEffect(100, 10, 10),))
        foe_cell = cell(20, 24)
        board = duel_board(cell(20, 20), foe_cell, foe_spells=(melee,))
        near = cast_plan(cell(20, 23), foe_cell)   # adjacent: eats the reply
        far = cast_plan(cell(20, 14), foe_cell)    # 10 cells: out of reach
        ranked = self.solver.evaluate_two_ply(board, [near, far])
        self.assertIs(ranked[0][0], far)
        self.assertGreater(ranked[0][1], ranked[1][1])
        # The far plan faces no reply at all: full ally share minus enemy share.
        self.assertAlmostEqual(ranked[0][1], 1.0 - 150.0 / 200.0, places=6)

        # Same finding through line of sight: a ranged enemy with a wall on
        # the line cannot answer, the open cell can.
        ranged = live_spell(id=2002, ap_cost=3, min_range=1, max_range=6,
                            requires_los=True,
                            effects=(LiveEffect(100, 10, 10),))
        flags = list(OPEN_FLAGS)
        flags[cell(20, 22)] = 1  # walkable, but not seeing: blocks the line
        walled = duel_board(cell(20, 20), foe_cell, foe_spells=(ranged,),
                            foe_mp=0, cells=CellMap(tuple(flags)))
        exposed = cast_plan(cell(20, 23), foe_cell)
        hidden = cast_plan(cell(20, 18), foe_cell)
        ranked = self.solver.evaluate_two_ply(walled, [exposed, hidden])
        self.assertIs(ranked[0][0], hidden)
        self.assertAlmostEqual(ranked[0][1], 1.0 - 150.0 / 200.0, places=6)
        self.assertLess(ranked[1][1], ranked[0][1])

    def test_two_ap_drain_zeroes_a_four_ap_reply(self):
        heavy = live_spell(id=2001, ap_cost=4, min_range=1, max_range=1,
                           effects=(LiveEffect(100, 10, 10),))
        drain = live_spell(id=1500, ap_cost=2, min_range=1, max_range=4,
                           effects=(LiveEffect(101, 2, 2),))
        me_cell, foe_cell = cell(20, 23), cell(20, 24)
        board = duel_board(me_cell, foe_cell, foe_spells=(heavy,), foe_ap=4,
                           my_spells=[live_spell(), drain])
        plain = cast_plan(me_cell, foe_cell)
        drained = cast_plan(me_cell, foe_cell, extra_actions=[
            PlannedAction(kind="cast", spell_id=1500, from_cell=me_cell,
                          target_id=2, target_cell=foe_cell, ap_cost=2)])
        ranked = self.solver.evaluate_two_ply(board, [plain, drained])
        self.assertIs(ranked[0][0], drained)
        # 4 AP - 2 drained = 2 < the 4-AP line: reply is exactly zero, and the
        # level prior (100 * 1.5) must NOT fill in for the unaffordable book.
        self.assertAlmostEqual(ranked[0][1], 1.0 - 150.0 / 200.0, places=6)
        self.assertLess(ranked[1][1], ranked[0][1])

    def test_reply_prices_the_refill_not_the_mid_turn_zero(self):
        # The duel harness leaves the opponent at 0 AP/MP during our turn
        # (advance_turn). The enemy answers on ITS turn, refilled: pricing the
        # reply from the mid-turn zero collapsed every reply to 0 and made
        # evaluate_two_ply bit-identical to the one-ply margin on 400 boards.
        heavy = live_spell(id=2001, ap_cost=4, min_range=1, max_range=1,
                           effects=(LiveEffect(100, 10, 10),))
        drain = live_spell(id=1500, ap_cost=2, min_range=1, max_range=4,
                           effects=(LiveEffect(101, 2, 2),))
        me_cell, foe_cell = cell(20, 23), cell(20, 24)
        board = duel_board(me_cell, foe_cell, foe_spells=(heavy,), foe_ap=0,
                           foe_mp=0, my_spells=[live_spell(), drain])
        board = replace(board, fighters=[
            board.fighters[0],
            replace(board.fighters[1], max_ap=4, max_mp=3)])
        plain = cast_plan(me_cell, foe_cell)
        drained = cast_plan(me_cell, foe_cell, extra_actions=[
            PlannedAction(kind="cast", spell_id=1500, from_cell=me_cell,
                          target_id=2, target_cell=foe_cell, ap_cost=2)])
        ranked = self.solver.evaluate_two_ply(board, [plain, drained])
        self.assertIs(ranked[0][0], drained)
        self.assertAlmostEqual(ranked[0][1], 0.25, places=6)
        self.assertLess(ranked[1][1], ranked[0][1])

    def test_observed_table_prices_an_unknown_spellbook(self):
        me_cell, foe_cell = cell(20, 23), cell(20, 24)
        board = duel_board(me_cell, foe_cell, foe_spells=(), foe_ap=6)
        board = replace(board, fighters=[
            board.fighters[0], replace(board.fighters[1], level=50)])
        table = {"Foe|50|777": {"name": "Foe", "level": 50, "spell_id": 777,
                                "hits": 4, "max": 40.0, "mean": 30.0}}
        near = cast_plan(me_cell, foe_cell)
        far = cast_plan(cell(20, 10), foe_cell)
        ranked = self.solver.evaluate_two_ply(board, [near, far],
                                              live_table=table)
        self.assertIs(ranked[0][0], far)
        # Worst recorded hit 40, two hits on 6 AP: 80 off the ally bar.
        self.assertAlmostEqual(ranked[1][1], (1000.0 - 80.0) / 1000.0 - 0.75,
                               places=6)
        self.assertAlmostEqual(ranked[0][1], 0.25, places=6)


class CloneAndLatencyTests(unittest.TestCase):
    def test_no_deepcopy_and_1000_steps_under_20ms(self):
        damage = spell_info(9100, effects=[DamageEffect("FIRE", 10, 14,
                                                        effect_id=99)],
                            crit_rate_percent=5.0)
        buff = spell_info(9101, ap_cost=2, power_bonus=50)
        state = base_state(action_points=10000, target_hp=10 ** 9,
                           target_max_hp=10 ** 9)
        with mock.patch.object(copy, "deepcopy",
                               side_effect=AssertionError("deepcopy is banned")):
            DofusWorldModel.step(state, damage)
            DofusWorldModel.step(state, buff)
            started = time.perf_counter()
            curr = state
            for index in range(1000):
                spell = damage if index % 2 else buff
                curr = DofusWorldModel.step(curr, spell).next_state
            elapsed = time.perf_counter() - started

            foe_cell = cell(20, 24)
            melee = live_spell(id=2001, ap_cost=3, min_range=1, max_range=1,
                               effects=(LiveEffect(100, 10, 10),))
            board = duel_board(cell(20, 20), foe_cell, foe_spells=(melee,))
            plans = [cast_plan(cell(20, 20 - i), foe_cell) for i in range(6)]
            ranked = BattleSolver().evaluate_two_ply(board, plans)
            self.assertEqual(len(ranked), 6)
        self.assertLess(elapsed, 0.020,
                        "1000 world-model steps took %.1f ms (gate: 20 ms)"
                        % (elapsed * 1000.0))


if __name__ == "__main__":
    unittest.main()