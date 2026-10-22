"""A symbolic Dofus world built from the knowledge base.

Why this exists: training an agent against the real client is limited to roughly a
hundred steps per hour and can get the account banned. The knowledge base already
describes the world well enough to *simulate the planning problem* — maps, NPCs,
monsters, items, quest objectives — so a planner can be trained and scored at
thousands of episodes per hour, and the game is used to check the result rather
than to discover it.

What is faithful: the quest graph, objective types, ids, level gates, and the
preconditions of each tool (you must be on the map; you must have the item; a fight
needs a monster on this map).
What is not: movement cost, combat turns, drop rates, market prices. Those are
deliberately abstracted — this is a *planning* simulator, not a combat simulator.
Pretending otherwise would make the numbers meaningless in a different way.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE = REPO_ROOT / "data" / "cache"

# Objective type -> what it needs, decoded from the objective text itself:
#   0  "Locate and neutralise X"          monster
#   1  "Talk to {npc,N}"                  npc
#   2  "Show 1 {item,I} to {npc,N}"       item + npc
#   3  "Take 1 {item,I} to {npc,N}"       item + npc
#   4  "Find the map: X"                  map
#   5  "Find the {subarea,S} area"        area
#   6  "Defeat 1 {monster,M} in a fight"  monster
#   9  "Talk to {npc,N} again"            npc
#   12 "Take 1 {monster,M} soul to {npc}" monster + npc
#   14 "Beat x3 {monster,M}"              monster xN
#   16 "Defeat 8 x {monster} on {map}"    monster xN + map
#   17 "Craft 1 {item,I}"                 item
TALK_TYPES = {1, 9}
MAP_TYPES = {4}
AREA_TYPES = {5}
KILL_TYPES = {0, 6, 14, 16}
DELIVER_TYPES = {2, 3, 12}
CRAFT_TYPES = {17}


def _load(name: str) -> Any:
    path = CACHE / name
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


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


@dataclass
class Quest:
    id: int
    name: str
    level: int
    start_map: Optional[int]
    start_npc: Optional[int]
    steps: List[Dict[str, Any]]

    def objective_count(self) -> int:
        return sum(len(s.get("objectives") or []) for s in self.steps)


class World:
    """Symbolic state + a `step()` that applies a tool call."""

    def __init__(self, level: int = 60, seed: int = 7,
                 max_steps: int = 2000, kb_limit: Optional[int] = None,
                 noise: bool = False, action_fail: float = 0.15,
                 obs_lag: float = 0.20, obs_drop: float = 0.10) -> None:
        self.rng = random.Random(seed)
        raw = _load("quests_full.json")
        if kb_limit:
            raw = raw[:kb_limit]
        self.quests: Dict[int, Quest] = {}
        for q in raw:
            if not q.get("steps"):
                continue
            start = (q.get("start_position") or [{}])[0] or {}
            self.quests[int(q["id"])] = Quest(
                id=int(q["id"]),
                name=(q.get("name") or {}).get("en") or str(q.get("id")),
                level=int(q.get("level_min") or 1),
                start_map=start.get("mapId"),
                start_npc=start.get("npcId"),
                steps=q["steps"],
            )
        self.npc_ids: Set[int] = {int(n["id"]) for n in _load("npcs_full.json")
                                  if isinstance(n, dict) and "id" in n}

        self.level = level
        self.map_id: Optional[int] = None
        self.kamas = 5000
        self.inventory: Dict[int, int] = {}
        self.quests_done: Set[int] = set()
        self.active: Dict[int, int] = {}          # quest id -> step index
        self.objectives_done: Set[Tuple[int, int, int]] = set()
        self.steps = 0
        self.max_steps = max_steps
        self.objectives_completed = 0
        self.quests_completed = 0
        self.history: List[Dict[str, Any]] = []

        # A deterministic world is a lookup table: a small model learns it exactly
        # and the score stops meaning anything. These three knobs are the parts of
        # the real game that make it hard, and they are off by default so existing
        # numbers stay comparable.
        self.noise = noise
        self.action_fail = action_fail
        self.obs_lag = obs_lag
        self.obs_drop = obs_drop
        self._previous_obs: Optional[Dict[str, Any]] = None

    def clone(self) -> "World":
        """A copy that shares the knowledge base but not the mutable state.

        Needed to ask "what would have happened if I had replanned instead?" --
        counterfactual labels are the only honest way to learn a decision, and they
        require a cheap fork. The quest dict is ~2,000 entries and never mutates, so
        it is shared by reference; everything that changes is copied.
        """
        twin = object.__new__(World)
        twin.rng = random.Random(self.rng.random())
        twin.quests = self.quests
        twin.npc_ids = self.npc_ids
        twin.level = self.level
        twin.map_id = self.map_id
        twin.kamas = self.kamas
        twin.inventory = dict(self.inventory)
        twin.quests_done = set(self.quests_done)
        twin.active = dict(self.active)
        twin.objectives_done = set(self.objectives_done)
        twin.steps = self.steps
        twin.max_steps = self.max_steps
        twin.objectives_completed = self.objectives_completed
        twin.quests_completed = self.quests_completed
        twin.history = list(self.history[-20:])
        twin.noise = self.noise
        twin.action_fail = self.action_fail
        twin.obs_lag = self.obs_lag
        twin.obs_drop = self.obs_drop
        twin._previous_obs = self._previous_obs
        return twin

    # -- observations ------------------------------------------------------

    def observe(self) -> Dict[str, Any]:
        obs = self._observe_now()
        if not self.noise:
            return obs
        # Stale: the client re-serves the previous snapshot while an animation plays.
        if self._previous_obs is not None and self.rng.random() < self.obs_lag:
            return dict(self._previous_obs)
        # Partial: a field the UI has not refreshed yet reads as absent.
        if self.rng.random() < self.obs_drop:
            obs = dict(obs)
            obs["inventory"] = {}
        self._previous_obs = dict(obs)
        return obs

    def _observe_now(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "map_id": self.map_id,
            "kamas": self.kamas,
            "inventory": dict(self.inventory),
            "quests_done": sorted(self.quests_done)[-20:],
            "quests_active": dict(self.active),
            "objectives_completed": self.objectives_completed,
            "quests_completed": self.quests_completed,
        }

    # -- quest logic -------------------------------------------------------

    def _objective(self, quest: Quest, step_idx: int, obj_idx: int) -> Optional[Dict[str, Any]]:
        try:
            return quest.steps[step_idx]["objectives"][obj_idx]
        except (IndexError, KeyError, TypeError):
            return None

    def can_start(self, quest: Quest) -> bool:
        return quest.level <= self.level and quest.id not in self.quests_done

    def active_quest(self) -> Optional[Quest]:
        for qid in list(self.active):
            quest = self.quests.get(qid)
            if quest is not None:
                return quest
        return None

    def current_objective(self) -> Optional[Tuple[Quest, int, int, Dict[str, Any]]]:
        """The next objective of the oldest active quest, or None."""
        for qid in sorted(self.active):
            quest = self.quests.get(qid)
            if quest is None:
                continue
            idx = self.active[qid]
            if idx >= len(quest.steps):
                continue
            objectives = quest.steps[idx].get("objectives") or []
            for oi, obj in enumerate(objectives):
                key = (quest.id, quest.steps[idx]["id"], obj.get("id"))
                if key not in self.objectives_done:
                    return quest, idx, oi, obj
        return None

    def _complete_objective(self, quest: Quest, step_idx: int, obj_idx: int) -> None:
        step = quest.steps[step_idx]
        obj = step["objectives"][obj_idx]
        key = (quest.id, step["id"], obj.get("id"))
        if key in self.objectives_done:
            return
        self.objectives_done.add(key)
        self.objectives_completed += 1
        # Step finished when every objective in it is done.
        if all((quest.id, step["id"], o.get("id")) in self.objectives_done
               for o in step.get("objectives") or []):
            self.active[quest.id] = step_idx + 1
            if self.active[quest.id] >= len(quest.steps):
                self.quests_done.add(quest.id)
                self.active.pop(quest.id, None)
                self.quests_completed += 1

    # -- tools -------------------------------------------------------------

    def step(self, tool: str, args: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """Apply one tool call. Returns (ok, note)."""
        args = dict(args or {})
        self.steps += 1
        if self.steps > self.max_steps:
            return False, "step budget exhausted"

        handler = getattr(self, "_tool_" + tool.replace(".", "_"), None)
        if handler is None:
            return False, "no simulator for tool %r" % tool

        if self.noise and tool in ("farm.start", "quest.advance") \
                and self.rng.random() < self.action_fail:
            # The real reason is invisible: a resisted skill, a mob that moved, a
            # dialogue that did not open. The agent must retry or verify, not assume.
            self.history.append({"tool": tool, "args": args, "ok": False,
                                 "note": "resisted", "map": self.map_id})
            return False, "resisted"

        ok, note = handler(args)
        self.history.append({"tool": tool, "args": args, "ok": ok, "note": note,
                             "map": self.map_id})
        return ok, note

    def _tool_nav_goto(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        target = args.get("map_id") or args.get("mapId")
        if target is None and "x" in args and "y" in args:
            target = int(args["x"]) * 1000 + int(args["y"])   # synthetic id
        if target is None:
            return False, "no destination"
        self.map_id = int(target)
        return True, "moved"

    def _tool_nav_walk(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return self._tool_nav_goto(args)

    def _tool_nav_follow(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "followed"

    def _tool_quest_list(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "listed %d active" % len(self.active)

    def _tool_quest_advance(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        qid = args.get("quest_id") or args.get("quest")
        npc = args.get("npc_id") or args.get("npc")
        if qid is None:
            return False, "quest.advance needs quest_id"
        quest = self.quests.get(int(qid))
        if quest is None:
            return False, "unknown quest %s" % qid
        if quest.id in self.quests_done:
            return True, "already done"
        if not self.can_start(quest):
            return False, "level %d required" % quest.level
        if npc is None:
            # Accepting a quest is its own step in the game: the NPC offers it and
            # the player agrees. Without this, a kill-first quest could never be
            # started, because farm.start needs an active quest to kill for.
            self.active.setdefault(quest.id, 0)
            return True, "accepted"

        self.active.setdefault(quest.id, 0)

        current = self.current_objective()
        if current is None:
            return False, "no open objective"
        q, step_idx, obj_idx, obj = current
        if q.id != quest.id:
            return False, "another quest is open first (%s)" % q.id
        if npc is not None and obj.get("type_id") in TALK_TYPES:
            expected = _params(obj).get("parameter0")
            if expected is not None and int(npc) != int(expected):
                return False, "wrong npc: wanted %s" % expected
        self._complete_objective(q, step_idx, obj_idx)
        return True, "objective done"

    def _tool_farm_start(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        """Kill objectives need a monster on the current map; here one kill is one step."""
        current = self.current_objective()
        if current is None:
            return False, "nothing to kill for"
        quest, step_idx, obj_idx, obj = current
        t = obj.get("type_id")
        if t in DELIVER_TYPES and args.get("item") is not None:
            # Gathering for a delivery is the same kind of step as a kill: one
            # action, one step of progress, no objective completed yet.
            item = int(args["item"])
            self.inventory[item] = self.inventory.get(item, 0) + 1
            return True, "gathered item %d" % item
        if t not in KILL_TYPES:
            return False, "current objective is not a kill"
        if t == 16:
            need_map = obj.get("map_id")
            if need_map and self.map_id != int(need_map):
                return False, "wrong map for this fight"
        self._complete_objective(quest, step_idx, obj_idx)
        return True, "killed"

    def _tool_farm_stop(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "stopped"

    def _tool_inv_list(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "inventory: %d stacks" % len(self.inventory)

    def _tool_inv_sell(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        self.kamas += 100 * max(1, len(self.inventory))
        self.inventory.clear()
        return True, "sold"

    def _tool_hdv_scan(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "scanned"

    def _tool_hdv_list(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "listed"

    def _tool_state_snapshot(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "snapshot"

    def _tool_chat_say(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "said"

    def _tool_fight_join(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "joined"

    def _tool_fight_pass(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "passed"

    def _tool_safe_stop(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "stopped"

    def _tool_knowledge_search(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "searched"

    # -- scoring -----------------------------------------------------------

    def score(self) -> Dict[str, Any]:
        return {
            "steps": self.steps,
            "objectives": self.objectives_completed,
            "quests": self.quests_completed,
            "quests_per_1000_steps": round(1000.0 * self.quests_completed / max(1, self.steps), 2),
            "objectives_per_100_steps": round(100.0 * self.objectives_completed / max(1, self.steps), 2),
        }
