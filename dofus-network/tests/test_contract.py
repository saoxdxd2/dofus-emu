#!/usr/bin/env python3
"""Checkpoint contract tests: feature/horizon alignment validation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from networkhealth.contract import CHECKPOINT_FORMAT_VERSION, ModelBundle, load_checkpoint
from networkhealth.features import FEATURE_NAMES, HORIZONS, Normaliser, TARGET_BLOCK


class TestContract(unittest.TestCase):
    def test_checkpoint_format_version(self):
        self.assertEqual(CHECKPOINT_FORMAT_VERSION, "2.0")

    def test_model_bundle_validation_passes(self):
        normaliser = Normaliser.fit(np.random.RandomState(0).randn(20, 10))
        bundle = ModelBundle(
            state_dict={},
            model_config={"features": 10},
            normaliser=normaliser,
            feature_names=tuple(FEATURE_NAMES),
            horizons=tuple(HORIZONS),
            target_block=TARGET_BLOCK,
            metadata={},
        )
        problems = bundle.validate()
        self.assertEqual(problems, [])

    def test_model_bundle_catches_feature_mismatch(self):
        normaliser = Normaliser.fit(np.random.RandomState(0).randn(20, 5))
        bundle = ModelBundle(
            state_dict={},
            model_config={"features": 5},
            normaliser=normaliser,
            feature_names=("a", "b", "c", "d", "e"),
            horizons=tuple(HORIZONS),
            target_block=TARGET_BLOCK,
            metadata={},
        )
        problems = bundle.validate()
        self.assertGreater(len(problems), 0)
        self.assertTrue(any("feature" in p.lower() for p in problems))

    def test_load_checkpoint_raises_on_missing_file(self):
        with self.assertRaises((FileNotFoundError, OSError, RuntimeError)):
            load_checkpoint(Path("/nonexistent/checkpoint.pt"))


if __name__ == "__main__":
    unittest.main()