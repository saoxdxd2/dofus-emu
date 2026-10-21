"""Thinking cache: never pay twice for the same reasoning, never reuse a stale one.

The problem
-----------
The planner's search is cheap in the common case (1.6 ms) and expensive in the
worst (a 40-ingredient recipe closure explores up to 3000 nodes). Expensive
reasoning that gets thrown away is wasted; reasoning that gets reused verbatim is
dangerous, because prices and beliefs move.

The split that makes both true
------------------------------
Cache the **structure** of the conclusion, re-price it on every use:

    stored   : which strategy was chosen, which entity, the tree of nodes
    not stored: any estimate

On a hit the tree is walked bottom-up and every node is recomputed from the
current cost model and posteriors. If the re-priced winner is no longer the
winner among the stored alternatives, the entry is **invalidated** and a full
search runs. So the cache saves the *search*, never the *judgement*.

What is worth caching
---------------------
* plan trees (the AND/OR search over buy / farm / craft / quest)
* recipe closures (ingredient trees, the genuinely expensive part)

What is not
-----------
* encoder inference (55 ms, and caching a classification by text would hide
  distribution drift)
* the ACT recursion (sub-millisecond)

Every entry carries the knowledge-base version: if ``data/cache`` changed, the
structure itself may be wrong, so the whole cache is treated as stale.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from planning.kb import DEFAULT_KB, REPO_ROOT

DEFAULT_CACHE = REPO_ROOT / "data" / "kb" / "thinking_cache.json"


def kb_version(path: Path = DEFAULT_KB) -> str:
    """Identity of the knowledge base: any rebuild invalidates cached structure."""
    try:
        stat = Path(path).stat()
    except OSError:
        return "unknown"
    return hashlib.sha1(f"{stat.st_size}:{int(stat.st_mtime)}".encode("utf-8")).hexdigest()[:12]


@dataclass
class CacheEntry:
    key: str
    version: str
    created: float
    structure: Dict[str, Any]
    reasoning_ms: float
    hits: int = 0
    last_hit: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "version": self.version, "created": self.created,
            "structure": self.structure, "reasoning_ms": self.reasoning_ms,
            "hits": self.hits, "last_hit": self.last_hit,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "CacheEntry":
        return cls(
            key=payload["key"], version=payload.get("version", ""),
            created=float(payload.get("created") or 0.0),
            structure=payload.get("structure") or {},
            reasoning_ms=float(payload.get("reasoning_ms") or 0.0),
            hits=int(payload.get("hits") or 0),
            last_hit=float(payload.get("last_hit") or 0.0),
        )


class ThinkingCache:
    """Structure cache with versioning, TTL, hit accounting and invalidation."""

    def __init__(
        self,
        path: Path = DEFAULT_CACHE,
        ttl_minutes: float = 180.0,
        max_entries: int = 2000,
    ) -> None:
        self.path = Path(path)
        self.ttl_seconds = ttl_minutes * 60.0
        self.max_entries = max_entries
        self.version = kb_version()
        self.entries: Dict[str, CacheEntry] = {}
        self.hits = 0
        self.misses = 0
        self.invalidations = 0
        self.saved_ms = 0.0
        self.load()

    # -- persistence ---------------------------------------------------------

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for blob in (payload.get("entries") or {}).values():
            try:
                entry = CacheEntry.from_dict(blob)
            except (KeyError, TypeError):
                continue
            self.entries[entry.key] = entry
        self.hits = int(payload.get("hits") or 0)
        self.saved_ms = float(payload.get("saved_ms") or 0.0)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "version": self.version,
            "hits": self.hits,
            "saved_ms": round(self.saved_ms, 1),
            "entries": {key: entry.as_dict() for key, entry in self.entries.items()},
        }, indent=1), encoding="utf-8")

    # -- keys ----------------------------------------------------------------

    @staticmethod
    def key(goal: Any, band: str) -> str:
        return f"{goal.item_id}:{goal.qty}:{band}:{int(bool(goal.need_equip))}"

    # -- access --------------------------------------------------------------

    def get(self, key: str) -> Optional[CacheEntry]:
        entry = self.entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        if entry.version != self.version:
            self.invalidate(key, "knowledge base rebuilt")
            return None
        if self.ttl_seconds and time.time() - entry.created > self.ttl_seconds:
            self.invalidate(key, "expired")
            return None
        entry.hits += 1
        entry.last_hit = time.time()
        self.hits += 1
        self.saved_ms += entry.reasoning_ms
        return entry

    def put(self, key: str, structure: Dict[str, Any], reasoning_ms: float) -> None:
        self.entries[key] = CacheEntry(
            key=key, version=self.version, created=time.time(),
            structure=structure, reasoning_ms=reasoning_ms)
        if len(self.entries) > self.max_entries:
            oldest = sorted(self.entries.values(), key=lambda e: e.last_hit or e.created)
            for entry in oldest[: max(1, len(self.entries) - self.max_entries)]:
                self.entries.pop(entry.key, None)

    def invalidate(self, key: str, reason: str = "") -> None:
        if self.entries.pop(key, None) is not None:
            self.invalidations += 1

    def stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        return {
            "entries": len(self.entries),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
            "invalidations": self.invalidations,
            "reasoning_ms_saved": round(self.saved_ms, 1),
            "version": self.version,
        }
