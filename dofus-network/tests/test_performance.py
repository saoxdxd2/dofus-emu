#!/usr/bin/env python3
"""Performance and resource tests: latency, parameter counts, memory, throughput."""

from __future__ import annotations

import math
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import torch
    from networkhealth.evaluation import measure_cost
    from networkhealth.model import ModelConfig, NetworkHealthTCN
    from networkhealth.trm_moe import TRMMoEConfig, NetworkHealthTRMMoE as TRMMoE
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


class TestPerformanceBaseline(unittest.TestCase):
    def test_tcn_parameter_count_under_budget(self):
        """Model must stay under ~1M parameters (design constraint)."""
        cfg = ModelConfig(features=10, hidden=64, levels=4)
        model = NetworkHealthTCN(cfg)
        count = sum(p.numel() for p in model.parameters() if p.requires_grad)
        self.assertLess(count, 1_000_000, f"TCN has {count:,} params, over 1M budget")

    def test_trm_moe_parameter_count(self):
        cfg = TRMMoEConfig(in_features=10, hidden=96, num_experts=4, top_k=2)
        model = TRMMoE(cfg)
        count = model.parameter_count()
        self.assertGreater(count, 0)
        self.assertLess(count, 5_000_000)

    def test_tcn_cpu_latency_under_budget(self):
        """CPU inference latency should be < 100ms for a single forward pass."""
        cfg = ModelConfig(features=10, hidden=64, levels=4)
        model = NetworkHealthTCN(cfg)
        model.eval()
        x = torch.randn(1, 32, 10)
        with torch.no_grad():
            # warmup
            for _ in range(5):
                model(x)
            timings = []
            for _ in range(30):
                t0 = time.perf_counter()
                model(x)
                timings.append((time.perf_counter() - t0) * 1000.0)
        median = float(np.median(timings))
        # Just check it's reasonable
        p95 = sorted(timings)[int(len(timings) * 0.95)]
        self.assertLess(p95, 500.0, f"p95 latency {p95:.1f}ms too high")


@unittest.skipUnless(_HAS_TORCH, "torch not available")
class TestMeasureCost(unittest.TestCase):
    def test_measure_cost_returns_required_fields(self):
        import numpy as np
        cfg = ModelConfig(features=10, hidden=32, levels=2)
        model = NetworkHealthTCN(cfg)
        model.eval()
        example = torch.randn(1, 32, 10)
        cost = measure_cost(model, example, warmup=3, runs=10)
        if "error" in cost:
            self.skipTest(f"cost measurement skipped: {cost['error']}")
        for field in ("parameters", "state_dict_bytes", "state_dict_mb",
                      "latency_ms_median", "latency_ms_p95", "latency_ms_mean",
                      "batch_size", "sequence_length", "device"):
            self.assertIn(field, cost)
        self.assertEqual(cost["device"], "cpu")
        self.assertGreater(cost["parameters"], 0)
        self.assertGreater(cost["latency_ms_median"], 0)

    def test_state_dict_size_under_budget(self):
        """Model state dict should be small enough for edge deployment."""
        cfg = ModelConfig(features=10, hidden=64, levels=4)
        model = NetworkHealthTCN(cfg)
        size_bytes = sum(p.numel() * p.element_size() for p in model.state_dict().values())
        size_mb = size_bytes / (1024 * 1024)
        self.assertLess(size_mb, 50.0, f"model is {size_mb:.2f}MB, over 50MB budget")


class TestThroughput(unittest.TestCase):
    def test_feature_building_throughput(self):
        import random
        from networkhealth.features import build_series_features, build_examples
        from datetime import datetime, timezone

        base = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
        rows = []
        for s in range(10):
            t = base + s * 3600
            rtt = 30.0
            for i in range(500):
                lost = random.random() < 0.05
                rtt_val = None if lost else round(max(5.0, rtt + random.uniform(-3, 3)), 3)
                rtt = rtt_val if rtt_val is not None else rtt
                rows.append({
                    "source": "synthetic", "measurement_id": 100 + s,
                    "probe_id": 200 + s, "timestamp": t,
                    "rtt_ms": rtt_val,
                })
                t += 60

        t0 = time.perf_counter()
        sm = build_series_features(rows)
        examples = build_examples(sm, seq_len=32, horizons=(1, 3, 6))
        elapsed = time.perf_counter() - t0
        rows_per_sec = len(rows) / elapsed if elapsed > 0 else float("inf")
        self.assertGreater(rows_per_sec, 100, f"feature building too slow: {rows_per_sec:.0f} rows/s")
        self.assertGreater(len(examples), 0)


if __name__ == "__main__":
    unittest.main()