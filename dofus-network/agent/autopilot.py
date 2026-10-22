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
from agent.controller import Controller, Decision
from agent.verifier import Verdict, Verifier
from agent.schema import TOOL_SCHEMA, validate_call

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = REPO_ROOT / "trajectories" / "autopilot.jsonl"
DEFAULT_STOP = REPO_ROOT / "AUTOPILOT_STOP"

# Tools that spend money irreversibly. Kept as a name list rather than a flag on
# the schema so adding one is a deliberate, reviewable edit.
FORBIDDEN_TOOLS = {"hdv.buy"}

# Listing tools: allowed, but only with an explicit price the caller supplied.
LISTING_TOOLS = {"hdv.list", "inv.sell"}

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
        self.controller = Controller(session=self.session)
        self.controller.set_objective("; ".join(goals))
        self.controller.load()
        self.on_replan = None            # set by the host: (reason, decision) -> None
        self._forced_call: Optional[ToolCall] = None
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
        self._last_obs: Optional[Dict[str, Any]] = None
        self._recent: List[str] = []
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

    def _should_stop(self) -> Optional[str]:
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

    def observe(self) -> Optional[Dict[str, Any]]:
        """Return the freshest state we can get.

        Prefers what the runtime already holds (the C# bridge piggybacks a
        snapshot on every directive response, at age 0), and falls back to an
        explicit `state.snapshot` when there is nothing yet -- at start-up, and
        after a step that produced no snapshot.
        """
        current, age_ms, source = self.runtime.current_observation()
        if isinstance(current, dict) and current and (age_ms is None or age_ms < 4000):
            self._last_obs = current
            return current

        if self.dry_run:
            return self._last_obs

        result = self.runtime.executor.execute(
            "state.snapshot", {}, instance=self.runtime.instance, source="live")
        if result.ok and isinstance(result.data, dict):
            snap = result.data.get("snapshot") or result.data
            if isinstance(snap, dict) and snap:
                self._last_obs = snap
                self.runtime.observe(snap, source="live")
                return snap
        return self._last_obs

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
        return None

    # -- labelling -----------------------------------------------------------

    def _verdict(self, result, before: Optional[Dict[str, Any]],
                 after: Optional[Dict[str, Any]], call: Optional[ToolCall] = None,
                 latency_ms: float = 0.0) -> Verdict:
        """Ask the second agent. It returns verdict + residual + drift together,
        so the loop stops re-deriving them from different key lists."""
        return self.verifier.label(
            tool=(call.tool if call is not None else "-"),
            args=(dict(call.args) if call is not None else {}),
            ok=bool(result.ok),
            error=(None if result.ok else str(result.error)),
            before=before,
            after=after,
            latency_ms=latency_ms,
            session=self.session,
            index=self._index,
        )

    # -- the loop ------------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        tools = [t["name"] for t in TOOL_SCHEMA["tools"]]
        budget = "unlimited" if self.max_steps <= 0 else (str(self.max_steps) + " steps")
        print(f"[autopilot] {len(self.goals)} goal(s), {budget}"
              f"{' (dry run)' if self.dry_run else ''}")

        for goal in self.goals:
            failures = 0
            for _ in range(self.steps_per_goal):
                reason = self._should_stop()
                if reason:
                    print(f"[autopilot] stopping: {reason}")
                    return self.summary(reason)

                before = self.observe()
                if self._forced_call is not None:
                    call = self._forced_call
                    self._forced_call = None
                    print(f"[autopilot] agent2 directs: {call.tool} {call.args}")
                else:
                    call = self.runtime.policy.choose(goal, before or {}, tools)
                if call is None:
                    self._emit(Step(index=self._index, goal=goal, tool="-", args={},
                                    ok=False, error="policy returned no call",
                                    latency_ms=0.0, verdict="blocked",
                                    note="nothing in the policy matched this goal"))
                    print(f"[autopilot] no action for goal {goal!r}; moving on")
                    break

                refusal = self.guard(call)
                if refusal:
                    self._emit(Step(index=self._index, goal=goal, tool=call.tool,
                                    args=dict(call.args), ok=False, error=refusal,
                                    latency_ms=0.0, verdict="blocked", note=refusal))
                    print(f"[autopilot] {refusal}")
                    break

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
                    goal=goal, source="live", observation=before)
                after = self.observe()
                v = self._verdict(result, before, after, call=call,
                                  latency_ms=(time.perf_counter() - started) * 1000.0)
                verdict = v.verdict

                note = ""
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
                    ok=bool(result.ok),
                    error=None if result.ok else str(result.error),
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    verdict=verdict,
                    residual_s=v.residual_s,
                    drift=v.drift,
                    session=self.session,
                    source=v.source,
                    observation_cell=_cell_of(after),
                    observation_map=_map_of(after),
                    obs_before=_compact(before),
                    obs_after=_compact(after),
                    note=note,
                )
                self._emit(step)
                self._index += 1

                mark = "ok" if result.ok else "FAIL"
                print(f"[autopilot] {self._index:3d} {call.tool:14s} {mark:4s} "
                      f"{verdict:8s} {step.latency_ms:6.0f}ms"
                      f"{'' if result.ok else '  ' + str(result.error)[:80]}")

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

                decision = self.controller.on_step(v, call.tool, result.ok, result.error,
                                                   before, after)
                self._pending = decision
                self.controller.save()

                outcome = self._apply_decision(decision, goal, call)
                if outcome == "abort":
                    break

                if not result.ok:
                    failures += 1
                    if failures >= self.consecutive_failure_limit:
                        print(f"[autopilot] {failures} failures in a row on {goal!r}; "
                              f"abandoning this goal")
                        break
                else:
                    failures = 0

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
                        print(f"[autopilot] no progress: {call.tool} repeated 3x with no change; "
                              f"abandoning {goal!r}")
                        break

                # A goal that produced a real world change is done; repeating it
                # would just re-list the same item.
                if verdict == "advanced" and call.tool in LISTING_TOOLS:
                    break

                if self.interval:
                    time.sleep(self.interval)

            # end steps_per_goal
        return self.summary("goals exhausted")

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

    def _apply_decision(self, decision: Decision, goal: str, call: ToolCall) -> str:
        """Turn agent 2's decision into what the actor does on the next step.

        Separate from the loop so it can be tested without a client: this is the
        join between "we noticed something" and "we changed what happens", and it
        is the thing that used to be missing entirely.
        """
        if decision.action == "retry":
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


def _compact(obs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Only the keys the verifier reads, so a step row stays small but trainable."""
    if not isinstance(obs, dict):
        return {}
    keys = ("cell", "map_id", "mapId", "kamas", "xp", "hp", "inventory_size")
    return {k: obs[k] for k in keys if k in obs}


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
    for key in ("map_id", "mapId", "map"):
        value = obs.get(key)
        if isinstance(value, int):
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
    parser.add_argument("--policy", choices=("rule", "encoder"), default="rule")
    parser.add_argument("--model-dir", default=str(REPO_ROOT / "models" / "encoder30_v15"))
    parser.add_argument("--instance", default="default")
    parser.add_argument("--directive-url", default="http://127.0.0.1:8893/directive")
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--stop-file", default=str(DEFAULT_STOP))
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
