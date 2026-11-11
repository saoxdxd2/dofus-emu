"""The supervisor's one promise: a failure never ends the run.

Everything here is about what happens when things go wrong, because that is the only
part of a supervisor that can be wrong. A loop that keeps going while everything is
healthy is a `while True`; the work is in what it does when the client is gone, the
brain is dead, the repair itself throws, and the tick that runs the repairs throws.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.forever import (BACKOFF_SECONDS, Component, Supervisor,
                           build_supervisor)  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _supervisor(components, clock, stop=lambda: False, **kwargs):
    return Supervisor(components=components, stop_requested=stop, clock=clock,
                      sleep=clock.advance, log=lambda line: None, **kwargs)


class NeverStopsTests(unittest.TestCase):
    def test_a_component_that_always_fails_does_not_end_the_run(self):
        clock = FakeClock()
        always_down = Component("client", lambda: (False, "gone"), lambda: "tried")
        supervisor = _supervisor([always_down], clock)
        result = supervisor.run(max_ticks=25)
        self.assertEqual(result["reason"], "max_ticks")
        self.assertEqual(supervisor.ticks, 25)
        self.assertGreaterEqual(always_down.failures, 1)

    def test_a_check_that_raises_does_not_end_the_run(self):
        clock = FakeClock()

        def explode():
            raise RuntimeError("the bridge is gone")

        supervisor = _supervisor([Component("brain", explode, lambda: "tried")], clock)
        result = supervisor.run(max_ticks=6)
        self.assertEqual(result["reason"], "max_ticks")
        self.assertIn("the bridge is gone", supervisor.components[0].last_detail)

    def test_a_repair_that_raises_does_not_end_the_run(self):
        clock = FakeClock()

        def bad_repair():
            raise OSError("no such executable")

        component = Component("tunnel", lambda: (False, "down"), bad_repair)
        supervisor = _supervisor([component], clock)
        result = supervisor.run(max_ticks=8)
        self.assertEqual(result["reason"], "max_ticks")
        self.assertEqual(component.repairs, 0)

    def test_a_tick_that_raises_does_not_end_the_run(self):
        clock = FakeClock()
        component = Component("farming", lambda: (False, "stuck"), lambda: "tried")
        supervisor = _supervisor([component], clock)
        original = supervisor.tick
        calls = {"n": 0}

        def flaky_tick():
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("bug in the supervisor itself")
            return original()

        supervisor.tick = flaky_tick
        result = supervisor.run(max_ticks=5)
        self.assertEqual(result["reason"], "max_ticks")
        self.assertEqual(calls["n"], 5)

    def test_one_wedged_component_does_not_block_the_others(self):
        clock = FakeClock()
        stuck = Component("tunnel", lambda: (False, "down"), lambda: "tried")
        healthy = Component("client", lambda: (True, "fine"), lambda: "unused")
        supervisor = _supervisor([stuck, healthy], clock)
        report = supervisor.tick()
        by_name = {c["name"]: c for c in report["components"]}
        self.assertTrue(by_name["client"]["healthy"])
        self.assertEqual(by_name["tunnel"]["action"], "repaired")


class StopTests(unittest.TestCase):
    def test_the_default_stop_predicate_is_a_predicate(self):
        # It used to be handed `stop_path` -- a function returning a Path, which is
        # always truthy -- so the real supervisor ended on its very first check while
        # every test passed, because every test injected its own predicate.
        from tools.forever import stop_requested
        self.assertIsInstance(stop_requested(), bool)

    def test_it_ends_when_the_operator_asks(self):
        clock = FakeClock()
        flag = {"stop": False}
        component = Component("client", lambda: (True, "fine"), lambda: "unused")
        supervisor = _supervisor([component], clock, stop=lambda: flag["stop"])
        original_tick = supervisor.tick
        seen = {"n": 0}

        def stop_after_three():
            seen["n"] += 1
            if seen["n"] == 3:
                flag["stop"] = True
            return original_tick()

        supervisor.tick = stop_after_three
        result = supervisor.run()
        self.assertTrue(result["stopped"])
        self.assertEqual(result["reason"], "stop requested")

    def test_it_does_not_stop_for_any_other_reason(self):
        clock = FakeClock()
        # Every component down, forever, and no stop request: the run must not end.
        components = [Component("a", lambda: (False, "x"), lambda: "t"),
                      Component("b", lambda: (False, "y"), lambda: "t")]
        supervisor = _supervisor(components, clock)
        result = supervisor.run(max_ticks=40)
        self.assertFalse(result["stopped"])


class BackoffTests(unittest.TestCase):
    def test_backoff_grows_and_then_caps(self):
        component = Component("client", lambda: (False, "x"), lambda: "t")
        waits = []
        for failures in range(0, len(BACKOFF_SECONDS) + 4):
            component.failures = failures
            waits.append(component.backoff())
        self.assertEqual(waits[0], 0.0)
        self.assertEqual(waits, sorted(waits))
        self.assertEqual(waits[-1], BACKOFF_SECONDS[-1])

    def test_it_waits_before_retrying_the_same_component(self):
        clock = FakeClock()
        component = Component("client", lambda: (False, "gone"), lambda: "tried")
        supervisor = _supervisor([component], clock)
        supervisor.tick()                      # first attempt, no wait
        self.assertEqual(component.repairs, 1)
        supervisor.tick()                      # still inside the backoff window
        self.assertEqual(component.repairs, 1)
        clock.advance(BACKOFF_SECONDS[-1])
        supervisor.tick()                      # window elapsed, try again
        self.assertEqual(component.repairs, 2)

    def test_a_recovery_clears_the_failure_count(self):
        clock = FakeClock()
        state = {"up": False}
        component = Component("client", lambda: (state["up"], "detail"), lambda: "t")
        supervisor = _supervisor([component], clock)
        supervisor.tick()
        supervisor.tick()
        self.assertGreaterEqual(component.failures, 1)
        state["up"] = True
        supervisor.tick()
        self.assertEqual(component.failures, 0)


class FarmingCheckTests(unittest.TestCase):
    """`auto_fight` is not progress, and a stuck character must be caught."""

    def _check(self, board, clock, idle=240.0):
        from tools.forever import FarmingCheck
        return FarmingCheck(idle_seconds=idle, clock=clock,
                            snapshot=lambda: dict(board))

    def _board(self, **over):
        board = {"in_world": True, "in_fight": False, "map_id": 146538496,
                 "cell": 176, "auto_fight": True,
                 "farm_session": {"fights": 3, "wins": 0, "losses": 3},
                 "position": {"map_id": 146538496, "cell": 176}}
        board.update(over)
        return board

    def test_auto_fight_alone_is_not_health(self):
        # The measured false positive: auto_fight true, nothing happening.
        clock = FakeClock()
        check = self._check(self._board(), clock, idle=60.0)
        check()                              # first look only starts the clock
        clock.advance(120.0)
        healthy, detail = check()
        self.assertFalse(healthy)
        self.assertIn("idle", detail)

    def test_a_fight_in_progress_is_health(self):
        clock = FakeClock()
        check = self._check(self._board(in_fight=True), clock, idle=60.0)
        healthy, detail = check()
        self.assertTrue(healthy)
        self.assertIn("fight", detail)

    def test_moving_resets_the_idle_clock(self):
        clock = FakeClock()
        board = self._board()
        check = self._check(board, clock, idle=60.0)
        check()
        clock.advance(30.0)
        board["cell"] = 200                  # walked one cell
        self.assertTrue(check()[0])
        clock.advance(30.0)
        # 60s since the move: right at the allowance, still counted as healthy.
        self.assertTrue(check()[0])
        clock.advance(30.0)
        self.assertFalse(check()[0])

    def test_a_new_fight_resets_the_idle_clock(self):
        clock = FakeClock()
        board = self._board()
        check = self._check(board, clock, idle=60.0)
        check()
        clock.advance(90.0)
        self.assertFalse(check()[0])
        board["farm_session"] = {"fights": 4, "wins": 0, "losses": 4}
        self.assertTrue(check()[0])

    def test_leaving_the_world_is_unhealthy(self):
        clock = FakeClock()
        check = self._check(self._board(in_world=False), clock)
        healthy, detail = check()
        self.assertFalse(healthy)
        self.assertIn("world", detail)

    def test_no_snapshot_is_unhealthy_rather_than_a_crash(self):
        from tools.forever import FarmingCheck

        def boom():
            raise RuntimeError("bridge down")

        healthy, detail = FarmingCheck(snapshot=boom)()
        self.assertFalse(healthy)
        self.assertIn("bridge down", detail)


class DryRunTests(unittest.TestCase):
    def test_dry_run_repairs_nothing(self):
        clock = FakeClock()
        component = Component("client", lambda: (False, "gone"), lambda: "tried")
        supervisor = _supervisor([component], clock, dry_run=True)
        report = supervisor.tick()
        self.assertEqual(component.repairs, 0)
        self.assertEqual(report["components"][0]["action"], "would-repair")


class RealComponentTests(unittest.TestCase):
    def test_the_real_set_covers_the_whole_stack(self):
        names = [c.name for c in build_supervisor().components]
        self.assertEqual(names, ["tunnel", "client", "brain", "farming"])

    def test_every_real_check_returns_a_reason_rather_than_raising(self):
        # Run outside the game: these must all answer "unhealthy, because ..." rather
        # than throwing, or the first tick of a cold start would end the run.
        for component in build_supervisor().components:
            healthy, detail = component.check()
            self.assertIsInstance(healthy, bool)
            self.assertIsInstance(detail, str)
            self.assertTrue(detail)


class RemedyChoiceTests(unittest.TestCase):
    """The supervisor may only choose among repairs the component already has.

    `agent.problem_nn` is a hint, not a dependency: with no advisor, no model or no
    opinion, the fixed repair runs exactly as it did before this existed. These
    tests pin both halves -- that the hint is followed when it exists, and that
    nothing about the old path changed when it does not.
    """

    class Recorder:
        """A duck-typed advisor: records what it was asked and what it answered."""

        def __init__(self, pick=None, raise_on_choose=False):
            self.pick = pick
            self.raise_on_choose = raise_on_choose
            self.asked = []
            self.learned = []
            self.labelled = []

        def choose(self, situation, offered):
            self.asked.append((dict(situation), list(offered)))
            if self.raise_on_choose:
                raise RuntimeError("model is a brick")
            return self.pick

        def learn(self, situation, remedy):
            self.learned.append((dict(situation), remedy))

        def label(self, component, outcome):
            self.labelled.append((component, outcome))
            return None

    def _component(self, ran, healthy_after=None):
        state = {"healthy": False}

        def check():
            return state["healthy"], "idle" if not state["healthy"] else "in a fight"

        def repair():
            ran.append("repair")
            if healthy_after is not None:
                state["healthy"] = healthy_after
            return "repaired"

        def travel():
            ran.append("travel")
            if healthy_after is not None:
                state["healthy"] = healthy_after
            return "travelled"

        def wait():
            ran.append("wait")
            return "waited"

        return Component("farming", check, repair,
                         remedies={"engage": repair, "travel": travel,
                                   "wait": wait},
                         default_remedy="engage")

    def test_without_an_advisor_the_fixed_repair_runs(self):
        clock = FakeClock()
        ran = []
        supervisor = _supervisor([self._component(ran)], clock)
        supervisor.tick()
        self.assertEqual(ran, ["repair"])

    def test_the_advisors_pick_is_what_runs(self):
        clock = FakeClock()
        ran = []
        advisor = self.Recorder(pick="travel")
        supervisor = _supervisor([self._component(ran)], clock, advisor=advisor)
        report = supervisor.tick()
        self.assertEqual(ran, ["travel"])
        self.assertEqual(report["components"][0]["remedy"], "travel")
        self.assertEqual(advisor.asked[0][1], ["engage", "travel", "wait"])

    def test_a_remedy_the_component_cannot_perform_is_ignored(self):
        clock = FakeClock()
        ran = []
        advisor = self.Recorder(pick="restart")
        supervisor = _supervisor([self._component(ran)], clock, advisor=advisor)
        supervisor.tick()
        self.assertEqual(ran, ["repair"])

    def test_an_advisor_that_raises_leaves_the_fixed_repair_in_place(self):
        clock = FakeClock()
        ran = []
        advisor = self.Recorder(pick="travel", raise_on_choose=True)
        supervisor = _supervisor([self._component(ran)], clock, advisor=advisor)
        supervisor.tick()
        self.assertEqual(ran, ["repair"])

    def test_a_recovery_labels_the_attempt_as_worked(self):
        clock = FakeClock()
        ran = []
        advisor = self.Recorder(pick="travel")
        component = self._component(ran, healthy_after=True)
        supervisor = _supervisor([component], clock, advisor=advisor)
        supervisor.tick()
        clock.advance(60)
        supervisor.tick()
        self.assertEqual(advisor.labelled, [("farming", "worked")])

    def test_still_down_labels_the_attempt_as_failed(self):
        clock = FakeClock()
        ran = []
        advisor = self.Recorder(pick="travel")
        supervisor = _supervisor([self._component(ran)], clock, advisor=advisor)
        supervisor.tick()
        clock.advance(60)
        supervisor.tick()
        self.assertEqual(advisor.labelled, [("farming", "failed")])

    def test_the_next_situation_carries_what_was_tried_last(self):
        clock = FakeClock()
        ran = []
        advisor = self.Recorder(pick="travel")
        supervisor = _supervisor([self._component(ran)], clock, advisor=advisor)
        supervisor.tick()
        clock.advance(60)
        supervisor.tick()
        second = advisor.asked[-1][0]
        self.assertEqual(second["last_remedy"], "travel")
        self.assertEqual(second["last_outcome"], "failed")
        self.assertGreaterEqual(second["failures"], 2)

    def test_a_dry_run_asks_nothing_and_records_nothing(self):
        clock = FakeClock()
        ran = []
        advisor = self.Recorder(pick="travel")
        supervisor = _supervisor([self._component(ran)], clock, advisor=advisor,
                                 dry_run=True)
        supervisor.tick()
        self.assertEqual(ran, [])
        self.assertEqual(advisor.asked, [])
        self.assertEqual(advisor.learned, [])

    def test_the_real_farming_component_offers_a_choice(self):
        farming = next(c for c in build_supervisor().components
                       if c.name == "farming")
        self.assertEqual(sorted(farming.remedies), ["engage", "wait"])
        self.assertEqual(farming.default_remedy, "engage")


if __name__ == "__main__":
    unittest.main(verbosity=1)
