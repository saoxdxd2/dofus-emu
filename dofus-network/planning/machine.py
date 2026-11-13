"""The live loop as a typed, replayable state machine.

Why this module exists
----------------------
The orchestration that drives a client -- observe, choose, act, verify, recover --
lives inside two files of roughly five thousand lines each. The transition rules are
therefore spread across `if` chains, and the only way to find out what happens after
three refused border crossings is to run a live session and watch. That is not a test.

This module states the loop once, as data:

    observe --advanced--> choose --advanced--> act --advanced--> verify
       ^                     |                   |                  |
       +----- noop ----------+                   |                  |
                             +--- failed --> recover --retry--> recover
                                             |                     |
                                             +-- exhausted --> blocked

Every state carries an explicit timeout and a retry budget, so "the loop hung" becomes
"this state exceeded its budget", which is a thing a test can assert. ``CycleDetector``
watches the rolling ``(map_id, cell_id, action_hash)`` window and reports the signature
that repeats; a stuck state is then a concrete triple, not a feeling.

``replay_rows`` re-runs a recorded session (``live_policy_decisions.jsonl``, the SQLite
WAL store, or a shard) through the same table with a synthetic clock, so a stuck session
reproduces offline in milliseconds and can be pinned in a regression test.

    python -m planning.machine replay trajectories/live_policy_decisions.jsonl
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "Phase", "Outcome", "State", "Transition", "Machine", "DEFAULT_MACHINE",
    "CycleReport", "CycleDetector", "action_hash", "replay_rows", "replay_path",
    "ReplayResult", "main",
]


class Phase(str, Enum):
    OBSERVE = "observe"
    CHOOSE = "choose"
    ACT = "act"
    VERIFY = "verify"
    RECOVER = "recover"
    BLOCKED = "blocked"
    DONE = "done"


class Outcome(str, Enum):
    ADVANCED = "advanced"
    NOOP = "noop"
    FAILED = "failed"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"

    @classmethod
    def coerce(cls, value: Any) -> "Outcome":
        if isinstance(value, cls):
            return value
        text = str(value or "").strip().lower()
        if text in ("advanced", "advance", "ok", "true", "1"):
            return cls.ADVANCED
        if text in ("noop", "no-op", "unchanged", "same"):
            return cls.NOOP
        if text in ("failed", "failure", "error", "false", "0"):
            return cls.FAILED
        if text in ("blocked", "refused", "denied"):
            return cls.BLOCKED
        if text in ("timeout", "timed_out", "stalled"):
            return cls.TIMEOUT
        return cls.NOOP


@dataclass(frozen=True)
class State:
    """One node: what it means, how long it may take, and where each outcome goes."""

    name: str
    phase: Phase
    timeout_s: float
    retries: int
    on_advanced: str
    on_noop: str
    on_failed: str
    on_blocked: str
    exhausted: str = "blocked"
    terminal: bool = False

    def target_for(self, outcome: Outcome) -> str:
        if outcome is Outcome.ADVANCED:
            return self.on_advanced
        if outcome is Outcome.NOOP:
            return self.on_noop
        if outcome is Outcome.FAILED:
            return self.on_failed
        if outcome is Outcome.BLOCKED:
            return self.on_blocked
        return self.on_failed          # a timeout is a failure with a clock on it


def _default_states() -> List[State]:
    return [
        State("observe", Phase.OBSERVE, 5.0, 2, "choose", "observe", "recover", "blocked"),
        State("choose", Phase.CHOOSE, 3.0, 2, "act", "observe", "recover", "blocked"),
        State("act", Phase.ACT, 20.0, 1, "verify", "verify", "recover", "blocked"),
        State("verify", Phase.VERIFY, 8.0, 1, "observe", "observe", "recover", "blocked"),
        State("recover", Phase.RECOVER, 30.0, 3, "observe", "observe", "recover",
              "blocked", exhausted="blocked"),
        State("blocked", Phase.BLOCKED, 0.0, 0, "blocked", "blocked", "blocked",
              "blocked", terminal=True),
        State("done", Phase.DONE, 0.0, 0, "done", "done", "done", "done", terminal=True),
    ]


DEFAULT_MACHINE: Tuple[State, ...] = tuple(_default_states())


@dataclass
class Transition:
    tick: int
    at: float
    source: str
    target: str
    outcome: Outcome
    reason: str
    elapsed_s: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tick": self.tick, "at": round(self.at, 3), "source": self.source,
            "target": self.target, "outcome": self.outcome.value,
            "reason": self.reason, "elapsed_s": round(self.elapsed_s, 3),
        }


class Machine:
    """A deterministic transition table. No clock of its own, no I/O, no globals."""

    def __init__(self, states: Sequence[State] = DEFAULT_MACHINE, start: str = "observe",
                 max_ticks: int = 100_000) -> None:
        self.states = {state.name: state for state in states}
        if start not in self.states:
            raise ValueError("unknown start state %r" % (start,))
        self.start = start
        self.max_ticks = max_ticks
        self.reset()

    def reset(self) -> None:
        self._name = self.start
        self._entered_at = 0.0
        self._attempts = 0
        self._ticks = 0
        self.history: List[Transition] = []

    @property
    def state(self) -> State:
        return self.states[self._name]

    @property
    def name(self) -> str:
        return self._name

    @property
    def attempts(self) -> int:
        return self._attempts

    def tick(self, outcome: Any = Outcome.ADVANCED, now: float = 0.0,
             note: str = "") -> Transition:
        """Apply one observation to the table and move."""
        state = self.state
        elapsed = float(now) - self._entered_at
        effective = Outcome.coerce(outcome)
        reason = note
        if not state.terminal and elapsed > state.timeout_s:
            effective = Outcome.TIMEOUT
            reason = "state timeout %.1fs > %.1fs" % (elapsed, state.timeout_s)

        target = state.target_for(effective)
        if state.terminal:
            reason = reason or "terminal"
        elif target == state.name:
            self._attempts += 1
            if self._attempts > state.retries:
                target = state.exhausted
                reason = reason or "retry budget exhausted (%d)" % state.retries
        else:
            self._attempts = 0

        if target not in self.states:
            raise ValueError("state %r points at unknown %r" % (state.name, target))
        self._ticks += 1
        transition = Transition(self._ticks, float(now), state.name, target, effective,
                                reason, elapsed)
        self.history.append(transition)
        self._name = target
        self._entered_at = float(now)
        if len(self.history) > self.max_ticks:
            del self.history[:len(self.history) - self.max_ticks]
        return transition


# -- cycle detection --------------------------------------------------------

def action_hash(action: Any) -> str:
    """Stable 8-hex digest of an action, whatever shape the caller logged."""
    if isinstance(action, dict):
        material = json.dumps(action, sort_keys=True, separators=(",", ":"), default=str)
    else:
        material = str(action)
    return hashlib.sha1(material.encode("utf-8", "replace")).hexdigest()[:8]


@dataclass(frozen=True)
class CycleReport:
    stuck: bool
    signature: str
    repeats: int
    window: int
    map_id: Optional[int]
    cell_id: Optional[int]
    action: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "stuck": self.stuck, "signature": self.signature, "repeats": self.repeats,
            "window": self.window, "map_id": self.map_id, "cell_id": self.cell_id,
            "action": self.action,
        }


class CycleDetector:
    """Rolling window over ``(map_id, cell_id, action_hash)``.

    A repeat count, not a time threshold: the same action taken from the same cell on
    the same map three times in twelve ticks is a loop no matter how fast the ticks
    were, and a slow session that is genuinely progressing never repeats the tuple.
    """

    def __init__(self, window: int = 12, repeat_limit: int = 3) -> None:
        self.window = max(2, int(window))
        self.repeat_limit = max(2, int(repeat_limit))
        self._seen: Deque[str] = deque(maxlen=self.window)

    def reset(self) -> None:
        self._seen.clear()

    def observe(self, map_id: Any, cell_id: Any, action: Any) -> CycleReport:
        action_key = action_hash(action)
        map_key = _as_int(map_id)
        cell_key = _as_int(cell_id)
        signature = "%s:%s:%s" % (map_key, cell_key, action_key)
        self._seen.append(signature)
        repeats = sum(1 for item in self._seen if item == signature)
        return CycleReport(stuck=repeats >= self.repeat_limit, signature=signature,
                           repeats=repeats, window=self.window, map_id=map_key,
                           cell_id=cell_key, action=action_key)


def _as_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


# -- replay -----------------------------------------------------------------

@dataclass
class ReplayResult:
    path: str
    rows: int
    transitions: List[Transition] = field(default_factory=list)
    first_stuck_at: Optional[int] = None
    first_stuck: Optional[Dict[str, Any]] = None
    final_state: str = "observe"
    blocked: bool = False
    distinct_signatures: int = 0
    longest_repeat: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path, "rows": self.rows,
            "transitions": [t.as_dict() for t in self.transitions],
            "first_stuck_at": self.first_stuck_at,
            "first_stuck": self.first_stuck,
            "final_state": self.final_state,
            "blocked": self.blocked,
            "distinct_signatures": self.distinct_signatures,
            "longest_repeat": self.longest_repeat,
        }


def _row_position(row: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    obs = None
    for key in ("observation", "obs_after", "obs", "state", "world"):
        candidate = row.get(key)
        if isinstance(candidate, dict):
            obs = candidate
            break
    if obs is None and isinstance(row.get("decision"), dict):
        candidate = row["decision"].get("observation")
        obs = candidate if isinstance(candidate, dict) else None
    obs = obs or {}
    map_id = _first_int(obs, ("map_id", "mapId", "map", "current_map"))
    cell_id = _first_int(obs, ("cell_id", "cellId", "cell", "current_cell"))
    if map_id is None:
        map_id = _first_int(row, ("map_id", "mapId", "map"))
    if cell_id is None:
        cell_id = _first_int(row, ("cell_id", "cellId", "cell"))
    # Combat decision rows (`live_policy_decisions.jsonl`) carry no overworld
    # coordinates: the world they move through is (fight, round). Using those keeps
    # the detector meaningful on that log instead of collapsing every row into
    # `None:None:action`, where 225 identical signatures were returned for 400 rows.
    if map_id is None:
        map_id = _first_int(row, ("fight_id", "fightId", "encounter"))
    if cell_id is None:
        cell_id = _first_int(row, ("round", "turn"))
    return map_id, cell_id


def _first_int(source: Dict[str, Any], keys: Iterable[str]) -> Optional[int]:
    for key in keys:
        if key in source:
            value = _as_int(source.get(key))
            if value is not None:
                return value
    return None


def _row_action(row: Dict[str, Any]) -> Any:
    for key in ("tool", "action", "directive"):
        if row.get(key):
            return row[key]
    decision = row.get("decision")
    if isinstance(decision, dict):
        for key in ("tool", "action", "directive"):
            if decision.get(key):
                return decision[key]
        # Observation-only rows (server warm-up, checkpoint records) have no action.
        # Their identity *is* the board they recorded: `same: 7` in the row counts
        # identical boards, so hashing the board reproduces exactly that signal
        # instead of collapsing every such row to the empty-string digest.
        observation = decision.get("observation")
        if isinstance(observation, dict) and observation.get("board") is not None:
            return {"board": observation["board"]}
    args = row.get("args")
    return {"tool": "", "args": args} if isinstance(args, dict) else ""


def _row_outcome(row: Dict[str, Any]) -> Outcome:
    if "verdict" in row:
        return Outcome.coerce(row.get("verdict"))
    if "ok" in row:
        return Outcome.ADVANCED if row.get("ok") else Outcome.FAILED
    decision = row.get("decision")
    if isinstance(decision, dict) and "ok" in decision:
        return Outcome.ADVANCED if decision.get("ok") else Outcome.FAILED
    if row.get("error"):
        return Outcome.FAILED
    return Outcome.NOOP


def replay_rows(rows: Iterable[Dict[str, Any]], path: str = "<memory>",
                detector: Optional[CycleDetector] = None,
                machine: Optional[Machine] = None,
                stop_on_stuck: bool = False) -> ReplayResult:
    """Run recorded rows through the table. Deterministic: the tick index is the clock."""
    detector = detector or CycleDetector()
    machine = machine or Machine()
    result = ReplayResult(path=path, rows=0)
    signatures: Counter = Counter()

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        result.rows += 1
        map_id, cell_id = _row_position(row)
        action = _row_action(row)
        report = detector.observe(map_id, cell_id, action)
        signatures[report.signature] += 1
        result.longest_repeat = max(result.longest_repeat, signatures[report.signature])
        if report.stuck and result.first_stuck_at is None:
            result.first_stuck_at = index
            result.first_stuck = report.as_dict()
            if stop_on_stuck:
                result.transitions.append(
                    machine.tick(Outcome.FAILED, now=float(index), note="stuck:" + report.signature))
                break
        result.transitions.append(
            machine.tick(_row_outcome(row), now=float(index)))

    result.final_state = machine.name
    result.blocked = machine.name == "blocked"
    result.distinct_signatures = len(signatures)
    return result


def replay_path(path: Any, **kwargs: Any) -> ReplayResult:
    from storage.rows import iter_dicts

    target = Path(path)
    return replay_rows(iter_dicts(target), path=str(target), **kwargs)


# -- cli --------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="planning.machine",
                                     description="Replay a recorded session through the FSM.")
    parser.add_argument("path", nargs="?", help="jsonl, .db or .shard path")
    parser.add_argument("--json", action="store_true", help="full result as JSON")
    parser.add_argument("--window", type=int, default=12)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="rows to read (0 = all)")
    parser.add_argument("--fail-on-stuck", action="store_true",
                        help="exit 2 when a cycle is detected")
    raw = list(argv) if argv is not None else sys.argv[1:]
    # Accept both `... machine replay <path>` and `... machine <path>`: the verb reads
    # naturally, and a bare path is what a shell's tab-completion produces.
    if raw and raw[0] == "replay":
        raw = raw[1:]
    args = parser.parse_args(raw)
    if not args.path:
        parser.error("a trajectory path is required")

    from storage.rows import iter_dicts

    rows = iter_dicts(args.path, limit=args.limit or None)
    result = replay_path_rows(rows, args)
    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print("replay: %d rows, %d transitions, final=%s%s"
              % (result.rows, len(result.transitions), result.final_state,
                 " (blocked)" if result.blocked else ""))
        print("signatures: %d distinct, longest repeat %d"
              % (result.distinct_signatures, result.longest_repeat))
        if result.first_stuck_at is not None:
            stuck = result.first_stuck or {}
            print("first cycle at row %d: %s (x%d in %d ticks)"
                  % (result.first_stuck_at, stuck.get("signature"), stuck.get("repeats", 0),
                     stuck.get("window", 0)))
        else:
            print("cycles: none detected")
    if args.fail_on_stuck and result.first_stuck_at is not None:
        return 2
    return 0


def replay_path_rows(rows: Iterable[Dict[str, Any]], args: Any) -> ReplayResult:
    return replay_rows(rows, path=str(args.path),
                       detector=CycleDetector(window=args.window, repeat_limit=args.repeat))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
