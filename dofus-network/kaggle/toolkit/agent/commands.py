"""The user-command contract: intent + slots, with a learned model or the rules.

A goal arrives as free text ("vends 500 bois de chêne", "fais les quêtes à Astrub").
Today that text is interpreted by `DofusNLPEngine` + the `RulePolicy` regexes. That is
the fallback, not the target: the command encoder from `docs/TRAINING_PLAN.md` predicts
this same schema, and it replaces a rule only after it beats the rule on held-out
prompts and survives a shadow session. This module is the one schema both paths speak,
so the dataset, the shadow log and the learned model cannot drift apart.

`parse_command(text)` is the rule baseline. `parse_command(text, model=...)` asks a
model first and falls back when it is unsure. The model object only needs
`predict(text) -> {"intent", "slots", "confidence"}`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.planner import _goal_mode

INTENTS = (
    "stop", "follow", "travel", "quest", "farm", "fight", "inventory", "market",
    "chat", "knowledge", "state", "auto", "unknown",
)

SLOT_KEYS = (
    "action", "item", "mob", "resource", "quest_id", "npc", "objective_id",
    "area", "map_id", "zone", "zaap", "x", "y", "cell", "direction",
    "quantity", "price", "price_rule", "level_min", "dungeon_only", "all",
    "channel", "target", "message", "query", "kind", "reply", "close", "accept",
    "name",
)

# What each intent can carry. This is a legality mask, not a parser: a learned
# model may emit a slot the intent has no use for (a message on a sale), and the
# harness must drop it before the planner sees the command.
INTENT_SLOTS = {
    "stop": {"action"},
    "follow": {"name"},
    "travel": {"x", "y", "zaap", "map_id", "npc", "area", "direction", "cell"},
    "quest": {"action", "quest_id", "objective_id", "area", "level_min",
              "dungeon_only", "npc", "reply", "close", "accept"},
    "farm": {"action", "mob", "resource", "zone", "map_id", "quantity"},
    "fight": {"action", "target"},
    "inventory": {"action", "item", "all", "quantity", "price_rule", "price", "kind"},
    "market": {"action", "item", "quantity", "price_rule", "price"},
    "chat": {"channel", "target", "message"},
    "knowledge": {"query", "kind"},
    "state": set(),
    "auto": set(),
    "unknown": set(),
}

# The canonical tool for a parsed intent/action. This is the bridge the planner
# needs before the learned parser can act: the model decides *what* the user
# wants, this table says *which registered tool* carries it. It is plumbing, not
# a decision; every value must exist in the tool schema (gated). Some intents are
# goal modes with no single tool (auto) and return None.
ACTION_TOOL = {
    "quest": {"run": "quest.advance", "advance": "quest.advance",
              "list": "quest.list"},
    "farm": {"start": "farm.start", "stop": "farm.stop"},
    "inventory": {"sell": "inv.sell", "list": "inv.list"},
    "market": {"list": "hdv.list", "scan": "hdv.scan"},
    "fight": {"join": "fight.join", "pass": "fight.pass"},
}
INTENT_TOOL = {
    "quest": "quest.advance",
    "farm": "farm.start",
    "inventory": "inv.list",
    "market": "hdv.scan",
    "travel": "nav.goto",
    "follow": "nav.follow",
    "stop": "safe.stop",
    "fight": "fight.join",
    "state": "state.snapshot",
    "knowledge": "knowledge.search",
    "chat": "chat.say",
}


def intent_tool(intent: str, action: Any = None) -> Optional[str]:
    """The tool a parsed command would call, or None for a goal mode."""
    chosen = ACTION_TOOL.get(str(intent or ""), {}).get(str(action or "").lower())
    return chosen or INTENT_TOOL.get(str(intent or ""))


# tool -> (intent, slots supplied by the tool's purpose)
TOOL_INTENT = {
    "safe.stop": ("stop", {"action": "all"}),
    "nav.follow": ("follow", {}),
    "fight.pass": ("fight", {"action": "pass"}),
    "fight.join": ("fight", {"action": "join"}),
    "nav.goto": ("travel", {}),
    "farm.start": ("farm", {"action": "start"}),
    "farm.stop": ("farm", {"action": "stop"}),
    "quest.list": ("quest", {"action": "list"}),
    "quest.advance": ("quest", {"action": "advance"}),
    "inv.list": ("inventory", {"action": "list"}),
    "inv.sell": ("inventory", {"action": "sell"}),
    "hdv.scan": ("market", {"action": "scan"}),
    "hdv.list": ("market", {"action": "list"}),
    "chat.say": ("chat", {}),
    "knowledge.search": ("knowledge", {}),
    "state.snapshot": ("state", {}),
}

# The tool names that carry a slot under a different key.
_ARG_SLOTS = {
    "monster": "mob",
    "npc_id": "npc",
    "objective_id": "objective_id",
    "map_id": "map_id",
    "level_min": "level_min",
    "dungeon_only": "dungeon_only",
    "price_rule": "price_rule",
    "zone": "zone",
    "zaap": "zaap",
    "item": "item",
    "resource": "resource",
    "area": "area",
    "x": "x",
    "y": "y",
    "cell": "cell",
    "direction": "direction",
    "quantity": "quantity",
    "price": "price",
    "all": "all",
    "channel": "channel",
    "target": "target",
    "query": "query",
    "kind": "kind",
    "text": "message",
    "quest_id": "quest_id",
    "name": "name",
    "reply": "reply",
    "close": "close",
    "accept": "accept",
}


def _lang(text: str) -> str:
    try:
        from ai.lang_detect import detect

        found = detect(text)
        return found.lang if getattr(found, "decided", False) else "auto"
    except Exception:      # noqa: BLE001 - language is metadata, not a gate
        return "auto"


def _args_slots(args: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(args, dict):
        return out
    for key, value in args.items():
        slot = _ARG_SLOTS.get(key)
        if slot is None or value is None:
            continue
        out[slot] = value
    return out


def _clean_slots(slots: Dict[str, Any]) -> Dict[str, Any]:
    """Slots are what the user asked for, not the tool's defaults.

    `inv.sell` always sends `price_rule=median_minus_1` and `all=True`; `hdv.list`
    defaults quantity to 1; `nav.follow` sends an empty name. Carrying those into the
    command schema would make a learned parser imitate defaults the user never said,
    and would make every gold row look wrong.
    """
    out: Dict[str, Any] = {}
    for key, value in slots.items():
        if value is None or value == "" or value is False:
            continue
        if key == "quantity" and value == 1:
            continue
        if key == "price_rule" and value == "median_minus_1":
            continue
        if key == "name" and not value:
            continue
        out[key] = value
    if "item" in out:
        # "sell the item" is not "sell everything in the inventory".
        out.pop("all", None)
    return out


def from_tool(tool: str, args: Any, text: str, lang: Optional[str] = None,
              source: str = "rule", confidence: float = 0.9) -> Dict[str, Any]:
    """The command a tool call was answering. The only tool->intent mapping."""
    intent, built_in = TOOL_INTENT.get(tool, ("unknown", {}))
    slots = dict(built_in)
    slots.update(_args_slots(args))
    return {
        "text": str(text or ""),
        "lang": lang or _lang(str(text or "")),
        "intent": intent,
        "slots": _clean_slots(slots),
        "source": source,
        "confidence": confidence,
    }


def _rule_policy() -> Any:
    global _POLICY
    if _POLICY is None:
        from agent.runtime import RulePolicy

        _POLICY = RulePolicy()
    return _POLICY


_POLICY: Any = None


def rule_parse(text: str) -> Dict[str, Any]:
    """The rule/engine baseline in the command schema."""
    text = (text or "").strip()
    lang = _lang(text)
    if not text:
        return {"text": "", "lang": lang, "intent": "auto", "slots": {},
                "source": "rule", "confidence": 1.0}
    try:
        call = _rule_policy().choose(text, {}, None)
    except Exception:      # noqa: BLE001 - a broken engine must not lose the goal
        call = None
    if call is not None:
        return from_tool(getattr(call, "tool", ""), getattr(call, "args", {}),
                         text, lang)
    mode = _goal_mode(text)
    if mode == "auto":
        return {"text": text, "lang": lang, "intent": "auto", "slots": {},
                "source": "rule", "confidence": 0.7}
    if mode == "quest":
        return {"text": text, "lang": lang, "intent": "quest",
                "slots": {"action": "run"}, "source": "rule", "confidence": 0.6}
    if mode == "farm":
        return {"text": text, "lang": lang, "intent": "farm",
                "slots": {"action": "start"}, "source": "rule", "confidence": 0.6}
    return {"text": text, "lang": lang, "intent": "unknown", "slots": {},
            "source": "rule", "confidence": 0.0}


def _valid_prediction(prediction: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(prediction, dict):
        return None
    intent = str(prediction.get("intent") or "")
    if intent not in INTENTS or intent == "unknown":
        return None
    slots = prediction.get("slots")
    if not isinstance(slots, dict):
        slots = {}
    slots = {key: value for key, value in slots.items()
             if key in SLOT_KEYS and value is not None}
    allowed = INTENT_SLOTS.get(intent)
    if allowed is not None:
        slots = {key: value for key, value in slots.items() if key in allowed}
    try:
        confidence = float(prediction.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return None
    return {"intent": intent, "slots": slots, "confidence": confidence}


def parse_command(text: str, model: Any = None,
                  threshold: float = 0.55) -> Dict[str, Any]:
    """The model's answer when it is sure, otherwise the rule baseline."""
    text = (text or "").strip()
    if model is not None:
        try:
            prediction = _valid_prediction(model.predict(text))
        except Exception:      # noqa: BLE001 - a broken model falls back
            prediction = None
        if prediction is not None and prediction["confidence"] >= threshold:
            return {
                "text": text,
                "lang": _lang(text),
                "intent": prediction["intent"],
                "slots": prediction["slots"],
                "source": "model",
                "confidence": prediction["confidence"],
            }
    return rule_parse(text)


def command_row(command: Dict[str, Any]) -> Dict[str, Any]:
    """The row the loop logs so a prompt and its outcome can be paired later."""
    return {
        "kind": "command",
        "text": command.get("text"),
        "lang": command.get("lang"),
        "intent": command.get("intent"),
        "slots": command.get("slots") or {},
        "source": command.get("source"),
        "confidence": command.get("confidence"),
    }
