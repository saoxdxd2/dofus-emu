#!/usr/bin/env python3
"""Split tests: leakage resistance, no shared series, no target bleed."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from networkhealth.features import build_examples, build_series_features
from networkhealth.splits import SplitConfig, build_split, compact_summary, describe_split


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


class TestSplits(unittest.TestCase):
    def test_time_split_no_target_bleed(self):
        rows = _synth_series(n=300, interval=60)
        sm = build_series_features(rows)
        examples = _make_examples(sm)
        config = SplitConfig(mode="time", valid_fraction=0.2,
                             purge_seconds=3600, gap_seconds=1800)
        result = build_split(examples, config)
        self.assertGreater(len(result.train_idx), 0)
        self.assertGreater(len(result.valid_idx), 0)
        overlap = set(result.train_idx) & set(result.valid_idx)
        self.assertEqual(overlap, set())

    def test_series_split_no_shared_series(self):
        rows_a = _synth_series(measurement_id=100, probe_id=200, n=200, seed=1)
        rows_b = _synth_series(measurement_id=101, probe_id=201, n=200, seed=2)
        rows_c = _synth_series(measurement_id=102, probe_id=202, n=200, seed=3)
        sm = build_series_features(rows_a + rows_b + rows_c)
        examples = _make_examples(sm)
        config = SplitConfig(mode="series", valid_fraction=0.33)
        result = build_split(examples, config)
        train_series = {examples[i].get("series_id") for i in result.train_idx}
        valid_series = {examples[i].get("series_id") for i in result.valid_idx}
        self.assertEqual(train_series & valid_series, set())

    def test_time_and_series_split_strictest(self):
        rows_a = _synth_series(measurement_id=100, probe_id=200, n=300, seed=1)
        rows_b = _synth_series(measurement_id=101, probe_id=201, n=300, seed=2)
        rows_c = _synth_series(measurement_id=102, probe_id=202, n=300, seed=3)
        sm = build_series_features(rows_a + rows_b + rows_c)
        examples = _make_examples(sm)
        config = SplitConfig(mode="time_and_series", valid_fraction=0.25,
                             purge_seconds=7200, gap_seconds=3600)
        result = build_split(examples, config)
        train_series = {examples[i].get("series_id") for i in result.train_idx}
        valid_series = {examples[i].get("series_id") for i in result.valid_idx}
        self.assertEqual(train_series & valid_series, set())

    def test_compact_summary_has_no_index_lists(self):
        rows = _synth_series(n=200)
        sm = build_series_features(rows)
        examples = _make_examples(sm)
        config = SplitConfig(mode="time_and_series", valid_fraction=0.2)
        result = build_split(examples, config)
        summary = compact_summary(result, examples)
        self.assertNotIn("train_index", summary)
        self.assertNotIn("valid_index", summary)
        self.assertIn("train_examples", summary)
        self.assertIn("valid_examples", summary)
        self.assertIn("shared_series", summary)

    def test_describe_split_reports_counts(self):
        rows = _synth_series(n=200)
        sm = build_series_features(rows)
        examples = _make_examples(sm)
        config = SplitConfig(mode="time", valid_fraction=0.2)
        result = build_split(examples, config)
        desc = describe_split(examples, result)
        self.assertEqual(desc["train_examples"], len(result.train_idx))
        self.assertEqual(desc["valid_examples"], len(result.valid_idx))
        # time mode CAN share series between train and valid
        self.assertIsInstance(desc["shared_series"], int)


if __name__ == "__main__":
    unittest.main()