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

from agent.features import objective_type

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

# Type 6 says "defeat X *in a fight*"; the open-world types do not. Keeping them
# apart is what gives fight.join a job that farm.start cannot do, and a tool that
# cannot be told from another tool is not a decision, it is noise in the label space.
FIGHT_TYPES = {6}
OPEN_KILL_TYPES = KILL_TYPES - FIGHT_TYPES

# A stack limit is the only reason to ever sell something. Without it inv.sell is a
# tool with no state effect, which means no counterfactual can ever prefer it.
DEFAULT_CAPACITY = 40


def _load(name: str) -> Any:
    path = CACHE / name
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


_MAP_INDEX: Optional[Tuple[Dict[int, int], Dict[int, int]]] = None


def map_index() -> Tuple[Dict[int, int], Dict[int, int]]:
    """map id -> (subarea id, area id), built once from the KB.

    This is the difference between a world where the teacher is optimal and one where
    it is not. With travel free, "one objective at a time, re-plan every step" cannot
    be beaten. With travel costing hops, the order of objectives starts to matter: the
    KB puts 8.91 placed objectives per quest across 2.73 subareas, so a planner that
    batches by subarea saves 6.18 travel steps per quest — 69% of the act budget.
    """
    global _MAP_INDEX
    if _MAP_INDEX is None:
        sub_of: Dict[int, int] = {}
        area_of: Dict[int, int] = {}
        for sub in _load("subareas_full.json"):
            try:
                sid = int(sub["id"])
            except (KeyError, TypeError, ValueError):
                continue
            for m in sub.get("mapIds") or []:
                try:
                    mid = int(m)
                except (TypeError, ValueError):
                    continue
                sub_of[mid] = sid
                area_of[mid] = sub.get("areaId")
        _MAP_INDEX = (sub_of, area_of)
    return _MAP_INDEX


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
                 obs_lag: float = 0.20, obs_drop: float = 0.10,
                 travel_cost: bool = False) -> None:
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
        self.capacity = DEFAULT_CAPACITY
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
        # Set by a read action: the next observation is served from the client, not
        # from the stale cache. This is the whole value of reading.
        self._force_fresh = False
        self.last_obs_kind = "fresh"
        # Off by default so every number quoted before this existed stays comparable,
        # the same convention `noise` follows. A world with free travel is the one
        # that made the teacher unbeatable, so any claim about planning has to be
        # quoted with this on.
        self.travel_cost = travel_cost

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
        twin._force_fresh = self._force_fresh
        twin.last_obs_kind = self.last_obs_kind
        twin.capacity = self.capacity
        twin.travel_cost = self.travel_cost
        return twin

    def travel_hops(self, here: Optional[int], there: Optional[int]) -> int:
        """How many steps it takes to get from one map to another.

        Coarse on purpose: same map 0, same subarea 1, same area 2, anything else 3
        (a zaap hop). The KB has 15,400 maps in 567 subareas across 67 areas, so this
        is a real geography and not a guess — but a hop count is not a path. What it
        has to be is *monotone in distance*, because that is the property the teacher
        lacks: under free travel, the cost of any plan is the number of actions in it.
        """
        if here is None or there is None:
            return 1
        here, there = int(here), int(there)
        if here == there:
            return 0
        sub_of, area_of = map_index()
        a, b = sub_of.get(here), sub_of.get(there)
        if a is not None and a == b:
            return 1
        aa, ab = area_of.get(here), area_of.get(there)
        if aa is not None and aa == ab:
            return 2
        return 3

    # -- observations ------------------------------------------------------

    def observe(self) -> Dict[str, Any]:
        obs = self._observe_now()
        if not self.noise:
            self.last_obs_kind = "fresh"
            return obs
        # A read was just executed: the client answers with the truth. This is the
        # only thing a read action buys, and without it every read tool is provably
        # worthless and the corpus teaches "never look".
        if self._force_fresh:
            self._force_fresh = False
            self._previous_obs = dict(obs)
            self.last_obs_kind = "fresh"
            return obs
        # Stale: the client re-serves the previous snapshot while an animation plays.
        if self._previous_obs is not None and self.rng.random() < self.obs_lag:
            self.last_obs_kind = "stale"
            return dict(self._previous_obs)
        # Partial: a field the UI has not refreshed yet reads as absent.
        if self.rng.random() < self.obs_drop:
            obs = dict(obs)
            obs["inventory"] = {}
            self.last_obs_kind = "partial"
        else:
            self.last_obs_kind = "fresh"
        self._previous_obs = dict(obs)
        return obs

    def _observe_now(self) -> Dict[str, Any]:
        cur = self.current_objective()
        tracker = None
        if cur is not None:
            q, step_idx, obj_idx, obj = cur
            tracker = {"quest_id": q.id, "step": step_idx, "objective_id": obj.get("id"),
                       "type_id": objective_type(obj),
                       "map_id": self._required_map(obj)}
        return {
            "level": self.level,
            "map_id": self.map_id,
            "kamas": self.kamas,
            "inventory": dict(self.inventory),
            "inventory_capacity": self.capacity,
            # The whole set, not the last 20. Truncating it here was a silent
            # correctness bug: the planner treats this list as "which quests are
            # finished", so after twenty quests the twenty-first became invisible,
            # got picked again, and the loop spent the rest of its budget re-accepting
            # a finished quest. Every score in the simulator was capped by it.
            "quests_done": sorted(self.quests_done),
            "quests_done_count": len(self.quests_done),
            "quests_active": dict(self.active),
            # The quest tracker, exactly as the client shows it. Without this the
            # policy has to guess which objective inside the current step is open,
            # and a guess that is wrong is indistinguishable from a bad tool.
            "current_objective": tracker,
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

    def _required_map(self, obj: Dict[str, Any]) -> Optional[int]:
        """The map this objective has to be done on, when the KB names one.

        ``map_id: 0`` is the KB's "no map", not map zero. Treating it as a real
        destination produced a hundred failed `nav.goto 0` calls in a row.
        """
        m = obj.get("map_id")
        if m is None and obj.get("type_id") in MAP_TYPES:
            m = _params(obj).get("parameter0")
        try:
            m = int(m) if m is not None else None
        except (TypeError, ValueError):
            return None
        return m if m else None

    def _wrong_map(self, obj: Dict[str, Any]) -> Optional[str]:
        need = self._required_map(obj)
        if need is not None and self.map_id != need:
            return "wrong map: need %d, on %s" % (need, self.map_id)
        return None

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
        target = args.get("map_id")
        if target is None:
            target = args.get("mapId")
        if target is None and "x" in args and "y" in args:
            target = int(args["x"]) * 1000 + int(args["y"])   # synthetic id
        if not target:
            return False, "no destination"
        hops = self.travel_hops(self.map_id, int(target))
        self.map_id = int(target)
        # Arriving is a state change the client can see immediately.
        self._force_fresh = True
        if self.travel_cost:
            # The call already consumed one step in step(); charge the rest.
            self.steps += max(0, hops - 1)
        current = self.current_objective()
        if current is not None:
            quest, step_idx, obj_idx, obj = current
            if obj.get("type_id") in MAP_TYPES and self._required_map(obj) == int(target):
                # "Find the map: X" is satisfied by standing on it. Without this the
                # objective is uncompletable and the planner travels in circles.
                self._complete_objective(quest, step_idx, obj_idx)
                return True, "arrived; map objective done (%d hops)" % hops
        return True, "moved (%d hops)" % hops

    def _tool_nav_walk(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return self._tool_nav_goto(args)

    def _tool_nav_follow(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "followed"

    def _tool_quest_list(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        # A read: it returns the truth and clears the stale cache.
        self._force_fresh = True
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
        # A tool may only complete the kind of objective it is for. Before this
        # guard, quest.advance completed whatever was open — a kill, a delivery,
        # anything — so every tool worked and the tool choice was not a decision.
        if obj.get("type_id") not in (TALK_TYPES | DELIVER_TYPES):
            return False, "quest.advance cannot complete objective type %s" % obj.get("type_id")
        wrong = self._wrong_map(obj)
        if wrong:
            return False, wrong
        if npc is not None and obj.get("type_id") in TALK_TYPES:
            expected = _params(obj).get("parameter0")
            if expected is not None and int(npc) != int(expected):
                return False, "wrong npc: wanted %s" % expected
        if obj.get("type_id") in DELIVER_TYPES:
            item = self._deliver_item(obj)
            if item is not None and not self.inventory.get(item):
                return False, "item %s not carried" % item
            if item is not None:
                self.inventory[item] -= 1
                if self.inventory[item] <= 0:
                    self.inventory.pop(item, None)
        self._complete_objective(q, step_idx, obj_idx)
        return True, "objective done"

    @staticmethod
    def _deliver_item(obj: Dict[str, Any]) -> Optional[int]:
        p = _params(obj)
        t = obj.get("type_id")
        item = p.get("parameter1") if t == 3 else p.get("parameter0")
        try:
            return int(item) if item is not None else None
        except (TypeError, ValueError):
            return None

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
            if len(self.inventory) >= self.capacity and item not in self.inventory:
                return False, "inventory full"
            if self.map_id is None:
                return False, "nowhere: no map loaded"
            self.inventory[item] = self.inventory.get(item, 0) + 1
            return True, "gathered item %d" % item
        if t in FIGHT_TYPES:
            # A quest that says "in a fight" cannot be finished by farming in the
            # open world. Keeping the two apart is what makes fight.join a decision.
            return False, "this objective needs a fight (fight.join)"
        if t not in KILL_TYPES:
            return False, "current objective is not a kill"
        wrong = self._wrong_map(obj)
        if wrong:
            return False, wrong
        self._complete_objective(quest, step_idx, obj_idx)
        return True, "killed"

    def _tool_farm_stop(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "stopped"

    def _tool_inv_list(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        self._force_fresh = True
        return True, "inventory: %d stacks" % len(self.inventory)

    def _tool_inv_sell(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        if not self.inventory:
            return False, "nothing to sell"
        self.kamas += 100 * max(1, len(self.inventory))
        self.inventory.clear()
        self._force_fresh = True
        return True, "sold"

    def _tool_hdv_scan(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "scanned"

    def _tool_hdv_list(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "listed"

    def _tool_state_snapshot(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        self._force_fresh = True
        return True, "snapshot"

    def _tool_chat_say(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "said"

    def _tool_fight_join(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        current = self.current_objective()
        if current is None:
            return False, "no fight to join"
        quest, step_idx, obj_idx, obj = current
        if obj.get("type_id") not in FIGHT_TYPES:
            return False, "current objective is not a fight"
        wrong = self._wrong_map(obj)
        if wrong:
            return False, wrong
        self._complete_objective(quest, step_idx, obj_idx)
        return True, "won the fight"

    def _tool_fight_pass(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "passed"

    def _tool_safe_stop(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "stopped"

    def _tool_knowledge_search(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        # Deliberately does NOT refresh the client observation: it reads the local
        # cache, which cannot tell you where you are standing.
        return True, "searched"

    # -- scoring -----------------------------------------------------------

    def stall(self, limit: int = 12) -> Dict[str, Any]:
        """Did the policy loop?

        A score that hides a loop is not a score. The planner used to spend 450 of
        500 steps re-accepting one finished quest and the report still said "21
        quests"; the number was true and the story it told was false.
        """
        best, run, at = 0, 0, 0
        tool = ""
        for i in range(1, len(self.history)):
            prev, cur = self.history[i - 1], self.history[i]
            if prev["tool"] == cur["tool"] and prev.get("args") == cur.get("args"):
                run += 1
            else:
                run = 1
            if run > best:
                best, at, tool = run, i, cur["tool"]
        return {"max_repeat": best, "repeat_tool": tool,
                "repeat_at_step": at, "stalled": best >= limit}

    def score(self) -> Dict[str, Any]:
        stall = self.stall()
        return {
            "steps": self.steps,
            "objectives": self.objectives_completed,
            "quests": self.quests_completed,
            "quests_per_1000_steps": round(1000.0 * self.quests_completed / max(1, self.steps), 2),
            "objectives_per_100_steps": round(100.0 * self.objectives_completed / max(1, self.steps), 2),
            **stall,
        }
