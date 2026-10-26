"""Offline regression gate for the agent loop — the contract the deleted
smoke_verifier used to enforce (KNOWN_GAPS.md G8).

No client, no network, no torch, no onnxruntime. It runs the REAL code paths:

  * the verifier rule engine (all four verdicts, nested-alias changes, the
    ready_objectives claim-proof, read-only-as-advanced),
  * the schema / capability / feature tool vocabulary (they must be one set,
    and hdv.buy must stay absent and forbidden),
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

    check("schema declares 21 tools", len(tool_names) == 21, len(tool_names))
    check("capabilities.json lists 21 tools", len(caps_ids) == 21, len(caps_ids))
    check("features.tool_registry lists the same set as the schema",
          set(reg_ids) == set(tool_names), (sorted(set(reg_ids) ^ set(tool_names))))
    check("capabilities.json matches the schema set",
          set(caps_ids) == set(tool_names), (sorted(set(caps_ids) ^ set(tool_names))))
    check("no duplicate tool ids", len(set(tool_names)) == len(tool_names))
    check("hdv.buy is not in the schema (purchases are forbidden)",
          "hdv.buy" not in tool_names)


def test_forbidden_and_validation() -> None:
    from agent.autopilot import FORBIDDEN_TOOLS
    from agent import schema

    section("safety gates")
    check("the purchase ban is exactly hdv.buy",
          set(FORBIDDEN_TOOLS) == {"hdv.buy"}, sorted(FORBIDDEN_TOOLS))

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
    check("a text-only wrapper plans no hunt even with a group on the map",
          planned == [], planned)
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
    check("and the fallback path plans no hunt",
          QuestPlanner().plan_objective({"id": 3841}, 0, derived, obs) == [])
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
    check("and the merged row plans no hunt",
          QuestPlanner().plan_objective({"id": 3841}, 0, merged, obs) == [])

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
          planner_src.count("declares_monster_target(obj) is False") == 3,
          planner_src.count("declares_monster_target(obj) is False"))


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
        return kctx.enrich_observation(obs, store)["knowledge"]

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
        store)["knowledge"]
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

    kept = kctx.enrich_observation(dialog(tracker), store)
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
                   "tools/test_dialog_links.js"):
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
              "fight_estimate, fight_outcome = self._track_fight(before, after)"))
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
    check("a text-only head is claimed once, then the loop sends nothing rather than a "
          "phantom hunt or a quest this game has never shown",
          alone[0][2] == ((3841, 14070), "quest.advance")
          and [row[0] for row in alone[1:]] == [None] * 3, str(alone))

    # The other head shape: an objective that does declare a creature, for a creature
    # this map does not report and no NPC can start. Its only plan is a read that names
    # no target. G27 took this shape away from the free-form wrapper (a text-only head now
    # plans nothing at all), so the declared hunt is what exercises the defer path -- and
    # without these two rows the defer and the deferred-wait branches are unprotected.
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
    check("the controller is handed the payload at the decision site",
          'detail=getattr(result, "data", None))' in loop_src)


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


def run() -> Tuple[int, List[str]]:
    test_verdict_vocabulary()
    test_verifier_rules()
    test_verifier_model_contract()
    test_tool_vocabulary()
    test_forbidden_and_validation()
    test_objective_buckets()
    test_objective_parameter_table()
    test_knowledge_identity()
    test_client_confirmed_starts()
    test_quest_advance_shapes()
    test_dialog_ranking()
    test_prompt_fidelity()
    test_named_mob_focus()
    test_thinking_layer()
    test_claim_quarantine()
    test_failure_causes()
    test_absent_client_recovery()
    test_repair_credit()
    test_reads_are_not_actions()
    test_dialog_labels()
    test_dialog_repeat_guard()
    test_dialog_label_reader()
    test_brain_autostart()
    test_brain_readiness()
    test_board_replay()
    test_group_scan_reader()
    test_npc_start_rotation_bound()
    test_group_refusal_routing()
    test_ready_objective_scan()
    test_session_report_census()
    test_report_instrument()
    test_settle_waits_for_promised_effect()
    test_intervention_instrument()
    test_js_page_suites()
    test_bundle_resilience()
    test_go_relay_suites()
    return _CHECKS, _FAILURES


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
