"""Attach the canonical knowledge to a live observation.

The client reports objective ids and a short tracker; the policy reads French
text. This module is the one bridge between the two: it resolves the current
objective against `knowledge.CanonicalStore` and adds the result under
`observation["knowledge"]`.

It does not plan and it does not invent. If the store is missing, or the
objective is not in it, the observation says why and the loop continues with
what the client provided.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from knowledge.store import CanonicalStore

DEFAULT_STORE = (
    Path(__file__).resolve().parents[1] / "data" / "knowledge" / "dofus_knowledge.sqlite"
)


def load_default_store(path: Optional[Path] = None) -> Optional[CanonicalStore]:
    target = Path(path) if path is not None else DEFAULT_STORE
    try:
        return CanonicalStore(target)
    except FileNotFoundError:
        return None


def _current_objective(obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tracker = obs.get("current_objective")
    if isinstance(tracker, dict) and tracker.get("objective_id") is not None:
        return tracker
    ready = obs.get("ready_objectives")
    if isinstance(ready, list):
        for entry in ready:
            if isinstance(entry, dict) and entry.get("objective_id") is not None:
                return entry
    return None


def enrich_observation(
    obs: Optional[Dict[str, Any]], store: Optional[CanonicalStore],
    language: str = "fr",
) -> Optional[Dict[str, Any]]:
    """Return a shallow copy of `obs` with `knowledge` added.

    The copy matters: the same snapshot is passed to the executor and written to
    the trajectory, and a second enrichment must not stack fields.
    """
    if not isinstance(obs, dict):
        return obs
    if store is None:
        return obs

    out = dict(obs)
    out.pop("knowledge", None)
    tracker = _current_objective(obs)
    if not tracker:
        out["knowledge"] = {"status": "no_active_objective"}
        return out

    try:
        objective_id = int(tracker["objective_id"])
    except (TypeError, ValueError):
        out["knowledge"] = {"status": "invalid_objective_id",
                            "objective_id": tracker.get("objective_id")}
        return out

    resolved = store.resolve_objective(objective_id, language)
    if resolved is None:
        out["knowledge"] = {
            "status": "objective_not_in_store",
            "objective_id": objective_id,
            "quest_id": tracker.get("quest_id"),
        }
        return out

    quest_id = resolved.get("quest_id") or tracker.get("quest_id")
    quest = store.quest(int(quest_id)) if quest_id is not None else None
    step = None
    if quest:
        for candidate in quest.get("steps") or []:
            if candidate.get("step_index") == resolved.get("step_index"):
                step = candidate
                break

    out["knowledge"] = {
        "status": "resolved",
        "language": language,
        "objective_id": objective_id,
        "quest_id": quest_id,
        "objective": {
            "text": resolved.get("text"),
            "raw_text": resolved.get("raw_text"),
            "type_id": resolved.get("type_id"),
            "map_id": resolved.get("map_id"),
            "params": resolved.get("params"),
            "entities": resolved.get("entities") or [],
            "unresolved": resolved.get("unresolved") or [],
            "source": resolved.get("source"),
        },
        "quest_name": (quest or {}).get("names", {}).get(language)
        or (quest or {}).get("names", {}).get("fr"),
        "step_name": (step or {}).get("names", {}).get(language)
        or (step or {}).get("names", {}).get("fr"),
        "step_description": (step or {}).get("descriptions", {}).get(language)
        or (step or {}).get("descriptions", {}).get("fr"),
    }
    return out
