"""Deliberate recovery after the fast policy has failed three times.

The policy is a reflex: it chooses from the current observation with the same
weights and the same context. When that reflex repeats the same failing call,
more inference does not help. This layer takes over with the one thing the
reflex lacks: memory of what was already tried, and a concrete list of
alternatives derived from the current board, the quest step and the tutorial
gate.

It is deliberately rule-based and bounded. Every proposal is different from the
calls in the recent history; when the alternatives are exhausted it returns
``None`` and the caller falls back to the scheduler or stops. A learned/LLM
reasoner can replace the candidate generator later; the interface and the
"do not repeat the failed call" invariant stay.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from agent.awareness import scene_key
from agent.dialog_policy import rank_dialog
from agent.features import declares_monster_target
from agent.observe import flatten
from agent.planner import QuestPlanner


@dataclass
class Thought:
    tool: str
    args: Dict[str, Any]
    why: str

    def signature(self) -> str:
        return json.dumps([self.tool, self.args], sort_keys=True,
                          ensure_ascii=False, default=str)


def _params(obj: Dict[str, Any]) -> Dict[str, Any]:
    raw = obj.get("params") if isinstance(obj, dict) else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cell_of(obs: Dict[str, Any]) -> Optional[int]:
    for value in (obs.get("cell"),
                  (obs.get("position") or {}).get("cell")
                  if isinstance(obs.get("position"), dict) else None,
                  (obs.get("map") or {}).get("cell")
                  if isinstance(obs.get("map"), dict) else None):
        cell = _as_int(value)
        if cell is not None:
            return cell
    return None


def _map_id_of(obs: Dict[str, Any]) -> Optional[int]:
    for value in (obs.get("map_id"),
                  (obs.get("position") or {}).get("map_id")
                  if isinstance(obs.get("position"), dict) else None,
                  (obs.get("map") or {}).get("map_id")
                  if isinstance(obs.get("map"), dict) else None):
        map_id = _as_int(value)
        if map_id is not None:
            return map_id
    return None


def _quest_id(obs: Dict[str, Any], obj: Dict[str, Any]) -> Optional[int]:
    tutorial = obs.get("tutorial") if isinstance(obs.get("tutorial"), dict) else {}
    for raw in (obj.get("quest_id"), tutorial.get("quest_id")):
        quest_id = _as_int(raw)
        if quest_id:
            return quest_id
    active = obs.get("quests_active") or {}
    if isinstance(active, dict) and len(active) == 1:
        return _as_int(next(iter(active)))
    return None


def _exits(obs: Dict[str, Any], avoid: Optional[int] = None) -> List[str]:
    """Walkable exits, minus the map just left when `avoid` names it.

    Thinking runs on replan, so an exit that undoes the last walk is how a step
    turns into a two-map loop: live 2026-10-29 the character walked a tutorial
    step back and forth between 145490435 and 145490947 for twenty steps, each
    walk reported `advanced`. Going back is the planner's decision (it knows the
    objective's map); a *repair* must not re-run the walk that just happened.
    """
    block = obs.get("map") if isinstance(obs.get("map"), dict) else {}
    here = _map_id_of(obs)
    out: List[str] = []
    for entry in block.get("exits") or obs.get("exits") or []:
        if not isinstance(entry, dict) or entry.get("blocked"):
            continue
        dest = _as_int(entry.get("map_id"))
        if here is not None and dest == here:
            continue
        if avoid is not None and dest is not None and dest == avoid:
            continue
        direction = entry.get("direction")
        if direction and direction not in out:
            out.append(str(direction))
    return out


def _came_from(obs: Dict[str, Any]) -> Optional[int]:
    nav = obs.get("last_nav") if isinstance(obs.get("last_nav"), dict) else {}
    return _as_int(nav.get("from_map"))


def _npcs(obs: Dict[str, Any]) -> List[int]:
    here = _cell_of(obs)
    entries = QuestPlanner._map_npcs(obs)
    entries.sort(key=lambda entry: QuestPlanner._cell_distance(
        here, entry.get("cell")))
    return [int(entry["npc_id"]) for entry in entries]


class ThinkingLayer:
    """Generate untried, grounded alternatives after repeated failure."""

    def __init__(self, max_scenes: int = 64) -> None:
        self.max_scenes = max_scenes
        self._attempted: Dict[Tuple[str, str], set] = {}

    def _tried(self, goal: str, obs: Dict[str, Any],
               steps: Sequence[Any]) -> Tuple[set, str]:
        scene = scene_key(obs)
        key = (str(goal), scene)
        tried = self._attempted.setdefault(key, set())
        for step in list(steps)[-8:]:
            tool = str(getattr(step, "tool", "") or "")
            args = getattr(step, "args", {}) or {}
            tried.add(Thought(tool, dict(args), "").signature())
        if len(self._attempted) > self.max_scenes:
            for stale in list(self._attempted)[:-self.max_scenes]:
                self._attempted.pop(stale, None)
        return tried, scene

    def propose(self, goal: str, observation: Optional[Dict[str, Any]],
                steps: Sequence[Any] = (), decision: Any = None
                ) -> Optional[Thought]:
        obs = flatten(observation)
        tried, _scene = self._tried(goal, obs, steps)
        diagnosis = self._diagnose(obs, steps, decision)
        for thought in self._candidates(obs, diagnosis):
            if thought.signature() in tried:
                continue
            tried.add(thought.signature())
            return thought
        return None

    # -- diagnosis -----------------------------------------------------------

    def _diagnose(self, obs: Dict[str, Any], steps: Sequence[Any],
                  decision: Any) -> str:
        obj = obs.get("current_objective")
        if not isinstance(obj, dict):
            obj = {}
        type_id = _as_int(obj.get("type_id"))
        tutorial = obs.get("tutorial") if isinstance(obs.get("tutorial"), dict) else {}
        if tutorial.get("active"):
            if tutorial.get("unblocked_npc_id"):
                return "the scripted step wants one specific NPC"
            if tutorial.get("unblocked_cells"):
                return "the scripted step wants a specific cell"
        if type_id == 6:
            params = _params(obj)
            visible = QuestPlanner._visible_target(obs, params.get("parameter0"))
            if visible is False and _npcs(obs):
                return "fight objective, no group on this map, NPCs are present"
            if visible is False:
                return "fight objective with no group and no NPC on this map"
        last = steps[-1] if steps else None
        last_tool = str(getattr(last, "tool", "") or "")
        cause = str(getattr(decision, "cause", "") or "")
        if last_tool == "nav.goto":
            return "navigation did not change the world (%s)" % (cause or "noop")
        if last_tool == "quest.advance":
            return "the interaction did not advance the quest (%s)" % (cause or "refused")
        if last_tool == "fight.join":
            return "the fight could not start from here (%s)" % (cause or "refused")
        return "repeated failure (%s)" % (cause or "unknown")

    # -- candidates ----------------------------------------------------------

    def _candidates(self, obs: Dict[str, Any], diagnosis: str
                    ) -> Iterable[Thought]:
        tutorial = obs.get("tutorial") if isinstance(obs.get("tutorial"), dict) else {}
        obj = obs.get("current_objective")
        if not isinstance(obj, dict):
            obj = {}
        quest_id = _quest_id(obs, obj)

        # The scripted gate is the strongest evidence in the state.
        if tutorial.get("active"):
            npc = _as_int(tutorial.get("unblocked_npc_id"))
            if npc and quest_id:
                yield Thought("quest.advance", {"quest_id": quest_id, "npc_id": npc},
                              "the tutorial names this NPC; talk to it")
            cells = tutorial.get("unblocked_cells") or []
            here = _cell_of(obs)
            for raw in cells:
                cell = _as_int(raw)
                if cell is not None and cell != here:
                    yield Thought("nav.goto", {"cell": cell},
                                  "the tutorial allows this cell; step onto it")

        # An open dialog is a question waiting for an answer.
        if obs.get("in_dialog") or (isinstance(obs.get("dialog"), dict)
                                    and (obs.get("dialog") or {}).get("open")):
            decision = rank_dialog(obs)
            reply = (decision or {}).get("reply") or {}
            dialog_quest = _as_int((decision or {}).get("quest_id")) or quest_id
            if dialog_quest and reply:
                yield Thought("quest.advance", {
                    "quest_id": dialog_quest, "reply": reply.get("index"),
                    "reply_text": reply.get("text")},
                    "the dialog is open; answer the best-ranked reply")

        type_id = _as_int(obj.get("type_id"))
        params = _params(obj)
        monster = params.get("parameter0")

        if type_id == 6:
            visible = QuestPlanner._visible_target(obs, monster)
            if visible:
                yield Thought("fight.join",
                              {"monster": monster} if monster is not None else {},
                              "the requested group is visible; join it")
            if quest_id:
                for npc in _npcs(obs):
                    yield Thought("quest.advance",
                                  {"quest_id": quest_id, "npc_id": npc},
                                  "no group; ask NPC %d to start the fight" % npc)
            need = _as_int(obj.get("map_id"))
            if need and need != _map_id_of(obs):
                yield Thought("nav.goto", {"map_id": need},
                              "the objective names a different map")
            for direction in _exits(obs, avoid=_came_from(obs)):
                yield Thought("nav.goto", {"direction": direction},
                              "try the %s exit" % direction)
            yield Thought("state.snapshot", {},
                          "wait for a group or a state change")
            return

        if type_id in (1, 9):          # talk objectives
            npc = _as_int(params.get("parameter0"))
            if npc and quest_id:
                yield Thought("quest.advance",
                              {"quest_id": quest_id, "npc_id": npc},
                              "talk to the objective's NPC")
            if quest_id:
                for other in _npcs(obs):
                    if other != npc:
                        yield Thought("quest.advance",
                                      {"quest_id": quest_id, "npc_id": other},
                                      "try another map NPC for this step")
            for direction in _exits(obs, avoid=_came_from(obs)):
                yield Thought("nav.goto", {"direction": direction},
                              "the NPC may be on the next map")
            yield Thought("state.snapshot", {}, "wait for the NPC or a state change")
            return

        if type_id == 4:               # discover a map
            for direction in _exits(obs, avoid=_came_from(obs)):
                yield Thought("nav.goto", {"direction": direction},
                              "discover by taking the %s exit" % direction)
            yield Thought("state.snapshot", {}, "wait for a map change")
            return

        if type_id in (0, 14, 16):     # kill/farm objectives
            if declares_monster_target(obj) is False:
                # The client's own parameter table says this objective carries
                # text, not a creature. Live 2026-10-29: a free-form step's
                # parameter0 (715354, a text id) became `fight.join {monster:
                # 715354}`, then `farm.start`, then a walk between two maps on
                # every replan -- three nonsensical repairs for a step that only
                # needed its equip half to finish.
                if quest_id:
                    for npc in _npcs(obs):
                        yield Thought("quest.advance",
                                      {"quest_id": quest_id, "npc_id": npc},
                                      "free-form step; ask a map NPC")
                need = _as_int(obj.get("map_id"))
                if need and need != _map_id_of(obs):
                    yield Thought("nav.goto", {"map_id": need},
                                  "the objective names a different map")
                yield Thought("state.snapshot", {},
                              "wait for the free-form step's own event")
                return
            if monster is not None:
                yield Thought("fight.join", {"monster": monster},
                              "join the target's group")
                yield Thought("farm.start", {"monster": monster},
                              "farm the target if no group is joinable")
            for direction in _exits(obs, avoid=_came_from(obs)):
                yield Thought("nav.goto", {"direction": direction},
                              "search the next map for the target")
            yield Thought("state.snapshot", {}, "wait for the target to spawn")
            return

        # Unknown objective: the map's NPCs and exits are still legal probes.
        if quest_id:
            for npc in _npcs(obs):
                yield Thought("quest.advance",
                              {"quest_id": quest_id, "npc_id": npc},
                              "ask a map NPC about the step")
        for direction in _exits(obs, avoid=_came_from(obs)):
            yield Thought("nav.goto", {"direction": direction},
                          "explore the %s exit" % direction)
        yield Thought("state.snapshot", {}, "refresh the observation")
