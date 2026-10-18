#!/usr/bin/env python3
"""Baselines tests: finite metrics, all baseline types compute."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import networkhealth.baselines as baseline_lib
from networkhealth.features import build_examples, build_series_features


def _synth_series(measurement_id=100, probe_id=200, n=200, interval=60, seed=1):
    import random
    rng = random.Random(seed)
    from datetime import datetime, timezone
    base = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
    rows = []
    rtt = 30.0
    t = base
    for i in range(n):
        lost = rng.random() < 0.05
        rtt_val = None if lost else round(max(5.0, rtt + rng.uniform(-3, 3)), 3)
        rtt = rtt_val if rtt_val is not None else rtt
        rows.append({
            "source": "synthetic", "measurement_id": measurement_id,
            "probe_id": probe_id, "timestamp": t,
            "rtt_ms": rtt_val, "received": 0 if lost else 1, "sent": 1,
        })
        t += interval + rng.randint(-2, 2)
    return rows


def _make_examples(sm, seq_len=16):
    return build_examples(sm, seq_len=seq_len, horizons=(1, 2, 3))


class TestBaselines(unittest.TestCase):
    def test_persistence_forecasts_are_finite(self):
        rows = _synth_series(n=200, seed=5)
        sm = build_series_features(rows)
        examples = _make_examples(sm)
        from networkhealth.splits import SplitConfig, build_split
        config = SplitConfig(mode="time", valid_fraction=0.2)
        result = build_split(examples, config)
        forecasts = baseline_lib.compute_baselines(sm, result.valid_idx, examples)
        persistence = forecasts["persistence"]
        # Check all horizons that have values are finite
        for key in ("1", "2", "3"):
            values = persistence.rtt_log.get(key, [])
            if values:  # only check if there are values
                for v in values:
                    self.assertTrue(math.isfinite(v), f"non-finite persistence value {v} at horizon {key}")
        # At least some horizon should have values
        total = sum(len(persistence.rtt_log.get(k, [])) for k in ("1", "2", "3"))
        self.assertGreater(total, 0, "no persistence values at any horizon")

    def test_all_baselines_compute(self):
        rows = _synth_series(n=200, seed=5)
        sm = build_series_features(rows)
        examples = _make_examples(sm)
        from networkhealth.splits import SplitConfig, build_split
        config = SplitConfig(mode="time", valid_fraction=0.2)
        result = build_split(examples, config)
        forecasts = baseline_lib.compute_baselines(sm, result.valid_idx, examples)
        for name in ("persistence", "moving_average", "ewma", "seasonal_naive", "loss_persistence"):
            self.assertIn(name, forecasts)
            bf = forecasts[name]
            for key in ("1", "2", "3"):
                vals = bf.rtt_log.get(key, [])
                for v in vals:
                    self.assertTrue(math.isfinite(v))

    def test_baseline_metrics_are_finite(self):
        rows = _synth_series(n=200, seed=5)
        sm = build_series_features(rows)
        examples = _make_examples(sm)
        from networkhealth.splits import SplitConfig, build_split
        config = SplitConfig(mode="time", valid_fraction=0.2)
        result = build_split(examples, config)
        forecasts = baseline_lib.compute_baselines(sm, result.valid_idx, examples)

        persistence = forecasts["persistence"]
        rtt_true, rtt_mask, loss_true, loss_prob = {}, {}, {}, {}
        for h in (1, 2, 3):
            hk = str(h)
            rtt_true[hk] = []
            rtt_mask[hk] = []
            loss_true[hk] = []
            loss_prob[hk] = []
        for h in (1, 2, 3):
            hk = str(h)
            n = len(persistence.rtt_log.get(hk, []))
            rtt_true[hk] = [0.0] * n
            rtt_mask[hk] = [1.0] * n
            loss_true[hk] = [0.0] * n
            loss_prob[hk] = persistence.loss_prob.get(hk, [0.0] * n)

        bundle = baseline_lib.compute_metrics(
            persistence.rtt_log, rtt_true, rtt_mask,
            persistence.jitter_log, persistence.loss_prob, loss_true,
        )
        for hk in ("1", "2", "3"):
            block = bundle.per_horizon.get(hk, {})
            for metric in ("rtt_log_mae", "rtt_log_rmse", "rtt_log_bias",
                           "jitter_log_mae", "loss_accuracy", "loss_event_rate"):
                val = block.get(metric)
                if val is not None:
                    self.assertTrue(math.isfinite(val), f"{hk}.{metric} = {val}")


if __name__ == "__main__":
    unittest.main()