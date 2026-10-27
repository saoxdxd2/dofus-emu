"""The one featuriser. Training and serving must not be able to disagree.

There were two copies of this before — one in `ai/train_planner_student.py`, one
implied by `agent/planner_nn.py` — and they drifted: the corpus was built with 26
numbers, the runtime fed 26 numbers, and the action head still had five outputs while
the registry had eighteen. Every one of those mismatches is silent at training time
and fatal at serving time.

So: one module, one length, one tool order. The tool list is read from
`data/capabilities.json` rather than written down here, because a second copy of the
action space is the same bug waiting for a later date.

Why these features: they are exactly what a policy may legitimately see at decision
time — the objective, the tracker, the state, and the recent history of its own
attempts. Nothing hidden (no quest table lookahead, no ground truth about whether the
action will work). A feature the agent could not have known in the game is a leak,
and a leak makes a validation score meaningless.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.jsonio import loads as fast_loads

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "data" / "capabilities.json"

OBJECTIVE_TYPES = [0, 1, 2, 3, 4, 5, 6, 9, 10, 12, 14, 16, 17]

# Objective type -> what it needs, decoded from the objective text itself:
#   0  "Locate and neutralise X"          monster
#   1  "Talk to {npc,N}"                  npc
#   2  "Show 1 {item,I} to {npc,N}"       item + npc
#   3  "Take 1 {item,I} to {npc,N}"       item + npc
#   4  "Find the map: X"                  map
#   5  "Find the {subarea,S} area"        area
#   6  "Defeat 1 {monster,M} in a fight"  monster
#   9  "Talk to {npc,N} again"            npc
#   12 "Take 1 {monster,M} soul to {npc}" monster + npc
#   14 "Beat x3 {monster,M}"              monster xN
#   16 "Defeat 8 x {monster} on {map}"    monster xN + map
#   17 "Craft 1 {item,I}"                 item
#
# These live with `objective_type` and `required_map` because they are the same
# concern: how an objective's parameters are read. The planner and the simulator each
# used to carry a private copy, and a bucket that means one thing to the teacher and
# another to the world is a corpus that teaches a rule the game does not have.
TALK_TYPES = {1, 9}
MAP_TYPES = {4}
AREA_TYPES = {5}
KILL_TYPES = {0, 6, 14, 16}
DELIVER_TYPES = {2, 3, 12}
CRAFT_TYPES = {17}

# Type 6 says "defeat X *in a fight*"; the open-world types do not. Keeping them
# apart is what gives fight.join a job that farm.start cannot do, and a tool that
# cannot be told from another tool is not a decision, it is noise in the label space.
FIGHT_TYPES = {6}
OPEN_KILL_TYPES = KILL_TYPES - FIGHT_TYPES

# What each objective type's parameters *are*, copied from the client's own
# objective-parameter table (`desktop/client/www/build/script.js`), keyed by the class
# name the page reports as `type`. The table is what the quest UI walks to turn a
# parameter into something a player can see, so it is the only authority on whether
# `parameter0` names a creature, a place, an item or nothing at all:
#
#   Monsters + $monster  -> a creature the map can show a group for
#   Npcs / Items / ...   -> that table's id
#   GetText              -> a number the client only formats into the objective text
#
# `agent/regression.py` re-reads the bundle and fails if this copy and the client's
# disagree, so the table cannot rot in silence.
#
# Why it exists: `KILL_TYPES` reads 0 as "monster" because the type->meaning map above
# was decoded from objective *text*, and the live corpus says otherwise — 18,654
# entries of `QuestObjectiveFreeForm` (type 0) carry a six-digit `parameter0`
# (715354, 713802, 719010, 698229) that matches no monster, item, npc or quest in any
# cache, while every real `QuestObjectiveFightMonster` (type 6) carries a four-digit
# one (4194, 4192). Handing the wrapper's number to `fight.join` is where the recorded
# "target monster 715354 is not on this map" refusals come from.
OBJECTIVE_PARAMETERS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "QuestObjectiveBringItemToNpc": (("table", "Npcs"), ("table", "Items")),
    "QuestObjectiveBringSoulToNpc": (("table", "Npcs"), ("table", "Monsters")),
    "QuestObjectiveCraftXItem": (("table", "Items"),),
    "QuestObjectiveDiscoverMap": (("getText", "GetText"),),
    "QuestObjectiveDiscoverSubArea": (("table", "SubAreas"),),
    "QuestObjectiveDuelSpecificPlayer": (("getText", "GetText"),),
    "QuestObjectiveFightMonster": (("table", "Monsters"),),
    "QuestObjectiveFightMonstersOnMap": (("table", "Monsters"),),
    "QuestObjectiveFreeForm": (("getText", "GetText"),),
    "QuestObjectiveGoToNpc": (("table", "Npcs"),),
    "QuestObjectiveMultiFightMonster": (("table", "Monsters"),),
    "QuestObjectiveValidChallenge": (("table", "Monsters"),),
    "QuestObjectiveWinMonsterInDungeon": (("table", "Monsters"),),
    "QuestObjectiveWinMonsterInDungeonValidChallenge": (("table", "Monsters"),
                                                        ("table", "Challenge")),
    "QuestObjectiveWinMonsterInDungeonXTimes": (("table", "Monsters"),),
    "QuestObjectiveWinPvpAgainstBreed": (("table", "Breeds"),),
    "QuestObjective": (("getText", "GetText"),),
}

MONSTER_PARAMETER_TYPES = frozenset(
    name for name, params in OBJECTIVE_PARAMETERS.items()
    if any(pid == "Monsters" for _kind, pid in params))


def declares_monster_target(obj: Dict[str, Any]) -> Optional[bool]:
    """Whether the client says this objective's parameter is a creature.

    `None` means the observation carried no class name, and the caller must keep the
    `type_id` route it has always used rather than silently lose the ability to plan a
    fight. An objective whose class the client declares as text-only is not a hunt, and
    no amount of re-reading `groups_seen` will make one.
    """
    name = obj.get("type") if isinstance(obj, dict) else None
    if isinstance(name, str) and name in OBJECTIVE_PARAMETERS:
        return name in MONSTER_PARAMETER_TYPES
    return None

# The error taxonomy, reusing the controller's classifier so a new marker cannot be
# added in one place and forgotten in the other.
ERROR_SLOTS = ["none", "structural", "transport", "action", "other"]


def tool_registry() -> List[str]:
    """The 18 tools, in registry order. This order *is* the label encoding."""
    with REGISTRY.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    tools = [c["id"] for c in payload.get("capabilities") or [] if c.get("id")]
    if not tools:
        raise RuntimeError("capability registry is empty: %s" % REGISTRY)
    return tools


TOOLS: List[str] = tool_registry()
TOOL_INDEX: Dict[str, int] = {name: i for i, name in enumerate(TOOLS)}


def _params(obj: Dict[str, Any]) -> Dict[str, Any]:
    raw = obj.get("params")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw.replace("'", '"'))
        except ValueError:
            return {}
    return {}


def objective_type(obj: Dict[str, Any]) -> int:
    """`type_id` as an int, with 0 preserved.

    `int(obj.get("type_id") or -1)` looks harmless and is not: type 0 is falsy, and
    type 0 is the most common objective in the KB — 5,669 open-world kills. Every
    corpus this project built encoded them as "unknown", which is why a model could
    not tell a kill from an objective type it had never seen. One helper, used
    everywhere, because the same `or` was copy-pasted into eight files.
    """
    t = obj.get("type_id")
    if t is None:
        return -1
    try:
        return int(t)
    except (TypeError, ValueError):
        return -1


_MAP_INDEX: Optional[Tuple[Dict[int, int], Dict[int, int]]] = None


def map_index() -> Tuple[Dict[int, int], Dict[int, int]]:
    """map id -> (subarea id, area id), built once from the knowledge base.

    Lives here rather than in the simulator because the planner needs it too: an agent
    that cannot tell which objective is closer cannot choose an order, and the order is
    worth 22% of its travel.
    """
    global _MAP_INDEX
    if _MAP_INDEX is None:
        sub_of: Dict[int, int] = {}
        area_of: Dict[int, int] = {}
        path = REPO_ROOT / "data" / "cache" / "subareas_full.json"
        if path.exists():
            for sub in fast_loads(path.read_bytes()):
                    try:
                        sid = int(sub["id"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    for m in sub.get("mapIds") or []:
                        try:
                            mid = int(m)
                        except (TypeError, ValueError):
                            continue
                        sub_of[mid] = sid
                        area_of[mid] = sub.get("areaId")
        _MAP_INDEX = (sub_of, area_of)
    return _MAP_INDEX


def travel_hops(here: Optional[int], there: Optional[int]) -> int:
    """Steps to get from one map to another: same 0, subarea 1, area 2, else 3.

    Coarse on purpose. A hop count is not a path, and it does not need to be: what it
    has to be is *monotone in distance*, because that is the property the simulator
    lacked when travel was free — there, the cost of any plan was just the number of
    actions in it, so the order of those actions could not matter.
    """
    if here is None or there is None:
        return 1
    try:
        here, there = int(here), int(there)
    except (TypeError, ValueError):
        return 1
    if here == there:
        return 0
    sub_of, area_of = map_index()
    a, b = sub_of.get(here), sub_of.get(there)
    if a is not None and a == b:
        return 1
    aa, ab = area_of.get(here), area_of.get(there)
    if aa is not None and aa == ab:
        return 2
    return 3


def required_map(obj: Dict[str, Any]) -> Optional[int]:
    """The map an objective has to be done on, or None. `map_id: 0` means no map.

    A DiscoverMap objective's `parameter0` is the *text id of the map's name*,
    not a map id: in the KB all 765 type-4 objectives with a destination carry it
    in `map_id`, and the 108 without one have `parameter0` values that appear in
    no map table. The live tutorial objective 13541 held 698953 there, and
    reading it as a destination made the client answer "map 698953 is not
    adjacent" nine times in a row. Only `map_id` names a place.
    """
    m = obj.get("map_id")
    try:
        m = int(m) if m is not None else None
    except (TypeError, ValueError):
        return None
    return m if m else None


# -- map index ---------------------------------------------------------------

_KNOWN_MAPS: Optional[set] = None


def known_map(map_id: Any) -> bool:
    """Whether `data/maps/_index.json` has this map, so a route can be planned.

    This is the routing graph, not a whitelist of quest targets: it is used only
    to decide whether a *destination map id* is something the agent can reason
    about. An objective whose id is not in the graph is not assumed invalid -- it
    may be a newer map -- it is treated as a detail-only objective and handed to
    the policy's edge/cell actions instead of a made-up transition.
    """
    global _KNOWN_MAPS
    try:
        mid = int(map_id)
    except (TypeError, ValueError):
        return False
    if _KNOWN_MAPS is None:
        ids: set = set()
        path = REPO_ROOT / "data" / "maps" / "_index.json"
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    for key in raw:
                        try:
                            ids.add(int(key))
                        except (TypeError, ValueError):
                            continue
                elif isinstance(raw, list):
                    for entry in raw:
                        if isinstance(entry, dict) and entry.get("id") is not None:
                            try:
                                ids.add(int(entry["id"]))
                            except (TypeError, ValueError):
                                continue
            except (OSError, ValueError):
                ids = set()
        _KNOWN_MAPS = ids
    return mid in _KNOWN_MAPS


# -- objective details -------------------------------------------------------
#
# A map objective can carry a bogus or missing destination (the tutorial's
# "click the right edge" names a map id that is not in any table). Its own text
# still says what to do. These read that text in four languages -- they do not
# know any quest id, so the same rule applies to every objective with the same
# instruction.

_DIRECTIONS = (
    ("right", r"\b(?:droite?|right|derecha|rechts)\b"),
    ("left", r"\b(?:gauche|left|izquierda|links)\b"),
    ("top", r"\b(?:haut|top|up|arriba|oben)\b"),
    ("bottom", r"\b(?:bas|bottom|down|abajo|unten)\b"),
)
_EDGE_WORDS = r"(?:rebord|bord|borde|orilla|edge|border|rand|seite)"
_CELL_RE = re.compile(
    r"\b(?:cell(?:ule)?|case|casilla)\s*(?:n[°o.\s]*)?(\d{1,4})\b", re.IGNORECASE)


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def objective_text(obj: Dict[str, Any]) -> str:
    text = obj.get("text")
    return text if isinstance(text, str) else ""


def direction_hint(obj: Dict[str, Any]) -> Optional[str]:
    """A screen-edge direction named by the objective's own text, if any.

    The edge-word test comes first so "the right item" is not read as an edge;
    the single-direction fallback handles an instruction like "go right".
    """
    text = _fold(objective_text(obj))
    if not text:
        return None
    for direction, pattern in _DIRECTIONS:
        if re.search(_EDGE_WORDS + r"\s+(?:de\s+|du\s+|la\s+|le\s+|the\s+)?" + pattern,
                     text):
            return direction
        if re.search(pattern + r"\s+(?:de\s+l['\u2019]|of\s+the\s+)?" + _EDGE_WORDS,
                     text):
            return direction
    hits = [direction for direction, pattern in _DIRECTIONS if re.search(pattern, text)]
    return hits[0] if len(hits) == 1 else None


def cell_hint(obj: Dict[str, Any]) -> Optional[int]:
    """A cell number named by the objective's own text, if it is a map cell."""
    match = _CELL_RE.search(_fold(objective_text(obj)))
    if not match:
        return None
    try:
        cell = int(match.group(1))
    except (TypeError, ValueError):
        return None
    return cell if 0 <= cell <= 559 else None


def _err_slot(note: Optional[str]) -> str:
    if not note:
        return "none"
    try:
        from agent.controller import error_class
        cls = error_class(note)
    except Exception:  # noqa: BLE001
        cls = "other"
    return cls if cls in ERROR_SLOTS else "other"


def objective_features(obj: Dict[str, Any], quest_level: int, step_index: int,
                       state: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> List[float]:
    """FEATURE_COUNT numbers, in FEATURE_NAMES order. A corpus written at a different width is a
    different model, so the count is never quoted as a literal here."""
    ctx = ctx or {}
    p = _params(obj)
    t = objective_type(obj)
    vec: List[float] = []

    for known in OBJECTIVE_TYPES:
        vec.append(1.0 if t == known else 0.0)
    vec.append(1.0 if t not in OBJECTIVE_TYPES else 0.0)

    vec.append(1.0 if p.get("parameter0") is not None else 0.0)
    vec.append(1.0 if p.get("parameter1") is not None else 0.0)
    vec.append(min(1.0, len(p) / 3.0))
    vec.append(1.0 if obj.get("map_id") else 0.0)
    vec.append(1.0 if obj.get("coords") else 0.0)

    vec.append(min(1.0, quest_level / 200.0))
    vec.append(min(1.0, step_index / 8.0))

    inv = state.get("inventory") or {}
    # The item this objective needs, and whether it is already carried. Missing this
    # pair is what made the first student gather forever and score zero.
    needed = None
    if t in (2, 3, 12):
        needed = p.get("parameter1") if t == 3 else p.get("parameter0")
    has_needed = 0.0
    if needed is not None:
        has_needed = 1.0 if (inv.get(needed) or inv.get(str(needed))) else 0.0

    target_map = required_map(obj)
    on_target = 0.0
    if target_map and state.get("map_id"):
        on_target = 1.0 if int(state["map_id"]) == int(target_map) else 0.0

    vec.append(min(1.0, len(inv) / 20.0))
    vec.append(has_needed)
    vec.append(on_target)
    # How far the objective is, not just whether we are on it. Once travel costs
    # something, "travel now" and "act now" have different prices depending on
    # distance, and a 0/1 on_target cannot tell one hop from three areas away — so a
    # model without this could not learn when travelling is worth it, and any negative
    # result about planning would be confounded by the missing input.
    vec.append(min(1.0, travel_hops(state.get("map_id"), target_map) / 3.0))
    vec.append(1.0 if state.get("map_id") else 0.0)
    vec.append(min(1.0, (state.get("level") or 1) / 200.0))

    vec.append(min(1.0, (state.get("objectives_completed") or 0) / 50.0))
    vec.append(min(1.0, (state.get("quests_completed") or 0) / 20.0))
    vec.append(min(1.0, len(state.get("quests_active") or {}) / 5.0))

    # History of its own attempts: the only way to learn "this keeps failing" is to
    # be told that it keeps failing.
    streak = float(ctx.get("streak") or 0)
    last_ok = ctx.get("last_ok")
    vec.append(min(1.0, streak / 5.0))
    vec.append(1.0 if last_ok is None else (1.0 if last_ok else 0.0))
    last_tool = ctx.get("last_tool")
    vec.append((TOOL_INDEX.get(last_tool, 0) / 17.0) if last_tool else 0.0)
    slot = _err_slot(ctx.get("last_note"))
    for name in ERROR_SLOTS:
        vec.append(1.0 if slot == name else 0.0)

    kind = ctx.get("obs_kind") or "fresh"
    vec.append(1.0 if kind == "stale" else 0.0)
    vec.append(1.0 if kind == "partial" else 0.0)

    capacity = int(state.get("inventory_capacity") or 0)
    vec.append(1.0 if capacity and len(inv) >= capacity else 0.0)
    vec.append(min(1.0, (state.get("kamas") or 0) / 10000.0))
    return vec


FEATURE_NAMES: List[str] = (
    ["type_%d" % t for t in OBJECTIVE_TYPES] + ["type_other"]
    + ["has_param0", "has_param1", "n_params", "obj_map", "obj_coords"]
    + ["quest_level", "step_index"]
    + ["inv_stacks", "has_needed", "on_target", "hops_to_target", "map_known", "level"]
    + ["objectives_done", "quests_done", "active_count"]
    + ["fail_streak", "last_ok", "last_tool"]
    + ["err_%s" % s for s in ERROR_SLOTS]
    + ["obs_stale", "obs_partial"]
    + ["inv_full", "kamas"]
)

FEATURE_COUNT = len(FEATURE_NAMES)


def self_check() -> None:
    """Fail loudly if the vector and the names ever disagree."""
    probe = objective_features({"type_id": 1, "params": "{}"}, 60, 0,
                               {"inventory": {}, "map_id": None, "level": 60}, {})
    if len(probe) != FEATURE_COUNT:
        raise RuntimeError("features are %d wide, names say %d" % (len(probe), FEATURE_COUNT))


class ObsTracker:
    """Turns the observation stream into the history features, in one place.

    The simulator knows whether an observation is stale; the agent does not. If the
    corpus used the simulator's knowledge and the runtime used a guess, the model
    would be trained on a signal it can never see again — the classic silent skew.
    So both sides run *this* tracker, and its guesses are the model's only evidence.

    Stale is inferred, not known: an observation identical to the previous one, on a
    field that should have changed, is what a lagged client looks like. That is a
    heuristic, and it is stated as one.
    """

    def __init__(self) -> None:
        self.prev: Optional[Dict[str, Any]] = None
        self.prev_objectives: Optional[int] = None
        self.streak = 0
        self.last_ok: Optional[bool] = None
        self.last_tool: Optional[str] = None
        self.last_note: Optional[str] = None
        self.kind = "fresh"

    def observe(self, obs: Dict[str, Any]) -> str:
        kind = "fresh"
        if self.prev is not None:
            same_position = obs.get("map_id") == self.prev.get("map_id")
            same_progress = obs.get("objectives_completed") == self.prev.get("objectives_completed")
            same_active = obs.get("quests_active") == self.prev.get("quests_active")
            if same_position and same_progress and same_active:
                kind = "stale"
            elif not (obs.get("inventory") or {}) and (self.prev.get("inventory") or {}):
                kind = "partial"
        self.prev = dict(obs)
        self.kind = kind
        return kind

    def record(self, tool: str, ok: bool, note: str, obs: Dict[str, Any]) -> None:
        self.last_tool = tool
        self.last_ok = ok
        self.last_note = note
        self.streak = 0 if ok else self.streak + 1
        self.prev_objectives = obs.get("objectives_completed")

    def ctx(self) -> Dict[str, Any]:
        return {"streak": self.streak, "last_ok": self.last_ok,
                "last_tool": self.last_tool, "last_note": self.last_note,
                "obs_kind": self.kind}


self_check()
