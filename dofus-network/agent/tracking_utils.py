"""Pure quest tracking utilities extracted from QuestPlanner.

These functions compute quest state signatures and tracker values without
depending on planner instance state. Extracted from agent/planner.py to
reduce its size and improve separation of concerns.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple


def tracker_step(obs: Dict[str, Any], quest_id: int) -> Any:
    """The current step value for a quest from the client's quests_active."""
    active = obs.get("quests_active") or {}
    for key in (str(quest_id), quest_id):
        try:
            if key in active:
                return active[key]
        except TypeError:
            continue
    return None


def situation_sig(obs: Dict[str, Any], quest_id: int,
                  tutorial_block: Dict[str, Any] | None = None) -> Tuple[Any, ...]:
    """What the world looked like when a quest was given up on.

    A quest retired because the tutorial blocked a crossing is not a quest that
    cannot be done. Remembering the situation lets the next plan reopen it when
    the step changes, the map changes, or the tutorial ends -- which is exactly
    what "I advanced the quest myself but the AI still ignored it" was.

    The map is deliberately not part of the signature. It was, and a live
    session (2026-10-29) retired quest 4262 on map 145490432, walked to
    145490946 for its sibling, and the crossing alone "changed the situation"
    and reopened 4262 -- so the next step walked back. A quest retired for
    having nothing to do on a map is a question about the map visit
    (`_exhausted_at` answers it); it is not a different quest on the next
    crossing.
    """
    tracker = obs.get("current_objective") or {}
    try:
        if int(tracker.get("quest_id") or 0) != int(quest_id):
            tracker = {}
    except (TypeError, ValueError):
        tracker = {}
    tutorial = tutorial_block or {}
    gate = tutorial.get("can_change_map") or {}
    return (
        tracker_step(obs, quest_id),
        tracker.get("step"),
        tracker.get("objective_id"),
        bool(tutorial),
        tuple(sorted((str(k), bool(v)) for k, v in gate.items())),
    )