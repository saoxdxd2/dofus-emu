#!/usr/bin/env python3
"""Inference runtime tests: rolling window, forecasts, multi-instance isolation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import torch
    from networkhealth.inference import (
        ForecastResult, MultiInstanceNetworkHealth, NetworkHealthInference,
        Observation, RollingWindow,
    )
    from networkhealth.features import Normaliser, FEATURE_NAMES, HORIZONS, SEQ_LEN
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


@unittest.skipUnless(_HAS_TORCH, "torch not available")
class TestRollingWindow(unittest.TestCase):
    def test_window_fills_and_flushes(self):
        window = RollingWindow(seq_len=8)
        for i in range(12):
            window.append(Observation(timestamp=float(i), rtt_ms=float(30 + i)))
        self.assertEqual(len(window), 8)
        # deque with maxlen keeps the last 8 items: indices 4..11
        rows = list(window._rows)
        self.assertEqual(rows[0]["timestamp"], 4.0)
        self.assertEqual(rows[-1]["timestamp"], 11.0)

    def test_window_handles_missing_rtt(self):
        window = RollingWindow(seq_len=8)
        window.append(Observation(timestamp=1.0, rtt_ms=None))
        window.append(Observation(timestamp=2.0, rtt_ms=30.0))
        self.assertEqual(len(window), 2)

    def test_window_fill_ratio(self):
        window = RollingWindow(seq_len=10)
        for i in range(3):
            window.append(Observation(timestamp=float(i), rtt_ms=30.0))
        self.assertAlmostEqual(window.fill, 0.3)


@unittest.skipUnless(_HAS_TORCH, "torch not available")
class TestMultiInstanceNetworkHealth(unittest.TestCase):
    def test_multi_instance_isolation(self):
        import tempfile, os
        # Create a minimal checkpoint for testing
        from networkhealth.model import ModelConfig, NetworkHealthTCN
        ckpt_path = Path(tempfile.mktemp(suffix=".pt"))
        cfg = ModelConfig(features=10, hidden=16, levels=1)
        model = NetworkHealthTCN(cfg)
        torch.save({
            "state_dict": model.state_dict(),
            "model_config": cfg.as_dict(),
            "normaliser": Normaliser.fit(
                np.random.RandomState(0).randn(20, 10)
            ).as_dict(),
            "feature_names": list(FEATURE_NAMES),
            "horizons": list(HORIZONS),
            "target_block": 5,
        }, ckpt_path)
        try:
            engine = MultiInstanceNetworkHealth(ckpt_path)
            r1 = engine.observe("slot-1", Observation(timestamp=1000.0, rtt_ms=30.0))
            r2 = engine.observe("slot-2", Observation(timestamp=1000.0, rtt_ms=90.0))
            # Different instances should have independent windows
            self.assertEqual(len(engine.active_instances()), 2)
            if r1 is not None:
                self.assertIsInstance(r1, ForecastResult)
            if r2 is not None:
                self.assertIsInstance(r2, ForecastResult)
        finally:
            ckpt_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()