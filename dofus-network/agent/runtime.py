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
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

from agent.bridge import directive_url as bridge_directive_url
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
        objective: Optional[Dict[str, Any]] = None,
    ) -> Optional[ToolCall]:
        """``objective`` is structured context from the schedule manager (§C):
        when the planner has already chosen a quest step, ``quest.advance`` must
        confirm that ``quest_id`` rather than guess it from the goal prose."""
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
# "list the bread on the market" -- deliberately narrow: it needs both a
# listing verb and a market word, so "list my quests" cannot be read as a sale.
_RE_LIST_MARKET = re.compile(
    r"\b(?:list|put|place|mettre|poner)\b(?:\s+up)?\s+(?:the\s+|le\s+|la\s+|les\s+|el\s+|los\s+)?"
    r"(.{2,40}?)\s+(?:on|in|at|au|sur|en|al)\s+(?:the\s+)?"
    r"(?:market|hdv|h[oô]tel de vente|subasta)\b",
    re.IGNORECASE)
_RE_SCAN = re.compile(r"(?:scan|price of|prix (?:de|du|d'|des)|precio de)\s+(.{2,60})$", re.IGNORECASE)
# "open inventory", "show my bag" -- a plain read of the inventory. It had no
# pattern at all, so the most obvious first thing a user types returned nothing.
_RE_INVENTORY = re.compile(
    r"\b(inventory|inventaire|inventario|backpack|sac|bag|stuff)\b", re.IGNORECASE)
_RE_QUEST = re.compile(r"\b(qu[eê]tes?|quests?|misi[oó]nes?)\b", re.IGNORECASE)
_RE_QUEST_ADVANCE = re.compile(
    r"\b(advance|continue|proceed|next step|prochaine étape|siguiente paso|"
    r"weiter|fortsetzen|étape suivante|avance|continua|continúa)\b", re.IGNORECASE)
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

    def _is_quest_advance_intent(self, goal: str) -> bool:
        """A quest-advance instruction, as opposed to 'list quests' or any other
        goal. Used by §C so the schedule's objective only confirms a quest.advance
        the player actually asked for, and never hijacks an unrelated command."""
        return bool(_RE_QUEST.search(goal)) and bool(_RE_QUEST_ADVANCE.search(goal))

    def choose(
        self,
        goal: str,
        observation: Optional[Dict[str, Any]] = None,
        tools: Optional[List[str]] = None,
        objective: Optional[Dict[str, Any]] = None,
    ) -> Optional[ToolCall]:
        goal = (goal or "").strip()
        if not goal:
            return None

        # §C: the schedule manager has already chosen the active quest step. When
        # the goal is an explicit quest-advance instruction, confirm the schedule's
        # quest_id instead of decoding it from prose -- but only then. A stop or
        # sell command must keep resolving to its own tool; the objective is
        # context, not a command override.
        if (objective and objective.get("tool") == "quest.advance"
                and objective.get("quest_id") is not None
                and self._is_quest_advance_intent(goal)):
            return ToolCall("quest.advance", {"quest_id": objective["quest_id"]}, goal)

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

        match = _RE_LIST_MARKET.search(goal)
        if match:
            item = match.group(1).strip(" .,!?:;")
            quantity = 1
            # "put 3 bread on the market" states a quantity before the item.
            lead = re.match(r"^(\d{1,4})\s+(.+)$", item)
            if lead:
                quantity, item = int(lead.group(1)), lead.group(2).strip()
            return ToolCall(
                "hdv.list",
                {"item": item, "quantity": quantity, "price_rule": "median_minus_1"},
                goal)

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
            item = match.group(1).strip(" .,!?:;")
            # "scan hdv for pain" captured "hdv for pain" -- the item is what
            # follows the market word, not the market word itself.
            item = re.sub(r"^(?:the\s+)?(?:hdv|market|march[eé]|mercado)\s+(?:for|pour|para|de)\s+",
                          "", item, flags=re.IGNORECASE)
            return ToolCall("hdv.scan", {"item": item.strip(" .,!?:;")}, goal)

        if _RE_INVENTORY.search(goal) and not _RE_SELL.search(goal):
            return ToolCall("inv.list", {}, goal)

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
        directive_url: Optional[str] = None,
        instance: str = "default",
        objective_provider: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
    ) -> None:
        self.recorder = recorder or TrajectoryRecorder(instance=instance)
        self.executor = executor or ToolExecutor(
            directive_url=directive_url, recorder=self.recorder)
        self.executor.recorder = self.recorder
        self.policy: Policy = policy or RulePolicy()
        self.instance = instance
        # §C hook: returns the schedule manager's active objective (a dict like
        # {"tool": "quest.advance", "quest_id": 1935, ...}) or None. When set,
        # the policy receives it as structured context on every decision.
        self.objective_provider = objective_provider
        # The most recent game-state snapshot the harness pushed, and when. The
        # executor stamps these onto every step it records, so a bridge that
        # streams observations does not have to repeat the whole state in the
        # body of every action request -- which is how the observation column
        # ended up null in all 2,759 recorded steps.
        self.last_observation: Optional[Dict[str, Any]] = None
        self.last_observation_ts: float = 0.0
        self.observations_seen: int = 0

    # -- observation stream --------------------------------------------------

    def observe(
        self,
        observation: Dict[str, Any],
        goal: Optional[str] = None,
        source: str = "live",
    ) -> Dict[str, Any]:
        """Accept a pushed game-state snapshot and remember it as current."""
        self.last_observation = observation
        self.last_observation_ts = time.time()
        self.observations_seen += 1
        return self.recorder.observation(observation, goal=goal, source=source)

    def current_observation(self) -> tuple[Optional[Dict[str, Any]], Optional[float], str]:
        """``(observation, age_ms, provenance)`` for the freshest snapshot.

        Provenance is ``"call"`` when the snapshot arrived with the request
        being served and ``"carried"`` when it is reused from an earlier push.
        A caller that already holds a fresh observation never consults this.
        """
        if not self.last_observation:
            return None, None, "call"
        age_ms = (time.time() - self.last_observation_ts) * 1000.0
        return self.last_observation, round(age_ms, 1), "carried"

    def note_observation(self, observation: Dict[str, Any]) -> None:
        """Adopt a snapshot the bridge returned with a directive response.

        The bridge captures it *after* the directive ran, so it is the state the
        next decision should be made against. It is not logged as an observation
        record: the next step records it as its own ``observation``, which is the
        same pairing and avoids writing every snapshot twice.
        """
        self.last_observation = observation
        self.last_observation_ts = time.time()
        self.observations_seen += 1

    def execute_tool(
        self,
        tool: str,
        args: Optional[Dict[str, Any]] = None,
        instance: Optional[str] = None,
        goal: Optional[str] = None,
        source: str = "live",
        observation: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """Execute one tool call, recording the observation it was made against.

        An explicit ``observation`` wins and is marked ``"call"``. Otherwise the
        runtime reuses the freshest pushed snapshot and marks it ``"carried"``,
        together with its age, so a downstream consumer can tell a fresh pairing
        from a stale one.
        """
        if observation is not None:
            return self.executor.execute(
                tool, args, instance=instance, goal=goal, source=source,
                observation=observation, observation_age_ms=0.0,
                observation_source="call")
        carried, age_ms, provenance = self.current_observation()
        return self.executor.execute(
            tool, args, instance=instance, goal=goal, source=source,
            observation=carried, observation_age_ms=age_ms,
            observation_source=provenance)

    def handle_goal(
        self,
        goal: str,
        instance: Optional[str] = None,
        observation: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """Resolve one natural-language goal into a tool call and execute it."""
        instance = instance or self.instance
        if observation:
            self.observe(observation, goal=goal)
            carried, age_ms, provenance = observation, 0.0, "call"
        else:
            carried, age_ms, provenance = self.current_observation()

        # §C: pull the schedule manager's active objective and hand it to the
        # policy as structured context. quest.advance then confirms the step the
        # planner already chose instead of guessing an id from the goal prose.
        objective = self.objective_provider() if self.objective_provider else None

        # The policy sees whatever the recorder will see. Passing the empty dict
        # here while the step record carries a snapshot would make the logged
        # input and the logged decision disagree.
        call = self.policy.choose(goal, carried or {}, tool_names(), objective=objective)
        if call is None:
            record = self.recorder.log("goal_unresolved", {
                "goal": goal,
                "policy": type(self.policy).__name__,
                "observation": carried,
                "observation_age_ms": age_ms,
                "observation_source": provenance,
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
            observation=carried,
            observation_age_ms=age_ms,
            observation_source=provenance,
        )
