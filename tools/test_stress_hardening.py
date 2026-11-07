"""Regression tests for the defects the 2026-11-07 stress run found.

Each test pins one behaviour that was wrong, so the fix cannot silently
regress. The HTTP tests use a real socket against a real
``AgentHTTPServer`` (with a stub runtime): the bugs were about what a client
sees on the wire -- a dropped connection instead of a 400, and a second
response on a connection whose body was never read -- and a mock of
``_send_json`` would not have caught either.

Run directly::

    python tools/test_stress_hardening.py
"""

from __future__ import annotations

import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "dofus-network"))
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# 1. validate_call must reject a non-object `args`, never raise
# ---------------------------------------------------------------------------

class ValidateCallArgsTests(unittest.TestCase):
    """`args = dict(args or {})` raised on every non-dict that is not None."""

    def test_non_dict_args_are_rejected_not_raised(self):
        from agent.schema import validate_call

        bad = ["ab", 42, 4.5, True, False, ["a"], [("a", 1)], b"xy",
               (1, 2), {1, 2}, complex(1, 2), object()]
        for value in bad:
            with self.subTest(args=value):
                ok, message = validate_call("quest.list", value)
                self.assertFalse(ok, "a non-object args must be rejected")
                self.assertIn("must be a JSON object", message)
                self.assertIn(type(value).__name__, message)

    def test_none_and_empty_dict_still_work(self):
        from agent.schema import validate_call

        for value in (None, {}):
            with self.subTest(args=value):
                ok, coerced = validate_call("quest.list", value)
                self.assertTrue(ok, coerced)
                self.assertIsInstance(coerced, dict)

    def test_valid_args_are_still_coerced(self):
        from agent.schema import validate_call

        ok, coerced = validate_call("quest.advance",
                                    {"quest_id": "3841", "accept": True})
        self.assertTrue(ok, coerced)
        self.assertEqual(coerced["quest_id"], 3841)

    def test_unknown_tool_is_a_rejection(self):
        from agent.schema import validate_call

        ok, message = validate_call("definitely.not.a.tool", {})
        self.assertFalse(ok)
        self.assertIn("unknown tool", message)


# ---------------------------------------------------------------------------
# 2. redaction must survive deep nesting and preserve identity
# ---------------------------------------------------------------------------

class RedactionTests(unittest.TestCase):
    """`_redact_copy` recursed once per level and raised at depth ~1000."""

    @staticmethod
    def _nested(depth: int, leaf):
        node = leaf
        for _ in range(depth):
            node = {"n": node}
        return node

    def test_deep_secret_is_redacted_without_recursion_error(self):
        from agent.recorder import _REDACTED, _redact

        for depth in (600, 1000, 2000, 5000):
            with self.subTest(depth=depth):
                out = _redact(self._nested(depth, {"password": "hunter2"}))
                node = out
                for _ in range(depth):
                    node = node["n"]
                self.assertEqual(node["password"], _REDACTED)

    def test_unchanged_subtree_keeps_identity(self):
        from agent.recorder import _redact_copy

        clean = {"inventory": [{"id": i} for i in range(50)]}
        value = {"clean": clean, "password": "x"}
        out = _redact_copy(value)
        self.assertIs(out["clean"], clean,
                      "an untouched subtree must not be rebuilt")

    def test_clean_value_is_returned_unchanged(self):
        from agent.recorder import _redact_copy

        clean = {"a": [1, 2, {"b": "c"}]}
        self.assertIs(_redact_copy(clean), clean)

    def test_url_credentials_are_scrubbed_at_depth(self):
        from agent.recorder import _redact

        value = self._nested(800, {"url": "http://user:pass@example.test/x"})
        out = _redact(value)
        node = out
        for _ in range(800):
            node = node["n"]
        self.assertNotIn("pass", node["url"])
        self.assertIn("[redacted]", node["url"])

    def test_cyclic_graph_does_not_loop(self):
        from agent.recorder import _redact_copy

        value = {"password": "x"}
        value["self"] = value
        out = _redact_copy(value)          # must terminate
        self.assertEqual(out["password"], "[redacted]")


# ---------------------------------------------------------------------------
# 3. RuleGate must bound its miss table
# ---------------------------------------------------------------------------

class RuleGateBoundTests(unittest.TestCase):
    """`_misses` kept every distinct key forever (110k after 200k observations)."""

    def test_miss_table_is_bounded(self):
        from agent.gate import MAX_MISS_KEYS, RuleGate

        gate = RuleGate()
        for i in range(MAX_MISS_KEYS * 3):
            gate.observe("nav.goto", {"map": i}, "failed")
        self.assertLessEqual(len(gate._misses), MAX_MISS_KEYS)
        self.assertGreater(len(gate._misses), MAX_MISS_KEYS // 2,
                           "eviction must not empty the table")

    def test_veto_still_fires_at_the_threshold(self):
        from agent.gate import RuleGate

        gate = RuleGate(veto_at=2)
        args = {"map": 1}
        gate.observe("nav.goto", args, "noop")
        self.assertFalse(gate.shadow("nav.goto", args)["would_veto"])
        gate.observe("nav.goto", args, "noop")
        self.assertTrue(gate.shadow("nav.goto", args)["would_veto"])

    def test_advance_clears_the_streak(self):
        from agent.gate import RuleGate

        gate = RuleGate(veto_at=2)
        args = {"map": 1}
        gate.observe("nav.goto", args, "noop")
        gate.observe("nav.goto", args, "advanced")
        self.assertEqual(gate.streak("nav.goto", args), 0)

    def test_read_only_tools_are_ignored(self):
        from agent.gate import RuleGate
        from agent.verifier import READ_ONLY_TOOLS

        if not READ_ONLY_TOOLS:
            self.skipTest("no read-only tools declared")
        tool = sorted(READ_ONLY_TOOLS)[0]
        gate = RuleGate()
        for _ in range(5):
            gate.observe(tool, {}, "failed")
        self.assertEqual(len(gate._misses), 0)


# ---------------------------------------------------------------------------
# 4. iter_array keeps every complete row before a torn tail
# ---------------------------------------------------------------------------

class IterArrayTornTailTests(unittest.TestCase):
    """Documents the guarantee: a torn tail costs the rows after it, no more."""

    def _write(self, content: str) -> Path:
        path = Path(tempfile.mkdtemp()) / "array.json"
        path.write_text(content, encoding="utf-8")
        return path

    def test_complete_rows_survive_a_torn_tail(self):
        from agent.jsonio import iter_array

        path = self._write('[{"i":1},{"i":2},{"i":3},{"i":4,"broken":')
        for chunk in (16, 64, 4096):
            with self.subTest(chunk=chunk):
                rows = list(iter_array(path, chunk_bytes=chunk))
                self.assertEqual([r["i"] for r in rows], [1, 2, 3])

    def test_non_array_file_yields_nothing(self):
        from agent.jsonio import iter_array

        path = self._write('{"not": "an array"}')
        self.assertEqual(list(iter_array(path)), [])

    def test_closed_array_yields_every_row(self):
        from agent.jsonio import iter_array

        path = self._write('[{"i":1},{"i":2}]')
        self.assertEqual([r["i"] for r in iter_array(path)], [1, 2])


# ---------------------------------------------------------------------------
# 5. the HTTP surface must answer errors, not drop the socket
# ---------------------------------------------------------------------------

class _StubResult:
    def __init__(self, tool, ok=True, error=None):
        self._payload = {"tool": tool, "ok": ok, "data": None, "error": error}

    def as_dict(self):
        return dict(self._payload)


class _StubRecorder:
    def stats(self):
        return {"lines": 0, "ok": 0, "fail": 0, "tools": {}}

    def tail(self, limit):
        return []


class _StubRuntime:
    """Just enough of AgentRuntime for the handler, but with the *real*
    ``validate_call`` in the execute path: the defect being pinned is that a
    non-object ``args`` escaped as an exception, so a stub that always
    succeeds would test nothing."""

    observations_seen = 0

    class _Policy:
        pass

    policy = _Policy()

    def __init__(self):
        self.executor = object()

    def current_observation(self):
        return None, None

    def execute_tool(self, tool, args, instance=None, goal=None, source="live",
                     observation=None):
        from agent.schema import validate_call

        ok, validated = validate_call(tool, args)
        if not ok:
            return _StubResult(tool, ok=False, error=str(validated))
        return _StubResult(tool)

    def handle_goal(self, text, instance=None, observation=None):
        return _StubResult("goal")

    def observe(self, observation, goal=None, source="live"):
        return {"kind": "observation"}


class _ServerMixin(unittest.TestCase):
    handler_timeout = 15
    max_concurrent = None          # None keeps the shipped cap

    def setUp(self):
        import os

        from agent import serve as serve_mod

        # Every loopback call is authenticated now, so the suite must present a
        # secret or it measures the 401 path instead of the behaviour it names.
        # Pinned rather than read from the real file: a test run must not depend
        # on (or touch) %LOCALAPPDATA%\DofusFarm\local_secret.
        self._saved_secret = os.environ.get("DOFUS_RUN_TOKEN")
        os.environ["DOFUS_RUN_TOKEN"] = "test-hardening-secret"

        self._saved_timeout = serve_mod.AgentRequestHandler.timeout
        serve_mod.AgentRequestHandler.timeout = self.handler_timeout
        self._saved_cap = serve_mod.AgentHTTPServer.max_concurrent_requests
        if self.max_concurrent is not None:
            serve_mod.AgentHTTPServer.max_concurrent_requests = self.max_concurrent
        self.server = serve_mod.AgentHTTPServer(
            ("127.0.0.1", 0), _StubRuntime(), _StubRecorder())
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02},
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        import os

        from agent import serve as serve_mod

        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        serve_mod.AgentRequestHandler.timeout = self._saved_timeout
        serve_mod.AgentHTTPServer.max_concurrent_requests = self._saved_cap
        if self._saved_secret is None:
            os.environ.pop("DOFUS_RUN_TOKEN", None)
        else:
            os.environ["DOFUS_RUN_TOKEN"] = self._saved_secret

    def _fix_host(self, payload: bytes) -> bytes:
        """Address the request to this server.

        The plane refuses a `Host` that is not its own authority, and it answers
        that refusal with the same 401 as a missing token. The suite used
        `Host: x` throughout, so once the authority check landed every raw test
        was measuring the refusal, not the behaviour it named.
        """
        line, sep, rest = payload.partition(b"\r\n")
        if not sep:
            return payload
        authority = b"Host: 127.0.0.1:" + str(self.port).encode() + b"\r\n"
        if b"Host: x\r\n" in rest:
            rest = rest.replace(b"Host: x\r\n", authority, 1)
        elif b"Host:" not in rest:
            rest = authority + rest
        return line + b"\r\n" + rest

    @staticmethod
    def _add_bearer(payload: bytes) -> bytes:
        """Insert the run's bearer after the request line."""
        line, sep, rest = payload.partition(b"\r\n")
        if not sep:
            return payload
        return (line + b"\r\n"
                + b"Authorization: Bearer test-hardening-secret\r\n" + rest)

    @staticmethod
    def _fix_content_type(payload: bytes) -> bytes:
        """Give a POST the JSON content type the plane insists on.

        Behind the token, a non-JSON body is refused with 415. These tests are
        about what the handler does with a body -- rejecting it, timing out on
        it, refusing to read an oversized one -- so the type is normalized here
        instead of being repeated in every payload.
        """
        line, sep, rest = payload.partition(b"\r\n")
        if not sep or not line.startswith(b"POST "):
            return payload
        if b"Content-Type:" in rest:
            return payload
        return line + b"\r\nContent-Type: application/json\r\n" + rest

    def _stalled_request(self) -> bytes:
        """An authenticated request whose body never arrives.

        The body has to sit *after* the auth check. Without the header the
        handler answers 401 and frees the slot at once, so a stall test would
        measure nothing while still passing.
        """
        return self._add_bearer(self._fix_content_type(self._fix_host(
            b"POST /agent/execute HTTP/1.1\r\nHost: x\r\n"
            b"Content-Length: 100\r\n\r\n{")))

    def _raw(self, payload: bytes, read_seconds: float = 2.0,
             authenticated: bool = True) -> bytes:
        payload = self._fix_content_type(self._fix_host(payload))
        if authenticated:
            payload = self._add_bearer(payload)
        with socket.create_connection(("127.0.0.1", self.port), timeout=read_seconds) as sock:
            sock.sendall(payload)
            return self._read_response(sock)

    @staticmethod
    def _read_response(sock) -> bytes:
        """Read one HTTP response, stopping at its Content-Length.

        Reading until EOF would block for the whole socket timeout, because
        HTTP/1.1 keeps the connection open after a normal response -- that made
        a 1 ms health reply look like a 3 s one.
        """
        buf = b""
        try:
            while b"\r\n\r\n" not in buf:
                block = sock.recv(65536)
                if not block:
                    return buf
                buf += block
            head, _, body = buf.partition(b"\r\n\r\n")
            length = 0
            for line in head.split(b"\r\n")[1:]:
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1].strip() or 0)
                    break
            while len(body) < length:
                block = sock.recv(65536)
                if not block:
                    break
                body += block
            return head + b"\r\n\r\n" + body
        except (socket.timeout, ConnectionResetError):
            return buf


class HttpErrorResponseTests(_ServerMixin):
    """A bad `args` value used to close the socket with no response at all."""

    def test_non_object_args_get_a_rejection_not_a_dropped_connection(self):
        # Production answers a validation failure as 200 + ok:false, the same
        # shape every other bad-argument call gets. What must never happen is
        # an empty response: before the fix `dict("ab")` raised inside the
        # handler and the socket was closed with nothing written.
        body = b'{"tool":"quest.list","args":"ab"}'
        request = (b"POST /agent/execute HTTP/1.1\r\nHost: x\r\n"
                   b"Content-Type: application/json\r\n"
                   b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        response = self._raw(request)
        self.assertTrue(response, "the server sent nothing: the connection was dropped")
        status = response.split(b"\r\n", 1)[0]
        self.assertIn(b"200", status)
        self.assertIn(b'"ok"', response)
        self.assertIn(b"false", response)
        self.assertIn(b"must be a JSON object", response)

    def test_non_object_args_for_a_typed_tool_also_answer(self):
        # A tool with real parameters, so the rejection goes through the
        # per-parameter path rather than the unknown-tool path.
        body = b'{"tool":"quest.advance","args":[1,2]}'
        request = (b"POST /agent/execute HTTP/1.1\r\nHost: x\r\n"
                   b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        response = self._raw(request)
        self.assertTrue(response, "the connection was dropped")
        self.assertIn(b'"ok"', response)
        self.assertIn(b"false", response)
        self.assertIn(b"must be a JSON object", response)

    def test_bad_json_body_gets_a_400(self):
        body = b"{not json"
        request = (b"POST /agent/execute HTTP/1.1\r\nHost: x\r\n"
                   b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        response = self._raw(request)
        self.assertIn(b"400", response.split(b"\r\n", 1)[0])

    def test_health_still_answers(self):
        response = self._raw(b"GET /agent/health HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertIn(b"200", response.split(b"\r\n", 1)[0])
        self.assertIn(b"dofus-agent", response)

    def test_a_request_without_the_secret_is_refused(self):
        # Everything else in this class now authenticates, so this is the one
        # place that pins the refusal: a page that can reach 127.0.0.1 still
        # cannot drive the agent without the run's secret.
        body = b'{"tool":"quest.list","args":{}}'
        request = (b"POST /agent/execute HTTP/1.1\r\nHost: x\r\n"
                   b"Content-Type: application/json\r\n"
                   b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        response = self._raw(request, authenticated=False)
        self.assertIn(b"401", response.split(b"\r\n", 1)[0])
        self.assertIn(b"unauthorized", response)
        self.assertNotIn(b'"ok"', response, "an unauthenticated call was executed")


class HttpDesyncTests(_ServerMixin):
    """An unread body left the next request bytes in the buffer."""

    def test_oversized_body_closes_instead_of_parsing_the_next_request(self):
        # The handler rejects length > 8 MiB without reading the body. The bytes
        # that follow are then the *next* request, which must not be served on
        # this connection: before the fix the server answered 400 and then a 501
        # for `{"observation":{}}GET /agent/health`.
        smuggled = b'{"observation":{}}GET /agent/health HTTP/1.1\r\nHost: x\r\n\r\n'
        request = (b"POST /agent/execute HTTP/1.1\r\nHost: x\r\n"
                   b"Content-Length: 8388609\r\n\r\n" + smuggled)
        response = self._raw(request)
        self.assertIn(b"400", response.split(b"\r\n", 1)[0])
        self.assertNotIn(b"501", response,
                         "the connection was reused after an unread body")

    def test_handler_declares_a_timeout(self):
        # setUp saves the shipped value before overriding it for the test.
        self.assertIsNotNone(self._saved_timeout)
        self.assertGreater(self._saved_timeout, 0)
        self.assertLessEqual(self._saved_timeout, 30,
                             "a longer read timeout keeps a stalled body too long")


class HttpStalledBodyTests(_ServerMixin):
    """A body that stops arriving must be cut off, not held forever.

    Its own class because it needs a 1 s handler timeout to stay fast, while
    the desync tests want the shipped value.
    """

    handler_timeout = 1

    def test_stalled_body_is_cut_off_by_the_handler_timeout(self):
        # Content-Length promises more than is sent, so the handler blocks in
        # rfile.read until its timeout. Without one it blocked forever and the
        # thread was pinned (160 sockets grew the process to 166 threads).
        request = (b"POST /agent/execute HTTP/1.1\r\nHost: x\r\n"
                   b"Content-Length: 100\r\n\r\n" + b'{"tool":')
        response = self._raw(request, read_seconds=8.0)
        self.assertTrue(response, "the stalled body was never answered")
        self.assertIn(b"400", response.split(b"\r\n", 1)[0])


class HttpConcurrencyCapTests(_ServerMixin):
    """Handler threads are capped, so a socket flood cannot exhaust the process.

    A 4-slot server with a 3 s handler timeout: 20 stalled connections can only
    occupy 4 threads. Without the cap it was one thread per connection.
    """

    handler_timeout = 3
    max_concurrent = 4

    def test_thread_count_stops_at_the_cap(self):
        baseline = threading.active_count()
        stalled = []
        try:
            for _ in range(20):
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
                sock.sendall(self._stalled_request())
                stalled.append(sock)

            # Sample while the four handlers are parked in rfile.read.
            peak = 0
            deadline = time.time() + 0.8
            while time.time() < deadline:
                peak = max(peak, threading.active_count())
                time.sleep(0.05)

            self.assertLessEqual(
                peak - baseline, self.max_concurrent + 2,
                "20 stalled sockets grew the process by %d threads with a cap of %d"
                % (peak - baseline, self.max_concurrent))
        finally:
            for sock in stalled:
                sock.close()

    def test_slots_are_released_and_service_resumes(self):
        stalled = []
        for _ in range(20):
            sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            sock.sendall(self._stalled_request())
            stalled.append(sock)
        for sock in stalled:
            sock.close()

        # Every slot must come back: a leaked one would wedge the server after
        # max_concurrent floods. The health request waits behind the backlog.
        response = self._raw(b"GET /agent/health HTTP/1.1\r\nHost: x\r\n\r\n",
                             read_seconds=10.0)
        self.assertIn(b"200", response.split(b"\r\n", 1)[0])
        self.assertIn(b"dofus-agent", response)


class HttpLivenessUnderFloodTests(_ServerMixin):
    """The cap must not starve the liveness endpoint.

    Taking a slot blocks the accept loop, so the cap is also the number of
    connections the plane will answer while they are all stalled. At 64 a
    160-socket flood starved `/agent/health` for the whole 15 s handler timeout
    (measured 2026-11-07: the stress harness reported "control plane stopped
    answering"). The shipped cap is 512, so a flood below it still leaves the
    plane answering.
    """

    handler_timeout = 5

    def test_shipped_cap_exceeds_the_measured_flood(self):
        # The cap is also the number of connections the plane will answer while
        # they are all stalled, so setting it below the flood size starves
        # /agent/health for the whole handler timeout. 64 did exactly that
        # against the harness's 160-socket flood (2026-11-07).
        from agent import serve as serve_mod

        cap = serve_mod.AgentHTTPServer.max_concurrent_requests
        self.assertGreaterEqual(
            cap, 160,
            "cap %d is below the 160-socket flood the stress harness uses; "
            "/agent/health would be starved" % cap)

    def test_health_answers_while_bodies_are_stalled(self):
        stalled = []
        try:
            for _ in range(40):
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
                sock.sendall(self._stalled_request())
                stalled.append(sock)

            started = time.perf_counter()
            response = self._raw(b"GET /agent/health HTTP/1.1\r\nHost: x\r\n\r\n",
                                 read_seconds=3.0)
            elapsed_ms = (time.perf_counter() - started) * 1000.0

            self.assertIn(b"200", response.split(b"\r\n", 1)[0],
                          "health did not answer during a 40-socket flood")
            self.assertLess(elapsed_ms, 1500,
                            "health took %.0f ms during a 40-socket flood" % elapsed_ms)
        finally:
            for sock in stalled:
                sock.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
