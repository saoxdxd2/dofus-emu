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
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE = REPO_ROOT / "data" / "cache"

TALK_TYPES = {1, 9}
MAP_TYPES = {4}
AREA_TYPES = {5}
KILL_TYPES = {0, 6, 14, 16}
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


class QuestPlanner:
    """A policy that plans over the quest graph."""

    def __init__(self, level_hint: int = 60, max_quest_level: int = 200,
                 store: Any = None) -> None:
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

        if t in MAP_TYPES:
            target = p.get("parameter0") or obj.get("map_id")
            if target:
                return [{"tool": "nav.goto", "args": {"map_id": int(target)},
                         "why": "objective %d: find the map" % t}]
            return []

        if t in TALK_TYPES:
            npc = p.get("parameter0")
            # The KB has no NPC -> map table, only a per-quest start position. Emitting
            # a nav.goto first made the planner re-plan the same quest forever: after
            # travelling, the observation still had no active quest, so it chose the
            # same quest and travelled again. Talk directly; travel belongs to the
            # objective types that name a map.
            if npc is None:
                return []
            calls = []
            # If the agent has discovered where this NPC is, travel first. The KB
            # has no npc -> map table; the store fills that gap at runtime.
            target = None
            if self.store is not None:
                try:
                    target = self.store.get("npc_map", "npc:%d" % int(npc))
                except Exception:  # noqa: BLE001
                    target = None
            if target and here != target:
                calls.append({"tool": "nav.goto", "args": {"map_id": int(target)},
                              "why": "travelled to npc %d (learned fact)" % int(npc)})
            calls.append({"tool": "quest.advance",
                          "args": {"quest_id": int(quest["id"]), "npc_id": int(npc)},
                          "why": "objective %d: talk" % t})
            return calls

        if t in KILL_TYPES:
            monster = p.get("parameter0")
            need_map = obj.get("map_id") if t == 16 else None
            calls = []
            if need_map and here != int(need_map):
                calls.append({"tool": "nav.goto", "args": {"map_id": int(need_map)},
                              "why": "objective %d: fight on that map" % t})
            calls.append({"tool": "farm.start",
                          "args": {"monster": monster} if monster else {},
                          "why": "objective %d: kill" % t})
            return calls

        if t in DELIVER_TYPES:
            item = p.get("parameter1") if t == 3 else p.get("parameter0")
            npc = p.get("parameter0") if t == 3 else p.get("parameter1")
            calls = []
            # Inventories arrive with either int or str keys depending on who built
            # the observation; a str-only lookup made the planner gather the same
            # item forever because it never believed it had one.
            inv = obs.get("inventory") or {}
            have = bool(item is not None and (inv.get(item) or inv.get(str(item))))
            if item is not None and not have:
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
            item = p.get("parameter0")
            return [{"tool": "inv.list", "args": {},
                     "why": "objective 17: crafting needs materials (unsupported in sim)"}]

        # Area objectives need the area -> map graph, which the KB does not expose
        # here. Refreshing the state is honest: it costs a step and keeps the loop
        # from inventing a destination.
        return []

    # -- policy interface --------------------------------------------------

    def choose(self, goal: str, observation: Optional[Dict[str, Any]],
               tools: Optional[Sequence[str]] = None) -> Optional[Any]:
        from agent.runtime import ToolCall

        obs = observation or {}
        level = int(obs.get("level") or self.level_hint)
        active = obs.get("quests_active") or {}

        # 1. Continue an open quest if the observation says one is running.
        quest_id = None
        step_idx = 0
        for qid, idx in sorted(active.items()):
            if int(qid) in self.quests:
                quest_id, step_idx = int(qid), int(idx)
                break

        # 2. Otherwise start the cheapest quest we can.
        if quest_id is None:
            for qid in self._curriculum(level):
                if qid in (obs.get("quests_done") or []):
                    continue
                quest_id, step_idx = qid, 0
                break

        if quest_id is None:
            return None

        quest = self.quests[quest_id]
        if step_idx >= len(quest["steps"]):
            return None

        # Not active yet: accept it first. A quest is a contract before it is a list
        # of objectives, and a kill-first quest is unstartable otherwise.
        if quest_id not in [int(k) for k in active.keys()]:
            self.current_quest = quest_id
            return ToolCall("quest.advance", {"quest_id": quest_id}, goal)
        step = quest["steps"][step_idx]
        for obj in step.get("objectives") or []:
            calls = self.plan_objective(quest, step_idx, obj, obs)
            if calls:
                first = calls[0]
                self.current_quest = quest_id
                return ToolCall(first["tool"], dict(first["args"]), goal)
        return None

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
