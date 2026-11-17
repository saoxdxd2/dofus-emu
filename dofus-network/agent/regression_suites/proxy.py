"""Extracted regression suite: proxy"""
from __future__ import annotations
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from agent.regression_suites.common import REPO_ROOT, ROOT, OPTIONAL_IMPORTS, check, section, logger

def _host_launch_cases(hc) -> None:
    """`preflight(force)` says ok only when force is set, exactly as the real one does."""
    import importlib.util
    clicks: List[str] = []
    hc.preflight = lambda force=False: {
        "ok": bool(force),
        "blocking": [{"level": "FAIL", "name": "egress", "detail": "upstream not listening"}],
        "entries": {"egress": "FAIL"}}
    hc._click = lambda x, y: clicks.append("click@%s,%s" % (x, y))
    hc.elements = lambda: []
    result = hc.launch_all(confirm_s=0.2)
    check("a failed preflight refuses to click Launch All",
          result.get("clicked") is None and result.get("refused") == "preflight"
          and not clicks, result)
    clicked: List[Any] = []
    hc.focus_host = lambda: True
    hc.running_counts = lambda items=None: {"running": 0, "configured": 1}
    hc.elements = lambda: [{"name": "Launch All (1x)", "type": "ControlType.Pane",
                            "x": 100, "y": 200, "w": 100, "h": 30},
                           {"name": "Dofus Touch Farm Manager", "type": "ControlType.Window",
                            "x": 90, "y": 100, "w": 1200, "h": 600}]
    hc._click = lambda x, y: clicked.append((x, y))
    forced = hc.launch_all(confirm_s=0.2, force=True)
    check("--force is the deliberate escape hatch", bool(clicked),
          {"attempts": forced.get("attempts"), "clicks": clicked})
    # an element sitting on the title-bar close cluster must never be clicked
    clicks2: List[Any] = []
    hc.preflight = lambda force=False: {"ok": True, "blocking": [], "entries": {}}
    hc._click = lambda x, y: clicks2.append((x, y))
    hc.elements = lambda: [{"name": "Launch All (1x)", "type": "ControlType.Pane",
                            "x": 1240, "y": 105, "w": 60, "h": 24},
                           {"name": "Dofus Touch Farm Manager", "type": "ControlType.Window",
                            "x": 90, "y": 100, "w": 1200, "h": 600}]
    refused = hc.launch_all(confirm_s=0.2)
    check("a rectangle over the window's close button is refused, not clicked",
          refused.get("verified") is not True and not clicks2,
          [a.get("reason") for a in refused.get("attempts", [])])
    hc._click = clicks.append
    spec2 = importlib.util.spec_from_file_location(
        "live_ready_guard", REPO_ROOT / "tools" / "live_ready.py")
    ready = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(ready)
    check("a fetch through a port nobody listens on is False, not an exception",
          ready._fetch_via(ready.LOGIN_ASSET, "http://127.0.0.1:1", timeout=1.0) is False, None)


def test_command_routing_and_bridge_fast_fail() -> None:
    section("'Do the quests' is a run, not a list; a wedged page fails fast")
    # The rule baseline labelled every quest prompt `list`, including the one
    # users type most ("do all the quests"), while the router sent it to the
    # quest planner. Two layers disagreeing about the same prompt is how the
    # command corpus and the live routing drifted apart.
    from agent.commands import intent_tool, rule_parse
    from agent.planner import _goal_mode
    from agent.runtime import RulePolicy

    do = rule_parse("do all the quests")
    check("an instruction to do quests is not a list request",
          do["intent"] == "quest" and do["slots"].get("action") == "run", do)
    check("and it maps to the tool that does quests",
          intent_tool(do["intent"], do["slots"].get("action")) == "quest.advance",
          intent_tool(do["intent"], do["slots"].get("action")))
    check("the router agrees, so rule and planner read the prompt the same way",
          _goal_mode("do all the quests") == "quest", _goal_mode("do all the quests"))
    listed = rule_parse("list my quests")
    check("a read of the log stays a list",
          listed["slots"].get("action") == "list"
          and intent_tool(listed["intent"], listed["slots"].get("action")) == "quest.list",
          listed)
    area = rule_parse("quests in incarnam")
    check("a named area is still a list with its area",
          area["slots"].get("action") == "list"
          and area["slots"].get("area") == "incarnam", area)
    check("the rule policy itself no longer answers work as a read",
          RulePolicy().choose("do all the quests", {}, None) is None,
          RulePolicy().choose("do all the quests", {}, None))

    section("A client page that stops answering opens a fast-fail circuit")
    from agent.executor import ToolExecutor

    executor = ToolExecutor.__new__(ToolExecutor)   # breaker only: no cache, no store
    executor.directive_url = "http://127.0.0.1:1/directive"
    executor.timeout = 6.0
    executor._bridge_fails = 0
    executor._bridge_open_until = 0.0
    executor._bridge_last_error = None
    calls: list = []

    def stalled(method, url, body=None):  # noqa: ARG001
        calls.append(1)
        return True, {"ok": False,
                      "error": "directive 'STATE_SNAPSHOT' timed out or the client page is not ready"}

    executor._http_json = stalled
    first = executor._directive("STATE_SNAPSHOT", "state.snapshot", {}, "live")
    second = executor._directive("STATE_SNAPSHOT", "state.snapshot", {}, "live")
    third = executor._directive("STATE_SNAPSHOT", "state.snapshot", {}, "live")
    check("two stalls are reported, not hidden",
          first.ok is False and second.ok is False
          and "timed out" in str(first.error), first.error)
    check("the third call fails without paying the page wait again",
          third.ok is False and (third.data or {}).get("circuit_open") is True
          and len(calls) == 2, "%d http calls, third=%s" % (len(calls), third.error))

    def answered(method, url, body=None):  # noqa: ARG001
        return True, {"ok": True, "result": {}, "snapshot": {}}

    executor._http_json = answered
    executor._bridge_open_until = 0.0      # the circuit's own 15 s window elapsed
    healed = executor._directive("STATE_SNAPSHOT", "state.snapshot", {}, "live")
    check("an answer closes the circuit again", healed.ok is True,
          healed.error)
    check("the failure counter is reset by the answer",
          executor._bridge_fails == 0 and executor._bridge_open_until == 0.0,
          executor._bridge_fails)


def test_egress_preflight() -> None:
    section("Egress preflight (no instance is launched into a dead path)")
    import importlib.util
    import socket as _socket
    import tempfile

    spec = importlib.util.spec_from_file_location(
        "live_ready_egress", REPO_ROOT / "tools" / "live_ready.py")
    live_ready = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live_ready)

    listener = _socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)   # several probes hit one socket; a backlog of 1 makes the test flaky
    open_port = listener.getsockname()[1]
    try:
        dead = live_ready.egress({"mode": "custom", "upstream": "socks5://127.0.0.1:1",
                                  "fail_closed": "1"}, relay_probe=False)
        check("a configured upstream that is not listening is a FAIL",
              dead["level"] == "FAIL" and "not listening" in dead["detail"], dead)
        live = live_ready.egress({"mode": "custom",
                                  "upstream": "socks5://127.0.0.1:%d" % open_port},
                                 relay_probe=False)
        check("an upstream that accepts connections passes",
              live["level"] == "PASS", live)
        check("direct is reported as a choice, not a failure",
              live_ready.egress({"mode": "direct"})["level"] == "INFO", None)
        check("wireproxy mode probes the tunnel port",
              live_ready.egress({"mode": "wireproxy", "socks_port": "1"},
                                relay_probe=False)["level"] == "FAIL", None)

        out = Path(tempfile.mkdtemp(prefix="pool-")) / "proxies.txt"
        out.write_text("# comment\nsocks5://127.0.0.1:1\nhttp://127.0.0.1:1\n",
                       encoding="utf-8")
        empty_pool = live_ready.egress({"mode": "custom", "upstream": ""}, relay_probe=False,
                                       pool_path=out)
        check("custom with a dead pool refuses to launch",
              empty_pool["level"] == "FAIL" and "none of which accepts" in empty_pool["detail"],
              empty_pool)
        out.write_text("socks5://127.0.0.1:%d\n" % open_port, encoding="utf-8")
        check("one live pool entry is enough to proceed",
              live_ready.egress({"mode": "custom", "upstream": ""}, relay_probe=False,
                                pool_path=out)["level"] == "PASS", None)
        check("an unprobed mode warns instead of passing",
              live_ready.egress({"mode": "auto"}, relay_probe=False)["level"] == "WARN", None)

        parsed = live_ready._endpoint("socks5://127.0.0.1:40000")
        check("the upstream endpoint is parsed, not string-matched",
              parsed == ("127.0.0.1", 40000), parsed)
        check("a bare word is not an endpoint", live_ready._endpoint("chain.Skip(1)") is None,
              None)
        # `network.ini` is in .gitignore's SECRETS block on purpose -- it carries the
        # proxy/upstream config. A clean clone has none, so `mode` is None and the
        # question "is it read as keys" has no answer to give. Asking anyway is how a
        # fresh checkout reports a failure that is really an absent local file.
        if (REPO_ROOT.parent / "network.ini").is_file():
            cfg = live_ready.network_config()
            check("network.ini is read as keys", cfg.get("mode") in
                  ("auto", "custom", "direct", "wireproxy", "warp"), cfg.get("mode"))
        else:
            logger.debug("   SKIP: network.ini is local-only (gitignored: it holds credentials)")
        entries = live_ready.egress()
        check("the real preflight reports a level for the actual config",
              entries.get("level") in ("PASS", "FAIL", "WARN", "INFO"), entries)
    finally:
        listener.close()


def test_host_launch_guard() -> None:
    section("Host launcher refuses a dead path (no six-dialog stampede)")
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "host_control_guard", REPO_ROOT / "tools" / "host_control.py")
    hc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hc)

    # The game clients belong to the same process as the panel. Both reads below used to take
    # "the first window of the process", which is a client: the panel's controls then vanished
    # from the tree and a launch that had worked looked unverified.
    src = (REPO_ROOT / "tools" / "host_control.py").read_text(encoding="utf-8")
    check("the UIA read excludes a client window of the same process",
          "-notmatch 'WebView2|Instance #'" in hc.UIA_SCRIPT, hc.UIA_SCRIPT[-420:])
    check("focus raises the manager by its own title, not by the word 'farm'",
          '_host_hwnd("farm manager")' in src, None)
    check("reading the running count re-raises the host first",
          "if items is None:\n        focus_host()\n        items = elements()" in src, None)

    # the launcher waits on the UI thread; a gate must not spend 20 real seconds doing it.
    # Only sleep is replaced — launch_all still needs time.time() for its own deadline.
    class _SlowClock:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def sleep(self, seconds):
            return None

    real_time, real_alerts = hc.time, hc.dismiss_alerts
    hc.time = _SlowClock(real_time)
    hc.dismiss_alerts = lambda limit=3: 0
    try:
        _host_launch_cases(hc)
    finally:
        hc.time, hc.dismiss_alerts = real_time, real_alerts


def test_auto_live_guards() -> None:
    section("Unattended live runner (gates, waits and round accounting)")
    import importlib.util
    import json as _json
    import tempfile

    spec = importlib.util.spec_from_file_location(
        "auto_live_guard", REPO_ROOT / "tools" / "auto_live.py")
    al = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(al)

    class _Clock:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def sleep(self, seconds):
            return None

    tmp = Path(tempfile.mkdtemp(prefix="auto-live-"))
    log = tmp / "auto_0.jsonl"
    # This is the shape the live loop really writes (see test_auto_live_summarise):
    # `index` + `tool` with `kind` null. The fixture used to invent `kind: "step"`
    # rows with a nested observation.position, which matched the code it was written
    # beside and not the game: the first real unattended round reported rows: 0.
    log.write_text("".join(_json.dumps(row) + chr(10) for row in [
        {"index": 0, "kind": None, "tool": "nav.goto", "ok": True,
         "obs_before": {"map_id": 100}, "obs_after": {"map_id": 101}},
        {"index": 1, "kind": None, "tool": "quest.advance", "ok": False,
         "obs_before": {"map_id": 101}, "obs_after": {"map_id": 101}},
        {"kind": "decision", "goal": "do all the quests"},
        "{ not json",
    ]), encoding="utf-8")
    summary = al.summarise(log)
    check("a round is counted from its own rows, not from a summary line",
          summary["rows"] == 2 and summary["ok"] == 1 and summary["map_changed"] == 1,
          summary)
    check("the tool mix is measured per round",
          summary["tools"].get("nav.goto") == 1, summary["tools"])

    al._load = lambda name: type("Stub", (), {"checks": staticmethod(lambda: [
        {"level": "FAIL", "name": "egress", "detail": "upstream not listening"},
        {"level": "PASS", "name": "client", "detail": "1 window"}])})()
    check("a dead egress stops the runner before anything is launched",
          al.preflight(force=False) == (False, [{"level": "FAIL", "name": "egress",
                                                 "detail": "upstream not listening"}]), None)
    ok, _ = al.preflight(force=True)
    check("--force is the operator's explicit override", ok is True, None)
    al._load = lambda name: type("Stub", (), {"checks": staticmethod(lambda: [
        {"level": "PASS", "name": "egress", "detail": "relay ok"}])})()
    check("a healthy preflight lets the run proceed", al.preflight(force=False)[0] is True, None)

    stop = tmp / "stop"
    calls: List[int] = []
    responses = [{"slot": 1, "error": "timed out"},
                 {"slot": 1, "in_world": False, "connected": True},
                 {"slot": 1, "in_world": True, "name": "Aiai", "level": 3}]
    real_probe, real_time = al.probe, al.time
    al.probe = lambda slot, timeout=12.0: (calls.append(slot), responses[min(len(calls), 3) - 1])[1]
    al.time = _Clock(real_time)
    dismissed: List[int] = []
    try:
        result = al.wait_for_slot(1, 60.0, stop, dismiss=lambda limit=2: dismissed.append(1))
        check("the loop waits for the page to report a world, not for a fixed timeout",
              result.get("ok") is True and len(calls) == 3, (result, len(calls)))
        check("an open dialog is dismissed while waiting", len(dismissed) >= 2, dismissed)
        stop.write_text("stop", encoding="utf-8")
        calls.clear()
        stopped = al.wait_for_slot(1, 60.0, stop)
        check("the stop file ends the wait immediately",
              stopped.get("reason") == "stop file", stopped)
        stop.unlink()
        al.probe = lambda slot, timeout=12.0: {"slot": slot, "in_world": False}
        out_of_time = al.wait_for_slot(2, 0.0, stop)
        check("a slot that never answers is reported, not assumed",
              out_of_time.get("ok") is False, out_of_time)
    finally:
        al.probe, al.time = real_probe, real_time

    # the login screen used to be the step that needed a person: prove the runner presses
    # the game's own guest button, and that it asks for the path the operator authorised
    press_calls: List[Dict[str, Any]] = []
    real_call = al.call
    al.call = (lambda slot, tool, args=None, timeout=20.0:
               (press_calls.append({"tool": tool, "args": args or {}}),
                {"result": {"ok": True, "data": {"pressed": "_playGuestBtn.emit(tap,event)",
                                                 "emit_error": None}}})[1])
    try:
        note = al.auto_login(1)
        check("auto-login asks for the guest path, not a saved Google session",
              press_calls and press_calls[0]["tool"] == "session.login"
              and press_calls[0]["args"].get("target") == "guest", press_calls)
        check("the press is reported with what it pressed",
              note.get("pressed") == "_playGuestBtn.emit(tap,event)", note)
        responses2 = [{"slot": 1, "in_world": False, "connected": True},
                      {"slot": 1, "in_world": False, "connected": True},
                      {"slot": 1, "in_world": True, "name": "Aiai", "level": 3}]
        calls2: List[int] = []
        al.probe = (lambda slot, timeout=12.0:
                    (calls2.append(slot), responses2[min(len(calls2), 3) - 1])[1])
        press_calls.clear()
        al.time = _Clock(real_time)
        got = al.wait_for_slot(1, 60.0, stop, login=al.auto_login, every_s=0.0)
        check("a connected page with no world is pressed rather than only polled",
              got.get("ok") is True and len(press_calls) == 2,
              (got, press_calls, len(press_calls)))
        check("the wait reports how many logins it took",
              got.get("logins") == 2, got)
        al.probe = lambda slot, timeout=12.0: {"slot": slot, "in_world": True, "name": "Y"}
        press_calls.clear()
        al.wait_for_slot(1, 60.0, stop, login=al.auto_login, every_s=0.0)
        check("a client already in a world is never pressed", press_calls == [], press_calls)
    finally:
        al.call, al.probe, al.time = real_call, real_probe, real_time

    class _Boom:
        def __enter__(self):
            raise OSError("page not answering")

        def __exit__(self, *args):
            return False

    real_urlopen = al.urllib.request.urlopen
    al.urllib.request.urlopen = lambda *a, **k: _Boom()
    try:
        errored = al.probe(3)
        check("an unanswered probe returns a state, never an exception",
              "error" in errored, errored)
    finally:
        al.urllib.request.urlopen = real_urlopen


def test_auto_live_summarise() -> None:
    section("Round accounting (the manifest row must see the steps the round took)")
    import importlib.util
    import json as _json
    import tempfile

    spec = importlib.util.spec_from_file_location(
        "auto_live_summarise", REPO_ROOT / "tools" / "auto_live.py")
    al = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(al)

    # The live loop's step rows carry `index`+`tool` and `kind: null`; the other
    # row kinds are decision / wait / identity. This is the shape of the first real
    # unattended round (2026-10-27), not an invention: a filter on `kind == "step"`
    # matched nothing and every manifest reported rows: 0.
    rows = [
        {"index": 1, "tool": "nav.goto", "ok": True, "kind": None, "verdict": "advanced",
         "obs_before": {"map_id": 100, "cell": 289}, "obs_after": {"map_id": 101, "cell": 4}},
        {"index": 2, "tool": "quest.advance", "ok": True, "kind": None, "verdict": "noop",
         "obs_before": {"map_id": 101, "cell": 4}, "obs_after": {"map_id": 101, "cell": 4}},
        {"kind": "decision", "tool": "quest.advance", "ok": True},
        {"kind": "wait", "tool": None},
        {"kind": "identity", "tool": None},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "round.jsonl"
        path.write_text("".join(_json.dumps(r) + "\n" for r in rows) + "torn-half-line",
                        encoding="utf-8")
        summary = al.summarise(path)
    check("a step row is one with an index and a tool, whatever its kind says",
          summary.get("rows") == 2, summary)
    check("only the executed rows are counted, not decisions, waits or identity",
          summary.get("ok") == 2 and set(summary.get("tools") or {}) == {"nav.goto", "quest.advance"},
          summary)
    check("a map change is measured from the vectors the rows really hold",
          summary.get("map_changed") == 1, summary)

    rounds = sorted((REPO_ROOT / "data" / "auto_live").glob("*/auto_*.jsonl"))
    if rounds:
        real = al.summarise(rounds[-1])
        check("the summariser counts the steps of a real recorded round",
              real.get("rows", 0) > 0, "%s -> %s" % (rounds[-1].name, real))


__all__ = ['test_command_routing_and_bridge_fast_fail', 'test_egress_preflight', 'test_host_launch_guard', 'test_auto_live_guards', 'test_auto_live_summarise']