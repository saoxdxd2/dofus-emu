import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.verifier import Verifier, Verdict
from agent.controller import Controller, Decision
from agent.planner import QuestPlanner, DIALOG_STALL_LIMIT

class TestDialogueFixes(unittest.TestCase):
    def test_verifier_dialogue_progression(self):
        verifier = Verifier()
        before = {
            "in_dialog": True,
            "dialog": {"message": "Hello warrior, choose your path.", "replies": [{"text": "Tell me more"}]},
            "position": {"map_id": 146276352, "cell": 330},
            "hp": {"life": 100},
            "kamas": 0
        }
        after = {
            "in_dialog": True,
            "dialog": {"message": "Here are the elemental disciplines.", "replies": [{"text": "Voie de la Terre"}, {"text": "Voie du Feu"}]},
            "position": {"map_id": 146276352, "cell": 330},
            "hp": {"life": 100},
            "kamas": 0
        }
        verdict = verifier.label("quest.advance", {"quest_id": 4262, "reply": 0}, True, None, before, after)
        self.assertEqual(verdict.verdict, "advanced")
        self.assertTrue(any("dialog_message" in reason or "dialog_replies" in reason for reason in verdict.reasons))

    def test_controller_no_reply_retry(self):
        controller = Controller()
        # Even on a noop verdict, dialogue replies must NOT be retried
        decision = controller.on_step(
            Verdict("noop", 0.6, 0.0, False, "rule", ("call succeeded but no observable state changed",)),
            "quest.advance",
            True,
            None,
            before={"in_dialog": True},
            after={"in_dialog": True},
            args={"quest_id": 4262, "reply": 0, "reply_text": "Head out. {icon:iconNewQuestMain}", "npc_id": 3892}
        )
        self.assertNotEqual(decision.action, "retry")
        self.assertEqual(decision.action, "continue")

    def test_planner_stall_resets_on_new_dialogue_text(self):
        planner = QuestPlanner()
        self.assertEqual(DIALOG_STALL_LIMIT, 16)
        
        # Simulate 10 sequential distinct dialogue messages
        for step_i in range(10):
            obs = {
                "in_dialog": True,
                "dialog": {
                    "open": True,
                    "text_ready": True,
                    "ids_ready": True,
                    "npc_id": 3892,
                    "message": f"Dialogue stage {step_i} text from Great Jarl Ordyn",
                    "replies": [{"index": 0, "text": "Continue", "reply_id": 41000 + step_i}]
                },
                "current_objective": {"quest_id": 4262, "objective_id": 13958}
            }
            call = planner._dialog_call(obs, "quest 4262")
            self.assertIsNotNone(call)
            # The planner must NOT close the dialogue while messages are progressing
            self.assertFalse(call.args.get("close", False))
            self.assertEqual(planner._dialog_stall, 1)

if __name__ == '__main__':
    unittest.main()
