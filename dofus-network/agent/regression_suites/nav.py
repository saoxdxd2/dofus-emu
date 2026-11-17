"""Extracted regression suite: nav"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List, Optional
from agent.regression_suites.common import REPO_ROOT, ROOT, check, section, logger

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


__all__ = ['test_doorless_map_asks_for_the_neighbour', 'test_free_form_searches_new_maps', 'test_objective_travel_is_bounded', 'test_return_bound', 'test_the_search_borders_are_bounded', 'test_search_prefers_fresh_maps']