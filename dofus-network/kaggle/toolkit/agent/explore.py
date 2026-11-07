"""Bounded exploration around the teacher, for identification rather than imitation.

The audit of 2026-10-29 measured what the live log can answer: the corpus holds one
action per state, so a changed policy has an effective sample size of ~19-44 rows out
of 9,393 (0.2-0.5%). A randomized arm gives ~50% of the eligible rows by construction.

The arm this wrapper takes is the safest one available. It does not invent arguments
and it does not reach for tools the planner never uses for the objective: it asks the
planner for the *next* plan after retiring the quest it just chose, and takes that call
instead with probability epsilon. Both arms are therefore calls the planner itself
produced, and the supply is measured - 351 of 420 recorded live turns (84%) have a
distinct second plan. What the arm varies is the ordering of ready objectives, which
is exactly the decision the observational corpus cannot identify.

Propensities are recorded, not assumed. At every eligible state the second plan is
computed *before* the coin, so the row can say `has_alternative` and `p_explore`
honestly: exactly epsilon when an alternative exists, zero when it does not, and no
tag at all when the state was not offered (fight, dialog, tutorial, no current quest).
Without that a teacher row could have been a state where exploration was impossible,
and a comparison of arms would be confounded by the eligibility mix. The cost is one
extra `fork()` + plan per eligible step, paid only when a run opted in.

The event travels with the call (`ToolCall.meta`) into the recorder and the derived
dataset (`explored`, `teacher_tool`), so `tools/explore_report.py` can estimate the
effect without pretending the corpus is observational. Default is off: this class is
only constructed when a run passes `--explore-objectives`.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from agent.runtime import ToolCall


class ExplorationPolicy:
    """epsilon-greedy between the teacher's plan and the planner's own next plan."""

    def __init__(self, inner: Any, epsilon: float = 0.1,
                 seed: Optional[int] = None) -> None:
        if not 0.0 < float(epsilon) <= 0.5:
            raise ValueError("exploration epsilon must be in (0, 0.5], got %r"
                             % (epsilon,))
        self.inner = inner
        self.epsilon = float(epsilon)
        self.rng = random.Random(seed)
        self.steps = 0
        self.explored = 0
        self.skipped = 0
        self.offered = 0
        self.last_event: Optional[Dict[str, Any]] = None

    # -- the policy interface -------------------------------------------------

    def choose(self, goal: str, observation: Dict[str, Any],
               tools: Optional[List[str]] = None) -> Optional[ToolCall]:
        call = self.inner.choose(goal, observation, tools)
        self.steps += 1
        self.last_event = None
        if call is None or not self._offered(observation):
            return call
        alternative = self._alternative(goal, observation, tools, call)
        self.offered += 1
        if alternative is None:
            self.skipped += 1
            call.meta = {"eligible": True, "has_alternative": False,
                         "p_explore": 0.0, "arm": "teacher"}
            return call
        teacher_meta = {"eligible": True, "has_alternative": True,
                        "p_explore": self.epsilon,
                        "teacher_tool": call.tool,
                        "teacher_args": dict(call.args or {})}
        if self.rng.random() >= self.epsilon:
            call.meta = dict(teacher_meta, arm="teacher")
            return call
        self.explored += 1
        alternative.meta = dict(teacher_meta, explored=True, arm="explore",
                                epsilon=self.epsilon)
        self.last_event = alternative.meta
        return alternative

    def note_result(self, tool: str, ok: bool, note: str,
                    observation: Dict[str, Any]) -> None:
        reporter = getattr(self.inner, "note_result", None)
        if callable(reporter):
            reporter(tool, ok, note, observation)

    # -- internals ------------------------------------------------------------

    def _offered(self, observation: Any) -> bool:
        """States where exploration is allowed at all. Everything else is teacher."""
        if not isinstance(observation, dict):
            return False
        if observation.get("in_fight") or observation.get("in_dialog"):
            return False
        tutorial = observation.get("tutorial")
        if isinstance(tutorial, dict) and tutorial.get("active"):
            return False
        planner = self._planner()
        if getattr(planner, "current_quest", None) is None:
            return False
        return callable(getattr(planner, "fork", None))

    def _planner(self) -> Any:
        return getattr(self.inner, "planner", self.inner)

    def _alternative(self, goal: str, observation: Dict[str, Any],
                     tools: Optional[List[str]], call: ToolCall
                     ) -> Optional[ToolCall]:
        planner = self._planner()
        twin = planner.fork()
        try:
            twin._skipped.add(int(planner.current_quest))
        except (TypeError, ValueError):
            return None
        try:
            alternative = twin.choose(goal, observation, tools)
        except Exception:  # noqa: BLE001 - a fork must never take the run down
            return None
        if alternative is None:
            return None
        if (alternative.tool, alternative.args) == (call.tool, call.args):
            return None
        return alternative
