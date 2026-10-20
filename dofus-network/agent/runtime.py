"""Agent runtime — the observe -> decide -> act loop.

The decision step is behind the ``Policy`` protocol so the rule-based baseline
(``RulePolicy``, which wraps the existing trilingual intent engine when it can
load) can be swapped for the neural policy without touching the loop, the
executor, or the recorder.

``RulePolicy`` is the data-collection baseline — not the destination. Every
goal it resolves (and every one it fails) is recorded, and those records are
the first SFT corpus for the 100M model.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from agent.executor import ToolExecutor, ToolResult
from agent.recorder import TrajectoryRecorder
from agent.schema import tool_names

logger = logging.getLogger("dofus.agent.runtime")


@dataclass
class ToolCall:
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    goal: Optional[str] = None


class Policy(Protocol):
    """Decision interface. The 100M model implements this later."""

    def choose(
        self,
        goal: str,
        observation: Dict[str, Any],
        tools: List[str],
    ) -> Optional[ToolCall]:
        ...


_RE_STOP = re.compile(r"\b(stop|arr[eê]te[rz]?|arr[eê]te|halt|para)\b", re.IGNORECASE)
_RE_FOLLOW = re.compile(r"(suis[- ]?moi|follow me|s[ií]gueme)", re.IGNORECASE)
_RE_PASS = re.compile(r"\b(passe|pass(?:\s+turn)?|pasa(?:\s+turno)?)\b", re.IGNORECASE)
_RE_COORD_BRACKET = re.compile(r"\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]")
_RE_COORD_NATURAL = re.compile(
    r"\b(?:va\s+en|go\s+to|ve\s+a|aller\s+en|coords?|pos)\s+(-?\d+)[,\s]+(-?\d+)\b", re.IGNORECASE)
_RE_ZAAP = re.compile(r"zaap\s+([A-Za-z\u00c0-\u017f' -]{2,40})", re.IGNORECASE)
_RE_SELL = re.compile(
    r"\b(sell|vends|vend(?:re)?|vender|vende)\b(?:\s+(.+?))?"
    r"(?:\s+(?:for|at|price|prix|precio|kamas?|at market|market price)\b.*)?$",
    re.IGNORECASE)
_RE_SCAN = re.compile(r"(?:scan|price of|prix (?:de|du|d'|des)|precio de)\s+(.{2,60})$", re.IGNORECASE)
_RE_QUEST = re.compile(r"\b(qu[eê]tes?|quests?|misi[oó]nes?)\b", re.IGNORECASE)
_RE_QUEST_PREP = (
    r"(?:\bin der\b|\ben el\b|\ben la\b|\ben los\b|\ben las\b"
    r"|\bde la\b|\bde los\b|\bde las\b"
    r"|d['\u2019]"
    r"|\bunder\b|\baround\b|\bnear\b|\bat\b|\bin\b|\ben\b|\bdans\b|\bvers\b"
    r"|\bà\b|\ba\b"
    r"|\bim\b|\bbei\b|\bdel\b|\bdu\b|\bdes\b|\bde\b)"
)
_RE_QUEST_AREA = re.compile(
    r"\b(?:qu[eê]tes?|quests?|misi[oó]nes?)\b[^!?.;\n]{0,40}?" + _RE_QUEST_PREP +
    r"\s*([\w\u00c0-\u017f'\u2019-]{3,30})",
    re.IGNORECASE)
_RE_FARM = re.compile(r"\b(farm|farmear|r[eé]colte[rz]?|gather|harvest|minar|collecte[rz]?)\b",
                      re.IGNORECASE)
_RE_JOIN = re.compile(r"\b(join (?:the )?fight|join combat|join|rejoins|entra al combate|pelea)\b",
                      re.IGNORECASE)
_RE_ALL_WORDS = re.compile(r"\b(all|tout|tous|toute|todo|toda|everything)\b", re.IGNORECASE)


class RulePolicy:
    """Trilingual baseline policy.

    Order of decision:

    1. The existing ``DofusNLPEngine`` (when its data dependencies load) —
       handles zaaps, resources, quest guides, farm loops.
    2. Compiled regexes — the executive verbs (stop/follow/pass) and the
       training-critical orders (sell all, scan price, go to [x,y], farm,
       join fight, list quests).

    Everything it cannot resolve returns ``None`` and is logged as an
    unresolved goal — those are exactly the samples the neural policy must
    learn to handle.
    """

    def __init__(self, current_pos: Optional[tuple[int, int]] = None) -> None:
        self.current_pos = current_pos or (4, -19)
        self._engine: Any = None
        self._engine_loaded = False

    # -- engine bridge -------------------------------------------------------

    def _get_engine(self) -> Any:
        if self._engine_loaded:
            return self._engine
        self._engine_loaded = True
        try:
            from navigation.nlp_intent_engine import DofusNLPEngine

            self._engine = DofusNLPEngine()
            logger.info("RulePolicy: DofusNLPEngine loaded")
        except Exception as exc:  # noqa: BLE001 - fallback is the regex path
            logger.info(f"RulePolicy: NLP engine unavailable ({type(exc).__name__}: {exc}); regex fallback")
            self._engine = None
        return self._engine

    # -- policy API ----------------------------------------------------------

    def choose(
        self,
        goal: str,
        observation: Optional[Dict[str, Any]] = None,
        tools: Optional[List[str]] = None,
    ) -> Optional[ToolCall]:
        goal = (goal or "").strip()
        if not goal:
            return None

        engine_call = self._choose_with_engine(goal)
        if engine_call is not None:
            return engine_call
        return self._choose_with_regex(goal)

    # -- engine-driven decisions --------------------------------------------

    def _choose_with_engine(self, goal: str) -> Optional[ToolCall]:
        engine = self._get_engine()
        if engine is None:
            return None
        try:
            result = engine.process_query(goal, current_pos=self.current_pos)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"engine.process_query failed: {exc}")
            return None

        intent = getattr(result, "intent", None)
        intent_name = getattr(intent, "value", str(intent))
        payload = getattr(result, "execution_payload", {}) or {}

        if intent_name == "NAVIGATE_COORDS":
            match = _RE_COORD_BRACKET.search(goal) or _RE_COORD_NATURAL.search(goal)
            if match:
                return ToolCall("nav.goto", {"x": int(match.group(1)), "y": int(match.group(2))}, goal)
            return None
        if intent_name == "NAVIGATE_ZAAP":
            zaap_name = payload.get("zaap")
            if zaap_name:
                return ToolCall("nav.goto", {"zaap": str(zaap_name)}, goal)
            return None
        if intent_name == "START_FARM_LOOP":
            loop = payload.get("farm_loop") or {}
            resource = loop.get("resource") or loop.get("resource_name") or payload.get("resource")
            args = {"resource": str(resource)} if resource else {}
            return ToolCall("farm.start", args, goal)
        if intent_name == "QUEST_GUIDE":
            args: Dict[str, Any] = {"limit": 10}
            match = _RE_QUEST_AREA.search(goal)
            if match:
                args["area"] = match.group(1)
            return ToolCall("quest.list", args, goal)
        if intent_name == "FIND_RESOURCE":
            return ToolCall("knowledge.search", {"query": goal, "kind": "item", "limit": 5}, goal)
        if intent_name == "RESOURCE_LEVEL_REQ":
            return ToolCall("knowledge.search", {"query": goal, "kind": "item", "limit": 5}, goal)
        if intent_name == "BOT_CONTROL":
            action = payload.get("action")
            if action == "STOP":
                return ToolCall("safe.stop", {"reason": goal}, goal)
            if action == "FOLLOW":
                return ToolCall("nav.follow", {"name": ""}, goal)
        if intent_name == "COMBAT_ORDER":
            if payload.get("action") == "PASS_TURN":
                return ToolCall("fight.pass", {}, goal)
        return None

    # -- regex decisions -----------------------------------------------------

    def _choose_with_regex(self, goal: str) -> Optional[ToolCall]:
        if _RE_STOP.search(goal):
            return ToolCall("safe.stop", {"reason": goal}, goal)
        if _RE_FOLLOW.search(goal):
            return ToolCall("nav.follow", {"name": ""}, goal)
        if _RE_PASS.search(goal):
            return ToolCall("fight.pass", {}, goal)

        match = _RE_COORD_BRACKET.search(goal) or _RE_COORD_NATURAL.search(goal)
        if match:
            return ToolCall("nav.goto", {"x": int(match.group(1)), "y": int(match.group(2))}, goal)

        match = _RE_ZAAP.search(goal)
        if match:
            return ToolCall("nav.goto", {"zaap": match.group(1).strip()}, goal)

        match = _RE_SELL.search(goal)
        if match:
            remainder = (match.group(2) or "").strip(" .,!?:;")
            if not remainder or _RE_ALL_WORDS.search(remainder):
                return ToolCall("inv.sell", {"all": True, "price_rule": "median_minus_1"}, goal)
            return ToolCall(
                "inv.sell",
                {"item": remainder, "all": True, "price_rule": "median_minus_1"},
                goal)

        match = _RE_SCAN.search(goal)
        if match:
            return ToolCall("hdv.scan", {"item": match.group(1).strip(" .,!?:;")}, goal)

        if _RE_QUEST.search(goal):
            args = {}
            match = _RE_QUEST_AREA.search(goal)
            if match:
                args["area"] = match.group(1)
            return ToolCall("quest.list", args, goal)

        if _RE_FARM.search(goal):
            return ToolCall("farm.start", {}, goal)

        if _RE_JOIN.search(goal):
            return ToolCall("fight.join", {}, goal)

        return None


class AgentRuntime:
    """Ties policy, executor and recorder together."""

    def __init__(
        self,
        executor: Optional[ToolExecutor] = None,
        recorder: Optional[TrajectoryRecorder] = None,
        policy: Optional[Policy] = None,
        directive_url: str = "http://127.0.0.1:8893/directive",
        instance: str = "default",
    ) -> None:
        self.recorder = recorder or TrajectoryRecorder(instance=instance)
        self.executor = executor or ToolExecutor(
            directive_url=directive_url, recorder=self.recorder)
        self.executor.recorder = self.recorder
        self.policy: Policy = policy or RulePolicy()
        self.instance = instance

    def handle_goal(
        self,
        goal: str,
        instance: Optional[str] = None,
        observation: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """Resolve one natural-language goal into a tool call and execute it."""
        instance = instance or self.instance
        if observation:
            self.recorder.observation(observation, goal=goal)

        call = self.policy.choose(goal, observation or {}, tool_names())
        if call is None:
            record = self.recorder.log("goal_unresolved", {
                "goal": goal,
                "policy": type(self.policy).__name__,
            })
            return ToolResult(
                tool="none", ok=False,
                error=("the rule policy could not map this goal to a tool; "
                       "it is logged as an unresolved goal for neural-policy training"),
                data={"record": record})

        logger.info(f"goal -> {call.tool}({call.args})")
        return self.executor.execute(
            call.tool,
            call.args,
            instance=instance,
            goal=goal,
            observation=observation,
        )
