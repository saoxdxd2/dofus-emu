"""Smoke test for the second agent - offline, no game, no network, no torch.

    cd dofus-network && python -m agent.smoke_verifier

Exit code 0 when every check passes. This is the "link and test in a real run"
step without needing the client: it proves the verifier is wired into the loop,
that all four verdicts are reachable, that drift fires on the contradictions we
care about, and that a human label overrides both.
"""

from __future__ import annotations

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

    # Exactly one copy in the tree. Walk rather than glob so an unexpected location
    # fails the check instead of being silently missed.
    copies = []
    for p in root.rglob("mobile-disguise*.js"):
        parts = set(p.parts)
        if parts & {"node_modules", "archive", "asset_cache", ".git", "__pycache__"}:
            continue
        copies.append(p)
    check("exactly one copy of the client script exists", len(copies) == 1,
          ", ".join(str(p.relative_to(root)) for p in copies))

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
                          max_steps=0, interval=0.0, preflight=False)
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
    # debug.* handlers are diagnostics, not agent tools: they exist to ask the
    # client what it actually contains when a reader is returning nulls.
    phantom = sorted(h for h in (handlers - directive - local)
                     if not h.startswith("debug."))
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
    test_a_dead_connection_stops_the_loop()
    test_layering_holds()
    test_a_ready_objective_is_claimed_first()
    test_verifier_reads_the_real_observation_shape()
    test_unlimited_means_unlimited()
    test_structural_failures_are_not_retried()
    test_learned_planner_matches_the_teacher()
    test_knowledge_store()
    test_knowledge_changes_the_plan()
    test_idle_trainer_respects_the_lock()
    test_planner_beats_the_rule_policy()
    test_controller_changes_the_next_action()
    test_loop_applies_the_decision()
    test_preflight_is_inert_without_a_model()

    failed = [r for r in RESULTS if not r[1]]
    print("\n%d checks, %d failed" % (len(RESULTS), len(failed)))
    for name, _, detail in failed:
        print("  FAILED: %s %s" % (name, detail))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
