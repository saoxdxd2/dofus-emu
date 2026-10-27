"""Autonomous loop: the agent drives the client instead of answering one order.

Why this module exists
----------------------
Every step until now was request/response. A human said "sell the bread", the
agent answered once, and the recorder wrote one row. That is why the encoder
plateaued: it has seen 2,273 hand-written rows and **zero of its own**. A model
cannot learn a policy from labels a human wrote about a game it never played.

This loop closes that: it observes, chooses, acts, labels the outcome, and
decides whether to continue -- on its own, for minutes or hours.

What it deliberately will not do
--------------------------------
* **It never buys.** `ExchangeBidHouseBuyMessage` spends money irreversibly at
  whatever price is on the listing, and there is no undo. Supply is safe to
  automate; demand is not.
* **It never invents a price.** A listing without an explicit integer price is
  refused before it reaches the client.
* **It never treats a listing as revenue.** Putting an item on the market is
  controllable; *someone buying it* is not. A successful `hdv.list` is recorded
  as `listing_open` with an expected fill time, never as kamas earned. Conflating
  the two is how a bot learns a drop rate that never happened.

The four labels it emits (and why they matter)
----------------------------------------------
Each step gets a verdict, a residual and a drift flag. Those are exactly the
heads the second agent needs, so this loop is also its training-data generator:

    verdict  advanced | noop | failed | blocked   (did the world change?)
    residual  seconds late vs the tool's own estimate, when it has one
    drift     the observation contradicts what the previous step assumed

Usage
-----
    python -m agent.autopilot --goal "list the bread at market price" --steps 12
    python -m agent.autopilot --goals-file goals.txt --minutes 30 --interval 2
    python -m agent.autopilot --goal "..." --dry-run      # choose, never send

Stop it with Ctrl-C, the `--stop-file`, or by touching `AUTOPILOT_STOP` in the
repo root. It calls `safe.stop` on the way out so the client is not left mid-loop.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence

from agent.runtime import AgentRuntime, RulePolicy, ToolCall
from agent.observe import flatten
from agent.awareness import character_key
from agent.features import DELIVER_TYPES
from agent.planner import _goal_mode
from agent.thinking import ThinkingLayer
from agent.knowledge_context import enrich_observation, load_default_store
from agent.controller import Controller, Decision
from agent.verifier import READ_ONLY_TOOLS, Verdict, Verifier, changed_keys
from agent.schema import TOOL_SCHEMA, validate_call
from agent.recorder import _redact
from agent.jsonio import dumps_bytes as _fast_dumps
from combat.fight_estimate import FightAdvisor, FightOutcomeLog
from combat.brain_service import ensure_brain

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = REPO_ROOT / "trajectories" / "autopilot.jsonl"
DEFAULT_STOP = REPO_ROOT / "AUTOPILOT_STOP"
LOG_MAX_BYTES = 64 * 1024 * 1024

# Tools that spend money irreversibly. Kept as a name list rather than a flag on
# the schema so adding one is a deliberate, reviewable edit.
FORBIDDEN_TOOLS = {"hdv.buy"}

# Listing tools: allowed, but only with an explicit price the caller supplied.
LISTING_TOOLS = {"hdv.list", "inv.sell"}

# Tools whose effect lands after the call returns.
#
# `quest.advance` walks to the NPC and talks in the walk callback; `nav.goto` asks the
# client's pathfinder and the map changes on the next server round trip; `fight.join`
# walks then attacks. All of them answer ok immediately, so observing in the same
# instant scores them `noop` — the cell has not moved yet because the walk just started.
# That is what a live session showed: 30 steps of `quest.advance ok / noop`, the loop
# cycling retry -> recover forever while the character was actually walking.
SETTLE_TOOLS = {"quest.advance", "nav.goto", "fight.join", "farm.start"}

# Tools that can start a fight. Both go through the estimator before the client
# sees them, so an unattended fight only begins with a passing estimate.
FIGHT_TOOLS = {"fight.join", "farm.start"}

# How old a snapshot may be before the loop refuses to decide on it.
#
# The bridge returns the post-action state with every directive, so the usual
# decision input is well under this; only a long pause (`--interval`) or a
# bridge that stopped answering makes the cache stale. An unknown age is not
# fresh: it means nobody recorded when the snapshot was taken.
OBS_FRESH_MS = 4000.0

# While a farm order is running, no fight/kill/kamas movement for this long
# means the bot is idle (no group on this map, or waiting for a respawn). Resume
# planning so the targeted farm can join the next matching group or explore;
# the farm.start no-op guard caps a truly dead loop at three attempts.
FARM_IDLE_S = 20.0

# How many read-only steps a repair decision may wait for before its outcome is
# considered unknowable. A recovery's own snapshot is one of them; twelve is roughly
# one farm-poll generation, after which crediting the decision would be guessing.
PENDING_CREDIT_STEPS = 12

# There is no client *window* at all — the host has none open, so the bridge
# answers without ever reaching a page. The words are the host's own
# (desktop/agentbridge.cs).
#
# This is not a failed step and not the same as `fell_out`: with no page there is
# no new snapshot, so the in_world transition that detects a kick never appears.
# A live run put 27 consecutive `state.snapshot` failures into exactly this state,
# each one answered with a replan that could only produce another doomed call.
ABSENT_CLIENT_MARKERS = (
    "no client instance", "not an active client", "no client window is open",
)


def client_absent(error: Any) -> bool:
    text = str(error or "").lower()
    return any(marker in text for marker in ABSENT_CLIENT_MARKERS)


# A wait's own clock. The live log paces the loop at a 0.374 s median gap between
# consecutive steps, of which the round trip is only 18.2 ms -- so the gap is the
# loop's pacing, and 0.35 s reproduces it. Keeping the cadence matters more than the
# exact number: the planner's wait gates count *passes*, so a different cadence
# silently changes how long a scripted tutorial pause lasts.
WAIT_POLL_S = 0.35

# How many wait passes in a row are still a wait rather than a stall.
#
# Measured on the baseline's 3,010 read-steps: they come in 671 runs with a median
# length of 1 and a p99 of 6 -- a legitimate wait is short, because it is standing
# between an action and the state the action promised. Two runs broke the shape: one
# of 149 passes (56 s) and one of 1,581 (586 s), which together are 57% of all the
# wait traffic. The long one is the case this bound exists for: 1,576 of its 1,581
# reads *succeeded*, so nothing in the loop could call it a failure -- the planner
# simply had no action for the goal and the run spent ten minutes looking, five reads
# per second, while reporting `advanced` every time.
#
# Past the bound a read is dispatched as one real step per 40 passes, which hands the
# spin back to the streak, refusal and stall machinery that already knows how to
# retire a goal. Same wall-clock cost, one step row per ~15 s instead of per 0.374 s.
MAX_CONSECUTIVE_WAITS = 40


# How many candidates the wait escalation is allowed to skip while looking for one
# that is an action. Measured, not guessed: the thinking layer's fallback candidate is
# `state.snapshot` ("refresh the observation"), which is the very thing the wait exists
# to stop dispatching -- taking it would reset the streak and double the spin instead of
# breaking it. Three tries finds any real alternative and then gives up.
ESCALATE_TRIES = 3


def fell_out_of_world(before: Any, after: Any) -> Optional[str]:
    """The reason the client left the world between two states, or None.

    Returns the client's own reason string, or `""` when the world simply vanished,
    or None when it is still there. Two ways to know the session is gone: we watched
    the world disappear, or the client told us why it left. Either alone is enough; a
    bare `in_world: false` in a state that was already out of the world is not,
    because a map transition can briefly report no map and that must not end the run.
    Both the post-step check and the wait check need this decision, and a second copy
    drifting away from the first is how a kick stops being detected.
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    if after.get("in_world") is not False:
        return None
    why = after.get("last_disconnect") or {}
    reason = why.get("reason") if isinstance(why, dict) else None
    if before.get("in_world") is not False or reason:
        return str(reason or "")
    return None


def _game_error(result: Any) -> Optional[str]:
    """The game's own rejection, inside a transport-successful response.

    A directive answers ``ok`` when the bridge executed it. Whether the *game*
    accepted it is ``response.result.ok``, and 70 of 264 live calls were game
    failures wearing a success flag ("no NPC on this map"). The loop has to see
    that, or it retries a call the server already refused.
    """
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return None
    response = data.get("response")
    if not isinstance(response, dict):
        return None
    inner = response.get("result")
    if not isinstance(inner, dict) or inner.get("ok") is not False:
        return None
    return str(inner.get("error") or "the client rejected the call")

# Minimum price we will ever list at. One kama is the floor the game itself
# enforces; anything below it is a typo, not a strategy.
PRICE_FLOOR = 1

# Price rules the client can resolve from a price the server actually sent
# (ObjectAveragePricesMessage / ExchangeBidHouseInListAddedMessage, cached in the
# page). The names must stay in sync with the schema's `hdv.list.price_rule`
# enum -- `fixed` is deliberately absent: it means "the caller knows the number".
MEASURED_PRICE_RULES = {"median_minus_1", "undercut"}


@dataclass
class Step:
    index: int
    goal: str
    tool: str
    args: Dict[str, Any]
    ok: bool
    error: Optional[str]
    latency_ms: float
    verdict: str
    residual_s: Optional[float] = None
    drift: bool = False
    observation_cell: Optional[int] = None
    observation_map: Optional[int] = None
    # Compact before/after state (the keys the verifier actually reads). Without
    # these, a logged step cannot be turned into a training example later: the
    # label would be there but not the input that produced it.
    obs_before: Dict[str, Any] = field(default_factory=dict)
    obs_after: Dict[str, Any] = field(default_factory=dict)
    note: str = ""
    session: str = ""
    source: str = "rule"          # which agent produced the label: rule | model | human
    at: float = field(default_factory=time.time)
    # The claim and its settlement. A fight step without these cannot be used
    # for calibration: the estimate that authorised it would be lost.
    fight_estimate: Optional[Dict[str, Any]] = None
    fight_outcome: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Autopilot:
    """Observe -> choose -> execute -> label, until a stop condition fires."""

    def __init__(
        self,
        runtime: AgentRuntime,
        goals: Sequence[str],
        log_path: Path = DEFAULT_LOG,
        stop_file: Path = DEFAULT_STOP,
        max_steps: int = 0,          # 0 = unlimited
        minutes: float = 0.0,        # 0 = unlimited
        interval: float = 0.0,
        steps_per_goal: int = 8,
        consecutive_failure_limit: int = 3,
        dry_run: bool = False,
        on_step=None,
        verifier_model: Optional[Path] = None,
        veto_threshold: float = 0.0,
        explore: float = 0.0,
        explore_seed: int = 0,
        preflight: bool = True,
        # Seconds to keep re-observing after an asynchronous tool before judging
        # it. 0 disables the wait (tests, dry runs).
        settle_s: float = 3.0,
        # Deliberate dwell after a world-changing action. The completion gate
        # already waits for movement/dialogs; this is the short "thinking token"
        # that lets animations and the next observation settle before deciding.
        think_s: float = 0.25,
        # Optional training data. Off leaves the solver unaffected; it only
        # stops writing the board snapshot rows used by a learned ranker.
        fight_boards: bool = True,
        # "auto" makes the loop start the combat brain if nothing is serving it;
        # "off" leaves fights to the page's own heuristic fallback.
        brain: str = "auto",
    ) -> None:
        self.runtime = runtime
        self.fight_boards = fight_boards
        self.brain_mode = brain if brain in ("auto", "off") else "auto"
        # What the combat brain probe last said, so the summary can answer "was the
        # learned policy available for this run?" instead of implying it was.
        self._brain: Dict[str, Any] = {}
        self._brain_attempts = 0
        self._brain_try_at = 0.0
        # Floor between probes. A brain that cannot start for a structural reason
        # (no interpreter imports its dependencies) will not be fixed by asking
        # again in five seconds, and the probe itself costs subprocess spawns.
        self.brain_retry_s = 300.0
        self.goals = list(goals)
        self.log_path = Path(log_path)
        self.stop_file = Path(stop_file)
        self.max_steps = max_steps
        self.session = uuid.uuid4().hex[:8]
        self.verifier = Verifier(model_path=verifier_model, veto_threshold=veto_threshold)
        # Agent 2: owns the objective, the strategy table and the failure memory.
        # The verifier answers "did that work"; the controller decides what the
        # actor does next, which is the part that used to be missing.
        self.preflight = preflight
        self.world_poll_s = 3.0
        self.world_report_s = 30.0
        self.settle_s = settle_s
        self.think_s = max(0.0, float(think_s))
        self._think_until = 0.0
        # Steps allowed without `objectives_completed` rising before the current quest
        # is considered stuck. Only applies to unlimited runs.
        self.stall_limit = 8
        self._best_progress = -1
        self._stall_since = 0
        self.controller = Controller(session=self.session, explore=explore,
                                     seed=explore_seed)
        self.controller.set_objective("; ".join(goals))
        self.controller.load()
        # After the fast policy repeats the same failure, this layer searches the
        # untried interactions the current board supports. It never replays a
        # recent call, and it yields back to the controller when exhausted.
        self.thinking = ThinkingLayer()
        self.on_replan = None            # set by the host: (reason, decision) -> None
        self._forced_call: Optional[ToolCall] = None
        self._hinted = False
        self._pending: Optional[Decision] = None
        self._pending_age = 0
        self.replan_requested = 0
        self.deadline = time.time() + minutes * 60.0 if minutes > 0 else 0.0
        self.interval = interval
        self.steps_per_goal = steps_per_goal
        self.consecutive_failure_limit = consecutive_failure_limit
        self.dry_run = dry_run
        self.on_step = on_step

        # Recent steps for the thinking layer and the UI. The run itself is
        # unlimited, so this list must not be: the counters below keep the full
        # history's information without holding every observation it carried.
        self.steps: List[Step] = []
        self.step_history = 256
        self._step_total = 0
        self._verdict_counts: Dict[str, int] = {}
        self._source_counts: Dict[str, int] = {}
        # Manual interventions, as counted by the client's trusted-input instrument.
        self._intervention_seen = 0
        self._intervention_total = 0
        # Recovery accounting: criterion 5 asks whether the run gets itself back,
        # so a rescue is a measured event, not a print line.
        self._recoveries = 0
        self._recoveries_ok = 0
        self._recovery_wait_s = 0.0
        # Waits that were never dispatched as steps: see WAIT_POLL_S.
        self._waits = 0
        self._consecutive_waits = 0
        self._forced_reason = ""
        # Dialog attempts and whether the conversation moved: see _record_dialog.
        self._dialogs = 0
        self._dialog_stalls = 0
        self._first_step_at = 0.0
        self._last_step_at = 0.0
        # A long run can list hundreds of items; keep the recent ledger for
        # inspection and the total for the summary.
        self.open_listings: Deque[Dict[str, Any]] = deque(maxlen=512)
        self._listing_total = 0
        self.knowledge = load_default_store()
        # Agent 3's read-only answers: one shared estimator behind both the guard
        # and the `fight.estimate` tool, so a step and its refusal cannot disagree.
        # Reuse the canonical connection (and its bounded name cache) instead of
        # opening a second handle to the same 42 MiB read-only database.
        self.fights = FightAdvisor(store=self.knowledge)
        self.fight_log = FightOutcomeLog()
        self._character_at = 0.0     # last accepted sheet
        self._character_try = 0.0    # last attempt, successful or not
        self._character_key: Optional[str] = None
        self.character_ttl_s = 60.0
        self.character_retry_s = 30.0
        # The page owns a fight once it starts. These track the wait so the loop
        # can arm the in-page bot periodically and say how long the wait has been.
        self._fight_arm_at = 0.0
        self._fight_wait_since: Optional[float] = None
        self._fight_wait_report = 0.0
        # A farm order is a standing state, not a per-step action. Re-issuing
        # farm.start reset the page's session (kills/kamas back to zero) and the
        # controller's snapshot recovery in between kept the loop busy without
        # progress. While the FarmBot is enabled the loop holds and watches.
        self._farm_wait = False
        self._farm_wait_since: Optional[float] = None
        self._farm_wait_report = 0.0
        self._farm_progress = None
        self._farm_progress_at = 0.0
        self._farm_objective = None
        # Board snapshots are the raw material for cell-level combat training:
        # the in-page bot plays, but the board and the eventual outcome are
        # still a labelled example for a plan ranker.
        self.fight_board_path = self.log_path.with_name("fight_boards.jsonl")
        self._fight_board_at = 0.0
        self._fight_board_key: Optional[bytes] = None
        # Maintained in memory: stat() per step / per board was one syscall per
        # append just to decide whether to rotate.
        try:
            self._log_size = self.log_path.stat().st_size
        except OSError:
            self._log_size = 0
        try:
            self._fight_log_size = self.fight_board_path.stat().st_size
        except OSError:
            self._fight_log_size = 0
        executor = getattr(runtime, "executor", None)
        if executor is not None and getattr(executor, "fights", None) is None:
            executor.fights = self.fights
        if executor is not None and self.knowledge is not None:
            executor.canonical_store = self.knowledge
        self._last_obs: Optional[Dict[str, Any]] = None
        # The last object that already carries both knowledge and fight
        # annotations. The carried post-action snapshot was enriched again at
        # the top of the next step; returning the prepared object avoids that
        # duplicate pass while preserving the same observation contract.
        self._prepared_obs: Optional[Dict[str, Any]] = None
        # Provenance of `_last_obs`, recorded with the step it justified. A
        # snapshot the loop reused must not be written down as if it arrived
        # with the call: that is the difference between a training pair and a
        # snapshot paired with an action taken much later.
        self._obs_age_ms: Optional[float] = None
        self._obs_source: str = "call"
        # G9: one run, one character. The bridge addresses an instance by slot, not by
        # account, so a slot that reloaded into a different login looks exactly like a
        # kick unless the character itself is compared.
        self._identity: Optional[Dict[str, Any]] = None
        self._identity_drift: Optional[Dict[str, Any]] = None
        self._recent: List[str] = []
        # Consecutive no-op outcomes per action tool. Read-only recoveries do
        # not reset it: farm.start -> state.snapshot -> farm.start used to run
        # forever because every snapshot cleared the exact-repeat streak.
        self._action_noops: Dict[str, int] = {}
        # Guard refusals are decisions, not crashes. A refused call is reported to
        # the policy and the controller, and the loop takes another step; only the
        # same refusal three times in a row is a dead end for the goal.
        self._refusals: List[str] = []
        self._index = 0
        self._log_path().parent.mkdir(parents=True, exist_ok=True)

    # -- plumbing ------------------------------------------------------------

    def _log_path(self) -> Path:
        return self.log_path

    def _rotate_log(self) -> None:
        """Keep an unlimited run from producing one unbounded step log."""
        try:
            if self._log_size >= LOG_MAX_BYTES:
                stamp = time.strftime("%Y%m%d-%H%M%S")
                rotated = self.log_path.with_name(
                    f"{self.log_path.stem}.{stamp}{self.log_path.suffix}")
                suffix = 0
                while rotated.exists():
                    suffix += 1
                    rotated = self.log_path.with_name(
                        f"{self.log_path.stem}.{stamp}-{suffix}{self.log_path.suffix}")
                self.log_path.rename(rotated)
                self._log_size = 0
        except OSError:
            pass

    def _append_log_line(self, blob: bytes) -> None:
        self._rotate_log()
        try:
            with self.log_path.open("ab") as handle:
                handle.write(blob)
            self._log_size += len(blob)
        except OSError:
            pass  # a full disk must not stop the bot from stopping cleanly

    def _emit(self, step: Step) -> None:
        if not self._first_step_at:
            self._first_step_at = step.at
        self._last_step_at = step.at
        # A step is the end of a wait: the streak that bounds the idle passes restarts,
        # and a reason written for a directive no longer labels anything.
        self._consecutive_waits = 0
        self._forced_reason = ""
        self._step_total += 1
        self._verdict_counts[step.verdict] = (
            self._verdict_counts.get(step.verdict, 0) + 1)
        self._source_counts[step.source] = (
            self._source_counts.get(step.source, 0) + 1)
        self.steps.append(step)
        if len(self.steps) > self.step_history:
            del self.steps[:len(self.steps) - self.step_history]
        self._append_log_line(_fast_dumps(step.as_dict()) + b"\n")

    def _emit_decision(self, decision: Decision, call: ToolCall, result: Any,
                       obs: Optional[Dict[str, Any]], ok: Optional[bool] = None,
                       error: Any = None) -> None:
        """Log the repair decision next to the step that provoked it.

        A decision is not recoverable from the steps: a retry and a continue both end
        in a tool call, so the old log could only guess the action from the tool name.
        Guessing is how the first reality check ended up ranking strategies it had
        inferred rather than observed. One extra line per decision removes the guess
        and makes the log a bandit dataset (state, action, reward) instead of a
        transcript.

        `ok`/`error` must be the values the controller was given, not the transport
        result: the bridge reports success for a call the game refused, so recording
        `result.ok` made 455 rule refusals read as successful steps with no error text
        -- and every offline analysis of "why did that repair fail" was then looking at
        a field that was always null.
        """
        if ok is None:
            ok = bool(getattr(result, "ok", False))
        if error is None:
            error = getattr(result, "error", None)
        self._append_log_line(_fast_dumps({
                    "kind": "decision",
                    "session": self.session,
                    "goal": None,
                    "action": decision.action,
                    "cause": decision.cause,
                    "explored": bool(decision.explored),
                    "confidence": decision.confidence,
                    "tool": call.tool,
                    "ok": bool(ok),
                    "error": error,
                    "obs": obs if isinstance(obs, dict) else {},
                    "at": time.time(),
                }) + b"\n")

    def _should_stop(self) -> Optional[str]:
        """A pre-existing stop file is reported explicitly.

        Otherwise the loop exits with "done: stop file" on its first check and the
        run looks like it did nothing -- which is exactly what a leftover file from
        the previous session produces.
        """
        drift = getattr(self, "_identity_drift", None)
        if drift is not None:
            bound = drift.get("bound") or {}
            seen = drift.get("seen") or {}
            return ("the slot logged into a different character (%s -> %s)"
                    % (bound.get("name") or bound.get("id"),
                       seen.get("name") or seen.get("id")))
        # Only three things end a run: the goals are done, the user stops it, or
        # an explicit budget was set. There is deliberately no default timeout --
        # a session that quits on a clock leaves the character mid-task.
        if self.max_steps > 0 and self._index >= self.max_steps:
            return f"step budget reached ({self.max_steps})"
        if self.deadline and time.time() >= self.deadline:
            return "time budget reached"
        if self.stop_file.exists():
            return f"stop file present ({self.stop_file.name})"
        return None

    def _check_identity(self, obs: Any) -> bool:
        """Bind the run to one character and fail closed when the slot changes.

        `snap.character` carries the client's own `{id, name}`. Unknown (character
        select, an older page, a null field) is not a change; only two comparable
        identities that disagree are. The id is authoritative when both sides have
        one -- a rename is the same account -- and the name is the fallback for a
        build that does not expose the id.
        """
        char = obs.get("character") if isinstance(obs, dict) else None
        if not isinstance(char, dict):
            return getattr(self, "_identity_drift", None) is None
        # At character selection the client can still hold the last character's base
        # informations; binding to that would turn a legitimate login into a drift on
        # the first real snapshot. Identity is only known in the world.
        in_world = obs.get("in_world")
        if in_world is not True:
            position = obs.get("position") if isinstance(obs.get("position"), dict) else {}
            if in_world is False or position.get("map_id") in (None, 0):
                return getattr(self, "_identity_drift", None) is None
        cid = char.get("id")
        name = char.get("name")
        if cid is None and not name:
            return getattr(self, "_identity_drift", None) is None
        seen = {"id": str(cid) if cid is not None else None,
                "name": str(name) if name not in (None, "") else None}
        if self._identity is None:
            self._identity = seen
            self._append_log_line(_fast_dumps({
                "kind": "identity", "session": self.session, "event": "bound",
                "character_id": seen["id"], "name": seen["name"],
                "at": time.time(),
            }) + b"\n")
            return True
        bound = self._identity
        if bound.get("id") is not None and seen.get("id") is not None:
            same = bound["id"] == seen["id"]
        elif bound.get("name") and seen.get("name"):
            same = bound["name"] == seen["name"]
        else:
            same = True          # nothing comparable on both sides
        if same:
            # Fill in a field the first snapshot lacked; never rewrite one.
            if seen.get("id") is not None and bound.get("id") is None:
                bound["id"] = seen["id"]
            if seen.get("name") and not bound.get("name"):
                bound["name"] = seen["name"]
            return True
        self._identity_drift = {"bound": dict(bound), "seen": dict(seen)}
        self._append_log_line(_fast_dumps({
            "kind": "identity", "session": self.session, "event": "drift",
            "bound": dict(bound), "seen": dict(seen), "at": time.time(),
        }) + b"\n")
        return False

    # -- observation ---------------------------------------------------------

    @staticmethod
    def _obs_map(obs: Any) -> Optional[int]:
        if not isinstance(obs, dict):
            return None
        for value in (obs.get("map_id"), (obs.get("position") or {}).get("map_id"),
                      (obs.get("map") or {}).get("map_id")):
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _obs_cell(obs: Any) -> Optional[int]:
        if not isinstance(obs, dict):
            return None
        for value in (obs.get("cell"), (obs.get("position") or {}).get("cell"),
                      (obs.get("map") or {}).get("cell")):
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                continue
        return None

    @classmethod
    def _world_key(cls, obs: Any) -> Tuple[Any, ...]:
        if not isinstance(obs, dict):
            return ()
        objective = obs.get("current_objective")
        objective_id = objective.get("objective_id") if isinstance(objective, dict) else None
        return (cls._obs_map(obs), cls._obs_cell(obs), bool(obs.get("in_dialog")),
                bool(obs.get("in_fight")), objective_id,
                obs.get("objectives_completed"), bool(obs.get("auto_fight")))

    @classmethod
    def _action_complete(cls, call: Any, before: Any, after: Any) -> bool:
        """Has the action reached the state it promised, not just started?"""
        if not isinstance(after, dict) or not after:
            return False
        before = before if isinstance(before, dict) else {}
        tool = str(getattr(call, "tool", "") or "")
        args = getattr(call, "args", {}) or {}
        if tool == "nav.goto":
            target = args.get("map_id")
            if target is not None:
                try:
                    return cls._obs_map(after) == int(target)
                except (TypeError, ValueError):
                    return False
            target_cell = args.get("cell")
            if target_cell is not None:
                try:
                    return cls._obs_cell(after) == int(target_cell)
                except (TypeError, ValueError):
                    return False
            before_map, after_map = cls._obs_map(before), cls._obs_map(after)
            return bool(after_map) and after_map != before_map
        if tool == "quest.advance":
            if after.get("in_dialog") and not before.get("in_dialog"):
                return True
            if after.get("objectives_completed") != before.get("objectives_completed"):
                return True
            b_obj = before.get("current_objective")
            a_obj = after.get("current_objective")
            if (a_obj or {}).get("objective_id") != (b_obj or {}).get("objective_id"):
                return True
            before_map, after_map = cls._obs_map(before), cls._obs_map(after)
            return bool(after_map) and after_map != before_map
        if tool in ("fight.join", "farm.start"):
            if after.get("in_fight") and not before.get("in_fight"):
                return True
            return bool(after.get("auto_fight")) and not bool(before.get("auto_fight"))
        return bool(changed_keys(before, after))

    def _settle(self, before: Any, after: Any, call: Any = None,
                step_s: float = 0.4) -> Any:
        """Wait until the action completes, then until the world is quiet.

        Returning on the first cell change let the next directive interrupt a
        walk or a dialog that was still running. The loop issues one action at a
        time: wait for the tool's own completion predicate (map change, target
        cell, dialog/objective, fight), or, for actions without one, until the
        world has been stable for a short quiet window. Only then is the next
        decision allowed, with the caller's think pause on top.
        """
        if self.settle_s <= 0:
            return after
        if not isinstance(before, dict):
            before = after if isinstance(after, dict) else None
            if before is None:
                return after
        tool = str(getattr(call, "tool", "") or "")
        # `_action_complete` knows what these four tools promised; for every other
        # tool "some key changed" is the best available definition of done.
        promised = tool in SETTLE_TOOLS
        window = self.settle_s
        if tool in ("nav.goto", "quest.advance"):
            window = max(window, 6.0)
        elif tool in ("fight.join", "farm.start"):
            window = max(window, 4.0)
        deadline = time.monotonic() + window
        quiet_s = min(0.8, max(0.4, self.think_s + 0.3))
        best = after
        last_key = self._world_key(best)
        last_change = time.monotonic()
        delay = step_s
        while time.monotonic() < deadline:
            if isinstance(best, dict) and best:
                if self._action_complete(call, before, best):
                    self._record_settled(best, after)
                    return best
                key = self._world_key(best)
                if key != last_key:
                    last_key = key
                    last_change = time.monotonic()
                elif (not promised
                      and changed_keys(before, best)
                      and time.monotonic() - last_change >= quiet_s):
                    # A quiet world is not a finished action. `changed_keys(before,
                    # best)` compares against the *start*, so any unrelated tick --
                    # AP, HP, a cell drift -- keeps this branch eligible, and a
                    # predicate tool used to exit here after ~0.8 s. Measured on the
                    # live log: `quest.advance` rows judged noop returned in 0.80 s
                    # (median) while the dialog they queued opened 1.2 s later, so
                    # 480 accepted claims were scored as doing nothing and the loop
                    # moved on -- and then interrupted its own walk. For a tool whose
                    # promise we can check, only the promise ends the wait.
                    self._record_settled(best, after)
                    return best
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(delay, remaining))
            delay = min(delay * 1.6, max(step_s, 0.8))
            nxt = self.observe(max_age_ms=-1.0, record=False)
            if nxt:
                best = nxt
        self._record_settled(best, after)
        return best

    def _record_observation(self, observation: Any) -> None:
        writer = getattr(getattr(self, "runtime", None), "observe", None)
        if callable(writer) and isinstance(observation, dict) and observation:
            try:
                writer(observation, source="live")
            except Exception:  # noqa: BLE001 - a log failure must not fail a step
                pass

    def _record_settled(self, best: Any, after: Any) -> None:
        # Record only a state produced by a poll. If no poll happened, `after`
        # came either from observe() (already recorded) or from the directive's
        # piggybacked snapshot (adopted without a row, as before).
        if best is after or not isinstance(best, dict):
            return
        writer = getattr(self.runtime, "observe", None)
        if callable(writer):
            try:
                writer(best, source="live")
            except Exception:  # noqa: BLE001 - a log failure must not fail a step
                pass

    def observe(self, max_age_ms: float = OBS_FRESH_MS,
                record: bool = True) -> Optional[Dict[str, Any]]:
        """Return the freshest state we can get, and remember how fresh it is.

        The runtime holds the snapshot the bridge returned with the previous
        directive. That snapshot is the state *after* that action, so it is a
        legitimate decision input for the next one — but only while it is
        recent. Anything older, or of unknown age, is fetched again: deciding
        on an old snapshot is how the loop replays a plan against a world that
        has already moved on.
        """
        current, age_ms, source = self.runtime.current_observation()
        if (isinstance(current, dict) and current and age_ms is not None
                and age_ms <= max_age_ms):
            if current is self._prepared_obs:
                self._last_obs = current
                self._obs_age_ms, self._obs_source = age_ms, source
                return current
            current = self._prepare_obs(current)
            self._last_obs = current
            self._obs_age_ms, self._obs_source = age_ms, source
            return current

        if self.dry_run:
            return self._last_obs

        executor = getattr(self.runtime, "executor", None)
        instance = getattr(self.runtime, "instance", None)
        snap = None
        # A polling read is not a step. `snapshot()` exists so the settle loop
        # can re-read the state without writing a state.snapshot row for every
        # poll; test doubles that only implement `execute` still work.
        reader = getattr(executor, "snapshot", None)
        if callable(reader):
            snap = reader(instance=instance)
        elif executor is not None:
            result = executor.execute(
                "state.snapshot", {}, instance=instance, source="live")
            if result.ok and isinstance(getattr(result, "data", None), dict):
                snap = result.data.get("snapshot") or result.data
        if isinstance(snap, dict) and snap:
            snap = self._prepare_obs(snap)
            self._last_obs = snap
            self._obs_age_ms, self._obs_source = 0.0, "call"
            if record:
                self.runtime.observe(snap, source="live")
            else:
                # Adopt without writing a full observation row; the settle
                # loop records the final state once.
                note = getattr(self.runtime, "note_observation", None)
                if callable(note):
                    note(snap)
            return snap
        if isinstance(current, dict) and current:
            # No independent read path (a test double, or a bridge that just
            # failed): use the runtime's copy, but say it is stale so the
            # recorder never presents it as a fresh pairing.
            current = self._prepare_obs(current)
            self._last_obs = current
            self._obs_age_ms, self._obs_source = age_ms, "stale"
            return current
        self._obs_source = "stale"
        return self._last_obs

    def _prepare_obs(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        prepared = self._annotate_fights(
            enrich_observation(observation, self.knowledge))
        self._prepared_obs = prepared
        return prepared

    # -- the fight advisor ---------------------------------------------------

    def _refresh_character(self, force: bool = False) -> None:
        """Keep the advisor's character sheet current, and cheap to keep.

        The estimator needs stats, spells and HP, which only `debug.character`
        provides. Polling it every step would be a second round trip per action;
        the sheet only changes on a level-up or an equipment change. A 60-second
        TTL is the fallback, and a character signature (level, max HP, breed,
        spells, equipment) forces an immediate refresh when the player changes
        something mid-run.
        """
        if self.dry_run:
            return
        sheet = self.fights.character
        now = time.time()
        signature = character_key(self._last_obs)
        changed = (sheet is not None and self._character_key is not None
                   and signature != self._character_key)
        if not force and not changed and sheet is not None \
                and (now - self._character_at) < self.character_ttl_s:
            return
        # A failed fetch must not count as a fresh sheet: without this gate the
        # first attempt before login would lock fights out for the whole TTL.
        if not force and not changed \
                and (now - self._character_try) < self.character_retry_s:
            return
        if changed:
            print("[autopilot] character sheet changed; refreshing")
        executor = getattr(self.runtime, "executor", None)
        reader = getattr(executor, "character_report", None)
        if not callable(reader):
            return
        self._character_try = now
        report = reader(instance=getattr(self.runtime, "instance", None))
        if report is not None and self.fights.use_character_report(report):
            self._character_at = now
            self._character_key = signature

    def _annotate_fights(self, obs: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Attach the per-group estimate the planner reads on this map.

        The actor never sees this; it is the controller's view of "can I take
        that group". Maps without monsters cost nothing, and the estimate is
        cached by group composition, not recomputed per snapshot.
        """
        if not isinstance(obs, dict) or not obs or obs.get("in_fight"):
            return obs
        groups = self.fights.groups(obs)
        if not groups:
            return obs
        self._refresh_character()
        return self.fights.annotate(obs, groups=groups)

    # -- guards --------------------------------------------------------------

    def guard(self, call: ToolCall) -> Optional[str]:
        """Return a refusal reason, or None when the call is safe to send."""
        if call.tool in FORBIDDEN_TOOLS:
            return ("refused: buying spends money irreversibly and is not automated; "
                    "a human does the purchase")
        if call.tool in LISTING_TOOLS:
            price = call.args.get("price")
            rule = call.args.get("price_rule")
            if isinstance(price, int) and not isinstance(price, bool):
                if price < PRICE_FLOOR:
                    return f"refused: price {price} is below the {PRICE_FLOOR} kama floor"
            elif rule in MEASURED_PRICE_RULES:
                # The page resolves the rule from a price the SERVER sent and must
                # report `price_source`; if it does not, the listing is recorded as
                # unverified rather than as a listing (see run()).
                pass
            else:
                return ("refused: a listing needs an explicit integer price, or a rule "
                        f"({', '.join(MEASURED_PRICE_RULES)}) the client can resolve from a "
                        "measured market price")
        ok, err = validate_call(call.tool, call.args)
        if not ok:
            return f"refused: schema rejected the call ({err})"
        if call.tool in FIGHT_TOOLS:
            return self._fight_guard(call)
        return None

    def _fight_guard(self, call: ToolCall) -> Optional[str]:
        """Refuse a fight the estimator cannot say yes to.

        The plan's rule is explicit: no unattended fight without a calibrated
        estimate that clears the threshold, the HP-loss limit, and the boss
        opt-in. A failed estimate is not permission to try.
        """
        observation = self._last_obs
        if not isinstance(observation, dict) or not observation:
            return "refused: no observation, so this fight cannot be estimated"
        if observation.get("in_fight"):
            return None  # already committed; the fight loop owns it now
        self._refresh_character()
        return self.fights.assess_call(call.tool, dict(call.args or {}), observation)

    def _track_fight(self, before: Optional[Dict[str, Any]],
                     after: Optional[Dict[str, Any]]):
        """Write the estimate when a fight starts, and the outcome when it ends.

        These two rows are the calibration set: the estimate is a falsifiable
        claim, and the HP before/after is the evidence. A fight that starts
        without an estimate (the session began mid-fight) writes nothing.
        """
        if not isinstance(after, dict):
            return None, None
        was = bool(isinstance(before, dict) and before.get("in_fight"))
        now = bool(after.get("in_fight"))
        if now and not was and self.fight_log.pending is None:
            estimate = self.fights.last_estimate
            if isinstance(estimate, dict) and estimate.get("features"):
                self.fight_log.start(estimate, before, session=self.session)
                return estimate, None
            return None, None
        # A fight can also end between two steps, so the resolution is driven
        # by the pending claim, not by watching `in_fight` go false.
        if self.fight_log.pending is not None and not now:
            outcome = self.fight_log.resolve(after, session=self.session)
            self._record_fight_outcome(outcome, after)
            return None, outcome
        return None, None

    # -- combat board capture ------------------------------------------------

    def _append_fight_log(self, record: Dict[str, Any]) -> None:
        try:
            self.fight_board_path.parent.mkdir(parents=True, exist_ok=True)
            if self._fight_log_size >= LOG_MAX_BYTES:
                stamp = time.strftime("%Y%m%d-%H%M%S")
                rotated = self.fight_board_path.with_name(
                    f"{self.fight_board_path.stem}.{stamp}"
                    f"{self.fight_board_path.suffix}")
                suffix = 0
                while rotated.exists():
                    suffix += 1
                    rotated = self.fight_board_path.with_name(
                        f"{self.fight_board_path.stem}.{stamp}-{suffix}"
                        f"{self.fight_board_path.suffix}")
                self.fight_board_path.rename(rotated)
                self._fight_log_size = 0
            blob = _fast_dumps(_redact(record)) + b"\n"
            with self.fight_board_path.open("ab") as handle:
                handle.write(blob)
            self._fight_log_size += len(blob)
        except OSError as exc:
            print(f"[autopilot] could not write fight board log: {exc}")

    def _record_fight_board(self, goal: Optional[str]) -> None:
        """Snapshot the live board once per distinct turn/HP/cell state.

        This is read-only and deliberately separate from the action trajectory:
        the in-page bot is playing, so the board is observation data for a
        learned plan ranker, not a record of an action this agent chose.
        """
        if not self.fight_boards:
            return
        executor = getattr(self.runtime, "executor", None)
        reader = getattr(executor, "fight_state", None)
        if not callable(reader):
            return
        try:
            board = reader()
        except Exception:  # noqa: BLE001 - a diagnostic read must not break the loop
            return
        if not isinstance(board, dict) or not board.get("in_fight"):
            return
        key = _fast_dumps({
            "fight_id": board.get("fight_id"),
            "round": board.get("round"),
            "turn": board.get("current_fighter_id"),
            "my_cell": board.get("my_cell"),
            "my_turn": board.get("is_my_turn"),
            "fighters": [
                [f.get("id"), f.get("hp"), f.get("cell"), f.get("alive")]
                for f in (board.get("fighters") or [])
                if isinstance(f, dict)
            ],
        })
        if key == self._fight_board_key:
            return
        self._fight_board_key = key
        self._append_fight_log({
            "kind": "fight_board",
            "run": self.session,
            "goal": goal,
            "board": board,
        })

    def _record_interventions(self, observation: Optional[Dict[str, Any]]) -> int:
        """Log trusted human input that arrived while the loop was running.

        The goal counts "zero manual interventions", and until the client could
        tell a real input event from the agent's own, nothing on disk could answer
        it: a steered run and an unattended run wrote identical rows. The page
        distinguishes them because everything the agent clicks is synthesised with
        `dispatchEvent` (`isTrusted: false`), so only genuine human input is
        counted in `user_input.during_run`.

        Counters are monotonic per client session, so the delta against the last
        value seen is the number of new interventions since this step. A page
        reload resets them, which is why the seen-watermark is taken downwards too
        instead of only ever rising: without that, one reload would re-report the
        same clicks as new interventions and inflate the number the goal is
        measured by.
        """
        if not isinstance(observation, dict):
            return 0
        ui = observation.get("user_input")
        if not isinstance(ui, dict):
            return 0
        try:
            seen = int(ui.get("during_run") or 0)
        except (TypeError, ValueError):
            return 0
        if seen < self._intervention_seen:
            self._intervention_seen = seen          # the page reloaded; re-baseline
            return 0
        delta = seen - self._intervention_seen
        if delta <= 0:
            return 0
        self._intervention_seen = seen
        self._intervention_total += delta
        self._append_log_line(_fast_dumps({
            "kind": "intervention",
            "session": self.session,
            "events": delta,
            "by_type": ui.get("by_type") or {},
            "client_last_ts": ui.get("last_ts"),
            "at": time.time(),
        }) + b"\n")
        return delta

    def _record_fight_outcome(self, outcome: Optional[Dict[str, Any]],
                              observation: Optional[Dict[str, Any]]) -> None:
        if not isinstance(outcome, dict):
            return
        self._append_fight_log({
            "kind": "fight_outcome",
            "run": self.session,
            "outcome": outcome,
            "observation": observation,
        })

    def _wait_out_fight(self, observation: Dict[str, Any],
                        goal: Optional[str] = None) -> None:
        """Hold the goal while the page plays the fight.

        A fight is turn-based: most of its wall-clock time belongs to the
        server and the animations, not to the policy. Issuing quest or travel
        directives during that time cannot work, and a recovery order formed
        before the fight (`safe.stop` in particular) disables the in-page bot
        that clicks ready. So the loop stays here and re-observes; the fight
        end is the signal to resume the goal on fresh state.

        Fights also start without `fight.join` -- a dialog reply or a quest can
        open one -- so auto-fight can be off when the first in-fight snapshot
        arrives. `fight.join` with no target only arms the bot when a fight is
        already open; it never attacks a second group.
        """
        if self._forced_call is not None:
            self._forced_call = None
            print("[autopilot] dropping a pre-fight recovery order; the fight "
                  "owns the next action")
        now = time.time()
        if self._fight_wait_since is None:
            self._fight_wait_since = now
            self._fight_wait_report = now
            print("[autopilot] in a fight; waiting out the turns instead of "
                  "sending world actions")
            # One more chance for a brain that was not up at startup (crashed, or was
            # never started because nothing asked). Bounded by brain_retry_s and by the
            # final-state rule, so a brain that cannot exist costs one probe.
            self._ensure_brain("fight_start")
        if (not self.dry_run and not observation.get("auto_fight")
                and now - self._fight_arm_at >= 5.0):
            self._fight_arm_at = now
            executor = getattr(self.runtime, "executor", None)
            if executor is not None:
                result = executor.execute(
                    "fight.join", {}, instance=getattr(self.runtime, "instance", None),
                    goal=goal, source="live", observation=observation,
                    observation_age_ms=self._obs_age_ms,
                    observation_source=self._obs_source)
                data = getattr(result, "data", None)
                armed = bool(isinstance(data, dict) and data.get("auto_fight"))
                print("[autopilot] auto-fight was off; arm -> "
                      + ("on" if armed else "not yet")
                      + ("" if getattr(result, "ok", False)
                         else " (" + str(getattr(result, "error", ""))[:80] + ")"))
        if now - self._fight_wait_report >= 30.0:
            self._fight_wait_report = now
            print(f"[autopilot] fight wait {now - self._fight_wait_since:.0f}s; "
                  f"auto_fight={bool(observation.get('auto_fight'))}")
        # A fight board only changes on a turn/action, and those states last
        # seconds. Sampling every second meant a second `fight_state` round
        # trip per second on top of the loop's world snapshot for no new
        # training row (the key dedupe ran only after the fetch).
        if now - self._fight_board_at >= 2.0:
            self._fight_board_at = now
            self._record_fight_board(goal)
        time.sleep(1.0)

    @staticmethod
    def _farm_counters(observation: Dict[str, Any]) -> Optional[tuple]:
        """(fights, kills, xp, kamas, drops) from the page session, if present."""
        session = observation.get("farm_session") if isinstance(observation, dict) else None
        if not isinstance(session, dict):
            return None
        return (session.get("fights"), session.get("kills"), session.get("xp"),
                session.get("kamas"), session.get("drop_count"))

    def _wait_out_farm(self, observation: Dict[str, Any],
                       goal: Optional[str] = None) -> None:
        """Hold the goal while the page's FarmBot is playing.

        The bot is a standing order: re-issuing farm.start is unnecessary and
        (before the page-side guard) reset its counters every cycle. Stay here
        while auto_fight is on, report progress, and resume planning when the
        farm stops, advances a quest objective, or stops producing for two
        minutes so the scheduler can try another route.
        """
        if self._forced_call is not None:
            self._forced_call = None
        # The farm order is a standing state, not a repair; do not credit the
        # controller's strategy with an unrelated step after the wait.
        self._pending = None
        self._pending_age = 0
        now = time.time()
        if not observation.get("auto_fight"):
            if self._farm_wait:
                print("[autopilot] the farm stopped; resuming planning")
                self._record_observation(observation)
            self._farm_wait = False
            self._farm_wait_since = None
            return
        if self._farm_wait_since is None:
            self._farm_wait_since = now
            self._farm_wait_report = now
            self._farm_progress = self._farm_counters(observation)
            self._farm_progress_at = now
            self._farm_objective = observation.get("objectives_completed")
            print("[autopilot] farm is running; holding the goal and watching progress")
        counters = self._farm_counters(observation)
        if counters is not None and counters != self._farm_progress:
            self._farm_progress = counters
            self._farm_progress_at = now
        progress = observation.get("objectives_completed")
        if (isinstance(progress, (int, float))
                and isinstance(self._farm_objective, (int, float))
                and progress > self._farm_objective):
            print("[autopilot] farm advanced the objective; resuming planning")
            self._record_observation(observation)
            self._farm_wait = False
            self._farm_wait_since = None
            return
        if now - self._farm_progress_at >= FARM_IDLE_S:
            print(f"[autopilot] the farm produced no kills/xp/kamas for "
                  f"{FARM_IDLE_S:.0f}s; resuming planning")
            self._record_observation(observation)
            self._farm_wait = False
            self._farm_wait_since = None
            return
        if now - self._farm_wait_report >= 30.0:
            self._farm_wait_report = now
            session = observation.get("farm_session") or {}
            print(f"[autopilot] farm wait {now - self._farm_wait_since:.0f}s; "
                  f"kills={session.get('kills')} kamas={session.get('kamas')} "
                  f"xp={session.get('xp')}")
        # The in-page bot plays the fights; the agent only watches for a stop,
        # an objective change or a stalled session. A 5 s poll cuts the idle
        # full-state snapshots by 60% without delaying any action it owns.
        time.sleep(5.0)

    # -- labelling -----------------------------------------------------------

    def _verdict(self, result, before: Optional[Dict[str, Any]],
                 after: Optional[Dict[str, Any]], call: Optional[ToolCall] = None,
                 latency_ms: float = 0.0) -> Verdict:
        """Ask the second agent. It returns verdict + residual + drift together,
        so the loop stops re-deriving them from different key lists."""
        game_error = _game_error(result)
        return self.verifier.label(
            tool=(call.tool if call is not None else "-"),
            args=(dict(call.args) if call is not None else {}),
            ok=bool(result.ok) and game_error is None,
            error=(game_error if game_error is not None
                   else (None if result.ok else str(result.error))),
            before=before,
            after=after,
            latency_ms=latency_ms,
            session=self.session,
            index=self._index,
        )

    # -- the loop ------------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        """Own the lock for the whole run, however it ends.

        A loop that dies without clearing its lock would block the idle trainer
        forever, so the release is in a `finally`, not on the happy path.
        """
        self._write_lock()
        try:
            return self._run()
        finally:
            # A fight still open when the session ends is not a win or a loss;
            # recording it as unresolved keeps it out of the calibration set
            # without losing the fact that it happened.
            self.fight_log.flush("session_end")
            self._clear_lock()

    def _run(self) -> Dict[str, Any]:
        tools = [t["name"] for t in TOOL_SCHEMA["tools"]]
        budget = "unlimited" if self.max_steps <= 0 else (str(self.max_steps) + " steps")
        print(f"[autopilot] {len(self.goals)} goal(s), {budget}"
              f"{' (dry run)' if self.dry_run else ''}")
        if self.preflight:
            self._warn_if_no_bridge()
            if not self._require_world():
                return self.summary(self._should_stop()
                                    or "stopped while waiting for the world")
            # Before the first fight, not during it: the page's fallback plays the
            # turns while the brain warms up, so starting it up front is what makes
            # "the learned policy decided this" true for the whole session.
            self._ensure_brain()

        for goal in self.goals:
            failures = 0
            # A directive the previous goal's repair queued must not be executed under
            # this goal's label. Observed live-shaped: a `noop` on one goal made the
            # controller force the same call again, the goal ended on its one directive,
            # and the next goal began by dispatching the previous goal's action.
            self._forced_call = None
            self._forced_reason = ""
            self._consecutive_waits = 0
            self._farm_wait = False
            self._farm_wait_since = None
            self._action_noops.clear()
            # Why this run ended. "goals exhausted" used to be reported even when the
            # session had died, which reads as success and hides the one thing worth
            # knowing.
            ended_because = "goals exhausted"
            # An unlimited step budget has to mean unlimited.
            #
            # `steps_per_goal` is a per-goal slice: Order mode means "do this one
            # thing, a few steps, done". Auto mode means "keep working until I stop
            # you". The two were conflated, so with --steps 0 the loop still ran
            # exactly eight directives per goal and then exited 0 — which from
            # outside is indistinguishable from "the AI does nothing". It was doing
            # eight steps and quitting, by design, every single time.
            budget = self.steps_per_goal if self.max_steps > 0 else None
            taken = 0
            while budget is None or taken < budget:
                taken += 1
                reason = self._should_stop()
                if reason:
                    print(f"[autopilot] stopping: {reason}")
                    return self.summary(reason)

                # The thinking token: a short deliberate dwell after a
                # world-changing action, on top of the completion gate. It is
                # skipped for reads, which do not need animation time.
                think = self._think_until - time.monotonic()
                if think > 0:
                    time.sleep(min(think, 1.0))

                # Idle farm polls are adopted, not recorded: an hours-long farm
                # would otherwise write a trajectory row every few seconds.
                before = self.observe(record=not self._farm_wait)
                if not self._check_identity(before):
                    print("[autopilot] stopping: " + str(self._should_stop()))
                    return self.summary(self._should_stop() or "identity drift")
                if isinstance(before, dict) and before.get("in_fight"):
                    # The page is playing a turn-based fight. Planning a quest
                    # step here was the live failure: `quest.advance` ran for
                    # minutes against a board, and the ready click never came
                    # because auto-fight had never been armed.
                    self._wait_out_fight(before, goal)
                    taken -= 1  # waiting is not one of the goal's directives
                    continue
                self._fight_wait_since = None
                self._fight_wait_report = 0.0
                self._fight_arm_at = 0.0
                if self.fight_log.pending is not None:
                    # The fight can end between two steps. Record its outcome
                    # with this fresh observation instead of waiting for the
                    # next action, which may never come if the run stops here.
                    self._track_fight(None, before)
                if self._farm_wait:
                    self._wait_out_farm(before, goal)
                    taken -= 1
                    continue
                if self._forced_call is not None:
                    call = self._forced_call
                    self._forced_call = None
                    print(f"[autopilot] agent2 directs: {call.tool} {call.args}")
                else:
                    call = self.runtime.policy.choose(goal, before or {}, tools)
                if call is not None and call.tool == "farm.start" \
                        and not self._farm_allowed(goal, before):
                    # The learned planner has this gate too, but the ThinkingLayer
                    # and controller can force a call around it. This is the one
                    # invariant every source must respect: a quests prompt never
                    # starts a farm session for a non-delivery objective.
                    print("[autopilot] refusing farm.start under a quest goal")
                    self._forced_reason = ("a farm.start was refused because this "
                                           "prompt is a quest goal")
                    call = ToolCall("state.snapshot", {}, goal)
                if call is None:
                    self._emit(Step(index=self._index, goal=goal, tool="-", args={},
                                    ok=False, error="policy returned no call",
                                    latency_ms=0.0, verdict="blocked", session=self.session,
                                    source="rule",
                                    note="nothing in the policy matched this goal"))
                    # Agent 2 must see this too. A goal the policy cannot express is
                    # an invalid assumption, and skipping the controller here meant
                    # the most common failure of all was invisible to it.
                    decision = self.controller.on_step(
                        Verdict("blocked", 0.9, None, False, "rule"),
                        "-", False, "policy returned no call", before, None)
                    self.controller.save()
                    # And on the record: this branch used to return a decision the
                    # log never saw, so the one failure class the whole run turns on
                    # was invisible to any offline read of the repair data.
                    self._emit_decision(decision, ToolCall("-", {}, goal), None,
                                        before or {}, ok=False,
                                        error="policy returned no call")
                    if decision.action in ("replan", "abort"):
                        self._apply_decision(decision, goal, None)
                    print(f"[autopilot] no action for goal {goal!r}; moving on")
                    if not self._hinted:
                        self._hinted = True
                        print("[autopilot] the rule policy understands, for example: "
                              "'open inventory', 'list quests', 'farm', 'go to [4,-19]', "
                              "'sell all', 'scan hdv for <item>', 'join fight', 'stop'. "
                              "Anything else needs the encoder policy (--policy encoder).")
                    break

                # A read is not an action. `state.snapshot` is what the planner and
                # the thinking layer return when the thing they want has not happened
                # yet ("wait for the NPC", "wait for a map change", "wait for the
                # target to spawn", the scripted tutorial gate) and what every
                # controller recovery hint points at. Dispatching it as a step made
                # the wait look like work in three places at once: 3,010 of the
                # baseline's 4,287 steps were these reads, 3,005 of them scored
                # `advanced`, so the run's own success rate was mostly counting
                # looking -- and a policy trained on that corpus learns that looking
                # is the most reliable thing it can do. The loop already takes a
                # snapshot at the top of every iteration, so the directive carried no
                # information the next pass would not have anyway.
                #
                # Past MAX_CONSECUTIVE_WAITS the read is dispatched again, so a goal
                # with no action left in it stays a failure the loop can see rather
                # than turning into an unfalsifiable wait.
                if (call is not None and call.tool == "state.snapshot"
                        and self._consecutive_waits < MAX_CONSECUTIVE_WAITS):
                    if not self._hold_wait(goal, before):
                        ended_because = "stopped while waiting"
                        break
                    taken -= 1  # waiting is not one of the goal's directives
                    continue

                refusal = self.guard(call)
                if refusal:
                    self._emit(Step(index=self._index, goal=goal, tool=call.tool,
                                    args=dict(call.args), ok=False, error=refusal,
                                    latency_ms=0.0, verdict="blocked", note=refusal,
                                    session=self.session, source="guard"))
                    self._index += 1
                    print(f"[autopilot] {refusal}")
                    # A refusal is an outcome the policy must see. The planner only
                    # learns a claim was server-counted or a target unreachable if
                    # the refusal is reported back; before, it was dropped and the
                    # same call was chosen again on the next step.
                    reporter = getattr(self.runtime.policy, "note_result", None)
                    if reporter is not None:
                        try:
                            reporter(call.tool, False, refusal, before or {})
                        except Exception:  # noqa: BLE001 - history must not kill a run
                            pass
                    decision = self.controller.on_step(
                        Verdict("blocked", 0.9, None, False, "guard"),
                        call.tool, False, refusal, before, None)
                    self.controller.save()
                    self._emit_decision(decision, call, None, before,
                                        ok=False, error=refusal)
                    if decision.action == "replan":
                        self._apply_decision(decision, goal, call)
                    # A guard refusal is deterministic: no recovery call can turn
                    # it into a yes, and the controller's recovery hint (a plain
                    # snapshot) would reset the refusal streak below. Let the
                    # planner choose again instead.
                    key = (f"{call.tool}|"
                           f"{json.dumps(call.args, sort_keys=True, ensure_ascii=False)}|"
                           f"{refusal}")
                    if self._refusals and self._refusals[-1] == key:
                        self._refusals.append(key)
                    else:
                        self._refusals = [key]
                    if len(self._refusals) < 3:
                        if self.interval:
                            time.sleep(self.interval)
                        continue
                    # The same refused call three times in a row is a dead end:
                    # the report above did not change the plan. Retire the quest
                    # it belongs to and let the next plan pass choose another,
                    # instead of ending the whole run as a silent success.
                    self._refusals.clear()
                    if _tutorial_active(before):
                        print("[autopilot] the tutorial is holding this step open; "
                              "waiting instead of abandoning the goal")
                        time.sleep(1.0)
                        continue
                    qid = self._retire_named_quest(call, before)
                    if qid is not None:
                        print(f"[autopilot] retiring quest {qid}; taking another")
                        continue
                    print(f"[autopilot] the guard refuses every route to {goal!r}; "
                          "moving on")
                    break

                # A call that made it past the guard ends any refusal streak.
                self._refusals.clear()
                if self.dry_run:
                    self._emit(Step(index=self._index, goal=goal, tool=call.tool,
                                    args=dict(call.args), ok=True, error=None,
                                    latency_ms=0.0, verdict="noop", note="dry run"))
                    print(f"[autopilot] would call {call.tool} {call.args}")
                    self._index += 1
                    key = f"{call.tool}|{json.dumps(call.args, sort_keys=True, ensure_ascii=False)}"
                    self._recent.append(key)
                    del self._recent[:-32]
                    if len(self._recent) >= 3 and len(set(self._recent[-3:])) == 1:
                        print(f"[autopilot] dry run: {call.tool} repeats with nothing to change; "
                              f"moving on from {goal!r}")
                        break
                    if self.interval:
                        time.sleep(self.interval)
                    continue

                started = time.perf_counter()
                result = self.runtime.executor.execute(
                    call.tool, call.args, instance=self.runtime.instance,
                    goal=goal, source="live", observation=before,
                    observation_age_ms=self._obs_age_ms,
                    observation_source=self._obs_source)
                # Stop the clock before observing. `observe()` can cost seconds when
                # it has to ask a dead bridge, and counting that as the action's
                # latency made every step look slow for the wrong reason.
                latency_ms = (time.perf_counter() - started) * 1000.0
                # The bridge returns the state immediately after the directive ran.
                # Adopt it: it is the one after-state available without a second
                # round trip. Calling observe() here handed back the pre-action
                # snapshot instead, so settle compared `before` with `before` and
                # every action looked inert.
                data = getattr(result, "data", None)
                snap = data.get("snapshot") if isinstance(data, dict) else None
                if isinstance(snap, dict) and snap:
                    snap = self._prepare_obs(snap)
                    adopt = getattr(self.runtime, "note_observation", None)
                    if callable(adopt):
                        adopt(snap)
                    self._last_obs = snap
                    self._obs_age_ms, self._obs_source = 0.0, "carried"
                    after = snap
                else:
                    after = self.observe()

                # Transport success is not game success. Keep the two apart: the
                # first says the bridge reached the page, the second says the game
                # accepted the action. The loop acts on the second.
                game_error = _game_error(result)
                step_ok = bool(result.ok) and game_error is None
                step_error = (game_error if game_error is not None
                              else (None if result.ok else str(result.error)))

                # No client window is not a failed step: it is the absence of an
                # environment. Wait for one to exist (the host opens a client, the
                # operator relaunches) rather than replanning into another doomed
                # directive — the settle poll and the retry ladder below would each
                # spend a round trip on a page that is not there. Checked before the
                # settle so a dead client cannot trigger a poll storm.
                if not step_ok and client_absent(step_error):
                    if not self._recover_world(str(step_error)[:80],
                                               trigger="client_absent"):
                        ended_because = (self._should_stop()
                                         or ("the client window is gone and did not come "
                                             "back (open an instance in DofusFarm)"))
                        break
                    continue

                # A walk is not finished when the call returns. Give the tools that move
                # the character, open a dialog or start a fight a moment to land before
                # judging them — otherwise every one of them scores `noop` and the loop
                # retries an action that is already in flight.
                if step_ok and call.tool in SETTLE_TOOLS:
                    after = self._settle(before, after, call=call)
                if call.tool not in READ_ONLY_TOOLS and call.tool not in LISTING_TOOLS:
                    self._think_until = time.monotonic() + self.think_s

                # A successful farm order is a standing state. In Auto mode hold
                # the goal instead of asking the scheduler again; an Order-mode
                # farm keeps its bounded step budget so it still finishes.
                if step_ok and call.tool == "farm.start" and self.max_steps <= 0:
                    self._farm_wait = True
                    self._farm_wait_since = time.time()
                    self._farm_wait_report = self._farm_wait_since
                    self._farm_progress = self._farm_counters(
                        after if isinstance(after, dict) else {})
                    self._farm_progress_at = self._farm_wait_since
                    self._farm_objective = ((after or {}).get("objectives_completed")
                                            if isinstance(after, dict) else None)

                # A closed connection is not a failed step.
                #
                # When the game ends the session — a kick, a timeout, a login from
                # somewhere else — the page reloads and every directive after that answers
                # into a socket that no longer exists. Retrying looks identical to a
                # stubborn agent, and it keeps knocking on a server that just closed the
                # door.
                #
                # `connected` is not the signal: it stays true as long as the client's
                # connectionManager object exists, which it does at the login screen too.
                # Falling out of the world mid-run is: we were on a map, and now we are not.
                if isinstance(after, dict) and isinstance(before, dict):
                    reason = fell_out_of_world(before, after)
                    if reason is not None:
                        if not self._recover_world(reason or None):
                            ended_because = (self._should_stop()
                                             or ("the client left the world"
                                                 + (f" ({reason})" if reason else "")))
                            break
                        continue

                fight_estimate, fight_outcome = self._track_fight(before, after)
                self._record_interventions(after)
                self._record_dialog(call, before, after, result)

                v = self._verdict(result, before, after, call=call, latency_ms=latency_ms)
                verdict = v.verdict

                # Report the outcome back to the policy. `note_result` existed and was
                # never called, so the planner's attempt history was always empty — it
                # could not tell a tool that had just failed from one it had never
                # tried, and any history feature trained on that would be a fiction.
                reporter = getattr(self.runtime.policy, "note_result", None)
                if reporter is not None:
                    # The refusal's own payload travels with the report. "no group here" and
                    # "groups here, no member readable" share one sentence and need opposite
                    # repairs (KNOWN_GAPS G7).
                    reported: Sequence[Any] = (call.tool, step_ok,
                                               str(step_error or verdict), after or {})
                    try:
                        if _accepts_detail(reporter):
                            reporter(*reported, getattr(result, "data", None))
                        else:
                            reporter(*reported)
                    except Exception:  # noqa: BLE001 - policy history must not kill a run
                        pass

                note = ""
                dialog_decision = getattr(self.runtime.policy, "last_dialog_decision", None)
                if isinstance(dialog_decision, dict) and (before or {}).get("in_dialog"):
                    note = ("dialog_%s: %s" % (dialog_decision.get("status"),
                                               dialog_decision.get("reason")))
                if result.ok and call.tool in LISTING_TOOLS:
                    data = result.data if isinstance(result.data, dict) else {}
                    source = str(data.get("price_source") or "")
                    explicit = isinstance(call.args.get("price"), int)
                    if explicit or source:
                        # A listing is an intent, not income: record it as open and
                        # never as kamas earned. The price and where it came from
                        # travel with it, so a bad listing can be traced later.
                        self.open_listings.append({
                            "tool": call.tool,
                            "args": dict(call.args),
                            "price": data.get("price", call.args.get("price")),
                            "price_source": source or "explicit",
                            "at": time.time(),
                            "state": "open",
                        })
                        self._listing_total += 1
                        note = "listing_open: fill depends on a buyer, not on us"
                    else:
                        note = ("listing_unverified: the client did not report where the price "
                                "came from, so this is not counted as a listing")

                step = Step(
                    index=self._index,
                    goal=goal,
                    tool=call.tool,
                    args=dict(call.args),
                    ok=step_ok,
                    error=step_error,
                    # The action's own latency, not the action plus the observation
                    # that followed it. Recomputed here it silently included the
                    # post-step snapshot, so a 200 ms query printed as 6 s.
                    latency_ms=latency_ms,
                    verdict=verdict,
                    residual_s=v.residual_s,
                    drift=v.drift,
                    session=self.session,
                    source=v.source,
                    observation_cell=_cell_of(after),
                    observation_map=_map_of(after),
                    obs_before=_compact(before),
                    obs_after=_compact(after),
                    fight_estimate=fight_estimate,
                    fight_outcome=fight_outcome,
                    note=note,
                )
                self._emit(step)
                self._index += 1

                mark = "ok" if step_ok else "FAIL"
                # Say what came back, not just that it came back. Without this the
                # panel shows "inv.list ok advanced 165ms" forever and the user has
                # no way to tell a working query from a silent no-op.
                detail = _summarise(result)
                print(f"[autopilot] {self._index:3d} {call.tool:14s} {mark:4s} "
                      f"{verdict:8s} {step.latency_ms:6.0f}ms{detail}"
                      f"{'' if step_ok else '  ' + str(step_error)[:80]}")

                if self.on_step:
                    try:
                        self.on_step(step)
                    except Exception:  # noqa: BLE001 - a UI callback must not kill the run
                        pass

                # ---- agent 2 decides what happens next ----------------------
                # First credit the previous decision with this outcome, then ask
                # for a new one. Without the credit step the strategy table never
                # learns which recovery works, and the controller degrades into a
                # counter that happens to have opinions.
                self._credit_pending(call, v)

                decision = self.controller.on_step(v, call.tool, step_ok, step_error,
                                                   before, after,
                                                   detail=getattr(result, "data", None))
                self._pending = decision
                self.controller.save()
                self._emit_decision(decision, call, result, after,
                                    ok=step_ok, error=step_error)

                outcome = self._apply_decision(decision, goal, call)
                if outcome == "abort":
                    break

                if not result.ok:
                    # A missing client is not a failed step. Retrying it three times and
                    # then retiring a quest is what the log showed: the window had closed,
                    # and the loop went on filing failures against a quest that had done
                    # nothing wrong, then abandoned the goal with "goals exhausted".
                    reason = str(result.error or "")
                    if "no client instance" in reason or "not reachable" in reason:
                        print("[autopilot] the client is gone: " + reason
                              + "; waiting for it to return instead of ending the session")
                        if not self._recover_world("client window closed"):
                            ended_because = "the client window closed"
                            break
                        print("[autopilot] the client is back; resuming")
                        continue

                    failures += 1
                    if failures >= self.consecutive_failure_limit:
                        print(f"[autopilot] {failures} failures in a row on {goal!r}; "
                              f"abandoning this goal")
                        break
                else:
                    failures = 0

                if (after or {}).get("in_fight"):
                    self._recent.clear()
                    self._action_noops.clear()
                    self._stall_since = self._index
                    time.sleep(1.0)
                    continue

                # Stall detection on world progress. Repeating the same call is only
                # one shape of a livelock; the commoner one here is the controller
                # cycling retry -> recover -> continue forever, where no call repeats
                # three times yet nothing is ever completed. `objectives_completed` is
                # the honest measure of whether the session is getting anywhere.
                #
                # While a tutorial step runs, "no progress" is the script's state,
                # not the quest's: the scenario holds the world still until its own
                # target is tapped. Retiring the quest then blamed the only live
                # quest for the tutorial's gate.
                tutorial_active = _tutorial_active(after)
                prog = (after or {}).get("objectives_completed")
                if isinstance(prog, (int, float)):
                    if prog > self._best_progress:
                        self._best_progress = prog
                        self._stall_since = self._index
                    elif (not tutorial_active and self.max_steps <= 0
                          and self._index - self._stall_since >= self.stall_limit):
                        qid = self._retire_named_quest(call, before)
                        if qid is not None:
                            print(f"[autopilot] {self.stall_limit} steps without progress; "
                                  f"retiring quest {qid} and taking another")
                        self._stall_since = self._index
                        self._recent.clear()
                        self._action_noops.clear()

                # No-progress detection. A rule policy will happily pick the same
                # tool for the same goal forever; without this the loop burns the
                # whole budget repeating one scan. Three identical calls with no
                # world change means the goal is not reachable this way.
                key = f"{call.tool}|{json.dumps(call.args, sort_keys=True, ensure_ascii=False)}"
                # A standing farm order is the one action whose no-op is hidden
                # by its own recovery snapshot. Count every settle tool by call
                # signature across interleaved reads; a snapshot that changes
                # nothing must not reset a navigation/quest no-op streak.
                track_action = call.tool in SETTLE_TOOLS
                if verdict == "advanced":
                    self._recent.clear()
                    if track_action:
                        self._action_noops.pop(key, None)
                else:
                    if track_action:
                        self._action_noops[key] = self._action_noops.get(key, 0) + 1
                    self._recent.append(key)
                    del self._recent[:-32]
                    repeated = (len(self._recent) >= 3
                                and len(set(self._recent[-3:])) == 1)
                    streak = self._action_noops.get(key, 0) if track_action else 0
                    if repeated or streak >= 3:
                        if streak >= 3 and not repeated:
                            print(f"[autopilot] no progress: {call.tool} no-op 3x "
                                  "(read-only recoveries ignored)")
                        else:
                            print(f"[autopilot] no progress: {call.tool} repeated 3x "
                                  "with no change")
                        if tutorial_active:
                            # The plan is waiting on the scripted step (a dialog
                            # reply list that has not arrived, an NPC animation).
                            # Retiring the quest or abandoning the goal here turns
                            # the tutorial's timing into a lost session.
                            print("[autopilot] the tutorial is holding this step open;"
                                  " waiting instead of retiring the quest")
                            self._recent.clear()
                            self._action_noops.clear()
                            self._stall_since = self._index
                            time.sleep(1.0)
                            continue
                        if self.max_steps <= 0:
                            # Retire the quest and take another, instead of ending the
                            # session. An objective that never moves is usually one
                            # this build cannot do at all (an area, a craft, an NPC
                            # that is elsewhere); the next quest may be fine.
                            qid = self._retire_named_quest(call, before)
                            if qid is not None:
                                print(f"[autopilot] retiring quest {qid}; taking another")
                                self._recent.clear()
                                self._action_noops.clear()
                                continue
                        print(f"[autopilot] abandoning {goal!r}")
                        break

                # A goal that produced a real world change is done; repeating it
                # would just re-list the same item.
                #
                # A query counts as done after one successful call too: "open
                # inventory" asked to *see* the inventory, and it was seen. Without
                # this the loop re-read the same list for the whole step budget,
                # which is what "the AI does nothing" looked like from outside.
                # In Order mode ("open the inventory") a successful read IS the
                # goal, so it ends the run. In Auto mode the goal is a standing
                # instruction and a read is a recovery — breaking here meant one
                # `state.snapshot` after one noop ended the whole session, which is
                # exactly what "the AI does nothing" looked like.
                if verdict == "advanced" and (call.tool in LISTING_TOOLS
                                              or call.tool in READ_ONLY_TOOLS):
                    if self.max_steps > 0:
                        break

                # A floor on the step rate, because this loop can run unattended for
                # hours. With interval 0 (the panel's default) it would issue
                # directives as fast as the client answers, which is not how a person
                # plays and is a good way to get an account flagged. 0.35 s is still
                # far faster than a human and invisible to the game.
                time.sleep(self.interval if self.interval > 0 else 0.35)

            # end per-goal loop (bounded in Order mode, unbounded in Auto mode)
        return self.summary(ended_because)

    # -- teardown ------------------------------------------------------------

    def stop_client(self) -> None:
        """Leave the client in a safe state. Best effort, never raises."""
        if self.dry_run:
            return
        try:
            self.runtime.executor.execute("safe.stop", {}, instance=self.runtime.instance,
                                          source="live")
        except Exception:  # noqa: BLE001
            pass

    def _write_lock(self) -> None:
        """Mark that a loop owns the account.

        The idle trainer refuses to touch the CPU or the artifacts while this
        exists: training during a live run is how you get a half-updated model and
        a ban in the same afternoon.
        """
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            lock = self.log_path.parent / "autopilot.lock"
            lock.write_text(json.dumps({"session": self.session,
                                        "pid": os.getpid(),
                                        "started": time.time()}), encoding="utf-8")
            self._lock_path = lock
        except OSError:
            self._lock_path = None

    def _clear_lock(self) -> None:
        try:
            if getattr(self, "_lock_path", None) is not None:
                self._lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _warn_if_no_bridge(self) -> None:
        """Say it once, up front, instead of grinding 6 s per directive.

        Without this, starting the loop with no client open looks like a hang: each
        call waits for the HTTP timeout, the controller retries, and three failures
        per goal take ~18 s before anything is printed.
        """
        # A TCP connect is not enough: a force-killed host leaves a listening socket
        # that accepts connections and never answers, which makes every directive
        # wait for the full HTTP timeout. Ask for a response instead.
        from agent.bridge import bridge_port, reachable

        if reachable():
            return
        port = bridge_port()
        print("[autopilot] FATAL: no client bridge answering on 127.0.0.1:%d." % port)
        print("[autopilot]   The app publishes its port to "
              "LOCALAPPDATA\\DofusFarm\\bridge_port.txt; a stale file means the app "
              "is not running, or its bridge never bound.")
        print("[autopilot]   A port held by another process is the usual cause: the"
              " bridge logs '[AGENT] directive bridge listening on ...' when it binds.")
        print("[autopilot]   Refusing to run: every directive would fail and the run"
              " would look like it finished.")
        raise SystemExit(3)

    # Probe results that will not improve by asking again.
    BRAIN_FINAL_STATES = ("reused", "started", "no_interpreter")

    def _ensure_brain(self, reason: str = "startup") -> None:
        """Make the trained combat brain reachable, and record whether it was.

        The page asks ``127.0.0.1:8891/v1/combat/decide`` for every fight turn and, when
        nothing answers, marks the brain down and plays the turn with a hardcoded
        heuristic. Measured over the recorded corpus: 388 in-fight observations, every
        one of them heuristic, and one warm-up row for the learned policy. So a run
        cannot say "the learned policy fought for me" unless something started the
        server -- and cannot say it afterwards either, which is what this row is for.

        A brain that dies mid-run is a component failure like any other: the page keeps
        playing with its fallback, and this gets one bounded retry per fight start
        rather than none or infinitely many.

        ``preflight`` is the real-session switch: a run constructed without it is the
        regression gate's synthetic loop, which must not leave a detached server process
        behind on the developer's machine.
        """
        if self.brain_mode == "off" or not self.preflight:
            return
        if self._brain.get("state") in self.BRAIN_FINAL_STATES:
            return
        now = time.time()
        if now - self._brain_try_at < self.brain_retry_s:
            return
        self._brain_try_at = now
        self._brain_attempts += 1
        state, detail = ensure_brain(dry_run=self.dry_run)
        row: Dict[str, Any] = {"kind": "brain", "session": self.session,
                               "when": reason, "mode": self.brain_mode,
                               "state": state, "attempt": self._brain_attempts}
        for key in ("model", "pid", "cmd", "reason", "waited_s"):
            if detail.get(key) is not None:
                row[key] = detail[key]
        self._brain = row
        self._append_log_line(_fast_dumps(row))
        if state == "started":
            print(f"[autopilot] combat brain started (pid {row.get('pid')}): "
                  f"{row.get('model')}")
        elif state == "reused":
            print(f"[autopilot] combat brain already answering: {row.get('model')}")
        elif state == "dry_run":
            print("[autopilot] dry run: the combat brain was left untouched")
        else:
            print(f"[autopilot] no combat brain ({state}: {str(row.get('reason'))[:90]})"
                  " -- fights are played by the page's hardcoded fallback")

    def _require_world(self, fresh: bool = False) -> bool:
        """Wait until the character is in the game world, then start.

        The client stays *connected* at character selection. There is no map, no
        cell and no NPC, so every directive answers ok and changes nothing — the
        loop then burns its budget, decides the plan is stuck, and exits 0. That
        is indistinguishable from a broken agent, and it is the state this
        project spent a night debugging.

        Being at the login screen when the AI is switched on is a wait, not an
        end: the human signs in when they can, which can take minutes. There is
        no deadline here. The wait ends when a map appears, when the operator
        stops the run, or when the client stops answering — and the last two are
        reported as such instead of looking like a finished session.
        """
        if self.dry_run:
            return True
        waiting_since = time.time()
        last_report = waiting_since
        announced = False
        while True:
            reason = self._should_stop()
            if reason:
                print("[autopilot] stopping while waiting for the world: " + reason)
                return False
            # A recovery wait must not trust the snapshot from before the kick:
            # it can still say in_world=true for a session that is already gone.
            obs = self.observe(max_age_ms=-1.0 if fresh else OBS_FRESH_MS)
            if not self._check_identity(obs):
                print("[autopilot] stopping: " + str(self._should_stop()))
                return False
            pos = obs.get("position") if isinstance(obs, dict) else None
            map_id = pos.get("map_id") if isinstance(pos, dict) else None
            if isinstance(obs, dict) and obs and obs.get("in_world") is not False \
                    and map_id not in (None, 0):
                if announced:
                    print("[autopilot] the character is in the world; starting")
                return True
            # A stale snapshot with no bridge behind it is a closed client, not a
            # slow login; waiting there would park the run forever.
            if self._obs_source != "call" and not self._bridge_alive():
                print("[autopilot] the client stopped answering while waiting for "
                      "the world; stopping")
                return False
            now = time.time()
            if not announced:
                print("[autopilot] the character is not in the world yet (login "
                      "screen or loading); waiting until a map appears")
                announced = True
                last_report = now
            elif now - last_report >= self.world_report_s:
                print("[autopilot] still waiting for the world (%ds); log in and "
                      "stand on a map" % int(now - waiting_since))
                last_report = now
            time.sleep(self.world_poll_s)

    @staticmethod
    def _farm_allowed(goal: str, obs: Optional[Dict[str, Any]]) -> bool:
        """A quests prompt may farm only to gather a delivery item."""
        if _goal_mode(goal) != "quest":
            return True
        obj = (obs or {}).get("current_objective") or {}
        try:
            return int(obj.get("type_id")) in DELIVER_TYPES
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _bridge_alive() -> bool:
        try:
            from agent.bridge import reachable

            return bool(reachable())
        except Exception:  # noqa: BLE001 - a probe must not end a run
            return True

    def _wait_for_window(self) -> bool:
        """Wait until the host has a client window again.

        `_require_world` decides from a snapshot, and with no page there is no new
        snapshot — the runtime's carried one still says the character is standing on a
        map, so asking it "are we in the world?" answers yes and the wait would exit
        instantly, straight back into the same doomed directive. That is a hot loop, not
        a recovery. The host's own count is the only honest signal here, and it costs no
        game packet.

        Like the login wait there is no deadline: the operator opens a window when they
        can. It ends on a window, on the stop file, or on a host that stopped answering.
        """
        if self.dry_run:
            return True
        from agent.bridge import client_count

        started = time.time()
        last_report = started
        announced = False
        while True:
            if self._should_stop():
                return False
            count = client_count()
            if count is None and not self._bridge_alive():
                print("[autopilot] the host stopped answering; ending the run")
                return False
            if count:
                if announced:
                    print("[autopilot] %d client window(s) open again after %.1f s"
                          % (count, time.time() - started))
                return True
            now = time.time()
            if not announced:
                print("[autopilot] no client window is open; waiting for one "
                      "(open an instance in DofusFarm)")
                announced = True
            elif now - last_report >= 30.0:
                print("[autopilot] still waiting for a client window (%.0f s)"
                      % (now - started))
                last_report = now
            time.sleep(1.0)

    def _recover_world(self, reason: Optional[str] = None,
                       trigger: str = "fell_out") -> bool:
        """Wait for a kicked/reloaded client instead of ending the run.

        A kick, relogin or connection reset returns the page to the login screen;
        sending more directives into it only repeats the failure. The same wait
        used before the first step is correct here too, and after the world
        returns the queue of no-ops and a stale farm/fight wait must be cleared
        so the next decision starts from the new session.

        The wait is measured and logged, because "recovery success" is a baseline
        number rather than an assumption: how many times the session had to be
        rescued, how long each rescue took, and whether it came back at all.
        """
        lead = ("the client window is gone" if trigger == "client_absent"
                else "the client left the world mid-run")
        print("[autopilot] %s%s; waiting for it to come back"
              % (lead, f" (reason: {reason})" if reason else ""))
        started = time.time()
        self._prepared_obs = None
        # With no window the snapshot cannot answer anything, so the presence check
        # comes first; for a kick or a reload the page exists and answers, and only
        # the world is missing.
        present = self._wait_for_window() if trigger == "client_absent" else True
        recovered = present and self._require_world(fresh=True)
        waited = round(time.time() - started, 1)
        self._recoveries += 1
        if recovered:
            self._recoveries_ok += 1
            self._recovery_wait_s += waited
        self._append_log_line(_fast_dumps({
            "kind": "recovery",
            "session": self.session,
            "trigger": trigger,
            "reason": (str(reason or "")[:120] or None),
            "recovered": bool(recovered),
            "waited_s": waited,
            "at": time.time(),
        }) + b"\n")
        if not recovered:
            return False
        print("[autopilot] the world is back after %.1f s; resuming" % waited)
        self._recent.clear()
        self._action_noops.clear()
        self._stall_since = self._index
        self._farm_wait = False
        self._farm_wait_since = None
        self._farm_wait_report = 0.0
        self._fight_wait_since = None
        self._fight_wait_report = 0.0
        self._fight_arm_at = 0.0
        self._forced_call = None
        self._prepared_obs = None
        return True

    def _retire_named_quest(self, call: Any, before: Optional[Dict[str, Any]]) -> Optional[int]:
        """Retire the quest a stuck call was about, not just the first active one.

        A start loop on a quest that never becomes active (584) used to retire the
        only quest that *was* active (3841): the dead start stayed in the curriculum
        and was chosen again on the next plan pass, so the loop outlived the quest it
        had wrongly blamed.
        """
        qid = None
        try:
            raw = (getattr(call, "args", None) or {}).get("quest_id")
            if raw is not None:
                qid = int(raw)
        except (TypeError, ValueError):
            qid = None
        if qid is None:
            # The policy knows which quest its last plan was for. The active list
            # does not: it is ordered by the game, and taking its first entry
            # retired the tutorial quest (3841) because a start loop for a
            # different quest had stalled. Never guess from that list.
            try:
                raw = getattr(self.runtime.policy, "current_quest", None)
                if raw is not None:
                    qid = int(raw)
            except (TypeError, ValueError):
                qid = None
        skipper = getattr(self.runtime.policy, "skip", None)
        if skipper is None or qid is None:
            return None
        skipper(qid)
        return qid

    def _hold_wait(self, goal: str, before: Any) -> bool:
        """Spend time, not a step, on a wait the policy asked for.

        Returns True to keep the run going. The wait is still visible -- a `kind:
        "wait"` row with the reason and how many passes it has taken -- and it keeps
        the two things a dispatched wait had: the same cadence (so the planner's own
        wait counters, which are calibrated in passes, still mean the same wall-clock
        amount) and the same kick detection (a wait at the login screen is a recovery,
        not a spin).

        What it no longer does is produce a step row, a verdict, or a reward: those
        were what turned 3,010 lookups into apparent success.
        """
        self._waits += 1
        self._consecutive_waits += 1
        reason = self._forced_reason or "the policy had no action to take"
        self._forced_reason = ""
        thought = None
        if self._consecutive_waits == MAX_CONSECUTIVE_WAITS:
            # Reaching the bound is the strongest signal the loop has that this goal has
            # no action left in it -- the shape of the baseline's 1,581-pass run. Before
            # spending a doomed round trip on that, ask the layer that searches the
            # current board for an interaction nobody has tried in this scene. Only a
            # real action counts as an answer: a read back would reset the streak and
            # double the spin. When it has nothing left this falls through to the plain
            # overflow read, so the escalation cannot become its own loop.
            idle = READ_ONLY_TOOLS | LISTING_TOOLS
            for _ in range(ESCALATE_TRIES):
                candidate = self.thinking.propose(goal, before, self.steps[-8:], None)
                if candidate is None:
                    break
                if candidate.tool not in idle and candidate.tool != "-":
                    thought = candidate
                    break
        self._append_log_line(_fast_dumps({
            "kind": "wait",
            "session": self.session,
            "goal": goal,
            "reason": reason,
            "consecutive": self._consecutive_waits,
            "escalated": thought.tool if thought is not None else None,
            "in_dialog": bool((before or {}).get("in_dialog"))
            if isinstance(before, dict) else None,
            "at": time.time(),
        }) + b"\n")
        if self._consecutive_waits == 1 or self._consecutive_waits % 30 == 0:
            print("[autopilot] wait x%d: %s" % (self._consecutive_waits, reason))
        if thought is not None:
            self._consecutive_waits = 0
            self._forced_call = ToolCall(tool=thought.tool, args=dict(thought.args),
                                         goal=goal)
            self._forced_reason = thought.why
            print("[autopilot] the wait had no action left; trying %s %s (%s)"
                  % (thought.tool, thought.args, thought.why))
        if not self.dry_run:
            # A dry run has no world to wait for, and ten fast passes must not turn
            # into seconds of sleeping on the way to a `--steps 8` smoke test.
            time.sleep(max(WAIT_POLL_S, self.interval))
            self._last_step_at = time.time()
        # A wait can outlive the session it was waiting inside. Without this the loop
        # would keep asking the policy for an action on a page that is back at the
        # login screen, and the scheduler would never see the kick it is supposed to
        # recover from: no step is dispatched here, so the post-step check never runs.
        # A wait that keeps reading a dead bridge is covered by the bound instead --
        # the next dispatched step takes the transport path and its own recovery.
        fresh = self.observe(record=False)
        left = fell_out_of_world(before, fresh)
        if left is not None:
            recovered = self._recover_world(left or None, trigger="wait_fell_out")
            if recovered:
                # The count measured a wait inside a session that has ended; the new
                # world gets its own budget.
                self._consecutive_waits = 0
            return recovered
        return True

    def _record_dialog(self, call: Any, before: Any, after: Any,
                       result: Any = None) -> None:
        """Pair a dialog reply with the page it produced.

        The ranker's *primary* signal is quest-objective token overlap, and on the 1,547
        open dialogs in the recorded corpus it scored 0.0 on every single one: all 1,524
        choices came from the question tie-break or from a page with one reply. Nothing in
        any log says whether the reply it picked moved the conversation, so criterion (4)
        -- replace the heuristic where data supports it -- has no data to support it. The
        pairing exists for one step and then is thrown away; this writes it down.

        Only dialog actions produce a row (a reply index, an explicit close, or an
        accepted offer), so the cost is one row per talk attempt -- 110 in an entire
        baseline session-set.

        The dispatch fields matter more than they look. The recorded corpus replays one
        page 87 times against a single reply whose server id is 41457, and the wire log
        shows the *server* answering that id with `LeaveDialogMessage`: the conversation
        ends, the loop re-opens it, and `outcome` alone reads as "the ranker picked badly".
        `reply_id` (which id went on the wire), `via` (which object answered), `dispatch_ok`
        and `ended_by` are what separate "we ended it", "the server ended it" and "nothing
        was sent" -- and `objective_type` is the feature that says whether any reply could
        have validated the objective in the first place.
        """
        args = getattr(call, "args", None)
        if not isinstance(args, dict):
            return
        chose = args.get("reply")
        accepted = args.get("accept") is True
        if chose is None and not args.get("close") and not accepted:
            return
        if not isinstance(before, dict) or not isinstance(after, dict):
            return
        db = before.get("dialog")
        if not isinstance(db, dict) or not db.get("open"):
            return
        da = after.get("dialog")
        da = da if isinstance(da, dict) else {}
        if not da:
            outcome = "unknown"        # no dialog block came back: it says nothing
        elif not da.get("open"):
            outcome = "closed"
        elif da.get("message") == db.get("message"):
            outcome = "same page"
        else:
            outcome = "advanced"
        asked = "accept" if accepted else ("close" if chose is None else int(chose))
        # A dialog that is gone after a reply we did not ask to close was ended by the
        # game, not by us. Without this split, `closed` flatters the agent: it looks like
        # a completed exchange, and the loop re-opens the same page believing it moved.
        ended_by = None
        if outcome == "closed":
            ended_by = ("accept" if accepted
                        else ("asked" if asked == "close" else "after_reply"))
        data = getattr(result, "data", None)
        data = data if isinstance(data, dict) else {}
        self._dialogs += 1
        if outcome == "same page":
            self._dialog_stalls += 1
        kn = before.get("knowledge")
        kn = kn if isinstance(kn, dict) else {}
        tracker = before.get("current_objective")
        tracker = tracker if isinstance(tracker, dict) else {}
        # The ranker's own features, kept with the outcome: a row without the scores that
        # produced the choice is a log line, not a training pair.
        decision = getattr(getattr(self.runtime, "policy", None),
                           "last_dialog_decision", None)
        decision = decision if isinstance(decision, dict) else {}
        replies = db.get("replies")
        replies = replies if isinstance(replies, list) else []
        self._append_log_line(_fast_dumps({
            "kind": "dialog",
            "session": self.session,
            "index": self._index,
            "quest_id": args.get("quest_id"),
            "objective_id": kn.get("objective_id") or tracker.get("objective_id"),
            "objective_type": kn.get("type_id", tracker.get("type_id")),
            "npc_id": db.get("npc_id"),
            "name_id": db.get("name_id"),
            "page": str(db.get("message") or "")[:120],
            "replies": [str(r.get("text") or "")[:80]
                        for r in replies[:8] if isinstance(r, dict)],
            "asked": asked,
            "reply_text": str(args.get("reply_text") or "")[:80],
            "reply_id": data.get("reply_id"),
            "via": data.get("via"),
            "accepted_offer": bool(data.get("accepted_offer")),
            "dispatch_ok": bool(getattr(result, "ok", True)),
            "error": None if getattr(result, "error", None) is None
                     else str(result.error)[:120],
            "reason": decision.get("reason"),
            "scores": decision.get("scores")[:8] if isinstance(
                decision.get("scores"), list) else [],
            "outcome": outcome,
            "ended_by": ended_by,
            "page_after": str(da.get("message") or "")[:120] if da.get("open") else None,
            # Which read the outcome was measured against. The corpus reconstruction
            # (G23) found every multi-reply pair in the old logs labelled from an
            # observation read *before* the answer, which makes `same page` mean
            # nothing; a label is only as good as the state it was compared against.
            "after_source": getattr(self, "_obs_source", "unknown"),
            "at": time.time(),
        }) + b"\n")

    def _credit_pending(self, call: Any, verdict: Any) -> None:
        """Score the held repair decision -- but only against a step that could
        have moved the world.

        Every recovery hint this project issues is `state.snapshot`, and the verifier
        rightly scores a successful read as `advanced`: a lookup that returns data did
        what was asked. Feeding *that* back as the reward for the repair measures the
        probe instead of the repair. On the live baseline it produced 348 successes in
        348 attempts for `incomplete:recover` and 145 of 145 for `stale_state:recover`,
        while the same decisions credited against the next real action scored 28 of 348
        and 0 of 145. A table trained on the first number learns "always recover", which
        is not a recovery policy, it is a tautology.

        So a read leaves the decision pending; the credit lands on the first step that
        could have changed something, and a decision nothing answers within
        `PENDING_CREDIT_STEPS` reads is dropped rather than guessed at.
        """
        if self._pending is None:
            return
        if str(getattr(call, "tool", "") or "") in READ_ONLY_TOOLS:
            self._pending_age += 1
            if self._pending_age > PENDING_CREDIT_STEPS:
                self._pending = None
                self._pending_age = 0
            return
        self.controller.observe_outcome(self._pending, verdict)
        self._pending = None
        self._pending_age = 0

    def _apply_decision(self, decision: Decision, goal: str, call: ToolCall) -> str:
        """Turn agent 2's decision into what the actor does on the next step.

        Separate from the loop so it can be tested without a client: this is the
        join between "we noticed something" and "we changed what happens", and it
        is the thing that used to be missing entirely.
        """
        if decision.action == "retry":
            if call is not None:
                self._forced_call = call
                self._forced_reason = decision.reason
            return "retry"
        if decision.action == "recover" and decision.next_call:
            self._forced_call = ToolCall(tool=decision.next_call["tool"],
                                         args=dict(decision.next_call.get("args") or {}),
                                         goal=goal)
            self._forced_reason = decision.reason
            return "recover"
        if decision.action == "replan":
            self.replan_requested += 1
            print(f"[autopilot] REPLAN ({decision.cause}): {decision.reason}")
            thought = self.thinking.propose(
                goal, self._last_obs, self.steps[-8:], decision)
            if thought is not None:
                self._forced_call = ToolCall(
                    tool=thought.tool, args=dict(thought.args), goal=goal)
                # A thought that says "wait for the NPC" and a read that says nothing
                # are the same step to the loop but different waits to a reader. The
                # reason is the thought's own words, kept for the wait row.
                self._forced_reason = thought.why or decision.reason
                print(f"[autopilot] THINK ({decision.cause}): {thought.why} "
                      f"-> {thought.tool} {thought.args}")
                return "think"
            print("[autopilot] THINK: no untried alternative; falling back to the scheduler")
            if self.on_replan is not None:
                try:
                    self.on_replan(decision.reason, decision)
                except Exception:  # noqa: BLE001 - a host callback must not kill the run
                    pass
            return "replan"
        if decision.action == "abort":
            print(f"[autopilot] agent2 aborts {goal!r}: {decision.reason}")
            return "abort"
        return "continue"

    def summary(self, reason: str) -> Dict[str, Any]:
        self.controller.save(force=True)
        # The refusal buckets live on the policy. Reached through `getattr` all the way, because
        # this call is what ends a run: a missing attribute here would lose the whole summary,
        # and criterion 5 is measured from nothing else.
        policy = getattr(getattr(self, "runtime", None), "policy", None)
        data = {
            "reason": reason,
            "session": self.session,
            "steps": self._step_total,
            "verdicts": dict(self._verdict_counts),
            "label_sources": dict(self._source_counts),
            "replan_requested": self.replan_requested,
            "manual_interventions": self._intervention_total,
            "recoveries": self._recoveries,
            "recoveries_ok": self._recoveries_ok,
            "recovery_wait_s": round(self._recovery_wait_s, 1),
            # Waits are reported, not counted as work. Before this, 3,010 of them were
            # the majority of "steps" and nearly all of the run's apparent success.
            "waits": self._waits,
            "dialogs": self._dialogs,
            "dialog_stalls": self._dialog_stalls,
            # Which combat policy was reachable for this run. A fight count without
            # this cannot say whether the learned policy or the page's fallback
            # decided the turns -- which is the difference the whole combat goal rests on.
            "brain": {key: self._brain.get(key)
                      for key in ("state", "model", "mode", "reason", "when")},
            # G7: which kind of group refusal the page gave, counted. A run that travelled
            # because the named mob is on another map and a run whose client could not read the
            # members of the groups under its nose are different failures with different fixes,
            # and only these counts tell them apart afterwards.
            "group_refusals": dict(getattr(policy, "_group_refusals", {}) or {}),
            "group_read_broken": bool(getattr(policy, "_group_read_broken", False)),
            "controller": self.controller.stats(),
            "open_listings": self._listing_total,
            "log": str(self.log_path),
            "elapsed_s": (round(self._last_step_at - self._first_step_at, 1)
                          if self._step_total > 1 else 0.0),
        }
        print(f"[autopilot] done: {reason} | {self._verdict_counts} | "
              f"{self._listing_total} open listing(s)")
        return data


def _accepts_detail(fn: Any) -> bool:
    """Does this policy's outcome report take the refusal payload?

    The call sits inside a `except Exception: pass`, because policy history must never kill a
    run -- which is exactly why a wrong arity has to be checked rather than discovered: it would
    be swallowed, and the planner would stop learning from failures with nothing printed.
    """
    try:
        return "detail" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _summarise(result: Any) -> str:
    """A short, human-readable trace of what a tool returned.

    Best effort on purpose: a result shape we do not recognise must not break the
    print, because this line is the only window the user has into the loop.
    """
    try:
        data = getattr(result, "data", None)
        if data is None:
            return ""
        # A directive result is an envelope: {directive_payload, response, snapshot}.
        # Unwrap it, or the summary shows request bookkeeping instead of the answer.
        for _ in range(3):
            if not isinstance(data, dict):
                break
            inner = None
            for key in ("response", "result", "data", "value"):
                if key in data and data[key] is not None:
                    inner = data[key]
                    break
            if inner is None or inner is data:
                break
            data = inner
        if isinstance(data, dict):
            for key in ("items", "inventory", "quests", "listings", "results"):
                value = data.get(key)
                if isinstance(value, list):
                    return "  -> %d %s" % (len(value), key)
            bits = []
            for key in ("kamas", "cell", "map_id", "inventory_size", "count", "name"):
                if key in data and data[key] is not None:
                    bits.append("%s=%s" % (key, data[key]))
                if len(bits) >= 3:
                    break
            if bits:
                return "  -> " + ", ".join(bits)
            # Never stringify a whole bridge snapshot just to print 60 chars;
            # some of them are hundreds of KB.
            keys = [str(key) for key in list(data)[:6]]
            more = "" if len(data) <= 6 else ", +%d" % (len(data) - 6)
            return "  -> {%s%s}" % (", ".join(keys), more)
        if isinstance(data, list):
            return "  -> %d row(s)" % len(data)
        text = str(data)
        return "  -> " + (text[:60] + ("..." if len(text) > 60 else ""))
    except Exception:  # noqa: BLE001
        return ""


def _tutorial_active(obs: Optional[Dict[str, Any]]) -> bool:
    """Whether a scripted tutorial step is running on this observation."""
    if not isinstance(obs, dict):
        return False
    block = obs.get("tutorial")
    return isinstance(block, dict) and bool(block.get("active"))


def _compact(obs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Only the keys the verifier reads, so a step row stays small but trainable.

    The snapshot nests position under `position.map_id`; reading only the top
    level made every logged step say `observation_map: null` while the planner
    (which flattens) knew exactly where the character stood.
    """
    if not isinstance(obs, dict):
        return {}
    flat = flatten(obs)
    keys = ("cell", "map_id", "mapId", "kamas", "xp", "hp", "inventory_size",
            "in_dialog", "in_fight", "objectives_completed")
    out = {k: flat[k] for k in keys if k in flat}
    for key in ("current_objective", "ready_objectives", "dialog", "knowledge"):
        if flat.get(key) is not None:
            out[key] = flat[key]
    # The planner's view of the map's groups. Bounded: a full map can list
    # dozens, and a step row is not a snapshot.
    estimates = flat.get("fight_estimates")
    if isinstance(estimates, list) and estimates:
        out["fight_estimates"] = estimates[:8]
    tutorial = flat.get("tutorial")
    if isinstance(tutorial, dict) and tutorial.get("active"):
        out["tutorial"] = {"active": True, "quest_id": tutorial.get("quest_id"),
                           "step_id": tutorial.get("step_id")}
    return out


def _cell_of(obs: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(obs, dict):
        return None
    for key in ("cell", "cellId", "cell_id"):
        value = obs.get(key)
        if isinstance(value, int):
            return value
    pos = obs.get("position")
    if isinstance(pos, dict):
        cell = pos.get("cell")
        if isinstance(cell, int):
            return cell
    return None


def _map_of(obs: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(obs, dict):
        return None
    for key in ("map_id", "mapId"):
        value = obs.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    for block in (obs.get("position"), obs.get("map")):
        if isinstance(block, dict):
            value = block.get("map_id", block.get("mapId"))
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    value = obs.get("map")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


# -- CLI ---------------------------------------------------------------------


def _load_goals(args: argparse.Namespace) -> List[str]:
    goals: List[str] = list(args.goal or [])
    if args.goals_file:
        path = Path(args.goals_file)
        if not path.exists():
            raise SystemExit(f"goals file not found: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                goals.append(line)
    return goals


def _build_policy(args: argparse.Namespace):
    if args.policy == "rule":
        return RulePolicy()
    if args.policy == "planner":
        from agent.planner_nn import NNPlanner

        # The learned planner: same interface, tool choice comes from the student
        # model instead of an if-chain. Falls back to the deterministic planner when
        # no model is present, which is what makes it safe to try.
        planner = NNPlanner(level_hint=int(args.level_hint), store=None)
        print(f"[autopilot] policy: {planner.note}")
        return planner
    if args.policy == "encoder":
        from ai.encoder_serve import EncoderToolPolicy  # imported lazily: needs onnxruntime

        model_dir = Path(args.model_dir)
        onnx = model_dir / "encoder_int8.onnx"
        if not onnx.exists():
            raise SystemExit(f"no INT8 graph at {onnx}")
        return EncoderToolPolicy(
            onnx=onnx,
            tokenizer=model_dir / "tokenizer.json",
            policy_json=model_dir / "policy.json",
        )
    return RulePolicy()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent.autopilot",
        description="Run the agent loop on its own against a live instance.")
    parser.add_argument("--goal", action="append", help="goal text (repeatable)")
    parser.add_argument("--goals-file", help="one goal per line, # for comments")
    parser.add_argument("--steps", type=int, default=0, help="step budget (0 = run until the goals are done)")
    parser.add_argument("--minutes", type=float, default=0.0, help="wall-clock budget (0 = none)")
    parser.add_argument("--verifier-model", type=Path, default=None,
                        help="ONNX verifier head (optional; rules are used without it)")
    parser.add_argument("--veto-threshold", type=float, default=0.0,
                        help="pre-flight veto confidence (0 = never veto; needs a model)")
    parser.add_argument("--interval", type=float, default=0.0, help="seconds between steps (0 = as fast as the client answers)")
    parser.add_argument("--think", type=float, default=0.25,
                        help="dwell after a world-changing action before the next decision")
    parser.add_argument("--steps-per-goal", type=int, default=8)
    parser.add_argument("--policy", choices=("rule", "encoder", "planner"), default="rule")
    parser.add_argument("--level-hint", type=int, default=60,
                        help="character level, for the planner's curriculum")
    parser.add_argument("--model-dir", default=str(REPO_ROOT / "models" / "encoder30_v15"))
    parser.add_argument("--instance", default="default")
    parser.add_argument("--directive-url", default=None,
                        help="default: the port the app published to bridge_port.txt")
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--stop-file", default=str(DEFAULT_STOP))
    parser.add_argument("--explore", type=float, default=0.0,
                        help="fraction of repair decisions to take at random, so a "
                             "session produces data that can rank the alternatives. "
                             "Costs real steps; 0 by default")
    parser.add_argument("--dry-run", action="store_true",
                        help="choose and guard, but never send a directive")
    parser.add_argument("--no-fight-boards", dest="fight_boards",
                        action="store_false", default=True,
                        help="do not record fight board snapshots for training")
    parser.add_argument("--brain", choices=("auto", "off"), default="auto",
                        help="auto: start the combat brain (127.0.0.1:8891) if nothing "
                             "serves it, so fights use the learned policy; "
                             "off: leave the page's heuristic fallback in charge")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    goals = _load_goals(args)
    if not goals:
        raise SystemExit("nothing to do: pass --goal or --goals-file")

    runtime = AgentRuntime(
        policy=_build_policy(args),
        directive_url=args.directive_url,
        instance=args.instance,
    )
    # One store for facts learned by playing and facts the knowledge tools read:
    # a planner that discovers "npc 1280 is on map X" must write the same place
    # `knowledge.remember`/`knowledge.search` use, or the fact is invisible to
    # the next session.
    if hasattr(runtime.policy, "store") and runtime.policy.store is None:
        runtime.policy.store = runtime.executor.store
    pilot = Autopilot(
        runtime=runtime,
        goals=goals,
        log_path=Path(args.log),
        stop_file=Path(args.stop_file),
        max_steps=args.steps,
        verifier_model=args.verifier_model,
        veto_threshold=args.veto_threshold,
        minutes=args.minutes,
        interval=args.interval,
        think_s=args.think,
        steps_per_goal=args.steps_per_goal,
        dry_run=args.dry_run,
        explore=args.explore,
        fight_boards=args.fight_boards,
        brain=args.brain,
    )
    try:
        summary = pilot.run()
    except KeyboardInterrupt:
        summary = pilot.summary("interrupted")
    finally:
        pilot.stop_client()
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
