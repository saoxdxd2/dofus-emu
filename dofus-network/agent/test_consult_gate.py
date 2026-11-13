"""The deferral gate must only speak for the model it was fitted on.

A floor is a property of one model's logit scale. The failure this guards is quiet:
carry 0.75 from the old head onto a new architecture and the gate either never fires
(the head always decides, and the gate is decoration) or always fires (the rules
always decide, and the retrain is invisible). Neither raises an error.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent import consult_gate


class AppliesToTest(unittest.TestCase):
    def _write(self, payload) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8")
        with handle as fh:
            json.dump(payload, fh)
        return Path(handle.name)

    def test_no_artifact_means_no_gate(self):
        missing = Path(tempfile.gettempdir()) / "definitely_no_gate.json"
        self.assertFalse(consult_gate.applies_to("abc", "mlp", path=missing))

    def test_legacy_artifact_applies_to_the_mlp_only(self):
        path = self._write({"floor": 0.75})
        self.assertTrue(consult_gate.applies_to("whatever", "mlp", path=path))
        self.assertFalse(consult_gate.applies_to("whatever", "dedicated-v1", path=path))

    def test_bound_artifact_requires_a_matching_hash(self):
        path = self._write({"floor": 1.05, "model_sha256": "a" * 64})
        self.assertTrue(consult_gate.applies_to("a" * 64, "dedicated-v1", path=path))
        self.assertFalse(consult_gate.applies_to("b" * 64, "dedicated-v1", path=path))
        self.assertFalse(consult_gate.applies_to(None, "dedicated-v1", path=path))

    def test_should_defer_still_reads_the_floor(self):
        path = self._write({"floor": 1.0})
        gate = consult_gate.load(path)
        self.assertTrue(consult_gate.should_defer(gap=0.4, gate=gate))
        self.assertFalse(consult_gate.should_defer(gap=1.4, gate=gate))


class ValueHeadAndStreakDeferralTest(unittest.TestCase):
    def setUp(self):
        import numpy as np
        from agent.planner_nn import NNPlanner

        class StubSession:
            def __init__(self, logits, value_logit=None):
                self.logits = np.asarray(logits, dtype="float32")
                self.value_logit = value_logit

            def run(self, _names, _feeds):
                out = [np.array([self.logits])]
                if self.value_logit is not None:
                    out.append(np.array([[self.value_logit]], dtype="float32"))
                return out

        class StubTracker:
            def __init__(self, streak=0):
                self.streak = streak
                self.last_tool = None

            def ctx(self):
                return {}

            def live_penalty(self, tools):
                return {}

        self.StubSession = StubSession
        self.StubTracker = StubTracker
        self.NNPlanner = NNPlanner
        self.tools = ["nav.goto", "quest.advance", "farm.start", "fight.join", "inv.list"]

    def _make_planner(self, logits, value_logit=None, streak=0, model_sha=None):
        nn = self.NNPlanner.__new__(self.NNPlanner)
        nn._model_loaded = True
        nn._warned = False
        nn.tools = self.tools
        nn.session = self.StubSession(logits, value_logit)
        nn.input_name = "features"
        nn.expected_features = 42
        nn.arch = "mlp"
        nn.history_steps = 0
        nn.model_sha256 = model_sha
        nn.last_value = None
        nn.tool_penalty = {}
        nn.stack = None
        nn.stack_mode = "off"
        nn.tracker = self.StubTracker(streak=streak)
        nn.bias_tracker = self.StubTracker(streak=0)
        nn.ledger = None
        nn.note = ""
        nn.last_margin = None
        nn.last_deferral = None
        nn.last_stack = {"tool": None}
        return nn

    def test_sure_prediction_with_high_value_does_not_defer(self):
        # raw value_logit = 0.0 -> sigmoid(0.0) = 0.5 >= 0.25
        # logits margin = 9.0 - 2.0 = 7.0 (large margin, no consult_gate deferral)
        planner = self._make_planner([0.0, 0.0, 2.0, 9.0, 1.0], value_logit=0.0, streak=0)
        call = planner._choose_tool({"level_min": 60, "id": 1}, 0,
                                    {"type_id": 1, "params": "{}", "text": "Talk."},
                                    {"map_id": 1, "level": 60, "inventory": {}, "in_fight": False})
        self.assertEqual(call, "fight.join")
        self.assertAlmostEqual(planner.last_value, 0.5)
        self.assertIsNone(planner.last_deferral)

    def test_low_value_head_defers_to_rules(self):
        # raw value_logit = -2.0 -> sigmoid(-2.0) = 0.1192 < 0.25
        # Large margin (7.0), streak 0, but low predicted progress -> defers!
        planner = self._make_planner([0.0, 0.0, 2.0, 9.0, 1.0], value_logit=-2.0, streak=0)
        call = planner._choose_tool({"level_min": 60, "id": 1}, 0,
                                    {"type_id": 1, "params": "{}", "text": "Talk."},
                                    {"map_id": 1, "level": 60, "inventory": {}, "in_fight": False})
        self.assertIsNone(call)
        self.assertIsNotNone(planner.last_value)
        self.assertLess(planner.last_value, 0.25)
        self.assertEqual(planner.last_deferral, planner.last_margin)

    def test_repeat_streak_ge_2_defers_to_rules(self):
        # High value (0.5), large margin (7.0), but streak = 2 consecutive failures -> defers!
        planner = self._make_planner([0.0, 0.0, 2.0, 9.0, 1.0], value_logit=0.0, streak=2)
        call = planner._choose_tool({"level_min": 60, "id": 1}, 0,
                                    {"type_id": 1, "params": "{}", "text": "Talk."},
                                    {"map_id": 1, "level": 60, "inventory": {}, "in_fight": False})
        self.assertIsNone(call)
        self.assertEqual(planner.last_deferral, planner.last_margin)

    def test_streak_1_masks_last_tool_without_deferring(self):
        # streak = 1 should mask the last tool but not defer if value >= 0.25 and margin is large
        planner = self._make_planner([0.0, 0.0, 2.0, 9.0, 1.0], value_logit=0.0, streak=1)
        planner.tracker.last_tool = "fight.join"
        call = planner._choose_tool({"level_min": 60, "id": 1}, 0,
                                    {"type_id": 1, "params": "{}", "text": "Talk."},
                                    {"map_id": 1, "level": 60, "inventory": {}, "in_fight": False})
        # "fight.join" masked (-inf), next highest is "farm.start" (score 2.0)
        self.assertEqual(call, "farm.start")
        self.assertIsNone(planner.last_deferral)


if __name__ == "__main__":
    unittest.main()
