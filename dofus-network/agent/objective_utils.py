"""Pure objective utilities extracted from QuestPlanner.

These functions evaluate quest objectives, level gates, and kill targets
without depending on planner instance state. Extracted from agent/planner.py
to reduce its size and improve separation of concerns.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from agent.features import _params, declares_monster_target, KILL_TYPES
from agent.planner_utils import LEVEL_GATE_RE


def level_gate_target(obj: Dict[str, Any]) -> Optional[int]:
    """The level a free-form objective demands, when it is a bare gate.

    Only `QuestObjectiveFreeForm` (type 0) with no monster target can be a
    gate: a kill objective's text can mention a level and must not be turned
    into "grind until level N".
    """
    if not isinstance(obj, dict):
        return None
    if declares_monster_target(obj) is not False:
        return None
    try:
        if int(obj.get("type_id")) != 0:
            return None
    except (TypeError, ValueError):
        return None
    match = LEVEL_GATE_RE.search(str(obj.get("text") or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def kill_objective_target(obj: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """(monster id, map id) a kill objective names, or (None, None)."""
    if not isinstance(obj, dict):
        return None, None
    if declares_monster_target(obj) is False:
        return None, None
    try:
        if int(obj.get("type_id")) not in KILL_TYPES:
            return None, None
    except (TypeError, ValueError):
        return None, None
    try:
        monster = int(_params(obj).get("parameter0"))
    except (TypeError, ValueError):
        return None, None
    try:
        need = int(obj.get("map_id")) if obj.get("map_id") else None
    except (TypeError, ValueError):
        need = None
    return monster, need


def objective_from_observation(entry: Any) -> Optional[Dict[str, Any]]:
    """Extract an objective dict from a catalog entry, or None."""
    if not isinstance(entry, dict):
        return None
    return {
        "id": entry.get("id") or entry.get("objective_id"),
        "quest_id": entry.get("quest_id"),
        "type_id": entry.get("type_id"),
        "text": entry.get("text"),
        "params": entry.get("params"),
        "step": entry.get("step") or entry.get("step_id"),
        "status": entry.get("status"),
    }

def objective_from_observation(entry: Any) -> Optional[Dict[str, Any]]:
    """One observation objective in the shape `plan_objective` reads."""
    if not isinstance(entry, dict):
        return None
    oid = entry.get("id", entry.get("objective_id"))
    if oid is None:
        return None
    obj: Dict[str, Any] = {"id": oid}
    if entry.get("type_id") is not None:
        obj["type_id"] = entry["type_id"]
    # The client's class name has to travel with the entry, or the parameter table
    # is never consulted on the path live quests actually take: the KB holds no live
    # quest id, so this fallback is *every* live quest, and without the name
    # `declares_monster_target` can only answer "unknown" and the invented monster
    # route stays in force (G27).
    if isinstance(entry.get("type"), str) and entry.get("type"):
        obj["type"] = entry["type"]
    if entry.get("map_id"):
        obj["map_id"] = entry["map_id"]
    params = entry.get("params")
    if isinstance(params, dict) and params:
        obj["params"] = params
    if entry.get("text"):
        obj["text"] = entry["text"]
    return obj