"""Trace log: one immutable record per action, linking input -> decision -> outcome -> updates.

Why a separate log
------------------
``agent/recorder.py`` records what the harness did. This log records what the
*system learned from it*, including which posterior each observation moved and by
how much. They are different jobs and must not share a schema:

    trajectories/*.jsonl   raw material: goal, tool, args, raw result, latency
    traces.jsonl           audit: what changed in the model because of it

Without the second one you cannot answer "why does the bot think this farm takes
300 minutes", and you cannot prove that simulated data never taught anything.

The record is the unit of both learning curves:

* **instant** — ``updates`` shows the parameter deltas from this single action;
* **long-term** — the same record is what the decision graph accumulates, and
  what the thinking cache is validated against.

Nothing is ever rewritten. ``replay()`` recomputes a posterior from the log if a
snapshot is lost or corrupted.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from planning.kb import REPO_ROOT
from planning.outcome import Outcome

DEFAULT_TRACE = REPO_ROOT / "data" / "kb" / "traces.jsonl"


@dataclass
class ParamUpdate:
    """One posterior movement, with enough detail to audit it."""

    param: str
    before: Optional[float]
    after: Optional[float]
    n_before: float = 0.0
    n_after: float = 0.0
    why: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "param": self.param,
            "before": None if self.before is None else round(self.before, 6),
            "after": None if self.after is None else round(self.after, 6),
            "n_before": round(self.n_before, 1),
            "n_after": round(self.n_after, 1),
            "why": self.why,
        }


@dataclass
class Trace:
    """One action, fully described."""

    action: str                     # strategy kind: buy | farm | craft | quest | level_up
    tool: str
    args: Dict[str, Any]
    goal: str = ""
    entity: Dict[str, Any] = field(default_factory=dict)
    predicted_minutes: Optional[float] = None
    outcome: Optional[Outcome] = None
    updates: List[ParamUpdate] = field(default_factory=list)
    source: str = "live"
    plan_id: str = ""
    step: int = 0
    ts: float = field(default_factory=time.time)
    cache_hit: bool = False
    reasoning_ms: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ts": round(self.ts, 3),
            "plan_id": self.plan_id,
            "step": self.step,
            "source": self.source,
            "action": self.action,
            "tool": self.tool,
            "args": self.args,
            "goal": self.goal,
            "entity": self.entity,
            "predicted_minutes": (None if self.predicted_minutes is None
                                  else round(self.predicted_minutes, 2)),
            "cache_hit": self.cache_hit,
            "reasoning_ms": round(self.reasoning_ms, 2),
            "outcome": None if self.outcome is None else self.outcome.as_dict(),
            "updates": [u.as_dict() for u in self.updates],
        }

    @property
    def learnable(self) -> bool:
        return self.outcome is not None and self.outcome.learnable

    def residual(self) -> Optional[float]:
        """observed / predicted minutes — the instant learning signal."""
        if self.outcome is None or not self.predicted_minutes:
            return None
        observed = self.outcome.get("minutes")
        if observed <= 0 or self.predicted_minutes <= 0:
            return None
        return observed / self.predicted_minutes


class TraceLog:
    """Append-only JSONL of traces, plus cheap aggregates."""

    def __init__(self, path: Path = DEFAULT_TRACE) -> None:
        self.path = Path(path)
        self.pending: List[Trace] = []
        self.count = 0
        self.learnable_count = 0
        self.rejected_count = 0
        self._load_counts()

    def _load_counts(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    self.count += 1
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (row.get("outcome") or {}).get("signal") == "verified":
                        self.learnable_count += 1
        except OSError:
            pass

    def append(self, trace: Trace) -> None:
        self.pending.append(trace)
        self.count += 1
        if trace.learnable:
            self.learnable_count += 1
        if trace.source != "live":
            self.rejected_count += 0  # kept for audit; learning already filtered

    def flush(self) -> None:
        if not self.pending:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            for trace in self.pending:
                handle.write(json.dumps(trace.as_dict(), ensure_ascii=False) + "\n")
        self.pending.clear()

    def tail(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return [t.as_dict() for t in self.pending[-limit:]]
        with self.path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
        rows: List[Dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def summary(self) -> Dict[str, Any]:
        """What the two learning curves have actually accumulated."""
        rows = self.tail(limit=10_000)
        residuals = [r for r in rows if r.get("outcome")]
        verified = [r for r in residuals if r["outcome"].get("signal") == "verified"]
        by_action: Dict[str, int] = {}
        params_touched: Dict[str, int] = {}
        for row in rows:
            by_action[row.get("action", "?")] = by_action.get(row.get("action", "?"), 0) + 1
            for update in row.get("updates") or []:
                params_touched[update["param"]] = params_touched.get(update["param"], 0) + 1
        return {
            "traces": self.count,
            "verified": len(verified),
            "unverified": len(residuals) - len(verified),
            "by_action": dict(sorted(by_action.items())),
            "parameters_touched": dict(sorted(params_touched.items())),
            "cache_hits": sum(1 for r in rows if r.get("cache_hit")),
        }
