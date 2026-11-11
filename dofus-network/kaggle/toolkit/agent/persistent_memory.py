"""Durable memory: what already failed here, and what already worked here.

`agent.action_memory` answers this for one episode and then forgets. That is the right
scope for a veto -- a dead end on this map for this objective should not outlive the
attempt -- but it is the wrong scope for a *solution*. When the agent works out how to
get past something, throwing that away means solving it again on the next run, which is
exactly the cost this module exists to remove.

Two stores, with deliberately different lifetimes:

  * dead ends  -- bounded, decayed, and keyed by context. A failure recorded in one
    session still vetoes the same call next session, because a wall does not stop being
    a wall. This is what turns `action_memory`'s 53% same-episode repeat rate into a
    cross-session guarantee.
  * solutions  -- keyed by context, recording the call that advanced it and how often.
    `best_solution` returns the most-proven one, so a situation the agent has already
    solved is answered from memory instead of from the model.

What this deliberately does not do: decide. It has no policy, no ranking beyond "worked
most often", and no notion of which tool is appropriate. It answers two questions --
has this exact call already failed here, and what has worked here before -- and the
caller decides what to do with them. A memory that starts deciding is a second planner
with no evaluation behind it.

Persistence is atomic (write a temp file, then replace) because a process killed mid-run
is the normal case for this loop, not the exception: a half-written memory would be
indistinguishable from a memory that never recorded anything, and would silently lose
every solution it held.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.action_memory import (
    FAIL_VERDICTS, ActionMemory, call_signature, context_signature,
)

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "learned_memory.json"

# A context is (objective id, objective type, step id, map id). Serialised as a joined
# string because JSON object keys must be strings, and a tuple would round-trip as a
# list that no longer hashes the same way.
_SEP = "\x1f"


def context_key(context: Optional[Tuple]) -> str:
    if context is None:
        return ""
    return _SEP.join("" if part is None else str(part) for part in context)


def parse_key(key: str) -> Tuple:
    if not key:
        return (None, None, None, None)
    parts = key.split(_SEP)
    out: List[Any] = []
    for part in parts:
        if part == "":
            out.append(None)
        else:
            try:
                out.append(int(part))
            except ValueError:
                out.append(part)
    while len(out) < 4:
        out.append(None)
    return tuple(out[:4])


class PersistentMemory(ActionMemory):
    """`ActionMemory` plus a cross-session record of dead ends and solutions."""

    MAX_CONTEXTS = 4096
    MAX_SOLUTIONS_PER_CONTEXT = 16

    def __init__(self, path: Optional[Path] = None) -> None:
        super().__init__()
        self.path = Path(path) if path is not None else DEFAULT_PATH
        # context_key -> {call_sig: {"tool","args","wins","last"}}
        self._solutions: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # context_key -> {call_sig: {"tool","args","count","last"}}
        self._dead: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._dirty = False
        self.loaded_from: Optional[str] = None
        self._load()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        try:
            doc = json.loads(raw)
        except ValueError:
            # A corrupt file is treated as empty rather than fatal: the loop must start.
            # The rename below means this should only ever be a hand-edited file.
            return
        if not isinstance(doc, dict):
            return
        self._solutions = doc.get("solutions") or {}
        self._dead = doc.get("dead") or {}
        self.loaded_from = str(self.path)

    def save(self, path: Optional[Path] = None) -> Optional[Path]:
        """Atomically write the store. Returns the path written, or None on failure."""
        target = Path(path) if path is not None else self.path
        self._bound()
        doc = {
            "version": 1,
            "saved_at": round(time.time(), 3),
            "solutions": self._solutions,
            "dead": self._dead,
        }
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=str(target.parent),
                prefix=target.name + ".", suffix=".tmp", delete=False)
            try:
                json.dump(doc, handle, indent=1, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.close()
            os.replace(handle.name, str(target))
        except OSError:
            return None
        self._dirty = False
        return target

    # -- recording ---------------------------------------------------------

    def observe(self, context: Optional[Tuple], tool: str,
                args: Optional[Dict[str, Any]], verdict: str) -> None:
        """Record one outcome in both the episode store and the durable one."""
        super().observe(context, tool, args, verdict)
        key = context_key(context)
        sig = call_signature(tool, args)
        now = round(time.time(), 3)
        if verdict == "advanced":
            bucket = self._solutions.setdefault(key, {})
            row = bucket.get(sig) or {"tool": tool, "args": dict(args or {}),
                                      "wins": 0, "last": 0.0}
            row["wins"] = int(row.get("wins", 0)) + 1
            row["last"] = now
            bucket[sig] = row
            if len(bucket) > self.MAX_SOLUTIONS_PER_CONTEXT:
                # Keep the most-proven solutions; a context that has accumulated this
                # many is one where the winner is clear.
                worst = min(bucket, key=lambda s: bucket[s].get("wins", 0))
                bucket.pop(worst, None)
            # A call that worked is no longer a dead end here.
            self._dead.get(key, {}).pop(sig, None)
            if not self._dead.get(key):
                self._dead.pop(key, None)
            self._dirty = True
            return
        if verdict in FAIL_VERDICTS:
            bucket = self._dead.setdefault(key, {})
            row = bucket.get(sig) or {"tool": tool, "args": dict(args or {}),
                                      "count": 0, "last": 0.0}
            row["count"] = int(row.get("count", 0)) + 1
            row["last"] = now
            bucket[sig] = row
            self._dirty = True

    # -- querying ----------------------------------------------------------

    def known_failure(self, context: Optional[Tuple], tool: str,
                      args: Optional[Dict[str, Any]]) -> bool:
        """Has this exact call already failed here, in any session?"""
        key = context_key(context)
        return call_signature(tool, args) in (self._dead.get(key) or {})

    def solutions(self, context: Optional[Tuple],
                  limit: int = 8) -> List[Dict[str, Any]]:
        """Every call that has advanced this context, most-proven first."""
        bucket = self._solutions.get(context_key(context)) or {}
        ranked = sorted(bucket.items(),
                        key=lambda kv: (-int(kv[1].get("wins", 0)),
                                        -float(kv[1].get("last", 0.0))))
        return [{"tool": row.get("tool"), "args": dict(row.get("args") or {}),
                 "wins": int(row.get("wins", 0)), "signature": sig}
                for sig, row in ranked[:limit]]

    def best_solution(self, context: Optional[Tuple]) -> Optional[Dict[str, Any]]:
        """The call that has advanced this context most often, if any."""
        ranked = self.solutions(context, limit=1)
        return ranked[0] if ranked else None

    def dead_ends(self, context: Optional[Tuple], limit: int = 5) -> List[Dict[str, Any]]:
        key = context_key(context)
        rows = list((self._dead.get(key) or {}).values())
        rows.sort(key=lambda r: -int(r.get("count", 0)))
        return rows[:limit]

    def describe_solution(self, context: Optional[Tuple]) -> Optional[str]:
        """One line for the model: what already worked here, in a previous session."""
        solution = self.best_solution(context)
        if not solution:
            return None
        bits = []
        for k in ("map_id", "direction", "npc_id", "quest_id", "item", "gid",
                  "x", "y", "cell", "elem_id", "slot"):
            if solution["args"].get(k) is not None:
                bits.append("%s=%s" % (k, solution["args"][k]))
        label = solution["tool"]
        if bits:
            label += "(" + ",".join(bits) + ")"
        return "worked here before: %s x%d" % (label, solution["wins"])

    def describe_failures(self, context: Optional[Tuple], limit: int = 5) -> Optional[str]:
        """The `ActionMemory` line, drawn from the durable store instead of the episode."""
        dead = self.dead_ends(context, limit)
        if not dead:
            return None
        return "already failed here: " + "; ".join(
            "%s%s" % (row.get("tool"),
                      " x%d" % row["count"] if int(row.get("count", 0)) > 1 else "")
            for row in dead)

    def describe(self, context: Optional[Tuple], limit: int = 5) -> Optional[str]:
        """One line for the model: what worked here, then what is exhausted."""
        parts = [self.describe_solution(context), self.describe_failures(context, limit)]
        parts = [p for p in parts if p]
        return " | ".join(parts) if parts else None

    # -- housekeeping ------------------------------------------------------

    def _bound(self) -> None:
        """Keep the durable store bounded by evicting the least recently touched."""
        for store, stamp in ((self._solutions, "last"), (self._dead, "last")):
            while len(store) > self.MAX_CONTEXTS:
                oldest = min(store, key=lambda k: max(
                    (float(v.get(stamp, 0.0)) for v in store[k].values()), default=0.0))
                store.pop(oldest, None)

    def stats(self) -> Dict[str, int]:
        return {
            "contexts_with_solutions": len(self._solutions),
            "contexts_with_dead_ends": len(self._dead),
            "solutions": sum(len(v) for v in self._solutions.values()),
            "dead_ends": sum(len(v) for v in self._dead.values()),
        }


def self_check() -> None:
    import tempfile as _tf
    with _tf.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mem.json"
        ctx = context_signature({"id": 4262, "type_id": 6}, {"map_id": 145752833})
        mem = PersistentMemory(path)
        assert mem.best_solution(ctx) is None
        mem.observe(ctx, "nav.goto", {"map_id": 145752833}, "failed")
        assert mem.known_failure(ctx, "nav.goto", {"map_id": 145752833})
        # a different call is not vetoed
        assert not mem.known_failure(ctx, "nav.goto", {"map_id": 999})
        # a success is remembered as a solution and clears the dead end
        mem.observe(ctx, "quest.advance", {"npc_id": 3888}, "advanced")
        assert not mem.known_failure(ctx, "quest.advance", {"npc_id": 3888})
        solution = mem.best_solution(ctx)
        assert solution and solution["tool"] == "quest.advance", solution
        assert mem.save() is not None

        # a fresh instance must see both, which is the whole point
        again = PersistentMemory(path)
        assert again.known_failure(ctx, "nav.goto", {"map_id": 145752833}), \
            "a dead end must survive a restart"
        assert again.best_solution(ctx)["tool"] == "quest.advance", \
            "a solution must survive a restart"
        # and the durable memory reports both in one line
        line = again.describe(ctx)
        assert line and "worked here" in line and "already failed" in line, line
        # repeated successes rank higher than single ones
        again.observe(ctx, "quest.advance", {"npc_id": 3888}, "advanced")
        assert again.best_solution(ctx)["wins"] == 2
        # context is part of the key: another map is a different situation
        other = context_signature({"id": 4262, "type_id": 6}, {"map_id": 1})
        assert again.best_solution(other) is None
        assert not again.known_failure(other, "nav.goto", {"map_id": 145752833})


if __name__ == "__main__":
    self_check()
    print("persistent_memory self_check: PASS")
