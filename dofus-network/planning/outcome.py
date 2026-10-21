"""Outcome extraction: turn a raw tool result into a learning signal.

The problem this solves
-----------------------
A tool result is *raw payload*, not a lesson. In the existing logs a single
``quest.list`` result can be 30–1976 full quest records with five localisations
each, while a farm result would carry counters — and ``ok`` means "the call was
executed", which is true even when the action achieved nothing. Feeding that
straight into learning mixes two different jobs:

    tool result   ->  what the backend returned      (raw, bulky, shape varies)
    outcome       ->  what actually happened          (normalised, small, learnable)

This module is the boundary between them. It never invents a signal: if the
result carries no verifiable quantity, the outcome is ``unverified`` and the
learning layer will refuse to update anything from it.

What counts as verified
-----------------------
A quantity that the game measured: kills, drops, elapsed minutes, kamas, a price,
an XP delta, a level change, a position. Anything else (a list of quests, a path,
a search hit) proves the *call* worked, not that the *goal* advanced.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Result keys that carry a measurable quantity, with their unit conversion.
# The extractor searches the whole result tree, so it does not care which
# backend produced the shape (local cache, Sentinel, or the client bridge).
QUANTITY_KEYS: Dict[str, Tuple[str, float]] = {
    # farm / fight
    "kills": ("kills", 1.0),
    "kill_count": ("kills", 1.0),
    "killed": ("kills", 1.0),
    "mobs_killed": ("kills", 1.0),
    "drops": ("drops", 1.0),
    "loot": ("drops", 1.0),
    "items_obtained": ("drops", 1.0),
    "fights": ("fights", 1.0),
    "wins": ("wins", 1.0),
    "deaths": ("deaths", 1.0),
    # time — the unit matters, so each key states its own conversion
    "minutes": ("minutes", 1.0),
    "duration_min": ("minutes", 1.0),
    "duration_minutes": ("minutes", 1.0),
    "elapsed_min": ("minutes", 1.0),
    "elapsed_minutes": ("minutes", 1.0),
    "duration_s": ("minutes", 1.0 / 60.0),
    "duration_sec": ("minutes", 1.0 / 60.0),
    "elapsed_s": ("minutes", 1.0 / 60.0),
    "elapsed_seconds": ("minutes", 1.0 / 60.0),
    "duration_ms": ("minutes", 1.0 / 60000.0),
    "elapsed_ms": ("minutes", 1.0 / 60000.0),
    # economy
    "kamas": ("kamas", 1.0),
    "kamas_gained": ("kamas", 1.0),
    "price": ("price", 1.0),
    "unit_price": ("price", 1.0),
    "median_price": ("price", 1.0),
    "quantity": ("quantity", 1.0),
    # progression
    "xp": ("xp", 1.0),
    "xp_gained": ("xp", 1.0),
    "level": ("level", 1.0),
    "level_after": ("level", 1.0),
}

# Keys whose value identifies *what* was acted on, for graph memory.
ENTITY_KEYS = (
    "item_id", "monster_id", "npc_id", "map_id", "quest_id", "zone_id",
    "instance", "target",
)

# Where to look first. Preferring the narrowest container keeps a nested
# ``request`` echo from shadowing the real counters.
PREFERRED_CONTAINERS = ("data", "result", "response", "stats", "counters", "summary")

MAX_DEPTH = 6


@dataclass
class Outcome:
    """What actually happened, in a shape the learning layer can consume."""

    signal: str                      # verified | unverified | failed
    achieved: Optional[bool]         # None when unverified
    reason: str = ""
    quantities: Dict[str, float] = field(default_factory=dict)
    entities: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    raw_bytes: int = 0

    @property
    def learnable(self) -> bool:
        return self.signal == "verified" and bool(self.quantities)

    def get(self, key: str, default: float = 0.0) -> float:
        return float(self.quantities.get(key, default))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "signal": self.signal,
            "achieved": self.achieved,
            "reason": self.reason,
            "quantities": {k: round(v, 4) for k, v in self.quantities.items()},
            "entities": self.entities,
            "latency_ms": round(self.latency_ms, 2),
        }


def _walk(node: Any, depth: int = 0) -> Iterable[Tuple[str, Any]]:
    """Yield (key, value) for every scalar in the tree, breadth-first-ish."""
    if depth > MAX_DEPTH or not isinstance(node, dict):
        return
    for key, value in node.items():
        if isinstance(value, (str, int, float, bool)) and not isinstance(value, bool):
            yield key, value
        elif isinstance(value, dict):
            yield from _walk(value, depth + 1)


def _collect(result: Any) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """Pull known quantities and entity ids out of an arbitrary result tree."""
    quantities: Dict[str, float] = {}
    entities: Dict[str, Any] = {}

    # Search the preferred containers first so a nested echo of the request
    # cannot win over the real counters.
    roots: List[Any] = []
    if isinstance(result, dict):
        for name in PREFERRED_CONTAINERS:
            if isinstance(result.get(name), dict):
                roots.append(result[name])
        roots.append(result)
    else:
        roots.append(result)

    for root in roots:
        for key, value in _walk(root):
            lowered = str(key).lower()
            if lowered in QUANTITY_KEYS:
                name, scale = QUANTITY_KEYS[lowered]
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(number) and name not in quantities:
                    quantities[name] = number * scale
            elif lowered in ENTITY_KEYS and lowered not in entities:
                entities[lowered] = value
    return quantities, entities


def _size_of(result: Any) -> int:
    """Rough payload size, so the trace can show how much was thrown away."""
    try:
        return len(str(result))
    except Exception:  # noqa: BLE001 - never let logging break the loop
        return 0


def extract(
    tool: str,
    args: Optional[Dict[str, Any]],
    result: Any,
    ok: bool,
    error: Optional[str] = None,
    latency_ms: float = 0.0,
) -> Outcome:
    """Normalise one tool result into an ``Outcome``.

    ``ok`` is execution success, not goal success — this function is where that
    distinction is made explicit.
    """
    args = args or {}
    size = _size_of(result)

    if not ok:
        entities = {k: args[k] for k in ENTITY_KEYS if k in args}
        return Outcome(
            signal="failed",
            achieved=False,
            reason=(error or "tool reported failure")[:300],
            entities=entities,
            latency_ms=latency_ms,
            raw_bytes=size,
        )

    quantities, entities = _collect(result)
    for key in ENTITY_KEYS:
        if key in args and key not in entities:
            entities[key] = args[key]

    if quantities:
        return Outcome(
            signal="verified",
            achieved=True,
            reason=f"{len(quantities)} measured quantities",
            quantities=quantities,
            entities=entities,
            latency_ms=latency_ms,
            raw_bytes=size,
        )

    # The call worked, but nothing measurable came back. That is not a success
    # to learn from, and pretending otherwise is how a model learns nonsense.
    return Outcome(
        signal="unverified",
        achieved=None,
        reason=f"{tool} returned data but no measurable outcome",
        entities=entities,
        latency_ms=latency_ms,
        raw_bytes=size,
    )


def intent_achieved(tool: str, outcome: Outcome, expected: Optional[Dict[str, Any]] = None) -> Optional[bool]:
    """Did the action satisfy its intent? Separate from "did it run".

    Used by the graph memory, which needs a boolean per (strategy, entity) rather
    than a bag of numbers.
    """
    if outcome.signal == "failed":
        return False
    if outcome.signal == "unverified":
        return None
    expected = expected or {}
    if tool == "farm.start":
        want = int(expected.get("kill_count") or 0)
        got = outcome.get("kills")
        if want and got:
            return got >= 0.8 * want
        return got > 0
    if tool in ("hdv.scan", "hdv.list"):
        return outcome.get("price") is not None
    if tool == "fight.join":
        return outcome.get("wins", 0) > 0 or outcome.get("fights", 0) > 0
    return True
