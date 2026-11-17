"""Pure map and observation reading utilities extracted from QuestPlanner.

These functions read game state from observations and map data without
depending on planner instance state. Extracted from agent/planner.py
to reduce its size and improve separation of concerns.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from agent.features import map_walk, walkable_border_cells


def cell_distance(a: Any, b: Any) -> int:
    """Dofus cell distance; raw cell ids are not row-major."""
    try:
        first, second = int(a), int(b)
    except (TypeError, ValueError):
        return 1 << 30

    def point(cell: int) -> Tuple[int, int]:
        t = cell % 14 - cell // 28
        return t + 19, t + cell // 14

    ax, ay = point(first)
    bx, by = point(second)
    return abs(ax - bx) + abs(ay - by)


def groups_here(obs: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The monster groups the snapshot shows on the current map."""
    block = obs.get("map") if isinstance(obs, dict) else None
    raw = None
    if isinstance(block, dict):
        raw = block.get("groups")
    if raw is None and isinstance(obs, dict):
        raw = obs.get("groups")
    return [row for row in (raw or []) if isinstance(row, dict)]

def visible_target(obs: Optional[Dict[str, Any]],
                   monster: Any) -> Optional[bool]:
    """Whether the map snapshot shows a group containing `monster`.

    None means the observation carries no map block (the offline corpus
    shape), so the caller keeps its historical choice instead of inventing
    a search.
    """
    groups = None
    block = obs.get("map") if isinstance(obs, dict) else None
    if isinstance(block, dict) and isinstance(block.get("groups"), list):
        groups = [row for row in block["groups"] if isinstance(row, dict)]
    if groups is None and isinstance(obs, dict) and isinstance(obs.get("groups"), list):
        groups = [row for row in obs["groups"] if isinstance(row, dict)]
    if groups is None:
        return None
    if monster is None:
        return bool(groups)
    try:
        target = int(monster)
    except (TypeError, ValueError):
        return True   # a name the guard resolves; not ours to second-guess
    for group in groups:
        for raw in group.get("monster_ids") or []:
            try:
                if int(raw) == target:
                    return True
            except (TypeError, ValueError):
                continue
        for member in group.get("members") or []:
            if not isinstance(member, dict):
                continue
            try:
                if int(member.get("monster_id")) == target:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def map_npcs(obs: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The NPCs the current map reports, by template id."""
    if not isinstance(obs, dict):
        return []
    block = obs.get("map") if isinstance(obs.get("map"), dict) else {}
    raw = obs.get("npcs") or block.get("npcs") or []
    out: List[Dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        npc_id = entry.get("npc_id", entry.get("templateId"))
        try:
            npc_id = int(npc_id)
        except (TypeError, ValueError):
            continue
        if npc_id <= 0:
            continue
        out.append({"npc_id": npc_id, "cell": entry.get("cell"),
                    "actor_id": entry.get("actor_id"),
                    "has_quest": bool(entry.get("has_quest")),
                    "has_exclamation": bool(entry.get("has_exclamation")),
                    "has_question": bool(entry.get("has_question")),
                    "quests_to_start": list(entry.get("quests_to_start") or []),
                    "quests_to_valid": list(entry.get("quests_to_valid") or [])})
    return out


def map_interactives(obs: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The map's interactive elements, from the nested block or the flat key.

    A free-form objective is performed through one of these; the element list is
    what the client's own renderer reports, not a table the planner invents.
    """
    if not isinstance(obs, dict):
        return []
    block = obs.get("map") if isinstance(obs.get("map"), dict) else {}
    raw = obs.get("interactives") or block.get("interactives") or []
    out: List[Dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        elem_id = entry.get("id", entry.get("elementId"))
        try:
            elem_id = int(elem_id)
        except (TypeError, ValueError):
            continue
        if elem_id <= 0:
            continue
        out.append({"element_id": elem_id, "cell": entry.get("cell"),
                    "type": entry.get("type", entry.get("elemType"))})
    return out


def sealed_borders(here: Any, neighbours: Any) -> Set[Tuple[str, int]]:
    """Borders the cached geometry says this map has no cell to cross from.

    `data/maps/_index.json` carries `walk`, one character per cell, for 2,696 maps,
    and nothing read it. This branch runs exactly when the client published no exit
    -- so the cached geometry is the only witness left, and it answers the question
    the run was spending steps to learn: is there a cell on that edge to stand on?
    """
    try:
        here_i = int(here)
    except (TypeError, ValueError):
        return set()
    walk = map_walk(here_i)
    # A walk string of the wrong shape is not evidence about this map: the cache
    # may predate a map revision. Only a well-formed one is allowed to refuse.
    if not walk or len(walk) < 4 * 14 or len(walk) % 14:
        return set()
    out: Set[Tuple[str, int]] = set()
    for direction, dest in (neighbours or {}).items():
        if direction not in ("left", "right", "top", "bottom"):
            continue
        try:
            dest_i = int(dest)
        except (TypeError, ValueError):
            continue
        if not walkable_border_cells(walk, str(direction)):
            out.add((str(direction), dest_i))
    return out