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
        self.observed = 0
        self.learned = 0
        self.verified = 0
        self.failed = 0
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
            return report
        except Exception as exc:  # noqa: BLE001 - observation must never break the loop
            self.failed += 1
            if not self._warned:
                self._warned = True
                log.warning("schedule tap disabled after %s: %s", type(exc).__name__, exc)
            return None

    def objective(self) -> Optional[Dict[str, Any]]:
        """The planner's current step, or None. Nothing in `agent/` consumes this yet."""
        return self.manager.active_objective()

    def submit_goal(self, item_id: int, qty: int = 1, level: int = 60) -> Any:
        """Explicit opt-in: the only way this tap ever produces an objective."""
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
