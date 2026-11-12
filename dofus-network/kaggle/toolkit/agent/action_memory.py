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

# Argument fields worth naming in a memory line, in reading order. `direction` is here
# because a navigation dead end is an *edge*, not a tool: five dead directions on one map
# are five different walls.
DEAD_ARG_KEYS = ("map_id", "direction", "npc_id", "item", "monster", "quest_id", "x", "y")
# A solution is replayed, so the cell it happened on is part of what worked.
SOLUTION_ARG_KEYS = ("map_id", "direction", "npc_id", "quest_id", "item", "gid",
                     "x", "y", "cell", "elem_id", "slot")


def label_call(tool: str, args: Optional[Dict[str, Any]], count: int = 1,
               keys: Iterable[str] = DEAD_ARG_KEYS, width: int = 40) -> str:
    """``tool(arg=value,...) xN`` -- the single renderer for every memory line.

    Both stores need this and both had their own copy: `ActionMemory.describe` rendered
    arguments, `PersistentMemory.describe_failures` did not, so the live line the policy
    actually read (2026-11-11, dofus-01) was
    ``already failed here: nav.goto x10; nav.goto x8; nav.goto x3; nav.goto x3`` --
    four distinct dead edges as four identical words. Fixing one copy left the other
    lying, which is why the format lives in exactly one place now.
    """
    clean = args if isinstance(args, dict) else {}
    bits: List[str] = []
    for k in keys:
        if clean.get(k) is not None:
            bits.append("%s=%s" % (k, clean[k]))
    if not bits and clean:
        # No named field: a digest still tells two calls apart. Prefer the non-volatile
        # remainder so `cell` alone does not masquerade as intent, but fall back to the
        # full arguments rather than collapsing to the bare tool name.
        rest = {k: v for k, v in clean.items() if k not in _VOLATILE}
        bits.append(json.dumps(rest or clean, sort_keys=True, default=str)[:width])
    label = str(tool)
    if bits:
        label += "(" + ",".join(bits) + ")"
    try:
        if int(count) > 1:
            label += " x%d" % int(count)
    except (TypeError, ValueError):
        pass
    return label


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
        """A compact line the model can read: what already failed here.

        The argument has to be in the label or the line is useless. Measured live
        2026-11-11 on dofus-01: 280 consecutive `nav.goto {"direction": "left"}`
        calls on one map, every one a noop, while the memory said
        `already failed here: nav.goto x5; nav.goto x3; nav.goto x2; nav.goto`.
        Four distinct dead edges, rendered as four identical words -- the policy
        was told the tool was dead but not which edge, so it kept choosing one.
        `direction` is therefore rendered, and a call whose arguments are all
        volatile falls back to a short digest instead of collapsing to the bare
        tool name.
        """
        rows = self.failures(context)
        if not rows:
            return None
        parts = [label_call(row["tool"], row["args"], row["count"]) for row in rows[:limit]]
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