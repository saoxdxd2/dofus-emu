"""Decision graph memory: the long-term curve.

The instant curve is one action's residual. This is the other one: the graph the
planner reasons over, accumulating evidence until it changes shape.

Three things evolve here
------------------------
1. **Edge statistics.** Every ``(strategy, entity)`` pair — "farm monster 47",
   "buy item 31631", "craft item 884" — carries a Beta success posterior and a
   log-normal duration posterior. Ten failed farms on a monster make the planner
   stop recommending it, without anyone writing a rule.

2. **Constraints.** When a route fails repeatedly and the failure looks
   level-related, the graph records ``blocked_below``. That is a *structural*
   change: the edge is pruned for low levels instead of merely being expensive.

3. **Discoveries.** When an action reveals a link the cache did not have (a drop
   observed from a monster that is not in ``drop_monster_ids``), the graph adds
   the edge with low confidence. The map therefore diverges from the static cache
   over time — which is the point: "it can vary and change from the original".

The graph never overrides feasibility or safety. It only re-weights and prunes
what the planner already considers possible.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from planning.experience import BetaRate, LogNormalDuration
from planning.kb import REPO_ROOT
from planning.trace import Trace

from agent.inert_attribution import first_failure, LAYERS  # noqa: E402  (import after planning modules is intentional; agent.inert_attribution imports only stdlib)

# Inert failure layers that must NOT poison the DecisionGraph posterior.
# These are infra/client/sensing faults, not genuine gameplay outcomes.
# Taken directly from ``agent.inert_attribution.LAYERS``.
INERT_LAYERS = frozenset({
    "dispatch:no_client",
    "dispatch:failed",
    "sensing:stale",
    "client:dialog",
})

DEFAULT_GRAPH = REPO_ROOT / "data" / "kb" / "decision_graph.json"

# A route that fails this many times below a level gets a structural constraint.
BLOCK_MIN_ATTEMPTS = 4
BLOCK_MAX_SUCCESS = 0.25

# BOCPD thresholds.
# When the posterior predictive probability of an observed outcome falls below
# this, a change point is suspected; after CONFIRM_RUNS consecutive surprises
# the posterior is reset to the prior.
BOCPD_SURPRISE_THRESHOLD = 0.05
BOCPD_CONFIRM_RUNS = 3


def _row_from_trace(trace: Trace) -> Dict[str, Any]:
    """Build a minimal row dict for ``agent.inert_attribution.first_failure``.

    ``first_failure`` reads ``refused``/``error`` text and classifies by keyword,
    then falls back to ``signals(row)`` (``ok``, ``repeat_streak``, ``obs_age_ms``,
    etc.). Traces carry the failure reason as a string, so we feed it into both
    ``refused`` and ``error`` to get the same keyword classification the corpus
    report uses.
    """
    reason = trace.outcome.reason or ""
    return {
        "refused": reason,
        "error": reason,
        "ok": trace.outcome.signal != "verified",
    }


def classify_outcome(trace: Trace) -> str:
    """Classify a trace's outcome using ``agent.inert_attribution.first_failure``.

    ``first_failure`` only detects ``sensing:stale`` via ``obs_age_ms`` in
    ``signals()``, which traces don't carry. We short-circuit on the reason text
    for that case so stale observations are still gated out.
    """
    reason = trace.outcome.reason or ""
    if "stale" in reason.lower():
        return "sensing:stale"
    return first_failure(_row_from_trace(trace))


def is_inert_layer(layer: str) -> bool:
    """True when ``layer`` is an infra/client/sensing fault that must not update the graph."""
    return layer in INERT_LAYERS


class BOCPD:
    """Bayesian Online Change-Point Detection for a single posterior.

    Tracks the run length of consecutive observations that are consistent with
    the current posterior. When the posterior predictive probability of an
    observed outcome falls below ``BOCPD_SURPRISE_THRESHOLD`` for
    ``BOCPD_CONFIRM_RUNS`` consecutive observations, a change point is declared
    and the posterior is reset to its prior.

    This lets gear upgrades or level-ups rapidly reset stale failure priors:
    after a level-up, a previously-impossible route starts succeeding, the
    predictive probability of the old (low) success rate drops, and the prior
    is reset so the edge is re-evaluated from scratch.
    """

    def __init__(self) -> None:
        self.run_length = 0
        self.surprise_run = 0

    def observe(self, predictive_prob: float) -> bool:
        """Feed one observation's posterior predictive probability.

        Returns ``True`` when a change point was detected and the posterior
        should be reset.
        """
        if predictive_prob < BOCPD_SURPRISE_THRESHOLD:
            self.surprise_run += 1
        else:
            self.surprise_run = 0
        self.run_length += 1

        if self.surprise_run >= BOCPD_CONFIRM_RUNS:
            self.run_length = 0
            self.surprise_run = 0
            return True
        return False

    def reset(self) -> None:
        """Reset the change-point detector (called after a posterior reset)."""
        self.run_length = 0
        self.surprise_run = 0


def entity_key(action: str, entity: Dict[str, Any]) -> Optional[str]:
    """Stable key for the thing acted on, from whatever the trace recorded."""
    if action in ("farm", "fight"):
        for name in ("monster_id", "target"):
            if entity.get(name) is not None:
                return f"monster:{entity[name]}"
    if action in ("buy", "craft", "sell", "scan"):
        if entity.get("item_id") is not None:
            return f"item:{entity['item_id']}"
        if entity.get("item"):
            return f"item:{entity['item']}"
    if action in ("quest", "advance"):
        if entity.get("quest_id") is not None:
            return f"quest:{entity['quest_id']}"
    if action == "goto":
        for name in ("map_id", "zone_id"):
            if entity.get(name) is not None:
                return f"map:{entity[name]}"
    return None


@dataclass
class Edge:
    key: str
    action: str
    success: BetaRate = field(default_factory=lambda: BetaRate(2.0, 2.0))
    time: LogNormalDuration = field(default_factory=lambda: LogNormalDuration(10.0, 2.0))
    attempts: int = 0
    blocked_below: Optional[int] = None
    last_ts: float = 0.0
    discovered: bool = False        # learned at runtime, not in the static cache
    notes: List[str] = field(default_factory=list)
    bocpd: BOCPD = field(default_factory=BOCPD)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "action": self.action,
            "success": self.success.to_dict(),
            "time": self.time.to_dict(),
            "attempts": self.attempts,
            "blocked_below": self.blocked_below,
            "last_ts": self.last_ts,
            "discovered": self.discovered,
            "notes": self.notes[-5:],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Edge":
        edge = cls(
            key=payload["key"],
            action=payload.get("action", "?"),
            success=BetaRate.from_dict(payload.get("success", {})),
            time=LogNormalDuration.from_dict(payload.get("time", {})),
            attempts=int(payload.get("attempts") or 0),
            blocked_below=payload.get("blocked_below"),
            last_ts=float(payload.get("last_ts") or 0.0),
            discovered=bool(payload.get("discovered")),
            notes=list(payload.get("notes") or []),
        )
        edge.bocpd = BOCPD()
        return edge


class DecisionGraph:
    """Edge statistics, constraints and discoveries, accumulated from traces."""

    def __init__(self, path: Path = DEFAULT_GRAPH) -> None:
        self.path = Path(path)
        self.edges: Dict[str, Edge] = {}
        # Same rule as the experience store: simulated and test actions must not
        # reshape the map. None would accept everything (demos only).
        self.sources_allowed: Optional[set] = {"live", "manual"}
        self.rejected = 0
        self.load()

    # -- persistence ---------------------------------------------------------

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for blob in (payload.get("edges") or {}).values():
            try:
                edge = Edge.from_dict(blob)
            except (KeyError, TypeError):
                continue
            self.edges[edge.key] = edge

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "edges": {key: edge.as_dict() for key, edge in sorted(self.edges.items())},
        }, indent=1), encoding="utf-8")

    # -- updating (the long-term curve) --------------------------------------

    def _edge(self, key: str, action: str) -> Edge:
        edge = self.edges.get(key)
        if edge is None:
            edge = Edge(key=key, action=action)
            self.edges[key] = edge
        return edge

    def update_from_trace(self, trace: Trace) -> Optional[Dict[str, Any]]:
        """Fold one action into the graph. Returns what changed, for the trace.

        Infra/client/sensing failures (``dispatch:*``, ``sensing:stale``,
        ``client:dialog``) are gated out by ``agent.inert_attribution`` so they
        cannot poison a valid edge's posterior. Only genuine gameplay outcomes
        update the world graph.

        Bayesian Online Change-Point Detection (BOCPD) runs on every accepted
        observation: when the posterior predictive probability of the observed
        outcome stays below threshold for ``BOCPD_CONFIRM_RUNS`` consecutive
        observations, the posterior is reset to its prior. This lets gear
        upgrades or level-ups rapidly reset stale failure priors.
        """
        if trace.outcome is None:
            return None
        if self.sources_allowed is not None and trace.source not in self.sources_allowed:
            self.rejected += 1
            return None
        key = entity_key(trace.action, trace.entity)
        if key is None:
            return None

        # Gate: inert failures must not reshape the world graph.
        layer = classify_outcome(trace)
        if is_inert_layer(layer):
            self.rejected += 1
            return None

        edge = self._edge(key, trace.action)

        achieved = trace.outcome.achieved
        if achieved is None:
            return None   # unverified: no success signal, but time may still help

        # BOCPD: compute the posterior predictive probability *before* updating,
        # so a sudden string of surprises can trigger a prior reset.
        if achieved:
            predictive = edge.success.mean()
        else:
            predictive = 1.0 - edge.success.mean()
        if edge.bocpd.observe(predictive):
            # Change point detected: reset both posteriors to their priors.
            edge.success = BetaRate(2.0, 2.0)
            edge.time = LogNormalDuration(10.0, 2.0)
            edge.bocpd.reset()
            edge.notes.append(
                "change point: posterior reset after %d surprises"
                % BOCPD_CONFIRM_RUNS)

        edge.attempts += 1
        edge.success.update(1.0 if achieved else 0.0, 1.0, trace.ts)
        edge.last_ts = trace.ts

        change: Dict[str, Any] = {
            "edge": key,
            "action": trace.action,
            "attempts": edge.attempts,
            "success_rate": round(edge.success.mean(), 3),
        }

        observed = trace.outcome.get("minutes")
        if observed > 0:
            edge.time.update(observed, 1.0, trace.ts)
            change["observed_minutes"] = round(observed, 1)

        # Structural change: repeated failure at a level becomes a constraint.
        if (edge.blocked_below is None
                and edge.attempts >= BLOCK_MIN_ATTEMPTS
                and edge.success.mean() < BLOCK_MAX_SUCCESS):
            level = trace.entity.get("monster_level") or trace.entity.get("level")
            if isinstance(level, (int, float)):
                edge.blocked_below = int(level)
                edge.notes.append(
                    f"blocked below {edge.blocked_below}: "
                    f"{edge.success.mean():.0%} success over {edge.attempts} attempts")
                change["blocked_below"] = edge.blocked_below

        return change

    def discover_drop(self, monster_id: int, item_id: int, note: str = "") -> bool:
        """Add an edge the static cache did not have. Returns True if it is new."""
        key = f"monster:{monster_id}"
        edge = self._edge(key, "farm")
        marker = f"drops item {item_id}"
        if marker in edge.notes:
            return False
        edge.discovered = True
        edge.notes.append(marker + (f" ({note})" if note else ""))
        return True

    # -- querying ------------------------------------------------------------

    def success_rate(self, action: str, entity: Dict[str, Any]) -> Optional[float]:
        key = entity_key(action, entity)
        if key is None:
            return None
        edge = self.edges.get(key)
        if edge is None or edge.attempts == 0:
            return None
        return edge.success.mean()

    def expected_minutes(self, action: str, entity: Dict[str, Any]) -> Optional[float]:
        key = entity_key(action, entity)
        if key is None:
            return None
        edge = self.edges.get(key)
        if edge is None or edge.time.n < 1:
            return None
        return edge.time.mean()

    def penalty(self, action: str, entity: Dict[str, Any]) -> float:
        """Multiplier for the planner's estimate: an unreliable route costs more.

        ``1 / success`` is the expected number of attempts per success, which is
        exactly the right inflation for a route that only works 40% of the time.
        """
        rate = self.success_rate(action, entity)
        if rate is None or rate >= 0.9:
            return 1.0
        return min(5.0, 1.0 / max(rate, 0.2))

    def blocked(self, action: str, entity: Dict[str, Any], level: Optional[int] = None) -> bool:
        key = entity_key(action, entity)
        if key is None:
            return False
        edge = self.edges.get(key)
        if edge is None or edge.blocked_below is None:
            return False
        return level is not None and level < edge.blocked_below

    def constraints(self) -> List[Dict[str, Any]]:
        return [
            {"edge": edge.key, "action": edge.action,
             "blocked_below": edge.blocked_below,
             "success_rate": round(edge.success.mean(), 3),
             "attempts": edge.attempts}
            for edge in self.edges.values() if edge.blocked_below is not None
        ]

    def discoveries(self) -> List[Dict[str, Any]]:
        return [
            {"edge": edge.key, "notes": edge.notes[-3:]}
            for edge in self.edges.values() if edge.discovered
        ]

    def stats(self) -> Dict[str, Any]:
        if not self.edges:
            return {"edges": 0}
        attempts = sum(edge.attempts for edge in self.edges.values())
        return {
            "edges": len(self.edges),
            "with_evidence": sum(1 for edge in self.edges.values() if edge.attempts),
            "attempts": attempts,
            "constraints": len(self.constraints()),
            "discoveries": sum(1 for edge in self.edges.values() if edge.discovered),
            "rejected_sources": self.rejected,
        }
