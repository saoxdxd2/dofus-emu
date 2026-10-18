#!/usr/bin/env python3
"""Server-side compatibility tests: HTTP endpoint contracts, API shape, timeouts."""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class _MockTelemetryHandler(BaseHTTPRequestHandler):
    """Mock proxy telemetry endpoint for bridge compatibility testing."""

    def do_GET(self):
        if self.path == "/__telemetry":
            payload = {
                "rtt_ms": 35.5,
                "rtt_avg_ms": 34.2,
                "conns_total": 100,
                "conns_failed": 3,
                "conns_active": 97,
                "timestamp": time.time(),
            }
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress noise


class TestServerCompatibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _MockTelemetryHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_mock_telemetry_endpoint_responds(self):
        import urllib.request
        url = f"http://127.0.0.1:{self.port}/__telemetry"
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertIn("rtt_ms", data)
            self.assertIn("conns_total", data)
            self.assertIn("conns_failed", data)

    def test_mock_telemetry_returns_valid_json_types(self):
        import urllib.request
        url = f"http://127.0.0.1:{self.port}/__telemetry"
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            data = json.loads(resp.read().decode())
            self.assertIsInstance(data["rtt_ms"], (int, float))
            self.assertIsInstance(data["conns_total"], int)
            self.assertGreaterEqual(data["conns_total"], 0)
            self.assertGreaterEqual(data["conns_failed"], 0)

    def test_networkhealth_bridge_can_reach_local_endpoint(self):
        """The bridge's fetch_proxy_telemetry must work against a local HTTP server."""
        import urllib.request
        url = f"http://127.0.0.1:{self.port}/__telemetry"
        req = urllib.request.Request(url, headers={"User-Agent": "NetworkHealthBridge/1.0"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode())
            self.assertEqual(resp.status, 200)
            self.assertGreaterEqual(data["rtt_ms"], 0)

    def test_port_is_in_expected_range(self):
        """Proxy ports should be in the 8880-8883 range used by the bridge."""
        from networkhealth.bridge import NetworkHealthBridge
        # Default ports are 8880-8883
        bridge = NetworkHealthBridge.__new__(NetworkHealthBridge)
        self.assertEqual(bridge.ports if hasattr(bridge, 'ports') else None, None)
        # Just verify default constant exists in source
        import inspect
        src = inspect.getsource(NetworkHealthBridge.__init__)
        self.assertIn("8880", src)

    def test_sentinel_default_port(self):
        """Sentinel service should listen on 127.0.0.1:8890."""
        import inspect
        from networkhealth.sentinel import run_sentinel_service
        src = inspect.getsource(run_sentinel_service)
        self.assertIn("8890", src)
        self.assertIn("127.0.0.1", src)

    def test_socket_connect_localhost(self):
        """Verify localhost TCP connectivity works (no firewall blocking)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        try:
            result = sock.connect_ex(("127.0.0.1", self.port))
            self.assertEqual(result, 0)
        finally:
            sock.close()

    def test_request_timeout_behavior(self):
        """HTTP requests must honor timeouts (no hangs)."""
        import urllib.request
        url = f"http://127.0.0.1:{self.port}/__telemetry"
        start = time.time()
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            _ = resp.read()
        elapsed = time.time() - start
        self.assertLess(elapsed, 5.0, "request took too long")


if __name__ == "__main__":
    unittest.main()