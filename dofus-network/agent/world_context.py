"""Structured world perception: everything the observation can tell a policy.

Why this module exists
----------------------
A policy can only learn a behaviour it can *see*. The live snapshot already
carries the whole map -- crossable exits, remembered door cells, mob groups,
NPCs, interactives, cell and coords -- but two consumers never read it:

* the encoder's prompt (``tool_dataset._observation_text``) renders exits, NPCs
  and interactives but not ``doors`` or ``groups``, so the model cannot tell a
  world map from a dungeon interior, or an empty room from one with mobs;
* the tool head reads a fixed 42-number vector (``agent.features``) with no
  map-shape signal at all.

Nothing here decides anything. It turns what the client reports into (a) a
compact, faithful text block a language policy can reason over and (b) an
ordered numeric block a head/WSP edit can learn from. The mapping from those
signals to an action is left to the model -- there is no ``if dungeon`` rule.

``interior`` is *derived*, not looked up: a known map that publishes no
crossable exit and names no neighbour is an interior (house, dungeon room), and
a door cell is how you leave it. That is a fact about the observation, not a
hand-maintained list of dungeon ids.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Ordered numeric block. Kept separate from ``agent.features.FEATURE_NAMES`` so
# the shipped 42-wide head is never silently widened: a retrained head opts in
# by appending this block, and ``objective_features`` stays byte-identical.
WORLD_FEATURE_NAMES: List[str] = [
    "interior",        # 1 when a known map has no crossable exit and no neighbour
    "n_exits",         # crossable border count (normalised below)
    "has_door",        # a remembered door cell leads somewhere
    "n_doors",         # number of remembered doors (normalised)
    "has_neighbours",  # the client named neighbour maps even with no exits
    "npc_here",        # an NPC is on this map
    "interactive_here",
    "groups_here",     # a mob group is visible here (a fight is available)
    "group_count",     # mob groups normalised by GROUP_SCALE
    "in_fight",
    "in_dialog",
    "auto_fight",
]
GROUP_SCALE = 5.0
EXIT_SCALE = 4.0
DOOR_SCALE = 4.0


def _map_block(observation: Any) -> Dict[str, Any]:
    if isinstance(observation, dict):
        block = observation.get("map")
        if isinstance(block, dict):
            return block
    return {}


def _in_world(observation: Any) -> bool:
    if not isinstance(observation, dict):
        return False
    if observation.get("in_world") is False:
        return False
    block = _map_block(observation)
    map_id = observation.get("map_id")
    if map_id is None:
        map_id = block.get("map_id")
    try:
        return int(map_id) > 0
    except (TypeError, ValueError):
        return False


def _exits(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [e for e in (block.get("exits") or []) if isinstance(e, dict)]


def _crossable_exit_count(block: Dict[str, Any]) -> int:
    return sum(1 for e in _exits(block) if not e.get("blocked") and e.get("direction"))


def describe_world(observation: Any) -> Dict[str, float]:
    """The numeric perception block, in ``WORLD_FEATURE_NAMES`` order semantics."""
    block = _map_block(observation)
    exits = _crossable_exit_count(block)
    neighbours = block.get("neighbours") if isinstance(block.get("neighbours"), dict) else {}
    doors = [d for d in (block.get("doors") or []) if isinstance(d, dict) and d.get("cell") is not None]
    npcs = [n for n in (block.get("npcs") or []) if isinstance(n, dict)]
    interactives = [i for i in (block.get("interactives") or []) if isinstance(i, dict)]
    groups = [g for g in (block.get("groups") or []) if isinstance(g, dict)]
    try:
        group_count = int(block.get("group_count") or len(groups))
    except (TypeError, ValueError):
        group_count = len(groups)

    known = _in_world(observation)
    interior = 1.0 if (known and exits == 0 and not neighbours) else 0.0
    return {
        "interior": interior,
        "n_exits": min(1.0, exits / EXIT_SCALE),
        "has_door": 1.0 if doors else 0.0,
        "n_doors": min(1.0, len(doors) / DOOR_SCALE),
        "has_neighbours": 1.0 if neighbours else 0.0,
        "npc_here": 1.0 if npcs else 0.0,
        "interactive_here": 1.0 if interactives else 0.0,
        "groups_here": 1.0 if (groups or group_count) else 0.0,
        "group_count": min(1.0, group_count / GROUP_SCALE),
        "in_fight": 1.0 if (isinstance(observation, dict) and observation.get("in_fight")) else 0.0,
        "in_dialog": 1.0 if (isinstance(observation, dict) and observation.get("in_dialog")) else 0.0,
        "auto_fight": 1.0 if (isinstance(observation, dict) and observation.get("auto_fight")) else 0.0,
    }


def world_feature_vector(observation: Any) -> List[float]:
    d = describe_world(observation)
    return [float(d[name]) for name in WORLD_FEATURE_NAMES]


def describe_world_text(observation: Any) -> Optional[str]:
    """A compact, faithful sentence the language policy can read. No decisions."""
    if not isinstance(observation, dict) or not observation:
        return None
    block = _map_block(observation)
    if not block and not _in_world(observation):
        return None
    d = describe_world(observation)
    parts: List[str] = []
    if d["interior"]:
        parts.append("interior map (no crossable exit)")
    elif d["n_exits"]:
        parts.append("open map with crossable exits")
    doors = [x for x in (block.get("doors") or []) if isinstance(x, dict)]
    if doors:
        seg = ", ".join("cell %s->map %s" % (x.get("cell"), x.get("to_map"))
                        for x in doors[:4])
        parts.append("doors: " + seg)
    groups = [g for g in (block.get("groups") or []) if isinstance(g, dict)]
    try:
        gc = int(block.get("group_count") or len(groups))
    except (TypeError, ValueError):
        gc = len(groups)
    if gc:
        cells = ", ".join("cell %s" % g.get("cell") for g in groups[:4])
        parts.append("mob groups here: %d%s" % (gc, (" (" + cells + ")") if cells else ""))
    npcs = [n for n in (block.get("npcs") or []) if isinstance(n, dict)]
    if npcs:
        parts.append("npcs: %d" % len(npcs))
    interactives = [i for i in (block.get("interactives") or []) if isinstance(i, dict)]
    if interactives:
        parts.append("interactives: %d" % len(interactives))
    if observation.get("in_fight"):
        parts.append("in fight")
    if observation.get("in_dialog"):
        parts.append("in dialog")
    return "; ".join(parts) if parts else None


def self_check() -> None:
    indoor = {
        "in_world": True, "map_id": 146538496,
        "map": {"map_id": 146538496, "exits": [], "neighbours": {},
                "doors": [{"cell": 331, "to_map": 145752833}],
                "groups": [], "group_count": 0, "npcs": [], "interactives": []},
    }
    outdoor = {
        "in_world": True, "map_id": 145490435,
        "map": {"map_id": 145490435,
                "exits": [{"direction": "left", "map_id": 1},
                          {"direction": "right", "map_id": 2}]},
    }
    a, b = describe_world(indoor), describe_world(outdoor)
    assert a["interior"] == 1.0 and a["has_door"] == 1.0, a
    assert b["interior"] == 0.0 and b["n_exits"] > 0.0, b
    assert describe_world_text(indoor) and "interior" in describe_world_text(indoor)
    assert describe_world_text(outdoor) and "crossable exits" in describe_world_text(outdoor)
    # a room with mobs reports a fight is available
    fight = {"in_world": True, "map_id": 9,
             "map": {"map_id": 9, "exits": [], "groups": [{"cell": 300}], "group_count": 1}}
    assert describe_world(fight)["groups_here"] == 1.0
    assert "mob groups here: 1" in (describe_world_text(fight) or "")


if __name__ == "__main__":
    self_check()
    print("world_context self_check: PASS")