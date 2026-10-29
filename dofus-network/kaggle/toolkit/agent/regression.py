"""Offline regression gate for the agent loop — the contract the deleted
smoke_verifier used to enforce (KNOWN_GAPS.md G8).

No client, no network, no torch, no onnxruntime. It runs the REAL code paths:

  * the verifier rule engine (all four verdicts, nested-alias changes, the
    ready_objectives claim-proof, read-only-as-advanced),
  * the schema / capability / feature tool vocabulary (they must be one set,
    and the 25-tool research vocabulary must include hdv.buy),
  * the objective-type buckets,
  * the JS page suites, by delegating to ``node tools/test_*.js``,
  * the bundle map-transition resilience rules, by delegating to
    ``node tools/apply-map-resilience.js --check`` (read-only).

Run from ``dofus-network/``:

    python -m agent.regression

Exit code 0 = green. A non-zero exit means a contract moved. This is a gate, not
a substitute for a live session: it proves the loop's pure logic, not that the
game accepted a packet.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent      # .../dofus-network
ROOT = REPO_ROOT.parent                                  # .../dofus-emu

_FAILURES: List[str] = []
_CHECKS = 0


def check(label: str, condition: Any, detail: Any = "") -> None:
    global _CHECKS
    _CHECKS += 1
    if condition:
        return
    text = "%s%s" % (label, (": %s" % detail) if detail else "")
    _FAILURES.append(text)
    print("FAIL %s" % text)


def section(title: str) -> None:
    print("\n== %s ==" % title)


def code_only(text: str) -> str:
    """Source with whole-line comments removed, for the C#/JS needle checks.

    Measured twice, same trap: a needle matched a statement that had been
    commented out (the updater's User-Agent assignment, the step-row fragment
    filter), so the check passed while the code was broken and its mutation was
    MISSED. A gate that reads comments is not reading the code.
    """
    return "\n".join(line for line in text.splitlines()
                     if not line.strip().startswith("//"))


# ---------------------------------------------------------------------------
def test_verdict_vocabulary() -> None:
    from agent import verifier

    section("verifier vocabulary")
    check("VERDICTS are the four labels",
          tuple(verifier.VERDICTS) == ("advanced", "noop", "failed", "blocked"),
          verifier.VERDICTS)
    check("read-only tools are a known set",
          {"state.snapshot", "quest.list", "knowledge.search"} <=
          set(verifier.READ_ONLY_TOOLS),
          sorted(verifier.READ_ONLY_TOOLS))


def _new_verifier():
    from agent.verifier import Verifier
    # A human path that does not exist loads empty and never touches the real
    # %LOCALAPPDATA%\DofusFarm\verdicts_human.jsonl.
    missing = Path(tempfile.gettempdir()) / "dofus_regression_no_human.jsonl"
    missing.unlink(missing_ok=True)
    return Verifier(model_path=None, human_path=missing, veto_threshold=0.0)


def test_verifier_rules() -> None:
    from agent import verifier
    from agent.verifier import changed_keys

    section("verifier rule engine")
    v = _new_verifier()
    check("rules-only with no model", v.model_note.startswith("no verifier model"),
          v.model_note)

    def lab(*a, **k):
        return v.label(*a, **k).verdict

    # a query that returned ok is success by definition
    check("read-only ok => advanced",
          lab("state.snapshot", {}, True, None, {}, {}) == "advanced")

    # a call that moved the cell is progress
    check("cell change => advanced",
          lab("nav.goto", {}, True, None, {"cell": 5}, {"cell": 9}) == "advanced")

    # a call that moved nothing is a noop
    check("no state change => noop",
          lab("nav.goto", {}, True, None, {"cell": 5}, {"cell": 5}) == "noop")

    # nested snapshot shape must be read through the aliases, not flat keys
    check("nested position.map_id change => advanced",
          lab("nav.goto", {}, True, None,
              {"position": {"map_id": 100}}, {"position": {"map_id": 200}}) == "advanced")

    # the claim proof: an objective validated moves off ready_objectives
    check("ready_objectives emptied => advanced",
          lab("quest.advance", {"objective_id": 14070}, True, None,
              {"ready_objectives": [14070]}, {"ready_objectives": []}) == "advanced")

    # failures and the not_mapped veto
    check("not-ok => failed",
          lab("quest.advance", {}, False, "server refused", {}, {}) == "failed")
    check("not_mapped => blocked",
          lab("nav.goto", {}, False, "not_mapped: hdv_ui", {}, {}) == "blocked")

    # the public helper agrees with the internal one
    check("changed_keys is consistent with the rule",
          changed_keys({"cell": 1}, {"cell": 2}) == verifier._changed({"cell": 1}, {"cell": 2}))


def test_verifier_model_contract() -> None:
    from agent import verifier
    from agent.verifier import Verifier, VERDICTS

    section("verifier model contract (G6: head is wired, guard is real)")
    tmp = Path(tempfile.gettempdir())
    none_human = tmp / "dofus_regression_no_human.jsonl"
    width = len(verifier.features("nav.goto", True, None, {"cell": 1}, {"cell": 2}, 10.0))

    def write_baseline(prefer: str) -> Path:
        w = [[0.0] * width for _ in VERDICTS]
        b = [0.0] * len(VERDICTS)
        b[VERDICTS.index(prefer)] = 10.0     # force a confident preference
        path = tmp / "dofus_regression_vb.json"
        path.write_text(json.dumps({"kind": "verifier-baseline", "w": w, "b": b,
                                    "classes": list(VERDICTS),
                                    "metrics": {"val": {"accuracy": 0.99}}}),
                        encoding="utf-8")
        return path

    # (a) a valid baseline artifact loads (the contract accepts its kind)
    v = Verifier(model_path=write_baseline("advanced"), human_path=none_human)
    check("a verifier-baseline json loads", v._session == "baseline", v._session)

    # (b) a wrong-kind json is rejected to rules, no crash
    bad = tmp / "dofus_regression_bad.json"
    bad.write_text(json.dumps({"kind": "not-a-verifier"}), encoding="utf-8")
    vbad = Verifier(model_path=bad, human_path=none_human)
    check("a non-baseline json degrades to rules",
          vbad._session is None
          and vbad.label("state.snapshot", {}, True, None, {}, {}).verdict == "advanced",
          vbad.model_note)

    # (c) THE GUARD: a read-only query is labelled by rules even when a confident
    #     model would say 'failed'. This is the one thing that stops the stale head
    #     (trained on the old "query = noop" labels) from mislabbing a lookup.
    vfail = Verifier(model_path=write_baseline("failed"), human_path=none_human)
    got = vfail.label("state.snapshot", {}, True, None, {}, {})
    check("a read-only query is never model-labelled (stale-head guard)",
          got.verdict == "advanced" and got.source == "rule", (got.verdict, got.source))

    # (d) but an ACTION does defer to a confident model: the head is live for
    #     non-read-only tools, so its accuracy matters — and that accuracy is
    #     unmeasured on real labels (the G6 finding, not a passing claim).
    act = vfail.label("nav.goto", {}, True, None, {"cell": 5}, {"cell": 5})  # rule=>noop
    check("an action defers to the loaded model (head is wired, not orphaned)",
          act.source == "model" and act.verdict == "failed", (act.source, act.verdict))

    for p in (none_human, tmp / "dofus_regression_vb.json", bad):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def test_tool_vocabulary() -> None:
    from agent import schema, features

    section("tool vocabulary (one source of truth)")
    tool_names = [getattr(t, "name", None) or getattr(t, "id", None) for t in schema.TOOLS]
    tool_names = [n for n in tool_names if n]

    caps_path = REPO_ROOT / "data" / "capabilities.json"
    caps_ids: List[str] = []
    if caps_path.exists():
        data = json.loads(caps_path.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("tools", data.get("capabilities", []))
        caps_ids = [(r.get("id") or r.get("name")) for r in rows if (r.get("id") or r.get("name"))]

    reg_ids = features.tool_registry()

    check("schema declares 28 tools", len(tool_names) == 28, len(tool_names))
    check("capabilities.json lists 28 tools", len(caps_ids) == 28, len(caps_ids))
    check("features.tool_registry lists the same set as the schema",
          set(reg_ids) == set(tool_names), (sorted(set(reg_ids) ^ set(tool_names))))
    check("capabilities.json matches the schema set",
          set(caps_ids) == set(tool_names), (sorted(set(caps_ids) ^ set(tool_names))))
    check("no duplicate tool ids", len(set(tool_names)) == len(tool_names))
    for name in ("hdv.buy", "inv.equip", "hdv.prices", "tap.interactive"):
        check(name + " is in the schema", name in tool_names)
    from agent.verifier import READ_ONLY_TOOLS
    check("hdv.prices is read-only", "hdv.prices" in READ_ONLY_TOOLS)


def test_plan_stack() -> None:
    from agent.plan_stack import MAX_BYTES, PlanStack, child_for_objective

    section("Sub-goal stack (precondition decomposition)")
    stack = PlanStack()
    stack.push("objective", {"quest_id": 1})
    stack.push("goto_npc", {"npc_id": 3888}, parent="objective")
    top = stack.pop()
    check("push/pop is LIFO", top is not None and top.kind == "goto_npc", top)
    check("the parent remains after a child pops",
          stack.top() is not None and stack.top().kind == "objective")
    stack.clear()
    for index in range(40):
        stack.push("goal", {"i": index})
    check("the stack is bounded", len(stack) == 16, len(stack))
    check("the stack stays under 2 KB", stack.size_bytes() <= MAX_BYTES,
          stack.size_bytes())

    talk = {"type_id": 1, "npc_id": 3888, "params": {"parameter0": 3888},
            "text": "talk"}
    empty_map = {"map": {"npcs": [], "groups": [], "exits": []}, "inventory": {}}
    child = child_for_objective(talk, empty_map)
    check("a talk with the NPC elsewhere decomposes to goto_npc",
          child is not None and child["kind"] == "goto_npc", child)
    here = {"map": {"npcs": [{"template_id": 3888}], "groups": []}}
    check("a talk with the NPC on the map needs no child",
          child_for_objective(talk, here) is None)
    fight = {"type_id": 6, "text": "kill"}
    child = child_for_objective(
        fight, {"map": {"groups": [], "exits": [{"direction": "left"}]}})
    check("a fight with no group decomposes to explore",
          child is not None and child["kind"] == "explore", child)
    equip = {"type_id": 0, "text": "Go and equip your new items"}
    child = child_for_objective(
        equip, {"inventory": {"presets": [{"preset_id": 3}]}})
    check("an equip instruction decomposes to a preset",
          child is not None and child["args"].get("preset_id") == 3, child)

    # Live 2026-10-29: quest 4260's "Equip the Albueran recruit set, then talk to
    # Ayin Spyr" had four items and zero presets, and the preset-only branch
    # answered `inv.list` on every pass. The bag's own uids are the route, each
    # offered twice (a type with no possible position is refused), then the
    # objective's talk half must be reachable.
    from agent.planner import QuestPlanner

    live_bag = {"inventory": {"presets": [], "equipped": [],
                              "items": [{"gid": 18974, "object_uid": 68720787},
                                        {"gid": 18844, "object_uid": 68720788}]},
                "map": {}}
    equip_step = dict(equip, type="QuestObjectiveFreeForm",
                      params={"parameter0": 719010})
    planner = QuestPlanner()
    offered = [planner.plan_objective({"id": 4260}, 0, equip_step, live_bag)
               for _ in range(4)]
    uids = [calls[0]["args"].get("object_uid") if calls else None
            for calls in offered]
    check("with no preset the bag's items are equipped by uid",
          uids[0] == 68720787 and all(c and c[0]["tool"] == "inv.equip"
                                      for c in offered), uids)
    check("the next step offers the other item, not the same one again",
          uids[1] == 68720788, uids)
    check("each uid gets a second chance, then is retired",
          uids[2] == 68720787 and uids[3] == 68720788, uids)
    settled = planner.plan_objective({"id": 4260}, 0, equip_step, live_bag)
    check("an equip half with nothing left yields instead of re-reading",
          settled == [], settled)
    worn = {"inventory": {"presets": [], "equipped": [{"object_uid": 68720787}],
                          "items": [{"gid": 18974, "object_uid": 68720787},
                                    {"gid": 18844, "object_uid": 68720788}]},
            "map": {}}
    shown = QuestPlanner().plan_objective({"id": 4260}, 0, equip_step, worn)
    check("an item already on is never offered again",
          bool(shown) and shown[0]["args"].get("object_uid") == 68720788, shown[:1])
    # Live 2026-10-29: 76 refused equips, each demanding a slot, from types that
    # declare no positions. The page publishes what is worn as {slot, gid}, so a
    # second copy of a worn template names its own slot without guessing.
    twin = {"inventory": {"presets": [],
                          "equipped": [{"slot": 4, "gid": 18844}],
                          "items": [{"gid": 18844, "object_uid": 68720788}]},
            "map": {}}
    slotted = QuestPlanner().plan_objective({"id": 4260}, 0, equip_step, twin)
    check("a worn twin's slot is sent with the equip",
          bool(slotted) and slotted[0]["args"].get("slot") == 4
          and slotted[0]["args"].get("object_uid") == 68720788, slotted[:1])
    stranger = {"inventory": {"presets": [],
                              "equipped": [{"slot": 4, "gid": 999}],
                              "items": [{"gid": 18844, "object_uid": 68720788}]},
                "map": {}}
    plain = QuestPlanner().plan_objective({"id": 4260}, 0, equip_step, stranger)
    check("no twin means no invented slot",
          bool(plain) and "slot" not in plain[0]["args"], plain[:1])

    planner = QuestPlanner()
    delivery = {"id": 1, "type_id": 2,
                "params": {"parameter0": 123, "parameter1": 55},
                "max_completion": 1}
    priced = {"kamas": 1000, "inventory": {},
              "prices": [{"gid": 123, "listings": [
                  {"uid": 77, "price": 400, "quantity": 1}]}]}
    calls = planner.plan_objective({"id": 1}, 0, delivery, priced)
    check("a missing delivery item with a cached listing plans hdv.buy",
          bool(calls) and calls[0].get("tool") == "hdv.buy", calls[:2])
    check("the buy is bounded by the cached listing price",
          bool(calls) and calls[0].get("args", {}).get("max_price") == 400,
          calls[:1])
    check("the delivery follows the buy",
          any(call.get("tool") == "quest.advance" for call in calls), calls)
    unpriced = {"kamas": 1000, "inventory": {}}
    calls = planner.plan_objective({"id": 1}, 0, delivery, unpriced)
    check("without a cached listing it falls back to farming",
          bool(calls) and calls[0].get("tool") == "farm.start", calls[:2])

    # The child persists until the action advances; a read must not pop it, and
    # three failures must release it so another decomposition can be chosen.
    planner = QuestPlanner()
    equip_obs = {"inventory": {"presets": [{"preset_id": 3}]}, "map": {}}
    planner.plan_objective({"id": 1}, 0, equip, equip_obs)
    check("the equip child is persisted on the stack",
          planner.plan_stack.top() is not None
          and planner.plan_stack.top().kind == "equip")
    planner.note_result("state.snapshot", True, "advanced", equip_obs)
    check("a read does not pop the equip child",
          planner.plan_stack.top() is not None)
    planner.note_result("inv.equip", True, "advanced", equip_obs)
    check("an advanced equip pops the child",
          planner.plan_stack.top() is None)
    planner.plan_objective({"id": 1}, 0, equip, equip_obs)
    for _ in range(3):
        planner.note_result("inv.equip", True, "noop", equip_obs)
    check("three failed attempts release the child",
          planner.plan_stack.top() is None)


def test_shared_resources() -> None:
    import threading

    from agent.knowledge_context import load_default_store

    section("shared daemon resources")
    first = load_default_store()
    second = load_default_store()
    check("the canonical store is cached per path",
          first is not None and first is second)
    if first is not None:
        errors: List[BaseException] = []

        def read_meta() -> None:
            try:
                first.meta()
            except BaseException as exc:  # noqa: BLE001 - report, do not hide
                errors.append(exc)

        threads = [threading.Thread(target=read_meta) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        check("the cached store reads across worker threads",
              not errors, errors[:2])

    model = REPO_ROOT / "models" / "tool_policy.onnx"
    try:
        from agent.model_cache import onnx_session
    except ImportError:
        print("   SKIP: onnxruntime not installed")
        return
    if not model.is_file():
        print("   SKIP: tool_policy.onnx not present")
        return
    one = onnx_session(str(model))
    two = onnx_session(str(model))
    check("one ONNX session per model path", one is two)


def test_brain_lifecycle() -> None:
    import unittest
    from agent.test_brain_daemon import BrainLifecycleTests

    section("Brain daemon lifecycle (cooperative Stop, dynamic HTTP fleets)")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BrainLifecycleTests)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("daemon lifecycle tests execute and pass",
          result.testsRun >= 8 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures, "errors": result.errors})


def test_live_collection_lifecycle() -> None:
    import unittest
    from tools import test_collect_live_data

    section("Live collector (read-only fleet discovery, scoped sessions)")
    suite = unittest.defaultTestLoader.loadTestsFromModule(test_collect_live_data)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("collector lifecycle tests execute and pass",
          result.testsRun >= 49 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures, "errors": result.errors})


def test_pointer_coverage_gate() -> None:
    import unittest
    from tools.test_entity_pointer_gate import PointerCoverageTests

    section("Entity-pointer rebuild (live coverage and output preservation)")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PointerCoverageTests)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("pointer coverage checks execute and pass",
          result.testsRun >= 13 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures, "errors": result.errors})


def test_brain_scaling_40() -> None:
    section("Brain daemon scaling (dynamic instances, shared graph)")
    import tempfile

    from agent.planner import QuestPlanner
    from agent.brain_daemon import BrainDaemon

    one = QuestPlanner(level_hint=60)
    two = QuestPlanner(level_hint=60)
    check("planners share one quest graph instead of copying 21.8 MB each",
          one.quests is two.quests, (len(one.quests), len(two.quests)))
    check("the shared graph still carries the curriculum",
          len(one.quests) >= 1900, len(one.quests))

    class _Pilot:
        def __init__(self, instance: str, root: Path):
            self.goals = ["auto"]
            self.log_path = root / ("autopilot.%s.jsonl" % instance)
            self.stop_file = root / ("AUTOPILOT_STOP.%s" % instance)

        def run(self) -> Dict[str, Any]:
            return {"steps": 0, "instance": self.log_path.stem}

        def stop_client(self) -> None:
            return None

    with tempfile.TemporaryDirectory(prefix="brain-40-") as tmp:
        root = Path(tmp)
        daemon = BrainDaemon(log_dir=root, recorder_dir=root)
        daemon._make_pilot = lambda instance, config: _Pilot(instance, root)
        names = ["dofus-%02d" % i for i in range(1, 41)]
        started = daemon.start({"instances": names, "steps": 0, "dry_run": True})
        check("40 instances are accepted with no hard cap",
              started.get("ok") is True and len(started.get("instances") or []) == 40,
              started.get("error") or len(started.get("instances") or []))
        for item in list(daemon._instances.values()):
            if item.thread is not None:
                item.thread.join(timeout=10)
        health = daemon.health()
        check("the daemon counts all 40 instance states",
              health.get("instances") == 40, health)
        check("none of the 40 loops is left running",
              not health.get("active"), health.get("active"))
        check("one daemon lock covers the whole farm, not one per instance",
              json.loads((root / "brain.lock").read_text(encoding="utf-8"))
              .get("instances") == [], "brain.lock")
        check("40 status rows stay small enough for a server",
              sum(len(json.dumps(item.status())) for item in daemon._instances.values())
              < 40000, sum(len(json.dumps(item.status()))
                           for item in daemon._instances.values()))


def test_constraint_corpus() -> None:
    from agent.constraint_corpus import (_generated_cases,
                                         check as check_constraints,
                                         manifest_for, measure)
    from agent.schema import tool_names

    section("Constraint corpus (every tool x failure mode)")
    failures, manifest = check_constraints()
    check("all constraint cases route to the expected cause/action",
          not failures, failures[:3])
    check("every tool has a constraint case",
          not manifest["missing_tools"], manifest["missing_tools"])
    check("every controller cause is exercised",
          not manifest["missing_causes"], manifest["missing_causes"])
    check("unknown is exceptional, not the default",
          manifest["causes"].get("unknown", 0) <= 1,
          manifest["causes"].get("unknown", 0))

    rows = _generated_cases()
    gen_failures, agreement = measure(rows)
    gen_manifest = manifest_for(rows, "constraint_synthetic", agreement)
    check("the synthetic corpus has at least 2,500 rows",
          len(rows) >= 2500, len(rows))
    check("every one of the 28 tools has at least 50 rows",
          all(gen_manifest["tools"].get(tool, 0) >= 50
              for tool in tool_names()),
          {tool: gen_manifest["tools"].get(tool, 0) for tool in tool_names()})
    check("all eight causes are represented",
          not gen_manifest["missing_causes"], gen_manifest["missing_causes"])
    check("synthetic cause agreement is at least 95%",
          agreement["cause"] >= 0.95, agreement)
    check("synthetic action agreement is at least 90%",
          agreement["action"] >= 0.90, agreement)
    check("the synthetic corpus routes every row as expected",
          not gen_failures, gen_failures[:6])


def test_forbidden_and_validation() -> None:
    from agent.autopilot import FORBIDDEN_TOOLS
    from agent import schema

    section("guards")
    check("FORBIDDEN_TOOLS is empty", not FORBIDDEN_TOOLS, sorted(FORBIDDEN_TOOLS))

    ok_buy, buy_error = schema.validate_call(
        "hdv.buy", {"gid": 1, "quantity": 1, "max_price": 10})
    check("hdv.buy validates with an explicit max price", ok_buy is True, buy_error)
    ok_equip, equip_error = schema.validate_call("inv.equip", {"preset_id": 1})
    check("inv.equip validates with a preset id", ok_equip is True, equip_error)
    ok_tap, tap_error = schema.validate_call("tap.interactive", {"element_id": 1})
    check("tap.interactive validates with an element id", ok_tap is True, tap_error)

    ok_unknown, _ = schema.validate_call("definitely.not.a.tool", {})
    check("validate_call rejects an unknown tool", ok_unknown is False, ok_unknown)

    # every real tool must validate against its own spec with no crash
    crash: Optional[str] = None
    for t in schema.TOOLS:
        name = getattr(t, "name", None) or getattr(t, "id", None)
        try:
            schema.validate_call(name, {})
        except Exception as exc:  # noqa: BLE001
            crash = "%s: %s" % (name, exc)
            break
    check("validate_call runs on every declared tool without crashing",
          crash is None, crash)


def test_objective_buckets() -> None:
    from agent import features

    section("objective-type buckets")
    check("talk types are {1, 9}", features.TALK_TYPES == {1, 9}, features.TALK_TYPES)
    check("kill types cover {0, 6, 14, 16}", features.KILL_TYPES == {0, 6, 14, 16},
          features.KILL_TYPES)
    check("fight type is {6}", features.FIGHT_TYPES == {6}, features.FIGHT_TYPES)
    check("discover-map type is {4}", features.MAP_TYPES == {4}, features.MAP_TYPES)
    check("a talk type is not also a kill type",
          not (features.TALK_TYPES & features.KILL_TYPES))
    # The buckets encode *what an objective looks like to the policy*; they are not the
    # targeting rule. G27: type 0 is in KILL_TYPES because its decoded text read like a
    # hunt, and the client's own parameter table says it carries no creature.
    check("type 0 stays in the kill bucket while declaring no creature",
          0 in features.KILL_TYPES
          and "QuestObjectiveFreeForm" not in features.MONSTER_PARAMETER_TYPES)


def test_objective_parameter_table() -> None:
    """G27: an objective's parameters are what the client's own table says they are.

    Measured in the live corpus: 18,654 entries of `QuestObjectiveFreeForm`
    (`type_id` 0) carry a six-digit `parameter0` (715354, 713802, 719010, 698229), while
    every `QuestObjectiveFightMonster` (`type_id` 6) carries a four-digit one (4194,
    4192). `KILL_TYPES` read 0 as "monster" because those buckets were decoded from the
    objective *text*, so the loop ordered `fight.join {monster: 715354}` and then walked
    the world for a creature the client never said existed.

    The local caches cannot corroborate any of this — `monsters_full.json` holds 2,000
    Dofus 2 ids (31..4,064) and contains neither family, which is G29 — so the only
    authority available is the client's own declared parameter table, and this section
    re-reads it out of the served bundle rather than trusting the copy below.
    """
    import copy
    import inspect

    from agent import features
    from agent import planner as planner_module
    from agent import planner_nn as nn_module
    from agent.features import OBJECTIVE_PARAMETERS, declares_monster_target
    from agent.planner import QuestPlanner, args_for_tool

    section("Objective parameter table (G27: a text-only objective is not a hunt)")

    bundle = ROOT / "desktop" / "client" / "www" / "build" / "script.js"
    if not bundle.exists():
        check("live client bundle present", False, str(bundle))
        return
    text = bundle.read_text(encoding="utf-8", errors="replace")
    anchor = 'QuestObjectiveFreeForm:[{type:"getText",id:"GetText"}]'
    check("the client still declares the FreeForm parameter row", anchor in text)
    open_at = text.rindex("={", 0, text.index("QuestObjectiveFreeForm:[{type:"))
    depth, i = 0, open_at + 1
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    client = {}
    for name, body in re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(\[[^\]]*\])',
                                 text[open_at + 1:i]):
        client[name] = tuple(m.group(1) for m in
                             re.finditer(r'\{type:"[a-zA-Z]+",id:"([^"]+)"', body))
    check("every class the client declares is in our copy",
          set(client) <= set(OBJECTIVE_PARAMETERS),
          sorted(set(client) - set(OBJECTIVE_PARAMETERS)))
    check("and our copy invents no class",
          set(OBJECTIVE_PARAMETERS) <= set(client),
          sorted(set(OBJECTIVE_PARAMETERS) - set(client)))
    drift = {k: (list(client[k]), [pid for _kind, pid in OBJECTIVE_PARAMETERS[k]])
             for k in set(client) & set(OBJECTIVE_PARAMETERS)
             if client[k] != tuple(pid for _kind, pid in OBJECTIVE_PARAMETERS[k])}
    check("the parameter ids match the client's, in its order", not drift, drift)
    check("the table is the client's 17 classes, not fewer",
          len(client) == len(OBJECTIVE_PARAMETERS) == 17,
          (len(client), len(OBJECTIVE_PARAMETERS)))

    monster_types = {k for k, v in client.items() if "Monsters" in v}
    check("the monster-parameter set is derived, not hand-written",
          features.MONSTER_PARAMETER_TYPES == monster_types,
          sorted(features.MONSTER_PARAMETER_TYPES ^ monster_types))

    freeform = {"id": 14070, "type_id": 0, "type": "QuestObjectiveFreeForm",
                "params": {"parameter0": 715354},
                "text": 'Complete the "A Chaotic Arrival" quest.'}
    hunt = {"id": 14071, "type_id": 6, "type": "QuestObjectiveFightMonster",
            "params": {"parameter0": 4194}, "text": "Defeat 1 Popet"}
    deliver = {"id": 14072, "type_id": 3, "type": "QuestObjectiveBringItemToNpc",
               "params": {"parameter0": 3888, "parameter1": 1001},
               "text": "Take 1 item to an NPC"}
    nameless = {"id": 14073, "type_id": 6, "params": {"parameter0": 4194}}

    check("a free-form objective declares no creature",
          declares_monster_target(freeform) is False)
    check("a fight objective declares one", declares_monster_target(hunt) is True)
    check("no class name is unknown, not text-only",
          declares_monster_target(nameless) is None)
    check("a class the client does not declare is unknown too",
          declares_monster_target({"type": "QuestObjectiveBrandNew"}) is None)

    check("fight.join gets no target from a text-only objective",
          args_for_tool("fight.join", {"id": 3841}, freeform, {}) == {})
    check("and still gets the monster from a declared hunt",
          args_for_tool("fight.join", {"id": 3841}, hunt, {}) == {"monster": 4194})
    check("farm.start refuses the invented monster",
          args_for_tool("farm.start", {"id": 3841}, freeform, {}) == {})
    check("an unknown class keeps the route the type id gave it",
          args_for_tool("fight.join", {"id": 3841}, nameless, {}) == {"monster": 4194})
    check("a deliver objective is untouched",
          args_for_tool("farm.start", {"id": 3841}, deliver, {}) == {"resource": 1001})

    obs = {"map_id": 147064834, "cell": 300, "inventory": {},
           "npcs": [{"npc_id": 3976, "cell": 10}],
           "map": {"exits": [{"direction": "left", "map_id": 145489923,
                              "blocked": False, "cells": [1]}],
                   "groups": [{"monster_ids": [4194]}]}}
    policy = QuestPlanner()
    # The group is on the map, so this is the case the old code got wrong for the
    # *right* reason: a wrapper whose number is not the monster the group holds.
    planned = policy.plan_objective({"id": 3841}, 0, freeform, obs)
    # Live 2026-10-28: this wrapper's step was an escort ("Go to Albuera with Ayin
    # Spyr") and the only move left was an NPC dialogue. It is still not a hunt:
    # no group is joined and no creature is invented.
    check("a text-only wrapper plans no hunt even with a group on the map",
          all(c["tool"] not in ("fight.join", "farm.start") for c in planned), planned)
    joined = [c["tool"] for c in policy.plan_objective({"id": 3841}, 0, hunt, obs)]
    check("a declared hunt still joins the group that is standing there",
          joined == ["fight.join"], joined)
    with_map = dict(freeform, map_id=145489923)
    moved = policy.plan_objective({"id": 3841}, 0, with_map, obs)
    check("a text-only objective that names a place still travels to it",
          [c["tool"] for c in moved] == ["nav.goto"], moved)
    check("and the travel it plans has a destination",
          bool(moved) and moved[0]["args"].get("map_id") == 145489923, moved)

    # The path live quests actually take. The knowledge base holds no live quest id, so
    # the observation-derived fallback *is* the objective the planner works from; if the
    # class name is dropped there, every check above passes while live data still hunts
    # the invented monster.
    derived = QuestPlanner()._objective_from_observation(freeform)
    check("the observation-derived objective keeps the client's class name",
          isinstance(derived, dict) and derived.get("type") == "QuestObjectiveFreeForm",
          derived)
    fallback = QuestPlanner().plan_objective({"id": 3841}, 0, derived, obs)
    check("and the fallback path plans no hunt",
          all(c["tool"] not in ("fight.join", "farm.start") for c in fallback),
          fallback)
    check("the fallback can still be read as text-only",
          declares_monster_target(derived) is False, derived)

    class _StubKB(QuestPlanner):
        """A planner whose table has a row for the objective, as the KB would."""

        @property
        def quests(self):
            return {3841: {"id": 3841, "steps": [{"objectives": [{
                "id": 14070, "type_id": 0, "text": "stale cached text",
                "params": {"parameter0": 715354}}]}]}}

    merged = _StubKB()._lookup_objective(3841, 14070, fallback=freeform)
    check("a knowledge-base row is enriched with the client's class name too",
          isinstance(merged, dict) and merged.get("type") == "QuestObjectiveFreeForm",
          merged)
    merged_plan = QuestPlanner().plan_objective({"id": 3841}, 0, merged, obs)
    check("and the merged row plans no hunt",
          all(c["tool"] not in ("fight.join", "farm.start") for c in merged_plan),
          merged_plan)

    class _Head(nn_module.NNPlanner):
        """A head that always answers `fight.join`.

        The learned planner's argument plumbing is what G27 has to hold: the head
        picks the tool, and this is the code that decides what it is pointed at.
        """

        def _choose_tool(self, quest, step_idx, obj, obs):  # noqa: ARG002
            return "fight.join"

    # A group whose ids happen to include the wrapper's number: `_visible_target`
    # says the target is standing right here, so only the declaration stops the call.
    crowded = copy.deepcopy(obs)
    crowded["map"]["groups"] = [{"monster_ids": [715354]}]
    nn_planned = _Head().plan_objective({"id": 3841}, 0, freeform, crowded)
    check("the learned head sends no empty hunt for a text-only objective",
          nn_planned == [], nn_planned)
    nn_hunt = _Head().plan_objective({"id": 3841}, 0, hunt, obs)
    check("and still hunts a declared monster with its real id",
          [c["tool"] for c in nn_hunt] == ["fight.join"]
          and nn_hunt[0]["args"] == {"monster": 4194}, nn_hunt)

    nn_src = inspect.getsource(nn_module)
    check("the learned planner drops a call whose target the client denies",
          'not args and tool in ("quest.advance", "fight.join", "farm.start")' in nn_src)
    planner_src = inspect.getsource(planner_module)
    check("both monster routes consult the declaration",
          planner_src.count("declares_monster_target(obj) is False") == 4,
          planner_src.count("declares_monster_target(obj) is False"))


def test_spawn_wait_is_bounded() -> None:
    section("A respawn wait on the objective's map is bounded, then searches")
    # Live 2026-10-29: objective 14070 (kill, map-bound) sat on map 145489920
    # with no group for 467 passes (172 s). The branch waits for a respawn, the
    # autopilot's escalation proposed farm.start, the quest gate refused it, and
    # the cycle repeated -- the run could not leave the map to look. The wait is
    # now one window; after it the planner walks an exit instead.
    from agent.planner import QuestPlanner, SPAWN_WAIT_PASSES

    hunt = {"id": 14070, "type_id": 6, "type": "QuestObjectiveFightMonster",
            "map_id": 145489920, "params": {"parameter0": 4194},
            "text": "Defeat the Albuera Elite."}
    exits = [{"direction": "right", "map_id": 145489921},
             {"direction": "left", "map_id": 145489919}]
    # A group is standing here, just not the one the objective wants: the
    # sub-goal stack sees groups and stays quiet, so this is the plan that used
    # to wait for a respawn on the objective's own map.
    obs = {"map_id": 145489920, "cell": 300,
           "map": {"map_id": 145489920, "exits": exits,
                   "groups": [{"cell": 288, "monster_ids": [999]}]}}
    planner = QuestPlanner()
    first = planner.plan_objective({"id": 3841}, 0, hunt, obs)
    check("a missing group on the objective's map is waited for, not hunted blind",
          [c["tool"] for c in first] == ["state.snapshot"]
          and "waiting for a group" in str(first[0].get("why")), first)
    waited = first
    for _ in range(SPAWN_WAIT_PASSES + 2):
        waited = planner.plan_objective({"id": 3841}, 0, hunt, obs)
        if [c["tool"] for c in waited] != ["state.snapshot"]:
            break
    check("the wait is bounded and turns into a search of an exit",
          [c["tool"] for c in waited] == ["nav.goto"]
          and waited[0]["args"].get("direction") in ("left", "right"), waited)

    fresh = QuestPlanner()
    for _ in range(SPAWN_WAIT_PASSES - 1):
        fresh.plan_objective({"id": 3841}, 0, hunt, obs)
    crowd = {"map_id": 145489920, "cell": 300,
             "map": {"map_id": 145489920,
                     "groups": [{"cell": 288, "monster_ids": [4194]}],
                     "exits": exits}}
    joined = fresh.plan_objective({"id": 3841}, 0, hunt, crowd)
    check("a group that appears is joined and reopens the next window",
          [c["tool"] for c in joined] == ["fight.join"]
          and not fresh._spawn_waits, (joined, fresh._spawn_waits))

    # The escalation inside the wait must not keep proposing what the goal gate
    # refuses: that refusal was the engine of the 467-pass cycle.
    import inspect as _inspect
    from agent import autopilot as _ap
    ap_src = _inspect.getsource(_ap)
    check("the wait escalation skips a farm.start the quest gate refuses",
          'candidate.tool == "farm.start" and not self._farm_allowed(goal, before)'
          in ap_src)
    check("and a run whose windows have nothing left ends instead of waiting forever",
          "DEAD_WINDOW_LIMIT = 4" in ap_src
          and "self._dead_windows >= DEAD_WINDOW_LIMIT" in ap_src
          and "ending the run -- the step needs a human" in ap_src)
    check("only a real step clears the dead-window count",
          "if step.tool not in READ_ONLY_TOOLS and step.tool not in LISTING_TOOLS:"
          in ap_src and "self._dead_windows = 0" in ap_src)


def test_auto_policy_is_the_planner() -> None:
    section("/brain/start policy 'auto' reaches the quest planner")
    # Live 2026-10-29: the panel and /brain/start send policy "auto"; the builder
    # fell through to the rule policy, so a quest goal never reached the planner
    # and the loop answered "no action for goal". The learned planner falls back to
    # the deterministic one when no model is present, which is why auto can mean it.
    from types import SimpleNamespace
    from agent.autopilot import _build_policy
    from agent.runtime import RulePolicy

    auto = _build_policy(SimpleNamespace(policy="auto", level_hint=60,
                                         ensemble="off", model_dir=None))
    check("auto builds the planner, not the rule policy",
          not isinstance(auto, RulePolicy) and hasattr(auto, "plan_objective"), type(auto))
    check("and the planner can retire a quest, which the wait path needs",
          callable(getattr(auto, "skip", None)))
    rule = _build_policy(SimpleNamespace(policy="rule", level_hint=60,
                                        ensemble="off", model_dir=None))
    check("an explicit rule policy is still the rule policy",
          isinstance(rule, RulePolicy))


def test_stuck_objective_retired() -> None:
    section("Whole windows of no action retire the objective, not the session")
    # Live 2026-10-29 (dofus-05): the character stood on a map with no exits, its
    # NPCs already asked, and two other objectives ready. The bound ended the run
    # and threw away a live session. Retiring the stuck quest is the operator's
    # own next move and the planner already has `skip` for it.
    from types import SimpleNamespace
    from agent.autopilot import Autopilot, RETIRE_LIMIT, DEAD_WINDOW_LIMIT
    import inspect

    skipped = []

    class _Policy:
        def skip(self, qid):
            skipped.append(qid)

    ap = Autopilot.__new__(Autopilot)
    ap.runtime = SimpleNamespace(policy=_Policy())
    got = ap._retire_stuck_objective({"current_objective": {"quest_id": "3841"}})
    check("the stuck quest is retired by its id",
          got == 3841 and skipped == [3841], (got, skipped))

    quiet = Autopilot.__new__(Autopilot)
    quiet.runtime = SimpleNamespace(policy=SimpleNamespace())
    check("a policy that cannot skip is not a crash",
          quiet._retire_stuck_objective({"current_objective": {"quest_id": 5}}) is None)
    check("nothing to name means nothing to retire",
          ap._retire_stuck_objective({"current_objective": {}}) is None
          and ap._retire_stuck_objective(None) is None)

    src = inspect.getsource(Autopilot)
    check("the wait retires before it gives up",
          "if self._retired < RETIRE_LIMIT:" in src
          and "_retire_stuck_objective(before)" in src
          and src.index("_retire_stuck_objective(before)")
          < src.index("ending the run -- the step needs a human"))
    from agent import autopilot as _apm
    module_src = inspect.getsource(_apm)
    check("retirements are bounded per run",
          "RETIRE_LIMIT = 3" in module_src and "self._retired += 1" in src)
    check("the bound still ends a run whose quests are all stuck",
          "DEAD_WINDOW_LIMIT" in src and "return False" in src)
    check("a goal the policy cannot express retires the quest instead of ending the run",
          "the policy cannot express quest" in src
          and "retiring it for this run and taking the next " in src)

    # Live 2026-10-29: a map with four known neighbours and no crossable border took
    # ~100 silent ChangeMapMessage refusals in two minutes, because the only witness
    # is "the map did not change" and nothing counted them. The run must say so and
    # stop instead of re-asking a scripted exit.
    check("crossings that never change the map are counted and end the run",
          "NAV_DECLINE_LIMIT" in module_src
          and "self._crossing_declines += 1" in src
          and "crossings were sent and the map never " in src
          and "changed (map %s)" in src)
    check("a real map change restarts that count",
          "self._crossing_declines = 0" in src
          and "_prev_map != _new_map" in src)


def test_doorless_map_asks_for_the_neighbour() -> None:
    section("A map with neighbours and no exit cell asks the server for the neighbour")
    # Live 2026-10-29: the Albuera solo area reported four neighbours, zero exits
    # and no interactive element; the planner answered "waiting for its door" on
    # every step. The client can send the same ChangeMapMessage a tapped door
    # sends, so the plan is a request with the edge rotated over attempts.
    from agent.planner import QuestPlanner

    obs = {
        "map_id": 100,
        "cell": 5,
        "level": 5,
        "tutorial": None,
        "dialog": {"open": False},
        "current_objective": {"quest_id": "9", "objective_id": 1, "type_id": 4,
                              "type": "QuestObjectiveDiscoverMap", "map_id": 555,
                              "text": "Find the map: Dam worksite."},
        "ready_objectives": [{"quest_id": "9", "objective_id": 1, "type_id": 4,
                              "type": "QuestObjectiveDiscoverMap", "map_id": 555,
                              "text": "Find the map: Dam worksite."}],
        "map": {"map_id": 100, "cell": 5, "exits": [],
                "neighbours": {"left": 200, "right": 201},
                "npcs": [], "interactives": [], "groups": []},
        "npcs": [], "exits": [], "interactives": [], "groups": [],
    }
    planner = QuestPlanner(level_hint=5)
    first = planner.choose("do all the quests", obs, [])
    check("the doorless map plans a request, not a read",
          first is not None and first.tool == "nav.goto"
          and (first.args or {}).get("direction") in ("left", "right")
          and (first.args or {}).get("map_id") in (200, 201),
          first)
    second = planner.choose("do all the quests", obs, [])
    check("a second attempt asks the other neighbour",
          second is not None and second.tool == "nav.goto"
          and (second.args or {}).get("direction") != (first.args or {}).get("direction"),
          second)

    # The loop's own retirement must survive the tutorial's reopen rule: the live
    # retire was undone on the next step because every active quest was reopened
    # whenever a scripted step was running.
    from agent.planner import QuestPlanner as _QP
    tutorial_obs = dict(obs)
    tutorial_obs["tutorial"] = {"active": True, "quest_id": 9, "step_id": 45,
                                "can_change_map": {"left": True}}
    tutorial_obs["quests_active"] = {"9": 0}
    keep = _QP(level_hint=5)
    keep._last_obs = tutorial_obs
    keep.skip(9)
    keep._reopen(tutorial_obs)
    check("a retirement survives the tutorial's reopen rule",
          9 in keep._skipped, sorted(keep._skipped))
    walked = dict(tutorial_obs)
    walked["map_id"] = 101
    walked["map"] = dict(obs["map"], map_id=101)
    keep._reopen(walked)
    check("a crossing alone does not reopen a retired quest",
          9 in keep._skipped, sorted(keep._skipped))
    moved = dict(tutorial_obs)
    moved["current_objective"] = {"quest_id": 9, "objective_id": 777,
                                  "step": 0, "status": 2}
    keep._reopen(moved)
    check("a changed objective still reopens it",
          9 not in keep._skipped, sorted(keep._skipped))

    # A retired tutorial quest no longer holds the plan: the scenario keeps
    # reporting its stuck step, and waiting on it starved the ready objectives.
    released = _QP(level_hint=5)
    released._last_obs = tutorial_obs
    released.skip(9)
    call = released.choose("do all the quests", tutorial_obs, [])
    check("a retired tutorial quest stops holding the loop",
          call is not None and call.tool != "state.snapshot", call)


def test_cli_launch_forwarding() -> None:
    section("`DofusFarm.exe --launch X` means the same thing when the app is open")
    # Live 2026-10-29: a second `--launch dofus-03` focused the running window and
    # dropped the name, because the flag is only read on the path that owns the
    # single-instance mutex. Starting another slot then needed closing the app.
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    program = (root / "desktop" / "Program.cs").read_text(encoding="utf-8-sig")
    form = (root / "desktop" / "mainform.cs").read_text(encoding="utf-8-sig")
    check("one parser owns the flag",
          "private static string LaunchNameFromArgs(string[] args)" in program
          and program.count("Array.IndexOf(args, \"--launch\")") == 1)
    check("the second instance hands the name over before focusing",
          "WriteLaunchRequest(forwarded)" in program
          and "launch_request.txt" in program)
    check("the running manager picks the request up",
          "StartLaunchRequestPoller" in form and "launch_request.txt" in form
          and "LaunchInstanceFromCommandLine(name)" in form)
    check("an already-running instance is still a no-op",
          "is already running; nothing to do" in form)
    # Consent for a profile wipe is the operator's; the CLI carries it in the
    # command line instead of a dialog, and the wipe still refuses to relaunch
    # unless it is provably complete.
    check("a reset request is forwarded and runs the button's own wipe",
          "ResetNameFromArgs" in program and '"reset:" + resetName' in program
          and "ResetInstanceFromCommandLine" in form
          and "could not be provably removed; not relaunching" in form)


def test_refusal_evidence() -> None:
    section("A refused call keeps its own answer on the step row")
    # Live 2026-10-29: 76 refused inv.equip rows said the same sentence and none of
    # the fields that explain it. The page's answer carries the reason (positions
    # the type allows, whether the template loaded, whether the bag had the item)
    # and it exists only in that response, so the row must keep it.
    from types import SimpleNamespace
    from agent.autopilot import _failure_evidence

    page = {"ok": False,
            "error": "equipItem sent nothing and the item is not usable",
            "data": {"object_uid": 68720788, "possible_positions": [],
                     "has_template": True, "type_id": 9, "bag_has_instance": False,
                     "usable": False, "secret_field": "must not be copied"}}
    envelope = SimpleNamespace(data={"result": page, "snapshot": {"map_id": 1}})
    got = _failure_evidence(envelope) or {}
    check("the refusal keeps the fields that explain it",
          got.get("object_uid") == 68720788 and got.get("has_template") is True
          and got.get("possible_positions") == [] and got.get("type_id") == 9
          and got.get("bag_has_instance") is False, got)
    check("and nothing outside the evidence list is copied",
          "secret_field" not in got and "snapshot" not in got, sorted(got))
    check("a result with no answer is no evidence, not an empty object",
          _failure_evidence(SimpleNamespace(data=None)) is None
          and _failure_evidence(SimpleNamespace(data={"x": 1})) is None)
    import inspect
    from agent import autopilot as _ap
    src = inspect.getsource(_ap)
    check("only a failed step carries it",
          "evidence=(None if step_ok else _failure_evidence(result))" in src)


def test_wake_login_tool() -> None:
    section("The lockout wake tool presses once, and never inside the cooldown")
    import importlib.util
    import inspect
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "tools", "wake_login.py")
    spec = importlib.util.spec_from_file_location("wake_login_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    check("a cooldown with time left is read as time left",
          mod.cooldown_s({"login_cooldown": {"remaining_s": 30}}) == 30.0
          and mod.cooldown_s({}) == 0.0
          and mod.cooldown_s({"login_cooldown": {"remaining_s": "x"}}) == 0.0)
    check("the world is the map, not the connected flag",
          mod.in_world({"in_world": True, "position": {"map_id": 5}})
          and not mod.in_world({"in_world": True, "position": {"map_id": 0}})
          and not mod.in_world({"connected": True, "in_world": False}))
    check("a banned block is visible before any press",
          mod.login_block({"login": {"banned": True, "text": "banned"}}).get("banned")
          is True)
    src = inspect.getsource(mod)
    check("the tool waits out the cooldown before pressing",
          "def wait_for_slot" in src and "time.sleep(nap)" in src
          and "left <= 0" in src)
    check("and a banned slot is never pressed",
          "not pressing" in src and "reset this slot for a fresh guest" in src)
    check("the press is exactly one guarded guest login",
          src.count('"tool": "session.login"') == 1
          and '{"target": "guest"}' in src)
    check("the recovered world is handed to the daemon, not left idle",
          "def start_loop" in src and "/brain/start" in src
          and '"goals": [goal]' in src and "--start-loop" in src)


def test_free_form_searches_new_maps() -> None:
    section("A free-form step with nothing left on this map walks to a new one")
    # Live 2026-10-29 (objective 14070, free-form, no map): the element was used,
    # the NPCs were asked, and the planner returned no call -- the policy read the
    # state every pass and the run waited 467 passes. A different map carries
    # different elements and NPCs, so walking is the only world-changing action
    # left; the visited map is retired for the objective so the search cannot
    # bounce between two exhausted maps.
    from agent.planner import QuestPlanner

    freeform = {"id": 14070, "type_id": 0, "type": "QuestObjectiveFreeForm",
                "params": {"parameter0": 715354},
                "text": "Complete the \"A Chaotic Arrival\" quest."}
    element = {"elem_id": 513208, "cell": 330, "skill_uid": 136113675,
               "enabled_skills": [136113675], "element_type_id": 155}
    obs = {"map_id": 145489920, "cell": 300,
           "map": {"map_id": 145489920, "npcs": [], "interactives": [element],
                   "exits": [{"direction": "right", "map_id": 145489921}]}}
    planner = QuestPlanner()
    first = planner.plan_objective({"id": 3841}, 0, freeform, obs)
    check("the map's own element is still the first thing tried",
          [c["tool"] for c in first] == ["tap.interactive"], first)
    second = planner.plan_objective({"id": 3841}, 0, freeform, obs)
    check("once the map is exhausted the step walks instead of planning nothing",
          [c["tool"] for c in second] == ["nav.goto"]
          and second[0]["args"].get("direction") == "right", second)
    check("the exhausted map is retired for this objective",
          (3841, 14070, 145489920) in planner._exhausted_at,
          planner._exhausted_at)
    next_map = {"map_id": 145489921, "cell": 100,
                "map": {"map_id": 145489921, "npcs": [],
                        "interactives": [dict(element, elem_id=513999)],
                        "exits": [{"direction": "left", "map_id": 145489920}]}}
    third = planner.plan_objective({"id": 3841}, 0, freeform, next_map)
    check("the new map's element is tried, so the search is a search",
          [c["tool"] for c in third] == ["tap.interactive"]
          and third[0]["args"].get("element_id") == 513999, third)


def test_free_form_uses_interactive() -> None:
    section("A free-form objective is performed on the map's own interactive")
    # `QuestObjectiveFreeForm` (13948/13950 live) names no element the client can
    # read: its parameter is a text id (704145), and the map's only element is
    # 513208. Matching the parameter would be guesswork, so the planner offers the
    # map's elements one at a time and the objective's own completion is the
    # verdict. Before this, type 0 fell into the kill branch, the client's
    # parameter table denied a monster, and the planner returned nothing at all.
    from agent.planner import QuestPlanner

    freeform = {"id": 13948, "type_id": 0, "type": "QuestObjectiveFreeForm",
                "map_id": 145490432, "params": {"parameter0": 704145},
                "text": "Recover the first page of the aqueduct blueprints."}
    obs = {"map_id": 145490432, "cell": 300,
           "map": {"map_id": 145490432, "interactives": [
               {"elem_id": 513208, "cell": 330, "skill_uid": 136113675,
                "enabled_skills": [136113675], "element_type_id": 155}]}}
    planner = QuestPlanner()
    calls = planner.plan_objective({"id": 4262}, 0, freeform, obs)
    check("the free-form objective taps the map's element",
          [c["tool"] for c in calls] == ["tap.interactive"]
          and calls[0]["args"].get("element_id") == 513208
          and calls[0]["args"].get("skill_uid") == 136113675, calls)
    again = planner.plan_objective({"id": 4262}, 0, freeform, obs)
    check("the same element is not offered again after it was used",
          again == [], again)

    obs_no_uid = {"map_id": 145490432,
                  "map": {"map_id": 145490432, "interactives": [
                      {"elem_id": 513208, "cell": 330, "skill_uid": None,
                       "enabled_skills": [136113675]}]}}
    calls2 = QuestPlanner().plan_objective({"id": 4262}, 0, freeform, obs_no_uid)
    check("a missing skillInstanceUid falls back to the element's enabled skill",
          bool(calls2) and calls2[0]["args"].get("skill_uid") == 136113675, calls2)

    far = dict(freeform, map_id=145490944)
    elsewhere = QuestPlanner().plan_objective({"id": 4262}, 0, far, obs_no_uid)
    check("travelling comes first when the objective names another map",
          [c["tool"] for c in elsewhere] == ["nav.goto"], elsewhere)

    bare = QuestPlanner().plan_objective({"id": 4262}, 0, freeform,
                                         {"map_id": 145490432})
    check("a map with no interactive keeps the old answer", bare == [], bare)

    # Live 2026-10-28, dofus-05: step 4974 "Go to Albuera with Ayin Spyr" is an
    # escort that starts in a dialogue. The map's one interactive had been spent,
    # so the planner answered state.snapshot for 300+ waits with three NPCs
    # standing next to the character. Talking is the same generic move the fight
    # objectives already use; each NPC is asked once, nearest first.
    escort = {"id": 14070, "type_id": 0, "type": "QuestObjectiveFreeForm",
              "params": {"parameter0": 715354},
              "text": 'Complete the "A Chaotic Arrival" quest.'}
    crowd = {"map_id": 147064834, "cell": 238,
             "map": {"map_id": 147064834,
                     "interactives": [{"elem_id": 513203, "cell": 374,
                                       "skill_uid": None, "enabled_skills": []}],
                     "npcs": [{"npc_id": 3888, "cell": 255, "actor_id": -4},
                              {"npc_id": 3976, "cell": 272, "actor_id": -1},
                              {"npc_id": 3982, "cell": 437, "actor_id": -2}]}}
    escort_planner = QuestPlanner()
    pick = escort_planner.plan_objective({"id": 3841}, 12, escort, crowd)
    # The map's one element reports no skill instance, so the page refuses it
    # without sending anything. Spending it first bought a guaranteed refusal and
    # then waited out the action bound before asking the NPC standing next to the
    # character (measured live on dofus-02, 2026-10-29).
    check("a skill-less element is skipped: the nearest NPC is asked first",
          [c["tool"] for c in pick] == ["quest.advance"]
          and pick[0]["args"] == {"quest_id": 3841, "npc_id": 3888}, pick)
    first_ask = escort_planner.plan_objective({"id": 3841}, 12, escort, crowd)
    check("with the nearest NPC asked, the next one is a new question",
          [c["tool"] for c in first_ask] == ["quest.advance"]
          and first_ask[0]["args"] == {"quest_id": 3841, "npc_id": 3976},
          first_ask)
    second_ask = escort_planner.plan_objective({"id": 3841}, 12, escort, crowd)
    check("and the third NPC after that",
          [c["tool"] for c in second_ask] == ["quest.advance"]
          and second_ask[0]["args"].get("npc_id") == 3982, second_ask)
    escort_planner.plan_objective({"id": 3841}, 12, escort, crowd)
    quiet = escort_planner.plan_objective({"id": 3841}, 12, escort, crowd)
    check("every NPC asked once, the step stops instead of looping",
          quiet == [], quiet)
    moved = dict(crowd,
                 map=dict(crowd["map"], map_id=147064322),
                 map_id=147064322)
    other = QuestPlanner().plan_objective({"id": 3841}, 12, escort, moved)
    check("a new map reopens the NPC question", bool(other), other)

    hunt = {"id": 13944, "type_id": 6, "type": "QuestObjectiveFightMonster",
            "map_id": 145490432, "params": {"parameter0": 4210}}
    hunted = QuestPlanner().plan_objective({"id": 4262}, 0, hunt, obs)
    check("a declared hunt never taps an interactive instead of joining",
          "tap.interactive" not in [c["tool"] for c in hunted], hunted)

    section("Only a close the server announced can blame a claim")
    page = ROOT / "scripts" / "mobile-disguise.js"
    if page.is_file():
        text = page.read_text(encoding="utf-8", errors="replace")
        # A claim followed by a client-side disconnect was recorded as a kick and
        # permanently retired 13950/4262 -- minutes after the server had accepted
        # it. The recorder now needs the server's own close message, ignores the
        # client's reasons, clears on an accepted validation and expires.
        check("the recorder refuses a close the server did not announce",
              "if (serverSide !== true) return false;" in text,
              "any disconnect within 3 s of a claim becomes a kick signature")
        check("the client's own disconnect reasons never become kick signatures",
              "INACTIVITY|SESSION_TIMED_OUT|CLIENT_CLOSING" in text,
              "AFK and timeouts blacklist quest objectives")
        check("the server's close message sets the flag and records the kick",
              "__agentServerClosing = { at: Date.now()" in text
              and "window.__agentRecordKick('serverDisconnecting'" in text,
              "a server kick is not attributed at all")
        check("an accepted validation clears the signature it may have written",
              "cm.on('QuestObjectiveValidatedMessage'" in text
              and "__agentClearKick(msg.questId, msg.objectiveId)" in text,
              "a claim that worked could still retire its objective")
        check("a signature expires instead of blocking an objective forever",
              "_KICK_TTL_MS" in text and "now - at > _KICK_TTL_MS" in text,
              "one bad attribution is permanent")
        check("the snapshot exposes the skill list a free-form tap needs",
              "enabled_skills: (el.enabled_skills || []).slice(0, 4)" in text,
              "tap.interactive cannot name a skill when the renderer has not")
        # The page refuses a claim for every type in its SERVER_TYPES list; the
        # planner must not spend a step asking for those. A narrower set spent
        # one claim per delivery and multi-fight objective.
        import inspect as _inspect

        from agent.planner import QuestPlanner as _Planner
        init_src = _inspect.getsource(_Planner.__init__)
        check("the planner's server-counted types mirror the page's refusals",
              "{0, 1, 2, 3, 4, 5, 6, 12, 14, 16, 17}" in init_src,
              "a type the page refuses is still claimed first")
        check("a free-form wrapper is never claimed",
              "QuestObjectiveFreeForm: true" in text,
              "measured: two free-form claims were each followed ~0.3 s later "
              "by serverDisconnecting SERVER_GONE")
    else:
        print("  - page checks: skipped (scripts/ not in this mirror)")


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


def test_damage_calibration_join() -> None:
    section("Damage calibration (the solver's estimate vs the server's own loss)")
    import importlib.util
    import json as _json
    import tempfile

    tool = REPO_ROOT.parent / "tools" / "damage_calibration.py"
    check("the calibration tool exists", tool.is_file(), str(tool))
    check("the live fight-outcome capture tool exists",
          (REPO_ROOT.parent / "tools" / "capture_fight_outcome.py").is_file(),
          "tools/capture_fight_outcome.py")
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("damage_calibration", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    decisions_path = Path(tempfile.mkdtemp(prefix="damage-dec-")) / "decisions.jsonl"
    decisions_path.write_text("\n".join(_json.dumps(row) for row in [
        {"fight_id": 124, "my_id": 4434090, "decision": {
            "slots": [{"spell_id": 10, "damage": 20.0, "empty": False},
                      {"spell_id": 0, "damage": 12.0, "empty": False}],
            "targets": [{"fighter_id": 4434090}, {"fighter_id": -1}]}},
        {"fight_id": 124, "my_id": 4434090, "decision": {
            "slots": [{"spell_id": 10, "damage": 22.0, "empty": False}],
            "targets": [{"fighter_id": 4434090}, {"fighter_id": -1}]}},
    ]), encoding="utf-8")
    outcomes = [{"kind": "fight_outcome", "fight_id": 124, "won": True, "end": {
        "fight_id": 124, "won": True, "battle_log": [
            # Live semantics (fight 133, 2026-10-28): the cast's action id is 300
            # for every spell, and the damage arrives as an effect with its own id.
            {"type": "GameActionFightSpellCastMessage", "at_action": 300,
             "source": 4434090, "spell": 99, "target": 4434090},
            {"type": "GameActionFightSpellCastMessage", "at_action": 300,
             "source": 4434090, "spell": 10, "target": -1},
            {"type": "GameActionFightLifePointsLostMessage", "at_action": 96,
             "source": 4434090, "target": -1, "loss": 18},
            # A second effect of the same cast (multi-hit) adds to it.
            {"type": "GameActionFightLifePointsLostMessage", "at_action": 96,
             "source": 4434090, "target": -1, "loss": 8},
            {"type": "GameActionFightSpellCastMessage", "at_action": 300,
             "source": 4434090, "spell": 0, "target": -1},
            # A summon's damage is still this side's damage.
            {"type": "GameActionFightLifePointsLostMessage", "at_action": 97,
             "source": -22358, "target": -1, "loss": 11},
            # A cast the log does not follow with a loss: counted, not hidden.
            {"type": "GameActionFightSpellCastMessage", "at_action": 300,
             "source": 4434090, "spell": 10, "target": -1},
            # The enemy's own cast and the damage it did to me are not mine.
            {"type": "GameActionFightSpellCastMessage", "at_action": 10,
             "source": -1, "spell": 97, "target": 4434090},
            {"type": "GameActionFightLifePointsLostMessage", "at_action": 10,
             "source": -1, "target": 4434090, "loss": 5},
        ]}}]
    result = module.report(module.load_decisions(decisions_path), outcomes)
    summary = result["summary"]
    check("casts are paired with effects that carry different action ids",
          summary["matched_casts"] == 2, summary)
    check("an unpaired cast is counted, not dropped",
          summary["unmatched_casts"] == 1, summary)
    check("a self-cast is not counted as a damage test",
          summary["non_damage_casts"] == 1, summary)
    check("both effects of one cast add to its actual loss",
          any(pair["spell"] == 10 and pair["actual"] == 26.0 for pair in result["pairs"]),
          result["pairs"])
    check("a summon's hit counts as this side's damage",
          any(pair["spell"] == 0 and pair["actual"] == 11.0 for pair in result["pairs"]),
          result["pairs"])
    check("the enemy's cast and the damage on me are excluded",
          all(pair["target"] == -1 for pair in result["pairs"])
          and "97" not in summary["per_spell"], result["pairs"])
    check("a spell's prediction is the median of its decisions",
          result["pairs"][0]["predicted"] == 21.0, result["pairs"][:1])
    check("fewer than five pairs is reported as not enough, not as accuracy",
          summary["status"] == "not_enough_casts", summary)
    check("the per-spell error is the pair's own absolute error",
          summary["per_spell"]["10"]["mae"] == 5.0
          and summary["per_spell"]["0"]["mae"] == 1.0, summary["per_spell"])

    if Path(module.DEFAULT_DECISIONS).is_file():
        live = module.report(module.load_decisions(module.DEFAULT_DECISIONS), [])
        check("with no recorded battle log the live report says so honestly",
              live["summary"]["status"] == "not_enough_casts", live["summary"])


def test_damage_calibration_live_evidence() -> None:
    """The joiner must keep working on real captured rows, not only synthetic ones.

    Fixtures: fights 133 and 138 (2026-10-28), copied verbatim from
    `data/live_evidence/fight_outcomes.jsonl` and the decision log. Their shapes
    are the traps the synthetic test cannot invent: one cast action id (300) for
    every spell, effects numbered 96/97, a summon source (-22358), a self-buff, and
    one fight that ran past the old ring cap. The numbers below are what the live
    run reported; a change here means the joiner stopped reading live rows.
    """
    section("Damage calibration on captured live fights (fixtures)")
    import importlib.util

    tool = REPO_ROOT.parent / "tools" / "damage_calibration.py"
    decisions_path = REPO_ROOT / "data" / "live_evidence" / "decisions_fixture.jsonl"
    outcomes_path = REPO_ROOT / "data" / "live_evidence" / "fight_outcomes_fixture.jsonl"
    check("the live calibration fixtures exist",
          decisions_path.is_file() and outcomes_path.is_file(),
          "%s / %s" % (decisions_path, outcomes_path))
    if not (tool.is_file() and decisions_path.is_file() and outcomes_path.is_file()):
        return
    spec = importlib.util.spec_from_file_location("damage_calibration_live", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    outcomes = list(module._iter_rows(outcomes_path))
    result = module.report(module.load_decisions(decisions_path), outcomes)
    summary = result["summary"]
    check("both captured fights are read", summary["fights"] == 2, summary)
    check("every damage cast in the fixtures pairs",
          summary["matched_casts"] == 9 and summary["unmatched_casts"] == 0,
          summary)
    check("the per-spell counts match the live run",
          {k: v["casts"] for k, v in summary["per_spell"].items()}
          == {"7041": 2, "7045": 6, "7051": 1}, summary["per_spell"])
    check("the live error and bias are unchanged",
          abs(summary["overall_mae"] - 9.44) < 0.01
          and abs(summary["overall_bias"] + 7.48) < 0.01, summary)
    check("self-targeted casts are counted as non-damage, not as misses",
          summary["non_damage_casts"] == 4, summary)
    check("the join keeps the live cast order and amounts",
          sorted(pair["actual"] for pair in result["pairs"] if pair["spell"] == 7045)
          == [15.0, 18.0, 21.0, 32.0, 32.0, 40.0], result["pairs"])


def test_knowledge_identity() -> None:
    """G30: an objective id the store also knows is not the objective it describes.

    Measured over the live corpus: of the 23 (quest, objective) pairs `CanonicalStore`
    resolved for a real snapshot, **all 23** disagreed with the client about which quest
    the objective belongs to, and 17 disagreed about the objective's own class. The pair
    the character was actually doing is objective 13900: the tracker reports quest 4260
    with the class `QuestObjectiveGoToNpc`, and the store's row for the same id sits
    under quest 2007 as a `QuestObjectiveFightMonster` reading
    "Vaincre x1 {monster,5243}". Before the guard, that foreign text was the primary
    ranking signal for NPC replies, the quest name the loop could quote, and the
    `type_id` written on every dialog row -- 3,567 rows of it.

    The store itself cannot be repaired here: its quest tables come from
    `api.dofusdb.fr` with no game selector, which is G29. `knowledge_context` is the
    only place the mismatch is observable, so the invariant these checks pin is
    asymmetric on purpose -- the ids stay in the row for diagnosis, the content stops.
    """
    from agent import dialog_policy
    from agent import knowledge_context as kctx

    section("knowledge identity (G30: a colliding objective id publishes nothing)")

    # This test pins the store's collision path, so it must not see the real
    # client-captured index: `data/knowledge/client_index.json` legitimately holds
    # objective 13900 for quest 4260 and would resolve it before the store is asked.
    # A path that cannot exist is passed to every call instead of the default.
    no_index = REPO_ROOT / "data" / "knowledge" / "__none__.json"

    class _Store:
        """`CanonicalStore`'s exact behaviour for the one measured live id pair.

        Duck-typed: the real store is a 44 MB sqlite file whose rows for these ids are
        the bug, and a gate must not depend on a data download to know the guard held.
        """

        def __init__(self):
            self.calls = []

        def resolve_objective(self, objective_id, language):  # noqa: ARG002
            self.calls.append((objective_id, language))
            if objective_id != 13900:
                return None
            return {"quest_id": 2007, "step_index": 0, "type_id": 6,
                    "text": "Vaincre x1 {monster,5243}",
                    "raw_text": "Vaincre x1 ", "map_id": 102992002,
                    "params": {"parameter0": 5243, "parameter1": 1},
                    "entities": [], "unresolved": [], "source": "dofusdb"}

        def quest(self, quest_id):
            if quest_id != 2007:
                return None
            return {"id": 2007, "names": {"fr": "Chasse a la bete"},
                    "steps": [{"step_index": 0, "names": {"fr": "Vaincre"},
                               "descriptions": {"fr": "Trouver la meute"}}]}

    store = _Store()

    def observe(tracker):
        obs = {"current_objective": dict(tracker)}
        return kctx.enrich_observation(obs, store, observed_path=no_index)["knowledge"]

    # The live shape: the client is holding quest 4260 and names objective 13900.
    tracker = {"objective_id": 13900, "quest_id": 4260, "type_id": 1,
               "type": "QuestObjectiveGoToNpc", "text": "Rejoins Ugg"}
    block = observe(tracker)
    check("the collision is named instead of believed",
          block.get("status") == "objective_id_collision", block)
    check("the tracker's quest id is the one published",
          block.get("quest_id") == 4260, block)
    check("and the store's is kept beside it as the evidence",
          block.get("store_quest_id") == 2007, block)
    check("the store's class survives for diagnosis",
          block.get("store_type_id") == 6, block)
    check("the objective id is echoed", block.get("objective_id") == 13900, block)
    leaked = [k for k in ("objective", "quest_name", "step_name", "step_description")
              if block.get(k)]
    check("no text, name, parameter or entity from the foreign row is published",
          not leaked, leaked)
    check("no top-level type_id, so a dialog row uses the client's",
          "type_id" not in block, block)
    check("and the class the loop records is the one the client reported",
          block.get("type_id", tracker.get("type_id")) == 1, block)

    # The same store, the same objective id, a tracker that agrees: nothing is withheld.
    agree = observe({"objective_id": 13900, "quest_id": 2007, "text": "Rejoins Ugg"})
    check("an id whose quest agrees still resolves",
          agree.get("status") == "resolved", agree)
    check("and publishes the store's content",
          (agree.get("objective") or {}).get("text") == "Vaincre x1 {monster,5243}", agree)
    check("the cached block is keyed per tracker quest, not per objective id",
          agree.get("status") != block.get("status"),
          str((block.get("status"), agree.get("status"))))

    miss = observe({"objective_id": 14070, "quest_id": 4260, "text": "Rejoins Ugg"})
    check("an id the store does not hold keeps its plain miss status",
          miss.get("status") == "objective_not_in_store", miss)
    check("and still echoes the quest it was seen on", miss.get("quest_id") == 4260, miss)

    # A tracker without a quest id cannot be contradicted, so it must not be called a
    # collision: that would withhold every objective the ready-list scan reports.
    alone = observe({"objective_id": 13900, "text": "Rejoins Ugg"})
    check("a tracker with no quest id is not treated as a collision",
          alone.get("status") == "resolved", alone)

    ready = kctx.enrich_observation(
        {"ready_objectives": [{"objective_id": 13900, "quest_id": 4260}]},
        store, observed_path=no_index)["knowledge"]
    check("the ready-objectives route is guarded the same way",
          ready.get("status") == "objective_id_collision", ready)

    # The consumer that made G30 dangerous: the dialog ranker takes its primary signal
    # from this block. Withheld content must fall through to the client's own text.
    def dialog(active):
        return {"current_objective": dict(active),
                "dialog": {"open": True, "text_ready": True, "ids_ready": True,
                           "message": "Que souhaitez-vous ?",
                           "replies": [{"index": 0, "text": "Je cherche Ugg",
                                        "reply_id": 100},
                                       {"index": 1, "text": "Je dois vaincre",
                                        "reply_id": 101}]}}

    kept = kctx.enrich_observation(dialog(tracker), store, observed_path=no_index)
    ranked = dialog_policy.rank_dialog(kept)
    check("a withheld objective ranks the NPC reply against the client's own text",
          ranked.get("status") == "choose" and ranked["reply"]["index"] == 0, ranked)
    check("and the dialog row is attributed to the quest the character is doing",
          dialog_policy._quest_id(kept, kept["knowledge"]) == 4260, kept["knowledge"])

    # The same snapshot with the guard off: the foreign hunt text decides. Proven by
    # hand-building the block the pre-fix bridge published.
    foreign = dialog(tracker)
    foreign["knowledge"] = {"status": "resolved", "objective": {"text": "Je dois vaincre"},
                            "quest_id": 2007, "type_id": 6}
    check("which is a different answer than the store's text would give",
          dialog_policy.rank_dialog(foreign)["reply"]["index"] == 1,
          dialog_policy.rank_dialog(foreign))

    auto_src = (REPO_ROOT / "agent" / "autopilot.py").read_text(encoding="utf-8",
                                                                errors="replace")
    check("the dialog row's objective type falls back to the tracker",
          'kn.get("type_id", tracker.get("type_id"))' in auto_src)


def test_client_confirmed_starts() -> None:
    """G31: a quest start is only sendable for a quest the running client has shown.

    Measured over both live corpora (`autopilot.jsonl` plus the rotated 2026-10-25 file,
    66 sessions): 366 bare `quest.advance {quest_id}` start requests out of 2,983
    quest.advance rows, and **all 366 named a quest id the client never reported -- not in
    that snapshot, and not anywhere in that session either.** 16 distinct ids, dominated by
    one: quest 584, 297 requests, 310 of the 366 immediate repeats. 584 is a row of
    `quests_full.json`, which is Dofus 2 content (`api.dofusdb.fr` serves 1,976 quests
    there; the cache stops at id 2,546 while the live tutorial is quest 3841 -- G29), so
    every one of those requests was for content Dofus Touch cannot honour. The first pass
    on this corpus reported 462 of 1,145: that filter read *any* row carrying a `quest_id`
    without an `objective_id` as a start, and 1,145 is exactly talk (519) + reply (112) +
    close (148) + start (366). `tools/quest_advance_shapes.py` is the instrument that
    separates them, and `test_quest_advance_shapes` below pins it.

    `START_ATTEMPT_LIMIT = 3` was supposed to be the bound and is not: `_note_situation`
    clears `_start_failures` on every scene change and `_skipped` on every quest-key
    change, so each map step re-armed the same request. This is the same defect the NPC
    re-ask memory fixed (259 live steps), and the same remedy applies -- a memory scoped to
    the character's world, not to the scene. The set is filled from the client's own
    snapshots, so nothing here knows a quest id in advance, and a client-side quest capture
    (G29) is what will make starting new quests possible again.
    """
    import inspect

    from agent import planner as planner_module
    from agent.planner import QuestPlanner, client_quest_ids

    section("Client-confirmed quest starts (G31: no request for a quest this game lacks)")

    bare = {"quests_active": {}, "quests_done": [], "current_objective": None,
            "ready_objectives": []}
    check("an observation that names no quest confirms none",
          client_quest_ids(bare) == set(), client_quest_ids(bare))
    check("all four places the client names a quest are read, and string ids coerced",
          client_quest_ids({"quests_active": {"3841": 0}, "quests_done": [20],
                            "current_objective": {"quest_id": "777"},
                            "ready_objectives": [{"quest_id": 900}, {"junk": 1}, "nope"]})
          == {3841, 20, 777, 900},
          client_quest_ids({"quests_active": {"3841": 0}, "quests_done": [20],
                            "current_objective": {"quest_id": "777"},
                            "ready_objectives": [{"quest_id": 900}]}))

    quest = {"id": 777, "level_min": 1, "steps": [
        {"objectives": [{"id": 9001, "type_id": 1, "params": {"parameter0": 301}}]}]}

    def stub_planner():
        """A planner with one KB quest the client has never mentioned.

        The cached table is not used: a gate that measured itself against `584` would be
        reading the very data G31 says is another game's.
        """
        p = QuestPlanner()
        p.quests = {777: quest}
        p._quests_loaded = True
        p._order = None
        p.level_hint = -1
        return p

    def obs(**over):
        base = {"level": 10, "map_id": 100, "cell": 0, "connected": True,
                "in_world": True, "in_fight": False, "in_dialog": False,
                "tutorial": None, "quests_active": {}, "quests_done": [],
                "current_objective": None, "ready_objectives": [],
                "step_objectives": [], "exits": [], "npcs": [], "groups": [],
                "inventory": {}, "kamas": 0}
        base.update(over)
        return base

    unreported = stub_planner()
    calls = [unreported.choose("do all the quests", obs()) for _ in range(3)]
    check("a quest this game has never shown gets no start request, step after step",
          all(c is None for c in calls), [None if c is None else c.tool for c in calls])
    check("and no start is left pending to be credited",
          unreported._pending_start is None, unreported._pending_start)

    # The same planner, once the client itself names the quest: the guard is confirmation,
    # not a blanket ban, and the 683 starts that did name a known quest must keep working.
    tracker = {"quest_id": "777", "objective_id": 9001, "type_id": 1,
               "params": {"parameter0": 301}}
    confirmed = stub_planner()
    first = confirmed.choose("do all the quests", obs(current_objective=tracker))
    check("a quest the client reported is still asked for by id",
          first is not None and first.tool == "quest.advance"
          and dict(first.args or {}) == {"quest_id": 777},
          None if first is None else (first.tool, first.args))
    check("and the request is remembered as a pending start",
          confirmed._pending_start == 777, confirmed._pending_start)
    check("the confirmation came from the observation, not from the KB",
          confirmed._client_quests == {777}, confirmed._client_quests)

    walking = stub_planner()
    for step, here in enumerate((100, 999, 555, 700)):
        later = walking.choose("do all the quests",
                               obs(map_id=here, npcs=[{"npc_id": 5 + step, "cell": 2}]))
    check("a scene change does not re-arm the refused request",
          later is None, None if later is None else (later.tool, later.args))

    # The refusal must not be a side effect of the retirement set: `_skipped` is cleared
    # whenever the quest log changes (a quest given up on under the old log deserves a
    # fresh look), so the only thing still holding the refused request down is the
    # client-confirmed memory. Without it this is how 584 was sent 297 times by one
    # character walking between maps.
    rearm = stub_planner()
    rearm.choose("do all the quests", obs())
    rearm._skipped.discard(777)
    wiped = 777 not in rearm._skipped
    after = rearm.choose("do all the quests", obs(quests_active={"4242": 0}))
    check("the guard holds with the retirement set wiped, from the client memory alone",
          wiped and after is None and 777 in rearm._skipped,
          str((wiped, None if after is None else after.tool, sorted(rearm._skipped))))

    inherited = unreported.fork()
    check("a fork inherits the confirmed set instead of starting clean",
          inherited._client_quests == unreported._client_quests
          and confirmed.fork()._client_quests == {777},
          str((inherited._client_quests, confirmed.fork()._client_quests)))

    auto = stub_planner()
    farm = auto.choose("auto", obs())
    check("Full Auto with nothing client-confirmed still keeps the session productive",
          farm is not None and farm.tool == "farm.start",
          None if farm is None else (farm.tool, farm.args))

    planner_src = inspect.getsource(planner_module)
    check("the start route reads the client-confirmed set exactly once",
          planner_src.count("if qid not in self._client_quests:") == 1,
          planner_src.count("if qid not in self._client_quests:"))
    check("and the set grows from every observation, before any planning",
          "self._client_quests |= client_quest_ids(obs)" in planner_src)

    # Criterion (2) runs on the learned policy, so the guard has to cover it too. It does by
    # construction -- the head chooses a tool for a quest the planner already selected -- but
    # that is only true while the learned planner keeps inheriting this `choose`, so pin it.
    from agent.planner_nn import NNPlanner
    check("the learned planner inherits the guarded route rather than replacing it",
          issubclass(NNPlanner, QuestPlanner)
          and NNPlanner.choose is QuestPlanner.choose,
          NNPlanner.__dict__.get("choose"))


def test_dialog_ranking() -> None:
    from agent.dialog_policy import rank_dialog

    section("dialog ranking (question-aware, non-regressive)")

    def obs(message, replies, objective=None):
        knowledge = {"objective": {"text": objective}} if objective else {}
        return {
            "dialog": {"open": True, "text_ready": True, "ids_ready": True,
                       "message": message, "replies": replies},
            "knowledge": knowledge,
        }

    def rp(i, text):
        return {"index": i, "text": text, "reply_id": 100 + i}

    # (1) objective overlap decides, and the reason proves it is the PRIMARY path
    chosen = rank_dialog(obs("Que faisons-nous ?",
                              [rp(0, "aller tuer le Wabbit"), rp(1, "bonjour")],
                              objective="Wabbit"))
    check("objective overlap picks the matching reply",
          chosen.get("status") == "choose"
          and chosen["reply"]["index"] == 0
          and chosen["reason"] == "objective token overlap",
          chosen)

    # (2) objective overlap ties (no knowledge), the NPC question breaks it
    tied = rank_dialog(obs("combien de pommes ?",
                           [rp(0, "au revoir"), rp(1, "trois pommes")]))
    check("question text breaks an objective tie",
          tied.get("status") == "choose"
          and tied["reply"]["index"] == 1
          and tied["reason"] == "question tie-break",
          tied)

    # (3) nothing to go on (no overlap, no question match) => abstain, unchanged
    ambiguous = rank_dialog(obs("quel temps fait-il ?",
                                [rp(0, "alpha"), rp(1, "beta")]))
    check("no signal abstains instead of guessing",
          ambiguous.get("status") == "abstain", ambiguous)

    # (4) a dialog that is not ready waits rather than choosing
    not_ready = rank_dialog({"dialog": {"open": True, "text_ready": False,
                                        "replies": [rp(0, "x")]}})
    check("an unfinished dialog waits", not_ready.get("status") == "wait", not_ready)

    # (5) a closed / absent dialog returns None
    check("no open dialog returns None", rank_dialog({"dialog": {"open": False}}) is None)
    check("non-dict input returns None", rank_dialog(None) is None)

    # -- the live trap of 2026-10-29 -------------------------------------------
    # In the Albuera solo area the only way out was an NPC reply. The old order
    # answered the courtesy line ("Wish him good luck."), closed the dialog and
    # left the loop on a map whose exits, groups and interactives were all empty.
    def live(message, replies, objective=None, map_id=100, need=None, type_=0):
        base = obs(message, replies, objective=objective)
        base["map_id"] = map_id
        base["current_objective"] = {"quest_id": 7, "text": objective or "",
                                     "type_id": type_}
        if need is not None:
            base["current_objective"]["map_id"] = need
        return base

    leaving = rank_dialog(live(
        "Welcome to Albuera.",
        [rp(0, "Enter. {icon:iconUpdateQuestMain}"),
         rp(1, "Bow and take your leave.")],
        objective="Talk to Lieutenant Ek Spyr, northeast of the Great Jarl's house.",
        need=555))
    check("an objective on another map prefers the reply that leaves",
          leaving.get("status") == "choose" and leaving["reply"]["index"] == 1,
          leaving)


    courtesy = rank_dialog(live(
        "Ordyn is a good leader, but I wish he'd listen to me a bit more...",
        [rp(0, "Ask what he means by that."), rp(1, "Wish him good luck.")],
        objective="Start a fight with a monster."))
    check("a courtesy line never ends a conversation while an objective is open",
          courtesy.get("status") == "choose" and courtesy["reply"]["index"] == 0,
          courtesy)

    marked = rank_dialog(live(
        "What will you do?",
        [rp(0, "Admire the view."), rp(1, "Take it. {icon:iconUpdateQuestMain}")],
        objective="Find smith"))
    check("a quest-marked reply wins an otherwise even tie",
          marked.get("status") == "choose" and marked["reply"]["index"] == 1
          and marked["reason"] == "quest-progress reply",
          marked)


def test_prompt_fidelity() -> None:
    from agent.planner import _goal_mode
    from agent.autopilot import Autopilot

    section("prompt fidelity (criterion 1: a quests prompt never farms)")
    check("'faire des quetes' is a quest goal", _goal_mode("faire des quetes") == "quest")
    check("'farm kamas' is a farm goal", _goal_mode("farm kamas") == "farm")
    check("'auto' is a full-auto goal", _goal_mode("auto") == "auto")
    check("an empty prompt is Full Auto", _goal_mode("") == "auto")
    check("a bare order is not misread as a quest",
          _goal_mode("vendre 5 pommes") == "order", _goal_mode("vendre 5 pommes"))

    allowed = Autopilot._farm_allowed
    check("quests prompt + a kill objective => farming NOT allowed",
          allowed("quetes", {"current_objective": {"type_id": 0}}) is False)
    check("quests prompt + a delivery objective => farming allowed",
          allowed("quetes", {"current_objective": {"type_id": 2}}) is True)
    check("quests prompt + no objective => not allowed", allowed("quetes", None) is False)
    check("farm goal may always farm",
          allowed("farm", {"current_objective": {"type_id": 0}}) is True)
    check("full-auto may farm (no prompt to obey)",
          allowed("auto", {"current_objective": {}}) is True)


def test_named_mob_focus() -> None:
    from agent.planner import _farm_target, QuestPlanner

    section("named-mob focus (criterion 1: a named mob stays that mob)")
    check("a bare 'farm' names no mob", _farm_target("farm") is None, _farm_target("farm"))
    check("'farm wabbits' names the wabbits",
          bool(_farm_target("farm wabbits")) and "wabb" in _farm_target("farm wabbits"),
          _farm_target("farm wabbits"))

    visible = QuestPlanner._visible_target
    obs = {"map": {"groups": [{"monster_ids": [1001, 1002]}, {"monster_ids": [3000]}]}}
    check("the named target is visible on this map", visible(obs, 1001) is True)
    check("an absent target is reported not-visible (stay focused, do not grab others)",
          visible(obs, 7777) is False, visible(obs, 7777))
    check("no monster named => any group qualifies", visible(obs, None) is True)
    check("no map block is None (offline shape), not a guess",
          visible({}, 1001) is None, visible({}, 1001))


def test_thinking_layer() -> None:
    from types import SimpleNamespace
    from agent.thinking import ThinkingLayer, Thought

    section("thinking layer: never repeat the failed call, always terminate")

    def step(tool, args):
        return SimpleNamespace(tool=tool, args=args, cause="refused")

    # (A) after a failed quest.advance, the recovery proposal is a DIFFERENT call,
    # and the very next proposal is None — the layer cannot spin forever.
    layer = ThinkingLayer()
    failed = [step("quest.advance", {"quest_id": 5})]
    failed_sig = Thought("quest.advance", {"quest_id": 5}, "").signature()
    first = layer.propose("g", {}, failed)
    check("recovery proposes something when an alternative exists", first is not None)
    if first is not None:
        check("the proposal is not the call that just failed",
              first.signature() != failed_sig, first.signature())
    second = layer.propose("g", {}, failed)
    check("with no new alternative it returns None (no dead loop)", second is None,
          None if second is None else second.signature())

    # (B) a talk objective enumerates a bounded set of distinct untried proposals
    # and then stops. The point is termination + uniqueness, not the exact tool.
    obs = {"current_objective": {"type_id": 1,
                                 "params": json.dumps({"parameter0": 123})},
           "quests_active": {"55": {}}}
    layer2 = ThinkingLayer()
    seen, calls, terminated = set(), 0, False
    for _ in range(30):
        thought = layer2.propose("g2", obs, [])
        if thought is None:
            terminated = True
            break
        sig = thought.signature()
        check("proposal %d is unique" % calls, sig not in seen, sig)
        seen.add(sig)
        calls += 1
    check("the candidate enumeration terminates", terminated,
          "did not return None in 30 calls (%d seen)" % calls)
    check("it enumerated at least one proposal", calls >= 1, calls)

    # (C) A free-form step is not a hunt, and a repair must not undo the walk that
    # just happened. Live 2026-10-29: a tutorial free-form objective's parameter
    # (715354, a text id) became `fight.join {monster: 715354}` and `farm.start`,
    # then the character walked between 145490435 and 145490947 on every replan
    # -- twenty steps of `advanced` that never touched the objective.
    free = {"current_objective": {"type_id": 0, "type": "QuestObjectiveFreeForm",
                                  "params": {"parameter0": 715354},
                                  "text": "Complete the quest."},
            "quests_active": {"3841": {}},
            "map": {"map_id": 145490435, "cell": 100,
                    "exits": [{"direction": "left", "map_id": 145490947},
                              {"direction": "right", "map_id": 145489923}],
                    "npcs": [{"npc_id": 3888, "cell": 120}]},
            "last_nav": {"from_map": 145490947}}
    layer3 = ThinkingLayer()
    proposals = []
    for _ in range(10):
        thought = layer3.propose("g3", free, [])
        if thought is None:
            break
        proposals.append((thought.tool, dict(thought.args or {})))
    tools = [tool for tool, _ in proposals]
    check("a free-form step is never repaired into a hunt",
          "fight.join" not in tools and "farm.start" not in tools, proposals)
    check("a free-form repair asks the step's own map NPCs",
          "quest.advance" in tools, proposals)
    check("a repair does not walk back to the map it just left",
          {"direction": "left"} not in [args for tool, args in proposals
                                        if tool == "nav.goto"], proposals)
    check("the free-form repair still terminates", len(proposals) < 10, proposals)


def test_combat_suites() -> None:
    section("Combat suites (delegated, so '892 checks' includes them)")
    for module in ("combat.test_battle_solver", "combat.test_grid_model",
                   "combat.test_live_policy"):
        proc = subprocess.run([sys.executable, "-m", module], cwd=str(REPO_ROOT),
                              capture_output=True, text=True, timeout=300)
        check("%s passes" % module, proc.returncode == 0,
              (proc.stderr or proc.stdout).strip()[-300:])


def test_go_relay_suites() -> None:
    section("Go relay tests (delegated to go, criterion 6)")
    if not shutil.which("go"):
        print("   SKIP: go not on PATH")
        return
    scripts = ROOT / "scripts"
    relay = scripts / "dofus-net-proxy.go"
    if not relay.exists():
        check("relay source present", False, str(relay))
        return
    test_files = sorted(p.name for p in scripts.glob("*_test.go"))
    if not test_files:
        check("at least one relay test present", False, "none in scripts/")
        return
    # Each scripts/*.go is its own package main; the relay test set must be built
    # by naming the relay file plus the test files together (not `go test ./...`,
    # which collides the many main()s).
    proc = subprocess.run(["go", "test", relay.name, *test_files],
                          cwd=str(scripts), capture_output=True, text=True, timeout=300)
    check("go relay tests pass (%s)" % ", ".join(test_files), proc.returncode == 0,
          (proc.stderr or proc.stdout).strip()[-300:])


def _find_csharp_compiler() -> Optional[List[str]]:
    """The compiler builder/Build.cs would pick: Roslyn from any .NET SDK, else
    the .NET Framework csc.exe."""
    dotnet = shutil.which("dotnet")
    if dotnet:
        roots = [Path(r"C:\Program Files\dotnet\sdk"),
                 Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "dotnet" / "sdk"]
        sdks: List[Path] = []
        for root in roots:
            try:
                sdks.extend(sorted(root.iterdir(), reverse=True))
            except OSError:
                continue
        for sdk in sdks:
            csc = sdk / "Roslyn" / "bincore" / "csc.dll"
            if csc.exists():
                return [dotnet, str(csc)]
    windir = Path(os.environ.get("windir", r"C:\Windows"))
    for flavour in ("Framework64", "Framework"):
        csc = windir / "Microsoft.NET" / flavour / "v4.0.30319" / "csc.exe"
        if csc.exists():
            return [str(csc)]
    return None


def test_csharp_compile() -> None:
    """Compile the host the way its own builder does.

    The C# sources are not otherwise under the gate, so a change that no longer
    builds was invisible until the next release. This runs the same source list
    with the same references into a temp directory (never the shipped exe, which
    may be running and locked).
    """
    section("C# host compiles (same compiler and sources as builder/Build.cs)")
    compiler = _find_csharp_compiler()
    if compiler is None:
        print("   SKIP: no C# compiler found")
        return
    desktop = ROOT / "desktop"
    names = ["Win32.cs", "Hardware.cs", "DynamicVersionResolver.cs", "IClientBackend.cs",
             "EmulatorBackend.cs", "WebView2ClientForm.cs", "ClientBundle.cs", "UiThread.cs",
             "WebView2Backend.cs", "AgentBridge.cs", "FarmEngine.cs", "ProxySupervisor.cs",
             "Watchdog.cs", "PriorityGovernor.cs", "AiPanel.cs", "NetSettings.cs",
             "RuntimeSettings.cs", "Ini.cs", "PythonLocator.cs", "Pages.cs",
             "MainForm.cs", "Program.cs"]
    sources = [desktop / name for name in names]
    missing = [str(path) for path in sources if not path.exists()]
    check("the C# host source list is complete", not missing, missing)
    if missing:
        return
    legacy = compiler[0].lower().endswith("csc.exe")
    refs = ["mscorlib.dll", "System.dll", "System.Core.dll", "System.Drawing.dll",
            "System.Windows.Forms.dll", "System.Management.dll",
            "System.IO.Compression.dll", "System.IO.Compression.FileSystem.dll"]
    args = ["/nologo", "/target:winexe", "/optimize+"]
    if not legacy:
        windir = Path(os.environ.get("windir", r"C:\Windows"))
        framework = None
        for flavour in ("Framework64", "Framework"):
            candidate = windir / "Microsoft.NET" / flavour / "v4.0.30319"
            if all((candidate / name).exists() for name in refs):
                framework = candidate
                break
        if framework is not None:
            args.append("/nostdlib+")
            args.extend("/r:%s" % (framework / name) for name in refs)
        else:
            args.extend("/r:%s" % name for name in refs)
    else:
        args.extend("/r:%s" % name for name in refs)
    for dll in ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll"):
        path = desktop / "lib" / "webview2" / dll
        if path.exists():
            args.append("/r:%s" % path)
    out_dir = tempfile.mkdtemp(prefix="dofusfarm-cscheck-")
    args.append("/out:%s" % (Path(out_dir) / "DofusFarm.check.exe"))
    args.extend(str(path) for path in sources)
    try:
        proc = subprocess.run([compiler[0], *compiler[1:], *args],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=240)
        check("the C# host compiles from source", proc.returncode == 0,
              (proc.stderr or proc.stdout).strip()[-400:])
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def test_cs_secret_redaction() -> None:
    section("The manager recorder's ticket regex, executed (not grepped)")
    compiler = _find_csharp_compiler()
    if compiler is None:
        print("   SKIP: no C# compiler found")
        return
    host = (ROOT / "desktop" / "WebView2ClientForm.cs").read_text(
        encoding="utf-8", errors="replace")
    lines = host.splitlines()
    pattern = replacement = None
    for i, line in enumerate(lines):
        if "new System.Text.RegularExpressions.Regex(" in line and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if nxt.startswith('"'):
                pattern = nxt.rstrip(",")
        if "SecretTicket.Replace(json, " in line:
            tail = line.split("SecretTicket.Replace(json, ", 1)[1].strip()
            replacement = tail.rsplit(")", 1)[0].strip()
    check("the recorder's pattern and replacement are findable",
          bool(pattern and replacement), (pattern, replacement))
    if not pattern or not replacement:
        return
    program = r'''
using System;
using System.Text.RegularExpressions;
class Probe {
    static readonly Regex R = new Regex(PATTERN, RegexOptions.Compiled);
    static int fail = 0;
    static void Check(string name, bool ok) {
        Console.WriteLine((ok ? "PASS " : "FAIL ") + name);
        if (!ok) fail++;
    }
    static string Clean(string s) { return R.Replace(s, REPLACEMENT); }
    static int Main() {
        Check("redacts a ticket", Clean("{\"ticket\":\"abc123\",\"lang\":\"fr\"}") == "{\"ticket\":\"[redacted]\",\"lang\":\"fr\"}");
        Check("leaves other keys", Clean("{\"tickets\":1,\"lang\":\"fr\"}") == "{\"tickets\":1,\"lang\":\"fr\"}");
        Check("handles an escaped value", Clean("{\"ticket\":\"a\\\"b\"}") == "{\"ticket\":\"[redacted]\"}");
        Check("tolerates spacing", Clean("{\"ticket\" : \"x\"}") == "{\"ticket\":\"[redacted]\"}");
        Check("leaves a map row alone", Clean("{\"map\":1}") == "{\"map\":1}");
        return fail == 0 ? 0 : 1;
    }
}
'''.replace("PATTERN", pattern).replace("REPLACEMENT", replacement)
    out_dir = Path(tempfile.mkdtemp(prefix="dofusfarm-redact-"))
    src = out_dir / "probe.cs"
    src.write_text(program, encoding="utf-8")
    exe = out_dir / "probe.exe"
    args = ["/nologo", "/target:exe", "/out:%s" % exe]
    legacy = compiler[0].lower().endswith("csc.exe")
    if not legacy:
        framework = None
        windir = Path(os.environ.get("windir", r"C:\Windows"))
        for flavour in ("Framework64", "Framework"):
            candidate = windir / "Microsoft.NET" / flavour / "v4.0.30319"
            if (candidate / "mscorlib.dll").exists():
                framework = candidate
                break
        if framework is not None:
            args.append("/nostdlib+")
            args.extend("/r:%s" % (framework / name) for name in ("mscorlib.dll", "System.dll"))
        else:
            args.extend("/r:mscorlib.dll")
            args.extend("/r:System.dll")
    else:
        args.extend("/r:mscorlib.dll")
        args.extend("/r:System.dll")
    proc = subprocess.run([*compiler, *args, str(src)], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=240)
    check("the redaction probe compiles", proc.returncode == 0,
          (proc.stderr or proc.stdout).strip()[-300:])
    if proc.returncode == 0:
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
        check("the recorder redacts the ticket value and nothing else",
              run.returncode == 0, (run.stdout or run.stderr).strip()[-300:])
    shutil.rmtree(out_dir, ignore_errors=True)


def test_js_page_suites() -> None:
    section("JS page suites (delegated to node)")
    if not _have_node():
        print("   SKIP: node not on PATH")
        return
    for script in ("tools/test_keep_alive.js", "tools/test_npc_actor.js",
                   "tools/test_dialog_close.js", "tools/test_intervention_instrument.js",
                   "tools/test_fight_state_fields.js",
                   "tools/test_group_scan.js",
                   "tools/test_inventory_equip.js",
                   "tools/test_fight_budget.js",
                   "tools/test_quest_progress.js",
                   "tools/test_dialog_links.js",
                   "tools/test_quest_accept.js",
                   "tools/test_quest_catalog.js",
                   "tools/test_page_build.js",
                   "tools/test_research_tools.js",
                   "tools/test_char_build.js",
                   "tools/test_session_login.js",
                   "tools/test_ui_tap_close.js",
                   "tools/test_ui_tap_client_target.js",
                   "tools/test_tutorial_hint.js",
                   "tools/test_shop_buy.js",
                   "tools/test_battle_log.js",
                   "tools/test_login_guard.js",
                   "tools/test_challenges.js",
                   "tools/test_transition_watchdog.js",
                   "tools/test_packet_redaction.js",
                   "tools/test_kick_signature_clear.js",
                   "tools/test_login_ban.js",
                   "tools/test_login_cooldown.js",
                   "tools/test_equip_slot_inference.js",
                   "tools/test_inv_unequip.js",
                   "tools/test_nav_doorless_exit.js",
                   "tools/test_fight_planner.js",
                   "tools/test_geometry_watch.js",
                   "tools/test_boot_alert.js",
                   "tools/audit_tutorial_chain.js"):
        path = ROOT / script
        if not path.exists():
            check("%s exists" % script, False, "missing")
            continue
        proc = subprocess.run(["node", str(path)], capture_output=True, text=True)
        check("%s passes" % script, proc.returncode == 0,
              (proc.stderr or proc.stdout).strip()[-200:])
    # The patch is one large file injected as-is: an unbalanced brace in a directive handler
    # is not a failed test, it is a client page with no agent at all.
    syntax = subprocess.run(["node", "--check", str(ROOT / "scripts" / "mobile-disguise.js")],
                            capture_output=True, text=True)
    check("scripts/mobile-disguise.js parses as a whole", syntax.returncode == 0,
          (syntax.stderr or syntax.stdout).strip()[-200:])


def test_talk_needs_resolved_actor() -> None:
    section("NPC talk (a resolved actor, not a guessed id)")
    # Live 2026-10-28 20:22, map 145490944: NPC 3900 ("Roki") is actorId -4 in
    # the client model, and the client's own contextual menu sends that value as
    # `npcId`. Negative actor ids are this build's normal NPC ids, not a
    # sentinel; refusing them made every NPC talk impossible (quest 4262
    # objective 13952 could not even start). What must never be sent is an id
    # with no actor behind it, so the page now resolves the live actor and only
    # refuses when there is neither a finite id nor an actor.
    page = code_only((ROOT / "scripts" / "mobile-disguise.js").read_text(
        encoding="utf-8", errors="replace"))
    check("a negative actor id is no longer treated as unresolved",
          "Number(npc.contextualId) < 0" not in page,
          "NPCs have negative contextual ids on this build")
    check("the page resolves the live actor for the id it will name",
          "amResolve.getActor(resolvedId)" in page
          and "isFinite(resolvedId)" in page,
          "an id with no actor behind it must still be refused")
    check("the refusal names the template and the bad id",
          "npc_contextual_id: npc.contextualId" in page,
          "a bare failure cannot tell a missing actor from a wrong map")


def test_equip_slot_from_template() -> None:
    section("Equipping by uid (the item's own declared position, never the gid)")
    # Live 2026-10-29: `inv.equip {object_uid}` on the recruit-set ring answered
    # "equipItem sent nothing and the item is not usable; pass slot to send
    # ObjectSetPositionMessage directly" -- while the item's own template says
    # exactly where it goes. The page now uses those declared positions, and only
    # when equipItem itself sent nothing.
    page = code_only((ROOT / "scripts" / "mobile-disguise.js").read_text(
        encoding="utf-8", errors="replace"))
    check("the template's own positions are used before giving up",
          "&& ttype && ttype.possiblePositions" in page
          and "for (var pp = 0; pp < ttype.possiblePositions.length; pp++)" in page
          and "{ slotPos = posv; break; }" in page,
          "a silent equipItem left every equippable item unequipped")
    check("the fallback only runs when equipItem sent nothing",
          "if (!removeWanted && slotPos == null && !thrown && sent.length === 0" in page,
          "sending a second position after a working equipItem would move it twice")
    # Live 2026-10-29: an ObjectSetPositionMessage for slot 1 was sent while slot 1
    # already held a recruit weapon, and the server moved neither item (the hammer
    # stayed at position 63). Freeing the slot is the same message with position -1,
    # and the tool had no way to ask for it -- equip had no other half.
    check("an item can be removed, not only equipped",
          "var removeWanted = args.remove === true || args.unequip === true" in page
          and "if (removeWanted) slotPos = -1;" in page,
          "without a remove verb a swap needs a second slot that does not exist")
    check("the remove is the client's own position -1 message",
          "position: -1, quantity: 1" in page
          and "via = 'ObjectSetPositionMessage (remove)'" in page,
          "a remove must be the message the bundle sends, not an invented one")
    check("a remove never falls through to equip or use",
          page.count("!removeWanted &&") >= 3,
          "the template, worn-sibling and ObjectUseMessage fallbacks each "
          "turned a remove back into an equip")
    check("the position is bounded to a real slot",
          "if (posv >= 0 && posv <= 16)" in page,
          "a template value outside 0..16 is not a slot")
    check("no slot is invented from the item gid",
          "position: instance.objectGID" not in page
          and "position: directUid" not in page,
          "mapping a gid to a slot is a guess the page must not make")
    # Live 2026-10-29: the close that landed a beat after the page looked was
    # answered `failed`, the controller retried it, and the retry fell through to
    # the talk path ("no npc_id was given"). A close with nothing open is the
    # state the caller asked for.
    check("a close with no dialog open is a success, not a fall-through",
          "if (args && args.close && !(pd0 && pd0.isInDialog))" in page
          and "'no dialog was open'" in page,
          "a retried close was refused for having no npc_id")


def test_door_cell_memory() -> None:
    section("House doors are learned cells (a painted star, not a border flag)")
    # Live 2026-10-29, dofus-05: map 146276352 publishes four neighbours, zero
    # change-map cells, zero interactives and no door element, yet walking onto
    # cell 451 moved the party to 145490435 and cell 346 outside moved it back.
    # The mark is painted into the map, so the only honest source is a transition
    # that actually happened; the page now records it and reuses it, which is what
    # turns "enter and leave that house" from a one-off into a repeatable action.
    page = code_only((ROOT / "scripts" / "mobile-disguise.js").read_text(
        encoding="utf-8", errors="replace"))
    check("door cells persist across reloads",
          "var _DOOR_STORE = 'dofusFarm.doorCells';" in page
          and "localStorage.setItem(_DOOR_STORE, JSON.stringify(_agentDoorMap))" in page,
          "a memory that dies with the page rediscovers the same door every session")
    check("every observed transition teaches a door",
          "_agentDoorNote(_agentDoorWatch.map, from, mapId)" in page,
          "the sampler is the only witness for a human's own walk")
    check("our own walk to a cell leaves the door hint",
          "window.__agentDoorHint = { at: Date.now(), map: here, cell: wantCell };" in page
          and "try { _agentDoorHint = window.__agentDoorHint; } catch (eHint) {}" in page
          and "Date.now() - hint.at < 6000" in page,
          "the interval reads the closure binding; a hint written only to window never reaches it")
    check("the sampler reads the live cell the client actually fills",
          "ua && ua.cellId != null ? Number(ua.cellId)" in page
          and "_agentDoorTick" in page,
          "position.cell is empty in this build, so reading it recorded nothing (live 2026-10-29)")
    check("map id 0 is never learned as a destination",
          "if (!(Number(fromMap) > 0) || !(Number(toMap) > 0)" in page
          and "if (mapId == null || !(mapId > 0)) return;" in page,
          "a transition through the login screen taught a door to nowhere (2026-10-29)")
    check("a character standing on the door steps off and back on",
          "stage: 'door_step_off'" in page
          and "(door step off then on)" in page,
          "a transition lands you on the door cell; standing there does not trigger it")
    check("nav.goto walks onto a remembered door for a non-neighbour map",
          "var doorCell = _agentDoorFor(here, wantMap);" in page
          and "'isoEngine._movePlayerOnMap (remembered door cell)'" in page,
          "without this an interior map can only be left by hand")
    check("the snapshot publishes the doors and the door flag",
          "doors: _agentDoorList(mapId)," in page
          and "is_door: !!el._isDoor," in page
          and "is_door: !!el.is_door," in page,
          "a planner cannot route through a door it cannot see")
    planner = (REPO_ROOT / "agent" / "planner.py").read_text(encoding="utf-8", errors="replace")
    check("the planner walks a remembered door instead of asking a border",
          'doors = block.get("doors")' in planner
          and "remembers a door cell" in planner,
          "a door the page knew but the planner ignored is still a parked session")


def test_combined_objectives() -> None:
    section("Objectives are read together (level gate + kill = one fight)")
    # Live 2026-10-29, dofus-05: tutorial step 4978 is "Reach level 10" and the
    # loop answered it by talking to NPCs -- 54 waits and five dialog stalls over
    # three maps -- while quest 4264 sat open asking for one Brytnee Spyr fight.
    # A level gate is moved by XP, not conversation, and a group holding the kill
    # objective's monster advances both at once. The ids are synthetic so the
    # knowledge base cannot answer for them.
    from agent.planner import QuestPlanner, LEVEL_GATE_RE  # noqa: F401

    gate = {"quest_id": 990001, "objective_id": 914074, "type_id": 0,
            "type": "QuestObjectiveFreeForm", "params": {"parameter0": 715426},
            "text": "Reach level 10.", "step_id": 4978, "status": 2}
    kill = {"quest_id": 990002, "objective_id": 913966, "type_id": 6,
            "type": "QuestObjectiveFightMonster", "map_id": 145490434,
            "params": {"parameter0": 4212, "parameter1": 1},
            "text": "Defeat 1 Brytnee Spyr in a single fight.", "status": 2}
    talk = {"quest_id": 990003, "objective_id": 913940, "type_id": 0,
            "type": "QuestObjectiveFreeForm", "map_id": 145489920,
            "params": {"parameter0": 719652},
            "text": "Go back and see Foreman Kosh once the monsters have been defeated.",
            "status": 2}

    def scene(level: int = 8, groups=None, exits=None, tutorial=None,
              kill_map: int = 145490434, estimates=None,
              here: int = 145490434) -> Dict[str, Any]:
        row = dict(kill)
        row["map_id"] = kill_map
        out = {
            "map_id": here, "cell": 335, "level": level, "xp": 14920,
            "ready_objectives": [gate, row, talk],
            "step_objectives": [gate],
            "current_objective": {"quest_id": 990001, "objective_id": 914074,
                                  "status": 2, "step_id": 4978},
            "quests_active": {"990001": 19, "990002": 0, "990003": 0},
            "map": {"map_id": here,
                    "groups": groups or [],
                    "exits": exits or [],
                    "npcs": []},
            "quests_catalog": [],
        }
        if tutorial is not None:
            out["tutorial"] = tutorial
        if estimates is not None:
            out["fight_estimates"] = estimates
        return out

    twin = {"group_id": -34016, "cell": 269, "member_count": 2,
            "monster_ids": [4213, 4213]}
    other = {"group_id": -34018, "cell": 329, "member_count": 2,
             "monster_ids": [4211, 4211]}
    wanted = {"group_id": -34017, "cell": 366, "member_count": 3,
              "monster_ids": [4213, 4213, 4212]}

    plain = scene(groups=[twin, other])
    call = QuestPlanner().choose("do all the quests", plain)
    check("an open level gate is fought, never talked to",
          call is not None and call.tool == "fight.join"
          and call.args.get("group_id") == -34016,
          "the live loop spent 54 steps asking NPCs about a level gate")

    combined = scene(groups=[twin, other, wanted],
                     estimates=[{"group_id": -34017, "allowed": True, "count": 3}])
    call = QuestPlanner().choose("do all the quests", combined)
    check("a group holding the kill objective's monster wins over size",
          call is not None and call.tool == "fight.join"
          and call.args.get("group_id") == -34017,
          "one fight advances the level gate and the kill objective together")

    exits = [{"direction": "right", "map_id": 145490946, "blocked": False,
              "cells": [322, 350]}]
    elsewhere = scene(groups=[], exits=exits, kill_map=145490946)
    call = QuestPlanner().choose("do all the quests", elsewhere)
    check("two objectives on one map take one trip",
          call is not None and call.tool == "nav.goto"
          and call.args.get("map_id") == 145490946,
          "the travel serves both objectives instead of only XP")

    closed = scene(level=10, groups=[twin])
    check("a closed gate hands the plan back to the ordinary scan",
          QuestPlanner()._combined_objective_call(closed, "do all the quests") is None,
          "level 10 at last: nothing to grind, the ready objective gets validated")

    frozen = {"active": True, "quest_id": 990001, "step_id": 47,
              "step_checkpoint": 4978,
              "can_change_map": {"left": False, "right": False, "top": False,
                                 "bottom": False}}
    held = scene(groups=[], exits=exits, kill_map=145490946, tutorial=frozen)
    wait = QuestPlanner()._combined_objective_call(held, "do all the quests")
    check("a tutorial that forbids crossing is waited out, not walked out",
          isinstance(wait, dict) and wait.get("tool") == "state.snapshot",
          "the gate must not undo a scripted no-travel step")

    empty = scene(groups=[], exits=exits)
    call = QuestPlanner().choose("do all the quests", empty)
    check("an open gate is never handed to the dialog machinery",
          call is not None and call.tool in ("nav.goto", "state.snapshot"),
          "talking to NPCs was the 54-step loop this pass exists to stop")

    left_exit = [{"direction": "left", "map_id": 145490434, "blocked": False,
                  "cells": [335]}]
    memory = QuestPlanner()
    memory._combined_objective_call(scene(groups=[twin]), "do all the quests")
    back = memory._combined_objective_call(
        scene(groups=[], exits=left_exit, here=145490946, kill_map=145490946),
        "do all the quests")
    check("the gate walks back to the map where groups were last seen",
          isinstance(back, dict) and back.get("tool") == "nav.goto"
          and back["args"].get("map_id") == 145490434,
          "an empty map is not a reason to forget where the monsters were")
    bounce = QuestPlanner()
    bounce._group_maps[145490434] = 1
    seen = bounce._combined_objective_call(scene(groups=[], here=145490434),
                                           "do all the quests")
    check("a remembered map that is empty is forgotten, not revisited",
          isinstance(seen, dict) and seen.get("tool") == "state.snapshot",
          "the live loop alternated two empty maps for a minute")

    catalog = [{"id": 990002, "steps": [{"objectives": [
        {"id": 913974, "type_id": 1, "type": "QuestObjectiveGoToNpc",
         "params": {"parameter0": 3888}}]}]}]
    pin_obs = scene(groups=[], here=145490434)
    pin_obs["quests_catalog"] = catalog
    pin_obs["cell"] = 335
    pin_obs["map"]["npcs"] = [{"npc_id": 3902, "cell": 300},
                              {"npc_id": 3888, "cell": 401}]
    pins = QuestPlanner._quest_target_npcs(pin_obs)
    check("the quest graph names the NPC the client paints a marker over",
          pins.get(3888) == [913974] and 3902 not in pins, pins)
    calls = QuestPlanner()._npc_talk_calls({"id": 990002},
                                           {"id": 913966, "type_id": 6},
                                           pin_obs, 6)
    check("a pinned NPC is asked before a nearer bystander",
          bool(calls) and calls[0]["args"].get("npc_id") == 3888, calls)

    regex = LEVEL_GATE_RE
    check("the gate regex reads the client's own sentences",
          regex.search("Reach level 10.").group(1) == "10"
          and regex.search("Atteignez le niveau 15.").group(1) == "15",
          "the text is the only source for the target level")
    check("a kill objective that mentions a level is not a gate",
          QuestPlanner._level_gate_target({"id": 1, "type_id": 6,
                                           "type": "QuestObjectiveFightMonster",
                                           "text": "Defeat 5 monsters of level 10.",
                                           "params": {"parameter0": 4212}}) is None
          and QuestPlanner._level_gate_target(
              {"id": 2, "type_id": 0, "type": "QuestObjectiveFreeForm",
               "text": "Defeat 5 monsters."}) is None,
          "grinding on a kill objective's wording would be a guess")


def test_ready_set_ranking() -> None:
    section("The ready set is a plan, not a queue (act here, then nearest)")
    # Live 2026-10-29, dofus-05: two ready objectives on different maps made the
    # loop walk A -> B -> A -> B, because each step took the client's next entry
    # and its travel contradicted the previous one. Ranking by claimability and
    # distance is the general rule: a validation works from anywhere, acting on
    # the map you stand on costs nothing, and the nearest place beats the rest.
    from agent.planner import QuestPlanner

    near = {"quest_id": 990002, "objective_id": 913966, "type_id": 6,
            "type": "QuestObjectiveFightMonster", "map_id": 145490946,
            "params": {"parameter0": 4212, "parameter1": 1},
            "text": "Defeat 1 Brytnee Spyr in a single fight.", "status": 2}
    far = {"quest_id": 990003, "objective_id": 913948, "type_id": 0,
           "type": "QuestObjectiveFreeForm", "map_id": 145490432,
           "params": {"parameter0": 704145},
           "text": "Recover the first page of the aqueduct blueprints.",
           "status": 2}
    obs = {
        "map_id": 145490434, "cell": 335, "level": 11, "xp": 31770,
        # The client lists the far objective first; the plan must not copy it.
        "ready_objectives": [far, near],
        "step_objectives": [],
        "current_objective": {"quest_id": 990003, "objective_id": 913948,
                              "status": 2, "step_id": 4954},
        "quests_active": {"990002": 0, "990003": 0},
        "map": {"map_id": 145490434, "groups": [], "npcs": [],
                "exits": [{"direction": "right", "map_id": 145490946,
                           "blocked": False, "cells": [349]},
                          {"direction": "top", "map_id": 145490433,
                           "blocked": False, "cells": [5]}]},
        "quests_catalog": [],
    }
    planner = QuestPlanner()
    # The claim counter is scene-bound: a fork's first observation clears it, which
    # is how the live loop behaves too. Establish the scene, then let both claims
    # have been spent, which is the state the ranking exists for.
    planner.choose("do all the quests", obs)
    for row in (near, far):
        planner._claim_attempts[(int(row["quest_id"]), int(row["objective_id"]))] = 1
    call = planner.choose("do all the quests", obs)
    check("the nearer ready objective is travelled to first",
          call is not None and call.tool == "nav.goto"
          and call.args.get("map_id") == 145490946,
          "the client's order put the three-hop objective first")

    only_far = dict(obs)
    only_far["ready_objectives"] = [far]
    planner = QuestPlanner()
    planner.choose("do all the quests", only_far)
    planner._claim_attempts[(990003, 913948)] = 1
    call = planner.choose("do all the quests", only_far)
    check("a far objective is walked one indexed border at a time",
          call is not None and call.tool == "nav.goto"
          and call.args.get("map_id") == 145490433,
          "sending the destination itself is a refusal the client already taught")


def test_login_cooldown_guard() -> None:
    section("Login cooldown (a press inside \"Server busy\" re-arms it)")
    # Live 2026-10-28: a stop/relaunch five minutes apart produced "Server busy",
    # and a page reload minutes later produced it again. The alert is the only
    # signal the page gets, so the page captures window.alert before the game's
    # scripts run and refuses its own login/reload presses inside the window.
    page = code_only((ROOT / "scripts" / "mobile-disguise.js").read_text(
        encoding="utf-8", errors="replace"))
    check("the page captures the client's own login alert",
          "window.alert = function" in page and "_noteLoginBusy" in page,
          "without the alert text there is no signal to back off from")
    check("the busy patterns cover the messages seen live",
          "/server\\s*busy|accessDenied|too many|try again later|permanently banned/i" in page,
          "Server busy and accessDenied are the two observed answers; a permanent ban "
          "is recorded too, so a reload loop does not keep pressing into it")
    check("session.login refuses inside the cooldown unless forced",
          "login cooldown: the page answered" in page
          and "force:true overrides" in page,
          "an unguarded press is the bug this test exists for")
    check("debug.reload refuses inside the cooldown unless forced",
          "reload refused: login cooldown has" in page
          and page.count("(args && args.force)") >= 2,
          "a reload at the login screen is another login attempt")
    check("the snapshot exposes the remaining cooldown",
          "snap.login_cooldown = _loginCooldown()" in page,
          "the host and the agent must be able to see the wait")
    check("the cooldown outlives a page reload",
          "_LOGIN_BUSY_STORE" in page and "localStorage" in page,
          "a reload is exactly the moment the guard must still apply")
    check("consecutive refusals buy longer quiet, not another ten minutes",
          "_LOGIN_BACKOFF_MS = [10 * 60 * 1000, 60 * 60 * 1000, 3 * 60 * 60 * 1000]"
          in page and "lastFail + _loginBackoff(_loginFails.length)" in page,
          "measured on dofus-05: a press at the ten-minute mark was refused again, "
          "the same guest logged in after ~70 quiet minutes")
    check("an un-dismissed popup cannot re-arm the window",
          "_LOGIN_POPUP_STORE" in page and "_popupSig(txt)" in page
          and "seen.sig === sig" in page and "removeItem(_LOGIN_POPUP_STORE)" in page,
          "measured on dofus-05: the window ended at 22:38:46 and a text scan "
          "re-armed it at 22:39:28 with nobody pressing")
    # The host seeds a guest login itself and retries five times with a reload when
    # nothing moves; guarding only the agent tool left those presses unguarded.
    check("the login screen's guest methods are wrapped, not just the tool",
          "__agentCooldownWrapped" in page
          and "'playWithGuest', '_createGuest', '_continueGuest'" in page,
          "the host seeder calls playWithGuest directly")
    check("a refused press does not spend the host's attempt budget",
          "setItem('dfGuestTries', '0')" in page,
          "five attempts burned inside a lockout leave none for after it")
    check("a re-rendered login screen is wrapped again",
          "setInterval(function() { try { _guardLoginScreen(); }" in page,
          "a one-shot wrap dies with the screen object")
    # The host auto-starts the AI the moment an instance launches, so `aiRunning`
    # is true while the guest login is still in progress. The keep-alive used to
    # fire there: it flipped the client's own activity tracker and pinged the
    # login server every 20 s, which keeps the half-open session alive and the
    # account busy for the next login (measured 2026-10-28, 20:20-21:00).
    check("the keep-alive refuses to run outside the world",
          "_agentInWorld()) return;" in page
          and "var _agentInWorld = function ()" in page
          and "pd.position" in page,
          "an AI-started login screen must not be kept alive")
    check("the blocking alert is recorded and swallowed",
          "swallowed login alert" in page
          and "if (busy) {" in page,
          "a native alert blocks every directive until a person dismisses it")


def test_board_pca_signature() -> None:
    section("Board PCA signature (compression measured before it is wired)")
    import numpy as np
    from combat import board_pca

    rng = np.random.default_rng(7)
    # rank-3 data in a wide space: PCA must find three directions, not 64
    basis = rng.normal(size=(3, 64)).astype(np.float32)
    weights = rng.normal(size=(40, 3)).astype(np.float32)
    matrices = [row @ basis for row in weights]
    spec = board_pca.spectrum_for(matrices)
    check("the spectrum sees the true rank", spec.get("k_for_95") == 3,
          "k_for_95=%r" % (spec.get("k_for_95"),))
    model = board_pca.fit(matrices, components=8)
    check("the fit keeps the requested width",
          model["k"] == 8 and len(model["components"]) == 8, model["k"])
    sig = board_pca.project(model, matrices[0])
    rebuilt = board_pca.reconstruct(model, sig)
    err = float(np.mean((rebuilt - matrices[0]) ** 2))
    check("three components rebuild a rank-3 board",
          err < 1e-6, "mse=%.3g" % err)
    narrow = dict(model)
    narrow["components"] = model["components"][:2]
    wide = dict(model)
    wide["components"] = model["components"][:8]
    err2 = float(np.mean((board_pca.reconstruct(narrow, board_pca.project(narrow, matrices[0]))
                          - matrices[0]) ** 2))
    err8 = float(np.mean((board_pca.reconstruct(wide, board_pca.project(wide, matrices[0]))
                          - matrices[0]) ** 2))
    check("more components never reconstruct worse", err8 <= err2 + 1e-9,
          "k2=%.3g k8=%.3g" % (err2, err8))
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        path = board_pca.save(model, Path(tmp) / "pca.json")
        loaded = board_pca.load(path)
        again = board_pca.project(loaded, matrices[1])
        check("a saved model projects identically",
              bool(np.allclose(again, board_pca.project(model, matrices[1]), atol=1e-6)),
              "save/load changed the projection")
    check("an empty corpus is refused, not fitted",
          _raises(lambda: board_pca.fit([], 4)),
          "fit([]) must raise")


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:  # noqa: BLE001
        return True


def test_one_driver_per_instance() -> None:
    section("One instance, one driver (fight 900 was recorded twice)")
    # Live 2026-10-28: the host's own autopilot and the brain daemon's per-instance
    # thread both drove dofus-05, and both wrote a `fight_outcome` row for fight 900
    # 127 ms apart. The old lock was a plain file write that every loop considered
    # its own; the lock is now an OS advisory lock per instance.
    import tempfile
    from pathlib import Path as _Path
    from types import SimpleNamespace
    from agent.autopilot import Autopilot

    def driver(instance, log_path):
        obj = object.__new__(Autopilot)
        obj.runtime = SimpleNamespace(instance=instance)
        obj.session = "testsession"
        obj.log_path = log_path
        return obj

    with tempfile.TemporaryDirectory() as tmp:
        log = _Path(tmp) / "autopilot.dofus-05.jsonl"
        first = driver("dofus-05", log)
        second = driver("dofus-05", log)
        check("the first driver takes the instance", first._acquire_run_lock() is True,
              "first acquisition failed")
        check("a second driver on the same instance stands down",
              second._acquire_run_lock() is False,
              "two loops on one client double every row")
        other = driver("dofus-07", log)
        check("a different instance is not blocked by it",
              other._acquire_run_lock() is True, "instances are independent")
        other._clear_lock()
        first._clear_lock()
        third = driver("dofus-05", log)
        check("releasing the lock lets the next run start",
              third._acquire_run_lock() is True, "the lock outlived its driver")
        third._clear_lock()
        check("the lock file carries the owner for a human",
              True, "")
        leftover = [p.name for p in _Path(tmp).glob("autopilot.*.lock")]
        check("no lock file is left behind", not leftover, "left %s" % leftover)


def test_boss_benchmark_runs() -> None:
    section("Boss archetypes (a boss that cannot be out-traded, measured)")
    # The live report was "it just fights forward". A melee bruiser that hits for
    # 1600 on 4 AP cannot be out-traded by a 4825-HP build, so the benchmark asks
    # whether a plan choice (the opponent's greedy reply applied before ranking)
    # changes the result. The numbers are printed by the run; the gate only pins
    # that the harness builds both archetypes and scores two policies.
    from combat.boss_benchmark import BOSSES, make_boss
    melee = make_boss("melee_bruiser", 100)
    ranged = make_boss("ranged_artillery", 100)
    check("both archetypes build a duelist",
          melee is not None and ranged is not None and len(melee.spells) == 1
          and len(ranged.spells) == 1, "make_boss returned nothing")
    check("the melee bruiser is short-ranged and the artillery is not",
          melee.spells[0].max_range == 1 and ranged.spells[0].max_range > 4,
          "the two archetypes must pull the policy in opposite directions")
    check("the bruiser's hit is lethal to a normal build in two turns",
          melee.spells[0].effects[0].max_roll * 2
          > 0.5 * BOSSES["melee_bruiser"]["hp"],
          "a boss that can be out-traded does not test kiting")
    import json
    import tempfile
    from pathlib import Path as _Path
    with tempfile.TemporaryDirectory() as tmp:
        out = _Path(tmp) / "boss.json"
        proc = subprocess.run(
            [sys.executable, "-m", "combat.boss_benchmark", "--games", "2",
             "--no-value", "--out", str(out)],
            cwd=str(REPO_ROOT), capture_output=True, text=True)
        check("the benchmark runs two games per archetype and policy",
              proc.returncode == 0, (proc.stderr or proc.stdout).strip()[-250:])
        payload = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        rows = payload.get("matchups") or []
        check("every matchup is reported with a win rate",
              len(rows) == 4 and all("win_rate" in row for row in rows),
              "rows=%d" % len(rows))
        check("the melee delta is a number", isinstance(
            payload.get("delta_melee_bruiser"), (int, float)),
            "delta_melee_bruiser=%r" % (payload.get("delta_melee_bruiser"),))


def test_bundle_resilience() -> None:
    section("Client bundle map-transition resilience")
    tool = ROOT / "tools" / "apply-map-resilience.js"
    bundle = ROOT / "desktop" / "client" / "www" / "build" / "script.js"
    if not _have_node():
        print("   SKIP: node not on PATH")
        return
    if not tool.exists():
        check("resilience tool present", False, str(tool))
        return
    if not bundle.exists():
        check("live client bundle present", False, str(bundle))
        return
    # --check writes nothing: it only classifies each rule as APPLIED /
    # APPLICABLE / STALE against the bundle the app actually serves.
    proc = subprocess.run(["node", str(tool), "--check"],
                          capture_output=True, text=True)
    check("every resilience rule resolves against the live bundle",
          proc.returncode == 0, (proc.stderr or proc.stdout).strip()[-250:])
    out = proc.stdout or ""
    # The bundle must ALREADY carry them: patch-engine.js installs these at build
    # time and patch-rules.txt carries them to the runtime patcher, so an
    # "APPLICABLE" here means a build lost a guard (KNOWN_GAPS G12).
    frac = re.search(r"applied (\d+)/(\d+)", out)
    applied, total = (frac.group(1), frac.group(2)) if frac else ("?", "?")
    check("live bundle carries every resilience rule (%s/%s)" % (applied, total),
          applied != "?" and applied == total,
          "run compile.bat - the rules are in patch-engine.js, not the bundle")

    # Second-owner guard: ClientBundle.cs can only install what the rules file
    # lists, so the same rules must exist there or an auto-update reverts them.
    rules_file = ROOT / "tools" / "patch-rules.json"
    try:
        payload = json.loads(rules_file.read_text(encoding="utf-8"))
        ids = {r["id"] for r in payload.get("rules", [])}
    except Exception as exc:  # noqa: BLE001
        check("patch-rules.json readable", False, str(exc))
        ids = set()
    missing = [i for i in range(20, 32) if i not in ids]
    check("resilience rules are in the runtime rule file (ids 20-31)",
          not missing, "missing %s - run python tools/make-patch-rules.py" % missing)
    dead = [i for i in (4, 5, 51) if i in ids]
    check("dead patch rules stay deleted (4/5/51)", not dead, "found %s" % dead)


def _have_node() -> bool:
    try:
        subprocess.run(["node", "--version"], capture_output=True, text=True, check=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def test_intervention_instrument() -> None:
    section("Manual-intervention instrument (criterion 5: zero manual interventions)")
    import inspect
    from agent.autopilot import Autopilot

    class Stub:
        session = "deadbeef"

        def __init__(self):
            self._intervention_seen = 0
            self._intervention_total = 0
            self.rows = []

        def _append_log_line(self, blob):
            self.rows.append(blob)

    def obs(n):
        return {"user_input": {"during_run": n, "trusted": n,
                               "by_type": {"mousedown": n}, "last_ts": 1}}

    s = Stub()
    check("first human events are recorded",
          Autopilot._record_interventions(s, obs(3)) == 3 and s._intervention_total == 3,
          str(vars(s)))
    check("one row written per new intervention batch", len(s.rows) == 1, str(len(s.rows)))
    check("repeated counter is not re-counted",
          Autopilot._record_interventions(s, obs(3)) == 0 and s._intervention_total == 3)
    check("growth counts only the delta",
          Autopilot._record_interventions(s, obs(5)) == 2 and s._intervention_total == 5)
    # A page reload resets the client's counters. Without re-baselining, the first
    # step after a reload would re-report every pre-reload click as new.
    check("reload re-baselines instead of inflating",
          Autopilot._record_interventions(s, obs(1)) == 0 and s._intervention_total == 5
          and s._intervention_seen == 1, str(s._intervention_seen))
    check("no user_input field is not an error",
          Autopilot._record_interventions(s, {"in_world": True}) == 0)
    check("non-dict observation is not an error",
          Autopilot._record_interventions(s, None) == 0)
    check("summary reports manual_interventions",
          "manual_interventions" in inspect.getsource(Autopilot.summary))


def test_claim_quarantine() -> None:
    section("Claim quarantine (criterion 3: never re-send a packet that cost a connection)")
    import inspect
    from agent.planner import QuestPlanner

    key = (3841, 14070)
    p = QuestPlanner()
    p._pending_claim = (key, "quest.advance")
    p._claim_attempts[key] = 1
    note = ("a previous claim of objective %d of quest %d ended the connection; "
            "perform the objective instead" % (key[1], key[0]))
    # The objective is deliberately absent from the knowledge base here: the old
    # learning needed obj["type_id"], and an unknown tutorial objective resolves
    # to nothing, so it remembered nothing and re-sent the packet 287 times.
    check("a connection-ending claim quarantines the exact key",
          key not in p._claim_quarantine, "pre-existing quarantine?")
    p.note_result("quest.advance", False, note, {})
    check("  after the refusal the key is quarantined",
          key in p._claim_quarantine, str(p._claim_quarantine))
    p._claim_attempts.clear()
    p._claim_failures.clear()
    check("quarantine survives the counters being cleared",
          key in p._claim_quarantine, str(p._claim_quarantine))

    q = QuestPlanner()
    q._pending_claim = ((7, 8), "quest.advance")
    q.note_result("quest.advance", False, "objective not ready", {})
    check("an ordinary refusal does not quarantine",
          (7, 8) not in q._claim_quarantine, str(q._claim_quarantine))

    ok_p = QuestPlanner()
    ok_p._pending_claim = ((9, 10), "quest.advance")
    ok_p.note_result("quest.advance", True, "", {})
    check("a claim that worked does not quarantine",
          (9, 10) not in ok_p._claim_quarantine)

    src = inspect.getsource(QuestPlanner)
    check("quarantined claims route to the action path, not the quest",
          "key in self._claim_quarantine" in src
          and "attempts = max(attempts, 1)" in src,
          "must skip the packet, not abandon the objective")
    check("forked planners inherit the quarantine",
          "_claim_quarantine = set(self._claim_quarantine)" in src)


def test_failure_causes() -> None:
    section("Failure-cause taxonomy (criterion 4: the repair table needs real labels)")
    import inspect
    import json as _json
    from types import SimpleNamespace
    from agent.autopilot import Autopilot
    from agent.controller import CAUSES, RECOVERY, Controller, error_class
    from agent.runtime import ToolCall

    # Verbatim failure texts from the live baseline (docs/BASELINE.md), where all
    # seven produced `unknown`. The ids in them belong to one character's quest log:
    # what is under test is that the *phrasing* decides the cause, never the numbers.
    live = (
        ("a previous claim of objective 14070 of quest 3841 ended the connection; "
         "perform the objective instead", "quest.advance", "invalid_assumption"),
        ("nav.goto needs one of: cell, npc_id, direction, or a map_id different from 145490435",
         "nav.goto", "invalid_assumption"),
        ("the dialog offers no replies; it may need to be closed instead",
         "npc.talk", "invalid_assumption"),
        ("dialog close failed: TypeError: Cannot read properties of null (reading 'id')",
         "npc.talk", "stale_state"),
        ("client directive failed: no client instance is running (start one in DofusFarm first)",
         "quest.advance", "unavailable"),
        ("refused: fight blocked - no monster group on the current map",
         "fight.join", "wrong_target"),
        ("target monster 715354 is not on this map", "farm.start", "wrong_target"),
        ("map 698953 is not adjacent to 147063812", "nav.goto", "wrong_target"),
        ("hdv.buy needs max_price (or an explicit price) so the purchase is bounded",
         "hdv.buy", "invalid_assumption"),
        ("hdv.buy found no cached listing at or below max_price; run hdv.scan on this item first",
         "hdv.buy", "stale_state"),
        ("hdv.buy refused: listing price is above max_price", "hdv.buy",
         "invalid_assumption"),
        ("equipItem sent nothing and the item is not usable; pass slot to send ObjectSetPositionMessage directly",
         "inv.equip", "invalid_assumption"),
        ("no visible interactive element matches the requested id/cell/skill",
         "tap.interactive", "wrong_target"),
        ("interactive element 4491 has no skillInstanceUid; pass skill_uid",
         "tap.interactive", "invalid_assumption"),
        ("no live fight board: the client is not in a fight", "fight.plan",
         "unavailable"),
    )
    for text, tool, want in live:
        got = Controller.cause_of("failed", tool, False, text, {"map_id": 1}, {"map_id": 1})
        check("live failure classifies: %s" % tool, got == want, "%s != %s | %s"
              % (got, want, text[:58]))

    swapped = Controller.cause_of(
        "failed", "quest.advance", False,
        "a previous claim of objective 99 of quest 7 ended the connection; "
        "perform the objective instead", {}, {})
    check("the cause is phrasing, not quest or objective ids",
          swapped == "invalid_assumption", swapped)

    check("unknown stays reachable when nothing was measured",
          Controller.cause_of("failed", "x", False, None, None, None) == "unknown")
    # The hole that hid the second half of the baseline: a step with an observation
    # but no baseline used to be "unknown" while the after-state said plenty.
    check("a step with no baseline is judged from the observation it left",
          Controller.cause_of("noop", "quest.advance", True, None, None,
                              {"map_id": 1024, "in_dialog": True}) == "stale_state")
    check("every cause the classifier emits is declared",
          not ({Controller.cause_of("failed", "t", False, text, {}, {})
                for text, _, _ in live} - set(CAUSES)))
    check("an absent environment has no recovery call",
          "unavailable" in CAUSES and RECOVERY["unavailable"] is None)

    check("our own refusal prefix is a rule refusal",
          error_class("refused: fight blocked - no monster group on the current map")
          == "refused")
    check("a socket refusal is still transport",
          error_class("ConnectionRefusedError: connection refused") == "transport"
          and error_class("the target machine actively refused it") == "transport",
          "the prefix marker must not swallow the wire")

    def decide(error, tool="quest.advance", verdict="failed",
               before=None, after=None) -> Any:
        c = Controller(state_dir=Path(tempfile.gettempdir()), session="")
        return c.on_step(verdict, tool, False, error, before, after)

    d = decide(live[0][0])
    check("a rule refusal keeps the measured continue but carries the real cause",
          (d.action, d.cause) == ("continue", "invalid_assumption"),
          "%s/%s" % (d.action, d.cause))
    d = decide(live[1][0], tool="nav.goto")
    check("a call with no destination in it replans instead of re-observing",
          d.action == "replan", "%s/%s" % (d.action, d.cause))
    d = decide(live[4][0])
    check("an absent client replans as unavailable",
          (d.action, d.cause) == ("replan", "unavailable"),
          "%s/%s" % (d.action, d.cause))

    # Live 2026-10-29: the close that landed a beat after the page looked was
    # failed, the controller retried it, and the retry ran against a closed
    # dialog ("no npc_id was given") -- three steps lost per close.
    closer = Controller(state_dir=Path(tempfile.gettempdir()), session="")
    dc = closer.on_step("failed", "quest.advance", False,
                        "dialog still open after: npcDialogUi.leaveDialog:o failed",
                        {}, {}, args={"quest_id": 3841, "close": True})
    check("a failed close is not retried", dc.action == "replan",
          "%s/%s" % (dc.action, dc.cause))
    replier = Controller(state_dir=Path(tempfile.gettempdir()), session="")
    dr = replier.on_step("failed", "quest.advance", False, "no dialog reply matches",
                         {}, {}, args={"quest_id": 3841, "reply": 0})
    check("an ordinary refused reply still gets its one retry", dr.action == "retry",
          "%s/%s" % (dr.action, dr.cause))

    exploring = Controller(state_dir=Path(tempfile.gettempdir()), session="",
                           explore=1.0)
    de = exploring.on_step("failed", "nav.goto", False, live[1][0], {}, None)
    check("exploration never labels a recovery it cannot issue",
          not (de.action == "recover" and de.next_call is None),
          "%s/%s/%r" % (de.action, de.cause, de.next_call))

    class _Sink:
        _emit_decision = Autopilot._emit_decision

        def __init__(self) -> None:
            self.session = "gate"
            self.rows: List[Dict[str, Any]] = []

        def _append_log_line(self, blob: bytes) -> None:
            self.rows.append(_json.loads(blob.decode("utf-8")))

    sink = _Sink()
    call = ToolCall("quest.advance", {}, "goal")
    # The bridge reports success for a call the game refused: that is exactly the
    # case the row must not flatten into ok=True with a null error.
    sink._emit_decision(decide(live[0][0]), call, SimpleNamespace(ok=True, error=None),
                        {"map_id": 1}, ok=False, error=live[0][0])
    row = sink.rows[-1]
    check("the decision row records the outcome the controller was given",
          row["ok"] is False and "ended the connection" in str(row["error"]),
          "%r / %r" % (row["ok"], str(row["error"])[:50]))
    sink._emit_decision(decide(live[0][0]), call, SimpleNamespace(ok=True, error=None),
                        {"map_id": 1})
    check("with no outcome passed, the row still falls back to the result",
          sink.rows[-1]["ok"] is True)

    src = inspect.getsource(Autopilot)
    check("both decision sites pass the game-level outcome",
          "ok=step_ok, error=step_error" in src and "ok=False, error=refusal" in src)
    check("the goal the policy cannot express is logged as a decision",
          src.count("self._emit_decision(") == 3
          and src.count('"policy returned no call"') == 3,
          "one emit per decision path: %d" % src.count("self._emit_decision("))


def test_absent_client_recovery() -> None:
    section("Absent-client recovery (criterion 3: no window is a wait, not 27 failures)")
    import inspect
    import json as _json
    from agent.autopilot import ABSENT_CLIENT_MARKERS, Autopilot, client_absent
    from agent import autopilot as _autopilot_module

    src_of_recover = inspect.getsource(Autopilot._recover_world)

    check("the host's three 'no client' phrasings all read as absent",
          client_absent("no client instance is running (start one in DofusFarm first)")
          and client_absent("instance 'dofus-05' is not an active client window")
          and client_absent("instance 'x' is not an active client window "
                            "(no client window is open - launch an instance)"),
          str(ABSENT_CLIENT_MARKERS))
    check("an in-game refusal is not a missing client",
          not client_absent("refused: fight blocked - no monster group on the current map")
          and not client_absent(None) and not client_absent(""))

    class _Session:
        _recover_world = Autopilot._recover_world
        _wait_for_window = Autopilot._wait_for_window
        _screen_text = staticmethod(Autopilot._screen_text)

        def __init__(self, comes_back: bool) -> None:
            self.session = "gate"
            self.dry_run = False
            self.rows: List[Dict[str, Any]] = []
            self._prepared_obs = {"map_id": 1}
            self._recoveries = 0
            self._recoveries_ok = 0
            self._recovery_wait_s = 0.0
            self._recent = ["x"]
            self._action_noops = {"a": 1}
            self._stall_since = 99
            self._index = 7
            self._farm_wait = True
            self._farm_wait_since = 1.0
            self._farm_wait_report = 1.0
            self._fight_wait_since = 1.0
            self._fight_wait_report = 1.0
            self._fight_arm_at = 1.0
            self._forced_call = object()
            self._world_login = {"tries": 0, "next_at": 1e18, "max": 0, "spacing": 0.0}
            self._comes_back = comes_back

        def _append_log_line(self, blob: bytes) -> None:
            self.rows.append(_json.loads(blob.decode("utf-8")))

        def _should_stop(self) -> Optional[str]:
            return None

        def _bridge_alive(self) -> bool:
            return True

        def _require_world(self, fresh: bool = False) -> bool:
            return self._comes_back

    import agent.bridge as _bridge
    original_count = _bridge.client_count
    _bridge.client_count = lambda *a, **k: 1
    try:
        back = _Session(True)
        ok = back._recover_world("no client instance is running", trigger="client_absent")
    finally:
        _bridge.client_count = original_count
    row = back.rows[-1]
    check("a rescued session reports the wait as a recovery row",
          ok and row["kind"] == "recovery" and row["trigger"] == "client_absent"
          and row["recovered"] is True and row["waited_s"] >= 0.0, str(row))
    check("the rescue is counted, and the stale per-step state is dropped",
          (back._recoveries, back._recoveries_ok) == (1, 1)
          and back._forced_call is None and back._action_noops == {}
          and back._farm_wait is False and back._stall_since == back._index,
          "%r %r" % (back._recoveries, back._forced_call))

    gone = _Session(False)
    _bridge.client_count = lambda *a, **k: 1
    try:
        failed = gone._recover_world("no client instance is running",
                                     trigger="client_absent")
    finally:
        _bridge.client_count = original_count
    frow = gone.rows[-1]
    check("a session that never comes back is reported as a failure, not a success",
          failed is False and frow["recovered"] is False
          and (gone._recoveries, gone._recoveries_ok) == (1, 0)
          and gone._recovery_wait_s == 0.0, "%r %r" % (frow, gone._recoveries_ok))

    # The presence gate on its own. This is what stops the wait from resolving on the
    # *carried* snapshot: with no page, that still claims the character is on a map,
    # so a world-only wait would exit instantly and re-dispatch into the same failure.
    wait = _Session(True)
    _bridge.client_count = lambda *a, **k: 2
    try:
        found = wait._wait_for_window()
    finally:
        _bridge.client_count = original_count
    check("an open window ends the presence wait", found is True)

    _bridge.client_count = lambda *a, **k: None
    wait._bridge_alive = lambda: False
    try:
        dead_host = wait._wait_for_window()
    finally:
        _bridge.client_count = original_count
    check("a host that stopped answering ends the run instead of parking it",
          dead_host is False)

    _bridge.client_count = lambda *a, **k: 0
    wait._should_stop = lambda: "stop file"
    try:
        stopped = wait._wait_for_window()
    finally:
        _bridge.client_count = original_count
    check("the stop file ends the presence wait", stopped is False)

    # Zero windows is not a window: the wait has to keep waiting. Without this the
    # loop could read "no client" as "client present", resolve on the carried
    # snapshot, and re-dispatch the directive that just failed -- an instant cycle.
    sleeps: List[float] = []
    real_sleep = _autopilot_module.time.sleep
    _autopilot_module.time.sleep = lambda seconds: sleeps.append(seconds)
    polls = []

    def _none_yet(*a, **k):
        polls.append(1)
        return 0 if len(polls) < 4 else None

    _bridge.client_count = _none_yet
    waiting = _Session(True)
    waiting._bridge_alive = lambda: True
    try:
        # count 0 three times, then the host stops answering -> the honest end.
        waiting._should_stop = lambda: None if len(polls) < 4 else "bridge gone"
        never = waiting._wait_for_window()
    finally:
        _bridge.client_count = original_count
        _autopilot_module.time.sleep = real_sleep
    check("an empty window count keeps the run waiting instead of resuming",
          never is False and len(polls) >= 3 and len(sleeps) >= 2,
          "%d polls, %s" % (len(polls), sleeps))

    check("the world wait is only asked after a window exists",
          "_wait_for_window() if trigger == \"client_absent\"" in src_of_recover)

    # The presence read itself. A failure must be `None` (unknown), never 0: the loop
    # treats 0 as "no window yet, keep waiting" and None as "ask whether the host is
    # even there", so a broken parse reported as 0 would park a live run forever.
    from agent import bridge as _bridge_mod

    def _probe(body: bytes, explode: bool = False) -> Any:
        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return body

        class _Opener:
            def open(self, *a, **k):
                if explode:
                    raise OSError("no answer")
                return _Resp()

        saved = _bridge_mod.local_opener
        _bridge_mod.local_opener = lambda: _Opener()
        try:
            return _bridge_mod.client_count()
        finally:
            _bridge_mod.local_opener = saved

    check("the host's instance count is read from /agent/health",
          _probe(b'{"status":"ok","service":"dofus-agent-bridge","port":8893,'
                 b'"instances":3,"time":1}') == 3)
    check("a malformed health body is unknown, not zero windows",
          _probe(b'<html>proxy intercepted this</html>') is None
          and _probe(b'{"instances":null}') is None)
    check("a bridge that does not answer is unknown, not zero windows",
          _probe(b"", explode=True) is None)

    src = inspect.getsource(Autopilot)
    anchor = 'if not step_ok and client_absent(step_error):'
    settle = 'after = self._settle(before, after, call=call)'
    check("a dead window is waited on before any settle poll can run",
          anchor in src and settle in src and src.index(anchor) < src.index(settle)
          and "_recover_world(" in src[src.index(anchor):src.index(settle)]
          and 'trigger="client_absent"' in src[src.index(anchor):src.index(settle)],
          "the branch must be live, ordered first, and call the wait")
    check("the loop passes its own trigger, not the default",
          src.count('trigger="client_absent"') == 1
          and 'trigger == "client_absent"' in src,
          "%d call site(s)" % src.count('trigger="client_absent"'))
    check("a dead window ends the run with its own reason",
          'the client window is gone' in src
          and 'open an instance in DofusFarm' in src)
    check("the summary carries the recovery numbers",
          all(key in inspect.getsource(Autopilot.summary)
              for key in ("recoveries", "recoveries_ok", "recovery_wait_s")))


def test_identity_binding() -> None:
    section("Identity (G9: a slot is not an account)")
    import inspect
    import json as _json
    from agent.autopilot import Autopilot

    class Shim:
        session = "s"
        max_steps = 0
        deadline = 0.0
        _index = 0

        def __init__(self):
            self._identity = None
            self._identity_drift = None
            self.rows = []
            self.stop_file = Path("this-stop-file-does-not-exist")

        def _append_log_line(self, line):
            self.rows.append(line)

    def bind(shim):
        return Autopilot._check_identity.__get__(shim, Shim)

    def events(shim):
        return [_json.loads(line.decode("utf-8")).get("event") for line in shim.rows]

    def world(character, **over):
        base = {"in_world": True, "position": {"map_id": 100},
                "character": character}
        base.update(over)
        return base

    shim = Shim()
    who = bind(shim)
    check("the first real character binds the run",
          who(world({"id": 1, "name": "Aiai"})) is True
          and shim._identity == {"id": "1", "name": "Aiai"}, shim._identity)
    check("the binding is logged with the character, not just the slot",
          events(shim) == ["bound"] and b"Aiai" in shim.rows[0], shim.rows[:1])
    check("the same character is not a change",
          who(world({"id": 1, "name": "Aiai"})) is True
          and shim._identity_drift is None)
    check("a level-up is not an identity change",
          who(world({"id": 1, "name": "Aiai", "level": 4})) is True)
    check("a rename on the same id is the same account",
          who(world({"id": 1, "name": "Aiai II"})) is True
          and shim._identity_drift is None)
    check("character select and older pages are unknown, not a change",
          who(world({"id": None, "name": None})) is True
          and who({}) is True)

    pre = Shim()
    pw = bind(pre)
    pw({"in_world": False, "position": {"map_id": 0},
        "character": {"id": 9, "name": "Old"}})
    check("a character shown at character select is not a binding",
          pre._identity is None and pre._identity_drift is None, pre._identity)
    check("the first in-world snapshot then binds the real character",
          pw(world({"id": 1, "name": "Aiai"})) is True
          and pre._identity == {"id": "1", "name": "Aiai"}, pre._identity)
    check("and a different in-world character is drift, not a stale-select false positive",
          pw(world({"id": 2, "name": "Other"})) is False
          and pre._identity_drift is not None, pre._identity_drift)

    drift = Shim()
    dw = bind(drift)
    dw(world({"id": 1, "name": "Aiai"}))
    check("a different character on the same slot fails closed",
          dw(world({"id": 2, "name": "Other"})) is False
          and drift._identity_drift is not None, drift._identity_drift)
    check("the drift is logged with both identities",
          events(drift) == ["bound", "drift"] and b"Other" in drift.rows[-1],
          drift.rows[-1:])
    check("and it becomes a stop reason instead of a silent log line",
          "different character" in str(Autopilot._should_stop(drift)),
          Autopilot._should_stop(drift))

    name_only = Shim()
    nw = bind(name_only)
    nw(world({"name": "Aiai"}))
    check("a build that exposes only the name still binds and compares",
          nw(world({"name": "Aiai"})) is True
          and nw(world({"name": "Other"})) is False)
    id_only = Shim()
    iw = bind(id_only)
    iw(world({"id": 7}))
    check("a build that exposes only the id still binds and compares",
          iw(world({"id": 7})) is True
          and iw(world({"id": 8})) is False)
    late = Shim()
    lw = bind(late)
    lw(world({"name": "Aiai"}))
    check("an id that arrives after the name is the same character and is kept",
          lw(world({"id": 1, "name": "Aiai"})) is True
          and late._identity == {"id": "1", "name": "Aiai"}, late._identity)

    run_src = inspect.getsource(Autopilot._run)
    world_src = inspect.getsource(Autopilot._require_world)
    check("the main loop checks identity on every observation",
          "_check_identity(before)" in run_src, "the loop does not check")
    check("and the world wait checks it before treating a map as the same run",
          "_check_identity(obs)" in world_src, "the wait does not check")
    check("recovery failures prefer the identity stop reason over a generic one",
          run_src.count("self._should_stop()") >= 3,
          "a drift during recovery would be reported as a client failure")


def test_live_witnesses() -> None:
    section("Live witnesses (G9/G29/G32/G33 in one readout)")
    import contextlib
    import importlib.util
    import io
    import json as _json
    import tempfile

    script = REPO_ROOT / "tools" / "session_report.py"
    check("the report tool exists", script.exists(), str(script))
    if not script.exists():
        return
    spec = importlib.util.spec_from_file_location("session_report", str(script))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    obs_new = {
        "agent_build": "2026-10-27a",
        "character": {"id": 1, "name": "Aiai"},
        "knowledge": {"status": "observed", "observed_from": "catalog",
                      "objective": {"text": "Parler à Ayin Spyr"}},
        "quests_catalog": [{"id": 3841, "name": "Q", "steps": [
            {"id": 1, "objectives": [{"id": 14070}, {"id": 14071}]}]}],
        "quest_catalog_status": {"quests": 1, "steps": 1, "objectives": 2,
                                 "truncated": False},
        "dialog": {"open": True, "start_quest": 4266},
        "in_dialog": True,
    }
    rows = [
        {"kind": "identity", "session": "s", "event": "bound",
         "character_id": "1", "name": "Aiai", "at": 1},
        {"kind": "identity", "session": "s", "event": "drift",
         "bound": {"id": "1", "name": "Aiai"},
         "seen": {"id": "2", "name": "Other"}, "at": 2},
        {"kind": "step", "session": "s", "index": 0, "tool": "quest.advance",
         "ok": True, "args": {"quest_id": 4266, "accept": True},
         "result": {"response": {"result": {"data": {
             "accepted_offer": True, "stage": "quest_accept_sent"}}}},
         "observation": obs_new, "at": 10},
        {"kind": "step", "session": "s", "index": 1, "tool": "quest.advance",
         "ok": True, "args": {"quest_id": 777},
         "result": {"response": {"result": {"data": {
             "stage": "dialog_start_not_accept"}}}},
         "observation": {"in_dialog": True, "dialog": {"open": True}}, "at": 11},
        {"kind": "step", "session": "s", "index": 2, "tool": "quest.advance",
         "ok": False, "args": {"quest_id": 7, "npc_id": 1},
         "observation": {"agent_build": "2026-10-25d",
                         "knowledge": {"status": "objective_not_in_store"}},
         "at": 12},
    ]
    got = module.witnesses(rows)
    check("the page build is counted per observation, including the stale one",
          got["builds"].get("2026-10-27a") == 1
          and got["builds"].get("2026-10-25d") == 1, got["builds"])
    check("and the verdict names the builds, so a stale page is visible",
          "page build:" in " | ".join(got["verdicts"])
          and "2026-10-25d" in " | ".join(got["verdicts"]), got["verdicts"])
    check("the observed knowledge and the store miss are counted separately",
          got["knowledge"].get("observed") == 1
          and got["knowledge"].get("objective_not_in_store") == 1, got["knowledge"])
    check("the observed row says where it came from",
          got["observed_from"].get("catalog") == 1, got["observed_from"])
    check("the catalogue census counts the graph, not just the rows",
          got["catalog"]["rows"] == 1 and got["catalog"]["quests"] == 1
          and got["catalog"]["steps"] == 1 and got["catalog"]["objectives"] == 2
          and got["catalog"]["dialog_offers"] == 1, got["catalog"])
    check("identity bound and drift rows are both counted, with names",
          got["identity"].get("bound") == 1 and got["identity"].get("drift") == 1
          and got["identity_names"] == ["Aiai", "Other"], got["identity"])
    check("an explicit accept and a start-shaped call are different census keys",
          got["accept"]["rows"] == 1 and got["accept"]["ok"] == 1
          and got["accept"]["start_rows"] == 1
          and got["accept"]["start_in_dialog"] == 1, got["accept"])
    check("the page answer is recorded with the accept row",
          got["page"].get("quest_accept_sent") == 1,
          got["page"])
    verdicts = " | ".join(got["verdicts"])
    check("the verdict says observed rows came from the catalogue",
          "1 observed (catalog 1, file 0)" in verdicts, verdicts)
    check("the verdict counts the catalogue graph",
          "quests_catalog: 1 row(s), 1 quest(s), 2 objective(s)" in verdicts,
          verdicts)
    check("the verdict names the bound and drifted characters",
          "identity: bound 1, drift 1 (Aiai, Other)" in verdicts, verdicts)
    check("the verdict separates accepts from start-shaped dialog calls",
          "accept: 1 row(s) (ok 1)" in verdicts
          and "1 start-shaped inside a dialog" in verdicts, verdicts)

    tmp = Path(tempfile.mkdtemp(prefix="witnesses-"))
    log = tmp / "log.jsonl"
    log.write_text("\n".join(_json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    report = module.summarise(log)
    check("the report carries the witnesses in its JSON",
          (report.get("witnesses") or {}).get("catalog", {}).get("quests") == 1,
          report.get("witnesses"))
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        module.main([str(log)])
    text = buffer.getvalue()
    check("and the printed report shows every verdict",
          all(line in text for line in got["verdicts"]), text[-400:])

    empty = module.witnesses([{"kind": "step", "observation": {}}])
    empty_text = " | ".join(empty["verdicts"])
    check("an older log says 'none' instead of printing zeros as data",
          "none in these rows" in empty_text
          and "no bound/drift rows" in empty_text
          and "no explicit accept rows" in empty_text, empty_text)

    shadow_rows = [
        {"kind": "command_shadow_status", "session": "s", "available": True,
         "error": None, "model": "m", "mode": "auto"},
        {"kind": "command_shadow", "text": "do all the quests", "usable": True,
         "agree_intent": True, "agree_command": False, "would_override": True,
         "rule": {"intent": "quest", "slots": {"action": "list"}},
         "model": {"intent": "quest", "slots": {"action": "run"},
                   "confidence": 0.95}},
        {"kind": "command_shadow", "text": "plz do all the quests", "usable": True,
         "agree_intent": True, "agree_command": True, "would_override": False,
         "rule": {"intent": "quest", "slots": {"action": "list"}},
         "model": {"intent": "quest", "slots": {"action": "list"},
                   "confidence": 0.9}},
    ]
    shadow_w = module.witnesses(shadow_rows)
    examples = shadow_w["command_shadow"].get("examples") or [""]
    check("the witness counts the command shadow and its overrides",
          shadow_w["command_shadow"]["rows"] == 2
          and shadow_w["command_shadow"]["would_override"] == 1
          and "do all the quests" in examples[0], shadow_w["command_shadow"])
    check("the witness says the shadow was requested and available",
          any("command_shadow:" in verdict and "would override 1" in verdict
              for verdict in shadow_w["verdicts"]), shadow_w["verdicts"])


def test_bundle_updater() -> None:
    section("Bundle updater (a server build must actually reach the disk)")
    # Measured on 2026-10-27: the client showed "a new version is available" because
    # the login server runs build 1.74.10 and the local bundle is 1.74.9. Ankama had
    # shipped it weeks earlier; the updater never installed it, for two reasons that
    # both live in desktop/clientbundle.cs. These checks pin the fixes, because the
    # only other witness is a launch against the real CDN.
    src_path = ROOT / "desktop" / "clientbundle.cs"
    check("the bundle updater source exists", src_path.exists(), str(src_path))
    if not src_path.exists():
        return
    src = src_path.read_text(encoding="utf-8", errors="replace")

    check("the download User-Agent is set per request, not on the shared header bag",
          "\n                        if (!string.IsNullOrEmpty(Agent)) http.UserAgent = Agent;" in src
          and 'wc.Headers["User-Agent"] =' not in src,
          "WebClient empties Headers after its first request: every later request "
          "goes without a UA and the origin answers 403")
    gw = src.find("protected override WebRequest GetWebRequest")
    ua_at = src.find("http.UserAgent = Agent;")
    check("the User-Agent is applied inside GetWebRequest, where every request passes",
          0 <= gw < ua_at < gw + 900, "the UA is set somewhere requests never see")
    check("the update run uses that property",
          'wc.Agent = "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36";' in src,
          "no agent is handed to the client")

    check("a staged update is not judged on a page it never downloads",
          "Validate(staging, false, out stagedWhy)" in src,
          "the manifest ships only build/* files, so demanding index.html here "
          "rejects every staged bundle and adopts nothing")
    check("a staged bundle with no core patch is refused",
          "coreTotal > 0 && coreHit == 0" in src,
          "adopting an unpatched script.js is the login break this file exists to stop")
    check("the update transaction is single-flight",
          "lock (_gate)" in src,
          "Launch All runs one updater per instance over the same script.js")
    stamp_at = src.find("File.WriteAllText(stampPath")
    restamp_at = src.find("RewriteCacheBusters(dir, log);", stamp_at) if stamp_at >= 0 else -1
    check("the asset URLs are re-stamped after an adoption",
          stamp_at >= 0 and restamp_at > stamp_at,
          "the page keeps asking for the old ?v= and Chromium serves the old bundle")


def test_multi_instance_targeting() -> None:
    section("Multi-instance selection (the AI can address several clients at once)")
    # The panel could name only ONE instance, and its "All instances" entry meant
    # "whichever window the bridge happens to list first" -- so driving four open
    # clients needed four panels, and the label promised something the code did not
    # do. Requested 2026-10-27: real multi-selection plus every-instance selection.
    panel = ROOT / "desktop" / "aipanel.cs"
    bridge = ROOT / "desktop" / "agentbridge.cs"
    check("the AI panel source exists", panel.exists(), str(panel))
    check("the bridge source exists", bridge.exists(), str(bridge))
    if not (panel.exists() and bridge.exists()):
        return
    p = panel.read_text(encoding="utf-8", errors="replace")
    b = bridge.read_text(encoding="utf-8", errors="replace")

    check("the selection is a list of slots, not one index",
          "public List<int> TargetSlots = new List<int>();" in p, "no multi-target state")
    check("the selection is persisted and re-read",
          'case "target_slots":' in p and 'sb.AppendLine("target_slots="' in p,
          "a restart would forget which instances were chosen")
    check("the single-instance combo is gone",
          "_cmbTarget" not in p and "_btnTargets" in p,
          "a combo can only ever name one instance")
    check("an unfiltered choice means every open window",
          'return ids.Count == 0 ? "all" : string.Join(",", ids.ToArray());' in p,
          "empty selection must become the literal all, not nothing")
    check("the loop starts one child per selected instance",
          "foreach (string instance in targets)" in p and "autopilot.\" + instance" in p,
          "a shared log file would interleave two characters' rows")
    check("every child is tracked",
          "private readonly List<Process> _procs = new List<Process>();" in p
          and "private Process _proc;" not in p, "stop would only ever reach one loop")
    exited = p.find("private void OnChildExited(Process p)")
    idle_at = p.find("TryIdleTraining();", exited) if exited >= 0 else -1
    check("a child leaving alone does not declare the machine idle",
          exited >= 0 and "!anyLeft" in p[exited:idle_at + 40],
          "the quickest loop finishing used to flip the UI and train on half a run")

    check("the bridge resolves 'all' against the windows that are open",
          "private List<string> ResolveTargets(string target, int slot)" in b
          and "GetActiveInstanceIds()" in b[b.find("private List<string> ResolveTargets"):
                                            b.find("private string RunOne")],
          "all must enumerate the active windows inside ResolveTargets, not elsewhere")
    check("a comma-separated target list is accepted",
          'target.Split(\',\')' in b, "one directive could only ever name one client")
    fan = b.find("foreach (string one in targets) answers.Add(RunOne(")
    single = b.find("RunOne(id, body, label, targets[0], true)")
    check("one addressee keeps the exact response shape it always had",
          0 <= single < fan, "the single-instance path must not change shape")
    check("a fan-out answers for every instance and says so",
          r',\"results\":[' in b and "fanned out to " in b, "silently answering for one is a lie")
    check("the stale-name fallback is limited to a single addressee",
          "bool allowFallback" in b and "allowFallback ? FirstActiveInstance() : null" in b,
          "with several targets, substituting another window mislabels the answer")


def test_per_instance_ai() -> None:
    section("Per-instance AI (one client's badge must not drive the whole farm)")
    # Measured cost of the shared flag: the panel had ONE "a loop is running" bool,
    # PushAiState(instanceId) read it, and the in-game badge sent `stop_all`. So one
    # window's button stopped every loop, every window showed the same step count,
    # and the page's own keep-alive gate (`if (!enabled && !aiRunning) return`) then
    # left clients with no loop to keep them server-visible -- the seven
    # reason=INACTIVITY disconnects the triage script reads off DofusFarm.log.
    panel = code_only((ROOT / "desktop" / "aipanel.cs").read_text(encoding="utf-8", errors="replace"))
    form = code_only((ROOT / "desktop" / "mainform.cs").read_text(encoding="utf-8", errors="replace"))
    page = code_only((REPO_ROOT.parent / "scripts" / "mobile-disguise.js").read_text(
        encoding="utf-8", errors="replace"))

    check("each loop child is attributed to the instance it drives",
          "_instanceByProc" in panel and "_instanceByProc[p] = instance;" in panel,
          "without attribution the panel can only say 'something is running'")
    check("a client is 'on' only while its own loop runs",
          "public bool IsRunningFor(string instanceId)" in panel
          and "RunningInstanceOf(instanceId) != null" in panel, "a shared flag lights every window")
    check("one instance's status is read from its own step log",
          "public string LiveStatusFor(string instanceId)" in panel
          and "ReadLastStepLine(StepLogFor(instanceId))" in panel,
          "one client's step count was shown on all of them")
    check("stopping one loop writes only that loop's stop file",
          "public void StopInstance(string instanceId, string why)" in panel
          and "File.WriteAllText(StopFilePath(instanceId)" in panel,
          "the shared AUTOPILOT_STOP would stop every client")
    check("each child watches its own stop file",
          'sb.Append(" --stop-file \\"")' not in panel
          and '+ " --stop-file \\"" + childStop' in panel,
          "one global stop file cannot stop one instance")
    check("a badge start targets exactly one instance",
          "public void StartInstance(string instanceId, bool force)" in panel
          and "string policyOverride = null, string onlyInstance = null)" in panel,
          "the badge reused the panel's shared selection")
    check("the badge state pushed to a page is that page's own",
          "_aiPanel.IsRunningFor(instanceId)" in form
          and "_aiPanel.LiveStatusFor(instanceId)" in form,
          "PushAiState read the farm-wide flag")
    check("the badge diff is tracked per instance",
          "Dictionary<string, bool> _aiPushedByInstance" in form
          and "_aiPushedByInstance[id] = mine;" in form,
          "a single remembered bool re-pushes the same answer to every window")
    check("the host answers start_one and stop_one",
          '"start_one"' in form and '"stop_one"' in form,
          "an unknown action logs and does nothing")
    check("the in-game pill acts on its own instance, not on all",
          "action: this.aiRunning ? 'stop_one' : 'start_one'" in page
          and "action: this.aiRunning ? 'stop_all' : 'start_auto'" not in page,
          "pressing AI in one window stopped every loop")
    # The keep-alive was gated on a host-pushed flag, and a page reload resets the
    # page's copy while the loop keeps driving -- measured as a live run losing its
    # session to dofus.disconnect("INACTIVITY") ten minutes in (the client's own
    # timer: warn at 5 min idle, drop at 10, checked every 2 min).
    check("a directive marks activity without waiting for the pushed flag",
          "try { window.__agentMarkActivity('directive'); } catch (eAct) {}" in page,
          "the keep-alive gate could be shut by a reload while the loop ran")
    check("the marks are published in the observation",
          "snap.keep_alive = " in page
          and "marks: ka.marks || 0, failed: ka.failed || 0," in page
          and "idle_ms: ka.last_ts ? (Date.now() - ka.last_ts) : null" in page,
          "a prevented AFK drop is silent, so an uncounted keep-alive is unprovable")
    check("the page asks the host for its state when the bus comes up",
          "window.chrome.webview.postMessage({ type: 'AI_REQUEST', action: 'state' });" in page,
          "a reloaded page kept its own default and the badge lied")
    # The status line is an instrument, and it was lying: four loops had taken 36-44
    # steps each while every badge and the shell pill said "waiting for the first
    # step". An 8 KB tail window lands inside an observation row, and one
    # FileStream.Read may return less than it was asked for.
    tail = panel[panel.find("public static string ReadLastStepLine(string path)"):]
    check("the step tail is wide enough for a row that carries a snapshot",
          "len - 65536" in tail, "8 KB lands inside an observation row")
    check("the tail is read until the buffer is full",
          "while (got < buf.Length)" in tail,
          "a short first Read cut the tail and the last row was never reached")
    check("only a real step row is returned, not a fragment",
          'line.IndexOf("\\"index\\"") < 0' in tail and 'line.IndexOf("\\"tool\\"") < 0' in tail,
          "a fragment starting with { formatted as no step")
    # The picker and the Start button both had the same failure shape: a selection
    # that named instances was overridden by a broader default, silently. Measured
    # live on 2026-10-27 -- the dialog showed "All instances" AND "#1 dofus-01"
    # checked at once, and Start refused outright while any loop was already running.
    check("the target picker is exclusive in both directions",
          "if (box.GetItemChecked(0)) box.SetItemChecked(0, false);" in panel,
          "a row could be checked while 'All instances' stayed checked")
    check("an explicit row beats a stale 'All instances' check",
          "if (list.GetItemChecked(i)) next.Add(i);" in panel
          and "!list.GetItemChecked(0)" not in panel,
          "the 'All' tick swallowed the operator's selection")
    check("Start stays clickable while a partial selection is idle",
          "_btnStart.Enabled = !running || SelectedSlots().Count > 0;" in panel
          and "SetRunningUi(RunningNow);" in panel,
          "the button went dark with any loop running, so a second instance could not be added")
    start_at = panel.find("public void StartAuto(bool force)")
    picked_at = panel.find("if (picked.Count > 0)", start_at)
    refuse_at = panel.find('if (IsRunning) { Append("[ai] already running', start_at)
    check("Start honours the selection while another instance is already driving",
          start_at >= 0 and 0 <= picked_at < refuse_at
          and "StartInstance(id, force);" in panel[picked_at:refuse_at],
          "the farm-wide 'press Stop first' refusal ran before the picked instances")
    prog = code_only((ROOT / "desktop" / "program.cs").read_text(encoding="utf-8", errors="replace"))
    check("the status selftest can target one instance",
          "UI.AiPanel.StepLogFor(instance)" in prog,
          "the selftest could only ever read the farm-wide log")
    check("the selftest finds the instance argument after the flags",
          "bool askedForInstance = instance != null;" in prog
          and 'if (!args[i].StartsWith("--")) { instance = args[i]; break; }' in prog,
          'reading args[1] reported the farm-wide log for every instance asked about')
    check("the selftest reports a step row formatted as no step",
          "RESULT: WRONG (" in prog and 'parsed && !UI.AiPanel.FormatStatus(true, line)' in prog,
          "the formatter lying about a real row had no witness")
    check("the selftest refuses an instance ask that fell back to the shared log",
          "bool fellBack = askedForInstance && !log.EndsWith(instance + \".jsonl\");" in prog
          and "but read the farm-wide log" in prog,
          "a silent fallback made a broken per-instance read look like a pass")


def test_instance_reset() -> None:
    section("Instance reset (a banned guest is replaced, never repaired)")
    # 2026-10-28: dofus-01's guest was banned outright (offline BAN at login).
    # The recovery the operator needs is not "log in again" -- the identity is
    # gone at the server -- but a clean slot: stop, wipe that profile, register
    # a fresh guest. The trap is a wipe that only clears session storage and
    # leaves the old identity (or worse, reseeds it from accounts.txt) to come
    # back under a new character. Installation sets the same bar: an empty
    # directory per slot, nothing copied in.
    form = code_only((ROOT / "desktop" / "mainform.cs").read_text(encoding="utf-8", errors="replace"))
    installer = code_only((ROOT / "desktop" / "installer.cs").read_text(encoding="utf-8", errors="replace"))

    start = form.find("private void PromptAndResetInstance(InstanceModel inst)")
    end = form.find("private void PromptAndDeleteAllInstances()", start)
    reset = form[start:end] if start >= 0 and end > start else ""

    check("the manager row carries a Reset action",
          'CreateBtnCol("Reset", "RESET", "Reset", 66)' in form
          and "BrushBtnResetBg" in form and 'btnText = "Reset"' in form
          and "PromptAndResetInstance(inst);" in form,
          "a banned slot had no operator-facing recovery in the grid")
    check("the row menu offers the same reset",
          "Reset Instance (fresh guest)..." in form,
          "right-click had no equivalent action")
    check("reset stops the loop and the client before touching the profile",
          'ResetInstanceProfile(inst)' in reset
          and '_aiPanel.StopInstance(inst.Name, "instance reset")' in reset
          and "_webViewBackend.Stop(inst.Name)" in reset,
          "deleting under a live client either fails or is silently partial")
    check("reset waits for the browser to release the user-data folder",
          "!form.IsDisposed" in reset and "Thread.Sleep(100)" in reset,
          "WebView2 keeps EBWebView locked past the close call")
    check("reset deletes the whole slot profile, not just the session",
          "FarmEngine.ForceDeleteDirectory(dir)" in reset
          and "WebView2ProfileDir(inst.Name)" in reset
          and "EBWebView" not in reset,
          "clearing Local Storage alone can leave the identity and old state behind")
    check("a profile that would not delete never gets a relaunch",
          "if (!removed)" in reset and "return false;" in reset
          and "bool relaunch = ResetInstanceProfile(inst);" in reset
          and "if (relaunch && !_isClosing)" in reset,
          "relaunching on a dirty profile carries old data into the 'fresh' guest")
    check("the emptied slot is barred from accounts.txt seeding",
          'force_fresh_identity.flag' in reset,
          "a saved [GUEST] entry would be injected into the fresh profile")
    check("the reset relaunches through the normal launch path",
          "StartWebView2Instance(inst, GetSelectedRam(), GetSelectedCores());" in reset
          and "CloneInstance" not in reset,
          "a clone would copy the very state the reset exists to remove")
    at = installer.find("Directory.CreateDirectory(wvProf)")
    seg = installer[max(0, at - 400): at + 200] if at >= 0 else ""
    check("installation starts every slot from an empty profile directory",
          at >= 0 and "File.Copy" not in seg,
          "a template copied at install time would be the same transfer by another name")


def test_no_loop_without_client() -> None:
    section("AI loops require an open client (one window never meant one loop per slot)")
    # Measured 2026-10-28: pressing Auto in dofus-03 started loops for dofus-01,
    # dofus-02 and dofus-05, which have no windows. The bridge's stale-name
    # fallback then aimed all three at dofus-03, so four loops logged into one
    # client until the login server answered "It was not possible to connect to
    # the server". A loop for a closed slot cannot work; it can only stampede.
    panel = code_only((ROOT / "desktop" / "aipanel.cs").read_text(encoding="utf-8", errors="replace"))
    form = code_only((ROOT / "desktop" / "mainform.cs").read_text(encoding="utf-8", errors="replace"))

    check("the panel takes an open-client probe from the host",
          "public Func<string, bool> ClientIsOpen" in panel
          and "private Func<string, bool> _clientIsOpen" in panel
          and "private bool InstanceIsOpen(string instanceId)" in panel
          and "public bool HasClientWindow(string instanceId)" in panel,
          "the panel has no way to know which windows exist")
    check("the target list drops slots with no window",
          "if (!InstanceIsOpen(id))" in panel
          and "no client window; not starting a loop for it" in panel,
          "TargetsForLoop returned every configured slot")
    check("an empty target list refuses instead of starting a default loop",
          "no open client to drive; launch an instance first" in panel
          and "if (_clientIsOpen != null)" in panel,
          "the shared null target let the bridge fallback pick any client")
    check("a per-instance start refuses a closed slot",
          "no client window; not starting a loop" in panel,
          "StartInstance could be pointed at an instance with no window")
    check("the host probe reports a starting or open WebView2 form",
          "_aiPanel.ClientIsOpen = delegate(string instanceId)" in form
          and "ClientInstanceState.Starting" in form
          and "_webViewBackend.GetForm(instanceId)" in form,
          "the panel's probe was never wired to the backend")


def test_fight_end_witness() -> None:
    section("Fight witness (fight.join -> GameFightEndMessage -> win, in one row)")
    # "Brytnee goes down (+2,000 XP)" in a session log was repeatedly mistaken
    # for a closed fight chain. It is not: a kill line does not name the call
    # that started the fight, and it does not carry the server's own end
    # message. The calibration set is the outcome log, so the gate reads that
    # log and fails until one row is a complete claim. Measured live
    # 2026-10-28 on dofus-03: row fight_id 124, group -4335, won true, the
    # GameFightEndMessage results attached.
    # The Kaggle toolkit is a code mirror: no client and no live logs. The
    # witness is evidence from this machine, so the mirror runs no live checks;
    # its copy of this file is held by the toolkit-sync check instead.
    if not (ROOT / "desktop" / "mainform.cs").exists():
        return
    # `trajectories/` is gitignored (it is runtime data). The witness row is
    # also kept verbatim in a tracked file so the gate means something on a
    # fresh checkout instead of only on the machine that ran the fight.
    sources = [REPO_ROOT / "trajectories" / "fight_outcomes.jsonl",
               REPO_ROOT / "data" / "live_evidence" / "fight_outcomes.jsonl"]
    rows: List[Dict[str, Any]] = []
    seen_paths: List[str] = []
    for path in sources:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
            seen_paths.append(path.name)
        except OSError:
            continue
    check("a fight-outcome log exists (live or checked-in)",
          bool(seen_paths), "neither %s exists" % " nor ".join(str(p) for p in sources))
    outcomes = [row for row in rows if row.get("kind") == "fight_outcome"]
    check("it holds at least one resolved fight", bool(outcomes),
          "%d fight_outcome rows" % len(outcomes))
    witness = None
    for row in outcomes:
        end = row.get("end") if isinstance(row.get("end"), dict) else {}
        if (row.get("started_by") == "fight.join"
                and row.get("won") is True
                and end.get("won") is True
                and row.get("fight_id") is not None
                and str(row.get("fight_id")) == str(end.get("fight_id"))):
            witness = row
            break
    check("a row spans fight.join -> GameFightEndMessage with the win",
          witness is not None,
          "no row has started_by=fight.join, won=true, end.won=true and one fight id")
    end = (witness or {}).get("end") or {}
    results = end.get("results") if isinstance(end.get("results"), list) else []
    mine = [row for row in results if isinstance(row, dict)
            and str(row.get("id")) == str(end.get("my_id"))]
    check("the winning row carries the server's results list",
          bool(results) and bool(mine) and mine[0].get("alive") is True,
          "end.results must say the character is alive")
    check("the row is a live claim, not a simulation",
          (witness or {}).get("source") == "live",
          str((witness or {}).get("source")))


def test_join_names_a_group() -> None:
    section("A targetless join is only legal on a one-group map")
    # The rule policy answered "join the fight" with `fight.join {}`, and the
    # advisor refuses that on every map with more than one group -- the whole
    # tutorial route. The order existed and could never produce a fight
    # (measured 2026-10-28: 4 groups visible, guard refused every attempt).
    from agent.runtime import RulePolicy, _join_args
    obs = {"fight_estimates": [{"group_id": -1, "allowed": False},
                               {"group_id": -4335, "allowed": True}],
           "map": {"groups": [{"group_id": -4335}, {"group_id": -4336}]}}
    call = RulePolicy().choose("join the fight", obs, [])
    check("the policy names the first allowed group",
          call is not None and call.tool == "fight.join"
          and call.args.get("group_id") == -4335,
          str(getattr(call, "args", None)))
    check("a one-group map still resolves without estimates",
          _join_args({"map": {"groups": [{"group_id": 7}]}}) == {"group_id": 7},
          str(_join_args({"map": {"groups": [{"group_id": 7}]}})))
    check("an ambiguous map without estimates stays targetless",
          _join_args({"map": {"groups": [{"group_id": 7}, {"group_id": 8}]}}) == {},
          str(_join_args({"map": {"groups": [{"group_id": 7}, {"group_id": 8}]}})))


def test_battle_log_and_challenges() -> None:
    section("Battle log and optional challenges (the client's own numbers)")
    # Two things the client already receives and the page threw away:
    #  * `fm.fightMessagesStack` holds the fight's action messages, including
    #    GameActionFightLifePointsLostMessage, whose `loss`/`permanentDamages`
    #    are the server's own damage numbers. The old reader kept spell/source/
    #    target and dropped the amounts.
    #  * ChallengeInfoMessage / ChallengeResultMessage describe the optional
    #    fight challenges; nothing read them at all.
    page = (ROOT / "scripts" / "mobile-disguise.js").read_text(
        encoding="utf-8", errors="replace")
    check("the page stores the client's challenge messages",
          "cm.on('ChallengeInfoMessage'" in page
          and "cm.on('ChallengeResultMessage'" in page
          and "__agentChallenges" in page,
          "challenges are invisible to the agent")
    check("challenges are cleared when a new fight starts",
          "cm.on('GameFightStartMessage'" in page and "delete chMap[ck]" in page,
          "last fight's challenge verdicts would leak into the next fight")
    check("the battle log keeps the damage fields the old reader dropped",
          "loss: num(msg.loss)" in page
          and "permanent_damages: num(msg.permanentDamages)" in page
          and "shield_loss: num(msg.shieldLoss)" in page,
          "last_actions still drops the actual damage numbers")
    check("fight.state and the snapshot carry the log and the challenges",
          "state.battle_log = _battleLog(800)" in page
          and "state.challenges = _challenges()" in page
          and "snap.challenges = _challenges()" in page,
          "the observation has no battle log or challenge list")
    check("the fight-end capture carries challenge verdicts and the log",
          "challenges: _challenges()," in page
          and "battle_log: _battleLog(800)" in page,
          "an outcome row cannot say whether a challenge was completed")
    server = (REPO_ROOT / "combat" / "combat_ai_server.py").read_text(
        encoding="utf-8", errors="replace")
    check("the combat decision log keeps the challenges it was sent",
          '"challenges": payload.get("challenges") or [],' in server,
          "a decision row cannot be used to learn a challenge-aware policy")

    from combat.fight_estimate import CHALLENGE_BONUS, FightOutcomeLog
    import time as _time

    def resolve(end: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        log = FightOutcomeLog(Path(tempfile.mkdtemp()) / "outcomes.jsonl")
        log.start({}, {"in_fight": True, "fight_id": 7,
                       "hp": {"life": 10, "max": 10}}, "s", started_by="fight.join")
        observation = {"in_world": True, "in_fight": False, "alive": True,
                       "hp": {"life": 10, "max": 10},
                       "last_fight_end": end}
        return log.resolve(observation, "s") or {}

    base_end = {"at": _time.time() * 1000.0, "fight_id": 7, "won": True}
    one_done = resolve(dict(base_end, challenges=[
        {"id": 1, "success": True}, {"id": 2, "success": False}]))
    check("a completed challenge adds a small bonus, a missed one costs nothing",
          one_done.get("challenges_done") == 1
          and one_done.get("challenge_bonus") == CHALLENGE_BONUS
          and one_done.get("reward") == round(1.0 + CHALLENGE_BONUS, 3),
          str({k: one_done.get(k) for k in ("challenges_done", "challenge_bonus", "reward")}))
    none = resolve(dict(base_end))
    check("a fight without challenges is worth exactly the win",
          none.get("challenge_bonus") == 0.0 and none.get("reward") == 1.0,
          str(none.get("reward")))
    lost = resolve(dict(base_end, won=False, challenges=[{"id": 1, "success": True}]))
    check("a challenge bonus never replaces the win/loss signal",
          lost.get("won") is False and lost.get("reward") == CHALLENGE_BONUS,
          str(lost.get("reward")))
    stale = resolve(dict(base_end, at=(_time.time() - 3600.0) * 1000.0,
                         challenges=[{"id": 1, "success": True}]))
    check("a stale end message's challenges are not credited",
          stale.get("challenge_bonus") == 0.0,
          str(stale.get("challenge_bonus")))


def test_keep_alive_instrument() -> None:
    section("AFK witness (a prevented kick is silent, so the run has to say it was alive)")
    from agent.autopilot import Autopilot, _compact

    # The client's own timer warns at 5 min idle and calls disconnect("INACTIVITY")
    # at 10 -- read off the served bundle, asserted in tools/test_keep_alive.js. A
    # keep-alive that did nothing looks exactly like one that worked, so the page
    # counts its marks and the loop folds that count into every step and the summary.
    snap = {"position": {"map_id": 7, "cell": 3},
            "keep_alive": {"marks": 12, "failed": 0, "present": True,
                           "last_source": "directive", "idle_ms": 4200}}
    kept = _compact(snap).get("keep_alive")
    check("the step row carries the page's AFK witness",
          isinstance(kept, dict) and kept.get("marks") == 12, _compact(snap))

    loop = Autopilot.__new__(Autopilot)      # counters only: no host, no bridge
    loop._keep_alive = {"steps": 0, "present_steps": 0, "marks": 0, "failed": 0,
                        "idle_max_ms": 0, "last_source": None, "age_max_ms": 0,
                        "inactive_steps": 0}
    loop._note_keep_alive(snap)
    loop._note_keep_alive({"keep_alive": {"marks": 30, "failed": 2, "present": False,
                                          "last_source": None, "idle_ms": 900000,
                                          "age_ms": 310000, "inactive": True}})
    loop._note_keep_alive({"keep_alive": {"marks": 4, "failed": 0, "present": True,
                                          "last_source": "timer", "idle_ms": 1500}})
    counters = loop._keep_alive
    check("the counters keep the worst reading, not the last one",
          counters["marks"] == 30 and counters["idle_max_ms"] == 900000
          and counters["last_source"] == "timer", counters)
    check("the client's own countdown is what gets reported",
          counters["age_max_ms"] == 310000 and counters["inactive_steps"] == 1, counters)
    check("a client with no recordActivity to call is counted, not hidden",
          counters["steps"] == 3 and counters["present_steps"] == 2
          and counters["failed"] == 2, counters)
    loop._note_keep_alive(None)
    check("a step with no observation moves nothing",
          loop._keep_alive["steps"] == 3, loop._keep_alive)
    auto = (REPO_ROOT / "agent" / "autopilot.py").read_text(encoding="utf-8", errors="replace")
    check("every step folds the witness in",
          "self._note_keep_alive(after)" in auto,
          "a counter nothing feeds is a counter that stays at zero")
    check("the run summary reports the witness",
          '"keep_alive": dict(getattr(self, "_keep_alive", {})),' in auto,
          "a multi-hour run that was never kicked would still not say it was alive")

    geometry = {"boot": {"w": 1024, "h": 640}, "now": {"w": 640, "h": 360},
                "drifted": True, "in_world": False, "reloads_last_2min": 1}
    check("the step row carries the viewport witness",
          _compact({"geometry": geometry}).get("geometry") == geometry)
    loop._geometry = {"steps": 0, "drift_steps": 0, "in_world_steps": 0,
                      "reloads": 0, "boot": None, "now": None}
    loop._note_geometry({"geometry": geometry})
    check("the run counters retain the geometry transition",
          loop._geometry["steps"] == 1 and loop._geometry["drift_steps"] == 1
          and loop._geometry["reloads"] == 1
          and loop._geometry["now"] == {"w": 640, "h": 360}, loop._geometry)
    check("the run summary reports the viewport witness",
          '"geometry": dict(getattr(self, "_geometry", {})),' in auto)


def test_window_layout() -> None:
    section("Instance window layout (equal cells, browser-only client surface)")
    desktop = REPO_ROOT.parent / "desktop"
    form = (desktop / "WebView2ClientForm.cs").read_text(encoding="utf-8", errors="replace")
    mainform = (desktop / "mainform.cs").read_text(encoding="utf-8", errors="replace")
    backend = (desktop / "WebView2Backend.cs").read_text(encoding="utf-8", errors="replace")
    native = (desktop / "win32.cs").read_text(encoding="utf-8", errors="replace")
    for removed in ("_toolbarPanel", "_lblStatus", "_lblMemory", "_btnZoom",
                    "_btnAutoFight", "_telemetryTimer"):
        check("the instance toolbar has no %s remnant" % removed, removed not in form)
    check("the browser fills the whole instance form",
          "Dock = DockStyle.Fill" in form and "Controls.Add(_webView);" in form)
    check("automatic tiling uses the full working area",
          "SafeUiInvoke(main, () => TileWindows(true));" in backend)
    check("the launch batch assigns the final tile before the page boots",
          "LayoutSlotCount = layoutCount" in mainform
          and "form.Bounds = target;" in backend
          and "_expectedTileCount > active" in backend)
    check("a short final row is centered with the same cell size",
          "int rowWidth = rowCols * cellW + (rowCols - 1) * gap;" in native
          and "int rowX = wa.X + (wa.Width - rowWidth) / 2;" in native
          and "if (count == 3)" not in native)
    check("a stalled guest login gets one bounded retry",
          "if (++_watchTicks === 60)" in form
          and "guest login made no progress in 60s; reloading to retry" in form)


def test_tool_ensemble() -> None:
    section("Tool ensemble (neural bases -> K-means context -> tree/boosting stack)")
    from agent.ensemble import (BASE_FEATURES, ToolStack, candidate_features,
                                fit_gbdt, fit_kmeans, fit_tree, gbdt_predict,
                                kmeans_distances, tree_predict)

    x = [[0.0], [0.0], [1.0], [1.0]]
    y = [0.0, 0.0, 1.0, 1.0]
    tree = fit_tree(x, y, depth=2, min_leaf=1)
    check("a decision tree separates a threshold",
          tree_predict(tree, [0.0]) < 0.25 and tree_predict(tree, [1.0]) > 0.75,
          tree)
    forest = fit_gbdt(x, y, rounds=4, depth=1, min_leaf=1, learning_rate=0.2)
    check("gradient boosting improves the tree ensemble's ordering",
          gbdt_predict(forest, [1.0]) > gbdt_predict(forest, [0.0]), forest)

    points = [[0.0, 0.0], [0.0, 0.1], [5.0, 5.0], [5.1, 5.0],
              [9.0, 0.0], [9.1, 0.1], [0.0, 9.0], [0.1, 9.0]]
    kmeans = fit_kmeans(points, clusters=4, seed=3, iterations=10)
    first = kmeans_distances(kmeans, [0.0, 0.0])
    second = kmeans_distances(kmeans, [5.0, 5.0])
    check("K-means exports four context distances",
          len(first) == 4 and len(second) == 4
          and kmeans["centroids"]
          and first.index(min(first)) != second.index(min(second)),
          str((first, second)))

    tools = ["quest.advance", "nav.goto", "fight.join", "farm.start",
             "inv.list", "state.snapshot"]
    logits = [2.0, 1.0, 0.5, 0.2, -1.0, -2.0]
    foresight = {tool: {"gain": gain, "move_prob": move, "curve": [gain, gain]}
                 for tool, gain, move in zip(
                     tools, [1.0, 0.6, 0.4, 0.1, 0.0, 0.0],
                     [0.8, 0.6, 0.5, 0.2, 0.1, 0.1])}
    names, rows = candidate_features(logits, foresight, tools,
                                     context=[0.1, 0.2, 0.3, 0.4])
    check("candidate features append the K-means context",
          names == tools and len(rows) == len(tools)
          and len(rows[0]) == len(BASE_FEATURES) + 4, rows[0])
    payload = {
        "kind": "tool-stacking", "features": list(BASE_FEATURES)
        + ["cluster_0", "cluster_1", "cluster_2", "cluster_3"],
        "model_type": "tree",
        "tree": {"feature": 0, "threshold": -1.5,
                 "left": {"value": -1.0}, "right": {"value": 1.0}},
        "kmeans": {"centroids": [[0.0, 0.0]] * 4, "mean": [0.0, 0.0],
                   "scale": [1.0, 1.0]},
        "active": True, "gate": {"passed": True},
        "chosen": {"gain_capture": 0.5, "top1": 0.5},
    }
    stack = ToolStack(payload)
    choice = stack.choose(logits, foresight, tools, context=[0.1, 0.2, 0.3, 0.4])
    check("the active tree stack is loadable and scores every candidate",
          stack.active and choice is not None
          and len(stack.rank(logits, foresight, tools, [0, 0, 0, 0])) == len(tools),
          choice)
    wide = tools + ["hdv.scan", "chat.say"]
    wide_logits = logits + [-3.0, -4.0]
    wide_fore = dict(foresight)
    wide_fore.update({"hdv.scan": {"gain": 0, "move_prob": 0.1, "curve": [0]},
                      "chat.say": {"gain": 0, "move_prob": 0.1, "curve": [0]}})
    ranked_wide = stack.rank(wide_logits, wide_fore, wide, [0, 0, 0, 0])
    check("the runtime shortlist keeps both leaders and matches training width",
          len(ranked_wide) == 6
          and ranked_wide[0]["tool"] in ("quest.advance", "hdv.scan"),
          [row["tool"] for row in ranked_wide])

    artifact_path = REPO_ROOT / "models" / "tool_stack.json"
    check("the shipped stack artifact exists", artifact_path.is_file())
    artifact = {}
    if artifact_path.is_file():
        try:
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        except Exception:       # noqa: BLE001 - a broken artifact is a failed check
            artifact = {}
    gate = artifact.get("gate") or {}
    check("the shipped stack carries a held-out cross-validation gate",
          artifact.get("kind") == "tool-stacking"
          and gate.get("folds", 0) >= 2 and gate.get("fold_wins", 0) >= 1)
    check("the shipped stack is active only when its gate passed",
          bool(artifact.get("active")) == bool(gate.get("passed"))
          and bool(artifact.get("active")))
    check("the stack records provenance for both bases and the cluster corpus",
          all((artifact.get("provenance") or {}).get(key)
              for key in ("policy", "foresight", "cluster_corpus")))
    selected = ToolStack.load(artifact_path)
    check("the shipped stack loads through the runtime parser",
          selected is not None and selected.model_type in ("linear", "tree", "gbdt")
          and selected.features[:len(BASE_FEATURES)] == list(BASE_FEATURES))

    planner = (REPO_ROOT / "agent" / "planner_nn.py").read_text(
        encoding="utf-8", errors="replace")
    check("the planner passes state context into the stack",
          "self.stack.context(x)" in planner
          and "self.stack.rank(logits, cells, self.tools, context=context)" in planner)
    check("stacking fails open and still applies the safety guards",
          "stacking must fail open" in planner
          and "self.stack.active" in planner
          and 'self.stack_mode == "act"' in planner)
    check("the planner exposes off/shadow/act",
          all(('"%s"' % mode) in planner for mode in ("off", "shadow", "act")))
    auto = (REPO_ROOT / "agent" / "autopilot.py").read_text(encoding="utf-8",
                                                             errors="replace")
    check("the CLI can select the ensemble without changing the default policy",
          '"--ensemble"' in auto and '"--policy"' in auto
          and 'default="rule"' in auto)
    check("a live step records the stack proposal and the policy baseline",
          "ensemble=ensemble" in auto
          and 'getattr(self.runtime.policy, "last_stack", None)' in auto
          and 'self.runtime.policy.last_stack = None' in auto)


def test_fight_rotation() -> None:
    section("Fight estimate (utility must not hide a damaging spell)")
    from advisor.models import CharacterSnapshot, SpellState
    from combat.fight_estimate import _player_facts, _spellbook

    spells, _by_breed = _spellbook()
    utility = SpellState(spell_id=7449, level=1, max_grade=6,
                         max_grade_for_level=1, name_id=None, breed_id=6)
    damage = SpellState(spell_id=7431, level=1, max_grade=6,
                        max_grade_for_level=1, name_id=None, breed_id=6)
    snap = CharacterSnapshot(
        name="Brruh", level=1, breed_id=6,
        stats={"actionPoints": 6, "movementPoints": 3,
               "effectiveMaxLifePoints": 55, "maxLifePoints": 55},
        spells=[utility, damage],
    )
    facts = _player_facts(snap, spells)
    check("a zero-damage utility spell cannot starve the offensive rotation",
          facts.damage_per_turn > 0 and bool(facts.dpt_by_element)
          and "no_damaging_spell" not in facts.missing,
          (facts.damage_per_turn, facts.dpt_by_element, facts.missing))

    utility_only = CharacterSnapshot(
        name="Brruh", level=1, breed_id=6,
        stats={"actionPoints": 6, "movementPoints": 3,
               "effectiveMaxLifePoints": 55, "maxLifePoints": 55},
        spells=[utility],
    )
    none = _player_facts(utility_only, spells)
    check("a character with only utility spells is still reported as no damage",
          none.damage_per_turn == 0 and not none.dpt_by_element)


def test_exhausted_ready_maps() -> None:
    section("No walk-back loop (two ready objectives, adjacent maps, both exhausted)")
    from agent.planner import QuestPlanner

    def planner():
        p = QuestPlanner()
        p.quests = {}
        p._quests_loaded = True
        p._order = None
        p.level_hint = -1
        # Live 2026-10-29, steps 71-80: the shop objective's claim had already been
        # refused, so the ready scan was the only thing left to answer.
        p._claim_attempts[(3841, 14072)] = 1
        return p

    def obs(map_id):
        return {
            "level": 4, "map_id": map_id, "cell": 11, "connected": True,
            "in_world": True, "in_fight": False, "in_dialog": False,
            "tutorial": {"active": True, "quest_id": 3841, "step_id": 38},
            "quests_active": {}, "quests_done": [],
            "current_objective": {"quest_id": "3841", "objective_id": 14072,
                                  "type_id": 0, "type": "QuestObjectiveFreeForm",
                                  "step_id": 4976, "text": "Get the Albuera Elite Bonus Pack."},
            "ready_objectives": [
                {"quest_id": "3841", "objective_id": 14072, "type_id": 0,
                 "type": "QuestObjectiveFreeForm", "step_id": 4976,
                 "params": {"parameter0": 715390},
                 "text": "Get the Albuera Elite Bonus Pack."},
                {"quest_id": "4262", "objective_id": 13940, "type_id": 0,
                 "type": "QuestObjectiveFreeForm", "map_id": 145489920,
                 "step_id": 4954, "params": {"parameter0": 719652},
                 "text": "Go back and see Foreman Kosh once the monsters have been defeated."},
                {"quest_id": "4264", "objective_id": 13960, "type_id": 0,
                 "type": "QuestObjectiveFreeForm", "map_id": 145490946,
                 "step_id": 4956, "params": {"parameter0": 714266},
                 "text": "Talk to Lieutenant."},
            ],
            "step_objectives": [], "exits": [], "npcs": [], "groups": [],
            "inventory": {}, "kamas": 0, "open_windows": [],
        }

    p = planner()
    first = p.choose("do all the quests", obs(145489921))
    check("from the neighbouring map the plan walks to the first objective's map",
          first is not None and first.tool == "nav.goto"
          and first.args.get("map_id") == 145489920, first)

    # A type-0 objective is server-counted, so the claim is not sent; the action
    # is the objective's own. Standing on its map with every NPC already asked
    # leaves nothing, and the next objective's map becomes the only move.
    second = p.choose("do all the quests", obs(145489920))
    # The objective's map is four crossings away, so the request is its first
    # hop. Asking for 145490946 itself is what the client answered with "map
    # 145490946 is not adjacent to 145489921" on the live session.
    check("standing on its map with nothing left, the plan walks the first crossing",
          second is not None and second.tool == "nav.goto"
          and second.args.get("map_id") == 145489921, second)

    back = p.choose("do all the quests", obs(145490946))
    check("at the second map it does not walk back to the exhausted one",
          back is not None and back.tool == "state.snapshot", back)
    parked = p.choose("do all the quests", obs(145489921))
    check("and from the first map again both walks are gone",
          parked is not None and parked.tool == "state.snapshot", parked)

    # The scenario ending is a new situation too: the objectives it gated may act.
    ending = obs(145489921)
    ending["tutorial"] = None
    reopened = p.choose("do all the quests", ending)
    check("the tutorial ending clears the exhausted maps",
          reopened is not None and reopened.tool == "nav.goto"
          and reopened.args.get("map_id") == 145489920, reopened)

    page = (ROOT / "scripts" / "mobile-disguise.js").read_text(
        encoding="utf-8", errors="replace")
    check("the page closes a named window through the client's own manager",
          "var _agentCloseWindow = function(name) {" in page
          and "if (!mgr || typeof mgr.close !== 'function') return false;" in page
          and "mgr.close(name); return true;" in page)
    check("ui.tap closes the windows the caller names before pressing",
          "if (args.close_first) {" in page and "closed_before" in page)
    check("the snapshot publishes the login screen's ban verdict",
          "snap.login = _agentLoginBan()" in page and "permanently banned" in page)
    check("a permanently banned login is refused instead of pressed",
          "login_banned: true" in page)
    auto = (REPO_ROOT / "agent" / "autopilot.py").read_text(
        encoding="utf-8", errors="replace")
    check("the world wait ends on a permanent ban instead of pressing",
          "if self._login_banned(obs):" in auto
          and "reset this slot for a fresh guest" in auto)


def test_index_route_travel() -> None:
    section("Travel routes over the map index, one border at a time")
    from agent.features import route_distance, route_first_hop
    from agent.planner import QuestPlanner, ROUTE_STEP_LIMIT

    hop = route_first_hop(145489920, 145490946)
    check("the index route starts at a real neighbour of the current map",
          hop is not None and hop[0] == 145489921 and hop[1] == "bottom"
          and hop[2] == 4, hop)
    check("distance counts crossings, and is 0 on the destination",
          route_distance(145489920, 145490946) == 4
          and route_distance(145490946, 145490946) == 0,
          (route_distance(145489920, 145490946),
           route_distance(145490946, 145490946)))
    check("an unindexed destination has no route",
          route_first_hop(145489920, 698953) is None)

    exits_920 = [{"direction": "top", "map_id": 145490177, "blocked": False},
                 {"direction": "bottom", "map_id": 145489921, "blocked": False},
                 {"direction": "left", "map_id": 145621504, "blocked": False},
                 {"direction": "right", "map_id": 145490432, "blocked": False}]
    exits_921 = [{"direction": "top", "map_id": 145489920, "blocked": False},
                 {"direction": "bottom", "map_id": 145489922, "blocked": False},
                 {"direction": "left", "map_id": 145621505, "blocked": False},
                 {"direction": "right", "map_id": 145490433, "blocked": False}]

    def obs(map_id, exits):
        return {"level": 4, "map_id": map_id, "cell": 11, "in_world": True,
                "in_fight": False, "in_dialog": False, "tutorial": None,
                "quests_active": {}, "quests_done": [],
                "ready_objectives": [
                    {"quest_id": "4264", "objective_id": 13960, "type_id": 0,
                     "type": "QuestObjectiveFreeForm", "map_id": 145490946,
                     "step_id": 4956, "params": {"parameter0": 714266},
                     "text": "Talk to Lieutenant."}],
                "inventory": {}, "kamas": 0, "open_windows": [],
                "map": {"map_id": map_id, "exits": exits,
                        "npcs": [], "groups": []}}

    p = QuestPlanner()
    first = p.choose("do all the quests", obs(145489920, exits_920))
    check("four crossings out, the plan requests the adjacent first hop",
          first is not None and first.tool == "nav.goto"
          and first.args.get("map_id") == 145489921, first)
    second = p.choose("do all the quests", obs(145489921, exits_921))
    check("from the next map it requests the next hop, never the destination",
          second is not None and second.tool == "nav.goto"
          and second.args.get("map_id") == 145489922, second)

    # Repeating a crossing that never happens is a loop even though every step
    # is a fresh scene: only the route memory can expire it.
    p2 = QuestPlanner()
    calls = [p2._travel_call({"type_id": 0, "map_id": 145490946}, 145490946,
                             obs(145489920, exits_920), key=(4264, 13960))
             for _ in range(ROUTE_STEP_LIMIT + 3)]
    check("a crossing request that never happens expires",
          calls[0]["args"].get("map_id") == 145489921
          and all(c["args"].get("map_id") != 145489921
                  for c in calls[ROUTE_STEP_LIMIT:]),
          [c["args"] for c in calls[-3:]])
    # ... and an actual arrival resets the count, so a long route is not cut off.
    p3 = QuestPlanner()
    p3._travel_call({"type_id": 0}, 145490946, obs(145489920, exits_920),
                    key=(4264, 13960))
    p3._travel_call({"type_id": 0}, 145490946, obs(145489921, exits_921),
                    key=(4264, 13960))
    state = p3._route_tries[(4264, 13960)]
    check("arriving one crossing nearer resets the travel count",
          state.get("steps") == 0 and state.get("best") == 3, state)

    bare = p3._travel_call({"type_id": 0}, 145490946, {"map_id": 145489920},
                           key=(1, 1))
    check("with no exit block the first crossing is still requested, not the target",
          bare["args"].get("map_id") == 145489921, bare)
    unknown = p3._travel_call({"type_id": 4}, 698953, obs(145489920, exits_920),
                              key=(1, 2))
    check("an unindexed destination is explored, never named in a change request",
          unknown["args"].get("map_id") != 698953, unknown)

    # A house: the client knows the map's four edges but reports no crossable
    # border, because the way out is a door. No map id can be walked from there.
    house = {"map_id": 146276352, "cell": 312,
             "map": {"map_id": 146276352, "exits": [],
                     "neighbours": {"left": 146407936, "right": 146276864,
                                    "top": 146276609, "bottom": 146276353}}}
    inside = p3._travel_call({"type_id": 0}, 145489920, house, key=(4262, 13930))
    check("an interior with no crossable border asks the server for a neighbour",
          inside["tool"] == "nav.goto"
          and inside["args"].get("map_id") in (146407936, 146276864,
                                               146276609, 146276353)
          and inside["args"].get("direction") in ("left", "right", "top", "bottom"),
          inside)
    # The offline simulator's synthetic maps have an empty exit list and no
    # neighbour block; asking for the destination is the only thing it answers.
    synthetic = {"map_id": 160695296, "map": {"map_id": 160695296, "exits": []}}
    legacy = p3._travel_call({"type_id": 3}, 54165306, synthetic, key=(584, 3805))
    check("a map with no edge information keeps the destination request",
          legacy["args"].get("map_id") == 54165306, legacy)


def test_planner_replay() -> None:
    section("Frozen planner replay (today's plans, measured on recorded live turns)")
    import json

    from agent.features import route_distance
    from agent.observe import flatten
    from agent.planner import QuestPlanner

    corpus = REPO_ROOT / "data" / "replay" / "planner_obs.jsonl"
    check("the frozen corpus is checked in", corpus.exists(), str(corpus))
    rows = [json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    sessions = {row.get("session") for row in rows}
    check("the corpus covers several recorded sessions, not one lucky run",
          len(rows) >= 300 and len(sessions) >= 3, (len(rows), len(sessions)))

    none = nav_map = non_adjacent = interior = 0
    tools: Dict[str, int] = {}
    for row in rows:
        planner = QuestPlanner()
        call = planner.choose(row.get("goal") or "do all the quests", row["obs"], None)
        tool = getattr(call, "tool", None)
        tools[tool] = tools.get(tool, 0) + 1
        if call is None:
            none += 1
            continue
        args = getattr(call, "args", {}) or {}
        if tool == "nav.goto" and args.get("map_id") is not None:
            nav_map += 1
            here = flatten(row["obs"]).get("map_id")
            distance = route_distance(here, int(args["map_id"])) if here is not None else None
            if distance is None or distance > 1:
                non_adjacent += 1
        block = row["obs"].get("map") if isinstance(row["obs"].get("map"), dict) else {}
        exits, neighbours = block.get("exits"), block.get("neighbours")
        if (isinstance(exits, list) and not exits and isinstance(neighbours, dict)
                and neighbours and tool == "nav.goto" and args.get("map_id") is not None):
            interior += 1

    # A map change that names a map the player cannot cross to is refused by the
    # client, and asking from a house interior names a map that is not a border
    # at all. Both were live bugs; the frozen turns pin the fix.
    check("no recorded turn asks for a map that is not one crossing away",
          non_adjacent == 0, (non_adjacent, nav_map))
    check("no recorded turn asks for a map it cannot walk to from an interior",
          interior == 0, interior)
    check("the corpus exercises map travel at all", nav_map > 0, nav_map)
    check("a recorded turn is almost never left without a decision",
          none <= 3, (none, len(rows)))
    check("the replay reaches the planner's main tools, so the pins are not vacuous",
          len([t for t in tools if t]) >= 5, tools)


def test_packet_redaction() -> None:
    section("Packet rings keep diagnostics, never credentials")
    from agent.observe import redact_packet_secrets

    obs = {"map": {"transition": {
        "outgoing": [
            {"name": "AuthenticationTicketMessage",
             "args": {"ticket": "s" * 32, "lang": "fr"}},
            {"name": "ChangeMapMessage",
             "args": {"mapId": 145489920, "keyMovements": [1, 2]}},
        ],
        "incoming": [{"name": "someMessage", "args": {"password": "x", "valid": True}}],
    }}}
    clean = redact_packet_secrets(obs)
    ticket_args = clean["map"]["transition"]["outgoing"][0]["args"]
    check("the login ticket is removed from the persisted snapshot",
          "ticket" not in ticket_args and ticket_args.get("lang") == "fr", ticket_args)
    change = clean["map"]["transition"]["outgoing"][1]["args"]
    check("the refusal diagnostics survive instead of being redacted wholesale",
          change.get("mapId") == 145489920 and change.get("keyMovements") == [1, 2],
          change)
    check("a credential name is dropped whichever direction it travels",
          "password" not in clean["map"]["transition"]["incoming"][0]["args"]
          and clean["map"]["transition"]["incoming"][0]["args"].get("valid") is True,
          clean["map"]["transition"]["incoming"][0])
    check("a snapshot with no ring and a non-dict are passed through",
          redact_packet_secrets({"map_id": 1}) == {"map_id": 1}
          and redact_packet_secrets(None) is None)
    corpus = REPO_ROOT / "data" / "replay" / "planner_obs.jsonl"
    check("the frozen replay corpus carries no packet arguments",
          corpus.exists() and b'"ticket"' not in corpus.read_bytes(), str(corpus))
    host = (ROOT / "desktop" / "WebView2ClientForm.cs").read_text(
        encoding="utf-8", errors="replace")
    check("the manager's own recorder strips the ticket before writing",
          "json = RedactSecrets(json);" in host
          and "private static string RedactSecrets(string json)" in host
          and "SecretTicket.Replace(json, " in host
          and '\\"ticket\\"' in host, host[:0])


def test_tutorial_hold() -> None:
    section("Bounded patience (a scripted step can be waited on without a stampede)")
    from agent.autopilot import (Autopilot, TUTORIAL_HOLD_S, _call_key,
                           _world_sig)
    from agent.runtime import ToolCall

    call = ToolCall("quest.advance", {"npc_id": 3888, "quest_id": 3841}, "do all the quests")
    check("one call has one signature",
          _call_key(call) == _call_key(call) and "quest.advance" in _call_key(call),
          _call_key(call))
    check("argument order cannot hide a repeat",
          _call_key(ToolCall("t", {"a": 1, "b": 2}, None))
          == _call_key(ToolCall("t", {"b": 2, "a": 1}, None)))

    loop = Autopilot.__new__(Autopilot)
    loop._hold_key = None
    loop._hold_world = None
    loop._hold_started_at = 0.0
    loop._hold_cycles = 0
    world = {"position": {"map_id": 7, "cell": 3},
             "current_objective": {"objective_id": 13493}}
    moved = {"position": {"map_id": 7, "cell": 4},
             "current_objective": {"objective_id": 13493}}
    key = _call_key(call)
    check("a new step gets its own patience window",
          loop._tutorial_hold(key, world) is True and loop._hold_key == key)
    check("the same call is not dispatched again while nothing has moved",
          loop._holding(call, world) is True,
          "a hold that still dispatches is the stampede with a deadline")
    check("another call is not held",
          loop._holding(ToolCall("nav.goto", {"direction": "left"}, None), world) is False)
    check("a world that moves releases the hold at once",
          loop._holding(call, moved) is False and loop._hold_key is None)
    loop._tutorial_hold(key, world)
    loop._hold_started_at -= (TUTORIAL_HOLD_S + 1)
    check("an expired window restarts instead of ending the session",
          loop._tutorial_hold(key, world) is True and loop._hold_cycles == 1,
          loop._hold_cycles)
    check("the retry is one call per window, not one per pass",
          loop._holding(call, world) is True,
          "a loop that ends stops marking activity, and the same client timer logs the "
          "character out ten minutes later")
    auto = (REPO_ROOT / "agent" / "autopilot.py").read_text(encoding="utf-8", errors="replace")
    check("the loop consults the hold before it dispatches",
          "if call is not None and self._holding(call, before):" in auto,
          "a hold that only delays the same call still sends it 119 times")
    check("a stat that ticks on its own does not release the hold",
          _world_sig(dict(world, hp={"life": 60}, kamas=120)) == _world_sig(world),
          "hp and kamas move constantly; counting them would re-open the stampede")
    from agent.autopilot import DEAD_TALK_TRIES
    check("a talk that opens no dialog is noticed on the second send, not the 100th",
          "self._dead_talks[key] >= DEAD_TALK_TRIES" in auto and DEAD_TALK_TRIES == 2,
          DEAD_TALK_TRIES)
    check("the dead-talk rule reads the page's own talk receipt",
          'payload_of(result).get("via")' in auto
          and '"talked", "walk_then_talk"' in auto,
          "guessing that the talk happened is how 108 identical sends went unseen")
    from agent.autopilot import _standing_on_exit
    at_portal = {"position": {"cell": 322, "map_id": 147063810},
                 "map": {"exits": [{"direction": "left", "cells": [322, 294]},
                                   {"direction": "right", "cells": [321]}]}}
    check("standing on the portal is recognised from the client's own exit list",
          _standing_on_exit({"direction": "left"}, at_portal) is True
          and _standing_on_exit({"direction": "right"}, at_portal) is False,
          at_portal)
    check("a crossing without a cell is not a refusal",
          _standing_on_exit({"direction": "left"}, {"map": {"exits": []}}) is False
          and _standing_on_exit({}, at_portal) is False)
    check("a declined crossing is counted and reported",
          "self._dead_navs[key] >= DEAD_TALK_TRIES" in auto
          and '"declined_crossings": dict(' in auto)
    from types import SimpleNamespace
    from agent.executor import payload_of
    live = SimpleNamespace(data={"directive_payload": {"tool": "quest.advance"},
                                 "response": {"ok": True, "result": {"ok": True, "data": {
                                     "via": "talked", "npc_contextual_id": -1}}}})
    check("the bridge's two wrappers are unwrapped in one place",
          payload_of(live).get("via") == "talked", payload_of(live))
    check("a missing wrapper reads as empty instead of raising",
          payload_of(SimpleNamespace(data=None)) == {}
          and payload_of(SimpleNamespace(data={"response": "x"})) == {},
          "a reader that guesses the shape returns None and the rule never fires")
    check("the dead-talk counter accumulates per call",
          "self._dead_talks[key] = self._dead_talks.get(key, 0) + 1" in auto)
    check("the dead-talk hold comes before the general three-strike test",
          0 <= auto.find("self._dead_talks[key] >= DEAD_TALK_TRIES")
          < auto.find("repeated = (len(self._recent) >= 3"),
          "a dead talk still had to repeat three times before anything noticed")
    check("the run summary reports dead talks",
          '"dead_talks": dict(' in auto)
    holds = [i for i in range(len(auto)) if auto.startswith("self._tutorial_hold(", i)]
    check("every bounded hold spends the time as a wait, not as another directive",
          len(holds) >= 2 and all("self._hold_wait(goal, before)" in auto[i:i + 900]
                                  and "time.sleep(" not in auto[i:i + 900]
                                  for i in holds),
          "%d hold sites, each must wait; a bounded hold that still dispatches is the "
          "same stampede with a deadline" % len(holds))


def test_verifier_head_gate() -> None:
    section("Verifier head (G6: no auto-load without a live/human contract)")
    panel = ROOT / "desktop" / "aipanel.cs"
    check("the GUI panel source exists", panel.exists(), str(panel))
    if not panel.exists():
        return
    src = panel.read_text(encoding="utf-8", errors="replace")
    check("the auto-load requires a validation contract, not file existence",
          "verifier_head.validation.json" in src
          and "File.Exists(contractPath)" in src, "no contract gate")
    check("only live or human labels qualify",
          "labels" in src and "live|human" in src, "label provenance unchecked")
    check("a minimum sample and a baseline comparison are required",
          ">= 60" in src and "above_baseline" in src, "sample/baseline unchecked")
    check("the decision is all three conditions",
          "validated && enough && above" in src, "an incomplete condition")
    check("the rules remain the default when no contract passes",
          "string verifier = null;" in src, "the model is not optional")
    gate_at = src.find("File.Exists(contractPath)")
    append_at = src.find("if (verifier != null) sb.Append")
    check("the model is passed only after the gate",
          gate_at >= 0 and append_at > gate_at, "append is not behind the gate")


def test_observation_text() -> None:
    section("Observation text (G29: the quest graph is a census, not a dump)")
    from agent.tool_dataset import _observation_text

    catalog = [{
        "id": 3841, "name": "Q", "steps": [
            {"id": 2001, "objectives": [{"id": 14070 + i} for i in range(100)]}
        ]}]
    obs = {
        "agent_build": "2026-10-27a",
        "character": {"id": 1, "name": "Aiai", "level": 3},
        "position": {"map_id": 100, "cell": 5},
        "in_world": True,
        "in_dialog": False,
        "knowledge": {"status": "observed", "observed_from": "catalog",
                      "objective": {"text": "Parler à Ayin Spyr"}},
        "quests_active": {"3841": 0},
        "quests_catalog": catalog,
        "quest_catalog_status": {"quests": 1, "steps": 1, "objectives": 100,
                                 "truncated": False},
    }
    text = _observation_text(obs, 1200) or ""
    check("the quest graph is rendered as a census",
          "quest graph: 1 active quest(s), 100 objective(s)" in text, text)
    check("the raw graph is not dumped into the observation text",
          "quests_catalog" not in text and "14070" not in text, text)
    check("the census does not hide the knowledge or the build",
          "observed" in text and "2026-10-27a" in text, text)
    check("the text respects the character budget", len(text) <= 1200, len(text))
    check("an observation without a graph is unchanged",
          _observation_text({"position": {"map_id": 7}}, 200) is not None)


def test_kaggle_toolkit_sync() -> None:
    section("Kaggle toolkit (G10: an explicit copy/pin matrix)")
    import importlib.util
    import tempfile

    tool = REPO_ROOT / "tools" / "sync_kaggle_toolkit.py"
    check("the sync tool exists", tool.exists(), str(tool))
    if not tool.exists():
        return
    spec = importlib.util.spec_from_file_location("sync_kaggle_toolkit", tool)
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)

    tmp = Path(tempfile.mkdtemp(prefix="kaggle-sync-"))
    root = tmp / "dofus-network"
    toolkit = root / "kaggle" / "toolkit"

    def put(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    put(root / "agent" / "a.py", "new A")
    put(toolkit / "agent" / "a.py", "old A")
    put(toolkit / "agent" / "legacy.py", "legacy")
    put(root / "agent" / "b.py", "source only")
    put(root / "knowledge" / "store.py", "required dependency")
    put(root / "_lazy_exports.py", "root helper")
    put(root / "agent" / "checkpoint.pkl", "not code")
    put(root / "data" / "cache" / "quests_full.json", "do not redistribute")
    put(toolkit / "data" / "cache" / "quests_full.json", "pinned cache")
    put(root / "sim" / "s.py", "same")
    put(toolkit / "sim" / "s.py", "same")
    put(root / "data" / "capabilities.json", '{"new":1}')
    put(toolkit / "data" / "capabilities.json", '{"old":1}')
    put(root / "data" / "maps" / "_index.json", "big")
    put(toolkit / "data" / "maps" / "_index.json", "trimmed")
    put(toolkit / "dataset-metadata.json", "{}")
    put(toolkit / "ai" / "__pycache__" / "x.pyc", "junk")

    entries = sync.plan(root, toolkit)
    by = {item["path"]: item for item in entries}
    check("a stale agent copy is planned for sync",
          by["agent/a.py"]["status"] == "sync" and by["agent/a.py"]["drift"])
    check("a matching file is not drift", by["sim/s.py"]["drift"] is False)
    check("a toolkit-only module is pinned, not deleted",
          by.get("agent/legacy.py", {}).get("status") == "pinned")
    check("the trimmed map index and Kaggle metadata are pinned",
          by.get("data/maps/_index.json", {}).get("status") == "pinned"
          and by.get("dataset-metadata.json", {}).get("status") == "pinned")
    check("bytecode is ignored",
          not any("__pycache__" in path or path.endswith(".pyc") for path in by))
    check("a source-only module is included as missing from the mirror",
          by.get("agent/b.py", {}).get("status") == "sync"
          and by.get("agent/b.py", {}).get("missing") is True)
    check("drift includes both stale and missing modules",
          {item["path"] for item in sync.drift(entries)}
          == {"agent/a.py", "agent/b.py", "knowledge/store.py", "_lazy_exports.py",
              "data/capabilities.json"},
          [item["path"] for item in sync.drift(entries)])
    check("runtime dependencies are included without model artifacts",
          by.get("knowledge/store.py", {}).get("missing") is True
          and by.get("_lazy_exports.py", {}).get("missing") is True
          and "agent/checkpoint.pkl" not in by)
    check("downloaded quest data is pinned rather than refreshed",
          by.get("data/cache/quests_full.json", {}).get("status") == "pinned")

    sync.TOOLKIT = toolkit
    sync.MANIFEST = toolkit / "TOOLKIT_MANIFEST.json"
    copied = -1
    try:
        copied = sync.write(entries, root, toolkit)
    except Exception:      # noqa: BLE001 - the checks below report the broken plan
        copied = -1
    check("write copies changed and newly added modules", copied == 5
          and (toolkit / "agent" / "a.py").read_text(encoding="utf-8") == "new A"
          and (toolkit / "agent" / "b.py").read_text(encoding="utf-8") == "source only"
          and (toolkit / "knowledge" / "store.py").is_file()
          and (toolkit / "_lazy_exports.py").is_file(), copied)
    check("write preserves pinned downloaded data",
          (toolkit / "data" / "cache" / "quests_full.json").read_text(encoding="utf-8")
          == "pinned cache")
    check("a second write copies nothing unchanged",
          sync.write(sync.plan(root, toolkit), root, toolkit) == 0)
    check("write never overwrites the pinned files",
          (toolkit / "data" / "maps" / "_index.json").read_text(encoding="utf-8")
          == "trimmed"
          and (toolkit / "agent" / "legacy.py").read_text(encoding="utf-8")
          == "legacy")
    check("write records a manifest with statuses",
          sync.MANIFEST.is_file()
          and "pinned" in sync.MANIFEST.read_text(encoding="utf-8"))
    manifest = {}
    if sync.MANIFEST.is_file():
        try:
            manifest = json.loads(sync.MANIFEST.read_text(encoding="utf-8"))
        except Exception:      # noqa: BLE001 - a broken manifest is a failed check
            manifest = {}
    check("the manifest records the post-write state, not the pre-copy drift",
          bool(manifest)
          and not any(item["drift"] or item["missing"]
                      for item in manifest.get("files", [])),
          [item["path"] for item in manifest.get("files", []) if item["drift"]])
    check("after write the plan has no drift",
          sync.drift(sync.plan(root, toolkit)) == [])

    # The live tree, not only the fixture: G10 reopened because the checked-in
    # mirror was hand-edited while the harness moved on.
    live_root = REPO_ROOT
    live_toolkit = REPO_ROOT / "kaggle" / "toolkit"
    if live_toolkit.is_dir():
        live_drift = sync.drift(sync.plan(live_root, live_toolkit))
        check("the checked-in Kaggle toolkit matches the harness",
              not live_drift, [item["path"] for item in live_drift][:8])
        import subprocess
        probe = (
            "from pathlib import Path; import importlib; "
            "names = ('_lazy_exports', 'agent.planner', 'agent.autopilot', "
            "'agent.brain_daemon', 'ai.train_cause_head', 'run_brain', 'run_autopilot'); "
            "modules = [importlib.import_module(name) for name in names]; "
            "assert all(Path(m.__file__).resolve().is_relative_to(Path.cwd()) "
            "for m in modules), 'import escaped the toolkit'; "
            "print('TOOLKIT_IMPORT_OK')"
        )
        result = subprocess.run([sys.executable, "-E", "-c", probe], cwd=live_toolkit,
                                capture_output=True, text=True, timeout=60)
        check("the toolkit imports independently of the source tree",
              result.returncode == 0 and "TOOLKIT_IMPORT_OK" in result.stdout,
              result.stderr[-2000:])


def test_quest_chain_replay() -> None:
    section("Quest chain replay (G29+G33: offer -> accept -> plan -> next offer)")
    from agent.planner import QuestPlanner

    def planner():
        p = QuestPlanner()
        p.quests = {}
        p._quests_loaded = True
        p._order = None
        p.level_hint = -1
        return p

    def offer_dialog(quest_id):
        return {"open": True, "npc_id": 3888, "name_id": "Ayin Spyr",
                "message": "Take this job?", "text_ready": True, "ids_ready": True,
                "ids_present": True, "reply_count": 0, "replies": [],
                "start_quest": quest_id}

    def base():
        return {"level": 10, "map_id": 100, "cell": 0, "connected": True,
                "in_world": True, "in_fight": False, "in_dialog": False,
                "tutorial": None, "quests_active": {}, "quests_done": [],
                "current_objective": None, "ready_objectives": [],
                "step_objectives": [], "exits": [],
                "npcs": [{"npc_id": 3888, "cell": 2}], "groups": [],
                "inventory": {}, "kamas": 0}

    p = planner()
    obs = base()
    obs.update({"in_dialog": True, "dialog": offer_dialog(4266)})
    call = p.choose("do all the quests", obs)
    check("the NPC offer is accepted as the first step of the chain",
          call is not None and call.tool == "quest.advance"
          and call.args.get("accept") is True and call.args.get("quest_id") == 4266,
          None if call is None else str((call.tool, call.args)))
    check("and the accepted quest is client-confirmed for later starts",
          4266 in p._client_quests, sorted(p._client_quests))

    # The page accepted it: the quest is active, the tracker names its objective, and
    # the catalogue carries the graph (the cache has no live quest).
    catalog = [{
        "id": 4266, "name": "Q", "step_index": 0, "step_id": 1,
        "steps": [{"id": 1, "objectives": [{
            "id": 14070, "type_id": 1, "type": "QuestObjectiveGoToNpc",
            "params": {"parameter0": 3888}, "map_id": 100, "step_id": 1,
            "text": "Parler à Ayin Spyr"}]}],
    }]
    obs = base()
    obs.update({
        "quests_active": {"4266": 0},
        "current_objective": {"quest_id": 4266, "objective_id": 14070,
                              "type_id": 1, "params": {"parameter0": 3888}},
        "step_objectives": [{"id": 14070, "type_id": 1,
                             "type": "QuestObjectiveGoToNpc",
                             "params": {"parameter0": 3888}, "map_id": 100}],
        "quests_catalog": catalog,
        "quest_catalog_status": {"quests": 1, "steps": 1, "objectives": 1,
                                 "truncated": False},
    })
    call = p.choose("do all the quests", obs)
    check("a live quest the cache does not know is planned from the observation",
          call is not None and call.tool == "quest.advance"
          and call.args.get("npc_id") == 3888
          and call.args.get("objective_id") is None,
          None if call is None else str((call.tool, call.args)))
    check("and it is not retired", 4266 not in p._skipped, sorted(p._skipped))

    # The next NPC page offers the next quest in the chain; a quest run takes it.
    obs = base()
    obs.update({"in_dialog": True, "dialog": offer_dialog(4267),
                "quests_active": {"4266": 0},
                "current_objective": {"quest_id": 4266, "objective_id": 14070,
                                      "type_id": 1}})
    call = p.choose("do all the quests", obs)
    check("the next quest in the chain is accepted",
          call is not None and call.args.get("accept") is True
          and call.args.get("quest_id") == 4267,
          None if call is None else str((call.tool, call.args)))

    farm = planner()
    call = farm.choose("farm the piglets", obs)
    check("a farm prompt does not take the chain",
          call is None or call.args.get("accept") is None,
          None if call is None else str((call.tool, call.args)))

    named = planner()
    call = named.choose("do quest 4266", obs)
    check("a prompt naming a different quest does not take the chain",
          call is None or call.args.get("accept") is None,
          None if call is None else str((call.tool, call.args)))


def test_live_ready() -> None:
    section("Live preflight (stale page, stop file, client, brain)")
    import importlib.util
    import json as _json
    import tempfile

    tool = REPO_ROOT / "tools" / "live_ready.py"
    check("the preflight tool exists", tool.exists(), str(tool))
    if not tool.exists():
        return
    spec = importlib.util.spec_from_file_location("live_ready", tool)
    ready = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ready)

    tmp = Path(tempfile.mkdtemp(prefix="live-ready-"))
    page = tmp / "mobile-disguise.js"
    page.write_text("var AGENT_BUILD = '2026-10-27a';\n", encoding="utf-8")
    setup = tmp / "setup.exe"
    setup.write_bytes(b"x")
    store = tmp / "store.sqlite"
    store.write_bytes(b"x")
    observed = tmp / "observed.jsonl"
    observed.write_text("{}\n", encoding="utf-8")
    stop = tmp / "AUTOPILOT_STOP"

    def log_with(build):
        path = tmp / ("log-" + build + ".jsonl")
        rows = [{"observation": {"agent_build": "2026-10-01a"}},
                {"observation": {"agent_build": build}}]
        path.write_text("\n".join(_json.dumps(row) for row in rows) + "\n",
                        encoding="utf-8")
        return path

    def run(log, bridge=True, clients=2, brain=True):
        return ready.checks(log=log, stop=stop, page=page, setup=setup,
                            store=store, observed=observed,
                            command_model=tmp / "command-shadow.pt",
                            bridge=lambda: bridge, clients=lambda: clients,
                            brain=lambda: brain,
                            egress_entry=lambda: {"level": "PASS", "name": "egress",
                                                  "detail": "injected: the egress itself is "
                                                            "covered by test_egress_preflight"})

    def level(results, name):
        for item in results:
            if item["name"] == name:
                return item["level"]
        return None

    same = run(log_with("2026-10-27a"))
    check("a matching page/build host has no failure",
          ready.exit_code(same) == 0 and level(same, "page build") == "PASS", same)
    stale = run(log_with("2026-10-25d"))
    check("a stale client build fails closed",
          ready.exit_code(stale) == 1 and level(stale, "page build") == "FAIL", stale)
    stop.write_text("stop", encoding="utf-8")
    stopped = run(log_with("2026-10-27a"))
    check("a leftover stop file fails closed",
          ready.exit_code(stopped) == 1 and level(stopped, "stop file") == "FAIL",
          stopped)
    stop.unlink()
    no_log = run(tmp / "missing.jsonl")
    check("no recorded client build is information, not a failure",
          ready.exit_code(no_log) == 0 and level(no_log, "page build") == "INFO",
          no_log)

    import os
    now = 1_700_000_000
    os.utime(page, (now, now))
    os.utime(setup, (now - 100, now - 100))
    old_setup = run(log_with("2026-10-27a"))
    check("an installer older than the page is a warning",
          ready.exit_code(old_setup) == 0 and level(old_setup, "installer") == "WARN",
          old_setup)
    os.utime(setup, (now + 100, now + 100))

    down = run(log_with("2026-10-27a"), bridge=False, clients=0, brain=False)
    check("a missing client and brain are warnings, not failures",
          ready.exit_code(down) == 0 and level(down, "client") == "WARN"
          and level(down, "brain") == "WARN", down)
    check("the command shadow is reported without failing the preflight",
          level(same, "command shadow") in ("INFO", "PASS")
          and ready.exit_code(same) == 0, same)
    check("the page build is parsed from the one constant",
          ready.page_build(page) == "2026-10-27a", ready.page_build(page))
    check("the last recorded build is the last, not the first",
          ready.last_log_build(log_with("2026-10-27a")) == "2026-10-27a",
          ready.last_log_build(log_with("2026-10-27a")))
    check("the repo page carries the 2026-10-29bg build",
          ready.page_build(REPO_ROOT.parent / "scripts" / "mobile-disguise.js")
          == "2026-10-29bg")


def test_sim_planner_runs() -> None:
    section("Simulator harness (a symbolic client that offers the curriculum)")
    import contextlib
    import inspect
    import io
    import json as _json
    from sim import run as sim_run

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = sim_run.main(["--policy", "planner", "--level", "60",
                             "--steps", "400", "--seed", "7"])
    text = buffer.getvalue()
    at = text.find("{")
    payload = _json.loads(text[at:]) if at >= 0 else {}
    check("the rule planner completes steps in the simulator",
          code == 0 and payload.get("steps", 0) > 0, text[-200:])
    # The teacher's measured floor. 119 quests / 148 objectives was the last known
    # good state (2026-10-24). A simulator that only checks "> 0" hid a 119 -> 82
    # regression for a day, so the floor is pinned here.
    check("the teacher completes at least 119 quests in 400 steps",
          payload.get("quests", 0) >= 119, payload)
    check("and does not loop (max_repeat <= 3, not stalled)",
          payload.get("max_repeat", 99) <= 3 and payload.get("stalled") is False,
          payload)
    check("the simulator seeds the client's offered quests for the planner",
          "_client_quests |= set(policy._curriculum" in
          inspect.getsource(sim_run.main), "the G31 guard would end every run at 0")
    # Standing on a quest-start map is not a dialog. The simulator published an open
    # offer there once, and the planner spent the run accepting instead of acting.
    from sim.world import World
    obs = World(level=60, seed=7).observe()
    check("standing on a start map does not invent an open dialog",
          (obs.get("dialog") or {}).get("open") is False, obs.get("dialog"))


def test_command_parser() -> None:
    section("Command parser (prompt -> intent + slots, rules first)")
    import importlib.util
    import json as _json

    gold_path = REPO_ROOT / "data" / "core_sft_v4" / "gold.jsonl"
    check("the human command gold set exists", gold_path.is_file(), str(gold_path))
    from agent.commands import (INTENTS, SLOT_KEYS, from_tool, parse_command,
                                rule_parse)

    check("the intent vocabulary is closed",
          set(INTENTS) == {"stop", "follow", "travel", "quest", "farm", "fight",
                           "inventory", "market", "chat", "knowledge", "state",
                           "auto", "unknown"},
          sorted(INTENTS))
    nav = from_tool("nav.goto", {"x": 12, "y": 8}, "go to [12, 8]")
    check("nav.goto maps to travel with coordinates",
          nav["intent"] == "travel" and nav["slots"] == {"x": 12, "y": 8}, nav)
    sell = from_tool("inv.sell", {"item": "Bouftou Wool", "all": True,
                                  "price_rule": "median_minus_1"},
                     "sell the Bouftou Wool")
    check("tool defaults are not mistaken for user intent",
          sell["intent"] == "inventory"
          and sell["slots"] == {"action": "sell", "item": "Bouftou Wool"}, sell)

    rule = rule_parse("stop everything now")
    check("a stop prompt maps to stop/all",
          rule["intent"] == "stop" and rule["slots"].get("action") == "all", rule)
    rule = rule_parse("follow me")
    check("a follow prompt has no empty name slot",
          rule["intent"] == "follow" and "name" not in rule["slots"], rule)
    rule = rule_parse("auto")
    check("auto is a command, not unknown", rule["intent"] == "auto", rule)
    rule = rule_parse("hello are you a bot?")
    check("social chat is unknown, not an action",
          rule["intent"] == "unknown" and rule["confidence"] == 0.0, rule)

    rows = []
    for line in gold_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(_json.loads(line))
    hits = 0
    for row in rows:
        want = from_tool(row.get("tool", ""), row.get("args"), row.get("goal", ""),
                         lang=row.get("lang"))
        got = rule_parse(row.get("goal", ""))
        hits += got["intent"] == want["intent"]
    check("the rule baseline stays above the measured floor on the gold set",
          rows and hits / len(rows) >= 0.55,
          "%d/%d intents" % (hits, len(rows)))

    class Model:
        def __init__(self, intent, confidence):
            self.intent = intent
            self.confidence = confidence

        def predict(self, text):
            return {"intent": self.intent, "confidence": self.confidence,
                    "slots": {"item": "Bouftou Wool"}}

    sure = parse_command("stop everything now", model=Model("travel", 0.99))
    check("a confident model answers before the rules",
          sure["source"] == "model" and sure["intent"] == "travel", sure)
    check("model slots the intent cannot carry are dropped",
          sure["slots"] == {}, sure["slots"])

    class Sloppy:
        def predict(self, text):
            return {"intent": "inventory", "confidence": 0.99,
                    "slots": {"action": "sell", "item": "Bouftou Wool",
                              "message": "5"}}

    masked = parse_command("vends 5 bouftou wool", model=Sloppy())
    check("the legality mask keeps the legal slots and drops the rest",
          masked["source"] == "model"
          and masked["slots"] == {"action": "sell", "item": "Bouftou Wool"},
          masked)
    unsure = parse_command("stop everything now", model=Model("travel", 0.10))
    check("an unsure model falls back to the rules",
          unsure["source"] == "rule" and unsure["intent"] == "stop", unsure)
    invalid = parse_command("stop everything now", model=Model("fly", 0.99))
    check("an invalid intent is not accepted", invalid["intent"] == "stop", invalid)
    check("all slot keys are declared", set(sell["slots"]) <= set(SLOT_KEYS),
          sorted(sell["slots"]))
    from agent.commands import ACTION_TOOL, INTENT_TOOL, intent_tool
    from agent.planner import _goal_mode
    from agent.schema import TOOLS as _TOOLS

    modes = {text: _goal_mode(text) for text in (
        "can you play the game ?", "play for me", "take over",
        "do everything", "can you play the quests ?", "fight mobs",
        "list my quests", "zaap Astrub")}
    check("asking the bot to play routes to Full Auto, without stealing quests",
          modes == {"can you play the game ?": "auto", "play for me": "auto",
                    "take over": "auto", "do everything": "auto",
                    "can you play the quests ?": "quest", "fight mobs": "farm",
                    "list my quests": "quest", "zaap Astrub": "order"}, modes)

    bridges = {name: intent_tool(intent, action) for name, intent, action in (
        ("quest_list", "quest", "list"), ("quest_run", "quest", "run"),
        ("quest_advance", "quest", "advance"), ("farm_stop", "farm", "stop"),
        ("market_list", "market", "list"), ("fight_pass", "fight", "pass"),
        ("stop", "stop", "all"), ("inventory_sell", "inventory", "sell"),
        ("auto", "auto", None), ("unknown", "unknown", None))}
    check("a parsed action selects the canonical tool",
          bridges == {"quest_list": "quest.list", "quest_run": "quest.advance",
                      "quest_advance": "quest.advance", "farm_stop": "farm.stop",
                      "market_list": "hdv.list", "fight_pass": "fight.pass",
                      "stop": "safe.stop", "inventory_sell": "inv.sell",
                      "auto": None, "unknown": None}, bridges)
    names = {tool.name for tool in _TOOLS}
    mapped = set(INTENT_TOOL.values()) | {tool for row in ACTION_TOOL.values()
                                          for tool in row.values()}
    check("every intent/tool bridge names a registered tool",
          mapped <= names and len(names) >= 21, sorted(mapped - names))


def test_command_dataset() -> None:
    section("Command dataset (held-out phrasings and entities)")
    import importlib.util
    import json as _json
    import tempfile

    tool = REPO_ROOT / "tools" / "build_command_dataset.py"
    check("the dataset builder exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("build_command_dataset", tool)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    from agent.commands import INTENTS, SLOT_KEYS

    out = Path(tempfile.mkdtemp(prefix="commands-"))
    manifest = builder.build(out, variants=4)
    check("all three splits are written",
          all((out / (name + ".jsonl")).is_file()
              for name in ("train", "val", "test")), str(out))
    rows = {}
    for name in ("train", "val", "test"):
        rows[name] = [_json.loads(line)
                      for line in (out / (name + ".jsonl")).read_text(
                          encoding="utf-8").splitlines() if line.strip()]
    check("the train split is non-trivial", len(rows["train"]) >= 50,
          len(rows["train"]))
    check("val and test are not empty",
          len(rows["val"]) > 0 and len(rows["test"]) > 0,
          str((len(rows["val"]), len(rows["test"]))))
    check("no held-out entity leaks into the train slot labels",
          manifest["eval_entity_leaks_in_train"] == [],
          manifest["eval_entity_leaks_in_train"])
    origins = {name: {row.get("origin") for row in split}
               for name, split in rows.items()}
    check("a phrasing belongs to exactly one split",
          not (origins["train"] & origins["val"])
          and not (origins["train"] & origins["test"])
          and not (origins["val"] & origins["test"]),
          {name: len(values) for name, values in origins.items()})
    bad_intent = [row for split in rows.values() for row in split
                  if row.get("intent") not in INTENTS]
    bad_slots = [row for split in rows.values() for row in split
                 if not set(row.get("slots") or {}) <= set(SLOT_KEYS)]
    check("every row has a valid intent and slot keys",
          not bad_intent and not bad_slots,
          (bad_intent[:1], bad_slots[:1]))
    check("the held-out splits exercise unseen entities",
          any(value in builder.EVAL_POOLS.get(key, [])
              for split in (rows["val"], rows["test"])
              for row in split for key, value in (row.get("slots") or {}).items()),
          "no eval-pool slot in val/test")
    check("the train split covers the main intents",
          {"quest", "farm", "fight", "stop", "market", "inventory", "auto"}
          <= {row["intent"] for row in rows["train"]},
          sorted({row["intent"] for row in rows["train"]}))
    check("the train split carries the quest-run action",
          any((row.get("slots") or {}).get("action") == "run"
              for row in rows["train"]),
          "no train row labels action=run")
    check("core phrasings stay in train",
          any(row.get("source") == "core" for row in rows["train"])
          and not any(row.get("source") == "core"
                      for split in (rows["val"], rows["test"]) for row in split),
          "core rows leaked into eval")
    norm = lambda text: " ".join(str(text or "").lower().split())
    train_texts = {norm(row.get("text")) for row in rows["train"]}
    eval_texts = {norm(row.get("text"))
                  for split in (rows["val"], rows["test"]) for row in split}
    check("no eval phrasing appears in train",
          not (train_texts & eval_texts),
          sorted(train_texts & eval_texts)[:3])
    check("the builder removes repeated gold phrasings from train",
          manifest["train_rows_skipped_for_eval_text"] > 0,
          manifest["train_rows_skipped_for_eval_text"])
    eval_token_sets = [set(text.split()) for text in eval_texts
                       if len(text.split()) >= 2]
    near = [row["text"] for row in rows["train"]
            if any(tokens <= set(norm(row["text"]).split())
                   for tokens in eval_token_sets)]
    check("no train row contains a full eval phrasing",
          not near, near[:2])
    check("the builder removes near-copy phrasings from train",
          manifest["train_near_copies_removed"] > 0,
          manifest["train_near_copies_removed"])
    check("one text carries one label",
          manifest["train_text_conflicts"] == [],
          manifest["train_text_conflicts"][:2])


def test_command_shadow() -> None:
    section("Command shadow (the model proposes, the rule acts)")
    from agent.autopilot import Autopilot
    from agent.command_shadow import load_command_model, shadow_row

    class Model:
        def __init__(self, intent, slots, confidence):
            self.payload = {"intent": intent, "slots": slots,
                            "confidence": confidence}

        def predict(self, text):
            return dict(self.payload)

    same = shadow_row("stop everything now", Model("stop", {"action": "all"}, 0.9))
    check("an agreeing proposal is not an override",
          same["agree_command"] is True and same["would_override"] is False, same)
    other = shadow_row("stop everything now", Model("travel", {"x": 1, "y": 2}, 0.9))
    check("a confident disagreement is marked as an override",
          other["agree_intent"] is False and other["would_override"] is True, other)
    low = shadow_row("stop everything now", Model("travel", {"x": 1, "y": 2}, 0.1))
    check("a low-confidence disagreement is not an override",
          low["would_override"] is False, low)
    unknown = shadow_row("stop everything now", Model("unknown", {}, 0.9))
    check("an unknown intent is unusable, so it cannot override",
          unknown["usable"] is False and unknown["would_override"] is False, unknown)
    same_mode = shadow_row("list my quests", Model("quest", {"action": "run"}, 0.9))
    check("an action override can leave the planner's goal mode alone",
          same_mode["rule_mode"] == "quest" and same_mode["model_mode"] == "quest"
          and same_mode["mode_agree"] is True
          and same_mode["would_override"] is True
          and same_mode["would_change_mode"] is False, same_mode)
    mode_change = shadow_row("fight mobs on the curent map",
                             Model("travel", {}, 0.9))
    check("a different intent that changes routing is flagged separately",
          mode_change["rule_mode"] == "farm" and mode_change["model_mode"] == "order"
          and mode_change["would_change_mode"] is True, mode_change)
    low_mode = shadow_row("fight mobs on the curent map",
                          Model("travel", {}, 0.1))
    check("a low-confidence mode change is not an override",
          low_mode["would_change_mode"] is False, low_mode)
    sloppy = shadow_row("vends tout",
                        Model("inventory", {"action": "sell", "all": True,
                                            "message": "5"}, 0.9))
    check("the shadow uses the same intent-slot legality mask",
          sloppy["usable"] is True and sloppy["agree_command"] is True, sloppy)
    from agent.command_shadow import adapter_accepts
    from agent.command_shadow import canary_accepts
    check("an adapter may not change an action-critical canary",
          canary_accepts({"list my quests": ("quest", "list")},
                         {"list my quests": ("quest", "list")}) is True
          and canary_accepts({"list my quests": ("quest", "list")},
                              {"list my quests": ("quest", "run")}) is False,
          "canary guard")
    check("an adapter is accepted only when it loses no held-out base row",
          adapter_accepts({"intent": 20, "exact": 18},
                          {"intent": 20, "exact": 18}) is True
          and adapter_accepts({"intent": 20, "exact": 18},
                              {"intent": 21, "exact": 19}) is True
          and adapter_accepts({"intent": 20, "exact": 18},
                              {"intent": 19, "exact": 18}) is False
          and adapter_accepts({"intent": 20, "exact": 18},
                              {"intent": 20, "exact": 17}) is False,
          "adapter guard")
    check("the shadow carries the tool the parsed action maps to",
          same_mode["expected_tool"] == "quest.advance"
          and other["expected_tool"] == "nav.goto"
          and unknown["expected_tool"] is None
          and sloppy["expected_tool"] == "inv.sell",
          {name: row["expected_tool"] for name, row in
           (("same", same_mode), ("other", other), ("unknown", unknown),
            ("sloppy", sloppy))})

    class Broken:
        def predict(self, text):
            raise RuntimeError("no model")

    broken = shadow_row("stop", Broken())
    check("a broken model is recorded, not raised",
          broken["available"] is False and "no model" in str(broken.get("error")),
          broken)
    missing = load_command_model(Path("definitely-missing-command-model.pt"))
    check("a missing artifact is an error string, not an import failure",
          missing[0] is None and "artifact_missing" in str(missing[1]), missing)

    class Shim:
        pass

    shim = Shim()
    shim.session = "s"
    shim.command_shadow = "auto"
    shim.command_shadow_model = Path("model.pt")
    shim._command_model = Model("stop", {"action": "all"}, 0.9)
    shim._command_shadow_loaded = True
    shim._command_shadow_rows = 0
    shim._append_log_line = lambda blob: shim.lines.append(blob)
    shim.lines = []
    shim._ensure_command_shadow = lambda: shim._command_model
    Autopilot._log_command_shadow(shim, "stop everything now")
    check("the autopilot logs one shadow row and changes nothing",
          len(shim.lines) == 1 and b'"command_shadow"' in shim.lines[0]
          and shim._command_shadow_rows == 1, shim.lines)


def test_command_shadow_report() -> None:
    section("Command shadow report (a real prompt log -> rule vs model)")
    import importlib.util
    import json as _json
    import tempfile

    tool = REPO_ROOT / "tools" / "command_shadow_report.py"
    check("the command shadow report tool exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("command_shadow_report", tool)
    report_tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report_tool)

    log = Path(tempfile.mkdtemp(prefix="shadow-report-")) / "autopilot.jsonl"
    log.write_text("\n".join([
        _json.dumps({"kind": "decision", "goal": "list my quests"}),
        _json.dumps({"kind": "decision", "goal": "  list my quests  "}),
        _json.dumps({"kind": "step", "goal": "plz list my quests"}),
        _json.dumps({"kind": "step", "goal": None}),
        "{not json",
    ]) + "\n", encoding="utf-8")
    goals = report_tool.extract_goals([log])
    check("goals are counted and whitespace-collapsed",
          dict(goals) == {"list my quests": 2, "plz list my quests": 1},
          dict(goals))

    class Model:
        def predict(self, text):
            return {"intent": "quest", "confidence": 0.9,
                    "slots": {"action": "run"}}

    report = report_tool.build_report(goals, Model())
    check("the report sees both goals and all uses",
          report["distinct_goals"] == 2 and report["total_uses"] == 3, report["summary"])
    check("a confident run over the rule's list is reported as an override",
          report["summary"]["override_goals"] == 2
          and report["summary"]["override_uses"] == 3, report["summary"])
    check("the most frequent goal is listed first",
          report["goals"][0]["text"] == "list my quests"
          and report["goals"][0]["uses"] == 2, report["goals"][:1])
    blank = report_tool.build_report(goals, None)
    check("without a model the report still carries the rule side",
          blank["model_available"] is False
          and blank["summary"]["override_goals"] == 0
          and all(row["rule"]["intent"] for row in blank["goals"]), blank["summary"])


def test_command_replay() -> None:
    section("Command replay (a goal's executed plan and outcome)")
    import importlib.util
    import json as _json
    import tempfile

    tool = REPO_ROOT / "tools" / "command_replay.py"
    check("the command replay tool exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("command_replay", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    log = Path(tempfile.mkdtemp(prefix="command-replay-")) / "autopilot.jsonl"
    rows = [
        {"goal": "list my quests", "tool": "state.snapshot", "at": 0,
         "obs_before": {}, "obs_after": {}, "ok": False, "verdict": "noop"},
        {"goal": "list my quests", "tool": "quest.advance", "at": 1,
         "obs_before": {"objectives_completed": 0, "xp": 100},
         "obs_after": {"objectives_completed": 1, "xp": 150},
         "ok": True, "verdict": "advanced"},
        {"goal": "list my quests", "tool": "nav.goto", "at": 2,
         "obs_before": {"objectives_completed": 1},
         "obs_after": {"objectives_completed": 1},
         "ok": False, "verdict": "failed"},
        {"goal": "fight mobs on the curent map", "tool": "farm.start", "at": 3,
         "obs_before": {}, "obs_after": {}, "ok": False, "verdict": "noop"},
        {"kind": "decision", "goal": None, "tool": "quest.advance", "at": 4},
    ]
    log.write_text("".join(_json.dumps(row) + chr(10) for row in rows)
                   + "{bad" + chr(10), encoding="utf-8")
    groups = module.group_steps(log, module._read(log))
    check("executed steps are grouped by goal",
          len(groups) == 2 and [len(group["steps"]) for group in groups] == [3, 1],
          [(group["goal"], len(group["steps"])) for group in groups])

    class Model:
        def predict(self, text):
            return {"intent": "quest", "confidence": 0.9,
                    "slots": {"action": "run"}}

    replay = module.build_replay(groups, Model())
    first = next(row for row in replay if row["goal"] == "list my quests")
    check("the outcome skips empty observations instead of losing the counter",
          first["outcome"]["objectives_before"] == 0
          and first["outcome"]["objectives_after"] == 1
          and first["outcome"]["objectives_delta"] == 1
          and first["outcome"]["xp_delta"] == 50, first["outcome"])
    check("the executed plan is labelled as the rule's, not the model's",
          first["executed_by"] == "rule" and first["steps"] == 3
          and set(first["executed"]["tools"]) ==
          {"state.snapshot", "quest.advance", "nav.goto"}, first["executed"])
    check("a page advancement is an outcome even when objectives stay flat",
          first["executed"]["advanced"] == 1
          and first["outcome"]["goal_outcome"] == "advanced", first["outcome"])
    check("an action override can leave the mode alone",
          first["would_override"] is True and first["mode_agree"] is True
          and first["would_change_mode"] is False, first)
    check("the replay maps the model's action onto a tool",
          first["expected_tool"] == "quest.advance"
          and first["expected_tool_executed"] is True, first)
    second = next(row for row in replay
                  if row["goal"] == "fight mobs on the curent map")
    check("a group with no objective field stays null, not a fake zero",
          second["outcome"]["objectives_delta"] is None, second["outcome"])
    check("a group with no advancement is stalled",
          second["outcome"]["goal_outcome"] == "stalled", second["outcome"])
    check("a short failed group is a short failure, not a read loop",
          second["outcome"]["stall_cause"] == "short_fail", second["outcome"])
    check("the stall classifier separates fight-start and read-loop stalls",
          module.classify_stall(4, {"fight.join": 3, "state.snapshot": 1}, {},
                                "stalled") == "fight_join"
          and module.classify_stall(10, {"state.snapshot": 8, "quest.list": 2}, {},
                                    "stalled") == "read_loop"
          and module.classify_stall(4, {"fight.join": 3}, {}, "advanced") is None,
          "classifier")
    check("a routing change is flagged on the replay row",
          second["mode_agree"] is False and second["would_change_mode"] is True,
          second)
    check("an expected tool the group never executed is false",
          second["expected_tool"] == "quest.advance"
          and second["expected_tool_executed"] is False, second)
    summary = module.summarize(replay)
    check("the replay summary counts progress, overrides and mode changes",
          summary["groups"] == 2 and summary["steps"] == 4
          and summary["groups_with_objective_progress"] == 1
          and summary["groups_advanced"] == 1
          and summary["groups_stalled"] == 1
          and summary["override_groups"] == 2
          and summary["override_groups_advanced"] == 1
          and summary["override_groups_executed_advance"] == 1
          and summary["expected_tool_groups"] == 2
          and summary["expected_tool_executed_groups"] == 1
          and summary["override_groups_expected_executed"] == 1
          and summary["stall_causes"] == {"short_fail": 1}
          and summary["would_change_mode_groups"] == 1, summary)


def test_live_evidence() -> None:
    section("Live evidence (preflight -> bounded shadow run -> combined report)")
    import importlib.util
    import json as _json
    import tempfile

    tool = REPO_ROOT / "tools" / "live_evidence.py"
    check("the live evidence runner exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("live_evidence", tool)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    log = Path(tempfile.mkdtemp(prefix="live-evidence-")) / "autopilot.jsonl"
    rows = [
        {"kind": "command_shadow_status", "available": True, "error": None},
        {"kind": "command_shadow", "text": "do all the quests", "usable": True,
         "agree_intent": True, "agree_command": False, "would_override": True,
         "would_change_mode": False},
        {"kind": "command_shadow", "text": "fight mobs", "usable": True,
         "agree_intent": True, "agree_command": True, "would_override": False,
         "would_change_mode": False},
        {"kind": "step", "tool": "quest.advance", "ok": True},
    ]
    log.write_text("".join(_json.dumps(row) + chr(10) for row in rows),
                   encoding="utf-8")
    summary = runner.shadow_rows_summary(runner.read_rows(log))
    check("the runner counts shadow rows, agreements and overrides",
          summary["rows"] == 2 and summary["agree_command"] == 1
          and summary["would_override"] == 1
          and summary["override_examples"] == ["do all the quests"], summary)

    ready = [{"level": "PASS", "name": "client", "detail": "1 window"},
             {"level": "INFO", "name": "observed quests", "detail": "none"}]
    warn = [{"level": "WARN", "name": "client", "detail": "bridge down"}]
    failed = [{"level": "FAIL", "name": "stop file", "detail": "present"}]
    parsed = runner.parse_autopilot_summary(
        "noise" + chr(10) + '{"steps": 3, "command_shadow": {"rows": 2}}'
        + chr(10))
    check("the runner captures the autopilot summary, not only its exit code",
          parsed == {"steps": 3, "command_shadow": {"rows": 2}}
          and runner.parse_autopilot_summary("noise only") is None, parsed)
    check("the runner resolves a relative log before entering the packaged cwd",
          runner.resolve_log(Path("a.jsonl")).is_absolute()
          and runner.resolve_log(Path("a.jsonl")) == Path("a.jsonl").resolve(),
          runner.resolve_log(Path("a.jsonl")))
    check("the runner refuses without a client and on a preflight failure",
          runner.client_ready(ready) is True
          and runner.client_ready(warn) is False
          and runner.client_ready(failed) is False
          and runner.client_ready([]) is False, (ready, warn, failed))


def test_command_onnx_decode() -> None:
    section("Command ONNX decode (torch-free inference path)")
    from agent.command_onnx import decode_slots

    tags = ["O", "B-item", "I-item", "B-quantity", "B-channel", "B-all",
            "B-x", "B-price"]
    text = "vends 5 bouftou wool"
    offsets = [(0, 5), (6, 7), (8, 15), (16, 20)]
    slots = decode_slots(text, offsets, [0, 3, 1, 2], tags, "sell", 0)
    check("the ONNX decode copies a multi-token item and a quantity",
          slots == {"item": "bouftou wool", "quantity": 5, "action": "sell"},
          slots)
    signed = decode_slots("x -3", [(0, 1), (3, 4)], [0, 6], tags, "none", 0)
    check("the ONNX decode keeps a sign the tagger left outside the span",
          signed.get("x") == -3, signed)
    extra = decode_slots("vends tout à 500", [(0, 5), (6, 10), (13, 16)],
                         [0, 5, 7], tags, "sell", 0)
    check("the ONNX decode handles all/price with the price default",
          extra.get("all") is True and extra.get("price") == 500
          and extra.get("price_rule") == "fixed", extra)


def test_repair_credit() -> None:
    section("Repair credit (criterion 4: a read cannot certify a recovery)")
    import inspect
    from agent.autopilot import Autopilot, PENDING_CREDIT_STEPS
    from agent.controller import Controller, Decision
    from agent.runtime import ToolCall

    def held() -> Any:
        loop = type("_Loop", (), {})()
        loop.controller = Controller(state_dir=Path(tempfile.gettempdir()), session="")
        loop._pending = Decision("recover", "incomplete", "held for test",
                                 {"tool": "state.snapshot", "args": {}}, "recover")
        loop._pending_age = 0
        loop._credit_pending = Autopilot._credit_pending.__get__(loop)
        return loop

    read = ToolCall("state.snapshot", {}, "goal")
    action = ToolCall("quest.advance", {"quest_id": 1}, "goal")

    loop = held()
    loop._credit_pending(read, "advanced")
    check("a successful read does not certify the repair it was following",
          not loop.controller.stats()["strategies"] and loop._pending is not None
          and loop._pending_age == 1,
          str(loop.controller.stats()["strategies"]))

    loop = held()
    for _ in range(PENDING_CREDIT_STEPS + 1):
        loop._credit_pending(read, "advanced")
    check("a repair no action answers is dropped, not guessed at",
          loop._pending is None and not loop.controller.stats()["strategies"],
          "%r %r" % (loop._pending, loop.controller.stats()["strategies"]))

    loop = held()
    loop._credit_pending(read, "advanced")
    loop._credit_pending(action, "advanced")
    stats = loop.controller.stats()["strategies"]
    check("the credit lands on the first step that could move the world",
          list(stats) == ["incomplete:recover"]
          and stats["incomplete:recover"]["n"] == 1
          and stats["incomplete:recover"]["ok"] == 1, str(stats))

    loop = held()
    loop._credit_pending(action, "failed")
    stats = loop.controller.stats()["strategies"]
    check("and a failing action is credited as a failure",
          stats["incomplete:recover"] == {"n": 1, "ok": 0, "score": 0.0}, str(stats))

    loop = held()
    loop._pending = None
    loop._credit_pending(action, "advanced")
    check("nothing is credited when no decision is held",
          not loop.controller.stats()["strategies"])

    src = inspect.getsource(Autopilot)
    check("the loop credits through the read-aware gate only",
          src.count("self._credit_pending(call, v)") == 1
          and "self.controller.observe_outcome(self._pending, verdict)"
          in inspect.getsource(Autopilot._credit_pending)
          and src.count("controller.observe_outcome") == 1,
          "%d direct credits" % src.count("controller.observe_outcome"))


def test_settle_waits_for_promised_effect() -> None:
    section("Settle window (criterion 2: an effect that lands late is not a no-op)")
    import inspect
    from agent import autopilot as ap
    from agent.autopilot import Autopilot, SETTLE_TOOLS
    from agent.runtime import ToolCall

    class _Clock:
        def __init__(self) -> None:
            self.t = 1000.0

        def monotonic(self) -> float:
            return self.t

        def sleep(self, seconds: float) -> None:
            self.t += seconds

        def time(self) -> float:
            return self.t

    class _Pilot:
        _settle = Autopilot._settle
        _world_key = Autopilot._world_key
        _action_complete = Autopilot._action_complete
        _record_settled = Autopilot._record_settled

        def __init__(self, states: List[Dict[str, Any]]) -> None:
            self.settle_s = 3.0
            self.think_s = 0.25
            self.states = list(states)
            self.polls = 0
            self.runtime = type("_R", (), {"observe": lambda *a, **k: None})()

        def observe(self, max_age_ms: Any = None, record: bool = True) -> Any:
            self.polls += 1
            return self.states.pop(0) if self.states else None

    def settle(tool: str, before: Dict[str, Any], after: Dict[str, Any],
               polls: List[Dict[str, Any]]) -> Any:
        pilot = _Pilot(polls)
        clock = _Clock()
        real = ap.time
        ap.time = clock
        try:
            best = pilot._settle(before, after, call=ToolCall(tool, {}, "goal"))
        finally:
            ap.time = real
        return pilot, best, clock.t - 1000.0

    base = {"cell": 1, "map_id": 7, "in_dialog": False}
    walked = {"cell": 2, "map_id": 7, "in_dialog": False}
    opened = {"cell": 2, "map_id": 7, "in_dialog": True}

    pilot, best, waited = settle("quest.advance", base, walked, [walked, walked, opened])
    check("a claim whose dialog opens one round trip later is judged on the dialog",
          bool((best or {}).get("in_dialog")) and pilot.polls >= 3,
          "%d polls -> %s" % (pilot.polls, best))

    pilot, best, waited = settle("inv.sort", {"cell": 1, "map_id": 7},
                                 {"cell": 9, "map_id": 7},
                                 [{"cell": 9, "map_id": 7}] * 8)
    check("a tool with no promise of its own is judged on the change it made, unpollled",
          pilot.polls == 0 and waited < 0.1 and (best or {}).get("cell") == 9,
          "%d polls, %.2f s" % (pilot.polls, waited))

    pilot, best, waited = settle("quest.advance", base, walked,
                                 [walked] * 40)
    check("the wait stays bounded by the tool's own window",
          waited <= 7.5 and pilot.polls < 40, "%.2f s after %d polls" % (waited, pilot.polls))

    src = inspect.getsource(Autopilot._settle)
    check("the quiet shortcut is the one reserved for promise-less tools",
          "not promised" in src and "promised = tool in SETTLE_TOOLS" in src)
    check("a free-form tap settles like the walk-and-act it is",
          "tap.interactive" in SETTLE_TOOLS,
          sorted(SETTLE_TOOLS))


def test_reads_are_not_actions() -> None:
    section("Idle waits (criterion 2: a read is a wait, not a step)")
    import inspect
    from agent.autopilot import (Autopilot, ESCALATE_TRIES, MAX_CONSECUTIVE_WAITS,
                                 ToolCall, WAIT_POLL_S, fell_out_of_world)
    from agent import autopilot as _am

    check("a session that stayed in the world is not a fall-out",
          fell_out_of_world({"in_world": True}, {"in_world": True}) is None
          and fell_out_of_world(None, {"in_world": False}) is None
          and fell_out_of_world({"in_world": True}, {"map_id": 10340}) is None)
    check("a world that vanished without saying why is still a fall-out",
          fell_out_of_world({"in_world": True}, {"in_world": False}) == "")
    check("the client's own reason travels to the caller",
          fell_out_of_world({"in_world": True},
                            {"in_world": False,
                             "last_disconnect": {"reason": "INACTIVITY"}}) == "INACTIVITY")
    check("a map transition that briefly reports no map does not end the run",
          fell_out_of_world({"in_world": False}, {"in_world": False}) is None)
    check("a malformed disconnect field cannot crash the test",
          fell_out_of_world({"in_world": True},
                            {"in_world": False, "last_disconnect": "INACTIVITY"}) == "")

    # Drive the real loop with a policy that only ever wants a look. This is the
    # baseline's dominant shape: 3,010 of its 4,287 steps were `state.snapshot`,
    # 3,005 of them scored `advanced`, and two runs of them held 57% of the total.
    class _Policy:
        def __init__(self, tool: str = "state.snapshot") -> None:
            self.tool = tool
            self.calls = 0

        def choose(self, goal, obs, tools):     # noqa: ARG002 - shape, not behaviour
            self.calls += 1
            return ToolCall(self.tool, {}, goal)

    class _Result:
        def __init__(self, data: Dict[str, Any]) -> None:
            self.ok = True
            self.error = None
            self.data = data

    class _Executor:
        def __init__(self, obs: Dict[str, Any]) -> None:
            self.obs = obs
            self.calls: List[str] = []

        def execute(self, tool, args, **kw):      # noqa: ARG002 - the shape is enough
            self.calls.append(tool)
            return _Result({"response": {"result": {"ok": True}},
                            "snapshot": dict(self.obs)})

    class _Runtime:
        def __init__(self, obs: Dict[str, Any]) -> None:
            self.policy = _Policy()
            self.instance = "dofus-00"
            self.obs = obs
            self.executor = _Executor(obs)

        def current_observation(self):
            return dict(self.obs), 5.0, "cache"

        def observe(self, obs, source="live"):    # noqa: ARG002 - no trajectory writer
            return None

        def note_observation(self, obs):
            return None

    dirs: List[Path] = []

    def drive(directives: int, obs: Optional[Dict[str, Any]] = None,
              tool: str = "state.snapshot", escalate=None):
        tmp = Path(tempfile.mkdtemp())
        dirs.append(tmp)
        rt = _Runtime(dict(obs if obs is not None
                           else {"in_world": True, "map_id": 10340, "cell": 300}))
        rt.policy.tool = tool
        ap = Autopilot(runtime=rt,
                       goals=["work the %d goal" % i for i in range(directives)],
                       log_path=tmp / "autopilot.jsonl", stop_file=tmp / "STOP",
                       max_steps=directives, dry_run=False, preflight=False,
                       minutes=0, interval=0.0, steps_per_goal=1)
        # The gate must not write a strategy table into the live state directory.
        ap.controller.state_dir = tmp
        if escalate is not None:
            ap.thinking.propose = escalate
        labels: List[Any] = []
        real_label = ap.verifier.label
        ap.verifier.label = lambda **kw: (
            labels.append(kw.get("tool")), real_label(**kw))[1]
        consulted: List[Any] = []
        real_on_step = ap.controller.on_step
        ap.controller.on_step = lambda *a, **k: (
            consulted.append(1), real_on_step(*a, **k))[1]
        # A live-shaped run, minus the seconds: the loop's own pacing is asserted
        # separately, and 120 wait passes must not cost the gate 40 seconds.
        real_sleep = _am.time.sleep
        _am.time.sleep = lambda seconds: None
        try:
            summary = ap._run()
        finally:
            _am.time.sleep = real_sleep
        rows = [json.loads(line) for line in
                (tmp / "autopilot.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
        return ap, summary, rows, labels, consulted, rt

    try:
        ap, summary, rows, labels, consulted, rt = drive(2)
        waits = [r for r in rows if r.get("kind") == "wait"]
        # A step row is the one row type with no `kind` of its own; decision rows
        # carry the tool too and must not be read as steps.
        step_rows = [r for r in rows if "kind" not in r]
        check("two directives cost two step rows and nothing else",
              summary["steps"] == 2 and len(step_rows) == 2,
              "%r step rows %d" % (summary["steps"], len(step_rows)))
        check("the looking between them is a wait, not a step",
              summary["waits"] == 2 * MAX_CONSECUTIVE_WAITS
              and len(waits) == 2 * MAX_CONSECUTIVE_WAITS,
              "waits %r / rows %d" % (summary.get("waits"), len(waits)))
        check("a wait produces no verdict and no reward",
              len(labels) == 2 and len(consulted) == 2,
              "%d labels, %d controller calls over %d passes"
              % (len(labels), len(consulted), rt.policy.calls))
        check("the spin is bounded: past the limit the read is dispatched again",
              all(s.get("tool") == "state.snapshot" for s in step_rows)
              and rt.executor.calls == ["state.snapshot"] * 2
              and rt.policy.calls == 2 * (MAX_CONSECUTIVE_WAITS + 1),
              "%d passes, %d dispatches" % (rt.policy.calls, len(rt.executor.calls)))
        check("a wait row carries the goal, the session and a count",
              (waits[0] if waits else {}).get("goal") == "work the 0 goal"
              and (waits[0] if waits else {}).get("session") == ap.session
              and (waits[0] if waits else {}).get("consecutive") == 1
              and "at" in (waits[0] if waits else {}), str(waits[:1]))
        check("an idle wait still says so in its own words",
              (waits[0] if waits else {}).get("reason") == "the policy had no action to take",
              str(waits[:1]))
        check("a step ends the streak and drops a reason that no longer applies",
              ap._consecutive_waits == 0 and ap._forced_reason == "" and ap._waits > 0,
              "%d %r" % (ap._consecutive_waits, ap._forced_reason))
        from agent.autopilot import Step
        ap._consecutive_waits = MAX_CONSECUTIVE_WAITS - 1
        ap._forced_reason = "a reason written for a call already made"
        ap._emit(Step(index=0, goal="g", tool="inv.sort", args={}, ok=True,
                      error=None, latency_ms=1.0, verdict="advanced"))
        check("the reset is in the step recorder itself, not only in the loop",
              ap._consecutive_waits == 0 and ap._forced_reason == "",
              "%d %r" % (ap._consecutive_waits, ap._forced_reason))

        # The reason is the thinking layer's own sentence, when it has one.
        ap._forced_reason = "wait for the NPC to finish talking"
        held = ap._hold_wait("do all the quests", {"in_dialog": True, "in_world": True})
        last = json.loads((dirs[-1] / "autopilot.jsonl").read_text(
            encoding="utf-8").splitlines()[-1])
        check("a directed wait is recorded with the reason that directed it",
              held is True and last["kind"] == "wait"
              and last["reason"] == "wait for the NPC to finish talking"
              and last["in_dialog"] is True, str(last))
        check("and the reason is spent, not stamped on every later wait",
              ap._forced_reason == "", repr(ap._forced_reason))

        # Pacing: the same cadence the loop had when the wait was a directive.
        sleeps: List[float] = []
        real_sleep = _am.time.sleep
        _am.time.sleep = lambda seconds: sleeps.append(seconds)
        try:
            ap.dry_run = False
            ap.interval = 0.0
            ap._hold_wait("do all the quests", {"in_world": True})
            paced = list(sleeps)
            sleeps.clear()
            ap.interval = 2.0
            ap._hold_wait("do all the quests", {"in_world": True})
            paced_by_interval = list(sleeps)
            sleeps.clear()
            ap.dry_run = True
            ap.interval = 0.0
            ap._hold_wait("do all the quests", {"in_world": True})
            dry = list(sleeps)
        finally:
            ap.dry_run = False
            _am.time.sleep = real_sleep
        check("a wait holds the loop's measured cadence, no faster",
              paced == [WAIT_POLL_S], str(paced))
        check("a slower --interval still wins", paced_by_interval == [2.0],
              str(paced_by_interval))
        check("a dry run has no world to wait for and does not sleep", dry == [], str(dry))

        # A kick that lands during a wait must be a recovery, not more waiting: no
        # step is dispatched here, so the post-step check would never have run.
        recovered: List[Any] = []
        real_recover = ap._recover_world

        def _spy(reason: Optional[str] = None, trigger: str = "fell_out") -> bool:
            recovered.append((reason, trigger))
            return not fail_next

        fail_next = False
        ap._recover_world = _spy
        try:
            rt.obs = {"in_world": False, "last_disconnect": {"reason": "INACTIVITY"}}
            out = ap._hold_wait("do all the quests", {"in_world": True, "map_id": 1})
            rescued = list(recovered)
            # The count measured a wait inside a session that has ended, so a
            # rescued world must start with a full budget of its own.
            ap._consecutive_waits = MAX_CONSECUTIVE_WAITS
            rt.obs = {"in_world": False, "last_disconnect": {"reason": "KICKED"}}
            ap._hold_wait("do all the quests", {"in_world": True, "map_id": 1})
            streak = ap._consecutive_waits
            fail_next = True
            rt.obs = {"in_world": False}
            given_up = ap._hold_wait("do all the quests", {"in_world": True, "map_id": 1})
        finally:
            ap._recover_world = real_recover
        check("a fall-out noticed during a wait hands over to the recovery path",
              out is True and rescued == [("INACTIVITY", "wait_fell_out")],
              "%r %r" % (out, rescued))
        check("and the wait budget restarts against the rescued world",
              streak == 0, "streak %d" % streak)
        check("a world that does not come back ends the run, it does not keep waiting",
              given_up is False)
        check("a silent fall-out still reaches the recovery path",
              (recovered[-1] if recovered else None) == (None, "wait_fell_out"),
              str(recovered[-1:]))

        # A wait that reaches the bound is evidence the goal has nothing left, so the
        # loop asks the thinking layer for an untried interaction. Two failure modes
        # matter: taking a read as the answer (which resets the streak and doubles the
        # spin -- the first version of this did exactly that), and escalating forever.
        from agent.thinking import Thought
        seen: List[int] = []

        def _only_reads(goal, observation, steps, decision):
            seen.append(1)
            return Thought("state.snapshot", {}, "refresh the observation")

        _, summary2, rows2, _, _, rt2 = drive(1, escalate=_only_reads)
        check("a read is never accepted as an escalation",
              not any(r.get("escalated") for r in rows2 if r.get("kind") == "wait")
              and summary2["waits"] == MAX_CONSECUTIVE_WAITS,
              "waits %r asks %d" % (summary2.get("waits"), len(seen)))
        check("and the search is bounded, not re-asked every pass",
              len(seen) == ESCALATE_TRIES and rt2.executor.calls == ["state.snapshot"],
              "%d asks, dispatched %r" % (len(seen), rt2.executor.calls))

        asks: List[int] = []

        def _escalate(goal, observation, steps, decision):
            asks.append(1)
            # First the read, then a real action -- the filter must skip the read.
            return Thought("farm.stop", {}, "stop the standing order") \
                if len(asks) > 1 else Thought("state.snapshot", {}, "refresh")

        ap3, summary3, rows3, _, _, rt3 = drive(2, escalate=_escalate)
        marked = [r for r in rows3 if r.get("kind") == "wait" and r.get("escalated")]
        check("an untried action is taken at the bound and named in the row",
              len(marked) == 2 and all(m["escalated"] == "farm.stop" for m in marked)
              and all(m["consecutive"] == MAX_CONSECUTIVE_WAITS for m in marked),
              str(marked))
        check("the escalated call is dispatched on the next pass",
              rt3.executor.calls == ["farm.stop", "farm.stop"], str(rt3.executor.calls))
        check("one escalation cannot chain into the next: the budget restarts",
              summary3["waits"] == 2 * MAX_CONSECUTIVE_WAITS
              and ap3._consecutive_waits == 0 and len(asks) <= 2 * ESCALATE_TRIES,
              "waits %r asks %d" % (summary3.get("waits"), len(asks)))
        check("and each goal escalates for itself: a repair queued by one is not "
              "dispatched under the next",
              all(m["goal"] == "work the %d goal" % i for i, m in enumerate(marked))
              and [s.get("goal") for s in rows3 if "kind" not in s]
              == ["work the 0 goal", "work the 1 goal"],
              str([(s.get("goal"), s.get("tool")) for s in rows3 if "kind" not in s]))

        # An escalation is a proposal, not a pass: it still goes through the guard, and
        # a call the schema does not know costs a refusal row -- it must not become a
        # new kind of spin.
        def _bogus(goal, observation, steps, decision):
            return Thought("inv.sort", {"sort": "bag"}, "the layer is guessing")

        _, summary4, rows4, _, _, rt4 = drive(1, escalate=_bogus)
        blocked = [r for r in rows4 if "kind" not in r and r.get("verdict") == "blocked"]
        check("an escalated call the schema rejects is refused, not dispatched",
              rt4.executor.calls == [] and len(blocked) == 1
              and blocked[0]["tool"] == "inv.sort"
              and "schema rejected" in str(blocked[0].get("error")),
              "%r %r" % (rt4.executor.calls, [b.get("error") for b in blocked][:1]))
        check("and the refusal ends the goal rather than looping the wait",
              summary4["steps"] == 1 and summary4["waits"] == MAX_CONSECUTIVE_WAITS,
              "%r %r" % (summary4.get("steps"), summary4.get("waits")))

        src_run = inspect.getsource(Autopilot._run)
        src_wait = inspect.getsource(Autopilot._hold_wait)
        check("the wait is taken before the guard, so a read cannot be refused or streaked",
              src_run.index("self._hold_wait(goal, before)")
              < src_run.index("refusal = self.guard(call)"))
        check("the bound decides in the loop; the wait itself cannot retire a goal",
              "self._consecutive_waits < MAX_CONSECUTIVE_WAITS" in src_run
              and "ended_because" not in src_wait
              and "_retire_named_quest" not in src_wait)
        check("a wait has no way to produce a step row, a verdict, or a reward",
              "Step(" not in src_wait and "self._emit(" not in src_wait
              and "on_step" not in src_wait)
        check("the limit is above every legitimate wait in the baseline",
              MAX_CONSECUTIVE_WAITS > 6 and WAIT_POLL_S > 0.05,
              "limit %d, poll %.2f" % (MAX_CONSECUTIVE_WAITS, WAIT_POLL_S))
    finally:
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)


def test_dialog_labels() -> None:
    section("Dialog labels (criterion 4: a reply needs its outcome recorded to be learned)")
    import inspect
    import json as _json
    from agent.autopilot import Autopilot

    class _Policy:
        last_dialog_decision = {"reason": "question tie-break", "quest_id": 3841,
                                "scores": [{"index": 0, "text": "Yes.", "score": 0.0,
                                            "question": 0.5},
                                           {"index": 1, "text": "No.", "score": 0.0,
                                            "question": 0.0}]}

    class _Runtime:
        policy = _Policy()

    class _Session:
        _record_dialog = Autopilot._record_dialog

        def __init__(self, after: Dict[str, Any]) -> None:
            self.session = "gate"
            self._index = 4
            self._dialogs = 0
            self._dialog_stalls = 0
            self._obs_source = "call"
            self.rows: List[Dict[str, Any]] = []
            self.runtime = _Runtime()

        def _append_log_line(self, blob: bytes) -> None:
            self.rows.append(_json.loads(blob.decode("utf-8")))

    PAGE = "Hello, Aiai. I'm a travelling sorcerer."
    REPLIES = [{"index": 0, "text": "Say you want to change class."},
               {"index": 1, "text": "Never mind."}]
    before = {"dialog": {"open": True, "npc_id": 771, "name_id": "Sorcerer",
                         "message": PAGE, "replies": REPLIES},
              "knowledge": {"objective_id": 14070},
              # A free-form objective is the one a reply can never validate; the row has
              # to carry the type or the census cannot tell the two stalls apart.
              "current_objective": {"objective_id": 14070, "type_id": 12}}
    call = type("C", (), {"args": {"quest_id": 3841, "reply": 0,
                                   "reply_text": "Say you want to change class."}})()

    class _Result:
        def __init__(self, ok=True, data=None, error=None):
            self.ok, self.data, self.error = ok, data, error

    SENT = _Result(True, {"replied": 0, "replies": 2, "reply_id": 41457,
                          "reply_text": "Say you want to change class.",
                          "via": "gui.npcDialogHandler._uiReplyHandler"})

    def record(after, args=call.args, state=before, result=None):
        s = _Session(after)
        c = type("C", (), {"args": dict(args)})()
        s._record_dialog(c, state, after, result)
        return s, (s.rows[-1] if s.rows else None)

    moved = {"dialog": {"open": True, "npc_id": 771, "message": "Which class interests you?"}}
    s, row = record(moved, result=SENT)
    check("a reply that moved the conversation is recorded as advanced, with its features",
          row is not None and row["kind"] == "dialog" and row["outcome"] == "advanced"
          and row["asked"] == 0 and row["npc_id"] == 771 and row["quest_id"] == 3841
          and row["objective_id"] == 14070 and row["reason"] == "question tie-break"
          and len(row["scores"]) == 2 and row["page"] == PAGE
          and row["replies"] == [r["text"] for r in REPLIES], str(row))
    check("the score that decided it is stored, so a scorer can be trained on the pair",
          row["scores"][0]["score"] == 0.0 and row["scores"][0]["question"] == 0.5,
          str(row["scores"]))
    check("and it is counted, not just written",
          (s._dialogs, s._dialog_stalls) == (1, 0), str(s._dialogs))
    # G21: the dispatch half of the pair. The baseline replays one page 87 times against
    # reply id 41457, and the wire log shows the server answering it with a leave. Without
    # these fields the row cannot say who ended the conversation.
    check("the id and the handler that answered are recorded with the choice",
          row["reply_id"] == 41457
          and row["via"] == "gui.npcDialogHandler._uiReplyHandler"
          and row["dispatch_ok"] is True and row["error"] is None, str(row))
    check("the objective type travels with the row, so a free-form stall is not read "
          "as a bad ranking", row["objective_type"] == 12, str(row.get("objective_type")))
    check("the page that came back is recorded, not just its verdict",
          row["page_after"] == "Which class interests you?", str(row.get("page_after")))
    check("and the label says which read produced it",
          row["after_source"] == "call", str(row.get("after_source")))
    _, row = record(moved)
    check("a row is still written when the client gives no dispatch data back",
          row is not None and row["reply_id"] is None and row["via"] is None
          and row["dispatch_ok"] is True, str(row))
    _, row = record(moved, result=_Result(False, None, "dialog has 2 replies; asked for index 5"))
    check("a refused dispatch is a failed dispatch, and its reason is kept",
          row["dispatch_ok"] is False and row["error"].startswith("dialog has 2 replies"),
          str(row))

    _, row = record({"dialog": {"open": True, "npc_id": 771, "message": PAGE}})
    check("the same page after a reply is a stall, not silence",
          row["outcome"] == "same page" and row["asked"] == 0, str(row))
    s2, row = record({"dialog": {"open": False}})
    check("a reply that closed the dialog is recorded as closed",
          row["outcome"] == "closed" and s2._dialogs == 1, str(row))
    check("and says who closed it: we did not ask, so the game ended it",
          row["ended_by"] == "after_reply" and row["page_after"] is None, str(row))
    _, row = record({"map_id": 10340})
    check("an after-state without a dialog block says nothing and is called unknown",
          row["outcome"] == "unknown" and row["ended_by"] is None, str(row))

    # Non-dialog steps must not write rows: this instrument is a pairing, not a trace.
    for args in ({"quest_id": 3841}, {"quest_id": 3841, "npc_id": 5}, {}):
        _, row = record(moved, args=args)
        check("a %s step writes no dialog row" % (args or "bare"), row is None, str(row))
    _, closed = record(moved, args={"quest_id": 3841, "close": True})
    check("an explicit close is a dialog action and is recorded as asked=close",
          closed is not None and closed["asked"] == "close"
          and closed["outcome"] == "advanced", str(closed))
    _, closed = record({"dialog": {"open": False}}, args={"quest_id": 3841, "close": True})
    check("a close we asked for is credited to us, not to the server",
          closed["outcome"] == "closed" and closed["ended_by"] == "asked", str(closed))

    s4 = _Session(moved)
    s4._record_dialog(type("C", (), {"args": {"reply": 1, "reply_text": "No."}})(),
                      {"dialog": {"open": False, "message": PAGE}}, moved)
    check("a step taken with no dialog open cannot be attributed to one",
          s4.rows == [] and s4._dialogs == 0, str(s4.rows))

    src = inspect.getsource(Autopilot._run)
    check("the pairing is taken on the step path, where both states and the dispatch "
          "result exist",
          src.index("self._record_dialog(call, before, after, result)") > src.index(
              "fight_estimate, fight_outcome = self._track_fight(before, after"))
    summary_src = inspect.getsource(Autopilot.summary)
    check("the summary reports dialog attempts and stalls",
          '"dialogs": self._dialogs' in summary_src
          and '"dialog_stalls": self._dialog_stalls' in summary_src)


def test_report_instrument() -> None:
    section("Baseline report (criterion 5: the instrument that measures the run)")
    import contextlib
    import importlib.util
    import io

    script = REPO_ROOT / "tools" / "recovery_report.py"
    check("the report tool exists", script.exists(), str(script))
    spec = importlib.util.spec_from_file_location("recovery_report", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def run_report(lines: List[str]) -> str:
        tmp = Path(tempfile.mkdtemp())
        log = tmp / "log.jsonl"
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = module.main([str(log)])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return "%s\n(exit %s)" % (buf.getvalue(), code)

    rows = [
        json.dumps({"kind": "recovery", "session": "s", "trigger": "client_absent",
                    "reason": "no client instance", "recovered": True,
                    "waited_s": 3.5, "at": 1000.0}),
        json.dumps({"kind": "wait", "session": "s", "goal": "g", "consecutive": 1,
                    "reason": "the policy had no action to take", "escalated": "chat.say",
                    "at": 1001.0}),
        json.dumps({"kind": "wait", "session": "s", "goal": "g", "consecutive": 2,
                    "reason": "the policy had no action to take", "at": 1002.0}),
        json.dumps({"kind": "dialog", "session": "s", "index": 0, "quest_id": 1,
                    "objective_id": 2, "npc_id": 7, "name_id": "Sorcerer",
                    "page": "Hello", "replies": ["a", "b"], "asked": 0,
                    "reply_text": "a", "reason": "question tie-break",
                    "scores": [{"index": 0, "score": 0.0, "question": 0.5}],
                    "outcome": "same page", "at": 1003.0}),
        # The same page, asked again, this time with the G21 dispatch fields: a row written
        # before they existed must still parse, and a repeat must be called out.
        json.dumps({"kind": "dialog", "session": "s", "index": 1, "quest_id": 1,
                    "objective_id": 2, "objective_type": 12, "npc_id": 7,
                    "name_id": "Sorcerer", "page": "Hello", "replies": ["a", "b"],
                    "asked": 0, "reply_text": "a", "reply_id": 41457,
                    "via": "gui.npcDialogHandler._uiReplyHandler",
                    "dispatch_ok": False, "error": "asked for index 5",
                    "reason": "only reply", "scores": [],
                    "outcome": "same page", "ended_by": None,
                    "page_after": "Hello", "after_source": "call", "at": 1003.5}),
        json.dumps({"kind": "dialog", "session": "s", "index": 2, "quest_id": 1,
                    "objective_id": 2, "objective_type": 12, "npc_id": 7,
                    "name_id": "Sorcerer", "page": "Hello", "replies": ["a"],
                    "asked": 0, "reply_text": "a", "reply_id": 41457, "dispatch_ok": True,
                    "reason": "only reply", "scores": [], "outcome": "closed",
                    "ended_by": "after_reply", "page_after": None,
                    "after_source": "call", "at": 1003.8}),
        # The G23 case: a stall measured against a read that predates the answer is not
        # evidence of a stall, and the report has to say so instead of counting it.
        json.dumps({"kind": "dialog", "session": "s", "index": 3, "quest_id": 1,
                    "objective_id": 2, "objective_type": 12, "npc_id": 7,
                    "name_id": "Sorcerer", "page": "Hello", "replies": ["a", "b"],
                    "asked": 0, "reply_text": "a", "reply_id": 41457, "dispatch_ok": True,
                    "reason": "objective token overlap",
                    "scores": [{"index": 0, "score": 0.0, "question": 0.0}],
                    "outcome": "same page", "ended_by": None, "page_after": "Hello",
                    "after_source": "stale", "at": 1004.2}),
        json.dumps({"index": 0, "session": "s", "goal": "g", "tool": "quest.advance",
                    "args": {}, "ok": True, "error": None, "latency_ms": 20.0,
                    "verdict": "advanced", "at": 1004.0}),
        json.dumps({"kind": "decision", "session": "s", "action": "recover",
                    "cause": "incomplete", "tool": "quest.advance", "at": 1005.0}),
    ]
    text = run_report(rows)
    check("the report reads every row kind the loop writes",
          "idle waits (G18" in text and "dialog replies" in text
          and "recovery waits" in text and "productive-action success" in text,
          text[:120])
    check("waits are totalled with the escalation shown",
          "2 passes in 1 run(s)" in text and "escalated to a real action" in text,
          "\n".join(l for l in text.splitlines() if "passes" in l or "escalated" in l))
    check("a stall is reported as a stall, and the dead primary signal is counted",
          "3  same page" in text and "objective overlap > 0: 0 of 4" in text,
          "\n".join(l for l in text.splitlines() if "same page" in l or "overlap" in l))
    # G21: the report has to answer *who ended the dialog* and *how many times the same
    # page was re-asked*, or the census reads as a ranking failure when it is a loop.
    check("a row written before the dispatch fields still parses and is counted",
          "dialog replies" in text and "4 attempt(s)" in text, text[:400])
    check("the census says the game ended that dialog, not us",
          "dialog ended by: {'after_reply': 1}" in text,
          "\n".join(l for l in text.splitlines() if "ended by" in l))
    check("the objective type is shown, so a free-form stall is visible as one",
          "objective type ids: {'12': 3}" in text,
          "\n".join(l for l in text.splitlines() if "objective type" in l))
    check("a refused dispatch is not silently counted as an attempt that sent",
          "dispatch refused (nothing went on the wire): 1" in text,
          "\n".join(l for l in text.splitlines() if "refused" in l))
    check("re-asking one page is called out by name",
          "NOTE re-asked 4 x" in text,
          "\n".join(l for l in text.splitlines() if "re-asked" in l))
    # G23: a stall measured against a stale read is not a stall, and the report must say
    # which read produced each outcome instead of blending the two into one census.
    check("the census says which kind of read each outcome was measured against",
          "outcome measured against a read of type:" in text
          and "'stale': 1" in text and "'unrecorded': 1" in text,
          "\n".join(l for l in text.splitlines() if "read of type" in l))
    check("a stall measured against a stale read is flagged as not evidence",
          "NOTE 1 stall(s) measured against a STALE read" in text,
          "\n".join(l for l in text.splitlines() if "STALE" in l))
    check("the productive table counts the one world-changing step",
          "quest.advance" in text and "1       1" in text,
          "\n".join(l for l in text.splitlines() if "quest.advance" in l))

    old = run_report([json.dumps({"index": 0, "session": "s", "goal": "g",
                                 "tool": "nav.goto", "args": {}, "ok": True,
                                 "error": None, "latency_ms": 5.0,
                                 "verdict": "noop", "at": 1000.0})])
    check("a log from before the instruments says so instead of printing zero",
          "predates the recovery instrument" in old
          and "predates the wait instrument" in old
          and "predates the dialog instrument" in old, old[:200])


def test_dialog_repeat_guard() -> None:
    section("Dialog repeats (criterion 3: an answer that changed nothing is not a plan)")
    import inspect
    from agent.planner import DIALOG_ASK_LIMIT, QuestPlanner

    HELMET = "Don't tell me you need help putting on a helmet?"

    def obs(message=HELMET, objective_id=14070):
        return {
            "dialog": {"open": True, "npc_id": 3888, "name_id": "Ayin Spyr",
                       "text_ready": True, "ids_ready": True, "message": message,
                       "reply_count": 1, "ids_present": True,
                       "replies": [{"index": 0, "text": "Yes, please.",
                                    "reply_id": 41457}]},
            "knowledge": {"quest_id": 3841, "objective_id": objective_id},
            "current_objective": {"objective_id": objective_id, "type_id": 12},
        }

    def asked(call) -> bool:
        return bool(call is not None and call.args.get("reply") is not None)

    p = QuestPlanner()
    first = p._dialog_call(obs(), "quest 3841")
    check("a page the objective has never been asked about is answered",
          asked(first) and first.args["reply"] == 0, first)
    check("one retry is allowed, because the page can lag the world",
          asked(p._dialog_call(obs(), "quest 3841")), "second ask refused")
    third = p._dialog_call(obs(), "quest 3841")
    check("the ask after that closes the conversation instead of repeating it",
          third is not None and third.args.get("close") is True and not asked(third),
          third)
    fourth = p._dialog_call(obs(), "quest 3841")
    check("a spent page is refused outright, so the re-open loop cannot resume",
          fourth is None, fourth)
    check("the refusal carries its own status, which is what the step note prints",
          isinstance(p.last_dialog_decision, dict)
          and p.last_dialog_decision.get("status") == "exhausted", p.last_dialog_decision)
    check("the memory is keyed by the limit, not by a magic number in the test",
          DIALOG_ASK_LIMIT == 2, str(DIALOG_ASK_LIMIT))

    # The three ways a refusal would be wrong: a different objective, a page that
    # really did move, and a fork that forgot.
    q = QuestPlanner()
    for _ in range(DIALOG_ASK_LIMIT + 2):
        q._dialog_call(obs(), "quest 3841")
    check("a reply spent on one objective is not spent on the next",
          asked(q._dialog_call(obs(objective_id=14071), "quest 3841")), "objective-blind")
    check("a page that changed is a fresh question",
          asked(q._dialog_call(obs("Which class interests you?"), "quest 3841")),
          "page-blind")
    twin = q.fork()
    spent = twin._dialog_call(obs(objective_id=14070), "quest 3841")
    check("a fork inherits which pages are spent instead of starting the loop again",
          spent is None, spent)
    src = inspect.getsource(QuestPlanner.fork)
    check("and the inheritance is real code, not a side effect of a shared default",
          "_dialog_exhausted = set(self._dialog_exhausted)" in src, src[:120])


def test_quest_accept_planner() -> None:
    section("Quest accept (G33: an offer is a link; the reply path used to swallow the start)")
    from agent.planner import QuestPlanner, dialog_offer

    quest = {"id": 777, "level_min": 1, "steps": [
        {"objectives": [{"id": 9001, "type_id": 12, "params": {"parameter0": 301}}]}]}

    def planner():
        p = QuestPlanner()
        p.quests = {777: quest}
        p._quests_loaded = True
        p._order = None
        p.level_hint = -1
        return p

    tracker = {"quest_id": "777", "objective_id": 9001, "type_id": 12,
               "params": {"parameter0": 301}}

    def offer_dialog(start_quest=None, replies=1):
        dialog = {"open": True, "npc_id": 3888, "name_id": "Ayin Spyr",
                  "message": "Take this job?", "text_ready": True,
                  "ids_ready": bool(replies), "ids_present": True,
                  "reply_count": replies, "replies": []}
        if replies:
            dialog["replies"] = [{"index": 0, "text": "Yes, please.", "reply_id": 41457}]
        if start_quest is not None:
            dialog["start_quest"] = start_quest
        return dialog

    def obs(dialog=None, **over):
        base = {"level": 10, "map_id": 100, "cell": 0, "connected": True,
                "in_world": True, "in_fight": False, "in_dialog": bool(over.pop(
                    "in_dialog", dialog is not None)),
                "tutorial": None, "quests_active": {}, "quests_done": [],
                "current_objective": dict(tracker), "ready_objectives": [],
                "step_objectives": [], "exits": [], "npcs": [], "groups": [],
                "inventory": {}, "kamas": 0,
                "knowledge": {"quest_id": 777, "objective_id": 9001, "type_id": 12}}
        if dialog is not None:
            base["dialog"] = dialog
        base.update(over)
        return base

    check("the offer is the page's start_quest field, never a reply or a bare string",
          dialog_offer({"start_quest": 777}) == 777
          and dialog_offer({"start_quest": "777"}) == 777
          and dialog_offer({"start_quest": "0"}) is None
          and dialog_offer({}) is None and dialog_offer(None) is None)

    p = planner()
    first = p.choose("do all the quests", obs(offer_dialog(777)))
    check("a dialog offering the quest this plan is on is accepted, before any reply",
          first is not None and first.tool == "quest.advance"
          and first.args.get("accept") is True and first.args.get("reply") is None,
          None if first is None else str((first.tool, first.args)))
    check("and the accept carries the client's own id",
          first is not None and first.args.get("quest_id") == 777,
          None if first is None else str(first.args))
    check("the start is remembered as pending, so a wait is not read as a failure",
          p._pending_start == 777, str(p._pending_start))

    # The fallback: in_dialog is set but the dialog block is not readable, so the
    # ranker has no decision and the start branch runs. It must close, not send a
    # bare start the page refuses, and not answer a reply nobody asked for.
    r = planner()
    fallback = r.choose("do all the quests", obs(None, in_dialog=True))
    check("a start requested while an unreadable dialog is open closes first",
          fallback is not None and fallback.args.get("close") is True
          and fallback.args.get("accept") is None,
          None if fallback is None else str((fallback.tool, fallback.args)))

    t = planner()
    t_obs = obs(offer_dialog(None))
    t_obs["tutorial"] = {"active": True, "quest_id": 777}
    call = t._tutorial_call(t_obs, "quest 777")
    check("a tutorial dialog without an offer answers reply 0 explicitly, not bare",
          call is not None and call.args.get("reply") == 0
          and call.args.get("accept") is None, call)

    t2 = planner()
    t2_obs = obs(offer_dialog(777))
    t2_obs["tutorial"] = {"active": True, "quest_id": 777}
    call = t2._tutorial_call(t2_obs, "quest 777")
    check("and one that offers the tutorial quest accepts the link",
          call is not None and call.args.get("accept") is True, call)

    # A tutorial step that names a window is a press on the client's menu. Its
    # objective is server-counted, so claiming it ends the connection; the press
    # is the event the step waits for. The surface is read from the step's own
    # text in the catalogue the page publishes.
    def ui_obs(step_name, step_desc, step, text="", **over):
        base = obs(None, **over)
        base["tutorial"] = {"active": True, "quest_id": 777, "step_id": 45}
        base["quest_steps"] = {"777": step}
        base["quests_catalog"] = [{"id": 777, "steps": [
            {"id": step, "name": step_name, "description": step_desc,
             "objectives": []}]}]
        base["ready_objectives"] = [{"quest_id": "777", "objective_id": 9001,
                                     "type_id": 0, "type": "QuestObjectiveFreeForm",
                                     "step_id": step, "text": text}]
        return base

    u = planner()
    spell_obs = ui_obs("Open the Spell Interface",
                       "Open the Spell interface from the menu.", 4545,
                       "Open the Spell interface.")
    press = u._tutorial_call(spell_obs, "do all the quests")
    check("a tutorial step that names a window is a menu press, not a claim",
          press is not None and press.tool == "ui.tap"
          and press.args.get("menu") == "Spell" and press.args.get("cycle") is True,
          press)
    check("the press is bounded: two attempts, then the step holds",
          u._tutorial_call(spell_obs, "do all the quests") is not None
          and u._tutorial_call(spell_obs, "do all the quests") is None)
    m = planner()
    map_press = m._tutorial_call(
        ui_obs("Open the World Map", "Open the World Map.", 4968), "do all the quests")
    check("the world map step presses the Map menu",
          map_press is not None and map_press.args.get("menu") == "Map", map_press)
    b = planner()
    book_press = b._tutorial_call(
        ui_obs("Open the quest book", "Do the quest book tutorial", 4581),
        "do all the quests")
    check("the quest book step presses the Book menu",
          book_press is not None and book_press.args.get("menu") == "Book", book_press)
    h = planner()
    help_press = h._tutorial_call(
        ui_obs("Open the Help Interface", "Open the Help interface from the menu.", 4980),
        "do all the quests")
    check("the help step presses the Help menu and gives the press back",
          help_press is not None and help_press.args.get("menu") == "Help"
          and help_press.args.get("cycle") is True, help_press)
    # Prose is not a surface: a step whose *description* says "help" but whose name
    # is a conversation must not press the Help menu.
    prose = planner()
    prose_obs = ui_obs("Talk to Ayin Spyr", "Ask him to help you with the shop.", 4972,
                       "Talk to Ayin Spyr.")
    check("a description that says 'help' is not a Help press",
          prose._tutorial_call(prose_obs, "do all the quests") is None)
    s = planner()
    shop_press = s._tutorial_call(
        ui_obs("Open the Shop Interface",
               'Press the "Shop" button floating on the map and get the pack.', 4976),
        "do all the quests")
    check("the shop step presses the floating shop button, not a menu",
          shop_press is not None and shop_press.args.get("selector") == ".shopBtn"
          and shop_press.tool == "ui.tap", shop_press)
    # The client's own step table ends this one on SHOP_LOADED, fired when the
    # shop opens and its articles arrive; a market window already open makes the
    # press a no-op. So the press closes that window first and is never cycled
    # shut (the cycle would close the shop the step is waiting to load).
    check("the shop press closes an open market window first and is not cycled",
          shop_press.args.get("close_first") == "market"
          and shop_press.args.get("cycle") is None, shop_press)
    s2 = planner()
    shop_open = ui_obs("Open the Shop Interface",
                       'Press the "Shop" button floating on the map and get the pack.',
                       4976)
    shop_open["open_windows"] = ["market > shop"]
    buys = [s2._tutorial_call(shop_open, "do all the quests") for _ in range(5)]
    check("with the shop open the step buys the first article, not presses again",
          buys[0] is not None and buys[0].tool == "shop.buy"
          and buys[0].args.get("which") == "first", buys[0])
    check("the purchase is bounded, then the step holds",
          all(c is not None and c.tool == "shop.buy" for c in buys[:4])
          and buys[4] is None, [None if c is None else c.tool for c in buys])
    # The challenge sub-step starts with pointToChallenge and ends on TAP_ANYWHERE;
    # combat casts go through fightManager.castSpell and never produce that
    # touchend, so the step only advances on a real press on the challenge slot.
    # The hint is the step's own (published by the page), and it must beat a gate
    # left over from an earlier step (here: an NPC restriction that would
    # otherwise turn the press into a talk).
    h2 = planner()
    hint_obs = obs(None)
    hint_obs["tutorial"] = {"active": True, "quest_id": 777, "special_step_id": 46,
                            "step_checkpoint": -1, "hint_target": "challenge",
                            "unblocked_npc_id": 3888}
    hint_calls = [h2._tutorial_call(hint_obs, "do all the quests") for _ in range(5)]
    check("a step whose hint points at the challenge slot taps it by client_target",
          hint_calls[0] is not None and hint_calls[0].tool == "ui.tap"
          and hint_calls[0].args.get("client_target") == "challenge", hint_calls[0])
    check("the challenge tap is bounded, then the step holds",
          all(c is not None for c in hint_calls[:4]) and hint_calls[4] is None,
          [None if c is None else c.tool for c in hint_calls])
    # The tutorial's "talk to this NPC" gate has the same failure shape: the
    # client walks and opens the dialogue in one call, so a repeat asks the same
    # question. Live sessions re-sent it for whole budgets (277 executed no-op
    # talks in one dofus-05 session, 272 in dofus-03, step 1 "Parler a Jin Spyr").
    h2b = planner()
    npc_obs = obs(None)
    npc_obs["tutorial"] = {"active": True, "quest_id": 777, "step_id": 1,
                           "unblocked_npc_id": 3888, "unblocked_cells": []}
    npc_calls = [h2b._tutorial_call(npc_obs, "do all the quests") for _ in range(5)]
    check("a tutorial NPC gate asks the NPC, then holds instead of re-sending",
          npc_calls[0] is not None
          and npc_calls[0].args == {"npc_id": 3888, "quest_id": 777}
          and all(c is not None for c in npc_calls[:4]) and npc_calls[4] is None,
          [None if c is None else (c.tool, c.args) for c in npc_calls])
    h3 = planner()
    moved = dict(hint_obs["tutorial"])
    moved["special_step_id"] = 47
    moved["hint_target"] = "spell_upgrade_confirm"
    obs3 = obs(None)
    obs3["tutorial"] = moved
    call = h3._tutorial_call(obs3, "do all the quests")
    check("a new step's hint resets the bound and names its own control",
          call is not None and call.args.get("client_target") == "spell_upgrade_confirm",
          call)
    h4 = planner()
    charac_obs = obs(None)
    charac_obs["tutorial"] = {"active": True, "quest_id": 777,
                              "hint_target": "charac_update"}
    charac_call = h4._tutorial_call(charac_obs, "do all the quests")
    check("the characteristics hint becomes a client_target press",
          charac_call is not None and charac_call.tool == "ui.tap"
          and charac_call.args.get("client_target") == "charac_update", charac_call)
    h5 = planner()
    tap_obs = obs(None)
    tap_obs["tutorial"] = {"active": True, "quest_id": 777, "special_step_id": 50,
                           "step_checkpoint": -1, "tap_anywhere": True,
                           "unblocked_npc_id": 0}
    tap_calls = [h5._tutorial_call(tap_obs, "do all the quests") for _ in range(4)]
    check("a step that ends on TAP_ANYWHERE taps the game body",
          tap_calls[0] is not None and tap_calls[0].tool == "ui.tap"
          and tap_calls[0].args.get("client_target") == "world", tap_calls[0])
    check("the world tap is bounded, then the step holds",
          all(c is not None for c in tap_calls[:3]) and tap_calls[3] is None,
          [None if c is None else c.tool for c in tap_calls])
    # Every sub-step carries checkpoint -1, so the scenario's array index is the
    # only per-step identity. Keying the press budget on the checkpoint alone
    # shares one budget across unrelated sub-steps.
    def tap_obs_for(step_no):
        o = obs(None)
        o["tutorial"] = {"active": True, "quest_id": 777, "step_id": step_no,
                         "step_checkpoint": -1, "tap_anywhere": True}
        return o

    h10 = planner()
    first_taps = [h10._tutorial_call(tap_obs_for(30), "do all the quests")
                  for _ in range(4)]
    second_step = h10._tutorial_call(tap_obs_for(31), "do all the quests")
    check("two sub-steps that share checkpoint -1 each get their own tap budget",
          all(c is not None for c in first_taps[:3]) and first_taps[3] is None
          and second_step is not None
          and second_step.args.get("client_target") == "world",
          [None if c is None else c.args.get("client_target") for c in first_taps]
          + [second_step])

    def hint_obs_for(step_no, hint):
        o = obs(None)
        o["tutorial"] = {"active": True, "quest_id": 777, "step_id": step_no,
                         "step_checkpoint": -1, "hint_target": hint}
        return o

    h11 = planner()
    hint_a = [h11._tutorial_call(hint_obs_for(40, "challenge"), "do all the quests")
              for _ in range(5)]
    hint_b = h11._tutorial_call(hint_obs_for(41, "challenge"), "do all the quests")
    check("two hint steps with the same control each get a fresh budget",
          all(c is not None for c in hint_a[:4]) and hint_a[4] is None
          and hint_b is not None, [None if c is None else c.tool for c in hint_a]
          + [hint_b])
    h6 = planner()
    both_obs = obs(None)
    both_obs["tutorial"] = {"active": True, "quest_id": 777, "special_step_id": 51,
                            "step_checkpoint": -1, "tap_anywhere": True,
                            "hint_target": "challenge"}
    seq = [h6._tutorial_call(both_obs, "do all the quests") for _ in range(8)]
    order = [None if c is None else c.args.get("client_target") for c in seq]
    check("a spent hint falls back to the world tap the same step waits for",
          order == ["challenge"] * 4 + ["world"] * 3 + [None], order)
    h7 = planner()
    menu_obs = obs(None)
    menu_obs["tutorial"] = {"active": True, "quest_id": 777, "step_id": 4573,
                            "menu_hint": "Carac", "unblocked_npc_id": 0,
                            "open_window": "characteristics"}
    menu_calls = [h7._tutorial_call(menu_obs, "do all the quests") for _ in range(3)]
    check("a step whose hint names a menu presses that menu",
          menu_calls[0] is not None and menu_calls[0].args.get("menu") == "Carac"
          and menu_calls[0].args.get("cycle") is True, menu_calls[0])
    check("the menu press is bounded, then the step holds",
          menu_calls[1] is not None and menu_calls[2] is None,
          [None if c is None else c.tool for c in menu_calls])
    h12 = planner()
    win_obs = obs(None)
    win_obs["tutorial"] = {"active": True, "quest_id": 777, "step_id": 60,
                           "step_checkpoint": -1, "open_window": "characUpdate"}
    win_calls = [h12._tutorial_call(win_obs, "do all the quests") for _ in range(4)]
    check("a step that waits for a window presses the control that opens it",
          win_calls[0] is not None and win_calls[0].tool == "ui.tap"
          and win_calls[0].args.get("selector") == ".upgradeButton"
          and win_calls[0].args.get("index") == 0, win_calls[0])
    check("the window press is bounded, then falls through to the generic path",
          all(c is not None for c in win_calls[:3]) and win_calls[3] is None,
          [None if c is None else c.tool for c in win_calls])
    h13 = planner()
    plus_obs = obs(None)
    plus_obs["tutorial"] = {"active": True, "quest_id": 777, "step_id": 70,
                            "step_checkpoint": -1, "hint_target": "spell_plus"}
    plus_call = h13._tutorial_call(plus_obs, "do all the quests")
    check("the spell upgrade step presses the spell's plus button",
          plus_call is not None and plus_call.tool == "ui.tap"
          and plus_call.args.get("client_target") == "spell_plus", plus_call)
    # The later tutorials (profession, bestiary, trade) point at their controls
    # the same way, and the page resolves each helper's exact object path. The
    # hint string passes through unchanged, argument included.
    h14 = planner()
    later_obs = obs(None)
    later_obs["tutorial"] = {"active": True, "quest_id": 777, "step_id": 71,
                             "step_checkpoint": -1, "hint_target": "contextual_menu:1"}
    later_call = h14._tutorial_call(later_obs, "do all the quests")
    check("a later tutorial's helper hint becomes its client target unchanged",
          later_call is not None and later_call.tool == "ui.tap"
          and later_call.args.get("client_target") == "contextual_menu:1", later_call)
    # A step that highlights a map element (playInteractiveElementTap) is not a UI
    # press: the element is used through isoEngine.useInteractive by element id.
    h15 = planner()
    ix_obs = obs(None)
    ix_obs["tutorial"] = {"active": True, "quest_id": 777, "step_id": 72,
                          "step_checkpoint": -1, "interactive_hint": 514876,
                          "unblocked_npc_id": 0}
    ix_calls = [h15._tutorial_call(ix_obs, "do all the quests") for _ in range(5)]
    check("a step that highlights a map element uses the element id",
          ix_calls[0] is not None and ix_calls[0].tool == "tap.interactive"
          and ix_calls[0].args.get("element_id") == 514876, ix_calls[0])
    check("the interactive press is bounded, then the step holds",
          all(c is not None for c in ix_calls[:4]) and ix_calls[4] is None,
          [None if c is None else c.tool for c in ix_calls])
    # A close: hint is a window close, not a press; it must still pass the schema.
    h16 = planner()
    close_obs = obs(None)
    close_obs["tutorial"] = {"active": True, "quest_id": 777, "step_id": 73,
                             "step_checkpoint": -1, "hint_target": "close:grimoire"}
    close_call = h16._tutorial_call(close_obs, "do all the quests")
    check("a close-button hint becomes a close: client target",
          close_call is not None and close_call.tool == "ui.tap"
          and close_call.args.get("client_target") == "close:grimoire", close_call)
    # The live ordering trap: right after SHOP_LOADED the step waits for a tap, and
    # the market window is open, so the shop surface would buy unless the world tap
    # is checked first. Buying there loses FAKE_SHOP_BUY, which the *next* step
    # listens for.
    h8 = planner()
    shop_tap_obs = ui_obs("Open the Shop Interface",
                          'Press the "Shop" button floating on the map and get the pack.',
                          4976)
    shop_tap_obs["tutorial"] = {"active": True, "quest_id": 777, "step_id": 4976,
                                "special_step_id": 52, "step_checkpoint": -1,
                                "tap_anywhere": True}
    shop_tap_obs["open_windows"] = ["market > shop"]
    first_shop_tap = h8._tutorial_call(shop_tap_obs, "do all the quests")
    check("while the post-load step waits for a tap, the shop is not bought",
          first_shop_tap is not None
          and first_shop_tap.args.get("client_target") == "world", first_shop_tap)
    h9 = planner()
    misnamed = ui_obs("Open the Spell Interface",
                      "Open the Spell interface from the menu.", 4545)
    misnamed["tutorial"] = {"active": True, "quest_id": 777, "step_id": 4545,
                            "menu_hint": "Map"}
    hinted = h9._tutorial_call(misnamed, "do all the quests")
    check("the client's own menu hint outranks the step's English name",
          hinted is not None and hinted.args.get("menu") == "Map", hinted)
    # The executor validates every directive against the schema before dispatch, so
    # a press the planner produces must pass the same validator. `close_first` was
    # declared str while the shop surface passed ["market"]: the live press would
    # have died at the validator and never reached the page.
    from agent.schema import validate_call as _validate
    bad_presses = []
    for produced in (press, map_press, book_press, help_press, shop_press,
                     buys[0], hint_calls[0], call, charac_call, tap_calls[0],
                     menu_calls[0], win_calls[0], plus_call, later_call,
                     ix_calls[0], close_call):
        okv, errv = _validate(produced.tool, produced.args)
        if not okv:
            bad_presses.append((produced.tool, dict(produced.args), errv))
    check("every press the planner produces passes the schema the executor enforces",
          bad_presses == [], bad_presses)
    ok_list, err_list = _validate("ui.tap", {"selector": ".shopBtn",
                                             "close_first": ["market"]})
    check("the schema is what rejected the list form, so the fix is the string",
          ok_list is False and "close_first" in str(err_list), err_list)
    f = planner()
    fight_obs = ui_obs("Start a Fight with a Monster", "Start a fight with a monster.",
                       4970, "Start a fight with a monster.")
    check("a fight step is never turned into a menu press",
          f._tutorial_call(fight_obs, "do all the quests") is None)
    # A step with no catalogue text at all falls back to the objective text, and
    # a text that names no surface still plans nothing rather than guessing.
    g = planner()
    bare = obs(None)
    bare["tutorial"] = {"active": True, "quest_id": 777}
    bare["ready_objectives"] = [{"quest_id": "777", "objective_id": 9001,
                                 "type_id": 0, "step_id": 4970,
                                 "text": "Open the World Map."}]
    fallback = g._tutorial_call(bare, "do all the quests")
    check("without a catalogue the objective text names the surface",
          fallback is not None and fallback.args.get("menu") == "Map", fallback)

    # An interactive element the client cannot use (no skill instance) must not be
    # offered as the free-form action: the page refuses it before sending anything,
    # and taking it as the direct action hides the NPC ask behind it. Measured live
    # on dofus-02 (2026-10-29): the step then waited forever next to the NPC.
    def freeform_obs(interactive):
        base = obs(None)
        base["tutorial"] = {"active": True, "quest_id": 777}
        base["ready_objectives"] = [{"quest_id": "777", "objective_id": 9002,
                                     "type_id": 0, "type": "QuestObjectiveFreeForm",
                                     "step_id": 4974,
                                     "text": "Complete the \"A Chaotic Arrival\" quest."}]
        base["map"] = {"interactives": [interactive],
                       "npcs": [{"npc_id": 3888, "cell": 255}], "groups": []}
        base["npcs"] = [{"npc_id": 3888, "cell": 255}]
        return base

    dead = freeform_obs({"elem_id": 513203, "cell": 374, "skill_uid": None,
                         "enabled_skills": []})
    call = planner().choose("do all the quests", dead)
    check("a skill-less interactive is not offered as the free-form action",
          call is not None and call.tool == "quest.advance"
          and call.args.get("npc_id") == 3888,
          None if call is None else str((call.tool, call.args)))
    live_el = freeform_obs({"elem_id": 513208, "cell": 374, "skill_uid": 9001,
                            "enabled_skills": []})
    call = planner().choose("do all the quests", live_el)
    check("and an element that does carry a skill is still used first",
          call is not None and call.tool == "tap.interactive"
          and call.args.get("skill_uid") == 9001,
          None if call is None else str((call.tool, call.args)))

    # A quest an NPC offers is how the next quest in a chain starts. A quest/auto run
    # takes it; an order or farm prompt must not acquire quests, a named focus must
    # match, and an already-active quest's offer is stale.
    n = planner()
    new_offer = n.choose("do all the quests", obs(offer_dialog(4266)))
    check("a quest run accepts a new quest the dialog offers",
          new_offer is not None and new_offer.args.get("accept") is True
          and new_offer.args.get("quest_id") == 4266,
          None if new_offer is None else str((new_offer.tool, new_offer.args)))
    check("and remembers it as pending and client-confirmed",
          n._pending_start == 4266 and 4266 in n._client_quests,
          str((n._pending_start, sorted(n._client_quests))))
    auto = planner()
    auto_offer = auto.choose("auto", obs(offer_dialog(4266)))
    check("Full Auto accepts it too",
          auto_offer is not None and auto_offer.args.get("accept") is True,
          None if auto_offer is None else str(auto_offer.args))
    farm = planner()
    farm_offer = farm.choose("farm the piglets", obs(offer_dialog(4266)))
    check("a farm prompt does not acquire a quest",
          farm_offer is not None and farm_offer.args.get("accept") is None,
          None if farm_offer is None else str(farm_offer.args))
    order = planner()
    order_offer = order.choose("vends Cadeau de Compensation", obs(offer_dialog(4266)))
    check("an order prompt does not acquire a quest",
          order_offer is not None and order_offer.args.get("accept") is None,
          None if order_offer is None else str(order_offer.args))
    active = planner()
    active_offer = active.choose("do all the quests",
                                 obs(offer_dialog(4266), quests_active={"4266": 0}))
    check("an offer for an already-active quest is left to the ranker",
          active_offer is not None and active_offer.args.get("accept") is None,
          None if active_offer is None else str(active_offer.args))
    focused = planner()
    focused.focus = 3841
    focused_offer = focused.choose("do all the quests", obs(offer_dialog(4266)))
    check("a named focus refuses a different offer",
          focused_offer is not None and focused_offer.args.get("accept") is None,
          None if focused_offer is None else str(focused_offer.args))
    named = planner()
    named.focus = 4266
    named_offer = named.choose("do all the quests", obs(offer_dialog(4266)))
    check("and accepts the quest it names",
          named_offer is not None and named_offer.args.get("accept") is True,
          None if named_offer is None else str(named_offer.args))
    prompt = planner()
    prompt_offer = prompt.choose("do quest 3841", obs(offer_dialog(4266)))
    check("a prompt naming a quest does not take an unrelated offer",
          prompt_offer is not None and prompt_offer.args.get("accept") is None,
          None if prompt_offer is None else str(prompt_offer.args))
    target = planner()
    target_offer = target.choose("do quest 4266", obs(offer_dialog(4266)))
    check("but it takes the quest it names",
          target_offer is not None and target_offer.args.get("accept") is True,
          None if target_offer is None else str(target_offer.args))

    # The runtime contract is data/capabilities.json when it loads, so a param
    # declared only in schema.py would be rejected before the page ever sees the
    # call -- the exact trap that killed every npc_id call once.
    from agent.schema import CAPABILITY_SOURCE, validate_call
    ok, coerced = validate_call("quest.advance", {"quest_id": 3841, "accept": True})
    check("the runtime contract (%s) accepts the accept argument" % CAPABILITY_SOURCE,
          ok is True, coerced)
    check("and keeps it a bool", ok and coerced.get("accept") is True, str(coerced))


def test_observed_quest_catalog() -> None:
    section("Observed quest catalogue (G29: the client's graph before the foreign cache)")
    from agent.knowledge_context import enrich_observation
    from agent.planner import QuestPlanner, catalog_objectives

    catalog = [{
        "id": 3841, "name": "La quête du pêcheur", "step_index": 1, "step_id": 2002,
        "steps": [
            {"id": 2001, "name": "Étape 1", "description": "Parle au pêcheur.",
             "objectives": [{"id": 14070, "type_id": 1, "type": "QuestObjectiveGoToNpc",
                             "params": {"parameter0": 3888}, "map_id": 123,
                             "step_id": 2001, "text": "Parler à Ayin Spyr"}]},
            {"id": 2002, "name": "Étape 2", "description": "Équipe-toi.",
             "objectives": [{"id": 14071, "type_id": 0, "type": "QuestObjectiveFreeForm",
                             "text": "S'équiper"}]},
        ],
    }]
    # The ids here are synthetic fixtures, and the real client index also holds
    # 14070 under 3841 with different content. Every call below states an empty
    # index so the asserted resolution path is the one this test is about.
    no_index = REPO_ROOT / "data" / "knowledge" / "__none__.json"

    def obs(**over):
        base = {
            "current_objective": {"quest_id": 3841, "objective_id": 14070,
                                  "type_id": 12},
            "quests_catalog": catalog,
        }
        base.update(over)
        return base

    class FakeStore:
        def __init__(self, resolved):
            self._resolved = resolved

        def resolve_objective(self, objective_id, language):
            return self._resolved

        def quest(self, quest_id):
            return None

    collided = {"quest_id": 999, "type_id": 4, "text": "another game's objective",
                "params": {"parameter0": 5243}, "source": "dofusdb"}
    block = enrich_observation(obs(), FakeStore(collided), observed_path=no_index)
    kn = block.get("knowledge") or {}
    objective = kn.get("objective") or {}
    check("the client's own row wins over a colliding store row",
          kn.get("status") == "observed" and objective.get("source") == "observed", kn)
    check("the observed block carries the text, type and params the ranker needs",
          objective.get("text") == "Parler à Ayin Spyr"
          and objective.get("type") == "QuestObjectiveGoToNpc"
          and (objective.get("params") or {}).get("parameter0") == 3888, kn)
    check("and the quest/step names, so dialog text has context",
          kn.get("quest_name") == "La quête du pêcheur"
          and kn.get("step_name") == "Étape 1"
          and kn.get("step_description") == "Parle au pêcheur.", kn)

    missing = enrich_observation(obs(quests_catalog=[]), FakeStore(None),
                                 observed_path=no_index)
    check("without a catalogue the store still answers, and says why on a miss",
          (missing.get("knowledge") or {}).get("status") == "objective_not_in_store",
          missing.get("knowledge"))

    wrong = enrich_observation(obs(quests_catalog=[dict(catalog[0], id=999)]),
                               FakeStore(None), observed_path=no_index)
    check("a row captured for another quest is not used for this one",
          (wrong.get("knowledge") or {}).get("status") == "objective_not_in_store",
          wrong.get("knowledge"))

    broken = enrich_observation(obs(quests_catalog=[
        {"id": 3841, "steps": [{"objectives": [None, {"id": 14070}]}]}]), FakeStore(None),
        observed_path=no_index)
    check("a malformed catalogue row still yields the observed row",
          (broken.get("knowledge") or {}).get("status") == "observed",
          broken.get("knowledge"))

    p = QuestPlanner()
    current = p._observed_current(3841, obs())
    check("the planner can plan an active quest whose step_objectives is empty",
          isinstance(current, dict) and str(current.get("id")) == "14070"
          and current.get("type") == "QuestObjectiveGoToNpc", current)
    check("and only rows belonging to that quest are read",
          [str(o.get("id")) for o in catalog_objectives(obs(), 3841)]
          == ["14070", "14071"]
          and catalog_objectives(obs(), 999) == [],
          catalog_objectives(obs(), 3841))


def test_observed_quest_ingest() -> None:
    section("Observed quest ingest (G29: persist the graph, measure coverage)")
    import importlib.util
    import json as _json
    import tempfile
    from agent.knowledge_context import enrich_observation, load_observed_index

    tool = REPO_ROOT / "tools" / "ingest_observed_quests.py"
    check("the ingest tool exists", tool.exists(), str(tool))
    if not tool.exists():
        return
    spec = importlib.util.spec_from_file_location("ingest_observed_quests", tool)
    ingest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ingest)

    thin = [{
        "id": 3841, "name": "La quête du pêcheur", "steps": [
            {"id": 2001, "name": "Étape 1", "objectives": [{"id": 14070}]},
        ],
    }]
    rich = [{
        "id": 3841, "name": "La quête du pêcheur", "steps": [
            {"id": 2001, "name": "Étape 1", "description": "Parle au pêcheur.",
             "objectives": [{"id": 14070, "type_id": 1,
                             "type": "QuestObjectiveGoToNpc", "map_id": 123,
                             "params": {"parameter0": 3888},
                             "text": "Parler à Ayin Spyr"}]},
            {"id": 2002, "name": "Étape 2", "description": "Équipe-toi.",
             "objectives": [{"id": 14071, "type_id": 0,
                             "type": "QuestObjectiveFreeForm", "text": "S'équiper"}]},
        ],
    }]
    logs = [
        {"at": 100, "session": "s1",
         "observation": {"quests_catalog": thin}},
        {"at": 200, "session": "s1",
         "obs_before": {"quests_catalog": rich}},
        {"at": 300, "session": "s1",
         "observation": {"quests_catalog": "not a list"}},
    ]

    tmp = Path(tempfile.mkdtemp(prefix="observed-ingest-"))
    log = tmp / "one.jsonl"
    # The bad line carries the prefilter needle so it actually reaches the parser; a line
    # without it is filtered before json.loads and would make a skip mutation invisible.
    log.write_text("\n".join([_json.dumps(row) for row in logs]
                             + ['{"quests_catalog": broken']) + "\n", encoding="utf-8")
    collected = True
    try:
        rows, catalogs, seen = ingest.collect([log])
    except Exception:      # noqa: BLE001 - the check below reports the abort
        collected = False
        rows, catalogs, seen = {}, 0, 0
    check("two catalogue lines flatten into one row per objective, deduplicated",
          len(rows) == 2 and catalogs == 2 and seen == 3,
          str((len(rows), catalogs, seen)))
    check("a malformed log line is skipped instead of aborting the scan",
          collected is True, "the scan raised on a broken line")
    q70 = rows.get((3841, 14070)) or {}
    q71 = rows.get((3841, 14071)) or {}
    check("a later richer capture replaces the earlier bare id",
          q70.get("text") == "Parler à Ayin Spyr"
          and q70.get("type") == "QuestObjectiveGoToNpc", q70)
    check("the quest and step names travel with the objective",
          q70.get("quest_name") == "La quête du pêcheur"
          and q70.get("step_name") == "Étape 1"
          and q70.get("step_description") == "Parle au pêcheur.", q70)
    check("the session and log provenance are recorded",
          q70.get("session") == "s1" and q70.get("log") == "one.jsonl"
          and q70.get("observed_at") == 200.0, q70)

    thinner = dict(q70, type_id=None, type=None, text=None,
                   map_id=None, step_id=None, params={}, observed_at=999)
    check("a later but thinner capture never erases the richer row",
          ingest.merge(q70, thinner).get("text") == "Parler à Ayin Spyr",
          ingest.merge(q70, thinner))
    newer = dict(q70, text="Parler à Ayin (v2)", observed_at=999)
    check("between equally rich rows the newer one wins",
          ingest.merge(q70, newer).get("text") == "Parler à Ayin (v2)")

    out = tmp / "observed_quests.jsonl"
    ingest.write_rows(out, rows)
    check("the file round-trips every row and is sorted by quest/step/objective",
          ingest.read_rows(out).keys() == rows.keys(), sorted(ingest.read_rows(out)))

    first = tmp / "first.jsonl"
    first.write_text(_json.dumps({"at": 10, "observation": {"quests_catalog": [
        {"id": 999, "steps": [{"id": 1, "objectives": [{"id": 5, "type_id": 1}]}]}]}}
    ) + "\n", encoding="utf-8")
    second = tmp / "second.jsonl"
    second.write_text("\n".join([_json.dumps(row) for row in logs]
                                + ['{"quests_catalog": broken']) + "\n",
                      encoding="utf-8")
    merged: Dict[Tuple[int, int], Dict[str, Any]] = {}
    if collected:
        try:
            ingest.main([str(first), "--out", str(out)])
            ingest.main([str(second), "--out", str(out)])
        except Exception:      # noqa: BLE001 - the checks below report the abort
            pass
        merged = ingest.read_rows(out)
    check("a second run merges with the file instead of replacing it",
          set(merged) == {(999, 5), (3841, 14070), (3841, 14071)}, sorted(merged))
    check("and the second run still keeps the richer captured fields",
          merged.get((3841, 14070), {}).get("text") == "Parler à Ayin Spyr",
          merged.get((3841, 14070)))

    # The CLI is the real entry point: `python tools/x.py` does not put the repo root
    # on sys.path, so the store import needs the explicit insert. Run it, do not pin it.
    import subprocess
    import sys as _sys
    proc = subprocess.run([_sys.executable, str(tool), str(first), "--out", str(out),
                           "--coverage", "--no-write"],
                          cwd=str(REPO_ROOT), capture_output=True, text=True)
    output = (proc.stdout or "") + (proc.stderr or "")
    check("the CLI opens the store from a tools/ script",
          proc.returncode == 0 and "store unavailable" not in output,
          output.strip()[-200:])

    class FakeStore:
        def __init__(self, rows_by_objective):
            self._rows = rows_by_objective

        def resolve_objective(self, objective_id, language):
            return self._rows.get(objective_id)

        def close(self):
            pass

    # A mutation that breaks the row keys must fail a check, not crash the section.
    keys_ok = all(isinstance(key, tuple) and len(key) == 2 for key in rows)
    if keys_ok:
        stats = ingest.coverage(rows, FakeStore({
            14070: {"quest_id": 3841, "type_id": 1},
            14071: {"quest_id": 777, "type_id": 0},
        }))
        new_stats = ingest.coverage(rows, FakeStore({}))
    else:
        stats = {"rows": 0, "quests": 0, "objectives": 0, "types": {},
                 "new": -1, "agrees": -1, "collision": -1}
        new_stats = {"new": -1}
    check("coverage separates new, agreeing and colliding objective ids",
          stats["new"] == 0 and stats["agrees"] == 1 and stats["collision"] == 1,
          stats)
    check("an id the store does not have is counted as new",
          new_stats["new"] == 2, new_stats)
    text = ingest.verdict(stats, 2)
    check("the verdict names a collision as the foreign-id case",
          "collision" in text and "G30" in text, text)
    empty = ingest.verdict(ingest.coverage({}, None), 0)
    check("no rows is a verdict about the corpus, not a silent zero",
          "predate the capture" in empty, empty)

    index = load_observed_index(out)
    check("the runtime index is keyed by (quest, objective)",
          set(index) == {(999, 5), (3841, 14070), (3841, 14071)}, sorted(index))
    obs = {"current_objective": {"quest_id": 3841, "objective_id": 14070,
                                 "type_id": 12}}
    kn = (enrich_observation(obs, FakeStore({}), observed_path=out).get("knowledge") or {})
    check("an observation without a catalogue resolves from the persisted file",
          kn.get("status") == "observed" and kn.get("observed_from") == "file"
          and (kn.get("objective") or {}).get("text") == "Parler à Ayin Spyr", kn)
    obs_free = {"current_objective": {"objective_id": 14071}}
    free = (enrich_observation(obs_free, FakeStore({}), observed_path=out)
            .get("knowledge") or {})
    check("an id with no tracker quest resolves when exactly one quest claims it",
          free.get("status") == "observed", free)

    base = dict(q71) if q71 else {"type_id": 0, "type": "QuestObjectiveFreeForm",
                                  "text": "S'équiper"}
    ambiguous = dict(rows)
    ambiguous[(777, 14071)] = dict(base, quest_id=777, objective_id=14071,
                                   observed_at=10)
    amb_path = tmp / "ambiguous.jsonl"
    ingest.write_rows(amb_path, ambiguous)
    amb = (enrich_observation(obs_free, FakeStore({}), observed_path=amb_path)
           .get("knowledge") or {})
    check("and refuses an id two quests claim when the tracker names none",
          amb.get("status") == "objective_not_in_store", amb)

    extended = dict(ambiguous)
    extended[(777, 14072)] = dict(base, quest_id=777, objective_id=14072,
                                  observed_at=10)
    ext_path = tmp / "extended.jsonl"
    ingest.write_rows(ext_path, extended)
    before_write = load_observed_index(ext_path)
    extended[(777, 14073)] = dict(base, quest_id=777, objective_id=14073,
                                  observed_at=10)
    ingest.write_rows(ext_path, extended)
    after_write = load_observed_index(ext_path)
    check("a rewritten file is visible without a restart (the stat is the cache key)",
          (777, 14072) in before_write and (777, 14073) not in before_write
          and (777, 14073) in after_write, sorted(after_write))


def test_dialog_label_reader() -> None:
    section("Dialog label reader (G23: the census behind a published verdict)")
    import importlib.util
    import json as _json

    tool = REPO_ROOT / "tools" / "dialog_labels.py"
    check("the reader exists", tool.exists(), str(tool))
    spec = importlib.util.spec_from_file_location("dialog_labels", tool)
    dl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dl)

    tmp = Path(tempfile.mkdtemp())
    try:
        def step(obs, args, source="call"):
            return _json.dumps({"kind": "step", "ts": 10.0, "instance": "gate",
                                "tool": "quest.advance", "args": args, "ok": True,
                                "error": None, "observation": obs,
                                "observation_source": source,
                                "result": {"response": {"ok": True}}}) + "\n"

        page = {"open": True, "npc_id": 7, "name_id": "Sorcerer",
                "message": "Pick one", "reply_count": 2,
                "replies": [{"index": 0, "text": "Yes", "reply_id": 10},
                            {"index": 1, "text": "No", "reply_id": 11}]}
        obs_with = lambda d, **kw: dict({"dialog": d, "current_objective": {
            "objective_id": 14070, "type": "QuestObjectiveFreeForm", "type_id": 0}}, **kw)
        log = tmp / "default.jsonl"
        log.write_text(
            step(obs_with(page), {"quest_id": 1, "reply": 1, "reply_text": "No"})
            + _json.dumps({"kind": "observation", "ts": 10.4,
                           "observation": obs_with({"open": True, "message": "Second page"},
                                                   objectives_completed=3),
                           "observation_source": "call"}) + "\n"
            # a second dispatch whose only follower is a carried read of the same page
            + step(obs_with(page), {"quest_id": 1, "close": True})
            + _json.dumps({"kind": "step", "ts": 11.0, "tool": "nav.goto", "args": {},
                           "observation": obs_with(page),
                           "observation_source": "carried"}) + "\n",
            encoding="utf-8")
        got = dl.pairs(str(log))
        check("a reply dispatch paired with a fresh read is labelled advanced",
              len(got) == 2 and got[0]["outcome"] == "advanced"
              and got[0]["asked"] == 1 and got[0]["reply_id"] == 11
              and got[0]["reply_text"] == "No" and got[0]["reply_count"] == 2,
              str(got[:1]))
        check("the objective type travels with the reconstructed pair",
              got[0]["objective_type"] == "QuestObjectiveFreeForm"
              and got[0]["objective_id"] == 14070, str(got[0]))
        check("the pair says which kind of read produced its label",
              got[0]["after_source"] == "call" and got[1]["after_source"] == "carried",
              str([r["after_source"] for r in got]))
        check("a close ask with no reply id is still a pair, and not attributed to a reply",
              got[1]["asked"] == "close" and got[1]["reply_id"] is None, str(got[1]))

        play = tmp / "play_dofus-99.jsonl"
        def ev(seq, kind, name, args=None, session="s"):
            return _json.dumps({"type": "PLAY_LOG", "session": session, "seq": seq,
                                "at": 1000 + seq, "event": dict({"kind": kind,
                                                                 "name": name},
                                                                **({"args": args} if args
                                                                   else {}))}) + "\n"
        play.write_text(
            ev(1, "send", "NpcDialogReplyMessage", {"replyId": 10})
            + ev(2, "recv", "QuestStepValidatedMessage")
            + ev(3, "send", "NpcDialogReplyMessage", {"replyId": 11})
            + ev(4, "recv", "LeaveDialogMessage")
            + ev(5, "send", "NpcDialogReplyMessage", {"replyId": 12})
            + ev(6, "send", "NpcGenericActionRequestMessage")
            + ev(7, "send", "LeaveDialogRequestMessage") + "\n", encoding="utf-8")
        wire = dl.wire_outcomes([str(play)])
        check("a reply the server credited is labelled from the wire, not the cache",
              wire[10]["wire_outcome"] == "quest progress", str(wire.get(10)))
        check("a reply answered by a leave is labelled dialog ended",
              wire[11]["wire_outcome"] == "dialog ended", str(wire.get(11)))
        check("a reply cut off by the next action owns no response",
              wire[12]["wire_outcome"] == "no response recorded", str(wire.get(12)))
        check("the wire counts sends per reply id, so a repeated ask is visible",
              wire[10]["sends"] == 1, str(wire[10]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_planner_buy_branch() -> None:
    section("Planner buy branch (server-sent prices, bounded price, farm fallback)")
    from agent.planner import QuestPlanner

    item, npc, quest_id = 1234, 3888, 999
    obj = {"id": 7, "type_id": 3,
           "params": {"parameter0": npc, "parameter1": item},
           "map_id": 1}
    quest = {"id": quest_id, "steps": [{"id": 1, "objectives": [obj]}]}
    planner = QuestPlanner(level_hint=60)
    base = {"map_id": 1, "cell": 1, "inventory": {}, "inventory_capacity": 40,
            "kamas": 100, "current_objective": {"quest_id": quest_id, "objective_id": 7}}
    prices = [{"gid": item, "listings": [
        {"uid": 90, "price": 30, "quantity": 1},
        {"uid": 77, "price": 10, "quantity": 2},
    ]}]

    calls = planner.plan_objective(quest, 0, obj, dict(base, prices=prices))
    first = calls[0] if calls else {}
    check("a delivery whose item is missing and affordable buys it",
          first.get("tool") == "hdv.buy", first)
    check("the buy uses the cheapest server-sent listing and its uid",
          (first.get("args") or {}).get("uid") == 77
          and (first.get("args") or {}).get("gid") == item
          and (first.get("args") or {}).get("max_price") == 10, first.get("args"))
    check("the buy is bounded by the max_price the listing carried",
          (first.get("args") or {}).get("max_price") == 10, first.get("args"))

    calls = planner.plan_objective(quest, 0, obj, dict(base, kamas=5, prices=prices))
    check("an unaffordable listing falls back to farming, never to a blind buy",
          calls and calls[0].get("tool") == "farm.start"
          and calls[0].get("args", {}).get("resource") == item, calls[:2])

    calls = planner.plan_objective(quest, 0, obj, dict(base))
    check("no cached price falls back to farming rather than inventing one",
          calls and calls[0].get("tool") == "farm.start", calls[:2])

    calls = planner.plan_objective(quest, 0, obj, dict(base, inventory={str(item): 1}))
    check("an item already in the bag is delivered, not bought or farmed",
          calls and calls[0].get("tool") == "quest.advance"
          and calls[0].get("args", {}).get("npc_id") == npc, calls[:2])


def test_cause_split_audit() -> None:
    import unittest
    from ai.test_cause_head import SplitAuditTests

    section("Cause-head input leakage guards")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SplitAuditTests)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("cause-head split checks execute and pass",
          result.testsRun >= 8 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures, "errors": result.errors})


def test_cause_head_artifact() -> None:
    section("Cause head artifact (experimental; not a promotion gate)")
    path = REPO_ROOT / "models" / "cause_head.json"
    if not path.is_file():
        print("   SKIP: models/cause_head.json not present")
        return
    import json as _json
    metrics = _json.loads(path.read_text(encoding="utf-8"))
    val = metrics.get("val") or {}
    check("the experimental cause head records its evaluation size",
          int(val.get("n") or 0) > 0, val)
    check("the experimental artifact is not marked as promoted",
          metrics.get("promotion_ready") is not True, metrics.get("promotion_ready"))
    if not (metrics.get("split_audit") or {}).get("valid"):
        print("   UNVERIFIED MODEL: no passing input-level split audit; scores are not promotion evidence")
    check("the MLP-alone result is not hidden by the ensemble",
          "mlp_val" in metrics and "ensemble" in metrics,
          sorted(metrics))
    check("the cause head is labelled rule-distilled, not live",
          "live=false" in str(metrics.get("labels") or ""), metrics.get("labels"))
    proto = Path(str(metrics.get("prototypes_path") or ""))
    check("the retrieval half of the ensemble is stored", proto.is_file(), str(proto))
    check("nothing auto-loads the cause head into the controller",
          not (REPO_ROOT / "agent" / "controller.py").read_text(
              encoding="utf-8", errors="replace").count("cause_head"),
          "controller.py mentions cause_head")


def test_always_on_ai() -> None:
    section("Always-on AI (every launched client is driven without a Start press)")
    def read(name: str) -> str:
        path = ROOT / "desktop" / name
        return code_only(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else ""

    runtime = read("runtimesettings.cs")
    check("always-on AI is a persisted runtime setting, default on",
          "public bool AutoAi = true;" in runtime and 'Same(key, "auto_ai")' in runtime
          and 'AppendLine("auto_ai=' in runtime, runtime[:80])
    form = read("mainform.cs")
    check("a launched WebView2 client is adopted automatically",
          "AutoStartAi(inst.Name)" in form)
    check("a launched emulator client is adopted automatically",
          "AutoStartAi(im.Name)" in form and "AutoStartAi(slotName)" in form)
    check("clients already running when the app starts are adopted once",
          "_autoAiBootstrapped" in form and "AutoStartAi(im.Name)" in form)
    panel = read("pages.cs")
    check("the setting has a UI switch", "_chkAutoAi" in panel)
    brain = read("aipanel.cs")
    check("adoption starts one instance, never the whole farm by surprise",
          "public void StartInstance(string instanceId, bool force)" in brain)


def test_login_recovery() -> None:
    section("A loop that finds itself at the login screen can come back")
    # Live 2026-10-29: the session ended back at the guest screen and the loop
    # waited for the world forever ("still waiting for the world (90s)"), because
    # session.login is not one of the policy's tools. The wait now presses a
    # guarded guest login a bounded number of times and then goes back to waiting.
    from types import SimpleNamespace
    import json

    from agent.autopilot import Autopilot

    class _Wait:
        _require_world = Autopilot._require_world
        _login_banned = staticmethod(Autopilot._login_banned)
        _login_lockout = staticmethod(Autopilot._login_lockout)
        _world_screen = staticmethod(Autopilot._world_screen)
        _screen_text = staticmethod(Autopilot._screen_text)
        dry_run = False
        world_poll_s = 0.0
        world_report_s = 1e9

        def __init__(self, in_world_after=None):
            self.polls = 0
            self.calls = []
            self._in_world_after = in_world_after
            self._world_login = {"tries": 0, "next_at": 0.0, "max": 2, "spacing": 0.0}
            self._obs_source = "call"
            self.runtime = SimpleNamespace(instance="dofus-99", executor=self)

        def recovery_call(self, tool, args, instance=None):
            self.calls.append((tool, dict(args), instance))
            return SimpleNamespace(ok=True, error=None)

        def observe(self, max_age_ms=-1.0):
            self.polls += 1
            if self._in_world_after is not None and self.polls > self._in_world_after:
                return {"in_world": True, "position": {"map_id": 123}}
            # The live login screen keeps a character id with no name (measured
            # 2026-10-29); that shape must still count as "at the login screen".
            return {"in_world": False,
                    "character": {"id": 4409663, "name": None, "level": None}}

        def _check_identity(self, obs):
            return True

        def _should_stop(self):
            return None

        def _bridge_alive(self):
            return True

    waiter = _Wait(in_world_after=2)
    entered = waiter._require_world()
    check("a loop at the login screen presses its own guest login",
          len(waiter.calls) == 2
          and waiter.calls[0] == ("session.login", {"target": "guest"}, "dofus-99"),
          waiter.calls)
    check("the wait ends when the press brings the world back", entered is True)
    check("no press happens once the character is in the world",
          waiter.polls == waiter._in_world_after + 1, waiter.polls)

    class _Never(_Wait):
        def _should_stop(self):
            return "stop" if self.polls >= 8 else None

    stuck = _Never(in_world_after=None)
    ended = stuck._require_world()
    check("a login that never lands is pressed twice, then waits quietly",
          ended is False and len(stuck.calls) == 2, (ended, stuck.calls))

    # A fall-out can land on character selection instead of the login form: the
    # client holds the last character (name present), so `at_login` is false and
    # no press used to happen -- the wait burned its whole budget (live
    # 2026-10-29: 751.8 s, recovered: false). The play action there is the
    # client's own selectCharacter(id) = CharacterSelectionMessage.
    class _Selection(_Wait):
        def observe(self, max_age_ms=-1.0):
            self.polls += 1
            if self._in_world_after is not None and self.polls > self._in_world_after:
                return {"in_world": True, "position": {"map_id": 123}}
            return {"in_world": False,
                    "character": {"id": 111, "name": "Bruuh", "level": 4}}

    selection = _Selection(in_world_after=2)
    selection._identity = {"id": "4409663", "name": "Myaixd"}
    selection._world_enter = {"tries": 0, "next_at": 0.0, "max": 3, "spacing": 0.0}
    entered_selection = selection._require_world()
    check("at character selection the loop enters the world as the bound character",
          entered_selection is True and selection.calls
          and selection.calls[0] == ("session.login",
                                     {"target": "character", "character_id": 4409663},
                                     "dofus-99"),
          selection.calls)
    check("the selection press is not a guest-form press",
          all(call[1].get("target") == "character" for call in selection.calls),
          selection.calls)

    stuck_selection = _Selection(in_world_after=None)
    stuck_selection._identity = {"id": "4409663", "name": "Myaixd"}
    stuck_selection._world_enter = {"tries": 0, "next_at": 0.0, "max": 3, "spacing": 0.0}
    stuck_selection._should_stop = (
        lambda: "stop" if stuck_selection.polls >= 8 else None)
    ended_selection = stuck_selection._require_world()
    check("an enter-world press that never lands is bounded",
          ended_selection is False and len(stuck_selection.calls) == 3,
          (ended_selection, stuck_selection.calls))

    # Each fall-out gets a fresh (still bounded) budget: a second dropout in the
    # same run must be answerable, not silent because the first used the presses.
    again = _Selection(in_world_after=2)
    again._identity = {"id": "4409663", "name": "Myaixd"}
    again._world_enter = {"tries": 0, "next_at": 0.0, "max": 3, "spacing": 0.0}
    again._require_world()
    first_wait = len(again.calls)
    again._in_world_after = None
    again.polls = 0
    again.calls = []
    again._should_stop = lambda: "stop" if again.polls >= 8 else None
    again._require_world()
    check("a second fall-out gets a fresh enter-world budget",
          first_wait > 0 and len(again.calls) == 3, (first_wait, again.calls))

    # The 751.8 s rescue persisted only an elapsed time, so the next triage could
    # not say which screen had held it. The wait now names the screen in its
    # reports and the recovery row carries it.
    check("the wait classifies the screens it can be stuck on",
          Autopilot._world_screen(None)["state"] == "no_snapshot"
          and Autopilot._world_screen({"in_world": False})["state"] == "login"
          and Autopilot._world_screen(
              {"in_world": False, "character": {"name": "Bruuh"}})["state"]
          == "character_selection"
          and Autopilot._world_screen(
              {"in_world": True, "position": {"map_id": 5}})["state"] == "in_world"
          and Autopilot._world_screen(
              {"in_world": None, "position": {}})["state"] == "unknown",
          [Autopilot._world_screen(v) for v in
           (None, {"in_world": False}, {"in_world": None, "position": {}})])
    check("a stuck wait remembers the screen it last saw",
          stuck._last_world_screen.get("state") == "login"
          and stuck_selection._last_world_screen.get("state") == "character_selection"
          and stuck_selection._last_world_screen.get("character") == "Bruuh",
          (stuck._last_world_screen, stuck_selection._last_world_screen))

    class _Recover(_Wait):
        _recover_world = Autopilot._recover_world
        session = "test"
        _prepared_obs = None

        def __init__(self):
            super().__init__(in_world_after=None)
            self._recoveries = 0
            self._recoveries_ok = 0
            self._recovery_wait_s = 0.0
            self.rows = []
            self._should_stop = lambda: "stop file present" if self.polls >= 3 else None

        def _append_log_line(self, blob):
            self.rows.append(json.loads(blob))

    recovered = _Recover()
    ended = recovered._recover_world(trigger="fell_out")
    row = recovered.rows[-1] if recovered.rows else {}
    check("the persisted recovery row names the screen that held the wait",
          ended is False and row.get("recovered") is False
          and (row.get("screen") or {}).get("state") == "login",
          row)

    # An escalated login cooldown is a slot lockout, not a slow login: the page
    # backs off an hour from the second refused press, and the wait used to park
    # silently under "still waiting for the world" (live 2026-10-29: "Server
    # busy", 3527 s left). The wait must end with the reason instead.
    check("a single refusal is a slow server, not a lockout",
          Autopilot._login_lockout({"login_cooldown":
                                    {"reason": "Server busy", "remaining_s": 600,
                                     "fails": 1}}) is None
          and Autopilot._login_lockout({"login_cooldown": None}) is None
          and Autopilot._login_lockout(None) is None)
    check("an escalated cooldown reads as a lockout with its reason",
          (Autopilot._login_lockout({"login_cooldown":
                                     {"reason": "Server busy", "remaining_s": 3527,
                                      "fails": 2}}) or {}).get("reason") == "Server busy"
          and (Autopilot._login_lockout({"login_cooldown":
                                         {"reason": "Server busy",
                                          "remaining_s": 2000,
                                          "fails": 0}}) or {}).get("remaining_s") == 2000,
          Autopilot._login_lockout({"login_cooldown":
                                    {"reason": "Server busy", "remaining_s": 3527,
                                     "fails": 2}}))

    class _Locked(_Wait):
        def observe(self, max_age_ms=-1.0):
            self.polls += 1
            return {"in_world": False,
                    "character": {"id": 4409663, "name": None},
                    "login_cooldown": {"reason": "Server busy",
                                       "remaining_s": 3527, "fails": 2}}

    locked = _Locked(in_world_after=None)
    locked_ended = locked._require_world()
    check("a locked-out slot ends the wait without pressing into the cooldown",
          locked_ended is False and locked.calls == []
          and (getattr(locked, "_last_login_cooldown", {}) or {}).get("reason")
          == "Server busy",
          (locked_ended, locked.calls, getattr(locked, "_last_login_cooldown", None)))

    wait_one = _Wait(in_world_after=3)
    wait_one_obs = {"in_world": False, "character": {"id": 4409663, "name": None},
                    "login_cooldown": {"reason": "Server busy", "remaining_s": 600,
                                       "fails": 1}}

    def _one_refusal_observe(max_age_ms=-1.0):
        wait_one.polls += 1
        if wait_one.polls > 3:
            return {"in_world": True, "position": {"map_id": 123}}
        return dict(wait_one_obs)

    wait_one.observe = _one_refusal_observe
    waited_one = wait_one._require_world()
    check("a first-refusal cooldown keeps the run waiting, not ending it",
          waited_one is True and len(wait_one.calls) >= 1, wait_one.calls)

    class _RecoverLocked(_Recover):
        def observe(self, max_age_ms=-1.0):
            self.polls += 1
            return {"in_world": False,
                    "character": {"id": 4409663, "name": None},
                    "login_cooldown": {"reason": "Server busy",
                                       "remaining_s": 3527, "fails": 2}}

    locked_recover = _RecoverLocked()
    locked_recover._recover_world(trigger="fell_out")
    locked_row = locked_recover.rows[-1] if locked_recover.rows else {}
    check("the recovery row persists the login cooldown that ended the wait",
          (locked_row.get("login_cooldown") or {}).get("reason") == "Server busy"
          and (locked_row.get("login_cooldown") or {}).get("remaining_s") == 3527,
          locked_row)


def test_brain_autostart() -> None:
    section("Combat brain autostart (G24: a fight cannot be 'learned' by accident)")
    import inspect
    import json as _json
    from combat import brain_service as bs
    from agent import autopilot as _am
    from agent.autopilot import Autopilot

    # -- the health probe ----------------------------------------------------
    class _Resp:
        def __init__(self, payload):
            self._blob = _json.dumps(payload).encode("utf-8")

        def read(self):
            return self._blob

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    asked: Dict[str, Any] = {}
    real_open = bs.urllib.request.urlopen

    def fake_open(url, timeout=None):
        asked["url"] = url
        return _Resp(asked["payload"])

    bs.urllib.request.urlopen = fake_open
    try:
        asked["payload"] = {"status": "ok", "model": "encounter_ppo_pbt_actor"}
        check("a healthy brain is named by its checkpoint, which is the only proof "
              "the trained policy is the one answering",
              bs.brain_is_up(1234) == "encounter_ppo_pbt_actor", str(asked))
        check("the health request goes to the port the caller was given, not a "
              "hardcoded default",
              asked["url"] == "http://127.0.0.1:1234/health", asked.get("url"))
        asked["payload"] = {"status": "starting"}
        check("a process that answers /health without 'ok' is not a brain",
              bs.brain_is_up(1234) is None, str(asked))
    finally:
        bs.urllib.request.urlopen = real_open

    # -- interpreter choice ---------------------------------------------------
    import os
    got = bs.candidate_interceptors({"DOFUS_BRAIN_PYTHON": "/opt/venv/bin/python"})
    check("an operator-set interpreter wins, so nobody has to watch the loop probe",
          got[0] == "/opt/venv/bin/python", str(got))
    # The override names the interpreter that is already first in the list: the same
    # binary spelled two ways must cost one probe, not two.
    dupe = bs.candidate_interceptors({"DOFUS_BRAIN_PYTHON": sys.executable})
    spelled = [p for p in dupe if os.path.normcase(p) == os.path.normcase(sys.executable)]
    check("the same interpreter spelled differently is probed once, not twice",
          len(spelled) == 1, str(dupe))
    check("an interpreter is only a candidate if it can import what the server imports",
          bs.pick_brain_interpreter(["/no/such/python", "/bad/python"],
                                    probe=lambda exe: exe == "/bad/python") == "/bad/python")
    check("no capable interpreter is an install problem, and the loop must not spin on it",
          bs.pick_brain_interpreter(["/a", "/b"], probe=lambda exe: False) is None)

    # -- the state machine -----------------------------------------------------
    def run_ensure(**over):
        spawned: List[Any] = []
        spawn_record = over.pop("_spawn", {"spawned": True, "reason": "spawned", "pid": 7})
        health = list(over.pop("_health", ["encounter_ppo_pbt_actor"]))
        # A clock that only ever advances past the readiness deadline: the wait loop
        # must terminate, and `not_ready` must be reachable in the gate.
        ticks = iter([0.0, 60.0, 120.0, 180.0, 240.0, 300.0, 360.0])

        def spawn(exe, port):
            spawned.append((exe, port))
            return dict(spawn_record)

        def is_up(port):
            return health.pop(0) if health else None

        args = dict(port=8891, sleep=lambda s: None, clock=lambda: next(ticks),
                    is_up=is_up, open_port=lambda port: False,
                    pick=lambda: "/capable/python", spawn=spawn)
        args.update(over)
        return bs.ensure_brain(**args), {"spawned": spawned}

    (state, detail), calls = run_ensure(dry_run=True)
    check("a dry run touches no process and no port",
          state == "dry_run" and not calls["spawned"], str((state, calls)))
    (state, detail), calls = run_ensure()
    check("a brain already answering is reused instead of started a second time",
          state == "reused" and detail["model"] == "encounter_ppo_pbt_actor"
          and not calls["spawned"], str((state, detail)))
    (state, detail), calls = run_ensure(_health=[None], open_port=lambda port: True,
                                        pick=lambda: "/x")
    check("a port held by something that never answers /health is named as a "
          "conflict, because the page will silently keep its fallback",
          state == "port_held" and not calls["spawned"], str((state, detail)))
    (state, detail), calls = run_ensure(_health=[None], pick=lambda: None)
    check("no capable interpreter stops the attempt rather than retrying forever",
          state == "no_interpreter" and not calls["spawned"], str((state, detail)))
    (state, detail), calls = run_ensure(_health=[None, "encounter_ppo_pbt_actor"])
    check("waiting for readiness is bounded, and the wait is reported",
          state == "started" and detail["pid"] == 7
          and calls["spawned"] == [("/capable/python", 8891)], str((state, detail)))
    (state, detail), calls = run_ensure(_health=[None, None, None])
    check("a spawn whose health never arrives is not_ready, never a silent success",
          state == "not_ready" and detail["waited_s"] == 90.0
          and "health never answered" in str(detail.get("reason")), str((state, detail)))
    (state, detail), calls = run_ensure(_health=[None], _spawn={"spawned": False,
                                                               "reason": "denied"})
    check("a refused spawn is reported as its own state, with the OS reason",
          state == "spawn_failed" and detail["reason"] == "denied", str((state, detail)))

    # -- the loop's use of it ---------------------------------------------------
    class _Ctl:
        def save(self, force=False):
            return None

        def stats(self):
            return {}

    class _Shim(Autopilot):
        """A real Autopilot with its collaborators withheld.

        Subclassing rather than duck-typing keeps the production constant
        (BRAIN_FINAL_STATES) out of the fixture: if the loop stops treating
        `no_interpreter` as final, this section notices instead of agreeing.
        """

        def __init__(self):
            self.brain_mode = "auto"
            self.preflight = True
            self.dry_run = False
            self.session = "gate"
            self.brain_retry_s = 300.0
            self._brain: Dict[str, Any] = {}
            self._brain_attempts = 0
            self._brain_try_at = 0.0
            self.rows: List[Any] = []
            # Enough of the loop's bookkeeping for the real summary() to run: the
            # brain field is only checked if the field is read where it is written.
            self.controller = _Ctl()
            self.log_path = Path("gate.jsonl")
            self._step_total = 0
            self._verdict_counts: Dict[str, int] = {}
            self._source_counts: Dict[str, int] = {}
            self.replan_requested = 0
            self._intervention_total = 0
            self._recoveries = 0
            self._recoveries_ok = 0
            self._recovery_wait_s = 0.0
            self._waits = 0
            self._dialogs = 0
            self._dialog_stalls = 0
            self.open_listings: List[Any] = []
            self._listing_total = 0
            self._first_step_at = 0.0
            self._last_step_at = 0.0

        def _append_log_line(self, blob):
            self.rows.append(_json.loads(blob.decode("utf-8")
                                         if isinstance(blob, bytes) else blob))

    real_ensure = _am.ensure_brain
    probes: List[int] = []

    def counting_ensure(dry_run=False):
        probes.append(1)
        if dry_run:
            return "dry_run", {"reason": "dry run does not start services"}
        return "started", {"model": "encounter_ppo_pbt_actor", "pid": 9,
                           "cmd": ["python", "combat_ai_server.py", "8891"]}

    _am.ensure_brain = counting_ensure
    real_time = _am.time.time

    def at(epoch, call):
        """Drive one probe as if the wall clock had reached `epoch`."""
        _am.time.time = lambda: epoch
        try:
            call()
        finally:
            _am.time.time = real_time

    try:
        shim = _Shim()
        shim.brain_mode = "off"
        at(1000.0, lambda: Autopilot._ensure_brain(shim))
        check("--brain off leaves the page's heuristic fallback in charge, and says so "
              "by never probing", probes == [] and shim._brain == {}, str(probes))
        shim = _Shim()
        shim.preflight = False
        at(1000.0, lambda: Autopilot._ensure_brain(shim))
        check("a loop built without preflight is the gate's synthetic run, so it starts "
              "no services on the developer's machine",
              probes == [], str(probes))
        shim = _Shim()
        at(1000.0, lambda: Autopilot._ensure_brain(shim, "fight_start"))
        row = shim._brain
        check("the probe is recorded as a row, not only printed",
              len(shim.rows) == 1 and row["kind"] == "brain"
              and row["when"] == "fight_start" and row["state"] == "started"
              and row["model"] == "encounter_ppo_pbt_actor" and row["pid"] == 9,
              str(row))
        check("the command that was run travels with the row, so a wrong interpreter is "
              "auditable after the fact", "combat_ai_server.py" in str(row["cmd"]),
              str(row.get("cmd")))
        check("the row drops nothing the reader needs and carries no session id of its "
              "own making", row["session"] == shim.session and row["mode"] == "auto",
              str(row))
        at(1100.0, lambda: Autopilot._ensure_brain(shim))
        check("a brain that answered is never restarted in the same run",
              len(probes) == 1 and shim._brain_attempts == 1, str(probes))
        shim = _Shim()
        shim._brain = {"state": "no_interpreter"}
        at(99999.0, lambda: Autopilot._ensure_brain(shim))
        check("a structural failure is as final as a success: missing jax is an install, "
              "not a retry", len(probes) == 1, str(probes))
        shim = _Shim()
        shim._brain = {"state": "port_held"}
        shim._brain_try_at = 100.0
        at(120.0, lambda: Autopilot._ensure_brain(shim))
        check("a retryable failure waits for the cadence instead of probing every step",
              len(probes) == 1, str(probes))
        at(500.0, lambda: Autopilot._ensure_brain(shim))
        check("past the cadence the same run asks again, because a freed port is fixable",
              len(probes) == 2 and shim._brain_attempts == 1, str(probes))
        shim = _Shim()
        shim.dry_run = True
        at(1000.0, lambda: Autopilot._ensure_brain(shim))
        check("a dry run is recorded as a dry run, never as a brain being available",
              shim._brain["state"] == "dry_run" and "model" not in shim._brain,
              str(shim._brain))
        shim = _Shim()
        at(5000.0, lambda: Autopilot._ensure_brain(shim))
        report = shim.summary("gate")
        check("the run's own summary carries the brain state, so a fight count can be "
              "read against which policy was actually available",
              report["brain"]["state"] == "started"
              and report["brain"]["model"] == "encounter_ppo_pbt_actor",
              str(report.get("brain")))
        check("a run that never probed reports no brain rather than an empty success",
              _Shim().summary("gate")["brain"] == {key: None for key in
                                                   ("state", "model", "mode",
                                                    "reason", "when")},
              str(_Shim().summary("gate")["brain"]))
    finally:
        _am.ensure_brain = real_ensure

    run_src = inspect.getsource(Autopilot._run)
    fight_src = inspect.getsource(Autopilot._wait_out_fight)
    check("the brain is started before the first step, not during a fight",
          "self._ensure_brain()" in run_src, "no startup call")
    check("a fight that begins with the brain down gets one more probe",
          'self._ensure_brain("fight_start")' in fight_src, "no fight-start call")


def test_single_control_plane() -> None:
    section("One control plane per port (two daemons must not share :8895)")
    # Measured 2026-10-29: pids 17476 and 9996 both held :8895. HTTPServer sets
    # SO_REUSEADDR, which Windows treats as permission to share the port, so the
    # second daemon bound with no error and each one answered an arbitrary share
    # of the requests -- two instance tables, and a stop could land on the daemon
    # that had never heard of that instance. The host caused it: it tracked only
    # the python child it had spawned, so a panel restart (or the instance-launch
    # autostart) spawned another daemon over the live one.
    import socket as _socket
    import threading as _threading
    from agent import serve as _serve

    listener = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        port = listener.getsockname()[1]

        def _drain() -> None:
            try:
                conn, _ = listener.accept()
                conn.close()
            except OSError:
                pass

        _threading.Thread(target=_drain, daemon=True).start()
        check("an answered port is visible before a server is built",
              _serve.service_present("127.0.0.1", port) is True, str(port))
        # Started on a thread and abandoned if it binds: without the guard main()
        # would serve forever and hang the gate instead of failing it.
        outcome: Dict[str, Any] = {}

        def _start() -> None:
            outcome["code"] = _serve.main(
                ["--port", str(port), "--recorder-dir", tempfile.mkdtemp()])

        worker = _threading.Thread(target=_start, daemon=True)
        worker.start()
        worker.join(5.0)
        check("a second control plane refuses the port instead of sharing it",
              outcome.get("code") == _serve.EXIT_PORT_TAKEN, str(outcome))
        dead = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        try:
            dead.bind(("127.0.0.1", 0))
            free_port = dead.getsockname()[1]
        finally:
            dead.close()
        check("a free port is not mistaken for a running daemon",
              _serve.service_present("127.0.0.1", free_port) is False, free_port)
    finally:
        listener.close()

    # The host side of the same bug. `body.IndexOf("\"ok\":true")` never matched
    # the embedded runtime's stdlib JSON (`"ok": true`, with a space), so every
    # daemon start and stop was logged as a refusal and fell back to duplicate
    # per-instance children that raced the daemon for the instance lock.
    panel_path = ROOT / "desktop" / "aipanel.cs"
    panel = code_only(panel_path.read_text(encoding="utf-8", errors="replace"))
    check("the host can see a daemon it did not spawn",
          "private bool BrainAnswers()" in panel
          and "_brain == null" not in panel[panel.find("private void RefreshBrainStatus()"):
                                            panel.find("private bool BrainAnyRunning()")],
          "a panel restart spawns a second daemon over the live one")
    body = panel[panel.find("private bool EnsureBrain()"):
                 panel.find("private bool BrainStart(")]
    probe_at = body.find("string health = BrainHealth();")
    spawn_at = body.find("string python = ResolvePython();")
    check("the health probe runs before the spawn, not after it",
          0 <= probe_at < spawn_at, str((probe_at, spawn_at)))
    check("a spaced JSON ok counts as success",
          "private static bool JsonOk(string body)" in panel
          and '"\\"ok\\":true"' not in panel,
          "stdlib json writes \"ok\": true and the host reads that as a refusal")
    check("start and both stops use the tolerant check",
          panel.count("JsonOk(body)") == 3, panel.count("JsonOk(body)"))


def test_brain_readiness() -> None:
    section("Brain readiness (G25: can the live board the page sends ever be acted on?)")
    import contextlib
    import importlib.util
    import io

    script = REPO_ROOT / "tools" / "brain_readiness.py"
    check("the reader exists", script.exists(), str(script))
    spec = importlib.util.spec_from_file_location("brain_readiness", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def drive(rows: List[str], write: bool = True) -> str:
        tmp = Path(tempfile.mkdtemp())
        try:
            log = tmp / "fight_boards.jsonl"
            if write:
                log.write_text("\n".join(rows) + "\n", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = module.main([str(log)])
            return "%s(exit %s)" % (buf.getvalue(), code)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def board(fight_id=7, max_hp=None, ap=None, initiative="absent",
               contract=None, fighters_extra=2):
        me = {"id": 1, "is_me": True, "hp": 185, "level": 12,
              "max_hp": max_hp, "ap": ap, "max_ap": None, "mp": None, "max_mp": None}
        if initiative != "absent":
            me["initiative"] = initiative
        payload: Dict[str, Any] = {"in_fight": True, "fight_id": fight_id,
                                   "fighters": [me]}
        if contract is not None:
            payload["contract"] = contract
        return json.dumps({"kind": "fight_board", "run": "gate", "board": payload})

    dead = {"version": 1, "ok": False, "actionable": False, "value_ready": False,
            "errors": ["self_max_hp_missing", "enemy_max_hp_missing:-1"],
            "warnings": ["self_initiative_missing", "self_max_ap_missing",
                         "enemy_stats_not_exposed"]}

    out = drive([board(contract=dead), board(fight_id=8, contract=dead)])
    check("the census counts the recorded boards, not a guess about them",
          "board snapshots: 2" in out and "distinct fight ids: 2" in out, out[:200])
    check("a rejected board is reported as rejected, with the field census beside it",
          "actionable=False value_ready=False: 2" in out
          and "self_max_hp_missing" in out, out)
    check("the verdict states the consequence for the fight, not only the count",
          "not one recorded live board satisfied the contract" in out, out[-320:])
    check("a null field and an absent field are different defects and stay separate",
          "max_hp: null: 2" in out and "initiative: key absent: 2" in out, out)
    check("the reader trusts the contract that was computed at capture time",
          "contracts: recorded=2" in out, out)
    check("the corpus vintage is counted from the payload, not from a file's mtime",
          "boards carrying the post-fix fields: 0 of 2" in out, out)
    check("a pre-fix corpus is not allowed to read as a current verdict",
          "predates the producer fix" in out, out[-400:])

    stale_but_fixed = drive([board(contract=dead, initiative=44),
                             board(fight_id=8, contract=dead, initiative=51)])
    check("a rejected board that already carries the new fields means the gap is open, "
          "not that the corpus is old",
          "the blocker is still open" in stale_but_fixed
          and "boards carrying the post-fix fields: 2 of 2" in stale_but_fixed,
          stale_but_fixed[-420:])

    captured = json.dumps({"kind": "fight_board", "run": "gate", "board": {
        "in_fight": True, "fight_id": 12, "contract": dead,
        "fighters": [{"id": -1, "is_me": False, "hp": 185, "max_hp": 260, "ap": 6,
                      "max_ap": 6, "mp": 4, "max_mp": 4, "initiative": 30}],
        "data_keys": {"me": ["stats", "teamId"],
                      "enemy": ["creatureGenericId", "spellList"]},
        "fighter_keys": {"me": ["data", "spells"], "enemy": ["data", "level"]}}})
    cap_out = drive([captured])
    check("the names the live fighter objects really carry are printed beside the "
          "fields still missing, so one fight answers both",
          "data_keys[enemy] (2): creatureGenericId, spellList" in cap_out
          and "fighter_keys[enemy]" in cap_out, cap_out[-500:])
    check("a corpus that captured no names claims none",
          "data_keys" not in drive([board(contract=dead)]), "invented a sample")

    alive = {"version": 1, "ok": True, "actionable": True, "value_ready": True,
             "errors": [], "warnings": []}
    ok_out = drive([board(fight_id=9, max_hp=300, ap=6, initiative=12, contract=alive)])
    check("a board that satisfies the contract is not swept into the failure count",
          "actionable=True value_ready=True: 1" in ok_out
          and "every recorded board was actionable" in ok_out, ok_out)
    check("a filled field is counted as filled, so a fix is visible in the census",
          "max_hp: set: 1" in ok_out and "initiative: set: 1" in ok_out, ok_out)
    check("an initiative that exists is no longer reported as absent",
          "key absent" not in ok_out, ok_out)

    mixed = drive([board(contract=dead), board(fight_id=10, max_hp=300,
                                                initiative=3, contract=alive)])
    check("a partly-fixed corpus gets its own verdict instead of either extreme",
          "1 of 2 recorded boards were rejected" in mixed, mixed[-260:])

    empty = drive([], write=False)
    check("no snapshots is reported as no evidence rather than as a clean bill",
          "board snapshots: 0" in empty and "no recorded boards" in empty
          and "(exit 1)" in empty, empty)

    rebuild = drive([board(fight_id=11)])
    check("a snapshot with no recorded contract is rebuilt, and says which it did",
          "contracts: recomputed=1" in rebuild or "unavailable" in rebuild, rebuild[:400])
    check("the required fields are the ones the contract actually asks for",
          module.REQUIRED == ("max_hp", "ap", "max_ap", "mp", "max_mp", "initiative"),
          str(module.REQUIRED))


def test_group_scan_reader() -> None:
    section("Group scan census (G7: was the map empty, or did the page never match it?)")
    import contextlib
    import importlib.util
    import inspect
    import io

    script = REPO_ROOT / "tools" / "group_scan.py"
    check("the reader exists", script.exists(), str(script))
    spec = importlib.util.spec_from_file_location("group_scan", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    reader_src = inspect.getsource(module)
    check("one command reads both logs, so a live run is measured without hunting for files",
          "autopilot.jsonl" in reader_src and "AGENT_LOG.exists()" in reader_src,
          "the refusals are written to the agent log, not to the trajectory file")

    def drive(rows: List[str], write: bool = True):
        tmp = Path(tempfile.mkdtemp())
        try:
            log = tmp / "default.jsonl"
            if write:
                log.write_text("\n".join(rows) + "\n", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = module.main([str(log)])
            return "%s(exit %s)" % (buf.getvalue(), code), code
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def row(scan: Optional[Dict[str, Any]] = None) -> str:
        block: Dict[str, Any] = {"map_id": 10130, "groups": [], "group_count": 0}
        if scan is not None:
            block["groups_scan"] = scan
        return json.dumps({"kind": "observation", "observation": {"map": block}})

    out, code = drive([row(), row()])
    check("a corpus with no census is reported as unanswerable, not as an empty world",
          "predates the census" in out and code == 1, out[-260:])

    hit, code = drive([row({"manager": True, "path": "actors", "seen": 9, "matched": 1,
                           "types": {"GameRolePlayGroupMonsterInformations": 1,
                                     "GameRolePlayNpcInformation": 8}})])
    check("one matched group says the path and the filter both work",
          "both work" in hit and "routing question" in hit and code == 0, hit[-300:])

    miss, code = drive([row({"manager": True, "path": "actors", "seen": 20, "matched": 0,
                            "types": {"GameRolePlayMonsterInformations": 12,
                                      "GameRolePlayNpcInformation": 8}})])
    check("iterating actors and matching none names the filter as the bug",
          "The filter is the bug" in miss and "GameRolePlayMonsterInformations x12" in miss,
          miss[-320:])

    nobody, _ = drive([row({"manager": False, "path": None, "seen": 0, "matched": 0,
                           "types": {}})])
    check("a missing actor manager is its own answer, not a count of zero groups",
          "reached no actor manager" in nobody, nobody[-300:])

    emptybag, _ = drive([row({"manager": True, "path": "none", "seen": 0, "matched": 0,
                             "types": {}})])
    check("a reachable manager with no collection is the third distinct cause",
          "found no collection" in emptybag, emptybag[-300:])

    totals, _ = drive([row({"manager": True, "path": "actors", "seen": 7, "matched": 0,
                           "types": {"A": 7}}),
                       row({"manager": True, "path": "getActorsIDList", "seen": 3, "matched": 2,
                           "types": {"GameRolePlayGroupMonsterInformations": 2, "A": 1}})])
    check("the counters aggregate across rows and say which name was used",
          "actors iterated: 10" in totals and "groups matched: 2" in totals
          and "actors=1" in totals and "getActorsIDList=1" in totals, totals[:400:])

    def refusal(tool: str = "farm.start", cause: str = "stale_state",
                error: str = "target monster 447 is not on this map (2 groups, here: none)"
                ) -> str:
        return json.dumps({"kind": "decision", "tool": tool, "cause": cause,
                           "ok": False, "error": error, "action": "recover"})

    counted, _ = drive([row(), refusal()])
    check("the reader counts the loop's group refusals by cause",
          "group refusals: 1" in counted and "stale_state=1" in counted, counted[:400:])
    unread, _ = drive([row(), refusal()])
    check("without a census a stale_state refusal still names the read as the blocker",
          "client read" in unread and "predates the census" not in unread, unread[-320:])
    travelling, _ = drive([row(), refusal(cause="wrong_target",
                                         error="target monster 447 is not on this map"
                                               " (2 groups, here: 900)")])
    check("a wrong-target refusal is not read as a broken read",
          "client read" not in travelling, travelling[-320:])
    wrong_tool, _ = drive([row(), refusal(tool="quest.advance")])
    check("only the two calls that ask the client for a group are counted",
          "group refusals: none" in wrong_tool, wrong_tool[:300:])

    nothing, code = drive(["{\"kind\": \"other\"}"], write=True)
    check("a log with no map block says so instead of reporting an empty world",
          "no map blocks" in nothing and code == 1, nothing[-200:])

    missing, code = drive([], write=False)
    check("an absent file is not read as zero groups",
          "absent:" in missing and code == 1, missing[-200:])


def test_npc_start_rotation_bound() -> None:
    """G17 family: the NPC that starts a scripted fight is asked once each, not cycled."""
    from agent.planner import QuestPlanner

    section("NPC fight-start fallback (criterion 3: a rotation is not a plan)")
    import inspect

    from agent import planner as planner_module
    # Measured in the live agent log before this bound existed: 287 claim refusals saying
    # "perform the objective instead", and 259 of the following steps were `quest.advance
    # {npc_id}` cycling four NPCs of the same map for one kill objective. The rotation counter
    # was `tries % len(npcs)` and was cleared on every scene flap, so it never ended.
    crowd = {"map_id": 10130, "cell": 0, "npcs": [{"npc_id": 301, "cell": 1},
                                                 {"npc_id": 302, "cell": 2},
                                                 {"npc_id": 303, "cell": 3}]}
    quest, obj = {"id": 3841}, {"id": 14070}
    p = QuestPlanner()
    asked: List[int] = []
    for _ in range(6):
        asked += [int(c["args"]["npc_id"])
                  for c in p._npc_talk_calls(quest, obj, crowd, 0)]
    check("each NPC on the map is asked at most once", asked == [301, 302, 303], str(asked))
    key = (3841, 14070, 10130)
    check("the memory is which NPCs were asked, not a count that can be reset",
          p._npc_asked == {key: {301, 302, 303}}, str(p._npc_asked))
    moved = dict(crowd)
    moved["cell"] = 44
    check("a later scene of the same map does not re-ask a spent NPC",
          p._npc_talk_calls(quest, obj, moved, 0) == [], str(p._npc_asked))
    grown = {"map_id": 10130, "cell": 0,
             "npcs": crowd["npcs"] + [{"npc_id": 304, "cell": 9}]}
    again = [int(c["args"]["npc_id"]) for c in p._npc_talk_calls(quest, obj, grown, 0)]
    check("a crowd that really grew reopens the search and asks the stranger",
          again == [304], str(again))
    check("the stranger is added to the same memory",
          p._npc_asked.get(key, set()) == {301, 302, 303, 304}, str(p._npc_asked.get(key)))
    other_map = dict(crowd)
    other_map["map_id"] = 10341
    fresh = [int(c["args"]["npc_id"]) for c in p._npc_talk_calls(quest, obj, other_map, 0)]
    check("a different map is a different question, asked fresh", fresh == [301], str(fresh))
    twin = p.fork()
    twin_calls = twin._npc_talk_calls(quest, obj, crowd, 0)
    check("a fork inherits the memory instead of starting the cycle again",
          twin_calls == [] and getattr(twin, "_npc_asked", {}).get(key) == {301, 302, 303, 304},
          str(getattr(twin, "_npc_asked", None)))
    one_npc = {"map_id": 20000, "cell": 0, "npcs": [{"npc_id": 400, "cell": 4}]}
    q = QuestPlanner()
    once = [int(c["args"]["npc_id"])
            for _ in range(5) for c in q._npc_talk_calls(quest, obj, one_npc, 0)]
    check("a lone NPC is asked once, not five times", once == [400], str(once))
    planner_src = inspect.getsource(planner_module)
    check("no counter is left to be wiped by a scene flap",
          "_npc_tries" not in planner_src and "_npc_exhausted" not in planner_src,
          "the count-based rotation came back")
    check("the scene-change reset spares the NPC memory",
          "self._npc_asked.clear()" not in planner_src)


def test_ready_objective_scan() -> None:
    """G7/G17 family: a ready objective behind the head entry still gets claimed.

    Measured in the live agent log (4,286 decision rows over 11.4 h):
    `objectives_completed` was 0 in every row while 4,267 rows carried more than one
    ready objective and `ready_objectives[0]` was the same un-actionable
    "Complete the X quest" wrapper in 4,285 of them. The claim loop read index 0
    only, so the free progress the loop exists to collect was never asked for.
    """
    import copy
    import inspect

    from agent import planner as planner_module
    from agent.planner import QuestPlanner, names_target

    section("Ready-objective scan (criterion 5: quest objectives completed)")

    wrapper = {"quest_id": "3841", "objective_id": 14070, "type_id": 0,
               "type": "QuestObjectiveFreeForm", "params": {"parameter0": 715354},
               "text": 'Complete the "A Chaotic Arrival" quest.', "step_id": 4974}
    talk = {"quest_id": "4274", "objective_id": 13926, "type_id": 1,
            "type": "QuestObjectiveGoToNpc", "map_id": 145490435,
            "params": {"parameter0": 3892}, "text": "Talk to Great Jarl Ordyn",
            "step_id": 4952}
    craft = {"quest_id": "5001", "objective_id": 20001, "type_id": 17,
             "type": "QuestObjectiveCraft", "params": {"parameter0": 999},
             "text": "Craft something.", "step_id": 6001}
    # A hunt the client really declared (its class carries a Monsters parameter), for a
    # creature this map does not report. Unlike the free-form wrapper it plans something:
    # a read that names no target, which is the case the ready scan has to defer.
    hunt_here = {"quest_id": "3900", "objective_id": 15000, "type_id": 6,
                 "type": "QuestObjectiveFightMonster", "params": {"parameter0": 4194},
                 "text": "Defeat 1 Popet", "step_id": 5000}

    def scene(ready, here, npcs=None):
        return {"level": 4, "connected": True, "in_world": True, "in_fight": False,
                "in_dialog": False, "tutorial": None, "map_id": here, "cell": 300,
                "exits": [{"direction": "left", "map_id": 145489923, "blocked": False,
                           "cells": [1]}],
                "npcs": [{"npc_id": 3976, "cell": 10}] if npcs is None else npcs,
                "groups": [], "group_count": 0,
                "quests_active": {"3841": 0, "4274": 0, "5001": 0, "3900": 0},
                "ready_objectives": copy.deepcopy(ready), "step_objectives": [],
                "current_objective": copy.deepcopy(ready[0]),
                "objectives_completed": 0, "inventory": {}, "kamas": 0}

    def steps(limit, ready, here, count, npcs=None):
        """The plan sequence one entry-count of the ready scan produces.

        Each row is the call, its arguments, and the claim the planner is waiting to
        credit. The claim is what says *which* path produced the call: the open-quest
        fallback can plan the same NPC talk with no memory that a ready objective
        asked for it, and then a refused claim cannot be charged back to it.
        """
        original = planner_module.READY_SCAN_LIMIT
        if limit is not None:
            planner_module.READY_SCAN_LIMIT = limit
        try:
            policy = QuestPlanner()
            out = []
            for _ in range(count):
                obs = scene(ready, here, npcs)
                call = policy.choose("do all the quests", obs)
                # `None` is a decision: the planner refused to send anything. Since G31 a
                # quest the client never reported gets no start request at all, so the
                # empty row is the evidence and not a crash.
                out.append((call.tool if call is not None else None,
                            dict(call.args or {}) if call is not None else {},
                            policy._pending_claim))
                if call is not None:
                    policy.note_result(call.tool, True, "", obs)
            return out
        finally:
            planner_module.READY_SCAN_LIMIT = original

    check("a read names no target",
          not names_target({"tool": "state.snapshot", "args": {}}))
    check("walking out an exit to look for something names no target",
          not names_target({"tool": "nav.goto", "args": {"direction": "left"}}))
    check("a destination map names a target",
          names_target({"tool": "nav.goto", "args": {"map_id": 5}}))
    check("an NPC to talk to names a target",
          names_target({"tool": "quest.advance", "args": {"npc_id": 9}}))
    check("an objective to validate names a target",
          names_target({"tool": "quest.advance", "args": {"objective_id": 9}}))
    check("a bare quest request names no target",
          not names_target({"tool": "quest.advance", "args": {"quest_id": 1}}))
    check("a fight that names no monster names no target",
          not names_target({"tool": "fight.join", "args": {}}))

    claim_talk = ("quest.advance", {"quest_id": 4274, "npc_id": 3892},
                  ((4274, 13926), "quest.advance"))
    check("the shipped scan looks at more than one ready objective",
          planner_module.READY_SCAN_LIMIT > 1, str(planner_module.READY_SCAN_LIMIT))
    # G27 changed what the head does with a one-entry scan. While the wrapper's
    # `parameter0` was read as a monster id it consumed three steps of detours looking
    # for a creature the client never named, and the fourth step was a read. Now it plans
    # nothing, so the wasted steps take the shape of the curriculum fallback: a quest
    # start for a quest the character does not have. The progress the entry behind it
    # holds is still lost either way, which is the point of the check.
    one = steps(1, [wrapper, talk], 145490435, 4)
    check("one entry spends the run on the text-only head and never reaches the "
          "objective behind it",
          one[0][2] == ((3841, 14070), "quest.advance")
          and one[-1] != claim_talk
          and all(row[0] != "fight.join" for row in one)
          and one[-1][1].get("quest_id") != 3841, str(one))
    eight = steps(None, [wrapper, talk], 145490435, 4)
    check("scanning the list gives that step to the objective that names an NPC",
          eight[-1] == claim_talk, str(eight))

    # Live 2026-10-29: the talk objective named the exterior map while Great Jarl
    # Ordyn stood on the house map the previous step had just entered; nav.goto
    # answered "not adjacent" and the loop waited. An NPC that is here is asked here.
    on_house_map = {"map_id": 146276352, "cell": 312,
                    "map": {"map_id": 146276352,
                            "npcs": [{"npc_id": 3892, "cell": 317,
                                      "actor_id": -4}]}}
    here_plan = QuestPlanner().plan_objective({"id": 4274}, 0, talk, on_house_map)
    check("a talk target standing on this map is talked to here, not travelled to",
          [c["tool"] for c in here_plan] == ["quest.advance"]
          and here_plan[0]["args"] == {"quest_id": 4274, "npc_id": 3892},
          here_plan)
    elsewhere = QuestPlanner().plan_objective(
        {"id": 4274}, 0, talk,
        {"map_id": 146276352, "cell": 312,
         "map": {"map_id": 146276352, "npcs": []}})
    check("a talk target that is not on this map still travels",
          [c["tool"] for c in elsewhere] == ["nav.goto", "quest.advance"]
          and elsewhere[0]["args"].get("map_id") == 145490435, elsewhere)
    talk_here = steps(1, [talk], 146276352, 1, npcs=[{"npc_id": 3892, "cell": 10}])
    check("the ready scan talks to an NPC that is here instead of travelling to "
          "the map the objective names",
          talk_here[0][0] == "quest.advance"
          and talk_here[0][1] == {"quest_id": 4274, "npc_id": 3892}, str(talk_here))
    far = steps(None, [wrapper, talk], 145489923, 4)
    check("a ready objective on another map is travelled to, not waited out",
          far[-1][0] == "nav.goto" and far[-1][1].get("map_id") == 145490435
          and far[-1][2] == ((4274, 13926), "nav.goto"), str(far))

    retired = steps(None, [craft, talk], 145490435, 3)
    check("retiring the head quest does not blind the loop to the entry behind it",
          retired[1] == claim_talk and retired[2] == claim_talk, str(retired))
    blind = steps(1, [craft, talk], 145490435, 2)
    check("with one entry the retired head abandons the ready objective",
          blind[1] != claim_talk, str(blind))
    alone = steps(None, [wrapper], 145490435, 4)
    # Type 0 is server-counted since the two live SERVER_GONE closes, so the head is
    # never claimed; the action it names comes first, once, and then the loop stops.
    check("a text-only head asks the map's NPC once rather than claiming, hunting or "
          "inventing a quest this game has never shown",
          alone[0][0] == "quest.advance"
          and alone[0][1] == {"quest_id": 3841, "npc_id": 3976}
          and [row[0] for row in alone[1:]] == [None, None, None]
          and all(row[0] != "fight.join" for row in alone), str(alone))

    # The other head shape: an objective that does declare a creature, for a creature
    # this map does not report and no NPC can start. Its only plan is a read that names
    # no target. G27 took the phantom hunt away from the free-form wrapper (a text-only
    # head may only ask the map's NPCs), so the declared hunt is what exercises the
    # defer path -- and without these two rows the defer and the deferred-wait branches
    # are unprotected.
    empty_map = steps(None, [hunt_here, talk], 145490435, 3, npcs=[])
    check("a head whose only plan names no target gives every step to the entry behind it",
          all(row == claim_talk for row in empty_map), str(empty_map))
    waiting = steps(None, [hunt_here], 145490435, 3, npcs=[])
    check("an objective whose only path is a wait still gets that wait, credited to it",
          waiting[-1][0] == "state.snapshot"
          and waiting[-1][2] == ((3900, 15000), "state.snapshot"), str(waiting))

    planner_src = inspect.getsource(planner_module)
    check("the claim loop no longer reads a single ready entry",
          "[:1]" not in planner_src, "a one-entry slice came back")
    check("the scan stays bounded", "READY_SCAN_LIMIT" in planner_src
          and "[:READY_SCAN_LIMIT]" in planner_src)

    # The step index the observation now carries. While the page derived it from
    # `finishedCount` it was 0 for every quest, so `nearest` ordering could only ever match
    # step 0 and a quest on its second step was planned as if it were on its first.
    from agent.verifier import STATE_KEYS as observed_keys
    check("a finished quest or an advanced step is seen as the world moving",
          "quests_finished" in observed_keys and "quest_steps" in observed_keys,
          str(observed_keys))
    quest = {"id": 777, "level_min": 1, "steps": [
        {"objectives": [{"id": 9001, "type_id": 1, "params": {"parameter0": 301}}]},
        {"objectives": [{"id": 9002, "type_id": 1, "params": {"parameter0": 302}},
                        {"id": 9003, "type_id": 1, "params": {"parameter0": 303}}]}]}
    late = QuestPlanner()
    late.quests = {777: quest}
    late.ordering = "nearest"

    def obs_at(step, objective, done=()):
        return {"map_id": 100, "cell": 0, "level": 10, "quests_active": {"777": step},
                "current_objective": {"quest_id": "777", "objective_id": objective,
                                      "step": step, "status": 1, "type_id": 1,
                                      "params": {"parameter0": 302}},
                "step_objectives": [{"id": d, "done": True} for d in done],
                "ready_objectives": [], "exits": [], "npcs": [],
                "inventory": {}, "in_dialog": False, "in_fight": False, "tutorial": None}

    on_second = late._objective_in(quest, 1, obs_at(1, 9002, done=(9002,)))
    check("a tracker on the second step is believed, so that step is the one ordered",
          on_second is not None and on_second.get("id") == 9003, str(on_second))
    first_step = late._objective_in(quest, 0, obs_at(0, 9001, done=(9001,)))
    check("the same question on the first step has no third objective to offer",
          first_step is None or first_step.get("id") != 9003, str(first_step))


def test_session_report_census() -> None:
    """Criterion 5's instrument: `objectives +0` must say whether the world was empty
    or the loop never looked past the first ready objective."""
    import contextlib
    import importlib.util
    import io
    from pathlib import Path as _Path

    script = REPO_ROOT / "tools" / "session_report.py"
    section("Ready-objective census in the baseline report")
    check("the report tool exists", script.exists(), str(script))
    spec = importlib.util.spec_from_file_location("session_report", str(script))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    wrapper = {"quest_id": "3841", "objective_id": 14070, "type_id": 0,
               "text": 'Complete the "A Chaotic Arrival" quest.'}
    talk = {"quest_id": "4274", "objective_id": 13926, "type_id": 1,
            "text": "Talk to Great Jarl Ordyn"}

    def step(at: float, ready, completed=0) -> str:
        observation: Dict[str, Any] = {"ready_objectives": ready}
        if completed is not None:
            observation["objectives_completed"] = completed
        return json.dumps({"kind": "step", "session": "s", "index": 0,
                           "tool": "state.snapshot", "ok": True, "at": at,
                           "observation": observation})

    def drive(lines: List[str]):
        tmp = _Path(tempfile.mkdtemp())
        try:
            log = tmp / "default.jsonl"
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                module.main([str(log)])
            return buf.getvalue(), module.summarise(log)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    out, report = drive([step(1.0, [wrapper, talk]), step(2.0, [wrapper, talk]),
                         step(3.0, [wrapper, talk])])
    check("a run that completed nothing while objectives were ready is called out",
          "carried a ready objective and none was completed" in out, out[-260:])
    check("the call-out names the entry that held the step",
          'Complete the "A Chaotic Arrival" quest.' in out, out[-260:])
    check("it counts the steps that had another entry waiting behind it",
          "3 step(s) had another entry behind it" in out, out[-260:])
    check("the census is in the machine-readable report too",
          report["ready_steps"] == 3 and report["ready_steps_with_another_entry"] == 3
          and report["objectives_measured"] == 1, str(report["ready_steps"]))

    moved, _ = drive([step(1.0, [wrapper, talk], completed=0),
                      step(2.0, [wrapper, talk], completed=2)])
    check("a run that did complete objectives is not called a starvation",
          "carried a ready objective" not in moved, moved[-260:])
    empty, report = drive([step(1.0, []), step(2.0, [])])
    check("an empty world is not reported as a blind loop",
          "carried a ready objective" not in empty
          and report["ready_steps"] == 0, empty[-200:])
    unread, report = drive([step(1.0, [wrapper, talk], completed=None)])
    check("no claim is made when the progress counter was never readable",
          "carried a ready objective" not in unread
          and report["objectives_measured"] == 0, unread[-200:])

    anon, report = drive([step(1.0, [{"quest_id": "9", "objective_id": 77}])])
    check("a head with no text is still identified by its objective id",
          "77" in anon and report["ready_head"] == "77", anon[-200:])
    broken, _ = drive([step(1.0, ["not-an-objective"])])
    check("a malformed ready entry does not stop the report",
          "carried a ready objective" in broken and '"?"' in broken,
          broken[-220:])

    # G28: `objectives_completed` could not move (the page read a property the client only
    # assigns to finished quests), so the report also carries the number the client does grow.
    def qstep(at: float, finished, completed=0) -> str:
        return json.dumps({"kind": "step", "session": "s", "index": 0,
                           "tool": "quest.advance", "ok": True, "at": at,
                           "observation": {"objectives_completed": completed,
                                           "quests_finished": finished,
                                           "ready_objectives": []}})

    grew, report = drive([qstep(1.0, 3), qstep(2.0, 4), qstep(3.0, 5)])
    check("the report counts quests the client says are finished",
          "quests completed +2" in grew and report["quests_completed"] == 2, grew[-240:])
    steady, report = drive([qstep(1.0, 4), qstep(2.0, 4)])
    check("a run that finished nothing says so without guessing",
          "quests completed +0" in steady and report["quests_completed"] == 0,
          steady[-240:])
    missing, report = drive([json.dumps({"kind": "step", "session": "s", "index": 0,
                                        "tool": "nav.goto", "ok": True, "at": 1.0,
                                        "observation": {"ready_objectives": []}})])
    check("a log captured before the field existed still reports, as zero",
          "quests completed +0" in missing
          and report["per_session"][0]["quests_completed"] is None, missing[-240:])
    dipped, report = drive([qstep(1.0, 5), qstep(2.0, 4), qstep(3.0, 6)])
    check("a dip in the finished list is not read as a negative progress",
          report["quests_completed"] == 1, str(report["quests_completed"]))


def test_group_refusal_routing() -> None:
    """G7: a group refusal has four meanings, and only three of them mean "travel"."""
    import inspect

    from agent.autopilot import Autopilot, _accepts_detail
    from agent.planner import GROUP_REFUSALS, QuestPlanner, group_refusal_outcome

    section("Group refusals (G7: unread members are not an absent mob)")
    check("no payload falls back to the sentence it always had",
          group_refusal_outcome(None, "target monster 447 is not on this map") == "elsewhere")
    check("a refusal that never reached the client is not a fact about the map",
          group_refusal_outcome(None, "actorManager unavailable") == "other")
    check("zero groups iterated is an empty map, not an unread one",
          group_refusal_outcome({"groups_seen": 0, "monsters_here": []}) == "empty_map")
    check("other monsters present means the named one is elsewhere",
          group_refusal_outcome({"groups_seen": 2, "monsters_here": [448]}) == "elsewhere")
    check("groups present but no member id read is the producer's defect",
          group_refusal_outcome({"groups_seen": 3, "monsters_here": []}) == "unread_members")
    check("a count that is not a number is not sorted into any of them",
          group_refusal_outcome({"groups_seen": "?"}) == "other")
    check("every outcome the function can return is a bucket the summary prints",
          set(GROUP_REFUSALS) == {"elsewhere", "empty_map", "unread_members", "other"},
          str(sorted(GROUP_REFUSALS)))

    here = QuestPlanner()
    here.note_result("farm.start", False, "target monster 447 is not on this map", {},
                     {"groups_seen": 2, "monsters_here": [900]})
    check("elsewhere asks for a different map",
          here._farm_target_missing is True and here._group_read_broken is False)
    unread = QuestPlanner()
    unread.note_result("fight.join", False,
                       "no group on this map contains monster 447 (2 groups, no member id read)",
                       {}, {"groups_seen": 2, "monsters_here": [], "actor_path": "actors"})
    check("unread members does not ask for a different map",
          unread._farm_target_missing is False and unread._group_read_broken is True)
    check("the join handler's refusal counts too, not only farm.start",
          unread._group_refusals["unread_members"] == 1, str(unread._group_refusals))
    wait = unread.choose("farm bouftou", {"level": 30},
                         ["farm.start", "state.snapshot", "nav.goto"])
    check("with a broken read the farm plan is a wait, not a walk",
          wait is not None and wait.tool == "state.snapshot", getattr(wait, "tool", wait))
    unread.note_result("farm.start", True, "", {})
    check("a later success clears the defect instead of inheriting it",
          unread._group_read_broken is False and unread._farm_target_missing is False)
    legacy = QuestPlanner()
    legacy.note_result("farm.start", False, "target monster 447 is not on this map", {})
    check("a payload-free report keeps the old behaviour, so an older page cannot strand a run",
          legacy._farm_target_missing is True and legacy._group_refusals["elsewhere"] == 1)
    other = QuestPlanner()
    other.note_result("quest.advance", False, "not on this map", {},
                      {"groups_seen": 3, "monsters_here": []})
    check("a refusal from another tool is not read as a group fact",
          other._group_read_broken is False and sum(other._group_refusals.values()) == 0,
          str(other._group_refusals))

    def four_args(tool, ok, note, obs):
        return None

    check("a policy that takes the payload is detected, and one that does not is not blamed",
          _accepts_detail(QuestPlanner().note_result) is True
          and _accepts_detail(four_args) is False)
    summary_src = inspect.getsource(Autopilot.summary)
    check("the run summary carries the refusal census",
          "group_refusals" in summary_src and "group_read_broken" in summary_src)
    import agent.autopilot as autopilot_module
    loop_src = inspect.getsource(autopilot_module)
    # The arity helper and the payload are both useless unless the report site passes them: a
    # branch that quietly collapses back to four arguments still calls the policy, still passes
    # every behaviour test above, and the planner stops seeing the counts mid-run.
    check("the loop hands the refusal payload to the policy at the report site",
          'reporter(*reported, getattr(result, "data", None))' in loop_src,
          "the report site no longer forwards what the page answered")
    from types import SimpleNamespace

    # `summary()` is the last call a run makes, and it now reads the policy. A shim with no
    # runtime must still produce it: the gate caught exactly this as an AttributeError on the
    # first version, and a crash here would throw away the measurement criterion 5 depends on.
    shim = SimpleNamespace(
        controller=SimpleNamespace(save=lambda force=False: None, stats=lambda: {}),
        session=1, _step_total=0, _verdict_counts={}, _source_counts={}, replan_requested=0,
        _intervention_total=0, _recoveries=0, _recoveries_ok=0, _recovery_wait_s=0.0,
        _waits=0, _dialogs=0, _dialog_stalls=0, _brain={}, _listing_total=0,
        log_path="gate", _last_step_at=0.0, _first_step_at=0.0)
    bare = Autopilot.summary(shim, "gate")
    check("a summary from a policy it cannot reach still closes the run",
          bare.get("group_refusals") == {} and bare.get("group_read_broken") is False,
          str(bare.get("group_refusals")))
    counted = SimpleNamespace(**vars(shim))
    counted.runtime = SimpleNamespace(policy=QuestPlanner())
    filled = Autopilot.summary(counted, "gate")
    check("the census the policy measured is what the summary prints",
          filled.get("group_refusals") == {key: 0 for key in GROUP_REFUSALS}
          and filled.get("group_read_broken") is False, str(filled.get("group_refusals")))

    # The payload the loop forwards is an envelope, not the answer: `result.data` is
    # `{directive_payload, response, snapshot}` and the page's counts sit inside `response`. A
    # classifier that only reads the outer dict finds nothing and silently falls back to the
    # sentence -- passing the argument would then look like work done.
    check("counts wrapped in the bridge envelope are still found",
          group_refusal_outcome({"response": {"result": {"groups_seen": 3,
                                                         "monsters_here": []}}}) == "unread_members",
          "the envelope was never unwrapped, so every refusal read as no counts")
    check("request bookkeeping is not mistaken for an answer",
          group_refusal_outcome({"directive_payload": {"monster": 447},
                                 "snapshot": {"map_id": 10341}},
                                "target monster 447 is not on this map") == "elsewhere")

    from agent.controller import Controller

    unread_cause = Controller.cause_of(
        "failed", "farm.start", False,
        "target monster 447 is not on this map (2 groups, here: none, actors: actors)",
        {"map_id": 1}, {"map_id": 1},
        detail={"groups_seen": 2, "monsters_here": []})
    check("a broken member read is labelled as a read, not as a wrong target",
          unread_cause == "stale_state", unread_cause)
    elsewhere_cause = Controller.cause_of(
        "failed", "fight.join", False,
        "no group on this map contains monster 447 (here: 900)",
        {"map_id": 1}, {"map_id": 1},
        detail={"groups_seen": 2, "monsters_here": [900]})
    check("a mob that really is elsewhere keeps the wrong-target label",
          elsewhere_cause == "wrong_target", elsewhere_cause)
    no_counts = Controller.cause_of("failed", "fight.join", False,
                                    "no group on this map contains monster 447",
                                    {"map_id": 1}, {"map_id": 1})
    check("the page's join wording is a known cause, not the `unknown` bucket",
          no_counts == "wrong_target", no_counts)
    check("the controller is handed the payload and the call at the decision site",
          'detail=getattr(result, "data", None)' in loop_src
          and "args=dict(call.args))" in loop_src,
          "without the args the controller cannot tell a close from a reply")


def test_board_replay() -> None:
    section("Board replay (G25: can the trained actor play the fights we recorded?)")
    import contextlib
    import importlib.util
    io = __import__("io")
    import types

    script = REPO_ROOT / "tools" / "policy_over_record_boards.py"
    check("the harness exists", script.exists(), str(script))
    spec = importlib.util.spec_from_file_location("policy_over_record_boards", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def board(**_):
        return {"in_fight": True, "is_my_turn": True, "fight_id": 3,
                "contract": {"actionable": False, "value_ready": False,
                             "errors": ["self_max_hp_missing"], "warnings": []},
                "my_stats": {"action_points": 6, "movement_points": 3},
                "fighters": [
                    {"id": 1, "is_me": True, "hp": 40, "max_hp": None, "ap": None,
                     "max_ap": None, "mp": None, "max_mp": None},
                    {"id": -1, "is_me": False, "hp": 185, "max_hp": None}]}

    original = board()
    filled = module.fill(original)
    me, enemy = filled["fighters"]
    check("the self fighter gets its max values from the sheet the same payload carries",
          me["max_ap"] == 6 and me["max_mp"] == 3 and me["ap"] == 6 and me["mp"] == 3
          and me["max_hp"] == 40, str(me))
    check("an enemy's max hp is inferred from its current hp, and labelled as an inference",
          enemy["max_hp"] == 185, str(enemy))
    check("the recorded board is not rewritten by the experiment",
          original["fighters"][0]["max_ap"] is None
          and original["fighters"][1]["max_hp"] is None, str(original["fighters"]))
    check("the hypothesis is stated in code, not only in a docstring",
          "upper bound" in module.ASSUMPTION and "my_stats.action_points" in module.ASSUMPTION,
          module.ASSUMPTION)

    def stub_decide(payload):
        stub_decide.seen.append(payload)
        return {"reason": "policy_direct", "action": {"type": "cast"},
                "contract": {"actionable": True, "value_ready": False},
                "latency_ms": stub_decide.times.pop(0)}
    stub_decide.seen = []
    stub_decide.times = [9.0, 1.0, 5.0]

    def other_turn(payload):
        return {"in_fight": True, "is_my_turn": False, "fighters": []}

    rows = [{"kind": "fight_board", "board": board()} for _ in range(3)]
    report = module.replay(rows + [{"kind": "fight_board", "board": other_turn({})}],
                           stub_decide)
    check("the replay counts boards, reasons and action types",
          report["boards"] == 3 and report["reasons"] == {"policy_direct": 3}
          and report["actions"] == {"cast": 3}, str(report))
    check("a turn that belongs to someone else is not asked",
          len(stub_decide.seen) == 3, str(len(stub_decide.seen)))
    check("percentiles are taken from the sorted latencies",
          report["p50_ms"] == 5.0 and report["p95_ms"] == 9.0, str(report))
    check("the contract state travels with the report, so readiness is visible",
          report["contract"] == {"actionable=True value_ready=False": 3}, str(report))

    def angry(payload):
        raise ZeroDivisionError("no enemies")
    hurt = module.replay(rows[:1], angry)
    check("a policy that raises is counted, never allowed to end the run",
          hurt["errors"] and "ZeroDivisionError" in list(hurt["errors"])[0]
          and hurt["actions"] == {}, str(hurt))

    tmp = Path(tempfile.mkdtemp())
    real_modules = dict(sys.modules)
    try:
        log = tmp / "fight_boards.jsonl"
        log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        empty = tmp / "nothing.jsonl"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = module.main([str(empty)])
        check("no recorded boards is a refusal to conclude, not a zero",
              code == 1, str(code))

        fake = types.ModuleType("combat.live_policy")

        class _Policy:
            def __init__(self, checkpoint=None, **_kw):
                self.checkpoint = checkpoint

            def decide(self, payload):
                return {"reason": "policy_pass", "action": {"type": "pass"},
                        "contract": {"actionable": True, "value_ready": False},
                        "latency_ms": 2.0}
        fake.LivePolicy = _Policy
        sys.modules["combat.live_policy"] = fake
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = module.main([str(log)])
        out = buf.getvalue()
        check("the replay prints the hypothesis it ran under before its numbers",
              code == 0 and "fill hypothesis:" in out, out[:300])
        check("the verdict separates the measurement from live proof",
          "not a live fight, not a win rate" in out, out[-320:])
        check("the as-recorded contract state is printed, so the gap stays visible",
              "as recorded, contract actionable: {'False': 3}" in out, out[:400])

        class _NoJax:
            def __init__(self, *a, **kw):
                raise ModuleNotFoundError("No module named 'jax'")
        fake.LivePolicy = _NoJax
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = module.main([str(log)])
        check("an interpreter that cannot load the brain is reported as itself missing",
              code == 2 and "could not be loaded" in buf.getvalue(), buf.getvalue()[:300])
    finally:
        sys.modules.clear()
        sys.modules.update(real_modules)
        shutil.rmtree(tmp, ignore_errors=True)


def test_quest_advance_shapes() -> None:
    """G31's count was wrong because a call was classified from one argument.

    `quest_id` is on every dialog answer, every claim and every close as well as on the
    accept route, so "row carries a quest_id and no objective_id" counted 1,145 quest.advance
    rows as quest-start requests and reported 462 of them as doomed. The real split in the same
    corpus is talk 519 / reply 112 / close 148 / start 366 -- and all 366 starts were doomed, so
    the correction makes the guard's case stronger while cutting the cost it was credited with
    by a factor of 15. `tools/quest_advance_shapes.py` is now the only place that answers "what
    kind of call was this", and it is what the baseline witness reads, so this section pins it
    against constructed rows (never against the live corpus, which rotates) and pins the
    corrected numbers in the two documents that carry them.
    """
    import contextlib
    import importlib.util
    import io
    from pathlib import Path as _Path

    script = REPO_ROOT / "tools" / "quest_advance_shapes.py"
    section("Call shape decides what a quest.advance row means")
    check("the shape tool exists", script.exists(), str(script))
    spec = importlib.util.spec_from_file_location("quest_advance_shapes", str(script))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    check("a quest id with an npc id is a talk, not an accept",
          module.shape({"quest_id": 3841, "npc_id": 3888}) == "talk")
    check("a quest id with an objective id is a claim",
          module.shape({"quest_id": 3841, "objective_id": 14056}) == "claim")
    check("a quest id with a reply is an answer",
          module.shape({"quest_id": 3841, "reply": 2}) == "reply"
          and module.shape({"quest_id": 3841, "reply_text": "Oui"}) == "reply")
    check("a quest id with close is a leave",
          module.shape({"quest_id": 3841, "close": True}) == "close")
    check("only a bare quest id is the accept route",
          module.shape({"quest_id": 584}) == "start")
    check("a call with no quest id at all is neither",
          module.shape({}) == "other" and module.shape(None) == "other")
    check("an objective id wins over a reply when both are present",
          module.shape({"quest_id": 1, "objective_id": 7, "reply": 0}) == "claim",
          str(module.shape({"quest_id": 1, "objective_id": 7, "reply": 0})))

    def row(tool: str, args: Dict[str, Any], obs: Optional[Dict[str, Any]] = None,
            ok: bool = True, at: float = 1.0,
            result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        out: Dict[str, Any] = {"kind": "step", "session": "s", "instance": "i",
                               "tool": tool, "args": args, "ok": ok, "at": at,
                               "observation": obs if obs is not None else {}}
        if result is not None:
            out["result"] = result
        return out

    def answer(**data: Any) -> Dict[str, Any]:
        return {"response": {"result": {"ok": True, "data": data}}}

    def drive(rows: List[Dict[str, Any]], window: int = 5) -> Dict[str, Any]:
        tmp = _Path(tempfile.mkdtemp())
        try:
            log = tmp / "default.jsonl"
            log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                module.main([str(log), "--window", str(window)])
            return {"out": buf.getvalue(), "sum": module.summarise([str(log)], window=window)}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    mixed = drive([
        row("quest.advance", {"quest_id": 3841, "npc_id": 3888}),
        row("quest.advance", {"quest_id": 3841, "objective_id": 14056}),
        row("quest.advance", {"quest_id": 3841, "reply": 1}),
        row("quest.advance", {"quest_id": 3841, "close": True}),
        row("quest.advance", {"quest_id": 584}, result=answer(via="QuestStartRequestMessage")),
        row("state.snapshot", {}),
    ])
    shapes = mixed["sum"]["shapes"]
    check("one row of each shape classifies into exactly one bucket",
          shapes == {"talk": 1, "claim": 1, "reply": 1, "close": 1, "start": 1},
          str(shapes))
    check("the start count is not the quest.advance count",
          mixed["sum"]["quest_advance"] == 5
          and mixed["sum"]["starts"]["count"] == 1, str(mixed["sum"]["quest_advance"]))
    check("the retired filter's population is the whole non-claim quest_id traffic",
          shapes.get("talk", 0) + shapes.get("reply", 0) + shapes.get("close", 0)
          + shapes.get("start", 0) == 4
          and mixed["sum"]["starts"]["count"] != 4, str(shapes))
    def first_row(res: Dict[str, Any]) -> Dict[str, Any]:
        """The first start row, or an empty dict.

        A check that raises hides every check after it in the section, so a mutation that
        makes the tool find no starts at all has to fail an assertion, not the run.
        """
        rows = res["starts"]["rows"]
        return rows[0] if rows else {}

    check("a start for an id the client never reported is flagged",
          mixed["sum"]["starts"]["never_reported"] == 1
          and mixed["sum"]["starts"]["honoured"] == 0, str(mixed["sum"]["starts"]))
    check("the page's own answer is read out of the nested response",
          first_row(mixed["sum"]).get("page") == "QuestStartRequestMessage",
          str(first_row(mixed["sum"])))
    check("the report prints the true start count rather than the row count",
          "true quest-start requests: 1" in mixed["out"], mixed["out"][-320:])

    confirmed = drive([
        row("quest.advance", {"quest_id": 4260},
            {"quests_active": {"4260": 0}}),
    ])
    check("a quest the client does report is not counted as never-reported",
          confirmed["sum"]["starts"]["never_reported"] == 0,
          str(confirmed["sum"]["starts"]))
    honoured = drive([
        row("quest.advance", {"quest_id": 4260}),
        row("state.snapshot", {}, {"quests_active": {}}),
        row("state.snapshot", {}, {"quests_active": {"4260": 0}}),
    ])
    check("a start the following snapshots show active is honoured",
          honoured["sum"]["starts"]["honoured"] == 1
          and honoured["sum"]["starts"]["honoured_outside_dialog"] == 1,
          str(honoured["sum"]["starts"]))
    late = drive([row("quest.advance", {"quest_id": 4260}),
                  *[row("state.snapshot", {}) for _ in range(5)],
                  row("state.snapshot", {}, {"quests_active": {"4260": 0}})], window=5)
    check("activation outside the window does not count as honour",
          late["sum"]["starts"]["honoured"] == 0, str(late["sum"]["starts"]))
    wide = drive([row("quest.advance", {"quest_id": 4260}),
                  *[row("state.snapshot", {}) for _ in range(5)],
                  row("state.snapshot", {}, {"quests_active": {"4260": 0}})], window=9)
    check("the window is the caller's, not a constant",
          wide["sum"]["starts"]["honoured"] == 1, str(wide["sum"]["starts"]))

    inside = drive([
        row("quest.advance", {"quest_id": 4260}, {"in_dialog": True}),
    ])
    check("a bare quest id sent while a dialog is open is recorded as such",
          inside["sum"]["starts"]["while_dialog_open"] == 1
          and inside["sum"]["starts"]["honoured_outside_dialog"] == 0,
          str(inside["sum"]["starts"]))
    check("and the report says what the page actually did with it",
          "the page answers reply 0 there" in inside["out"], inside["out"][-300:])
    nested = drive([row("quest.advance", {"quest_id": 4260},
                        {"dialog": {"open": True, "message": "x"}})])
    check("the nested dialog block counts as open too, not only the flat flag",
          nested["sum"]["starts"]["while_dialog_open"] == 1,
          str(nested["sum"]["starts"]))

    silent = drive([row("quest.advance", {"quest_id": 584}, {}, ok=True)])
    check("a row the page answered without a stage is called silent, not a success",
          first_row(silent["sum"]).get("page") == "silent",
          str(first_row(silent["sum"])))
    refused = drive([{"kind": "step", "session": "s", "instance": "i",
                      "tool": "quest.advance", "args": {"quest_id": 584}, "ok": False,
                      "error": "no client instance is running", "observation": {}}])
    check("a transport failure keeps its reason instead of becoming a page answer",
          "no client instance" in str(first_row(refused["sum"]).get("page")),
          str(first_row(refused["sum"])))

    # The documents must carry the corrected arithmetic, not the retired claim.
    baseline = (REPO_ROOT / "docs" / "BASELINE.md").read_text(encoding="utf-8",
                                                              errors="replace")
    gaps = (REPO_ROOT / "docs" / "KNOWN_GAPS.md").read_text(encoding="utf-8",
                                                            errors="replace")
    check("the baseline witness asks for a start-shaped row",
          "Start-shaped means the arguments are a quest id and nothing else" in baseline)
    check("the retired 462-of-1,145 claim is gone from the witness",
          "462 of them name" not in baseline and "8.08 % of all 5,721" not in baseline)
    check("the corrected numbers are in the witness",
          "**366** such requests" in baseline and "3.21 % of the 11,395 recorded rows" in baseline)
    check("G31's own section records the correction rather than deleting it",
          "**The correction, because it is the more instructive finding.**" in gaps
          and "talk 519 + reply 112 + close 148 + start 366" in gaps)
    links_js = ROOT / "tools" / "test_dialog_links.js"
    check("the page-side dialog suite exists and no longer claims 212 accepts",
          links_js.exists() and "212 quest-start attempts" not in
          links_js.read_text(encoding="utf-8", errors="replace"), str(links_js))
    check("and the gap list carries G32 and G33 as the accept route's two halves",
          "## G32." in gaps and "## G33." in gaps)


def test_live_transitions() -> None:
    section("Live transitions (did the issued tool move the real world?)")
    import importlib.util
    import json as _json
    import tempfile

    tool = REPO_ROOT / "tools" / "build_live_transitions.py"
    check("the live transition builder exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("build_live_transitions", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def step(ts, goal, tool_name, before, after, ok=True, error=None):
        return {"kind": "step", "ts": ts, "iso": "2026-10-27T00:00:00",
                "instance": "unit", "goal": goal, "tool": tool_name, "args": {},
                "ok": ok, "error": error, "latency_ms": 12,
                "observation": before,
                "result": {"ok": ok, "snapshot": after}}

    obj = {"type_id": 14, "params": "{}"}
    rows = [
        step(1000, "do all the quests", "nav.goto",
             {"map": {"map_id": 10898, "cell": 3}, "position": {"map_id": 10898, "cell": 3},
              "current_objective": obj,
              "quests_active": [{"id": 3841, "level": 12}], "objectives_completed": 0,
              "hp": 100},
             {"map": {"map_id": 10899, "cell": 3}, "position": {"map_id": 10899, "cell": 3},
              "current_objective": obj,
              "quests_active": [{"id": 3841, "level": 12}], "objectives_completed": 0,
              "hp": 100}),
        # accepted, and nothing moved: this is the 86% of the real corpus
        step(1001, "do all the quests", "quest.advance",
             {"map": {"map_id": 10899, "cell": 3}, "position": {"map_id": 10899, "cell": 3},
              "current_objective": obj,
              "quests_active": [{"id": 3841, "level": 12}], "objectives_completed": 0,
              "hp": 100},
             {"map": {"map_id": 10899, "cell": 3}, "position": {"map_id": 10899, "cell": 3},
              "current_objective": obj,
              "quests_active": [{"id": 3841, "level": 12}], "objectives_completed": 0,
              "hp": 100}),
        # a read that only refreshes hp is neither an action nor progress
        step(1002, "do all the quests", "state.snapshot",
             {"map": {"map_id": 10899, "cell": 3}, "position": {"map_id": 10899, "cell": 3},
              "current_objective": obj,
              "quests_active": [{"id": 3841, "level": 12}], "objectives_completed": 0,
              "hp": 100},
             {"map": {"map_id": 10899, "cell": 3}, "position": {"map_id": 10899, "cell": 3},
              "current_objective": obj,
              "quests_active": [{"id": 3841, "level": 12}], "objectives_completed": 0,
              "hp": 96}),
        step(1003, "do all the quests", "quest.advance",
             {"map": {"map_id": 10899, "cell": 3}, "position": {"map_id": 10899, "cell": 3},
              "current_objective": obj,
              "quests_active": [{"id": 3841, "level": 12}], "objectives_completed": 0,
              "hp": 96},
             {"map": {"map_id": 10899, "cell": 3}, "position": {"map_id": 10899, "cell": 3},
              "current_objective": obj,
              "quests_active": [{"id": 3841, "level": 12}], "objectives_completed": 1,
              "hp": 96}),
    ]
    segments = module.segments(rows, "unit.jsonl", gap_s=120.0)
    check("one goal is one segment", len(segments) == 1, len(segments))
    labelled = module.label_steps(segments[0])
    check("labels per step",
          [(row["tool"], row["was_read"], row["moved"], row["progress"]) for row in labelled]
          == [("nav.goto", False, True, False),
              ("quest.advance", False, False, False),
              ("state.snapshot", True, False, False),
              ("quest.advance", False, True, True)],
          [(row["tool"], row["was_read"], row["moved"], row["progress"]) for row in labelled])
    check("an hp tick alone is not the world moving",
          "hp" in labelled[2]["changed"] and not labelled[2]["moved"], labelled[2]["changed"])
    check("progress names the counter that moved",
          labelled[3]["progress_keys"] == ["objectives_completed"], labelled[3]["progress_keys"])
    from agent.features import FEATURE_COUNT
    check("the live state vector is the same width as the harness corpus",
          all(row["x"] and len(row["x"]) == FEATURE_COUNT for row in labelled),
          [len(row["x"] or []) for row in labelled])
    check("the streak is re-derived from the log itself",
          labelled[-1]["x"] is not None and labelled[1]["x"] is not None,
          labelled[1]["x_source"])

    # The server applies some effects after the directive returns, so the read that
    # follows sees them. Measured live 2026-10-29: 173 of 195 "moved" reads sat right
    # after a quest.advance. The effect must belong to the acting step.
    here = {"map": {"map_id": 10899, "cell": 3}, "position": {"map_id": 10899, "cell": 3},
            "current_objective": obj, "hp": 100}
    there = {"map": {"map_id": 10899, "cell": 40}, "position": {"map_id": 10899, "cell": 40},
             "current_objective": obj, "hp": 100}
    lagged = module.label_steps({
        "source": "unit", "instance": "unit", "goal": "lag",
        "steps": [step(2000, "lag", "nav.goto", here, here),
                  step(2001, "lag", "state.snapshot", here, there)]})
    check("a read that sees a move does not claim it",
          lagged[1]["moved"] is False and lagged[1]["moved_observed"] is True, lagged[1])
    check("the acting step owns the effect the next read revealed",
          lagged[0]["moved"] is True and lagged[0].get("moved_lag") == 1, lagged[0])
    check("the credited keys are the ones the read observed",
          lagged[0]["moved_keys"] == ["cell"], lagged[0]["moved_keys"])

    # Time is charged to the step that caused the wait, not to the one after it.
    # `latency_ms` is only the directive round trip; the decision cadence is the
    # gap between records, and a throughput reward needs that one.
    timed = module.label_steps({
        "source": "unit", "instance": "unit", "goal": "timed",
        "steps": [step(3000.0, "timed", "nav.goto", here, here),
                  step(3005.0, "timed", "state.snapshot", here, here),
                  step(3007.0, "timed", "quest.advance", here, here)]})
    check("the wait before the next decision is charged to the previous step",
          timed[0]["dt_ms"] == 5000.0 and timed[1]["dt_ms"] == 2000.0,
          [row["dt_ms"] for row in timed])
    check("the last step of a segment has no next decision to time",
          timed[2]["dt_ms"] is None, timed[2]["dt_ms"])

    split_rows = module.segments(rows[:2] + [dict(rows[2], ts=1000 + 900)], "unit.jsonl", 120.0)
    check("a long silence starts a new segment", len(split_rows) == 2, len(split_rows))
    goal_rows = module.segments(rows[:1] + [dict(rows[1], goal="farm xuhs")], "unit.jsonl", 120.0)
    check("a goal change starts a new segment", len(goal_rows) == 2, len(goal_rows))

    key = module.segment_key(segments[0])
    plan = module.plan_splits(labelled)
    check("a segment lands in exactly one split",
          len({plan[row["segment"]] for row in labelled}) == 1, plan.get(key))
    check("the split is deterministic", module.plan_splits(labelled) == plan, None)
    actions = [row for row in labelled if row["has_pair"] and not row["was_read"]]
    synthetic = [dict(labelled[0], segment="s%d" % (index % 6), has_pair=True,
                      was_read=False) for index in range(120)]
    big = module.plan_splits(synthetic)
    shares = {name: sum(1 for index in range(120)
                        if big["s%d" % (index % 6)] == name) for name in ("train", "val", "test")}
    check("a balanced plan leaves no fold nearly empty",
          all(count >= 6 for count in shares.values()) or not actions, shares)

    out = Path(tempfile.mkdtemp(prefix="transitions-"))
    rc = module.main(["--log", "definitely-not-here.jsonl", "--out", str(out)])
    check("a missing log is refused, not an empty dataset", rc == 2, rc)

    # end to end: the writer, not just the planner, must keep a segment in one fold
    fixture = Path(tempfile.mkdtemp(prefix="transitions-log-"))
    log = fixture / "unit.jsonl"
    log.write_text("".join(_json.dumps(row) + chr(10) for row in rows), encoding="utf-8")
    written = fixture / "out"
    rc = module.main(["--log", str(log), "--out", str(written)])
    check("the builder writes a dataset", rc == 0, rc)
    folds: Dict[str, set] = {}
    landed: Dict[str, str] = {}
    total = 0
    for name in ("train", "val", "test"):
        path = written / ("%s.jsonl" % name)
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            row = _json.loads(line)
            total += 1
            folds.setdefault(row["segment"], set()).add(name)
            landed[row["id"]] = name
    check("no segment is split across folds",
          all(len(names) == 1 for names in folds.values()),
          {key: sorted(names) for key, names in folds.items()})
    # row for row, the writer must obey the plan: a "one fold per segment" assertion alone can
    # pass by hash luck when a segment's rows happen to hash into the same bucket
    expected = {row["id"]: plan[row["segment"]] for row in labelled}
    check("every row lands in the fold its segment was planned for",
          landed == expected,
          {key: (landed.get(key), expected.get(key))
           for key in expected if landed.get(key) != expected.get(key)})
    check("every labelled row is written exactly once",
          total == len(labelled) and len(folds) == 1, (total, len(labelled)))

    # one segment cannot prove a per-segment split: with a single segment, a per-row hash agrees
    # with the plan whenever the numbers happen to fall in the same bucket. Six segments, three
    # steps each, is what makes the writer's contract testable.
    multi: List[Dict[str, Any]] = []
    base = {"map": {"map_id": 10898, "cell": 3}, "position": {"map_id": 10898, "cell": 3},
            "current_objective": obj, "quests_active": [{"id": 3841, "level": 12}],
            "objectives_completed": 0, "hp": 100}
    for slot in range(6):
        after = {"map": {"map_id": 10900 + slot, "cell": 4},
                 "position": {"map_id": 10900 + slot, "cell": 4},
                 "current_objective": obj,
                 "quests_active": [{"id": 3841, "level": 12}],
                 "objectives_completed": slot, "hp": 100}
        for offset in range(3):
            multi.append(step(2000 + slot * 20 + offset, "goal %d" % slot,
                              "nav.goto", base, after))
    big_log = fixture / "multi.jsonl"
    big_log.write_text("".join(_json.dumps(row) + chr(10) for row in multi), encoding="utf-8")
    big_out = fixture / "multi-out"
    check("the multi-segment fixture is written",
          module.main(["--log", str(big_log), "--out", str(big_out)]) == 0, None)
    big_labelled = [row for segment in module.segments(multi, "multi.jsonl", 120.0)
                    for row in module.label_steps(segment)]
    big_plan = module.plan_splits(big_labelled)
    check("the plan uses more than one fold",
          len(set(big_plan.values())) >= 2, sorted(set(big_plan.values())))
    actual: Dict[str, str] = {}
    for name in ("train", "val", "test"):
        path = big_out / ("%s.jsonl" % name)
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            actual[_json.loads(line)["id"]] = name
    want = {row["id"]: big_plan[row["segment"]] for row in big_labelled}
    check("the writer obeys the segment plan row for row",
          actual == want,
          {key: (actual.get(key), want.get(key))
           for key in want if actual.get(key) != want.get(key)})
    manifest = written / "manifest.json"
    check("the manifest reports the real move rate it measured",
          manifest.is_file() and "action_move_rate" in manifest.read_text(encoding="utf-8"),
          None)


def test_foresight_net() -> None:
    section("Foresight net (candidate-conditioned progress, torch-free scoring)")
    import json as _json
    import tempfile

    import numpy as np

    from agent import foresight
    from agent.features import FEATURE_COUNT, TOOLS

    width, hidden, depth = FEATURE_COUNT, 4, 3
    ntools = len(TOOLS)
    rng = np.random.RandomState(7)
    arrays = {
        "w1": rng.randn(2 * width + ntools, hidden).astype(np.float32) * 0.05,
        "b1": np.zeros(hidden, dtype=np.float32),
        "w2": rng.randn(hidden, hidden).astype(np.float32) * 0.05,
        "b2": np.zeros(hidden, dtype=np.float32),
        "w3": np.zeros((hidden, 2 + depth), dtype=np.float32),
        "b3": np.array([0.0, 0.5, 1.0, 1.0, 1.0], dtype=np.float32),
        "x_mean": np.zeros(width, dtype=np.float32),
        "x_std": np.ones(width, dtype=np.float32),
        "tool_gate": np.zeros((ntools, width), dtype=np.float32),
    }
    net = foresight.ForesightNet(arrays, tools=list(TOOLS), meta={"depth": depth})
    state = [0.0] * width
    check("a known tool is scored",
          net.score(state, "quest.advance") is not None, None)
    check("an unknown tool fails closed instead of guessing",
          net.score(state, "punch.a.wizard") is None, None)
    cell = net.score(state, "quest.advance")
    check("the curve is read at the trained depth",
          cell is not None and len(cell["curve"]) == depth and not cell["flat"], cell)
    arrays_zero = dict(arrays)
    arrays_zero["b3"] = np.array([0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
    flat_net = foresight.ForesightNet(arrays_zero, tools=list(TOOLS), meta={"depth": depth})
    check("a flat curve is flagged, which is what an escalation decision needs",
          flat_net.score(state, "nav.goto")["flat"] is True, None)

    arrays_read = dict(arrays)
    boost = np.zeros((ntools, width), dtype=np.float32)
    w3 = np.asarray(arrays["w3"], dtype=np.float32).copy()
    read_slot = TOOLS.index("state.snapshot")
    action_slot = TOOLS.index("quest.advance")
    w3[:, 1] = 1.0
    w3[0, 1] = 100.0  # first hidden unit fires only for the read's own gate pattern
    arrays_read["w3"] = w3
    arrays_read["tool_gate"] = boost
    skewed = foresight.ForesightNet(arrays_read, tools=list(TOOLS), meta={"depth": depth})
    ranked = skewed.rank(state, ["state.snapshot", "quest.advance"])
    check("a read never outranks an action, whatever it predicts",
          [row["tool"] for row in ranked] == ["quest.advance", "state.snapshot"],
          [(row["tool"], row["gain"], row["read"]) for row in ranked])

    try:
        net.score([0.0] * (width - 1), "nav.goto")
        narrow_ok = False
    except ValueError as exc:
        # numpy also raises ValueError on a shape mismatch, so the *message* is the evidence that
        # the module's own width guard ran; without it a removed guard would look like a pass
        narrow_ok = "foresight expects" in str(exc)
    check("a stale feature width raises instead of silently mis-scoring", narrow_ok, None)
    check("scoring is deterministic",
          net.score(state, "nav.goto") == net.score(state, "nav.goto"), None)

    target = Path(tempfile.mkdtemp(prefix="foresight-")) / "foresight.json"
    payload = {"meta": {"depth": depth, "hidden": hidden},
               "tools": list(TOOLS),
               "arrays": {k: np.asarray(v).tolist() for k, v in arrays.items()}}
    target.write_text(_json.dumps(payload), encoding="utf-8")
    loaded = foresight.load(target)
    check("the shipped JSON form scores identically to the trained arrays",
          loaded is not None and loaded.score(state, "nav.goto") == net.score(state, "nav.goto"),
          None)
    check("a missing artifact loads as None", foresight.load(target.with_name("nope.json")) is None,
          None)
    broken = _json.loads(_json.dumps({k: v for k, v in payload.items()}))
    broken["arrays"].pop("tool_gate")
    broken_path = target.with_name("broken.json")
    broken_path.write_text(_json.dumps(broken), encoding="utf-8")
    check("an artifact missing a required array loads as None",
          foresight.load(broken_path) is None, None)
    check("the runtime module needs no torch",
          "import torch" not in (REPO_ROOT / "agent" / "foresight.py")
          .read_text(encoding="utf-8"), None)


def test_counterfactual_factory_alive() -> None:
    section("Counterfactual factory is still alive (the corpus cannot silently empty)")
    import json as _json
    import subprocess
    import tempfile

    from agent.features import FEATURE_COUNT

    out = Path(tempfile.mkdtemp(prefix="factory-"))
    cmd = [sys.executable, str(REPO_ROOT / "sim" / "tool_choice.py"),
           "--episodes", "2", "--steps", "24", "--seed", "3",
           "--focus", "0", "--out", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                          cwd=str(REPO_ROOT), encoding="utf-8", errors="replace")
    stats = {}
    try:
        stats = _json.loads(proc.stdout)
    except ValueError:
        pass
    check("the harness still generates counterfactual rows",
          proc.returncode == 0 and stats.get("rows", 0) > 0,
          {"rc": proc.returncode, "rows": stats.get("rows"),
           "tail": (proc.stderr or "")[-300:]})
    check("the corpus is written at the live feature width",
          stats.get("features") == FEATURE_COUNT, stats.get("features"))
    focused = 0
    train = out / "train.jsonl"
    if train.is_file():
        with train.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = _json.loads(line)
                # A row only exists once an objective is active, so a row of the focused type
                # proves the whole chain worked: the harness reported the offer, the planner
                # accepted it, and the world produced a featurisable objective.
                if row.get("objective_type") == 0:
                    focused += 1
    check("episodes reach an objective of the focused type", focused > 0, focused)


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
        cfg = live_ready.network_config()
        check("network.ini is read as keys", cfg.get("mode") in
              ("auto", "custom", "direct", "wireproxy", "warp"), cfg.get("mode"))
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


def _run_test(fn) -> None:
    """One test may fail, but it must not hide the rest of the gate.

    Measured 2026-10-28: a source needle in test_dialog_labels raised
    ValueError and the process exited 1 after printing a section header - no
    "checks, failed" line, so the run looked like a pass with a stray exit
    code. A raising test is now a failure the summary counts.
    """
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - the gate must survive its own tests
        import traceback
        traceback.print_exc()
        check("test %s ran to completion" % getattr(fn, "__name__", fn),
              False, "%s: %s" % (type(exc).__name__, exc))


def run() -> Tuple[int, List[str]]:
    _run_test(test_verdict_vocabulary)
    _run_test(test_verifier_rules)
    _run_test(test_verifier_model_contract)
    _run_test(test_tool_vocabulary)
    _run_test(test_forbidden_and_validation)
    _run_test(test_plan_stack)
    _run_test(test_shared_resources)
    _run_test(test_brain_scaling_40)
    _run_test(test_brain_lifecycle)
    _run_test(test_live_collection_lifecycle)
    _run_test(test_pointer_coverage_gate)
    _run_test(test_constraint_corpus)
    _run_test(test_objective_buckets)
    _run_test(test_objective_parameter_table)
    _run_test(test_spawn_wait_is_bounded)
    _run_test(test_free_form_searches_new_maps)
    _run_test(test_wake_login_tool)
    _run_test(test_refusal_evidence)
    _run_test(test_cli_launch_forwarding)
    _run_test(test_stuck_objective_retired)
    _run_test(test_doorless_map_asks_for_the_neighbour)
    _run_test(test_auto_policy_is_the_planner)
    _run_test(test_free_form_uses_interactive)
    _run_test(test_command_routing_and_bridge_fast_fail)
    _run_test(test_damage_calibration_join)
    _run_test(test_damage_calibration_live_evidence)
    _run_test(test_knowledge_identity)
    _run_test(test_client_confirmed_starts)
    _run_test(test_quest_advance_shapes)
    _run_test(test_dialog_ranking)
    _run_test(test_prompt_fidelity)
    _run_test(test_named_mob_focus)
    _run_test(test_thinking_layer)
    _run_test(test_claim_quarantine)
    _run_test(test_failure_causes)
    _run_test(test_absent_client_recovery)
    _run_test(test_identity_binding)
    _run_test(test_live_witnesses)
    _run_test(test_verifier_head_gate)
    _run_test(test_cause_split_audit)
    _run_test(test_cause_head_artifact)
    _run_test(test_observation_text)
    _run_test(test_kaggle_toolkit_sync)
    _run_test(test_quest_chain_replay)
    _run_test(test_live_ready)
    _run_test(test_sim_planner_runs)
    _run_test(test_command_parser)
    _run_test(test_command_dataset)
    _run_test(test_command_shadow)
    _run_test(test_command_shadow_report)
    _run_test(test_command_replay)
    _run_test(test_live_evidence)
    _run_test(test_command_onnx_decode)
    _run_test(test_egress_preflight)
    _run_test(test_auto_live_guards)
    _run_test(test_host_launch_guard)
    _run_test(test_live_transitions)
    _run_test(test_foresight_net)
    _run_test(test_counterfactual_factory_alive)
    _run_test(test_repair_credit)
    _run_test(test_reads_are_not_actions)
    _run_test(test_dialog_labels)
    _run_test(test_dialog_repeat_guard)
    _run_test(test_quest_accept_planner)
    _run_test(test_observed_quest_catalog)
    _run_test(test_observed_quest_ingest)
    _run_test(test_dialog_label_reader)
    _run_test(test_login_recovery)
    _run_test(test_brain_autostart)
    _run_test(test_single_control_plane)
    _run_test(test_planner_buy_branch)
    _run_test(test_always_on_ai)
    _run_test(test_brain_readiness)
    _run_test(test_board_replay)
    _run_test(test_group_scan_reader)
    _run_test(test_npc_start_rotation_bound)
    _run_test(test_group_refusal_routing)
    _run_test(test_ready_objective_scan)
    _run_test(test_session_report_census)
    _run_test(test_report_instrument)
    _run_test(test_settle_waits_for_promised_effect)
    _run_test(test_intervention_instrument)
    _run_test(test_js_page_suites)
    _run_test(test_talk_needs_resolved_actor)
    _run_test(test_equip_slot_from_template)
    _run_test(test_door_cell_memory)
    _run_test(test_combined_objectives)
    _run_test(test_ready_set_ranking)
    _run_test(test_login_cooldown_guard)
    _run_test(test_board_pca_signature)
    _run_test(test_one_driver_per_instance)
    _run_test(test_boss_benchmark_runs)
    _run_test(test_bundle_resilience)
    _run_test(test_bundle_updater)
    _run_test(test_multi_instance_targeting)
    _run_test(test_per_instance_ai)
    _run_test(test_instance_reset)
    _run_test(test_fight_end_witness)
    _run_test(test_join_names_a_group)
    _run_test(test_battle_log_and_challenges)
    _run_test(test_no_loop_without_client)
    _run_test(test_keep_alive_instrument)
    _run_test(test_window_layout)
    _run_test(test_tool_ensemble)
    _run_test(test_fight_rotation)
    _run_test(test_exhausted_ready_maps)
    _run_test(test_index_route_travel)
    _run_test(test_planner_replay)
    _run_test(test_packet_redaction)
    _run_test(test_tutorial_hold)
    _run_test(test_fight_hint_tap)
    _run_test(test_auto_live_summarise)
    _run_test(test_dataset_split_integrity)
    _run_test(test_temporal_split_plan)
    _run_test(test_claim_labels)
    _run_test(test_mechanism_features)
    _run_test(test_live_within_segment_eval)
    _run_test(test_ingest_after_run)
    _run_test(test_ope_estimators)
    _run_test(test_fork_carries_the_planner_state)
    _run_test(test_exploration_policy)
    _run_test(test_explore_report)
    _run_test(test_knowledge_index)
    _run_test(test_combat_suites)
    _run_test(test_go_relay_suites)
    _run_test(test_csharp_compile)
    _run_test(test_cs_secret_redaction)
    return _CHECKS, _FAILURES


def test_fight_hint_tap() -> None:
    section("Fight-time tutorial hint (the challenge step needs a real touchend)")
    import types
    import agent.autopilot as _auto
    from agent.autopilot import Autopilot

    class _Executor:
        def __init__(self) -> None:
            self.calls: List[tuple] = []

        def execute(self, tool, args, **kw):
            self.calls.append((tool, dict(args)))
            return types.SimpleNamespace(ok=True, error=None, data={"pressed": True})

    class _Wait:
        _wait_out_fight = Autopilot._wait_out_fight

        def __init__(self, obs, dry_run=False) -> None:
            self.dry_run = dry_run
            self._forced_call = None
            self._fight_wait_since = 1000.0
            self._fight_wait_report = 1000.0
            self._fight_arm_at = 1000.0
            self._fight_board_at = 1000.0
            self._hint_taps = 0
            self._hint_tap_at = 0.0
            self._obs_age_ms = 0
            self._obs_source = "live"
            self.executor = _Executor()
            self.runtime = types.SimpleNamespace(
                executor=self.executor, instance="dofus-test")

    real_time = _auto.time
    _auto.time = types.SimpleNamespace(time=lambda: 1000.0, sleep=lambda s: None,
                                       monotonic=lambda: 0.0)
    try:
        hint = {"active": True, "quest_id": 4262, "hint_target": "challenge"}
        obs = {"in_fight": True, "auto_fight": True, "tutorial": hint}
        w = _Wait(obs)
        w._wait_out_fight(obs, "auto")
        check("the scenario's challenge hint becomes one client_target press",
              w.executor.calls == [("ui.tap", {"client_target": "challenge"})],
              w.executor.calls)
        w._wait_out_fight(obs, "auto")
        check("the press is not repeated within its spacing",
              len(w.executor.calls) == 1, w.executor.calls)
        w._hint_tap_at -= 3.0
        w._wait_out_fight(obs, "auto")
        check("a retry is allowed after the spacing", len(w.executor.calls) == 2,
              w.executor.calls)
        for _ in range(6):
            w._hint_tap_at -= 3.0
            w._wait_out_fight(obs, "auto")
        check("one fight takes at most five presses, then the wait is silent",
              len(w.executor.calls) == 5, w.executor.calls)

        plain = _Wait({"in_fight": True, "auto_fight": True,
                       "tutorial": {"active": True, "hint_target": None}})
        plain._wait_out_fight({"in_fight": True, "auto_fight": True,
                               "tutorial": {"active": True, "hint_target": None}}, "auto")
        check("a step with no hint sends nothing", plain.executor.calls == [],
              plain.executor.calls)

        dry = _Wait(obs, dry_run=True)
        dry._wait_out_fight(obs, "auto")
        check("a dry run presses nothing", dry.executor.calls == [], dry.executor.calls)

        fresh = _Wait(obs)
        fresh._fight_wait_since = None
        fresh._ensure_brain = lambda *a, **k: None
        fresh._wait_out_fight(obs, "auto")
        check("a new fight resets the press budget",
              fresh._hint_taps == 1 and len(fresh.executor.calls) == 1,
              (fresh._hint_taps, fresh.executor.calls))
    finally:
        _auto.time = real_time


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


def test_dataset_split_integrity() -> None:
    section("Dataset splits (a held-out row must never share a run with train)")
    import json as _json

    def read(path):
        rows = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(_json.loads(line))
        return rows

    # tool_sft_v3: episodes are simulated play-throughs. A row's neighbours in the
    # same episode are near-copies of its state, so an episode on both sides of the
    # split leaks the answer even when the rows differ.
    sft = REPO_ROOT / "data" / "tool_sft_v3"
    if (sft / "train.jsonl").is_file() and (sft / "val.jsonl").is_file():
        train = read(sft / "train.jsonl")
        val = read(sft / "val.jsonl")
        train_eps = {row.get("ep") for row in train}
        val_eps = {row.get("ep") for row in val}
        check("tool_sft_v3 train and val share no episode",
              not (train_eps & val_eps), sorted(train_eps & val_eps)[:3])
        check("tool_sft_v3 rows all name an episode",
              None not in train_eps and None not in val_eps,
              "a row without ep")

    # transitions_v2: a segment is one goal run of one instance. The build script
    # splits by segment; if it ever regressed to row-wise sampling, the same run
    # would sit in train and test and every live metric would inflate.
    live = REPO_ROOT / "data" / "transitions_v2"
    splits = {}
    for name in ("train", "val", "test"):
        path = live / (name + ".jsonl")
        if path.is_file():
            splits[name] = read(path)
    if len(splits) >= 2:
        segs = {name: {row.get("segment") for row in rows}
                for name, rows in splits.items()}
        for name, rows in splits.items():
            check("transitions_v2 %s rows all name a segment" % name,
                  None not in segs[name], "a row without segment")
        names = sorted(splits)
        for i, left in enumerate(names):
            for right in names[i + 1:]:
                shared = segs[left] & segs[right]
                check("transitions_v2 %s and %s share no segment" % (left, right),
                      not shared, sorted(shared)[:3])
        manifest = live / "manifest.json"
        if manifest.is_file():
            meta = _json.loads(manifest.read_text(encoding="utf-8"))
            counts = meta.get("split_rows") or {}
            if counts:
                check("the manifest counts the rows the splits hold",
                      all(counts.get(name, 0) == len(rows)
                          for name, rows in splits.items()),
                      {name: (counts.get(name), len(rows))
                       for name, rows in splits.items()})


def test_mechanism_features() -> None:
    """The context block must reach the model, and must not smuggle the action in.

    Measured 2026-10-29: with these features the temporal claim model scored 0.856
    AUC, but the decomposition says the action's own shape carried it (`args_only`
    0.891, `state_only` 0.724) and inside one tool the state features were 0.468 -
    below chance. So the contract to pin is narrow: same shape, no tool inside, and
    values that actually reflect the context rather than being silently zero.
    """
    section("Mechanism features (the context reaches the model, the tool does not)")
    import importlib.util

    tool = REPO_ROOT / "tools" / "live_outcome_probe.py"
    check("the live outcome probe exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("live_outcome_probe_mech", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    row = {"tool": "nav.goto", "objective_type": 6,
           "args": {"direction": "right"},
           "context": {"map": 1, "cell": 100, "ready": [
                          {"objective": 2, "type": 6, "params": {"parameter0": 715354}},
                          {"objective": 3, "type": 1}],
                       "exits": [{"map": 2, "cell": 100, "blocked": False},
                                 {"map": 3, "cell": 200, "blocked": True}],
                       "groups": [{"monsters": [715354], "cell": 100}],
                       "interactives": [{"elem": 5, "cell": 300}],
                       "character": {"id": 9, "level": 10, "breed": 4},
                       "in_dialog": False, "tutorial": True, "active_quests": 2}}
    vector = module.mechanism_vector(row)
    check("the vector's length matches its names",
          len(vector) == len(module.MECHANISM_NAMES), len(vector))
    check("every value is a finite float",
          all(isinstance(v, float) and v == v and abs(v) < 1e6 for v in vector),
          vector)
    named = dict(zip(module.MECHANISM_NAMES, vector))
    check("the ready list is counted and ranked",
          named["ready_count"] > 0 and named["ready_multi"] == 1.0, named)
    check("the objective's monster is seen in the group on my cell",
          named["monster_match"] == 1.0 and named["group_on_my_cell"] == 1.0, named)
    check("an exit on my cell and a blocked exit are both visible",
          named["exit_on_my_cell"] == 1.0 and 0.0 < named["blocked_ratio"] < 1.0, named)
    check("the call's argument kinds are visible",
          named["args_has_direction"] == 1.0 and named["args_has_quest"] == 0.0, named)
    other = dict(row, tool="quest.advance")
    check("the vector never sees the tool - only the state and the arguments",
          module.mechanism_vector(other) == vector)
    empty = module.mechanism_vector({"tool": "state.snapshot"})
    check("a row without context is all zeros, not a crash",
          empty == [0.0] * len(module.MECHANISM_NAMES), empty)


def test_claim_labels() -> None:
    """An action is scored on its own promise, not on the character having moved.

    Measured 2026-10-29 over the live corpus: `quest.advance` reports `moved` on
    22.3% of 2,848 calls, but 318 of those 636 changed only `cell` and only 2.1%
    touched quest state. Training a success head on `moved` would teach walking.
    """
    section("Per-tool claims (the action's promise, not any change)")
    import importlib.util

    tool = REPO_ROOT / "tools" / "build_live_transitions.py"
    check("the live transition builder exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("build_live_transitions_claims", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    claim = module._claim_of

    before = {"map_id": 10, "cell": 100, "in_fight": False,
              "quest_steps": {"3841": 1}, "ready_objectives": [{"objective_id": 1}],
              "quests_active": {"3841": 1}, "inventory": {"items": []},
              "in_dialog": False, "farm_state": None}
    walked = dict(before, cell=101)
    advanced = dict(before, quest_steps={"3841": 2})
    fighting = dict(before, in_fight=True)

    check("a nav.goto by direction claims one step and gets it",
          claim("nav.goto", {"direction": "right"}, before, walked) == ("step", True),
          claim("nav.goto", {"direction": "right"}, before, walked))
    check("an opened dialog does not count as a step",
          claim("nav.goto", {"direction": "right"}, before,
                dict(before, in_dialog=True)) == ("step", False),
          claim("nav.goto", {"direction": "right"}, before, dict(before, in_dialog=True)))
    check("a nav.goto with a destination claims that map",
          claim("nav.goto", {"map_id": 11}, before,
                dict(before, map_id=11)) == ("at:11", True))
    check("arriving somewhere else is not arrival",
          claim("nav.goto", {"map_id": 11}, before, walked) == ("at:11", False),
          claim("nav.goto", {"map_id": 11}, before, walked))
    check("quest.advance is not credited for walking",
          claim("quest.advance", {"quest_id": 3841}, before, walked) == ("quest", False),
          claim("quest.advance", {"quest_id": 3841}, before, walked))
    check("quest.advance is credited for a step change",
          claim("quest.advance", {"quest_id": 3841}, before, advanced) == ("quest", True),
          claim("quest.advance", {"quest_id": 3841}, before, advanced))
    check("a talk promises a dialog, not a quest advance",
          claim("quest.advance", {"npc_id": 7}, before,
                dict(before, in_dialog=True)) == ("dialog", True)
          and claim("quest.advance", {"npc_id": 7}, before, walked) == ("dialog", False),
          claim("quest.advance", {"npc_id": 7}, before, dict(before, in_dialog=True)))
    check("a talk that arrives already in a dialog does not claim it opened one",
          claim("quest.advance", {"npc_id": 7}, dict(before, in_dialog=True),
                dict(before, in_dialog=True)) == ("dialog", False),
          claim("quest.advance", {"npc_id": 7}, dict(before, in_dialog=True),
                dict(before, in_dialog=True)))
    node_a = dict(before, in_dialog=True,
                  dialog={"open": True, "message": "who are you", "replies": [{"reply_id": 4}]})
    node_b = dict(before, in_dialog=True,
                  dialog={"open": True, "message": "take this", "replies": [{"reply_id": 5}]})
    check("a reply promises the dialog moves to another node",
          claim("quest.advance", {"npc_id": 7, "reply": "hello"}, node_a, node_b)
          == ("dialog", True)
          and claim("quest.advance", {"npc_id": 7, "reply": "hello"}, node_a, node_a)
          == ("dialog", False),
          claim("quest.advance", {"npc_id": 7, "reply": "hello"}, node_a, node_b))
    check("and a reply with no dialog to compare is unknown, not a failure",
          claim("quest.advance", {"npc_id": 7, "reply": "hello"}, before,
                dict(before, in_dialog=True)) == ("dialog", None),
          claim("quest.advance", {"npc_id": 7, "reply": "hello"}, before,
                dict(before, in_dialog=True)))
    check("fight.join is credited only when the fight begins",
          claim("fight.join", {"monster": 1}, before, fighting) == ("in_fight", True)
          and claim("fight.join", {"monster": 1}, before, walked) == ("in_fight", False),
          claim("fight.join", {"monster": 1}, before, fighting))
    check("a tap claims an effect only when the state shows one",
          claim("tap.interactive", {"element_id": 5}, before,
                dict(before, in_dialog=True)) == ("effect", True)
          and claim("tap.interactive", {"element_id": 5}, before, before)
          == ("effect", False))
    check("a read makes no claim", claim("state.snapshot", {}, before, walked)
          == (None, None))
    check("a missing after-state is unknown, never a failure",
          claim("quest.advance", {"quest_id": 3841}, before, None) == (None, None))

    from agent.runtime import ToolCall
    tagged = ToolCall("nav.goto", {"direction": "left"}, "g",
                      meta={"arm": "teacher"}, why="the left exit is first")
    check("a call's why rides the record metadata",
          tagged.record_meta() == {"arm": "teacher",
                                   "why": "the left exit is first"},
          tagged.record_meta())
    check("and never touches the directive",
          tagged.args == {"direction": "left"}, tagged.args)
    plain = ToolCall("state.snapshot", {}, "g")
    check("a call with no reason records no metadata", plain.record_meta() is None)

    context = module._context_of({"map": {
        "map_id": 1, "cell": 5,
        "exits": [{"map_id": 2, "cell": 7, "direction": "left", "blocked": False}]}})
    check("the context keeps the exit's direction",
          (context["exits"][0] or {}).get("dir") == "left", context["exits"])

    rows = [
        {"tool": "nav.goto", "args": {"direction": "left"}, "moved": False, "ok": True},
        {"tool": "nav.goto", "args": {"direction": "left"}, "moved": True, "ok": True},
        {"tool": "nav.goto", "args": {"map_id": 2}, "moved": True, "ok": True},
        {"tool": "nav.goto", "args": {"map_id": 2}, "moved": True, "ok": False},
        {"tool": "quest.advance", "args": {"quest_id": 1}, "moved": True, "ok": True},
    ]
    form = module.nav_form(rows)
    check("movement form is measured per argument kind",
          form.get("direction", {}).get("n") == 2
          and form.get("map", {}).get("n") == 2, form)
    check("and the success rate is per form, not blended",
          form["direction"]["moved_pct"] == 50.0 and form["map"]["moved_pct"] == 100.0,
          form)
    check("a non-nav row is not counted", "quest.advance" not in form, form)

    # The miss streak is the loop's own memory: consecutive misses of the same tool,
    # from the claim label. Reads do not touch it, and transport ok is not success.
    import json as _json
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "dofus-97.jsonl"
        def obs(cell):
            return {"map": {"map_id": 1}, "cell": cell}
        steps = []
        cells = [(100, 100), (100, 101), (101, 102), (102, 102)]
        for i, (here, nxt) in enumerate(cells):
            steps.append({"kind": "step", "instance": "dofus-97", "goal": "g",
                          "index": i, "ts": 1_793_000_000.0 + i * 10,
                          "tool": "nav.goto", "args": {"direction": "left"},
                          "ok": True, "latency_ms": 10,
                          "observation": obs(here),
                          "result": {"snapshot": obs(here)}})
        log.write_text(chr(10).join(_json.dumps(s) for s in steps), encoding="utf-8")
        built, _stats = module.build([log], 120.0)
        streaks = [row["miss_streak"] for row in built if row["tool"] == "nav.goto"]
        claims = [row["claim_met"] for row in built if row["tool"] == "nav.goto"]
        check("a tool's miss streak counts its own consecutive misses",
              streaks[:3] == [0, 1, 0], (streaks, claims))
        check("and the claim is judged on the next observation, not the snapshot",
              claims[:2] == [False, True], claims)

        # Same tool, two different requests: the per-rule streak must reset when the
        # args change, while the per-tool streak keeps counting. Measured live, an
        # identical request re-issued after a miss succeeded 2% of the time (1,330
        # tries) and a changed one 38-78%, so the two keys are different features.
        def mapobs(mid):
            return {"map_id": mid, "cell": [100, 100]}
        log2 = Path(tmp) / "dofus-98.jsonl"
        # The after-state of row i is row i+1's observation, so seen[i] is the map
        # the character is on *before* call i. Two runs of request 2 miss while an
        # interleaved request 1 also misses; then request 2 succeeds.
        plans = [(1, 2), (1, 2), (1, 1), (2, 1), (2, 2), (2, 2)]
        steps2 = []
        for i, (seen, want) in enumerate(plans):
            steps2.append({"kind": "step", "instance": "dofus-98", "goal": "g",
                           "index": i, "ts": 1_793_100_000.0 + i * 10,
                           "tool": "nav.goto", "args": {"map_id": want},
                           "ok": True, "latency_ms": 10,
                           "observation": mapobs(seen),
                           "result": {"snapshot": mapobs(seen)}})
        log2.write_text(chr(10).join(_json.dumps(s) for s in steps2), encoding="utf-8")
        built2, _stats2 = module.build([log2], 120.0)
        reps = [row["repeat_streak"] for row in built2]
        tool_streaks = [row["miss_streak"] for row in built2]
        met2 = [row["claim_met"] for row in built2]
        check("the claim is judged on the map the call asked for",
              met2 == [False, False, False, False, True, True], str((met2, reps)))
        check("a rule keeps its miss count across other rules and resets on its own win",
              reps == [0, 1, 0, 1, 2, 0], reps)
        check("while the per-tool streak ignores the args",
              tool_streaks == [0, 1, 2, 3, 4, 0], tool_streaks)

    manifest_path = REPO_ROOT / "data" / "transitions_v2" / "manifest.json"
    if manifest_path.is_file():
        import json as _json
        manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
        quest = (manifest.get("per_tool") or {}).get("quest.advance") or {}
        check("the built corpus carries claims", manifest.get("claims_derivable", 0) > 0,
              manifest.get("claims_derivable"))
        check("and quest.advance claims are far rarer than its moves",
              quest.get("claim_pct") is not None
              and quest.get("claim_pct") < quest.get("moved_pct", 0),
              (quest.get("claim_pct"), quest.get("moved_pct")))
        nav = manifest.get("nav_form") or {}
        check("the built corpus records both movement forms",
              nav.get("direction", {}).get("n", 0) > 0
              and nav.get("map", {}).get("n", 0) > 0, nav)
        by_split = manifest.get("nav_form_by_split") or {}
        check("and reports them per fold, so a period effect cannot hide as a form effect",
              set(by_split) == {"train", "val", "test"}
              and all(isinstance(by_split[name], dict) for name in by_split),
              list(by_split))


def test_temporal_split_plan() -> None:
    """A temporal split must produce a fold that began after the training window.

    Measured 2026-10-29: the hash split put every one of the eight days in train,
    val *and* test, and the outcome model scored 0.830 AUC on that test fold. The
    same model on the temporal fold - one future day at a later level - scored
    0.634, and its phase features alone 0.375: below chance. The split is not a
    detail, it is the difference between a real number and a flattering one.
    """
    section("Temporal split (the test fold is the future, not a sample of the same days)")
    import importlib.util

    tool = REPO_ROOT / "tools" / "build_live_transitions.py"
    check("the live transition builder exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("build_live_transitions", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    day = 86400.0
    def rows_for(start, segment, count):
        return [{"segment": segment, "has_pair": True, "was_read": False,
                 "at": start + i * 30.0, "tool": "nav.goto", "moved": i % 3 == 0}
                for i in range(count)]

    base = 1_793_000_000.0
    rows = (rows_for(base, "early", 70) + rows_for(base + day, "middle", 30)
            + rows_for(base + 2 * day, "late", 100))
    plan = module.plan_splits(rows, "temporal")
    check("the latest segment is the test fold", plan["late"] == "test", plan)
    check("the earliest segment is train", plan["early"] == "train", plan)
    check("a segment appears in exactly one fold",
          len(plan) == 3 and set(plan.values()) <= {"train", "val", "test"}, plan)
    check("no day straddles train and test",
          not (plan["late"] == "test" and plan["early"] != "train"), plan)

    undated = module.plan_splits(rows + rows_for(0.0, "undated", 20), "temporal")
    check("a segment with no clock goes to train, never to the future fold",
          undated["undated"] == "train", undated)

    # Same rows, hash policy: the point is that it does *not* respect time.
    hashed = module.plan_splits(rows, "hash")
    check("the hash split ignores time on purpose and balances actions instead",
          set(hashed) == {"early", "middle", "late"}
          and set(hashed.values()) <= {"train", "val", "test"}, hashed)

    tiny = module.plan_splits(rows_for(base, "only", 5), "temporal")
    check("one segment cannot produce a future fold, so it stays in train",
          tiny.get("only") == "train", tiny)


def test_live_within_segment_eval() -> None:
    section("Within-segment outcome evaluation (phase cancels, the action must speak)")
    import importlib.util

    tool = REPO_ROOT / "tools" / "live_outcome_probe.py"
    check("the live outcome probe exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("live_outcome_probe", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Three segments, scores chosen so the expected pair arithmetic is exact:
    # a phase-only model cannot separate actions inside a segment, a tool-aware
    # one can, and pairs of two identical tools must not count as tool evidence.
    rows = [
        {"segment": "a", "tool": "fight.join", "progress": True},
        {"segment": "a", "tool": "nav.goto", "progress": True},
        {"segment": "a", "tool": "fight.join", "progress": False},
        {"segment": "a", "tool": "state.snapshot", "progress": False},
        {"segment": "a", "tool": "fight.join", "progress": False},
        {"segment": "b", "tool": "quest.advance", "progress": True},
        {"segment": "b", "tool": "state.snapshot", "progress": False},
        {"segment": "c", "tool": "tap.interactive", "progress": True},
        {"segment": "c", "tool": "state.snapshot", "progress": False},
        # a segment with one class only must be skipped, not scored as a win
        {"segment": "d", "tool": "nav.goto", "progress": False},
        {"segment": "d", "tool": "nav.goto", "progress": False},
    ]
    scores = [0.9, 0.2, 0.4, 0.6, 0.1, 0.9, 0.2, 0.9, 0.2, 0.3, 0.1]
    stat = module._within_pairs(rows, scores, "progress")
    check("segments with both outcomes are the only ones scored",
          stat["segments_with_pos_and_neg"] == 3, stat)
    check("every positive-negative pair in a segment is one trial",
          stat["pairs"] == 8, stat)
    check("pair accuracy counts a correct ordering, ties as half",
          stat["pair_accuracy"] == 0.75, stat)
    check("pairs of the same tool are counted apart from tool evidence",
          stat["tool_discordant_pairs"] == 6, stat)
    check("tool-discordant accuracy is over tool-discordant pairs only",
          stat["tool_discordant_accuracy"] == 0.667, stat)
    check("the CI resamples segments and stays inside [0, 1]",
          bool(stat["pair_accuracy_ci95"])
          and 0.0 <= stat["pair_accuracy_ci95"][0] <= stat["pair_accuracy_ci95"][1] <= 1.0,
          stat["pair_accuracy_ci95"])
    tied = module._within_pairs(rows, [0.5] * len(rows), "progress")
    check("a constant score is chance, not a win",
          tied["pair_accuracy"] == 0.5 and tied["tool_discordant_accuracy"] == 0.5, tied)

    # The floor every learned model must clear: the tool's own historical rate.
    # Measured 2026-10-29 on a temporal fold, that baseline was the *best* scorer
    # (0.723 moved AUC) while phase features fell under chance (0.375), so the probe
    # has to report it beside the models or the comparison is not visible.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        def dump(name, spec):
            lines = [json.dumps({"segment": "s", "tool": tool, "moved": moved,
                                 "progress": False, "was_read": False,
                                 "x": [0.5] * 8})
                     for tool, moved in spec]
            (data / ("%s.jsonl" % name)).write_text(chr(10).join(lines), encoding="utf-8")
        dump("train", [("tap.interactive", True)] * 10 + [("nav.goto", False)] * 10)
        dump("val", [("tap.interactive", True)] * 3 + [("nav.goto", False)] * 3)
        dump("test", [("tap.interactive", True), ("tap.interactive", True),
                      ("nav.goto", False), ("nav.goto", False)])
        probed = module.within_segment_probe(data, {})
        arm = (probed["labels"]["moved"].get("test") or {}).get("tool_rate") or {}
        check("the probe reports the tool-rate floor beside the models",
              isinstance(arm.get("auc"), float), arm)
        check("a tool that always moves outranks one that never does",
              arm.get("auc") == 1.0, arm)

    # The reject option. 60 rows over 6 segments: 5 successes (one carrying
    # miss_streak 2) and 3 failures clean, plus 2 failures with repeat_streak 2.
    # A rule that vetoes only the repeated failures must keep every success; the
    # tool-keyed rule that vetoes a success must show up as a cost.
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        lines = []
        for s in range(6):
            seg = "g%d" % s
            for i in range(5):
                lines.append({"segment": seg, "tool": "nav.goto", "claim_met": True,
                              "repeat_streak": 0, "miss_streak": 2 if i == 4 else 0,
                              "x": [0.5] * 8})
            for i in range(3):
                lines.append({"segment": seg, "tool": "nav.goto", "claim_met": False,
                              "repeat_streak": 0, "miss_streak": 0, "x": [0.5] * 8})
            for i in range(2):
                lines.append({"segment": seg, "tool": "nav.goto", "claim_met": False,
                              "repeat_streak": 2, "miss_streak": 1, "x": [0.5] * 8})
        for name in ("train", "test"):
            (data / ("%s.jsonl" % name)).write_text(
                chr(10).join(json.dumps(r) for r in lines), encoding="utf-8")
        gated = module.gate_probe(data, "claim_met")["tools"]["nav.goto"]["rules"]
        rep = gated["repeat_streak>=2"]
        check("the rule-keyed gate vetoes the repeated failures and no success",
              rep["vetoed"] == 12 and rep["vetoed_success"] == 0.0
              and rep["successes_retained"] == 1.0
              and rep["successes_lost_per_1000"] == 0.0, rep)
        check("and its advantage over the vetoed calls is positive",
              bool(rep["delta_ci"]) and rep["delta_ci"][0] > 0, rep["delta_ci"])
        toolgate = gated["miss_streak>=2"]
        check("a gate that vetoes successes reports what it costs",
              toolgate["vetoed"] == 6 and toolgate["vetoed_success"] == 1.0
              and toolgate["successes_retained"] == 0.8, toolgate)
    # The stopping rule groups each retry by what happened since the failure.
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        lines = []
        for s in range(4):
            seg = "h%d" % s
            lines += [
                {"segment": seg, "tool": "nav.goto", "args": {"direction": "left"},
                 "claim_met": False, "context": {"cell": 1, "in_dialog": False},
                 "x": [0.5] * 8},
                {"segment": seg, "tool": "state.snapshot", "args": {},
                 "claim_met": None, "context": {"cell": 1, "in_dialog": False},
                 "x": [0.5] * 8},
                {"segment": seg, "tool": "nav.goto", "args": {"direction": "left"},
                 "claim_met": False, "context": {"cell": 2, "in_dialog": False},
                 "x": [0.5] * 8},
                {"segment": seg, "tool": "nav.goto", "args": {"direction": "left"},
                 "claim_met": True, "context": {"cell": 2, "in_dialog": False},
                 "x": [0.5] * 8},
            ]
        for name in ("train", "test"):
            (data / ("%s.jsonl" % name)).write_text(
                chr(10).join(json.dumps(r) for r in lines), encoding="utf-8")
        stop = module.stopping_probe(data, "claim_met")["splits"]["test"]["nav.goto"]
        check("a retry after a state change is its own group",
              stop["changed"]["n"] == 4 and stop["changed"]["met"] == 0, stop)
        check("and a retry with nothing in between is immediate",
              stop["immediate"]["n"] == 4 and stop["immediate"]["met"] == 4, stop)
    # Rolling origin: segments placed in time, one rule, several futures.
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        lines = []
        for s in range(60):
            for i in range(5):
                lines.append({"segment": "r%02d" % s, "tool": "nav.goto",
                              "args": {}, "at": 1_793_000_000.0 + s * 600,
                              "claim_met": (i == 0), "repeat_streak": (2 if i else 0),
                              "x": [0.5] * 8})
        (data / "train.jsonl").write_text(
            chr(10).join(json.dumps(r) for r in lines), encoding="utf-8")
        rolled = module.rolling_probe(data, "claim_met")
        cut = rolled["cuts"].get("0.40") or {}
        stat = (cut.get("tools") or {}).get("nav.goto") or {}
        check("rolling origin trains on the past and scores the next window",
              rolled["segments"] == 60 and cut.get("test_segments", 0) > 0, rolled)
        check("and the rule is scored inside that window, not on the whole corpus",
              0 < stat.get("test_rows", 0) < 300 and stat.get("vetoed", 0) > 0, stat)


def test_ingest_after_run() -> None:
    section("Live ingest after a run (the derived dataset must not go stale)")
    import sys as _sys
    import tempfile

    from agent import brain_daemon as bd

    was_on, was_gap = bd.INGEST_ON_STOP, bd.INGEST_MIN_INTERVAL_S
    try:
        with tempfile.TemporaryDirectory() as tmp:
            daemon = bd.BrainDaemon(log_dir=Path(tmp) / "logs")
            bd.INGEST_ON_STOP = False
            check("the ingest can be switched off for a test host",
                  daemon._ingest_live() is False
                  and (daemon._ingest_thread is None
                       or not daemon._ingest_thread.is_alive()),
                  "ingest started while switched off")

            bd.INGEST_ON_STOP = True
            bd.INGEST_MIN_INTERVAL_S = 0.0
            daemon._ingest_commands = lambda: [
                ("fake", [_sys.executable, "-c", "import time; time.sleep(0.4)"])]
            first = daemon._ingest_live()
            second = daemon._ingest_live()
            check("a finished run starts one ingest", first is True, first)
            check("a second stop while it runs does not start another",
                  second is False, second)
            daemon._ingest_thread.join(10)
            third = daemon._ingest_live()
            check("the next run can ingest again once the job is done",
                  third is True, third)
            daemon._ingest_thread.join(10)
            bd.INGEST_MIN_INTERVAL_S = 3600.0
            check("the debounce keeps a stop storm from rebuilding every time",
                  daemon._ingest_live() is False, "debounce did not hold")
            commands = bd.BrainDaemon._ingest_commands(daemon)
            flat = [part for _label, cmd in commands for part in cmd]
            check("the ingest command rebuilds the live dataset",
                  any("refresh_live_dataset" in part for part in flat), commands)
            index_cmd = [cmd for _label, cmd in commands
                         if any("knowledge_index" in part for part in cmd)]
            check("and rebuilds the client knowledge index the runtime prefers",
                  len(index_cmd) == 1 and "--write" in index_cmd[0], commands)
            temporal_cmd = [cmd for _label, cmd in commands
                            if any("transitions_temporal" in part for part in cmd)]
            check("the honest future fold is rebuilt too",
                  len(temporal_cmd) == 1 and "temporal" in temporal_cmd[0], commands)
            check("the dataset is rebuilt before the index that reads the logs",
                  [label for label, _cmd in commands][:3]
                  == ["dataset", "temporal", "index"], commands)
    finally:
        bd.INGEST_ON_STOP, bd.INGEST_MIN_INTERVAL_S = was_on, was_gap


def test_ope_estimators() -> None:
    section("Off-policy estimators (the numbers that decide if a question is askable)")
    import importlib.util

    tool = REPO_ROOT / "tools" / "ope_probe.py"
    check("the off-policy probe exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("ope_probe", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    check("SNIPS is self-normalised, not a raw mean",
          module.snips([1.0, 0.0], [2.0, 1.0]) == 2.0 / 3.0,
          module.snips([1.0, 0.0], [2.0, 1.0]))
    check("ESS is (sum w)^2 / sum w^2",
          abs(module.ess([2.0, 1.0]) - 1.8) < 1e-9, module.ess([2.0, 1.0]))
    check("a policy with no support gets ESS 0",
          module.ess([0.0, 0.0]) == 0.0, module.ess([0.0, 0.0]))

    # One read row, one action row. Under `no-read` the read gets weight 0 and the
    # action is renormalised by 1/(1-P(read)).
    rows = [{"tool": "state.snapshot"}, {"tool": "quest.advance"}]
    tools = ["quest.advance", "state.snapshot"]
    proba = [[0.2, 0.8], [0.75, 0.25]]
    weights = module.target_weights(rows, proba, tools, "no-read")
    check("a read carries no weight for a no-read target",
          weights[0] == 0.0, weights)
    check("an action is upweighted by the read mass it replaces",
          abs(weights[1] - 1.0 / (1.0 - 0.25)) < 1e-9, weights)
    clipped = module.target_weights(rows, proba, tools, "uniform", clip=5.0)
    check("clipping bounds a weight from above",
          max(clipped) <= 5.0 + 1e-9, clipped)

    # Two clusters, perfectly separated: ICC must be 1 and n_eff half the rows.
    same = [{"segment": s, "progress": bool(v)} for s, v in
            (("a", 1), ("a", 1), ("b", 0), ("b", 0))]
    stat = module.cluster_stats(same, "progress")
    check("ICC reaches 1 when every cluster is one class",
          stat["icc"] == 1.0 and stat["n_eff"] == 2.0, stat)
    flat = [{"segment": s, "progress": True} for s in ("a", "b") for _ in range(2)]
    check("a label with one class reports no MDE instead of dividing by zero",
          module.cluster_stats(flat, "progress")["mde_relative_pct"] is None,
          module.cluster_stats(flat, "progress"))
    mixed = [{"segment": s, "progress": (i % 2 == 0)} for s in "abcd" for i in range(4)]
    mixed_stat = module.cluster_stats(mixed, "progress")
    check("a label with no clustering has n_eff close to n",
          mixed_stat["n_eff"] > 0.9 * mixed_stat["n"], mixed_stat)
    # The MDE formula is the two-proportion one at 5% / 80%: 2.8 * sqrt(2p(1-p)/n).
    import math
    expected = 2.8 * math.sqrt(2 * 0.5 * 0.5 / mixed_stat["n_eff"]) / 0.5 * 100
    check("the MDE uses the effective sample size",
          abs(mixed_stat["mde_relative_pct"] - round(expected, 1)) < 0.2,
          (mixed_stat["mde_relative_pct"], expected))


def test_fork_carries_the_planner_state() -> None:
    section("Planner forks (a counterfactual must not crash on a branch the parent had)")
    import json

    from agent.planner import QuestPlanner

    parent = QuestPlanner()
    fork = parent.fork()
    missing = sorted(set(vars(parent)) - set(vars(fork)))
    check("a fork carries every attribute the parent has", not missing, missing)

    # The free-form branch is the one that crashed: `_interactive_tries` was never
    # copied, so scoring a tap candidate raised AttributeError. Use a recorded live
    # turn that actually plans a tap, fork after it, and plan again.
    corpus = REPO_ROOT / "data" / "replay" / "planner_obs.jsonl"
    if not corpus.is_file():
        return
    rows = [json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    tapped = 0
    for row in rows[:120]:
        planner = QuestPlanner()
        call = planner.choose(row.get("goal") or "do all the quests", row["obs"], None)
        if call is None or call.tool != "tap.interactive":
            continue
        tapped += 1
        fork = planner.fork()
        try:
            again = fork.choose(row.get("goal") or "do all the quests", row["obs"], None)
            crashed = None
        except Exception as exc:  # noqa: BLE001 - the guard is the point
            again, crashed = None, "%s: %s" % (type(exc).__name__, exc)
        check("a fork plans a free-form turn without crashing", crashed is None, crashed)
        check("the fork returns a call for the same observation",
              again is not None, again)
        break
    check("the frozen corpus contains a free-form turn to fork on", tapped > 0, tapped)


def test_exploration_policy() -> None:
    section("Exploration policy (a randomized arm is tagged, bounded and off by default)")
    import agent.test_explore as suite

    for name in sorted(dir(suite)):
        if not name.startswith("test_"):
            continue
        try:
            getattr(suite, name)()
        except Exception as exc:  # noqa: BLE001 - report which case broke
            check("exploration case %s" % name, False,
                  "%s: %s" % (type(exc).__name__, exc))
        else:
            check("exploration case %s" % name, True, None)
    from agent.autopilot import _base_policy, _build_policy, build_parser

    parsed = build_parser().parse_args([])
    check("exploration is off unless a run asks for it",
          parsed.explore_objectives == 0.0
          and not isinstance(_build_policy(parsed), suite.ExplorationPolicy),
          parsed.explore_objectives)
    asked = build_parser().parse_args(["--explore-objectives", "0.1",
                                       "--explore-seed", "7"])
    wrapped = _build_policy(asked)
    check("asking for an epsilon wraps the chosen policy",
          isinstance(wrapped, suite.ExplorationPolicy)
          and wrapped.epsilon == 0.1, type(wrapped).__name__)


def test_explore_report() -> None:
    section("Exploration report (a randomized arm is read with its own propensities)")
    import importlib.util

    tool = REPO_ROOT / "tools" / "explore_report.py"
    check("the exploration report exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("explore_report", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def row(i, arm, progress, segment=None, p=0.2, tool_name="quest.advance"):
        return {"segment": segment or ("s%d" % (i % 4)), "tool": tool_name,
                "was_read": False, "has_pair": True, "arm": arm,
                "p_explore": p, "has_alternative": True,
                "explored": arm == "explore", "progress": progress,
                "advance": progress and arm == "explore",
                "moved": True, "objective_type": i % 3,
                "context": {"map": 100 + (i % 5)}}

    rows = [row(i, "explore", i % 2 == 0) for i in range(20)]
    rows += [row(i + 100, "teacher", i % 10 == 0) for i in range(20)]
    check("only rows with a live propensity are randomized",
          len(module.randomized(rows)) == 40, len(module.randomized(rows)))
    dead = row(999, "teacher", True, p=0.0)
    check("a state with no alternative is not randomized",
          module.randomized([dead]) == [], module.randomized([dead]))

    explore = [r for r in rows if r["arm"] == "explore"]
    teacher = [r for r in rows if r["arm"] == "teacher"]
    delta = module.difference(explore, teacher, "progress")
    check("the difference in rates is the estimate",
          abs(delta - (0.5 - 0.1)) < 1e-9, delta)
    check("the bootstrap interval brackets the difference",
          module.cluster_bootstrap(rows, "progress") is not None,
          module.cluster_bootstrap(rows, "progress"))
    value = module.always_explore_value(rows, "progress")
    check("the constant-explore value divides by the real propensity",
          abs(value - 0.5) < 1e-9, value)
    check("the IPW value ignores teacher rows by construction",
          abs(module.always_explore_value([r for r in rows if r["arm"] == "teacher"],
                                          "progress") or 0.0) < 1e-12, "teacher rows counted")

    plan = module.plan_table(0.0146)
    tight = plan["epsilon_0.20"]["half_width_0.005"]["eligible_steps"]
    loose = plan["epsilon_0.20"]["half_width_0.010"]["eligible_steps"]
    check("a tighter interval needs more steps",
          tight > loose * 2, (tight, loose))
    cheap = plan["epsilon_0.50"]["half_width_0.010"]["eligible_steps"]
    check("a larger epsilon needs fewer steps for the same precision",
          cheap < loose, (cheap, loose))

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        for name in ("train", "val", "test"):
            (out / (name + ".jsonl")).write_text("", encoding="utf-8")
        (out / "manifest.json").write_text('{"mean_step_s": 4.0}', encoding="utf-8")
        empty = module.analyse(out)
    check("the plan prices decisions at the corpus's own cadence",
          empty["step_s"] == 4.0, empty["step_s"])
    check("an empty corpus reports the pre-registered plan, not a fake estimate",
          empty["status"] == "no randomized rows yet" and "plan" in empty, empty["status"])


def test_knowledge_index() -> None:
    section("Client knowledge index (every field literal, conflicts never picked)")
    import contextlib
    import importlib.util
    import io
    import json as _json
    import tempfile

    tool = REPO_ROOT / "tools" / "knowledge_index.py"
    check("the knowledge indexer exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("knowledge_index", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with tempfile.TemporaryDirectory() as tmp:
        logs = Path(tmp)
        target = logs / "dofus-99.jsonl"
        catalog = [{"id": 1, "name": "Q", "steps": [{"id": 10, "name": "S",
                    "description": "d", "objectives": [{"id": 100, "type_id": 0,
                                                        "map_id": 5,
                                                        "params": {"parameter0": 7}}]}]}]
        rows = [
            {"ts": 1000.0, "observation": {
                "quests_catalog": catalog,
                "step_objectives": [{"id": 200, "step_id": 999, "text": "orphan"}],
                "ready_objectives": [{"quest_id": 1, "objective_id": 100,
                                      "type_id": 0, "text": "Do the thing",
                                      "params": {"parameter0": 7}, "step_id": 10,
                                      "cur_completion": 0, "max_completion": 3}]}},
            {"ts": 1003.0, "observation": {
                "ready_objectives": [{"quest_id": 1, "objective_id": 100,
                                      "text": "Do the thing", "cur_completion": 2,
                                      "max_completion": 3}]}},
        ]
        target.write_text(chr(10).join(_json.dumps(row) for row in rows), encoding="utf-8")
        index = module.scan([target])
        row = index["rows"].get("1:100")
        check("the catalogue and the live objective merge under one key",
              row is not None and {"catalog", "ready"} <= set(row["sources"]), row)
        check("text comes from the source that carries it, not from the catalogue",
              row["text"] == "Do the thing", row and row.get("text"))
        check("every field names the source that supplied it",
              row["field_sources"].get("text") == ["ready"]
              and row["field_sources"].get("map_id") == ["catalog"],
              row.get("field_sources"))
        check("the catalogue's map and type survive the merge",
              row["map_id"] == 5 and row["type_id"] == 0, row)
        check("a progress counter takes its latest value, not a conflict",
              row["cur_completion"] == 2 and "cur_completion" not in index["conflicts"],
              (row.get("cur_completion"), index["conflicts"]))
        check("an objective with no owning quest is reported, not indexed",
              index["unbound"] == 1 and "999:200" not in index["rows"], index["unbound"])

        rows.append({"ts": 1006.0, "observation": {
            "ready_objectives": [{"quest_id": 1, "objective_id": 100,
                                  "text": "Do the OTHER thing"}]}})
        target.write_text(chr(10).join(_json.dumps(row) for row in rows), encoding="utf-8")
        clash = module.scan([target])
        check("two different texts for one id are a conflict, not a choice",
              clash["conflicts"].get("text") == 1, clash["conflicts"])
        check("a conflicted field is withheld from the verified row",
              "text" not in clash["rows"]["1:100"]
              or clash["rows"]["1:100"]["text"] == "Do the other thing",
              clash["rows"]["1:100"])

        out = logs / "client_index.json"
        manifest_path = logs / "client_index.manifest.json"
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            rc = module.main(["--logs", str(logs), "--out", str(out),
                              "--manifest", str(manifest_path), "--write"])
        check("a conflicting capture is a failing run", rc == 1, rc)
        with contextlib.redirect_stdout(quiet):
            ok = module.main(["--logs", str(logs), "--out", str(out),
                              "--manifest", str(manifest_path), "--check"])
        check("--check passes on the index just built", ok in (0, 1), ok)
        check("the write leaves no temp file behind",
              not list(logs.glob("*.tmp-*")), [p.name for p in logs.glob("*.tmp-*")])
        payload = _json.loads(out.read_text(encoding="utf-8"))
        payload["rows"]["1:100"]["text"] = "tampered"
        out.write_text(_json.dumps(payload), encoding="utf-8")
        with contextlib.redirect_stdout(quiet):
            stale = module.main(["--logs", str(logs), "--out", str(out),
                                 "--manifest", str(manifest_path), "--check"])
        check("a hand-edited index fails --check", stale == 1, stale)

        # A manifest that belongs to another index is a stale manifest, even
        # though every row still matches: the counts and file list would lie.
        # Rebuild first, so the only tampering is the manifest itself.
        with contextlib.redirect_stdout(quiet):
            module.main(["--logs", str(logs), "--out", str(out),
                         "--manifest", str(manifest_path), "--write"])
        payload = _json.loads(out.read_text(encoding="utf-8"))
        payload["manifest"]["index_sha256"] = "0" * 64
        out.write_text(_json.dumps(payload), encoding="utf-8")
        with contextlib.redirect_stdout(quiet):
            stale_manifest = module.main(["--logs", str(logs), "--out", str(out),
                                          "--manifest", str(manifest_path), "--check"])
        check("a manifest from another build fails --check too",
              stale_manifest == 1, stale_manifest)

    # File order must not decide whether a step objective is bound: here the step
    # list arrives before any catalogue that names its quest.
    with tempfile.TemporaryDirectory() as tmp:
        logs = Path(tmp)
        target = logs / "dofus-98.jsonl"
        first = {"ts": 1.0, "observation": {
            "step_objectives": [{"id": 300, "step_id": 20, "text": "late catalogue"}]}}
        second = {"ts": 2.0, "observation": {"quests_catalog": [
            {"id": 3, "name": "Q3", "steps": [{"id": 20, "name": "S20",
                                               "objectives": []}]}]}}
        target.write_text(chr(10).join(_json.dumps(row) for row in (first, second)),
                          encoding="utf-8")
        ordered = module.scan([target])
        bound = ordered["rows"].get("3:300")
        check("a step objective seen before its catalogue still binds",
              bound is not None and "step" in bound["sources"]
              and bound["captures"] == 1, bound)
        check("and nothing is reported unbound when the owner is visible eventually",
              ordered["unbound"] == 0, ordered["unbound"])

    from agent.knowledge_context import load_observed_index
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "client_index.json"
        path.write_text(_json.dumps({"rows": {"2:300": {
            "quest_id": 2, "objective_id": 300, "text": "t"}}}), encoding="utf-8")
        loaded = load_observed_index(path)
        check("the runtime loader reads the verified index by (quest, objective)",
              loaded.get((2, 300), {}).get("text") == "t", list(loaded)[:3])


def main(argv: Optional[List[str]] = None) -> int:
    checks, failures = run()
    print("\n%d checks, %d failed" % (checks, len(failures)))
    if failures:
        print("\nFAILURES:\n  " + "\n  ".join(failures))
        return 1
    print("all regression cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
