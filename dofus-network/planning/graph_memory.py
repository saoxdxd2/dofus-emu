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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from planning.experience import BetaRate, LogNormalDuration
from planning.kb import REPO_ROOT
from planning.trace import Trace

DEFAULT_GRAPH = REPO_ROOT / "data" / "kb" / "decision_graph.json"

# A route that fails this many times below a level gets a structural constraint.
BLOCK_MIN_ATTEMPTS = 4
BLOCK_MAX_SUCCESS = 0.25


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
        edge = cls(key=payload["key"], action=payload.get("action", "?"))
        if isinstance(payload.get("success"), dict):
            edge.success = BetaRate.from_dict(payload["success"])
        if isinstance(payload.get("time"), dict):
            edge.time = LogNormalDuration.from_dict(payload["time"])
        edge.attempts = int(payload.get("attempts") or 0)
        edge.blocked_below = payload.get("blocked_below")
        edge.last_ts = float(payload.get("last_ts") or 0.0)
        edge.discovered = bool(payload.get("discovered"))
        edge.notes = list(payload.get("notes") or [])
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
        """Fold one action into the graph. Returns what changed, for the trace."""
        if trace.outcome is None:
            return None
        if self.sources_allowed is not None and trace.source not in self.sources_allowed:
            self.rejected += 1
            return None
        key = entity_key(trace.action, trace.entity)
        if key is None:
            return None
        edge = self._edge(key, trace.action)

        achieved = trace.outcome.achieved
        if achieved is None:
            return None   # unverified: no success signal, but time may still help
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
