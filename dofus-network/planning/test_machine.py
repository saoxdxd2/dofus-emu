"""FSM + cycle detector + replay determinism."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from planning.machine import (
    CycleDetector, Machine, Outcome, ReplayResult, replay_rows,
)


def test_state_timeout_is_explicit() -> None:
    machine = Machine()
    transition = machine.tick(Outcome.ADVANCED, now=1.0)
    assert (transition.source, transition.target) == ("observe", "choose")
    # observe's budget is 5 s: a tick 6 s later is a timeout, not an advance.
    transition = machine.tick(Outcome.ADVANCED, now=7.0)
    assert transition.outcome is Outcome.TIMEOUT
    assert transition.target == "recover"


def test_retry_budget_exhausts_into_blocked() -> None:
    machine = Machine(start="recover")
    targets = []
    for tick in range(6):
        targets.append(machine.tick(Outcome.FAILED, now=float(tick)).target)
    assert targets[:3] == ["recover", "recover", "recover"]
    assert targets[3] == "blocked", targets


def test_cycle_detector_reports_the_repeating_triple() -> None:
    detector = CycleDetector(window=8, repeat_limit=3)
    reports = [detector.observe(100139008, 311, {"tool": "nav.goto", "args": {"cell": 311}})
               for _ in range(3)]
    assert [report.stuck for report in reports] == [False, False, True]
    assert reports[-1].signature.endswith(reports[-1].action)
    other = detector.observe(100139008, 311, {"tool": "fight.join"})
    assert not other.stuck


def test_replay_is_deterministic_and_finds_the_stuck_row() -> None:
    rows = []
    for index in range(10):
        rows.append({"tool": "nav.goto", "ok": True, "verdict": "advanced",
                     "observation": {"map_id": 1, "cell_id": 100 + index}})
    for _ in range(4):
        rows.append({"tool": "nav.goto", "ok": True, "verdict": "noop",
                     "observation": {"map_id": 1, "cell_id": 200}})

    first = replay_rows(rows)
    second = replay_rows(rows)
    assert first.first_stuck_at == second.first_stuck_at == 12
    assert first.first_stuck is not None
    assert first.first_stuck["map_id"] == 1 and first.first_stuck["cell_id"] == 200
    assert [t.as_dict() for t in first.transitions] == [t.as_dict() for t in second.transitions]
    assert isinstance(first, ReplayResult)


def test_replay_tolerates_unknown_row_shapes() -> None:
    result = replay_rows([{"decision": {"board": {}}}, {"kind": "status"}, "not a row"])
    assert result.rows == 2
    assert result.first_stuck_at is None


def _main() -> int:
    failures = 0
    names = [name for name in sorted(globals()) if name.startswith("test_")]
    for name in names:
        function = globals()[name]
        try:
            function()
        except AssertionError as exc:
            failures += 1
            print("FAIL %s: %s" % (name, exc))
        else:
            print("ok   %s" % name)
    print("%d machine checks, %d failed" % (len(names), failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
