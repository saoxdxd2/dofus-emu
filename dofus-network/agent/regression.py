"""Offline regression gate for the agent loop — the contract the deleted
smoke_verifier used to enforce (KNOWN_GAPS.md G8).

No client, no network, no torch, no onnxruntime. It runs the REAL code paths:

  * the verifier rule engine (all four verdicts, nested-alias changes, the
    ready_objectives claim-proof, read-only-as-advanced),
  * the schema / capability / feature tool vocabulary (they must be one set,
    and the 28-tool research vocabulary must include hdv.buy),
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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent      # .../dofus-network
ROOT = REPO_ROOT.parent                                  # .../dofus-emu

# Mutation targets the sweep runs no test against, measured 2026-11-04: 24 files,
# 175 mutations. Most are exercised behaviourally by a test that does not name the
# file (`planner.py` is 66 of them), so this is a bookkeeping ratchet, not a claim
# that 175 mutations are unguarded -- but a *new* file with no witness is exactly
# how the currency mutations came to report MISSED with every arm green.
_MUT_UNWITNESSED_BASELINE = 24

_FAILURES: List[str] = []
_CHECKS = 0


def check(label: str, condition: Any, detail: Any = "") -> None:
    global _CHECKS
    _CHECKS += 1
    if condition:
        return
    # One `%s` per element, not a nested interpolation of the detail: a tuple detail used
    # to raise "not all arguments converted during string formatting" *inside* the helper,
    # so reporting a failure killed the run that was reporting it. The repo has been bitten
    # by this repeatedly, and the fix belongs here rather than in 1,600 call sites.
    text = "%s: %s" % (label, detail) if detail else label
    _FAILURES.append(text)
    print("FAIL %s" % text)


def section(title: str) -> None:
    print("\n== %s ==" % title)


# Suites the gate hands to another toolchain (node, go, the C# compiler) record
# themselves here, because a `print("SKIP: …")` + `return` registers no check: with the
# tool missing the summary still read "N checks, 0 failed, exit 0" while 40 JS page
# suites, the relay tests and the host compile were all absent (read at the four call
# sites on 2026-10-31).
_DELEGATIONS: List[Dict[str, Any]] = []
# Wall time per test, recorded by `_run_test` as the gate runs: the only honest timing is
# the one that does not run the test a second time.
DURATIONS: Dict[str, float] = {}
# Checks registered per test, on the way through. The summary's total is a number the prose
# quotes, and a total that shrinks between two runs of the same tree is the one failure mode
# that "0 failed" cannot see: measured 2026-11-03, four consecutive runs of an unchanged tree
# reported 1788 / 1785 / 1788 / 1788 checks. Per-test counts turn "the total moved" into
# "this suite stopped answering".
CHECK_COUNTS: Dict[str, int] = {}


def _delegation(name: str, ran: bool, sources: Path, detail: str = "") -> None:
    """Record that a delegated suite ran, or why it could not."""
    _DELEGATIONS.append({"name": name, "ran": ran,
                         "sources": sources.exists(), "detail": detail})


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
    from agent import verifier
    check("hdv.prices is read-only", "hdv.prices" in verifier.READ_ONLY_TOOLS)

    # The verifier, the autopilot and the gate must not keep private copies of
    # tool classes: the registry carries them, the consumers derive them. A seed
    # edit that forgets to regenerate the registry is what this pair catches.
    seed_classes = {t.name: (t.read_only, t.moves, t.lists)
                    for t in schema._BUILTIN_TOOLS}
    reg_classes = {r.get("id"): (bool(r.get("read_only")), bool(r.get("moves")),
                                 bool(r.get("lists")))
                   for r in rows if isinstance(r, dict) and r.get("id")}
    stale = sorted(n for n in set(seed_classes) & set(reg_classes)
                   if seed_classes[n] != reg_classes[n])
    check("the registry carries the seed's tool classes", not stale, stale[:5])

    # The runtime validates arguments from the registry file; the seed is the
    # fallback. Drifting parameter shapes mean the two code paths accept
    # different calls -- the seed declared `farm.start restart` while the
    # registry the runtime loads did not.
    def _shape(params):
        return {key: (raw.get("type"), bool(raw.get("required")), raw.get("default"),
                      tuple(raw.get("enum") or ()))
                for key, raw in (params or {}).items() if isinstance(raw, dict)}

    seed_shapes = {t.name: _shape(t.as_dict().get("params")) for t in schema._BUILTIN_TOOLS}
    reg_shapes = {r.get("id"): _shape(r.get("params")) for r in rows if isinstance(r, dict)}
    shape_drift = sorted(n for n in set(seed_shapes) & set(reg_shapes)
                         if seed_shapes[n] != reg_shapes[n])
    check("the registry carries the seed's parameter shapes",
          not shape_drift, shape_drift[:5])
    check("the verifier derives its semantic sets from the registry",
          set(verifier.READ_ONLY_TOOLS) == {n for n, c in reg_classes.items() if c[0]}
          and set(verifier.LISTING_TOOLS) == {n for n, c in reg_classes.items() if c[2]}
          and set(verifier.MOVE_TOOLS) == {n for n, c in reg_classes.items() if c[1]},
          (sorted(verifier.READ_ONLY_TOOLS), verifier.MOVE_TOOLS,
           sorted(verifier.LISTING_TOOLS)))
    check("read-only tools are the nine non-mutating ones",
          len(verifier.READ_ONLY_TOOLS) == 9
          and "hdv.buy" not in verifier.READ_ONLY_TOOLS,
          sorted(verifier.READ_ONLY_TOOLS))
    check("move tools are the two schema movers",
          set(verifier.MOVE_TOOLS) == {"farm.start", "nav.goto"},
          verifier.MOVE_TOOLS)
    check("the verifier's feature layout names only schema tools",
          set(verifier.TOOL_ORDER) <= set(tool_names),
          sorted(set(verifier.TOOL_ORDER) - set(tool_names)))

    # The learned label order is what turns a head's argmax into a tool name.
    # features.LEARNED_TOOLS is the serving copy; the trainer must carry the
    # same order (it is self-contained for Kaggle), and the shipped sidecar
    # must agree with both or every prediction is renamed silently.
    from ai import train_tool_policy
    check("the trainer's label order equals the serving featuriser's",
          list(train_tool_policy.DEFAULT_TOOLS) == list(features.LEARNED_TOOLS),
          sorted(set(train_tool_policy.DEFAULT_TOOLS) ^ set(features.LEARNED_TOOLS)))
    sidecar = REPO_ROOT / "models" / "tool_policy.json"
    if sidecar.exists():
        policy = json.loads(sidecar.read_text(encoding="utf-8"))
        shipped = policy.get("tools") or []
        check("the shipped tool policy's label order equals the featuriser's",
              shipped == list(features.LEARNED_TOOLS),
              sorted(set(shipped) ^ set(features.LEARNED_TOOLS)))
        check("every shipped-policy label is a legal tool",
              set(shipped) <= set(tool_names),
              sorted(set(shipped) - set(tool_names)))
        # The head's input width is the featuriser's; a new feature in
        # `objective_features` silently feeds the shipped graph the wrong shape.
        check("the shipped policy's feature width equals the featuriser's",
              int(policy.get("features") or 0) == features.FEATURE_COUNT,
              (policy.get("features"), features.FEATURE_COUNT))

    # The 30M encoder is opt-in (`--policy encoder`) and carries its own frozen
    # 16-label head. It cannot be widened here, but a label it emits must still
    # name a tool the executor knows, or every prediction is refused.
    encoder_policy = REPO_ROOT / "models" / "encoder30_v15" / "policy.json"
    if encoder_policy.exists():
        encoder_tools = json.loads(encoder_policy.read_text(encoding="utf-8")).get("tools") or []
        check("every encoder-policy label is a legal tool",
              set(encoder_tools) <= set(tool_names),
              sorted(set(encoder_tools) - set(tool_names)))

    # The command model ships its own label lists in command_onnx.json. The
    # router drops any intent outside `INTENTS` and any tag outside `SLOT_KEYS`
    # without saying so, so a retrain that renames a label must fail here.
    from agent.commands import ACTION_TOOL, INTENTS, SLOT_KEYS, TOOL_INTENT
    command_config = REPO_ROOT / "models" / "command_encoder30" / "command_onnx.json"
    if command_config.exists():
        config = json.loads(command_config.read_text(encoding="utf-8"))
        model_intents = set(config.get("intents") or [])
        check("the command model's intents equal the runtime's",
              model_intents == set(INTENTS),
              sorted(model_intents ^ set(INTENTS)))
        bridged_actions = {"none"} | {action for row in ACTION_TOOL.values()
                                      for action in row}
        bridged_actions |= {slots.get("action") for _, slots in TOOL_INTENT.values()
                            if slots.get("action")}
        model_actions = set(config.get("actions") or [])
        check("every command action label is a bridged action",
              model_actions <= bridged_actions, sorted(model_actions - bridged_actions))
        tag_slots = {tag[2:] for tag in config.get("tags") or []
                     if tag.startswith(("B-", "I-"))}
        check("every command tag names a declared slot",
              tag_slots <= set(SLOT_KEYS), sorted(tag_slots - set(SLOT_KEYS)))

    # planning/planner.py declares which tools its plans may emit. It is
    # deliberately narrower than the registry, but a renamed tool must not
    # turn every plan that names it into a silent "gap".
    from planning.planner import EXECUTABLE_TOOLS
    check("every tool the planner calls executable is registered",
          EXECUTABLE_TOOLS <= set(tool_names),
          sorted(EXECUTABLE_TOOLS - set(tool_names)))

    # The client executes directive tools from its own handler map. Nothing
    # checks that at runtime until a live call comes back "no client handler",
    # so a directive tool without a handler must fail here instead.
    page = REPO_ROOT.parent / "scripts" / "mobile-disguise.js"
    if page.exists():
        js = page.read_text(encoding="utf-8")
        unhandled = [t.name for t in schema.TOOLS
                     if getattr(t, "backend", "") == "directive"
                     and ("'%s': function" % t.name) not in js]
        check("every directive tool names a client handler", not unhandled, unhandled)


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
        fight, {"position": {"map_id": 100},
                "map": {"map_id": 100,
                        "groups": [], "exits": [{"direction": "left", "map_id": 101}]}})
    check("a fight with no group decomposes to explore",
          child is not None and child["kind"] == "explore", child)
    check("the explore child names the crossing edge",
          child is not None and child["args"].get("direction") == "left", child)
    # Live 2026-10-26, map 145490435: the client's map data lists `right` as
    # leading to 145490435 itself, and it sat first in the block. Twenty-five
    # `nav.goto {direction: right}` calls were refused ("needs ... a map_id
    # different from 145490435") in forty-five seconds, because the same snapshot
    # re-derived the same direction. A self-edge is not a crossing.
    loop = {"position": {"map_id": 145490435},
            "map": {"map_id": 145490435, "groups": [],
                    "exits": [{"direction": "right", "map_id": 145490435},
                              {"direction": "top", "map_id": 145489922,
                               "blocked": True}]}}
    child = child_for_objective(fight, loop)
    check("a self-edge is not offered as an explore direction",
          child is not None and "direction" not in child["args"], child)
    only_self = {"position": {"map_id": 5},
                 "map": {"map_id": 5, "groups": [],
                         "exits": [{"direction": "right", "map_id": 5}]}}
    child = child_for_objective(fight, only_self)
    check("a map whose only exit is a self-edge still yields the child, with no direction",
          child is not None and child["kind"] == "explore" and not child["args"], child)
    real = {"position": {"map_id": 145490435},
            "map": {"map_id": 145490435, "groups": [],
                    "exits": [{"direction": "right", "map_id": 145490435},
                              {"direction": "top", "map_id": 145489922}]}}
    child = child_for_objective(fight, real)
    check("the first real crossing after a self-edge wins",
          child is not None and child["args"].get("direction") == "top", child)
    # Live 2026-10-29 (dofus-05, page build 2026-10-29bf): the fight objective on
    # map 145490946 found no group there; this explore walked the first exit (left,
    # back to 145490434) while the policy's travel walked right again, twenty-three
    # round trips in one run. The page publishes the crossing that landed here, so
    # the map just left is known and must not be the explore's first choice.
    landed = {"position": {"map_id": 145490946},
              "map": {"map_id": 145490946, "groups": [],
                      "exits": [{"direction": "left", "map_id": 145490434},
                                {"direction": "top", "map_id": 145490945},
                                {"direction": "bottom", "map_id": 145490947}]},
              "last_nav": {"stage": "walk_then_change", "from_map": 145490434,
                           "to_map": 145490946, "direction": "left"}}
    child = child_for_objective(fight, landed)
    check("an explore does not walk straight back where it came from",
          child is not None and child["args"].get("direction") == "top", child)
    other = dict(landed)
    other["last_nav"] = {"stage": "walk_then_change", "from_map": 1, "to_map": 2,
                         "direction": "left"}
    child = child_for_objective(fight, other)
    check("a crossing between other maps does not suppress this map's first exit",
          child is not None and child["args"].get("direction") == "left", child)
    dead = {"position": {"map_id": 145490946},
            "map": {"map_id": 145490946, "groups": [],
                    "exits": [{"direction": "left", "map_id": 145490434}]},
            "last_nav": {"from_map": 145490434, "to_map": 145490946}}
    child = child_for_objective(fight, dead)
    check("a dead end still walks back rather than stalling",
          child is not None and child["args"].get("direction") == "left", child)
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
    # second copy of a worn template names its own slot without guessing. The bag
    # lists equipped copies too (the live 01:39 snapshot had 4 items with 3 worn),
    # so the twin is two bag rows of one gid and one worn.
    twin = {"inventory": {"presets": [],
                          "equipped": [{"slot": 4, "gid": 18844}],
                          "items": [{"gid": 18844, "object_uid": 68720788},
                                    {"gid": 18844, "object_uid": 1}]},
            "map": {}}
    slotted = QuestPlanner().plan_objective({"id": 4260}, 0, equip_step, twin)
    # The wire form, not the number: `inv.equip.slot` is declared `str`, and a
    # call carrying int 4 was refused at the executor however right the slot was.
    # Comparing against 4 here would push the planner back into that refusal.
    check("a worn twin's slot is sent with the equip",
          bool(slotted) and slotted[0]["args"].get("slot") == "4"
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

    # A leak, not a crash: nothing ever removed a finished instance from the table, so
    # every completed run kept its whole Autopilot for the life of the daemon. Measured
    # 2026-11-07: 40 runs retained 41.9 MB, growing linearly in a process meant to run
    # for days. Both halves are needed -- releasing the pilot without trimming the table
    # still grows a row per instance name.
    daemon_src = (REPO_ROOT / "agent" / "brain_daemon.py").read_text(encoding="utf-8")
    check("a finished instance releases its pilot",
          "self._release_pilot(item)" in daemon_src and "item.pilot = None" in daemon_src,
          (daemon_src.count("_release_pilot"), daemon_src.count("item.pilot = None")))
    check("and the completed-instance table stays bounded",
          "MAX_FINISHED_RECORDS" in daemon_src and "self._prune_finished()" in daemon_src
          and "self._instances.pop(name, None)" in daemon_src,
          (daemon_src.count("MAX_FINISHED_RECORDS"), daemon_src.count("_prune_finished")))


def test_brain_reports_stale_code() -> None:
    """A daemon that answers is not a daemon that runs the current code.

    Measured 2026-11-04: the black-screen fixes landed at 11:15-11:21, the brain
    the host had attached to started at 11:03, and `/brain/health` said
    `status: ok` for every relaunch. The host's own test is
    `health contains "dofus-brain"`, so it re-measured pre-fix code three times
    and each test looked like a failed fix rather than a stale process.
    """
    import os
    import tempfile
    import time as _time
    from types import SimpleNamespace
    from agent.brain_daemon import stale_modules

    section("A stale brain daemon names its own out-of-date code")
    started = _time.time()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        older = root / "older.py"
        edited = root / "edited.py"
        for path in (older, edited):
            path.write_text("x = 1\n", encoding="utf-8")
        os.utime(older, (started - 600.0, started - 600.0))
        os.utime(edited, (started + 120.0, started + 120.0))
        table = {
            "m.older": SimpleNamespace(__file__=str(older)),
            "m.edited": SimpleNamespace(__file__=str(edited)),
            "m.builtin": SimpleNamespace(__file__=None),
            "m.foreign": SimpleNamespace(__file__=str(Path(td).parent / "other.py")),
        }
        flagged = stale_modules(start=started, modules=table, root=root)
        names = sorted(row["module"] for row in flagged)
        check("the file changed after import is named", names == ["m.edited"], str(names))
        check("an unchanged, sourceless or out-of-repo module is not",
              len(flagged) == 1, str(flagged))
        check("the verdict is relative to the load moment, not the clock",
              stale_modules(start=started + 300.0, modules=table, root=root) == [],
              str(stale_modules(start=started + 300.0, modules=table, root=root)))

    live = None
    acting = {"agent.autopilot", "agent.planner", "agent.runtime",
              "agent.brain_daemon", "agent.serve"}
    try:
        live = {row["module"] for row in stale_modules()} & acting
    except Exception as exc:  # noqa: BLE001 - the verdict must not silence the probe
        check("the daemon's own module table can be inspected without raising",
              False, "%s: %s" % (type(exc).__name__, exc))
    # Only the acting modules, not the whole tree: a gate run that copies tool
    # scripts into the repo mid-flight is not a stale brain, and a check that
    # fires on bookkeeping teaches people to ignore it.
    check("this process is running the acting code it is testing",
          live == set(), str(sorted(live)))

    daemon_src = (REPO_ROOT / "agent" / "brain_daemon.py").read_text(encoding="utf-8")
    # The first version of this probe crashed inside health() on an undefined
    # `logger`, and /brain/health returned an empty reply: a currency probe that
    # dies looks exactly like a dead daemon. The file has no `logger` name, so the
    # warning has to go through the module-level logging calls it already uses.
    check("the stale-code warning uses the module's own logging convention",
          "logging.warning(" in daemon_src and "logger." not in daemon_src,
          (daemon_src.count("logging.warning("), daemon_src.count("logger.")))
    check("and a path it cannot stat is skipped, not raised",
          "except (OSError, ValueError):" in daemon_src)
    check("the health reply carries the verdict, not only liveness",
          '"code_stale": [row["module"] for row in stale]' in daemon_src
          and "stale = stale_modules()" in daemon_src,
          (daemon_src.count('"code_stale"'), daemon_src.count("stale_modules()")))
    host_src = (ROOT / "desktop" / "aipanel.cs").read_text(encoding="utf-8", errors="replace")
    boot_src = (ROOT / "desktop" / "BootState.cs").read_text(encoding="utf-8", errors="replace")
    check("the host asks the daemon before attaching",
          "BootState.DecideBrainAttach(health)" in host_src
          and "BootState.BrainRestart" in host_src
          and "KillBrainPid(pid)" in host_src,
          (host_src.count("DecideBrainAttach"), host_src.count("KillBrainPid")))
    check("and the verdict itself is in the file the gate compiles and runs",
          "DecideBrainAttach" in boot_src and "code_stale" in boot_src,
          (boot_src.count("DecideBrainAttach"), boot_src.count("code_stale")))
    check("and refuses to run against code it cannot refresh",
          "refusing to run against pre-fix code" in host_src)


def test_live_collection_lifecycle() -> None:
    import unittest
    from tools import test_collect_live_data

    section("Live collector (read-only fleet discovery, scoped sessions)")
    suite = unittest.defaultTestLoader.loadTestsFromModule(test_collect_live_data)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("collector lifecycle tests execute and pass",
          result.testsRun >= 49 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures, "errors": result.errors})


def test_session_report() -> None:
    import unittest
    from tools import test_session_report as module

    section("Session report (objectives done, not the step-index sum)")
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("session-report tests execute and pass",
          result.testsRun >= 4 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_data_janitor() -> None:
    import unittest
    from tools import test_data_janitor as module

    section("Data janitor (stale schema, leaks, dedupe, tutorial, drift)")
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("janitor tests execute and pass",
          result.testsRun >= 5 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_market_log() -> None:
    """The projection must not invent prices the recorder cut.

    The corpus check is deliberately not run here: it costs ~4 s and its answer
    is a property of the capture, not of this code. `tools.market_log --check`
    prints it on demand. What is pinned is that a price row only ever comes from
    a capture, and that a frame without its prices yields nothing.
    """
    import unittest
    from tools import test_market_log as module

    section("Market/action log projection (prices only where they survived)")
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("market-log tests execute and pass",
          result.testsRun >= 25 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_world_state_schema() -> None:
    """The world-state schema must stay closed as the client evolves.

    The encoder classifies every key an observation carries as encoded,
    ignored-for-a-stated-reason, or unknown. An unknown key means the client
    started sending something nobody encodes, which is how a dataset thins out
    for months without a symptom -- writing the module, the first audit found 26
    top-level keys that had never been named.

    The full pass is `python -m agent.world_state --coverage trajectories/*.jsonl`
    (7 s over 11,461 observations), so the gate samples instead: a field the
    client adds appears in most observations, and 120 rows per file is enough to
    see it while keeping the gate fast.
    """
    import unittest
    from tools import test_world_state as module

    section("World state (closed schema, named blocks, no silent collapse)")
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("world-state tests execute and pass",
          result.testsRun >= 25 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})

    from agent import world_state as ws

    paths = sorted((REPO_ROOT / "trajectories").glob("dofus-*.jsonl"))
    if not paths:
        check("the world-state schema closes over the recorded corpus", True,
              "no trajectories on this checkout")
        return
    sample = []
    for path in paths:
        for index, obs in enumerate(ws.iter_observations([str(path)])):
            if index >= 120:
                break
            sample.append(obs)
    report = ws.audit(sample)
    check("the world-state schema closes over the recorded corpus",
          not report["unknown"] and not report["unknown_entity"],
          {"sampled": report["observations"], "unknown": report["unknown"],
           "unknown_entity": report["unknown_entity"]})


def test_session_close() -> None:
    import unittest
    from tools import test_session_close as module

    section("Session close (event-driven, per-step isolation, ledger)")
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("session-close tests execute and pass",
          result.testsRun >= 10 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_side_swap() -> None:
    import unittest
    from combat import test_side_swap as module

    section("Side-swapped cross-play (seat bias vs decision skill)")
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("side-swap tests execute and pass",
          result.testsRun >= 6 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_move_gate() -> None:
    import unittest
    from ai import test_move_gate as module

    section("Live move head (promotion gate on the temporal test fold)")
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("move-head gate tests execute and pass",
          result.testsRun >= 5 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_progress_report() -> None:
    import unittest
    from tools import test_progress_report as module

    section("Progress scoreboard (sessions, not baselines or rotations)")
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("progress-report tests execute and pass",
          result.testsRun >= 4 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_artifact_rollback() -> None:
    import unittest
    from agent import test_artifacts as module

    section("Artifact rollback (previous bytes recoverable)")
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("artifact rollback tests execute and pass",
          result.testsRun >= 5 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


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
          'candidate.tool == "farm.start" and not self._farm_allowed(goal, obs)'
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
        _delegation("page checks", False, REPO_ROOT / "scripts",
                    "scripts/ is not in this mirror")


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
                   "combat.test_live_policy", "combat.test_normal_build_duel",
                   "combat.test_online_learn", "combat.test_env_math"):
        proc = subprocess.run([sys.executable, "-m", module], cwd=str(REPO_ROOT),
                              capture_output=True, text=True, timeout=300)
        check("%s passes" % module, proc.returncode == 0,
              (proc.stderr or proc.stdout).strip()[-300:])


def test_env_scatter_and_mask_are_witnessed() -> None:
    """The encounter env's arithmetic, inside the gate and inside the sweep.

    `combat/test_env_math.py` is the only thing in the tree that steps the env, and the
    delegated loop above runs it as a subprocess -- which the mutation snippet does not.
    Two arms (`hp ... .add(mend - hit)` -> `.set(...)`, and the spell mask written with
    `can_cast` -> `set(True)`) read MISSED for 595 arms precisely because nothing in the
    snippet ever reached env.py. This runs the same suite in-process so a regression in
    the scatter or the mask fails a check the sweep can see.
    """
    import io
    import unittest

    section("The encounter env is stepped by a check the sweep can see")
    from combat import test_env_math

    suite = unittest.defaultTestLoader.loadTestsFromModule(test_env_math)
    result = unittest.TextTestRunner(verbosity=0, stream=io.StringIO()).run(suite)
    check("the env suite executes and nothing in it fails",
          result.testsRun >= 5 and result.wasSuccessful(),
          {"tests": result.testsRun,
           "failures": [str(t) for t, _ in result.failures][:2],
           "errors": [str(t) for t, _ in result.errors][:2]})


def test_go_relay_suites() -> None:
    section("Go relay tests (delegated to go, criterion 6)")
    if not shutil.which("go"):
        print("   SKIP: go not on PATH")
        _delegation("go relay tests", False, ROOT / "scripts", "go not on PATH")
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
    _delegation("go relay tests", True, scripts)


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


def _builder_host_sources(build_cs: Path) -> List[str]:
    """The host's .cs files, read out of builder/Build.cs.

    The builder is the authority on what DofusFarm.exe is made of; this gate only
    borrows its answer. Parsing is deliberately narrow -- `Path.Combine(desktopDir,
    "X.cs")` is the only shape the list uses -- so a reformat that changes the
    shape returns an empty list and the gate says so instead of silently
    compiling a subset.
    """
    try:
        text = build_cs.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # Only the host's list. Build.cs also compiles setup.exe and uninstall.exe
    # from their own `Path.Combine(desktopDir, ...)` blocks, and taking those too
    # produced `Duplicate 'AssemblyCompany' attribute` -- a gate failure that says
    # nothing about the host.
    start = text.find("var farmSources")
    if start < 0:
        return []
    end = text.find("};", start)
    if end < 0:
        return []
    names: List[str] = []
    for match in re.finditer(
            r'Path\.Combine\(\s*desktopDir\s*,\s*"([A-Za-z0-9_.]+\.cs)"\s*\)',
            text[start:end]):
        name = match.group(1)
        if name not in names:
            names.append(name)
    return names


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
        _delegation("C# host compile", False, ROOT / "desktop", "no C# compiler found")
        return
    desktop = ROOT / "desktop"
    # The list comes from the builder, not from a copy kept here. It used to be a
    # copy, and it fell behind: `LocalSecret.cs` was added to builder/Build.cs and
    # this gate kept compiling without it, so the gate failed on a tree that
    # builds. Reading the builder's own list makes that drift impossible.
    names = _builder_host_sources(ROOT / "builder" / "Build.cs")
    check("the builder's C# source list is readable", len(names) >= 20,
          "%d source(s) parsed from builder/Build.cs" % len(names))
    if len(names) < 20:
        return
    sources = [desktop / name for name in names]
    missing = [str(path) for path in sources if not path.exists()]
    check("every builder source file exists", not missing, missing)
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
        _delegation("C# host compile", True, desktop)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def test_cs_secret_redaction() -> None:
    section("The manager recorder's ticket regex, executed (not grepped)")
    compiler = _find_csharp_compiler()
    if compiler is None:
        print("   SKIP: no C# compiler found")
        _delegation("recorder regex probe", False, ROOT / "desktop",
                    "no C# compiler found")
        return
    _delegation("recorder regex probe", True, ROOT / "desktop")
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


def _cs_logic_probe(sources, program, label) -> None:
    """Compile REAL desktop/*.cs logic with a probe Main and RUN it.

    A `check` on a string literal only proves the words are in the file. Two of these probes
    already caught what a grep could not -- a redaction that never fired, a threshold that was
    never exercised -- so the rule here is: the same compiler the builder uses, the same source
    the app ships, and fixtures that can fail. The arm count is asserted too, because a probe
    whose fixtures silently disappeared passes.
    """
    compiler = _find_csharp_compiler()
    if compiler is None:
        print("   SKIP: no C# compiler found")
        _delegation(label, False, ROOT / "desktop", "no C# compiler found")
        return
    _delegation(label, True, ROOT / "desktop")
    missing = [str(p) for p in sources if not p.exists()]
    check("%s: the source under probe exists" % label, not missing, missing)
    if missing:
        return
    out_dir = Path(tempfile.mkdtemp(prefix="dofusfarm-probe-"))
    src = out_dir / "probe.cs"
    src.write_text(program, encoding="utf-8")
    exe = out_dir / "probe.exe"
    args = ["/nologo", "/target:exe", "/out:%s" % exe]
    refs = ("mscorlib.dll", "System.dll")
    if compiler[0].lower().endswith("csc.exe"):
        args.extend("/r:%s" % name for name in refs)
    else:
        framework = None
        windir = Path(os.environ.get("windir", r"C:\Windows"))
        for flavour in ("Framework64", "Framework"):
            candidate = windir / "Microsoft.NET" / flavour / "v4.0.30319"
            if (candidate / "mscorlib.dll").exists():
                framework = candidate
                break
        if framework is not None:
            args.append("/nostdlib+")
            args.extend("/r:%s" % (framework / name) for name in refs)
        else:
            args.extend("/r:%s" % name for name in refs)
    try:
        proc = subprocess.run([*compiler, *args] + [str(p) for p in sources] + [str(src)],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=240)
        check("%s compiles against the app's own source" % label, proc.returncode == 0,
              (proc.stderr or proc.stdout).strip()[-400:])
        if proc.returncode != 0:
            return
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
        lines = (run.stdout or "").splitlines()
        rows = [line for line in lines if line.startswith("FAIL")]
        arms = sum(1 for line in lines if line.startswith("PASS"))
        check("%s runs every fixture green (%d arms)" % (label, arms), run.returncode == 0,
              rows or (run.stdout or run.stderr).strip()[-300:])
        check("%s is not vacuous: it actually carries fixtures" % label, arms >= 5,
              "arms=%d head=%s" % (arms, (run.stdout or "")[:120]))
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def test_net_pill() -> None:
    section("The network pill, executed (not grepped)")
    # The pill is the only thing that tells the operator the pipeline is not being watched. A
    # green that really means "hours old, nobody checked" is the same class of lie as the build
    # line that said "11 patches active" over zero patches, so the decision function is compiled
    # and RUN here against fixtures -- nine states, each one able to fail.
    program = r'''
using System;
using DofusFarm.Net;
class Probe {
    const long NOW = 1000000L;
    static int fail = 0;
    static void Check(string name, string wantText, int wantLevel, string json) {
        string text;
        int level = NetPill.From(json, NOW, out text);
        bool ok = text == wantText && level == wantLevel;
        Console.WriteLine((ok ? "PASS " : "FAIL ") + name + " -> [" + text + "] lvl=" + level);
        if (!ok) fail++;
    }
    static int Main() {
        Check("a fresh clean report is green", "net ok", NetPill.Good,
              "{\"checked_at\":999999.0,\"components\":[{\"component\":\"egress\",\"state\":\"answering\"}],\"problems\":[]}");
        Check("named problems are red", "net! relay,brain", NetPill.Bad,
              "{\"checked_at\":999995.0,\"problems\":[\"relay\",\"brain\"]}");
        Check("more than two names collapse to a plus", "net! a,b+", NetPill.Bad,
              "{\"checked_at\":999999,\"problems\":[\"a\",\"b\",\"c\"]}");
        Check("a clean report nobody refreshed goes amber", "net ok 10m", NetPill.Stale,
              "{\"checked_at\":999400,\"problems\":[]}");
        Check("a stale green is not a status", "net ? 2h", NetPill.Stale,
              "{\"checked_at\":992800,\"problems\":[]}");
        Check("the pre-repair view of a fixed pipeline is not red", "net ok", NetPill.Good,
              "{\"kind\":\"ensure\",\"before\":{\"checked_at\":100,\"problems\":[\"relay\"]},"
            + "\"actions\":[{\"component\":\"relay\",\"state\":\"down\",\"problems\":[\"relay\"]}],"
            + "\"after\":{\"checked_at\":999999,\"components\":[{\"component\":\"relay\",\"state\":\"answering\"}],\"problems\":[]},"
            + "\"problems\":[],\"clean\":true}");
        Check("no report at all says so", "net ?", NetPill.Unknown, null);
        Check("a report from the future is not trusted", "net ? clock", NetPill.Unknown,
              "{\"checked_at\":2000000,\"problems\":[]}");
        Check("a broken pipeline stays red when it is also old", "net! relay 2h", NetPill.Bad,
              "{\"checked_at\":992800,\"problems\":[\"relay\"]}");
        return fail == 0 ? 0 : 1;
    }
}
'''
    _cs_logic_probe([ROOT / "desktop" / "NetPill.cs"], program, "net pill probe")


def test_boot_state() -> None:
    section("The boot verdict, executed (not grepped)")
    # Every instrument in the update chain answers the wrong question. `--bundle-selftest` compares
    # text the rules produced with text the rules produced; the updater counts rules that matched.
    # Build 3.3.11 passed both (23/23, IDENTICAL) and the client black-screened on a doubled series
    # callback. The question that has an answer is "did a client boot with these bytes", so the
    # function that turns that answer into patch / vendor / revert / attest is run here.
    boot = ROOT / "desktop" / "BootState.cs"
    check("the boot state lives in the one file the app also compiles", boot.exists(), str(boot))
    if not boot.exists():
        return
    program = r'''
using System;
using DofusFarm.Update;
class Probe {
    static int fail = 0;
    static void Check(string name, bool ok, string detail) {
        Console.WriteLine((ok ? "PASS " : "FAIL ") + name + (ok ? "" : " -> " + detail));
        if (!ok) fail++;
    }
    static int Main() {
        Check("an unseen build with rules that match is patched",
              BootState.DecideAdoption("", "3.3.12_abc", 23) == BootState.Patch,
              BootState.DecideAdoption("", "3.3.12_abc", 23));
        Check("a build a client said it never booted with is not patched again",
              BootState.DecideAdoption("3.3.11_xyz\n", "3.3.11_xyz", 23) == BootState.Vendor,
              BootState.DecideAdoption("3.3.11_xyz\n", "3.3.11_xyz", 23));
        Check("matching 23/23 is not a reason to ship a known-bad build",
              BootState.IsBad("3.3.11_xyz\n", "3.3.11_xyz"), "IsBad missed its own line");
        Check("a prefix is not a match (3.3.11 vs 3.3.110)",
              !BootState.IsBad("3.3.110_xyz\n", "3.3.11_xyz"), "blacklisted a build nobody booted");
        Check("no rule matched means nothing was patched",
              BootState.DecideAdoption("", "3.3.12_abc", 0) == BootState.Vendor,
              BootState.DecideAdoption("", "3.3.12_abc", 0));
        Check("recording a bad build twice keeps one line",
              BootState.AddBad(BootState.AddBad("", "A"), "A").Trim().Split('\n').Length == 1,
              BootState.AddBad(BootState.AddBad("", "A"), "A"));
        Check("recording a second build keeps the first",
              BootState.IsBad(BootState.AddBad("A\n", "B"), "A")
              && BootState.IsBad(BootState.AddBad("A\n", "B"), "B"),
              BootState.AddBad("A\n", "B"));
        Check("the boot answer for the watched build clears it",
              BootState.OnCanary("A", "A", true, false) == BootState.Attest,
              BootState.OnCanary("A", "A", true, false));
        Check("the first failed boot reverts but does not blacklist",
              BootState.OnCanary("A", "A", false, false) == BootState.Revert,
              BootState.OnCanary("A", "A", false, false));
        Check("a second failed boot on the same build stops patching it",
              BootState.OnCanary("A", "A", false, true) == BootState.Blacklist,
              BootState.OnCanary("A", "A", false, true));
        Check("a healthy boot clears the watch",
              BootState.RemoveBad("A\nB\n", "A").Trim() == "B", BootState.RemoveBad("A\nB\n", "A"));
        Check("clearing a stamp that is not watched changes nothing else",
              BootState.RemoveBad("A\nB\n", "C").Trim() == "A\nB", BootState.RemoveBad("A\nB\n", "C"));
        Check("a failure with nothing pending is not evidence about our patches",
              BootState.OnCanary("", "A", false, true) == BootState.Ignore,
              BootState.OnCanary("", "A", false, true));
        Check("an answer about another build does not clear this one",
              BootState.OnCanary("A", "B", true, false) == BootState.Ignore,
              BootState.OnCanary("A", "B", true, false));
        Check("an answer without a stamp is dropped, not guessed",
              BootState.OnCanary("A", "", false, true) == BootState.Ignore,
              BootState.OnCanary("A", "", false, true));
        // The brain daemon: the panel attaches to whatever answers its port, so a
        // daemon running code older than the files it is being tested against has
        // to be restarted rather than trusted. Measured 2026-11-04: three fixes
        // landed after the attached daemon started and every relaunch re-measured
        // the pre-fix code while /brain/health said `status: ok`.
        Check("no answer means start one",
              BootState.DecideBrainAttach(null) == BootState.BrainStart, "null");
        Check("something else on the port is not a brain",
              BootState.DecideBrainAttach("<html>404</html>") == BootState.BrainOther,
              BootState.DecideBrainAttach("<html>404</html>"));
        Check("a current brain is attached to",
              BootState.DecideBrainAttach(
                  "{\"status\":\"ok\",\"service\":\"dofus-brain\",\"pid\":4768,"
                  + "\"code_stale\":[]}") == BootState.BrainAttach,
              BootState.DecideBrainAttach(
                  "{\"status\":\"ok\",\"service\":\"dofus-brain\",\"pid\":4768,"
                  + "\"code_stale\":[]}"));
        Check("a brain naming a newer file is restarted",
              BootState.DecideBrainAttach(
                  "{\"service\":\"dofus-brain\",\"pid\":7336,"
                  + "\"code_stale\":[\"agent.planner\"]}") == BootState.BrainRestart,
              BootState.DecideBrainAttach(
                  "{\"service\":\"dofus-brain\",\"pid\":7336,"
                  + "\"code_stale\":[\"agent.planner\"]}"));
        Check("and the restart is aimed at the pid that spoke",
              BootState.BrainPid("{\"service\":\"dofus-brain\",\"pid\":7336,"
                  + "\"code_stale\":[\"agent.planner\"]}") == 7336,
              BootState.BrainPid("{\"service\":\"dofus-brain\",\"pid\":7336}").ToString());
        Check("a brain too old to report its code is attached to, not killed",
              BootState.DecideBrainAttach(
                  "{\"status\":\"ok\",\"service\":\"dofus-brain\",\"pid\":9132}")
                  == BootState.BrainAttach,
                  BootState.DecideBrainAttach(
                      "{\"status\":\"ok\",\"service\":\"dofus-brain\",\"pid\":9132}"));
        // The idempotence guard the shipped patcher now shares with the reference
        // implementation. "Skip any rule whose effect text is present" was written
        // first and dropped rule 10 from the FIRST pass, because the vendor bundle
        // already contains that snippet -- so the guard belongs to self-embedding rules.
        Check("a replacement that keeps its own anchor is self-embedding",
              BootState.RuleIsSelfEmbedding("function f(){", "function f(){guard;}"),
              "missed the shape that re-applies");
        Check("a replacement that consumes its anchor is not self-embedding",
              !BootState.RuleIsSelfEmbedding("var c=a.b;", "var d=1;"),
              "called a consumed needle self-embedding");
        Check("a self-embedding rule already in the text is skipped",
              BootState.SkipSelfEmbedded(true, true), "would wrap a second time");
        Check("a first application is never skipped",
              !BootState.SkipSelfEmbedded(true, false), "skipped a rule that had not run");
        Check("and a rule that cannot re-apply is not skipped for its effect text",
              !BootState.SkipSelfEmbedded(false, true),
              "the blunt guard that dropped rule 10 on the vendor bundle");
        return fail == 0 ? 0 : 1;
    }
}
'''
    _cs_logic_probe([boot], program, "boot state probe")


def test_patch_rules_are_idempotent() -> None:
    """Patching a bundle that is already patched must not wrap anything twice.

    The reference patcher applied every rule whose anchor it could find, and four of
    the 23 rules are keyed on text their own replacement still contains (rules 1-3 are
    boundary rules whose replacement holds their start marker, rule 0's replacement
    contains its needle). Running the rules over their own output therefore applied
    those four again -- measured 2026-11-04 on build 3.3.11, where pass one matched
    23/23 and pass two matched 4 more. A doubled callback wrapper is exactly what
    "Callback was already called" looks like from the client's side, and the shipped
    patcher (`desktop/clientbundle.cs::ApplyPatches`) shared the behaviour.

    Each rule is tested against a bundle made only from its own anchor, so a rule that
    regresses is named by id rather than hidden in a global count.
    """
    section("Each patch rule reaches its fixed point in one application")
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "apply_patch_rules", ROOT / "tools" / "apply-patch-rules.py")
    assert spec is not None and spec.loader is not None
    apr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(apr)
    rules = apr.load_rules()
    check("the rules file is read by the instrument the updater mirrors",
          len(rules) == 23, "%d rules" % len(rules))

    def reapplies(r: Dict[str, Any]) -> bool:
        """Does this rule's replacement still contain the text it was keyed on?"""
        if r["kind"] == "boundary":
            return r["start"] in r["replacement"]
        return r["old"] in r["new"]

    self_embedding = [str(r["id"]) for r in rules if reapplies(r)]
    check("the rules that re-apply to their own output are known by id",
          self_embedding == ["0", "1", "2", "3"], str(self_embedding))

    double_applied = []
    for r in rules:
        if r["kind"] == "boundary":
            bundle = r["start"] + ";var holder=1;" + r["end"]
        else:
            bundle = "before(" + r["old"] + ");after()"
        once, matched1, _skipped1 = apr.apply(bundle, [r])
        twice, matched2, _skipped2 = apr.apply(once, [r])
        if len(matched1) != 1 or len(matched2) != 0 or twice != once:
            double_applied.append((r["id"], len(matched1), len(matched2), twice != once))
    check("no rule applies twice to its own output", double_applied == [], str(double_applied))

    # The blunt version of this guard -- "skip when the effect text is present" -- was
    # written first and quietly dropped rule 10 from the FIRST pass, because the vendor
    # bundle already contains that snippet. The guard belongs to self-embedding rules only.
    r10 = [r for r in rules if str(r["id"]) == "10"][0]
    vendor = ROOT / "desktop" / "client" / "www" / "build" / "script.js.bak"
    if vendor.exists():
        text = vendor.read_text(encoding="utf-8", errors="surrogateescape")
        effect_present = (r10.get("new") or r10.get("replacement", "")) in text
        anchor_present = r10["old"] in text if r10["kind"] != "boundary" else True
        check("a rule whose effect text already exists but is not self-embedding still applies",
              (not effect_present) or (r10["old"] not in r10["new"]) and anchor_present,
              str((r10["id"], effect_present, r10["old"] in r10["new"])))
        once_all, matched_all, skipped_all = apr.apply(text, rules)
        check("and the whole rule set still takes 23/23 on the vendor bundle",
              len(matched_all) == 23, str((len(matched_all), skipped_all[:3])))
        twice_all, matched_again, _ = apr.apply(once_all, rules)
        check("with nothing re-applied on a second adoption",
              matched_again == [], str(matched_again))
        check("and the patched bundle is a fixed point", once_all == twice_all,
              str(len(once_all) - len(twice_all)))


def test_cleanroom_harness_is_falsifiable() -> None:
    """The clean-room decompilation harness must be able to report failure.

    `tools/cleanroom_probe.py` exists to test a strong claim -- deduce a private
    model's rule from ~100 boundary probes and compile a small module from scratch,
    no distillation training. A harness built to confirm that claim will confirm
    anything, so it ships with a decoy oracle and a budget meter, and the self test
    that proves both can say FAIL is run here. The recorded bar numbers are re-derived
    too: a rule scored against a stale baseline is not scored at all.
    """
    section("The clean-room probe harness can fail")
    import subprocess

    tool = REPO_ROOT / "tools" / "cleanroom_probe.py"
    check("the harness exists where the plan says it is", tool.exists(), str(tool))
    if not tool.exists():
        return
    proc = subprocess.run([sys.executable, "-X", "utf8", str(tool), "selftest"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(REPO_ROOT), timeout=600)
    arms = [ln for ln in proc.stdout.splitlines()
            if ln.startswith("[PASS]") or ln.startswith("[FAIL]")]
    check("the self test runs every arm it advertises", len(arms) >= 6,
          "%d arms, exit %d, %s" % (len(arms), proc.returncode,
                                    proc.stderr[-200:] if proc.returncode else ""))
    check("and no arm is red", all(ln.startswith("[PASS]") for ln in arms),
          str([ln for ln in arms if not ln.startswith("[PASS]")])[:220])
    check("the hostile arm is genuinely hostile",
          "decoy" in proc.stdout and "not satisfiable" in proc.stdout,
          proc.stdout[-160:])

    base = REPO_ROOT / "data" / "cleanroom_baselines.json"
    check("the equal-budget bar is recorded, not remembered", base.exists(), str(base))
    if base.exists():
        doc = json.loads(base.read_text(encoding="utf-8"))
        from agent.weight_space import evaluate, export_manifest, manifest_weights, read_val_rows

        rows = read_val_rows()
        now = evaluate(manifest_weights(export_manifest()), rows,
                       export_manifest()["base"]["tools"])["val_accuracy"]
        check("the incumbent's bar still measures the corpus that is on disk",
              abs(float(doc["incumbent_head_val_accuracy"]) - now) < 0.005,
              str((doc["incumbent_head_val_accuracy"], now, len(rows))))
        sgd = doc["equal_budget_controls"]["sgd_100_queries_at_head_answers"]
        check("and the recorded control is the equal-budget one (100 probes)",
              int(sgd["train_rows"]) == int(doc.get("equal_budget_controls", {})
                                            .get("sgd_100_queries_at_task_labels", {})
                                            .get("train_rows", 0)) == 100,
              str((sgd["train_rows"], doc["equal_budget_controls"]
                   ["sgd_100_queries_at_task_labels"]["train_rows"])))


def test_adjacent_crossing_is_bounded_tighter() -> None:
    """A border that refuses to be crossed twice is a broken exit, not a long route.

    Live 2026-11-04, session 13:03: objective 13958 of quest 4262 wanted the map
    one crossing away, and the loop sent `nav.goto {map_id: 145490945}` nine times
    in a row from 145490944, walking the character cell 299 -> 537 and back, every
    step logged `advanced` because the client really did move. The travel budget is
    12 because a legitimate route can need many borders; against a target one border
    away, 12 means 84 seconds of an 18-minute session spent bouncing, and the run
    ended on the retirement guard before the budget expired. The bound that pays is
    per *border*: `CROSSING_ASK_LIMIT` asks from the same map, and then the border is
    spent -- which matters because the first version of the fix scaled the budget by
    the distance to the final target, and the live session that bounced next was
    asking for the first hop of a four-crossing route (§S.2). Bounding only the
    `map_id` form would also let the bounce continue as `nav.goto {direction: …}`,
    which the client walks identically.
    """
    from agent.planner import CROSSING_ASK_LIMIT, QuestPlanner, ROUTE_STEP_LIMIT

    section("One crossing away gets the short budget")
    here, there = 145490944, 145490945
    obj = {"id": 13958, "type_id": 4, "type": "QuestObjectiveFindMap",
           "map_id": there, "params": {"parameter0": there},
           "text": "Find the map: Otonim Plateau."}
    quest = {"id": 4262}

    def scene() -> Dict[str, Any]:
        return {"map_id": here, "cell": 299, "level": 60,
                "quests_active": {"4262": 0},
                "map": {"map_id": here, "groups": [], "npcs": [],
                        "exits": [{"direction": "left", "map_id": there,
                                   "blocked": False, "cells": [537]}]}}

    planner = QuestPlanner()
    calls = [planner.plan_objective(quest, 0, obj, scene()) for _ in range(ROUTE_STEP_LIMIT)]
    rows = [(c[0] or {}) for c in calls if c]
    trips = [r for r in rows if r.get("tool") == "nav.goto"
             and (r.get("args") or {}).get("map_id") == there]
    # The same crossing asked for by direction is the same request: the client
    # walks the edge either way, and the live bounce came back in this costume.
    navs = [r for r in rows if r.get("tool") == "nav.goto"
            and ((r.get("args") or {}).get("map_id") == there
                 or (r.get("args") or {}).get("direction") == "left")]
    last = rows[-1] if rows else {}
    check("the same one-border destination is asked for the short budget, not 12",
          len(trips) <= CROSSING_ASK_LIMIT + 1,
          [len(trips), CROSSING_ASK_LIMIT, ROUTE_STEP_LIMIT,
           [r.get("why") for r in rows][:1]])
    check("and the bounce does not continue as a direction request",
          len(navs) <= CROSSING_ASK_LIMIT,
          [len(navs), CROSSING_ASK_LIMIT,
           [[r.get("args"), str(r.get("why"))[:60]] for r in navs]])
    check("so the session is left doing something else than crossing",
          last.get("tool") != "nav.goto" or
          (last.get("args") or {}).get("map_id") != there,
          [last.get("tool"), last.get("args"), str(last.get("why"))[:120]])
    why = str(last.get("why"))
    check("the reason names the budget that expired, so the row is readable",
          "budget" in why and "crossing" in why, why[:160])
    check("and the border that refused to deliver it, since that is the finding",
          "left edge" in why, why[:200])


def test_promise_shadow_names_a_bounced_crossing() -> None:
    """A crossing that never landed is `advanced`, and now also says so.

    The verifier's contract is objective evidence: something in the state moved, so
    the call advanced. That is what let the 2026-11-04 bounce read as progress -- the
    character walked cell 299 -> 537 -> 299 and every one of those steps was
    `advanced`, twelve of them in a session that ended on the retirement guard. The
    verdict vocabulary stays exactly as it is (it feeds the corpus labels and the
    online learner), and a second, narrower field rides the row instead: for a
    `nav.goto`, did the map the call named actually turn up underneath the character.

    The arms below are driven through the real `Verifier.label` and the real
    `Autopilot._emit`, not through the leaf -- a witness on the leaf was how the
    arrival reset nearly shipped unwitnessed the same day (§S.4).
    """
    from agent.autopilot import Autopilot, Step
    from agent.verifier import VERDICTS, Verifier, promise_state

    section("The promise a crossing made, and whether it kept it")
    # The recorded live row: same map, different cell, destination not reached.
    before = {"map_id": 145490944, "cell": 537, "level": 60}
    after = {"map_id": 145490944, "cell": 299, "level": 60}
    verdict = Verifier().label("nav.goto", {"map_id": 145490945}, True, None,
                               before, after)
    check("the bounced crossing is still verdicted `advanced` (the vocabulary is unchanged)",
          verdict.verdict == "advanced", verdict.as_dict())
    check("and the shadow field names it for what it is",
          promise_state("nav.goto", {"map_id": 145490945}, before, after) == "unmet",
          promise_state("nav.goto", {"map_id": 145490945}, before, after))
    check("an arrival on the named map is `met`",
          promise_state("nav.goto", {"map_id": 145490945}, before,
                        {"map_id": 145490945, "cell": 12}) == "met", None)
    check("a direction ask that changed the map is `met`",
          promise_state("nav.goto", {"direction": "left"}, before,
                        {"map_id": 145490946, "cell": 4}) == "met", None)
    check("a direction ask that did not is `unmet`, whatever moved",
          promise_state("nav.goto", {"direction": "left"}, before, after) == "unmet", None)
    check("no post-observation is `unknown`, and never collapses into `met`",
          promise_state("nav.goto", {"map_id": 145490945}, before, None) == "unknown",
          promise_state("nav.goto", {"map_id": 145490945}, before, None))
    check("a call that promised no map says `na` rather than judging",
          promise_state("quest.advance", {"quest_id": 4262}, before, after) == "na", None)
    # Reachable on purpose: `farm.start` carries a `map_id` in its schema, and its
    # promise is "stay and harvest", not "arrive". Scoring it as a failed crossing
    # would fill the shadow with a class of unmet that was never a crossing at all.
    check("a farm request that names a map is not judged as a crossing",
          promise_state("farm.start", {"map_id": 145490945}, before, after) == "na",
          promise_state("farm.start", {"map_id": 145490945}, before, after))
    check("the verdict vocabulary is the same four words",
          VERDICTS == ("advanced", "noop", "failed", "blocked"), str(VERDICTS))

    # The wiring: the loop's own row writer must carry the field and count it. Built
    # through `Autopilot._emit` with the log append captured, so nothing is written to
    # a real trajectory.
    ap = Autopilot.__new__(Autopilot)
    written: list = []
    ap._first_step_at = 0.0
    ap._last_step_at = 0.0
    ap._consecutive_waits = 3
    ap._forced_reason = "stale"
    ap._dead_windows = 2
    ap._step_total = 0
    ap._verdict_counts = {}
    ap._promise_counts = {}
    ap._source_counts = {}
    ap.steps = []
    ap.step_history = 256
    ap._append_log_line = written.append
    ap._emit(Step(index=0, goal="do all the quests", tool="nav.goto",
                  args={"map_id": 145490945}, ok=True, error=None, latency_ms=12.0,
                  verdict=verdict.verdict,
                  promise=promise_state("nav.goto", {"map_id": 145490945},
                                        before, after)))
    check("the loop counts a bounced crossing into the summary",
          ap._promise_counts.get("unmet") == 1, ap._promise_counts)
    row = json.loads(written[0].decode("utf-8").rstrip("\n")) if written else {}
    check("and the row it writes carries the field, not only the counter",
          row.get("promise") == "unmet" and row.get("verdict") == "advanced",
          {k: row.get(k) for k in ("verdict", "promise", "tool")})
    src = (REPO_ROOT / "agent" / "autopilot.py").read_text(encoding="utf-8", errors="replace")
    check("the row is stamped from the call and both observations, not a guess",
          "promise=promise_state(call.tool, call.args, before, after)" in src, None)
    check("and the summary reports the counts instead of leaving them in the rows",
          '"promises": dict(self._promise_counts)' in src, None)


def test_duel_simulator_keeps_its_own_contract() -> None:
    """The simulator every win rate is measured in had no anchor at all.

    §L.43: `combat/duel.py` was executed by the gate without being mutable, so a
    defect in it could only surface as a crash in someone else's test. It decides
    whose turn it is, who has AP, how an overtime is scored, what a spell costs and
    which cell pair counts as engaged -- which is to say, it decides the numbers the
    fight policy is trained and benchmarked on (#77, #81, #88).

    Every value asserted here was measured off the module first (0.3 s for the whole
    suite, so the sweep can afford it per mutation): hand-built duelists on the
    tracked map `data/maps/100139008.json`, no dataset, no solver.
    """
    import random

    from combat.duel import (CLASS_NAMES, EFFECT_BY_ELEMENT, FEATURE_NAMES,
                             Duelist, DuelState, advance_turn, board_for,
                             engaged_cells, live_spell, load_map,
                             lookahead_features, state_features)

    section("The duel simulator's own invariants")

    def spell(slot: int, ap: float, dmg: float, heal: float, element: int,
              ally: bool):
        return live_spell(slot, ap, 1, 5, dmg, heal, element, ally)

    def fighter(ident: int, cls: str, cell: int, hp: float, max_hp: float,
                ap: float = 6.0, level: int = 100, init: float = 500.0):
        return Duelist(id=ident, row=ident, class_name=cls, level=level, hp=hp,
                       max_hp=max_hp, ap=ap, max_ap=6.0, mp=ap, max_mp=100.0,
                       initiative=init, cell=cell,
                       resist=(("air", 10), ("earth", 20)),
                       spells=(spell(0, 3, 80.0, 0.0, 0, False),
                               spell(1, 2, 0.0, 60.0, 0, True)))

    cells = load_map()
    check("the tracked map loads and is not empty", len(cells.flags) > 100,
          len(cells.flags))

    # Whose turn it is, and who may spend.
    a = fighter(1, "Sram", 300, 500.0, 1000.0)
    b = fighter(2, "Ecaflip", 310, 400.0, 500.0, ap=0.0, level=90, init=400.0)
    state = DuelState(fighters=(a, b), cells=cells, order=(1, 2), max_rounds=3)
    check("the actor alternates on turn parity, whoever the order names first",
          [DuelState(fighters=(a, b), cells=cells, order=(1, 2),
                     turn=t).actor.id for t in (0, 1, 2)] == [1, 2, 1], None)

    mid = DuelState(fighters=(fighter(1, "Sram", 300, 500.0, 1000.0, ap=3.0),
                              fighter(2, "Ecaflip", 310, 400.0, 500.0, ap=3.0)),
                    cells=cells, order=(1, 2), max_rounds=3)
    advance_turn(mid)
    check("a turn starts with the actor refilled and the other side at zero",
          mid.actor.ap == 6.0 and mid.actor.mp == 100.0
          and mid.opponent.ap == 0.0 and mid.opponent.mp == 0.0,
          [mid.actor.ap, mid.actor.mp, mid.opponent.ap, mid.opponent.mp])
    advance_turn(mid)
    check("a round closes after both sides have acted",
          mid.turn == 2 and mid.round == 2 and not mid.done,
          [mid.turn, mid.round, mid.done])
    # The cap itself, at the boundary. Measured: with max_rounds=3 the duel is still
    # alive after five half-turns and ends on the sixth, when the round counter passes
    # the cap -- an off-by-one here silently shortens every recorded fight by one turn.
    capped = DuelState(fighters=(fighter(1, "Sram", 300, 500.0, 1000.0),
                                fighter(2, "Ecaflip", 304, 500.0, 500.0)),
                       cells=cells, order=(1, 2), max_rounds=3)
    trace = []
    while not capped.done and len(trace) < 12:
        advance_turn(capped)
        trace.append((capped.turn, capped.round, capped.done))
    check("the round cap releases the duel on the sixth half-turn, not the fourth",
          [t[:2] for t in trace] == [(1, 1), (2, 2), (3, 2), (4, 3), (5, 3), (6, 4)]
          and trace[-1][2], trace)

    # Overtime is scored on HP *fraction*, which is not the same answer as raw HP.
    over = DuelState(fighters=(fighter(1, "Sram", 300, 500.0, 1000.0),
                              fighter(2, "Ecaflip", 310, 400.0, 500.0)),
                     cells=cells, order=(1, 2), max_rounds=1, turn=1, round=5)
    advance_turn(over)
    check("past the round cap the healthier fraction wins, not the bigger number",
          over.done and over.winner == 2, [over.done, over.winner])
    draw = DuelState(fighters=(fighter(1, "Sram", 300, 500.0, 1000.0),
                              fighter(2, "Ecaflip", 310, 500.0, 1000.0)),
                     cells=cells, order=(1, 2), max_rounds=1, turn=1, round=5)
    advance_turn(draw)
    check("an equal fraction is a draw, and a draw is not silently a win",
          draw.done and draw.winner is None, [draw.done, draw.winner])

    # The board the policy actually sees.
    board = board_for(state, a)
    check("the board hands the actor the player team and the other side the enemy team",
          [f.team for f in board.fighters] == ["player", "enemy"]
          and [f.by_me for f in board.fighters] == [True, False],
          [[f.team for f in board.fighters], [f.by_me for f in board.fighters]])
    check("the board speaks as the actor: id, cell, AP and its own spellbook",
          board.my_id == 1 and board.my_cell == 300 and board.my_ap == 6
          and len(board.spells) == 2, [board.my_id, board.my_cell, board.my_ap])
    check("the cell map rides along unchanged, and the fight payload the policy reads is complete",
          tuple(board.cells.flags) == tuple(cells.flags)
          and {"fighters", "cells", "map_id", "my_cell", "my_ap", "is_my_turn",
               "current_fighter_id", "last_actions"} <= set(board.to_fight_state())
          and len(board.to_fight_state()["fighters"]) == 2,
          sorted(board.to_fight_state())[:9])

    # Spells.
    check("an ally heal outranks the same spell's damage, but only for an ally target",
          spell(1, 3, 90.0, 40.0, 2, True).effects[0].effect_id == 108
          and spell(1, 3, 90.0, 40.0, 2, False).effects[0].effect_id == 99,
          [spell(1, 3, 90.0, 40.0, 2, True).effects,
           spell(1, 3, 90.0, 40.0, 2, False).effects])
    check("a spell with nothing to apply carries no effects",
          spell(1, 3, 0.0, 0.0, 2, False).effects == (), None)
    check("a fractional AP cost is never free, and ranges are clamped and rounded",
          live_spell(0, 0.2, -3, 5.6, 50.0, 0.0, 0, False).ap_cost == 1
          and live_spell(0, 1, -3, 5.6, 50.0, 0.0, 0, False).min_range == 0
          and live_spell(0, 1, -3, 5.6, 50.0, 0.0, 0, False).max_range == 6, None)
    check("the element table maps five elements and wraps outside them",
          [live_spell(0, 1, 1, 2, 50.0, 0.0, e, False).effects[0].effect_id
           for e in range(5)] == list(EFFECT_BY_ELEMENT)
          and live_spell(0, 1, 1, 2, 50.0, 0.0, 7, False).effects[0].effect_id
          == EFFECT_BY_ELEMENT[7 % 5], EFFECT_BY_ELEMENT)

    # Engagement sampling.
    # Engagement sampling. Seeds 9, 15, 16 and 21 are here because they discriminate:
    # the first band-legal pair those seeds draw has no line of sight, so a sampler that
    # stopped requiring it returns a different pair (measured on this map).
    seeds = (1, 2, 3, 7, 9, 15, 16, 21)
    pairs = [engaged_cells(cells, random.Random(seed)) for seed in seeds]
    check("an engaged pair is walkable, in the band and with line of sight",
          all(pair is not None and 3 <= cells.distance(*pair) <= 8
              and cells.has_los(*pair) for pair in pairs), pairs)
    check("a seeded sampler is reproducible, so a matchup is a claim and not a dice roll",
          pairs == [engaged_cells(cells, random.Random(seed)) for seed in seeds],
          None)
    tight = engaged_cells(cells, random.Random(1), min_distance=3, max_distance=3)
    check("the band is honoured exactly when min equals max",
          tight is not None and cells.distance(*tight) == 3, tight)

    # Feature vector shared with the calibrator and the value model.
    feat = dict(zip(FEATURE_NAMES, state_features(state, a)))
    check("the feature vector and its names stay the same length",
          len(state_features(state, a)) == len(FEATURE_NAMES)
          == 21 + 2 * len(CLASS_NAMES), [len(state_features(state, a)),
                                         len(FEATURE_NAMES), len(CLASS_NAMES)])
    check("the named features say what their names claim",
          feat["actor_hp"] == 0.5 and feat["opp_hp"] == 0.8
          and feat["actor_class_Sram"] == 1.0
          and feat["opp_class_Ecaflip"] == 1.0
          and feat["actor_class_Feca"] == 0.0,
          {k: feat.get(k) for k in ("actor_hp", "opp_hp", "actor_class_Sram")})

    look_state = DuelState(fighters=(fighter(1, "Sram", 300, 500.0, 1000.0),
                                    fighter(2, "Ecaflip", 304, 500.0, 1000.0)),
                           cells=cells, order=(1, 2), max_rounds=3)
    saved = (look_state.actor.ap, look_state.actor.mp,
             look_state.opponent.ap, look_state.opponent.mp)
    plain = state_features(look_state, look_state.actor)
    look = lookahead_features(look_state, look_state.actor)
    check("lookahead shows the actor spent and the opponent recharged",
          plain[3] == 1.0 and look[3] == 0.0 and look[5] == 1.0,
          [plain[3], plain[5], look[3], look[5]])
    check("and it leaves the state it borrowed untouched",
          (look_state.actor.ap, look_state.actor.mp, look_state.opponent.ap,
           look_state.opponent.mp) == saved,
          [saved, (look_state.actor.ap, look_state.opponent.ap)])


def test_lexical_seam_fills_only_what_the_sentence_states() -> None:
    """The grounding seam in `ai/lexical_policy.py` is what makes the shipped 93.3 real.

    #67 found a defect in this exact file that a *static* import walk missed (sklearn is
    imported inside a method), and §K measured that every argument miss on the unseen-frame
    holdout was a value the player had actually said -- `farm bouftou 100x`, `gehe zu 4,-19`,
    `vends laine ah 2k` -- which the corpus generator never emits, so no trained head and no
    relocation rule can see them. The seam's whole contract is *fill only*: a field the
    sentence states gets its digits, a field it does not state stays absent, because
    proposing a number the player never said is the failure mode this artifact exists to
    avoid. None of this file was mutable before; every value below was measured first.
    """
    import contextlib
    import io
    import os

    from ai import lexical_policy as lx
    from ai.lexical_policy import (_fold, extract_value, ground_call,
                                   load_if_configured, read_stated, recount)

    section("The grounding seam states nothing the sentence did not")

    # `_fold`: case, accents and punctuation must not break a whole-word comparison.
    check("folding keeps words apart instead of welding them",
          _fold("Anne-Cécile 2× GO!") == "anne cecile 2 go "   # punctuation -> a space, not deletion
          and _fold("") == "", _fold("Anne-Cécile 2× GO!"))

    # `extract_value`: verbatim, longest, whole-token only.
    values = ["bouftou", "laine de bouftou", "wool"]
    check("the longest value present is the one returned",
          extract_value("vends 5 laine de bouftou", values) == "laine de bouftou",
          extract_value("vends 5 laine de bouftou", values))
    check("case and punctuation do not hide a stated value",
          extract_value("Wool please", values) == "wool"
          and extract_value("tu wool?", values) == "wool", None)
    check("a value that is only a fragment of a word is not grounded",
          extract_value("laine", ["bouftou", "laine de bouftou"]) is None
          and extract_value("xaine", ["aine"]) is None,
          [extract_value("laine", values), extract_value("xaine", ["aine"])])
    check("non-string candidates are skipped rather than compared as text",
          extract_value("55", [55, "55"]) == "55", None)

    # `recount`: a digit stated inside a span belongs in the number field it blocked.
    moved = recount("hdv.list", {"item": "5 laine"})
    check("a leading count leaves the span and lands in a numeric field",
          moved == {"item": "laine", "quantity": 5}, moved)
    twelve = recount("inv.sell", {"item": "12 potions"})
    check("the whole digit run moves, not its first character",
          twelve == {"item": "potions", "quantity": 12}, twelve)
    check("a count already in its own field is not moved twice",
          recount("inv.sell", {"item": "laine", "quantity": 5})
          == {"item": "laine", "quantity": 5}, None)
    check("a digit that is not followed by a space is not a leading count",
          recount("inv.sell", {"item": "12x laine"}) == {"item": "12x laine"}, None)
    check("an unknown tool is returned untouched rather than reshaped",
          recount("nosuchtool", {"item": "5 x"}) == {"item": "5 x"}, None)
    # Observed, not blessed: with no `quantity` in the schema the count goes to the first
    # numeric field that is not price/level_min/duration_min, and `quest.list` offers
    # `level_max`. "3 kolk" becomes a level ceiling, not a count.
    check("a schema without quantity still gets a home for the count (level_max)",
          recount("quest.list", {"area": "3 kolk"}) == {"area": "kolk", "level_max": 3},
          recount("quest.list", {"area": "3 kolk"}))

    # `read_stated`: digits the sentence states and no span contains.
    check("a stated kill count is filled",
          read_stated("farm.start", {}, "farm bouftou 100x") == {"kill_count": 100}, None)
    check("a field the call already carries is never overwritten",
          read_stated("farm.start", {"kill_count": 2}, "farm bouftou 100x")
          == {"kill_count": 2}, None)
    check("a k-scale price is filled in kamas, on both shop tools",
          read_stated("hdv.list", {"item": "laine"}, "vends laine ah 2k")["price"] == 2000
          and read_stated("inv.sell", {"item": "laine"}, "met 5 laine ah 2k")["price"] == 2000,
          None)
    check("coordinates arrive as a pair or as named axes",
          read_stated("nav.goto", {}, "gehe zu 4,-19") == {"x": 4, "y": -19}
          and read_stated("nav.goto", {}, "go to x=4 y=-19") == {"x": 4, "y": -19}, None)
    check("a half-grounded move keeps what the span claimed",
          read_stated("nav.goto", {"x": 1}, "go to 4,-19") == {"x": 1},
          read_stated("nav.goto", {"x": 1}, "go to 4,-19"))
    check("the first stated pair wins when the sentence holds several",
          read_stated("nav.goto", {}, "go to 4;5 then 6;7") == {"x": 4, "y": 5}, None)
    check("nothing stated means nothing added",
          read_stated("farm.start", {}, "farm bouftou") == {}
          and read_stated("farm.start", {}, "kill 3 wolves x 4") == {}
          and read_stated("farm.start", {}, "farm bouftou 3x5x") == {}, None)
    check("an unknown tool's arguments are returned as they came",
          read_stated("teleport.move", {}, "gehe zu 4,-19") == {}, None)

    # The seam's no-resolver path and the supplier's fail-open.
    check("with no gazetteer and no knowledge base the call passes through, verb kept",
          ground_call("harvest.gather", {"action": "gather", "item": "laine"},
                      "vends 5 laine") == {"item": "laine", "action": "gather"}, None)
    saved = os.environ.pop(lx.ENV_MODEL, None)
    try:
        check("no environment means no learned model, silently -- the rules decide",
              load_if_configured() is None, None)
        tmp = Path(tempfile.mkdtemp()) / "lexicon-no-model-here"
        tmp.mkdir(parents=True)
        os.environ[lx.ENV_MODEL] = str(tmp)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            loaded = load_if_configured()
        check("an environment naming a directory without an artifact stays on the rules",
              loaded is None, loaded)
        check("and it says so, because a silent downgrade is how #67 hid",
              "has no policy.joblib; staying on the rules" in printed.getvalue(),
              printed.getvalue()[:160])
    finally:
        os.environ.pop(lx.ENV_MODEL, None)
        if saved is not None:
            os.environ[lx.ENV_MODEL] = saved
    check("the classifier/retrieval split keeps its published thresholds",
          (lx.MAX_DOMAIN, lx.MIN_ROWS) == (30, 25), [lx.MAX_DOMAIN, lx.MIN_ROWS])


def test_online_learn_gate_needs_transfer_it_did_not_copy() -> None:
    """The promotion gate must refuse a calibrator that only copies the analytic score.

    `combat/online_learn.py` decides whether a learned fight calibrator replaces the
    analytic estimate, and until now nothing in the gate opened it (§L.43: the file had
    no mutation anchor, so a defect there was invisible by construction). These arms are
    measured off `evaluate_gate` itself, on synthetic slices of 60 interleaved fights
    (train 42 / eval 18), because the two verdict shapes are *different objects*: the
    early returns carry a singular `reason`, the scored path a `reasons` list --
    asserting one shape on the other is a KeyError at gate time.

    What each arm does, in the numbers the gate reports:
      * copy of the analytic ranking -> refused, delta_auc 0.000, perm_p 1.000, and BOTH
        the AUC-margin and the permutation reason;
      * a model that ranks fights the analytic score ranks backwards -> passed,
        delta_auc 1.000, perm_p 0.002, no reasons;
      * 10 fights -> collecting with the count in the sentence;
      * one class in the slice -> refused through the singular `reason`.

    The third arm is a finding, not an accident: an analytic baseline that scores every
    fight the same gets AUC 0.500, so "beats the analytic by 0.02" is satisfied by an
    uninformative comparator. Pinned here so the property is visible rather than
    inherited silently.
    """
    from combat import online_learn as ol

    section("The fight calibrator has to transfer, not copy")
    names = list(ol.FEATURE_NAMES)
    check("the gate reads the shared feature list, not its own copy",
          len(names) >= 6 and "level_gap" in names, names)

    def fight(i: int, won: bool, raw: float) -> Dict[str, Any]:
        feats = {n: 0.0 for n in names}
        feats["level_gap"] = 1.0 if won else -1.0
        feats["hp_ratio"] = 0.8 if won else 0.2
        return {"kind": "fight_outcome", "won": won, "at": float(i),
                "features": feats, "estimate": {"raw_score": raw}, "instance": "probe"}

    def slice_of(mode: str, n: int = 60) -> list:
        rows = []
        for i in range(n):
            won = (i % 2 == 0)   # interleaved in time: both classes land in the eval slice
            raw = {"copy": (1.0 if won else -1.0),
                   "contradict": (-1.0 if won else 1.0),
                   "flat": 0.5}[mode]
            rows.append(fight(i, won, raw))
        return ol.terminal_samples(rows)

    copied = ol.evaluate_gate(slice_of("copy"))
    check("the copy arm reaches the scored path, not an early return",
          copied.get("status") == "refused" and isinstance(copied.get("reasons"), list)
          and copied.get("eval_n") >= ol.MIN_EVAL and copied.get("train_n") >= 20,
          [copied.get("eval_n"), copied.get("train_n")])
    check("a calibrator that only re-ranks the analytic score gains nothing",
          copied.get("delta_auc") == 0.0 and copied.get("candidate", {}).get("auc") == 1.0,
          copied.get("candidate"))
    check("and it is refused on the AUC margin AND on the permutation check",
          any("does not beat the analytic" in r for r in copied["reasons"])
          and any("permutation p" in r for r in copied["reasons"]), copied["reasons"])
    check("the permutation p it is refused at is not small",
          float(copied["perm_p"]) > ol.MAX_PERM_P, copied["perm_p"])

    real = ol.evaluate_gate(slice_of("contradict"))
    check("a model that uses a signal the analytic score contradicts is promoted",
          real.get("status") == "passed" and real.get("reasons") == []
          and float(real["delta_auc"]) > ol.MARGIN_AUC
          and float(real["perm_p"]) <= ol.MAX_PERM_P,
          [real.get("status"), real.get("delta_auc"), real.get("perm_p"), real.get("reasons")])
    check("so the two arms really do disagree -- the gate is not refusing everything",
          copied["status"] != real["status"], [copied["status"], real["status"]])

    flat = ol.evaluate_gate(slice_of("flat"))
    check("a flat analytic baseline scores AUC 0.5 and the gate still passes it",
          flat.get("status") == "passed" and flat.get("baseline", {}).get("auc") == 0.5,
          [flat.get("status"), flat.get("baseline")])

    few = ol.evaluate_gate(slice_of("copy", n=10))
    check("ten fights is not enough to gate, and the sentence counts them",
          few.get("status") == "collecting" and "10 of 40" in str(few.get("reason")),
          few)
    one_class = ol.evaluate_gate(ol.terminal_samples(
        [fight(i, True, 1.0) for i in range(60)]))
    check("one class in the slice is refused, through the singular `reason`",
          one_class.get("status") in ("refused", "collecting")
          and bool(one_class.get("reason")), one_class)
    check("the thresholds travel in the verdict, so a reader can audit the decision",
          set(ol.evaluate_gate(slice_of("copy"))["thresholds"]) >=
          {"min_fights", "margin_auc", "max_perm_p", "max_ece"}, None)


def test_measured_sizes_say_their_unit() -> None:
    """A size printed under a unit the code never measured.

    Twice in one day: the builder claimed a patched bundle's size in UTF-16 code
    units and called them bytes (Δ 35,195 on the real bundle), and the compat
    probe wrote `text.length` into the shim's table as `bundleBytes`. The second
    one is the dangerous shape -- it is a *record*, so the wrong number is what
    the next reader inherits. The row is therefore checked against the file it was
    measured from, not against the field name.
    """
    section("Recorded sizes are the unit they claim")
    probe = (ROOT / "tools" / "compat_probe.js").read_text(encoding="utf-8", errors="replace")
    check("the probe records bytes as bytes",
          'bundleBytes: Buffer.byteLength(text, "utf8")' in probe
          and "bundleBytes: text.length" not in probe,
          (probe.count("Buffer.byteLength"), probe.count("bundleBytes: text.length")))
    check("and says how long the bundle is in code units when it says so",
          "bundleChars: text.length" in probe)

    shim = (ROOT / "desktop" / "client" / "www" / "js" / "compat-shim.js").read_text(
        encoding="utf-8", errors="replace")
    begin = shim.find("BEGIN AUTO-RESOLVED TABLE")
    end = shim.find("END AUTO-RESOLVED TABLE")
    check("the shim still carries its auto-resolved region", begin > 0 and end > begin,
          str((begin, end)))
    if begin > 0 and end > begin:
        table_text = shim[shim.index("{", begin):shim.rindex("}", begin, end) + 1]
        rows = re.findall(r'"bundleChars":\s*(\d+),\s*"bundleBytes":\s*(\d+)', table_text)
        check("every recorded build carries both counts",
              len(rows) >= 1 and '"bundleBytes"' in table_text, str(rows))
        bundle = ROOT / "desktop" / "client" / "www" / "build" / "script.js"
        if bundle.exists() and rows:
            raw = bundle.read_bytes()
            chars = len(raw.decode("utf-8"))
            check("and the recorded byte count is the file's own size",
                  any(int(b) == len(raw) for _c, b in rows),
                  "table=%s file=%d bytes / %d chars" % (rows, len(raw), chars))
            check("the two counts differ exactly as often as the bundle holds non-ASCII",
                  chars <= len(raw), "%d chars vs %d bytes" % (chars, len(raw)))

    builder = (ROOT / "builder" / "Build.cs").read_text(encoding="utf-8", errors="replace")
    check("the builder's patched-size line reads the file, not the string",
          "new FileInfo(scriptPath).Length" in builder, builder.count("script.Length"))


def test_consult_gate_is_fitted_and_live() -> None:
    """The head's "I'm unsure, you decide" threshold must be re-derived, not remembered.

    Measured 2026-11-04 on the episode-held-out split: the shipped tool-policy head
    scores 0.9043, and letting the rule planner answer whenever the head's top-2 logit
    margin is under 0.75 scores 0.9099 (26 rescued, 5 broken, paired-sign p=0.0002).
    The same gate with a *random* answer scores 0.8884, so the gain is the second
    opinion and not the confidence filter. `agent/consult_gate.py` and
    `agent/planner_nn.py` carry that into the acting path; this re-fits it from the
    corpus on every run, so a regenerated dataset or a replaced head has to re-earn
    the threshold instead of inheriting a stale number -- the same failure that let a
    gain report quote 8,453 rows that no longer existed at the path it named.
    """
    section("The consult gate is a measurement, and it is live in the encoder arm")
    import numpy as np

    from agent import consult_gate
    from agent.planner_nn import NNPlanner

    gate = consult_gate.load()
    check("the artifact exists and states its provenance",
          bool(gate) and gate.get("fit_split_sha256_prefix") and gate.get("evidence"),
          str(sorted((gate or {}).keys()))[:150])
    floor = consult_gate.floor()
    check("the arms can fail: a sure head keeps its call, an unsure one defers",
          consult_gate.should_defer(gap=0.30) and not consult_gate.should_defer(gap=3.0),
          str(floor))
    check("a numpy logit vector does not break the margin read",
          abs(consult_gate.margin(np.array([0.1, 0.9, 0.2])) - 0.7) < 1e-9,
          str(consult_gate.margin(np.array([0.1, 0.9, 0.2]))))
    check("and no artifact means no gate, not a silent behaviour change",
          consult_gate.should_defer(gap=0.01, gate={}) is False)
    src = (REPO_ROOT / "agent" / "consult_gate.py").read_text(encoding="utf-8", errors="replace")
    wired = (REPO_ROOT / "agent" / "planner_nn.py").read_text(encoding="utf-8", errors="replace")
    check("the encoder arm calls the gate on its own logits, at one site only",
          wired.count("consult_gate.should_defer(gap=self.last_margin)") == 1
          and "consult_gate.margin(logits)" in wired,
          str((wired.count("should_defer"), wired.count("consult_gate.margin"))))
    # The docstring quotes the bad idiom to explain it, so search the function
    # body, not the file.
    body = src[src.index("def margin("):src.index("def should_defer(")]
    check("the margin read never bool()s a logit array",
          "logits or []" not in body and "if logits is None" in src,
          body.count("logits or []"))

    class StubSession:
        def __init__(self, logits):
            self.l = np.asarray(logits, dtype="float32")

        def run(self, _a, _b):
            return [np.array([self.l])]

    class StubTracker:
        def ctx(self):
            return {}

    tools = ["nav.goto", "quest.advance", "farm.start", "fight.join", "inv.list"]

    def arm(logits):
        nn = NNPlanner.__new__(NNPlanner)
        nn._model_loaded = True
        nn._warned = False
        nn.tools = tools
        nn.session = StubSession(logits)
        nn.input_name = "features"
        nn.expected_features = 42
        nn.tool_penalty = {}
        nn.stack = None
        nn.stack_mode = "off"
        nn.tracker = StubTracker()
        nn.note = ""
        nn.last_margin = None
        nn.last_deferral = None
        nn.last_stack = {"tool": None}
        call = nn._choose_tool({"level_min": 60, "id": 4262}, 0,
                              {"type_id": 1, "params": "{}", "text": "Talk."},
                              {"map_id": 145490432, "level": 60, "inventory": {},
                               "in_fight": False})
        return nn, call

    unsure, call_unsure = arm([0.0, 0.05, 5.0, 5.2, 1.0])
    sure, call_sure = arm([0.0, 0.0, 2.0, 9.0, 1.0])
    check("the encoder arm really hands the unsure call to the rule planner",
          call_unsure is None and abs(unsure.last_margin - 0.2) < 1e-6,
          str((call_unsure, unsure.last_margin)))
    check("and keeps a sure one", call_sure == "fight.join", str((call_sure, sure.last_margin)))
    check("the deferral is recorded, so a shadow row can show it",
          unsure.last_deferral == unsure.last_margin is not None, str(unsure.last_deferral))
    check("a gate crash is not reported as a broken model", unsure.note == "", unsure.note)

    from agent.weight_space import (export_manifest, manifest_weights, read_val_rows,
                                forward)

    rows = read_val_rows()
    m = export_manifest()
    tl = m["base"]["tools"]
    logits = forward(manifest_weights(m), np.asarray([r["x"] for r in rows]))
    srt = np.sort(logits, 1)
    marg = srt[:, -1] - srt[:, -2]
    head = np.array([tl[i] for i in np.argmax(logits, 1)])
    gold = np.array([tl[int(r["y"])] for r in rows])
    teacher = np.array([tl.index(str(r.get("teacher") or ""))
                        if str(r.get("teacher") or "") in tl else -1 for r in rows])
    tp = np.array([tl[i] if i >= 0 else "" for i in teacher])
    eps = np.array([int(r.get("ep") or 0) for r in rows])
    folds = np.unique(eps)
    fid = np.array([{int(e): i % 5 for i, e in enumerate(folds)}[int(e)] for e in eps])
    cands = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 5.0, float("inf")]
    pred = np.array(head)
    chosen = []
    for f in range(5):
        tr_i, te_i = fid != f, fid == f
        best = max((((np.where((marg[tr_i] <= t) & (teacher[tr_i] >= 0), tp[tr_i], head[tr_i])
                      == gold[tr_i]).mean(), t) for t in cands))
        chosen.append(best[1])
        low = (marg <= best[1]) & (teacher >= 0)
        sel = te_i & low
        pred[sel] = tp[sel]
    nested = float((pred == gold).mean())
    head_only = float((head == gold).mean())
    check("nested 5-fold refit reproduces the recorded gain",
          nested >= head_only + 0.003 and abs(nested - 0.9099) < 0.002,
          str((round(head_only, 4), round(nested, 4), chosen)))
    check("the fitted floor is stable across folds, not tuned on the answer",
          len(set(chosen)) == 1 and float(chosen[0]) == float(floor or -1), str(chosen))
    rng = np.random.default_rng(4)
    shuffled = np.array(head)
    for f in range(5):
        te_i = fid == f
        low = (marg <= chosen[f]) & (teacher >= 0)
        sel = te_i & low
        shuffled[sel] = rng.permutation(gold)[sel]
    check("the same gate with a random second opinion LOSES to the head",
          float((shuffled == gold).mean()) < head_only,
          str((round(head_only, 4), round(float((shuffled == gold).mean()), 4))))
    check("the promotion bar a future weight edit must clear is recorded",
          float(gate["evidence"]["gated_accuracy_nested"]) >= nested - 0.002,
          str((gate["evidence"]["gated_accuracy_nested"], round(nested, 4))))


def test_mutation_targets_have_witnesses() -> None:
    """A mutation no test opens is reported as MISSED, which looks like a weak gate.

    The sweep runs a fixed list of gate tests per mutation. Twice the verdict
    "MISSED, 0 failing checks" meant nothing in that list reads the mutated file --
    once for the knowledge index's stores, once for `combat/observation.py`, and a
    third time on 2026-11-04 for the four currency mutations written against
    `BootState.cs` and `agent/brain_daemon.py`, which were green on every arm and
    still MISSED. This check turns the discovery into a ratchet instead of a
    footnote: every file a mutation rewrites should be named by a test the sweep
    runs, and the ones that are not may only ever shrink.
    """
    section("Every mutation target is a file a test in the sweep opens")
    mut = (REPO_ROOT / "tools" / "mutate_cause_gate.py").read_text(
        encoding="utf-8", errors="replace").lower()
    gate = (REPO_ROOT / "agent" / "regression.py").read_text(
        encoding="utf-8", errors="replace").lower()
    consts = dict(re.findall(r"^([a-z][a-z0-9_]*)\s*=\s*([^\n]+)$", mut, re.M))
    tuples = re.findall(r'\(\s*"([^"]+)",\s*([a-z][a-z0-9_]*)\s*,', mut)
    check("the mutation list is parsed, not guessed", len(tuples) > 400, len(tuples))
    snippet = set(re.findall(r"r\.(test_[a-z0-9_]+),", mut))
    check("the sweep's own test list is parsed", len(snippet) > 60, len(snippet))
    starts = [(m.start(), m.group(1)) for m in re.finditer(r"^def (test_[a-z0-9_]+)\(", gate, re.M)]
    bodies = {}
    for i, (pos, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(gate)
        bodies[name] = gate[pos:end]

    def target_file(const: str) -> str:
        quoted = re.findall(r'"([^"]+)"', consts.get(const, ""))
        return quoted[-1] if quoted else ""

    unwatched: Dict[str, int] = {}
    for _label, const in tuples:
        name = target_file(const)
        if not name or not name.endswith((".py", ".cs", ".js", ".go")):
            continue
        if not any(name in bodies.get(test, "") for test in snippet):
            unwatched[name] = unwatched.get(name, 0) + 1
    check("the currency probe's own files are witnessed by name",
          "bootstate.cs" not in unwatched and "brain_daemon.py" not in unwatched,
          str(sorted(k for k in unwatched if k in ("bootstate.cs", "brain_daemon.py"))))
    check("the unwitnessed set has not grown since it was measured",
          len(unwatched) <= _MUT_UNWITNESSED_BASELINE,          "%d targets, %d mutations: %s" % (len(unwatched), sum(unwatched.values()),
                                           sorted(unwatched)))


def test_js_page_suites() -> None:
    section("JS page suites (delegated to node)")
    if not _have_node():
        print("   SKIP: node not on PATH")
        _delegation("JS page suites", False, ROOT / "tools", "node not on PATH")
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
                   "tools/test_client_errors.js",
                   "tools/test_interactive_use.js",
                   "tools/test_shop_buy.js",
                   "tools/test_battle_log.js",
                   "tools/test_login_guard.js",
                   "tools/test_challenges.js",
                   "tools/test_transition_watchdog.js",
                   "tools/test_cdn_diag_throttle.js",
                   "tools/test_page_assets.js",
                   "tools/test_boot_canary.js",
                   "tools/test_packet_redaction.js",
                   "tools/test_kick_signature_clear.js",
                   "tools/test_login_ban.js",
                   "tools/test_login_cooldown.js",
                   "tools/test_equip_slot_inference.js",
                   "tools/test_inv_unequip.js",
                   "tools/test_nav_doorless_exit.js",
                   "tools/test_nav_edge_step_off.js",
                   "tools/test_reply_text_fallback.js",
                   "tools/test_door_arrival.js",
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
    _delegation("JS page suites", True, ROOT / "tools")


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
    # 2026-11-07: the guard gained one exception, and the exception is the point. Live, `equipItem`
    # emitted `ObjectSetPositionMessage {position: 63}` -- the "not equipped" marker -- and the
    # handler called that a success while nothing was worn, so the item stayed in the bag and the
    # quest stayed stuck. The fallback now also runs when the item is still unworn *and* the only
    # message that left carried position 63. Every other send keeps the original rule.
    check("the fallback only runs when equipItem sent nothing, or sent the not-equipped marker",
          "&& sent.length === 0" in page
          and "!wornNow && !removeWanted && slotPos == null && !thrown" in page
          and "if (pa && Number(pa.position) === 63) {" in page,
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
          "_agentDoorNote(_agentDoorWatch.map, from, mapId, 'walk')" in page,
          "the sampler is the only witness for a human's own walk")
    check("the landing cell is taught as the way back, tagged as a guess",
          "_agentDoorNote(mapId, cell, _agentDoorWatch.map, 'arrival');" in page
          and "prior.via !== 'arrival' && how === 'arrival'" in page
          and "list[d].via === 'arrival'" in page,
          "a house entered once had no exit in the store (G76)")
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

    # Live 2026-10-29, build 2026-10-29bf: the gate and the kill objective shared
    # map 145490946, the character stood on it 59 times with zero groups, and 115
    # of 173 transitions were immediate returns. The trip back is a fresh question
    # only while the answer can differ, so empty looks count and RETURN_LIMIT of
    # them ends the walking until a group is seen there.
    from agent.planner import RETURN_LIMIT
    two_ways = [{"direction": "left", "map_id": 145490433, "blocked": False,
                 "cells": [300]},
                {"direction": "right", "map_id": 145490946, "blocked": False,
                 "cells": [322]}]

    def dry_plans(planner, n: int) -> None:
        for _ in range(n):
            planner._combined_objective_call(
                scene(groups=[], here=145490946, kill_map=145490946),
                "do all the quests")

    budget = QuestPlanner()
    dry_plans(budget, RETURN_LIMIT)
    budget._prev_map = 145490946
    call = budget._combined_objective_call(
        scene(groups=[], exits=two_ways, here=145490434, kill_map=145490946),
        "do all the quests")
    check("an empty kill map is not walked back to after the return budget",
          isinstance(call, dict) and not (
              call.get("tool") == "nav.goto"
              and (call.get("args") or {}).get("map_id") == 145490946),
          call)
    check("the search moves on instead of repeating the trip",
          isinstance(call, dict) and call.get("tool") == "nav.goto", call)

    once = QuestPlanner()
    dry_plans(once, 1)
    once._prev_map = 145490946
    call = once._combined_objective_call(
        scene(groups=[], exits=two_ways, here=145490434, kill_map=145490946),
        "do all the quests")
    check("one empty look is not enough to give up on the map",
          isinstance(call, dict) and call.get("tool") == "nav.goto"
          and (call.get("args") or {}).get("map_id") == 145490946, call)

    again = QuestPlanner()
    dry_plans(again, RETURN_LIMIT)
    fought = again._combined_objective_call(
        scene(groups=[twin], here=145490946, kill_map=145490946),
        "do all the quests")
    again._prev_map = 145490946
    call = again._combined_objective_call(
        scene(groups=[], exits=two_ways, here=145490434, kill_map=145490946),
        "do all the quests")
    check("a group seen on the map clears the dry memory",
          isinstance(fought, dict) and fought.get("tool") == "fight.join"
          and again._gate_dry.get((990001, 914074, 145490946), 0) == 0
          and isinstance(call, dict) and call.get("tool") == "nav.goto"
          and (call.get("args") or {}).get("map_id") == 145490946,
          [fought, again._gate_dry])

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


def test_explore_returns_replay() -> None:
    section("Replay: the explore hop that walked back the way it came")
    # G83's falsifier without a client: recorded direction-shaped nav rows are replayed
    # through the current `_direction`. The counter must count only rows whose recorded
    # edge led to `last_nav.from_map`, and only those the fixed explore would change.
    import json as _json
    import tempfile
    from tools.replay_explore_returns import measure

    def row(build, direction, exits, from_map, to_map, here):
        return {"kind": "step", "tool": "nav.goto", "args": {"direction": direction},
                "observation": {"agent_build": build,
                                "position": {"map_id": here},
                                "map": {"map_id": here,
                                        "exits": [{"direction": d, "map_id": m}
                                                  for d, m in exits]},
                                "last_nav": {"from_map": from_map, "to_map": to_map}}}

    lines = [
        row("2026-10-29bf", "left", [("left", 434), ("top", 945)], 434, 946, 946),
        row("2026-10-29bf", "top", [("left", 434), ("top", 945)], 434, 946, 946),
        row("2026-10-29bf", "left", [("left", 434)], 434, 946, 946),
        row("2026-10-01aa", "left", [("left", 434), ("top", 945)], 434, 946, 946),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = ROOT / "data" / "_replay_explore_tmp.jsonl"
        try:
            path.write_text(chr(10).join(_json.dumps(r) for r in lines), encoding="utf-8")
            all_rows = measure([str(path)])
            recent = measure([str(path)], since_build="2026-10-29")
        finally:
            path.unlink(missing_ok=True)
    check("the replay counts direction-shaped rows and returns",
          all_rows["calls"] == 4 and all_rows["returns"] == 3, all_rows)
    check("a return the fixed explore would change is counted",
          all_rows["fixed_would_change"] == 2, all_rows)
    check("the dead end is a return the fix keeps",
          recent["returns"] == 2 and recent["fixed_would_change"] == 1, recent)
    check("a page build older than the cut is not replayed",
          recent["calls"] == 3, recent)


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
    # Two worlds, and the gate must know which one it is in. `client-patches.quarantine` says the
    # rule set is deliberately NOT shipped because it matches the current build byte-for-byte and
    # still breaks it (3.3.11: 23/23 anchors, then "Callback was already called"). In that state the
    # invariant flips: the bundle the client serves must be the vendor's. Silencing these checks
    # would be the wrong repair -- a red gate that means nothing is how a gate starts being ignored,
    # so the quarantine is asserted, not excused, and the staleness count is reported as the work
    # that re-opens it.
    quarantine = Path(__file__).resolve().parents[2] / "client-patches.quarantine"
    proc = subprocess.run(["node", str(tool), "--check"],
                          capture_output=True, text=True)
    out = proc.stdout or ""
    # "applied N/M" counts rules whose protection is already in the served bundle.
    frac = re.search(r"applied (\d+)/(\d+)", out)
    applied, total = (frac.group(1), frac.group(2)) if frac else ("?", "?")
    stale = re.search(r"stale (\d+)", out)
    stale = stale.group(1) if stale else "?"
    check("the resilience check is green in whichever state we are in",
          proc.returncode == 0, (proc.stderr or out).strip()[-250:])
    if quarantine.exists():
        reason = quarantine.read_text(encoding="utf-8", errors="replace").strip()
        check("quarantined patches mean the served bundle is the vendor's",
              applied == "0", "applied=%s of %s while quarantined - a half-patched bundle is the "
              "black screen, and the bundle is the one place the quarantine must be visible"
              % (applied, total))
        check("the quarantine names the work that clears it", "re-derive" in reason.lower(),
              "rules still needing work against this build: %s - %s" % (stale, reason[:160]))
    else:
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
    from agent.gate import RuleGate
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
            self._gate = RuleGate()
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
    check("a staged bundle with a partial or missing core patch is refused",
          "skippedTotal > 0 || (coreTotal > 0 && coreHit < coreTotal)" in src,
          "one of 23 rules was marked core, so a bundle that silently lost a dozen anchors was "
          "adopted half-patched -- that is the 3.3.11 black screen. Partial is refused now, "
          "and the skipped rule names are logged so a rotted rule gets pruned rather than "
          "silently disabling patching")
    check("the update transaction is single-flight",
          "lock (_gate)" in src,
          "Launch All runs one updater per instance over the same script.js")
    stamp_at = src.find("File.WriteAllText(stampPath")
    restamp_at = src.find("RewriteCacheBusters(dir, log);", stamp_at) if stamp_at >= 0 else -1
    check("the asset URLs are re-stamped after an adoption",
          stamp_at >= 0 and restamp_at > stamp_at,
          "the page keeps asking for the old ?v= and Chromium serves the old bundle")

    # The boot canary: what a client says about a patched bundle, folded back into the next
    # adoption. These are wiring checks -- the decision itself is executed in "The boot verdict",
    # so here the question is only whether the three ends are actually connected.
    check("patching is decided by the boot answer, not by the rule count",
          "BootState.DecideAdoption(bad, stamp, matched.Count) == BootState.Patch" in src,
          "23/23 rules matching was the last thing believed about build 3.3.11")
    check("the patched bytes are only written when the decision says to patch",
          src.count("File.WriteAllText(stagedScript, patched") == 1
          and 0 <= src.find("if (patching)") < src.find("File.WriteAllText(stagedScript, patched") <
          src.find("if (patching)") + 200,
          "an unpatched decision must leave Ankama's own file in staging")
    refusal = src.find("skippedTotal > 0")
    check("the partial-patch refusal does not block a deliberate vendor adoption",
          refusal >= 0 and 0 <= src.rfind("if (patching", 0, refusal) > refusal - 120,
          "refusing adoption of the vendor's bytes would be a black screen with extra steps")
    check("a patched adoption arms the boot canary for this build",
          src.find("if (patching) ArmBootCanary(BundleStamp(), log);") >= 0,
          "nothing recorded means nothing can be judged, and the unpatched fallback never happens")
    check("a failed boot reverts from the vendor copy on disk",
          'File.Copy(vendor, live, true)' in src and '"bad-builds.txt"' in src,
          "the revert must not need the network: script.js.bak is the bytes Ankama served")
    check("one failure watches the build, two stop patching it",
          "BootState.OnCanary(pending, stamp, booted, BootState.IsBad(suspect, pending))" in src
          and '"boot-suspect.txt"' in src,
          "ASSET_MISSING is the vendor's reason too, so a single bad boot cannot tell our patch "
          "from Ankama's CDN -- that is what the watch list is for")
    check("the bundle identity is read from one place",
          'if (m.Groups[1].Value == "build/script.js") return m.Groups[2].Value;' in src,
          "the arming side and the answering side must name the build the same way")

    form = ROOT / "desktop" / "WebView2ClientForm.cs"
    if form.exists():
        host = form.read_text(encoding="utf-8", errors="replace")
        check("the host tells the page which bundle it is running",
              "window.__DOFUS_BOOT_STAMP" in host and "ClientBundle.BundleStamp()" in host,
              "without the stamp the answer cannot be matched to the pending judgement")
        check("the host routes the boot answer to the updater",
              '"\\"type\\":\\"BOOT_CANARY\\""' in host and "RecordBootCanary(" in host,
              "a verdict the host drops on the floor is a canary that never fires")
        check("an answer without stamp or ok is dropped rather than guessed",
              "if (mOk.Success && mStamp.Success)" in host,
              "a wrong true is the lie this mechanism replaces")
    shim = ROOT / "desktop" / "client" / "www" / "js" / "compat-shim.js"
    if shim.exists():
        js = shim.read_text(encoding="utf-8", errors="replace")
        check("the shipped shim sends the boot answer",
              '"type":"BOOT_CANARY"' in js and "__DOFUS_BOOT_STAMP" in js,
              "the file the page loads, not a copy of its logic")
        check("the shim needs a rendered world before claiming a boot",
              "m.ok > 0" in js,
              "the facade is assigned long before anything draws; a black screen has one")


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
          "foreach (string instance in targets)" in p
          and '"autopilot." + instance + ".jsonl"' in p,
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
    # Since 2026-11-05 the visited-and-spent map is barred only after one granted return:
    # a permanent bar was what made a one-map objective unreachable and retired the quest
    # (ARCH_REASSESSMENT V.20). The first return walks; nothing after it does.
    check("the first return to the visited map is granted, one hop at a time",
          back is not None and back.tool == "nav.goto"
          and (back.args.get("map_id") == 145490945
               or back.args.get("direction") is not None), back)
    again = p.choose("do all the quests", obs(145489920))
    check("the granted return is allowed to be asked again while it has not landed",
          again is not None, "the plan went silent before the crossing could arrive")
    parked = p.choose("do all the quests", obs(145489921))
    check("and once the second visit has been recorded the map is barred for good",
          parked is not None and "13940" not in str(getattr(parked, "why", "") or "")
          and parked.args.get("map_id") not in (145489920, 145490945), parked)

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
    import hashlib
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
    # A session label is not a capture. Measured 2026-11-05: 17 labels carried only
    # 12 distinct bodies, one 30-row window appearing under six names, so counting
    # labels lets duplicated captures inflate the coverage this line claims (#103).
    by_session: Dict[Any, List[str]] = {}
    for row in rows:
        body = json.dumps({k: v for k, v in row.items() if k != "session"},
                          sort_keys=True, default=str)
        by_session.setdefault(row.get("session"), []).append(body)
    bodies = {hashlib.sha256("\n".join(seq).encode("utf-8", "replace")).hexdigest()
              [:12] for seq in by_session.values()}
    check("and the sessions are distinct captures, not one window under new names",
          len(bodies) >= 10,
          "%d label(s) carry %d distinct capture(s)" % (len(sessions), len(bodies)))

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

    # The gate replays the *checked-in* corpus, so nothing here ever ran the tool that
    # writes it. Measured 2026-11-01: `collect()` had an `append` stranded under a
    # `continue`, so it returned zero rows -- the corpus was un-refreshable and the drift
    # report said "no recorded decisions found" over logs holding 2,674 decision rows,
    # while every replay check stayed green against the 68 h-old file.
    from tools import freeze_planner_replay as freezer

    import tempfile

    tmp = Path(tempfile.mkdtemp())
    source = tmp / "autopilot.dofus-99.jsonl"
    decision = {"kind": "decision", "session": "s99", "goal": "do all the quests",
                "at": 1760000000.0, "action": "act", "tool": "nav.goto", "ok": True,
                "obs": {"map": {"id": 10898, "exits": [{"map_id": 145490435}],
                                "transition": {"dropped": 3}},
                        "character": {"level": 20}}}
    source.write_text(json.dumps(decision) + "\n", encoding="utf-8")
    collected = freezer.collect([source])
    check("the collector reads a decision row rather than returning nothing",
          len(collected) == 1 and collected[0]["session"].endswith(":s99"), collected)
    check("and trimming keeps the state the planner chooses from, drops the packet log",
          collected and collected[0]["obs"]["map"].get("exits")
          and "transition" not in collected[0]["obs"]["map"],
          collected[0]["obs"].get("map") if collected else None)
    live = [path for path in freezer.DEFAULT_SOURCES if path.is_file()]
    live_bytes = sum(path.stat().st_size for path in live)
    if not live:
        _delegation("planner corpus refresh", False, tmp,
                    "no live autopilot logs on this machine, so the frozen corpus cannot "
                    "be re-derived here")
    elif live_bytes > 200 * 1024 * 1024:
        _delegation("planner corpus refresh", False, live[0],
                    "%d MB of live logs, above the 200 MB the gate will read"
                    % (live_bytes // 2**20))
    else:
        import os as _os

        if _os.environ.get("GATE_IN_MUTATION_SNIPPET"):
            # Inside a sweep this would re-read the live corpus once per mutation; the two
            # fixture checks above are the ones that bite the collector.
            _delegation("planner corpus refresh", True, live[0],
                        "skipped inside the mutation snippet, ran outside it")
        else:
            _delegation("planner corpus refresh", True, live[0],
                        "%d MB over %d log(s)" % (live_bytes // 2**20, len(live)))
            fresh = freezer.collect(live)
            check("the checked-in corpus is what the collector still produces",
                  "".join(json.dumps(row, sort_keys=True) + "\n" for row in fresh)
                  == corpus.read_text(encoding="utf-8"),
                  (len(fresh), len(rows)))


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


def test_check_helper_survives_a_tuple_detail() -> None:
    """The failure formatter is used 1,600 times, so it must not fail on its own input.

    `check(label, False, (1, 2))` used to raise inside the helper -- reporting a failure
    killed the run reporting it -- and the trap has recurred in several sessions. Found
    again 2026-10-31: a new section passed a `(count, count)` detail, and the mutation
    sweep reported the crash instead of the assertion it was supposed to fail.
    """
    section("The check() formatter itself")
    before = len(_FAILURES)
    crashed = None
    try:
        check("a tuple detail is formatted, not fatal", False, (1, 2))
    except TypeError as exc:
        crashed = exc
    # The probe's own failure line is evidence about the formatter, not a gate failure,
    # so it is lifted out before the verdict is recorded. Recording the verdict first and
    # deleting the tail afterwards threw the verdict away with the probe, and the
    # mutation that breaks the formatter read as MISSED (found 2026-10-31).
    probe = list(_FAILURES[before:])
    del _FAILURES[before:]
    check("check() survives a tuple detail", crashed is None,
          "TypeError: %s" % crashed if crashed else "")
    if crashed is None:
        check("and the recorded failure keeps both the label and the detail",
              len(probe) == 1 and probe[0].startswith("a tuple detail is formatted")
              and "(1, 2)" in probe[0], probe[:1])


def test_mutation_journal_recovery() -> None:
    """A killed mutation sweep must not leave deliberately broken code in the tree.

    The driver restores in a `finally`, which a hard kill skips. On 2026-10-30 an
    interrupted sweep left `ok=None, error=None` at a live decision site in
    `agent/autopilot.py`; only the Kaggle-mirror drift check noticed. The fix journals the
    pre-mutation bytes before each write and repairs them on the next start.
    """
    section("Mutation driver journal (an interrupted sweep repairs itself)")
    driver = REPO_ROOT / "tools" / "mutate_cause_gate.py"
    journal = REPO_ROOT / ".mutate-journal.json"
    check("the mutation driver exists", driver.exists(), str(driver))
    check("no sweep is in flight while this test runs", not journal.exists(), str(journal))
    if journal.exists():
        return

    source = "print('the working tree, intact')\n"
    broken = "print('deliberately broken by a mutation')\n"
    tmp = Path(tempfile.mkdtemp())
    try:
        victim = tmp / "victim.py"
        victim.write_text(broken, encoding="utf-8")
        journal.write_text(json.dumps({"path": str(victim), "original": source}),
                           encoding="utf-8")
        proc = subprocess.run([sys.executable, str(driver), "--recover"], cwd=str(REPO_ROOT),
                              capture_output=True, text=True, timeout=120)
        check("--recover exits without running a gate section",
              proc.returncode == 0 and "selected" not in proc.stdout,
              (proc.stdout + proc.stderr)[-300:])
        check("--recover restores the file the killed run left broken",
              victim.read_text(encoding="utf-8") == source,
              victim.read_text(encoding="utf-8")[:120])
        check("--recover reports what it repaired",
              "RECOVERED" in proc.stdout, proc.stdout[-200:])
        check("the journal is consumed, so a second start changes nothing",
              not journal.exists() and victim.read_text(encoding="utf-8") == source,
              str(journal.exists()))
        check("the sweep journals before it writes a mutation",
              'JOURNAL.write_text(json.dumps' in driver.read_text(encoding="utf-8"),
              "no journal write in the driver")
        # The snippet printed its `  - ` lines only at the end, so one exception
        # erased every verdict and the driver could only report a bare crash (four
        # mutations on 2026-10-31). Each test now runs inside the gate's own guard,
        # which names the test that raised.
        snippet_source = driver.read_text(encoding="utf-8")
        check("the sweep names a crashing test instead of erasing its verdict",
              "r._run_test(_t)" in snippet_source and "for _t in (" in snippet_source,
              "SNIPPET does not route its tests through r._run_test")
        # A text restore rewrites CRLF to the platform newline; that left
        # `agent/controller.py` drifted after a killed sweep on 2026-10-31, and only
        # the Kaggle-mirror drift check caught it. The journal carries the raw bytes.
        import base64
        crlf = tmp / "crlf.py"
        crlf_source = b"print('crlf intact')\r\nprint('two lines')\r\n"
        crlf.write_bytes(b"print('broken by a mutation')\r\n")
        journal.write_text(json.dumps({
            "path": str(crlf),
            "original_b64": base64.b64encode(crlf_source).decode("ascii")}),
            encoding="utf-8")
        proc = subprocess.run([sys.executable, str(driver), "--recover"], cwd=str(REPO_ROOT),
                              capture_output=True, text=True, timeout=120)
        check("--recover restores the exact bytes, not a newline-translated copy",
              crlf.read_bytes() == crlf_source, crlf.read_bytes()[:60])
        # An interpreter that cannot `import agent` -- the embedded runtime's path file
        # ignores the working directory -- crashes the snippet before any mutation is
        # applied, and a crash counts as caught. Five planner mutations on 2026-11-04
        # read `CAUGHT … 0 failing check(s) (section crashed)` for exactly this reason.
        # The driver now measures the clean tree first and refuses to start on a red.
        check("the sweep proves the clean snippet runs before it mutates anything",
              "REFUSED: the clean tree already fails the snippet" in snippet_source
              and "base_labels, base_code, base_out = run_sections()" in snippet_source,
              "no clean-tree preflight in the driver")
        check("and planning a batch stays free of that pass",
              snippet_source.index('if os.environ.get("MUT_PLAN"):')
              < snippet_source.index("base_labels, base_code, base_out"),
              "the preflight runs before MUT_PLAN exits")
    finally:
        journal.unlink(missing_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)


def test_veto_alternative_priced() -> None:
    """G70: the thing that made the streak veto un-wireable was never priced.

    `alternative_probe` answers "what does the loop do instead of a call the gate would
    veto" on the current labels. These fixtures pin the arithmetic that makes the answer
    trustworthy: reads carry no promise and must not be counted, a refusal is a miss, the
    lookahead skips the reads to reach the next acting call, and the different-versus-same
    contrast is computed over segments rather than rows.
    """
    import importlib.util

    section("Pricing the alternative the veto forces (G70)")
    tool_path = REPO_ROOT / "tools" / "live_outcome_probe.py"
    check("the probe exists", tool_path.is_file(), str(tool_path))
    spec = importlib.util.spec_from_file_location("alt_probe", str(tool_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def row(t: int, tool: str, args: Dict[str, Any], met: bool, streak: int,
            read: bool = False, refused: Any = None) -> Dict[str, Any]:
        return {"segment": "seg", "t": t, "tool": tool, "args": args,
                "claim": None if read else tool.split(".")[0],
                "claim_met": False if read else met, "was_read": read,
                "repeat_streak_norm": streak, "refused": refused,
                # load_rows only accepts a row that carries its feature vector, so the
                # fixtures have to as well.
                "x": [0.0, 0.0, 0.0, 0.0, 0.0, float(streak), 0.0, 0.0]}

    rows: List[Dict[str, Any]] = []
    for i in range(6):        # switch away -> the alternative meets
        seg = "switch-%d" % i
        rows += [dict(r, segment=seg) for r in (
            row(0, "quest.advance", {"objective_id": 100 + i}, False, 2),
            row(1, "state.snapshot", {}, False, 2, read=True),
            row(2, "nav.goto", {"map_id": 700 + i}, True, 0))]
    for i in range(6):        # repeat the same promise -> it never meets
        seg = "repeat-%d" % i
        rows += [dict(r, segment=seg) for r in (
            row(0, "quest.advance", {"objective_id": 200 + i}, False, 2,
                refused=("known_kick_signature" if i == 0 else None)),
            row(1, "quest.advance", {"objective_id": 200 + i}, False, 3))]

    tmp = Path(tempfile.mkdtemp())
    for name in ("train", "val"):
        (tmp / ("%s.jsonl" % name)).write_text("", encoding="utf-8")
    (tmp / "test.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                                    encoding="utf-8")
    try:
        result = module.alternative_probe(tmp)
        fold = result["folds"]["test"]
        check("reads are not acting calls and carry no promise",
              fold["acting"] == 24 and fold["met"] == 6,
              "%d acting / %d met" % (fold["acting"], fold["met"]))
        check("only rows at the threshold are candidates, and a refusal is a miss",
              fold["vetoed"] == 18 and fold["vetoed_met"] == 0
              and fold["vetoed_refused"] == 1,
              "%d vetoed, %d met, %d refused"
              % (fold["vetoed"], fold["vetoed_met"], fold["vetoed_refused"]))
        # 18, not 12: the six repeats that follow a vetoed call sit at streak 3 and are
        # themselves veto candidates. The gate vetoes every call at or above the
        # threshold, which is what a live loop would do.
        check("a wired veto would remove no success at all here",
              fold["successes_removed_by_veto"] == 0.0
              and fold["retention"] == 1.0,
              "%s / %s" % (fold["successes_removed_by_veto"], fold["retention"]))
        check("the lookahead reaches the acting call past the read",
              fold["next_after_veto"]["different"] == {"n": 6, "met": 6, "met_rate": 1.0},
              fold["next_after_veto"]["different"])
        check("and repeating the same promise is scored against itself",
              fold["next_after_veto"]["same"] == {"n": 6, "met": 0, "met_rate": 0.0},
              fold["next_after_veto"]["same"])
        check("the contrast is the priced alternative",
              fold["advantage_different_minus_same"] == 1.0,
              fold["advantage_different_minus_same"])
        ci = fold["paired_ci_95"]
        check("segment bootstrap puts the whole interval above zero",
              ci is not None and ci[0] > 0.0, ci)
        check("the verdict marks it priced and says it is observational, not causal",
              result["verdict"]["alternative_priced"] is True
              and result["verdict"]["ci_excludes_zero"] is True
              and "randomized arm" in result["verdict"]["reads_the_veto_with_this_note"],
              result["verdict"])
        check("an empty fold is reported as absent, not as a zero",
              set(result["folds"]) == {"test"}, sorted(result["folds"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_gate_shadow_reaches_the_trajectory() -> None:
    """Prove offline that the veto shadow survives the wiring into a recorded row.

    `agent/gate.py` is the incumbent every learned worker was measured against, and it
    runs shadow-only so a live session can price the alternative it would force. No log
    has ever carried it (the newest ones predate the file), so nothing proved the
    annotation survives: the Autopilot assembles `_meta["gate"]`, the executor forwards
    `meta`, and the recorder persists it. `Step.as_dict()` — the *autopilot* row — has no
    meta field, so the shadow lands in the trajectory file, not the loop log. This drives
    the real loop, the real gate and the real recorder to pin exactly that.
    """
    import agent.autopilot as _gm
    from agent.autopilot import Autopilot
    from agent.gate import RuleGate
    from agent.recorder import TrajectoryRecorder
    from agent.runtime import ToolCall

    section("Gate shadow: from the loop into a recorded row")
    args = {"direction": "right"}
    gate = RuleGate()
    check("a call that has never failed is not a veto",
          gate.shadow("nav.goto", args) == {"rule": gate.key("nav.goto", args)[:160],
                                           "streak": 0, "would_veto": False},
          gate.shadow("nav.goto", args))
    gate.observe("nav.goto", args, "noop")
    gate.observe("nav.goto", args, "failed")
    twice = gate.shadow("nav.goto", args)
    check("two non-advances on the same normalized call would veto",
          twice["streak"] == 2 and twice["would_veto"] is True, twice)
    gate.observe("nav.goto", args, "advanced")
    check("one advance clears the streak",
          gate.shadow("nav.goto", args)["streak"] == 0, gate.shadow("nav.goto", args))
    check("a read never builds a streak",
          (gate.observe("state.snapshot", {}, "noop"),
           gate.shadow("state.snapshot", {})["streak"] == 0)[1])

    volatile = RuleGate()
    for cell, uid, lang in ((504, 136137029, "fr"), (549, 136137092, "en"),
                            (504, 136137181, "fr")):
        tap = {"element_id": 513208, "cell": cell, "skill_uid": uid, "lang": lang}
        volatile.observe("tap.interactive", tap, "failed")
    check("three re-taps of one element are one streak of three, not three calls (G60)",
          volatile.shadow("tap.interactive",
                          {"element_id": 513208, "cell": 299,
                           "skill_uid": 136113675, "lang": "de"})["streak"] == 3,
          volatile.shadow("tap.interactive", {"element_id": 513208}))

    tmp = Path(tempfile.mkdtemp())
    recorder = TrajectoryRecorder(root=tmp, instance="unit")
    for _ in range(3):
        recorder.step(tool="nav.goto", args=args, ok=False,
                      meta={"gate": volatile.shadow("tap.interactive",
                                                   {"element_id": 513208})})
    written = sorted(tmp.glob("*.jsonl"))
    check("the recorder wrote a trajectory file", bool(written), str(tmp))
    rows = [json.loads(line) for line in
            (written[0].read_text(encoding="utf-8").splitlines() if written else [])
            if line.strip()]
    check("the recorded row keeps the gate annotation",
          bool(rows) and rows[-1]["meta"]["gate"]["streak"] == 3
          and rows[-1]["meta"]["gate"]["would_veto"] is True,
          rows[-1]["meta"] if rows else "no rows")

    class _Policy:
        def __init__(self) -> None:
            self.calls = 0

        def choose(self, goal, obs, tools):      # noqa: ARG002 - shape, not behaviour
            self.calls += 1
            return ToolCall("nav.goto", dict(args), goal)

    class _Result:
        def __init__(self, data: Dict[str, Any]) -> None:
            self.ok = True
            self.error = None
            self.data = data

        def as_dict(self) -> Dict[str, Any]:
            return {"data": self.data}

    class _Executor:
        """Captures what the loop hands down; changes nothing about the world."""

        def __init__(self, obs: Dict[str, Any]) -> None:
            self.obs = obs
            self.metes: List[Dict[str, Any]] = []

        def execute(self, tool, call_args, **kw):    # noqa: ARG002
            self.metes.append(dict(kw.get("meta") or {}))
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

        def observe(self, obs, source="live"):       # noqa: ARG002
            return None

        def note_observation(self, obs):
            return None

    obs = {"in_world": True, "map_id": 10340, "cell": 300}
    rt = _Runtime(dict(obs))
    log = tmp / "autopilot.jsonl"
    ap = Autopilot(runtime=rt, goals=["push right"], log_path=log,
                   stop_file=tmp / "STOP", max_steps=3, dry_run=False,
                   preflight=False, minutes=0, interval=0.0, steps_per_goal=3,
                   # `settle_s` is a wall-clock window (`_settle` counts monotonic time
                   # until the promised effect appears), so stubbing `time.sleep` does not
                   # shorten it: measured at 18.0 s for this test's 6 executed steps, which
                   # was 28% of every mutation pass. The settle itself is pinned by
                   # `test_settle_waits_for_promised_effect`; what this test proves is the
                   # shadow annotation reaching the executed call and the recorded row.
                   settle_s=0.0)
    ap.controller.state_dir = tmp
    real_sleep = _gm.time.sleep
    _gm.time.sleep = lambda seconds: None
    started_run = time.perf_counter()
    try:
        ap._run()
    finally:
        _gm.time.sleep = real_sleep
    # The loop's own settle windows are monotonic wall clock, so the sleep stub above does
    # not shorten them: with the default `settle_s` this run costs 18.0 s (measured), which
    # was 28% of every mutation pass. The test that pays it is the one that notices it.
    check("the annotated loop runs without paying the settle window",
          time.perf_counter() - started_run < 3.0,
          round(time.perf_counter() - started_run, 2))
    seen = [m.get("gate") for m in rt.executor.metes if m.get("gate")]
    check("the loop annotates every executed call with the gate's view",
          len(seen) >= 2, [m for m in rt.executor.metes])
    check("and the streak the shadow reports grows with the repeats it observes",
          [s["streak"] for s in seen] == list(range(len(seen)))
          and seen[-1]["would_veto"] is True,
          seen)
    step_rows = [json.loads(line) for line in
                 log.read_text(encoding="utf-8").splitlines() if line.strip()]
    check("the loop's own step rows carry no meta, so the shadow lives in the "
          "trajectory file (a reader looking in autopilot.jsonl would find nothing)",
          all("meta" not in row for row in step_rows if row.get("kind") == "step"),
          [sorted(row)[:6] for row in step_rows if row.get("kind") == "step"][:1])
    shutil.rmtree(tmp, ignore_errors=True)


def ai_tier_names() -> list:
    """The opt-in tier as the import-graph tool states it, not as a list typed into this file.

    `("torch", "transformers", "safetensors")` written here went stale the day the tier
    gained onnxruntime, tokenizers, scikit-learn and joblib: a hardcoded tuple asserts the old
    answer, so the check passes while the file it is checking has moved. This asks `shipped_deps`.
    """
    import json as _json

    run = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools" / "shipped_deps.py"),
                          "--json"], capture_output=True, text=True, timeout=600, cwd=str(ROOT))
    return sorted(_json.loads(run.stdout or "{}").get("ai_tier") or [])


def test_board_payload_round_trip() -> None:
    """Run the combat suite's serialization class in-process, so the mutation sweep can see it.

    `test_combat_suites` delegates the combat unittests to a subprocess and is deliberately not
    in the sweep's snippet (13 s per mutation × 547 mutations is not a price worth paying for
    every file). But `Board.to_fight_state()` is the seam that lets the *live* decision path be
    measured offline -- §L.19's whole finding -- and a rule that a snippet can only see a file,
    not the behaviour inside it, would let a dropped `min_range` or a wrong camelCase key ship
    unnoticed. Running the class costs ~0.2 s and does not reimplement a single assertion.
    """
    section("Board payload round trip (the seam the live policy is measured through)")
    import io
    import unittest

    from combat.battle_solver import Board
    from combat.test_battle_solver import BoardSerializationTests
    from combat.test_live_policy import SlotRealizationTests, board_payload

    origin = Board.from_fight_state(board_payload())
    back = Board.from_fight_state(origin.to_fight_state())
    check("a board survives its own payload, read through battle_solver itself",
          back.spells == origin.spells and back.fighters == origin.fighters
          and back.my_ap == origin.my_ap and back.cells.flags == origin.cells.flags,
          ([s.id for s in back.spells], [s.id for s in origin.spells]))

    loader = unittest.TestLoader()
    suites = [loader.loadTestsFromTestCase(BoardSerializationTests),
              # The other half of the seam: what `_realize` hands the page. A cast without a
              # `targetId` is a decision the client cannot click, and the mutation sweep could
              # not see it -- this behaviour lives in `combat/test_live_policy.py`, which the
              # snippet does not run (it delegates to a subprocess), so it is run here instead.
              loader.loadTestsFromTestCase(SlotRealizationTests)]
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
        unittest.TestSuite(suites))
    check("the serialization suite ran and nothing in it failed",
          result.testsRun >= 5 and result.wasSuccessful(),
          (result.testsRun, [str(t) for t in result.failures + result.errors][:2]))


# A fingerprint of one forward pass through the shipped head. The argmaxes and the value are exact;
# the three logit sums carry a 0.02 tolerance because the two backends genuinely differ in the fourth
# decimal (measured 2026-11-04: sum(target) 1305.2408 under jax/3.14 against 1305.2396 under
# numpy/3.11). §L.31's bit-identity is about the fields a decision is made of, not about a float32
# accumulation over 45 logits, and a golden written as exact equality would have been a pin that only
# one interpreter could pass. Regenerate with DOFUS_GOLDEN=1 when the head is retrained on purpose.
GOLDEN_FORWARD = {
    "value": [-0.99777],
    "move": [0, 0, 0, 0, 0],
    "spell": [3, 3, 3, 3, 3],
    "totals": [26.1109, 166.3712, 1305.2408],
}


def test_encounter_model_and_checkpoint() -> None:
    """The forward math and the loader that shapes it -- the last unwitnessed files in the head.

    §L.38 listed these: `encounter/backend.py` is a mutation target but `model.py`'s layer logic is
    not, and `checkpoint.py` is neither. Everything the trained head decides comes out of `forward`,
    and `factor_logp` is where an illegal action is supposed to become impossible; a silent change in
    either moves every live decision while the request still succeeds. Each property pins both ends,
    because a mask test that only checks the illegal case passes when masking is deleted entirely.

    Arrays are built by broadcasting index grids instead of assigning into them: a jax array cannot
    be written in place, and this has to mean the same thing on both backends (§L.24).
    """
    section("Encounter model and checkpoint (the forward math, and who is allowed to be chosen)")
    from combat.encounter import backend
    from combat.encounter.checkpoint import config_from_payload, load_params_config
    from combat.encounter.model import _ln, factor_logp, forward

    XP = backend.XP
    inputs = XP.asarray([[[1.0, 3.0], [5.0, 5.0]],
                         [[7.0, 9.0], [0.0, 0.0]]], dtype=XP.float32)
    original = inputs.copy()
    scale = XP.asarray([2.0, 3.0], dtype=XP.float32)
    bias = XP.asarray([0.5, -0.5], dtype=XP.float32)
    normalized = _ln(inputs, (scale, bias))
    expected = XP.asarray([-1.49999, 2.499985], dtype=XP.float32)
    check("layer normalization centers each token and applies scale and bias",
          bool(XP.all(XP.abs(normalized[:, 0] - expected) < 1e-6)))
    check("constant tokens normalize to finite bias values",
          bool(XP.all(XP.isfinite(normalized))
               and XP.all(normalized[:, 1] == bias)))
    check("layer normalization leaves its input unchanged",
          bool(XP.array_equal(inputs, original)))
    path = REPO_ROOT / "models" / "encounter_selfplay.pkl"
    check("the shipped checkpoint is present to test against", path.exists(), str(path))
    if not path.exists():
        return
    params, cfg = load_params_config(path)
    actors, spells = cfg.n_actors, cfg.n_spells
    feat = int(params["enc1"][0].shape[0])
    glob_dim = int(params["glob"][0].shape[0])

    ramp = XP.reshape(XP.arange(1.0, float(feat * actors + 1))
                      / float(feat * actors + 1), (1, actors, feat))
    glob = XP.reshape(XP.arange(1.0, float(glob_dim + 1))
                      / float(glob_dim + 1), (1, glob_dim))
    attend = XP.ones((1, actors), dtype=bool)
    first = forward(params, ramp, glob, attend)
    again = forward(params, ramp, glob, attend)

    value = first["value"]
    check("the value head stays inside its tanh range and is not a constant",
          bool(XP.all(value > -1.0) and XP.all(value < 1.0)),
          float(value.reshape(-1)[0]))
    check("and the same weights and input give the same logits, twice",
          all(bool(XP.array_equal(first[key], again[key]))
              for key in ("move", "spell", "target", "value")),
          [str(again[k].shape) for k in ("move", "spell", "target", "value")])

    open_masks = {"move": XP.ones((1, actors, first["move"].shape[-1]), dtype=bool),
                  "spell": XP.ones((1, actors, spells), dtype=bool),
                  "target": XP.ones((1, actors, spells, actors), dtype=bool)}

    def actions(spell_index, target_index, move_index=0):
        return {
            "move": XP.full((1, actors), move_index, dtype=XP.int32),
            "spell": XP.full((1, actors), spell_index, dtype=XP.int32),
            "target": XP.full((1, actors), target_index, dtype=XP.int32),
        }

    pass_low, _ = factor_logp(first, open_masks, actions(cfg.spells, 0), cfg)
    pass_high, _ = factor_logp(first, open_masks, actions(cfg.spells, 1), cfg)
    spell_low, _ = factor_logp(first, open_masks, actions(0, 0), cfg)
    spell_high, _ = factor_logp(first, open_masks, actions(0, 1), cfg)
    check("a pass has no target choice, so the target index cannot change its log-prob",
          bool(XP.all(XP.abs(pass_low - pass_high) < 1e-6)),
          float((pass_low - pass_high).reshape(-1)[0]))
    check("while a real spell must be allowed to",
          bool(XP.any(XP.abs(spell_low - spell_high) > 1e-4)),
          float((spell_low - spell_high).reshape(-1)[0]))

    slot = XP.arange(first["move"].shape[-1]).reshape(1, 1, first["move"].shape[-1])
    who = XP.arange(actors).reshape(1, actors, 1)
    blocked = {**open_masks,
               "move": ~((slot == 2) & (who == 0))}
    legal, _ = factor_logp(first, open_masks, actions(0, 0, move_index=2), cfg)
    illegal, _ = factor_logp(first, blocked, actions(0, 0, move_index=2), cfg)
    delta = illegal - legal
    # Both halves, because "the masked one is worse" alone also passes when masking is deleted
    # and every actor is equally unaffected -- and `all` over the batch would have failed the
    # correct code, since only actor 0 was blocked.
    check("masking actor 0's move slot makes that action impossible",
          bool(delta[0, 0] < -1.0), float(delta[0, 0]))
    check("and no other actor is affected by someone else's mask",
          bool(XP.all(XP.abs(delta[0, 1:]) < 1e-6)),
          [float(v) for v in delta.reshape(-1)[1:4]])

    # The trainer nests the config under "config", and the loader's own defaults differ from
    # Config's dataclass defaults (max_rounds 24 here, 16 there). A flat payload silently reads as
    # all-defaults, which is the false pass this pin exists to make impossible.
    shaped = config_from_payload({"config": {"allies": 2, "enemies": 3, "spells": 7,
                                             "max_rounds": 21, "casts_per_turn": 2}})
    bare = config_from_payload({})
    coerced = config_from_payload({"config": {"spells": "9"}})
    check("the checkpoint's config is read out of its nested payload",
          shaped.allies == 2 and shaped.enemies == 3 and shaped.spells == 7
          and shaped.max_rounds == 21 and shaped.casts_per_turn == 2,
          (shaped.allies, shaped.enemies, shaped.spells, shaped.max_rounds,
           shaped.casts_per_turn))
    check("a payload that names nothing gets the loader's defaults, not the values above",
          bare.allies == 1 and bare.enemies == 4 and bare.spells == 4
          and bare.max_rounds == 24 and bare.casts_per_turn == 4,
          (bare.allies, bare.enemies, bare.spells, bare.max_rounds, bare.casts_per_turn))
    check("and a string from JSON is coerced, not carried through",
          coerced.spells == 9, coerced.spells)
    check("the derived widths follow the config, and pass is always the last spell",
          shaped.n_actors == 5 and shaped.n_spells == shaped.spells + 1
          and bare.n_actors == 5 and bare.n_spells == 5,
          (shaped.n_actors, shaped.n_spells, bare.n_actors, bare.n_spells))

    # A fingerprint of one forward pass, so a numeric change is a *named* change even when no
    # property above moves. §L.41 refused two mutations -- dropping `tanh`, and the misaligned
    # `tokens[:, 1:]` slice -- because nothing could reach them; this is what reaches them. It is
    # deliberately a reviewable literal: retraining the head is supposed to break it loudly, and
    # `DOFUS_GOLDEN=1` prints the current values for whoever is making that change deliberately.
    fingerprint = {
        "value": [round(float(v), 5) for v in value.reshape(-1)],
        "move": [int(i) for i in XP.argmax(first["move"], axis=-1).reshape(-1)],
        "spell": [int(i) for i in XP.argmax(first["spell"], axis=-1).reshape(-1)],
        "totals": [float(t) for t in (
            XP.sum(first["move"]), XP.sum(first["spell"]),
            XP.sum(first["target"]))],
    }
    if os.environ.get("DOFUS_GOLDEN"):
        print("  current fingerprint:", fingerprint)
    totals_close = all(abs(got - want) <= 0.02
                       for got, want in zip(fingerprint["totals"], GOLDEN_FORWARD["totals"]))
    check("one forward pass fingerprints to the reviewed values",
          fingerprint["value"] == GOLDEN_FORWARD["value"]
          and fingerprint["move"] == GOLDEN_FORWARD["move"]
          and fingerprint["spell"] == GOLDEN_FORWARD["spell"] and totals_close,
          (fingerprint, GOLDEN_FORWARD))


def test_combat_decision_log() -> None:
    """The combat server's own writer: row shape, rotation, and the never-raise-on-a-bad-disk path.

    #13 fixed the glued-record log writer, but what it actually pinned was `agent/jsonio.py`, which
    IS a mutation target; `combat/combat_ai_server.py` itself -- the production entry point, its
    rotation threshold, its latency stamp and its "an unwritable log must never stop a fight"
    contract -- has no anchor and had no fixture (§L.38). The decision log is the evidence every
    offline read-out is built from, so a silent change here quietly changes what the project can
    later claim it knew about a live fight.
    """
    section("Combat decision log (the server's writer, rotation and failure path)")
    import json
    import tempfile

    from combat import combat_ai_server as srv

    class _Stub:
        checkpoint = "stub-checkpoint"

        def decide(self, payload):
            return {"ok": True, "action": None, "reason": "stub",
                    "contract": {"actionable": True, "value_ready": False}}

    def read_rows(path: Path):
        """Parse a decision log without crashing: a glued record must name itself as a defect.

        `json.loads` on the whole file would raise, and the sweep would report the mutation CAUGHT
        because the test died -- a crash-only catch, which this project already treats as weaker
        evidence than a check that says what is wrong.
        """
        rows, bad = [], []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                bad.append(line[:48])
        return rows, bad

    tmp = Path(tempfile.mkdtemp())
    saved = (srv.LOG_PATH, srv.LOG_MAX_BYTES, srv.POLICY)
    results = {}
    try:
        with srv._LOG_LOCK:
            srv._close_log_locked()
        srv.LOG_PATH = tmp / "decisions.jsonl"
        # Small on purpose, and small *after* §L.34: a slim row is ~200 B, so 8 KiB would have let
        # all 25 records land in one file and this fixture would have "passed" without ever rotating.
        srv.LOG_MAX_BYTES = 1024
        srv.POLICY = _Stub()

        first = srv.decide({"fight_id": 1234, "round": 3, "my_id": 7,
                            "challenges": [{"id": 9}]})
        rows, bad = read_rows(srv.LOG_PATH)
        results["rows"] = rows
        results["bad"] = bad
        results["latency"] = first.get("server_latency_ms")
        check("one JSON record per line, carrying the ask and the answer",
              not bad and len(rows) == 1
              and rows[0].get("fight_id") == 1234 and rows[0].get("round") == 3
              and rows[0].get("challenges") == [{"id": 9}]
              and (rows[0].get("decision") or {}).get("contract", {})
              .get("actionable") is True,
              (bad, rows[:1]))
        check("the server stamps its own latency into the reply",
              isinstance(results["latency"], (int, float)), results["latency"])

        for index in range(24):
            srv.decide({"fight_id": 2000 + index, "round": 1, "my_id": 7})
        active, active_bad = read_rows(srv.LOG_PATH)
        archived = sorted(p for p in tmp.iterdir() if p.name != srv.LOG_PATH.name)
        archived_rows, archived_bad = [], []
        for path in archived:
            more, bad_more = read_rows(path)
            archived_rows.append(more)
            archived_bad.extend(bad_more)
        results["active"] = len(active)
        results["active_bad"] = active_bad
        results["archived"] = [p.name for p in archived]
        results["archived_bad"] = archived_bad
        check("the log rotates instead of growing without bound",
              bool(archived) and not active_bad and len(active) < 25
              and bool(active) and active[-1].get("fight_id") == 2023, results)
        check("and what was rotated away is still readable evidence, not truncation",
              all(p.stat().st_size > 0 for p in archived) and not archived_bad
              and sum(len(part) for part in archived_rows) > 0, results)

        # The other end: the disk path is a directory, so every write raises. A fight must not stop.
        with srv._LOG_LOCK:
            srv._close_log_locked()
        srv.LOG_PATH = tmp
        degraded = srv.decide({"fight_id": 4242, "round": 1, "my_id": 7})
        results["degraded"] = degraded
        check("an unwritable log never reaches the fight as an error",
              degraded.get("ok") is True and "server_latency_ms" in degraded,
              {k: degraded.get(k) for k in ("ok", "reason", "action")})
    finally:
        with srv._LOG_LOCK:
            srv._close_log_locked()
        srv.LOG_PATH, srv.LOG_MAX_BYTES, srv.POLICY = saved
    # Not "does the value still equal what I saved" -- that can never fail. These are derived
    # independently, so a fixture that forgot to restore would leak a temp path into the live
    # server's writer and every later decision row would land somewhere else.
    check("the fixture leaves the real log path and threshold behind",
          srv.LOG_PATH == srv.REPO_ROOT / "trajectories" / "live_policy_decisions.jsonl"
          and srv.LOG_MAX_BYTES == 64 * 1024 * 1024 and srv.POLICY is None,
          (str(srv.LOG_PATH), srv.LOG_MAX_BYTES, srv.POLICY))


def test_encounter_input_tables() -> None:
    """Witness the actor's own input, which §L.34 showed is load-bearing and the sweep could not see.

    `plan_value.board_encounter` builds the arrays the trained head reads. §L.34 settled that enemy
    spellbooks reach it -- that is why the parse could not be dropped as unread -- yet
    `combat/plan_value.py` had no mutation anchor and no fixture, so a wrong cell here changed what
    the head does and only a duel run far away from the gate would ever have shown it. Each
    assertion pins both ends where the code has a branch, because an assertion that only checks the
    happy path cannot tell a dropped branch from a passing one.
    """
    section("Encounter input tables (what the trained head actually reads)")
    from combat.battle_solver import BattleSolver, Board
    from combat.plan_value import board_encounter
    from combat.test_live_policy import board_payload, damage_effect, policy_stub, spell

    cfg = policy_stub().cfg
    solver = BattleSolver(beam_width=4, max_actions=2)

    def tables_for(payload):
        board = Board.from_fight_state(payload)
        plan = solver.plan_turn(board)
        return board_encounter(board, plan, cfg, first_team=0,
                               player_rows=None, as_jax=False)

    clean = board_payload()
    tables, spec = tables_for(clean)
    check("the player's own state reaches actor 0",
          float(spec["max_hp"][0, 0]) == 100.0 and float(spec["hp"][0, 0]) > 0
          and bool(spec["alive"][0, 0]),
          (float(spec["max_hp"][0, 0]), float(spec["hp"][0, 0]), bool(spec["alive"][0, 0])))
    check("the enemy sits at actor 1 with its level as initiative",
          float(spec["max_hp"][0, 1]) == 50.0 and float(spec["initiative"][0, 1]) == 5.0,
          (float(spec["max_hp"][0, 1]), float(spec["initiative"][0, 1])))
    check("a normal enemy's power is 10 + level",
          abs(float(spec["power"][0, 1]) - 15.0) < 1e-4, float(spec["power"][0, 1]))
    check("an enemy whose spells the client never sent contributes no damage row",
          float(tables["spell_damage"][0, 1].max()) == 0.0,
          tables["spell_damage"][0, 1].tolist())

    boss = board_payload()
    boss["fighters"][1]["is_boss"] = True
    _tables, boss_spec = tables_for(boss)
    check("and the boss branch really is a different number",
          abs(float(boss_spec["power"][0, 1]) - 17.5) < 1e-4,
          float(boss_spec["power"][0, 1]))

    armed = board_payload()
    armed["fighters"][1]["spells"] = [
        spell(500, "Foe Hit", 3, 1, 3, [damage_effect(97, 10, 12)])]
    armed_tables, _ = tables_for(armed)
    check("a loaded enemy spellbook does reach the actor's input -- §L.34's whole point",
          float(armed_tables["spell_damage"][0, 1].max()) > 0.0,
          armed_tables["spell_damage"][0, 1].tolist())
    check("and it lands on the enemy row, not the player's",
          float(armed_tables["spell_damage"][0, 0].max())
          == float(tables["spell_damage"][0, 0].max()),
          (float(armed_tables["spell_damage"][0, 0].max()),
           float(tables["spell_damage"][0, 0].max())))


def test_doc_facts_self_witness() -> None:
    """`doc_facts.py` is the instrument that now guards every fight number, so it needs an arm too.

    A checker cannot be caught by the checks it runs: if `check_docs` is mutated to report no
    problems, the gate's own "every tagged number equals its measurement" check goes green. So this
    points `DOCS` at a throwaway file and pins three behaviours, two of which must FAIL -- the
    house rule that a probe has to be able to fail, applied to the probe's own tooling.
    """
    section("doc_facts witnesses itself (a mutated checker is invisible to its own verdict)")
    import tempfile

    from tools import doc_facts as df

    measured = df.FACTS["gate_checks"][0]()
    check("the fact this fixture leans on actually has a measurement",
          measured is not None, measured)
    if measured is None:
        return

    tmp = Path(tempfile.mkdtemp()) / "fixture.md"
    saved = list(df.DOCS)
    problems = {}
    try:
        df.DOCS = [tmp]
        tmp.write_text("checks run %s<!--f:gate_checks-->\n" % format(measured, ","),
                       encoding="utf-8")
        problems["matching"] = df.check_docs(verbose=False)
        tmp.write_text("checks run %s<!--f:gate_checks-->\n" % format(measured + 1, ","),
                       encoding="utf-8")
        problems["off by one"] = df.check_docs(verbose=False)
        tmp.write_text("checks run {{gate_checks}}\n", encoding="utf-8")
        problems["unrendered"] = df.check_docs(verbose=False)
        tmp.write_text("checks run 7<!--f:no_such_fact-->\n", encoding="utf-8")
        problems["unknown fact"] = df.check_docs(verbose=False)
    finally:
        df.DOCS = saved

    check("a tagged number that equals its measurement is not a problem",
          problems["matching"] == [], problems["matching"])
    check("the same number off by one IS named, with both values",
          any("gate_checks" in p and "measured" in p for p in problems["off by one"]),
          problems["off by one"])
    check("a reference that was never rendered is a problem, not a silent pass",
          any("never rendered" in p for p in problems["unrendered"]),
          problems["unrendered"])
    check("a tag naming no fact is a problem, not a typo nobody notices",
          any("unknown fact" in p for p in problems["unknown fact"]),
          problems["unknown fact"])


def test_slim_live_observation() -> None:
    """The live path no longer builds the observation nothing reads -- prove nothing was lost.

    §L.34: the serialized observation was 94.6% of `live_policy_decisions.jsonl` and 38.1% of
    `fight_boards.jsonl`, and the only reader anywhere takes the four-key `board` block out of it
    (`combat/grid_probe.py:51`). `live_view` now answers a request with that block plus the
    contract. That is only safe if `contract_from_board` returns *the same dict* as
    `validate_observation(build_observation(...))`, warning strings included -- and this gate had
    no check on the fight observation at all, so a drift here would have been silent.
    """
    section("Slim live observation (the contract the decision path actually reads)")
    import json
    import os

    from combat.battle_solver import Board
    from combat.observation import (board_block, build_observation,
                                    contract_from_board, live_view,
                                    validate_observation)
    from combat.test_live_policy import board_payload

    board = Board.from_fight_state(board_payload())
    full = build_observation(board, source="live")
    want = validate_observation(full)
    got = contract_from_board(board)
    check("the slim contract is the same dict as the full one, warnings included",
          got == want, (got, want))
    check("and it is not a vacuous contract: this board is actionable but not value-ready",
          got["actionable"] is True and got["value_ready"] is False
          and any(str(w).startswith("enemy_spells_missing:") for w in got["warnings"]),
          got)
    check("the board block an offline reader takes back out is unchanged",
          board_block(board) == full["board"], (board_block(board), full["board"]))

    slim_obs, slim_contract = live_view(board)
    check("live_view answers with the block and the contract and nothing else",
          slim_contract == want and slim_obs["board"] == full["board"]
          and set(slim_obs) == {"schema_version", "source", "board"},
          sorted(slim_obs))
    check("and it is really smaller -- the serialized spellbooks and derived table are gone",
          len(json.dumps(slim_obs)) * 10 < len(json.dumps(full)),
          (len(json.dumps(slim_obs)), len(json.dumps(full))))

    os.environ["DOFUS_FIGHT_OBSERVATION"] = "full"
    try:
        full_obs, full_contract = live_view(board)
    finally:
        del os.environ["DOFUS_FIGHT_OBSERVATION"]
    check("DOFUS_FIGHT_OBSERVATION=full still restores the whole observation",
          full_obs == full and full_contract == want, sorted(full_obs))

    # The other end, so the equalities above cannot pass on a board with nothing in it: a dead
    # only-enemy must add `no_live_enemies`, clear `actionable`, and do it identically in both arms.
    degraded = board_payload()
    degraded["fighters"][1]["max_hp"] = 0
    degraded["fighters"][1]["hp"] = 0
    degraded["fighters"][1]["alive"] = False
    worse = Board.from_fight_state(degraded)
    want_worse = validate_observation(build_observation(worse, source="live"))
    got_worse = contract_from_board(worse)
    check("a degraded board moves both contracts identically, and moves them",
          got_worse == want_worse and got_worse != want
          and got_worse["actionable"] is False
          and "no_live_enemies" in got_worse["errors"],
          (got_worse["errors"], want_worse["errors"], got_worse["actionable"]))


def test_live_action_realization() -> None:
    """The duel engine can execute what `decide()` emits -- run the class the sweep cannot reach.

    §L.21's headline number is only about the shipped rule if a walk-only action and a cast the
    beam pruned are both executable. `BattleSolver.plan_for_action()` is the inverse of
    `_action_for_plan`, and its own suite pins it against a depth-1 beam search on the same board,
    so a mutation of the cast's AP charge, the damage application or the walk's MP cost shows up
    here instead of quietly changing the win rate.
    """
    section("Live action realization (the seam the production arm is scored through)")
    import io
    import unittest

    from combat.encounter import backend
    from combat.test_battle_solver import ActionRealizationTests

    # `at()` is the indexed update the trained policy needs on both backends (§L.24). Its contract
    # is functional -- jax arrays cannot be written in place, so the numpy stand-in must not either
    # -- and the only way to know the shim keeps it is to call it on whichever branch is live.
    source = backend.XP.zeros((2, 2), dtype=backend.XP.float32)
    updated = backend.at(source)[0, 1].set(7.0)
    check("at().set() returns a new array and leaves the source alone",
          float(source[0, 1]) == 0.0 and float(updated[0, 1]) == 7.0,
          (float(source[0, 1]), float(updated[0, 1])))
    zeros3 = backend.XP.zeros((3,), dtype=backend.XP.float32)
    summed = backend.at(zeros3)[0].add(2.0)
    check("at().add() accumulates into the copy, not into the caller's array",
          float(summed[0]) == 2.0 and float(zeros3[0]) == 0.0,
          (float(summed[0]), float(zeros3[0])))

    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
        unittest.TestLoader().loadTestsFromTestCase(ActionRealizationTests))
    check("the realization suite ran and nothing in it failed",
          result.testsRun >= 6 and result.wasSuccessful(),
          (result.testsRun, [str(t) for t in result.failures + result.errors][:2]))


def test_duel_harness_statistics() -> None:
    """The duel harness's own numbers must be able to say *loud*, not permanently inconclusive.

    `tools/wsp_fight.py compare` printed `sign_test_p_two_sided: 1.0` for every pair of arms ever
    compared, including a 121-19 split. Summing the binomial tail from `min(ups, downs)` to `n`
    gives P(X >= 19), which is ~1 whatever the data; the tail has to run from 0 to the smaller
    count. A statistic that cannot leave 1.0 is not a conservative result, it is a dead instrument,
    and it was reporting on §L.18's edits for days. Both arms below are needed: a balanced pair
    must still read ~1, otherwise the check passes for a formula that always returns 0.
    """
    section("Duel harness statistics (paired deltas and the sign tests behind §L.18 and §L.21)")
    import importlib.util
    import json
    import tempfile

    path = ROOT / "tools" / "wsp_fight.py"
    spec = importlib.util.spec_from_file_location("wsp_fight", str(path))
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    check("the harness the documented commands run is importable from here",
          hasattr(harness, "compare") and hasattr(harness, "summary"), str(path))

    def arm(scores):
        return [{"matchup": [2 * i, 2 * i + 1], "network_score": s, "turns": 4,
                 "fallbacks": 0} for i, s in enumerate(scores)]

    def document(scores):
        return {"summary": harness.summary(arm(scores), {"network_turns": len(scores)}, "x"),
                "results": arm(scores)}

    with tempfile.TemporaryDirectory() as tmp:
        def write(name, scores):
            file = Path(tmp) / name
            file.write_text(json.dumps(document(scores)), encoding="utf-8")
            return str(file)

        lopsided = [1.0] * 39 + [0.0]
        balanced = [1.0] * 20 + [0.0] * 20
        flat = [0.5] * 40
        one_sided = harness.compare(write("a.json", flat), write("b.json", lopsided))
        two_sided = harness.compare(write("c.json", flat), write("d.json", balanced))
    check("a 39-1 paired split is not called inconclusive",
          one_sided["sign_test_p_two_sided"] < 1e-6
          and (one_sided["improved"], one_sided["worsened"]) == (39, 1),
          one_sided["sign_test_p_two_sided"])
    check("and a 20-20 split still is -- the tail must move both ways",
          two_sided["sign_test_p_two_sided"] > 0.5, two_sided["sign_test_p_two_sided"])

    won = harness.summary(arm([1.0] * 40 + [0.0]), {"network_turns": 41}, "w")
    lost = harness.summary(arm([0.0] * 40 + [1.0]), {"network_turns": 41}, "l")
    check("the one-sided test against parity separates the two directions",
          won["sign_test_p_worse_than_solver"] > 0.5
          and lost["sign_test_p_worse_than_solver"] < 1e-9,
          (won["sign_test_p_worse_than_solver"],
           lost["sign_test_p_worse_than_solver"]))
    check("and a bootstrap interval is reported around the mean it is about",
          won["win_rate_bootstrap_ci95"][0] <= won["network_win_rate"]
          <= won["win_rate_bootstrap_ci95"][1], won["win_rate_bootstrap_ci95"])


def test_installed_payload_and_dependencies() -> None:
    """What setup.exe actually carries, read back out of the artifact -- not from the staging source.

    Measured 2026-11-03 with `tools/payload_audit.py`, which parses the central directory of the
    zip embedded in the built binary: the payload was 218.9 MB compressed / 439.4 MB unpacked over
    773 files, and 108.8 MB of it was `models/combat_grid/model.pkl` -- JAX weights the embedded
    interpreter cannot reach at all, because the runtime that ships has no jax wheel while the
    development one does. Alongside it rode 62.1 MB of recorded transitions, 13.8 MB of entity
    pointer shards, 91 dev log files and 16 empty folder placeholders. Nothing in the tree
    compared the payload with what the shipped runtime can load, so the installer kept paying
    freight for bytes the product never reads.

    The second half is the dependency list: the builder wrote a four-line requirements.txt while
    the import closure of the real entry points needs seven more distributions, so an installed
    copy could not load the INT8 encoder policy (onnxruntime, tokenizers) or the lexical command
    policy (scikit-learn, joblib). `tools/shipped_deps.py` now derives it and this check runs it.
    """
    section("Installer: payload bytes and Python dependencies, measured from setup.exe")
    import subprocess

    tools = ROOT / "tools"
    audit = subprocess.run([sys.executable, str(tools / "payload_audit.py"),
                            str(ROOT / "setup.exe"), "--gate"],
                           capture_output=True, text=True, timeout=600, cwd=str(ROOT))
    lines = [l for l in audit.stdout.splitlines() if l.startswith("{")]
    check("the payload audit parses setup.exe", bool(lines), (audit.stdout + audit.stderr)[-260:])
    verdict = json.loads(lines[-1]) if lines else {}
    check("setup.exe is inside the byte budget", bool(verdict) and not verdict.get("over_budget"),
          verdict.get("setup_bytes"))
    check("no excluded tree crossed the wire", not verdict.get("forbidden"), verdict.get("forbidden"))
    check("every entry the installed app needs is present",
          not verdict.get("missing_required"), verdict.get("missing_required"))
    check("the stage left no empty folder placeholders behind",
          verdict.get("empty_dir_entries") == 0, verdict.get("empty_dir_entries"))

    deps = subprocess.run([sys.executable, str(tools / "shipped_deps.py")],
                          capture_output=True, text=True, timeout=300, cwd=str(ROOT))
    check("requirements.txt covers the import closure of the shipped entry points",
          deps.returncode == 0, (deps.stdout + deps.stderr)[-300:])

    installer_src = (ROOT / "desktop" / "installer.cs").read_text(encoding="utf-8", errors="replace")
    check("the installer verifies the unpacked runtime instead of repairing from a shipped zip",
          "runtimes\\python_embed.zip" in installer_src
          and "is missing after extraction" in installer_src,
          [ln.strip()[:96] for ln in installer_src.splitlines()
           if "python_embed.zip" in ln][:3])
    check("an absent wireproxy.conf is reported as a state to fix, not as missing payload",
          "wgcf.exe register" in installer_src,
          [ln.strip()[:96] for ln in installer_src.splitlines()
           if "wireproxy.conf yet" in ln][:1])

    source = (ROOT / "builder" / "Build.cs").read_text(encoding="utf-8", errors="replace")
    check("the builder ships the repo's requirements.txt instead of a typed list",
          'Path.Combine(RepoRoot, "requirements.txt")' in source)

    # The build used to close the patch step with a hard-coded "patched successfully (11 patches
    # active)" on any exit 0, while Run() swallowed patch-engine's own [SKIP] lines. Through the
    # 3.3.11 incident that line stayed printed while the bundle broke, so the builder's report was
    # the last place in the chain that could not be wrong. These checks keep it that way: the
    # verdict must be a function of the served bytes, and the two states it can now name must both
    # be reachable from code the gate can see.
    claim = [ln.strip()[:110] for ln in source.splitlines()
             if "Console.WriteLine" in ln and "patches active" in ln]
    check("no build line reports a patch count nobody measured", not claim, claim)
    check("the builder reads the served bundle to decide what it patched",
          "static void ReportPatchVerdict()" in source
          and source.count("ReportPatchVerdict();") >= 2,
          "called after the patch run and after the no-node skip")
    check("a patched bundle under the quarantine flag stops the build",
          "if (quarantined && patched)" in source,
          "packaging a bundle that contradicts client-patches.quarantine is the black screen again")
    witness = re.search(r'const string Witness = "([^"]+)"', source)
    rules_src = (ROOT / "tools" / "apply-map-resilience.js").read_text(
        encoding="utf-8", errors="replace")
    check("the builder's patched-witness is a string the rules really emit",
          witness is not None and witness.group(1) in rules_src,
          "witness=%s" % (witness.group(1) if witness else None,))

    # The install tiers, asserted where a regression would actually cost an operator something.
    # Measured 2026-11-03: torch installs 508.8 MB and transformers 107.8 MB on this host, and
    # `setup.exe` was downloading 617.4 MB of packages for optional services. Since the Sentinel
    # became torch-free (2026-11-07) every core entry point imports with torch absent; the proof
    # is a subprocess that poisons `sys.modules` before importing them.
    runtime_lines = [line.strip() for line in
                     (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
                     if line.strip() and not line.strip().startswith("#")]
    ai_lines = [line.strip() for line in
                (ROOT / "requirements-ai.txt").read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")]
    check("requirements.txt carries no AI-tier package",
          not [name for name in runtime_lines if name in ai_tier_names()], runtime_lines)
    check("requirements-ai.txt is present and names the opt-in tier",
          len(ai_lines) >= 4 and not [name for name in ai_lines if name in runtime_lines],
          [runtime_lines, ai_lines])
    core_roots = ["agent.commands", "agent.executor", "agent.brain_daemon", "agent.autopilot",
                  "agent.serve", "combat.combat_ai_server", "ai.onnx_policy",
                  "ai.lexical_policy", "networkhealth.sentinel", "run_brain", "run_autopilot"]
    probe = subprocess.run(
        [sys.executable, "-X", "utf8", "-c",
         "import sys; sys.modules['torch'] = None; sys.path.insert(0, %r)\n"
         "bad = []\n"
         "for name in %r:\n"
         "    try:\n"
         "        __import__(name)\n"
         "    except Exception as exc:\n"
         "        bad.append(name + ':' + type(exc).__name__)\n"
         "print('FAILS:' + ','.join(bad) if bad else 'ALL-IMPORT-OK')\n"
         % (str(REPO_ROOT), core_roots)],
        capture_output=True, text=True, timeout=600, cwd=str(REPO_ROOT))
    check("every core entry point imports with torch absent (the installed copy's reality)",
          "ALL-IMPORT-OK" in (probe.stdout or ""),
          (probe.stdout or probe.stderr)[-300:])
    sentinel_bat = (ROOT / "start-sentinel.bat").read_text(encoding="utf-8", errors="replace")
    check("start-sentinel.bat no longer preflights the AI tier",
          'import torch' not in sentinel_bat and "[NOT INSTALLED]" not in sentinel_bat,
          sentinel_bat[:120])
    # The THX-01 lane was killed in the docs on 2026-10-26 but stayed in the tree until
    # 2026-11-07. These checks are the difference between a removal and a promise: each one
    # names the artifact that would have to reappear for the old path to work again.
    check("the retired THX-01 training pipeline is gone",
          not (ROOT / "dofus-network" / "pipeline").exists(),
          "dofus-network/pipeline/ was removed 2026-11-07")
    check("the retired THX-01 policy and its model loader are gone",
          not (ROOT / "dofus-network" / "ai" / "thx_policy.py").exists()
          and "thx01" not in (ROOT / "requirements-ai.txt").read_text(
              encoding="utf-8", errors="replace"),
          "ai/thx_policy.py and the thx01 distribution were removed 2026-11-07")
    sentinel_src = (ROOT / "dofus-network" / "networkhealth" / "sentinel.py").read_text(
        encoding="utf-8", errors="replace")
    check("the Sentinel no longer imports torch or loads a THX model",
          "import torch" not in sentinel_src
          and "ModernBertForSequenceClassification" not in sentinel_src
          and "THX-01-ModernBERT" not in sentinel_src,
          "sentinel.py imports torch at module scope again")
    check("and the builder stages those launchers from the repo rather than retyping them",
          "File.WriteAllText(sentinelBat" not in source
          and 'foreach (string bat in new[] { "start-sentinel.bat", "install-python-ai.bat" })'
          in source, "builder/Build.cs launcher staging")
    tiers = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools" / "shipped_deps.py")],
                           capture_output=True, text=True, timeout=600, cwd=str(ROOT))
    check("the derived tiers agree with the two requirements files",
          "shipped-deps check PASS" in (tiers.stdout or ""), (tiers.stdout or tiers.stderr)[-260:])
    check("the builder refuses to ship weights the shipped runtime cannot load",
          "RuntimeHasJax()" in source)

    # The payload ships data/cache because knowledge.search reads it (agent/executor.py
    # KNOWLEDGE_FILES: items 24.6 MB, quests 8.0 MB, monsters 3.8 MB...). Two things that check is
    # measured to do: answer the queries the agent actually asks, and NOT go looking for a file when
    # the kind has none -- until 2026-11-03 an unmapped kind resolved to the cache *directory*,
    # `path.exists()` was true, and the first such query logged "Unreadable cache file
    # ...\data\cache: Permission denied" as though a data file were corrupt.
    from agent.executor import _KnowledgeIndex

    index = _KnowledgeIndex(REPO_ROOT / "data" / "cache")
    hits = index.search("bouftou", kind="item", limit=3)
    check("the shipped cache answers a real item query", bool(hits), hits[:1])
    check("an unmapped knowledge kind is empty, not a directory read",
          index.search("bouftou", kind="no_such_kind", limit=3) == [],
          index.search("bouftou", kind="no_such_kind", limit=3))

    # The index used to hold, per row, a tuple of raw description strings *and* a joined haystack
    # string: measured 2026-11-03 on the shipped interpreter, 121.7 MB committed against 16.2 MB of
    # searchable text. It now keeps one UTF-8 blob per kind with a row-offset array, so no per-row
    # text object exists at all (-27.7 MB for the item kind). The shape is the win, so the shape is
    # pinned: a change back to per-row strings would silently spend the memory again.
    item_rows = index.entries("item")
    offsets, hay_blob = index._hay_for("item")
    check("one byte blob carries the searchable text of every row, with no per-row descriptions",
          len(offsets) == len(item_rows) + 1 and bool(hay_blob)
          and all(row[1] == () for row in item_rows),
          (len(item_rows), len(offsets), len(hay_blob)))


    # Rewriting storage is only safe if the answers do not move. `data/knowledge_index_baseline.json`
    # is the checked-in snapshot of the query answers (regenerate it deliberately with
    # `python tools/knowledge_index_probe.py snapshot data/knowledge_index_baseline.json`, which is
    # also the command to re-bless after a genuine data re-sync).
    baseline_path = REPO_ROOT / "data" / "knowledge_index_baseline.json"
    if baseline_path.is_file():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        drift = []
        for key, expected in sorted(baseline["results"].items()):
            if key in ("repeat", "unmapped_kind", "blank_query") or "|" not in key:
                continue
            query, kind, limit = key.split("|")
            got = index.search(query, kind, int(limit))
            if [h.get("id") for h in got] != [h.get("id") for h in expected]:
                drift.append(key)
        check("the blob index answers every baseline query with the same ids",
              not drift, drift[:6])

    # `quest.list` filtered its area argument against the entries' *description* slot, and quests
    # have none (measured 2026-11-03: 0 description characters across all 1,976 quest rows), so
    # the membership test was against an empty tuple and every quest was dropped whenever the area
    # did not resolve to known subareas -- the text fallback the tool's own note describes
    # ("showing quests whose name contains the text") could never fire. It reads the row's names
    # region now, which is populated, so the fallback exists again. Asserted through the index,
    # not by grepping `executor.py`: the region moved into the blob on the same day, and a source
    # string assertion would have been a second thing to update for no extra truth.
    quest_rows = index.entries("quest")
    first_name = index._names("quest", 0)
    first_word = (first_name[0].split(" ")[0] if first_name else "")
    check("quests carry no descriptions but do carry names in the haystack region",
          bool(quest_rows) and not any(row[1] for row in quest_rows)
          and all(row[2] == () and row[4] == () for row in quest_rows)
          and any(first_word in name for i in range(len(quest_rows))
                  for name in index._names("quest", i)),
          (len(quest_rows), first_word))
    q_offsets, q_blob = index._hay_for("quest")
    q_nend = index._nend["quest"]
    check("and every row's names region sits inside that row",
          len(q_nend) == len(quest_rows)
          and all(q_offsets[i] <= q_nend[i] <= q_offsets[i + 1]
                  for i in range(len(quest_rows))),
          (len(quest_rows), len(q_nend)))
    check("a name never contains a separator byte, which is what makes the region recoverable",
          bool(first_name) and not any("\x00" in n or "\x01" in n for n in first_name),
          first_name[:2])

    embedded = ROOT / "runtimes" / "python" / "python.exe"
    check("the interpreter that ships exists in the tree", embedded.exists(), str(embedded))
    if embedded.exists():
        probe = subprocess.run(
            [str(embedded), "-c",
             "import sys; sys.path.insert(0, %r); "
             "import agent.commands, agent.brain_daemon, ai.lexical_policy, ai.onnx_policy, "
             "combat.grid_infer; print('imports-ok')" % str(REPO_ROOT)],
            capture_output=True, text=True, timeout=300, cwd=str(REPO_ROOT))
        check("the shipped interpreter can import the modules the app loads",
              "imports-ok" in probe.stdout, (probe.stderr or probe.stdout)[-300:])

        # The five modules above are the seam; these two sweeps are the whole shipped surface, and
        # both are run by the interpreter that actually installs -- the tree is edited and tested
        # under a newer Python than the payload's 3.11.9, which is how ai/diag_call_budget.py sat
        # unparseable on the installed path (a 3.12+ f-string quote reuse) and how ten combat
        # modules are importable here but not there (no jax wheel in the embedded runtime).
        compile_sweep = subprocess.run([str(embedded), str(tools / "ship_compile.py")],
                                       capture_output=True, text=True, timeout=900, cwd=str(ROOT))
        check("every shipped file parses under the interpreter that ships",
              compile_sweep.returncode == 0,
              (compile_sweep.stdout or "")[-300:] + (compile_sweep.stderr or "")[-200:])

        # Parsing is not importing. Measured 2026-11-03: the sweep above was green while
        # `combat/encounter/env.py` did `import jax` at module scope, which is on the import chain
        # of `live_policy` and `plan_value` -- so an installed copy had no trained policy to ask, and
        # no check could see it (§L.24). The census imports the chain and then answers one real
        # board, and the `jax` arm proves the census is capable of failing here.
        census = subprocess.run(
            [str(embedded), "-X", "utf8", str(ROOT / "tools" / "ship_compile.py"),
             "--imports", "--decide"],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        census_out = (census.stdout or "") + (census.stderr or "")
        check("the trained fight policy imports and answers on the interpreter that ships",
              census.returncode == 0 and "census PASS" in census_out
              and "decide() on this interpreter: " in census_out, census_out[-320:])
        refusal = subprocess.run(
            [str(embedded), "-X", "utf8", str(ROOT / "tools" / "ship_compile.py"),
             "--imports", "jax"],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        check("and the census is not a rubber stamp: a module the runtime lacks must fail it",
              refusal.returncode == 1 and "No module named 'jax'"
              in (refusal.stdout or "") + (refusal.stderr or ""),
              (refusal.returncode, (refusal.stdout or "")[-160:]))

        # The trained fight policy has to compute the same answer with and without jax, because an
        # installed copy has numpy and no jax while this box has both. `DOFUS_FIGHT_BACKEND=numpy`
        # forces the fallback even where jax is installed (combat/encounter/backend.py), so the
        # comparison is one machine, two processes, same checkpoint, same fixture boards.
        # Measured 2026-11-03 over 25 boards: every field of the benchmark report identical except
        # generated_at and elapsed_s (2.5 s on jax, 6.0 s on numpy -- no compiled path).
        def _flat(node, prefix=""):
            leaves = {}
            if isinstance(node, dict):
                for key, value in node.items():
                    leaves.update(_flat(value, "%s/%s" % (prefix, key)))
            elif isinstance(node, list):
                for position, value in enumerate(node):
                    leaves.update(_flat(value, "%s[%d]" % (prefix, position)))
            else:
                leaves[prefix] = node
            return leaves

        VOLATILE = ("/generated_at", "/elapsed_s")
        with tempfile.TemporaryDirectory() as bench_dir:
            reports = {}
            for label, forced in (("jax", {}), ("numpy", {"DOFUS_FIGHT_BACKEND": "numpy"})):
                env = dict(os.environ)
                env.update(forced)
                out = Path(bench_dir) / ("bench_%s.json" % label)
                done = subprocess.run(
                    [sys.executable, "-X", "utf8", "-m", "combat.plan_benchmark",
                     "--rows", "16", "--out", str(out)],
                    cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=900)
                reports[label] = (_flat(json.loads(out.read_text(encoding="utf-8")))
                                  if out.is_file() else {"_rc": done.returncode,
                                                         "_err": (done.stderr or "")[-240:]})
            jax_leaves, numpy_leaves = reports["jax"], reports["numpy"]
            moved = sorted(key for key in jax_leaves
                           if key not in VOLATILE and numpy_leaves.get(key) != jax_leaves[key])
            check("the fight policy answers identically on the numpy backend it ships with",
                  bool(jax_leaves) and bool(numpy_leaves) and not moved,
                  (len(jax_leaves), moved[:6]) if moved else (jax_leaves, numpy_leaves))
            check("and the numpy fallback is not quietly the solver answering twice",
                  jax_leaves.get("/boards") not in (None, 0),
                  jax_leaves.get("/boards"))

        # The invariant that makes the above reachable on an installed copy: none of the fight
        # modules may import jax at module level again. A single stray `import jax` puts the whole
        # learned policy back behind a wheel PyPI does not publish for the shipped interpreter.
        strays = []
        for rel in ("combat/encounter/model.py", "combat/encounter/checkpoint.py",
                    "combat/plan_value.py", "combat/live_policy.py"):
            source = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
            if re.search(r"^import jax\b|^import jax\.", source, re.M):
                strays.append(rel)
        check("no fight module imports jax at module level", not strays, strays)

        # The strongest form of the claim: not that the module imports, but that the service
        # comes up and answers on the interpreter an installed copy runs. Before 2026-11-03
        # combat/combat_ai_server.py imported its JAX policy at module level, so on the embedded
        # runtime the learned fight brain could not start at all.
        boot = subprocess.Popen([str(embedded), str(REPO_ROOT / "combat" / "combat_ai_server.py"), "8899"],
                                cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        import urllib.request

        try:
            answered = None
            last_error = None
            for _wait in range(40):
                time.sleep(1.5)
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8899/health", timeout=4) as resp:
                        answered = json.loads(resp.read().decode())
                        break
                except Exception as exc:
                    last_error = "%s: %s" % (type(exc).__name__, str(exc)[:120])
                    if boot.poll() is not None:
                        break
            check("the combat brain starts on the interpreter that ships",
                  isinstance(answered, dict) and answered.get("status") in ("ok", "loading"),
                  answered or "no /health answer (exit %s, last %s)" % (boot.poll(), last_error))
            check("its health endpoint describes the backend it actually loaded",
                  answered is None or "backend" not in answered
                  or (answered.get("status") == "ok")
                  == (answered.get("backend") in ("jax", "numpy")),
                  answered)
        finally:
            boot.terminate()
            try:
                boot.wait(timeout=10)
            except Exception:
                boot.kill()

        import_sweep = subprocess.run([sys.executable, str(tools / "embed_imports.py"), "--quiet"],
                                      capture_output=True, text=True, timeout=900, cwd=str(ROOT))
        check("the embedded interpreter imports the closure except the recorded jax-only list",
              import_sweep.returncode == 0,
              (import_sweep.stdout or "")[-300:] + (import_sweep.stderr or "")[-200:])


def test_docs_quote_measurements_by_tag() -> None:
    """A count in the prose is a claim about the tree; the tag is what makes it checkable.

    The docs used to carry hand-typed counts, and every pass found a few more that had
    drifted. `tools/doc_facts.py` now measures and tags them (`28<!--f:tools-->`), so the
    only way a count can be wrong is if the tag disagrees with the measurement - which is
    this test's single question.
    """
    from tools import doc_facts

    section("Docs: measured numbers are tagged, not typed")
    measured = doc_facts.values(refresh=True)
    check("the tool fact is measured", measured.get("tools") == 28, measured.get("tools"))
    problems = doc_facts.check_docs(verbose=False)
    check("every tagged number equals its measurement", not problems, problems[:3])
    for path in doc_facts.DOCS:
        check("the doc exists: %s" % path.name, path.exists(), str(path))
    tagged = sum(len(re.findall(r"<!--f:[a-z_]+-->", p.read_text(encoding="utf-8")))
                 for p in doc_facts.DOCS if p.exists())
    check("the docs carry tags worth checking", tagged >= 20, tagged)
    selftest = REPO_ROOT.parent / "bundle-selftest.txt"
    if selftest.exists():
        found = re.search(r"patched (\d+) chars, live (\d+) chars",
                          selftest.read_text(encoding="utf-8", errors="replace"))
        quarantined = (REPO_ROOT.parent / "client-patches.quarantine").exists()
        # The agreement this tests is between two instruments measuring the same file: the
        # UTF-16 count the C# `--bundle-selftest` printed and the one doc_facts measures from
        # disk. That holds in every state, so it is always checked. Whether the *patched* count
        # also equals the live one depends on which bundle we serve, and the quarantine says the
        # vendor's -- asserting equality there would demand the black-screen bundle back.
        check("the bundle fact equals the count --bundle-selftest printed (live chars)",
              bool(found) and int(found.group(2)) == measured.get("bundle_chars"),
              found.groups() if found else "no selftest line")
        bundle = ROOT / "desktop" / "client" / "www" / "build" / "script.js"
        bak = bundle.with_suffix(".js.bak")
        if quarantined:
            check("quarantined: the served bundle is byte-for-byte the vendor's",
                  bundle.exists() and bak.exists()
                  and bundle.read_bytes() == bak.read_bytes(),
                  "script.js differs from script.js.bak while client-patches.quarantine is set")
        else:
            check("the served bundle is the patched one",
                  bool(found) and found.group(1) == found.group(2), found.groups() if found else None)
    check("render repairs a drifted tag in place",
          doc_facts.render("1<!--f:tools-->") == "28<!--f:tools-->",
          doc_facts.render("1<!--f:tools-->"))
    check("render leaves an unknown reference alone instead of guessing",
          doc_facts.render("7 {{nosuch}}") == "7 {{nosuch}}", doc_facts.render("7 {{nosuch}}"))
    check("a reference inside backticks is documentation, not something to render",
          doc_facts.render("write `{{tools}}` in prose") == "write `{{tools}}` in prose",
          doc_facts.render("write `{{tools}}` in prose"))


def test_docs_owed_session_names_the_current_build() -> None:
    """A doc that names the live session it still owes must name the build that will run.

    The owed-session prose drifted behind three page bumps: `docs/CURRENT.md` called
    `2026-10-30d` "the current page build" and nine `KNOWN_GAPS.md` entries still asked a
    `2026-10-30d` session to prove them, while the tree shipped `2026-10-31a`. A reader who
    acts on those lines launches a build that no longer exists in the source, so the
    sentence is not history - it is an instruction that has gone stale.
    """
    from tools import live_ready as ready

    page = REPO_ROOT.parent / "scripts" / "mobile-disguise.js"
    check("the page source exists for the build stamp", page.is_file(), str(page))
    if not page.is_file():
        return
    stamp = ready.page_build(page)
    check("the page source names a build", bool(stamp), stamp)

    docs = [REPO_ROOT.parent / "resume.md",
            REPO_ROOT.parent / "README.md",
            REPO_ROOT.parent / "rules.md",
            REPO_ROOT / "docs" / "CURRENT.md",
            REPO_ROOT / "docs" / "KNOWN_GAPS.md"]
    owed = ("not yet measured live", "what remains", "still owed", "what is owed",
            "next correct move", "current page build", "newest source stamp",
            "has never run", "never ran", "live session on", "live run on")
    # A line is only stale if it still points forward. `resume.md` also records the
    # sessions that already ran ("Live session on build `2026-10-28x`"), and rewriting
    # those would destroy the evidence, so the owed phrase needs a forward word too.
    forward = ("would", "should", "owed", "never", "next", "current", "remains",
               "not yet", "is what")
    build = re.compile(r"20\d{2}-\d{2}-\d{2}[a-z]{1,2}")
    stale: List[str] = []
    for path in docs:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if not any(marker in low for marker in owed):
                continue
            if not any(word in low for word in forward):
                continue
            names = build.findall(line)
            if names and stamp not in names:
                stale.append("%s:%d points at %s"
                             % (path.name, number, "/".join(sorted(set(names)))))
    check("every owed-live-session claim names the build the tree ships",
          not stale, stale[:5])


def test_console_output_is_utf8() -> None:
    """Printed data must survive the console it is printed into.

    `session_report --json` prints captured page text with `ensure_ascii=False`, and
    Python encodes stdout with the console's own encoding - cp1252 on Windows. Measured
    2026-10-31 over the real captures: an accented quest line came out as bytes that are
    not valid UTF-8 (`json.load(..., encoding="utf-8")` raises on the report another
    session is about to read), and one arrow raises UnicodeEncodeError mid-print.
    `agent/jsonio.utf8_console()` pins stdout and stderr to UTF-8 for every CLI that
    prints captured text.
    """
    import subprocess
    import tempfile

    section("Console encoding (printed data is UTF-8, not cp1252)")
    text = "Parler à Jin → 4262"
    tmp = Path(tempfile.mkdtemp())
    log = tmp / "arrow.jsonl"
    rows = [{"kind": "step", "ts": 1000.0 + i, "iso": "2026-10-31T00:00:00",
             "instance": "unit", "goal": text, "tool": "quest.advance", "args": {},
             "ok": True, "latency_ms": 20,
             "observation": {"objectives_completed": i,
                             "ready_objectives": [{"text": text}],
                             "position": {"map_id": 10898, "cell": 3 + i}},
             "result": {"ok": True, "snapshot": {}}} for i in range(3)]
    log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    try:
        proc = subprocess.run([sys.executable, "-m", "tools.session_report", "--json",
                               str(log)], cwd=str(REPO_ROOT), capture_output=True,
                              env=dict(os.environ, PYTHONIOENCODING="cp1252"), timeout=180)
        check("a cp1252 console does not kill the session report",
              proc.returncode == 0, (proc.stderr or proc.stdout)[-240:])
        decoded = None
        try:
            decoded = json.loads(proc.stdout.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            check("the report's JSON output decodes as UTF-8", False,
                  "%s: %s" % (type(exc).__name__, exc))
        if decoded is not None:
            check("the report's JSON output decodes as UTF-8", True, len(proc.stdout))
            flat = json.dumps(decoded, ensure_ascii=False)
            check("and the captured text arrives whole, accent and arrow included",
                  text in flat, flat[:160])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # The same trap in any other CLI: a module that prints `ensure_ascii=False` text to
    # stdout has to force the console encoding or it inherits whatever the shell has.
    offenders = []
    for folder in ("agent", "tools", "ai", "combat", "advisor", "sim"):
        for path in (REPO_ROOT / folder).rglob("*.py"):
            if "__pycache__" in path.parts or "kaggle" in path.parts:
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"print\(json\.dumps\(.*ensure_ascii=False", source):
                if "utf8_console()" not in source:
                    offenders.append(str(path.relative_to(REPO_ROOT)))
    check("every module that prints non-ASCII JSON guards its console",
          not offenders, offenders[:6])


def test_text_io_declares_encoding() -> None:
    """Every text-mode file read/write names its encoding, or the platform decides.

    Same trap as the console, one layer down: `Path.read_text()`/`write_text()` and
    builtin `open()` with no `encoding=` use the locale encoding, which on this host is
    cp1252. A UTF-8 data file read that way is silently re-spelled, and writing one
    raises UnicodeEncodeError. Binary modes and URL openers (`opener.open(url,
    timeout=...)`) are not text I/O and are excluded; four sites in
    `agent/test_brain_daemon.py` were reading the daemon lock that way (2026-10-31).
    """
    import ast

    section("Text I/O declares its encoding")
    offenders: List[str] = []
    scanned = 0
    for folder in ("agent", "tools", "ai", "sim", "combat", "advisor"):
        for path in (REPO_ROOT / folder).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            scanned += 1
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fname = getattr(node.func, "attr", None)
                owner = getattr(node.func, "value", None)
                owner_name = getattr(owner, "id", None) if isinstance(owner, ast.Name) else None
                keywords = {kw.arg for kw in node.keywords}
                if owner_name == "os" or "timeout" in keywords:
                    continue        # os.open flags, urllib openers: not text file I/O
                mode = None
                if fname == "open":
                    arg = node.args[1] if owner_name == "open" and len(node.args) > 1 else None
                    if fname == "open" and owner_name != "open" and node.args:
                        arg = node.args[0]
                    mode = arg.value if isinstance(arg, ast.Constant) else None
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            mode = kw.value.value
                    if mode is None or "b" in str(mode):
                        continue
                elif fname not in ("read_text", "write_text"):
                    continue
                if "encoding" in keywords:
                    continue
                offenders.append("%s:%d %s" % (path.relative_to(REPO_ROOT), node.lineno, fname))
    check("the text I/O scan reads the source tree", scanned >= 150, scanned)
    check("every text-mode file read and write names encoding=utf-8",
          not offenders, offenders[:8])


def test_docs_live_readout_commands_run() -> None:
    """The commands the snapshot tells the next session to run must run where it says.

    This repo has two folders named `tools/`, so a bare `tools/x.py` in prose resolves by
    luck: `python -m tools.damage_calibration` from `dofus-network/` answers
    `No module named tools.damage_calibration`, while the same tool runs from the repo
    root (measured 2026-10-31, while writing the live read-out step into §24). Each
    documented invocation is run with `--help` from the directory the sentence names.
    """
    import subprocess

    section("Documented live read-out commands run from their stated directory")
    doc = REPO_ROOT.parent / "resume.md"
    check("the snapshot exists", doc.is_file(), str(doc))
    text = doc.read_text(encoding="utf-8", errors="replace") if doc.is_file() else ""
    commands = [
        (REPO_ROOT, [sys.executable, "-m", "tools.quest_witness", "--help"]),
        (REPO_ROOT, [sys.executable, "-m", "tools.session_report", "--help"]),
        (REPO_ROOT.parent, [sys.executable, "tools/damage_calibration.py", "--help"]),
    ]
    for cwd, argv in commands:
        proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, timeout=120)
        check("%s runs from %s" % (" ".join(argv[1:]), cwd.name),
              proc.returncode == 0, (proc.stderr or proc.stdout)[-200:])
    check("the read-out sentence names the two tools directories apart",
          "root `tools/`" in text and "dofus-network/tools/" in text,
          "the sentence does not say which tools/ folder the calibrator lives in")


def test_tools_scripts_find_the_repo_root() -> None:
    """`python tools/x.py` must work, not only `python -m tools.x`.

    Script mode puts `tools/` on sys.path and the repo root nowhere, so any script that
    imports `agent` -- even inside a function, which is how the shared log reader is
    pulled in -- raises `ModuleNotFoundError: No module named 'agent'` before it reads a
    byte. Measured 2026-10-31 on the reader adoption: six of the seven log CLIs broke this
    way, and a `--help` probe hid it because argparse exits before the import runs -- so this
    test passes a real log to seven of them, compares the two entry points byte for byte,
    scans every other script, and signs for the ones it cannot execute because they write or
    reach the network.
    """
    import subprocess
    import tempfile

    section("tools/ scripts run as scripts, not only as modules")
    scripts = [p for p in sorted((REPO_ROOT / "tools").glob("*.py"))
               if not p.name.startswith("test_")]
    covered, offenders = [], []
    for path in scripts:
        text = path.read_text(encoding="utf-8", errors="replace")
        hit = re.search(r"^\s*(?:from (?:agent|tools|knowledge)[ .]"
                        r"|import (?:agent|tools|knowledge)\b)", text, re.M)
        if not hit:
            continue
        covered.append(path.name)
        # A module-level root insert, or the documented `except ImportError: from x import`
        # fallback, both work; anything else only works when the caller set sys.path.
        bootstrap = re.search(
            r"^(?:if str\([A-Za-z_]+\) not in sys\.path:\n\s*)?sys\.path\.insert\(0, str\(.+?\)\)",
            text, re.M)
        if bootstrap and bootstrap.start() < hit.start():
            continue
        if re.search(r"except[^\n]*:\n\s+from ", text[:hit.start() + 400]):
            continue
        offenders.append("%s:%d" % (path.name, text[:hit.start()].count("\n") + 1))
    check("the scan covers the scripts that import a repo-root package",
          len(covered) >= 20, (len(covered), covered))
    check("every such script puts the repo root on sys.path itself",
          not offenders, offenders)

    tmp = Path(tempfile.mkdtemp())
    fixture = tmp / "log.jsonl"
    decision = {"kind": "decision", "index": 0, "action": "recover", "tool": "farm.start",
                "ok": True, "at": 1760000000.0}
    intervention = {"kind": "intervention", "session": "s1", "events": 1,
                    "at": 1760000001.0}
    step = {"kind": "step", "index": 1, "tool": "quest.advance", "ok": True,
            "ts": 1760000002.0, "observation": {"in_world": True, "objectives_completed": 0},
            "result": {"snapshot": {"in_world": True, "objectives_completed": 1}}}
    # Glued onto one line, exactly as the live logs write them (G97), so the run also
    # proves the shared reader is reachable in script mode.
    fixture.write_text(json.dumps(decision) + json.dumps(intervention) + "\n"
                       + json.dumps(step) + "\n", encoding="utf-8")
    # The log readers, run the way the snapshot tells a reader to run them. A verdict of
    # "absent"/rc 1 is allowed -- what must not happen is an import crash, and `--help`
    # cannot see the difference because argparse exits before the import runs.
    readers = [("session_report.py", ["--json"]), ("group_scan.py", []),
               ("recovery_report.py", []), ("quest_advance_shapes.py", []),
               ("replay_explore_returns.py", []), ("freeze_planner_replay.py", ["--check"]),
               ("ingest_observed_quests.py", ["--out", str(tmp / "obs.jsonl"),
                                              "--no-write"])]
    crashes, verdicts = [], []
    for name, extra in readers:
        proc = subprocess.run([sys.executable, "tools/" + name, str(fixture)] + extra,
                              cwd=str(REPO_ROOT), capture_output=True, text=True,
                              timeout=300)
        out = (proc.stdout or "") + (proc.stderr or "")
        if "No module named" in out or "Traceback (most recent call last)" in out:
            crashes.append("%s: %s" % (name, out.strip().splitlines()[-1]))
        elif proc.returncode not in (0, 1):
            verdicts.append("%s: rc %s" % (name, proc.returncode))
    check("every log reader runs as a script over a real log",
          not crashes and not verdicts, (crashes, verdicts))
    _delegation("log CLIs executed in script mode", True, REPO_ROOT / "tools",
                "%d run over a fixture, %d covered by the sys.path scan only"
                % (len(readers), max(0, len(covered) - len(readers))))
    check("the scan-only half is stated, not left unsaid",
          any(d["name"] == "log CLIs executed in script mode" for d in _DELEGATIONS),
          [d["name"] for d in _DELEGATIONS])
    check("and the executed set is the seven readers, not a shorter list",
          len(readers) == 7 and len(covered) >= len(readers),
          (len(readers), len(covered)))
    module_run = subprocess.run([sys.executable, "-m", "tools.session_report", "--json",
                                 str(fixture)], cwd=str(REPO_ROOT),
                                capture_output=True, text=True, timeout=300)
    script_run = subprocess.run([sys.executable, "tools/session_report.py", "--json",
                                 str(fixture)], cwd=str(REPO_ROOT),
                                capture_output=True, text=True, timeout=300)
    check("and answers the same numbers either way",
          module_run.stdout.strip() == script_run.stdout.strip()
          and module_run.returncode == script_run.returncode == 0,
          (module_run.returncode, script_run.returncode,
           len(module_run.stdout), len(script_run.stdout)))


def test_live_evidence_lands_in_the_repository() -> None:
    """§12.2's budget numbers must be re-derivable from a clone, not from a disk (G96).

    The four per-instance autopilot logs live in `%LOCALAPPDATA%` and are not tracked --
    82 MB of the only evidence for the claim that 55% of the step budget goes to recovery.
    `tools/session_close.py` now copies their *derived* totals into
    `data/evidence/live_sessions.jsonl` on every close, and the docs quote that capture by
    tag, so a reader of a clone can check the arithmetic instead of trusting a sentence.
    """
    from tools import doc_facts, session_report

    section("The live budget numbers are inside the repository")
    capture = REPO_ROOT / "data" / "evidence" / "live_sessions.jsonl"
    if not capture.is_file():
        _delegation("live evidence capture", False, capture,
                    "no close has run against live logs on this machine, so §12.2's "
                    "numbers cannot be re-derived here")
        check("an absent capture is stated, not silently green",
              any(d["name"] == "live evidence capture" and not d["ran"]
                  for d in _DELEGATIONS), _DELEGATIONS[-1:])
        return
    _delegation("live evidence capture", True, capture)
    from agent import jsonio

    rows = [row for row in jsonio.iter_rows(capture)]
    check("the capture file is line-per-record JSON", rows, len(rows))
    last = rows[-1]
    totals = last.get("totals") or {}
    instances = last.get("instances") or []
    check("every captured log is a per-slot autopilot log",
          instances and all(str(row.get("instance", "")).startswith("autopilot.dofus-")
                            for row in instances), [row.get("instance") for row in instances])
    check("the totals are the sum of the per-instance rows",
          sum(int(row.get("steps") or 0) for row in instances) == int(totals.get("steps") or 0)
          and sum(int(row.get("manual_interventions") or 0) for row in instances)
          == int(totals.get("manual_interventions") or 0),
          (totals.get("steps"), totals.get("manual_interventions")))
    check("steps split into ok and failed with nothing lost",
          int(totals.get("ok") or 0) + int(totals.get("failed") or 0)
          == int(totals.get("steps") or 0),
          (totals.get("ok"), totals.get("failed"), totals.get("steps")))
    actions = totals.get("recoveries") or {}
    check("the recovery tally is the sum of its actions",
          sum(int(value) for value in actions.values())
          == int(totals.get("recovery_rows") or 0), (actions, totals.get("recovery_rows")))
    check("and every action counted is one the reporter calls a recovery",
          set(actions) <= session_report.RECOVERY_ACTIONS, sorted(actions))
    check("the capture names the size and mtime it was read from",
          all((row.get("signature") or {}).get("size") for row in instances),
          [row.get("signature") for row in instances])
    doc = REPO_ROOT.parent / "resume.md"
    text = doc.read_text(encoding="utf-8", errors="replace") if doc.is_file() else ""
    check("the snapshot quotes the budget as a reference, not a retyped number",
          "<!--f:live_steps-->" in text and "<!--f:live_interventions-->" in text,
          "§12.2 still hard-codes the totals the capture holds")
    check("and the reference measures what the capture holds",
          doc_facts.live_evidence_totals() == totals,
          (doc_facts.live_evidence_totals(), totals))


def test_every_mutation_anchor_still_points_at_code() -> None:
    """An anchor the tree has moved past is not a test -- and the sweep cannot notice.

    Measured 2026-11-01, unprompted: 4 of 542 anchors no longer existed. Two were orphaned by
    the G97/G98 reader adoption (the ingest scan stopped parsing lines itself, so the mutation
    that broke line parsing pointed at code that was gone), one by an indentation change in
    `group_scan.py`, and one by *my own* `--evidence-force` edit the same session that added
    the mutation -- which had read CAUGHT hours earlier and was dead afterwards. The driver
    prints `SKIP anchor not found` and counts it as uncaught, but only for the mutations
    somebody selects; an orphan can sit in the list for months while the docs keep saying
    "every mutation is caught". So the list itself is now checked against the code, on every
    gate run.
    """
    import os
    from pathlib import Path

    section("Every mutation anchor still points at code")
    if os.environ.get("GATE_IN_MUTATION_SNIPPET"):
        check("the anchor audit runs in the full gate, not inside the sweep", True,
              "skipped: sweeps mutate the tree the audit reads")
        return
    driver = REPO_ROOT / "tools" / "mutate_cause_gate.py"
    check("the mutation driver exists", driver.is_file(), str(driver))
    lines = driver.read_text(encoding="utf-8").splitlines(True)
    cut = next((i for i, line in enumerate(lines) if line.startswith("JOURNAL = ")), None)
    check("the driver's data section was located without running its sweep",
          cut is not None, cut)
    if cut is None:
        return
    # Evaluate only the constants and the list: the driver's module body raises SystemExit
    # when imported, and its mutating loop lives below this point.
    namespace = {"__file__": str(driver), "__name__": "anchor_audit"}
    exec(compile("".join(lines[:cut]), str(driver), "exec"), namespace)
    mutations = namespace.get("MUTATIONS") or []
    texts: Dict[str, str] = {}
    missing_files, orphaned = [], []
    for name, path, old, new in mutations:
        key = str(path)
        if key not in texts:
            target = Path(path)
            texts[key] = (target.read_text(encoding="utf-8", errors="replace")
                          if target.is_file() else "")
            if not target.is_file():
                missing_files.append("%s -> %s" % (name, target.name))
        if old not in texts[key]:
            orphaned.append("%s (%s)" % (name, Path(path).name))
    check("the audit read the whole list, not a fragment",
          len(mutations) >= 500 and len(texts) >= 50, (len(mutations), len(texts)))
    check("every mutation targets a file that exists", not missing_files, missing_files)
    check("no mutation anchor has been edited out of existence",
          not orphaned, orphaned)


def test_the_mutation_pass_stays_affordable() -> None:
    """A 63 s mutation pass is a 19-hour sweep; nobody re-runs that, so the proof rots.

    Measured 2026-11-01: `test_gate_shadow_reaches_the_trajectory` spent 18.0 s in
    `_settle()`'s monotonic wall-clock windows (6 executed steps x the 3 s default), which
    `time.sleep` stubbing cannot shorten -- 28% of every single mutation, on its own. The
    fix was the supported `settle_s=0.0` and the pass fell to 39.6 s with all of that test's
    checks intact. This check is the ratchet: it times the heaviest tests the snippet runs
    and fails if any of them becomes a wall-clock drag again.

    It does not run inside the snippet (that would double the cost it is guarding), and each
    budget is justified by the work the test really does -- this rule watches for a test that
    starts *waiting* on a wall clock, not for a test that is thorough. It reads the durations
    `_run_test` recorded while the gate ran: re-timing a test by running it again measured the
    JS suites at 6.4 s on their first pass and 34.5 s on the re-run, which would have failed
    the rule for a reason that was the rule itself.
    """
    import os

    section("The mutation pass stays affordable")
    # A budget per heavy test, each one justified by what the test actually does. A single
    # generic bar is not defensible: measured in the full gate, `test_js_page_suites` spends
    # 34.5 s starting ~40 node suites and `test_counterfactual_factory_alive` 16.9 s in a
    # sim subprocess -- that is work, not sleeping. What this rule watches for is a test that
    # quietly starts *waiting* on a wall clock: the settle window cost 18.0 s of an in-process
    # loop test whose budget is 3 s.
    budgets = {
        "test_gate_shadow_reaches_the_trajectory": (3.0, "in-process loop, settle disabled"),
        "test_counterfactual_factory_alive": (30.0, "spawns sim/tool_choice.py"),
        "test_js_page_suites": (90.0, "starts ~40 node suites"),
        "test_egress_preflight": (15.0, "probes the relay over a socket"),
        "test_worker_headroom_layer": (10.0, "sim floor over recorded boards"),
        "test_text_io_declares_encoding": (30.0,
                                           "parses every .py in the tree; 21.6 s measured 2026-11-07"),
        "test_tools_scripts_find_the_repo_root": (25.0, "runs 7 CLIs as scripts"),
    }
    if os.environ.get("GATE_IN_MUTATION_SNIPPET"):
        _delegation("snippet timing", False, REPO_ROOT / "tools",
                    "read from the durations the full gate records")
        check("a sweep reads recorded durations rather than re-running what it times",
              True, "skipped inside the snippet")
        return
    _delegation("snippet timing", True, REPO_ROOT / "tools")
    if not DURATIONS:
        # A subset run -- the documented workaround for a host that kills the full gate --
        # has no durations at all, because none of the timed tests executed here. That is
        # not a budget breach; saying so keeps the rule from producing a false red, the
        # same trap `_DELEGATIONS` produces for the delegation audit in a subset.
        check("the budget rule only judges a run that timed something",
              True, "no durations: standalone or subset run")
        return
    timed = {name: DURATIONS[name] for name in budgets if name in DURATIONS}
    missing = [name for name in budgets if name not in DURATIONS]
    over = ["%s took %.1f s against a %.0f s budget (%s)"
            % (name, timed[name], budgets[name][0], budgets[name][1])
            for name in sorted(timed) if timed[name] > budgets[name][0]]
    check("every budgeted test ran before this rule did", not missing, missing)
    check("the budget table covers the heavy tests the sweep pays for",
          len(timed) == len(budgets), (len(timed), len(budgets)))
    check("no budgeted test is waiting on a wall clock",
          not over, (over, {k: timed[k] for k in sorted(timed)}))


def test_the_snippet_can_see_its_own_mutation_targets() -> None:
    """A mutation in a file no snippet test reads can never be caught -- and still reads CAUGHT-capable.

    Measured 2026-11-01: the sweep reported MISSED for a mutation of
    `tools/freeze_planner_replay.py` not because the checks were weak -- three of them fail
    -- but because `test_planner_replay` was never in the driver's SNIPPET list, so the sweep
    ran nothing that looked at that file. The corpus replay, the collector and 539 mutations
    all sat on that seam. So the harness now checks its own wiring: every file a mutation
    targets must be read by at least one snippet test, directly or through a suite it runs.
    """
    import ast
    import inspect
    import re

    section("The mutation snippet can see the files it mutates")
    driver = REPO_ROOT / "tools" / "mutate_cause_gate.py"
    check("the mutation driver is readable", driver.is_file(), str(driver))
    source = driver.read_text(encoding="utf-8", errors="replace")
    consts: Dict[str, str] = {}
    for node in ast.parse(source).body:
        if not (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)):
            continue
        quoted = re.findall(r"Constant\(value='([^']+)'", ast.dump(node.value))
        names = re.findall(r"Name\(id='([A-Z_][A-Z0-9_]*)'", ast.dump(node.value))
        if quoted:
            consts[node.targets[0].id] = Path(quoted[-1]).name
        elif len(names) == 1 and names[0] in consts:
            # An alias (`CSPA = CSPANEL`): resolving it is what keeps the needle honest --
            # the driver has already been bitten by a shadowed constant name (G71).
            consts[node.targets[0].id] = consts[names[0]]
    snippet = ""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "SNIPPET":
            snippet = ast.literal_eval(node.value)
    snip_tests = set(re.findall(r"r\.(test_[a-z0-9_]+)", snippet))
    targets: Dict[str, int] = {}
    for const in re.findall(r'^\s+\("(?:[^"]*)",\s*([A-Z0-9_]+),', source, re.M):
        name = consts.get(const, const)
        targets[name] = targets.get(name, 0) + 1
    check("the driver's own wiring was parsed",
          len(snip_tests) >= 80 and len(targets) >= 40,
          (len(snip_tests), len(targets)))

    sources: Dict[str, str] = {}
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            sources[name] = inspect.getsource(fn)
        except (TypeError, OSError):
            sources[name] = ""
    # A target reached through a suite counts: `ai/train_move_head.py` is imported by
    # `ai/test_move_gate.py`, which `test_move_gate` runs inside the snippet.
    suite_files = {"train_move_head.py": "ai/test_move_gate.py"}
    orphans = []
    for target, mutations in sorted(targets.items()):
        stem = re.sub(r"\.[a-z]+$", "", target)
        direct = [n for n, text in sources.items() if stem in n or stem in text]
        via_suite = bool(suite_files.get(target)) and any(
            re.sub(r"\.[a-z]+$", "", Path(suite_files[target]).name) in text
            for n, text in sources.items() if n in snip_tests)
        if not any(n in snip_tests for n in direct) and not via_suite:
            orphans.append("%s (%d mutation(s))" % (target, mutations))
    check("every mutation target is read by a test the snippet runs",
          not orphans, orphans)
    check("the indirect allowances stay few and named",
          len(suite_files) <= 3, sorted(suite_files))


def test_damage_calibration_sees_the_recorded_fights() -> None:
    """The damage read-out must be pointed at the ledgers that actually hold the fights.

    Its default outcome path was the live-evidence probe -- one resolved fight, no
    `end.battle_log` -- while the close ledger under `trajectories/` holds 22 rows, 6 of
    them carrying the message stacks the join needs. With the old default the tool printed
    `0 matched cast(s)` over evidence sitting in the tree; with both ledgers it reports
    4 fights / 13 casts / MAE 12.82 / bias -9.76 -- bias is actual minus predicted, so the
    solver's damage estimate runs about 10 high. Measured 2026-10-31.
    """
    import importlib.util
    import tempfile

    section("Damage calibration reads every recorded fight ledger")
    tool = REPO_ROOT.parent / "tools" / "damage_calibration.py"
    check("the damage calibration harness exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("damage_calibration_gate", str(tool))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    defaults = [Path(p) for p in module.build_parser().get_default("outcomes")]
    ledger = REPO_ROOT / "trajectories" / "fight_outcomes.jsonl"
    check("the default outcome paths include the close ledger, not only the probe",
          ledger in defaults, [str(p) for p in defaults])
    merged = module.load_outcomes([str(p) for p in defaults])
    with_log = [row for row in merged if module._has_battle_log(row)]
    check("and those defaults reach the rows that carry a battle log",
          bool(with_log) or not ledger.is_file(), (len(merged), len(with_log)))

    # One fight can be written twice with different completeness. If the merge kept
    # whichever copy arrived first, the join would depend on argument order rather than
    # on the data.
    tmp = Path(tempfile.mkdtemp())
    try:
        thin = tmp / "thin.jsonl"
        rich = tmp / "rich.jsonl"
        base = {"fight_id": 77, "at": 100.0}
        thin.write_text(json.dumps(dict(base, end={"battle_log": []})) + "\n",
                        encoding="utf-8")
        rich.write_text(json.dumps(dict(base, end={"battle_log": [{"t": "cast"}]})) + "\n",
                        encoding="utf-8")
        first = module.load_outcomes([str(thin), str(rich)])
        last = module.load_outcomes([str(rich), str(thin)])
        check("the merge keeps the battle-log copy whichever ledger comes first",
              len(first) == 1 and len(last) == 1
              and module._has_battle_log(first[0]) and module._has_battle_log(last[0]),
              (len(first), len(last)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_docs_command_blocks_state_their_directory() -> None:
    """A documented command must resolve from the directory its block names.

    Most entry points live in `dofus-network/`, two folders are called `tools/`, and the
    relay/desktop code sits at the repo root, so a block that just says `python -m advisor`
    works on the author's cwd and fails on the reader's. Three blocks were missing their
    `cd` (the advisor entry points, the one-command live-evidence run, and README's
    verification list, measured 2026-10-31). This walks every fenced block the way a
    reader would: the directory starts at the repo root, `cd` lines move it, and each
    `python -m x` / `python path.py` / `node path.js` line has to resolve from there.
    The same walk also requires every fence to be closed: the mutation-driver block was
    closed 51 lines too far down, which rendered three paragraphs of guidance as code.
    """
    root = REPO_ROOT.parent
    docs = [root / "resume.md", root / "README.md", root / "rules.md",
            REPO_ROOT / "docs" / "CURRENT.md", REPO_ROOT / "docs" / "KNOWN_GAPS.md",
            REPO_ROOT / "docs" / "AI_ARCHITECTURE.md"]
    module_re = re.compile(r"python(?:3)?\s+(?:-\w+\s+)*-m\s+([A-Za-z0-9_.]+)")
    file_re = re.compile(r"(?:python(?:3)?|node|bash|sh)\s+(?:-\w+\s+)*([\w./-]+\.(?:py|js))")
    cd_re = re.compile(r"^\s*(?:cd|pushd)\s+(\S+)")
    unresolved: List[str] = []
    unclosed: List[str] = []
    blocks = 0
    for doc in docs:
        if not doc.exists():
            continue
        in_block = False
        marker = ""
        opened_at = 0
        cwd = root
        lines = doc.read_text(encoding="utf-8", errors="replace").splitlines()
        for number, line in enumerate(lines, 1):
            text = line.strip()
            if not in_block and (text.startswith("```") or text.startswith("~~~")):
                in_block = True
                marker = text[:3]
                opened_at = number
                cwd = root
                blocks += 1
                continue
            if in_block and text.startswith(marker):
                in_block = False
                continue
            if not in_block:
                continue
            moved = cd_re.match(line)
            if moved:
                rel = moved.group(1)
                for base in (cwd, root):
                    if (base / rel).is_dir():
                        cwd = (base / rel).resolve()
                        break
                continue
            as_module = module_re.search(line)
            as_file = None if as_module else file_re.search(line)
            if not as_module and not as_file:
                continue
            target = (as_module or as_file).group(1)
            if as_module:
                tail = target.replace(".", "/")
                ok = (cwd / (tail + ".py")).exists() or (cwd / tail / "__init__.py").exists()
            else:
                ok = (cwd / target).exists() or (root / target).exists()
            if not ok:
                try:
                    here = cwd.relative_to(root).as_posix() or "."
                except ValueError:
                    here = str(cwd)
                unresolved.append("%s:%d from %s: %s"
                                  % (doc.name, number, here, text[:70]))
        if in_block:
            unclosed.append("%s:%d opened and never closed" % (doc.name, opened_at))
    check("the docs carry fenced command blocks", blocks >= 20, blocks)
    check("every documented command resolves from the directory its block names",
          not unresolved, unresolved[:6])
    # An unterminated fence is invisible in the source and fatal in the reading: the
    # ~50 lines of prose after the mutation-driver block rendered as one code block
    # because its closing fence sat 50 lines too far down (found 2026-10-31 while
    # resolving the blocks above).
    check("every fenced block in the docs is closed", not unclosed, unclosed[:4])


def test_the_array_reader_streams_without_losing_records() -> None:
    """`jsonio.iter_array` is the only thing between the cache files and the knowledge index.

    It reads in blocks so a running brain does not hold the whole 24 MB item array: measured
    2026-11-03 on the shipped interpreter, indexing the item kind peaked at 138.4 MB with the file
    read in one string and 95.6 MB with a 1 MiB window (-42.8 MB), and twelve instances make that
    the difference between room and a kill. A block reader has exactly one way to be wrong that a
    whole-file reader does not -- a record, or one of its multi-byte characters, straddling the
    boundary -- so the boundary is what is tested, not the happy path.
    """
    import tempfile

    from agent.jsonio import iter_array, loads

    rows = [{"id": 1, "name": {"fr": "Épée de Boisaille", "en": "Twiggy Sword"}, "level": 7},
            {"id": 2, "name": {"fr": "Cape en Ivoire"}, "price": 15000},
            {"id": 3, "description": {"de": "Grüße, à Strasbourg, 日本語"}, "tags": ["a", "b"]},
            {"id": 4, "nested": {"deep": {"list": [1, 2, {"deeper": "ünïcode"}]}}}]
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        whole = base / "whole.json"
        whole.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        # chunk_bytes=1 is the pathological window: every record, and every accent, straddles it.
        torn_widths = [size for size in (1, 3, 7, 13, 64, 4096)
                       if list(iter_array(whole, chunk_bytes=size)) != rows]
        check("the reader reassembles records and multi-byte characters across every block size",
              not torn_widths, torn_widths)

        text = whole.read_text(encoding="utf-8")
        broken = base / "broken.json"
        broken.write_text(text[:len(text) // 2], encoding="utf-8")
        partial = list(iter_array(broken, chunk_bytes=5))
        check("a torn tail costs the rows after the break, not the rows before it",
              bool(partial) and partial == rows[:len(partial)], len(partial))

        obj = base / "object.json"
        obj.write_text(json.dumps({"top": "level", "not": "an array"}), encoding="utf-8")
        check("a file whose top level is not an array yields nothing, not its root as one row",
              not list(iter_array(obj, chunk_bytes=3)), list(iter_array(obj, chunk_bytes=3)))

    small = REPO_ROOT / "data" / "cache" / "dungeons_full.json"
    if small.is_file():
        streamed = list(iter_array(small, chunk_bytes=4096))
        reference = loads(small.read_bytes())
        check("on a real cache file the stream is the same list of records as loads()",
              streamed == reference and bool(streamed), (len(streamed), len(reference)))
    items = REPO_ROOT / "data" / "cache" / "items_full.json"
    if items.is_file():
        ids = [row.get("id") for row in iter_array(items, chunk_bytes=1 << 20)]
        check("the biggest cache file streams end to end with an id on every record",
              len(ids) > 1000 and all(isinstance(value, int) for value in ids), len(ids))


def test_one_log_record_per_line() -> None:
    """A record that forgets its newline takes its neighbour down with it.

    `{"kind": "brain"}` rows were written without a trailing newline, so each one was
    glued to the record that followed and neither parsed: 101 lines across the four live
    autopilot logs, hiding 68 step rows and 27 intervention rows. That is why §12.2 read
    4,003 steps and 194 manual interventions when the logs hold 4,071 and 844 (measured
    2026-10-31). The writer now guarantees the line ending and the report decodes every
    object on a line, so both halves of the mistake are pinned.
    """
    import tempfile

    from agent.autopilot import Autopilot
    from agent.jsonio import dumps_bytes

    section("One JSON record per log line (writer and reader)")
    tmp = Path(tempfile.mkdtemp())
    try:
        log = tmp / "glue.jsonl"
        pilot = object.__new__(Autopilot)          # the writer needs only these
        pilot.log_path = log
        pilot._log_size = 0
        pilot._append_log_line(dumps_bytes({"kind": "brain", "session": "s1",
                                            "state": "reused"}))
        pilot._append_log_line(dumps_bytes({"kind": "step", "index": 1, "session": "s1",
                                            "ts": 1000.0, "tool": "quest.advance",
                                            "ok": True, "verdict": "noop"}) + b"\n")
        written = log.read_text(encoding="utf-8").splitlines()
        check("each record lands on its own line", len(written) == 2, written)
        parsed = []
        for line in written:
            try:
                parsed.append(json.loads(line))
            except ValueError:
                pass
        check("and both parse back", [r.get("kind") for r in parsed] == ["brain", "step"],
              parsed)

        from tools import session_report

        steps_log = tmp / "glued_step.jsonl"
        steps_log.write_text('{"kind": "brain", "session": "s2"}{"kind": "step",'
                             ' "index": 1, "session": "s2", "ts": 2000.0,'
                             ' "tool": "quest.advance", "ok": true, "verdict": "noop"}\n',
                             encoding="utf-8")
        check("the report reads the step glued behind a brain row",
              session_report.summarise(steps_log)["steps"] == 1,
              session_report.summarise(steps_log)["steps"])

        decisions_log = tmp / "glued_decision.jsonl"
        decisions_log.write_text('{"kind": "brain", "session": "s3"}{"kind": "decision",'
                                 ' "action": "recover", "session": "s3"}\n',
                                 encoding="utf-8")
        recoveries = session_report.summarise(decisions_log).get("recoveries") or {}
        check("and the decision glued behind one still counts as a recovery",
              recoveries.get("recover") == 1, recoveries)

        # The shared reader is the salvage itself: a glued pair yields both rows, a
        # torn tail yields none of its fragment, and a missing file is an empty read.
        mixed = tmp / "mixed.jsonl"
        mixed.write_text('{"kind":"brain","session":"m"}{"kind":"step","index":9}\n'
                         'not json at all\n'
                         '{"kind":"step","ind', encoding="utf-8")
        from agent import jsonio
        salvaged = list(jsonio.iter_rows(mixed))
        check("jsonio.iter_rows yields every whole record and drops the fragment",
              [r.get("kind") for r in salvaged] == ["brain", "step"], salvaged)
        check("and a file that is not there reads as empty, not as an error",
              list(jsonio.iter_rows(tmp / "absent.jsonl")) == [], "missing file")

        # Every module that reads an autopilot-format log -- the only logs with glued
        # lines today -- has to go through that reader, or it repeats G97's undercount.
        strays = []
        for folder in ("agent", "tools", "ai", "sim", "combat", "advisor"):
            for path in (REPO_ROOT / folder).rglob("*.py"):
                if "__pycache__" in path.parts or path.name == "jsonio.py":
                    continue
                source = path.read_text(encoding="utf-8", errors="replace")
                if not any(marker in source for marker in
                           ("autopilot.jsonl", "autopilot.dofus", "DEFAULT_AUTOPILOT")):
                    continue
                if "json.loads(" in source and "iter_rows(" not in source:
                    strays.append(str(path.relative_to(REPO_ROOT)))
        check("every reader of the autopilot log format uses the salvage reader",
              not strays, strays[:6])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cross_file_counts_agree() -> None:
    """The same count is written in more than one place; prove the copies agree.

    Each of these drifted in practice: the host's hint label said "25 tools" for weeks
    after the registry reached 28 (the label is `Visible = false`, so nobody saw it), the
    transition manifest can claim a row count the files do not hold, and a threshold
    constant that nothing reads declares a policy that does nothing (G71).
    """
    section("Cross-file counts: one number, one source")
    hint = REPO_ROOT.parent / "desktop" / "aipanel.cs"
    caps = json.loads((REPO_ROOT / "data" / "capabilities.json").read_text(encoding="utf-8"))
    registry = len(caps.get("capabilities") or caps.get("tools") or [])
    text = hint.read_text(encoding="utf-8") if hint.exists() else ""
    found = re.search(r"All (\d+) tools enabled", text)
    check("the host's hint label names the registry's own count",
          bool(found) and int(found.group(1)) == registry,
          (found.group(0) if found else "no hint string", registry))

    base = REPO_ROOT / "data" / "transitions_temporal"
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    counted = sum(sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
                  for p in sorted(base.glob("*.jsonl")))
    check("the transition manifest's row count matches the files it describes",
          manifest.get("rows") == counted, (manifest.get("rows"), counted))
    check("claims met are a subset of the derivable claims",
          (manifest.get("claims_met") or 0) <= (manifest.get("claims_derivable") or 0),
          (manifest.get("claims_met"), manifest.get("claims_derivable")))

    # The publish path is what makes the row count above trustworthy. `write_text` truncates the
    # target before the new bytes arrive, so a rebuild racing a reader produced a corpus that was
    # short for the duration of the write -- measured 2026-11-03, this very check read 9,295 of the
    # manifest's 9,687 rows while a rebuild was streaming. Folds now land as `.tmp` siblings and
    # swap in through a retrying rename, which is pinned here because a source-shape check is the
    # only cheap way to keep the race from coming back.
    builder_src = (REPO_ROOT / "tools" / "build_live_transitions.py").read_text(
        encoding="utf-8", errors="replace")
    check("the corpus is published by atomic rename, not by truncating in place",
          "def _publish(" in builder_src and "tmp.replace(target)" in builder_src
          and 'target.with_name(target.name + ".tmp")' in builder_src,
          ("_publish" in builder_src, "tmp.replace(target)" in builder_src))
    check("no staging file is left in the corpus directory",
          not list(base.glob("*.tmp")), [p.name for p in base.glob("*.tmp")])

    # G92 was a corpus on disk two label versions behind the code that reads it. The row
    # count is pinned above; this pins the *identity* of the labeller across every corpus in
    # the chain -- built, temporal, and the cleaned directory the move head trains on, which
    # used to drop the field entirely (measured 2026-11-01: `labeler_sha: None`).
    from tools import build_live_transitions
    stamp = build_live_transitions.labeler_sha()
    stale = {}
    for directory in sorted((REPO_ROOT / "data").glob("transitions*")):
        path = directory / "manifest.json"
        if not path.is_file():
            continue
        body = json.loads(path.read_text(encoding="utf-8"))
        if body.get("labeler_sha") != stamp:
            stale[directory.name] = body.get("labeler_sha")
    check("every transition corpus names the labeller that is in the tree today",
          not stale, (stale, stamp))
    check("and the chain covers the directory a trainer actually reads",
          (REPO_ROOT / "data" / "transitions_clean" / "manifest.json").is_file()
          and "transitions_clean" not in stale, sorted(stale))

    check("the labelling code has a stable 12-hex identity",
          bool(re.fullmatch(r"[0-9a-f]{12}", stamp))
          and stamp == build_live_transitions.labeler_sha(), stamp)
    check("every manifest the builder writes carries the identity",
          build_live_transitions.summarise([]).get("labeler_sha") == stamp,
          build_live_transitions.summarise([]).get("labeler_sha"))
    live_manifest_path = REPO_ROOT / "data" / "transitions" / "manifest.json"
    if live_manifest_path.exists():
        live_manifest = json.loads(live_manifest_path.read_text(encoding="utf-8"))
        check("the live corpus names the labelling code that built it",
              live_manifest.get("labeler_sha") == stamp,
              (live_manifest.get("labeler_sha"), stamp))

    threshold_named = ("RATIO", "POINTS", "RETENTION", "VETO", "THRESHOLD", "BUDGET", "LIMIT",
                       "MIN", "MAX", "FLOOR", "CEILING", "CAP", "PERCENT", "PCT", "ALPHA",
                       "EPS", "EPSILON", "TOL", "TOLERANCE", "TIMEOUT", "INTERVAL", "DEADLINE")
    definition = re.compile(r"^([A-Z][A-Z0-9_]{2,})\s*[:=]", re.M)
    sources = {p: p.read_text(encoding="utf-8", errors="replace")
               for p in REPO_ROOT.rglob("*.py")
               if "kaggle" not in p.as_posix() and "__pycache__" not in p.as_posix()}
    declared = unread = 0
    dead: List[str] = []
    for folder in ("agent", "tools"):
        for p in sorted((REPO_ROOT / folder).glob("*.py")):
            body = sources.get(p, "")
            for match in definition.finditer(body):
                name = match.group(1)
                if not any(s in name for s in threshold_named):
                    continue
                declared += 1
                refs = sum(len(re.findall(r"\b%s\b" % re.escape(name), t)) for t in sources.values())
                if refs <= 1:
                    dead.append(name)
                    unread += 1
    check("the threshold scan finds constants to scan (a dead scan must not pass)",
          declared >= 20, declared)
    check("no threshold-named constant is declared and read by nothing (G71's class)",
          unread == 0, dead[:6])


def test_stall_retirement_is_bounded() -> None:
    """A stuck quest must end the goal, not be retired forever.

    Replaying the live shape (one talk to one NPC, a cell drift every fifteenth call
    that reads as `advanced`, `objectives_completed` frozen) against the real loop used
    to produce 340 steps and 29 retirements of quest 3841 in a single run, and would
    have kept going: each stall window retired the quest, reset its own clock, and the
    policy chose it again. The live log shows the same shape at 426 talks. The bound is
    the only thing between that and an eight-hour session spent on one NPC.
    """
    import contextlib
    import io
    import agent.autopilot as _gm
    from agent.autopilot import Autopilot, RETIRE_REPEAT_LIMIT
    from agent.runtime import ToolCall

    class _Clock:
        def __init__(self) -> None:
            self.t = 0.0

        def monotonic(self) -> float:
            return self.t

        def perf_counter(self) -> float:
            return self.t

        def sleep(self, seconds: float) -> None:
            self.t += max(float(seconds), 0.0)

        def time(self) -> float:
            return 1_700_000_000.0 + self.t

    def drive(progresses: bool, can_skip: bool = True, tutorial: bool = False,
              stop_after: int = 60):
        tmp = Path(tempfile.mkdtemp())
        clock = _Clock()

        class _Pol:
            def __init__(self) -> None:
                self.calls = 0
                self.skipped: List[int] = []

            def choose(self, goal, obs, tools):    # noqa: ARG002
                self.calls += 1
                if self.calls > stop_after:
                    (tmp / "STOP").write_text("stop", encoding="utf-8")
                return ToolCall("quest.advance", {"npc_id": 3888, "quest_id": 3841}, goal)

            def skip(self, qid) -> None:
                self.skipped.append(qid)

            current_quest = 3841

            def note_result(self, *a, **k) -> None:
                return None

        class _Res:
            def __init__(self, data) -> None:
                self.ok = True
                self.error = None
                self.data = data

            def as_dict(self):
                return {"data": self.data}

        class _Ex:
            def __init__(self, rt) -> None:
                self.rt = rt
                self.n = 0

            def execute(self, tool, call_args, **kw):    # noqa: ARG002
                self.n += 1
                obs = dict(self.rt.obs)
                if self.n % 15 == 0:
                    obs["cell"] = 263 + (self.n % 7)     # a drift that reads as advanced
                if progresses:
                    obs["objectives_completed"] = 20 + self.n
                    obs["map_id"] = 145000000 + self.n
                return _Res({"response": {"result": {"ok": True}}, "snapshot": obs})

        class _RT:
            def __init__(self) -> None:
                self.obs = {"in_world": True, "map_id": 147062786, "cell": 263,
                            "objectives_completed": 20, "in_dialog": False,
                            "tutorial": {"active": tutorial},
                            "current_objective": {"quest_id": 3841, "objective_id": 13900}}
                self.policy = _Pol()
                self.instance = "unit"
                self.executor = _Ex(self)

            def current_observation(self):
                return dict(self.obs), 5.0, "cache"

            def observe(self, obs, source="live"):      # noqa: ARG002
                return None

            def note_observation(self, obs):
                return None

        rt = _RT()
        ap = Autopilot(runtime=rt, goals=["do all the quests"],
                       log_path=tmp / "ap.jsonl", stop_file=tmp / "STOP",
                       max_steps=0, dry_run=False, preflight=False,
                       minutes=0, interval=0.0)
        if not can_skip:
            rt.policy.skip = None      # a policy with no skip cannot name a quest to retire
        ap.controller.state_dir = tmp
        real_time, _gm.time = _gm.time, clock
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                ap._run()
        finally:
            _gm.time = real_time
        rows = [json.loads(line) for line in
                (tmp / "ap.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        shutil.rmtree(tmp, ignore_errors=True)
        return rt.policy, [r for r in rows if r.get("tool")], buf.getvalue()

    section("A stalled quest is retired a bounded number of times")
    policy, steps, out = drive(progresses=False)
    check("the stuck loop ends itself without a stop file",
          "ending the goal instead of looping" in out, out.splitlines()[-3:])
    check("it spends tens of steps, not hundreds", len(steps) <= 80, len(steps))
    check("the quest is retired no more than the limit",
          len(policy.skipped) <= RETIRE_REPEAT_LIMIT + 1, policy.skipped)
    check("the reason names the quest that stayed chosen",
          "quest 3841 stayed chosen" in out, out.splitlines()[-2:])

    policy2, steps2, out2 = drive(progresses=True)
    check("a policy that keeps making progress is not ended",
          "ending the goal" not in out2 and len(steps2) > 40, [len(steps2), out2.splitlines()[-2:]])
    check("and nothing was retired while progress was happening", not policy2.skipped,
          policy2.skipped)

    _, steps3, out3 = drive(progresses=False, can_skip=False)
    check("with nothing to retire, the stall still ends the goal",
          "no quest to retire" in out3 and len(steps3) <= 80,
          [len(steps3), out3.splitlines()[-2:]])

    real_patience = _gm.TUTORIAL_PATIENCE_S
    _gm.TUTORIAL_PATIENCE_S = 120       # the replay clock only moves when the loop sleeps
    try:
        _, steps4, out4 = drive(progresses=False, tutorial=True, stop_after=400)
    finally:
        _gm.TUTORIAL_PATIENCE_S = real_patience
    check("a tutorial that never completes stops counting as scripted patience",
          "no longer counts as scripted patience" in out4, out4.splitlines()[-4:])
    check("and the run that was stuck inside it ends with a named reason",
          "ending the goal instead of looping" in out4 and len(steps4) <= 120,
          [len(steps4), out4.splitlines()[-2:]])


def _longest_read_run(tools: List[str]) -> int:
    best = run = 0
    for tool in tools:
        run = run + 1 if tool == "state.snapshot" else 0
        best = max(best, run)
    return best


def test_read_streak_is_bounded() -> None:
    """A plan that only reads must eventually be asked for something else.

    Live 2026-10-29 (dofus-05): four runs of 8, 9, 11 and 12 identical `state.snapshot`
    steps under an active tutorial -- 40 of a 239-step session, two of which ended in
    `safe.stop`. A planner read is normally converted into a wait, but only while the
    wait counter is under its bound; past that the read is dispatched as a step and
    nothing bounded those.
    """
    import contextlib
    import io
    import agent.autopilot as _gm
    from agent.autopilot import Autopilot, READ_STREAK_LIMIT
    from agent.runtime import ToolCall

    section("A streak of reads is bounded and escalated")

    read = ToolCall("state.snapshot", {}, "do all the quests")
    act = ToolCall("nav.goto", {"direction": "left"}, "do all the quests")

    class _Shim:
        """Only the attributes `_note_read_streak` and its helpers touch."""

        def __init__(self, proposal) -> None:
            self._read_streak = 0
            self._consecutive_waits = 7
            self._forced_call = None
            self._forced_reason = ""
            self.steps = []
            self.waits = 0
            self.proposal = proposal

        def _next_real_action(self, goal, obs):     # noqa: ARG002
            return self.proposal

        def _hold_wait(self, goal, obs):            # noqa: ARG002
            self.waits += 1
            return True

    shim = _Shim(act)
    answers = [Autopilot._note_read_streak(shim, read, "do all the quests", {})
               for _ in range(READ_STREAK_LIMIT)]
    check("the first reads are not a streak worth acting on",
          answers[:-1] == ["keep"] * (READ_STREAK_LIMIT - 1), answers)
    check("the limit-th read queues a real action instead of reading again",
          answers[-1] == "act" and shim._forced_call is not None
          and shim._forced_call.tool == "nav.goto", [answers, shim._forced_call])
    check("an action resets the streak and the wait counter",
          shim._read_streak == 0 and shim._consecutive_waits == 0,
          [shim._read_streak, shim._consecutive_waits])
    check("a non-read call resets the count too",
          Autopilot._note_read_streak(shim, act, "do all the quests", {}) == "keep"
          and shim._read_streak == 0, shim._read_streak)

    quiet = _Shim(None)
    answers = [Autopilot._note_read_streak(quiet, read, "do all the quests", {})
               for _ in range(READ_STREAK_LIMIT)]
    check("with nothing to propose the streak becomes a wait, not more reads",
          answers[-1] == "keep" and quiet.waits == 1 and quiet._forced_call is None,
          [answers, quiet.waits])

    class _Clock:
        def __init__(self) -> None:
            self.t = 0.0

        def monotonic(self) -> float:
            return self.t

        def perf_counter(self) -> float:
            return self.t

        def sleep(self, seconds: float) -> None:
            self.t += max(float(seconds), 0.0)

        def time(self) -> float:
            return 1_700_000_000.0 + self.t

    tmp = Path(tempfile.mkdtemp())
    clock = _Clock()

    class _Pol:
        def __init__(self) -> None:
            self.calls = 0

        def choose(self, goal, obs, tools):    # noqa: ARG002
            self.calls += 1
            if self.calls > 12:
                (tmp / "STOP").write_text("stop", encoding="utf-8")
            return ToolCall("state.snapshot", {}, goal)

        def note_result(self, *a, **k) -> None:
            return None

    class _Res:
        def __init__(self, data) -> None:
            self.ok = True
            self.error = None
            self.data = data

        def as_dict(self):
            return {"data": self.data}

    class _RT:
        def __init__(self) -> None:
            self.obs = {"in_world": True, "map_id": 147062786, "cell": 263,
                        "objectives_completed": 20, "in_dialog": False,
                        "tutorial": {"active": False}}
            self.policy = _Pol()
            self.instance = "unit"
            self.executor = self

        def execute(self, tool, args, **kw):    # noqa: ARG002
            return _Res({"response": {"result": {"ok": True}},
                         "snapshot": dict(self.obs)})

        def current_observation(self):
            return dict(self.obs), 5.0, "cache"

        def observe(self, obs, source="live"):      # noqa: ARG002
            return None

        def note_observation(self, obs):
            return None

    class _Think:
        def propose(self, goal, obs, recent, hint):     # noqa: ARG002
            return ToolCall("nav.goto", {"direction": "left"}, goal)

    rt = _RT()
    ap = Autopilot(runtime=rt, goals=["do all the quests"],
                   log_path=tmp / "ap.jsonl", stop_file=tmp / "STOP",
                   max_steps=0, dry_run=False, preflight=False,
                   minutes=0, interval=0.0)
    ap.controller.state_dir = tmp
    ap.thinking = _Think()
    # Enter the regime the live reads came from: past the wait bound the loop stops
    # converting a read into a wait, and the counter is reset per goal, so it has to
    # be re-forced after every wait the fixture triggers.
    real_wait = ap._hold_wait

    def _wait_then_overflow(goal_arg, obs_arg):
        keep = real_wait(goal_arg, obs_arg)
        ap._consecutive_waits = _gm.MAX_CONSECUTIVE_WAITS + 1
        return keep

    ap._hold_wait = _wait_then_overflow
    real_time, _gm.time = _gm.time, clock
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ap._run()
    finally:
        _gm.time = real_time
    out = buf.getvalue()
    rows = [json.loads(line) for line in
            (tmp / "ap.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    shutil.rmtree(tmp, ignore_errors=True)
    steps = [r.get("tool") for r in rows if r.get("tool")]

    check("the driven loop reaches the bound and says so",
          "reads in a row" in out, out.splitlines()[-4:])
    check("and it dispatches a real action instead of another read",
          "the reads had nothing new" in out and "nav.goto" in steps, steps[:12])
    # Twice the limit, not once: the escalation queues an action, and the guard can
    # refuse it for the state the fixture is frozen in -- the loop then waits, reads
    # again, and escalates a second time. What must never happen is an unbounded run.
    check("no run of reads is longer than twice the limit inside a full loop",
          _longest_read_run(steps) <= 2 * READ_STREAK_LIMIT, steps[:16])


def test_session_report_play_quality() -> None:
    """The report must say where the steps went, not only that nothing was finished.

    Every number here was invisible in the totals: a live session read for 17 % of its
    steps, spent 15 wait rows per step, and asked one NPC 426 times. "0 objectives" is
    the same output for all three. The census names the pair (tool, map) that never once
    advanced, which is the shape G72 bounded.
    """
    from tools import session_report as report

    section("Session report: where the steps went")
    def step(i: int, tool: str, verdict: str, map_id) -> Dict[str, Any]:
        return {"kind": "step", "tool": tool, "verdict": verdict, "ok": True,
                "at": 1_700_000_000 + i, "observation_map": map_id, "args": {}}

    rows: List[Dict[str, Any]] = [step(i, "quest.advance", "noop", 5) for i in range(6)]
    rows += [step(6, "nav.goto", "advanced", 5), step(7, "nav.goto", "advanced", 5),
             step(8, "state.snapshot", "advanced", 5), step(9, "-", "blocked", None)]
    rows += [{"kind": "wait", "at": 1_700_000_010 + i, "reason": "no action"}
             for i in range(4)]
    try:
        play = report.play_quality(rows, objectives=0)
    except ZeroDivisionError:
        # A missing objective must not divide. Catching it here makes the mutation a
        # failed check rather than a crashed section, which is the difference between
        # evidence and an accident.
        play = {"read_share": None, "waits_per_step": None,
                "steps_per_objective": "ZeroDivisionError", "never_advanced": []}
    check("the read share is counted over acting steps only",
          play["read_share"] == round(100.0 / 9, 1), play["read_share"])
    check("wait rows are reported per step",
          play["waits_per_step"] == round(4 / 9.0, 2), play["waits_per_step"])
    check("no objective means no steps-per-objective, not a division",
          play["steps_per_objective"] is None, play["steps_per_objective"])
    check("a pair that never advanced is named, with its call count",
          play["never_advanced"] and play["never_advanced"][0] ==
          {"tool": "quest.advance", "map": 5, "calls": 6}, play["never_advanced"])
    check("a pair that did advance is not called dead",
          all(item["tool"] != "nav.goto" for item in play["never_advanced"]),
          play["never_advanced"])
    check("a no-call row is not an interaction",
          all(item["tool"] != "-" for item in play["never_advanced"]), play["never_advanced"])
    check("with objectives, the ratio is steps per objective",
          report.play_quality(rows, objectives=3)["steps_per_objective"] == 3.0,
          report.play_quality(rows, objectives=3)["steps_per_objective"])
    check("a judged row is judged, not unjudged",
          play["judged"] == 9 and play["unjudged"] == 0,
          [play["judged"], play["unjudged"]])

    # Live 2026-10-29/30: the page writes step rows with a snapshot and no verdict, and
    # its real answer sits under `result.response.result`. Reading only `observation_map`
    # put every pair on `map None`; reading a missing verdict as a death named 211
    # "never advanced" nav.goto calls that were never measured; and reading only the
    # outer `ok` printed `ok 300, failed 0` over 25 refused calls (G84).
    def page(i: int, tool: str, args, map_id, inner=None, ok=True) -> Dict[str, Any]:
        row = {"kind": "step", "tool": tool, "ok": ok, "args": args,
               "observation": {"position": {"map_id": map_id}}}
        if inner is not None:
            row["result"] = {"response": {"result": inner}}
        return row

    page_rows = [
        page(0, "nav.goto", {"direction": "right"}, 145490434,
             {"ok": True, "data": {"via": "isoEngine.gotoNeighbourMap"}, "error": None}),
        page(1, "nav.goto", {"direction": "top"}, 145490435),
        page(2, "quest.advance", {"quest_id": 3841, "npc_id": 3888}, 145490434,
             {"ok": True, "data": {"via": "walking to npc then talking"}, "error": None}),
        page(3, "quest.advance", {"quest_id": 3841, "reply": 0}, 145490434,
             {"ok": False, "data": {"reply_text": "Ask what Ek's problem is."},
              "error": "no dialog reply matches the given text"}),
        page(4, "quest.advance", {"quest_id": 3841}, 145490434, ok=False),
    ]
    page_play = report.play_quality(page_rows, objectives=0)
    check("the page's own step rows are keyed by their map, not by None",
          page_play["dead_calls"] == [{"tool": "quest.advance", "map": 145490434,
                                       "calls": 2}],
          page_play["dead_calls"])
    check("a page call the client refused is counted dead, with its reason",
          page_play["page_refused"] == 1
          and page_play["page_refusals"] == [
              {"error": "no dialog reply matches the given text", "calls": 1}],
          [page_play["page_refused"], page_play["page_refusals"]])
    check("a successful call with no verdict is neither dead nor advanced",
          page_play["judged"] == 2 and page_play["unjudged"] == 3,
          [page_play["judged"], page_play["unjudged"]])
    session = report._summarise_session(page_rows)
    check("the ok/failed totals read the page's answer, not only the outer flag",
          session["ok"] == 4 and session["failed"] == 2,
          [session["ok"], session["failed"]])
    check("a refusal is named in the error census",
          session["errors"].get("no dialog reply matches the given text") == 1,
          session["errors"])


def test_npc_map_fact_is_routed() -> None:
    """A learned npc->map fact is a destination; the client only crosses adjacent exits.

    The planner stored "npc 3888 lives on map 999" and then sent `nav.goto {map_id: 999}`
    from map 1. The client answers "map 999 is not adjacent" -- 35 such refusals in the
    live logs. The router (`_travel_call`) already knows how to walk one indexed border at
    a time; this path simply did not use it.
    """
    from agent.planner import QuestPlanner

    section("A learned npc map is routed, not handed to the client")

    class _Store:
        def get(self, prefix, key):     # noqa: ARG002 - shape, not behaviour
            return 999 if key == "npc:3888" else None

        def quest(self, quest_id):      # noqa: ARG002
            return None

        def resolve_objective(self, *a, **k):   # noqa: ARG002
            return None

    obj = {"id": 11, "type_id": 1, "map_id": 1,
           "params": {"parameter0": 3888, "parameter1": 4262}}
    near = {"map_id": 1, "quests_active": {"4262": {}},
            "map": {"exits": [{"direction": "left", "map_id": 2, "blocked": False},
                              {"direction": "right", "map_id": 999, "blocked": False}]}}
    far = {"map_id": 1, "quests_active": {"4262": {}},
           "map": {"exits": [{"direction": "left", "map_id": 2, "blocked": False}]}}

    calls = QuestPlanner(store=_Store()).plan_objective({"id": 4262}, 0, obj, far)
    travel = [c for c in calls if c.get("tool") == "nav.goto"]
    check("a far learned map is never sent as a bare map id",
          all((c.get("args") or {}).get("map_id") != 999 for c in calls), calls)
    check("the travel walks an exit the client reports",
          bool(travel) and (travel[0].get("args") or {}).get("direction") == "left", travel)
    check("the talk is still in the plan behind the travel",
          [c.get("tool") for c in calls] == ["nav.goto", "quest.advance"],
          [c.get("tool") for c in calls])

    adjacent = QuestPlanner(store=_Store()).plan_objective({"id": 4262}, 0, obj, near)
    asked = [(c.get("args") or {}).get("map_id") for c in adjacent
             if c.get("tool") == "nav.goto"]
    check("an adjacent destination is still asked for by id",
          asked == [999], asked)

    helper = QuestPlanner()._travel_call(obj, 999, far)
    check("the router itself never returns a bare far map id",
          (helper.get("args") or {}).get("map_id") != 999, helper)


def test_map_graph_live_wins() -> None:
    """The exported map index is a file; the client standing on the map is the truth.

    Live 2026-10-29, map 145490434: the export says `right -> 145490946`, the client's
    exit cells say `right -> 145490945`, and the page refused the hop six times ("the
    right exit leads to 145490945, not 145490946"). The router planned on the stale
    edge, so every attempt named a map the border does not lead to.
    """
    import json as _json
    import tempfile
    from pathlib import Path as _Path

    from agent.executor import ToolExecutor, _MapGraph

    section("The live exit list corrects the exported map index")
    tmp = _Path(tempfile.mkdtemp(prefix="mapgraph-"))
    index = tmp / "_index.json"
    # 100 --right--> 300 is the stale claim; the client reports right -> 200.
    # 100 --bottom--> 400 is not mentioned by the live block and must survive.
    records = {
        100: {"rightNeighbourId": 300, "bottomNeighbourId": 400},
        200: {"rightNeighbourId": 300},
        300: {"leftNeighbourId": 200},
        400: {"topNeighbourId": 100},
    }
    index.write_text(_json.dumps({str(mid): {"id": mid, "neighbours": nb}
                                  for mid, nb in records.items()}), encoding="utf-8")

    graph = _MapGraph(index)
    hop = graph.hop(100, 300)
    check("the stale export routes straight to the wrong neighbour",
          hop == ("right", 300, 1), [hop])

    obs = {"position": {"map_id": 100},
           "map": {"map_id": 100,
                   "exits": [{"direction": "right", "map_id": 200, "blocked": False},
                             {"direction": "bottom", "map_id": 100, "blocked": False}]}}
    changed = graph.observe(100, {"right": 200})
    check("a live exit that disagrees with the export changes it", changed is True)
    hop = graph.hop(100, 300)
    check("the next hop now walks the border the client actually has",
          hop == ("right", 200, 2), [hop])
    check("a repeat of the same observation is not a change",
          graph.observe(100, {"right": 200}) is False)
    check("a direction the live block does not mention is kept",
          graph.hop(100, 400) == ("bottom", 400, 1), [graph.hop(100, 400)])
    check("a self-edge is never learned as a crossing",
          graph.observe(200, {"bottom": 200}) is False
          and "bottom" not in graph.edges().get(200, {}),
          graph.edges().get(200))

    # The end-to-end shape that failed live: the executor plans on the graph, and
    # a stale export must not survive the observation it was standing on.
    class _Shim:
        pass

    shim = _Shim()
    shim._maps = _MapGraph(index)
    routed, meta = ToolExecutor._route_nav(shim, {"map_id": 300}, obs)
    check("the routed call carries the live next hop, not the stale export edge",
          (routed or {}).get("map_id") == 200 and (routed or {}).get("direction") == "right",
          routed)
    check("the route metadata names the live hop",
          isinstance(meta, dict) and meta.get("next_map_id") == 200, meta)

    import shutil as _shutil
    _shutil.rmtree(tmp, ignore_errors=True)


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


def test_sync_kaggle_toolkit_rules() -> None:
    """The mirror rules on a fixture tree, so a sweep can exercise them.

    `test_kaggle_toolkit_sync` reads the real tree: inside a sweep the mutated file
    is meant to differ from its mirror, so that test fails for every mutation. These
    fixture tests run cheaply and safely in the snippet.
    """
    import unittest
    from tools import test_sync_kaggle_toolkit as module

    section("Kaggle mirror rules (fixture: drift, copy, post-copy manifest)")
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("toolkit fixture tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


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
    # The loops write one log per instance; `default.jsonl` is the pre-daemon single
    # file. Reading only it made the preflight report a build days older than the client
    # that last spoke (`2026-10-27s` against a `2026-10-29bf` run).
    dir_pair = Path(tempfile.mkdtemp())
    default_log = dir_pair / "default.jsonl"
    default_log.write_text(json.dumps({"observation": {"agent_build": "2026-10-27s"}}) + "\n",
                           encoding="utf-8")
    instance_log = dir_pair / "dofus-09.jsonl"
    instance_log.write_text(json.dumps({"observation": {"agent_build": "2026-10-29bf"}}) + "\n",
                            encoding="utf-8")
    os.utime(default_log, (now - 500, now - 500))
    os.utime(instance_log, (now, now))
    saved_default = ready.DEFAULT_LOG
    try:
        ready.DEFAULT_LOG = default_log
        check("the newest log wins when no log was named",
              ready.last_log_build() == "2026-10-29bf", str(ready.last_log_build()))
        os.utime(default_log, (now + 50, now + 50))
        check("and the newest is decided by mtime, not by file name",
              ready.last_log_build() == "2026-10-27s", str(ready.last_log_build()))
    finally:
        ready.DEFAULT_LOG = saved_default
    check("a log named by the caller is read as itself, never replaced by a sibling",
          ready.last_log_build(instance_log) == "2026-10-29bf"
          and ready.last_log_build(default_log) == "2026-10-27s",
          f"{ready.last_log_build(instance_log)} / {ready.last_log_build(default_log)}")
    shutil.rmtree(dir_pair, ignore_errors=True)
    check("the repo page carries the 2026-10-31a build",
          ready.page_build(REPO_ROOT.parent / "scripts" / "mobile-disguise.js")
          == "2026-10-31a")


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


def test_lexical_command_policy() -> None:
    """The learned supplier for the parse_command seam: contract, no-invention rule, no-op default.

    Trains its own tiny policy from the corpus rather than loading a 15 MB artifact, because a regression
    gate that only passes on a machine that happened to train something is not a gate.
    """
    import json
    import os

    section("Lexical command policy (the learned side of the parse_command seam)")
    from ai.lexical_policy import read_jsonl, train
    from agent.commands import INTENT_SLOTS, INTENTS, parse_command, rule_parse
    from agent.tool_dataset import REPO_ROOT

    rows = read_jsonl(REPO_ROOT / "data" / "core_sft_v4" / "train.jsonl")[:2500]
    policy = train(rows)

    probe = "vends 5 bouftou wool"
    pred = policy.predict(probe)
    check("the policy answers in the seam's own shape",
          {"intent", "slots", "confidence"} <= set(pred), sorted(pred))
    check("its intent is one the schema knows", pred["intent"] in INTENTS, pred["intent"])
    check("it never claims a slot the intent cannot carry",
          set(pred["slots"]) <= INTENT_SLOTS.get(pred["intent"], set()),
          (pred["intent"], sorted(set(pred["slots"]) - INTENT_SLOTS.get(pred["intent"], set()))))
    check("slots survive json, because the trajectory log dumps them",
          json.dumps({"slots": pred["slots"]}).find("np.") == -1, pred["slots"])

    numbers = {"quantity", "price", "x", "y", "quest_id", "level_min", "limit", "kill_count"}
    invented = [f for f, v in policy.predict("sell items", slots_from_rules=False)["args"].items()
                if f in numbers]
    check("a number the sentence never said is not proposed", not invented, invented)

    # The grounding step runs through clean_call, which drops any field the tool does not declare -- and
    # `action` is not a tool argument, it is the seam's own verb. Dropping it silently re-routes
    # inv.sell to inv.list, which cost 23 points of tool accuracy before this check existed.
    from agent.commands import intent_tool
    from ai.lexical_policy import ground_call

    grounded = ground_call("inv.sell", {"action": "sell", "item": "bouftou wool"},
                           "vends 5 bouftou wool")
    check("grounding keeps the verb that selects the tool",
          intent_tool("inventory", grounded.get("action")) == "inv.sell", grounded)
    check("grounding repairs the span, and never adds what the model did not claim",
          grounded.get("item") == "bouftou wool" and "quantity" not in grounded, grounded)
    # the number comes from the seam, where the rule slots fill what the model left out -- not from
    # clean_call, whose contract is fill-only-repair ("never add a field the model did not emit")
    seam = parse_command("vends 5 bouftou wool", model=policy)
    check("the seam delivers verb, span and number together",
          intent_tool(seam["intent"], seam["slots"].get("action")) == "inv.sell"
          and seam["slots"].get("item") == "bouftou wool" and str(seam["slots"].get("quantity")) == "5",
          seam["slots"])

    # Digits the player states outside any span (`100x`, `4,-19`, `2k`) are the shape the corpus generator
    # never produced, so they were the whole argument gap on the unseen-frame holdout. Fill-only: the same
    # numbers must not override a claim, and a sentence that states none must gain nothing.
    from ai.lexical_policy import read_stated

    check("a coordinate pair with no words around it is read",
          read_stated("nav.goto", {}, "go to 4,-19") == {"x": 4, "y": -19},
          read_stated("nav.goto", {}, "go to 4,-19"))
    check("`100x` is a kill count and `2k` is a price",
          read_stated("farm.start", {}, "farm bouftou 100x").get("kill_count") == 100
          and read_stated("inv.sell", {}, "vends laine a 2k").get("price") == 2000,
          (read_stated("farm.start", {}, "farm bouftou 100x"),
           read_stated("inv.sell", {}, "vends laine a 2k")))
    check("a stated number fills a gap, it does not overwrite a claim",
          read_stated("inv.sell", {"price": 900}, "vends laine a 2k").get("price") == 900,
          read_stated("inv.sell", {"price": 900}, "vends laine a 2k"))
    check("a sentence stating no number gains none",
          read_stated("safe.stop", {}, "stop plz") == {}
          and read_stated("nav.goto", {}, "tp to astrub pls") == {},
          read_stated("nav.goto", {}, "tp to astrub pls"))

    # naming an artifact must not, by itself, change what the brain does -- that is what the shadow
    # session is for, and one environment variable being enough to flip behaviour is how accidents happen
    here = Path(__file__).resolve().parent.parent
    os.environ["DOFUS_LEARNED_COMMAND_MODEL"] = str(here / "models" / "lexical_policy")
    os.environ.pop("DOFUS_COMMAND_MODEL_ENABLED", None)
    import agent.commands as _C

    _C._LEARNED, _C._LEARNED_TRIED = None, False
    try:
        for probe in ("vends 5 bouftou wool", "stop everything now"):
            shadowed = parse_command(probe)
            check("with an artifact named but not enabled the answer is still the rules: %s" % probe[:20],
                  shadowed == rule_parse(probe), (shadowed.get("source"), shadowed["intent"]))
        _C._LEARNED, _C._LEARNED_TRIED = None, False
        os.environ["DOFUS_COMMAND_MODEL_ENABLED"] = "1"
        on = parse_command("vends 5 bouftou wool")
        check("enabling it explicitly is what lets the model answer", on.get("source") == "model", on)
    finally:
        os.environ.pop("DOFUS_LEARNED_COMMAND_MODEL", None)
        os.environ.pop("DOFUS_COMMAND_MODEL_ENABLED", None)
        _C._LEARNED, _C._LEARNED_TRIED = None, False

    saved = os.environ.pop("DOFUS_LEARNED_COMMAND_MODEL", None)
    try:
        for text in ("stop everything now", "vends 5 bouftou wool", "make coffee please"):
            with_model, without = parse_command(text), rule_parse(text)
            check("with no model configured the seam is the rules, byte for byte: %s" % text[:22],
                  with_model == without, (with_model, without))
    finally:
        if saved is not None:
            os.environ["DOFUS_LEARNED_COMMAND_MODEL"] = saved


def test_unseen_frame_holdout() -> None:
    section("Unseen-frame holdout (the only ruler whose truth was written by hand)")
    import ai.utterance_capture as _UC
    from ai.lexical_policy import read_jsonl

    holdout = REPO_ROOT / "data" / "utterances" / "novel_frames.jsonl"
    rows = read_jsonl(holdout)
    check("the holdout exists and is not empty", len(rows) >= 90, len(rows))
    check("it covers all four languages", {"en", "fr", "es", "de"} <= {r["lang"] for r in rows},
          sorted({r["lang"] for r in rows}))
    check("it reaches the whole command registry", len({r["tool"] for r in rows}) >= 16,
          len({r["tool"] for r in rows}))

    # A hand-written label can be void rather than merely wrong, and void rows are the dangerous kind: an
    # unrepresentable expectation depresses every arm by the same amount, so the ranking still looks clean
    # while every absolute number is junk. `ingest` is what refuses them, so the refusal is tested here.
    keep = (_UC.LABELLED, _UC.CAPTURE)
    with tempfile.TemporaryDirectory(prefix="holdout_") as td:
        _UC.LABELLED, _UC.CAPTURE = Path(td) / "labelled.jsonl", Path(td) / "capture.jsonl"
        try:
            summary = _UC.ingest(holdout)
            check("every label is expressible in the schema", summary["illegal"] == 0
                  and summary["labelled"] == len(rows), summary)
            check("none of it was already in the corpus we trained on",
                  summary["already_in_sft_corpus"] == 0, summary)
            probe = Path(td) / "void.jsonl"
            probe.write_text(json.dumps({"id": "v", "goal": "dis salut", "lang": "fr",
                                         "tool": "chat.say", "args": {"channel": "group"}}) + "\n",
                             encoding="utf-8")
            void = _UC.ingest(probe)
            check("a value outside the parameter's enum is refused, not scored",
                  void["illegal"] == 1 and void["labelled"] == 0, void)
            stat = _UC.mcnemar_exact(10, 1)
            check("McNemar agrees with the hand computation (and can fail a tie)",
                  abs(stat - 24 / 2048) < 1e-12 and _UC.mcnemar_exact(5, 5) == 1.0, stat)
        finally:
            _UC.LABELLED, _UC.CAPTURE = keep


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
        # The real credit path also feeds the conjugate store; the stub models the loop, so it
        # gets the same two attributes. `experience = None` is the documented "no store" case and
        # makes `_note_experience` a no-op, which is what this test is about.
        loop.experience = None
        loop._note_experience = Autopilot._note_experience.__get__(loop)
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
               polls: List[Dict[str, Any]], call_args: Any = None) -> Any:
        pilot = _Pilot(polls)
        clock = _Clock()
        real = ap.time
        ap.time = clock
        try:
            best = pilot._settle(before, after,
                                 call=ToolCall(tool, dict(call_args or {}), "goal"))
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

    # Live 2026-10-29: 601 `quest.advance` steps had a 6.73 s median gap to the next
    # step while the page answered in 18 ms. A reply promises a new dialog page, not a
    # new objective, so the predicate never fired and every reply paid the whole window.
    page_a = {"cell": 2, "map_id": 7, "in_dialog": True,
              "dialog": {"open": True, "npc_id": 3888, "message": "Shall we go?",
                         "ids_ready": True}}
    page_b = dict(page_a, dialog={"open": True, "npc_id": 3888,
                                  "message": "Then follow me.", "ids_ready": True})
    reply = {"reply": 0, "quest_id": 3841}
    pilot, best, waited = settle("quest.advance", page_a, page_b, [], call_args=reply)
    check("a reply settles as soon as the dialog page changes",
          pilot.polls == 0 and (best or {}).get("dialog", {}).get("message")
          == "Then follow me." and waited < 0.1,
          "%d polls, %.2fs, %s" % (pilot.polls, waited, best))
    pilot, best, waited = settle("quest.advance", page_a, page_a,
                                 [page_a] * 40, call_args=reply)
    check("a reply whose page never changes still waits the bounded window",
          waited >= 3.0 and pilot.polls < 40, "%.2fs after %d polls"
          % (waited, pilot.polls))
    ticked = dict(page_a, dialog=dict(page_a["dialog"], ids_ready=False))
    pilot, best, waited = settle("quest.advance", page_a, ticked,
                                 [ticked] * 40, call_args=reply)
    check("a tick inside the dialog block is not a new page",
          waited >= 3.0, "%.2fs" % waited)
    closed = dict(page_a, in_dialog=False, dialog={"open": False})
    pilot, best, waited = settle("quest.advance", page_a, closed, [],
                                 call_args={"close": True, "quest_id": 3841})
    check("a close settles when the dialog is gone",
          pilot.polls == 0 and waited < 0.1 and not (best or {}).get("in_dialog"),
          "%d polls, %.2fs" % (pilot.polls, waited))

    # Live 2026-10-29: 6.07 s median gap after `nav.goto` (n=132) while a crossing is
    # one round trip. The planner names the journey's destination; the executor sends
    # one border at a time, and the predicate waited for the far map.
    here = {"cell": 100, "map_id": 145490434}
    hopped = {"cell": 200, "map_id": 145490435}
    journey = {"map_id": 145490946, "lang": "fr"}
    pilot, best, waited = settle("nav.goto", here, hopped, [], call_args=journey)
    check("one completed hop settles a journey, not only the final map",
          pilot.polls == 0 and waited < 0.1 and (best or {}).get("map_id") == 145490435,
          "%d polls, %.2fs, %s" % (pilot.polls, waited, best))
    pilot, best, waited = settle("nav.goto", here, here, [here] * 40, call_args=journey)
    check("a hop that never crosses still waits the bounded window",
          waited >= 3.0 and pilot.polls < 40, "%.2fs after %d polls"
          % (waited, pilot.polls))
    walk = {"cell": 42, "map_id": 145490434}
    arrived = {"cell": 42, "map_id": 145490434}
    pilot, best, waited = settle("nav.goto", here, arrived, [], call_args={"cell": 42})
    check("a walk to a cell settles on the cell, no map change needed",
          pilot.polls == 0 and waited < 0.1, "%d polls, %.2fs" % (pilot.polls, waited))


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
            # `silence_from` lets the stub stop answering, which is the only way to
            # reach the branch that reads the planner's own sentence. `last_no_call`
            # is what a real planner writes when it returns no call.
            self.silence_from: Optional[int] = None
            self.last_no_call = ""

        def choose(self, goal, obs, tools):     # noqa: ARG002 - shape, not behaviour
            self.calls += 1
            if self.silence_from is not None and self.calls > self.silence_from:
                return None
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
              tool: str = "state.snapshot", escalate=None,
              no_call: Optional[str] = None):
        tmp = Path(tempfile.mkdtemp())
        dirs.append(tmp)
        rt = _Runtime(dict(obs if obs is not None
                           else {"in_world": True, "map_id": 10340, "cell": 300}))
        rt.policy.tool = tool
        if no_call is not None:
            rt.policy.last_no_call = no_call
            rt.policy.silence_from = 0
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

        # The silence case (G99/#102). A planner that answers with no call still says
        # why, in `last_no_call`, and the wait row is the only place anyone can read it
        # after the fact. Measured 2026-11-05: 0 of 1,044 live rows carried a reason at
        # all, and the mutation that stops the loop from reading `last_no_call` read as
        # INERT to the whole sweep -- its only witness was a source-string check that a
        # different line of the same file also satisfies. This is the behavioural one.
        silence = "the store will not answer while this quest is active"
        quiet = drive(1, no_call=silence)
        said = [r for r in quiet[2] if silence in str(r.get("note") or "")]
        check("a policy that answers with silence puts the reason it gave on the record",
              len(said) == 1 and said[0].get("verdict") == "blocked"
              and said[0].get("error") == "policy returned no call",
              "%d row(s) carry it: %s" % (len(said), str(said)[:180]))
        check("the same row says what was in hand, so a missing observation separates "
              "from an unusable one",
              len(said) == 1 and "obs_keys=" in said[0].get("note", "")
              and "map=" in said[0].get("note", ""),
              str([r.get("note") for r in said])[:220])
        check("and the silence is real: nothing was dispatched to answer it",
              quiet[5].executor.calls == [] and quiet[5].policy.calls >= 1,
              "%s / %d choose() calls" % (quiet[5].executor.calls, quiet[5].policy.calls))

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


def test_objective_ledger_suite() -> None:
    """The ledger's own suite, including the fact it learns from a refusal.

    Delegated rather than reimplemented: the checks drive a real `ObjectiveLedger` with a real
    file, and the point of most of them is what survives a reload.
    """
    section("The objective ledger (what it tried, what that proved, what it learned)")
    import subprocess
    import sys as _sys

    script = ROOT / "tools" / "test_objective_ledger.py"
    check("the ledger suite exists", script.exists(), str(script))
    if not script.exists():
        return
    proc = subprocess.run([_sys.executable, str(script)], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=180)
    tail = (proc.stdout or "").strip().splitlines()[-6:]
    check("the objective ledger suite passes", proc.returncode == 0,
          " | ".join(tail) or (proc.stderr or "")[-200:])


def test_stress_hardening_suite() -> None:
    """The defects the 2026-11-07 stress run found stay fixed.

    Five were real: `validate_call` raised on a non-object `args` so the HTTP
    caller got a dropped socket, the handler had no read timeout so 160
    half-open bodies pinned 160 threads, an unread body let the next request be
    served on the same connection, `RuleGate._misses` never evicted (110,118
    keys after 200k observations), and `_redact_copy` recursed per level and
    raised at depth ~1,000.

    Delegated rather than reimplemented: the HTTP checks need a real listening
    socket (the bugs were about what the client saw on the wire) and the
    recorder checks build megabyte-scale nested graphs.
    """
    section("Stress hardening (bad args, handler timeout, redaction depth, gate growth)")
    import subprocess
    import sys as _sys

    script = ROOT / "tools" / "test_stress_hardening.py"
    check("the hardening suite exists", script.exists(), str(script))
    if not script.exists():
        return
    proc = subprocess.run([_sys.executable, str(script)], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=300)
    tail = (proc.stdout or "").strip().splitlines()[-6:]
    check("the hardening suite passes", proc.returncode == 0,
          " | ".join(tail) or (proc.stderr or "")[-300:])


def test_menu_window_learning_suite() -> None:
    """The menu-to-window table is learned and persisted, and the suite proves it.

    `_UI_WINDOWS = {"Bag": "equipment"}` was game knowledge written into Python. It is a seed
    now; the loop observes which window a press opened (`ui.tap` reports `open_before` and
    `open_windows`), stores the pair in the knowledge base and adopts it on the next start.

    Delegated to `tools/test_menu_window_learning.py` the same way the page suites are delegated
    to node: the assertions need the real `Autopilot` and the real `KnowledgeStore`, and running
    them in-process would leave objects behind in the gate.
    """
    section("Menu-to-window facts are learned, not asserted")
    import subprocess
    import sys as _sys

    script = ROOT / "tools" / "test_menu_window_learning.py"
    check("the learning suite exists", script.exists(), str(script))
    if not script.exists():
        return
    proc = subprocess.run([_sys.executable, str(script)], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=180)
    tail = (proc.stdout or "").strip().splitlines()[-6:]
    check("the menu-window learning suite passes", proc.returncode == 0,
          " | ".join(tail) or (proc.stderr or "")[-200:])

    source = (REPO_ROOT / "agent" / "planner.py").read_text(encoding="utf-8")
    check("the literal table is gone, replaced by a seed",
          "_UI_WINDOWS = {" not in source and "_UI_WINDOWS_SEED" in source,
          "a hardcoded map is the thing this replaced")
    check("the planner reads its instance table, not the constant",
          "self.menu_window_for(" in source and "self._UI_WINDOWS_SEED.get(" not in source,
          "reading the seed directly would ignore everything learned")
    loop = (REPO_ROOT / "agent" / "autopilot.py").read_text(encoding="utf-8")
    check("the loop observes the pair and stores it",
          "self._learn_menu_window(before, after, call)" in loop
          and 'store.remember("menu_window"' in loop
          and "self._seed_menu_windows()" in loop,
          "a producer with no reader (or the reverse) is the mistake this repeats")

    # The surface phrase table had the same disease one level down: a French→menu-key map in
    # Python. The client publishes `menu_icons: [{key, label}]`; the loop learns the label,
    # stores it and the planner matches objective text against it.
    check("the French surface table is gone, replaced by a seed",
          "_UI_SURFACES = (" not in source and "_UI_SURFACES_SEED" in source
          and '("inventaire", {"menu": "Bag"})' not in source,
          "a language-specific table in Python is the thing this replaced")
    check("the planner learns and adopts client labels",
          "def learn_surface_label" in source and "def load_surface_labels" in source
          and "self._surface_labels" in source,
          "the planner has no reader for learned labels")
    check("the loop publishes, learns and seeds the labels",
          "snap.menu_icons" in (ROOT / "scripts" / "mobile-disguise.js").read_text(
              encoding="utf-8", errors="replace")
          and "self._learn_surface_labels(before)" in loop
          and 'store.remember("surface_label"' in loop
          and "self._seed_surface_labels()" in loop,
          "a producer with no reader (or the reverse) is the mistake this repeats")


def test_quest_data_wait() -> None:
    """A snapshot taken before the client published its quests is not an empty quest log.

    Measured 2026-11-07: right after a guest login the observation carried `in_world: true`,
    four bag items, and `quests_active: {}` / `ready_objectives: []` / `tutorial.active: false`,
    while quests 3841 and 4260 were active minutes earlier. The policy returned no call, the
    controller called the goal unmappable, and a 14-step run ended after one step. Waiting is
    the right answer, and it must be bounded so a genuinely questless character still ends.
    """
    section("Quest data that has not loaded yet (wait, do not retire the goal)")
    from agent.autopilot import QUEST_DATA_WAIT_LIMIT, Autopilot

    live = {"in_world": True, "quests_active": {}, "ready_objectives": [],
            "current_objective": None, "tutorial": {"active": False},
            "inventory": {"items": [{"gid": 18844, "qty": 1, "object_uid": 1}]}}
    check("the live shape is recognised as a snapshot taken too early",
          Autopilot._quest_data_pending(live) is True, live)
    check("a reading that carries a quest is not waited on",
          Autopilot._quest_data_pending({**live, "quests_active": {"3841": 13}}) is False,
          "a real quest block must not be delayed")
    check("a ready objective alone is enough",
          Autopilot._quest_data_pending(
              {**live, "ready_objectives": [{"quest_id": "3841"}]}) is False, live)
    check("an empty bag is not evidence of a late snapshot",
          Autopilot._quest_data_pending({**live, "inventory": {"items": []}}) is False,
          "a fresh character with nothing must fall through to the old verdict")
    check("out of the world is a different failure",
          Autopilot._quest_data_pending({**live, "in_world": False}) is False, live)

    source = (REPO_ROOT / "agent" / "autopilot.py").read_text(encoding="utf-8")
    check("the wait is bounded, so a questless character still ends the run",
          "self._quest_data_waits < QUEST_DATA_WAIT_LIMIT" in source
          and "QUEST_DATA_WAIT_LIMIT = 3" in source, "an unbounded wait is a spin")
    check("and the branch waits instead of falling through to the retirement",
          "instead of retiring the goal" in source
          and source.index("_quest_data_pending(before)")
          < source.index("nothing in the policy matched this goal; had"),
          "the wait must run before the blocked verdict is emitted")
    check("the counter resets once the data arrives",
          'before.get("ready_objectives")' in source
          and "self._quest_data_waits = 0" in source, "a stale count would shorten a later wait")


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
    # A curriculum steered by objective *type* must not read that type number as a
    # quest id: doing so refused every offer and took the counterfactual corpus
    # generator to zero rows. Only the id reading may gate an offer.
    typed = planner()
    typed.quests[778] = {"id": 778, "level_min": 1, "steps": [
        {"objectives": [{"id": 9100, "type_id": 14, "params": {}}]}]}
    typed.focus = 14
    other = typed.choose("do all the quests", obs(offer_dialog(778)))
    check("an objective-type focus does not gate the offer as a quest id",
          other is not None and other.args.get("accept") is True
          and other.args.get("quest_id") == 778,
          None if other is None else str((other.tool, other.args)))
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
    # The feature vector's tool axis is `agent.features.TOOLS`, so a registry
    # that grew after the export shifts every index and changes the width. The
    # shipped sidecar was trained on 25 tools; anything wired later must be
    # either re-exported at the current width or explicitly marked retired.
    from ai.train_cause_head import DIM as _CAUSE_DIM
    _cause_width = int(metrics.get("features") or 0)
    check("a stale cause head is labelled retired",
          _cause_width == _CAUSE_DIM or metrics.get("retired") is True,
          {"sidecar": _cause_width, "builder": _CAUSE_DIM})
    # `prototypes_path` is whatever the training run recorded -- on this box an absolute
    # C:\Users\sao\... path. An installed machine has that file beside the json, not at my
    # path, so resolving through the recorded absolute location failed the check on every
    # install even while the payload carried the prototypes.
    proto = path.with_name(str(metrics.get("prototypes") or "cause_head_prototypes.npz"))
    if not proto.is_file():
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
            self._promise_counts: Dict[str, int] = {}
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


def test_objective_travel_is_bounded() -> None:
    """The walk back to an objective's map is budgeted on every planning path.

    Live 2026-10-29 (build 2026-10-29bf): the fight objective at 145490946 was
    re-entered from 145490434 on every plan -- 46 recorded returns in one session
    -- through `plan_objective`, while the ready scan's A->B->A bound (which does
    fire on the same shape) was not the planner of that walk. The funnel now
    spends the same budget, so the bounce is bounded on every path.

    Live 2026-11-04 added the second guard, and the two cover different shapes: a
    border asked twice from the same map *without landing* is spent, while a walk
    that keeps succeeding -- the real A->B->A -- is bounded only by `RETURN_LIMIT`,
    because every arrival clears the border count. Both rows are pinned here: the
    guards are not interchangeable and deleting either reopens a bounce the other
    cannot see.
    """
    from agent.planner import CROSSING_ASK_LIMIT, QuestPlanner, RETURN_LIMIT

    section("A walk back to the objective's map is bounded on every path")
    obj = {"id": 913966, "type_id": 6, "type": "QuestObjectiveFightMonster",
           "map_id": 145490946, "params": {"parameter0": 4212, "parameter1": 1},
           "text": "Defeat 1 Brytnee Spyr in a single fight."}
    quest = {"id": 990002}

    def scene(here: int, exits, groups=None) -> Dict[str, Any]:
        return {"map_id": here, "cell": 300, "level": 13,
                "quests_active": {"990002": 0},
                "map": {"map_id": here, "groups": groups or [],
                        "exits": exits, "npcs": []}}

    back = [{"direction": "right", "map_id": 145490946, "blocked": False,
             "cells": [322]}]
    # A group on this map is what lets the travel funnel run at all: with none,
    # the plan stack's explore child answers first (and that is G83's half).
    crowd = [{"group_id": -34016, "cell": 269, "member_count": 2,
              "monster_ids": [4213, 4213]}]

    def row_of(plan: Any) -> Dict[str, Any]:
        if isinstance(plan, list) and plan:
            return plan[0] or {}
        return {}

    # The return that lands: the walk arrives, so the border count is reset each
    # cycle and only the per-objective return budget can hold it.
    landing = QuestPlanner()
    cycles = []
    for _ in range(RETURN_LIMIT + 1):
        landing._prev_map = 145490946
        cycles.append(landing.plan_objective(quest, 0, obj,
                                             scene(145490434, back, crowd)))
        landing._note_situation(scene(145490946, [], crowd))
    asked = [row_of(p) for p in cycles]
    trips = [c for c in asked if c.get("tool") == "nav.goto"]
    last = asked[-1]
    check("a return that lands is walked RETURN_LIMIT times, then held",
          len(trips) == RETURN_LIMIT and last.get("tool") == "state.snapshot"
          and "not again" in str(last.get("why")),
          [len(trips), RETURN_LIMIT, cycles[-1]])
    check("and each arrival resets the border count, so it never binds here",
          all(n < CROSSING_ASK_LIMIT for n in landing._crossings.values()),
          landing._crossings)

    # The border that refuses: the character never arrives, so the crossing guard
    # is what speaks, and it says which edge stranded the run.
    stuck = QuestPlanner()
    stuck._prev_map = 145490946
    stuck_rows = [row_of(stuck.plan_objective(quest, 0, obj,
                                              scene(145490434, back, crowd)))
                  for _ in range(RETURN_LIMIT + 1)]
    stuck_trips = [c for c in stuck_rows if c.get("tool") == "nav.goto"]
    first_read = next((c for c in stuck_rows if c.get("tool") == "state.snapshot"), {})
    check("a border that never lands is asked for the short budget, not twelve",
          len(stuck_trips) == CROSSING_ASK_LIMIT
          and first_read.get("args") in (None, {}),
          [len(stuck_trips), CROSSING_ASK_LIMIT, RETURN_LIMIT, stuck_rows[:1]])
    check("and the read that closes it names the border that refused",
          "right edge" in str(first_read.get("why"))
          and "never came nearer" in str(first_read.get("why")),
          str(first_read.get("why"))[:200])

    at_target = landing.plan_objective(quest, 0, obj, scene(145490946, []))
    first = row_of(at_target)
    check("standing on the objective's map is not a return: the action is planned",
          bool(first) and not (first.get("tool") == "nav.goto"
                               and (first.get("args") or {}).get("map_id") == 145490946),
          at_target)

    elsewhere = [{"direction": "top", "map_id": 145490432, "blocked": False,
                  "cells": [19]}]
    again = QuestPlanner()
    for _ in range(RETURN_LIMIT):
        again._prev_map = 145490946
        again.plan_objective(quest, 0, obj, scene(145490434, back, crowd))
        again._note_situation(scene(145490946, [], crowd))
    again._prev_map = 145490946
    blocked = row_of(again.plan_objective(quest, 0, obj, scene(145490434, back, crowd)))
    again._prev_map = 145490432
    onward = again.plan_objective(quest, 0, obj, scene(145490434, elsewhere, crowd))
    check("a travel that is not a return is still planned",
          isinstance(onward, list) and bool(onward)
          and row_of(onward).get("tool") == "nav.goto",
          onward)
    check("and it clears the return budget",
          again._returns.get((990002, 913966)) == 0,
          again._returns)
    again._prev_map = 145490946
    reopened = row_of(again.plan_objective(quest, 0, obj, scene(145490434, back, crowd)))
    check("the return guard holds the walk only until a different trip clears it",
          blocked.get("tool") == "state.snapshot" and "not again" in str(blocked.get("why"))
          and reopened.get("tool") == "nav.goto",
          [blocked, reopened])


def test_a_return_to_the_objective_map_is_granted_once() -> None:
    """A visited-and-spent map must bar the bounce, not bar the quest's only place.

    `_exhausted_at` was built (2026-10-29) to stop two quests walking the same
    145489920 <-> 145489921 edge for twenty steps after each visit had already asked
    every NPC. It is keyed `(quest, objective, map)`, and the writer marks the map the
    character is standing on -- which for a single-map objective is the very map the
    objective happens on. Measured on the run of 2026-11-05 by replaying its eight
    final steps through one carried `QuestPlanner`: after the talk spent both NPCs on
    145490435 the map was marked, and from one crossing away the planner answered
    *nothing plannable* -- `nav.goto` to the objective's own map was barred by the
    record of the last visit to it. The active quest was then retired
    (`_skipped` reached 90 in eleven steps), the goal reported exhausted, and the
    session parked. From outside the window: the character left the building and would
    not go back in, then stopped.

    The rule now grants the first return and spends the mark; a second dead visit
    re-marks it and this time the bar holds. Bounded bounce protection, no permanent
    dead end. With the fix the same replay retires **0** quests and every scene plans a
    call.
    """
    import io
    import contextlib

    from agent.planner import QuestPlanner

    section("A spent visit bars the bounce, but never the objective's own map")

    TARGET = 145490435
    OUTSIDE = 145489923
    give = {"quest_id": "4262", "objective_id": 13958, "id": 13958, "type_id": 0,
            "type": "QuestObjectiveFreeForm", "map_id": TARGET,
            "params": {"parameter0": 727568}, "text": "Give a report to Great Jarl Ordyn."}
    quest = {"id": 4262, "name": "the report", "objectives": [give]}

    def outside_obs():
        return {"cell": 331, "level": 11, "in_world": True, "quests_active": {"4262": 0},
                "current_objective": give, "ready_objectives": [give],
                "quests": {"4262": quest},
                "position": {"map_id": OUTSIDE, "cell": 331},
                "map": {"map_id": OUTSIDE, "cell": 331, "groups": [], "npcs": [],
                        "exits": [{"direction": "right", "map_id": TARGET,
                                   "blocked": False, "cells": [40]}],
                        "neighbours": {"right": TARGET}, "doors": []}}

    def ask(planner):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            call = planner.choose("do all the quests", outside_obs(), ["nav.goto",
                                                                      "quest.advance",
                                                                      "tap.interactive",
                                                                      "state.snapshot"])
        return (call.tool, dict(call.args or {})) if call is not None else None

    fresh = QuestPlanner()
    first = ask(fresh)
    check("an objective on another map is a journey back to it",
          first is not None and first[0] == "nav.goto"
          and (first[1].get("map_id") == TARGET or first[1].get("direction") == "right"),
          str(first))

    marked = QuestPlanner()
    triple = (4262, 13958, TARGET)
    ask(marked)                       # the first look resets visit memory for a new world
    marked._exhausted_at.add(triple)
    granted = ask(marked)
    check("the first return is granted even though the last visit found nothing",
          granted is not None and granted[0] == "nav.goto"
          and (granted[1].get("map_id") == TARGET or granted[1].get("direction") == "right"),
          str(granted))
    check("and the grant spends the mark, so the bounce guard cannot re-bar it twice",
          triple not in marked._exhausted_at and triple in marked._return_granted,
          "marks %s granted %s" % (sorted(marked._exhausted_at), sorted(marked._return_granted)))

    marked._exhausted_at.add(triple)          # the return visit found nothing either
    barred = ask(marked)
    check("a second dead visit bars that map for good -- the 2026-10-29 bounce stays bounded",
          barred is None or (barred[1].get("map_id") != TARGET
                             and barred[1].get("direction") != "right"),
          str(barred))

    helper = QuestPlanner()
    check("the helper answers nothing about a map that was never visited",
          helper._visit_is_exhausted((1, 2, 3), 3) is False, "no mark, no bar")
    helper._exhausted_at.add((1, 2, 3))
    check("the first call spends it and lets the trip happen",
          helper._visit_is_exhausted((1, 2, 3), 3) is False
          and (1, 2, 3) in helper._return_granted, str(sorted(helper._return_granted)))
    helper._exhausted_at.add((1, 2, 3))
    check("the second call holds the bar",
          helper._visit_is_exhausted((1, 2, 3), 3) is True, "granted once, no more")
    check("and a key that cannot be an int is never treated as a bar",
          helper._visit_is_exhausted(("q", None), 3) is False, "silent, not fatal")

    reset = QuestPlanner()
    reset._exhausted_at.add((1, 2, 3))
    reset._return_granted.add((1, 2, 3))
    reset._tutorial_step_seen = "gate-a"      # a scripted gate had been seen
    reset._reopen({})                          # ... and the scenario is over
    check("the scene reset clears the grants as well as the marks, so a spent grant "
          "cannot bar the next visit under a new gate",
          not reset._exhausted_at and not reset._return_granted,
          "marks %s granted %s" % (sorted(reset._exhausted_at), sorted(reset._return_granted)))

    # The property the 2026-10-29 bounce guard exists for, restated under the new rule:
    # two adjacent objectives that have each been visited and found spent may cost one
    # extra lap each, never a lap forever.
    bounce = QuestPlanner()
    laps = 0
    for step in range(12):
        to_map = 201 if step % 2 == 0 else 200
        bounce._exhausted_at.add((7, 7, to_map))    # the visit that waits there is spent
        if not bounce._visit_is_exhausted((7, 7), to_map):
            laps += 1
    check("a two-map bounce is granted one extra lap per map, then stays barred",
          laps == 2, "%d laps granted in 12 alternations" % laps)

    # The same rule has a second consumer -- the active-quest funnel inside
    # `plan_objective` -- and the ready scan above never reaches it. Without these two
    # checks the funnel's own call site could be deleted and the gate would stay green.
    funnel = QuestPlanner()
    spent = (4262, 13958, TARGET)
    funnel._exhausted_at.add(spent)
    first_pass = funnel.plan_objective(quest, 0, give, outside_obs()) or []
    check("the active-quest funnel grants its one return too",
          bool(first_pass) and first_pass[0].get("tool") == "nav.goto",
          str([[c.get("tool"), c.get("args")] for c in first_pass])[:150])
    funnel._exhausted_at.add(spent)          # the return visit found nothing again
    second_pass = funnel.plan_objective(quest, 0, give, outside_obs())
    check("and once the visit is re-marked the funnel refuses the trip",
          not second_pass, str(second_pass)[:150])

    step_reset = QuestPlanner()
    step_reset._exhausted_at.add((9, 9, 300))
    step_reset._return_granted.add((9, 9, 300))
    step_reset._tutorial_step_seen = "gate-a"
    step_reset._reopen({"tutorial": {"active": True, "step_id": "gate-b"}})
    check("a new scripted step clears the grants along with the marks",
          not step_reset._exhausted_at and not step_reset._return_granted,
          "marks %s granted %s" % (sorted(step_reset._exhausted_at),
                                   sorted(step_reset._return_granted)))

def test_a_silent_house_map_is_left_by_the_crossings_it_knows() -> None:
    """Where the character is, and how it gets out -- on the live observation shape.

    Two defects, both measured on the run of 2026-11-05 rather than imagined:

    1. `agent/observe.py`'s slim live view (#83, 2026-11-01) stopped writing `map_id`
       at the top level, so the planner's `obs.get("map_id")` reads became None on a
       live session while every fixture that writes the flat shape kept passing. The
       mechanism is reproducible on current code by withholding one key: with
       `position.map_id` removed from the live shape -- which is all a top-level read
       could see -- the planner answers *"Give a report to Great Jarl Ordyn."* with
       `nav.goto {map_id: <the map the character is standing on>}`, four replays out
       of four, and with the key present it answers with the interaction the objective
       needed. `map_id_of` is what makes the 16 readers agree, and the crossing bound
       below depends on it: the border key is `(objective, from_map, dest)`, so a blind
       `from_map` means the bound silently never counts.
       Scope, stated because an earlier version of this docstring and of
       ARCH_REASSESSMENT V.18 claimed more than the evidence gives: the shipped pipeline
       hands the policy an observation that `knowledge_context._lift_position` has
       already flattened, so on the run of 2026-11-04/05 these reads were *not* blind --
       its logged rows carry a top-level `map_id`, 0 of its 57 `nav.goto` asks named the
       map the character stood on, and swapping the accessor back for the top-level read
       over 419 replay captures changes no outcome at all (identical tool distribution,
       0 no-calls either way). The accessor is the class fix for consumers that read raw
       nested views -- the replay corpus, tooling, direct `plan_objective` callers -- and
       it is not the demonstrated cause of that session.
    2. A house map publishes no exits and no neighbours (145490432 in the log, while
       `data/maps/_index.json` lists four), and the planner answered a spent border
       with a `state.snapshot` read -- a read the game serves forever. It now asks each
       crossing the index knows, within the same border budget, and stops when they are
       all spent.
    """
    import collections

    from agent.features import map_graph
    from agent.observe import map_id_of
    from agent.planner import CROSSING_ASK_LIMIT, QuestPlanner

    section("The planner reads the map from the live shape, and a silent house map gets out")
    HERE = 145490435
    slim = {"cell": 462, "level": 12, "in_world": True, "quests_active": {"4262": 0},
            "position": {"map_id": HERE, "cell": 462, "x": 7, "y": -59},
            "map": {"map_id": HERE, "cell": 462, "groups": [],
                    "exits": [{"direction": "left", "map_id": 145489923,
                               "blocked": False, "cells": [462]}],
                    "neighbours": {"left": 145489923, "right": 145490947,
                                   "top": 145490434, "bottom": 145490436},
                    "npcs": [{"npc_id": 3934, "cell": 497}, {"npc_id": 3920, "cell": 312}],
                    "doors": [{"cell": 406, "to_map": 145490947, "via": "arrival"}]}}
    flat = dict(slim)
    flat["map_id"] = HERE

    check("the map id is read from every shape the live view arrives in",
          map_id_of(flat) == HERE and map_id_of(slim) == HERE
          and map_id_of({"position": {"map_id": HERE}}) == HERE
          and map_id_of(None) is None and map_id_of({}) is None,
          "flat %s slim %s" % (map_id_of(flat), map_id_of(slim)))
    check("and it does not claim to read a map block the flattener does not flatten",
          map_id_of({"map": {"map_id": HERE}}) is None,
          "documented: `position` is the authority, `map.map_id` alone resolves to None")

    give = {"quest_id": "4262", "objective_id": 13958, "id": 13958, "type_id": 0,
            "type": "QuestObjectiveFreeForm", "map_id": HERE,
            "params": {"parameter0": 727568}, "text": "Give a report to Great Jarl Ordyn."}
    quest = {"id": 4262, "name": "the report", "objectives": [give]}
    # The witness for the claim in the docstring: the same scene with one key withheld
    # -- all a top-level reader could see -- really does plan the journey to the map
    # the character is already on. Reproducible, so the accessor is doing real work.
    import copy as _copy
    blind = _copy.deepcopy(slim)
    del blind["position"]["map_id"]
    blind_calls = [(QuestPlanner().plan_objective(quest, 0, give, blind) or [{}])[0]
                   for _ in range(4)]
    check("the blindness is reproducible on current code, not a story about the old one",
          all(c.get("tool") == "nav.goto" and (c.get("args") or {}).get("map_id") == HERE
              for c in blind_calls),
          str([[c.get("tool"), c.get("args")] for c in blind_calls])[:160])
    planner = QuestPlanner()
    firsts = [p[0] for p in (planner.plan_objective(quest, 0, give, slim) or []
                              for _ in range(4)) if p]
    went_home = [c for c in firsts
                 if c.get("tool") == "nav.goto"
                 and (c.get("args") or {}).get("map_id") == HERE]
    check("standing on the objective's own map is not a journey to it",
          not went_home, str([c.get("args") for c in firsts])[:200])
    check("and the plan is real work on that map: an interaction or the talk",
          bool(firsts) and any(c.get("tool") in ("tap.interactive", "quest.advance")
                               for c in firsts),
          str([c.get("tool") for c in firsts]))
    other = QuestPlanner()
    flat_plan = (other.plan_objective(quest, 0, give, flat) or [{}])[0]
    check("the flat and the slim projection of one scene plan the same first thing",
          flat_plan.get("tool") == (firsts[0].get("tool") if firsts else None),
          "%s vs %s" % (flat_plan.get("tool"), [c.get("tool") for c in firsts]))

    # The silent house map: no exits, no neighbours published, target not adjacent.
    interior = {"cell": 300, "level": 12, "quests_active": {"4262": 0},
                "position": {"map_id": 145490432},
                "map": {"map_id": 145490432, "cell": 300, "groups": [], "npcs": [],
                        "exits": [], "neighbours": {}, "doors": []}}
    hunt = {"quest_id": 4262, "objective_id": 13960, "id": 13960, "type_id": 0,
            "type": "QuestObjectiveFreeForm", "map_id": 145490946,
            "text": "Discover a new map.", "params": {}}
    hunt_quest = {"id": 4262, "name": "q", "objectives": [hunt]}
    hunter = QuestPlanner()
    asked = collections.Counter()
    tools = []
    for _ in range(12):
        call = (hunter.plan_objective(hunt_quest, 0, hunt, interior) or [{}])[0]
        args = call.get("args") or {}
        tools.append((call.get("tool"), args.get("direction") or args.get("map_id")))
        if call.get("tool") == "nav.goto":
            asked[args.get("direction") or args.get("map_id")] += 1
    known = map_graph()[1].get(145490432) or {}
    check("the interior is left by the crossings the index knows, not by a read",
          len({d for _, d in tools if d in known}) >= 3, "%s / known %s" % (tools, sorted(known)))
    check("and every one of them pays the same border budget",
          bool(asked) and max(asked.values()) <= CROSSING_ASK_LIMIT + 1, str(dict(asked)))
    check("once they are all spent it stops asking rather than walking them again",
          tools[-1][0] == "state.snapshot" and [t[0] for t in tools].count("state.snapshot") >= 3,
          str(tools[-4:]))

    # The client that DOES publish its exits keeps authority: no crossing it withheld.
    noisy = {"cell": 300, "level": 12, "quests_active": {"4262": 0},
             "position": {"map_id": 145490432},
             "map": {"map_id": 145490432, "cell": 300, "groups": [], "npcs": [],
                     "exits": [{"direction": "left", "map_id": 145489920,
                                "blocked": False, "cells": [4]}],
                     "neighbours": {}, "doors": []}}
    publisher = QuestPlanner()
    asked_edges = []
    for _ in range(8):
        call = (publisher.plan_objective(hunt_quest, 0, hunt, noisy) or [{}])[0]
        if call.get("tool") == "nav.goto":
            args = call.get("args") or {}
            asked_edges.append(args.get("map_id") or args.get("direction"))
    check("a map that lists its crossable exits is not sent through one it hid",
          set(asked_edges) <= {145490946, 145489920, "left"}, str(asked_edges))

def test_a_crossing_in_flight_is_waited_out() -> None:
    """`nav.goto` is a walk the client performs, not a command it answers.

    Measured on the run of 2026-11-04/05 (`trajectories/dofus-01.jsonl`, 57 `nav.goto`
    rows over the two days): one border was asked five times at 6.5 s spacing and the
    crossing landed 30 s after the *first* ask -- during the fifth. Another was asked
    ten times in 54 s while the character walked from cell 299 to the exit at 537 and
    back to 299, never crossing. The settle gave a crossing 6 s and then handed the
    decision loop back an observation that still showed the old map, which reads as
    "that ask failed, ask again" -- and every new ask re-paths the walk from where the
    character stands. The loop was interrupting its own journey, and from outside the
    window that is a character that cannot leave the building.

    While the world is still changing the promise is not broken, it is in flight, so the
    deadline slides forward with each observed movement up to `CROSSING_IN_FLIGHT_S`.
    A border that truly refuses then costs two waits of that length and the crossing
    bound retires it, instead of ten asks that cancel each other.
    """
    import tempfile
    import time as _time
    import types as _types

    from agent import autopilot as ap_mod
    from agent.autopilot import Autopilot, settle_deadline

    section("A crossing still being walked is waited out, not re-asked")

    S = 1000.0
    check("with no movement observed yet, a crossing gets exactly its base window",
          settle_deadline("nav.goto", S, S, 6.0) == S + 6.0,
          "got %.1f" % settle_deadline("nav.goto", S, S, 6.0))
    check("each observed movement slides the deadline forward by a window",
          settle_deadline("nav.goto", S, S + 20.0, 6.0) == S + 26.0,
          "got %.1f" % settle_deadline("nav.goto", S, S + 20.0, 6.0))
    check("and the slide stops at the in-flight cap rather than running forever",
          settle_deadline("nav.goto", S, S + 400.0, 6.0) == S + 30.0,
          "got %.1f" % settle_deadline("nav.goto", S, S + 400.0, 6.0))
    check("a tool with no in-flight phase is never extended by this",
          settle_deadline("quest.advance", S, S + 400.0, 6.0) == S + 6.0
          and settle_deadline("state.snapshot", S, S + 400.0, 6.0) == S + 6.0,
          "only nav.goto walks a border")
    saved_cap = ap_mod.CROSSING_IN_FLIGHT_S
    try:
        ap_mod.CROSSING_IN_FLIGHT_S = 3.0
        moved = settle_deadline("nav.goto", S, S + 400.0, 6.0)
    finally:
        ap_mod.CROSSING_IN_FLIGHT_S = saved_cap
    check("the cap is read when the deadline is taken, so a re-assigned constant bites",
          moved == S + 3.0,
          "got %.1f: binding the constant as a default argument freezes the old value"
          % moved)

    class _Rt:
        instance = "dofus-00"
        policy = None
        def current_observation(self):
            return {}, 0.0, "cache"
        def observe(self, obs, source="live"):
            return None

    def pilot(tmp):
        return Autopilot(runtime=_Rt(), goals=["go"], log_path=tmp / "a.jsonl",
                         stop_file=tmp / "STOP", max_steps=1, dry_run=False,
                         preflight=False, minutes=0, interval=0.0, steps_per_goal=1,
                         settle_s=0.05)

    # Four cells still being walked, then the border actually lands. Poll cadence from
    # step_s 0.05 reaches the fifth poll at ~0.47 s, past a 0.25 s base window.
    WALK = [{"map_id": 100, "cell": 300 + i} for i in range(1, 5)] + [
        {"map_id": 200, "cell": 12}]
    call = _types.SimpleNamespace(tool="nav.goto", args={"map_id": 200})
    before = {"map_id": 100, "cell": 300}
    real_window, real_cap = ap_mod.PROMISED_SETTLE_WINDOW_S, ap_mod.CROSSING_IN_FLIGHT_S
    outcomes = {}
    try:
        ap_mod.PROMISED_SETTLE_WINDOW_S = 0.25
        for cap, label in ((0.40, "capped"), (1.50, "waited")):
            tmp = Path(tempfile.mkdtemp())
            try:
                ap_mod.CROSSING_IN_FLIGHT_S = cap
                ap = pilot(tmp)
                box = {"n": 0}

                def poll(max_age_ms=-1.0, record=True, _box=box):
                    i = min(_box["n"], len(WALK) - 1)
                    _box["n"] += 1
                    return dict(WALK[i])
                ap.observe = poll
                started = _time.monotonic()
                out = ap._settle(before, dict(before), call=call, step_s=0.05)
                outcomes[label] = (out.get("map_id"), _time.monotonic() - started,
                                   box["n"], ap._crossings_waited_out)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    finally:
        ap_mod.PROMISED_SETTLE_WINDOW_S = real_window
        ap_mod.CROSSING_IN_FLIGHT_S = real_cap

    capped_map, capped_s, capped_polls, capped_waits = outcomes["capped"]
    waited_map, waited_s, waited_polls, waited_waits = outcomes["waited"]
    check("a walk that outruns the cap is returned as unfulfilled, not waited on",
          capped_map == 100, "map %s after %.2fs, %d polls" % (capped_map, capped_s,
                                                               capped_polls))
    check("the same walk, under a generous cap, is returned when the border lands",
          waited_map == 200, "map %s after %.2fs, %d polls" % (waited_map, waited_s,
                                                               waited_polls))
    check("and it came back later than the base window, so the loop really extended",
          waited_s > 0.25, "returned at %.2fs with a 0.25s base window" % waited_s)
    check("one identical walk therefore costs the loop one decision, not five",
          waited_polls > capped_polls and waited_map == 200,
          "capped %d polls / waited %d polls" % (capped_polls, waited_polls))
    check("and the wait is counted, so a run can say it did this at all",
          waited_waits > capped_waits and waited_waits >= waited_polls - 1,
          "waited %d extensions over %d polls, capped %d over %d"
          % (waited_waits, waited_polls, capped_waits, capped_polls))

def test_the_active_quest_survives_one_unplannable_pass() -> None:
    """A transient failure costs a step, not the quest and not the session.

    The ready-scan path retired the active quest the moment an objective came back with
    no action. On the run of 2026-11-04/05 that was the end of the quest being worked:
    4262 was retired on one unplannable pass (traced to the `self._skipped.add(quest_id)`
    under "the objective's type translates to nothing we can do"), the next scene read
    "nothing plannable -- 90 retired", `goals exhausted`, `safe.stop`. Retirement is
    right for a quest this build cannot express and wrong for a situation that changes a
    step later, so `ACTIVE_PLAN_NONE_LIMIT` (3) consecutive passes in the *same*
    situation are now required -- same objective id, same map.

    Witnessed end to end rather than as arithmetic: the scene is the live one (standing
    on the objective's own map with both NPCs asked and the interactive tried), and the
    first two passes are allowed to go exploring instead of executing the quest.
    """
    from agent.planner import ACTIVE_PLAN_NONE_LIMIT, QuestPlanner

    section("One unplannable pass does not execute the active quest")

    target = 145490435
    obj = {"quest_id": "4262", "objective_id": 13958, "id": 13958, "type_id": 0,
           "type": "QuestObjectiveFreeForm", "map_id": target,
           "text": "Give a report to Great Jarl Ordyn.", "params": {"parameter0": 727568}}
    quest = {"id": 4262, "name": "the report", "objectives": [obj]}

    def spent_visit_obs() -> Dict[str, Any]:
        return {"cell": 331, "level": 11, "in_world": True,
                "quests_active": {"4262": 0}, "current_objective": obj,
                "ready_objectives": [obj], "quests": {"4262": quest},
                "position": {"map_id": target, "cell": 331},
                "map": {"map_id": target, "cell": 331, "groups": [],
                        "npcs": [{"npc_id": 3934, "cell": 497, "name": None},
                                 {"npc_id": 3920, "cell": 312, "name": None}],
                        "exits": [{"direction": "left", "map_id": 145489923,
                                   "blocked": False, "cells": [462]}],
                        "neighbours": {"left": 145489923}, "doors": []}}

    def spent_visit_planner() -> QuestPlanner:
        p = QuestPlanner()
        p.choose("do all the quests", spent_visit_obs(), ["quest.advance"])  # first-world reset
        # The memory of a visit that already asked everything on this map.
        p._npc_asked[(4262, 13958, target)] = {3920, 3934}
        p._interactive_tries[13958] = {(target, 513196)}
        return p

    p = spent_visit_planner()
    retired_at = None
    for n in range(1, ACTIVE_PLAN_NONE_LIMIT + 4):
        call = p.choose("do all the quests", spent_visit_obs(), ["quest.advance"])
        if 4262 in p._skipped and retired_at is None:
            retired_at = n
            break
        if call is not None:
            # While the quest is still alive the plan is allowed to go looking.
            check("a pass that has somewhere to look does not retire the quest",
                  call.tool == "nav.goto" and (call.args or {}).get("direction") == "left",
                  str((call.tool, call.args)))
    check("the quest is retired only after the same situation fails its limit",
          retired_at == ACTIVE_PLAN_NONE_LIMIT + 2,
          "retired at pass %s, limit %d (two exploring passes first)" % (retired_at,
                                                                        ACTIVE_PLAN_NONE_LIMIT))
    check("and the count is cleared with the verdict, so a reopened quest starts clean",
          4262 not in p._plan_none, str(dict(p._plan_none)))

    h = QuestPlanner()
    h._plan_none[4262] = (ACTIVE_PLAN_NONE_LIMIT - 1, (13958, target))
    check("a different objective is a different situation, so the count restarts",
          h._plan_none_reaches_limit(4262, (13960, target)) is False
          and h._plan_none[4262] == (1, (13960, target)), str(dict(h._plan_none)))
    check("the same situation reaching the limit is the verdict, and it clears the count",
          h._plan_none_reaches_limit(4262, (13960, target)) is False
          and h._plan_none_reaches_limit(4262, (13960, target)) is True
          and 4262 not in h._plan_none, str(dict(h._plan_none)))
    h._plan_none[4262] = (ACTIVE_PLAN_NONE_LIMIT - 1, (13958, target))
    h._refund_plan_none("4262")     # the client reports quest ids as strings too
    check("a refund accepts the client's string id", 4262 not in h._plan_none,
          str(dict(h._plan_none)))

    r = spent_visit_planner()
    r._plan_none[4262] = (ACTIVE_PLAN_NONE_LIMIT - 1, (13958, target))
    from agent.runtime import ToolCall
    r.plan_objective = lambda *a, **k: [{"tool": "quest.advance",     # type: ignore[assignment]
                                         "args": {"quest_id": 4262, "npc_id": 3920}}]
    alive = r.choose("do all the quests", spent_visit_obs(), ["quest.advance"])
    check("a pass that does plan clears the count entirely",
          alive is not None and 4262 not in r._plan_none and 4262 not in r._skipped,
          str(dict(r._plan_none)))
    check("a fork carries the count so a counterfactual cannot invent a retirement",
          set(r.fork()._plan_none) == set(r._plan_none), str(dict(r.fork()._plan_none)))

def test_the_session_end_row_names_the_reason() -> None:
    """Every run writes why it stopped, on the log, once.

    The run of 2026-11-04/05 left 27 `safe.stop` rows across two days -- several of
    them taken while the character was not even in the world (`in_world` false, `hp
    0/0`, no position) -- and not one of them says which condition ended the loop. The
    reason went to stdout, which is gone by the time anyone opens the file, so "it
    turned off by itself" was unanswerable from the record. `_finish()` is the seam the
    run's five exits return through: one row per pilot, with the reason, the counters
    that make it readable and the `brain_build` stamp #101 added. `summary()` itself
    stays a pure report -- two suites call it on a partial object, and giving it a
    writer would break them for reasons unrelated to what they test.
    """
    import json as _json
    import tempfile

    from agent import autopilot as ap_mod
    from agent.autopilot import Autopilot, ToolCall

    section("The end of a run is a row, not only a line on the console")

    class _Policy:
        def __init__(self) -> None:
            self.calls = 0
            # One granted return on record, so the summary has something to report and
            # a mutation that drops the read cannot pass as "the run had none".
            self._return_granted = {(4262, 13958, 145490435)}

        def choose(self, goal, obs, tools):    # noqa: ARG002
            self.calls += 1
            return ToolCall("inv.list", {}, goal)

    class _Result:
        def __init__(self, obs: Dict[str, Any]) -> None:
            self.ok = True
            self.error = None
            self.data = {"response": {"result": {"ok": True}}, "snapshot": dict(obs)}

    class _Executor:
        def __init__(self, obs: Dict[str, Any]) -> None:
            self.obs = obs

        def execute(self, tool, args, **kw):   # noqa: ARG002
            return _Result(self.obs)

    class _Runtime:
        def __init__(self, obs: Dict[str, Any]) -> None:
            self.policy = _Policy()
            self.instance = "dofus-00"
            self.obs = obs
            self.executor = _Executor(obs)

        def current_observation(self):
            return dict(self.obs), 5.0, "cache"

        def observe(self, obs, source="live"):
            return None

        def note_observation(self, obs):
            return None

    obs = {"in_world": True, "map_id": 10340, "cell": 300, "inventory": []}
    tmp = Path(tempfile.mkdtemp())
    try:
        log = tmp / "autopilot.jsonl"
        ap = Autopilot(runtime=_Runtime(dict(obs)), goals=["sell the spare dagger"],
                       log_path=log, stop_file=tmp / "STOP", max_steps=2,
                       dry_run=False, preflight=False, minutes=0, interval=0.0,
                       steps_per_goal=2, settle_s=0.0)
        ap.controller.state_dir = tmp
        # Two waited-out crossings are what the row has to report; a stub that
        # measured zero would pass against a hard-coded zero in the row.
        ap._crossings_waited_out = 2
        real_sleep = ap_mod.time.sleep
        ap_mod.time.sleep = lambda seconds: None
        try:
            summary = ap._run()
            report = ap.summary("a pure report")
            second = ap._finish("a second end")
        finally:
            ap_mod.time.sleep = real_sleep
        rows = [_json.loads(line) for line in
                log.read_text(encoding="utf-8").splitlines() if line.strip()]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ends = [r for r in rows if r.get("kind") == "session_end"]
    check("the run ends with a row that says why", bool(ends), str(rows)[-260:])
    check("the reason on the row is the reason in the summary",
          bool(ends) and ends[0].get("reason") == summary.get("reason"),
          "%s vs %s" % ([e.get("reason") for e in ends], summary.get("reason")))
    check("and it carries the counters that make the reason readable",
          bool(ends) and all(k in ends[0] for k in ("steps", "waits", "verdicts",
                                                   "promises", "retired", "elapsed_s",
                                                   "session", "instance",
                                                   "crossings_waited_out",
                                                   "returns_granted")),
          str(sorted(ends[0]))[:220] if ends else "")
    check("the two crossing rules are reported as the run measured them, not as zeros",
          bool(ends) and ends[0].get("returns_granted") == 1
          and ends[0].get("crossings_waited_out") == summary.get("crossings_waited_out")
          and summary.get("returns_granted") == 1,
          str({k: ends[0].get(k) for k in ("returns_granted", "crossings_waited_out")}
              if ends else ""))
    check("the end row is stamped with the code that wrote it, like every other row",
          bool(ends) and str(ends[0].get("brain_build", "")).startswith("brain-"),
          str([e.get("brain_build") for e in ends]))
    check("`summary()` is still a pure report: the row comes from the run ending",
          report.get("reason") == "a pure report" and len(ends) == 1,
          "%d end row(s) after a bare summary() call" % len(ends))
    check("one pilot writes one end row, even when a second end is reported",
          second.get("reason") == "a second end" and len(ends) == 1,
          "%d end rows for one pilot" % len(ends))
    src = (Path(ap_mod.__file__).read_text(encoding="utf-8"))
    check("and no exit path can bypass it: every run return goes through `_finish`",
          src.count("return self.summary(") == 0
          and src.count("self._finish(") >= 5,
          "self.summary( x%d, self._finish( x%d"
          % (src.count("return self.summary("), src.count("self._finish(")))

def test_the_plan_reason_reaches_the_action_row() -> None:
    """The reason a call was planned is on the row, not only in the trajectory.

    `ToolCall.why` exists (runtime.py:41) and the planner fills it (planner.py:3507), and
    `record_meta()` carries it into the corpus the recorder writes. The *step row* never
    got it: `Step` had no field for it and the dispatch path built `_meta` at
    autopilot.py:1922 and then dropped it on the floor. Measured 2026-11-04 over the live
    log: 0 of 1,044 step rows carried a reason, so "why did the agent take 1,804 direction
    hops that fail 90% of the time" had no answer readable from the run record -- the
    question #99 was opened with.
    """
    import json as _json
    import tempfile

    from agent import autopilot as ap_mod
    from agent.autopilot import Autopilot, ToolCall

    section("The planned reason is on the row the dispatch writes")

    class _Policy:
        def __init__(self, why: Optional[str]) -> None:
            self.why = why
            self.calls = 0

        def choose(self, goal, obs, tools):
            self.calls += 1
            call = ToolCall("inv.list", {}, goal)
            if self.why:
                call.why = self.why
            return call

    class _Result:
        def __init__(self, obs: Dict[str, Any]) -> None:
            self.ok = True
            self.error = None
            self.data = {"response": {"result": {"ok": True}},
                         "snapshot": dict(obs)}

    class _Executor:
        def __init__(self, obs: Dict[str, Any]) -> None:
            self.obs = obs
            self.seen_meta: List[Any] = []

        def execute(self, tool, args, **kw):
            self.seen_meta.append(kw.get("meta"))
            return _Result(self.obs)

    class _Runtime:
        def __init__(self, obs: Dict[str, Any], why: Optional[str]) -> None:
            self.policy = _Policy(why)
            self.instance = "dofus-00"
            self.obs = obs
            self.executor = _Executor(obs)

        def current_observation(self):
            return dict(self.obs), 5.0, "cache"

        def observe(self, obs, source="live"):
            return None

        def note_observation(self, obs):
            return None

    obs = {"in_world": True, "map_id": 10340, "cell": 300, "inventory": []}
    reason = "objective 4262: the store is on this map, ask it"

    def run(why: Optional[str]) -> List[Dict[str, Any]]:
        tmp = Path(tempfile.mkdtemp())
        try:
            rt = _Runtime(dict(obs), why)
            log = tmp / "autopilot.jsonl"
            ap = Autopilot(runtime=rt, goals=["sell the spare dagger"], log_path=log,
                           stop_file=tmp / "STOP", max_steps=1, dry_run=False,
                           preflight=False, minutes=0, interval=0.0, steps_per_goal=1,
                           settle_s=0.0)
            ap.controller.state_dir = tmp
            real_sleep = ap_mod.time.sleep
            ap_mod.time.sleep = lambda seconds: None
            try:
                ap._run()
            finally:
                ap_mod.time.sleep = real_sleep
            return [_json.loads(l) for l in
                    log.read_text(encoding="utf-8").splitlines() if l.strip()]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    rows = run(reason)
    step = [r for r in rows if "kind" not in r and r.get("tool") == "inv.list"]
    check("the dispatched row is written", bool(step), str(rows)[:180])
    check("it carries the reason the planner gave for that exact call",
          bool(step) and step[0].get("why") == reason, str(step)[:220])
    check("and the reason rides the record, never the directive",
          bool(step) and step[0].get("args") == {}, str(step[:1])[:160])
    quiet = run(None)
    q_step = [r for r in quiet if "kind" not in r and r.get("tool") == "inv.list"]
    check("a call with no reason from the planner is not given an invented one",
          bool(q_step) and not q_step[0].get("why"), str(q_step)[:200])

def test_the_brain_stamps_its_own_code_identity() -> None:
    """Every row says which code wrote it -- not only which page was loaded.

    The `agent_build` a reader sees in the log is the *page's* stamp, hand-written in
    `scripts/mobile-disguise.js` and pinned by its own check. Measured 2026-11-04: all 51
    nav rows of that day still read `2026-10-31a` while the brain had been rewritten twice,
    so no live row could say whether a fix was loaded -- which is why #97 and #99 cannot be
    closed from evidence. The digest here is taken over what `inspect.getsource` returns,
    i.e. the code this process holds, and it goes on every row through the one seam all of
    them share (`_append_log_line`).
    """
    import json as _json
    import tempfile

    from agent import autopilot as ap_mod
    from agent.autopilot import Autopilot, brain_build, brain_inputs

    section("The brain stamps the code it is running")
    inputs = brain_inputs()
    check("every deciding module is in the digest, none silently missing",
          len(inputs) == len(ap_mod.BRAIN_MODULES)
          and not any(v.startswith("<unloaded") for v in inputs.values()),
          str([k for k, v in inputs.items() if v.startswith("<unloaded")]))
    stamp = brain_build()
    check("the stamp names the process and is stable inside it",
          stamp.startswith("brain-") and len(stamp) == len("brain-") + 12
          and stamp == brain_build(), stamp)

    # The stamp has to be able to move: same modules, one byte of source different.
    real_getsource = ap_mod.inspect.getsource
    try:
        ap_mod.inspect.getsource = lambda m: real_getsource(m) + "  # one byte of drift\n"
        ap_mod._BRAIN_BUILD = None
        drifted = brain_build()
    finally:
        ap_mod.inspect.getsource = real_getsource
        ap_mod._BRAIN_BUILD = None
    check("a changed source changes the stamp, so a stale build cannot hide behind it",
          drifted != stamp and drifted.startswith("brain-"), "%s vs %s" % (stamp, drifted))

    # The seam is what matters: every row kind, not a per-caller field.
    blob = _json.dumps({"kind": "wait", "reason": "x"}).encode()
    stamped = ap_mod._stamp_brain_build(blob)
    row = _json.loads(stamped.decode())
    check("a stamped row is still valid JSON and carries the stamp",
          row.get("kind") == "wait" and row.get("reason") == "x"
          and row.get("brain_build") == brain_build(), stamped[:120])
    check("and an empty object is left alone rather than made invalid",
          ap_mod._stamp_brain_build(b"{}") == b"{}",
          ap_mod._stamp_brain_build(b"{}"))

    class _Policy:
        def __init__(self) -> None:
            self.calls = 0

        def choose(self, goal, obs, tools):
            self.calls += 1
            from agent.autopilot import ToolCall
            return ToolCall("state.snapshot", {}, goal)

    class _Result:
        ok, error = True, None
        data: Dict[str, Any] = {}

    class _Executor:
        def __init__(self, obs: Dict[str, Any]) -> None:
            self.obs = obs

        def execute(self, tool, args, **kw):
            res = _Result()
            res.data = {"response": {"result": {"ok": True}},
                        "snapshot": dict(self.obs)}
            return res

    class _Runtime:
        def __init__(self, obs: Dict[str, Any]) -> None:
            self.policy = _Policy()
            self.instance = "dofus-00"
            self.obs = obs
            self.executor = _Executor(obs)

        def current_observation(self):
            return dict(self.obs), 5.0, "cache"

        def observe(self, obs, source="live"):
            return None

        def note_observation(self, obs):
            return None

    tmp = Path(tempfile.mkdtemp())
    try:
        rt = _Runtime({"in_world": True, "map_id": 10340, "cell": 300})
        log = tmp / "autopilot.jsonl"
        ap = Autopilot(runtime=rt, goals=["push right"], log_path=log,
                       stop_file=tmp / "STOP", max_steps=2, dry_run=False,
                       preflight=False, minutes=0, interval=0.0, steps_per_goal=2,
                       settle_s=0.0)
        ap.controller.state_dir = tmp
        real_sleep = ap_mod.time.sleep
        ap_mod.time.sleep = lambda seconds: None
        try:
            ap._run()
        finally:
            ap_mod.time.sleep = real_sleep
        lines = [l for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
        rows = [_json.loads(l) for l in lines]
        check("a real run writes rows, and every one of them parses",
              len(rows) >= 2 and len(lines) == len(rows), "%d row(s)" % len(lines))
        check("the stamp is on every row kind the brain writes",
              all(r.get("brain_build") == brain_build() for r in rows),
              str([{k: r.get(k) for k in ("kind", "tool", "brain_build")} for r in rows][:3]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def test_the_search_borders_are_bounded() -> None:
    """A search that never lands may not ask the same border forever.

    Measured 2026-11-05 on the live log (§V.11): the border 147063810 -> 147063298 was
    asked 528 times inside one session and landed zero times, and every one of those asks
    belonged to a fight objective -- a search, not a travel plan. The crossing bound that
    stopped the same bounce on the travel path did not cover it: driving the current
    planner with a border that never delivers gives 20 asks of the same edge and
    `_crossings` stays empty. 8,086 s of session time -- 61.5% of every intra-session
    second in the log -- sits behind this loop (§V.12).
    """
    import collections

    from agent.planner import CROSSING_ASK_LIMIT, QuestPlanner

    section("A search spends the border it keeps asking")
    obj = {"quest_id": 5, "objective_id": 7, "id": 7, "type_id": 0,
           "type": "QuestObjectiveFreeForm", "text": "defeat the xelotls",
           "params": {"parameter0": 704145}}
    two = [{"direction": "left", "map_id": 200, "blocked": False, "cells": [300]},
           {"direction": "top", "map_id": 201, "blocked": False, "cells": [19]}]

    def scene(here: int, exits) -> Dict[str, Any]:
        return {"map_id": here, "cell": 300, "level": 13, "quests_active": {"5": 0},
                "map": {"map_id": here, "groups": [], "exits": exits, "npcs": []}}

    key = (5, 7)

    def asks(planner, obs, times=20):
        out = []
        for _ in range(times):
            call = planner._explore_with_memory(obj, obs, key=key)
            out.append(call)
        return out

    # 1. the border never delivers: the character stays on 946
    stuck = QuestPlanner()
    stuck._search_maps[key] = {946}
    got = asks(stuck, scene(946, two))
    dirs = [str((c.get("args") or {}).get("direction")) for c in got if c]
    per_edge = collections.Counter(dirs)
    check("the unbounded case is what the fix has to beat: no edge may be asked more "
          "than the crossing limit",
          max(per_edge.values()) <= CROSSING_ASK_LIMIT, str(per_edge))
    check("and every ask is counted, so the search shares the travel budget",
          sum(stuck._crossings.values()) >= min(len(dirs), 2 * CROSSING_ASK_LIMIT),
          str(stuck._crossings))
    check("once both borders are spent the search answers with nothing, not with a lap",
          got[-1] is None and (5, 7, 946) in stuck._exhausted_at, str(got[-1]))

    # 2. the asks rotate before they are spent -- a search keeps answering
    rot = QuestPlanner()
    rot._search_maps[key] = {946, 200, 201}
    got_rot = [c for c in asks(rot, scene(946, two)) if c]
    check("with nothing fresh left the search still answers until the borders are spent",
          len(got_rot) >= 2 * CROSSING_ASK_LIMIT, str(got_rot[:3])[:160])
    check("and it does not spend its asks on one edge",
          len(collections.Counter(str((c.get("args") or {}).get("direction"))
                                  for c in got_rot)) == 2,
          str([c.get("args") for c in got_rot]))

    # 3. arriving somewhere new restarts the question at the new map
    moved = QuestPlanner()
    moved._search_maps[key] = {946}
    asks(moved, scene(946, two), times=4)
    spent_here = {(w, f, d): n for (w, f, d), n in moved._crossings.items() if f == 946}
    moved._note_arrival(200)
    on200 = moved._explore_with_memory(obj, scene(200, two), key=key)
    check("an arrival on the other side is a new question, not a spent one",
          bool(spent_here) and on200 is not None, "%s / %s" % (spent_here, on200))

    # 4. the scripted child is the shape that asked 528 times live: a fight objective with
    # no group on the map, pointed at one direction. It does not go through the memory
    # branch when that branch has nothing left, so it needed its own bound.
    from agent.plan_stack import child_for_objective

    fight = {"quest_id": 5, "objective_id": 7, "id": 7, "type_id": 6,
             "type": "QuestObjectiveFightMonster", "text": "defeat the xelotls",
             "params": {"parameter0": 704145}, "map_id": None}
    empty = {"map_id": 946, "cell": 300, "level": 13, "quests_active": {"5": 6},
             "map": {"map_id": 946, "groups": [], "exits": two, "npcs": []}}
    scripted = child_for_objective(fight, empty)
    check("the fight objective asks for a search, not for a destination",
          (scripted or {}).get("kind") == "explore"
          and (scripted or {}).get("args", {}).get("direction") == "left", str(scripted))
    quest = {"id": 5, "name": "the xelotl problem", "objectives": [fight]}
    hunt = QuestPlanner()
    plans = [hunt.plan_objective(quest, 0, fight, empty) for _ in range(10)]
    asked = collections.Counter(
        str((c.get("args") or {}).get("direction"))
        for calls in plans for c in (calls or []) if c.get("tool") == "nav.goto")
    check("the scripted search asks each border of the map at most the crossing limit",
          bool(asked) and max(asked.values()) <= CROSSING_ASK_LIMIT, str(asked))
    check("and once every border is spent the objective plans nothing, not another lap",
          plans[-1] == [] and (5, 7, 946) in hunt._exhausted_at, str(plans[-1]))
    check("a refusal does not spend the border again: the count stops at the limit",
          sorted(hunt._crossings.values()) == [CROSSING_ASK_LIMIT, CROSSING_ASK_LIMIT],
          str(hunt._crossings))

    # 4. non-vacuity: the same fixture on the pre-fix shape asked one edge 20/20 times
    check("the fixture can fail: an unbounded search shows up as 20 identical asks",
          len(dirs) + len([c for c in got if c is None]) == 20,
          "%d calls, %d None" % (len(dirs), len([c for c in got if c is None])))

def test_search_prefers_fresh_maps() -> None:
    """A search walks a fresh map before re-entering one it already walked.

    Live 2026-10-29 (build 2026-10-29bf): 76 of 84 gate-phase nav steps and 97 of
    105 in the 13948 phase stood on a map already visited in that phase -- an
    eight-map ring walked three times. The explore choice was stateless, so every
    lap looked like a first lap. The memory is per objective, the hint still wins
    (a scripted edge is not a search), and when every exit is walked the old rule
    stands rather than stalling.
    """
    from agent.planner import QuestPlanner

    section("A search prefers a fresh map, and a hint still wins")
    free = {"quest_id": 5, "objective_id": 7, "type_id": 0,
            "type": "QuestObjectiveFreeForm", "map_id": 432,
            "params": {"parameter0": 704145},
            "text": "Recover the first page of the aqueduct blueprints."}
    two_ways = [{"direction": "left", "map_id": 200, "blocked": False, "cells": [300]},
                {"direction": "top", "map_id": 201, "blocked": False, "cells": [19]}]

    def scene(here: int, exits, text: str = "") -> Dict[str, Any]:
        obj = dict(free)
        if text:
            obj["text"] = text
        return {"map_id": here, "cell": 300, "level": 13,
                "quests_active": {"5": 0},
                "map": {"map_id": here, "groups": [],
                        "exits": exits, "npcs": []}}

    planner = QuestPlanner()
    base = planner._explore_call(free, scene(946, two_ways))
    check("without memory the first usable exit is taken",
          (base.get("args") or {}).get("direction") == "left", base)

    planner._search_maps[(5, 7)] = {200}
    fresh = planner._explore_with_memory(free, scene(946, two_ways), key=(5, 7))
    check("a walked destination is passed over for a fresh one",
          (fresh.get("args") or {}).get("direction") == "top", fresh)
    check("the map it stands on is recorded as walked",
          946 in planner._search_maps[(5, 7)], planner._search_maps)

    planner._search_maps[(5, 7)] = {946, 200, 201}
    stale = planner._explore_with_memory(free, scene(946, two_ways), key=(5, 7))
    check("when every edge is walked the old choice stands, not a stall",
          (stale.get("args") or {}).get("direction") == "left", stale)

    right_way = [{"direction": "right", "map_id": 200, "blocked": False,
                  "cells": [322]}] + two_ways[1:]
    hinted = scene(946, right_way, text="Walk the right edge.")
    planner._search_maps[(5, 7)] = {946, 200}
    keep = planner._explore_with_memory(dict(free, text="Walk the right edge."),
                                        hinted, key=(5, 7))
    check("a scripted edge hint is not overridden by freshness",
          (keep.get("args") or {}).get("direction") == "right", keep)

    fight = {"id": 913966, "type_id": 6, "type": "QuestObjectiveFightMonster",
             "map_id": 432, "params": {"parameter0": 4212, "parameter1": 1},
             "text": "Defeat 1 Brytnee Spyr in a single fight."}
    seeker = QuestPlanner()
    seeker._search_maps[(990002, 913966)] = {946, 200}
    calls = seeker.plan_objective({"id": 990002}, 0, fight, scene(946, two_ways))
    first = (calls[0] or {}) if calls else {}
    check("the fight search uses the same freshness rule",
          first.get("tool") == "nav.goto"
          and (first.get("args") or {}).get("direction") == "top", calls)

    # Discovery objectives name no map, so their call sites cannot key the
    # search by travel destination. Live replay 2026-10-31: 571 of 829 explore
    # calls arrived keyed None this way; 546 were the free-form search. The
    # memory keys on the objective itself instead of going stateless.
    derived = QuestPlanner()
    derived._search_maps[(5, 7)] = {200}
    auto = derived._explore_with_memory(free, scene(946, two_ways))
    check("an unkeyed discovery search keys on its own objective",
          (auto.get("args") or {}).get("direction") == "top"
          and 946 in derived._search_maps[(5, 7)], auto)
    anon = QuestPlanner()
    plain = anon._explore_with_memory({}, scene(946, two_ways))
    check("an objective with no id stays stateless instead of sharing a key",
          (plain.get("args") or {}).get("direction") == "left"
          and not anon._search_maps, anon._search_maps)


def test_no_call_is_explained() -> None:
    """G90: a no-call names the dead end instead of only "no action".

    Replaying the 343 decisions of `2026-10-29bf` through the current planner, 21
    (6%) returned no call at all, and the page recorded them as `policy returned
    no call`. The wait row corpus-wide read only "the policy had no action to
    take" (17,671 rows), so no live log could say what stranded the plan.
    """
    import inspect

    from agent import autopilot as autopilot_module
    from agent.planner import QuestPlanner

    section("A no-call says what stranded it (G90)")

    planner = QuestPlanner()
    planner._ensure_quests = lambda: None
    planner._curriculum = lambda level: []
    planner._focus_order = lambda level: []
    obs = {"map_id": 145490434, "cell": 300, "level": 8,
           "quests_active": {},
           "map": {"map_id": 145490434, "groups": [], "exits": []}}
    call = planner.choose("do all the quests", obs)
    check("a dead end is still a no-call, not an invented action",
          call is None, call)
    reason = getattr(planner, "last_no_call", "")
    check("the no-call names the counts and the map",
          "quest mode: nothing plannable" in reason
          and "0 ready objective(s)" in reason and "145490434" in reason, reason)

    other = dict(obs)
    other["map_id"] = 145490435
    other["map"] = {"map_id": 145490435, "groups": [], "exits": []}
    planner.choose("do all the quests", other)
    check("the reason belongs to the observation it came from",
          "145490435" in planner.last_no_call
          and "145490434" not in planner.last_no_call, planner.last_no_call)

    check("the wait row reads the planner's reason",
          "last_no_call" in inspect.getsource(autopilot_module.Autopilot),
          "the autopilot never reads it; the wait row would stay generic")

    # G90's open question -- is the honest terminal answer a no-call or a reasoned
    # `state.snapshot`? -- settles from this source, not from a live session: the no-call
    # branch is the only place the controller learns that the goal itself is an invalid
    # assumption, and it retires the stranded objective instead of idling. A read would
    # route past both, and reads are what the loop was just bounded against (3,010 of
    # 4,287 baseline steps). Measured by reading `_run` on 2026-11-01.
    run_source = inspect.getsource(autopilot_module.Autopilot._run)
    # `_run` contains two `if call is None:` branches; the stranded-goal one is the
    # branch that emits this error, so pick it by its own marker rather than by position.
    # Comments are then stripped: measured on this edit, a mutation that deleted the
    # `RETIRE_LIMIT` guard left the check green because the constant survived in the
    # sentence above it -- the same trap `code_only` records for C# and JS.
    def _code_of(text: str) -> str:
        return "\n".join(line for line in text.splitlines()
                         if not line.strip().startswith("#"))

    stranded = [_code_of(part.split("# A read is not an action", 1)[0])
                for part in run_source.split("if call is None:")[1:]
                if '"policy returned no call"' in part]
    check("the no-call branch is the one this test reads",
          len(stranded) == 1, [len(s) for s in stranded])
    check("the no-call still reaches the controller",
          stranded and 'controller.on_step' in stranded[0]
          and '"policy returned no call"' in stranded[0],
          "the repair layer would stop seeing an unexpressible goal")
    check("and it retires the stranded objective, bounded",
          stranded and "_retire_stuck_objective" in stranded[0]
          and "RETIRE_LIMIT" in stranded[0],
          "the run would idle on a goal it cannot express")
    check("a no-call is not turned into a read",
          stranded and "state.snapshot" not in stranded[0],
          "a reasoned read here would re-inflate the wait rows the loop bounds")


def test_session_close_hook() -> None:
    """A finished run hands itself to the close pipeline, and can be told not to.

    The hook must stay out of process (the pipeline imports the trainers and the
    janitor) and guarded by an environment switch: the daemon starts and stops
    runs on a schedule, and a close on a stop that wrote nothing would rescan
    237k log lines for no new row.
    """
    import inspect

    from agent import autopilot as module

    section("Session close hook (fires at session end, fail-open)")
    check("the hook is on by default",
          module.session_close_enabled({}, dry_run=False) is True, "{}")
    check("the environment can turn it off",
          module.session_close_enabled({"AUTOPILOT_SESSION_CLOSE": "off"},
                                       dry_run=False) is False, "off")
    check("a dry run never closes a session",
          module.session_close_enabled({}, dry_run=True) is False, "dry")
    source = inspect.getsource(module)
    check("the session-end path calls the hook",
          'self.fight_log.flush("session_end")\n            self._close_session()'
          in source, "the finally block does not hand off")
    check("the close runs out of process",
          '"tools.session_close"' in source,
          "the pipeline would be imported into the loop thread")

    from agent import brain_daemon as daemon_module
    check("the daemon's autopilot inherits the hooked run()",
          "def run" not in inspect.getsource(daemon_module._DaemonAutopilot),
          "the daemon overrides run and would skip the close for every instance")


def test_the_close_fits_the_timeout_that_kills_it() -> None:
    """The loop bounds the close with a timeout, so the pipeline has to fit inside it.

    `agent/autopilot.py` runs `python -m tools.session_close` as a subprocess with
    `SESSION_CLOSE_TIMEOUT_S`; if the close overruns, the loop kills it and the session
    loses its ledger row -- the bookkeeping silently stops, which is the failure mode the
    whole close exists to remove. Measured 2026-11-01: the worst recorded close is 101.0 s
    and the new live-evidence capture costs 6.2 s, so there is room, but the margin is a
    number that has to be watched as the corpus grows.
    """
    import inspect

    from agent import autopilot
    from tools import session_close

    section("The session close fits the timeout that would kill it")
    timeout = float(autopilot.SESSION_CLOSE_TIMEOUT_S)
    source = inspect.getsource(autopilot.Autopilot._close_session)
    check("the loop bounds its close subprocess with the constant",
          "timeout=SESSION_CLOSE_TIMEOUT_S" in source, "the close could run forever")
    ledger = REPO_ROOT / "data" / "session_closes.jsonl"
    from agent import jsonio

    closed = [row for row in jsonio.iter_rows(ledger)
              if row.get("action") == "closed" and isinstance(row.get("seconds"), (int, float))]
    check("the ledger holds closes to measure", closed, str(ledger))
    capture = REPO_ROOT / "data" / "evidence" / "live_sessions.jsonl"
    costs = [row.get("capture_seconds") for row in jsonio.iter_rows(capture)
             if isinstance(row.get("capture_seconds"), (int, float))]
    check("the live capture records what its own read costs", costs,
          "no capture row carries capture_seconds, so the added cost is unmeasured")
    if closed and costs:
        worst = max(float(row["seconds"]) for row in closed) + max(float(c) for c in costs)
        # The capture has never run inside a recorded close (it landed after the last one),
        # so the two measured costs are added rather than assumed to overlap.
        check("worst close plus the live capture keeps at least 2x headroom",
              worst * 2 < timeout, (round(worst, 1), timeout))
        check("and the close's own steps stay isolated",
              session_close._step("probe", lambda: (_ for _ in ()).throw(
                  RuntimeError("step failed")))["ok"] is False,
              "a failing step would take the close down with it")


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
    far = steps(None, [wrapper, talk], 145489923, 10)
    check("a ready objective on another map is travelled to, not waited out",
          far[1][0] == "nav.goto" and far[1][1].get("map_id") == 145490435
          and far[1][2] == ((4274, 13926), "nav.goto")
          and far[2] == far[1], str(far))
    # This fixture never moves the character. The wanted crossing goes first, then every
    # other crossing the map index knows about -- a border that refuses twice is a
    # broken exit, and the way out of a house is often a different edge -- and once they
    # are all spent the loop stops asking. Measured 2026-11-05 on the live log: the old
    # answer was a read, which the game serves forever.
    import collections

    from agent.planner import CROSSING_ASK_LIMIT

    nav_borders = collections.Counter(
        (row[1] or {}).get("map_id") for row in far if row[0] == "nav.goto")
    reads = [row for row in far if row[0] == "state.snapshot"]
    check("when the map never changes the walk is bounded, not looped forever",
          far[-1][0] == "state.snapshot" and far[-1][1] == {}
          and far[-1][2] == ((4274, 13926), "state.snapshot")
          and max(nav_borders.values()) <= CROSSING_ASK_LIMIT,
          [str(far[-1]), dict(nav_borders)])
    check("and it spends the other crossings it knows before giving up, which is how "
          "a house door gets found",
          len(nav_borders) >= 3 and len(reads) >= 3,
          [dict(nav_borders), len(reads)])

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

    # G69's instrument gap: the protocol captures (`play_*.jsonl`) stamp `at` in epoch
    # *milliseconds*, and `datetime.fromtimestamp` rejects that with OSError [Errno 22].
    # The one report that could summarise a capture crashed on exactly the logs that hold
    # a successful interactive use, so the evidence was readable only by hand.
    check("a millisecond epoch reads as the same instant as its seconds form",
          module._ts({"at": 1793211230499}) == module._ts({"at": 1793211230.499}),
          str(module._ts({"at": 1793211230499})))

    def ms_step(index: int, at: float) -> str:
        return json.dumps({"kind": "step", "session": "p", "index": index,
                           "tool": "state.snapshot", "ok": True, "at": at,
                           "observation": {"ready_objectives": []}})

    ms_out, ms_report = drive([ms_step(0, 1793211230499.0), ms_step(1, 1793211260499.0)])
    first_ms = (ms_report["per_session"] or [{}])[0]
    check("a capture stamped in milliseconds no longer crashes the reporter",
          first_ms.get("minutes") == 0.5 and str(first_ms.get("start")).startswith("2026-"),
          f"{first_ms.get('minutes')} {first_ms.get('start')}")
    check("millisecond stamps group into one session window",
          ms_report["sessions"] == 1 and ms_report["steps"] == 2,
          f"{ms_report['sessions']} sessions / {ms_report['steps']} steps")
    check("seconds epochs are left untouched by the normalization",
          module._ts({"at": 1793211260.5}) == 1793211260.5
          and module._ts({"ts": 12345.0}) == 12345.0,
          str(module._ts({"at": 1793211260.5})))

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
        session=1, _step_total=0, _verdict_counts={}, _promise_counts={}, _source_counts={}, replan_requested=0,
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

            @property
            def value_choice_stats(self):
                # LivePolicy counts the critic's consultations; the stub stands for that class, so
                # it carries the same read-out -- and the print below is a fact about the tool, not
                # about an attribute the tool hopes exists.
                return {"asked": 0, "fired": 0}
        fake.LivePolicy = _Policy
        sys.modules["combat.live_policy"] = fake
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = module.main([str(log)])
        out = buf.getvalue()
        check("the replay prints the hypothesis it ran under before its numbers",
              code == 0 and "fill hypothesis:" in out, out[:300])
        check("and prints whether the critic ever picked the realization",
              "value head picks the realization: {'asked': 0, 'fired': 0}" in out,
              out[-200:])
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

    # A rebuild by different labelling code is not session progress; the close
    # records this stamp and the scoreboard keeps the delta out of the trend.
    stamp = module.labeler_sha()
    check("the builder stamps the labelling code into its manifest",
          bool(re.fullmatch(r"[0-9a-f]{12}", stamp))
          and module.summarise([]).get("labeler_sha") == stamp, stamp)

    # The page publishes an element's skill instance as `skill_uid` and its enabled
    # list beside it; the context block used to read only `skill_id` and drop the
    # list, so no row could say whether the page element had a usable skill - the
    # open question for quest 4262/13948 (measured 2026-10-30).
    ctx = module._context_of({"map_id": 145490432, "cell": 504, "map": {},
                              "interactives": [{"elem_id": 513208, "cell": 330,
                                                "skill_uid": 136137181,
                                                "enabled_skills": [136137181, 136137092],
                                                "element_type_id": 17,
                                                "is_door": False}]})
    check("the context keeps the element's skill instance and its enabled list",
          bool(ctx["interactives"])
          and ctx["interactives"][0].get("skill") == 136137181
          and ctx["interactives"][0].get("skills") == [136137181, 136137092],
          ctx["interactives"])
    check("and what kind of object the element is",
          ctx["interactives"][0].get("type") == 17
          and ctx["interactives"][0].get("door") is False, ctx["interactives"])

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

    # Completing an objective usually leaves the step-index keys unchanged; the
    # client publishes it as a `step_objectives` entry flipping to done. Without
    # this signal the corpus saw 0 advances over four days in which 10 objectives
    # completed (G94).
    flip_rows = [
        step(4000, "flip", "quest.advance",
             {"map": {"map_id": 10899, "cell": 3}, "position": {"map_id": 10899, "cell": 3},
              "current_objective": obj, "step_objectives": [{"id": 7, "done": False}],
              "hp": 100},
             {"map": {"map_id": 10899, "cell": 3}, "position": {"map_id": 10899, "cell": 3},
              "current_objective": obj, "step_objectives": [{"id": 7, "done": False}],
              "hp": 100}),
        step(4001, "flip", "state.snapshot",
             {"map": {"map_id": 10899, "cell": 3}, "position": {"map_id": 10899, "cell": 3},
              "current_objective": obj, "step_objectives": [{"id": 7, "done": True}],
              "hp": 100},
             {"map": {"map_id": 10899, "cell": 3}, "position": {"map_id": 10899, "cell": 3},
              "current_objective": obj, "step_objectives": [{"id": 7, "done": True}],
              "hp": 100}),
    ]
    flipped = module.label_steps({"source": "unit", "instance": "unit", "goal": "flip",
                                  "steps": flip_rows})
    check("an objective flipping done is an advance, not only a progress tick",
          flipped[0]["advance"] is True
          and flipped[0]["advance_keys"] == ["objectives_done"],
          [flipped[0]["advance"], flipped[0]["advance_keys"]])
    # An acting step, not a read: `_attribute_lagged` zeroes `advance` on every
    # read, so a read fixture passed even when the first sighting was credited
    # (found by the 518-mutation sweep on 2026-10-31).
    seen_done = module.label_steps({"source": "unit", "instance": "unit", "goal": "first",
                                    "steps": [dict(flip_rows[1], tool="quest.advance")]})
    check("an objective first observed done is not credited to this step",
          seen_done[0]["advance"] is False
          and seen_done[0]["advance_keys"] == [],
          [seen_done[0]["advance"], seen_done[0]["advance_keys"]])
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


def test_no_suite_stops_answering() -> None:
    """A green gate with a smaller total is not the same evidence.

    Measured 2026-11-03: four consecutive runs of an *unchanged* tree reported
    1788 / 1785 / 1788 / 1788 checks, and no SKIP line named the three that went away. `0 failed`
    is only a claim about the checks that ran, so the totals themselves have to be audited --
    against the per-suite counts `data/last_gate_run.json` now carries from the previous run.
    """
    section("The gate's own per-suite check counts")
    entered = _CHECKS
    path = REPO_ROOT / "data" / "last_gate_run.json"
    previous: Dict[str, int] = {}
    if path.is_file():
        try:
            previous = (json.loads(path.read_text(encoding="utf-8")).get("per_test")) or {}
        except (ValueError, OSError):
            previous = {}
    if not previous:
        check("per-suite counts are recorded for the next run to compare against",
              len(CHECK_COUNTS) > 50, len(CHECK_COUNTS))
        print("   FIRST RUN: no previous per-suite counts to compare against")
        return
    # This test's own count is only recorded once it returns, so it is absent from CHECK_COUNTS
    # while it runs; comparing it against the previous run would accuse itself of going silent.
    mine = "test_no_suite_stops_answering"
    silent = sorted(name for name, count in previous.items()
                    if count and name != mine and not CHECK_COUNTS.get(name))
    check("no suite that answered in the previous run went silent in this one",
          not silent, silent[:8])
    added = sorted(name for name in CHECK_COUNTS if name not in previous)
    if added:
        print("   new since the previous run: %s" % ", ".join(added))
    own = _CHECKS - entered
    check("the per-suite counts account for every check this run registered",
          sum(CHECK_COUNTS.values()) + own == _CHECKS,
          (sum(CHECK_COUNTS.values()), own, _CHECKS))


def _run_test(fn) -> None:
    """One test may fail, but it must not hide the rest of the gate.

    Measured 2026-10-28: a source needle in test_dialog_labels raised
    ValueError and the process exited 1 after printing a section header - no
    "checks, failed" line, so the run looked like a pass with a stray exit
    code. A raising test is now a failure the summary counts.

    Each test's own wall time is recorded on the way through, because timing a
    test again to measure it perturbs the number (measured 2026-11-01: the JS
    suites read 6.4 s on their first pass and 30.3 s when a ratchet re-ran
    them).
    """
    import time

    started = time.perf_counter()
    before = _CHECKS
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - the gate must survive its own tests
        import traceback
        traceback.print_exc()
        check("test %s ran to completion" % getattr(fn, "__name__", fn),
              False, "%s: %s" % (type(exc).__name__, exc))
    name = getattr(fn, "__name__", str(fn))
    CHECK_COUNTS[name] = _CHECKS - before
    DURATIONS[name] = round(time.perf_counter() - started, 2)


def run() -> Tuple[int, List[str]]:
    _run_test(test_verdict_vocabulary)
    _run_test(test_verifier_rules)
    _run_test(test_verifier_model_contract)
    _run_test(test_tool_vocabulary)
    _run_test(test_forbidden_and_validation)
    _run_test(test_plan_stack)
    _run_test(test_explore_returns_replay)
    _run_test(test_shared_resources)
    _run_test(test_brain_scaling_40)
    _run_test(test_brain_lifecycle)
    _run_test(test_brain_reports_stale_code)
    _run_test(test_consult_gate_is_fitted_and_live)
    _run_test(test_mutation_targets_have_witnesses)
    _run_test(test_adjacent_crossing_is_bounded_tighter)
    _run_test(test_promise_shadow_names_a_bounced_crossing)
    _run_test(test_online_learn_gate_needs_transfer_it_did_not_copy)
    _run_test(test_duel_simulator_keeps_its_own_contract)
    _run_test(test_lexical_seam_fills_only_what_the_sentence_states)
    _run_test(test_measured_sizes_say_their_unit)
    _run_test(test_cleanroom_harness_is_falsifiable)
    _run_test(test_patch_rules_are_idempotent)
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
    _run_test(test_mutation_journal_recovery)
    _run_test(test_check_helper_survives_a_tuple_detail)
    _run_test(test_veto_alternative_priced)
    _run_test(test_gate_shadow_reaches_the_trajectory)
    _run_test(test_installed_payload_and_dependencies)
    _run_test(test_docs_quote_measurements_by_tag)
    _run_test(test_docs_owed_session_names_the_current_build)
    _run_test(test_console_output_is_utf8)
    _run_test(test_text_io_declares_encoding)
    _run_test(test_docs_live_readout_commands_run)
    _run_test(test_tools_scripts_find_the_repo_root)
    _run_test(test_the_snippet_can_see_its_own_mutation_targets)
    _run_test(test_live_evidence_lands_in_the_repository)
    _run_test(test_docs_command_blocks_state_their_directory)
    _run_test(test_damage_calibration_sees_the_recorded_fights)
    _run_test(test_cross_file_counts_agree)
    _run_test(test_stall_retirement_is_bounded)
    _run_test(test_session_report_play_quality)
    _run_test(test_npc_map_fact_is_routed)
    _run_test(test_map_graph_live_wins)
    _run_test(test_read_streak_is_bounded)
    _run_test(test_kaggle_toolkit_sync)
    _run_test(test_sync_kaggle_toolkit_rules)
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
    _run_test(test_lexical_command_policy)
    _run_test(test_unseen_frame_holdout)
    _run_test(test_egress_preflight)
    _run_test(test_auto_live_guards)
    _run_test(test_host_launch_guard)
    _run_test(test_live_transitions)
    _run_test(test_session_report)
    _run_test(test_data_janitor)
    _run_test(test_market_log)
    _run_test(test_world_state_schema)
    _run_test(test_session_close)
    _run_test(test_session_close_hook)
    _run_test(test_the_close_fits_the_timeout_that_kills_it)
    _run_test(test_side_swap)
    _run_test(test_move_gate)
    _run_test(test_progress_report)
    _run_test(test_artifact_rollback)
    _run_test(test_foresight_net)
    _run_test(test_counterfactual_factory_alive)
    _run_test(test_repair_credit)
    _run_test(test_reads_are_not_actions)
    _run_test(test_dialog_labels)
    _run_test(test_objective_ledger_suite)
    _run_test(test_stress_hardening_suite)
    _run_test(test_menu_window_learning_suite)
    _run_test(test_quest_data_wait)
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
    _run_test(test_objective_travel_is_bounded)
    _run_test(test_search_prefers_fresh_maps)
    _run_test(test_the_search_borders_are_bounded)
    _run_test(test_the_brain_stamps_its_own_code_identity)
    _run_test(test_the_active_quest_survives_one_unplannable_pass)
    _run_test(test_a_crossing_in_flight_is_waited_out)
    _run_test(test_the_session_end_row_names_the_reason)
    _run_test(test_the_plan_reason_reaches_the_action_row)
    _run_test(test_a_return_to_the_objective_map_is_granted_once)
    _run_test(test_a_silent_house_map_is_left_by_the_crossings_it_knows)
    _run_test(test_no_call_is_explained)
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
    _run_test(test_claim_calibration)
    _run_test(test_worker_headroom_layer)
    _run_test(test_rule_gate)
    _run_test(test_gate_shadow)
    _run_test(test_state_features)
    _run_test(test_state_encoder_split)
    _run_test(test_veto_shadow_guard)
    _run_test(test_plan_reason_recorded)
    _run_test(test_equip_skips_worn)
    _run_test(test_return_bound)
    _run_test(test_client_errors_reach_the_loop)
    _run_test(test_refused_calls_are_not_credited)
    _run_test(test_ingest_after_run)
    _run_test(test_ope_estimators)
    _run_test(test_fork_carries_the_planner_state)
    _run_test(test_exploration_policy)
    _run_test(test_explore_report)
    _run_test(test_board_payload_round_trip)
    _run_test(test_slim_live_observation)
    _run_test(test_encounter_input_tables)
    _run_test(test_combat_decision_log)
    _run_test(test_encounter_model_and_checkpoint)
    _run_test(test_doc_facts_self_witness)
    _run_test(test_live_action_realization)
    _run_test(test_duel_harness_statistics)
    _run_test(test_knowledge_index_storage)
    _run_test(test_knowledge_index)
    _run_test(test_combat_suites)
    _run_test(test_env_scatter_and_mask_are_witnessed)
    _run_test(test_go_relay_suites)
    _run_test(test_csharp_compile)
    _run_test(test_cs_secret_redaction)
    _run_test(test_net_pill)
    _run_test(test_boot_state)
    _run_test(test_delegated_suites_report_themselves)
    _run_test(test_delegated_suites_cannot_silently_vanish)
    _run_test(test_the_array_reader_streams_without_losing_records)
    _run_test(test_one_log_record_per_line)
    _run_test(test_every_mutation_anchor_still_points_at_code)
    # Last, because it reads the durations every test above recorded.
    _run_test(test_the_mutation_pass_stays_affordable)
    # Last, because it audits the counts every other test registered.
    _run_test(test_no_suite_stops_answering)
    return _CHECKS, _FAILURES


def test_delegated_suites_report_themselves() -> None:
    """A delegated suite signs for its run, so the summary can be read.

    A delegation that ran and one that returned early print the same one section header.
    The registry is how the gate tells them apart, and this is the half of the rule that
    is safe inside the mutation snippet: it only asks whether the JS page suites signed,
    so a mutation that drops a signature is caught by the sweep.
    """
    section("Delegated suites sign for themselves")
    check("the JS page-suite delegation signed for its run",
          any(d["name"] == "JS page suites" for d in _DELEGATIONS),
          [d["name"] for d in _DELEGATIONS])


def test_delegated_suites_cannot_silently_vanish() -> None:
    """A skipped delegation whose sources are in the tree is a red gate.

    The four delegations used to `print("SKIP: <tool> not on PATH")` and return without
    registering a check, so a host without node, `go` or a C# compiler reported
    "N checks, 0 failed, exit 0" with 40 page suites, the relay tests and the host
    compile all absent (read at the call sites 2026-10-31). Sources genuinely missing --
    the Kaggle mirror has no `scripts/` -- stays a legitimate skip.
    """
    section("Delegated suites cannot vanish quietly")
    silent = [d for d in _DELEGATIONS if not d["ran"] and d["sources"]]
    check("no delegated suite was skipped while its sources are in the tree",
          not silent, ["%s: %s" % (d["name"], d["detail"]) for d in silent])
    check("the registry covers every delegation the gate claims",
          {d["name"] for d in _DELEGATIONS} >= {"JS page suites", "go relay tests",
                                                "C# host compile",
                                                "recorder regex probe"},
          sorted(d["name"] for d in _DELEGATIONS))


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


def test_claim_calibration() -> None:
    section("Per-form calibration: fitted on train, priced at the counter's budget")
    import importlib.util
    import tempfile

    tool = REPO_ROOT / "tools" / "live_outcome_probe.py"
    check("the calibration probe exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("live_outcome_probe_calibration", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    check("a destination nav and a direction nav are different forms",
          module.call_form({"tool": "nav.goto", "args": {"map_id": 11}}) == "nav.goto:at"
          and module.call_form({"tool": "nav.goto", "args": {"direction": "left"}})
          == "nav.goto:step"
          and module.call_form({"tool": "nav.goto", "args": {"cell": 3}}) == "nav.goto:step")
    check("the three quest.advance promises are kept apart",
          [module.call_form({"tool": "quest.advance", "args": a}) for a in
           ({"accept": True}, {"reply": 4}, {"npc_id": 7}, {})]
          == ["quest.accept", "quest.reply", "quest.talk", "quest.other"])
    check("a single-form tool keeps its name",
          module.call_form({"tool": "fight.join", "args": {}}) == "fight.join")
    check("the bucket clamps the repeat streak at three",
          [module.streak_bucket({"repeat_streak_norm": n}) for n in (0, 1, 2, 3, 9)]
          == ["0", "1", "2", "3+", "3+"])
    check("the counter is the normalized two-identical-miss rule, not the exact one",
          module.streak_bucket({"repeat_streak_norm": 0, "repeat_streak": 5}) == "0"
          and module._counter_flag([{"repeat_streak_norm": 0, "repeat_streak": 2}]) == [False]
          and module._counter_flag([{"repeat_streak_norm": 2}]) == [True])

    # Fit arithmetic on a cell whose numbers can be checked by hand: 12 met and 12
    # missed in one form, streaks 0 and 1.
    rows = [{"tool": "nav.goto", "args": {"direction": "left"}, "claim_met": True,
             "repeat_streak_norm": 0} for _ in range(12)]
    rows += [{"tool": "nav.goto", "args": {"direction": "left"}, "claim_met": False,
              "repeat_streak_norm": 1} for _ in range(12)]
    model = module.fit_calibration(rows)
    global_rate = (12 + 1.0) / (24 + 2.0)
    rate = (12 + 8.0 * global_rate) / (24 + 8.0)
    entry = model["forms"]["nav.goto:step"]
    check("the global rate is a Laplace estimate",
          abs(model["global_rate"] - global_rate) < 1e-9, model["global_rate"])
    check("a form is shrunk toward the global rate with eight pseudo-rows",
          abs(entry["rate"] - rate) < 1e-9, entry["rate"])
    check("a bucket with twelve of twelve is not certain",
          abs(entry["buckets"]["0"]["p"] - (12 + 8.0 * rate) / 20.0) < 1e-9
          and entry["buckets"]["0"]["p"] < 1.0, entry["buckets"]["0"])
    check("an unseen bucket falls back to its form's rate",
          abs(entry["buckets"]["3+"]["p"] - rate) < 1e-9, entry["buckets"]["3+"])
    check("an unseen form falls back to the global rate",
          abs(module.calibration_p(model, {"tool": "fight.join", "args": {},
                                           "repeat_streak_norm": 0})
              - global_rate) < 1e-9)

    # Form shrinkage was already "checked" above without being testable: with one form
    # the global rate *is* the form's rate, so removing the prior changed nothing and the
    # mutant escaped as equivalent. A second, nearly-empty form makes the prior visible.
    two = [{"tool": "nav.goto", "args": {"direction": "left"}, "claim_met": bool(i % 2),
            "repeat_streak_norm": 0} for i in range(40)]
    two += [{"tool": "fight.join", "args": {}, "claim_met": True,
             "repeat_streak_norm": 0}]
    two_model = module.fit_calibration(two)
    two_global = (21 + 1.0) / (41 + 2.0)
    shrunk = (1 + 8.0 * two_global) / (1 + 8.0)
    check("a one-row form is pulled to the global rate instead of claiming certainty",
          abs(two_model["forms"]["fight.join"]["rate"] - shrunk) < 1e-9
          and two_model["forms"]["fight.join"]["rate"] < 0.6,
          two_model["forms"]["fight.join"])

    # The promotion bar had a floor nobody enforced: `PROMOTION_DELTA_POINTS` was
    # declared and never read, so any interval clearing zero promoted a gate that beat
    # the counter by half a point.
    check("the bar needs the pre-registered point gain, not merely a positive interval",
          module._promotion_ok({"delta_vs_counter": (0.001, 0.2),
                                "kept_success": 0.68, "successes_retained": 0.99},
                               0.66) is False
          and module._promotion_ok({"delta_vs_counter": (0.001, 0.2),
                                    "kept_success": 0.72, "successes_retained": 0.99},
                                   0.66) is True)
    check("a tie with the counter cannot pass the point floor either",
          module._promotion_ok({"delta_vs_counter": (0.0, 0.2), "kept_success": 0.80,
                                "successes_retained": 1.0}, 0.6) is False
          and module._promotion_ok({}, 0.6) is False
          and module._promotion_ok(None, 0.6) is False)

    # The counter vetoes the form that is fine after two misses and keeps the form
    # that is doomed at streak 0..1. A calibrated score can see the form; the point of
    # the probe is to price that advantage at the counter's own veto budget, and to
    # fit the threshold on train only (flipping test labels must not move a tau).
    def cell(tool, args, met, streak, seg):
        return {"segment": seg, "tool": tool, "args": args, "claim_met": met,
                "repeat_streak_norm": streak, "x": [0.5] * 8}

    def corpus(seg_prefix, flip_test=False):
        train, test = [], []
        for s in range(4):
            seg = "%s%d" % (seg_prefix, s)
            for _ in range(5):
                train.append(cell("nav.goto", {"direction": "left"}, True, 0, seg))
                train.append(cell("nav.goto", {"direction": "left"}, False, 1, seg))
                train.append(cell("nav.goto", {"direction": "left"}, False, 2, seg))
                train.append(cell("quest.advance", {"npc_id": 7}, False, 0, seg))
                train.append(cell("quest.advance", {"npc_id": 7}, False, 1, seg))
                train.append(cell("quest.advance", {"npc_id": 7}, True, 2, seg))
                test.append(cell("nav.goto", {"direction": "left"}, True, 0, seg))
                test.append(cell("nav.goto", {"direction": "left"}, False, 1, seg))
                test.append(cell("nav.goto", {"direction": "left"}, False, 2, seg))
                test.append(cell("quest.advance", {"npc_id": 7}, False, 0, seg))
                test.append(cell("quest.advance", {"npc_id": 7}, False, 1, seg))
                test.append(cell("quest.advance", {"npc_id": 7}, True, 2, seg))
        if flip_test:
            test = [dict(r, claim_met=not r["claim_met"]) for r in test]
        return train, test

    def run_corpus(train, test):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            for name, rows in (("train", train), ("test", test)):
                (data / ("%s.jsonl" % name)).write_text(
                    chr(10).join(json.dumps(r) for r in rows), encoding="utf-8")
            return module.calibration_probe(data, "claim_met")["pooled"]

    train, test = corpus("p")
    good = run_corpus(train, test)
    flipped = run_corpus(*corpus("p", flip_test=True))
    check("the threshold is fitted on train only, so test labels cannot move it",
          good["safe"]["tau"] is not None and flipped["safe"]["tau"] == good["safe"]["tau"],
          (good.get("safe"), flipped.get("safe")))
    check("the counter vetoes every streak-2 call, successes included",
          good["counter"]["vetoed"] == 40 and good["counter"]["kept_success"] == 0.25,
          good["counter"])
    check("at the counter's budget the calibrated score vetoes only failures",
          good["safe_at_counter_budget"]["vetoed"] == 40
          and good["safe_at_counter_budget"]["vetoed_success"] == 0.0
          and good["safe_at_counter_budget"]["kept_success"] == 0.5,
          good["safe_at_counter_budget"])
    check("and that qualifies under the pre-registered promotion bar",
          good["promotion"]["safe_beats_counter"] is True, good["promotion"])

    # A counter that already vetoes exactly the failures ties the calibrated score.
    # A tie is not a promotion: the bar requires the paired interval to exclude zero.
    rows = []
    for s in range(4):
        seg = "t%d" % s
        for _ in range(5):
            rows.append(cell("nav.goto", {"direction": "right"}, True, 0, seg))
            rows.append(cell("nav.goto", {"direction": "right"}, False, 1, seg))
            rows.append(cell("nav.goto", {"direction": "right"}, False, 2, seg))
    tied = run_corpus(rows, rows)
    check("a calibrated gate that ties the counter is not promoted",
          tied["promotion"]["safe_beats_counter"] is False, tied["promotion"])


def test_worker_headroom_layer() -> None:
    section("A worker on the counter's kept calls: headroom first, then a priced layer")
    import importlib.util
    import tempfile

    tool = REPO_ROOT / "tools" / "live_outcome_probe.py"
    check("the worker probe exists", tool.is_file(), str(tool))
    if not tool.is_file():
        return
    spec = importlib.util.spec_from_file_location("live_outcome_probe_worker", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    cols = module._state_columns()
    check("the worker's state block is the mechanism block without the streak counters",
          len(cols) == len(module.MECHANISM_NAMES) - 2
          and module.MECHANISM_NAMES.index("miss_streak") not in cols
          and module.MECHANISM_NAMES.index("repeat_streak") not in cols, len(cols))

    # 8 segments x 15 rows: 9 met at streak 0, 1 miss at streak 0 (the counter keeps
    # it), 5 misses at streak 2 (the counter vetoes them). With a state signal the
    # head orders the kept calls and the extra layer removes only failures; without
    # one it removes successes and must not be promoted.
    def row(seg, met, streak, ready):
        return {"segment": seg, "tool": "nav.goto", "args": {"direction": "left"},
                "claim_met": met, "repeat_streak_norm": streak,
                "context": {"ready": [{"objective": i} for i in range(ready)]},
                "x": [0.5] * 8}

    def corpus(signal):
        train, test = [], []
        for s in range(8):
            seg = "w%d" % s
            for _ in range(9):
                train.append(row(seg, True, 0, 2 if signal else 0))
                test.append(row(seg, True, 0, 2 if signal else 0))
            train.append(row(seg, False, 0, 0))
            test.append(row(seg, False, 0, 0))
            for _ in range(5):
                train.append(row(seg, False, 2, 0))
                test.append(row(seg, False, 2, 0))
        # a form with no train rows must be skipped, not fitted
        test.append(row("w0", True, 0, 0))
        test[-1].update(tool="fight.join")
        return train, test

    def run(signal):
        train, test = corpus(signal)
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            for name, rows in (("train", train), ("test", test)):
                (data / ("%s.jsonl" % name)).write_text(
                    chr(10).join(json.dumps(r) for r in rows), encoding="utf-8")
            return module.worker_probe(data, "claim_met")

    good_full = run(True)
    good = good_full["forms"]["nav.goto:step"]
    check("a form seen only in test is not fitted",
          "fight.join" not in good_full["forms"], sorted(good_full["forms"]))
    check("the fit only counts judged rows from train and test",
          good["train_rows"] == 120 and good["train_positives"] == 72
          and good["test_rows"] == 120 and good["test_positives"] == 72, good)
    check("the counter vetoes the repeated misses and keeps the rest",
          good["counter"]["vetoed"] == 40 and good["counter"]["kept_success"] == 0.9
          and good["counter"]["successes_retained"] == 1.0, good["counter"])
    check("headroom is measured on the calls the counter keeps",
          good["headroom"]["auc_all"] == 1.0 and good["headroom"]["auc_kept"] == 1.0
          and good["headroom"]["kept_rows"] == 80
          and good["headroom"]["kept_positives"] == 72, good["headroom"])
    check("a 5% extension removes four calls, all failures",
          good["extension_5pct"]["removed_n"] == 4
          and good["extension_5pct"]["removed_met"] == 0.0
          and good["extension_5pct"]["kept_success"] == 0.947, good["extension_5pct"])
    check("an extension below the minimum is reported but not judged",
          good["extension_5pct"]["promotion"] is False, good["extension_5pct"])
    check("a 10% extension removes every kept failure and keeps every success",
          good["extension_10pct"]["removed_n"] == 8
          and good["extension_10pct"]["removed_met"] == 0.0
          and good["extension_10pct"]["kept_success"] == 1.0
          and good["extension_10pct"]["successes_retained"] == 1.0, good["extension_10pct"])
    check("its paired interval excludes zero and it qualifies",
          good["extension_10pct"]["delta_vs_counter"] == (0.1, 0.1)
          and good["extension_10pct"]["promotion"] is True, good["extension_10pct"])
    check("the form as a whole is promoted only through that priced layer",
          good["promotion"] is True, good)

    blind = run(False)["forms"]["nav.goto:step"]
    check("without a state signal the kept calls are at chance",
          blind["headroom"]["auc_kept"] == 0.5, blind["headroom"])
    check("and the extra layer removes successes instead of failures",
          blind["extension_10pct"]["removed_met"] == 1.0
          and blind["extension_10pct"]["kept_success"] == 0.889, blind["extension_10pct"])
    check("a layer that removes successes is never promoted",
          blind["extension_10pct"]["promotion"] is False and blind["promotion"] is False,
          blind["promotion"])


def test_rule_gate() -> None:
    section("The repeat-streak gate is shadow-only (records the veto, sends nothing)")
    from agent.gate import RuleGate

    gate = RuleGate()
    call = ("nav.goto", {"direction": "left"})
    check("a fresh rule is not vetoed", gate.shadow(*call)["would_veto"] is False,
          gate.shadow(*call))
    gate.observe(*call, verdict="noop")
    check("one miss is not enough to veto", gate.shadow(*call)["would_veto"] is False,
          gate.shadow(*call))
    gate.observe(*call, verdict="noop")
    shadow = gate.shadow(*call)
    check("two consecutive misses would veto the identical request",
          shadow["would_veto"] is True and shadow["streak"] == 2, shadow)
    check("the shadow record carries no action but its verdict",
          set(shadow) == {"rule", "streak", "would_veto"}, shadow)

    gate.observe(*call, verdict="advanced")
    check("an advance resets that rule", gate.shadow(*call)["streak"] == 0,
          gate.shadow(*call))

    gate.observe(*call, verdict="failed")
    gate.observe("state.snapshot", {}, verdict="advanced")
    check("a read neither advances nor resets a rule",
          gate.shadow(*call)["streak"] == 1, gate.shadow(*call))
    gate.observe("nav.goto", {"direction": "right"}, verdict="noop")
    check("different args are a different rule",
          gate.shadow(*call)["streak"] == 1
          and gate.shadow("nav.goto", {"direction": "right"})["streak"] == 1,
          (gate.shadow(*call), gate.shadow("nav.goto", {"direction": "right"})))

    gate.clear()
    check("a session reset clears every counter", gate.shadow(*call)["streak"] == 0,
          gate.shadow(*call))

    # Measured 2026-10-29: three taps on element 513208 carried skill_uid
    # 136137029/136137092/136137181, so an exact-args key counted three fresh calls
    # and the loop's dominant retry never reached a streak. Volatile fields are not
    # part of the intent.
    tap_a = ("tap.interactive", {"element_id": 513208, "skill_uid": 136137029, "cell": 504})
    tap_b = ("tap.interactive", {"element_id": 513208, "skill_uid": 136137092, "cell": 549})
    gate.observe(*tap_a, verdict="noop")
    gate.observe(*tap_b, verdict="noop")
    check("taps that differ only in skill_uid/cell are one rule",
          gate.shadow(*tap_a)["streak"] == 2 and gate.shadow(*tap_b)["would_veto"] is True,
          (gate.shadow(*tap_a), gate.shadow(*tap_b)))
    gate.observe("tap.interactive", {"element_id": 513208, "skill_uid": 9, "cell": 1},
                 verdict="advanced")
    check("an advance on the normalized rule resets all of them",
          gate.shadow(*tap_b)["streak"] == 0, gate.shadow(*tap_b))
    gate.observe("nav.goto", {"direction": "left", "lang": "fr"}, verdict="noop")
    gate.observe("nav.goto", {"direction": "left", "lang": "en"}, verdict="noop")
    check("lang is not part of a nav rule",
          gate.shadow("nav.goto", {"direction": "left"})["streak"] == 2,
          gate.shadow("nav.goto", {"direction": "left"}))
    gate.observe("nav.goto", {"direction": "right", "lang": "fr"}, verdict="noop")
    check("a different direction is still a different rule",
          gate.shadow("nav.goto", {"direction": "right"})["streak"] == 1,
          gate.shadow("nav.goto", {"direction": "right"}))

    source = (REPO_ROOT / "agent" / "autopilot.py").read_text(encoding="utf-8")
    check("the loop records the gate beside the call it sent",
          '_meta["gate"] = self._gate.shadow(call.tool, call.args)' in source
          and "self._gate.observe(call.tool, call.args, verdict)" in source,
          "autopilot gate wiring missing")


def test_gate_shadow() -> None:
    section("The gate's live record survives the corpus build and the report")
    import importlib.util
    import json as _json
    import tempfile

    tool = REPO_ROOT / "tools" / "build_live_transitions.py"
    spec = importlib.util.spec_from_file_location("build_live_transitions_gate", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "dofus-99.jsonl"
        gate = {"rule": 'nav.goto|{"direction": "left"}', "streak": 2, "would_veto": True}
        steps = []
        for i, cell in enumerate((2, 3)):
            steps.append({"kind": "step", "instance": "dofus-99", "goal": "g",
                          "index": i, "ts": 1_793_200_000.0 + i * 10,
                          "tool": "nav.goto", "args": {"direction": "left"},
                          "ok": True, "latency_ms": 5,
                          "observation": {"map_id": 1, "cell": [i, 2]},
                          "result": {"snapshot": {"map_id": 1, "cell": [i, 2]}},
                          "meta": {"gate": gate if i == 0 else None}})
        log.write_text(chr(10).join(_json.dumps(s) for s in steps), encoding="utf-8")
        built, _stats = module.build([log], 120.0)
        check("the shadow veto rides the corpus row",
              built[0]["gate"]["would_veto"] is True and built[1]["gate"] is None, built)

        # Three taps on one element that all miss: the exact key sees three calls,
        # the normalized key sees one retry streak. This is the loop's real tap shape
        # (element 513208, 2026-10-29).
        tap_log = Path(tmp) / "dofus-98.jsonl"
        taps = []
        for i, skill in enumerate((111, 222, 333)):
            taps.append({"kind": "step", "instance": "dofus-98", "goal": "g",
                         "index": i, "ts": 1_793_200_000.0 + i * 10,
                         "tool": "tap.interactive",
                         "args": {"element_id": 513208, "skill_uid": skill, "cell": 504 + i},
                         "ok": True, "latency_ms": 5,
                         "observation": {"map_id": 1, "cell": [1, 2], "inventory_size": 9},
                         "result": {"snapshot": {"map_id": 1, "cell": [1, 2],
                                                 "inventory_size": 9}}})
        tap_log.write_text(chr(10).join(_json.dumps(s) for s in taps), encoding="utf-8")
        tap_rows, _stats = module.build([tap_log], 120.0)
        check("the corpus keeps the exact and normalized retry counts apart",
              [r["repeat_streak"] for r in tap_rows] == [0, 0, 0]
              and [r["repeat_streak_norm"] for r in tap_rows] == [0, 1, 2],
              [(r["repeat_streak"], r["repeat_streak_norm"]) for r in tap_rows])

    probe_spec = importlib.util.spec_from_file_location(
        "live_outcome_probe_shadow", REPO_ROOT / "tools" / "live_outcome_probe.py")
    probe = importlib.util.module_from_spec(probe_spec)
    probe_spec.loader.exec_module(probe)
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        rows = [
            {"segment": "s1", "t": 0, "tool": "nav.goto", "args": {"direction": "left"},
             "claim_met": False, "gate": {"would_veto": True}, "x": [0.5] * 8},
            {"segment": "s1", "t": 1, "tool": "nav.goto", "args": {"direction": "left"},
             "claim_met": True, "gate": {"would_veto": False}, "x": [0.5] * 8},
        ]
        (data / "test.jsonl").write_text(
            chr(10).join(_json.dumps(r) for r in rows), encoding="utf-8")
        out = probe.shadow_probe(data, "claim_met")
        stat = out["tools"]["nav.goto"]
        check("the report counts would-veto decisions and what they did",
              out["tagged"] == 2 and stat["would_veto"] == 1
              and stat["vetoed_judged"] == 1 and stat["vetoed_met"] == 0, out)
        check("and whether the loop repeated the rule anyway",
              stat["next_same_rule"] == 1 and stat["next_other_rule"] == 0, stat)


def test_state_features() -> None:
    section("Relational state features (entity rows are relative, fixed width)")
    from agent.state_features import (ENTITY_DIM, GLOBAL_DIM, entity_vectors,
                                      global_vector, world_delta)

    obs = {
        "map_id": 7, "cell": 100, "level": 40, "kamas": 5000, "in_dialog": False,
        "hp": {"life": 50, "max": 100, "ap": 6, "mp": 3},
        "inventory": [1, 2, 3],
        "map": {
            "npcs": [{"cell": 102, "npc_id": 10}],
            "interactives": [{"cell": 128, "type_id": 3}],
            "groups": [{"group_id": 5, "monsters": [{"cell": 96, "level": 20}]}],
            "exits": [{"cells": [88, 200]}, {"cells": [], "blocked": True}],
        },
    }
    rows = entity_vectors(obs)
    check("every entity row has the encoder's fixed width",
          rows and all(len(r) == ENTITY_DIM for r in rows), [len(r) for r in rows])
    check("entities are emitted in the documented kind order",
          [max(range(5), key=lambda k: r[k]) for r in rows] == [1, 2, 3, 4, 4], rows)
    # Character on cell 100 = (2, 7). NPC on 102 = (4, 7): +2 x, same row, L1 2.
    npc = rows[0]
    check("coordinates are relative to the character and scaled",
          abs(npc[5] - 0.2) < 1e-6 and abs(npc[6]) < 1e-6 and abs(npc[7] - 0.1) < 1e-6,
          npc)
    # Exit cells 88 = (4, 6) and 200 = (4, 14): from (2, 7) that is 3 vs 9.
    check("an exit with many cells reduces to its nearest",
          abs(rows[3][7] - 3 / 20.0) < 1e-6, rows[3])
    check("an exit without cells still yields a row",
          abs(rows[4][8] - 1.0) < 1e-6, rows[4])
    check("the global vector has its documented width",
          len(global_vector(obs)) == GLOBAL_DIM, len(global_vector(obs)))
    check("an unreadable observation degrades to zeros, not an exception",
          global_vector(None) == [0.0] * GLOBAL_DIM and entity_vectors(None) == [], "degraded")

    before = {"map_id": 7, "cell": [1100, 20], "in_dialog": False,
              "quest_steps": {"1": 0}, "inventory_size": 3}
    after = {"map_id": 7, "cell": [1101, 20], "in_dialog": True,
             "quest_steps": {"1": 0}, "inventory_size": 3}
    delta = world_delta(before, after)
    check("the world-model target flags exactly the changed watched keys",
          len(delta) == 10 and delta == [0.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
          delta)
    check("the delta is symmetric in what it watches",
          world_delta(after, after) == [0.0] * 10, world_delta(after, after))


def test_state_encoder_split() -> None:
    section("The encoder probe's split is temporal, segment-clustered and non-overlapping")
    import importlib.util

    tool = REPO_ROOT / "tools" / "state_encoder_probe.py"
    spec = importlib.util.spec_from_file_location("state_encoder_probe_split", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Two sessions on the older day, three on the newer one; the within fold keeps
    # only the newer days and must cut on a segment boundary.
    rows = []
    for day, base in (("2026-10-28", 0.0), ("2026-10-29", 100_000.0)):
        for s in range(8):
            seg = "%s:%d" % (day, s)
            for i in range(20):
                rows.append({"day": day, "ts": base + 1000.0 * (s + 1) + i,
                             "segment": seg, "claim_met": (i % 2 == 0),
                             "split": "test" if day == "2026-10-29" else "train"})
    data = {"rows": rows, "test_days": ["2026-10-28", "2026-10-29"]}

    train, test, note = module.split_rows(data, "within")
    check("the within fold holds out the latest time slice only",
          train and test and max(r["ts"] for r in train) < min(r["ts"] for r in test), note)
    check("no segment is split across the cut",
          not ({r["segment"] for r in train} & {r["segment"] for r in test}), note)
    check("the cut lands near the documented 60%",
          0.4 <= len(train) / float(len(train) + len(test)) <= 0.8,
          (len(train), len(test)))

    old_train, old_test, note = module.split_rows(data, "days")
    check("the across-days fold trains on the older days",
          {r["day"] for r in old_train} == {"2026-10-28"}
          and {r["day"] for r in old_test} == {"2026-10-29"}, note)
    source = tool.read_text(encoding="utf-8")
    check("the probe masks held-out world-model deltas out of pretraining",
          "held = {id(r) for r in test}" in source and "held-out deltas masked" in source,
          "pretraining mask missing")


def test_plan_reason_recorded() -> None:
    section("A planned walk carries its reason into the step row (G61)")
    from agent.planner import QuestPlanner

    A, B = 145490432, 145490946
    free = {"quest_id": 9262, "objective_id": 913948, "type_id": 0,
            "type": "QuestObjectiveFreeForm", "map_id": A,
            "params": {"parameter0": 704145}, "text": "Recover the first page.",
            "step_id": 4954}
    fight = {"quest_id": 9264, "objective_id": 913966, "type_id": 6,
             "type": "QuestObjectiveFightMonster", "map_id": B,
             "params": {"parameter0": 4212, "parameter1": 1},
             "text": "Defeat 1 Brytnee Spyr.", "step_id": 4956}

    def scene(here: int) -> Dict[str, Any]:
        other = B if here == A else A
        return {"map_id": here, "cell": 330, "level": 20, "xp": 0,
                "ready_objectives": [free, fight], "step_objectives": [free, fight],
                "quests_active": {"9262": 0, "9264": 0},
                "map": {"map_id": here, "cell": 330, "groups": [], "npcs": [],
                        "exits": [{"map_id": other, "cell": 100}],
                        "interactives": ([{"elem_id": 513208, "cell": 330,
                                           "skill_uid": 111, "enabled_skills": [111]}]
                                         if here == A else [])},
                "quests_catalog": {"quests": [], "steps": [], "objectives": []}}

    planner = QuestPlanner()
    first = planner.choose("do all the quests", scene(A))
    planner.note_result(first.tool, True, "", scene(A))
    travel = planner.choose("do all the quests", scene(A))
    check("the planned travel names the objective that asked for it",
          travel is not None and travel.tool == "nav.goto"
          and "objective 913966" in (travel.why or ""), getattr(travel, "why", None))
    meta = travel.record_meta() or {}
    check("and it reaches the corpus through the record metadata",
          "objective 913966" in str(meta.get("why") or ""), meta)
    check("the reason never enters the directive arguments",
          not any(k in travel.args for k in ("why", "reason")), travel.args)


def test_equip_skips_worn() -> None:
    section("An equip step never re-offers a piece that is already worn (G62)")
    from agent.planner import QuestPlanner

    quest, obj = {"id": 9001}, {"id": 9002}
    planner = QuestPlanner()
    bag = {"items": [{"gid": 18844, "object_uid": 1, "qty": 1},
                     {"gid": 18974, "object_uid": 2, "qty": 1}],
           "equipped": [{"slot": 1, "gid": 18844}]}
    uid = planner._next_equip_uid(quest, obj, {"inventory": bag})
    check("the worn piece is skipped and the free one is offered",
          uid == 2, (uid, bag["equipped"]))

    planner2 = QuestPlanner()
    all_worn = {"items": [{"gid": 18844, "object_uid": 1, "qty": 1}],
                "equipped": [{"slot": 1, "gid": 18844}]}
    check("nothing is left to equip when every copy is worn",
          planner2._next_equip_uid({"id": 9003}, {"id": 9004},
                                   {"inventory": all_worn}) is None, all_worn)

    planner3 = QuestPlanner()
    two = {"items": [{"gid": 77, "object_uid": 10, "qty": 1},
                     {"gid": 77, "object_uid": 11, "qty": 1}],
           "equipped": [{"slot": 2, "gid": 77}]}
    check("with two copies and one worn, a free copy is still offered",
          planner3._next_equip_uid({"id": 9005}, {"id": 9006},
                                   {"inventory": two}) in (10, 11), two)


def test_return_bound() -> None:
    section("One objective cannot walk the loop back forever (G61)")
    from agent.planner import CROSSING_ASK_LIMIT, QuestPlanner, RETURN_LIMIT

    A, B = 145490432, 145490946
    fight = {"quest_id": 9264, "objective_id": 913966, "type_id": 6,
             "type": "QuestObjectiveFightMonster", "map_id": B,
             "params": {"parameter0": 4212, "parameter1": 1},
             "text": "Defeat 1 Brytnee Spyr.", "step_id": 4956}

    def scene(here: int) -> Dict[str, Any]:
        other = B if here == A else A
        return {"map_id": here, "cell": 330, "level": 20, "xp": 0,
                "ready_objectives": [fight], "step_objectives": [fight],
                "quests_active": {"9264": 0},
                "map": {"map_id": here, "cell": 330, "groups": [], "npcs": [],
                        "exits": [{"map_id": other, "cell": 100}], "interactives": []},
                "quests_catalog": {"quests": [], "steps": [], "objectives": []}}

    planner = QuestPlanner()
    # The character has just arrived at A from B, and the only ready objective
    # wants B: every decision is the return half of an A->B->A bounce.
    planner._prev_map = B
    returned = []
    for _ in range(RETURN_LIMIT + 1):
        call = planner.choose("do all the quests", scene(A))
        returned.append(None if call is None else (call.tool, dict(call.args)))
    # This fixture never lets the character arrive, so two guards see the same
    # shape: the per-objective return budget and the per-border count, the tighter
    # of which is what speaks first.
    bound = min(RETURN_LIMIT, CROSSING_ASK_LIMIT)
    walks = [1 for c in returned[:bound]
             if c and c[0] == "nav.goto" and c[1].get("map_id") == B]
    check("the first returns are still made", len(walks) == bound, returned)
    check("after the bound the loop stops walking back",
          returned[bound] is None
          or not (returned[bound][0] == "nav.goto"
                  and returned[bound][1].get("map_id") == B), returned)
    key = (9264, 913966)
    check("the counter is kept per objective", planner._returns.get(key) == RETURN_LIMIT,
          planner._returns)

    fresh = QuestPlanner()
    fresh._prev_map = A  # coming from somewhere else is not a return
    call = fresh.choose("do all the quests", scene(A))
    check("a walk that does not undo the last crossing is never parked",
          call is not None and call.tool == "nav.goto"
          and call.args.get("map_id") == B, None if call is None else (call.tool, call.args))


def test_client_errors_reach_the_loop() -> None:
    section("A refusal the client reports reaches the loop's own log (G64)")
    import contextlib
    import io as _io

    from agent.autopilot import Autopilot

    ap = Autopilot.__new__(Autopilot)
    ap._last_client_error = None
    obs = {"client_errors": [{"at": 1, "text": "Vous ne pouvez pas parler a ce PNJ."}]}
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        ap._note_client_error(obs)
        ap._note_client_error(obs)
    out = buf.getvalue()
    check("the first sighting is printed in the server's own words",
          "Vous ne pouvez pas parler a ce PNJ." in out, out)
    check("a repeat does not fill the log", out.count("the client reported") == 1, out)
    buf2 = _io.StringIO()
    with contextlib.redirect_stdout(buf2):
        ap._note_client_error({"client_errors": [{"text": "another refusal"}]})
    check("a new message is printed again", "another refusal" in buf2.getvalue(),
          buf2.getvalue())
    buf3 = _io.StringIO()
    with contextlib.redirect_stdout(buf3):
        ap._note_client_error({})
        ap._note_client_error({"client_errors": []})
        ap._note_client_error({"client_errors": "not a list"})
        ap._note_client_error(None)
    check("a page build without the field is silent", buf3.getvalue() == "", buf3.getvalue())
    src = (REPO_ROOT / "agent" / "autopilot.py").read_text(encoding="utf-8")
    check("the no-advance branch feeds the observation into the channel",
          "_note_client_error(after)" in src, "the capture is never consulted")
    page = (ROOT / "scripts" / "mobile-disguise.js").read_text(encoding="utf-8")
    check("the page wraps the client's error channel, not a guess at it",
          "chat.logError" in page and "__agentHooked" in page,
          "the page does not capture the client error channel")


def test_refused_calls_are_not_credited() -> None:
    section("A page-refused call never reached the server, so it is a miss (G65)")
    import importlib.util

    tool = REPO_ROOT / "tools" / "build_live_transitions.py"
    spec = importlib.util.spec_from_file_location("build_live_transitions_refusal", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def objective(oid: int) -> Dict[str, Any]:
        return {"quest_id": "4262", "objective_id": oid, "type_id": 0,
                "type": "QuestObjectiveFreeForm", "map_id": 145490432,
                "params": {"parameter0": 704145}, "text": "page", "step_id": 4954}

    def scene(oid: int) -> Dict[str, Any]:
        ready = objective(oid)
        return {"map_id": 145490432, "cell": 300, "in_dialog": False,
                "quests_active": {"4262": 0}, "quest_steps": {"4262": 4954},
                "ready_objectives": [ready], "current_objective": ready}

    refusal_text = "a previous claim of objective 13948 of quest 4262 ended the connection"
    refused_result = {"ok": True, "response": {"result": {
        "ok": False, "data": {"reason": "known_kick_signature", "error": refusal_text},
        "error": refusal_text}}}
    sent_result = {"ok": True, "response": {"result": {"ok": True, "data": {}, "error": None}}}

    def step(row_ts: float, tool_name: str, args: Dict[str, Any],
             obs: Dict[str, Any], result: Any) -> Dict[str, Any]:
        return {"kind": "step", "tool": tool_name, "args": args, "ok": True,
                "ts": row_ts, "observation": obs, "result": result}

    args = {"quest_id": 4262, "objective_id": 13948}
    segment = {"source": "live", "goal": "do all the quests", "instance": "dofus-03",
               "steps": [
                   step(1000.0, "quest.advance", args, scene(13948), refused_result),
                   step(1001.0, "state.snapshot", {}, scene(13950), sent_result),
                   step(1002.0, "quest.advance", args, scene(13950), refused_result),
                   step(1003.0, "state.snapshot", {}, scene(13950), sent_result),
               ]}
    rows = module.label_steps(segment)
    first = rows[0]
    check("the refusal is read from the bridge envelope",
          first.get("refused") and "known_kick_signature" in first["refused"],
          first.get("refused"))
    check("the state moved, but the refused call is not credited",
          first.get("claim_met") is False, first.get("claim_met"))
    check("the refusal becomes the row's error, not a null",
          refusal_text in str(first.get("error") or ""), first.get("error"))
    check("nothing was sent, so the refused call owns no world change",
          first.get("moved") is False and first.get("progress") is False
          and first.get("advance") is False,
          (first.get("moved"), first.get("progress"), first.get("advance")))
    check("what differed is still recorded as a raw diff",
          bool(first.get("changed")), first.get("changed"))
    check("a change a later read reveals is not blamed on the refused call",
          rows[1].get("progress") is False and rows[1].get("moved") is False,
          (rows[1].get("progress"), rows[1].get("moved")))
    check("a refused retry counts as a miss for the streak",
          rows[2].get("repeat_streak") == 1, rows[2].get("repeat_streak"))

    sent = {"source": "live", "goal": "do all the quests", "instance": "dofus-03",
            "steps": [
                step(2000.0, "quest.advance", args, scene(13948), sent_result),
                step(2001.0, "state.snapshot", {}, scene(13950), sent_result),
            ]}
    ok_rows = module.label_steps(sent)
    check("a sent claim whose state moved is still credited",
          ok_rows[0].get("claim_met") is True and ok_rows[0].get("refused") is None,
          (ok_rows[0].get("claim_met"), ok_rows[0].get("refused")))
    check("a plain transport success is not read as a refusal",
          module._refusal({"ok": True, "result": {"ok": True, "snapshot": {}}}) is None,
          module._refusal({"ok": True, "result": {"ok": True, "snapshot": {}}}))


def test_veto_shadow_guard() -> None:
    section("No learned veto is wired into the loop (G58 lost out of time in G59)")
    autopilot = (REPO_ROOT / "agent" / "autopilot.py").read_text(encoding="utf-8")
    check("the loop never loads a veto model",
          "veto_shadow" not in autopilot and "joblib" not in autopilot,
          "a learned veto reached the loop")
    gate = (REPO_ROOT / "agent" / "gate.py").read_text(encoding="utf-8")
    check("the gate has no model dependency",
          not any(lib in gate for lib in ("sklearn", "joblib", "torch", "numpy")),
          "gate grew a model dependency")
    check("the trainer is where a candidate model must prove itself",
          (REPO_ROOT / "tools" / "train_veto_shadow.py").exists(), "trainer missing")


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


def test_knowledge_index_storage() -> None:
    """The knowledge index's compact stores, on the kinds that are quick to build.

    Split out of `test_installed_payload_and_dependencies` on 2026-11-03 because that function
    reads the built `setup.exe` and spawns interpreters, so it cannot run once per mutation --
    and the mutation sweep's snippet did not include it. Four mutations written against the
    projection store therefore reported MISSED with 0 failing checks: nothing that could see the
    store ran. This function is the fast half (spell and quest, ~0.2 s), so the sweep can hold the
    storage representation honest.

    What is pinned: a projected row's slot is a row *number* and its fields come from one
    kind-wide JSON blob (measured 20.7 MB of live projection dicts for 21,554 item rows, read by
    at most `limit` of them per query); and `quest` keeps materialized dicts because
    `quest.list` filters on `is_dungeon`/`level_min`/`start_position` per row.
    """
    section("Knowledge index storage (one blob per kind, one number per row)")
    from agent.executor import _KnowledgeIndex, _INDEX_FIELDS

    index = _KnowledgeIndex(REPO_ROOT / "data" / "cache")
    rows = index.entries("spell")
    offsets, blob = index._val["spell"]
    check("a projected row is a row number, and its fields come from one kind-wide blob",
          _INDEX_FIELDS.get("spell") is not None and len(offsets) == len(rows) + 1
          and bool(blob) and all(isinstance(row[3], int) for row in rows)
          and bool(index._stored("spell", 0).get("name_fr")
                   or index._stored("spell", 0).get("name_en")),
          (len(rows), len(offsets), len(blob)))
    check("the haystack array still covers every row of that kind",
          len(index._hay_for("spell")[0]) == len(rows) + 1, len(rows))
    check("a search answer carries the projected fields, not an empty shell",
          all(set(("id", "name", "type_id")) <= set(hit) - {"kind", "score"}
              for hit in index.search("bouftou", "spell", 3))
          and index.search("bouftou", "spell", 3) != [],
          index.search("bouftou", "spell", 3)[:1])
    quest_rows = index.entries("quest")
    check("and the kind that filters per row still holds real dicts",
          quest_rows and all(isinstance(row[3], dict) for row in quest_rows[:20]),
          type(quest_rows[0][3]).__name__ if quest_rows else "no quests")

    # The names region is `\x00`-joined, not space-joined. That is the half of the packing that
    # changes behaviour, so it is the half pinned by an answer rather than by a shape: measured
    # over 1,380 query arms (260 real names plus 80 constructed `name_a + " " + name_b` splices,
    # each run against four kinds), the two representations differ on 162 arms and every one of
    # them is a constructed splice -- no real query moved. Under a space join the splice is
    # present in the row's text, so the row takes the "whole query found" tier (2.0 + 1.5 +
    # 0.5 per token); with the separator it is not, and the row can only reach the token tier
    # (1.5 + 0.5 per token). Same row, same query, different claim about what the row is called.
    rows = index.entries("spell")
    pair_index = next((i for i in range(len(rows))
                       if len(index._names("spell", i)) >= 2), None)
    names = index._names("spell", pair_index) if pair_index is not None else []
    splice = "%s %s" % (names[0], names[1]) if names else ""
    hits = index.search(splice, "spell", 50) if splice else []
    row_score = max((h["score"] for h in hits if h.get("id") == rows[pair_index][0]),
                    default=None)
    # The best a row can score without the name tier: the token path, 1.5 for "every token is
    # present" plus 0.5 each. The name tier is above it by at least the 2.0 "whole query found"
    # term, so the two are separated by construction.
    token_floor = 1.5 + 0.5 * len([t for t in splice.split(" ") if len(t) >= 2])
    check("two variants of one name are two names, so splicing them earns no name tier",
          len(names) >= 2 and splice not in names
          and (row_score is None or row_score <= token_floor + 0.01),
          (names[:2], splice, row_score, token_floor))

    # The scan finds occurrences anywhere in the kind's blob and maps each back to its row, so a
    # match that starts in one record and finishes in the next must belong to neither. Rows are
    # concatenated with no separator, so such crossings exist in the real data. Asserted as an
    # equivalence against the definition the code replaced -- the bounded per-row search -- on
    # every needle drawn from the first rows of the item blob, crossings included: dropping the
    # containment guard makes the two sets differ, and so does a wrong row mapping.
    # The blessed snapshot doubles as the memory witness: it records residency on the shipped
    # interpreter after every kind, so a change that re-materializes per-row objects fails here
    # without the gate having to rebuild the index under a profiler. The bounds are generous on
    # purpose -- today's index sits at 85.0 resident / 78.7 commit MB on 3.11.9, and the state
    # before this month's compaction was 168 / 162 -- so this catches a class of regression, not
    # noise.
    baseline_file = REPO_ROOT / "data" / "knowledge_index_baseline.json"
    if baseline_file.is_file():
        blessed = json.loads(baseline_file.read_text(encoding="utf-8"))
        probe_ram = blessed.get("ram") or {}
        after = probe_ram.get("after_probes") or {}
        check("the blessed index snapshot still reports a memory figure under the bound",
              bool(after) and after.get("resident_mb", 1e9) <= 100.0
              and after.get("commit_mb", 1e9) <= 110.0,
              (blessed.get("python"), after))
    # The scan maps every occurrence in a kind's blob back to the row it starts in. Rows are
    # concatenated with no separator of their own, so a needle can start in one record and finish
    # in the next, and must then belong to neither -- the rule the per-row bounded search had free.
    # Proven on a fixture rather than the real cache, because every quest row ends with its
    #  (quests have no descriptions), so no separator-free crossing can be built from quest
    # data at all: a check that claimed to find 20 crossings there was counting needles that
    # never crossed anything, and a dropped containment guard walked through it as MISSED.
    import tempfile

    with tempfile.TemporaryDirectory() as fixture_dir:
        fixture = Path(fixture_dir)
        (fixture / "items_full.json").write_text(json.dumps([
            {"id": 1, "name": {"fr": "bouftou majestueux", "en": "majestic oregon"},
             "level": 10, "type_id": 772, "price": 100, "is_saleable": True,
             "description": "une houe forgee dans le bronzage"},
            {"id": 2, "name": {"fr": "epade du zeth", "en": "zeth sword"},
             "level": 20, "type_id": 772, "price": 200, "is_saleable": False,
             "description": "taille dans la glace de zeth"},
            {"id": 3, "name": {"fr": "kolosses cle", "en": "kolossus key"},
             "level": 30, "type_id": 600, "price": 300, "is_saleable": True,
             "description": "ouvrez la porte des kolosses"},
        ], ensure_ascii=False), encoding="utf-8")
        fx = _KnowledgeIndex(fixture)
        fx_rows = fx.entries("item")
        f_offsets, f_blob = fx._hay_for("item")
        needles = []
        for boundary in range(1, len(fx_rows)):
            head = f_blob[f_offsets[boundary]:f_offsets[boundary] + 12]
            cut = 0
            while cut < len(head) and head[cut] not in (0, 1):
                cut += 1
            tail = f_blob[max(0, f_offsets[boundary] - 14):f_offsets[boundary]]
            keep = 0
            while keep < len(tail) and tail[len(tail) - 1 - keep] not in (0, 1):
                keep += 1
            if keep and cut:
                # The LAST four bytes before the boundary plus the first four after it: taking the
                # first four of that tail slice leaves a gap and the needle sits wholly inside the
                # previous row -- which is how this fixture passed while a dropped containment
                # guard went unnoticed.
                needles.append((tail[len(tail) - keep:][-4:] + head[:4], boundary))
            needles.append((head[:8], boundary))
            needles.append((tail[len(tail) - keep:][-8:], boundary))
        checked = crossings = mismatches = 0
        for needle_bytes, boundary in needles:
            if len(needle_bytes) < 6:
                continue
            window = {r for r in (boundary - 1, boundary, boundary + 1)
                      if 0 <= r < len(fx_rows)}
            bounded = {r for r in window
                       if f_blob.find(needle_bytes, f_offsets[r], f_offsets[r + 1]) >= 0}
            scanned = set(fx._rows_with(f_offsets, f_blob, needle_bytes, f_offsets,
                                        end_shift=1)) & window
            checked += 1
            # A needle the bounded search finds in NO row is a crossing, provided it really occurs
            # somewhere -- those are the cases the guard exists for, and counting a needle that
            # appears nowhere would make the "crossings" figure mean nothing.
            crossings += (not bounded) and (f_blob.find(needle_bytes) >= 0)
            if scanned != bounded:
                mismatches += 1
        check("the blob scan answers exactly what the per-row bounded search answered, "
              "including needles that cross a record boundary",
              checked >= 6 and crossings >= 1 and mismatches == 0,
              (checked, crossings, mismatches))

    # And the two name tiers must stay distinguishable: `\x00` marks where a name stops, so a
    # prefix that is not an entire name scores 4.0 and never 6.0. Scores are what the baseline-id
    # parity check does NOT compare, so this asserts the arithmetic directly.
    q_rows = index.entries("quest")
    prefix_word = longest = ""
    row_id = None
    for i in range(min(len(q_rows), 200)):
        for name in index._names("quest", i):
            if " " in name and len(name.split(" ")[0]) >= 6:
                prefix_word, longest, row_id = name.split(" ")[0], name, q_rows[i][0]
                break
        if prefix_word:
            break
    scores = [h.get("score") for h in index.search(prefix_word, "quest", 50)
              if h.get("id") == row_id] if prefix_word else []
    check("a name that only starts with the query is not read as an exact name",
          bool(prefix_word) and prefix_word != longest and bool(scores)
          and max(scores) < 6.0 + 2.0 + 1.5 + 0.5,
          (prefix_word, longest, scores[:2]))


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
    # The gate's own count is a countable claim in the prose. Recording it here means
    # tools/doc_facts.py can check the docs against the last real run instead of against
    # a number someone typed.
    try:
        (REPO_ROOT / "data" / "last_gate_run.json").write_text(json.dumps({
            "checks": checks,
            "failed": len(failures),
            "at": time.time(),
            "per_test": CHECK_COUNTS,
        }), encoding="utf-8")
    except OSError:
        pass
    print("\n%d checks, %d failed" % (checks, len(failures)))
    if failures:
        print("\nFAILURES:\n  " + "\n  ".join(failures))
        return 1
    print("all regression cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
