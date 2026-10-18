#!/usr/bin/env python3
"""Cleaning tests: preserve failures, drop bad rows, flag outliers."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from networkhealth.clean import clean_rows


class TestClean(unittest.TestCase):
    def test_preserves_failure_rows(self):
        rows = [
            {"source": "s", "measurement_id": 1, "probe_id": 2, "timestamp": 1000, "rtt_ms": None},
            {"source": "s", "measurement_id": 1, "probe_id": 2, "timestamp": 1001, "rtt_ms": 30.0},
        ]
        cleaned, report = clean_rows(rows, window_start=900, window_stop=2000)
        self.assertEqual(report.failures_preserved, 1)
        self.assertEqual(len(cleaned), 2)
        self.assertIsNone(cleaned[0]["rtt_ms"])
        self.assertTrue(cleaned[0]["is_failure"])

    def test_drops_implausible_rtt(self):
        rows = [
            {"source": "s", "measurement_id": 1, "probe_id": 2, "timestamp": 1000, "rtt_ms": -5.0},
            {"source": "s", "measurement_id": 1, "probe_id": 2, "timestamp": 1001, "rtt_ms": 1e12},
            {"source": "s", "measurement_id": 1, "probe_id": 2, "timestamp": 1002, "rtt_ms": 30.0},
        ]
        cleaned, report = clean_rows(rows, window_start=900, window_stop=2000)
        # Implausible RTT rows are converted to failures (rtt=None), not dropped entirely.
        self.assertEqual(report.dropped_implausible_rtt, 2)
        self.assertEqual(len(cleaned), 3)
        self.assertIsNone(cleaned[0]["rtt_ms"])
        self.assertIsNone(cleaned[1]["rtt_ms"])
        self.assertEqual(cleaned[2]["rtt_ms"], 30.0)

    def test_drops_duplicate_rows(self):
        row = {"source": "s", "measurement_id": 1, "probe_id": 2, "timestamp": 1000, "rtt_ms": 30.0}
        rows = [dict(row), dict(row)]
        cleaned, report = clean_rows(rows, window_start=900, window_stop=2000)
        self.assertEqual(report.dropped_duplicate, 1)
        self.assertEqual(len(cleaned), 1)

    def test_drops_bad_timestamps(self):
        rows = [
            {"source": "s", "measurement_id": 1, "probe_id": 2, "timestamp": -1, "rtt_ms": 30.0},
            {"source": "s", "measurement_id": 1, "probe_id": 2, "timestamp": "not-a-number", "rtt_ms": 30.0},
            {"source": "s", "measurement_id": 1, "probe_id": 2, "timestamp": 1000, "rtt_ms": 30.0},
        ]
        cleaned, report = clean_rows(rows, window_start=900, window_stop=2000)
        self.assertEqual(len(cleaned), 1)
        self.assertGreater(report.dropped_bad_timestamp + report.dropped_out_of_window, 0)

    def test_flags_outliers_but_keeps_plausible(self):
        rows = [
            {"source": "s", "measurement_id": 1, "probe_id": 2, "timestamp": 1000, "rtt_ms": 30.0},
            {"source": "s", "measurement_id": 1, "probe_id": 2, "timestamp": 1001, "rtt_ms": 8000.0},
        ]
        cleaned, report = clean_rows(rows, window_start=900, window_stop=2000, plausible_rtt_ms=5000.0)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(report.flagged_outliers, 1)
        self.assertTrue(cleaned[1]["is_outlier"])

    def test_report_is_json_serialisable(self):
        import random
        rows = []
        rng = random.Random(1)
        base = 1000
        rtt = 30.0
        for i in range(20):
            lost = rng.random() < 0.05
            rtt_val = None if lost else round(max(5.0, rtt + rng.uniform(-3, 3)), 3)
            rtt = rtt_val if rtt_val is not None else rtt
            rows.append({
                "source": "s", "measurement_id": 1, "probe_id": 2,
                "timestamp": base + i, "rtt_ms": rtt_val,
            })
        cleaned, report = clean_rows(rows, window_start=0, window_stop=10**12)
        json.dumps(report.as_dict())


if __name__ == "__main__":
    unittest.main()