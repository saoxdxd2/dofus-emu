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


if __name__ == "__main__":
    unittest.main()
