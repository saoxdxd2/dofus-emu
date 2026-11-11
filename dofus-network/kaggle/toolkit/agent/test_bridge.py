"""A loopback call must not be handed to HTTP_PROXY.

The fault this file pins was found three times in the same tree, each time from the
symptom rather than the cause: a directive sent to the bridge went through a local
proxy and came back 502; the network-health collector asked a proxy to fetch a
loopback port; and finally the regression gate announced "the combat brain does not
start on the interpreter that ships" while that brain was answering `status: ok` on
the very port it had just probed. The measured cause was the same each time --
`urllib.request.urlopen` honours `HTTP_PROXY`, and on a Windows machine with a VPN or
proxy client installed that variable is set machine-wide.

The last one is why this is a test and not a comment. A gate that reports the
harness's own fault as a product failure is worse than no gate: it sends someone to
debug working code, and it teaches everyone to ignore the check.

Two properties, deliberately different in kind:

* `ProxyIgnoredTests` is behavioural. It stands up a real loopback server, points
  `HTTP_PROXY` at a port with nothing behind it, and asks the two product probes that
  decide "is the brain alive" whether they still answer. On the pre-fix code the
  request goes to the dead proxy and the probe returns None -- a live brain declared
  dead, which is what makes `ensure_brain` respawn one.
* `NoBareLoopbackUrlopenTests` is structural. It parses every module in the tree and
  fails on a `urlopen` whose literal argument is a loopback address, because the
  behavioural test can only cover the call sites that existed when it was written.
"""

from __future__ import annotations

import ast
import json
import os
import socket
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from agent.bridge import local_opener, opener_for

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories that are not this project's source: a vendored environment, the
# generated Kaggle mirror (checked at its source instead), and build residue.
SKIP_DIRS = {".venv", "venv", "kaggle", "__pycache__", ".git", "node_modules", "build"}

LOOPBACK_PREFIXES = ("http://127.0.0.1", "http://localhost", "http://[::1]")


def _dead_port() -> int:
    """A port that is bound and then released, so nothing is listening on it.

    Racy in principle -- the OS may hand the port to someone else -- and accepted:
    the test only needs the proxy target to refuse connections, and a refused
    connection is what the old code produced in production too.
    """
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _HealthHandler(BaseHTTPRequestHandler):
    body = b'{"status": "ok", "model": "loopback-test"}'

    def do_GET(self):                      # noqa: N802 - the stdlib's spelling
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):          # keep the suite's output readable
        pass


class ProxyIgnoredTests(unittest.TestCase):
    """The probes that answer "is the brain alive" must survive a proxy env."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        # The discriminating condition. `NO_PROXY` naming 127.0.0.1 would let the
        # broken code pass, so it is removed for the duration of the test and put
        # back afterwards -- a test that only fails on machines without a VPN is
        # not a test.
        self._saved = {key: os.environ.get(key) for key in
                       ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                        "NO_PROXY", "no_proxy", "DOFUS_BRAIN_HOST")}
        for key in ("NO_PROXY", "no_proxy"):
            os.environ.pop(key, None)
        dead = "http://127.0.0.1:%d" % _dead_port()
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ[key] = dead
        # `brain_is_up` reads this; a machine that points it elsewhere would otherwise
        # make the behavioural assertions below test the wrong host.
        os.environ["DOFUS_BRAIN_HOST"] = "127.0.0.1"

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_the_premise_a_bare_urlopen_really_does_go_to_the_dead_proxy(self):
        """Without this the rest of the class could pass for the wrong reason.

        If `urlopen` stopped honouring `HTTP_PROXY`, every assertion below would hold
        on broken code and the file would certify nothing. A *freshly built* opener is
        used on purpose: `_ENV_OPENER` was constructed at import, before this test set
        the variable, so asking it would test the process's start-up environment
        rather than the mechanism.
        """
        default = urllib.request.build_opener()
        handler = next(h for h in default.handlers
                       if isinstance(h, urllib.request.ProxyHandler))
        self.assertIn("127.0.0.1", handler.proxies.get("http", ""),
                      "the environment proxy was not picked up; this class proves nothing")
        # The live loopback server is up, and a bare opener still cannot reach it.
        with self.assertRaises((urllib.error.URLError, OSError)):
            default.open("http://127.0.0.1:%d/health" % self.port, timeout=3)

    def test_a_local_opener_reaches_the_loopback_server(self):
        with local_opener().open("http://127.0.0.1:%d/health" % self.port,
                                 timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(json.loads(resp.read())["model"], "loopback-test")

    def test_opener_for_picks_direct_for_loopback_and_env_for_the_rest(self):
        from agent import bridge

        self.assertIs(opener_for("http://127.0.0.1:8891/health"), bridge._LOCAL_OPENER)
        self.assertIs(opener_for("http://localhost:8891/health"), bridge._LOCAL_OPENER)
        self.assertIs(opener_for("https://api.dofusdb.fr/monsters"), bridge._ENV_OPENER)
        # A host that merely starts with the loopback text is not loopback.
        self.assertIs(opener_for("http://127.0.0.1.evil.test/"), bridge._ENV_OPENER)
        self.assertIs(opener_for("not a url"), bridge._ENV_OPENER)

    def test_brain_is_up_answers_through_a_proxy_environment(self):
        """The product path: a live brain must not be declared dead."""
        from combat import brain_service

        self.assertEqual(brain_service.brain_is_up(self.port, timeout=3), "loopback-test")

    def test_brain_identity_answers_through_a_proxy_environment(self):
        from combat import brain_service

        body = brain_service.brain_identity(self.port, timeout=3)
        self.assertIsInstance(body, dict)
        self.assertEqual(body.get("status"), "ok")


class NoBareLoopbackUrlopenTests(unittest.TestCase):
    """A new loopback call site must use `opener_for`, not `urlopen`."""

    def _literal_prefix(self, node: ast.AST) -> str:
        """The literal start of an argument, for a str constant or an f-string."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr) and node.values:
            head = node.values[0]
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                return head.value
        return ""

    def test_no_module_sends_a_loopback_url_through_urlopen(self):
        offenders = []
        for path in REPO_ROOT.rglob("*.py"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:            # a deliberately broken fixture, not ours
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name != "urlopen" or not node.args:
                    continue
                if self._literal_prefix(node.args[0]).startswith(LOOPBACK_PREFIXES):
                    offenders.append("%s:%d" % (path.relative_to(REPO_ROOT), node.lineno))
        self.assertEqual(offenders, [], "use agent.bridge.opener_for for these")


if __name__ == "__main__":
    unittest.main(verbosity=2)
