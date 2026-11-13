"""The observable world as typed records, and a named encoding of them.

`agent/state_features.py` is the incumbent: 12 floats per entity, 5 kinds, and a
monster group collapses to `(count, group_id % 7, first level)` — every fact that
makes a group dangerous is discarded. This module is the alternative: one typed
record per element kind, and a *named* feature layout per block, so a consumer can
point at a number and say what it means.

Three properties are deliberate:

* **Nothing is truncated silently.** Entity lists keep every element. The encoder
  pads to fixed slots and reports how many did not fit in `coverage()["dropped"]`;
  a corpus that drops nothing says so with an empty dict.
* **The schema is closed.** `coverage()` walks the observation and classifies
  every key as encoded, ignored-for-a-stated-reason, or unknown. An unknown key is
  the signal that the client started sending something this encoder has never been
  taught — the failure mode that degrades a dataset for months without a symptom.
  Writing this file, the first audit found 26 top-level keys nobody had named.
* **Actions are not state.** `user_input` (who drove the last inputs) and
  `fight_estimates` (the policy's own prediction) are deliberately excluded from
  the tensors: one is provenance for the label, the other would feed the model its
  own guess back.

Measured over `trajectories/dofus-*.jsonl` (11,461 observations, 11,452 with a map):
the incumbent's caps (12 NPCs, 8 interactives) never bind — the maximum observed is
4 NPCs and 1 interactive — so the loss is semantic, not volumetric. What was
actually missing: group composition (7 fields per group, all discarded), NPC
identity, interactive skill lists, door targets, quest structure, and terrain.

    python -m agent.world_state --coverage trajectories/dofus-01.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from agent.jsonio import utf8_console
import logging
logger = logging.getLogger(__name__)

SCHEMA_VERSION = int(os.environ.get("DOFUS_WORLD_SCHEMA_VERSION", "2"))

# The client's grid is 14 cells per row.
CELLS_PER_ROW = int(os.environ.get("DOFUS_CELLS_PER_ROW", "14"))

# Fixed slot counts. The measured maxima are 4 NPCs, 4 groups, 4 exits, 1
# interactive, 10 doors, 1 quest, 1 ready objective; the slots are that plus
# headroom. Anything beyond is counted in `coverage()["dropped"]`, never lost
# quietly.
DEFAULT_SLOT_COUNTS = {"npc": 8, "interactive": 8, "group": 8, "exit": 8, "door": 12,
         "objective": 8, "quest": 8}
SLOTS = (
    json.loads(os.environ["DOFUS_WORLD_SLOTS"])
    if os.environ.get("DOFUS_WORLD_SLOTS")
    else dict(DEFAULT_SLOT_COUNTS)
)

# ---------------------------------------------------------------------------
# named feature layouts
#
# Every block is a flat list of floats and a list of names, same length. A
# consumer that indexes position 3 of the group block can look it up instead of
# carrying a comment that goes stale.

GLOBAL_FEATURES: Tuple[str, ...] = (
    "level_over_200", "hp_fraction", "ap_over_12", "mp_over_6",
    "kamas_log", "inventory_over_60", "in_dialog", "in_fight", "alive",
    "ready_objectives_over_10", "quests_active_over_20", "tutorial",
    "npc_count_over_8", "group_count_over_8", "interactive_count_over_8",
    "exit_count_over_8", "blocked_exit_fraction", "has_terrain",
    "xp_log", "quests_finished_over_50", "objectives_completed_over_50",
    "catalog_quests_over_10", "catalog_steps_over_50", "catalog_objectives_over_100",
    "catalog_truncated", "has_stats", "has_spells",
    "open_windows_over_5", "challenges_over_6", "challenges_success_fraction",
    "prices_over_100", "step_objectives_over_10",
    "walkable_fraction", "cell_walkable",
)

_COMMON = ("present", "dx", "dy", "manhattan")

NPC_FEATURES: Tuple[str, ...] = _COMMON + ("gid_log", "has_name")
INTERACTIVE_FEATURES: Tuple[str, ...] = _COMMON + (
    "type_id_log", "is_door", "skill_count_over_4", "skill0_log", "has_name")
GROUP_FEATURES: Tuple[str, ...] = _COMMON + (
    "members_over_8", "total_level_over_400", "max_level_over_200", "is_boss",
    "species_count_over_8", "level_spread_over_200", "avg_level_over_200")
EXIT_FEATURES: Tuple[str, ...] = _COMMON + (
    "blocked", "walk_cells_over_6", "dir_left", "dir_right", "dir_top",
    "dir_bottom")
DOOR_FEATURES: Tuple[str, ...] = _COMMON + ("has_target", "via_present")
OBJECTIVE_FEATURES: Tuple[str, ...] = (
    "present", "type_id_log", "step_id_log", "objective_id_log",
    "has_params", "param0_log", "text_over_100")
QUEST_FEATURES: Tuple[str, ...] = ("present", "quest_id_log", "step_index_over_20",
                                   "current_step_id_log")

BLOCK_FEATURES: Dict[str, Tuple[str, ...]] = {
    "npc": NPC_FEATURES,
    "interactive": INTERACTIVE_FEATURES,
    "group": GROUP_FEATURES,
    "exit": EXIT_FEATURES,
    "door": DOOR_FEATURES,
    "objective": OBJECTIVE_FEATURES,
    "quest": QUEST_FEATURES,
}

# ---------------------------------------------------------------------------
# closed schema
#
# Every key the observation can carry, and what happens to it. `ENCODED` means a
# value reaches the tensors. `IGNORED` means it is deliberately not encoded, and
# the reason is in the table — not in someone's memory.

ENCODED_TOP = frozenset({
    "level", "hp", "kamas", "inventory", "in_dialog", "in_fight", "alive",
    "ready_objectives", "quests_active", "tutorial", "map", "character",
    "dialog", "life_state", "position", "xp", "xp_level",
    "step_objectives", "objectives_completed", "quests_finished",
    "quests_catalog", "quest_catalog_status", "quest_steps", "points", "challenges",
    "battle_log_tail", "fight_id", "open_windows", "prices",
})

IGNORED_TOP: Dict[str, str] = {
    "agent_build": "provenance of the recorder, not world state",
    "auto_fight": "a policy setting, not an observation",
    "current_objective": "duplicates ready_objectives",
    "farm_session": "session bookkeeping",
    "farm_state": "bookkeeping; in_fight covers the decision-relevant part",
    "connected": "transport state; a stale True is worse than no feature",
    "in_world": "implied by position.map_id being present",
    "knowledge": "the model's own memory, not the world",
    "last_disconnect": "historical event, not current state",
    "last_nav": "historical event, not current state",
    "last_fight_end": "historical event, not current state",
    "keep_alive": "transport heartbeat",
    "geometry": "browser viewport and reload count, not the game world",
    "user_input": "who drove the inputs, not what the world is; use it to label "
                  "provenance, never as a feature",
    "fight_estimates": "the policy's own prediction; feeding it back is a loop",
    "login": "redacted credential material",
    "login_cooldown": "session timing",
    "client_errors": "recorder health, not world state",
    "cell": "legacy duplicate of position.cell",
    "map_id": "legacy duplicate of position.map_id",
    # Named 2026-11-10 by the gate's coverage sample, which found all four in the
    # recorded corpus and none in this table. Naming them does not make them
    # encoded; it makes the gap reviewable instead of invisible, which is the
    # whole reason this dict exists.
    "stats": "the character sheet. Decision-relevant for combat, and deliberately "
             "deferred: encoding it widens the encoder's input, which is pinned by "
             "the shipped 266-wide policy. Revisit with the next policy refit.",
    "element": "the character's build element. Not a per-step feature: the dialog "
               "ranker reads it directly (`dialog_policy._character_element`), so "
               "encoding it here would be a second definition of the same fact.",
    "menu_icons": "the client's own menu labels `{key, label}`. The planner learns "
                  "these as surface labels (a knowledge fact that outlives the step), "
                  "so they are not a feature of this observation.",
}

ENCODED_MAP = frozenset({
    "map_id", "cell", "x", "y", "neighbours", "exits", "doors", "npcs",
    "interactives", "groups", "group_count", "terrain",
})

IGNORED_MAP: Dict[str, str] = {
    "groups_scan": "a separate scan record, not the map's own state",
    "transition": "movement history ring; belongs to a temporal model",
}

# Per-entity fields that reach the tensors. Anything else on an entity row is
# carried in the JSON record and counted by `coverage()`.
ENCODED_ENTITY = {
    "npc": frozenset({"npc_id", "name", "cell"}),
    "interactive": frozenset({"elem_id", "cell", "element_type_id", "is_door",
                              "enabled_skills", "name"}),
    "group": frozenset({"cell", "members", "monster_ids", "member_count",
                        "total_level", "max_level", "is_boss", "levels_known"}),
    "exit": frozenset({"direction", "map_id", "blocked", "cells"}),
    "door": frozenset({"cell", "to_map", "via"}),
}

IGNORED_ENTITY = {
    "npc": {"actor_id": "contextual id changes per spawn; not stable across sessions",
            # Named 2026-11-10 by the gate's coverage sample. Both are the quest
            # marker the client draws over an NPC, and the fact behind it is already
            # in the graph: a talk / go-to-NPC objective's `parameter0` is the NPC's
            # template id (`QuestPlanner._quest_target_npcs`). Encoding the paint
            # would add a column that duplicates a lookup the planner already does.
            "has_quest": "quest marker; the objective's parameter0 already names the NPC",
            "has_exclamation": "the same marker drawn differently",
            "has_question": "the same marker for a dialog that is ready to continue",
            "quests_to_start": "how many quests the client would offer here. The graph "
                               "is the authority on which quest an NPC gives; a marker "
                               "count would be a second, staler answer",
            "quests_to_valid": "the same count for turn-ins"},
    "interactive": {"skill_uid": "instance id, not a property of the element"},
    "group": {"group_id": "contextual id; the composition is the signal",
              "contextualId": "same as group_id",
              "scale_level": "a client render setting, not world state",
              "static_keys": "recorder bookkeeping"},
    "exit": {},
    "door": {"at": "when the door was seen, not what it is"},
}


# ---------------------------------------------------------------------------
# records


def _num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return default
        return float(value)
    # Quest ids arrive as strings (`{"3841": 0}`), and a string read as 0 made
    # every quest_id feature identically zero — a silent hole in the block.
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _log(value: Any) -> float:
    """log1p, so a gid of 19774 and one of 3 are both in a usable range."""
    return math.log1p(max(0.0, _num(value))) / 10.0


def _cell_xy(cell: Any) -> Optional[Tuple[float, float]]:
    try:
        value = int(cell)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return float(value % CELLS_PER_ROW), float(value // CELLS_PER_ROW)


def _rel(cell: Any, origin: Optional[Tuple[float, float]]) -> Tuple[float, float, float]:
    """(dx, dy, manhattan), all normalised. Unknown cell reads as 'far away'."""
    xy = _cell_xy(cell)
    if xy is None or origin is None:
        return 0.0, 0.0, 1.0
    dx = (xy[0] - origin[0]) / 10.0
    dy = (xy[1] - origin[1]) / 10.0
    return dx, dy, (abs(xy[0] - origin[0]) + abs(xy[1] - origin[1])) / 20.0


def _size(value: Any) -> int:
    if isinstance(value, (list, tuple, dict, set)):
        return len(value)
    return 0


def walkable_cells(terrain: Optional[str]) -> List[bool]:
    """Expand the packed terrain into one bool per cell.

    The page writes four cells per hex digit, low bit first: bit 0 of a cell's
    `l` flag is walkability, which is the client's own `isWalkable` test. An
    empty or malformed string yields no cells rather than a wrong grid.
    """
    if not isinstance(terrain, str) or not terrain:
        return []
    out: List[bool] = []
    for char in terrain:
        try:
            nibble = int(char, 16)
        except ValueError:
            return []
        for bit in range(4):
            out.append(bool(nibble & (1 << bit)))
    return out


@dataclass
class WorldState:
    """One observation, typed. `extra` keeps anything the schema does not name."""

    version: int = SCHEMA_VERSION
    # self
    level: Optional[float] = None
    xp: Optional[float] = None
    hp: Optional[float] = None
    hp_max: Optional[float] = None
    ap: Optional[float] = None
    mp: Optional[float] = None
    kamas: Optional[float] = None
    inventory_size: int = 0
    in_dialog: bool = False
    in_fight: bool = False
    alive: bool = True
    tutorial: bool = False
    has_stats: bool = False
    has_spells: bool = False
    # quests
    ready_objectives: List[Dict[str, Any]] = field(default_factory=list)
    step_objectives: List[Dict[str, Any]] = field(default_factory=list)
    quests_active: Dict[str, Any] = field(default_factory=dict)
    quest_steps: Dict[str, Any] = field(default_factory=dict)
    objectives_completed: int = 0
    quests_finished: int = 0
    catalog: Dict[str, int] = field(default_factory=dict)
    # place
    map_id: Optional[float] = None
    cell: Optional[float] = None
    x: Optional[float] = None
    y: Optional[float] = None
    neighbours: Dict[str, float] = field(default_factory=dict)
    terrain: Optional[str] = None
    # elements
    npcs: List[Dict[str, Any]] = field(default_factory=list)
    interactives: List[Dict[str, Any]] = field(default_factory=list)
    groups: List[Dict[str, Any]] = field(default_factory=list)
    exits: List[Dict[str, Any]] = field(default_factory=list)
    doors: List[Dict[str, Any]] = field(default_factory=list)
    # context that is counted rather than encoded per-element
    open_windows: List[str] = field(default_factory=list)
    challenges: List[Dict[str, Any]] = field(default_factory=list)
    battle_log_tail: int = 0
    fight_id: Optional[float] = None
    prices: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "self": {"level": self.level, "xp": self.xp, "hp": self.hp,
                     "hp_max": self.hp_max, "ap": self.ap, "mp": self.mp,
                     "kamas": self.kamas, "inventory_size": self.inventory_size,
                     "in_dialog": self.in_dialog, "in_fight": self.in_fight,
                     "alive": self.alive, "tutorial": self.tutorial,
                     "has_stats": self.has_stats, "has_spells": self.has_spells},
            "quests": {"ready": self.ready_objectives,
                       "steps": self.step_objectives,
                       "active": self.quests_active,
                       "current_steps": self.quest_steps,
                       "completed": self.objectives_completed,
                       "finished": self.quests_finished,
                       "catalog": self.catalog},
            "place": {"map_id": self.map_id, "cell": self.cell, "x": self.x,
                      "y": self.y, "neighbours": self.neighbours,
                      "terrain": self.terrain},
            "npcs": self.npcs,
            "interactives": self.interactives,
            "groups": self.groups,
            "exits": self.exits,
            "doors": self.doors,
            "context": {"open_windows": self.open_windows,
                        "challenges": self.challenges,
                        "battle_log_tail": self.battle_log_tail,
                        "fight_id": self.fight_id, "prices": self.prices},
            "extra": self.extra,
        }

    def counts(self) -> Dict[str, int]:
        return {"npc": len(self.npcs), "interactive": len(self.interactives),
                "group": len(self.groups), "exit": len(self.exits),
                "door": len(self.doors), "objective": len(self.ready_objectives),
                "quest": len(self.quests_active)}


def _dicts(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _members(group: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("members", "monsters"):
        rows = _dicts(group.get(key))
        if rows:
            return rows
    return []


def _catalog_counts(catalog: Any) -> Dict[str, int]:
    """Quests/steps/objectives in the static quest catalog.

    The catalog is knowledge, not state: it repeats on every observation and only
    the counts are encoded. A consumer that needs the graph should read it once
    per session from `to_dict()["quests"]["catalog"]`.
    """
    quests = _dicts(catalog)
    steps = 0
    objectives = 0
    for quest in quests:
        for step in _dicts(quest.get("steps")):
            steps += 1
            objectives += len(_dicts(step.get("objectives")))
    return {"quests": len(quests), "steps": steps, "objectives": objectives}


def from_observation(obs: Any) -> WorldState:
    """Build a WorldState from one recorded observation. Never raises."""
    state = WorldState()
    if not isinstance(obs, dict):
        return state

    hp = obs.get("hp") if isinstance(obs.get("hp"), dict) else {}
    char = obs.get("character") if isinstance(obs.get("character"), dict) else {}
    state.level = _num(char.get("level"), _num(obs.get("level"))) or None
    state.xp = _num(obs.get("xp")) if obs.get("xp") is not None else None
    state.hp = _num(hp.get("life")) if "life" in hp else None
    state.hp_max = _num(hp.get("max")) if "max" in hp else None
    state.ap = _num(hp.get("ap")) if "ap" in hp else None
    state.mp = _num(hp.get("mp")) if "mp" in hp else None
    state.kamas = _num(obs.get("kamas")) if obs.get("kamas") is not None else None
    state.inventory_size = _size(obs.get("inventory"))
    state.in_dialog = bool(obs.get("in_dialog"))
    state.in_fight = bool(obs.get("in_fight"))
    state.alive = obs.get("alive") is not False
    state.tutorial = bool(obs.get("tutorial"))

    points = obs.get("points") if isinstance(obs.get("points"), dict) else {}
    state.has_stats = points.get("stats") is not None
    state.has_spells = points.get("spells") is not None

    state.ready_objectives = _dicts(obs.get("ready_objectives"))
    state.step_objectives = _dicts(obs.get("step_objectives"))
    active = obs.get("quests_active")
    state.quests_active = active if isinstance(active, dict) else {}
    steps = obs.get("quest_steps")
    state.quest_steps = steps if isinstance(steps, dict) else {}
    state.objectives_completed = int(_num(obs.get("objectives_completed")))
    state.quests_finished = int(_num(obs.get("quests_finished")))
    state.catalog = _catalog_counts(obs.get("quests_catalog"))
    status = obs.get("quest_catalog_status")
    if isinstance(status, dict) and status.get("truncated"):
        state.catalog["truncated"] = 1

    # `position` is the canonical location; `map.*` is the older carrier and is
    # still read as a fallback because 9 observations have one and not the other.
    pos = obs.get("position") if isinstance(obs.get("position"), dict) else {}
    m = obs.get("map") if isinstance(obs.get("map"), dict) else {}
    state.cell = _num(pos.get("cell")) if pos.get("cell") is not None else (
        _num(m.get("cell")) if m.get("cell") is not None else None)
    state.x = _num(pos.get("x")) if pos.get("x") is not None else (
        _num(m.get("x")) if m.get("x") is not None else None)
    state.y = _num(pos.get("y")) if pos.get("y") is not None else (
        _num(m.get("y")) if m.get("y") is not None else None)
    state.map_id = (_num(pos.get("map_id")) or _num(m.get("map_id"))) or None

    if m:
        neighbours = m.get("neighbours")
        if isinstance(neighbours, dict):
            state.neighbours = {str(k): _num(v) for k, v in neighbours.items()}
        terrain = m.get("terrain")
        if isinstance(terrain, str) and terrain:
            state.terrain = terrain
        state.npcs = _dicts(m.get("npcs"))
        state.interactives = _dicts(m.get("interactives"))
        state.groups = _dicts(m.get("groups"))
        state.exits = _dicts(m.get("exits"))
        state.doors = _dicts(m.get("doors"))

    windows = obs.get("open_windows")
    state.open_windows = [str(w) for w in windows] if isinstance(windows, list) else []
    state.challenges = _dicts(obs.get("challenges"))
    state.battle_log_tail = _size(obs.get("battle_log_tail"))
    state.fight_id = _num(obs.get("fight_id")) if obs.get("fight_id") is not None else None
    state.prices = _size(obs.get("prices"))

    known = ENCODED_TOP | set(IGNORED_TOP)
    state.extra = {k: v for k, v in obs.items() if k not in known}
    return state


# ---------------------------------------------------------------------------
# encoding


def _row_common(cell: Any, origin: Optional[Tuple[float, float]]) -> List[float]:
    dx, dy, dist = _rel(cell, origin)
    return [1.0, dx, dy, dist]


def _encode_npc(npc: Dict[str, Any], origin) -> List[float]:
    row = _row_common(npc.get("cell"), origin)
    row.append(_log(npc.get("npc_id")))
    row.append(1.0 if npc.get("name") else 0.0)
    return row


def _encode_interactive(element: Dict[str, Any], origin) -> List[float]:
    row = _row_common(element.get("cell"), origin)
    skills = element.get("enabled_skills")
    skills = skills if isinstance(skills, list) else []
    row.extend([
        _log(element.get("element_type_id")),
        1.0 if element.get("is_door") else 0.0,
        min(1.0, len(skills) / 4.0),
        _log(skills[0]) if skills else 0.0,
        1.0 if element.get("name") else 0.0,
    ])
    return row


def _encode_group(group: Dict[str, Any], origin) -> List[float]:
    members = _members(group)
    levels = [_num(m.get("level")) for m in members if m.get("level") is not None]
    if not levels:
        raw = group.get("levels_known")
        if isinstance(raw, list):
            levels = [_num(v) for v in raw]
    species = group.get("monster_ids")
    species = species if isinstance(species, list) else []
    count = _num(group.get("member_count"), len(members) or len(species))
    total = _num(group.get("total_level"), sum(levels))
    peak = _num(group.get("max_level"), max(levels) if levels else 0.0)
    spread = (max(levels) - min(levels)) if levels else 0.0
    avg = (sum(levels) / len(levels)) if levels else 0.0
    row = _row_common(group.get("cell"), origin)
    row.extend([
        min(1.0, count / 8.0),
        min(1.0, total / 400.0),
        min(1.0, peak / 200.0),
        1.0 if group.get("is_boss") else 0.0,
        min(1.0, len(species) / 8.0),
        min(1.0, spread / 200.0),
        min(1.0, avg / 200.0),
    ])
    return row


def _encode_exit(exit_row: Dict[str, Any], origin) -> List[float]:
    cells = exit_row.get("cells")
    cells = cells if isinstance(cells, list) else []
    # The nearest walkable cell is where the crossing actually starts, so
    # distance to the exit means distance to that cell.
    best = None
    for cell in cells:
        xy = _cell_xy(cell)
        if xy is None or origin is None:
            continue
        dist = abs(xy[0] - origin[0]) + abs(xy[1] - origin[1])
        if best is None or dist < best[0]:
            best = (dist, cell)
    anchor = best[1] if best else None
    row = _row_common(anchor, origin)
    direction = str(exit_row.get("direction") or "")
    row.extend([
        1.0 if exit_row.get("blocked") else 0.0,
        min(1.0, len(cells) / 6.0),
        1.0 if direction == "left" else 0.0,
        1.0 if direction == "right" else 0.0,
        1.0 if direction == "top" else 0.0,
        1.0 if direction == "bottom" else 0.0,
    ])
    return row


def _encode_door(door: Dict[str, Any], origin) -> List[float]:
    row = _row_common(door.get("cell"), origin)
    row.extend([
        1.0 if _num(door.get("to_map")) > 0 else 0.0,
        1.0 if door.get("via") else 0.0,
    ])
    return row


def _encode_objective(objective: Dict[str, Any], _origin) -> List[float]:
    params = objective.get("params") if isinstance(objective.get("params"), dict) else {}
    text = objective.get("text")
    return [
        1.0,
        _log(objective.get("type_id")),
        _log(objective.get("step_id")),
        _log(objective.get("objective_id")),
        1.0 if params else 0.0,
        _log(params.get("parameter0")),
        min(1.0, (len(text) if isinstance(text, str) else 0) / 100.0),
    ]


def _encode_quest(item: Tuple[str, Any, Any], _origin) -> List[float]:
    quest_id, step_index, step_id = item
    return [1.0, _log(quest_id), min(1.0, _num(step_index) / 20.0), _log(step_id)]


_ENCODERS = {
    "npc": _encode_npc,
    "interactive": _encode_interactive,
    "group": _encode_group,
    "exit": _encode_exit,
    "door": _encode_door,
    "objective": _encode_objective,
    "quest": _encode_quest,
}


def encode(state: WorldState) -> Dict[str, Any]:
    """Named blocks. `dropped` counts elements that did not fit a slot."""
    origin = _cell_xy(state.cell)
    counts = state.counts()
    exits = state.exits
    blocked = sum(1 for row in exits if row.get("blocked"))
    challenges = state.challenges
    won = sum(1 for row in challenges if row.get("success"))
    walk = walkable_cells(state.terrain)
    here_walkable = 0.0
    if walk and state.cell is not None:
        index = int(state.cell)
        if 0 <= index < len(walk):
            here_walkable = 1.0 if walk[index] else 0.0

    global_row = [
        _num(state.level) / 200.0,
        (_num(state.hp) / _num(state.hp_max)) if state.hp_max else 0.0,
        _num(state.ap) / 12.0,
        _num(state.mp) / 6.0,
        _log(state.kamas),
        min(1.0, state.inventory_size / 60.0),
        1.0 if state.in_dialog else 0.0,
        1.0 if state.in_fight else 0.0,
        1.0 if state.alive else 0.0,
        min(1.0, len(state.ready_objectives) / 10.0),
        min(1.0, len(state.quests_active) / 20.0),
        1.0 if state.tutorial else 0.0,
        min(1.0, counts["npc"] / 8.0),
        min(1.0, counts["group"] / 8.0),
        min(1.0, counts["interactive"] / 8.0),
        min(1.0, counts["exit"] / 8.0),
        (blocked / len(exits)) if exits else 0.0,
        1.0 if state.terrain else 0.0,
        _log(state.xp),
        min(1.0, state.quests_finished / 50.0),
        min(1.0, state.objectives_completed / 50.0),
        min(1.0, state.catalog.get("quests", 0) / 10.0),
        min(1.0, state.catalog.get("steps", 0) / 50.0),
        min(1.0, state.catalog.get("objectives", 0) / 100.0),
        1.0 if state.catalog.get("truncated") else 0.0,
        1.0 if state.has_stats else 0.0,
        1.0 if state.has_spells else 0.0,
        min(1.0, len(state.open_windows) / 5.0),
        min(1.0, len(challenges) / 6.0),
        (won / len(challenges)) if challenges else 0.0,
        min(1.0, state.prices / 100.0),
        min(1.0, len(state.step_objectives) / 10.0),
        (sum(1 for cell in walk if cell) / len(walk)) if walk else 0.0,
        here_walkable,
    ]

    sources = {
        "npc": state.npcs,
        "interactive": state.interactives,
        "group": state.groups,
        "exit": state.exits,
        "door": state.doors,
        "objective": state.ready_objectives,
        "quest": [(qid, index, state.quest_steps.get(qid))
                  for qid, index in state.quests_active.items()],
    }

    blocks: Dict[str, List[List[float]]] = {}
    dropped: Dict[str, int] = {}
    for kind, slots in SLOTS.items():
        source = sources[kind]
        width = len(BLOCK_FEATURES[kind])
        rows = [_ENCODERS[kind](row, origin) for row in source[:slots]]
        rows = [row[:width] + [0.0] * max(0, width - len(row)) for row in rows]
        dropped[kind] = max(0, len(source) - slots)
        while len(rows) < slots:
            rows.append([0.0] * width)
        blocks[kind] = rows

    return {
        "version": SCHEMA_VERSION,
        "global": global_row,
        "blocks": blocks,
        "features": {"global": list(GLOBAL_FEATURES),
                     **{k: list(v) for k, v in BLOCK_FEATURES.items()}},
        "counts": counts,
        "dropped": dropped,
        "dims": {"global": len(GLOBAL_FEATURES),
                 **{k: len(v) for k, v in BLOCK_FEATURES.items()}},
    }


BLOCK_ORDER: Tuple[str, ...] = ("npc", "interactive", "group", "exit", "door", "objective", "quest")
FLAT_DIM: int = len(GLOBAL_FEATURES) + sum(SLOTS[k] * len(BLOCK_FEATURES[k]) for k in BLOCK_ORDER)


def flat_names() -> List[str]:
    """Names of all FLAT_DIM (482) features in flattened world-state order."""
    names: List[str] = ["global." + f for f in GLOBAL_FEATURES]
    for kind in BLOCK_ORDER:
        feats = BLOCK_FEATURES[kind]
        for slot in range(SLOTS[kind]):
            for feat in feats:
                names.append(f"{kind}_{slot}.{feat}")
    return names


def flatten(encoded: Dict[str, Any]) -> List[float]:
    """Flatten an encoded world-state dictionary into a 1D float list of length FLAT_DIM (482).

    Layout: global (34 floats) followed by fixed slot blocks in BLOCK_ORDER:
    npc (48), interactive (72), group (88), exit (80), door (72), objective (56), quest (32).
    """
    out: List[float] = list(encoded.get("global") or [0.0] * len(GLOBAL_FEATURES))
    blocks = encoded.get("blocks") or {}
    for kind in BLOCK_ORDER:
        expected_width = len(BLOCK_FEATURES[kind])
        rows = blocks.get(kind) or []
        for slot in range(SLOTS[kind]):
            if slot < len(rows) and isinstance(rows[slot], list):
                row = rows[slot]
                if len(row) < expected_width:
                    out.extend(row + [0.0] * (expected_width - len(row)))
                else:
                    out.extend(row[:expected_width])
            else:
                out.extend([0.0] * expected_width)
    return out


# ---------------------------------------------------------------------------
# closed-schema coverage


def coverage(obs: Any) -> Dict[str, Any]:
    """Classify every key in one observation: encoded, ignored, or unknown.

    `unknown` is the only alarming list. It means the client sent a field this
    encoder has never been taught about, so a dataset built from it is silently
    missing whatever that field carried.
    """
    report: Dict[str, Any] = {"encoded": [], "ignored": [], "unknown": [],
                              "unknown_entity": [], "dropped": {}}
    if not isinstance(obs, dict):
        report["unknown"] = ["<not a dict>"]
        return report

    for key in obs:
        if key in ENCODED_TOP:
            report["encoded"].append(key)
        elif key in IGNORED_TOP:
            report["ignored"].append(key)
        else:
            report["unknown"].append(key)

    m = obs.get("map")
    if isinstance(m, dict):
        for key in m:
            if key in ENCODED_MAP:
                report["encoded"].append("map." + key)
            elif key in IGNORED_MAP:
                report["ignored"].append("map." + key)
            else:
                report["unknown"].append("map." + key)

        for kind, rows in (("npc", m.get("npcs")), ("interactive", m.get("interactives")),
                           ("group", m.get("groups")), ("exit", m.get("exits")),
                           ("door", m.get("doors"))):
            for row in _dicts(rows):
                for key in row:
                    if key in ENCODED_ENTITY[kind]:
                        continue
                    if key in IGNORED_ENTITY[kind]:
                        continue
                    report["unknown_entity"].append("%s.%s" % (kind, key))

    encoded = encode(from_observation(obs))
    report["dropped"] = {k: v for k, v in encoded["dropped"].items() if v}
    return report


def audit(observations: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Fold `coverage` over a corpus. Lists become sorted key sets with counts."""
    unknown: Dict[str, int] = {}
    unknown_entity: Dict[str, int] = {}
    dropped: Dict[str, int] = {}
    seen = 0
    with_map = 0
    for obs in observations:
        report = coverage(obs)
        seen += 1
        if isinstance(obs, dict) and isinstance(obs.get("map"), dict):
            with_map += 1
        for key in report["unknown"]:
            unknown[key] = unknown.get(key, 0) + 1
        for key in report["unknown_entity"]:
            unknown_entity[key] = unknown_entity.get(key, 0) + 1
        for kind, count in report["dropped"].items():
            dropped[kind] = dropped.get(kind, 0) + count
    return {"observations": seen, "with_map": with_map,
            "unknown": dict(sorted(unknown.items(), key=lambda kv: -kv[1])),
            "unknown_entity": dict(sorted(unknown_entity.items(), key=lambda kv: -kv[1])),
            "dropped": dict(sorted(dropped.items())),
            "schema_version": SCHEMA_VERSION}


# ---------------------------------------------------------------------------
# cli


def iter_observations(paths: Sequence[str]) -> Iterator[Dict[str, Any]]:
    for path in paths:
        try:
            handle = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    obs = record.get("observation")
                    if isinstance(obs, dict):
                        yield obs


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--coverage", action="store_true",
                        help="audit the corpus against the closed schema")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show", type=int, default=0,
                        help="print the encoded blocks for the first N observations")
    args = parser.parse_args(argv)

    if not args.paths:
        parser.error("give at least one trajectory file")

    if args.coverage:
        report = audit(iter_observations(args.paths))
        if args.json:
            # The report can carry a client field name, so the console has to be
            # UTF-8 before anything is printed.
            utf8_console()
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return 0
        print("observations        %d (%d with a map block)"
              % (report["observations"], report["with_map"]))
        print("schema version      %d" % report["schema_version"])
        print("unknown top-level   %s"
              % (", ".join("%s=%d" % kv for kv in report["unknown"].items()) or "none"))
        print("unknown entity      %s"
              % (", ".join("%s=%d" % kv for kv in report["unknown_entity"].items()) or "none"))
        print("dropped by slots    %s"
              % (", ".join("%s=%d" % kv for kv in report["dropped"].items()) or "none"))
        return 0

    for index, obs in enumerate(iter_observations(args.paths)):
        if args.show and index >= args.show:
            break
        state = from_observation(obs)
        print(json.dumps(state.to_dict(), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
