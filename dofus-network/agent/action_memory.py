"""Episodic action memory: never repeat an action that already failed here.

Why this and not another heuristic. Measured on the val corpus (2026-11-08): the
policy re-issues a tool that already failed for the same objective in the same
episode on **53% of steps** (1,973 / 3,719). Per-step payoff cannot see that cost
because each loop step looks locally fine; the damage is temporal -- the episode
is burned. So the agent is not short of information, it is short of *memory*.

This module is that memory, and nothing more: it records (context, tool, args) ->
outcome for the current episode and answers two questions:

    is_failed(context, tool, args) -> bool   # veto an exact repeat of a failure
    describe(context)              -> str    # one line the model can read

Nothing here decides which tool to use. It only forbids re-running an action that
already failed in this context, and reports the dead ends so a language policy
can choose a different one. The rule is derived from the agent's own history, so
it is learned behaviour, not a hardcoded domain rule -- there is no "if dungeon".

Context is what makes "here" mean something: an objective id/type plus the map.
A failure on map A does not mask the same call on map B, and a new objective
starts clean. Volatile argument fields (``lang``, ``cell``, ``skill_uid``) are
ignored in the signature, exactly like the repeat-streak gate, so three taps at
the same intent are one call and not three.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:  # reuse the gate's own definition of "the same call"
    from agent.gate import VOLATILE_ARGS as _VOLATILE
except Exception:  # noqa: BLE001 - import must never fail the loop
    _VOLATILE = {"lang", "skill_uid", "cell"}

FAIL_VERDICTS = ("failed", "blocked", "noop")


def context_signature(objective: Optional[Dict[str, Any]],
                      observation: Optional[Dict[str, Any]]) -> Tuple:
    """A stable key for 'here': which objective, on which map."""
    obj = objective if isinstance(objective, dict) else {}
    oid = obj.get("id") or obj.get("quest_id")
    otype = obj.get("type_id")
    step = obj.get("step_id")
    obs = observation if isinstance(observation, dict) else {}
    block = obs.get("map") if isinstance(obs.get("map"), dict) else {}
    map_id = obs.get("map_id") or block.get("map_id")
    return (oid, otype, step, map_id)


def call_signature(tool: str, args: Optional[Dict[str, Any]]) -> str:
    clean = {k: v for k, v in (args or {}).items() if k not in _VOLATILE}
    try:
        return "%s|%s" % (tool, json.dumps(clean, sort_keys=True, default=str))
    except (TypeError, ValueError):
        return "%s|%s" % (tool, str(clean))


class ActionMemory:
    """Per-instance, per-episode record of dead ends. Bounded and stdlib-only."""

    MAX_FAILED = 512

    def __init__(self) -> None:
        self._context: Optional[Tuple] = None
        self._failed: Dict[str, Dict[str, int]] = {}   # tool -> {sig: count}
        self._meta: Dict[str, Dict[str, Any]] = {}      # sig -> {tool, args}

    def reset(self, context: Optional[Tuple] = None) -> None:
        self._context = context
        self._failed = {}
        self._meta = {}

    def _ensure(self, context: Optional[Tuple]) -> None:
        if context != self._context:
            self.reset(context)

    def observe(self, context: Optional[Tuple], tool: str,
                args: Optional[Dict[str, Any]], verdict: str) -> None:
        """Feed one outcome. ``advanced`` clears the dead end for that exact call."""
        self._ensure(context)
        sig = call_signature(tool, args)
        bucket = self._failed.setdefault(tool, {})
        if verdict == "advanced":
            bucket.pop(sig, None)
            self._meta.pop(sig, None)
            if not bucket:
                self._failed.pop(tool, None)
            return
        if verdict in FAIL_VERDICTS:
            bucket[sig] = bucket.get(sig, 0) + 1
            self._meta[sig] = {"tool": tool, "args": dict(args or {})}
            # Bound the store: evict the least-repeated dead ends first.
            total = sum(len(b) for b in self._failed.values())
            while total > self.MAX_FAILED:
                victim_tool = max(self._failed, key=lambda t: len(self._failed[t]))
                victim_sig = next(iter(self._failed[victim_tool]))
                self._failed[victim_tool].pop(victim_sig, None)
                self._meta.pop(victim_sig, None)
                if not self._failed[victim_tool]:
                    self._failed.pop(victim_tool, None)
                total -= 1

    def is_failed(self, context: Optional[Tuple], tool: str,
                  args: Optional[Dict[str, Any]]) -> bool:
        self._ensure(context)
        return call_signature(tool, args) in self._failed.get(tool, {})

    def failures(self, context: Optional[Tuple]) -> List[Dict[str, Any]]:
        """Every dead end here, most-repeated first."""
        self._ensure(context)
        out: List[Dict[str, Any]] = []
        for tool, bucket in self._failed.items():
            for sig, count in bucket.items():
                meta = self._meta.get(sig) or {"tool": tool, "args": {}}
                out.append({"tool": tool, "args": meta.get("args", {}), "count": count})
        out.sort(key=lambda row: -row["count"])
        return out

    def describe(self, context: Optional[Tuple], limit: int = 5) -> Optional[str]:
        """A compact line the model can read: what already failed here."""
        rows = self.failures(context)
        if not rows:
            return None
        parts: List[str] = []
        for row in rows[:limit]:
            arg_bits = []
            for k in ("map_id", "npc_id", "item", "x", "y", "quest_id"):
                if row["args"].get(k) is not None:
                    arg_bits.append("%s=%s" % (k, row["args"][k]))
            label = row["tool"]
            if arg_bits:
                label += "(" + ",".join(arg_bits) + ")"
            if row["count"] > 1:
                label += " x%d" % row["count"]
            parts.append(label)
        more = len(rows) - limit
        text = "already failed here: " + "; ".join(parts)
        if more > 0:
            text += "; +%d more" % more
        return text


def self_check() -> None:
    mem = ActionMemory()
    ctx = context_signature({"id": 4262, "type_id": 6}, {"map_id": 145752833})
    call = {"map_id": 145752833}
    assert not mem.is_failed(ctx, "nav.goto", call)
    mem.observe(ctx, "nav.goto", call, "failed")
    assert mem.is_failed(ctx, "nav.goto", call), "a failed call must be vetoed"
    # different args are a different call
    assert not mem.is_failed(ctx, "nav.goto", {"map_id": 145752834})
    # volatile args do not distinguish the same intent
    assert mem.is_failed(ctx, "nav.goto", {"map_id": 145752833, "lang": "es"})
    # success clears the dead end
    mem.observe(ctx, "nav.goto", call, "advanced")
    assert not mem.is_failed(ctx, "nav.goto", call)
    # a new map is a fresh context
    ctx2 = context_signature({"id": 4262, "type_id": 6}, {"map_id": 1})
    assert not mem.is_failed(ctx2, "nav.goto", call)
    # describe surfaces the dead end
    mem.observe(ctx2, "quest.advance", {"npc_id": 3888}, "failed")
    line = mem.describe(ctx2)
    assert line and "quest.advance" in line and "3888" in line, line
    # noop counts as a dead end, blocked too
    mem.observe(ctx2, "fight.join", {}, "noop")
    assert mem.is_failed(ctx2, "fight.join", {})


if __name__ == "__main__":
    self_check()
    print("action_memory self_check: PASS")