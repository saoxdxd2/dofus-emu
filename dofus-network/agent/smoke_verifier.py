"""Smoke test for the second agent - offline, no game, no network, no torch.

    cd dofus-network && python -m agent.smoke_verifier

Exit code 0 when every check passes. This is the "link and test in a real run"
step without needing the client: it proves the verifier is wired into the loop,
that all four verdicts are reachable, that drift fires on the contradictions we
care about, and that a human label overrides both.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.autopilot import Autopilot, Step
from agent.verifier import HumanLabels, Verdict, Verifier

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, (" - " + detail) if detail else ""))


class FakeResult:
    def __init__(self, ok: bool, error=None):
        self.ok = ok
        self.error = error


def base_obs(**over):
    obs = {"cell": 100, "map_id": 7, "kamas": 1000, "xp": 10, "hp": 100, "inventory_size": 5}
    obs.update(over)
    return obs


def test_verdicts() -> None:
    print("\n== verdicts ==")
    v = Verifier(human_path=Path(tempfile.gettempdir()) / "nonexistent_human.jsonl")

    r = v.label("hdv.list", {}, True, None, base_obs(), base_obs(cell=101), 120.0)
    check("advanced on a changed cell", r.verdict == "advanced" and r.source == "rule", r.verdict)

    r = v.label("hdv.list", {}, True, None, base_obs(), base_obs(), 120.0)
    check("noop when nothing moved", r.verdict == "noop", r.verdict)

    r = v.label("hdv.list", {}, False, "connection reset", base_obs(), None, 120.0)
    check("failed on a not-ok call", r.verdict == "failed", r.verdict)

    r = v.label("quest.advance", {}, False, "not_mapped: npc 123", base_obs(), None, 120.0)
    check("blocked on not_mapped", r.verdict == "blocked", r.verdict)

    r = v.label("hdv.list", {}, True, None, base_obs(), base_obs(kamas=1200), 120.0)
    check("kamas change counts as advanced", r.verdict == "advanced", r.verdict)


def test_residual_and_drift() -> None:
    print("\n== residual / drift ==")
    v = Verifier(human_path=Path(tempfile.gettempdir()) / "nonexistent_human.jsonl")

    r = v.label("farm.step", {}, True, None, base_obs(), base_obs(cell=101), 3500.0, expected_s=2.0)
    check("residual = actual - expected", r.residual_s == 1.5, str(r.residual_s))

    r = v.label("hdv.list", {}, True, None, base_obs(), base_obs(kamas=900), 100.0)
    check("drift: kamas dropped without a purchase", r.drift and any("kamas" in x for x in r.reasons),
          str(r.reasons))

    r = v.label("hdv.list", {}, True, None, base_obs(), base_obs(map_id=8), 100.0)
    check("drift: map changed on a non-movement tool", r.drift, str(r.reasons))

    # before has hp, after does not: the client stopped reporting a field the
    # previous step relied on.
    moved = {"cell": 100, "kamas": 1000, "hp": 100}
    after = {"cell": 101, "kamas": 1000}
    r = v.label("farm.step", {}, True, None, moved, after, 100.0)
    check("drift: observation lost fields", r.drift and any("lost" in x for x in r.reasons),
          str(r.reasons))

    r = v.label("nav.goto", {}, True, None, base_obs(), base_obs(map_id=9), 100.0)
    check("no drift when a movement tool changes the map", not r.drift, str(r.reasons))


def test_human_override() -> None:
    print("\n== human in the loop ==")
    tmp = Path(tempfile.mkdtemp()) / "verdicts_human.jsonl"
    v = Verifier(human_path=tmp)

    r = v.label("hdv.list", {}, True, None, base_obs(), base_obs(), 100.0, session="abc123", index=4)
    check("rule first, no human label yet", r.source == "rule", r.source)

    v.human.add("abc123", 4, "failed", "the listing never appeared in game")
    r = v.label("hdv.list", {}, True, None, base_obs(), base_obs(), 100.0, session="abc123", index=4)
    check("human overrides the rule", r.source == "human" and r.verdict == "failed",
          "%s/%s" % (r.source, r.verdict))

    r = v.label("hdv.list", {}, True, None, base_obs(), base_obs(), 100.0, session="other", index=4)
    check("human label is scoped to its session", r.source == "rule", r.source)

    fresh = HumanLabels(tmp)
    check("human labels reload from disk", fresh.count() == 1, str(fresh.count()))


def test_loop_wiring() -> None:
    print("\n== autopilot wiring ==")
    tmp = Path(tempfile.mkdtemp())
    ap = Autopilot(runtime=object(), goals=["g"], log_path=tmp / "autopilot.jsonl",
                   stop_file=tmp / "STOP", max_steps=1)

    check("autopilot has a session id", len(ap.session) == 8, ap.session)
    check("autopilot owns a verifier", isinstance(ap.verifier, Verifier))

    v = ap._verdict(FakeResult(True), base_obs(), base_obs(xp=20), call=None, latency_ms=50.0)
    check("_verdict returns a Verdict, not a string", isinstance(v, Verdict), type(v).__name__)
    check("_verdict advances on xp change", v.verdict == "advanced", v.verdict)

    step = Step(index=0, goal="g", tool="hdv.list", args={}, ok=True, error=None,
                latency_ms=10.0, verdict="noop", residual_s=0.2, drift=True,
                session=ap.session, source="rule")
    row = step.as_dict()
    for key in ("verdict", "residual_s", "drift", "session", "source"):
        check("step json has %s" % key, key in row, str(row.get(key)))
    json.dumps(row)  # must be serialisable as-is


def test_baseline_model() -> None:
    """The shipped baseline must load and actually predict.

    It is trained on rule-labelled data, so agreement with the rules is the
    expected result — this checks the wiring (features -> weights -> Verdict),
    not the model's quality.
    """
    print("\n== baseline model ==")
    path = REPO_ROOT / "models" / "verifier_baseline.json"
    if not path.exists():
        print("  [SKIP] no models/verifier_baseline.json")
        return

    v = Verifier(model_path=path, human_path=Path(tempfile.gettempdir()) / "nonexistent_human.jsonl")
    check("baseline loaded", v._baseline is not None, v.model_note)

    r = v.label("hdv.list", {}, True, None, base_obs(), base_obs(cell=101), 100.0)
    check("model labels an advance", r.source == "model" and r.verdict == "advanced",
          "%s/%s p=%s" % (r.source, r.verdict, r.confidence))

    r = v.label("hdv.list", {}, False, "connection reset", base_obs(), None, 100.0)
    check("model labels a failure", r.source == "model" and r.verdict == "failed",
          "%s/%s" % (r.source, r.verdict))

    r = v.label("hdv.list", {}, True, None, base_obs(), base_obs(), 100.0)
    check("model labels a noop", r.source == "model" and r.verdict == "noop",
          "%s/%s" % (r.source, r.verdict))

    # A human label still wins over the model.
    tmp = Path(tempfile.mkdtemp()) / "h.jsonl"
    v2 = Verifier(model_path=path, human_path=tmp)
    v2.human.add("s1", 2, "blocked", "reviewer disagrees")
    r = v2.label("hdv.list", {}, True, None, base_obs(), base_obs(cell=101), 100.0,
                 session="s1", index=2)
    check("human beats the model", r.source == "human" and r.verdict == "blocked", r.source)


def test_step_summary() -> None:
    """The panel line must say what came back, not just that something did."""
    print("\n== step summary ==")
    from agent.autopilot import _summarise

    class R:
        def __init__(self, data):
            self.data = data

    envelope = {"directive_payload": {"id": "x"}, "response": {"ok": True,
                "result": {"items": [{"gid": 1}] * 23}}}
    check("directive envelope is unwrapped", _summarise(R(envelope)) == "  -> 23 items",
          _summarise(R(envelope)))
    check("empty result prints nothing", _summarise(R(None)) == "", repr(_summarise(R(None))))
    check("unknown shape does not raise", isinstance(_summarise(R({"weird": 1})), str))


def test_controller_changes_the_next_action() -> None:
    """The point of the whole exercise: does agent 2 alter what happens next?"""
    print("\n== agent 2 controller ==")
    from agent.controller import Controller
    from agent.runtime import ToolCall

    tmp = Path(tempfile.mkdtemp())

    def v(verdict, drift=False):
        return Verdict(verdict, 0.8, None, drift, "rule")

    c = Controller(state_dir=tmp, session="t1")
    ok_obs = base_obs()

    d = c.on_step(v("failed"), "hdv.list", False, "connection reset", ok_obs, None)
    check("first failure -> retry, not silence", d.action == "retry", d.action)
    check("retry keeps the same call", d.next_call is None, str(d.next_call))

    d = c.on_step(v("failed"), "hdv.list", False, "connection reset", ok_obs, None)
    check("second failure -> a different action", d.action == "recover", d.action)
    check("recover names a concrete next call",
          d.next_call is not None and d.next_call.get("tool") == "state.snapshot",
          str(d.next_call))

    d = c.on_step(v("blocked"), "quest.advance", False, "not_mapped: npc 4211", ok_obs, None)
    check("not_mapped -> replan immediately (the plan is wrong, not the call)",
          d.action == "replan" and d.cause == "invalid_assumption", "%s/%s" % (d.action, d.cause))

    c2 = Controller(state_dir=tmp, session="t2")
    for _ in range(3):
        d = c2.on_step(v("failed"), "farm.start", False, "cannot reach", ok_obs, None)
    check("three failures of one tool -> replan", d.action == "replan", d.action)
    check("cause is plan_wrong after repetition", d.cause in ("plan_wrong", "wrong_target"), d.cause)

    # drift authority, with the freshness gate
    c3 = Controller(state_dir=tmp, session="t3")
    c3.on_step(v("advanced"), "farm.step", True, None, ok_obs, base_obs(cell=101))
    d1 = c3.on_step(v("advanced", drift=True), "farm.step", True, None, base_obs(cell=101), base_obs(cell=102))
    d2 = c3.on_step(v("advanced", drift=True), "farm.step", True, None, base_obs(cell=102), base_obs(cell=103))
    check("two fresh drifts -> replan", d2.action == "replan" and d2.cause == "stale_state",
          "%s/%s" % (d2.action, d2.cause))

    c4 = Controller(state_dir=tmp, session="t4")
    same = base_obs()
    c4.on_step(v("advanced"), "farm.step", True, None, same, dict(same))
    d1 = c4.on_step(v("advanced", drift=True), "farm.step", True, None, same, dict(same))
    d2 = c4.on_step(v("advanced", drift=True), "farm.step", True, None, same, dict(same))
    check("stale (identical) observations do not replan", d2.action != "replan", d2.action)

    # The most common failure in practice: the policy cannot express the goal.
    d = Controller(state_dir=tmp, session="t6").on_step(
        v("blocked"), "-", False, "policy returned no call", ok_obs, None)
    check("an unexpressable goal is an invalid assumption",
          d.cause == "invalid_assumption", d.cause)

    # memory: a recovery that worked becomes the preferred one
    c5 = Controller(state_dir=tmp, session="t5")
    dec = c5.on_step(v("failed"), "hdv.scan", False, "unknown id 999", ok_obs, None)
    c5.observe_outcome(dec, v("advanced"))
    strat, score = c5._best_strategy("wrong_target")
    check("successful recovery is remembered", score > 0.0 and strat != "", "%s %.2f" % (strat, score))

    c5.save()
    c6 = Controller(state_dir=tmp, session="t5")
    c6.load()
    check("state survives a restart", any(k.startswith("wrong_target") for k in c6._strategies),
          str(list(c6._strategies)))


def test_loop_applies_the_decision() -> None:
    """The join: a decision must actually change the actor's next call."""
    print("\n== loop wiring ==")
    from agent.controller import Decision
    from agent.runtime import ToolCall

    tmp = Path(tempfile.mkdtemp())
    ap = Autopilot(runtime=object(), goals=["g"], log_path=tmp / "a.jsonl",
                   stop_file=tmp / "S", max_steps=1)
    call = ToolCall(tool="hdv.list", args={"item": 1}, goal="g")

    out = ap._apply_decision(Decision("recover", "wrong_target", "x",
                                      {"tool": "state.snapshot", "args": {}}, "recover"), "g", call)
    check("recover sets a forced call", out == "recover" and ap._forced_call is not None,
          str(ap._forced_call))
    check("forced call is the recovery, not the original",
          ap._forced_call.tool == "state.snapshot", ap._forced_call.tool)

    ap._forced_call = None
    out = ap._apply_decision(Decision("retry", "unknown", "x", None, "retry"), "g", call)
    check("retry re-issues the same call", out == "retry" and ap._forced_call.tool == "hdv.list",
          str(ap._forced_call))

    seen = []
    ap.on_replan = lambda reason, d: seen.append(d.cause)
    ap._apply_decision(Decision("replan", "plan_wrong", "3 failures", None, "replan"), "g", call)
    check("replan reaches the host callback", seen == ["plan_wrong"] and ap.replan_requested == 1,
          str(seen))

    out = ap._apply_decision(Decision("abort", "plan_wrong", "x", None, "abort"), "g", call)
    check("abort stops the goal", out == "abort", out)


def test_the_policy_hears_its_outcomes() -> None:
    """`note_result` existed on the planner and was never called.

    The planner's attempt history was therefore always empty: it could not tell a tool
    that had just failed from one it had never tried. Any history feature (and the
    corpus built from these steps) would be a fiction. This runs the real loop against
    a policy that records what it is told.
    """
    print("\n== policy feedback ==")
    from agent.autopilot import Autopilot
    from agent.runtime import ToolCall

    tmp = Path(tempfile.mkdtemp())

    class Result:
        ok = True
        error = None
        data = {"snapshot": {"in_world": True, "position": {"map_id": 1, "cell": 100}}}

    class Policy:
        def __init__(self):
            self.notes = []
            self.chosen = 0

        def choose(self, goal, obs, tools):
            self.chosen += 1
            if self.chosen > 1:
                return None
            return ToolCall("state.snapshot", {}, goal)

        def note_result(self, tool, ok, note, obs):
            self.notes.append((tool, ok, note))

    class Runtime:
        def __init__(self):
            self.policy = Policy()
            self.instance = None
            self.executor = self

        def current_observation(self):
            return ({"in_world": True, "position": {"map_id": 1, "cell": 100}}, 0.0, "call")

        def observe(self, snapshot, source="live"):
            pass

        def execute(self, tool, args, **kw):
            return Result()

    rt = Runtime()
    ap = Autopilot(runtime=rt, goals=["q"], log_path=tmp / "a.jsonl",
                   stop_file=tmp / "S", max_steps=1, interval=0.0,
                   preflight=False, settle_s=0.0)
    ap._run()
    check("the loop reports each outcome to the policy",
          bool(rt.policy.notes) and rt.policy.notes[0][0] == "state.snapshot",
          str(rt.policy.notes[:1]))


def test_onnx_head() -> None:
    """The neural head must load through onnxruntime and predict like the model."""
    print("\n== onnx head ==")
    path = REPO_ROOT / "models" / "verifier_head.onnx"
    if not path.exists():
        print("  [SKIP] no models/verifier_head.onnx")
        return
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        print("  [SKIP] onnxruntime not installed (use runtimes\\python)")
        return

    v = Verifier(model_path=path, human_path=Path(tempfile.gettempdir()) / "nonexistent_human.jsonl")
    check("onnx head loaded", hasattr(v._session, "run"), v.model_note)

    r = v.label("hdv.list", {}, True, None, base_obs(), base_obs(cell=101), 100.0)
    check("onnx labels an advance", r.source == "model" and r.verdict == "advanced",
          "%s/%s p=%s" % (r.source, r.verdict, r.confidence))

    r = v.label("hdv.list", {}, False, "connection reset", base_obs(), None, 100.0)
    check("onnx labels a failure", r.source == "model" and r.verdict == "failed",
          "%s/%s" % (r.source, r.verdict))


def test_exploration_is_opt_in() -> None:
    """A deterministic rule logs one action per state; no overlap, nothing to learn.

    Every attempt to learn the repair decision from simulated failures has failed
    because "continue" is right 79% of the time there. Learning it from real failures
    needs the alternatives to actually be taken sometimes, which costs real steps in a
    real game — so it is off by default and never applies to a structural failure,
    which the taxonomy has already settled with evidence (0 of 9 retries worked).
    """
    print("\n== exploration ==")
    from agent.controller import Controller

    tmp = Path(tempfile.mkdtemp())

    def once(**kw):
        c = Controller(state_dir=tmp, session="tx", **kw)
        return c.on_step(Verdict("failed", 0.9, None, False, "rule"), "farm.start",
                         False, "timed out", {"a": 1}, {"a": 2})

    d = once()
    check("exploration is off by default", not d.explored, "%s explored=%s"
          % (d.action, d.explored))

    c = Controller(state_dir=tmp, session="ty", explore=1.0, seed=5)
    actions = [c.on_step(Verdict("failed", 0.9, None, False, "rule"), "farm.start",
                         False, "timed out", {"a": 1}, {"a": 2}) for _ in range(12)]
    check("with explore=1.0 the action varies",
          len({x.action for x in actions}) > 1,
          "%s" % sorted({x.action for x in actions}))
    check("explored decisions say so", all(x.explored for x in actions),
          "%d of %d" % (sum(x.explored for x in actions), len(actions)))

    cs = Controller(state_dir=tmp, session="tz", explore=1.0, seed=5)
    structural = [cs.on_step(Verdict("failed", 0.9, None, False, "rule"), "farm.start",
                             False, "no client instance is running", {}, {})
                  for _ in range(6)]
    check("a structural failure is never explored",
          all(x.action == "replan" and not x.explored for x in structural),
          "%s" % sorted({(x.action, x.explored) for x in structural}))


def test_session_log_becomes_a_dataset() -> None:
    """The live session is the only remaining source of this decision.

    A log that cannot be turned into (state, action, reward) is a transcript, not
    data. This checks the conversion without needing the client, so a session that
    runs overnight does not turn out to be unreadable in the morning.
    """
    print("\n== session log -> dataset ==")
    from ai.export_decision_log import build

    obs = {"level": 60, "map_id": 123, "inventory": {"1": 2},
           "quests_active": {"5": 0}, "inventory_capacity": 40,
           "current_objective": {"quest_id": 5, "type_id": 1, "map_id": 123}}
    rows = []
    for i in range(8):
        rows.append({"kind": "decision", "action": ["continue", "retry", "recover",
                                                    "replan"][i % 4],
                     "cause": "plan_wrong", "explored": i % 2 == 0,
                     "tool": "farm.start", "error": "timed out", "obs": obs})
        rows.append({"tool": "farm.start", "ok": i % 2 == 0, "error": None})

    dataset, stats = build(rows)
    check("every decision row becomes a sample", len(dataset) == 8, "%d" % len(dataset))
    check("the reward is the next step's outcome",
          sum(r["reward"] for r in dataset) == 4,
          "%d of 8" % sum(r["reward"] for r in dataset))
    check("coverage is counted per cause and action", len(stats["cells"]) == 4,
          "%d cells" % len(stats["cells"]))
    check("the observation's own fields are detected, not assumed",
          stats["fields_seen"].get("current_objective") == 8,
          str(stats["fields_seen"]))


def test_bridge_port_is_discovered() -> None:
    """The bridge publishes its port; assuming 8893 cost a whole session.

    The app runs, the bridge fails to bind because something else holds the port, and
    every directive goes to a socket nobody serves. The loop then takes one step, fails
    everything, and exits looking like it finished. Discovery turns that into either a
    working session or a clear failure.
    """
    print("\n== bridge discovery ==")
    import agent.bridge as bridge

    tmp = Path(tempfile.mkdtemp())
    original = bridge.state_dir
    bridge.state_dir = lambda: tmp          # type: ignore[assignment]
    try:
        check("no published port falls back to the default",
              bridge.bridge_port() == bridge.DEFAULT_PORT, str(bridge.bridge_port()))

        (tmp / "bridge_port.txt").write_text("8894", encoding="utf-8")
        check("a published port is used", bridge.bridge_port() == 8894,
              str(bridge.bridge_port()))
        check("the directive URL follows the published port",
              bridge.directive_url().endswith(":8894/directive"), bridge.directive_url())

        (tmp / "bridge_port.txt").write_text("not a port", encoding="utf-8")
        check("garbage falls back instead of crashing",
              bridge.bridge_port() == bridge.DEFAULT_PORT, str(bridge.bridge_port()))

        (tmp / "bridge_port.txt").write_text("1", encoding="utf-8")
        check("a port nobody serves is not reachable", not bridge.reachable(timeout=0.5))

        # A loopback call must never go through HTTP_PROXY. On a machine running a VPN
        # or a local proxy client that variable is set machine-wide, and urlopen() sent
        # every directive to the bridge through it — a 502 from your own machine, and a
        # second failure point between the agent and the client.
        import urllib.request as _ur
        local = bridge.opener_for("http://127.0.0.1:8894/directive")
        proxy_handlers = [h for h in getattr(local, "handlers", [])
                          if isinstance(h, _ur.ProxyHandler)]
        check("a loopback call bypasses HTTP_PROXY",
              all(not h.proxies for h in proxy_handlers),
              "proxies=%r" % (proxy_handlers[0].proxies if proxy_handlers else {}))
        check("a remote call still honours the environment",
              bridge.opener_for("https://example.com/x") is not local,
              "different opener")
    finally:
        bridge.state_dir = original        # type: ignore[assignment]


def test_host_messages_are_not_double_encoded() -> None:
    """A postMessage that stringifies arrives escaped, and the host drops it.

    Two conventions had grown up side by side: postMessage(obj) and
    postMessage(JSON.stringify(obj)). Every C# branch matches on "type":"X", so the
    second form never matched anything — the in-game badge sent AI_REQUEST that way and
    the host logged it and did nothing. One convention, checked here, so the two cannot
    drift apart again.
    """
    print("\n== host message encoding ==")
    js = REPO_ROOT.parent / "scripts" / "mobile-disguise.js"
    if not js.exists():
        print("  [SKIP] client script not found")
        return
    text = js.read_text(encoding="utf-8")
    bad = text.count("postMessage(JSON.stringify")
    check("no postMessage double-encodes its payload", bad == 0,
          "%d occurrence(s)" % bad if bad else "object form only")


def test_one_transport_one_copy() -> None:
    """The client script must exist once and run once.

    It used to exist three times — `scripts/`, `desktop/client/www/js/` and
    `client/www/js/` — byte-identical, because `builder/build.cs` copied it after every
    build. Worse, two transports both loaded it: the WebView2 host injects
    `scripts/mobile-disguise.js` with AddScriptToExecuteOnDocumentCreated, and
    `standalone.html` also pulled in the copy with a `<script src>` tag. The log showed
    the result on every page load:

        [FarmBot] Bound to connection manager.
        [x2] [FarmBot] Bound to connection manager.

    Two `FarmBot.init()` calls, two `waitForConnection` timers, two bindings of
    `dofus.connectionManager` — two bots driving one HUD. Nothing failed, nothing
    warned; the agent simply ran its tick loop twice.

    These checks pin the fix, because a copy is easy to re-add and impossible to notice.
    """
    print("\n== one transport, one copy ==")
    root = REPO_ROOT.parent
    script = root / "scripts" / "mobile-disguise.js"
    if not script.exists():
        check("the client script exists where the host looks for it", False, str(script))
        return

    text = script.read_text(encoding="utf-8", errors="replace")
    check("the client script guards against a second execution",
          "__DofusFarmAgentLoaded" in text,
          "window.__DofusFarmAgentLoaded present" if "__DofusFarmAgentLoaded" in text
          else "no idempotence guard: a re-added transport would run two bots")

    # Every copy in the tree must be identical.
    #
    # This used to require exactly one file, which was the wrong invariant: the client tree
    # legitimately holds generated copies, and three byte-identical files are not a problem —
    # three files that have drifted apart are. Walk rather than glob so an unexpected location
    # is seen, then compare contents. Identical copies pass; one edited copy fails and names
    # the file that disagrees.
    copies = []
    for p in root.rglob("mobile-disguise*.js"):
        parts = set(p.parts)
        if parts & {"node_modules", "archive", "asset_cache", ".git", "__pycache__"}:
            continue
        copies.append(p)
    digests = {}
    for p in copies:
        try:
            digests.setdefault(hashlib.sha256(p.read_bytes()).hexdigest(), []).append(p)
        except OSError:
            pass
    drifted = [str(p.relative_to(root)) for group in list(digests.values())[1:] for p in group]
    check("every copy of the client script is identical", copies and not drifted,
          ("%d copies, %d distinct version(s)" % (len(copies), len(digests)))
          + ((" — drifted: " + ", ".join(drifted)) if drifted else ""))

    # No page may load it itself; the host injects it.
    tags = []
    for html in root.rglob("standalone.html"):
        if "node_modules" in html.parts:
            continue
        body = html.read_text(encoding="utf-8", errors="replace")
        if "mobile-disguise" in body and "script" in body:
            for line in body.splitlines():
                if "mobile-disguise" in line and "<script" in line:
                    tags.append("%s: %s" % (html.relative_to(root), line.strip()[:70]))
    check("no page loads the client script with a <script> tag", not tags,
          "; ".join(tags) if tags else "injected by the host only")

    # The builder must not recreate the copies. Matched per line on File.Copy rather
    # than on the filename alone: the comment explaining the removal also names the
    # file, and a substring check would flag its own documentation.
    build = root / "builder" / "build.cs"
    if build.exists():
        src = build.read_text(encoding="utf-8", errors="replace")
        offenders = [ln.strip() for ln in src.splitlines()
                     if "File.Copy" in ln and "mobile-disguise" in ln]
        check("the builder does not copy the script into www/js", not offenders,
              "; ".join(offenders)[:90] if offenders else "scripts/ is the only source")


def test_layering_holds() -> None:
    """The harness must not depend on the simulator, and shared vocabulary has one owner.

    `agent/` is the production harness; `sim/` is the offline stand-in. If the harness
    imports the simulator the dependency is inverted and the simulator stops being
    replaceable. Separately, the objective-type buckets and `_params()` used to be
    copy-pasted between `planner.py`, `world.py` and `decisions.py`; they now live in
    `agent/features.py`, and identity checks below catch a re-introduced copy.
    """
    print("\n== layering ==")
    import re as _re

    offenders = []
    for path in sorted((REPO_ROOT / "agent").glob("*.py")):
        if path.name in ("smoke_verifier.py",):
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        for m in _re.finditer(r"^\s*(?:from|import)\s+sim\b", body, _re.MULTILINE):
            offenders.append("%s:%d" % (path.name, body[:m.start()].count("\n") + 1))
    check("no harness module imports the simulator", not offenders,
          ", ".join(offenders) if offenders else "agent/ does not depend on sim/")

    try:
        from agent import features
        from sim import world
        from agent import planner
        check("objective buckets have one owner",
              world.KILL_TYPES is features.KILL_TYPES
              and planner.KILL_TYPES is features.KILL_TYPES,
              "shared object" if world.KILL_TYPES is features.KILL_TYPES
              else "a copy exists: the two can drift")
        check("_params has one owner", planner._params is features._params,
              "shared" if planner._params is features._params else "duplicated")
    except Exception as exc:  # noqa: BLE001
        check("shared vocabulary is importable", False, "%s: %s" % (type(exc).__name__, exc))


def test_a_dead_connection_stops_the_loop() -> None:
    """Falling out of the world mid-run ends the session, it does not become a retry.

    When the game ends a session the page reloads, and every directive after that answers
    into a socket that no longer exists. The loop used to treat that as an ordinary failed
    step and keep going, which is both useless and a good way to look like a bot to a
    server that just closed the door. `connected` is not the signal — it stays true at the
    login screen — so the trigger is leaving the world after having been in it.
    """
    print("\n== dead connection ==")
    import os
    from agent.autopilot import Autopilot
    from agent.runtime import ToolCall

    tmp = Path(tempfile.mkdtemp())

    class Policy:
        def choose(self, goal, observation, tools=None):
            return ToolCall("state.snapshot", {}, goal)

    class Executor:
        def execute(self, tool, args, **kwargs):
            return FakeResult(True)

    class Runtime:
        def __init__(self):
            self.policy = Policy()
            self.executor = Executor()
            self.instance = "test"
            self.calls = 0

        def current_observation(self):
            # Alternate: in the world, then out of it, so the first step sees the
            # transition — which is the signal the loop is meant to react to.
            self.calls += 1
            if self.calls % 2 == 1:
                return {"in_world": True, "position": {"map_id": 150736645, "cell": 328}}, 0.0, "call"
            return ({"in_world": False, "position": {"map_id": None, "cell": None},
                     "last_disconnect": {"reason": "SOCKET_LOST", "via": "disconnect"}},
                    0.0, "call")

        def observe(self, snapshot, source="live"):
            pass

    old = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = str(tmp)
    try:
        pilot = Autopilot(runtime=Runtime(), goals=["do all the quests"],
                          log_path=tmp / "a.jsonl", stop_file=tmp / "AUTOPILOT_STOP",
                          max_steps=0, interval=0.0, preflight=False,
                          settle_s=0)
        seen = {"n": 0}

        def on_step(_step):
            seen["n"] += 1
            if seen["n"] > 12:
                (tmp / "AUTOPILOT_STOP").write_text("enough", encoding="utf-8")

        pilot.on_step = on_step
        pilot.run()
    finally:
        if old is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old

    check("leaving the world stops the loop instead of retrying", seen["n"] <= 3,
          "%d step(s) before it stopped" % seen["n"])


def test_the_model_chain_is_wired() -> None:
    """The network-health model has to be reachable from the tools that quote it.

    This chain has four links and each one has been broken at least once today: a checkpoint
    that predated the architecture, a cadence mismatch between the training corpus and the
    sampling tools, a tool that hardcoded the wrong checkpoint, and a payload that shipped the
    code without the model. A check per link is cheaper than rediscovering them.
    """
    print("\n== the model chain ==")
    try:
        from networkhealth.inference import resolve_checkpoint
    except Exception as exc:  # noqa: BLE001
        check("the resolver imports", False, str(exc))
        return

    path, kind, note = resolve_checkpoint()
    check("a checkpoint resolves", kind in ("local", "global"), "%s: %s" % (kind, note))
    if kind == "missing":
        return

    check("the resolver prefers the local-cadence model when it exists",
          kind == "local" or not (REPO_ROOT / "networkhealth_artifacts" / "local_model" / "model.pt").exists(),
          "kind=%s" % kind)

    # It must actually load against the current code — the original defect.
    try:
        from networkhealth.inference import NetworkHealthInference
        engine = NetworkHealthInference(path)
        check("the resolved checkpoint loads against the current architecture", True,
              "%s, %d-step window" % (path.name, engine.window.seq_len))
    except Exception as exc:  # noqa: BLE001
        check("the resolved checkpoint loads against the current architecture", False,
              str(exc).split("\n")[0][:90])

    # And the tools that quote a forecast must use the resolver, not a hardcoded path.
    hardcoded = []
    for name in ("diagnose_network.py", "score_proxies.py"):
        source = (REPO_ROOT / "ai" / name)
        if not source.exists():
            continue
        text = source.read_text(encoding="utf-8")
        if "resolve_checkpoint" not in text:
            hardcoded.append(name)
    check("both inference tools resolve the checkpoint instead of hardcoding it",
          not hardcoded, ", ".join(hardcoded) if hardcoded else "diagnose_network, score_proxies")


def test_the_emulator_stays_inside_its_box() -> None:
    """Nothing here may change the user's system networking.

    The whole point of the local relay is that it binds a port on 127.0.0.1 and leaves
    Windows routing, DNS and proxy settings alone. One mode broke that rule: `mode=warp`
    shells out to `warp-cli mode proxy`, which takes over routing and DNS for *every*
    program on the machine — so when that tunnel drops, the user's entire internet drops
    with it, and their other apps silently change exit address. That is a failure this
    project has no business causing, and it must stay behind an explicit opt-in.

    This is a source-level check on purpose: it is cheap, it runs in CI, and the failure
    it prevents is invisible at runtime until it has already happened to someone.
    """
    print("\n== stays inside the box ==")
    root = REPO_ROOT.parent
    sources = []
    for pattern in ("desktop/*.cs", "scripts/*.go", "scripts/*.js", "dofus-network/agent/*.py"):
        sources.extend(root.glob(pattern))

    # Each of these changes the machine, not the emulator.
    #
    # The registry values are matched with their quotes: `ProxyServer` on its own also
    # matches the class name `InProcessProxyServer`, and a check that cries wolf on its
    # own source code is a check people learn to ignore.
    forbidden = {
        "netsh ": "changes Windows network configuration",
        "route add": "changes the system routing table",
        '"ProxyEnable"': "writes the system-wide proxy setting",
        '"ProxyServer"': "writes the system-wide proxy setting",
        "Set-DnsClientServerAddress": "changes the system DNS",
        "drivers\\etc\\hosts": "edits the hosts file",
    }

    hits = []
    for path in sources:
        # Skip this file: it contains the needles by definition, and a check that fails on
        # itself is a check that gets deleted.
        if path.name == "smoke_verifier.py":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("//", "*", "#", "/*")):
                continue
            for needle, why in forbidden.items():
                if needle in line:
                    hits.append("%s:%d %r — %s" % (path.name, number, needle, why))
    check("no source changes the system network", not hits,
          "; ".join(hits[:3]) if hits else "%d files scanned" % len(sources))

    # mode=warp is the one exception, and it must be asked for by name.
    engine = root / "desktop" / "farmengine.cs"
    if engine.exists():
        text = engine.read_text(encoding="utf-8", errors="replace")
        guard = text.find("AllowSystemWarp")
        takeover = text.find('RunWarpCli(cli, "mode proxy"')
        check("system-wide WARP is gated behind an explicit opt-in",
              guard != -1 and takeover != -1 and guard < takeover,
              "guard at %d, takeover at %d" % (guard, takeover))

    # The relay must bind loopback, never a routable interface.
    settings = root / "network.ini"
    if settings.exists():
        ini = settings.read_text(encoding="utf-8-sig", errors="replace")
        check("the relay is configured on a local port only",
              "socks_port=" in ini and "base_port=" in ini,
              "socks_port + base_port present")


def test_the_planner_can_see_the_world() -> None:
    """Every key the planner reads must survive the trip from the client.

    `state.snapshot` returns a nested document (`position.map_id`, `character.level`,
    `inventory.items[{gid, qty}]`) and the planner reads flat keys (`map_id`, `level`,
    `inventory[gid]`). On a live client the planner therefore got None for all of them:

        map_id -> None    level -> None    inventory[7405] -> None

    It could not tell which map it was on, so every `required_map` comparison failed, and
    it never saw the items it already carried, so a delivery objective always planned to
    go and farm them again. This is the "it does not understand the situation" complaint
    in code, and the keys are extracted from planner.py rather than listed here, so a new
    read in the planner is covered without anyone remembering to add it.
    """
    print("\n== planner sees the world ==")
    import re as _re

    from agent.observe import flatten
    from agent.planner import QuestPlanner

    # The shape state.snapshot actually produces, taken from the live client.
    snapshot = {
        "in_world": True, "in_fight": False, "in_dialog": False,
        "character": {"id": 4422236, "name": "Aiismine", "level": 18, "breed": 4},
        "position": {"cell": 328, "x": 4, "y": -21, "map_id": 150736645},
        "hp": {"life": 120, "max": 120},
        "quests_active": {"584": 0},
        "current_objective": {"quest_id": 584, "step": 0},
        "step_objectives": [], "ready_objectives": [], "objectives_completed": 3,
        "inventory": {"kamas": 418, "items": [{"gid": 7405, "qty": 2}]},
        "tutorial": {"active": False, "can_change_map": {}, "unblocked_cells": None,
                     "unblocked_npc_id": None},
        "map": {"map_id": 150736645, "neighbours": {"right": 150736646},
                "exits": [], "npcs": [{"npc_id": 1280, "cell": 300}],
                "interactives": []},
    }
    flat = flatten(snapshot)

    check("the map is visible to the planner", flat.get("map_id") == 150736645,
          str(flat.get("map_id")))
    check("the level is visible to the planner", flat.get("level") == 18,
          str(flat.get("level")))
    check("the cell is visible to the planner", flat.get("cell") == 328,
          str(flat.get("cell")))
    inventory = flat.get("inventory") or {}
    check("an item in the bag is visible by id", inventory.get(7405) == 2,
          "gid 7405 -> %r" % inventory.get(7405))
    check("kamas survive the flattening", inventory.get("kamas") == 418,
          str(inventory.get("kamas")))

    # The general form: whatever planner.py asks for, flatten() must supply.
    source = (REPO_ROOT / "agent" / "planner.py").read_text(encoding="utf-8")
    wanted = set(_re.findall(r'obs\.get\("([a-z_]+)"\)', source))
    # Optional by design: the client does not report a slot capacity, and a finished-quest
    # id list is not in the snapshot (the active list already excludes finished quests).
    optional = {"inventory_capacity", "quests_done"}
    missing = sorted(k for k in wanted - optional if k not in flat)
    check("every key the planner reads is present after flatten()", not missing,
          ", ".join(missing) if missing else "%d keys covered" % len(wanted - optional))

    # And the plan that comes out uses the map it can now see.
    planner = QuestPlanner(level_hint=1)
    call = planner.choose("do quests", snapshot, None)
    check("the planner plans against a real map",
          call is not None and (call.args or {}).get("map_id") not in (None, 0),
          "%s %s" % (call.tool if call else None, call.args if call else None))


def test_a_walk_gets_time_to_land() -> None:
    """A directive that starts a walk must not be judged before the walk has moved.

    A live session showed 30 steps of `quest.advance ok / noop`: the handler walks to the
    NPC and talks in the walk callback, so it answers ok immediately, the cell has not
    changed yet, and the verifier scores it `noop`. The controller retried an action that
    was already in flight, forever. Waiting for the promised change is what turns that
    into a real verdict — while a genuinely inert call still ends up `noop`, just later.
    """
    print("\n== settle ==")
    from agent.autopilot import Autopilot

    tmp = Path(tempfile.mkdtemp())

    class Runtime:
        def __init__(self):
            self.n = 0

        def current_observation(self):
            # The move lands on the second look, as a walk does.
            self.n += 1
            cell = 100 if self.n == 1 else 200
            return ({"in_world": True, "position": {"map_id": 1, "cell": cell}},
                    0.0, "call")

        def observe(self, snapshot, source="live"):
            pass

    pilot = Autopilot(runtime=Runtime(), goals=["q"], log_path=tmp / "a.jsonl",
                      stop_file=tmp / "S", max_steps=1, interval=0.0,
                      preflight=False, settle_s=1.0)

    before = {"in_world": True, "position": {"map_id": 1, "cell": 100}}
    settled = pilot._settle(before, dict(before), step_s=0.05)
    check("a move that lands late is seen, not scored as noop",
          (settled or {}).get("position", {}).get("cell") == 200,
          str((settled or {}).get("position")))

    # Nothing ever changes: the wait must end and report the unchanged state, so an
    # inert call is still `noop` rather than hanging the loop.
    class Still(Runtime):
        def current_observation(self):
            self.n += 1
            return ({"in_world": True, "position": {"map_id": 1, "cell": 100}},
                    0.0, "call")

    inert = Autopilot(runtime=Still(), goals=["q"], log_path=tmp / "b.jsonl",
                      stop_file=tmp / "S", max_steps=1, interval=0.0,
                      preflight=False, settle_s=0.2)
    unchanged = inert._settle(before, dict(before), step_s=0.05)
    check("a call that changes nothing still ends as noop",
          (unchanged or {}).get("position", {}).get("cell") == 100,
          str((unchanged or {}).get("position")))


def test_the_loop_uses_the_post_action_state() -> None:
    """The state in a directive's reply is the *after*, and the loop must use it.

    The bridge captures that snapshot immediately after the directive ran. The
    loop ignored it whenever it had supplied its own `before`, then called
    observe(), which handed back the same pre-action snapshot. Every step was
    therefore judged against itself: settle waited on a state that could not
    change, every action scored `noop`, and the controller retried actions that
    had already landed.
    """
    print("\n== post-action state ==")
    from agent.autopilot import Autopilot
    from agent.runtime import ToolCall

    tmp = Path(tempfile.mkdtemp())
    before = {"in_world": True, "position": {"map_id": 1, "cell": 100}}
    after = {"in_world": True, "position": {"map_id": 1, "cell": 200}}

    class Policy:
        def __init__(self):
            self.chosen = 0

        def choose(self, goal, obs, tools):
            self.chosen += 1
            return None if self.chosen > 1 else ToolCall("nav.goto", {"map_id": 1}, goal)

    class Executor:
        def __init__(self):
            self.kw = {}
            self.probes = 0

        def execute(self, tool, args, **kw):
            self.kw = kw

            class Result:
                ok = True
                error = None
                data = {"snapshot": after}
            return Result()

        def snapshot(self, instance=None):
            self.probes += 1
            return after

    class Runtime:
        def __init__(self):
            self.policy = Policy()
            self.executor = Executor()
            self.instance = None
            self.seen = []

        def current_observation(self):
            return before, 1234.0, "carried"

        def observe(self, snapshot, source="live"):
            self.seen.append(snapshot)

        def note_observation(self, snapshot):
            self.seen.append(snapshot)

    rt = Runtime()
    ap = Autopilot(runtime=rt, goals=["q"], log_path=tmp / "a.jsonl",
                   stop_file=tmp / "S", max_steps=1, interval=0.0,
                   preflight=False, settle_s=0.0)
    ap._run()
    step = ap.steps[0]
    check("the step is judged against the post-action state",
          step.verdict == "advanced" and step.observation_cell == 200,
          "%s cell=%s" % (step.verdict, step.observation_cell))
    check("the decision input is the pre-action state, with its real age",
          rt.executor.kw.get("observation") is before
          and rt.executor.kw.get("observation_age_ms") == 1234.0
          and rt.executor.kw.get("observation_source") == "carried",
          str({k: rt.executor.kw.get(k) for k in
               ("observation_age_ms", "observation_source")}))
    check("a reply that carries the state costs no extra probing round trip",
          rt.executor.probes == 0, str(rt.executor.probes))

    class Refusing(Executor):
        def execute(self, tool, args, **kw):
            class Result:
                ok = True
                error = None
                data = {"snapshot": before,
                        "response": {"result": {"ok": False,
                                                "error": "no NPC on this map"}}}
            return Result()

    rt2 = Runtime()
    rt2.executor = Refusing()
    ap2 = Autopilot(runtime=rt2, goals=["q"], log_path=tmp / "b.jsonl",
                    stop_file=tmp / "S", max_steps=1, interval=0.0,
                    preflight=False, settle_s=0.0)
    ap2._run()
    refused = ap2.steps[0]
    check("a game-level rejection is not recorded as a success",
          refused.ok is False and refused.verdict == "failed"
          and "no NPC" in str(refused.error),
          "%s %s %s" % (refused.ok, refused.verdict, refused.error))


def test_the_labeler_does_not_rewind_time() -> None:
    """A retry carries the snapshot it already had; that snapshot is not an after.

    When a claim succeeds the loop's next calls still hold the pre-claim snapshot.
    The labeler used to adopt the first later observation as the after-state, so a
    retry was paired with a state older than the claim that had just cleared it:
    objective None -> objective active, a progress row that never happened. Every
    retry in the burst produced another copy of it.
    """
    print("\n== labeler keeps time forward ==")
    from agent.label_steps import label_file, mark_transitions, summarize

    tmp = Path(tempfile.mkdtemp())
    active = {
        "position": {"map_id": 150736645, "cell": 342},
        "kamas": 418,
        "inventory": {"kamas": 418, "items": [{"gid": 1, "qty": 2}]},
        "current_objective": {"quest_id": "4278", "objective_id": 13986, "status": 2},
        "quests_active": {"4278": 0},
        "ready_objectives": [{"objective_id": 13986, "quest_id": "4278"}],
    }
    cleared = {k: v for k, v in active.items() if k != "current_objective"}
    cleared["current_objective"] = None
    cleared["quests_active"] = {}
    cleared["ready_objectives"] = []

    def step(ts, tool, args, observation, reply):
        return {
            "kind": "step", "ts": ts, "instance": "default",
            "tool": tool, "args": args, "ok": True, "latency_ms": 10.0,
            "observation": observation,
            "result": {"response": {"target": "default", "ok": True,
                                    "result": {"ok": True, "data": reply},
                                    "snapshot": reply}},
        }

    records = [
        step(0.0, "state.snapshot", {}, active, active),
        step(1.0, "quest.advance", {"quest_id": 4278, "objective_id": 13986},
             active, cleared),
        # The loop's refresh still carries the pre-claim snapshot; its own read
        # proves the world has moved on.
        step(2.0, "state.snapshot", {}, active, cleared),
        step(3.0, "quest.advance", {"quest_id": 4278, "objective_id": 13986},
             active, cleared),
        step(4.0, "state.snapshot", {}, active, cleared),
        step(5.0, "state.snapshot", {}, cleared, cleared),
    ]
    path = tmp / "rewind.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    rows = label_file(path)
    unique = mark_transitions(rows)
    summary = summarize(rows, [str(path)], unique)

    def current(row):
        return (row.get("before") or {}).get("current_objective")

    rewinds = [r for r in rows if current(r) is None
               and isinstance((r.get("after") or {}).get("current_objective"), dict)]
    check("one claim is one transition, however many retries follow",
          unique == 1, "%d unique, %d progress rows" % (unique,
              sum(1 for r in rows if r.get("label") == "quest_progress")))
    check("a retry is never credited with an objective the claim already cleared",
          not rewinds, "%d rewound row(s)" % len(rewinds))
    check("the retry is a noop against the claim's own reply",
          rows[3].get("label") == "noop", str(rows[3].get("label")))
    check("the claim itself is the usable row",
          rows[1].get("label") == "quest_progress"
          and summary["usable_sft_rows"] == 1,
          "%s / %d usable" % (rows[1].get("label"), summary["usable_sft_rows"]))


def test_the_planner_can_call_what_it_plans() -> None:
    """Every call the planner produces must pass the contract that will execute it.

    This is the check that was missing, and its absence cost a night. The planner sent
    `quest.advance {quest_id, npc_id}` while the contract declared only
    `{quest_id, step_id}`; it sent `farm.start {monster: 182}` while the contract
    declared `monster` as a *string*; it sent `farm.start {item: 7405}` where no such
    argument existed; and it sent `fight.join {monster: 447}` to a contract that declared
    `target`. Every one of those was rejected before it reached the game:

        refused: schema rejected the call (unknown argument(s) for quest.advance:
                 objective_id)

    The agent then looked incapable while it was doing exactly the right thing, which is
    why the obvious diagnosis — "train it more" — would have been the wrong one.

    The objective types come from the knowledge base rather than a hand-written list, so
    a new type in the KB is covered without anyone remembering to add it here.
    """
    print("\n== plan vs contract ==")
    from agent.planner import QuestPlanner
    from agent.schema import validate_call

    planner = QuestPlanner(level_hint=60)
    types = sorted({int(o["type_id"])
                    for q in planner.quests.values()
                    for st in (q.get("steps") or [])
                    for o in (st.get("objectives") or [])
                    if o.get("type_id") is not None})
    check("the knowledge base has objective types to plan", bool(types), "%d types" % len(types))

    rejected = []
    total = 0
    for type_id in types:
        objective = {"id": 900000 + type_id, "type_id": type_id,
                     "params": {"parameter0": 447, "parameter1": 7405},
                     "map_id": 150736645}
        quest = {"id": 584, "steps": [{"id": 1, "objectives": [objective]}]}
        try:
            calls = planner.plan_objective(quest, 0, objective,
                                           {"map_id": 1, "inventory": {}})
        except Exception as exc:  # noqa: BLE001
            rejected.append("type %d raised %s" % (type_id, type(exc).__name__))
            continue
        for call in calls:
            total += 1
            ok, err = validate_call(call["tool"], call.get("args") or {})
            if not ok:
                rejected.append("type %d -> %s: %s" % (type_id, call["tool"], err))

    check("every planned call passes the contract", not rejected,
          "; ".join(rejected[:3]) if rejected else "%d calls, 0 rejected" % total)

    # The other direction: an argument the client reads but the contract does not declare
    # can never be sent by anyone, which is how npc_id and objective_id stayed invisible.
    js = REPO_ROOT.parent / "scripts" / "mobile-disguise.js"
    if not js.exists():
        print("  [SKIP] client script not found")
        return
    import json as _json
    import re as _re

    caps_path = REPO_ROOT / "data" / "capabilities.json"
    caps = {c["id"]: set((c.get("params") or {}))
            for c in _json.loads(caps_path.read_text(encoding="utf-8"))["capabilities"]}
    text = js.read_text(encoding="utf-8", errors="replace")
    start = text.find("var _agentHandlers = {")
    table = text[start:text.find("\n        };", start)]

    def snake(name: str) -> str:
        return _re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

    undeclared = []
    parts = _re.split(r"\n {12}'([a-z][a-z0-9_.]+)':\s*function", table)
    for i in range(1, len(parts), 2):
        name, code = parts[i], parts[i + 1]
        declared = caps.get(name, set())
        for read in set(_re.findall(r"args\.([A-Za-z_][A-Za-z0-9_]*)", code)):
            if read not in declared and snake(read) not in declared:
                undeclared.append("%s.%s" % (name, read))
    check("the contract declares every argument the client reads", not undeclared,
          ", ".join(sorted(undeclared)) if undeclared else "no hidden arguments")


def test_every_declared_tool_exists_in_the_client() -> None:
    """The capabilities file is a contract; nothing checked it against the client.

    `nav.goto` was declared with `backend: "sentinel"` — a third backend that nothing
    implemented — so every plan that needed to move returned "no client handler for
    tool nav.goto". Since nav.goto is 24-38% of every plan, that alone made the agent
    unable to do anything in the real game while looking perfectly healthy in the
    simulator. A missing handler must be a test failure, not a runtime surprise.
    """
    print("\n== tool contract ==")
    import json
    import re

    caps_path = REPO_ROOT / "data" / "capabilities.json"
    js_path = REPO_ROOT.parent / "scripts" / "mobile-disguise.js"
    if not caps_path.exists() or not js_path.exists():
        print("  [SKIP] capabilities or client script not found")
        return

    caps = json.loads(caps_path.read_text(encoding="utf-8"))["capabilities"]
    text = js_path.read_text(encoding="utf-8")
    start = text.find("_agentHandlers")
    brace = text.find("{", start)
    depth = 0
    block = ""
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                block = text[brace:i + 1]
                break
    # Handler entries sit at the table's own indentation. Matching any depth picks up
    # nested keys inside a handler's return value, which is how a diagnostic's own
    # payload started looking like a phantom tool.
    handlers = set(re.findall(r"^ {12}['\"]([a-z][a-z0-9_.]+)['\"]\s*:", block,
                              re.MULTILINE))

    declared = {c["id"]: c for c in caps}
    directive = {t for t, c in declared.items() if c.get("backend") == "directive"}
    local = {t for t, c in declared.items() if c.get("backend") == "local"}
    other = set(declared) - directive - local

    check("no tool claims a backend that does not exist", not other,
          str(sorted(other)) if other else "directive | local only")
    missing = sorted(directive - handlers)
    check("every directive tool has a client handler", not missing,
          str(missing) if missing else "%d of %d" % (len(directive), len(directive)))
    check("travel is one of them", "nav.goto" in handlers,
          "nav.goto %s" % ("present" if "nav.goto" in handlers else "MISSING"))
    # debug.* handlers, and the advisor's read-only market captures, are
    # diagnostics rather than agent tools: they exist to ask the client what it
    # actually contains when a reader is returning nulls. `advisor/cli.py` calls
    # market.capture directly over the bridge; it is deliberately not in the
    # model's action space.
    diagnostic = {"hdv.prices", "market.capture"}
    phantom = sorted(h for h in (handlers - directive - local)
                     if not h.startswith("debug.") and h not in diagnostic)
    check("the client has no handlers the contract does not declare", not phantom,
          str(phantom) if phantom else "none")


def test_a_ready_objective_is_claimed_first() -> None:
    """objectiveStatus 2 means the game already accepts it — claiming is free progress.

    The planner had no notion of a ready objective, so a quest whose conditions were met
    looked exactly like one that had not started, and the only way it ever advanced was
    by luck. It now claims the first ready objective before planning anything else.
    """
    print("\n== ready objectives ==")
    from agent.planner import QuestPlanner

    p = QuestPlanner(level_hint=60)
    obs = {"level": 60, "map_id": 150736645, "inventory": {}, "quests_active": {},
           "ready_objectives": [{"quest_id": 584, "objective_id": 9001}]}
    call = p.choose("do quests", obs, None)
    check("a ready objective is validated first",
          call is not None and call.tool == "quest.advance"
          and call.args.get("objective_id") == 9001,
          "%s %s" % (call.tool if call else None, call.args if call else None))

    p2 = QuestPlanner(level_hint=60)
    obs2 = {"level": 60, "map_id": 150736645, "inventory": {}, "quests_active": {},
            "ready_objectives": [{"quest_id": "bad"}]}
    call2 = p2.choose("do quests", obs2, None)
    check("a malformed ready entry does not stop the plan", call2 is not None,
          call2.tool if call2 else "no call")


def test_objectives_the_kb_lacks() -> None:
    """A quest the cached tables do not have is still plannable.

    The tutorial the client starts on (3841) is newer than `quests_full.json`, so the
    knowledge base had no row for it. The planner therefore had no type to translate and
    could only re-send `QuestObjectiveValidationMessage`, which the server ignored — the
    live run spent its entire budget on objective 13493 that way.

    The client's own `dbObjectives` carries the type, parameters and map, and the
    snapshot now reports them. These checks pin both halves: the snapshot must include
    the metadata, and the planner must use it.
    """
    print("\n== objectives the KB lacks ==")
    from agent.planner import CLAIM_ACTION_LIMIT, QuestPlanner

    script = REPO_ROOT.parent / "scripts" / "mobile-disguise.js"
    if script.exists():
        text = script.read_text(encoding="utf-8", errors="replace")
        check("the snapshot reports the objective's own type and parameters",
              "dbObjectives" in text and "type_id" in text and "_objParams" in text,
              "dbObjectives/type_id present" if "dbObjectives" in text else "metadata missing")
        # 1.74 keeps the dialog's reply sender on gui.npcDialogHandler; requiring
        # the view (npcDialogUi) failed every open dialog in a live session.
        check("quest.advance can answer through npcDialogHandler",
              "addOwner('gui.npcDialogHandler'" in text
              and "typeof owner._uiReplyHandler === 'function'" in text
              and "owner.replyHandler._uiReplyHandler" in text,
              "handler fallback present"
              if "addOwner('gui.npcDialogHandler'" in text else "handler fallback missing")

    qid = 999999
    p = QuestPlanner(level_hint=1)
    check("the synthetic quest is genuinely absent from the KB", qid not in p.quests,
          "%d quests" % len(p.quests))

    obs = {"level": 1, "map_id": 1, "inventory": {}, "quests_active": {str(qid): 0},
           "current_objective": {"quest_id": str(qid), "objective_id": 1, "status": 1,
                                 "map_id": 12345, "type_id": 4},
           "step_objectives": [{"id": 1, "status": 1, "done": False,
                                "map_id": 12345, "type_id": 4}]}
    call = p.choose("do quests", obs, None)
    check("an unknown quest's map objective becomes nav.goto",
          call is not None and call.tool == "nav.goto" and call.args.get("map_id") == 12345,
          "%s %s" % (call.tool if call else None, call.args if call else None))

    # The live stuck loop. Quest 3841's ready DiscoverMap objective carried
    # 698953 in `parameters.parameter0` — the text id of the map's *name*, not a
    # map id (the DB's `mapId` field is the place, and it was absent). Reading
    # the text id as a destination made the client reject nav.goto nine times.
    live = {"level": 2, "map_id": 147063812, "inventory": {},
            "quests_active": {"3841": 0},
            "map": {"map_id": 147063812,
                    "exits": [{"direction": "left", "map_id": 147063300},
                              {"direction": "right", "map_id": 147064324}]},
            "current_objective": {"quest_id": "3841", "objective_id": 13541,
                                  "status": 2, "type_id": 4,
                                  "params": {"parameter0": 698953}},
            "ready_objectives": [{"quest_id": 3841, "objective_id": 13541, "status": 2,
                                  "type_id": 4, "params": {"parameter0": 698953}}],
            "step_objectives": [{"id": 13541, "status": 2, "done": False, "type_id": 4,
                                 "params": {"parameter0": 698953}}]}
    p7 = QuestPlanner(level_hint=2)
    first = p7.choose("do quests", live, None)
    check("a type-4 text id is never sent as a map id",
          first is not None and first.tool == "quest.advance"
          and first.args.get("objective_id") == 13541,
          "%s %s" % (first.tool if first else None, first.args if first else None))
    p7.note_result(first.tool, False, "objective_server_counted", live)
    live_call = p7.choose("do quests", live, None)
    check("after a refused DiscoverMap claim the agent walks an exit",
          live_call is not None and live_call.tool == "nav.goto"
          and live_call.args.get("map_id") is None
          and live_call.args.get("direction") in ("left", "right"),
          "%s %s" % (live_call.tool if live_call else None,
                     live_call.args if live_call else None))

    adj = dict(live,
               current_objective={"quest_id": "3841", "objective_id": 13541,
                                  "status": 2, "type_id": 4, "map_id": 147064324,
                                  "params": {"parameter0": 698953}},
               ready_objectives=[{"quest_id": 3841, "objective_id": 13541, "status": 2,
                                  "type_id": 4, "map_id": 147064324,
                                  "params": {"parameter0": 698953}}],
               step_objectives=[{"id": 13541, "status": 2, "done": False,
                                 "map_id": 147064324, "type_id": 4,
                                 "params": {"parameter0": 698953}}])
    adj_call = QuestPlanner(level_hint=2).choose("do quests", adj, None)
    check("a target that is one of this map's exits is still sent by id",
          adj_call is not None and adj_call.args.get("map_id") == 147064324,
          "%s %s" % (adj_call.tool if adj_call else None,
                     adj_call.args if adj_call else None))

    p8 = QuestPlanner(level_hint=2)
    claim = p8.choose("do quests", live, None)
    p8.note_result(claim.tool, False, "objective_server_counted", live)
    probe = None
    for _ in range(CLAIM_ACTION_LIMIT + 1):
        probe = p8.choose("do quests", live, None)
        if probe is None or 3841 in p8._skipped:
            break
        p8.note_result(probe.tool, True, "crossed", live)
    check("exploration that never changes map is bounded",
          3841 in p8._skipped,
          "%s %s" % (probe.tool if probe else None, probe.args if probe else None))

    obs_talk = dict(obs, current_objective={"quest_id": str(qid), "objective_id": 2,
                                            "status": 1, "type_id": 9,
                                            "params": {"parameter0": 77}},
                    step_objectives=[{"id": 2, "status": 1, "done": False, "type_id": 9,
                                      "params": {"parameter0": 77}}])
    call = QuestPlanner(level_hint=1).choose("do quests", obs_talk, None)
    check("an unknown quest's talk objective names its NPC",
          call is not None and call.tool == "quest.advance"
          and call.args.get("npc_id") == 77,
          "%s %s" % (call.tool if call else None, call.args if call else None))

    # A ready objective that does not move after validation: talk, and keep
    # taking the action while it is accepted. Retirement needs evidence from
    # `note_result`, not the passage of a plan pass: the safety guard refuses
    # server-counted claims, and retiring after one fallback action would lose
    # the quest the guard just protected.
    p3 = QuestPlanner(level_hint=1)
    ready = {"level": 1, "map_id": 147062786, "inventory": {},
             "quests_active": {str(qid): 0},
             "current_objective": {"quest_id": str(qid), "objective_id": 3, "status": 2,
                                   "type_id": 1, "params": {"parameter0": 4231}},
             "ready_objectives": [{"quest_id": str(qid), "objective_id": 3, "status": 2,
                                   "type_id": 1, "params": {"parameter0": 4231}}],
             "step_objectives": [{"id": 3, "status": 2, "done": False, "type_id": 1,
                                  "params": {"parameter0": 4231}}]}
    first = p3.choose("do quests", ready, None)
    check("a ready objective is validated once",
          first is not None and first.args.get("objective_id") == 3,
          "%s %s" % (first.tool if first else None, first.args if first else None))
    p3.note_result(first.tool, False, "claim refused", ready)
    second = p3.choose("do quests", ready, None)
    check("a refused claim falls back to the NPC",
          second is not None and second.args.get("npc_id") == 4231
          and "objective_id" not in second.args,
          "%s %s" % (second.tool if second else None, second.args if second else None))
    p3.note_result(second.tool, True, "talked", ready)
    third = p3.choose("do quests", ready, None)
    check("an accepted fallback keeps the quest on its action path",
          third is not None and third.args.get("npc_id") == 4231,
          "%s %s" % (third.tool if third else None, third.args if third else None))
    for _ in range(3):
        p3.note_result(third.tool, False, "no NPC on this map", ready)
        third = p3.choose("do quests", ready, None)
    check("three hard failures in a row retire the quest",
          third is None or third.args.get("quest_id") != qid,
          "%s %s" % (third.tool if third else None, third.args if third else None))

    # The bound has to hold even when every action reports success: a tool that is
    # accepted but never changes anything would otherwise loop forever.
    p5 = QuestPlanner(level_hint=1)
    p5.choose("do quests", ready, None)
    p5.note_result("quest.advance", False, "claim refused", ready)
    call = None
    for _ in range(CLAIM_ACTION_LIMIT):
        call = p5.choose("do quests", ready, None)
        if call is not None:
            p5.note_result(call.tool, True, "accepted", ready)
    after = p5.choose("do quests", ready, None)
    check("an accepted action that never changes anything is bounded",
          after is None or after.args.get("quest_id") != qid,
          "%s %s" % (after.tool if after else None, after.args if after else None))

    # Crossing the world is progress, not an inert loop. A destination more than
    # CLAIM_ACTION_LIMIT maps away must not exhaust the bound before arrival.
    p6 = QuestPlanner(level_hint=1)
    travel = {"level": 1, "map_id": 100, "inventory": {}, "quests_active": {},
              "ready_objectives": [{"quest_id": 999997, "objective_id": 5, "status": 2,
                                    "type_id": 4, "map_id": 200,
                                    "params": {"parameter0": 55374}}],
              "step_objectives": []}
    crossed = 0
    for _ in range(12):
        travel["map_id"] = 100 + crossed
        call = p6.choose("do quests", travel, None)
        if call is None or 999997 in p6._skipped:
            break
        p6.note_result(call.tool, True, "moved", travel)
        crossed += 1
    check("map progress resets the action bound", crossed == 12,
          "%d crossings before the puzzle was abandoned" % crossed)

    # Not every ready objective is a conversation. The fallback is whatever the
    # objective's own type translates to, so a ready kill is fought, not re-validated.
    p4 = QuestPlanner(level_hint=1)
    ready_kill = {"level": 1, "map_id": 147062786, "inventory": {},
                  "quests_active": {}, "current_objective": None,
                  "ready_objectives": [{"quest_id": 999998, "objective_id": 4,
                                        "status": 2, "type_id": 6,
                                        "params": {"parameter0": 182}}],
                  "step_objectives": []}
    p4.choose("do quests", ready_kill, None)
    kill = p4.choose("do quests", ready_kill, None)
    check("a ready kill falls back to the fight, not another claim",
          kill is not None and kill.tool == "fight.join" and kill.args.get("monster") == 182,
          "%s %s" % (kill.tool if kill else None, kill.args if kill else None))


def test_the_tutorial_is_the_authority() -> None:
    """While a tutorial step runs, its own gate decides what can happen next.

    A live session walked left because the exit list is ordered left/right/top/
    bottom and `_explore_call` took the first entry. The tutorial wanted the right
    edge, and the crossing it forbade could not happen at all. Worse, the client's
    `gotoNeighbourMap` bypasses the scenario gate: it walks to the border and asks
    for a transition the tutorial refuses, which looks like a stuck character.
    These checks pin the whole chain: the observation marks blocked exits, the
    planner reads the step's NPC/cell gate before the quest graph, retirement is
    undone when the situation changes, and the loop never blames an unrelated quest.
    """
    print("\n== tutorial gate ==")
    from types import SimpleNamespace

    from agent.autopilot import Autopilot, _map_of
    from agent.executor import _MapGraph, _obs_map_exits
    from agent.planner import QuestPlanner

    exits = {"map": {"exits": [
        {"direction": "left", "map_id": 1, "blocked": True},
        {"direction": "right", "map_id": 2},
    ]}}
    check("a tutorial-blocked exit is not offered to the planner",
          {e["direction"] for e in QuestPlanner._observed_exits(exits)} == {"right"},
          str(QuestPlanner._observed_exits(exits)))
    check("the executor does not route through a blocked exit",
          _obs_map_exits(exits) == {2: "right"}, str(_obs_map_exits(exits)))

    hinted = {"type_id": 4, "text": "Cliquez sur le rebord droit"}
    pick = QuestPlanner._explore_call(hinted, {
        "map": {"map_id": 100, "exits": [
            {"direction": "left", "map_id": 1},
            {"direction": "right", "map_id": 2},
        ]}})
    check("exploration follows the edge the objective's text names",
          pick is not None and pick["args"].get("direction") == "right",
          str(pick and pick["args"]))
    blocked_only = QuestPlanner._explore_call({"type_id": 4}, {
        "map": {"map_id": 100, "exits": [
            {"direction": "left", "map_id": 1},
            {"direction": "right", "map_id": 2, "blocked": True},
        ]}})
    check("exploration never picks a blocked crossing",
          blocked_only is not None and blocked_only["args"].get("direction") == "left",
          str(blocked_only and blocked_only["args"]))
    held = QuestPlanner._travel_call({"type_id": 4}, 2, {
        "map_id": 100,
        "map": {"exits": [
            {"direction": "left", "map_id": 2, "blocked": True},
            {"direction": "top", "map_id": 9},
        ]}})
    check("a forbidden crossing waits instead of walking the contrary way",
          held.get("tool") == "state.snapshot", str(held))

    tmp = Path(tempfile.mkdtemp())
    graph_path = tmp / "maps.json"
    graph_path.write_text(json.dumps({
        "1": {"id": 1, "neighbours": {"leftNeighbourId": 2, "rightNeighbourId": 3}},
        "2": {"id": 2, "neighbours": {"rightNeighbourId": 1}},
        "3": {"id": 3, "neighbours": {"leftNeighbourId": 1}},
    }), encoding="utf-8")
    graph = _MapGraph(path=graph_path)
    check("a route avoids a blocked first edge when another exists",
          graph.hop(1, 3, blocked_first={"left"}) == ("right", 3, 1),
          str(graph.hop(1, 3, blocked_first={"left"})))
    check("a route with only a blocked first edge is not routable",
          graph.hop(1, 2, blocked_first={"left"}) is None,
          str(graph.hop(1, 2, blocked_first={"left"})))

    def tutorial_obs(**over):
        obs = {"level": 2, "map_id": 100, "cell": 200, "inventory": {},
               "quests_active": {"3841": 0},
               "current_objective": {"quest_id": "3841", "objective_id": 13493,
                                     "step": 1, "status": 1, "type_id": 1,
                                     "params": {"parameter0": 3888}},
               "step_objectives": [{"id": 13493, "status": 1, "done": False,
                                    "type_id": 1, "params": {"parameter0": 3888}}],
               "tutorial": {"active": True, "quest_id": 3841, "step_id": 1,
                            "can_change_map": {"left": False, "right": False,
                                               "top": False, "bottom": False},
                            "unblocked_cells": [], "unblocked_npc_id": 3888}}
        obs.update(over)
        return obs

    call = QuestPlanner(level_hint=2).choose("do quests", tutorial_obs(), None)
    check("the tappable tutorial NPC is talked to before any quest-graph plan",
          call is not None and call.tool == "quest.advance"
          and call.args.get("npc_id") == 3888 and call.args.get("quest_id") == 3841,
          "%s %s" % (call.tool if call else None, call.args if call else None))

    movement = tutorial_obs(tutorial={"active": True, "quest_id": 3841, "step_id": 2,
                                      "can_change_map": {"left": False, "right": True,
                                                         "top": False, "bottom": False},
                                      "unblocked_cells": [317, 318],
                                      "unblocked_npc_id": 0})
    call = QuestPlanner(level_hint=2).choose("do quests", movement, None)
    check("the tutorial's allowed cells become the walk target",
          call is not None and call.tool == "nav.goto"
          and call.args.get("cell") in (317, 318),
          "%s %s" % (call.tool if call else None, call.args if call else None))

    waiting = tutorial_obs(quests_active={"999999": 0}, current_objective={},
                           step_objectives=[], tutorial={
                               "active": True, "quest_id": 3841, "step_id": 3,
                               "can_change_map": {"left": False, "right": False,
                                                  "top": False, "bottom": False},
                               "unblocked_cells": [], "unblocked_npc_id": 0})
    call = QuestPlanner(level_hint=2).choose("do quests", waiting, None)
    check("no unrelated quest is started while the tutorial is active",
          call is not None and call.tool == "state.snapshot",
          "%s %s" % (call.tool if call else None, call.args if call else None))

    p = QuestPlanner(level_hint=2)
    before = tutorial_obs()
    p._retire(3841, before)
    check("a tutorial-stalled quest can be retired", 3841 in p._skipped,
          str(sorted(p._skipped)))
    p._reopen(tutorial_obs(map_id=101))
    check("changing map reopens the quest the tutorial stalled", 3841 not in p._skipped,
          str(sorted(p._skipped)))
    p._skipped.add(424242)
    p._reopen(tutorial_obs(map_id=102))
    check("a permanently unplannable quest stays retired", 424242 in p._skipped,
          str(sorted(p._skipped)))

    check("the logged step now carries the nested map id",
          _map_of({"position": {"map_id": 150736645, "cell": 328}}) == 150736645,
          str(_map_of({"position": {"map_id": 150736645, "cell": 328}})))

    class Policy:
        current_quest = None

        def __init__(self):
            self.skipped = []

        def skip(self, quest_id):
            self.skipped.append(quest_id)

    policy = Policy()
    pilot = SimpleNamespace(runtime=SimpleNamespace(policy=policy))
    blocker = SimpleNamespace(args={})
    retired = Autopilot._retire_named_quest(
        pilot, blocker, {"quests_active": {"3841": 0}})
    check("a stuck call with no quest id does not retire the first active quest",
          retired is None and policy.skipped == [],
          "retired=%s skipped=%s" % (retired, policy.skipped))
    policy.current_quest = 3841
    retired = Autopilot._retire_named_quest(
        pilot, blocker, {"quests_active": {"584": 0}})
    check("a stuck call retires the quest the policy planned for",
          retired == 3841 and policy.skipped == [3841],
          "retired=%s skipped=%s" % (retired, policy.skipped))


def test_a_quest_the_server_never_starts() -> None:
    """`ok` from the client means "start request sent", not "quest active".

    A live session spent every step after its first fight re-sending quest 584: the
    client returned ok, the server never activated it, and the planner had no memory
    of its own starts. `quests_active` is the only evidence that counts, and the
    autopilot must retire the quest the repeated call names -- it used to retire the
    first *active* quest (3841) instead, leaving the dead start in the curriculum.
    """
    print("\n== quest start ==")
    from agent.controller import Controller, error_class
    from agent.planner import START_ATTEMPT_LIMIT, QuestPlanner

    script = REPO_ROOT.parent / "scripts" / "mobile-disguise.js"
    text = script.read_text(encoding="utf-8", errors="replace")
    check("the fight gate reads the character's life state, not playerData.lifePoints",
          "pdF.lifePoints === 0" not in text and "_agentLife()" in text,
          "stale playerData.lifePoints check removed" if "_agentLife()" in text
          else "fight.join still trusts the stale field")
    check("the snapshot reports whether the character is alive",
          "snap.alive" in text and "snap.life_state" in text,
          "alive/life_state present" if "snap.alive" in text else "missing")
    check("a quest start is verified against the client's quest log",
          "__agentQuestStarts" in text and "not_activated: true" in text,
          "start request tracked" if "__agentQuestStarts" in text else "no verification")

    def obs_with(active):
        return {"level": 1, "map_id": 1, "inventory": {},
                "quests_active": active, "quests_done": []}

    def first_startable(planner):
        for candidate in planner._curriculum(1):
            if planner._plannable(candidate):
                return candidate
        return None

    p = QuestPlanner(level_hint=1)
    qid = first_startable(p)
    check("the curriculum has a quest to start", qid is not None, str(qid))
    if qid is not None:
        p._curriculum = lambda level: [qid]
        call = p.choose("do quests", obs_with({}), None)
        check("an inactive quest is started with quest.advance",
              call is not None and call.tool == "quest.advance"
              and call.args.get("quest_id") == qid,
              "%s %s" % (call.tool if call else None, call.args if call else None))
        # The first report after a start is taken from the snapshot the decision was
        # made from, so it cannot show the activation. Evidence starts with the next
        # observation; retirement therefore needs one more attempt than the limit.
        for _ in range(START_ATTEMPT_LIMIT + 1):
            p.note_result("quest.advance", True, "start sent", obs_with({}))
            call = p.choose("do quests", obs_with({}), None)
        check("a quest the server never activates is retired",
              qid in p._skipped,
              "skipped=%s next=%s" % (sorted(p._skipped)[:5],
                                      call.args if call else None))

    p2 = QuestPlanner(level_hint=1)
    qid2 = first_startable(p2)
    if qid2 is not None:
        p2._curriculum = lambda level: [qid2]
        p2.choose("do quests", obs_with({}), None)
        p2.note_result("quest.advance", True, "started", obs_with({str(qid2): 0}))
        check("an activated start is not counted against the quest",
              not p2._start_failures and qid2 not in p2._skipped,
              "failures=%s" % p2._start_failures)

    guard = ("objective 13499 (QuestObjectiveFightMonster) is completed by the server; "
             "perform the action instead of claiming it")
    check("a server-counted claim is a rule refusal", error_class(guard) == "refused",
          error_class(guard))
    c = Controller(state_dir=Path(tempfile.mkdtemp()), session="refusal")
    d = c.on_step(Verdict("failed", 0.9, None, False, "rule"), "quest.advance", False,
                  guard, {}, {})
    check("a rule refusal is not retried", d.action == "continue",
          "%s/%s" % (d.action, d.cause))

    class _Policy:
        def __init__(self):
            self.skipped = []

        def skip(self, quest_id):
            self.skipped.append(quest_id)

    class _Fake:
        pass

    fake = _Fake()
    fake.runtime = _Fake()
    fake.runtime.policy = _Policy()
    named = _Fake()
    named.args = {"quest_id": 584}
    retired = Autopilot._retire_named_quest(
        fake, named, {"quests_active": {"3841": 0}})
    check("a repeated start retires the quest it names, not the active one",
          retired == 584 and fake.runtime.policy.skipped == [584],
          "retired=%s skipped=%s" % (retired, fake.runtime.policy.skipped))


def test_verifier_reads_the_real_observation_shape() -> None:
    """The rules compared flat keys against a nested snapshot and saw no change.

    The snapshot is `{position: {map_id, cell}, hp: {life}, inventory: {items}}`, but
    STATE_KEYS asked for `map_id`, `cell`, `hp`, `inventory_size` at the top level. Both
    sides were None for every key, so `_changed` was always empty, every step scored
    `noop`, and the loop could not tell a real map change from standing still. This is
    the bug that made the agent look incapable while it was actually moving.
    """
    print("\n== observation shape ==")
    from agent.verifier import _changed

    before = {"position": {"map_id": 150736645, "cell": 328},
              "hp": {"life": 120}, "inventory": {"items": [1, 2]}, "in_dialog": False}
    after_cell = {"position": {"map_id": 150736645, "cell": 402},
                  "hp": {"life": 120}, "inventory": {"items": [1, 2]}, "in_dialog": False}
    after_map = {"position": {"map_id": 150736646, "cell": 328},
                 "hp": {"life": 120}, "inventory": {"items": [1, 2]}, "in_dialog": False}
    after_dialog = {"position": {"map_id": 150736645, "cell": 328},
                    "hp": {"life": 120}, "inventory": {"items": [1, 2]}, "in_dialog": True}

    check("a moved cell is a change", "cell" in _changed(before, after_cell),
          str(_changed(before, after_cell)))
    check("a changed map is a change", "map_id" in _changed(before, after_map),
          str(_changed(before, after_map)))
    check("an opened dialog is a change", "in_dialog" in _changed(before, after_dialog),
          str(_changed(before, after_dialog)))
    check("standing still is not a change", not _changed(before, dict(before)),
          str(_changed(before, dict(before))))

    # Claiming an objective changes no cell, no map and no HP. It moves an entry off the
    # ready list, and that is the only observable proof it worked. Without this key the
    # most valuable action the agent has scored `noop` every time, and the controller
    # retired a quest it had just advanced.
    claim_before = {"ready_objectives": [{"quest_id": 4278, "objective_id": 13986}],
                    "objectives_completed": 3, "quests_active": {"4278": 0}}
    claim_after = {"ready_objectives": [], "objectives_completed": 3,
                   "quests_active": {"4278": 0}}
    check("claiming an objective counts as progress",
          "ready_objectives" in _changed(claim_before, claim_after),
          str(_changed(claim_before, claim_after)))
    step_after = {"ready_objectives": [], "objectives_completed": 4,
                  "quests_active": {"4278": 0}}
    check("finishing a step counts as progress",
          "objectives_completed" in _changed(claim_before, step_after),
          str(_changed(claim_before, step_after)))


def test_unlimited_means_unlimited() -> None:
    """An Auto run must not stop after eight steps because a default said so.

    `steps_per_goal` was a per-goal slice from Order mode, applied even when the user
    asked for an unlimited budget. One goal therefore meant eight directives and exit
    0, which is indistinguishable from "the AI does nothing". This drives the loop with
    a policy that never makes progress and checks it keeps going past the old cap.
    """
    print("\n== unlimited budget ==")
    import os
    from agent.autopilot import Autopilot
    from agent.runtime import ToolCall

    tmp = Path(tempfile.mkdtemp())
    # objectives_completed never rises: a stuck quest by definition.
    obs = {"map_id": 150736645, "cell": 328, "kamas": 418, "level": 18,
           "quests_active": {"584": 0}, "inventory": {}, "objectives_completed": 3}

    class Policy:
        def __init__(self):
            self.skipped = []

        def choose(self, goal, observation, tools=None):
            return ToolCall("quest.advance", {"quest_id": 584}, goal)

        def skip(self, quest_id):
            self.skipped.append(quest_id)

    class Executor:
        def execute(self, tool, args, **kwargs):
            return FakeResult(True)

    class Runtime:
        def __init__(self):
            self.policy = Policy()
            self.executor = Executor()
            self.instance = "test"
            self._obs = obs

        def current_observation(self):
            return self._obs, 0.0, "call"

        def observe(self, snapshot, source="live"):
            self._obs = snapshot

    old = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = str(tmp)
    try:
        stop = tmp / "AUTOPILOT_STOP"
        pilot = Autopilot(runtime=Runtime(), goals=["do all the quests"],
                          log_path=tmp / "a.jsonl", stop_file=stop,
                          max_steps=0, interval=0.0, preflight=False)
        seen = {"n": 0}

        def on_step(_step):
            seen["n"] += 1
            if seen["n"] >= 12:
                stop.write_text("enough", encoding="utf-8")

        pilot.on_step = on_step
        pilot.run()
    finally:
        if old is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old

    check("an unlimited run passes the old 8-step cap", seen["n"] >= 12,
          "%d steps" % seen["n"])
    check("a stuck quest is retired rather than ending the session",
          len(pilot.runtime.policy.skipped) >= 1,
          "skipped %s" % pilot.runtime.policy.skipped)


def test_structural_failures_are_not_retried() -> None:
    """Real data: 9 of 15 client failures were 'no active client', 0/9 retries worked."""
    print("\n== error taxonomy ==")
    from agent.controller import Controller, error_class

    check("no client is structural",
          error_class("client directive failed: instance 'x' is not an active client window")
          == "structural")
    check("a timeout is a transport failure",
          error_class("The operation has timed out") == "transport")
    check("an in-game refusal is an action failure",
          error_class("resisted") == "action")

    tmp = Path(tempfile.mkdtemp())

    # The measured claim: after an in-game refusal, continuing beats re-issuing.
    c3 = Controller(state_dir=tmp, session="tz")
    d3 = c3.on_step(Verdict("failed", 0.9, None, False, "rule"), "farm.start", False,
                    "resisted", {}, {})
    check("an in-game refusal continues instead of retrying", d3.action == "continue",
          "%s/%s" % (d3.action, d3.cause))

    c = Controller(state_dir=tmp, session="tx")
    d = c.on_step(Verdict("failed", 0.9, None, False, "rule"), "state.snapshot", False,
                  "client directive failed: no client instance is running", {}, {})
    check("a structural failure replans instead of retrying",
          d.action == "replan" and d.cause == "unavailable", "%s/%s" % (d.action, d.cause))

    c2 = Controller(state_dir=tmp, session="ty")
    d2 = c2.on_step(Verdict("failed", 0.9, None, False, "rule"), "farm.start", False,
                    "The operation has timed out", {}, {})
    check("a transport failure still retries once", d2.action == "retry",
          "%s/%s" % (d2.action, d2.cause))


def test_learned_planner_matches_the_teacher() -> None:
    """The tool choice is a model now; it must not be worse than the if-chain."""
    print("\n== learned planner ==")
    try:
        from agent.planner_nn import NNPlanner
        from sim.run import run
        from agent.planner import QuestPlanner
    except Exception as exc:  # noqa: BLE001
        print("  [SKIP] %s" % exc)
        return

    nn = NNPlanner(level_hint=60)
    if nn.session is None:
        print("  [SKIP] %s" % nn.note)
        return

    teacher = run(QuestPlanner(level_hint=60), level=60, steps=200)
    student = run(NNPlanner(level_hint=60), level=60, steps=200)
    check("the learned planner still completes quests", student["quests"] > 0,
          "%d quests" % student["quests"])
    check("and is not worse than the hardcoded table",
          student["quests"] >= teacher["quests"],
          "%d vs %d" % (student["quests"], teacher["quests"]))


def test_learned_navigation_cannot_bypass_the_exit_rule() -> None:
    """A learned nav.goto must cross a real edge, never a non-adjacent map id."""
    print("\n== learned navigation ==")
    try:
        from agent.planner_nn import NNPlanner
    except Exception as exc:  # noqa: BLE001
        print("  [SKIP] %s" % exc)
        return

    nn = NNPlanner(level_hint=60)
    nn._choose_tool = lambda *a, **k: "nav.goto"  # type: ignore[assignment]
    here = 147063812
    obs = {"map_id": here,
           "map": {"map_id": here, "exits": [
               {"direction": "left", "map_id": 987654321},
               {"direction": "bottom", "map_id": 987654322}]}}

    # Type 4 whose parameter0 is a map *name* id: the objective names no place.
    generic = {"type_id": 4, "text": "Discover a map",
               "parameters": {"parameter0": 698953}}
    plan = nn.plan_objective({}, 0, generic, obs)
    check("a learned generic DiscoverMap walks an observed exit",
          len(plan) == 1 and plan[0]["tool"] == "nav.goto"
          and plan[0]["args"] == {"direction": "left"},
          str(plan))

    # A named target the graph does not know and the client cannot cross to.
    unknown = {"type_id": 4, "map_id": 987654399, "text": "Discover a map"}
    plan = nn.plan_objective({}, 0, unknown, obs)
    check("a learned unknown destination falls back to an exit",
          len(plan) == 1 and plan[0]["tool"] == "nav.goto"
          and plan[0]["args"] == {"direction": "left"}
          and "map_id" not in plan[0]["args"],
          str(plan))

    adjacent = {"type_id": 4, "map_id": 987654321, "text": "Discover a map"}
    plan = nn.plan_objective({}, 0, adjacent, obs)
    check("a learned adjacent destination keeps its map id",
          len(plan) == 1 and plan[0]["args"] == {"map_id": 987654321},
          str(plan))

    plan = nn.plan_objective({}, 0, generic,
                             {"map_id": here, "map": {"map_id": here, "exits": []}})
    check("a learned walk with no observed exit plans nothing", plan == [], str(plan))

    from agent.knowledge_store import KnowledgeStore
    store = KnowledgeStore(Path(tempfile.mkdtemp()) / "k.jsonl")
    store.remember("npc_map", "npc:447", 147063300, source="observed", confidence=0.8)
    talker = NNPlanner(level_hint=60, store=store)
    talker._choose_tool = lambda *a, **k: "nav.goto"  # type: ignore[assignment]
    talk = {"type_id": 1, "params": {"parameter0": 447}}
    npc_obs = {"map_id": here, "map": {"map_id": here, "exits": [
        {"direction": "right", "map_id": 147063300}]}}
    plan = talker.plan_objective({"id": 9, "level_min": 60}, 0, talk, npc_obs)
    check("a learned walk to a known NPC uses the stored map",
          len(plan) == 1 and plan[0]["args"] == {"map_id": 147063300}, str(plan))

    plan = talker.plan_objective({"id": 9, "level_min": 60}, 0, talk,
                                 {"map_id": 147063300,
                                  "map": {"map_id": 147063300, "exits": []}})
    check("a learned walk when already at the NPC talks instead",
          len(plan) == 1 and plan[0]["tool"] == "quest.advance"
          and plan[0]["args"].get("npc_id") == 447, str(plan))


def test_knowledge_store() -> None:
    """Knowledge lives in the store, so the model can stay small."""
    print("\n== knowledge store ==")
    from agent.knowledge_store import KnowledgeStore

    tmp = Path(tempfile.mkdtemp())
    store = KnowledgeStore(tmp / "k.jsonl")

    changed, note = store.remember("npc_map", "npc:447", 12345, source="observed", confidence=0.8)
    check("a discovered fact is stored", changed and note == "added", note)
    check("it can be read back", store.get("npc_map", "npc:447") == 12345)

    changed, note = store.remember("npc_map", "npc:447", 12345)
    check("the same fact confirms instead of duplicating", not changed, note)
    check("confirmation raises confidence", store._facts[("npc_map", "npc:447")].confidence > 0.8)

    # A weaker source must not overwrite a stronger one.
    changed, note = store.remember("npc_map", "npc:447", 999, source="guess", confidence=0.2)
    check("a guess cannot overwrite an observation", not changed and "disputed" in note, note)
    check("the value is unchanged", store.get("npc_map", "npc:447") == 12345)

    # A human correction wins.
    changed, note = store.remember("npc_map", "npc:447", 777, source="human", confidence=0.9)
    check("a human correction wins", changed and store.get("npc_map", "npc:447") == 777, note)

    check("forget removes it", store.forget("npc_map", "npc:447", "wrong"))
    check("and it is gone", store.get("npc_map", "npc:447") is None)

    # Persistence: a restart must not lose what was learned.
    store.remember("price", "item:1746", 1200)
    again = KnowledgeStore(tmp / "k.jsonl")
    check("facts survive a restart", again.get("price", "item:1746") == 1200)

    # A contradicted fact dies instead of lingering forever.
    store.remember("route", "a->b", 3, source="observed", confidence=0.9)
    for _ in range(3):
        store.remember("route", "a->b", 4, source="guess", confidence=0.1)
    check("three disputes drop the fact", store.get("route", "a->b") is None)


def test_knowledge_changes_the_plan() -> None:
    """Discovered knowledge must change behaviour without retraining."""
    print("\n== knowledge affects the plan ==")
    from agent.knowledge_store import KnowledgeStore
    from agent.planner import QuestPlanner
    import json as _json

    tmp = Path(tempfile.mkdtemp())
    store = KnowledgeStore(tmp / "k.jsonl")
    planner = QuestPlanner(level_hint=60, store=store)

    # A talk objective the KB does not place on a map: the plan has no travel of its
    # own, so any nav.goto after this point can only come from the learned fact.
    found = None
    for q, data in planner.quests.items():
        for si, step in enumerate(data["steps"]):
            for oi, o in enumerate(step.get("objectives") or []):
                if o.get("type_id") in (1, 9) and not o.get("map_id"):
                    found = (q, si, oi)
                    break
            if found:
                break
        if found:
            break
    if found is None:
        print("  [SKIP] no unplaced talk objective in the KB")
        return

    qid, si, oi = found
    obj = planner.quests[qid]["steps"][si]["objectives"][oi]
    npc = _json.loads(str(obj["params"]).replace("'", '"'))["parameter0"]
    obs = {"map_id": None, "inventory": {}}

    before = [c["tool"] for c in planner.plan_objective(planner.quests[qid], si, obj, obs)]
    store.remember("npc_map", "npc:%d" % npc, 4242, source="human", confidence=0.9)
    after = [c["tool"] for c in planner.plan_objective(planner.quests[qid], si, obj, obs)]

    check("a learned npc_map adds travel to the plan",
          "nav.goto" not in before and bool(after) and after[0] == "nav.goto",
          "%s -> %s" % (before, after))

    # The fact does not have to be written by hand. Any observation that shows
    # the NPC on a map is the same fact, and this is how the agent fills the
    # table the KB lacks while it plays. Use another NPC: an observed fact must
    # not overwrite the human one above, and that rule is what makes the store
    # trustworthy.
    other = int(npc) + 1
    planner.choose("q", {"map_id": 777, "map": {"map_id": 777,
                     "npcs": [{"npc_id": other, "cell": 1}]},
                     "quests_active": {}}, None)
    learned = store.get("npc_map", "npc:%d" % other)
    check("an observed map teaches where its NPCs are", learned == 777, str(learned))
    check("an observation does not overwrite what a human taught",
          store.get("npc_map", "npc:%d" % npc) == 4242,
          str(store.get("npc_map", "npc:%d" % npc)))

    # And the other half of the contract: when the objective itself names a map,
    # travel does not need to be learned at all.
    placed = None
    for q, data in planner.quests.items():
        for si2, step in enumerate(data["steps"]):
            for oi2, o in enumerate(step.get("objectives") or []):
                if o.get("map_id") and o.get("type_id") in (1, 9):
                    placed = (q, si2, oi2)
                    break
            if placed:
                break
        if placed:
            break
    if placed:
        q2, si3, oi3 = placed
        obj2 = planner.quests[q2]["steps"][si3]["objectives"][oi3]
        plan = planner.plan_objective(planner.quests[q2], si3, obj2,
                                      {"map_id": None, "inventory": {}})
        check("an objective that names a map travels there unasked",
              bool(plan) and plan[0]["tool"] == "nav.goto",
              "%s" % [c["tool"] for c in plan])


def test_idle_trainer_respects_the_lock() -> None:
    """Training must never run while a loop owns the account."""
    print("\n== idle trainer ==")
    try:
        import ai.idle_trainer as trainer
    except Exception as exc:  # noqa: BLE001
        print("  [SKIP] %s" % exc)
        return

    tmp = Path(tempfile.mkdtemp())
    old_lock, old_steps = trainer.LOCK, trainer.STEPS
    trainer.LOCK = tmp / "autopilot.lock"
    trainer.STEPS = tmp / "autopilot.jsonl"
    try:
        trainer.LOCK.write_text("{}", encoding="utf-8")
        why = trainer.busy_reason()
        check("a held lock blocks training", why is not None and "lock" in why, str(why))
        trainer.LOCK.unlink()
    finally:
        trainer.LOCK, trainer.STEPS = old_lock, old_steps


def test_planner_beats_the_rule_policy() -> None:
    """The number the whole design rests on: a plan completes quests, a rule does not."""
    print("\n== planner vs rule (symbolic world) ==")
    try:
        from sim.run import RulePolicy, run
        from agent.planner import QuestPlanner
    except Exception as exc:  # noqa: BLE001
        print("  [SKIP] %s" % exc)
        return

    planner = run(QuestPlanner(level_hint=60), level=60, steps=200)
    rule = run(RulePolicy(), level=60, steps=200)

    check("the planner completes quests", planner["quests"] > 0,
          "%d quests in 200 steps" % planner["quests"])
    check("the rule policy does not", rule["quests"] == 0, "%d quests" % rule["quests"])
    check("planner is strictly better", planner["quests"] > rule["quests"],
          "%d vs %d" % (planner["quests"], rule["quests"]))

    # Every call the planner emits must name ids that exist, or the plan is fiction.
    from sim.world import World
    world = World(level=60, max_steps=40)
    p = QuestPlanner(level_hint=60)
    bad = 0
    for _ in range(30):
        call = p.choose("do all the quests", world.observe(), None)
        if call is None:
            break
        if call.tool == "quest.advance" and call.args.get("quest_id") not in p.quests:
            bad += 1
        world.step(call.tool, call.args)
    check("planner only names quests that exist", bad == 0, "%d invalid" % bad)


def test_preflight_is_inert_without_a_model() -> None:
    print("\n== pre-flight ==")
    v = Verifier(human_path=Path(tempfile.gettempdir()) / "nonexistent_human.jsonl")
    check("no model -> no veto", v.preflight("hdv.buy", {}, base_obs()) is None, "inert")
    v.veto_threshold = 0.9
    check("threshold without a model still cannot veto", v.preflight("hdv.buy", {}, base_obs()) is None)


def test_combat_waiting_does_not_retire_quests() -> None:
    print("\n== combat integration ==")
    from unittest.mock import patch
    from agent.planner import QuestPlanner
    from agent.runtime import ToolCall

    planner = QuestPlanner(level_hint=60)
    call = planner.choose("do quests", {
        "in_fight": True,
        "ready_objectives": [{"quest_id": 584, "objective_id": 9001}],
    }, None)
    check("the planner observes instead of acting during combat",
          call is not None and call.tool == "state.snapshot" and not call.args,
          "%s %s" % (call.tool if call else None, call.args if call else None))

    tmp = Path(tempfile.mkdtemp())
    obs = base_obs(in_world=True, in_fight=True, objectives_completed=0)

    class Result:
        ok = True
        error = None
        data = {"snapshot": obs}

    class Policy:
        def __init__(self):
            self.calls = 0

        def choose(self, goal, observation, tools):
            self.calls += 1
            return ToolCall("state.snapshot", {}, goal) if self.calls <= 3 else None

        def note_result(self, tool, ok, note, observation):
            pass

    class Runtime:
        instance = None

        def __init__(self):
            self.policy = Policy()
            self.executor = self

        def current_observation(self):
            return obs, 0.0, "call"

        def execute(self, tool, args, **kwargs):
            return Result()

    retired = []
    pilot = Autopilot(runtime=Runtime(), goals=["quest 584"],
                      log_path=tmp / "fight.jsonl", stop_file=tmp / "STOP",
                      max_steps=0, preflight=False, settle_s=0.0)
    pilot.stall_limit = 1
    pilot._retire_named_quest = lambda *args: retired.append(args) or 584
    with patch("agent.autopilot.time.sleep") as sleep:
        pilot._run()
    check("combat snapshots do not retire the active quest", not retired, str(retired))
    combat_steps = [step for step in pilot.steps if step.tool == "state.snapshot"]
    check("combat waiting does not trip repeated-call detection", len(combat_steps) == 3,
          "%d combat steps" % len(combat_steps))
    check("combat observation history is cleared", not pilot._recent, str(pilot._recent))
    check("combat polling uses the slower cadence",
          sleep.call_count == 3 and all(c.args == (1.0,) for c in sleep.call_args_list),
          str(sleep.call_args_list))


def test_client_combat_handoff() -> None:
    print("\n== client combat handoff ==")
    script = REPO_ROOT.parent / "scripts" / "mobile-disguise.js"
    text = script.read_text(encoding="utf-8")
    check("a successful attack enables the fight driver",
          "if (result.ok && window.__FarmBot) window.__FarmBot.setEnabled(true);" in text)
    check("walk-then-attack records the asynchronous attack result",
          "stage: result.ok ? 'attacked' : 'attack_failed'" in text)
    check("an active fight does not select another monster",
          "return _agentResult(true, { in_fight: true, auto_fight: !!window.__FarmBot });" in text)
    check("missing fighter identity cannot authorise a turn",
          "return window.gui.playerData.characterBaseInformations.id === id;\n                }\n"
          "                return false;" in text)
    check("the unused class-specific AoE spell is gone", "aoeSpellId" not in text)


def test_fight_state_is_a_read_only_board() -> None:
    """The combat board must be readable before anything is allowed to act on it.

    `fight.state` is the eyes of the combat work: turn owner, every fighter's cell and
    resources, and the controlled character's real spell descriptors. It is deliberately
    a *read*: if this handler ever sends a message or casts, then a loop that only wanted
    to look can move the character, and every later measurement of "what did the AI do"
    becomes ambiguous. The check pins that separation, and pins the payload keys the
    future solver will consume.
    """
    print("\n== fight.state board ==")
    import json
    import re

    caps_path = REPO_ROOT / "data" / "capabilities.json"
    js_path = REPO_ROOT.parent / "scripts" / "mobile-disguise.js"
    if not caps_path.exists() or not js_path.exists():
        print("  [SKIP] capabilities or client script not found")
        return

    caps = {c["id"]: c for c in json.loads(caps_path.read_text(encoding="utf-8"))["capabilities"]}
    entry = caps.get("fight.state")
    check("fight.state is a declared capability", entry is not None, str(entry))
    check("fight.state takes no arguments", entry is not None and not (entry.get("params") or {}),
          str((entry or {}).get("params")))
    check("fight.state is a directive backed by the client",
          entry is not None and entry.get("backend") == "directive"
          and entry.get("directive") == "FIGHT_STATE",
          "%s -> %s" % ((entry or {}).get("backend"), (entry or {}).get("directive")))

    text = js_path.read_text(encoding="utf-8")
    check("the FIGHT_STATE directive reaches the handler",
          "'FIGHT_STATE': 'fight.state'" in text)
    start = text.find("'fight.state': function")
    check("the client has a fight.state handler", start >= 0)
    if start >= 0:
        body = text[start:text.find("\n            'nav.follow': function", start)]
        mutators = [token for token in (
            "_cmSend", "sendPacket", "castSpellOnTarget", "finishTurn", "setEnabled",
            "_movePlayerOnMap", "gotoNeighbourMap", "_requestMapChange", "PASS_TURN",
        ) if token in body]
        check("fight.state only reads", not mutators, ", ".join(mutators) or "no writes")
        for field in ("current_fighter_id", "is_my_turn", "fighters", "spells",
                      "effects", "critical_effects", "cell", "hp", "max_hp", "ap", "mp",
                      "map_id", "cells", "my_stats", "resist_percent", "resist_fixed",
                      "critical_hit_probability"):
            check("fight.state exposes %s" % field, field + ":" in body)

    from agent import verifier
    check("a fight.state read is not scored as a failed world change",
          "fight.state" in verifier.READ_ONLY_TOOLS, str(sorted(verifier.READ_ONLY_TOOLS)))
    # The verifier's ONNX head is width-locked to TOOL_ORDER. fight.state was appended
    # to the capability registry but must not enter that vector until the head is
    # retrained, or a working model silently starts failing on a shape mismatch.
    check("fight.state is kept out of the width-locked verifier head",
          "fight.state" not in verifier.TOOL_ORDER, str(len(verifier.TOOL_ORDER)))

    from agent.executor import ToolExecutor, ToolResult

    calls = []

    def fake_dispatch(tool, args, **_kwargs):
        calls.append((tool, args))
        return ToolResult(tool=tool, ok=True, source="live", data={"response": {
            "ok": True,
            "result": {"ok": True, "data": {
                "in_fight": True, "current_fighter_id": 7,
                "fighters": [{"id": 7, "cell": 300}], "spells": [],
            }},
        }})

    executor = object.__new__(ToolExecutor)
    executor._dispatch = fake_dispatch
    board = executor.fight_state()
    check("the executor asks for fight.state with no arguments",
          calls == [("fight.state", {})], str(calls))
    check("the executor unwraps the bridge's nested board",
          board is not None and board.get("in_fight") is True
          and board.get("current_fighter_id") == 7
          and board.get("fighters") == [{"id": 7, "cell": 300}], str(board))


def test_fight_plan_scores_the_live_board_without_acting() -> None:
    """fight.plan is the solver's only entry point, and it must stay read-only.

    The plan is worth having only if it is derived from the live board rather than
    from a guess, so this feeds a synthetic board through the executor exactly as
    the bridge would and checks the arithmetic. It also pins that planning issues
    no cast/move: one board read, zero writes.
    """
    print("\n== fight.plan solver ==")
    import json

    from agent import verifier
    from agent.executor import ToolExecutor, ToolResult
    from agent.schema import TOOLS_BY_NAME
    from combat.battle_solver import _POINTS

    caps_path = REPO_ROOT / "data" / "capabilities.json"
    caps = {c["id"]: c for c in json.loads(caps_path.read_text(encoding="utf-8"))["capabilities"]}
    entry = caps.get("fight.plan")
    check("fight.plan is a declared capability", entry is not None, str(entry))
    check("fight.plan is computed locally, not sent to the client",
          entry is not None and entry.get("backend") == "local"
          and not entry.get("directive"),
          "%s -> %s" % ((entry or {}).get("backend"), (entry or {}).get("directive")))
    check("fight.plan declares its search knobs",
          set(((entry or {}).get("params") or {})) == {"beam_width", "max_actions"},
          str(sorted(((entry or {}).get("params") or {}))))
    check("the schema fallback also knows fight.plan", "fight.plan" in TOOLS_BY_NAME)
    check("a fight.plan read is not scored as a failed world change",
          "fight.plan" in verifier.READ_ONLY_TOOLS, str(sorted(verifier.READ_ONLY_TOOLS)))
    check("fight.plan is kept out of the width-locked verifier head",
          "fight.plan" not in verifier.TOOL_ORDER, str(len(verifier.TOOL_ORDER)))

    me_cell = _POINTS[(20, 20)]
    foe_cell = _POINTS[(21, 20)]
    board = {
        "in_fight": True,
        "in_preparation": False,
        "is_my_turn": True,
        "my_id": 7,
        "my_cell": me_cell,
        "map_id": 1,
        "cells": [3] * 560,
        "my_stats": {},
        "fighters": [
            {"id": 7, "name": "Me", "cell": me_cell, "team": "player",
             "hp": 1000, "max_hp": 1000, "ap": 3, "mp": 3, "shield": 0,
             "level": 100, "alive": True, "is_me": True},
            {"id": 8, "name": "Foe", "cell": foe_cell, "team": "enemy",
             "hp": 100, "max_hp": 100, "ap": 4, "mp": 3, "shield": 0,
             "level": 50, "alive": True},
        ],
        "spells": [{
            "id": 1, "name": "Strike", "ap_cost": 3, "min_range": 1,
            "range": 4, "castable": True, "max_cast_per_turn": 2,
            "max_cast_per_target": 2,
            "requires_los": False, "critical_hit_probability": 0,
            "effects": [{"effect_id": 100, "dice_num": 10, "dice_side": 10}],
            "critical_effects": [],
        }],
    }

    calls = []

    def fake_dispatch(tool, args, **_kwargs):
        calls.append((tool, args))
        return ToolResult(tool=tool, ok=True, source="live", data={"response": {
            "ok": True, "result": {"ok": True, "data": board},
        }})

    executor = object.__new__(ToolExecutor)
    executor._dispatch = fake_dispatch
    result = executor.fight_plan({})
    check("fight.plan succeeds on a live board", result.ok, result.error or "")
    plan = result.data or {}
    check("fight.plan chooses a cast", (plan.get("actions") or [{}])[0].get("kind") == "cast",
          str(plan.get("actions")))
    check("fight.plan does not walk when the target is already in range",
          (plan.get("actions") or [{}])[0].get("mp_cost") == 0
          and (plan.get("actions") or [{}])[0].get("move_to") is None,
          str(plan.get("actions")))
    check("fight.plan prices the cast from resistances and stats",
          plan.get("expected_damage") == 10.0, str(plan.get("expected_damage")))
    check("fight.plan spends the AP it says it spends",
          plan.get("remaining_ap") == 0, str(plan.get("remaining_ap")))
    check("planning reads the board and writes nothing",
          calls == [("fight.state", {})], str(calls))


def main() -> int:
    test_verdicts()
    test_residual_and_drift()
    test_human_override()
    test_loop_wiring()
    test_baseline_model()
    test_onnx_head()
    test_step_summary()
    test_exploration_is_opt_in()
    test_session_log_becomes_a_dataset()
    test_bridge_port_is_discovered()
    test_every_declared_tool_exists_in_the_client()
    test_host_messages_are_not_double_encoded()
    test_one_transport_one_copy()
    test_the_planner_can_call_what_it_plans()
    test_the_planner_can_see_the_world()
    test_the_emulator_stays_inside_its_box()
    test_the_model_chain_is_wired()
    test_a_walk_gets_time_to_land()
    test_the_loop_uses_the_post_action_state()
    test_the_labeler_does_not_rewind_time()
    test_a_dead_connection_stops_the_loop()
    test_layering_holds()
    test_a_ready_objective_is_claimed_first()
    test_objectives_the_kb_lacks()
    test_the_tutorial_is_the_authority()
    test_a_quest_the_server_never_starts()
    test_verifier_reads_the_real_observation_shape()
    test_unlimited_means_unlimited()
    test_structural_failures_are_not_retried()
    test_learned_planner_matches_the_teacher()
    test_learned_navigation_cannot_bypass_the_exit_rule()
    test_knowledge_store()
    test_knowledge_changes_the_plan()
    test_idle_trainer_respects_the_lock()
    test_planner_beats_the_rule_policy()
    test_controller_changes_the_next_action()
    test_loop_applies_the_decision()
    test_the_policy_hears_its_outcomes()
    test_preflight_is_inert_without_a_model()
    test_combat_waiting_does_not_retire_quests()
    test_client_combat_handoff()
    test_fight_state_is_a_read_only_board()
    test_fight_plan_scores_the_live_board_without_acting()

    failed = [r for r in RESULTS if not r[1]]
    print("\n%d checks, %d failed" % (len(RESULTS), len(failed)))
    for name, _, detail in failed:
        print("  FAILED: %s %s" % (name, detail))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
