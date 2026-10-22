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

    r = v.label("nav.walk", {}, True, None, base_obs(), base_obs(map_id=9), 100.0)
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
