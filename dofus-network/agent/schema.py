"""Canonical tool schema — the text-to-action contract.

Every ability of the agent is one tool call: ``{"tool": name, "args": {...}}``.
The schema is intentionally small and closed-vocabulary so a 100M model can
learn it exactly and a grammar-constrained decoder can make malformed calls
impossible later. Training data, the recorder and the executor all reference
these names — change a name here and every layer follows.

Backends:

* ``local``     — answered from ``data/cache/*`` (no game needed).
* ``sentinel``  — HTTP call to the Sentinel service (``127.0.0.1:8890``).
* ``directive`` — emitted to the client bridge as a commander directive; the
                  in-client JS (``mobile-disguise.js``) performs it and reports
                  the result back.

Parameter ``type`` values are the training-friendly set: ``str``, ``int``,
``float``, ``bool``, ``str_list``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
CAPABILITIES_FILE = REPO_ROOT / "data" / "capabilities.json"

# Languages the models are expected to speak (speech, not game locale).
SPOKEN_LANGUAGES = ("en", "fr", "es", "de")


@dataclass
class ToolSpec:
    name: str
    description: str
    params: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    backend: str = "local"
    directive: Optional[str] = None
    returns: str = ""

    def required_params(self) -> List[str]:
        return [key for key, spec in self.params.items() if spec.get("required")]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "backend": self.backend,
            "directive": self.directive,
            "returns": self.returns,
            "params": self.params,
        }


def _p(type_name: str, doc: str, required: bool = False,
       enum: Optional[List[str]] = None, default: Any = None) -> Dict[str, Any]:
    spec: Dict[str, Any] = {"type": type_name, "doc": doc, "required": required}
    if enum is not None:
        spec["enum"] = enum
    if default is not None:
        spec["default"] = default
    return spec


_BUILTIN_TOOLS: List[ToolSpec] = [
    # -- knowledge -----------------------------------------------------------
    ToolSpec(
        name="knowledge.search",
        description="Look up game knowledge (monsters, spells, quests, items, NPCs, "
                    "achievements, dungeons) in the local DofusDB cache. Use before "
                    "claiming any game fact.",
        params={
            "query": _p("str", "Free-text name or keyword, any of the 4 languages.", required=True),
            "kind": _p("str", "Entity kind to search, or 'any'.", enum=[
                "any", "monster", "spell", "quest", "item", "npc", "achievement", "dungeon"],
                default="any"),
            "limit": _p("int", "Max results.", default=5),
        },
        backend="local",
        returns="list of compact records with 'id', 'name' (localised dict) and key fields",
    ),
    ToolSpec(
        name="knowledge.remember",
        description="Record something you discovered that the cache does not know "
                    "(an NPC's map, a drop, a price, a quest prerequisite). Facts are "
                    "stored with provenance, so a wrong one can be corrected or "
                    "forgotten later. Never use this to copy the cache.",
        params={
            "kind": _p("str", "Fact kind.", required=True,
                       enum=["npc_map", "monster_drop", "item_source", "price",
                             "quest_prereq", "route", "other"]),
            "key": _p("str", "Stable key, e.g. 'npc:447' or 'item:1746'.", required=True),
            "value": _p("str", "The fact as text or JSON, e.g. '12345'.", required=True),
            "confidence": _p("float", "0-1. Use 0.9 for something you saw, 0.5 for an inference.",
                             default=0.5),
        },
        backend="local",
        returns="{'changed': bool, 'note': str}",
    ),
    ToolSpec(
        name="knowledge.forget",
        description="Delete a fact that turned out to be wrong. Use it instead of "
                    "remembering a contradicting value, so the store stays small.",
        params={
            "kind": _p("str", "Fact kind.", required=True),
            "key": _p("str", "Key of the fact to remove.", required=True),
            "reason": _p("str", "Why it is wrong (kept in the log).", default=""),
        },
        backend="local",
        returns="{'removed': bool}",
    ),
    # -- navigation ----------------------------------------------------------
    ToolSpec(
        name="nav.goto",
        description="Travel to a target: [x,y] coordinates, a zaap name, an NPC, a "
                    "map id, a cell (a specific spot on this map), or a direction "
                    "(leave by that edge).",
        params={
            "x": _p("int", "Target X coordinate."),
            "y": _p("int", "Target Y coordinate."),
            "zaap": _p("str", "Zaap name, any language."),
            "npc": _p("str", "NPC name to travel to."),
            "map_id": _p("int", "Raw map id."),
            "cell": _p("int", "Walk to this cell on the current map."),
            "direction": _p("str", "Leave by this edge of the current map.",
                            enum=["left", "right", "top", "bottom"]),
            "npc_id": _p("int", "Walk to this NPC's cell, by template id."),
            "lang": _p("str", "Reply language.", enum=["fr", "en", "es", "de"], default="fr"),
            "from_x": _p("int", "Current X (default 4)."),
            "from_y": _p("int", "Current Y (default -19)."),
        },
        backend="sentinel",
        returns="navigation path with steps; the client bridge then walks it",
    ),
    ToolSpec(
        name="nav.follow",
        description="Follow a character on the current map and through map changes. "
                    "Empty name means 'follow the order giver'.",
        params={"name": _p("str", "Character name to follow; empty = the order giver.", default="")},
        backend="directive",
        directive="FOLLOW",
        returns="directive ack",
    ),
    # -- quests --------------------------------------------------------------
    ToolSpec(
        name="quest.list",
        description="List quests suitable for a level (and optionally an area), with "
                    "start position and step objectives.",
        params={
            "area": _p("str", "Area or subarea name filter, any language (e.g. 'astrub')."),
            "level_min": _p("int", "Player level lower bound."),
            "level_max": _p("int", "Player level upper bound."),
            "dungeon_only": _p("bool", "Only dungeon quests.", default=False),
            "limit": _p("int", "Max results.", default=10),
        },
        backend="local",
        returns="list of quests with id, localised name, level range and step objectives",
    ),
    ToolSpec(
        name="quest.advance",
        description="Execute the next actionable step of a quest: navigate, talk to the "
                    "NPC, harvest, or fight as the step objective requires.",
        params={
            "quest_id": _p("int", "Quest id from quest.list.", required=True),
            # These three are read by the client handler and sent by the planner. They
            # were missing from this contract while the code used them, so the validator
            # rejected every talk and deliver call ("unknown argument(s) for
            # quest.advance: npc_id") and every ready-objective claim — the call never
            # reached the game. A contract that under-declares its own arguments blocks
            # the very thing it is meant to protect.
            "npc_id": _p("int", "Talk to this NPC, by the template id the objective carries."),
            "objective_id": _p("int", "Validate this objective. Use it to claim one the "
                                      "game already accepts (objectiveStatus 2)."),
            "reply": _p("int", "Which dialog reply to choose, by index. Defaults to 0, "
                               "which is what an unanswered NPC dialog needs."),
            "reply_text": _p("str", "Which dialog reply to choose, by the exact French text "
                                    "reported in dialog.replies. With reply, both must agree."),
        },
        backend="directive",
        directive="QUEST_ADVANCE",
        returns="per-step progress report",
    ),
    # -- farming / combat ----------------------------------------------------
    ToolSpec(
        name="farm.start",
        description="Start a farm loop: kill monster groups and/or harvest resources, "
                    "looting according to the filter, optionally auto-selling.",
        params={
            # `monster` was declared as a string name while the planner derives an int
            # id from the objective and the client does parseInt() on it. Every kill
            # objective was rejected with "farm.start.monster: expected a string" — 36.5%
            # of the knowledge base, dead at the contract. `id_or_name` accepts both
            # vocabularies: the planner's id and the encoder's name.
            "monster": _p("id_or_name", "Monster id from the objective, or a name from "
                                        "the corpus. The client needs an id; the harness "
                                        "resolves a name."),
            "resource": _p("id_or_name", "Resource id from the objective, or a name. The "
                                         "client harvests the nearest interactive element."),
            "zone": _p("str", "Area/subarea to stay inside, any language."),
            "map_id": _p("int", "Farm on one specific map."),
            "kill_count": _p("int", "Stop after this many kills.", default=100),
            "duration_min": _p("int", "Stop after this many minutes."),
            "loot_filter": _p("str_list", "Item names to keep; others can be dropped."),
            "auto_sell": _p("bool", "Auto-list kept loot on the market when bags fill.", default=False),
        },
        backend="directive",
        directive="FARM_START",
        returns="farm session id and live counters",
    ),
    ToolSpec(
        name="farm.stop",
        description="Stop the active farm loop and return to a safe idle state.",
        params={},
        backend="directive",
        directive="FARM_STOP",
        returns="final farm counters",
    ),
    ToolSpec(
        name="fight.join",
        description="Join a fight: a monster group containing the named monster, an "
                    "explicit group, or the nearest group.",
        params={
            # `target` was declared while the planner sent `monster` and the client read
            # `group_id` — three names with no overlap, so every fight objective was
            # rejected by the validator before it reached the game.
            "monster": _p("int", "Monster id from the objective. The group containing it "
                                 "is chosen; the client knows which group that is."),
            "group_id": _p("int", "Explicit monster-group id, when the caller has one."),
        },
        backend="directive",
        directive="AUTO_JOIN_COMBAT",
        returns="fight state",
    ),
    ToolSpec(
        name="fight.pass",
        description="Pass the current combat turn.",
        params={},
        backend="directive",
        directive="PASS_TURN",
        returns="ack",
    ),
    ToolSpec(
        name="fight.state",
        description="Read the live combat board: turn owner, fighters, cells, HP/AP/MP, "
                    "team membership, and the controlled character's spell descriptors. "
                    "The result also carries a versioned observation and a contract "
                    "naming any fact the client did not expose.",
        params={},
        backend="directive",
        directive="FIGHT_STATE",
        returns="turn, fighter and spell facts from the live client, plus observation/contract",
    ),
    ToolSpec(
        name="fight.plan",
        description="Read the live board and compute this turn's legal move-and-cast plan: "
                    "exact cell geometry, line of sight, AP/MP, cooldowns, resistances, "
                    "damage and healing. top_k > 1 also returns alternative plans "
                    "(including pass) for a ranker. Read-only; it never casts or moves anything. "
                    "Every answer carries the live observation and its contract.",
        params={
            "beam_width": _p("int", "How many partial turns to keep while searching.",
                             default=16),
            "max_actions": _p("int", "Maximum casts to consider in the plan.",
                              default=4),
            "top_k": _p("int", "Also return this many candidate plans; 1 = best only.",
                        default=1),
            "rank_with_value": _p(
                "bool", "Compatibility alias for ranker=value: rank candidates with "
                        "the learned encounter value head.",
                default=False),
            "ranker": _p(
                "str", "Which plan ranker to use. 'solver' keeps the solver order; "
                        "'value' ranks candidates with the learned value head and "
                        "refuses when the live observation lacks self stats or enemy "
                        "spell facts.",
                enum=["solver", "value"], default="solver"),
        },
        backend="local",
        returns=("actions with cells/targets/AP/MP/damage, plus the normalized live "
                 "observation, its contract, and the ranker decision"),
    ),
    ToolSpec(
        name="fight.estimate",
        description="Estimate whether the controlled character can win a monster group "
                    "visible on this map: turns to kill, incoming damage, expected HP "
                    "loss, and a calibrated win probability. Read-only; it never joins "
                    "or casts.",
        params={
            "group_id": _p("int", "Monster group id from map.groups."),
            "monster": _p("id_or_name", "Monster id or name. When several visible "
                                        "groups contain it, the least favourable one "
                                        "is estimated."),
        },
        backend="local",
        returns=("p_win (null until calibrated), raw_score, expected_hp_loss and turns, "
                 "confidence, missing inputs, allowed and blocked_reason"),
    ),
    # -- inventory / market --------------------------------------------------
    ToolSpec(
        name="inv.list",
        description="List inventory contents, optionally filtered by kind.",
        params={
            "kind": _p("str", "Inventory slice.", enum=[
                "all", "resources", "equipment", "consumables", "quest_items"], default="all"),
        },
        backend="directive",
        directive="INV_LIST",
        returns="items with id, name, quantity, unit weight",
    ),
    ToolSpec(
        name="inv.sell",
        description="Sell inventory items on the market (HDV) at a price rule. Default "
                    "rule is median market price minus 1 kama.",
        params={
            "item": _p("str", "Item name; omit when all=true."),
            "all": _p("bool", "Consider the whole inventory.", default=False),
            "quantity": _p("int", "Units to sell; default: all owned (respecting min_stock)."),
            "min_stock": _p("int", "Keep at least this many units.", default=1),
            "price_rule": _p("str", "Pricing rule.", enum=["median_minus_1", "undercut", "fixed"],
                             default="median_minus_1"),
            "price": _p("int", "Price per unit when price_rule=fixed."),
        },
        backend="directive",
        directive="INV_SELL",
        returns="per-item listing report (price used, quantity, market depth)",
    ),
    ToolSpec(
        name="hdv.scan",
        description="Scan the market (HDV) for an item and return price statistics "
                    "(min/median/max over current listings).",
        params={
            # The client reads all six; only `item` was declared, so any caller that
            # wanted to browse by category or type was rejected at the contract.
            "item": _p("str", "Item name to look up."),
            "category": _p("int", "Market category id, when browsing rather than searching."),
            "type": _p("int", "Item type id filter."),
            "item_type": _p("int", "Alias of type."),
            "id": _p("int", "Item id filter."),
            "gid": _p("int", "Item group id filter."),
        },
        backend="directive",
        directive="HDV_SCAN",
        returns="price stats and listing count",
    ),
    ToolSpec(
        name="hdv.list",
        description="List an item for sale on the market at an explicit or rule-based price.",
        params={
            "item": _p("str", "Item name, any language.", required=True),
            "quantity": _p("int", "Units to list.", default=1),
            "price_rule": _p("str", "Pricing rule.", enum=["median_minus_1", "undercut", "fixed"],
                             default="median_minus_1"),
            "price": _p("int", "Price per unit when price_rule=fixed."),
        },
        backend="directive",
        directive="HDV_LIST",
        returns="listing confirmations",
    ),
    # -- chat / state / safety ----------------------------------------------
    ToolSpec(
        name="chat.say",
        description="Send a chat message.",
        params={
            "text": _p("str", "Message text.", required=True),
            "channel": _p("str", "Chat channel.", enum=["say", "team", "guild", "whisper"],
                          default="say"),
            "target": _p("str", "Recipient for whisper."),
        },
        backend="directive",
        directive="CHAT_SAY",
        returns="ack",
    ),
    ToolSpec(
        name="state.snapshot",
        description="Request a structured snapshot of current game state (position, "
                    "map, HP, inventory summary, active fight).",
        params={},
        backend="directive",
        directive="STATE_SNAPSHOT",
        returns="observation dict recorded into the trajectory log",
    ),
    ToolSpec(
        name="safe.stop",
        description="Emergency stop: halt farm/fight loops, stop acting, log out of "
                    "dangerous situations. Always available.",
        params={"reason": _p("str", "Why the stop was triggered.")},
        backend="directive",
        directive="SAFE_STOP",
        returns="ack",
    ),
]

# ---------------------------------------------------------------------------
# capability registry — actions are DATA, not code
# ---------------------------------------------------------------------------
#
# The list above is only a fallback. At runtime the action space comes from
# ``data/capabilities.json``, and the encoder's action head is derived from the
# capability *descriptions* (see ``ai/encoder_policy.py`` anchor mode). Adding a
# capability therefore means appending an entry and re-encoding its description —
# no new class id, no retraining, no code change here.


def _load_capabilities(path: Path = CAPABILITIES_FILE) -> Optional[List[ToolSpec]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    specs: List[ToolSpec] = []
    for entry in payload.get("capabilities") or []:
        name = str(entry.get("id") or "").strip()
        if not name:
            continue
        params: Dict[str, Dict[str, Any]] = {}
        for key, raw in (entry.get("params") or {}).items():
            if not isinstance(raw, dict):
                continue
            spec = {
                "type": str(raw.get("type") or "str"),
                "doc": str(raw.get("doc") or ""),
                "required": bool(raw.get("required")),
            }
            if raw.get("enum"):
                spec["enum"] = list(raw["enum"])
            if raw.get("default") is not None:
                spec["default"] = raw["default"]
            params[key] = spec
        specs.append(ToolSpec(
            name=name,
            description=str(entry.get("description") or ""),
            params=params,
            backend=str(entry.get("backend") or "local"),
            directive=entry.get("directive"),
            returns=str(entry.get("returns") or ""),
        ))
    return specs or None


_registry = _load_capabilities()
TOOLS: List[ToolSpec] = _registry or _BUILTIN_TOOLS
CAPABILITY_SOURCE: str = "registry" if _registry else "builtin"


def capability_descriptions() -> List[str]:
    """One description per capability, in head order. The encoder encodes these."""
    return [spec.description or spec.name for spec in TOOLS]


def save_capabilities(path: Path = CAPABILITIES_FILE) -> None:
    """Persist the current action space, so growth survives a restart."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1,
        "languages": list(SPOKEN_LANGUAGES),
        "capabilities": [
            {"id": spec.name, "verb": spec.name.split(".")[0],
             "description": spec.description, "backend": spec.backend,
             "directive": spec.directive, "returns": spec.returns,
             "params": spec.params}
            for spec in TOOLS
        ],
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def add_capability(spec: ToolSpec, path: Path = CAPABILITIES_FILE) -> int:
    """Append a capability at runtime. Returns its index in the action head."""
    global TOOLS, TOOLS_BY_NAME
    if spec.name in TOOLS_BY_NAME:
        raise ValueError(f"capability {spec.name!r} already exists")
    TOOLS = list(TOOLS) + [spec]
    TOOLS_BY_NAME = {item.name: item for item in TOOLS}
    save_capabilities(path)
    return len(TOOLS) - 1


TOOLS_BY_NAME: Dict[str, ToolSpec] = {spec.name: spec for spec in TOOLS}


def tool_names() -> List[str]:
    return [spec.name for spec in TOOLS]


def to_json_schema() -> Dict[str, Any]:
    """Full schema document: languages, tool list, per-tool JSON-ish schema."""
    return {
        "version": 1,
        "languages": list(SPOKEN_LANGUAGES),
        "tools": [
            {
                **spec.as_dict(),
                "json_schema": {
                    "type": "object",
                    "properties": spec.params,
                    "required": spec.required_params(),
                },
            }
            for spec in TOOLS
        ],
    }


TOOL_SCHEMA: Dict[str, Any] = to_json_schema()

# String fields whose value is *derived* from the goal rather than copied out of
# it. The goal renders them through a per-language word map or an enum phrase
# ("say hello in team chat" -> channel="team"), so a literal containment test
# would report a false "invented label". Kept here, beside the parameter specs,
# so the corpus builder (``ai.prepare_encoder_sft.sanitize_labels``) and the
# solvability audit (``ai.diag_call_budget.unsolvable_reason``) agree on one
# definition instead of drifting apart.
DERIVED_FIELDS = frozenset({"channel", "kind", "price_rule", "lang"})


def _coerce(type_name: str, value: Any) -> Tuple[bool, Any]:
    """Type coercion for tool args. Returns ``(ok, coerced_or_error)``."""
    if type_name == "str":
        if isinstance(value, str):
            return True, value
        return False, "expected a string"
    if type_name == "int":
        if isinstance(value, bool):
            return False, "expected an integer, got a boolean"
        if isinstance(value, int):
            return True, value
        if isinstance(value, float) and value.is_integer():
            return True, int(value)
        if isinstance(value, str):
            try:
                return True, int(value.strip())
            except ValueError:
                return False, "expected an integer"
        return False, "expected an integer"
    if type_name == "id_or_name":
        # Two callers reach these arguments with different vocabularies: the planner
        # derives an id from the objective's parameters, and the trained encoder emits
        # the entity name from its corpus. The client only understands ids. Declaring
        # this as `str` (which it was) rejected the planner outright — every kill
        # objective died at the validator with "expected a string" — and declaring it
        # `int` would reject the model instead. Accept both, and let the harness resolve
        # a name through data/cache/monsters_full.json.
        if isinstance(value, bool):
            return False, "expected an id or a name, got a boolean"
        if isinstance(value, int):
            return True, value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return False, "expected an id or a name"
            try:
                return True, int(text)
            except ValueError:
                return True, text
        return False, "expected an id or a name"
    if type_name == "float":
        if isinstance(value, bool):
            return False, "expected a number, got a boolean"
        if isinstance(value, (int, float)):
            return True, float(value)
        return False, "expected a number"
    if type_name == "bool":
        if isinstance(value, bool):
            return True, value
        return False, "expected a boolean"
    if type_name == "str_list":
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return True, value
        if isinstance(value, str):
            return True, [part.strip() for part in value.split(",") if part.strip()]
        return False, "expected a list of strings"
    return False, f"unknown type '{type_name}'"


def _special_rules(name: str, args: Dict[str, Any]) -> Optional[str]:
    """Cross-parameter rules JSON schema cannot express. Returns an error or None."""
    if name == "nav.goto":
        has_xy = "x" in args and "y" in args
        has_half_xy = ("x" in args) != ("y" in args)
        if has_half_xy:
            return "nav.goto needs both x and y, or neither"
        targets = ("map_id", "zaap", "npc", "cell", "direction", "npc_id")
        if not (has_xy or any(key in args for key in targets)):
            return ("nav.goto needs one of: x+y, zaap, npc, map_id, cell, "
                    "direction, npc_id")
    if name == "inv.sell":
        if not args.get("item") and not args.get("all"):
            return "inv.sell needs an item name or all=true"
        if args.get("price_rule") == "fixed" and not isinstance(args.get("price"), int):
            return "inv.sell with price_rule=fixed needs an integer price"
    if name == "hdv.list":
        if args.get("price_rule") == "fixed" and not isinstance(args.get("price"), int):
            return "hdv.list with price_rule=fixed needs an integer price"
    if name == "chat.say":
        if args.get("channel") == "whisper" and not args.get("target"):
            return "chat.say on the whisper channel needs a target"
    return None


def canonicalize_args(name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Drop optional arguments that sit at their schema default.

    Why this exists
    ---------------
    ``validate_call`` materialises every optional field's default, so a gold row
    for the goal ``vends Cadeau de Compensation`` carries
    ``{"item": ..., "all": False, "min_stock": 1, "price_rule": "median_minus_1"}``.
    Three of those four values are constants the player never expressed. A model
    that answers ``{"item": ...}`` is *correct* and is scored wrong; a model that
    happens to emit all three constants is scored right. That is noise, and it is
    most of why ``inv.sell`` looked like a 20%-call tool.

    The canonical form is: required fields, plus optional fields whose value
    differs from the schema default. It is what the model should be trained to
    produce, what the scorer should compare against, and what the executor should
    receive -- ``validate_call`` re-applies the defaults at execution time, so
    nothing is lost.

    Unknown tools and unknown fields are passed through untouched rather than
    dropped, so a capability added to the registry without a default still
    round-trips.
    """
    args = dict(args or {})
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        return args
    out: Dict[str, Any] = {}
    for key, value in args.items():
        param = spec.params.get(key)
        if param is None:
            out[key] = value
            continue
        if param.get("required"):
            out[key] = value
            continue
        default = param.get("default")
        if default is None or value != default or type(value) is not type(default):
            out[key] = value
    return out


def validate_call(name: str, args: Optional[Dict[str, Any]] = None) -> Tuple[bool, Any]:
    """Validate a tool call against the schema.

    Returns ``(True, coerced_args)`` on success, ``(False, error_message)``
    otherwise. Coerced args are what the executor (and the recorder) see, so
    training data can never contain, e.g., ``"12"`` where an int is required.
    """
    args = dict(args or {})
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        return False, f"unknown tool '{name}'; known tools: {', '.join(tool_names())}"

    unknown = [key for key in args if key not in spec.params]
    if unknown:
        return False, f"unknown argument(s) for {name}: {', '.join(sorted(unknown))}"

    coerced: Dict[str, Any] = {}
    for key, param in spec.params.items():
        if key not in args or args[key] is None:
            if param.get("required"):
                return False, f"{name} is missing required argument '{key}'"
            default = param.get("default")
            if default is not None:
                coerced[key] = default
            continue
        ok, result = _coerce(param["type"], args[key])
        if not ok:
            return False, f"{name}.{key}: {result}"
        enum = param.get("enum")
        if enum is not None and result not in enum:
            return False, f"{name}.{key}: must be one of {', '.join(map(str, enum))}"
        coerced[key] = result

    rule_error = _special_rules(name, coerced)
    if rule_error:
        return False, rule_error
    return True, coerced
