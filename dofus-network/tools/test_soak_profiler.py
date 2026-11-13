"""Unit tests for the 8-Instance Headless Soak & Profiler."""

import importlib.util
import json
import os
import platform
import tempfile
import time
import unittest
from pathlib import Path
import urllib.request

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOAK_PATH = _REPO_ROOT / "tools" / "soak_profiler.py"
_spec = importlib.util.spec_from_file_location("tools.soak_profiler", _SOAK_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load soak_profiler from {_SOAK_PATH}")
_soak_module = importlib.util.module_from_spec(_spec)
import sys
sys.modules["tools.soak_profiler"] = _soak_module
_spec.loader.exec_module(_soak_module)

LocalMockServer = _soak_module.LocalMockServer
compute_linear_regression = _soak_module.compute_linear_regression
generate_markdown_report = _soak_module.generate_markdown_report
get_process_win32_stats = _soak_module.get_process_win32_stats
parse_duration = _soak_module.parse_duration


class TestSoakProfilerMath(unittest.TestCase):
    def test_duration_parsing(self):
        self.assertEqual(parse_duration("30s"), 30)
        self.assertEqual(parse_duration("5m"), 300)
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("45"), 45)

    def test_linear_regression_flat(self):
        timestamps = [float(i) for i in range(20)]
        values = [25.0 + (0.1 if i % 2 == 0 else -0.1) for i in range(20)]
        res = compute_linear_regression(timestamps, values, leak_rate_threshold_per_sec=0.001, min_delta=5.0)
        self.assertFalse(res.is_leak)
        self.assertAlmostEqual(res.slope, 0.0, places=2)
        self.assertLess(res.delta, 1.0)

    def test_linear_regression_leaking(self):
        timestamps = [float(i) for i in range(20)]
        values = [10.0 + 0.5 * t for t in timestamps]
        res = compute_linear_regression(
            timestamps, values, leak_rate_threshold_per_sec=0.01, min_delta=5.0, r2_threshold=0.85
        )
        self.assertTrue(res.is_leak)
        self.assertAlmostEqual(res.slope, 0.5, places=3)
        self.assertGreaterEqual(res.r_squared, 0.99)
        self.assertEqual(res.delta, 9.5)


class TestSoakProfilerComponents(unittest.TestCase):
    def test_win32_process_inspection(self):
        if platform.system() != "Windows":
            self.skipTest("Win32 tests only run on Windows")
        stats = get_process_win32_stats(os.getpid())
        self.assertIsNotNone(stats)
        self.assertGreater(stats.private_ws_mb, 0.0)
        self.assertGreater(stats.handles, 0)

    def test_mock_server_serving(self):
        server = LocalMockServer(port=0)
        server.start()
        try:
            port = server.port
            url_base = f"http://127.0.0.1:{port}"

            # Check health
            with urllib.request.urlopen(f"{url_base}/__health", timeout=2.0) as r:
                self.assertEqual(r.status, 200)
                body = json.loads(r.read().decode("utf-8"))
                self.assertEqual(body.get("status"), "ok")

            # Check script.js
            with urllib.request.urlopen(f"{url_base}/build/script.js", timeout=2.0) as r:
                self.assertEqual(r.status, 200)
                content = r.read()
                self.assertIn(b"Dofus Touch Mock Client Bundle", content)

            # Check sprites.png
            with urllib.request.urlopen(f"{url_base}/assets/sprites.png", timeout=2.0) as r:
                self.assertEqual(r.status, 200)
                self.assertGreater(len(r.read()), 1024)

            # Check game data
            with urllib.request.urlopen(f"{url_base}/game/data.json", timeout=2.0) as r:
                self.assertEqual(r.status, 200)
                data = json.loads(r.read().decode("utf-8"))
                self.assertEqual(data.get("map_id"), 84674566)
        finally:
            server.stop()

    def test_markdown_report_formatting(self):
        scorecard = {
            "status": "success",
            "passed": True,
            "duration_sec": 30.0,
            "slots_count": 8,
            "sample_count": 30,
            "budgets": {
                "ram_per_instance_mb": {"limit": 200.0, "measured_peak": 24.5, "passed": True},
                "combat_latency_p99_ms": {"limit": 10.0, "measured_peak": 4.2, "passed": True},
                "uidispatcher_dropped": {"limit": 0, "measured": 0, "samples_evaluated": 240, "passed": True},
            },
            "leak_slopes": {
                "csharp_pws_total": {
                    "slope_per_sec": 0.0001, "rate_per_min": 0.006, "r_squared": 0.12,
                    "delta": 0.2, "min_val": 180.0, "max_val": 180.2, "is_leak": False,
                }
            },
            "per_slot_peak_pws_mb": {f"dofus-{i+1:02d}": 24.5 for i in range(8)},
            "combat_profile": {
                "latency": {"count": 100, "min": 0.5, "p50": 0.8, "p95": 1.9, "p99": 4.2, "max": 5.1},
                "top_allocators": [{"filename": "live_policy.py", "lineno": 120, "size_bytes": 4096, "count": 10}],
            },
        }

        with tempfile.TemporaryDirectory() as td:
            out_md = Path(td) / "test_report.md"
            report_text = generate_markdown_report(scorecard, out_md)
            self.assertTrue(out_md.exists())
            self.assertIn("# 8-Instance Headless Soak & Memory/Latency Profile Report", report_text)
            self.assertIn("**PASSED**", report_text)
            self.assertIn("`< 200.0 MB`", report_text)
            self.assertIn("`24.5 MB`", report_text)


if __name__ == "__main__":
    unittest.main()
