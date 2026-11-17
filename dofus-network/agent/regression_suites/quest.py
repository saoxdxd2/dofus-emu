"""Extracted regression suite: quest"""
from __future__ import annotations
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from agent.regression_suites.common import REPO_ROOT, ROOT, check, section, logger

def _gap_text() -> str:
    """Every gap entry, whether it sits in the register or in the archive.

    On 2026-11-13 the register was split: `docs/KNOWN_GAPS.md` keeps the entries that are
    still open plus a generated index, and the entries whose own heading says `(fixed ...)`
    moved to `docs/archive/KNOWN_GAPS_closed.md` verbatim. A check that asserted an archived
    entry's text against the register alone would have started failing for a reason that is
    not a defect -- so the checks read the union and keep asserting the same thing: the
    corrected arithmetic is still in the documentation, wherever the documentation lives.
    """
    parts = []
    for path in [REPO_ROOT / "docs" / "KNOWN_GAPS.md"] + sorted(
            (REPO_ROOT / "docs" / "archive").glob("*.md")):
        if path.exists():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def test_objective_buckets() -> None:
    from agent import features

    section("objective-type buckets")
    check("talk types are {1, 9}", features.TALK_TYPES == {1, 9}, features.TALK_TYPES)
    # 45 = QuestObjectiveWinMonsterInDungeon ("Defeat Belladonna in this boss's
    # dungeon"): a real fight objective, routed through the kill/fight planner.
    check("kill types cover {0, 6, 14, 16, 45}", features.KILL_TYPES == {0, 6, 14, 16, 45},
          features.KILL_TYPES)
    check("fight types are {6, 45}", features.FIGHT_TYPES == {6, 45}, features.FIGHT_TYPES)
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
    # The bound moved behind an environment override (`AUTOPILOT_RETIRE_LIMIT`, default 3)
    # when the constants became tunable; the property this pins is unchanged -- a default
    # of three and a counter that is actually incremented -- so it reads the default out
    # of the declaration instead of matching one spelling of it.
    check("retirements are bounded per run",
          'RETIRE_LIMIT = int(os.environ.get("AUTOPILOT_RETIRE_LIMIT", "3"))' in module_src
          and "self._retired += 1" in src,
          "the per-run retirement bound is not declared or not counted")
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
    gaps = _gap_text()
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


def test_quest_area_rejects_the_prompt_echo() -> None:
    """A name slot filled with the observation text is refused, not served empty.

    Measured: the learned tool policy answers `quest.list` with a slice of its own
    prompt -- `area="urrent state: where: map 146538496 cell 176"` -- in 168 rows of
    the 2026-11-08 trajectory and 20 more in the 2026-11-11 live run. Served as an
    empty list it reads as a successful read, so the policy repeats it; refused, it is
    an outcome the memory can record.
    """
    section("quest.list refuses an area that is the observation text")
    from agent.executor import ToolExecutor, _is_prompt_echo

    check("the live string is an echo",
          _is_prompt_echo("urrent state: where: map 146538496 cell 176"))
    check("so is the full prompt line",
          _is_prompt_echo("where: map 146538496 cell 176 in_world true"))
    for name in ("astrub", "3 kolk", "champs de glace", "la foret des abraknydes",
                 "amakna"):
        check("%r is a place name, not an echo" % name, not _is_prompt_echo(name))

    class _Knowledge:
        def entries(self, kind):        # noqa: ARG002 - only truthiness is read
            return [1]

    executor = object.__new__(ToolExecutor)
    executor.knowledge = _Knowledge()
    refused = executor._quest_list(
        {"area": "urrent state: where: map 146538496 cell 176"}, "test")
    check("the handler refuses it", refused.ok is False, refused.ok)
    check("and says what was wrong with the argument",
          "observation text" in str(refused.error), refused.error)


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
        # accepted, and nothing moved: the "86% inert" in old reviews was the third
        # chronological quarter of the corpus (88.6%); the newest quarter is 39.5%.
        # See `agent/inert_attribution.py` for the era-stratified measurement.
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
          and 'QUEST_DATA_WAIT_LIMIT = int(os.environ.get('
              '"AUTOPILOT_QUEST_DATA_WAIT_LIMIT", "3"))' in source,
          "an unbounded wait is a spin")
    check("and the branch waits instead of falling through to the retirement",
          "instead of retiring the goal" in source
          and source.index("_quest_data_pending(before)")
          < source.index("nothing in the policy matched this goal; had"),
          "the wait must run before the blocked verdict is emitted")
    check("the counter resets once the data arrives",
          'before.get("ready_objectives")' in source
          and "self._quest_data_waits = 0" in source, "a stale count would shorten a later wait")


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


__all__ = ['test_objective_buckets', 'test_objective_parameter_table', 'test_stuck_objective_retired', 'test_quest_advance_shapes', 'test_quest_area_rejects_the_prompt_echo', 'test_live_transitions', 'test_quest_data_wait', 'test_quest_accept_planner']