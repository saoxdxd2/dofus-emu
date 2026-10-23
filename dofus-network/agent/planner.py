"""Agent 2's planner: the quest graph turned into tool calls.

The verifier answers "did that work"; the controller decides "what now"; this is
the missing "what is the *plan*". It reads the knowledge base (1,976 quests with
machine-readable objectives), picks a quest, and translates the current objective
into the one call the actor should make next.

Two deliberate choices:

* **The translation is deterministic.** `objective type 4 → nav.goto(map_id)` is a
  fact about the data, not something to learn. Learning it would be a way to get it
  wrong. What *is* learned later is which quest to pick and what to do when a step
  fails.
* **It re-plans every step.** The planner is a pure function of (observation, KB),
  so a world change is picked up on the next call with no invalidation logic — which
  is the reactivity that action-chunking approaches give up.

`choose()` implements the same `Policy` protocol as the rule policy, so the loop
does not need to know which one it is talking to.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.features import ObsTracker, required_map, travel_hops  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE = REPO_ROOT / "data" / "cache"

TALK_TYPES = {1, 9}
MAP_TYPES = {4}
AREA_TYPES = {5}
KILL_TYPES = {0, 6, 14, 16}
FIGHT_TYPES = {6}          # "in a fight": needs fight.join, not farm.start
DELIVER_TYPES = {2, 3, 12}
CRAFT_TYPES = {17}


def _params(obj: Dict[str, Any]) -> Dict[str, Any]:
    raw = obj.get("params")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw.replace("'", '"'))
        except ValueError:
            return {}
    return {}


def args_for_tool(tool: str, quest: Dict[str, Any], obj: Dict[str, Any],
                  obs: Dict[str, Any]) -> Dict[str, Any]:
    """The arguments a tool needs, derived from the objective's own parameters.

    This is data movement, not judgement: `parameter0` *is* the npc id. It lives here
    so the teacher, the student and the corpus generator cannot each invent their own
    slightly different idea of what `farm.start` should be given.
    """
    p = _params(obj)
    t = obj.get("type_id")
    if tool == "nav.goto":
        need = obj.get("map_id")
        if need is None and t in MAP_TYPES:
            need = p.get("parameter0")
        try:
            need = int(need) if need is not None else None
        except (TypeError, ValueError):
            need = None
        return {"map_id": need} if need else {}
    if tool == "quest.advance":
        npc = p.get("parameter0") if t in (1, 9, 3) else p.get("parameter1")
        args: Dict[str, Any] = {"quest_id": int(quest["id"])}
        if npc is not None:
            try:
                args["npc_id"] = int(npc)
            except (TypeError, ValueError):
                pass
        return args
    if tool == "farm.start":
        if t in DELIVER_TYPES:
            item = p.get("parameter1") if t == 3 else p.get("parameter0")
            try:
                return {"item": int(item)} if item is not None else {}
            except (TypeError, ValueError):
                return {}
        monster = p.get("parameter0")
        try:
            return {"monster": int(monster)} if monster is not None else {}
        except (TypeError, ValueError):
            return {}
    if tool == "fight.join":
        monster = p.get("parameter0")
        try:
            return {"monster": int(monster)} if monster is not None else {}
        except (TypeError, ValueError):
            return {}
    return {}


class QuestPlanner:
    """A policy that plans over the quest graph."""

    def __init__(self, level_hint: int = 60, max_quest_level: int = 200,
                 store: Any = None, curriculum: str = "greedy",
                 seed: int = 0, focus: Optional[int] = None,
                 ordering: str = "step") -> None:
        self.quests: Dict[int, Dict[str, Any]] = {}
        path = CACHE / "quests_full.json"
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                for q in json.load(fh):
                    if q.get("steps"):
                        self.quests[int(q["id"])] = q
        self.level_hint = level_hint
        self.max_quest_level = max_quest_level
        self.current_quest: Optional[int] = None
        self._order: Optional[List[int]] = None
        # Monotone memory of what the observation ever said was finished. The
        # planner is otherwise a pure function of the observation, which is correct
        # only while the observation is complete — and it is not: the client pages
        # the quest log, and a paged list made this planner re-pick a finished quest
        # for four hundred consecutive steps. A fact once observed cannot un-happen,
        # so remembering it costs nothing and removes a whole class of loop.
        self._seen_done: set = set()
        # Quests the planner has looked at and cannot plan (area objectives, crafts
        # with no recipe). Retiring them keeps the loop from re-deriving the same
        # dead end every step.
        self._skipped: set = set()
        self._plannable_cache: Dict[int, bool] = {}
        # "greedy" is the shipping policy: fewest objectives first. "random" exists
        # because a corpus must cover the decisions an agent faces, and the greedy
        # curriculum does not: it always finishes the shortest quests first, so the
        # state distribution it produces is whatever the shortest quests happen to
        # contain. That is a property of the curriculum, not of the game.
        self.curriculum = curriculum
        self.rng = random.Random(seed)
        # A corpus builder may choose which states to include. Following one policy's
        # preferences instead is how a corpus ends up 87% deliveries: the greedy
        # curriculum always finishes the shortest quests first, and the shortest
        # quests are all the same shape.
        self.focus = focus
        # "step" is the KB's order, which is arbitrary. "nearest" costs nothing to
        # compute and is exactly optimal on this data (7.3 vs 7.3 travel steps per
        # step), so the only reason to keep "step" is to have something to compare to.
        self.ordering = ordering
        self._by_type: Optional[Dict[int, List[int]]] = None
        # History of its own attempts. The caller reports the outcome back through
        # `note_result`, so the policy sees exactly what the corpus saw — a policy
        # that infers its own failures from an observation delta is trained on one
        # signal and served another.
        self.tracker = ObsTracker()
        # Discovered facts (npc -> map, for example) change plans without any
        # retraining. This is the point of keeping knowledge out of the weights.
        self.store = store

    # -- curriculum --------------------------------------------------------

    def _curriculum(self, level: int) -> List[int]:
        """Quests worth doing, cheapest first.

        Level gate first (a level-130 quest at level 42 is not a plan, it is a
        wish), then fewest objectives per level band — the short quests finish, and
        a finished quest is progress the loop can verify.
        """
        if self._order is not None and level == self.level_hint:
            return self._order
        rows = []
        for qid, q in self.quests.items():
            qlevel = int(q.get("level_min") or 1)
            if qlevel > level or qlevel > self.max_quest_level:
                continue
            objectives = sum(len(s.get("objectives") or []) for s in q["steps"])
            if objectives == 0:
                continue
            rows.append((objectives, qlevel, qid))
        rows.sort()
        self._order = [qid for _, _, qid in rows]
        self.level_hint = level
        return self._order

    # -- objective translation --------------------------------------------

    def plan_objective(self, quest: Dict[str, Any], step_idx: int,
                       obj: Dict[str, Any], obs: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The calls this objective needs, in order. Empty when unsupported."""
        t = obj.get("type_id")
        p = _params(obj)
        here = obs.get("map_id")

        # Where this objective has to happen. The knowledge base names a map for
        # 10,421 of its objectives and the world refuses to complete one from the
        # wrong place, so travelling is a precondition, not a suggestion.
        need = obj.get("map_id")
        if need is None and t in MAP_TYPES:
            need = p.get("parameter0")
        travel: List[Dict[str, Any]] = []
        if need is not None:
            try:
                need = int(need)
            except (TypeError, ValueError):
                need = None
        if not need:          # 0 is the KB's "no map", not a destination
            need = None
        if need is not None and here != need:
            travel = [{"tool": "nav.goto", "args": {"map_id": need},
                       "why": "objective %s: go to map %d" % (t, need)}]

        if t in MAP_TYPES:
            if need is not None:
                return travel
            return []

        if t in TALK_TYPES:
            npc = p.get("parameter0")
            if npc is None:
                return []
            calls = list(travel)
            # If the agent has discovered where this NPC is, travel first. The KB
            # has no npc -> map table; the store fills that gap at runtime.
            target = None
            if self.store is not None:
                try:
                    target = self.store.get("npc_map", "npc:%d" % int(npc))
                except Exception:  # noqa: BLE001
                    target = None
            if target and not travel and here != target:
                calls.append({"tool": "nav.goto", "args": {"map_id": int(target)},
                              "why": "travelled to npc %d (learned fact)" % int(npc)})
            calls.append({"tool": "quest.advance",
                          "args": {"quest_id": int(quest["id"]), "npc_id": int(npc)},
                          "why": "objective %d: talk" % t})
            return calls

        if t in KILL_TYPES:
            monster = p.get("parameter0")
            calls = list(travel)
            if t in FIGHT_TYPES:
                calls.append({"tool": "fight.join",
                              "args": {"monster": monster} if monster else {},
                              "why": "objective %d: fight" % t})
                return calls
            calls.append({"tool": "farm.start",
                          "args": {"monster": monster} if monster else {},
                          "why": "objective %d: kill" % t})
            return calls

        if t in DELIVER_TYPES:
            item = p.get("parameter1") if t == 3 else p.get("parameter0")
            npc = p.get("parameter0") if t == 3 else p.get("parameter1")
            calls = list(travel)
            # Inventories arrive with either int or str keys depending on who built
            # the observation; a str-only lookup made the planner gather the same
            # item forever because it never believed it had one.
            inv = obs.get("inventory") or {}
            have = bool(item is not None and (inv.get(item) or inv.get(str(item))))
            cap = int(obs.get("inventory_capacity") or 0)
            if item is not None and not have and cap and len(inv) >= cap:
                # Nowhere to put the thing we need. Selling is a precondition here,
                # not an optimisation: without it the loop gathers into a full bag
                # for the rest of its budget.
                calls.append({"tool": "inv.sell", "args": {},
                              "why": "objective %d: bag full, sell first" % t})
            elif item is not None and not have:
                # Getting the item is a farming problem; the honest first move is to
                # go and farm it rather than pretend the delivery can happen.
                calls.append({"tool": "farm.start", "args": {"item": item},
                              "why": "objective %d: need item %s first" % (t, item)})
            if npc is not None:
                calls.append({"tool": "quest.advance",
                              "args": {"quest_id": int(quest["id"]), "npc_id": int(npc)},
                              "why": "objective %d: deliver" % t})
            return calls

        if t in CRAFT_TYPES:
            # Crafting needs a recipe and materials model the simulator does not
            # have. Returning inv.list here looked harmless and cost 213 steps: a
            # read is not progress, and repeating one is a loop with a good excuse.
            return []

        # Area objectives need the area -> map graph, which the KB does not expose
        # here. Refreshing the state is honest: it costs a step and keeps the loop
        # from inventing a destination.
        return []

    def _focus_order(self, level: int) -> List[int]:
        """Quests that contain the focused objective type, cheapest first."""
        if self.focus is None:
            return []
        if self._by_type is None:
            index: Dict[int, List[int]] = {}
            for qid, q in self.quests.items():
                for step in q.get("steps") or []:
                    for obj in step.get("objectives") or []:
                        t = obj.get("type_id")
                        if t is None:
                            continue
                        try:
                            index.setdefault(int(t), []).append(qid)
                        except (TypeError, ValueError):
                            continue
            self._by_type = {k: sorted(set(v)) for k, v in index.items()}
        return self._by_type.get(int(self.focus), [])

    # -- policy interface --------------------------------------------------

    def _objective_in(self, quest: Dict[str, Any], step_idx: int,
                      obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Which objective is actually open.

        The quest tracker in the observation is the only trustworthy answer: the
        planner used to take "the first objective in the step that produces calls",
        which is a different objective as soon as the first one is already done.
        That mismatch made a talk call complete a kill objective — every tool worked,
        so the tool choice was not a decision at all.

        With ``ordering="nearest"`` it takes the closest open objective instead of the
        first. The step order is arbitrary; the distance is not, and it is worth 22% of
        the travel. Only usable when the tracker belongs to this quest — otherwise the
        step list describes someone else's step and "nearest" would be a guess.
        """
        step = quest["steps"][step_idx]
        objectives = step.get("objectives") or []
        tracker = obs.get("current_objective") or {}

        if self.ordering == "nearest" and int(tracker.get("quest_id") or 0) == int(quest["id"]) \
                and int(tracker.get("step", -1)) == int(step_idx):
            done = {o.get("id") for o in (obs.get("step_objectives") or []) if o.get("done")}
            here = obs.get("map_id")
            best, best_cost = None, None
            for obj in objectives:
                if obj.get("id") in done:
                    continue
                if not self.plan_objective(quest, step_idx, obj, obs):
                    continue
                need = required_map(obj)
                cost = travel_hops(here, need) if need else 0
                if best_cost is None or cost < best_cost:
                    best, best_cost = obj, cost
            if best is not None:
                return best

        if tracker and int(tracker.get("quest_id") or 0) == int(quest["id"]):
            for obj in objectives:
                if obj.get("id") == tracker.get("objective_id"):
                    return obj
        for obj in objectives:
            if self.plan_objective(quest, step_idx, obj, obs):
                return obj
        return None

    def _plan_quest(self, quest_id: int, step_idx: int,
                    obs: Dict[str, Any], goal: str = "do all the quests") -> Optional[Any]:
        from agent.runtime import ToolCall

        quest = self.quests.get(quest_id)
        if quest is None or step_idx >= len(quest["steps"]):
            return None
        obj = self._objective_in(quest, step_idx, obs)
        if obj is None:
            return None
        calls = self.plan_objective(quest, step_idx, obj, obs)
        if not calls:
            return None
        first = calls[0]
        self.current_quest = quest_id
        return ToolCall(first["tool"], dict(first["args"]), goal)

    def note_result(self, tool: str, ok: bool, note: str, obs: Dict[str, Any]) -> None:
        """The caller reports what happened. Without this the policy cannot learn
        from its own failures, and any history feature would be a guess."""
        self.tracker.record(tool, ok, note, obs)

    def fork(self) -> "QuestPlanner":
        """A planner for a counterfactual fork.

        The policy carries state — which quests it has retired, which it has seen
        finished. Letting every fork share one planner contaminated the corpus: a
        twin's dead end was recorded as the real episode's, so after a few dozen rows
        the teacher refused every quest and every candidate scored zero. The policy
        has to fork with the world or the labels are fiction.
        """
        twin = object.__new__(QuestPlanner)
        twin.quests = self.quests
        twin.level_hint = self.level_hint
        twin.max_quest_level = self.max_quest_level
        twin.current_quest = self.current_quest
        twin._order = self._order
        twin._seen_done = set(self._seen_done)
        twin._skipped = set(self._skipped)
        twin._plannable_cache = self._plannable_cache   # pure function of the KB
        twin.store = self.store
        twin.curriculum = self.curriculum
        twin.rng = random.Random(self.rng.random())
        twin.focus = self.focus
        twin.ordering = self.ordering
        twin._by_type = self._by_type
        twin.tracker = ObsTracker()
        return twin

    def _plannable(self, quest_id: int) -> bool:
        """Can this quest be finished at all with the tools and KB we have?

        Checked *before* accepting. Accepting first and discovering later is how the
        loop deadlocks: an accepted quest with one unsupported objective becomes the
        world's current objective forever, every call fails against it, and the run
        burns its whole budget. The pre-check costs one pass over the objective list.
        """
        cached = self._plannable_cache.get(quest_id)
        if cached is not None:
            return cached
        quest = self.quests.get(quest_id)
        ok = bool(quest)
        if ok:
            probe = {"map_id": None, "inventory": {}, "inventory_capacity": 0}
            for step_idx, step in enumerate(quest["steps"]):
                for obj in step.get("objectives") or []:
                    # The teacher's translation, deliberately: "plannable" is a
                    # property of the KB and the tools, not of whatever weights are
                    # loaded today. Asking the student would let a bad epoch decide
                    # that the whole quest graph is unplannable.
                    if not QuestPlanner.plan_objective(self, quest, step_idx, obj, probe):
                        ok = False
                        break
                if not ok:
                    break
        self._plannable_cache[quest_id] = ok
        return ok

    def choose(self, goal: str, observation: Optional[Dict[str, Any]],
               tools: Optional[Sequence[str]] = None) -> Optional[Any]:
        from agent.runtime import ToolCall

        obs = observation or {}
        self.tracker.observe(obs)
        level = int(obs.get("level") or self.level_hint)
        active = obs.get("quests_active") or {}
        self._seen_done |= {int(q) for q in (obs.get("quests_done") or [])}
        active_ids = [int(k) for k in active.keys()]

        # 0. Claim anything the game says is already done.
        #
        # objectiveStatus 2 (CAN_BE_FINISHED) means the conditions are met and the only
        # thing missing is the validation call. That is free progress, and leaving it
        # unclaimed is why a quest could look stuck while it was actually ready.
        for item in (obs.get("ready_objectives") or [])[:1]:
            try:
                return ToolCall("quest.advance",
                                {"quest_id": int(item["quest_id"]),
                                 "objective_id": int(item["objective_id"])},
                                goal)
            except (KeyError, TypeError, ValueError):
                # A malformed ready-objective entry must not stop the plan, but it must
                # not be silently swallowed either: a bare `continue` here hid a
                # four-argument ToolCall for a while, and the branch never ran.
                continue

        # 1. Continue an open quest if the observation says one is running.
        for qid, idx in sorted(active.items()):
            if int(qid) not in self.quests:
                continue
            # A quest this session has given up on stays in `quests_active` — the
            # game does not let go of it — but re-planning it every step produces the
            # same call forever. `_skipped` used to filter only *new* quests, so
            # retiring an active one had no effect at all.
            if int(qid) in self._skipped:
                continue
            call = self._plan_quest(int(qid), int(idx), obs, goal)
            if call is not None:
                return call
            # The open quest cannot be planned with what we know (an area objective,
            # a craft with no recipe). Retiring it is honest; guessing a tool for it
            # is how a policy spends a hundred steps doing the wrong thing.
            self._skipped.add(int(qid))

        # 2. Otherwise start the cheapest quest we can actually finish.
        order = self._curriculum(level)
        focused = [q for q in self._focus_order(level) if q in set(order)]
        if focused:
            # Rotate inside the focused set so one long quest cannot monopolise an
            # episode, then fall back to the ordinary order.
            start = self.rng.randrange(len(focused))
            order = focused[start:] + focused[:start] + order
        elif self.curriculum == "random" and order:
            head = order[:300]
            start = self.rng.randrange(len(head))
            order = head[start:] + head[:start] + order[300:]
        seen_ids: set = set()
        for qid in order:
            if qid in seen_ids:
                continue
            seen_ids.add(qid)
            if qid in self._seen_done or qid in self._skipped:
                continue
            if not self._plannable(qid):
                self._skipped.add(qid)
                continue
            if qid not in active_ids:
                # Not active yet: accept it first. A quest is a contract before it
                # is a list of objectives, and a kill-first quest is unstartable
                # otherwise.
                self.current_quest = qid
                return ToolCall("quest.advance", {"quest_id": qid}, goal)
            call = self._plan_quest(qid, int(active[str(qid)] if str(qid) in active else active[qid]), obs, goal)
            if call is not None:
                return call
            self._skipped.add(qid)
        return None

    def skip(self, quest_id: Any) -> None:
        """Retire a quest the agent cannot advance.

        Nothing inside `_plan_quest` can tell "not yet" from "not ever": it reads the
        KB and the observation, and both say the objective is still open. Only the
        loop knows it has tried three times with no change, so only the loop can say
        this.
        """
        try:
            self._skipped.add(int(quest_id))
        except (TypeError, ValueError):
            pass

    def plan_quest(self, quest_id: int, obs: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The whole plan for a quest — used to build the SFT corpus."""
        quest = self.quests.get(quest_id)
        if quest is None:
            return []
        out: List[Dict[str, Any]] = []
        for step_idx, step in enumerate(quest["steps"]):
            for obj in step.get("objectives") or []:
                out.extend(self.plan_objective(quest, step_idx, obj, obs))
        return out
