"""Per-instance sub-goal stack.

A macro objective often has an unmet precondition: the NPC is on another map, the
quest item is not in the bag, the gear is not worn. The flat loop used to return
``None`` for those states (the 28–384-pass waits) or retire the quest. This stack
keeps the parent and pushes a concrete child goal:

    acquire(Item_X, 4)
      ├─ kamas >= price  -> buy(Item_X, max_price)
      ├─ monster drops   -> farm(monster, 1)
      ├─ in inventory    -> equip / deliver
      └─ otherwise       -> navigate

The stack is deliberately tiny and serialisable. It does not decide legality or
invent prices; it only decomposes a goal into the existing 25 tools.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.features import DELIVER_TYPES, FIGHT_TYPES, MAP_TYPES, TALK_TYPES
from agent.observe import flatten

MAX_GOALS = 16
MAX_BYTES = 2048


@dataclass
class SubGoal:
    kind: str
    args: Dict[str, Any] = field(default_factory=dict)
    parent: Optional[str] = None
    attempts: int = 0
    created_at: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "args": self.args,
            "parent": self.parent,
            "attempts": self.attempts,
            "created_at": round(self.created_at, 3),
        }

    @classmethod
    def from_dict(cls, row: Dict[str, Any]) -> "SubGoal":
        return cls(
            kind=str(row.get("kind") or "unknown"),
            args=dict(row.get("args") or {}),
            parent=row.get("parent"),
            attempts=int(row.get("attempts") or 0),
            created_at=float(row.get("created_at") or time.time()),
        )


class PlanStack:
    """A bounded LIFO of concrete child goals for one instance."""

    def __init__(self, goals: Optional[List[Dict[str, Any]]] = None,
                 max_depth: int = MAX_GOALS) -> None:
        self.max_depth = max(1, int(max_depth))
        self._goals: List[SubGoal] = [
            SubGoal.from_dict(row) for row in (goals or []) if isinstance(row, dict)
        ][-self.max_depth:]

    def __len__(self) -> int:
        return len(self._goals)

    def top(self) -> Optional[SubGoal]:
        return self._goals[-1] if self._goals else None

    def push(self, kind: str, args: Optional[Dict[str, Any]] = None,
             parent: Optional[str] = None) -> SubGoal:
        goal = SubGoal(kind=kind, args=dict(args or {}), parent=parent)
        self._goals.append(goal)
        if len(self._goals) > self.max_depth:
            self._goals = self._goals[-self.max_depth:]
        return goal

    def pop(self) -> Optional[SubGoal]:
        return self._goals.pop() if self._goals else None

    def clear(self) -> None:
        self._goals.clear()

    def advance(self, moved: bool = True) -> Optional[SubGoal]:
        """Pop the top when its child goal reports a real world change."""
        if moved and self._goals:
            return self._goals.pop()
        if self._goals:
            self._goals[-1].attempts += 1
        return None

    def as_dict(self) -> Dict[str, Any]:
        return {"max_depth": self.max_depth,
                "goals": [goal.as_dict() for goal in self._goals]}

    def restore(self, payload: Dict[str, Any]) -> None:
        self.max_depth = max(1, int(payload.get("max_depth") or self.max_depth))
        self._goals = [SubGoal.from_dict(row) for row in payload.get("goals") or []
                       if isinstance(row, dict)][-self.max_depth:]

    def size_bytes(self) -> int:
        return len(json.dumps(self.as_dict(), ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8"))


def _npc_on_map(observation: Dict[str, Any], npc_id: Any) -> bool:
    if npc_id is None:
        return False
    try:
        wanted = int(npc_id)
    except (TypeError, ValueError):
        return False
    for row in ((observation.get("map") or {}).get("npcs") or []):
        if not isinstance(row, dict):
            continue
        for key in ("template_id", "npc_id", "actor_id", "contextual_id"):
            try:
                if int(row.get(key)) == wanted:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _direction(observation: Dict[str, Any]) -> Optional[str]:
    for row in ((observation.get("map") or {}).get("exits") or []):
        if isinstance(row, dict) and row.get("direction"):
            return str(row.get("direction"))
    return None


def _first_preset(observation: Dict[str, Any]) -> Optional[int]:
    presets = ((observation.get("inventory") or {}).get("presets") or [])
    for row in presets:
        if isinstance(row, dict) and row.get("preset_id") is not None:
            try:
                return int(row["preset_id"])
            except (TypeError, ValueError):
                return None
    return None


def child_for_objective(objective: Optional[Dict[str, Any]],
                        observation: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A concrete child goal for the first unmet precondition, or None.

    This is deliberately conservative: it acts only on fields the live page
    exposes. It never invents an NPC, a map, an item id or a price.
    """
    if not isinstance(objective, dict) or not isinstance(observation, dict):
        return None
    flat = flatten(observation)
    params = objective.get("params") if isinstance(objective.get("params"), dict) else {}
    try:
        type_id = int(objective.get("type_id", objective.get("objective_type")))
    except (TypeError, ValueError):
        type_id = -1
    text = str(objective.get("text") or "").lower()
    npc_id = (objective.get("npc_id") or params.get("npc_id")
              or params.get("parameter0"))
    if type_id in TALK_TYPES or type_id in DELIVER_TYPES:
        if npc_id is not None and not _npc_on_map(observation, npc_id):
            return {"kind": "goto_npc", "args": {"npc_id": npc_id}}
    if type_id in FIGHT_TYPES:
        if not ((observation.get("map") or {}).get("groups")):
            scan = (observation.get("map") or {}).get("groups_scan") or {}
            try:
                if int(scan.get("matched") or 0) > 0:
                    # Groups are visible but the page could not read them into
                    # `groups`; walking away would chase a read defect.
                    return None
            except (TypeError, ValueError):
                pass
            direction = _direction(observation)
            return {"kind": "explore", "args": {"direction": direction} if direction else {}}
    if type_id in MAP_TYPES:
        map_id = objective.get("map_id")
        if map_id not in (None, flat.get("map_id")):
            return {"kind": "goto_map", "args": {"map_id": map_id}}
    if "equip" in text or "équipe" in text or "equipa" in text:
        preset = _first_preset(observation)
        if preset is not None:
            return {"kind": "equip", "args": {"preset_id": preset}}
        if ((observation.get("inventory") or {}).get("items")):
            return {"kind": "equip", "args": {"needs_preset": True}}
    if any(word in text for word in ("bring", "collect", "rapporte", "apporte",
                                     "trae", "recoge")):
        gid = (params.get("item_id") or params.get("gid")
               or objective.get("item_id"))
        if gid is not None:
            qty = params.get("quantity") or params.get("parameter1") or 1
            return {"kind": "acquire", "args": {"gid": gid, "quantity": qty}}
    return None


def next_goal(stack: PlanStack, objective: Optional[Dict[str, Any]],
              observation: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the active goal after one observation, pushing a child if needed."""
    child = child_for_objective(objective, observation)
    if child is not None:
        stack.push(child["kind"], child.get("args"), parent="objective")
        return stack.top().as_dict() if stack.top() else None
    if stack.top() is None and objective:
        stack.push("objective", {"objective_id": objective.get("objective_id"),
                                 "quest_id": objective.get("quest_id")})
    return stack.top().as_dict() if stack.top() else None
