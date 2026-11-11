"""Slot binding and realization: the weapon is spell id 0, not an empty slot.

Measured live (trajectories/live_policy_decisions.jsonl): 29 decisions had the
policy choose the character's weapon - a real spell with id 0 - and the engine
answered "empty_spell_slot", discarding the cast. The padded slots carried the
weapon's name too, so the decision log showed four identical hammer rows. These
fixtures pin the two halves of that identity: a real id-0 spell must bind and
cast, and padding must be identifiable without looking at the id.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from combat.battle_solver import NB_CELLS, Board, _POINTS
from combat.encounter.env import Config
from combat.live_policy import LivePolicy

MY_CELL = _POINTS[(10, 10)]
FOE_CELL = _POINTS[(11, 10)]
OPEN_FLAGS = [3] * NB_CELLS


def damage_effect(effect_id: int, low: int, high: int) -> dict:
    return {"effect_id": effect_id, "dice_num": low, "dice_side": high,
            "target_id": 0, "duration": 0}


def spell(spell_id: int, name: str, ap: int, rmin: int, rmax: int,
          effects: list, **extra) -> dict:
    raw = {
        "id": spell_id, "name": name, "ap_cost": ap,
        "min_range": rmin, "range": rmax,
        "requires_los": False, "castable": True,
        "casts_this_turn": 0, "effects": effects, "critical_effects": [],
    }
    raw.update(extra)
    return raw


def board_payload(extra_spells: list = None) -> dict:
    spells = [
        spell(0, "Hammer", 4, 1, 1, [damage_effect(100, 5, 6)],
              is_weapon=True, max_cast_per_turn=1),
        spell(10, "Hit", 3, 1, 3, [damage_effect(97, 10, 12)]),
        spell(11, "Boozer", 1, 0, 0,
              [{"effect_id": 950, "value": 1, "target_id": 0, "duration": -1}]),
    ]
    spells.extend(extra_spells or [])
    return {
        "in_fight": True, "in_preparation": False, "is_my_turn": True,
        "my_id": 1, "my_cell": MY_CELL, "my_ap": 6, "my_mp": 3,
        "round": 1, "fight_id": 1, "map_id": 1,
        "cells": OPEN_FLAGS, "spells": spells,
        "my_stats": {"strength": 10, "agility": 20, "power": 0, "damage": 0},
        "fighters": [
            {"id": 1, "name": "me", "cell": MY_CELL, "team": "player",
             "hp": 100, "max_hp": 100, "ap": 6, "max_ap": 6, "mp": 3,
             "max_mp": 3, "level": 10, "alive": True, "is_me": True,
             "spells": []},
            {"id": -1, "name": "foe", "cell": FOE_CELL, "team": "enemy",
             "hp": 50, "max_hp": 50, "ap": 4, "max_ap": 4, "mp": 3,
             "max_mp": 3, "level": 5, "alive": True, "spells": []},
        ],
    }


def policy_stub() -> LivePolicy:
    """Binding/realization logic only; no checkpoint and no compiled graph."""
    policy = LivePolicy.__new__(LivePolicy)
    policy.cfg = Config()
    return policy


class SolverOwnsTheWalkTests(unittest.TestCase):
    """A turn with nothing in range used to leave the solver's hands entirely.

    `_solver_turn` returns None when the candidate list holds no action plan, and the
    caller then falls back to the actor -- measured 0.0875 worse than the solver
    (brain_service §L.31). On the captured corpus that was 114 of 152 turns, so the
    common case was the fallback case. The solver now offers a walk, and this pins that
    the walk reaches the page as a move instead of a pass.
    """

    def solver_only(self) -> LivePolicy:
        from combat.battle_solver import BattleSolver

        policy = policy_stub()
        policy.solver = BattleSolver(beam_width=8, max_actions=4)
        policy.plan_k = 8
        # The rankers need a checkpoint; this test is about what the solver offers,
        # so both are made to abstain and the first plan is taken.
        policy._value_choice = lambda board, plans: None
        policy._grid_choice = lambda board, plans: None
        return policy

    def test_a_turn_with_nothing_in_range_walks_instead_of_passing(self):
        payload = board_payload()
        payload["fighters"][1]["cell"] = _POINTS[(14, 10)]
        board = Board.from_fight_state(payload)
        action, _fallback, reason = self.solver_only()._solver_turn(board)
        self.assertEqual(action["type"], "move", action)
        self.assertLess(board.cells.distance(action["cell"], _POINTS[(14, 10)]),
                        board.cells.distance(MY_CELL, _POINTS[(14, 10)]))
        self.assertTrue(action.get("path"), "the page needs the path, not just the cell")
        self.assertEqual(reason, "solver_move")

    def test_a_turn_that_can_cast_still_casts(self):
        board = Board.from_fight_state(board_payload())
        action, _fallback, _reason = self.solver_only()._solver_turn(board)
        self.assertEqual(action["type"], "cast", action)


class SlotBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.board = Board.from_fight_state(board_payload())
        self.policy = policy_stub()
        self.slots, _rows = self.policy._bind_slots(self.board)
        self.targets = self.policy._bind_targets(self.board)

    def test_weapon_with_id_zero_binds_as_a_real_slot(self):
        weapon = [s for s in self.slots if s["spell_id"] == 0 and not s["empty"]]
        self.assertEqual(len(weapon), 1)
        self.assertEqual(weapon[0]["name"], "Hammer")
        self.assertGreater(weapon[0]["damage"], 0)

    def test_padding_is_flagged_and_unnamed(self):
        padding = [s for s in self.slots if s["empty"]]
        self.assertTrue(padding)
        for slot in padding:
            self.assertEqual(slot["name"], "")
            self.assertEqual(slot["damage"], 0)
            self.assertEqual(slot["heal"], 0)

    def test_zero_damage_support_spell_does_not_take_a_damage_slot(self):
        ids = [s["spell_id"] for s in self.slots if not s["empty"]]
        self.assertNotIn(11, ids)

    def test_uncastable_spells_do_not_bind(self):
        payload = board_payload([spell(12, "Locked", 3, 1, 2,
                                       [damage_effect(98, 9, 9)],
                                       castable=False)])
        board = Board.from_fight_state(payload)
        slots, _rows = policy_stub()._bind_slots(board)
        self.assertNotIn(12, [s["spell_id"] for s in slots])


class SlotRealizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.board = Board.from_fight_state(board_payload())
        self.policy = policy_stub()
        self.slots, _rows = self.policy._bind_slots(self.board)
        self.targets = self.policy._bind_targets(self.board)
        self.weapon_slot = next(i for i, s in enumerate(self.slots)
                                if s["spell_id"] == 0 and not s["empty"])
        self.empty_slot = next(i for i, s in enumerate(self.slots) if s["empty"])

    def test_a_planned_cast_reaches_the_page_with_its_spell_and_target(self):
        # `_action_for_plan` is the last translation before the client clicks: the page needs
        # `spellId` to select and `targetId` to aim. Writing this test was the point of a
        # mutation sweep -- dropping `targetId` here was MISSED by every check in the tree,
        # because the realization tests that do assert a targetId go through `_direct_cast`.
        from combat.battle_solver import PlannedAction, TurnPlan

        plan = TurnPlan(
            actions=[PlannedAction("cast", spell_id=10, spell_name="Hit",
                                   from_cell=MY_CELL, target_id=-1, target_cell=FOE_CELL,
                                   ap_cost=3, damage=12.0)],
            final_cell=MY_CELL, remaining_ap=3, remaining_mp=3.0,
            expected_damage=12.0, expected_healing=0.0, kills=[], score=12.0)
        action = self.policy._action_for_plan(self.board, plan)
        self.assertEqual(action["type"], "cast")
        self.assertEqual(action["spellId"], 10)
        self.assertEqual(action["targetId"], -1)
        self.assertEqual(action["cell"], FOE_CELL)

    def test_the_value_head_branch_reports_whether_it_was_reached(self):
        # §L.21's ranker ablation stands or falls on this counter. `_value_choice` runs only when
        # the actor's chosen cast cannot land from any cell the MP reaches, so either the trained
        # critic picks the realization or it never gets asked -- and the run can only say which if
        # the branch counts itself, before the ranking rather than after it.
        from combat.battle_solver import PlannedAction, TurnPlan

        def plan(cell):
            return TurnPlan(
                actions=[PlannedAction("cast", spell_id=10, from_cell=cell, target_id=-1,
                                       target_cell=FOE_CELL, ap_cost=3, damage=12.0)],
                final_cell=cell, remaining_ap=3, remaining_mp=3.0, expected_damage=12.0,
                expected_healing=0.0, kills=[], score=12.0)

        self.assertEqual(self.policy.value_choice_stats, {"asked": 0, "fired": 0})
        # not value-ready: the guard answers, and the critic was never consulted
        self.assertIsNone(self.policy._value_choice(self.board,
                                                    [plan(MY_CELL), plan(FOE_CELL)]))
        self.assertEqual(self.policy.value_choice_stats, {"asked": 0, "fired": 0})
        self.policy._value_ready = True
        # value-ready with one plan: there is nothing to choose between
        self.assertIsNone(self.policy._value_choice(self.board, [plan(MY_CELL)]))
        self.assertEqual(self.policy.value_choice_stats["asked"], 0)
        # two plans and a stub with no params: reached and counted, but it does not report a pick
        # it did not make.
        self.assertIsNone(self.policy._value_choice(self.board,
                                                    [plan(MY_CELL), plan(FOE_CELL)]))
        self.assertEqual(self.policy.value_choice_stats, {"asked": 1, "fired": 0})
        # A ranker that answers is different from a ranker that was never reached.
        # `fired` counts picks, not consultations: the case that separates the two is a
        # ranked list whose top entry carries no plan, which the empty-list and raise
        # paths both skip -- and a counter that adds one per ask reads identical there.
        import combat.plan_value as plan_value

        real_rank = plan_value.rank_plans
        # The stub carries no params on purpose above, so the raise path is what the
        # counter sees there. A pick can only be counted if the ranker is actually
        # reached, which needs the attributes its call reads.
        had_params = hasattr(self.policy, "params")
        saved_params = getattr(self.policy, "params", None)
        self.policy.params = {}
        try:
            plan_value.rank_plans = lambda *a, **k: [(1.0, None)]
            self.assertIsNone(self.policy._value_choice(
                self.board, [plan(MY_CELL), plan(FOE_CELL)]))
            self.assertEqual(self.policy.value_choice_stats, {"asked": 2, "fired": 0})
            plan_value.rank_plans = lambda *a, **k: []
            self.assertIsNone(self.policy._value_choice(
                self.board, [plan(MY_CELL), plan(FOE_CELL)]))
            self.assertEqual(self.policy.value_choice_stats, {"asked": 3, "fired": 0})
            chosen = plan(FOE_CELL)
            plan_value.rank_plans = lambda *a, **k: [(1.0, chosen)]
            self.assertIs(self.policy._value_choice(
                self.board, [plan(MY_CELL), plan(FOE_CELL)]), chosen)
            self.assertEqual(self.policy.value_choice_stats, {"asked": 4, "fired": 1})
        finally:
            plan_value.rank_plans = real_rank
            if had_params:
                self.policy.params = saved_params
            else:
                del self.policy.params

    def test_the_policy_can_cast_the_weapon(self):
        action, fallback, reason = self.policy._realize(
            self.board, self.weapon_slot, 1, 0, self.slots, self.targets)
        self.assertEqual(reason, "policy_direct")
        self.assertFalse(fallback)
        self.assertEqual(action["type"], "cast")
        self.assertEqual(action["spellId"], 0)
        self.assertEqual(action["targetId"], -1)

    def test_a_padded_slot_is_still_an_empty_slot(self):
        action, fallback, reason = self.policy._realize(
            self.board, self.empty_slot, 1, 0, self.slots, self.targets)
        self.assertIsNone(action)
        self.assertTrue(fallback)
        self.assertEqual(reason, "empty_spell_slot")


class MoveDirectionTests(unittest.TestCase):
    """The trained actor moves every turn; the live action has to carry that.

    The encounter env applies the move head (0 toward, 1 hold, 2 away) after
    casting every turn. Live realization dropped it: once AP ran out the policy
    passed, the page's local heuristic walked forward, and the move dimension
    was never executed. These pin the standalone move and its direction.
    """

    def build(self, my_point=(10, 10)):
        payload = board_payload()
        payload["my_cell"] = _POINTS[my_point]
        payload["fighters"][0]["cell"] = _POINTS[my_point]
        board = Board.from_fight_state(payload)
        policy = policy_stub()
        slots, _rows = policy._bind_slots(board)
        return board, policy, slots, policy._bind_targets(board)

    def test_a_pass_with_a_direction_walks_away(self):
        board, policy, slots, targets = self.build()
        action, fallback, reason = policy._realize(
            board, policy.cfg.spells, 0, 2, slots, targets)
        self.assertEqual(reason, "policy_retreat")
        self.assertFalse(fallback)
        self.assertEqual(action["type"], "move")
        self.assertGreater(board.cells.distance(action["cell"], FOE_CELL),
                           board.cells.distance(MY_CELL, FOE_CELL))
        self.assertEqual(len(action["path"]), len(set(action["path"])))

    def test_a_pass_with_a_direction_closes_in(self):
        board, policy, slots, targets = self.build((10, 14))
        start = board.cells.distance(board.my_cell, FOE_CELL)
        action, fallback, reason = policy._realize(
            board, policy.cfg.spells, 0, 0, slots, targets)
        self.assertEqual(reason, "policy_approach")
        self.assertFalse(fallback)
        self.assertEqual(action["type"], "move")
        self.assertLess(board.cells.distance(action["cell"], FOE_CELL), start)

    def test_hold_still_passes(self):
        board, policy, slots, targets = self.build()
        action, fallback, reason = policy._realize(
            board, policy.cfg.spells, 0, 1, slots, targets)
        self.assertEqual(action, {"type": "pass"})
        self.assertEqual(reason, "policy_pass")

    def test_zero_mp_does_not_invent_a_move(self):
        payload = board_payload()
        payload["my_mp"] = 0
        payload["fighters"][0]["mp"] = 0
        board = Board.from_fight_state(payload)
        policy = policy_stub()
        slots, _rows = policy._bind_slots(board)
        targets = policy._bind_targets(board)
        action, _fallback, reason = policy._realize(
            board, policy.cfg.spells, 0, 2, slots, targets)
        self.assertEqual(action, {"type": "pass"})
        self.assertEqual(reason, "policy_pass")

    def test_a_retreating_policy_does_not_walk_into_the_target(self):
        # Out of range of a melee-only kit with "back off" chosen: the old path
        # walked to the cheapest cast cell, i.e. straight at the enemy.
        payload = board_payload()
        payload["spells"] = [spell(10, "Punch", 3, 1, 1,
                                   [damage_effect(97, 10, 12)])]
        payload["my_cell"] = _POINTS[(10, 14)]
        payload["fighters"][0]["cell"] = _POINTS[(10, 14)]
        board = Board.from_fight_state(payload)
        policy = policy_stub()
        slots, _rows = policy._bind_slots(board)
        targets = policy._bind_targets(board)
        action, _fallback, reason = policy._realize(
            board, 0, 1, 2, slots, targets)
        self.assertEqual(reason, "policy_retreat")
        self.assertEqual(action["type"], "move")
        self.assertGreater(
            board.cells.distance(action["cell"], FOE_CELL),
            board.cells.distance(board.my_cell, FOE_CELL))


class PlanCellChoiceTests(unittest.TestCase):
    """Which castable cell a plan ends on, when the cast itself is already chosen.

    `_realize` collects one plan per reachable cell that executes the actor's spell and
    target, then asks `_move_score` to pick one. The two branches for the actor's own
    direction codes (0 toward, 2 away -- the codes `_walk_action` reads) were written
    the other way round, so a retreating turn was ranked by how far it *closed* and the
    character ended next to the enemy it had just decided to leave. Pinned here on the
    scoring function itself, because the boards that reach it need a real cast to be
    legal and a stub cannot exercise that.
    """

    def board(self):
        return Board.from_fight_state(board_payload())

    def plan(self, cell, exposure=0.0):
        return SimpleNamespace(final_cell=cell, exposure=exposure)

    def test_toward_ends_on_the_nearer_cell(self):
        board, policy = self.board(), policy_stub()
        near = self.plan(_POINTS[(10, 10)])
        far = self.plan(_POINTS[(10, 13)])
        self.assertLess(board.cells.distance(near.final_cell, FOE_CELL),
                        board.cells.distance(far.final_cell, FOE_CELL))
        self.assertGreater(policy._move_score(board, near, {"cell": FOE_CELL,
                                                            "fighter_id": -1}, 0),
                           policy._move_score(board, far, {"cell": FOE_CELL,
                                                           "fighter_id": -1}, 0))

    def test_away_ends_on_the_farther_cell(self):
        board, policy = self.board(), policy_stub()
        near = self.plan(_POINTS[(10, 10)])
        far = self.plan(_POINTS[(10, 13)])
        self.assertGreater(policy._move_score(board, far, {"cell": FOE_CELL,
                                                           "fighter_id": -1}, 2),
                           policy._move_score(board, near, {"cell": FOE_CELL,
                                                            "fighter_id": -1}, 2))

    def test_hold_keeps_the_distance_and_lets_exposure_decide(self):
        board, policy = self.board(), policy_stub()
        first = self.plan(_POINTS[(10, 10)], exposure=40.0)
        second = self.plan(_POINTS[(12, 10)], exposure=5.0)
        self.assertEqual(board.cells.distance(first.final_cell, FOE_CELL),
                         board.cells.distance(second.final_cell, FOE_CELL))
        self.assertGreater(policy._move_score(board, second, {"cell": FOE_CELL,
                                                              "fighter_id": -1}, 1),
                           policy._move_score(board, first, {"cell": FOE_CELL,
                                                             "fighter_id": -1}, 1))

    def test_exposure_cannot_overrule_the_direction_it_was_asked_for(self):
        # The term is a tie-break: even the threat model's capped swing has to lose
        # to one cell of the direction the actor chose.
        from combat.threat import MAX_PRICED_SWING

        board, policy = self.board(), policy_stub()
        near = self.plan(_POINTS[(10, 10)], exposure=MAX_PRICED_SWING)
        far = self.plan(_POINTS[(10, 13)], exposure=0.0)
        self.assertGreater(policy._move_score(board, near, {"cell": FOE_CELL,
                                                            "fighter_id": -1}, 0),
                           policy._move_score(board, far, {"cell": FOE_CELL,
                                                           "fighter_id": -1}, 0))


class ValueChoiceTests(unittest.TestCase):
    """The trained critic ranks realizations only when its inputs are complete.

    Several plans can realize the actor's chosen spell/target; the old tiebreak
    was cell distance. The value head (P(win)) is the better judge, but only
    when the enemy's spells are loaded -- otherwise its inputs are padding. Not
    ready or a ranker failure must leave the old choice in place.
    """

    def setUp(self):
        self.board = Board.from_fight_state(board_payload())
        self.policy = policy_stub()
        self.policy.params = {}          # the ranker is patched in these tests

    def test_not_ready_never_consults_the_ranker(self):
        from unittest.mock import patch

        self.policy._value_ready = False
        with patch("combat.plan_value.rank_plans") as ranker:
            picked = self.policy._value_choice(self.board, ["first", "second"])
        self.assertIsNone(picked)
        ranker.assert_not_called()

    def test_ready_picks_the_critics_top_plan(self):
        from unittest.mock import patch

        self.policy._value_ready = True
        plans = ["first", "second"]
        with patch("combat.plan_value.rank_plans",
                   return_value=[(0.9, "second"), (0.1, "first")]):
            picked = self.policy._value_choice(self.board, plans)
        self.assertEqual(picked, "second")

    def test_a_failing_ranker_leaves_the_turn_to_the_solver(self):
        from unittest.mock import patch

        self.policy._value_ready = True
        with patch("combat.plan_value.rank_plans",
                   side_effect=RuntimeError("shape mismatch")):
            picked = self.policy._value_choice(self.board, ["first", "second"])
        self.assertIsNone(picked)

    def test_decide_feeds_the_contract_into_the_choice(self):
        import inspect

        from combat.live_policy import LivePolicy as Policy

        source = inspect.getsource(Policy.decide)
        self.assertIn('self._value_ready = bool(contract.get("value_ready"))',
                      source)


class UnspentApRescueTests(unittest.TestCase):
    """A turn that ends with AP in hand and a legal cast on the board is a lost turn.

    Measured on the 1009 live decisions in `trajectories/live_policy_decisions.jsonl`:
    of the 850 turns that had a castable spell, 355 (42%) ended in a pass, and 146 of
    those were holding a 38-damage spell at the time. The path is ordinary -- the
    actor chose to walk, the walk was impossible because the MP was already spent, and
    the solver had no plan left.

    The rescue runs only when the answer is already a pass, so these tests also pin
    that it can never override a real decision.
    """

    def setUp(self) -> None:
        self.policy = policy_stub()

    def bind(self, payload):
        board = Board.from_fight_state(payload)
        slots, _rows = self.policy._bind_slots(board)
        targets = self.policy._bind_targets(board)
        return board, slots, targets

    def two_foes(self, near_hp=50, far_hp=50):
        payload = board_payload()
        payload["fighters"][1]["hp"] = near_hp
        payload["fighters"][1]["max_hp"] = near_hp
        payload["fighters"].append(
            {"id": -2, "name": "foe2", "cell": _POINTS[(10, 11)], "team": "enemy",
             "hp": far_hp, "max_hp": far_hp, "ap": 4, "max_ap": 4, "mp": 3,
             "max_mp": 3, "level": 5, "alive": True, "spells": []})
        return payload

    def test_a_turn_with_ap_left_casts_instead_of_passing(self):
        board, slots, targets = self.bind(board_payload())
        action = self.policy._last_resort_cast(board, slots, targets)
        self.assertIsNotNone(action, "a legal cast existed and the rescue passed anyway")
        self.assertEqual(action["type"], "cast")
        self.assertEqual(action["targetId"], -1)

    def test_a_kill_is_taken_over_a_bigger_hit(self):
        # 12 damage finishes the 10-hp foe; the 30-damage spell would only dent the
        # healthy one. Removing an enemy deletes a whole enemy turn from the fight.
        payload = board_payload([spell(13, "Nuke", 3, 1, 3,
                                       [damage_effect(97, 30, 30)])])
        payload["fighters"][1]["hp"] = 10
        payload["fighters"][1]["max_hp"] = 50
        payload["fighters"].append(
            {"id": -2, "name": "tank", "cell": _POINTS[(10, 11)], "team": "enemy",
             "hp": 400, "max_hp": 400, "ap": 4, "max_ap": 4, "mp": 3,
             "max_mp": 3, "level": 5, "alive": True, "spells": []})
        board, slots, targets = self.bind(payload)
        action = self.policy._last_resort_cast(board, slots, targets)
        self.assertEqual(action["targetId"], -1)
        self.assertLessEqual(action["spellId"], 13)

    def test_the_weakest_reachable_enemy_is_focused(self):
        payload = self.two_foes(near_hp=50, far_hp=8)
        board, slots, targets = self.bind(payload)
        action = self.policy._last_resort_cast(board, slots, targets)
        self.assertEqual(action["targetId"], -2)

    def test_nothing_legal_is_still_nothing(self):
        # Out of AP: every cast is refused, so the rescue must return None and let
        # the turn end rather than inventing an action the client would reject.
        payload = board_payload()
        for fighter in payload["fighters"]:
            if fighter.get("is_me"):
                fighter["ap"] = 0
        payload["my_stats"]["action_points"] = 0
        board, slots, targets = self.bind(payload)
        self.assertIsNone(self.policy._last_resort_cast(board, slots, targets))

    def test_an_out_of_range_enemy_is_not_cast_at(self):
        # Every spell in the fixture reaches 3 cells, so a foe across the map is
        # not a target. Picked by the board's own distance, not by raw cell id.
        payload = board_payload()
        probe = Board.from_fight_state(payload)
        far = max(_POINTS.values(),
                  key=lambda cell: probe.cells.distance(MY_CELL, cell))
        payload["fighters"][1]["cell"] = far
        board, slots, targets = self.bind(payload)
        self.assertGreater(board.cells.distance(MY_CELL, far), 3)
        self.assertIsNone(self.policy._last_resort_cast(board, slots, targets))

    def test_the_rescue_never_overrides_a_real_decision(self):
        import inspect

        from combat.live_policy import LivePolicy as Policy

        source = inspect.getsource(Policy.decide)
        self.assertIn('action.get("type") == "pass"', source)
        self.assertIn('"unspent_ap_cast"', source)


class SpentMovementTests(unittest.TestCase):
    """`mp: 0` is a spent budget; `mp: null` is a client that does not publish one.

    Collapsing the two re-priced a fighter that had walked at its full movement
    budget, so every plan after the first move of a turn was built on movement the
    character no longer had -- the solver kept emitting walks the client refused, and
    the AP was never spent.
    """

    def me_row(self, payload):
        return next(f for f in payload["fighters"] if f.get("is_me"))

    def test_a_published_zero_is_a_spent_budget(self):
        payload = board_payload()
        self.me_row(payload)["mp"] = 0
        self.assertEqual(Board.from_fight_state(payload).my_mp, 0)

    def test_a_missing_value_falls_back_to_the_budget(self):
        payload = board_payload()
        self.me_row(payload)["mp"] = None
        payload["my_stats"]["movement_points"] = 3
        self.assertEqual(Board.from_fight_state(payload).my_mp, 3)

    def test_a_fighter_that_has_walked_is_not_planned_another_walk(self):
        payload = board_payload()
        payload["fighters"][1]["cell"] = _POINTS[(14, 10)]
        self.me_row(payload)["mp"] = 0
        board = Board.from_fight_state(payload)
        policy = policy_stub()
        action = policy._last_resort_cast(
            board, *[f(board) for f in (
                lambda b: policy._bind_slots(b)[0],
                lambda b: policy._bind_targets(b))])
        self.assertIsNone(action, "out of reach with no MP: nothing to cast")


if __name__ == "__main__":
    unittest.main()
