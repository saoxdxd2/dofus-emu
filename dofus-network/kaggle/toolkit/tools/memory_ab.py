"""Compare the two memory arms on the sessions that actually ran.

The question this answers: does dispatching a remembered call, when the policy has nothing
to say, beat the loop's own repair? That cannot be settled offline -- the holdout showed
the remembered call agrees with what the agent does only 1.2% of the time, which says the
memory is not a policy but says nothing about whether it is better than silence.

So the arms are:

  record+replay  the default: the store records, and answers when the policy is silent
  record-only    the store still records (nothing is lost), but never dispatches

Both arms are labelled by the run itself (`session_end.memory_arm`), so a log is
self-describing and this tool never has to guess which file was which.

Reported per arm: steps, advance rate, quest objectives gained, and for the replay arm
the advance rate of the replayed steps specifically -- that last number is the one that
matters, because it is the only population where the memory changed the decision.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ADVANCED = "advanced"


def default_sources() -> List[str]:
    paths = sorted(glob.glob(str(REPO_ROOT / "trajectories" / "*.jsonl")))
    state = os.environ.get("LOCALAPPDATA")
    if state:
        paths.extend(sorted(glob.glob(os.path.join(state, "DofusFarm", "autopilot*.jsonl"))))
    return paths


def rows(path: str) -> Iterator[Dict[str, Any]]:
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line or line[0] != "{":
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def wilson(successes: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    """95% interval for a proportion. Small samples here are the norm, not the exception."""
    if total <= 0:
        return (0.0, 1.0)
    p = successes / total
    denom = 1.0 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    spread = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / denom
    return (max(0.0, centre - spread), min(1.0, centre + spread))


class Arm:
    def __init__(self, name: str) -> None:
        self.name = name
        self.sessions = 0
        self.steps = 0
        self.advanced = 0
        self.replays = 0
        self.replay_advanced = 0
        self.objectives = 0
        self.elapsed = 0.0

    def rate(self) -> float:
        return self.advanced / self.steps if self.steps else 0.0


def summarise(path: str, arms: Dict[str, Arm]) -> None:
    session_arm: Dict[str, str] = {}
    pending: Dict[Tuple[str, Any], str] = {}
    objective_marks: Dict[str, List[int]] = defaultdict(list)
    step_verdicts: Dict[Tuple[str, Any], str] = {}

    for row in rows(path):
        kind = row.get("kind")
        session = str(row.get("session") or "")
        if kind == "session_end":
            arm = str(row.get("memory_arm") or "unlabelled")
            session_arm[session] = arm
            bucket = arms[arm]
            bucket.sessions += 1
            bucket.elapsed += float(row.get("elapsed_s") or 0.0)
            continue
        if kind == "memory_replay":
            pending[(session, row.get("index"))] = str(row.get("tool") or "")
            continue
        if kind is not None:
            continue
        # A decided step. The arm is known from the session row, which is written at the
        # end of the same file, so the aggregation happens after the read.
        index = row.get("index")
        verdict = str(row.get("verdict") or "")
        step_verdicts[(session, index)] = verdict
        before = row.get("obs_before")
        if isinstance(before, dict):
            done = before.get("objectives_completed")
            if isinstance(done, int):
                objective_marks[session].append(done)

    for (session, index), tool in pending.items():
        arm = arms[session_arm.get(session, "unlabelled")]
        arm.replays += 1
        if step_verdicts.get((session, index)) == ADVANCED:
            arm.replay_advanced += 1

    for (session, index), verdict in step_verdicts.items():
        arm = arms[session_arm.get(session, "unlabelled")]
        arm.steps += 1
        if verdict == ADVANCED:
            arm.advanced += 1

    for session, marks in objective_marks.items():
        if not marks:
            continue
        arms[session_arm.get(session, "unlabelled")].objectives += max(0, max(marks) - min(marks))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", action="append", default=None,
                        help="a step log to read; repeatable (default: all found)")
    parser.add_argument("--min-steps", type=int, default=1,
                        help="ignore arms with fewer decided steps than this")
    args = parser.parse_args(list(argv) if argv is not None else None)

    paths = args.source or default_sources()
    arms: Dict[str, Arm] = defaultdict(lambda: Arm(""))
    for path in paths:
        summarise(path, arms)
    for name, arm in arms.items():
        arm.name = name

    order = ["record+replay", "record-only", "off", "unlabelled"]
    shown = [arms[name] for name in order if name in arms]
    shown += [a for n, a in arms.items() if n not in order]
    shown = [a for a in shown if a.steps >= args.min_steps]
    if not shown:
        print("no labelled sessions found; run with and without --no-memory-replay first")
        return 1

    print("%-15s %8s %8s %10s %9s %8s %11s" % (
        "arm", "sessions", "steps", "advance%", "95% CI", "quests", "replays"))
    for arm in shown:
        lo, hi = wilson(arm.advanced, arm.steps)
        print("%-15s %8d %8d %9.1f%% %9s %8d %11s" % (
            arm.name, arm.sessions, arm.steps, 100 * arm.rate(),
            "[%.1f,%.1f]" % (100 * lo, 100 * hi), arm.objectives,
            "%d (%d ok)" % (arm.replays, arm.replay_advanced) if arm.replays else "-"))

    base = arms.get("record-only")
    replay = arms.get("record+replay")
    if base and replay and base.steps and replay.steps:
        diff = replay.rate() - base.rate()
        lo1, hi1 = wilson(replay.advanced, replay.steps)
        lo2, hi2 = wilson(base.advanced, base.steps)
        # Two independent proportions: the interval on the difference is the sum in
        # quadrature of the two standard errors, not the difference of the intervals.
        se = ((hi1 - lo1) / 3.92) ** 2 + ((hi2 - lo2) / 3.92) ** 2
        se = se ** 0.5
        print("\nadvance-rate difference (replay - record-only): %+.2f pp, 95%% CI [%+.2f, %+.2f]"
              % (100 * diff, 100 * (diff - 1.96 * se), 100 * (diff + 1.96 * se)))
        if diff - 1.96 * se > 0:
            print("replay is ahead of the control arm at this sample size")
        elif diff + 1.96 * se < 0:
            print("replay is behind the control arm: the memory is costing progress")
        else:
            print("not separable at this sample size; the interval still contains zero")
    if replay and replay.replays:
        lo, hi = wilson(replay.replay_advanced, replay.replays)
        print("replayed steps advanced %d/%d = %.1f%% (95%% CI [%.1f, %.1f])"
              % (replay.replay_advanced, replay.replays,
                 100 * replay.replay_advanced / replay.replays, 100 * lo, 100 * hi))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
