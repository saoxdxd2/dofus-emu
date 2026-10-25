"""Schedule manager: run a plan, notice when reality disagrees, replan.

A plan is a hypothesis about the future. The manager is the part that keeps it
honest:

* it hands the harness **one executable action at a time**, in plan order;
* it watches the clock and the observed drops against the estimate;
* it replans when the estimate is wrong, when a cheaper branch appears (the
  player now has enough kamas to buy instead of farm), or when the character
  changed level and better farms unlocked;
* it persists only the *goal* and progress counters, and re-plans on restart,
  because a plan is cheap to rebuild and expensive to keep stale.

Replan triggers, in priority order:

1. ``level_changed``   — new farms and new level gates opened up
2. ``cheaper_branch``  — the buy route became affordable mid-farm
3. ``drop_rate_drift`` — 3x the expected kills with no drop
4. ``overrun``         — a step took > 1.5x its estimate
5. ``stale``           — the plan is older than ``max_age_minutes``
"""

from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from planning.bandit import ARMS, StrategyRanker
from planning.economy import CostModel, Economy, PlayerState
from planning.graph_memory import DecisionGraph
from planning.outcome import extract
from planning.trace import ParamUpdate, Trace, TraceLog
from planning.kb import REPO_ROOT
from planning.planner import EXECUTABLE_TOOLS, Goal, Plan, PlanNode, Planner

STATE_FILE = REPO_ROOT / "data" / "kb" / "schedule_state.json"


@dataclass
class StepRecord:
    label: str
    kind: str
    expected_minutes: float
    started_at: float
    finished_at: Optional[float] = None
    outcome: str = "running"


@dataclass
class Schedule:
    """What the manager persists across restarts."""

    goal: Dict[str, Any]
    player: Dict[str, Any]
    pointer: int = 0
    created_at: float = field(default_factory=time.time)
    steps: List[Dict[str, Any]] = field(default_factory=list)


class ScheduleManager:
    def __init__(
        self,
        planner: Planner,
        economy: Economy,
        state_file: Path = STATE_FILE,
        max_age_minutes: float = 30.0,
        overrun_factor: float = 1.5,
        ranker=None,
        graph: Optional[DecisionGraph] = None,
        traces: Optional[TraceLog] = None,
    ) -> None:
        self.planner = planner
        self.economy = economy
        self.ranker = ranker
        self.graph = graph if graph is not None else DecisionGraph()
        self.traces = traces if traces is not None else TraceLog()
        self.plan_id = ""
        self.goal_text = ""
        self.reasoning_ms = 0.0
        self.state_file = state_file
        self.max_age_minutes = max_age_minutes
        self.overrun_factor = overrun_factor

        self.plan: Optional[Plan] = None
        self.goal: Optional[Goal] = None
        self.player: Optional[PlayerState] = None
        self.pointer = 0
        self.created_at = 0.0
        self.leaf_cache: List[PlanNode] = []
        self.history: List[StepRecord] = []
        self.replans: List[Dict[str, Any]] = []

    # -- lifecycle -----------------------------------------------------------

    def submit(self, goal: Goal, player: PlayerState, goal_text: str = "") -> Plan:
        self.goal = goal
        self.goal_text = goal_text
        self.plan_id = uuid.uuid4().hex[:12]
        self.player = player
        self.plan = self.planner.plan(goal, player)
        self.leaf_cache = self.plan.leaves()
        self.pointer = 0
        self.created_at = time.time()
        self.history = []
        self._persist()
        return self.plan

    def restore(self) -> Optional[Plan]:
        """Reload the goal, then re-plan against the current economy state."""
        if not self.state_file.exists():
            return None
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        goal = Goal(**payload["goal"])
        player = PlayerState(**payload["player"])
        self.submit(goal, player)
        self.pointer = int(payload.get("pointer") or 0)
        return self.plan

    def _persist(self) -> None:
        if self.goal is None or self.player is None:
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({
            "goal": {"item_id": self.goal.item_id, "qty": self.goal.qty,
                     "need_equip": self.goal.need_equip, "lang": self.goal.lang},
            "player": self.player.as_dict(),
            "pointer": self.pointer,
            "created_at": self.created_at,
            "steps": [
                {"label": s.label, "kind": s.kind, "outcome": s.outcome,
                 "expected_minutes": s.expected_minutes}
                for s in self.history
            ],
        }, indent=2), encoding="utf-8")

    # -- execution -----------------------------------------------------------

    def tick(self, player: Optional[PlayerState] = None) -> Optional[Dict[str, Any]]:
        """Return the next tool call, or ``None`` when the plan is exhausted."""
        if self.plan is None:
            return None
        if player is not None:
            self.player = player

        reason = self._drift_reason()
        if reason:
            self._replan(reason)
            if self.plan is None:
                return None

        while self.pointer < len(self.leaf_cache):
            leaf = self.leaf_cache[self.pointer]
            tool = leaf.meta.get("tool")
            if leaf.kind == "blocked":
                self.pointer += 1
                continue
            args = leaf.meta.get("args") or {}
            if not tool or tool not in EXECUTABLE_TOOLS:
                preflight = leaf.meta.get("preflight_tool")
                if not preflight or preflight not in EXECUTABLE_TOOLS:
                    # Nothing the harness can do for this step: skip it, but keep
                    # the gap visible through status()['missing_tools'].
                    self.pointer += 1
                    continue
                tool = preflight
                args = leaf.meta.get("preflight_args") or {}
            self.history.append(StepRecord(
                label=leaf.label, kind=leaf.kind,
                expected_minutes=leaf.minutes, started_at=time.time()))
            return {
                "tool": tool,
                "args": args,
                "why": leaf.label,
                "expected_minutes": None if math.isinf(leaf.minutes) else round(leaf.minutes, 1),
                "executable": tool in EXECUTABLE_TOOLS,
                "step": self.pointer,
                "steps_total": len(self.leaf_cache),
            }
        return None

    def report(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Feed a tool result back: normalise -> learn -> trace -> replan decision."""
        if not self.history:
            return {"status": "ignored", "reason": "no step running"}

        step = self.history[-1]
        step.finished_at = time.time()
        elapsed = (step.finished_at - step.started_at) / 60.0
        leaf = self.leaf_cache[self.pointer] if self.pointer < len(self.leaf_cache) else None
        source = str(result.get("source") or "live")

        # 1-3. normalise -> learn -> trace (shared with out-of-plan actions)
        entity: Dict[str, Any] = {}
        if leaf is not None:
            for name in ("monster_id", "item_id", "monster_level", "quest_id", "map_id"):
                if leaf.meta.get(name) is not None:
                    entity[name] = leaf.meta[name]
        ingest = self._ingest(
            action=step.kind,
            tool=str(result.get("tool") or (leaf.meta.get("tool") if leaf else "")),
            args=dict(leaf.meta.get("args") or {}) if leaf else {},
            entity=entity,
            predicted_minutes=step.expected_minutes,
            result=result,
            source=source,
            elapsed_minutes=elapsed,
            plan_id=self.plan_id,
            step=self.pointer,
            cache_hit=bool(self.plan.cache_hit) if self.plan is not None else False,
            reasoning_ms=self.reasoning_ms,
        )
        outcome = ingest["outcome"]

        # 4. decide what happens next
        status, reason = "continue", ""
        if not result.get("ok"):
            status, reason = "replan", "step_failed"
        elif (not math.isinf(step.expected_minutes)
              and elapsed > step.expected_minutes * self.overrun_factor):
            status, reason = "replan", "overrun"

        self.pointer += 1
        if self.pointer >= len(self.leaf_cache):
            status = "done" if status == "continue" else status

        if status == "replan":
            self._replan(reason)

        self.economy.record_run({
            "kind": step.kind, "label": step.label,
            "minutes": round(elapsed, 2), "kamas": int(outcome.get("kamas")),
            "ok": bool(result.get("ok")),
        })
        self.economy.save()
        self.traces.flush()
        self.graph.save()
        self._persist()
        return {
            "status": status,
            "reason": reason,
            "elapsed_minutes": round(elapsed, 2),
            "signal": ingest["signal"],
            "updates": ingest["updates"],
            "graph": ingest["graph"],
            "residual": ingest["residual"],
        }

    def _ingest(
        self,
        action: str,
        tool: str,
        args: Dict[str, Any],
        entity: Dict[str, Any],
        predicted_minutes: Optional[float],
        result: Dict[str, Any],
        source: str = "live",
        elapsed_minutes: float = 0.0,
        plan_id: str = "",
        step: int = 0,
        cache_hit: bool = False,
        reasoning_ms: float = 0.0,
    ) -> Dict[str, Any]:
        """The single learning path: payload -> outcome -> posteriors -> trace -> graph.

        Every action, whether it came from a plan or not, goes through here. One
        path means one place to audit and one place to change.
        """
        level = self.player.level if self.player else 1
        outcome = extract(
            tool=tool, args=args, result=result.get("data"),
            ok=bool(result.get("ok")), error=result.get("error"),
            latency_ms=float(result.get("latency_ms") or 0.0),
        )
        if "minutes" not in outcome.quantities and elapsed_minutes > 0:
            # The action's own wall-clock is a real duration even when the
            # backend did not report one.
            outcome.quantities["minutes"] = elapsed_minutes
        for key, value in entity.items():
            outcome.entities.setdefault(key, value)

        updates: List[ParamUpdate] = []
        if outcome.signal == "verified" and action in ARMS:
            updates = self._learn(action, outcome, entity, source, level,
                                  predicted_minutes)

        trace = Trace(
            action=action, tool=tool, args=args, goal=self.goal_text,
            entity=dict(outcome.entities),
            predicted_minutes=(None if predicted_minutes is None
                               or math.isinf(predicted_minutes) else predicted_minutes),
            outcome=outcome, updates=updates, source=source,
            plan_id=plan_id, step=step, cache_hit=cache_hit,
            reasoning_ms=reasoning_ms,
        )
        graph_change = self.graph.update_from_trace(trace)
        self.traces.append(trace)
        return {
            "outcome": outcome,
            "signal": outcome.signal,
            "updates": [update.as_dict() for update in updates],
            "graph": graph_change,
            "residual": trace.residual(),
        }

    def record_action(
        self,
        action: str,
        tool: str,
        args: Dict[str, Any],
        result: Dict[str, Any],
        entity: Optional[Dict[str, Any]] = None,
        predicted_minutes: Optional[float] = None,
        elapsed_minutes: float = 0.0,
        source: str = "live",
    ) -> Dict[str, Any]:
        """Feed an action that did not come from the planner (chat, manual, retry)."""
        outcome = self._ingest(
            action=action, tool=tool, args=args, entity=entity or {},
            predicted_minutes=predicted_minutes, result=result, source=source,
            elapsed_minutes=elapsed_minutes, plan_id=self.plan_id or "manual",
        )
        self.economy.save()
        self.traces.flush()
        self.graph.save()
        return {
            "signal": outcome["signal"],
            "updates": outcome["updates"],
            "graph": outcome["graph"],
            "residual": outcome["residual"],
        }

    def _learn(self, action: str, outcome, entity: Dict[str, Any], source: str,
               level: int, predicted_minutes: Optional[float] = None) -> List[ParamUpdate]:
        """Apply one outcome to the posteriors it is evidence about.

        Each write is bracketed by a before/after read so the trace can show
        exactly which parameter moved and by how much. Nothing is updated without
        appearing in that list.
        """
        updates: List[ParamUpdate] = []
        store = self.economy.experience
        item_id = entity.get("item_id")
        monster_id = entity.get("monster_id")
        kills = outcome.get("kills")
        drops = outcome.get("drops")
        minutes = outcome.get("minutes")

        def bump(key: str, why: str, call) -> None:
            before = store.get(key)
            mean_before = None if before is None else before.mean()
            n_before = 0.0 if before is None else before.n
            call()
            after = store.get(key)
            mean_after = None if after is None else after.mean()
            n_after = 0.0 if after is None else after.n
            if mean_after != mean_before or n_after != n_before:
                updates.append(ParamUpdate(
                    param=key, before=mean_before, after=mean_after,
                    n_before=n_before, n_after=n_after, why=why))

        if action == "farm" and item_id is not None and kills > 0:
            bump(Economy.drop_key(int(item_id)), "drops / kills",
                 lambda: self.economy.record_drop_run(
                     int(item_id), int(drops), int(kills), source=source))

        if action == "farm" and monster_id is not None and kills > 0 and minutes > 0:
            bump(CostModel.kpm_key(int(monster_id), level), "kills / minutes",
                 lambda: self.economy.cost.record_farm(
                     int(monster_id), level, int(kills), minutes, source=source))

        if action == "buy" and item_id is not None and outcome.get("price") > 0:
            bump(Economy.price_key(int(item_id)), "scanned price",
                 lambda: self.economy.record_price(
                     int(item_id), int(outcome.get("price")), source=source))

        if (self.ranker is not None and minutes > 0 and predicted_minutes
                and not math.isinf(predicted_minutes)):
            bump(StrategyRanker.rank_key(level, action), "observed / predicted",
                 lambda: self.ranker.observe(
                     action, level, predicted_minutes, minutes,
                     meta={"source": source}))

        return updates

    # -- drift detection -----------------------------------------------------

    def _drift_reason(self) -> Optional[str]:
        if self.plan is None or self.player is None or self.goal is None:
            return None
        if time.time() - self.created_at > self.max_age_minutes * 60:
            return "stale"

        # Level change invalidates the farm ranking and the level gate.
        if self.plan.root.kind == "level_up":
            target = self.plan.root.meta.get("item_level")
            if target and self.player.level >= target:
                return "level_reached"

        # Re-plan and compare strategies, not just the current step: the whole
        # route can flip (farm -> buy) once the player's kamas change, and that
        # must be noticed even when the old plan is already exhausted.
        fresh = self.planner.plan(self.goal, self.player)
        if not fresh.feasible:
            return None
        current = self.plan.minutes
        if fresh.root.kind != self.plan.root.kind:
            if math.isinf(current) or fresh.minutes < current * 0.7:
                return "cheaper_branch"
        elif not math.isinf(current) and fresh.minutes < current * 0.6:
            return "cheaper_branch"
        return None

    def _replan(self, reason: str) -> None:
        if self.goal is None or self.player is None:
            return
        before = self.plan.minutes if self.plan else None
        self.plan = self.planner.plan(self.goal, self.player)
        self.leaf_cache = self.plan.leaves()
        self.pointer = 0
        self.created_at = time.time()
        self.replans.append({
            "reason": reason,
            "before_minutes": None if before is None or math.isinf(before) else round(before, 1),
            "after_minutes": (None if math.isinf(self.plan.minutes)
                              else round(self.plan.minutes, 1)),
        })
        self._persist()

    # -- introspection -------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        if self.plan is None:
            return {"state": "idle"}
        return {
            "state": "running",
            "goal": self.plan.goal.item_id,
            "minutes_estimated": (None if math.isinf(self.plan.minutes)
                                  else round(self.plan.minutes, 1)),
            "step": self.pointer,
            "steps_total": len(self.leaf_cache),
            "replans": self.replans,
            "missing_tools": self.plan.missing_tools(),
        }

    def active_objective(self) -> Optional[Dict[str, Any]]:
        """The step the planner currently wants executed, without advancing it.

        Returns ``{"tool", "args", "why", "quest_id"?}`` for the leaf at the
        current pointer, or ``None`` when the plan is idle/exhausted. The agent
        runtime reads this as structured context (§C) so a ``quest.advance``
        decision confirms the schedule's chosen ``quest_id`` rather than guessing
        it. Unlike ``tick()``, this does not advance the pointer or record a step.
        """
        if self.plan is None or not self.leaf_cache or self.pointer >= len(self.leaf_cache):
            return None
        leaf = self.leaf_cache[self.pointer]
        if leaf.kind == "blocked":
            return None
        tool = leaf.meta.get("tool")
        if not tool or tool not in EXECUTABLE_TOOLS:
            preflight = leaf.meta.get("preflight_tool")
            if not preflight or preflight not in EXECUTABLE_TOOLS:
                return None
            tool = preflight
        args = dict(leaf.meta.get("args") or {})
        obj: Dict[str, Any] = {"tool": tool, "args": args, "why": leaf.label}
        if tool == "quest.advance" and args.get("quest_id") is not None:
            obj["quest_id"] = args["quest_id"]
        return obj
