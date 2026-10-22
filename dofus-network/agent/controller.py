"""Agent 2 as a controller, not a commentator.

The verifier answers "did that work?". That is not the same as "what should
happen next", and for a while this project confused the two: the loop recorded a
verdict, printed `REPLAN`, and then asked the same policy the same question with
the same context. Nothing changed, so nothing was learned.

This module is the missing half. It owns the *persistent* state the actor must
not carry:

    objective + progress      how far along the current goal is
    strategy table            which recovery works, per failure cause
    failure memory            what keeps failing, and how
    freshness gate            whether the observation is trustworthy at all

and it returns a Decision the loop must obey:

    continue   the step worked; ask the policy as usual
    retry      one more attempt at the same call, no new information
    recover    try a *different* call, chosen from the cause
    replan     the current plan is wrong; ask the scheduler for a new one
    abort      this goal cannot be completed; stop wasting steps

Everything is rule-based on purpose. The point of this layer is not to be clever
per token, it is to be the thing that remembers. A model can replace individual
judgements later; the state has to exist first.

Kept dependency-free (stdlib only) so it runs in the embedded runtime.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Failure causes. Each one implies a *different* response, which is the whole
# reason to distinguish them: "wrong target" needs a new target, "stale state"
# needs a refresh, "invalid assumption" means the tool cannot express the goal.
CAUSES = (
    "none",              # the step worked
    "wrong_target",      # the entity/id the call used does not exist or is not here
    "stale_state",       # the observation contradicts itself or is old
    "incomplete",        # it moved, but not as far as it should have
    "invalid_assumption",# the tool cannot express this goal at all (not_mapped)
    "plan_wrong",        # several different actions fail for several reasons
    "unknown",
)

# Recovery hints per cause. `recover` calls are concrete and cheap: refresh the
# observation, re-resolve the target, or change approach.
RECOVERY = {
    "wrong_target": {"tool": "state.snapshot", "args": {}},
    "stale_state": {"tool": "state.snapshot", "args": {}},
    "incomplete": {"tool": "state.snapshot", "args": {}},
    "invalid_assumption": None,      # no recovery: the plan must change
    "plan_wrong": None,
    "unknown": {"tool": "state.snapshot", "args": {}},
}


@dataclass
class Decision:
    action: str                 # continue | retry | recover | replan | abort
    cause: str
    reason: str
    next_call: Optional[Dict[str, Any]] = None
    strategy: str = ""
    confidence: float = 0.5

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyStat:
    """Attempts and successes for one (cause, strategy) pair.

    Wilson lower bound rather than a raw ratio: with 2/2 you should not believe
    a strategy more than 40/60, and the actor has no time to be subtle about it.
    """

    attempts: int = 0
    successes: int = 0

    @property
    def rate(self) -> float:
        return self.successes / self.attempts if self.attempts else 0.0

    def score(self) -> float:
        n = self.attempts
        if n == 0:
            return 0.5                      # unknown: neutral, not optimistic
        z = 1.64
        p = self.successes / n
        denom = 1 + z * z / n
        centre = p + z * z / (2 * n)
        margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
        return max(0.0, (centre - margin) / denom)


STRUCTURAL_MARKERS = (
    "no client instance", "not an active client", "unknown tool",
    "not_mapped", "no local handler", "no sentinel handler",
    "no simulator for tool", "needs quest_id", "requires quest_id",
)
TRANSIENT_MARKERS = (
    "timed out", "timeout", "refused", "reset by peer", "resisted",
    "temporarily", "connection aborted", "502", "503", "504",
)


def error_class(error: Optional[str]) -> str:
    """Structural failures are permanent for this call; transient ones are not.

    The distinction is the difference between retrying and looping. It has to be
    made on the message because the transport flattens everything into a string.
    """
    text = str(error or "").lower()
    if any(marker in text for marker in STRUCTURAL_MARKERS):
        return "structural"
    if any(marker in text for marker in TRANSIENT_MARKERS):
        return "transient"
    return "unknown"


class Controller:
    """Persistent strategic state for one session."""

    def __init__(self, state_dir: Optional[Path] = None, session: str = "",
                 max_retries: int = 1, drift_to_replan: int = 2,
                 failure_to_replan: int = 3) -> None:
        self.session = session
        self.state_dir = Path(state_dir) if state_dir else Path(
            os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm" / "controller"
        self.max_retries = max_retries
        self.drift_to_replan = drift_to_replan
        self.failure_to_replan = failure_to_replan

        self.objective = ""
        self.progress = 0.0
        self.steps = 0
        self.replans = 0

        self._retries: Dict[str, int] = {}          # tool -> retries used
        self._failures: Dict[str, int] = {}         # tool -> consecutive failures
        self._cause_counts: Dict[str, int] = {}
        self._drift_streak = 0
        self._last_obs: Optional[Dict[str, Any]] = None
        self._last_obs_at = 0.0
        self._strategies: Dict[str, StrategyStat] = {}
        self._history: List[Dict[str, Any]] = []

    # -- state ------------------------------------------------------------

    def set_objective(self, objective: str) -> None:
        self.objective = objective

    def stats(self) -> Dict[str, Any]:
        return {
            "session": self.session,
            "objective": self.objective,
            "progress": round(self.progress, 3),
            "steps": self.steps,
            "replans": self.replans,
            "causes": dict(self._cause_counts),
            "failures": dict(self._failures),
            "strategies": {k: {"n": v.attempts, "ok": v.successes, "score": round(v.score(), 3)}
                           for k, v in self._strategies.items()},
        }

    def save(self) -> None:
        """Persist across process restarts: the whole point of a long-term brain."""
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            payload = self.stats()
            payload["strategies_raw"] = {k: asdict(v) for k, v in self._strategies.items()}
            path = self.state_dir / ("%s.json" % (self.session or "default"))
            path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        except OSError:
            pass

    def load(self) -> None:
        try:
            path = self.state_dir / ("%s.json" % (self.session or "default"))
            if not path.exists():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            for key, raw in (data.get("strategies_raw") or {}).items():
                self._strategies[key] = StrategyStat(attempts=raw.get("attempts", 0),
                                                     successes=raw.get("successes", 0))
            self.replans = int(data.get("replans", 0) or 0)
        except (OSError, ValueError):
            pass

    # -- cause ------------------------------------------------------------

    @staticmethod
    def cause_of(verdict: Any, tool: str, ok: bool, error: Optional[str],
                 before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> str:
        """Why did it fail? Four buckets that need four different responses."""
        name = getattr(verdict, "verdict", verdict)
        if name in ("advanced",):
            return "none"

        err = str(error or "").lower()
        # "policy returned no call" is the most common failure in practice: the
        # goal exists but the policy cannot express it. That is an invalid
        # assumption about the plan, not an unknown error.
        if ("not_mapped" in err or "unknown tool" in err
                or "no call" in err or "no action" in err):
            return "invalid_assumption"
        if any(w in err for w in ("unknown", "not found", "no such", "invalid id",
                                  "out of range", "too far", "cannot reach")):
            return "wrong_target"

        b = before if isinstance(before, dict) else {}
        a = after if isinstance(after, dict) else {}

        # No observation at all after a successful call: nothing was measured.
        if ok and not a:
            return "stale_state"
        # The world did not move at all on a tool that should have moved it.
        if ok and b and a and all(b.get(k) == a.get(k) for k in a):
            return "incomplete"
        # Kamas/HP moved but the thing we asked for did not: partial execution.
        if ok and b and a:
            return "incomplete"
        return "unknown"

    # -- decision ---------------------------------------------------------

    def on_step(self, verdict: Any, tool: str, ok: bool, error: Optional[str],
                before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]],
                now: Optional[float] = None) -> Decision:
        now = time.time() if now is None else now
        self.steps += 1
        name = getattr(verdict, "verdict", verdict)
        drift = bool(getattr(verdict, "drift", False))
        cause = self.cause_of(verdict, tool, ok, error, before, after)
        self._cause_counts[cause] = self._cause_counts.get(cause, 0) + 1

        # Freshness gate: drift is only evidence if the observation is new. A
        # delayed animation or a replayed snapshot produces "drift" that says
        # nothing about the plan, and replanning on it is worse than waiting.
        fresh = self._is_fresh(after, now)
        self._last_obs = dict(after) if isinstance(after, dict) else self._last_obs
        self._last_obs_at = now

        if drift and fresh:
            self._drift_streak += 1
        elif not drift:
            self._drift_streak = 0
        # stale drift does not reset the streak either: it is simply not evidence

        # Drift outranks "advanced": a step can move the character and still
        # contradict the plan's assumption (a map change on a non-movement tool).
        # Checking this after the success branch is why the streak never fired.
        if self._drift_streak >= self.drift_to_replan and fresh:
            self._bump("replan", "stale_state", False)
            self.replans += 1
            self._drift_streak = 0
            return Decision("replan", "stale_state",
                            "two fresh contradictions in a row; the plan's assumption is wrong",
                            None, "replan", 0.75)

        if name == "advanced":
            self._failures[tool] = 0
            self._retries[tool] = 0
            self._bump("continue", cause, True)
            self.progress = min(1.0, self.progress + 0.02)
            return Decision("continue", "none", "step advanced", None, "continue", 0.8)

        # Failure paths.
        self._failures[tool] = self._failures.get(tool, 0) + 1
        consecutive = self._failures[tool]

        if cause == "invalid_assumption":
            self._bump("replan", cause, False)
            self.replans += 1
            return Decision("replan", cause,
                            "the tool cannot express this goal; the plan must change",
                            None, "replan", 0.9)

        if consecutive >= self.failure_to_replan:
            self._bump("replan", "plan_wrong", False)
            self.replans += 1
            self._failures[tool] = 0
            return Decision("replan", "plan_wrong",
                            "%s failed %d times in a row" % (tool, consecutive),
                            None, "replan", 0.7)

        # A structural failure cannot be fixed by asking again. Real data from the
        # client: 9 of 15 recorded failures were "instance is not an active client
        # window", and every retry of them failed too -- 0 for 9. Retrying an
        # unavailable environment is not resilience, it is a loop.
        kind = error_class(error)
        if kind == "structural":
            self._bump("replan", "unavailable", False)
            self.replans += 1
            self._failures[tool] = 0
            return Decision("replan", "unavailable",
                            "%s cannot succeed here (%s); retrying would be a loop"
                            % (tool, str(error)[:60]), None, "replan", 0.9)

        # Pick the recovery the history says works best for this cause.
        strategy, score = self._best_strategy(cause)
        if self._retries.get(tool, 0) < self.max_retries and score < 0.5:
            self._retries[tool] = self._retries.get(tool, 0) + 1
            self._bump("retry", cause, False)
            return Decision("retry", cause,
                            "first failure of %s; one retry before changing approach" % tool,
                            None, "retry", 0.4)

        hint = RECOVERY.get(cause) or RECOVERY["unknown"]
        if hint is not None:
            self._bump("recover", cause, False)
            return Decision("recover", cause,
                            "recovering from %s via %s" % (cause, hint["tool"]),
                            dict(hint), strategy, max(0.4, score))
        self._bump("replan", cause, False)
        self.replans += 1
        return Decision("replan", cause, "no recovery for %s" % cause, None, "replan", 0.6)

    # -- memory -----------------------------------------------------------

    def observe_outcome(self, decision: Decision, next_verdict: Any) -> None:
        """Credit or blame the previous decision, once its result is known.

        This is what makes the table worth keeping: without it the controller
        would pick a strategy on the first step and never revise it.
        """
        key = "%s:%s" % (decision.cause, decision.strategy or decision.action)
        stat = self._strategies.setdefault(key, StrategyStat())
        stat.attempts += 1
        if getattr(next_verdict, "verdict", next_verdict) == "advanced":
            stat.successes += 1

    def _best_strategy(self, cause: str) -> Tuple[str, float]:
        best, score = "recover", 0.0
        for key, stat in self._strategies.items():
            if not key.startswith(cause + ":"):
                continue
            s = stat.score()
            if s > score:
                best, score = key.split(":", 1)[1], s
        return best, score

    def _bump(self, action: str, cause: str, ok: bool) -> None:
        self._history.append({"t": time.time(), "action": action, "cause": cause, "ok": ok})
        if len(self._history) > 500:
            del self._history[:200]

    def _is_fresh(self, after: Optional[Dict[str, Any]], now: float) -> bool:
        if not isinstance(after, dict) or not after:
            return False
        if self._last_obs is None:
            return True
        # Identical to the previous observation: the client is re-serving a stale
        # snapshot, so a "change" reported now is not new evidence.
        if after == self._last_obs and (now - self._last_obs_at) < 3.0:
            return False
        return True
