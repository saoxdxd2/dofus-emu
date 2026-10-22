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


def test_structural_failures_are_not_retried() -> None:
    """Real data: 9 of 15 client failures were 'no active client', 0/9 retries worked."""
    print("\n== error taxonomy ==")
    from agent.controller import Controller, error_class

    check("no client is structural",
          error_class("client directive failed: instance 'x' is not an active client window")
          == "structural")
    check("a timeout is transient", error_class("The operation has timed out") == "transient")
    check("resisted is transient", error_class("resisted") == "transient")

    tmp = Path(tempfile.mkdtemp())
    c = Controller(state_dir=tmp, session="tx")
    d = c.on_step(Verdict("failed", 0.9, None, False, "rule"), "state.snapshot", False,
                  "client directive failed: no client instance is running", {}, {})
    check("a structural failure replans instead of retrying",
          d.action == "replan" and d.cause == "unavailable", "%s/%s" % (d.action, d.cause))

    c2 = Controller(state_dir=tmp, session="ty")
    d2 = c2.on_step(Verdict("failed", 0.9, None, False, "rule"), "farm.start", False,
                    "The operation has timed out", {}, {})
    check("a transient failure still retries once", d2.action == "retry",
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

    qid = None
    for q, data in planner.quests.items():
        objs = (data["steps"][0].get("objectives") or [])
        if objs and objs[0].get("type_id") in (1, 9):
            qid = q
            break
    if qid is None:
        print("  [SKIP] no talk-first quest in the KB")
        return

    obj = planner.quests[qid]["steps"][0]["objectives"][0]
    npc = _json.loads(str(obj["params"]).replace("'", '"'))["parameter0"]
    obs = {"map_id": None, "inventory": {}}

    before = [c["tool"] for c in planner.plan_objective(planner.quests[qid], 0, obj, obs)]
    store.remember("npc_map", "npc:%d" % npc, 4242, source="human", confidence=0.9)
    after = [c["tool"] for c in planner.plan_objective(planner.quests[qid], 0, obj, obs)]

    check("a learned npc_map adds travel to the plan", after != before and after[0] == "nav.goto",
          "%s -> %s" % (before, after))


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
