"""The live actor must bind whatever character the board describes."""

from __future__ import annotations

import unittest

from combat.live_policy import LivePolicy
from combat.test_observation import live_payload


class LivePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = LivePolicy(beam_width=4, max_actions=2)

    def _forced(self, move=2, spell_slot=0, target_slot=1):
        original = self.policy._choose
        self.policy._choose = lambda logits, masks: (
            move, spell_slot, target_slot, 0.9, 0.5)
        return original

    def test_the_live_spell_and_enemy_are_realized(self):
        payload = live_payload()
        original = self._forced()
        try:
            decision = self.policy.decide(payload)
        finally:
            self.policy._choose = original
        self.assertTrue(decision["ok"], decision.get("reason"))
        self.assertEqual(decision["slots"][0]["spell_id"], 123)
        self.assertEqual(decision["targets"][1]["fighter_id"], 2)
        action = decision["action"]
        self.assertEqual(action["type"], "cast")
        self.assertEqual(action["spellId"], 123)
        self.assertEqual(action["targetId"], 2)
        self.assertNotIn(164, [slot["spell_id"] for slot in decision["slots"]])
        self.assertIn("observation", decision)
        self.assertEqual(decision["observation"]["self"]["level"], 12)

    def test_a_different_instance_rebinds_without_fixed_spells(self):
        payload = live_payload(level=3)
        payload["spells"][0]["id"] = 77
        payload["fighters"][0]["level"] = 3
        payload["fighters"][1]["id"] = 9
        original = self._forced()
        try:
            decision = self.policy.decide(payload)
        finally:
            self.policy._choose = original
        self.assertTrue(decision["ok"], decision.get("reason"))
        self.assertEqual(decision["slots"][0]["spell_id"], 77)
        self.assertEqual(decision["targets"][1]["fighter_id"], 9)
        self.assertEqual(decision["action"]["spellId"], 77)
        self.assertEqual(decision["action"]["targetId"], 9)
        self.assertEqual(decision["observation"]["self"]["level"], 3)

    def test_the_real_checkpoint_returns_a_legal_action_or_pass(self):
        decision = self.policy.decide(live_payload())
        self.assertTrue(decision["ok"], decision.get("reason"))
        action = decision["action"]
        self.assertIn(action["type"], {"cast", "move", "pass"})
        if action["type"] == "cast":
            slot_ids = {slot["spell_id"] for slot in decision["slots"]
                        if slot["spell_id"] > 0}
            target_ids = {target["fighter_id"] for target in decision["targets"]
                          if target.get("fighter_id") is not None}
            self.assertIn(action["spellId"], slot_ids)
            self.assertIn(action["targetId"], target_ids)
        self.assertIn("policy", decision)
        self.assertIn("fallback", decision["policy"])

    def test_a_board_that_is_not_our_turn_is_refused(self):
        payload = live_payload()
        payload["is_my_turn"] = False
        decision = self.policy.decide(payload)
        self.assertFalse(decision["ok"])
        self.assertIsNone(decision["action"])
        self.assertEqual(decision["reason"], "observation_not_actionable")


if __name__ == "__main__":
    unittest.main()
