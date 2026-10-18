#!/usr/bin/env python3
"""Evaluation metrics tests: mathematical soundness, JSON serialisation."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from networkhealth.evaluation import (
    expected_calibration_error, masked_bias, masked_mae, masked_rmse,
    brier_score, detection_metrics, _json_default,
)


class TestEvaluation(unittest.TestCase):
    def test_masked_mae_rmse_bias(self):
        pred = np.array([1.0, 2.0, 3.0, 4.0])
        true = np.array([1.1, 1.9, 3.2, 3.8])
        mask = np.array([1.0, 1.0, 0.0, 1.0])
        mae = masked_mae(pred, true, mask)
        rmse = masked_rmse(pred, true, mask)
        bias = masked_bias(pred, true, mask)
        self.assertIsNotNone(mae)
        self.assertIsNotNone(rmse)
        self.assertIsNotNone(bias)
        expected_mae = (abs(1.0 - 1.1) + abs(2.0 - 1.9) + abs(4.0 - 3.8)) / 3.0
        self.assertAlmostEqual(mae, expected_mae)
        self.assertTrue(math.isfinite(mae) and math.isfinite(rmse) and math.isfinite(bias))

    def test_masked_mae_all_invalid_returns_none(self):
        pred = np.array([1.0, 2.0])
        true = np.array([1.0, 2.0])
        mask = np.array([0.0, 0.0])
        self.assertIsNone(masked_mae(pred, true, mask))
        self.assertIsNone(masked_rmse(pred, true, mask))
        self.assertIsNone(masked_bias(pred, true, mask))

    def test_brier_score(self):
        prob = np.array([0.1, 0.9, 0.8, 0.2])
        truth = np.array([0.0, 1.0, 1.0, 0.0])
        b = brier_score(prob, truth)
        expected = float(np.mean((prob - truth) ** 2))
        self.assertAlmostEqual(b, expected)
        self.assertTrue(0.0 <= b <= 1.0)

    def test_ece_perfect_calibration_is_low(self):
        # With only 4 points and 4 bins, each bin has 1 point so ECE can be up to ~0.25.
        # Use more points for a tighter calibration check.
        rng = np.random.RandomState(42)
        prob = rng.rand(200)
        truth = (prob > 0.5).astype(float)
        prob = np.clip(prob, 0.05, 0.95)
        truth = (prob + rng.normal(0, 0.1, 200) > 0.5).astype(float)
        ece = expected_calibration_error(prob, truth, bins=10)
        self.assertIsNotNone(ece)
        self.assertGreaterEqual(ece, 0.0)
        self.assertLess(ece, 0.3)  # reasonable calibration

    def test_detection_metrics_perfect(self):
        prob = np.array([0.9, 0.8, 0.3, 0.1])
        truth = np.array([1.0, 1.0, 0.0, 0.0])
        m = detection_metrics(prob, truth, threshold=0.5)
        self.assertEqual(m["true_positive"], 2)
        self.assertEqual(m["false_positive"], 0)
        self.assertEqual(m["true_negative"], 2)
        self.assertEqual(m["false_negative"], 0)
        self.assertEqual(m["precision"], 1.0)
        self.assertEqual(m["recall"], 1.0)
        self.assertEqual(m["f1"], 1.0)

    def test_json_default_handles_numpy(self):
        # numpy types are handled correctly (caught before numbers.Number line)
        self.assertEqual(_json_default(np.float64(3.5)), 3.5)
        self.assertEqual(_json_default(np.int32(7)), 7)
        self.assertEqual(_json_default(np.array([1, 2, 3])), [1, 2, 3])

    def test_json_default_math_generic_bug_fixed(self):
        """BUG-001 FIXED: math.generic replaced with numbers.Number — _json_default
        now handles int/float/Decimal/etc. without AttributeError."""
        self.assertEqual(_json_default(42), 42)
        self.assertEqual(_json_default(3.14), 3.14)
        # Non-numeric types still raise TypeError
        with self.assertRaises(TypeError):
            _json_default({"not": "serialisable"})


if __name__ == "__main__":
    unittest.main()