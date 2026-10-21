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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from planning.economy import Economy, PlayerState
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
    ) -> None:
        self.planner = planner
        self.economy = economy
        self.ranker = ranker
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

    def submit(self, goal: Goal, player: PlayerState) -> Plan:
        self.goal = goal
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
        """Feed a tool result back. Returns ``{'status': ..., 'reason': ...}``."""
        if not self.history:
            return {"status": "ignored", "reason": "no step running"}

        step = self.history[-1]
        step.finished_at = time.time()
        step.outcome = "ok" if result.get("ok") else "failed"
        elapsed = (step.finished_at - step.started_at) / 60.0

        status, reason = "continue", ""

        # ---- learning: this is the only place the system gets smarter -----
        data = result.get("data") or {}
        source = str(result.get("source") or "live")
        leaf = self.leaf_cache[self.pointer] if self.pointer < len(self.leaf_cache) else None
        if isinstance(data, dict):
            kills = int(data.get("kills") or 0)
            drops = int(data.get("drops") or 0)
            farm_minutes = float(data.get("minutes") or elapsed or 0.0)
            item_id = leaf.meta.get("item_id") if leaf is not None else None
            monster_id = leaf.meta.get("monster_id") if leaf is not None else None

            # 1. drop rate: Beta posterior per item
            if step.kind == "farm" and kills and item_id:
                self.economy.record_drop_run(int(item_id), drops, kills, source=source)
            # 2. throughput: Gamma posterior per monster x level band
            if step.kind == "farm" and kills and monster_id and farm_minutes > 0:
                self.economy.cost.record_farm(
                    int(monster_id), self.player.level if self.player else 1,
                    kills, farm_minutes, source=source)
            # 3. market price: log-normal with a 3-day half-life
            if step.kind == "buy" and data.get("price"):
                self.economy.record_price(
                    int(data.get("item_id") or 0), int(data["price"]), source=source)
            # 4. strategy calibration: did this arm take as long as predicted?
            if (self.ranker is not None and step.kind in ("buy", "farm", "craft", "quest")
                    and elapsed > 0 and not math.isinf(step.expected_minutes)):
                self.ranker.observe(
                    step.kind,
                    self.player.level if self.player else 1,
                    step.expected_minutes,
                    elapsed,
                    meta={"label": step.label, "source": result.get("source", "live")},
                )

        if not result.get("ok"):
            status, reason = "replan", "step_failed"
        elif not math.isinf(step.expected_minutes) and elapsed > step.expected_minutes * self.overrun_factor:
            status, reason = "replan", "overrun"

        self.pointer += 1
        if self.pointer >= len(self.leaf_cache):
            status = "done" if status == "continue" else status

        if status == "replan":
            self._replan(reason)

        self.economy.record_run({
            "kind": step.kind, "label": step.label,
            "minutes": round(elapsed, 2), "kamas": int(data.get("kamas") or 0),
            "ok": bool(result.get("ok")),
        })
        self.economy.save()
        self._persist()
        return {"status": status, "reason": reason, "elapsed_minutes": round(elapsed, 2)}

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
