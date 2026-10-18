#!/usr/bin/env python3
"""Feature construction tests: causality, determinism, normalisation."""

from __future__ import annotations

import math
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from networkhealth.features import (
    FEATURE_NAMES, HORIZONS, SEQ_LEN, TARGET_BLOCK,
    Normaliser, build_examples, build_series_features, build_targets,
)


def _synth_series(measurement_id=100, probe_id=200, n=200, interval=60,
                  base_rtt=30.0, loss_rate=0.05, seed=1):
    import random
    rng = random.Random(seed)
    base = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
    rows = []
    rtt = base_rtt
    t = base
    for i in range(n):
        lost = rng.random() < loss_rate
        rtt_val = None if lost else round(max(5.0, rtt + rng.uniform(-3, 3)), 3)
        rtt = rtt_val if rtt_val is not None else rtt
        rows.append({
            "source": "synthetic", "measurement_id": measurement_id,
            "probe_id": probe_id, "timestamp": t,
            "rtt_ms": rtt_val, "received": 0 if lost else 1, "sent": 1,
        })
        t += interval + rng.randint(-2, 2)
    return rows


class TestFeatures(unittest.TestCase):
    def test_feature_count_and_names(self):
        self.assertEqual(len(FEATURE_NAMES), 10)
        self.assertEqual(FEATURE_NAMES, (
            "log_rtt_ms", "is_missing", "log_gap_seconds", "log_rtt_delta",
            "loss_rate_8", "loss_rate_32", "hour_sin", "hour_cos",
            "weekday_sin", "weekday_cos",
        ))

    def test_build_series_features_returns_all_rows(self):
        rows = _synth_series(n=100)
        sm = build_series_features(rows)
        key = ("synthetic", 100, 200)
        self.assertIn(key, sm)
        self.assertEqual(len(sm[key]["rows"]), 100)
        self.assertEqual(sm[key]["features"].shape, (100, 10))

    def test_features_are_causal_no_future_leakage(self):
        rows = _synth_series(n=80)
        sm = build_series_features(rows)
        key = next(iter(sm))
        feats = sm[key]["features"]
        future_idx = 50
        rows_copy = [dict(r) for r in rows]
        rows_copy[future_idx]["rtt_ms"] = 999.0
        sm2 = build_series_features(rows_copy)
        key2 = next(iter(sm2))
        feats2 = sm2[key2]["features"]
        for i in range(future_idx):
            np.testing.assert_array_almost_equal(feats[i], feats2[i], decimal=6)

    def test_loss_rate_windows(self):
        rows = _synth_series(n=40, loss_rate=0.5, seed=3)
        sm = build_series_features(rows)
        key = next(iter(sm))
        feats = sm[key]["features"]
        self.assertTrue(np.all((feats[:, 4] >= 0.0) & (feats[:, 4] <= 1.0)))
        self.assertTrue(np.all((feats[:, 5] >= 0.0) & (feats[:, 5] <= 1.0)))

    def test_normaliser_fit_transform_roundtrip(self):
        data = np.random.RandomState(0).randn(50, 10).astype(np.float32) * 10 + 50
        normaliser = Normaliser.fit(data)
        self.assertEqual(len(normaliser.mean), 10)
        self.assertEqual(len(normaliser.std), 10)
        transformed = normaliser.transform(data)
        for col in range(10):
            col_std = float(np.std(data[:, col]))
            if col_std > 1e-6:
                self.assertAlmostEqual(float(transformed[:, col].mean()), 0.0, places=3)
                self.assertAlmostEqual(float(transformed[:, col].std()), 1.0, places=3)

    def test_normaliser_handles_constant_columns(self):
        data = np.ones((20, 10), dtype=np.float32) * 3.14
        normaliser = Normaliser.fit(data)
        transformed = normaliser.transform(data)
        self.assertTrue(np.allclose(transformed, 0.0, atol=1e-6))
        for s in normaliser.std:
            self.assertGreaterEqual(s, 1.0)

    def test_build_targets_structure(self):
        rows = _synth_series(n=50)
        targets = build_targets(rows, end=10, horizons=(1, 3))
        self.assertEqual(len(targets), 10)  # 2 horizons * 5 values
        future_rtt = rows[10 + 1].get("rtt_ms")
        self.assertEqual(targets[4], 1.0 if future_rtt is None else 0.0)

    def test_build_examples_rejects_short_series(self):
        rows = _synth_series(n=10)
        sm = build_series_features(rows)
        examples = build_examples(sm, seq_len=16, horizons=(1, 2, 3))
        self.assertEqual(len(examples), 0)

    def test_examples_have_required_fields(self):
        rows = _synth_series(n=200)
        sm = build_series_features(rows)
        examples = build_examples(sm, seq_len=16, horizons=(1, 2, 3))
        self.assertGreater(len(examples), 0)
        ex = examples[0]
        for field in ("x", "targets", "timestamp", "series_id", "source",
                      "measurement_id", "probe_id", "horizon_seconds"):
            self.assertIn(field, ex)
        self.assertEqual(ex["x"].shape, (16, 10))
        self.assertEqual(len(ex["targets"]), 15)  # 3 * 5

    def test_targets_have_correct_length(self):
        rows = _synth_series(n=200)
        sm = build_series_features(rows)
        examples = build_examples(sm, seq_len=16, horizons=(1, 3, 6))
        ex = examples[0]
        self.assertEqual(len(ex["targets"]), 15)  # 3 * 5


if __name__ == "__main__":
    unittest.main()