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

from agent.features import (DELIVER_TYPES, FIGHT_TYPES, KILL_TYPES,  # noqa: F401
                            MAP_TYPES, OPEN_KILL_TYPES, TALK_TYPES,
                            _params, objective_type, required_map,
                            travel_hops)

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE = REPO_ROOT / "data" / "cache"

# A stack limit is the only reason to ever sell something. Without it inv.sell is a
# tool with no state effect, which means no counterfactual can ever prefer it.
DEFAULT_CAPACITY = 40


def _load(name: str) -> Any:
    path = CACHE / name
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


_MAP_GRAPH: Optional[Dict[int, Dict[str, Any]]] = None


def map_graph() -> Dict[int, Dict[str, Any]]:
    """The simulator's routing graph, keyed by map id.

    The live agent reads the Touch map index. The toolkit ships a superset that
    also contains every map id the DofusDB knowledge base names, because a corpus
    should not lose 91 discovery quests to a version mismatch. The synthetic edges
    are a property of the simulator, never of the live router.
    """
    global _MAP_GRAPH
    if _MAP_GRAPH is None:
        path = REPO_ROOT / "data" / "maps" / "_index.json"
        graph: Dict[int, Dict[str, Any]] = {}
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                for key, value in raw.items():
                    try:
                        graph[int(key)] = value
                    except (TypeError, ValueError):
                        continue
            except (OSError, ValueError):
                graph = {}
        _MAP_GRAPH = graph
    return _MAP_GRAPH


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
        # A player is always standing somewhere. Starting at `None` meant every
        # gather and delivery failed with "nowhere: no map loaded" until some
        # objective happened to name a map — and under the diverse curriculum, whose
        # first objectives are often deliveries with no map, that never happened. The
        # agent was not failing to plan; it was standing in the void. Start on the
        # lowest-numbered quest's start map, which is a real map from the KB.
        for _q in sorted(self.quests.values(), key=lambda x: x.id):
            if _q.start_map:
                self.map_id = int(_q.start_map)
                break
        # The places the character has stood on. A generic "discover a map"
        # objective is satisfied by the first crossing to a map not in this set;
        # named objectives are still matched by id.
        self.visited: Set[int] = {int(self.map_id)} if self.map_id else set()
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
        twin.visited = set(self.visited)
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
        """Steps to get from one map to another. One implementation, in features."""
        return travel_hops(here, there)

    def exits(self) -> List[Dict[str, Any]]:
        """The edges of the current map, in the shape the client reports."""
        if self.map_id is None:
            return []
        entry = map_graph().get(int(self.map_id))
        if not isinstance(entry, dict):
            return []
        neighbours = entry.get("neighbours") or {}
        out: List[Dict[str, Any]] = []
        for direction, key in (("top", "topNeighbourId"),
                               ("bottom", "bottomNeighbourId"),
                               ("left", "leftNeighbourId"),
                               ("right", "rightNeighbourId")):
            try:
                dest = int(neighbours.get(key))
            except (TypeError, ValueError):
                continue
            if dest > 0 and dest != self.map_id:
                out.append({"direction": direction, "map_id": dest})
        return out

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

    def state(self) -> Dict[str, Any]:
        """The truthful post-action snapshot, as a directive reply carries it.

        Decisions use `observe()`, which can be stale; feedback must not. Reporting
        the pre-action observation made a successful quest start look like a server
        that never activated it, and the planner retired live quests for it.
        """
        return self._observe_now()

    def _observe_now(self) -> Dict[str, Any]:
        cur = self.current_objective()
        tracker = None
        step_objs: List[Dict[str, Any]] = []
        if cur is not None:
            q, step_idx, obj_idx, obj = cur
            tracker = {"quest_id": q.id, "step": step_idx, "objective_id": obj.get("id"),
                       "type_id": objective_type(obj),
                       "map_id": self._required_map(obj)}
            # Every objective in the open step, with its status. The tracker names one
            # of them; without the whole list a policy can only continue the first,
            # which makes "do the nearby one first" inexpressible — and that order is
            # worth 22% of its travel.
            step = q.steps[step_idx]
            for oi, o in enumerate(step.get("objectives") or []):
                key = (q.id, step["id"], o.get("id"))
                step_objs.append({
                    "id": o.get("id"), "type_id": objective_type(o),
                    "map_id": self._required_map(o),
                    "done": key in self.objectives_done,
                })
        return {
            "level": self.level,
            "map_id": self.map_id,
            # The same nested block the live client returns. Exits are what make a
            # discovery objective plannable: its text names no destination, so the
            # only actionable move is crossing one of these edges.
            "map": {"map_id": self.map_id, "cell": None, "exits": self.exits()},
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
            "step_objectives": step_objs,
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
        return required_map(obj)

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
        direction = args.get("direction")
        if target is None:
            target = args.get("mapId")
        if target is None and direction:
            # A direction is an edge, not a place: resolve it on the map the
            # character is standing on. This is the move the live planner makes
            # when a DiscoverMap objective names no destination.
            picked = next((e for e in self.exits() if e["direction"] == direction), None)
            if picked is None:
                return False, "no %s exit on map %s" % (direction, self.map_id)
            target = picked["map_id"]
        if target is None and "x" in args and "y" in args:
            target = int(args["x"]) * 1000 + int(args["y"])   # synthetic id
        if not target:
            return False, "no destination"
        hops = self.travel_hops(self.map_id, int(target))
        arrived = int(target)
        discovered = arrived not in self.visited
        self.map_id = arrived
        self.visited.add(arrived)
        # Arriving is a state change the client can see immediately.
        self._force_fresh = True
        if self.travel_cost:
            # The call already consumed one step in step(); charge the rest.
            self.steps += max(0, hops - 1)
        found = self._target_in_active("map", arrived, MAP_TYPES)
        if found is not None:
            # "Find the map: X" is satisfied by standing on it. Without this the
            # objective is uncompletable and the planner travels in circles.
            quest, step_idx, obj_idx, _ = found
            self._complete_objective(quest, step_idx, obj_idx)
            return True, "arrived; map objective done (%d hops)" % hops
        if discovered:
            # A discovery objective with no id in the KB is satisfied by entering a
            # map that has not been visited this session.
            for quest in self._active_quests():
                for step_idx, obj_idx, obj in self._open_objectives(quest):
                    if objective_type(obj) in MAP_TYPES and self._required_map(obj) is None:
                        self._complete_objective(quest, step_idx, obj_idx)
                        return True, ("crossed the %s edge; discovered map %d"
                                      % (direction or "?", arrived))
        return True, "moved (%d hops)" % hops

    def _tool_nav_walk(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return self._tool_nav_goto(args)

    def _tool_nav_follow(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "followed"

    def _tool_quest_list(self, args: Dict[str, Any]) -> Tuple[bool, str]:
        # A read: it returns the truth and clears the stale cache.
        self._force_fresh = True
        return True, "listed %d active" % len(self.active)

    def _open_objectives(self, quest: Quest) -> List[Tuple[int, int, Dict[str, Any]]]:
        """The objectives of the quest's current step that are not yet done.

        Only the current step: steps are sequential in the KB and the world advances
        `active[quest]` only when every objective in the step is finished, so there is
        nothing to do in a later step yet.
        """
        idx = self.active.get(quest.id)
        if idx is None or idx >= len(quest.steps):
            return []
        step = quest.steps[idx]
        out: List[Tuple[int, int, Dict[str, Any]]] = []
        for oi, obj in enumerate(step.get("objectives") or []):
            key = (quest.id, step["id"], obj.get("id"))
            if key not in self.objectives_done:
                out.append((idx, oi, obj))
        return out

    @staticmethod
    def _npc_of(obj: Dict[str, Any]) -> Optional[int]:
        p = _params(obj)
        t = objective_type(obj)
        npc = p.get("parameter0") if t in (1, 9, 3) else p.get("parameter1")
        try:
            return int(npc) if npc is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _monster_of(obj: Dict[str, Any]) -> Optional[int]:
        p = _params(obj)
        m = p.get("parameter0")
        try:
            return int(m) if m is not None else None
        except (TypeError, ValueError):
            return None

    def _target(self, quest: Quest, want: str, value: Any,
                types: Set[int]) -> Optional[Tuple[int, int, Dict[str, Any]]]:
        """Which open objective this call is about.

        The agent names the thing it walked to — an NPC, a monster, an item, a map —
        and that names the objective. The world used to apply every call to "the first
        open objective", which meant the policy could not choose an order even when it
        could see one: with seven objectives in a step spread over three subareas, the
        order is worth 22% of travel, and it was not expressible.
        """
        if value is None:
            return None
        for step_idx, obj_idx, obj in self._open_objectives(quest):
            if objective_type(obj) not in types:
                continue
            if want == "npc" and self._npc_of(obj) == int(value):
                return step_idx, obj_idx, obj
            if want == "monster" and self._monster_of(obj) == int(value):
                return step_idx, obj_idx, obj
            if want == "item" and self._deliver_item(obj) == int(value):
                return step_idx, obj_idx, obj
            if want == "map" and self._required_map(obj) == int(value):
                return step_idx, obj_idx, obj
        return None

    def _active_quests(self) -> List[Quest]:
        return [self.quests[qid] for qid in sorted(self.active) if qid in self.quests]

    def _target_in_active(self, want: str, value: Any, types: Set[int]
                          ) -> Optional[Tuple[Quest, int, int, Dict[str, Any]]]:
        """Resolve an explicitly named target against every active quest.

        A player can fight a monster or gather for a delivery while another quest is
        tracked; binding every call to the oldest active quest made the simulator
        reject correct actions for any other quest as "wrong map", which is how a
        benchmark that should measure planning turned into a repeated-failure loop.
        """
        if value is None:
            return None
        for quest in self._active_quests():
            picked = self._target(quest, want, value, types)
            if picked is not None:
                return (quest,) + picked
        return None

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

        picked = self._target(quest, "npc", npc, TALK_TYPES | DELIVER_TYPES)
        if picked is None:
            return False, "no open objective for npc %s" % npc
        step_idx, obj_idx, obj = picked
        # A tool may only complete the kind of objective it is for. Before this
        # guard, quest.advance completed whatever was open — a kill, a delivery,
        # anything — so every tool worked and the tool choice was not a decision.
        wrong = self._wrong_map(obj)
        if wrong:
            return False, wrong
        if objective_type(obj) in DELIVER_TYPES:
            item = self._deliver_item(obj)
            if item is not None and not self.inventory.get(item):
                return False, "item %s not carried" % item
            if item is not None:
                self.inventory[item] -= 1
                if self.inventory[item] <= 0:
                    self.inventory.pop(item, None)
        self._complete_objective(quest, step_idx, obj_idx)
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
        # `resource`, matching the client (`args.resource` starts harvesting) and the
        # contract. This read `item`, which the client never looked at and the validator
        # never accepted, so gathering for a delivery was unreachable in both worlds.
        wanted_resource = args.get("resource")
        if wanted_resource is not None:
            found = self._target_in_active("item", wanted_resource, DELIVER_TYPES)
            if found is None:
                return False, "no open delivery wants item %s" % wanted_resource
            # Gathering for a delivery is the same kind of step as a kill: one
            # action, one step of progress, no objective completed yet.
            item = int(wanted_resource)
            if len(self.inventory) >= self.capacity and item not in self.inventory:
                return False, "inventory full"
            if self.map_id is None:
                return False, "nowhere: no map loaded"
            self.inventory[item] = self.inventory.get(item, 0) + 1
            return True, "gathered item %d" % item

        if args.get("monster") is not None:
            found = self._target_in_active("monster", args["monster"], KILL_TYPES)
            if found is None:
                return False, "no open objective for monster %s" % args["monster"]
            quest, step_idx, obj_idx, obj = found
            if objective_type(obj) in FIGHT_TYPES:
                # A quest that says "in a fight" cannot be finished by farming in the
                # open world. Keeping the two apart is what makes fight.join a decision.
                return False, "this objective needs a fight (fight.join)"
            wrong = self._wrong_map(obj)
            if wrong:
                return False, wrong
            self._complete_objective(quest, step_idx, obj_idx)
            return True, "killed"

        # No argument: fall back to the first open kill, which is what an older caller
        # (and the app's one-shot "farm" verb) means.
        current = self.current_objective()
        if current is None:
            return False, "nothing to kill for"
        quest = current[0]
        for step_idx, obj_idx, obj in self._open_objectives(quest):
            if objective_type(obj) in OPEN_KILL_TYPES:
                wrong = self._wrong_map(obj)
                if wrong:
                    return False, wrong
                self._complete_objective(quest, step_idx, obj_idx)
                return True, "killed"
        return False, "current objective is not a kill"

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
        found = None
        if args.get("monster") is not None:
            found = self._target_in_active("monster", args["monster"], FIGHT_TYPES)
        if found is None:
            for quest in self._active_quests():
                for step_idx, obj_idx, obj in self._open_objectives(quest):
                    if objective_type(obj) in FIGHT_TYPES:
                        found = (quest, step_idx, obj_idx, obj)
                        break
                if found is not None:
                    break
        if found is None:
            return False, "current objective is not a fight"
        quest, step_idx, obj_idx, obj = found
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
