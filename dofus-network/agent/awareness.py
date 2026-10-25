"""State fingerprints for the live controller.

The observation is the truth; these keys only decide what an agent must forget.
When the map, quest, dialogue, NPCs or groups change, a claim/retry plan from the
old scene is stale. When the character's level, max HP, spells or build change,
the fight advisor's cached sheet is stale. Hashing the relevant facts gives both
layers a cheap, deterministic invalidation signal without putting transient
world state into a model's weights.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         default=str).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()


def _block(obs: Any, key: str) -> Dict[str, Any]:
    if not isinstance(obs, dict):
        return {}
    block = obs.get(key)
    return block if isinstance(block, dict) else {}


def _map_id(obs: Dict[str, Any]) -> Any:
    position = _block(obs, "position")
    block = _block(obs, "map")
    for value in (obs.get("map_id"), position.get("map_id"), block.get("map_id")):
        if value is not None:
            return value
    return None


def _objective_key(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return None
    return {
        "id": raw.get("objective_id", raw.get("id")),
        "quest_id": raw.get("quest_id"),
        "step": raw.get("step", raw.get("step_id")),
        "status": raw.get("status"),
        "type_id": raw.get("type_id"),
        "params": raw.get("params"),
        "text": raw.get("text"),
    }


def _scene_components(obs: Dict[str, Any]) -> Dict[str, Any]:
    block = _block(obs, "map")
    groups = obs.get("groups") if isinstance(obs.get("groups"), list) \
        else block.get("groups") or []
    npcs = obs.get("npcs") if isinstance(obs.get("npcs"), list) \
        else block.get("npcs") or []
    exits = block.get("exits") or obs.get("exits") or []

    def group_key(group: Any) -> Any:
        if not isinstance(group, dict):
            return group
        members = group.get("members") or []
        return {
            "group_id": group.get("group_id"),
            "cell": group.get("cell"),
            "monster_ids": sorted(group.get("monster_ids") or []),
            "members": sorted(
                (member.get("monster_id"), member.get("level"))
                for member in members if isinstance(member, dict)),
        }

    def npc_key(npc: Any) -> Any:
        if not isinstance(npc, dict):
            return npc
        return {"npc_id": npc.get("npc_id", npc.get("templateId")),
                "actor_id": npc.get("actor_id"), "cell": npc.get("cell")}

    def exit_key(exit_: Any) -> Any:
        if not isinstance(exit_, dict):
            return exit_
        return {"direction": exit_.get("direction"),
                "map_id": exit_.get("map_id"),
                "blocked": bool(exit_.get("blocked"))}

    dialog = _block(obs, "dialog")
    dialog_key = {
        "open": bool(obs.get("in_dialog") or dialog.get("open")),
        "npc_id": dialog.get("npc_id"),
        "text": dialog.get("text", dialog.get("question")),
        "replies": dialog.get("replies"),
    }
    tutorial = _block(obs, "tutorial")
    tutorial_key = {
        "active": bool(tutorial.get("active")),
        "quest_id": tutorial.get("quest_id"),
        "step_id": tutorial.get("step_id"),
        "special_step_id": tutorial.get("special_step_id"),
        "unblocked_npc_id": tutorial.get("unblocked_npc_id"),
        "unblocked_cells": sorted(tutorial.get("unblocked_cells") or []),
        "can_change_map": tutorial.get("can_change_map"),
    }
    active = obs.get("quests_active") or {}
    active_key = sorted(
        (str(key), value) for key, value in active.items()) \
        if isinstance(active, dict) else []
    ready = obs.get("ready_objectives") or []
    ready_key = []
    for item in ready:
        if not isinstance(item, dict):
            continue
        key = _objective_key(item)
        ready_key.append((key.get("id"), key.get("quest_id"), key.get("status")))
    ready_key.sort()
    return {
        "map_id": _map_id(obs),
        "in_fight": bool(obs.get("in_fight")),
        "in_dialog": bool(obs.get("in_dialog")),
        "dialog": dialog_key,
        "current_objective": _objective_key(obs.get("current_objective")),
        "ready": ready_key,
        "active": active_key,
        "tutorial": tutorial_key,
        "groups": sorted((group_key(group) for group in groups),
                         key=lambda item: json.dumps(item, sort_keys=True,
                                                     default=str)),
        "npcs": sorted((npc_key(npc) for npc in npcs),
                       key=lambda item: json.dumps(item, sort_keys=True,
                                                   default=str)),
        "exits": sorted((exit_key(exit_) for exit_ in exits),
                        key=lambda item: json.dumps(item, sort_keys=True,
                                                    default=str)),
    }


def scene_key(obs: Optional[Dict[str, Any]]) -> str:
    """Fingerprint of the world situation a plan was made in."""
    if not isinstance(obs, dict):
        return ""
    return _digest(_scene_components(obs))


def quest_key(obs: Optional[Dict[str, Any]]) -> str:
    """Fingerprint of the quest/script state that can reopen a retired quest."""
    if not isinstance(obs, dict):
        return ""
    tutorial = _block(obs, "tutorial")
    active = obs.get("quests_active") or {}
    return _digest({
        "level": obs.get("level") or _block(obs, "character").get("level"),
        "current_objective": _objective_key(obs.get("current_objective")),
        "ready": _objective_key_list(obs.get("ready_objectives")),
        "active": sorted((str(key), value) for key, value in active.items())
        if isinstance(active, dict) else [],
        "tutorial": {
            "active": bool(tutorial.get("active")),
            "quest_id": tutorial.get("quest_id"),
            "step_id": tutorial.get("step_id"),
        },
        "map_id": _map_id(obs),
    })


def _objective_key_list(raw: Any) -> List[Any]:
    if not isinstance(raw, list):
        return []
    keys = [_objective_key(item) for item in raw if isinstance(item, dict)]
    return sorted(keys, key=lambda item: json.dumps(item, sort_keys=True,
                                                    default=str))


def character_key(obs: Optional[Dict[str, Any]]) -> str:
    """Fingerprint of the character facts that change an estimate."""
    if not isinstance(obs, dict):
        return ""
    character = _block(obs, "character")
    hp = _block(obs, "hp")
    spells = obs.get("spells") or obs.get("spell_ids") or []
    if isinstance(spells, dict):
        spells = list(spells.keys())
    normalized_spells = sorted(
        str(spell.get("id")) if isinstance(spell, dict) else str(spell)
        for spell in spells)
    level = obs.get("level") or character.get("level")
    max_hp = (hp.get("max") or character.get("max_hp")
              or obs.get("max_hp"))
    return _digest({
        "level": level,
        "max_hp": max_hp,
        "id": character.get("id"),
        "breed": character.get("breed"),
        "spells": normalized_spells,
        "equipment": obs.get("equipment") or character.get("equipment"),
    })
