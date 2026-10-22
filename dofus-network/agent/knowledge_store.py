"""The mutable knowledge layer — what the harness owns so the model doesn't have to.

The rule for this project: **the model is not a database.** Facts live here, where
they can be looked up, corrected and deleted in one edit; the model learns to *use*
tools and to *manage* this store, which is a much smaller and more transferable
skill than memorising 24,000 items.

Three consequences that shape the design:

1. **Only discoveries go in.** Anything already in the DofusDB cache is looked up,
   not copied. A store that duplicates the KB would be as big as the KB and as
   stale as the model — the opposite of the goal.
2. **Every fact carries provenance.** `source` (human / observed / derived / guess),
   confidence, when it was first and last seen, and how many times it was confirmed.
   A fact with no source is a rumour.
3. **Facts can die.** A contradicted fact accumulates disputes and is dropped after
   three; an unused fact expires. Otherwise the store only grows, and a growing store
   is a slow store.

Kept dependency-free: it runs in the embedded runtime next to the agent.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Higher wins a conflict. Same discipline as label weighting: a human correction
# outranks what the loop inferred, and an observation outranks a guess.
SOURCE_RANK = {"human": 3, "observed": 2, "derived": 1, "guess": 0}

DEFAULT_TTL_DAYS = 45          # unused for this long and it is dropped
DISPUTES_TO_DROP = 3           # contradicted this many times and it is dropped


def _now() -> float:
    return time.time()


@dataclass
class Fact:
    kind: str                  # "npc_map", "monster_drop", "price", "quest_prereq"...
    key: str                   # "npc:447", "item:1746", ...
    value: Any
    source: str = "observed"
    confidence: float = 0.5
    first_seen: float = field(default_factory=_now)
    last_seen: float = field(default_factory=_now)
    hits: int = 0              # times the fact was used and not contradicted
    disputes: int = 0          # times it was contradicted

    @property
    def rank(self) -> int:
        return SOURCE_RANK.get(self.source, 0)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class KnowledgeStore:
    """Append-only fact log with an in-memory index and a compaction pass."""

    def __init__(self, path: Optional[Path] = None, ttl_days: float = DEFAULT_TTL_DAYS) -> None:
        self.path = Path(path) if path else Path(
            os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm" / "knowledge.jsonl"
        self.ttl = ttl_days * 86400.0
        self._facts: Dict[Tuple[str, str], Fact] = {}
        self.load()

    # -- persistence -------------------------------------------------------

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except ValueError:
                        continue
                    fact = Fact(**raw)
                    self._facts[(fact.kind, fact.key)] = fact
        except OSError:
            pass

    def _append(self, fact: Fact) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(fact.as_dict(), ensure_ascii=False) + "\n")
        except OSError:
            pass

    def compact(self) -> int:
        """Rewrite the log with only the live facts. Returns how many were dropped.

        The append-only log is what makes the store auditable; compaction is what
        keeps it from growing without bound. Both matter, so both exist.
        """
        self.expire()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                for fact in self._facts.values():
                    fh.write(json.dumps(fact.as_dict(), ensure_ascii=False) + "\n")
            tmp.replace(self.path)
        except OSError:
            pass
        return len(self._facts)

    # -- writing -----------------------------------------------------------

    def remember(self, kind: str, key: str, value: Any, source: str = "observed",
                 confidence: float = 0.5) -> Tuple[bool, str]:
        """Add or update a fact. Returns (changed, note)."""
        if not kind or not key:
            return False, "kind and key are required"
        if source not in SOURCE_RANK:
            return False, "unknown source %r" % source

        existing = self._facts.get((kind, key))
        if existing is None:
            fact = Fact(kind=kind, key=key, value=value, source=source,
                        confidence=confidence)
            self._facts[(kind, key)] = fact
            self._append(fact)
            return True, "added"

        if existing.value == value:
            existing.hits += 1
            existing.last_seen = _now()
            existing.confidence = min(1.0, existing.confidence + 0.02)
            self._append(existing)
            return False, "confirmed (already known)"

        # Conflict. A weaker source does not overwrite a stronger one; it only
        # registers a dispute, and enough disputes eventually kill the fact.
        incoming_rank = SOURCE_RANK[source]
        if incoming_rank < existing.rank and confidence <= existing.confidence:
            existing.disputes += 1
            if existing.disputes >= DISPUTES_TO_DROP:
                del self._facts[(kind, key)]
                self._append(Fact(kind=kind, key=key, value=None, source="derived",
                                  confidence=0.0, disputes=existing.disputes))
                return True, "dropped after %d disputes" % existing.disputes
            self._append(existing)
            return False, "disputed (%d/%d)" % (existing.disputes, DISPUTES_TO_DROP)

        existing.value = value
        existing.source = source
        existing.confidence = max(confidence, existing.confidence * 0.5)
        existing.last_seen = _now()
        existing.disputes = 0
        self._append(existing)
        return True, "updated"

    def forget(self, kind: str, key: str, reason: str = "") -> bool:
        fact = self._facts.pop((kind, key), None)
        if fact is None:
            return False
        tomb = Fact(kind=kind, key=key, value=None, source="human", confidence=0.0)
        tomb.hits = -1                      # marks a tombstone in the log
        self._append(tomb)
        return True

    # -- reading -----------------------------------------------------------

    def get(self, kind: str, key: str) -> Optional[Any]:
        fact = self._facts.get((kind, key))
        if fact is None:
            return None
        fact.hits += 1
        return fact.value

    def find(self, kind: str, prefix: str = "") -> List[Fact]:
        return [f for (k, key), f in self._facts.items()
                if k == kind and key.startswith(prefix)]

    def search(self, text: str, limit: int = 8) -> List[Dict[str, Any]]:
        text = (text or "").lower()
        hits = []
        for fact in self._facts.values():
            hay = ("%s %s %s" % (fact.kind, fact.key, fact.value)).lower()
            if text in hay:
                hits.append(fact.as_dict())
        hits.sort(key=lambda f: -f["confidence"])
        return hits[:limit]

    # -- maintenance -------------------------------------------------------

    def expire(self) -> int:
        """Drop facts nobody used for `ttl`. A store that only grows is a slow store."""
        now = _now()
        dead = [k for k, f in self._facts.items() if now - f.last_seen > self.ttl]
        for k in dead:
            del self._facts[k]
        return len(dead)

    def stats(self) -> Dict[str, Any]:
        kinds: Dict[str, int] = {}
        for (kind, _), _f in self._facts.items():
            kinds[kind] = kinds.get(kind, 0) + 1
        return {
            "facts": len(self._facts),
            "kinds": kinds,
            "sources": {s: sum(1 for f in self._facts.values() if f.source == s)
                        for s in SOURCE_RANK},
            "bytes": self.path.stat().st_size if self.path.exists() else 0,
        }
