"""Which mode the stack starts in, and what that mode is allowed to do.

Two things are pinned here, and they answer different questions.

The first is the default: `shadow` computed the stack's ranking on every step
and then discarded it, which is the most expensive way to do nothing. The
default is now `act`, overridable with `DOFUS_STACK_MODE`.

The second is the invariant that makes that safe. A mode is a *permission*, not
a promotion: `act` only returns the stack's choice when the artifact itself
cleared its held-out gate (`ToolStack.active` requires both `payload["active"]`
and `gate["passed"]`). So a fitted-but-ungated artifact stays in shadow no
matter what the mode says, and changing the default cannot promote a model that
did not earn it.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.planner_nn import NNPlanner  # noqa: E402


def _planner(**kwargs):
    # No model path: the constructor must not need an ONNX session to resolve a
    # mode, and a test that loaded one would be measuring onnxruntime.
    return NNPlanner(model_path=Path("does-not-exist.onnx"), **kwargs)


class ModeResolutionTests(unittest.TestCase):

    def test_the_default_is_act(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DOFUS_STACK_MODE", None)
            self.assertEqual(_planner().stack_mode, "act")

    def test_the_environment_can_pin_shadow(self):
        with mock.patch.dict(os.environ, {"DOFUS_STACK_MODE": "shadow"}):
            planner = _planner()
            self.assertEqual(planner.stack_mode, "shadow")
            self.assertEqual(planner.stack_mode_source, "env")

    def test_the_environment_can_pin_off(self):
        with mock.patch.dict(os.environ, {"DOFUS_STACK_MODE": "OFF"}):
            self.assertEqual(_planner().stack_mode, "off")

    def test_an_explicit_argument_beats_the_environment(self):
        with mock.patch.dict(os.environ, {"DOFUS_STACK_MODE": "off"}):
            planner = _planner(stack_mode="act")
            self.assertEqual(planner.stack_mode, "act")
            self.assertEqual(planner.stack_mode_source, "argument")

    def test_a_misspelt_environment_value_does_not_silently_disable_the_stack(self):
        """A typo must fall back to the useful default, not to 'off'."""
        with mock.patch.dict(os.environ, {"DOFUS_STACK_MODE": "active"}):
            self.assertEqual(_planner().stack_mode, "act")


class GateIsAPermissionNotAPromotionTests(unittest.TestCase):
    """`act` cannot make an uncalibrated artifact drive the character."""

    @staticmethod
    def _stack(active: bool, gate_passed: bool):
        return {
            "kind": "tool-stacking",
            "features": list(_base_features()),
            "model_type": "linear",
            "weights": {name: 0.0 for name in _base_features()},
            "shortlist": 6,
            "active": active,
            "gate": {"passed": gate_passed},
        }

    def test_act_with_an_ungated_artifact_leaves_the_stack_inactive(self):
        from agent.ensemble import ToolStack

        stack = ToolStack(self._stack(active=True, gate_passed=False))
        self.assertTrue(stack.active is False,
                        "a stack whose gate did not pass must not be allowed to act")

    def test_act_with_a_gated_artifact_is_active(self):
        from agent.ensemble import ToolStack

        stack = ToolStack(self._stack(active=True, gate_passed=True))
        self.assertTrue(stack.active)

    def test_a_gate_that_passed_but_an_artifact_marked_inactive_stays_inactive(self):
        """The two fields must agree; either one alone is not a promotion."""
        from agent.ensemble import ToolStack

        stack = ToolStack(self._stack(active=False, gate_passed=True))
        self.assertFalse(stack.active)


class ActUsesTheStackTests(unittest.TestCase):
    """`act` must let the fitted stack move the choice where the policy is torn.

    The whole argument for promoting the mode is that the stack contributes
    something the policy head cannot see. That is only true where the head's own
    margin is small; when the head is confident its logit dominates the fitted
    coefficients and the stack correctly leaves it alone. Both cases are pinned,
    because a test that only showed the confident one would pass on a stack that
    returns the policy's argmax unconditionally.
    """

    def _stack(self):
        from agent.ensemble import ToolStack

        path = REPO_ROOT / "models" / "tool_stack.json"
        if not path.is_file():
            self.skipTest("no shipped stack artifact")
        stack = ToolStack.load(path)
        if stack is None or not stack.active:
            self.skipTest("the shipped stack is not active")
        return stack

    def test_a_torn_policy_is_corrected_by_the_foresight_signal(self):
        import numpy as np

        stack = self._stack()
        tools = ["a", "b", "c", "d"]
        logits = np.asarray([2.00, 1.98, 0.0, 0.0])
        cells = {tool: {"move_prob": 0.5, "gain": 0.1} for tool in tools}
        cells["b"] = {"move_prob": 0.95, "gain": 4.0}
        ranked = stack.rank(logits, cells, tools, context=None)
        self.assertEqual(ranked[0]["tool"], "b", ranked)

    def test_a_confident_policy_is_left_alone(self):
        import numpy as np

        stack = self._stack()
        tools = ["a", "b", "c", "d"]
        logits = np.asarray([5.0, 0.0, 0.0, 0.0])
        cells = {tool: {"move_prob": 0.5, "gain": 0.1} for tool in tools}
        cells["b"] = {"move_prob": 0.99, "gain": 5.0}
        ranked = stack.rank(logits, cells, tools, context=None)
        self.assertEqual(ranked[0]["tool"], "a",
                         "the stack overrode a decisive policy head: %r" % (ranked,) )


class ShippedArtifactTests(unittest.TestCase):
    """The artifact that ships, judged by the same rule the runtime uses."""

    def test_the_shipped_stack_agrees_with_its_own_gate(self):
        from agent.ensemble import ToolStack

        path = REPO_ROOT / "models" / "tool_stack.json"
        if not path.is_file():
            self.skipTest("no shipped stack artifact")
        payload = json.loads(path.read_text(encoding="utf-8"))
        gate = payload.get("gate") or {}
        self.assertEqual(bool(payload.get("active")), bool(gate.get("passed")),
                         "active and gate.passed disagree")
        stack = ToolStack.load(path)
        self.assertIsNotNone(stack)
        self.assertEqual(stack.active, bool(payload.get("active")))

    def test_the_shipped_stack_records_what_it_was_measured_on(self):
        path = REPO_ROOT / "models" / "tool_stack.json"
        if not path.is_file():
            self.skipTest("no shipped stack artifact")
        payload = json.loads(path.read_text(encoding="utf-8"))
        gate = payload.get("gate") or {}
        # A gate with no held-out folds is a fit, not a measurement.
        self.assertGreaterEqual(int(gate.get("folds") or 0), 2, gate)
        self.assertIn("cv_top1", gate, "the gate does not report its held-out score")


def _base_features():
    from agent.ensemble import BASE_FEATURES

    return BASE_FEATURES


if __name__ == "__main__":
    unittest.main(verbosity=1)
