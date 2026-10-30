"""The repeat-streak gate, shadow-only.

The corpus says the same call re-issued after two misses meets ~2-3% of the time
while an unchanged retry succeeds 99% less often than a changed one would (G48,
G51): `repeat_streak >= 2` keeps 99%+ of the successes on four rolling-origin
cuts (G54). This class carries that counter for the live loop so each decision
can record what the gate *would* have done. It has no method that changes what is
sent: the planner still owns every decision until a shadow run prices the re-plan
the veto would force.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from agent.verifier import READ_ONLY_TOOLS

VETO_AT = 2

# Fields that change between two attempts at the *same* intent. Measured 2026-10-29:
# the three taps on element 513208 carry skill_uid 136137029/136137092/136137181, so
# an exact-args key sees three different calls and never reaches a streak of two.
# `lang` is a renderer hint on every nav.goto and `cell` on an element tap is where
# the character happened to stand, not what was asked for.
VOLATILE_ARGS = {"lang", "skill_uid", "cell"}


class RuleGate:
    """Consecutive non-advance outcomes per call, minus its volatile arguments."""

    def __init__(self, veto_at: int = VETO_AT) -> None:
        self._misses: Dict[str, int] = {}
        self.veto_at = veto_at

    @staticmethod
    def key(tool: str, args: Optional[Dict[str, Any]]) -> str:
        clean = {k: v for k, v in (args or {}).items() if k not in VOLATILE_ARGS}
        try:
            payload = json.dumps(clean, sort_keys=True, default=str)
        except (TypeError, ValueError):
            payload = str(clean)
        return "%s|%s" % (tool, payload)

    def observe(self, tool: str, args: Optional[Dict[str, Any]], verdict: str) -> None:
        if tool in READ_ONLY_TOOLS:
            return
        key = self.key(tool, args)
        if verdict == "advanced":
            self._misses.pop(key, None)
        elif verdict in ("noop", "failed"):
            self._misses[key] = self._misses.get(key, 0) + 1

    def streak(self, tool: str, args: Optional[Dict[str, Any]]) -> int:
        return self._misses.get(self.key(tool, args), 0)

    def shadow(self, tool: str, args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        key = self.key(tool, args)
        streak = self._misses.get(key, 0)
        return {"rule": key[:160], "streak": streak, "would_veto": streak >= self.veto_at}

    def clear(self) -> None:
        self._misses.clear()
