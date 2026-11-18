"""Incremental, activatable computation for the live loop.

A decision reads the same observation more than once. The planner fingerprints
the scene and the quest state, the thinking layer fingerprints the scene again,
the guard counts the quest ids, and each of those walks a different part of the
same document. On a carried snapshot the object is literally the same one, so
the second read is repeated work.

:class:`ComputeTree` names those derivations as nodes:

* a node is a value, a compute function, and its dependencies;
* ``get`` recomputes only a dirty node (or one whose dependency version moved,
  or whose TTL expired) and returns the cached value otherwise;
* ``invalidate`` marks a node and every downstream node dirty;
* ``activate`` hands the dirty branches to a
  :class:`agent.scheduler.DeadlineScheduler`, so the work happens in a wait
  window instead of on the critical path.

:class:`IdentityMemo` is the leaf-level cache used by :mod:`agent.awareness`.
It is keyed by object identity, not by a content hash, because hashing the
document is the work being avoided. It holds the object it cached against, so
an id cannot be reused while the entry is alive; a caller that mutates an
observation in place must call ``clear`` (the live path prepares a fresh dict
before any consumer sees it).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

_MISSING = object()

Compute = Callable[["ComputeTree", Dict[str, Any]], Any]


@dataclass
class Node:
    """One named value in the tree."""

    key: str
    compute: Compute
    deps: Tuple[str, ...] = ()
    ttl: float = 0.0
    value: Any = _MISSING
    computed_at: float = 0.0
    version: int = 0
    dep_versions: Tuple[int, ...] = ()
    dirty: bool = True


class ComputeTree:
    """Dependency-aware memoization with explicit dirty propagation."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._nodes: Dict[str, Node] = {}
        self._dependants: Dict[str, Set[str]] = {}
        self._context: Dict[str, Any] = {}
        self._computing: Set[str] = set()
        self.hits = 0
        self.misses = 0

    # -- definition --------------------------------------------------------

    def define(self, key: str, compute: Compute,
               deps: Sequence[str] = (), ttl: float = 0.0) -> Node:
        """Add a node. Dependencies must already exist."""
        if key in self._nodes:
            raise KeyError("compute node already defined: %s" % key)
        for dep in deps:
            if dep not in self._nodes:
                raise KeyError("unknown compute dependency %r for %r" % (dep, key))
        node = Node(key=key, compute=compute, deps=tuple(deps),
                    ttl=max(0.0, float(ttl)))
        self._nodes[key] = node
        for dep in node.deps:
            self._dependants.setdefault(dep, set()).add(key)
        return node

    def set_context(self, **values: Any) -> None:
        """Update the values compute functions read.

        Context is data, not a cache key. A caller that changes an input must
        also invalidate the nodes that read it; the live loop does both when it
        accepts a new observation.
        """
        self._context.update(values)

    def context(self) -> Dict[str, Any]:
        return self._context

    # -- invalidation ------------------------------------------------------

    def invalidate(self, key: Optional[str] = None) -> None:
        """Mark ``key`` and its dependants dirty, or the whole tree."""
        if key is None:
            for node in self._nodes.values():
                node.dirty = True
            return
        stack = [key]
        seen: Set[str] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            node = self._nodes[current]
            node.dirty = True
            stack.extend(self._dependants.get(current, ()))

    def touch(self, key: str) -> None:
        """Mark one node dirty without walking dependants (rare, explicit)."""
        self._nodes[key].dirty = True

    # -- evaluation --------------------------------------------------------

    def get(self, key: str) -> Any:
        """Return the node's value, recomputing it when it is stale."""
        node = self._nodes[key]
        dep_versions = tuple(self._nodes[dep].version for dep in node.deps)
        fresh = (not node.dirty
                 and not any(self._nodes[dep].dirty for dep in node.deps)
                 and node.dep_versions == dep_versions
                 and (node.ttl <= 0.0
                      or (self._clock() - node.computed_at) < node.ttl))
        if fresh:
            self.hits += 1
            return node.value

        if key in self._computing:
            raise RuntimeError("compute cycle at %s" % key)
        # Dependencies first: a parent that is clean while a dependency moved
        # still has to see the new value.
        for dep in node.deps:
            self.get(dep)
        dep_versions = tuple(self._nodes[dep].version for dep in node.deps)

        self._computing.add(key)
        try:
            value = node.compute(self, self._context)
        finally:
            self._computing.discard(key)

        node.value = value
        node.computed_at = self._clock()
        node.dep_versions = dep_versions
        node.version += 1
        node.dirty = False
        self.misses += 1
        return value

    # -- scheduling --------------------------------------------------------

    def dirty_nodes(self, keys: Optional[Iterable[str]] = None) -> List[str]:
        keys = tuple(self._nodes) if keys is None else tuple(keys)
        return [key for key in keys if self._nodes[key].dirty]

    def activate(self, scheduler: Any, keys: Optional[Iterable[str]] = None,
                 delay: float = 0.0) -> List[str]:
        """Schedule the dirty branches on ``scheduler``, coalesced per node.

        The scheduler keeps one pending task per node key, so calling this on
        every poll of a long wait does not grow the queue.
        """
        scheduled: List[str] = []
        for key in self.dirty_nodes(keys):
            scheduler.schedule(delay, self.get, key, key="compute:" + key)
            scheduled.append(key)
        return scheduled

    # -- reporting ---------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        return {"nodes": len(self._nodes), "hits": self.hits,
                "misses": self.misses,
                "dirty": sum(1 for node in self._nodes.values() if node.dirty)}


class IdentityMemo:
    """One cached value per name, valid while the object is the same object.

    This is deliberately not an LRU: the live loop has a handful of named
    derivations, and holding the last observation per name is bounded and
    keeps the identity check exact. The cached object is held in the entry so
    Python cannot recycle its id while the entry is alive.
    """

    def __init__(self) -> None:
        self._values: Dict[str, Tuple[Any, Any]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, name: str, obj: Any, compute: Callable[[Any], Any]) -> Any:
        entry = self._values.get(name)
        if entry is not None and entry[0] is obj:
            self.hits += 1
            return entry[1]
        self.misses += 1
        value = compute(obj)
        self._values[name] = (obj, value)
        return value

    def clear(self) -> None:
        self._values.clear()

    def stats(self) -> Dict[str, int]:
        return {"hits": self.hits, "misses": self.misses,
                "names": len(self._values)}
