"""Stage II module 1 gate: typed world-model mechanics and the 2-ply reply.

Four findings this file has to keep true:

* a push into a wall converts the untravelled cells into collision damage,
  and an open push does not;
* at equal one-turn damage, a plan that leaves the enemy out of reach or out
  of line of sight outranks one that stands in its face;
* removing 2 AP from an enemy whose only line costs 4 zeroes its reply --
  a published spellbook is evidence, so the level prior must not fill in;
* a reply picks the ally that costs the most HP share, not always the player,
  and the replies are applied in initiative order: a kill by an earlier enemy
  frees the dead ally's cell and opens the line for a later one;
* the whole path runs without `copy.deepcopy` and 1000 world-model steps
  stay under 20 ms on the shipped interpreter.
"""

from __future__ import annotations

import copy
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from combat.battle_solver import (BattleSolver, Board, CellMap, LiveEffect,
                                  PlannedAction, TurnPlan)
from combat.test_battle_solver import (OPEN_FLAGS, cell, fighter, live_spell,
                                       open_map)
from combat.world_model import (BuffEffect, CombatState, DofusWorldModel,
                                pushback_damage)
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


class ReplyTargetAndOrderTests(unittest.TestCase):
    def test_reply_targets_the_ally_that_costs_the_most_share(self):
        # The enemy cannot touch the player from its cell, but it can hit a
        # teammate standing next to it.  The reply must price that teammate
        # (the old model only ever priced the player and returned zero here).
        melee = live_spell(id=2001, ap_cost=3, min_range=1, max_range=1,
                           effects=(LiveEffect(100, 10, 10),))
        board = duel_board(cell(20, 20), cell(20, 25), foe_spells=(melee,),
                           foe_mp=0)
        board = replace(board, fighters=board.fighters + [
            fighter(3, "Ally", cell(20, 26), "player", hp=200, max_hp=200)])
        plan = cast_plan(cell(20, 20), cell(20, 25))
        value = BattleSolver().evaluate_two_ply(board, [plan])[0][1]
        # 2 hits of 10 off a 200-HP ally; 1200 ally max, 150 enemy HP left.
        self.assertAlmostEqual(value, (1200.0 - 20.0) / 1200.0 - 0.75, places=6)
        self.assertLess(value, 0.25)

    def test_initiative_order_applies_the_kill_before_the_later_reply(self):
        # The ally at (20,23) sits on the only straight line from the ranged
        # enemy at (20,26) to the player at (20,20).  The melee enemy at
        # (21,23) kills the ally.  If that kill lands first, the line opens
        # and the ranged enemy hits the player; the same board with the
        # initiative swapped hides the player and is worth more.
        melee = live_spell(id=2001, ap_cost=3, min_range=1, max_range=1,
                           effects=(LiveEffect(100, 10, 10),))
        ranged = live_spell(id=2002, ap_cost=3, min_range=1, max_range=6,
                            requires_los=True,
                            effects=(LiveEffect(100, 10, 10),))
        board = duel_board(cell(20, 20), cell(21, 23), foe_spells=(melee,),
                           foe_mp=0)
        board = replace(board, fighters=board.fighters + [
            fighter(3, "Ally", cell(20, 23), "player", hp=15, max_hp=100),
            fighter(4, "Sniper", cell(20, 26), "enemy", hp=200, max_hp=200,
                    ap=6, mp=0, initiative=10, spells=(ranged,))])
        board = replace(board, fighters=[
            replace(board.fighters[0]),
            replace(board.fighters[1], initiative=20),
            board.fighters[2], board.fighters[3]])
        plan = cast_plan(cell(20, 20), cell(21, 23))
        solver = BattleSolver()

        killed_first = solver.evaluate_two_ply(board, [plan])[0][1]
        # Melee (initiative 20) kills the 15-HP ally, then the sniper sees the
        # player and lands 2 hits of 10: 980 / 1100 - 350 / 400.
        self.assertAlmostEqual(killed_first, 980.0 / 1100.0 - 350.0 / 400.0,
                               places=6)

        sniper_first = solver.evaluate_two_ply(replace(board, fighters=[
            replace(board.fighters[0]), replace(board.fighters[1], initiative=5),
            board.fighters[2], board.fighters[3]]), [plan])[0][1]
        # The sniper shoots first and the blocked line buys nothing; the melee
        # still kills the ally afterwards: 1000 / 1100 - 350 / 400.
        self.assertAlmostEqual(sniper_first, 1000.0 / 1100.0 - 350.0 / 400.0,
                               places=6)
        self.assertGreater(sniper_first, killed_first)


class SearchDeadlineTests(unittest.TestCase):
    """A wall-clock budget must fire, and must not change an unbounded search.

    The plan's rule is that the budget returns the best validated candidate
    when it expires. One expansion and this turn's first candidate are never
    suppressed by it: measured under three concurrent instances, a 1.5 ms
    budget that could fire before the first expansion silently turned casts
    into passes on 9-10 of 160 recorded turns, because the per-turn fixed
    work already outlasted the budget under load.
    """

    def setUp(self):
        self.solver = BattleSolver(beam_width=8, max_actions=4)
        self.board = duel_board(cell(20, 20), cell(20, 24))

    def test_an_expired_budget_returns_the_first_candidate_not_a_pass(self):
        plan = self.solver.plan_turn(self.board, deadline=time.perf_counter() - 1.0)
        self.assertNotEqual(plan.actions[0].kind, "pass")
        self.assertIn("search stopped at the wall-clock budget", plan.notes)

    def test_expiry_mid_search_returns_the_best_plan_so_far(self):
        # The clock reads zero for the first expansion's work and jumps past
        # the deadline afterwards. By then depth 0 has produced cast
        # candidates, so the plan must carry one instead of degrading to a
        # pass. The budget is read per walk-ring cell and per candidate, not
        # just per node, so the allowance covers one full ring.
        clock = [0.0] * 60

        def tick():
            return clock.pop(0) if clock else 5.0

        with mock.patch("combat.battle_solver.time.perf_counter", side_effect=tick):
            plan = self.solver.plan_turn(self.board, deadline=1.0)
        self.assertNotEqual(plan.actions[0].kind, "pass")
        self.assertGreaterEqual(plan.remaining_ap, 0)
        self.assertIn("search stopped at the wall-clock budget", plan.notes)

    def test_a_generous_deadline_matches_the_unbounded_search(self):
        def signature(plan):
            return tuple((action.kind, action.spell_id, action.target_id,
                          action.move_to) for action in plan.actions)

        unbounded = self.solver.plan_turn(self.board)
        bounded = self.solver.plan_turn(self.board,
                                        deadline=time.perf_counter() + 30.0)
        self.assertEqual(signature(bounded), signature(unbounded))
        self.assertEqual(bounded.score, unbounded.score)
        self.assertNotIn("wall-clock", " ".join(bounded.notes))


class InterpreterGateTests(unittest.TestCase):
    def test_timing_suite_uses_the_shipped_interpreter_and_direct_entrypoint(self):
        from types import SimpleNamespace
        from agent.regression_suites import combat as gate

        expected_file = str(gate.REPO_ROOT / "combat" / "test_two_ply_world_model.py")
        expected_python = str(gate.REPO_ROOT.parent / "runtimes" / "python" / "python.exe")
        completed = SimpleNamespace(returncode=0, stdout="Ran 1 test in 0.001s\nOK\n", stderr="")
        with mock.patch.object(gate.subprocess, "run", return_value=completed) as launch, \
                mock.patch.object(gate.sys, "executable", "development-python"), \
                mock.patch.object(gate, "check"):
            gate.test_combat_suites()
        commands = [call.args[0] for call in launch.call_args_list
                    if "combat.test_two_ply_world_model" in call.args[0]
                    or expected_file in call.args[0]]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], expected_python)
        self.assertIn(expected_file, commands[0])


class PlanProjectionTests(unittest.TestCase):
    def test_projection_is_branch_local_and_preserves_recorded_cells(self):
        board = duel_board(cell(20, 20), cell(20, 24))
        blocker = fighter(3, "Blocker", cell(20, 22), "enemy")
        board = replace(board, fighters=board.fighters + [blocker])
        plan = cast_plan(cell(20, 21), cell(20, 24), extra_actions=[
            PlannedAction(kind="cast", spell_id=1500, target_id=2,
                          moved_target=0)])
        plan.final_hp[3] = 0
        plan.remaining_ap = 4
        plan.remaining_mp = 1.5
        branch = board.after_plan(plan)
        self.assertEqual((branch.my_cell, branch.my_ap, branch.my_mp),
                         (cell(20, 21), 4, 1.5))
        self.assertEqual((branch.me.cell, branch.me.ap, branch.me.mp),
                         (cell(20, 21), 4, 1.5))
        self.assertEqual(branch.fighters[1].cell, 0)
        self.assertEqual(branch.fighters[1].hp, 150)
        self.assertFalse(branch.fighters[2].alive)
        self.assertEqual(branch.fighters[2].hp, 0)
        self.assertIs(branch.cells, board.cells)
        self.assertIs(branch.spells[0], board.spells[0])
        self.assertIsNone(branch.source_payload)
        branch.my_stats["power"] = 50
        branch.spells.clear()
        branch.fighters.pop()
        self.assertEqual(board.my_stats["power"], 0)
        self.assertTrue(board.spells)
        self.assertEqual(len(board.fighters), 3)
        self.assertEqual(board.fighters[1].cell, cell(20, 24))
        self.assertTrue(board.fighters[2].alive)
        self.assertEqual(board.my_cell, cell(20, 20))

    def test_duel_application_uses_the_same_projection(self):
        from combat.duel import Duelist, DuelState, apply_plan, board_for

        board = duel_board(cell(20, 20), cell(20, 24))
        actors = tuple(Duelist(
            id=f.id, row=f.id, class_name="Iop", level=f.level,
            hp=f.hp, max_hp=f.max_hp, ap=f.ap, max_ap=f.ap,
            mp=f.mp, max_mp=f.mp, initiative=100 - f.id, cell=f.cell,
            resist=(), spells=tuple(board.spells)) for f in board.fighters)
        state = DuelState(fighters=actors, cells=board.cells, order=(1, 2))
        plan = cast_plan(cell(20, 21), cell(20, 24), extra_actions=[
            PlannedAction(kind="cast", spell_id=1500, target_id=2,
                          moved_target=cell(22, 24))])
        expected = board_for(state, state.actor).after_plan(plan)
        branch = apply_plan(state.clone(), plan)
        actual = board_for(branch, branch.actor)
        self.assertEqual(actual.fighters, expected.fighters)
        self.assertEqual((actual.my_cell, actual.my_ap, actual.my_mp),
                         (expected.my_cell, expected.my_ap, expected.my_mp))
        self.assertEqual(state.opponent.cell, cell(20, 24))
        self.assertEqual(state.opponent.hp, 200)

    def test_dead_fighter_cannot_supply_reply_cover(self):
        strike = live_spell(id=2001, ap_cost=3, min_range=1, max_range=6,
                            requires_los=True, effects=(LiveEffect(100, 10, 10),))
        board = duel_board(cell(20, 20), cell(20, 24),
                           foe_spells=(strike,), foe_mp=0)
        blocker = fighter(3, "Blocker", cell(20, 22), "enemy")
        plan = cast_plan(board.my_cell, board.fighters[1].cell)
        plan.final_hp[3] = 0
        solver = BattleSolver()
        scores = [solver.evaluate_two_ply(replace(
            board, fighters=board.fighters + [replace(blocker, cell=at)]),
            [plan])[0][1] for at in (cell(20, 22), cell(22, 22))]
        self.assertAlmostEqual(scores[0], scores[1])
        self.assertLess(scores[0], 1.0 - 150.0 / 400.0)

    def test_reply_receives_the_successor_and_recorded_displacement(self):
        board = duel_board(cell(20, 20), cell(20, 24), foe_mp=0)
        plan = cast_plan(cell(20, 21), board.fighters[1].cell, extra_actions=[
            PlannedAction(kind="cast", spell_id=1001, target_id=2,
                          moved_target=cell(22, 24))])
        solver = BattleSolver()
        with mock.patch.object(solver, "_enemy_reply", return_value=0.0) as reply:
            solver.evaluate_two_ply(board, [plan])
        successor, enemy, me, reach, at, *_ = reply.call_args.args
        self.assertEqual(successor.my_cell, plan.final_cell)
        self.assertEqual(me.cell, plan.final_cell)
        self.assertEqual(enemy.cell, cell(22, 24))
        self.assertEqual(reach, {enemy.cell: 0.0})
        occupied = reply.call_args.args[-1]
        self.assertNotIn(board.fighters[1].cell, occupied)
        self.assertNotIn(enemy.cell, occupied)
        self.assertIn(plan.final_cell, occupied)


class CloneAndLatencyTests(unittest.TestCase):
    def test_buff_aging_cache_is_bounded_and_reverts_are_branch_local(self):
        DofusWorldModel._tick_buffs.cache_clear()
        try:
            expiry = (BuffEffect("power", 1, "power", 10),)
            state = base_state(power=10, active_buffs=expiry)
            plain = spell_info(9100)
            buff = spell_info(9101, ap_cost=2, power_bonus=50)
            first = DofusWorldModel.step(state, plain).next_state
            renewed = DofusWorldModel.step(state, buff).next_state
            second = DofusWorldModel.step(state, plain).next_state
            self.assertEqual((first.power, renewed.power, second.power), (0, 50, 0))
            self.assertEqual(state.power, 10)
            self.assertEqual(state.active_buffs, expiry)
            self.assertGreaterEqual(DofusWorldModel._tick_buffs.cache_info().hits, 2)
            aged, expired = DofusWorldModel._tick_buffs(expiry)
            self.assertEqual(aged, ())
            self.assertEqual(expired, (("power", 10),))
            for index in range(300):
                DofusWorldModel._tick_buffs((BuffEffect(str(index), 2, "power", index),))
            info = DofusWorldModel._tick_buffs.cache_info()
            self.assertEqual(info.maxsize, 256)
            self.assertLessEqual(info.currsize, 256)
        finally:
            DofusWorldModel._tick_buffs.cache_clear()

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
        self.measured_step_ms = elapsed * 1000.0
        self.assertLess(elapsed, 0.020,
                        "1000 world-model steps took %.1f ms (gate: 20 ms)"
                        % (elapsed * 1000.0))


if __name__ == "__main__":
    unittest.main()