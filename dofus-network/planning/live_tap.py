"""Give the planning stack a production caller, without changing what the AI decides.

Measured 2026-11-13 (G126): `build_schedule_stack()` had exactly one construction site,
`agent/serve.py --schedule`, and nothing launches that flag -- the host starts
`run_autopilot.py` from `desktop/aipanel.cs`. `ScheduleManager.submit`, `tick`, `report`
and `record_action` had zero production callers, so the AND/OR planner, the conjugate
posteriors, the strategy ranker, the decision graph and the structure cache (G125) were
all constructed and inert. The live loop had its own `ExperienceStore` for repair
posteriors, but it never fed a tool result into `planning/`.

This module closes the **learning half** of that gap:

* it reuses the loop's *existing* `ExperienceStore` (never a second one -- a forked
  store is the bug `tools/test_online_learning_wiring.py` warns about);
* it sends every executed action through `ScheduleManager.record_action`, so measured
  kills/drops/prices/durations reach the posteriors, traces and decision graph;
* it keeps every state file under the per-user app-data directory, never under
  `dofus-network/data/kb`, because that tree is staged into `setup.exe` and the
  installer is already 4.93 MiB over its byte budget (G129);
* it never raises into the live loop, and `DOFUS_SCHEDULE_TAP=0` switches it off.

It deliberately does **not** close the decision half. `submit()` needs a `Goal(item_id,
qty)`, and the live loop's objective is natural-language quest prose with no trustworthy
item id. Inventing one would put a fabricated `quest_id` in front of the policy, which
is a behaviour change with no falsifier. `objective()` and `submit_goal()` are here so
the seam exists and is testable; nothing in `agent/` calls them.

    python -m unittest planning.test_live_tap
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from planning.economy import PlayerState
from planning.goal_compiler import ActiveGoalFalsifier, GoalCompiler, GoalResolution
from planning.graph_memory import DecisionGraph
from planning.outcome import ENTITY_KEYS
from planning.schedule import build_schedule_stack
from planning.thinking_cache import ThinkingCache
from planning.trace import TraceLog

log = logging.getLogger(__name__)

DISABLE_ENV = "DOFUS_SCHEDULE_TAP"
STATE_NAMES = ("schedule_state.json", "economy_state.json", "traces.jsonl",
               "decision_graph.json", "thinking_cache.json")

# Which posterior family a tool's outcome is evidence about. This is deliberately
# narrow: the 28-tool vocabulary has no crafting tool, and a `state.snapshot` is not
# a farm run. Unmapped tools still get a trace and a graph edge, just no posterior
# update, because `_ingest` only learns when `action in ARMS`.
ARM_BY_TOOL: Dict[str, str] = {
    "hdv.buy": "buy", "shop.buy": "buy",
    "hdv.list": "buy", "hdv.scan": "buy", "hdv.prices": "buy",
    "farm.start": "farm", "fight.join": "farm",
    "quest.advance": "quest",
}


def _entity(call: Any, observation: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Entity ids the caller named, plus the ones the current observation carries."""
    out: Dict[str, Any] = {}
    for source in (observation or {}, getattr(call, "args", None) or {}):
        if not isinstance(source, dict):
            continue
        for key in ENTITY_KEYS:
            if key not in out and source.get(key) is not None:
                out[key] = source[key]
    return out


class ScheduleTap:
    """One live loop <-> planning stack adapter. Construct via `attach()`."""

    def __init__(self, experience: Any, root: Path) -> None:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.paths = {name: root / name for name in STATE_NAMES}
        # `cache=` is a Path here, so the cache lives in app data too. `build_schedule_stack`
        # keeps the economy's save file separate from the schedule's restore file.
        self.manager = build_schedule_stack(
            experience=experience,
            state_file=self.paths["schedule_state.json"],
            traces=TraceLog(self.paths["traces.jsonl"]),
            graph=DecisionGraph(self.paths["decision_graph.json"]),
            cache=ThinkingCache(self.paths["thinking_cache.json"]),
        )
        self.experience = experience
        self.compiler = GoalCompiler()
        self.observed = 0
        self.learned = 0
        self.verified = 0
        self.failed = 0
        self.goals_submitted = 0
        self.goals_verified = 0
        self.goals_rejected = 0
        self.falsified_aborts = 0
        self.active_falsifier: Optional[ActiveGoalFalsifier] = None
        self.last_falsifier: Optional[ActiveGoalFalsifier] = None
        self._warned = False
        self._last_report: Dict[str, Any] = {}

    def observe(self, call: Any, result: Any, *, elapsed_ms: float = 0.0,
                observation: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Feed one executed step. Never raises: a broken learner must not stop a run."""
        try:
            tool = str(getattr(call, "tool", "") or "")
            args = dict(getattr(call, "args", None) or {})
            payload = (result.as_dict() if hasattr(result, "as_dict")
                       else dict(result or {}))
            level = (observation or {}).get("level")
            if isinstance(level, (int, float)) and level > 0:
                # Without a submitted plan `self.player` is None and `_ingest` buckets
                # every posterior into level 1. The observation already knows better.
                self.manager.player = PlayerState(level=int(level))
            report = self.manager.record_action(
                action=ARM_BY_TOOL.get(tool, ""),
                tool=tool,
                args=args,
                result=payload,
                entity=_entity(call, observation),
                elapsed_minutes=max(0.0, float(elapsed_ms)) / 60000.0,
                source=str(payload.get("source") or "live") or "live",
            )
            self.observed += 1
            if report.get("signal") == "verified":
                self.verified += 1
            if report.get("updates"):
                self.learned += 1
            self._last_report = report

            # Condition (b) runtime falsifier check
            self._check_runtime_falsifier(call, result, observation)

            return report
        except Exception as exc:  # noqa: BLE001 - observation must never break the loop
            self.failed += 1
            if not self._warned:
                self._warned = True
                log.warning("schedule tap disabled after %s: %s", type(exc).__name__, exc)
            return None

    def _check_runtime_falsifier(self, call: Any, result: Any,
                                 observation: Optional[Dict[str, Any]]) -> None:
        """Enforce Condition (b): verify progress within K steps or auto-abort to base loop."""
        f = self.active_falsifier
        if f is None or f.confirmed or f.falsified:
            return

        progress = self._detect_progress(f, call, result, observation)
        if progress:
            f.confirmed = True
            f.confirmed_at_step = self.observed
            f.confirmation_reason = progress
            return

        steps_elapsed = self.observed - f.submitted_at_step
        if steps_elapsed >= f.horizon_k:
            f.falsified = True
            self.falsified_aborts += 1
            log.warning(
                "Falsifiable Goal Compiler: goal %s auto-aborted after %d steps without progress",
                f.goal, steps_elapsed
            )
            self.abort_goal(reason=f"falsified_no_progress_within_{f.horizon_k}_steps")

    def _detect_progress(self, f: ActiveGoalFalsifier, call: Any, result: Any,
                         observation: Optional[Dict[str, Any]]) -> Optional[str]:
        # 1. Check inventory delta from observation
        curr_inv = self._extract_inventory_qty(observation, f.item_id)
        if curr_inv > f.initial_inventory_qty:
            return f"inventory_delta:+{curr_inv - f.initial_inventory_qty}"

        # 2. Check result payload from tool execution
        payload = result.as_dict() if hasattr(result, "as_dict") else dict(result or {})
        tool = str(getattr(call, "tool", "") or "")
        ok = getattr(result, "ok", True)
        if ok and tool in ("farm.start", "fight.join", "hdv.buy", "shop.buy"):
            res_item = payload.get("item_id")
            if res_item is not None:
                try:
                    if int(res_item) == f.item_id:
                        drops = int(payload.get("drops") or payload.get("qty") or payload.get("bought") or 0)
                        if drops > 0:
                            return f"tool_produced_item:{tool}:+{drops}"
                except (ValueError, TypeError):
                    pass

        # 3. Check quest advance or counter delta
        if ok and tool == "quest.advance":
            q_id = (getattr(call, "args", None) or {}).get("quest_id") or payload.get("quest_id")
            if q_id is not None and f.quest_id is not None:
                try:
                    if int(q_id) == int(f.quest_id):
                        return "quest_advance_success"
                except (ValueError, TypeError):
                    pass
            elif f.quest_id is None:
                return "quest_advance_success"

        curr_counter = self._extract_quest_counter(observation)
        if curr_counter > f.initial_quest_counter:
            return f"quest_counter_delta:+{curr_counter - f.initial_quest_counter}"

        return None

    @staticmethod
    def _extract_inventory_qty(obs: Optional[Dict[str, Any]], item_id: int) -> int:
        if not obs or not isinstance(obs, dict):
            return 0
        inv = obs.get("inventory") or obs.get("items")
        if isinstance(inv, dict):
            if item_id in inv:
                return int(inv[item_id] or 0)
            if str(item_id) in inv:
                return int(inv[str(item_id)] or 0)
        elif isinstance(inv, list):
            for entry in inv:
                if isinstance(entry, dict):
                    eid = entry.get("item_id") or entry.get("id") or entry.get("gid")
                    if eid is not None:
                        try:
                            if int(eid) == item_id:
                                return int(entry.get("quantity") or entry.get("qty") or entry.get("count") or 1)
                        except (ValueError, TypeError):
                            pass
        return 0

    @staticmethod
    def _extract_quest_counter(obs: Optional[Dict[str, Any]]) -> int:
        if not obs or not isinstance(obs, dict):
            return 0
        for key in ("quest_counter", "quest_step", "step_num", "step", "quest_progress", "objectives_completed"):
            val = obs.get(key)
            if isinstance(val, (int, float)):
                return int(val)
        return 0

    def objective(self) -> Optional[Dict[str, Any]]:
        """The planner's current step, or None. Nothing in `agent/` consumes this yet."""
        return self.manager.active_objective()

    def compile_prose(self, prose: str, observation: Optional[Dict[str, Any]] = None) -> GoalResolution:
        """Resolve prose and verify Condition (a) against quest requirements or recipe DAG."""
        return self.compiler.resolve(prose, observation)

    def submit_prose_goal(self, prose: str, observation: Optional[Dict[str, Any]] = None,
                          level: int = 60, horizon_k: int = 5) -> Optional[Any]:
        """Compile prose and submit to ScheduleManager only if structurally verified.

        Enforces Condition (a) prior to submission and arms Condition (b) runtime tracking.
        Returns the created Plan, or None if rejected / structurally falsified.
        """
        resolution = self.compiler.resolve(prose, observation)
        if not resolution.verified or resolution.goal is None:
            self.goals_rejected += 1
            log.info("Goal rejected by structural falsifier: %s (%s)", prose, resolution.verification_reason)
            return None

        self.goals_verified += 1
        self.goals_submitted += 1
        plan = self.manager.submit(
            resolution.goal,
            PlayerState(level=int(level), kamas=0),
            goal_text=prose,
        )

        init_inv = self._extract_inventory_qty(observation, resolution.item_id)
        init_counter = self._extract_quest_counter(observation)
        self.active_falsifier = ActiveGoalFalsifier(
            goal=resolution.goal,
            prose=prose,
            item_id=int(resolution.item_id),
            target_qty=int(resolution.qty),
            quest_id=resolution.quest_id,
            guide_id=resolution.guide_id,
            submitted_at_step=self.observed,
            horizon_k=max(1, int(horizon_k)),
            initial_inventory_qty=init_inv,
            initial_quest_counter=init_counter,
        )
        return plan

    def abort_goal(self, reason: str = "aborted") -> None:
        """Cancel active plan and reset to base loop state."""
        self.manager.abort(reason=reason)
        if self.active_falsifier is not None:
            self.active_falsifier.aborted = True
            self.last_falsifier = self.active_falsifier
            self.active_falsifier = None

    def submit_goal(self, item_id: int, qty: int = 1, level: int = 60) -> Any:
        """Explicit opt-in: direct submission without prose compilation."""
        from planning.planner import Goal

        return self.manager.submit(Goal(int(item_id), int(qty)),
                                   PlayerState(level=int(level), kamas=0))

    def stats(self) -> Dict[str, Any]:
        return {
            "observed": self.observed, "verified": self.verified,
            "learned": self.learned, "failed": self.failed,
            "params": len(self.experience), "events": self.experience.events_seen,
            "objective": self.objective() is not None,
            "root": str(self.root),
            "compiler": {
                "submitted": self.goals_submitted,
                "verified": self.goals_verified,
                "rejected": self.goals_rejected,
                "aborts": self.falsified_aborts,
                "active_goal": self.active_falsifier.to_dict() if self.active_falsifier else None,
            },
        }


def attach(experience: Any, root: Optional[Path] = None,
           env: Optional[Dict[str, str]] = None) -> Optional[ScheduleTap]:
    """Build the tap, or return None for any reason at all. Startup stays safe."""
    environ = env if env is not None else os.environ
    if environ.get(DISABLE_ENV, "1") == "0" or experience is None:
        return None
    try:
        if root is None:
            root = Path(environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm"
        return ScheduleTap(experience, root)
    except Exception as exc:  # noqa: BLE001 - never block a run on the planner
        log.warning("schedule tap unavailable: %s: %s", type(exc).__name__, exc)
        return None
