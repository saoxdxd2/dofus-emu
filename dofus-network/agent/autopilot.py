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
import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from agent.runtime import AgentRuntime, RulePolicy, ToolCall
from agent.observe import flatten
from agent.awareness import character_key
from agent.thinking import ThinkingLayer
from agent.knowledge_context import enrich_observation, load_default_store
from agent.controller import Controller, Decision
from agent.verifier import READ_ONLY_TOOLS, Verdict, Verifier, changed_keys
from agent.schema import TOOL_SCHEMA, validate_call
from agent.recorder import _redact
from combat.fight_estimate import FightAdvisor, FightOutcomeLog

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = REPO_ROOT / "trajectories" / "autopilot.jsonl"
DEFAULT_STOP = REPO_ROOT / "AUTOPILOT_STOP"

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
    ) -> None:
        self.runtime = runtime
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
        self._drift_streak = 0
        self.replan_requested = 0
        self.deadline = time.time() + minutes * 60.0 if minutes > 0 else 0.0
        self.interval = interval
        self.steps_per_goal = steps_per_goal
        self.consecutive_failure_limit = consecutive_failure_limit
        self.dry_run = dry_run
        self.on_step = on_step

        self.steps: List[Step] = []
        self.open_listings: List[Dict[str, Any]] = []
        self.knowledge = load_default_store()
        # Agent 3's read-only answers: one shared estimator behind both the guard
        # and the `fight.estimate` tool, so a step and its refusal cannot disagree.
        self.fights = FightAdvisor()
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
        # Board snapshots are the raw material for cell-level combat training:
        # the in-page bot plays, but the board and the eventual outcome are
        # still a labelled example for a plan ranker.
        self.fight_board_path = self.log_path.with_name("fight_boards.jsonl")
        self._fight_board_at = 0.0
        self._fight_board_key: Optional[str] = None
        executor = getattr(runtime, "executor", None)
        if executor is not None and getattr(executor, "fights", None) is None:
            executor.fights = self.fights
        self._last_obs: Optional[Dict[str, Any]] = None
        # Provenance of `_last_obs`, recorded with the step it justified. A
        # snapshot the loop reused must not be written down as if it arrived
        # with the call: that is the difference between a training pair and a
        # snapshot paired with an action taken much later.
        self._obs_age_ms: Optional[float] = None
        self._obs_source: str = "call"
        self._recent: List[str] = []
        # Guard refusals are decisions, not crashes. A refused call is reported to
        # the policy and the controller, and the loop takes another step; only the
        # same refusal three times in a row is a dead end for the goal.
        self._refusals: List[str] = []
        self._index = 0
        self._log_path().parent.mkdir(parents=True, exist_ok=True)

    # -- plumbing ------------------------------------------------------------

    def _log_path(self) -> Path:
        return self.log_path

    def _emit(self, step: Step) -> None:
        self.steps.append(step)
        try:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(step.as_dict(), ensure_ascii=False) + "\n")
        except OSError:
            pass  # a full disk must not stop the bot from stopping cleanly

    def _emit_decision(self, decision: Decision, call: ToolCall, result: Any,
                       obs: Optional[Dict[str, Any]]) -> None:
        """Log the repair decision next to the step that provoked it.

        A decision is not recoverable from the steps: a retry and a continue both end
        in a tool call, so the old log could only guess the action from the tool name.
        Guessing is how the first reality check ended up ranking strategies it had
        inferred rather than observed. One extra line per decision removes the guess
        and makes the log a bandit dataset (state, action, reward) instead of a
        transcript.
        """
        try:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "kind": "decision",
                    "session": self.session,
                    "goal": None,
                    "action": decision.action,
                    "cause": decision.cause,
                    "explored": bool(decision.explored),
                    "confidence": decision.confidence,
                    "tool": call.tool,
                    "ok": bool(getattr(result, "ok", False)),
                    "error": getattr(result, "error", None),
                    "obs": obs if isinstance(obs, dict) else {},
                    "at": time.time(),
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _should_stop(self) -> Optional[str]:
        """A pre-existing stop file is reported explicitly.

        Otherwise the loop exits with "done: stop file" on its first check and the
        run looks like it did nothing -- which is exactly what a leftover file from
        the previous session produces.
        """
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

    # -- observation ---------------------------------------------------------

    def _settle(self, before: Any, after: Any, step_s: float = 0.4) -> Any:
        """Re-observe until the world shows the action landed, or time runs out.

        A directive that starts a walk returns ok before the character has moved. Judging
        it in that same instant produces `noop`, the controller retries, and the agent
        looks broken while it is walking. This waits for the change the tool promised.

        Returns the first observation that differs from `before`, or the last one seen if
        nothing ever changed — so a genuinely inert call is still scored `noop`, just
        after the wait rather than before it.
        """
        if self.settle_s <= 0:
            return after
        if not isinstance(before, dict):
            # No baseline yet, which is the first step of every run.
            #
            # Returning here meant the very first action was always judged against nothing and
            # scored `noop` — and the live log shows exactly that: `quest.advance ok noop 103ms`,
            # a walk that had barely started. Take a baseline now and give the action its window
            # like any other step.
            before = after if isinstance(after, dict) else None
            if before is None:
                return after
        deadline = time.monotonic() + self.settle_s
        best = after
        while time.monotonic() < deadline:
            if isinstance(best, dict) and changed_keys(before, best):
                return best
            time.sleep(step_s)
            # A negative budget forces a real re-read: the settle window exists
            # to see the world *after* the action, and reusing the snapshot
            # adopted when the directive returned compared the before-state with
            # itself for the whole window. That is why every SETTLE_TOOL used to
            # score `noop` no matter how far the character walked.
            nxt = self.observe(max_age_ms=-1.0)
            if nxt:
                best = nxt
        return best

    def observe(self, max_age_ms: float = OBS_FRESH_MS) -> Optional[Dict[str, Any]]:
        """Return the freshest state we can get, and remember how fresh it is.

        The runtime holds the snapshot the bridge returned with the previous
        directive. That snapshot is the state *after* that action, so it is a
        legitimate decision input for the next one — but only while it is
        recent. Anything older, or of unknown age, is fetched again: deciding
        on an old snapshot is how the loop replays a plan against a world that
        has already moved on.
        """
        current, age_ms, source = self.runtime.current_observation()
        current = enrich_observation(current, self.knowledge)
        current = self._annotate_fights(current)
        if (isinstance(current, dict) and current and age_ms is not None
                and age_ms <= max_age_ms):
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
            snap = self._annotate_fights(enrich_observation(snap, self.knowledge))
            self._last_obs = snap
            self._obs_age_ms, self._obs_source = 0.0, "call"
            self.runtime.observe(snap, source="live")
            return snap
        if isinstance(current, dict) and current:
            # No independent read path (a test double, or a bridge that just
            # failed): use the runtime's copy, but say it is stale so the
            # recorder never presents it as a fresh pairing.
            self._last_obs = current
            self._obs_age_ms, self._obs_source = age_ms, "stale"
            return current
        self._obs_source = "stale"
        return self._last_obs

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
        if not self.fights.groups(obs):
            return obs
        self._refresh_character()
        return self.fights.annotate(obs)

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
            with self.fight_board_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_redact(record), ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"[autopilot] could not write fight board log: {exc}")

    def _record_fight_board(self, goal: Optional[str]) -> None:
        """Snapshot the live board once per distinct turn/HP/cell state.

        This is read-only and deliberately separate from the action trajectory:
        the in-page bot is playing, so the board is observation data for a
        learned plan ranker, not a record of an action this agent chose.
        """
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
        key = json.dumps({
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
        }, sort_keys=True)
        if key == self._fight_board_key:
            return
        self._fight_board_key = key
        self._append_fight_log({
            "kind": "fight_board",
            "run": self.session,
            "goal": goal,
            "board": board,
        })

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
        if now - self._fight_board_at >= 1.0:
            self._fight_board_at = now
            self._record_fight_board(goal)
        time.sleep(1.0)

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
                return self.summary("stopped while waiting for the world")

        for goal in self.goals:
            failures = 0
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

                before = self.observe()
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
                if self._forced_call is not None:
                    call = self._forced_call
                    self._forced_call = None
                    print(f"[autopilot] agent2 directs: {call.tool} {call.args}")
                else:
                    call = self.runtime.policy.choose(goal, before or {}, tools)
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
                    self._emit_decision(decision, call, None, before)
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
                    snap = enrich_observation(snap, self.knowledge)
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

                # A walk is not finished when the call returns. Give the tools that move
                # the character, open a dialog or start a fight a moment to land before
                # judging them — otherwise every one of them scores `noop` and the loop
                # retries an action that is already in flight.
                if step_ok and call.tool in SETTLE_TOOLS:
                    after = self._settle(before, after)

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
                    why = after.get("last_disconnect") or {}
                    reason = why.get("reason") if isinstance(why, dict) else None
                    # Two ways to know the session is gone: we watched the world
                    # disappear between two steps, or the client told us why it left.
                    # Either alone is enough; a bare `in_world: false` is not, because a
                    # map transition can briefly report no map and that must not end the
                    # run.
                    fell_out = after.get("in_world") is False and (
                        before.get("in_world") is not False or bool(reason))
                    if fell_out:
                        print("[autopilot] the client left the world mid-run"
                              + (f" (reason: {reason})" if reason else "")
                              + "; stopping instead of sending directives into a dead session")
                        print("[autopilot]   log in again, then start the loop.")
                        ended_because = ("the client left the world"
                                         + (f" ({reason})" if reason else ""))
                        break

                fight_estimate, fight_outcome = self._track_fight(before, after)

                v = self._verdict(result, before, after, call=call, latency_ms=latency_ms)
                verdict = v.verdict

                # Report the outcome back to the policy. `note_result` existed and was
                # never called, so the planner's attempt history was always empty — it
                # could not tell a tool that had just failed from one it had never
                # tried, and any history feature trained on that would be a fiction.
                reporter = getattr(self.runtime.policy, "note_result", None)
                if reporter is not None:
                    try:
                        reporter(call.tool, step_ok,
                                 str(step_error or verdict), after or {})
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
                if self._pending is not None:
                    self.controller.observe_outcome(self._pending, v)
                    self._pending = None

                decision = self.controller.on_step(v, call.tool, step_ok, step_error,
                                                   before, after)
                self._pending = decision
                self.controller.save()
                self._emit_decision(decision, call, result, after)

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
                        print("[autopilot] the client is gone: " + reason)
                        print("[autopilot]   stopping. Launch an instance, log in, then start"
                              " the loop again.")
                        ended_because = "the client window closed"
                        break

                    failures += 1
                    if failures >= self.consecutive_failure_limit:
                        print(f"[autopilot] {failures} failures in a row on {goal!r}; "
                              f"abandoning this goal")
                        break
                else:
                    failures = 0

                if (after or {}).get("in_fight"):
                    self._recent.clear()
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

                # No-progress detection. A rule policy will happily pick the same
                # tool for the same goal forever; without this the loop burns the
                # whole budget repeating one scan. Three identical calls with no
                # world change means the goal is not reachable this way.
                key = f"{call.tool}|{json.dumps(call.args, sort_keys=True, ensure_ascii=False)}"
                if verdict == "advanced":
                    self._recent.clear()
                else:
                    self._recent.append(key)
                    if len(self._recent) >= 3 and len(set(self._recent[-3:])) == 1:
                        print(f"[autopilot] no progress: {call.tool} repeated 3x with no change")
                        if tutorial_active:
                            # The plan is waiting on the scripted step (a dialog
                            # reply list that has not arrived, an NPC animation).
                            # Retiring the quest or abandoning the goal here turns
                            # the tutorial's timing into a lost session.
                            print("[autopilot] the tutorial is holding this step open;"
                                  " waiting instead of retiring the quest")
                            self._recent.clear()
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

    def _require_world(self) -> bool:
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
            obs = self.observe()
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
    def _bridge_alive() -> bool:
        try:
            from agent.bridge import reachable

            return bool(reachable())
        except Exception:  # noqa: BLE001 - a probe must not end a run
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

    def _apply_decision(self, decision: Decision, goal: str, call: ToolCall) -> str:
        """Turn agent 2's decision into what the actor does on the next step.

        Separate from the loop so it can be tested without a client: this is the
        join between "we noticed something" and "we changed what happens", and it
        is the thing that used to be missing entirely.
        """
        if decision.action == "retry":
            if call is not None:
                self._forced_call = call
            return "retry"
        if decision.action == "recover" and decision.next_call:
            self._forced_call = ToolCall(tool=decision.next_call["tool"],
                                         args=dict(decision.next_call.get("args") or {}),
                                         goal=goal)
            return "recover"
        if decision.action == "replan":
            self.replan_requested += 1
            print(f"[autopilot] REPLAN ({decision.cause}): {decision.reason}")
            thought = self.thinking.propose(
                goal, self._last_obs, self.steps[-8:], decision)
            if thought is not None:
                self._forced_call = ToolCall(
                    tool=thought.tool, args=dict(thought.args), goal=goal)
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
        counts: Dict[str, int] = {}
        for step in self.steps:
            counts[step.verdict] = counts.get(step.verdict, 0) + 1
        sources: Dict[str, int] = {}
        for step in self.steps:
            sources[step.source] = sources.get(step.source, 0) + 1
        data = {
            "reason": reason,
            "session": self.session,
            "steps": len(self.steps),
            "verdicts": counts,
            "label_sources": sources,
            "replan_requested": self.replan_requested,
            "controller": self.controller.stats(),
            "open_listings": len(self.open_listings),
            "log": str(self.log_path),
            "elapsed_s": round(self.steps[-1].at - self.steps[0].at, 1) if len(self.steps) > 1 else 0.0,
        }
        print(f"[autopilot] done: {reason} | {counts} | {len(self.open_listings)} open listing(s)")
        return data


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
        steps_per_goal=args.steps_per_goal,
        dry_run=args.dry_run,
        explore=args.explore,
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
