"""The activation-steering and transition-probe seam in the live planner.

Two artifacts read the 192-wide pre-logit tensor: the SAE steering direction
(Phase 1A) and the linear transition probe (Phase 1B). Both are optional and
both must fail open. The tests pin the arithmetic against the offline formula,
the state-feature gate, the disabled-probe path, and the binding between an
artifact and the checkpoint it was measured on.

Run: python agent/test_planner_steering.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.features import FEATURE_NAMES  # noqa: E402
from agent.planner_nn import NNPlanner  # noqa: E402

# The runtime reads the mask index from the artifact; the test pins that index
# to the feature contract it was measured on rather than to a bare literal.
OBS_STALE = FEATURE_NAMES.index("obs_stale")
MODEL = REPO_ROOT / "models" / "tool_policy.onnx"


def _planner(tools=("a", "b", "c")):
    planner = NNPlanner(model_path=Path("does-not-exist.onnx"))
    planner.tools = list(tools)
    return planner


def _steering(alpha=1.75, scale=4.0, hidden=192, n_tools=3):
    direction = np.full(hidden, 1.0 / np.sqrt(hidden), dtype=np.float32)
    weight = np.asarray(
        [[0.01 * (row + 1) * ((i % 7) - 3) for i in range(hidden)]
         for row in range(n_tools)], dtype=np.float32)
    return {
        "latent": 901,
        "alpha": alpha,
        "logit_scale": scale,
        "d_off_map": direction.tolist(),
        "w_out": weight.tolist(),
        "mask": {"feature": OBS_STALE, "name": "obs_stale", "threshold": 0.5},
    }


def _features(stale):
    state = np.zeros(42, dtype=np.float32)
    state[OBS_STALE] = 1.0 if stale else 0.0
    return state


def _probe(enabled, *, b=-10.0, tau=0.2121, penalty=0.25, hidden=192):
    return {
        "enabled": enabled,
        "tau": tau,
        "penalty": penalty,
        "b": b,
        "w": [0.0] * hidden,
        "mean": [0.0] * hidden,
        "std": [1.0] * hidden,
        "read_only_tools": ["state.snapshot"],
    }


class SteeringArithmeticTests(unittest.TestCase):
    """The runtime delta must be the offline delta, not an approximation."""

    def test_the_delta_matches_the_offline_formula(self):
        planner = _planner()
        block = _steering()
        planner.steering = block
        rng = np.random.default_rng(7)
        hidden = rng.normal(size=192).astype(np.float32)
        logits = rng.normal(size=3).astype(np.float32)
        direction = np.asarray(block["d_off_map"], dtype=np.float32)
        weight = np.asarray(block["w_out"], dtype=np.float32)
        projection = max(0.0, float(hidden @ direction))
        expected = logits - (block["alpha"] * projection) * (
            (weight @ direction) * block["logit_scale"])
        got = planner._apply_activation_steering(
            logits, hidden, _features(stale=True))
        np.testing.assert_allclose(got, expected, rtol=1e-6, atol=1e-6)

    def test_the_stale_gate_leaves_fresh_rows_untouched(self):
        planner = _planner()
        planner.steering = _steering()
        # Positive projection: a zero-mean vector dotted with the constant
        # direction is exactly zero, which the relu gate would correctly leave
        # alone and the test would then measure nothing.
        hidden = np.linspace(0.0, 1.0, 192, dtype=np.float32)
        logits = np.asarray([0.5, -0.25, 1.0], dtype=np.float32)
        fresh = planner._apply_activation_steering(
            logits, hidden, _features(stale=False))
        np.testing.assert_array_equal(fresh, logits)
        stale = planner._apply_activation_steering(
            logits, hidden, _features(stale=True))
        self.assertFalse(np.allclose(stale, logits),
                         "the mask fired but the logits did not move")

    def test_a_negative_projection_is_not_steered(self):
        """The offline gate is relu(h @ d): anti-aligned states are untouched."""
        planner = _planner()
        block = _steering()
        planner.steering = block
        direction = np.asarray(block["d_off_map"], dtype=np.float32)
        logits = np.asarray([0.5, -0.25, 1.0], dtype=np.float32)
        got = planner._apply_activation_steering(
            logits, -direction, _features(stale=True))
        np.testing.assert_array_equal(got, logits)

    def test_a_shape_mismatch_fails_open(self):
        planner = _planner()
        block = _steering()
        block["w_out"] = block["w_out"][:2]
        planner.steering = block
        logits = np.asarray([0.5, -0.25, 1.0], dtype=np.float32)
        got = planner._apply_activation_steering(
            logits, np.ones(192, dtype=np.float32), _features(stale=True))
        np.testing.assert_array_equal(got, logits)

    def test_no_artifact_returns_the_logits_unchanged(self):
        planner = _planner()
        logits = np.asarray([0.5, -0.25, 1.0], dtype=np.float32)
        got = planner._apply_activation_steering(
            logits, np.ones(192, dtype=np.float32), _features(stale=True))
        np.testing.assert_array_equal(got, logits)


class ProbeTests(unittest.TestCase):
    """A disabled probe may observe; it may not move the decision."""

    def test_a_disabled_probe_logs_the_score_but_does_not_penalise(self):
        planner = _planner(("a", "state.snapshot", "b"))
        planner.probe = _probe(enabled=False)
        logits = np.asarray([1.0, 0.5, 0.25], dtype=np.float32)
        got = planner._apply_transition_probe(
            logits, np.zeros(192, dtype=np.float32))
        np.testing.assert_array_equal(got, logits)
        self.assertIsNotNone(planner.last_probe)
        self.assertFalse(planner.last_probe["enabled"])
        self.assertLess(planner.last_probe["score"], planner.last_probe["tau"])

    def test_an_enabled_probe_penalises_only_non_read_tools(self):
        planner = _planner(("a", "state.snapshot", "b"))
        planner.probe = _probe(enabled=True)
        logits = np.asarray([1.0, 0.5, 0.25], dtype=np.float32)
        got = planner._apply_transition_probe(
            logits, np.zeros(192, dtype=np.float32))
        self.assertAlmostEqual(float(got[0]), 0.75, places=6)
        self.assertAlmostEqual(float(got[1]), 0.5, places=6)
        self.assertAlmostEqual(float(got[2]), 0.0, places=6)

    def test_an_enabled_probe_above_tau_leaves_the_logits_alone(self):
        planner = _planner(("a", "state.snapshot", "b"))
        planner.probe = _probe(enabled=True, b=10.0)
        logits = np.asarray([1.0, 0.5, 0.25], dtype=np.float32)
        got = planner._apply_transition_probe(
            logits, np.zeros(192, dtype=np.float32))
        np.testing.assert_array_equal(got, logits)
        self.assertGreaterEqual(planner.last_probe["score"],
                                planner.last_probe["tau"])

    def test_a_shape_mismatch_fails_open(self):
        planner = _planner(("a", "state.snapshot", "b"))
        block = _probe(enabled=True)
        block["w"] = [0.0] * 10
        planner.probe = block
        logits = np.asarray([1.0, 0.5, 0.25], dtype=np.float32)
        got = planner._apply_transition_probe(
            logits, np.zeros(192, dtype=np.float32))
        np.testing.assert_array_equal(got, logits)
        self.assertIsNone(planner.last_probe)


class ShippedArtifactTests(unittest.TestCase):
    """The artifacts that ship, judged against the checkpoint they name."""

    def test_the_steering_artifact_is_bound_to_the_shipped_checkpoint(self):
        path = REPO_ROOT / "models" / "tool_policy_sae.json"
        if not path.is_file():
            self.skipTest("no steering artifact")
        payload = json.loads(path.read_text(encoding="utf-8"))
        sha = hashlib.sha256(MODEL.read_bytes()).hexdigest()
        self.assertEqual(payload.get("model_sha256"), sha,
                         "steering artifact was measured on a different graph")
        block = payload.get("steering") or {}
        self.assertEqual(int(block.get("latent")), 901)
        self.assertAlmostEqual(float(block.get("alpha")), 1.75, places=6)
        self.assertEqual(int((block.get("mask") or {}).get("feature")), OBS_STALE)
        self.assertEqual(len(block.get("d_off_map") or []), 192)
        self.assertEqual(len(block.get("w_out") or []), 21)

    def test_the_probe_artifact_is_disabled_and_bound_to_the_checkpoint(self):
        path = REPO_ROOT / "models" / "tool_policy_probe.json"
        if not path.is_file():
            self.skipTest("no probe artifact")
        payload = json.loads(path.read_text(encoding="utf-8"))
        sha = hashlib.sha256(MODEL.read_bytes()).hexdigest()
        self.assertEqual(payload.get("model_sha256"), sha)
        self.assertFalse(bool(payload.get("enabled")),
                         "the probe did not clear the 0.65 enable rule")
        self.assertLess(float((payload.get("metrics") or {})
                              .get("val", {}).get("auc", 0.0)), 0.65)

    def test_the_planner_loads_the_hidden_graph_and_logs_the_probe(self):
        if not MODEL.is_file():
            self.skipTest("no shipped checkpoint")
        planner = NNPlanner(model_path=MODEL, stack_mode="off")
        planner._ensure_model()
        self.assertEqual(planner.hidden_output, "/Mul_5_output_0")
        self.assertEqual(planner.hidden_index, 2)
        self.assertIsNotNone(planner.steering)
        self.assertIsNotNone(planner.probe)
        with mock.patch("agent.features.objective_features_history",
                        return_value=[0.0] * planner.expected_features):
            planner._choose_tool({"level_min": 1}, 0, {}, {})
        self.assertIsNotNone(planner.last_probe,
                             "the probe score was not recorded on the decision")
        self.assertIn("score", planner.last_probe)


if __name__ == "__main__":
    unittest.main(verbosity=1)
