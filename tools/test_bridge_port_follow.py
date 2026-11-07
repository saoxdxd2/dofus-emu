"""The executor must follow the bridge when the host republishes its port.

Measured 2026-11-06: the host walks `CANDIDATE_PORTS` (8893, 8894, ... 8899) and rewrites
`bridge_port.txt` whenever it moves -- which it does whenever a leaked socket holds the previous
port. The executor read the port once at construction, so a move mid-session turned every later
directive into "not reachable": three `ui.tap` steps failed against 8896 while the host was
answering on 8894, the observation went stale (`src=carried`), and the session ended with the
policy having no call.

The fix is one re-resolve, only when the published port actually changed, and only once -- a retry
loop against a dead host would just spend the step budget differently.

    python tools/test_bridge_port_follow.py
"""
from __future__ import annotations

import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "dofus-network"))

from agent import executor as executor_module  # noqa: E402
from agent.executor import ToolExecutor  # noqa: E402


class BridgePortFollow(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.published = "http://127.0.0.1:8896/directive"
        self.live = "http://127.0.0.1:8894/directive"
        self._real = executor_module.bridge_directive_url
        executor_module.bridge_directive_url = lambda: self.published
        self.executor = ToolExecutor(directive_url="http://127.0.0.1:8896/directive",
                                     timeout=1.0)

    def tearDown(self):
        executor_module.bridge_directive_url = self._real

    def _fake_http(self, ok_url):
        def call(method, url, body=None):
            self.calls.append(url)
            if url == ok_url:
                return True, {"ok": True, "result": {"ok": True, "data": {"connected": True}}}
            return False, "unreachable: %s" % url
        return call

    def test_follows_a_moved_port(self):
        """The host moved to 8894 while we held 8896: one retry, and the new port sticks."""
        self.published = self.live
        self.executor._http_json = self._fake_http(self.live)
        result = self.executor._directive("state.snapshot", "state.snapshot", {}, "test")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(self.calls, ["http://127.0.0.1:8896/directive",
                                      "http://127.0.0.1:8894/directive"])
        self.assertTrue(result.data.get("port_recovered"))
        self.assertEqual(self.executor.directive_url, self.live)
        # The next call must go straight to the new port, not retry again.
        self.calls.clear()
        self.executor._directive("state.snapshot", "state.snapshot", {}, "test")
        self.assertEqual(self.calls, [self.live])

    def test_does_not_retry_when_the_port_did_not_move(self):
        """A wedged page is not a moved port: one attempt, then the failure is reported."""
        self.published = "http://127.0.0.1:8896/directive"
        self.executor._http_json = self._fake_http("http://127.0.0.1:1/never")
        result = self.executor._directive("state.snapshot", "state.snapshot", {}, "test")
        self.assertFalse(result.ok)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("not reachable", result.error or "")

    def test_a_dead_new_port_keeps_the_old_url(self):
        """If the republished port is also dead, do not leave the executor pointing at it."""
        self.published = "http://127.0.0.1:8895/directive"
        self.executor._http_json = self._fake_http("http://127.0.0.1:1/never")
        result = self.executor._directive("state.snapshot", "state.snapshot", {}, "test")
        self.assertFalse(result.ok)
        self.assertEqual(len(self.calls), 2)          # old port, then the republished one
        self.assertEqual(self.executor.directive_url, "http://127.0.0.1:8896/directive")


if __name__ == "__main__":
    unittest.main(verbosity=2)
