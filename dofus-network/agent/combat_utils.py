"""Pure combat and group utilities extracted from QuestPlanner.

These functions evaluate monster groups, travel permissions, and combat
readiness without depending on planner instance state. Extracted from
agent/planner.py to reduce its size and improve separation of concerns.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def map_groups(obs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The monster groups the snapshot shows on the current map."""
    block = obs.get("map") if isinstance(obs, dict) else None
    raw = None
    if isinstance(block, dict) and isinstance(block.get("groups"), list):
        raw = block["groups"]
    elif isinstance(obs, dict) and isinstance(obs.get("groups"), list):
        raw = obs["groups"]
    if raw is None:
        return []
    return [row for row in raw if isinstance(row, dict)]


def group_members(group: Dict[str, Any]) -> int:
    """The number of members in a monster group, at least 1."""
    try:
        return max(1, int(group.get("member_count")
                            or len(group.get("members") or []) or 1))
    except (TypeError, ValueError):
        return 1


def group_has(group: Dict[str, Any], monster: int) -> bool:
    """Whether a group contains a monster with the given id."""
    for raw in group.get("monster_ids") or []:
        try:
            if int(raw) == monster:
                return True
        except (TypeError, ValueError):
            continue
    for member in group.get("members") or []:
        if not isinstance(member, dict):
            continue
        try:
            if int(member.get("monster_id")) == monster:
                return True
        except (TypeError, ValueError):
            continue
    return False


def group_allowed(group: Dict[str, Any], estimates: Any) -> bool:
    """Whether this group may be attacked.

    The brain's own estimator is the authority when it has spoken for the
    group; without one, a group of at most two is the envelope every
    tutorial fight on this account has been accepted in, and anything larger
    stays for the estimator rather than being walked into blind.
    """
    gid = group.get("group_id")
    if isinstance(estimates, list) and estimates:
        for row in estimates:
            if not isinstance(row, dict):
                continue
            try:
                if row.get("group_id") is not None and int(row["group_id"]) == int(gid):
                    return bool(row.get("allowed"))
            except (TypeError, ValueError):
                continue
    return group_members(group) <= 2


def travel_allowed(obs: Dict[str, Any]) -> bool:
    """Whether the tutorial permits changing map right now."""
    tutorial = obs.get("tutorial")
    if not isinstance(tutorial, dict) or not tutorial.get("active"):
        return True
    gates = tutorial.get("can_change_map")
    if isinstance(gates, dict) and gates:
        return any(bool(value) for value in gates.values())
    return True