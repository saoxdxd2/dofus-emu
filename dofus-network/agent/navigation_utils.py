"""Pure navigation utilities extracted from QuestPlanner.

These functions read map exits and build explore/travel calls without
depending on planner instance state. Extracted from agent/planner.py
to reduce its size and improve separation of concerns.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from agent.features import direction_hint, known_map
from agent.observe import map_id_of


def observed_exits(obs: Optional[Dict[str, Any]],
                   include_blocked: bool = False) -> List[Dict[str, Any]]:
    """The exits the client reports for the map it is standing on.

    An exit the tutorial marks `blocked` is not an exit this agent can take:
    the client's tap gate refuses the crossing, and walking to the border cell
    just parks the character there. Filtering here means every caller — explore,
    travel, a learned tool choice — sees only crossings that can happen.
    """
    if not isinstance(obs, dict):
        return []
    block = obs.get("map")
    rows = block.get("exits") if isinstance(block, dict) else None
    try:
        here = int(map_id_of(obs) or (block or {}).get("map_id")
                   or (obs.get("position") or {}).get("map_id") or 0)
    except (TypeError, ValueError):
        here = 0
    exits: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        direction = row.get("direction")
        try:
            dest = int(row.get("map_id"))
        except (TypeError, ValueError):
            continue
        # A self-edge is not a crossing. The live tutorial map reported
        # right -> its own map id; the client refused every attempt with
        # "needs ... a map_id different from <here>".
        if here and dest == here:
            continue
        blocked = bool(row.get("blocked"))
        if blocked and not include_blocked:
            continue
        if direction and dest > 0:
            exits.append({"direction": direction, "map_id": dest,
                          "blocked": blocked})
    return exits


def open_exits(obs: Optional[Dict[str, Any]],
               spent: Optional[Iterable[int]] = None) -> List[Dict[str, Any]]:
    """The crossings this map reports, minus borders already walked and refused.

    `spent` names maps an edge has been walked towards without delivering them.
    They are removed before the "all exits lead back" fallback, because that
    fallback exists for a lap, not for a broken exit: re-listing a refused
    border is how a bounded bounce came back as an unbounded one.
    """
    exits = observed_exits(obs)
    if spent:
        spent_ids = {int(m) for m in spent}
        exits = [e for e in exits if e["map_id"] not in spent_ids]
    return exits


def explore_call(obj: Dict[str, Any],
                 obs: Optional[Dict[str, Any]],
                 avoid: Optional[int] = None,
                 spent: Optional[Iterable[int]] = None
                 ) -> Optional[Dict[str, Any]]:
    """Walk an exit of the current map when the destination is not nameable.

    A DiscoverMap objective with no `map_id` is a generic discovery: the
    client says "discover a map" and names none, so the only action that can
    satisfy it is entering a map. Prefer the edge the objective's own text
    names ("click the right edge"); without a hint, prefer a neighbour the
    routing graph does not have, since that is the crossing that also extends
    what the agent knows. None when the observation reports no usable exits.

    Picking `exits[0]` here is what walked a live tutorial character left when
    the tutorial wanted the right edge: the list is ordered left/right/top/
    bottom, which is the client's layout, not a preference. `direction_hint`
    reads the instruction in four languages and knows no quest id.
    """
    exits = open_exits(obs, spent)
    if not exits:
        return None
    hint = direction_hint(obj)
    probe = None
    if hint:
        probe = next((e for e in exits if e["direction"] == hint), None)
    if probe is None:
        candidates = [e for e in exits if avoid is None or e["map_id"] != avoid]
        if not candidates:
            candidates = exits  # all exits lead back; walking back beats stalling
        probe = next((e for e in candidates if not known_map(e["map_id"])),
                     candidates[0])
    return {
        "tool": "nav.goto",
        "args": {"direction": probe["direction"]},
        "why": ("objective %s: discover a map by walking the %s edge (-> %s)"
                % (obj.get("type_id"), probe["direction"], probe["map_id"])),
    }