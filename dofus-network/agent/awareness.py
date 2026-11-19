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
from typing import Any, Dict, List, Optional, Tuple

from agent.compute_tree import IdentityMemo

import logging
logger = logging.getLogger(__name__)

try:
    import orjson as _orjson
except ImportError:  # pragma: no cover - orjson ships with the runtime
    _orjson = None

# -- luminance stall guard ---------------------------------------------------
# A black canvas means the renderer never started (or the client crashed behind a
# live bridge); a near-uniform white frame is the AWS WAF challenge the desktop
# host intercepts. Both look like "the world did not move" to a structured
# observation, so they need a claim about the *pixels* before the loop retires an
# objective for it.
#
# The check is deliberately a pure-NumPy sample, not an image pipeline: the guard
# runs inside the decision loop, and its budget is 0.2 ms. A source frame is
# strided down to FRAME_MAX_SAMPLES values, so the cost is independent of the
# window size and the answer is still exact for a flat frame.
BLACK_MEAN, BLACK_STD = 8.0, 4.0
WHITE_MEAN, WHITE_STD = 242.0, 8.0
FRAME_MAX_SAMPLES = 4096


def frame_stats(frame: Any) -> Optional[Tuple[float, float]]:
    """``(mean, std)`` of a decoded frame or a precomputed stats block.

    ``None`` for anything that is not a numeric array: a base64 PNG is a string,
    and decoding it here would blow the guard's budget by three orders of
    magnitude. The page may send ``{"mean": ..., "std": ...}`` when it already
    measured the frame.
    """
    if isinstance(frame, dict):
        try:
            mean = float(frame["mean"])
            std = float(frame["std"])
        except (KeyError, TypeError, ValueError):
            return None
        return (mean, std) if mean == mean and std == std else None
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with the runtime
        return None
    try:
        array = np.asarray(frame)
    except (TypeError, ValueError):
        return None
    if array.dtype.kind not in "biuf" or array.size < 16:
        return None
    flat = array.reshape(-1)
    stride = max(1, flat.size // FRAME_MAX_SAMPLES)
    sample = flat[::stride][:FRAME_MAX_SAMPLES].astype("float32", copy=False)
    return float(sample.mean()), float(sample.std())


def classify_frame(frame: Any) -> Optional[str]:
    """``"black_screen"``, ``"white_waf"`` or ``None`` for a normal frame."""
    stats = frame_stats(frame)
    if stats is None:
        return None
    mean, std = stats
    if mean < BLACK_MEAN and std < BLACK_STD:
        return "black_screen"
    if mean > WHITE_MEAN and std < WHITE_STD:
        return "white_waf"
    return None


def stall_frame_route(observation: Any, streak: int = 0,
                      settle_timed_out: bool = False) -> Optional[str]:
    """Route a suspected stall to recovery, but only when it is one.

    The guard is armed by the caller's own stall signals (a repeat streak or a
    settle timeout), which is what keeps it off the normal path: a rendered frame
    is not evidence of a fault unless the loop already failed to move.
    """
    if not (settle_timed_out or int(streak or 0) >= 2):
        return None
    if not isinstance(observation, dict):
        return None
    frame = observation.get("frame_stats")
    if frame is None:
        frame = observation.get("frame")
    if frame is None:
        frame = observation.get("screen")
    return classify_frame(frame)


# A carried snapshot is the same dict object across consecutive steps, so the
# second fingerprint of it is pure repeated work. The memo holds the object it
# cached against, which makes the identity check exact instead of an id guess.
_SCENE_MEMO = IdentityMemo()
_QUEST_MEMO = IdentityMemo()
_CHARACTER_MEMO = IdentityMemo()


def _digest(value: Any) -> str:
    # The scene key is compared for equality only; its concrete hex value is
    # opaque. orjson's sorted compact encoding is the same canonical fact in
    # far less CPU than stdlib json, which matters on maps with many groups.
    if _orjson is not None:
        try:
            encoded = _orjson.dumps(value, option=_orjson.OPT_SORT_KEYS)
            return hashlib.sha1(encoded).hexdigest()
        except (TypeError, ValueError) as exc:
            logger.warning("[awareness] orjson digest failed: %s", exc)
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
    # Sort by direct primitive tuples, not by JSON text. The list order only
    # has to be deterministic so the digest does not change when the client
    # reorders the same groups/NPCs/exits; serialising each item just to obtain
    # a sort key was repeated work on every decision.
    def group_order(item: Any) -> Any:
        if not isinstance(item, dict):
            return ("", repr(item))
        members = item.get("members") or []
        return (
            str(item.get("group_id")), str(item.get("cell")),
            tuple(str(value) for value in item.get("monster_ids") or []),
            tuple((str(member[0]), str(member[1])) for member in members
                  if isinstance(member, (list, tuple)) and len(member) >= 2),
        )

    def npc_order(item: Any) -> Any:
        if not isinstance(item, dict):
            return ("", repr(item))
        return (str(item.get("npc_id")), str(item.get("actor_id")),
                str(item.get("cell")))

    def exit_order(item: Any) -> Any:
        if not isinstance(item, dict):
            return ("", repr(item))
        return (str(item.get("direction")), str(item.get("map_id")),
                bool(item.get("blocked")))

    return {
        "map_id": _map_id(obs),
        "in_fight": bool(obs.get("in_fight")),
        "in_dialog": bool(obs.get("in_dialog")),
        "dialog": dialog_key,
        "current_objective": _objective_key(obs.get("current_objective")),
        "ready": ready_key,
        "active": active_key,
        "tutorial": tutorial_key,
        "groups": sorted((group_key(group) for group in groups), key=group_order),
        "npcs": sorted((npc_key(npc) for npc in npcs), key=npc_order),
        "exits": sorted((exit_key(exit_) for exit_ in exits), key=exit_order),
    }


def scene_key(obs: Optional[Dict[str, Any]]) -> str:
    """Fingerprint of the world situation a plan was made in."""
    if not isinstance(obs, dict):
        return ""
    return _SCENE_MEMO.get("scene", obs, lambda value: _digest(_scene_components(value)))


def quest_key(obs: Optional[Dict[str, Any]]) -> str:
    """Fingerprint of the quest/script state that can reopen a retired quest.

    The map is deliberately absent. `QuestPlanner._observe` clears `_skipped` wholesale
    when this key moves, and `QuestPlanner._situation_sig` -- the per-quest signature that
    `_reopen` reads -- also leaves the map out, for the reason recorded there: a crossing
    is not a new situation for a quest. With `map_id` in this key the two disagreed, and
    the disagreement is what produced the live cycle -- retire, cross, `_skipped` wiped,
    the same quest back with the same nothing to do, forty waits, retire again. The
    per-quest signature is the mechanism that decides a reopening; a coarse global clear
    that fires on a border is not a second opinion, it is the first one being undone.

    The map visit has its own record (`_exhausted_at`, keyed by objective and map), so
    nothing is lost by not carrying it here.
    """
    if not isinstance(obs, dict):
        return ""
    return _QUEST_MEMO.get("quest", obs, _quest_key_uncached)


def _quest_key_uncached(obs: Dict[str, Any]) -> str:
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
            # The gate is what a scripted step forbids ("do not change map"). A step whose
            # gate lifts is a new situation for every quest it was blocking, and this is
            # the only coarse event that has to survive the crossing rule above.
            "gate": sorted((str(key), bool(value)) for key, value
                           in (tutorial.get("can_change_map") or {}).items())
            if isinstance(tutorial.get("can_change_map"), dict) else [],
        },
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
    return _CHARACTER_MEMO.get("character", obs, _character_key_uncached)


def _character_key_uncached(obs: Dict[str, Any]) -> str:
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
