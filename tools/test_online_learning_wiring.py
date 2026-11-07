"""Pin the online-learning loop: it is assembled, it learns, and it refuses the wrong sources.

Why this exists. The layer in `planning/` is a conjugate-posterior experience store that the
economy writes to and the strategy ranker reads. Nothing exercised it: `agent/serve.py` assembled
the stack as `ScheduleManager(Planner(), Economy())`, which raises `TypeError` twice over (Economy
needs a cost model, Planner needs kb/cost/economy), and a broad `except` logged "schedule wiring
failed" and carried on. The visible symptom was silence -- `data/kb/experience.jsonl` did not exist,
because no code path that records had ever run.

So the checks are: the stack builds, one store is shared (not two), a live event moves a posterior,
a simulated event does not, and the event log can rebuild the snapshot from scratch.

    python tools/test_online_learning_wiring.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "dofus-network"))

from planning.economy import CostModel  # noqa: E402
from planning.experience import Event, ExperienceStore  # noqa: E402
from planning.schedule import build_schedule_stack  # noqa: E402


class OnlineLearningWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.store = ExperienceStore(log_path=root / "experience.jsonl",
                                     snapshot_path=root / "posteriors.json")
        self.state = root / "schedule_state.json"
        self.stack = build_schedule_stack(experience=self.store, state_file=self.state)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stack_builds(self):
        """The assembly that used to raise TypeError now returns a manager."""
        self.assertIsNotNone(self.stack.planner)
        self.assertIsNotNone(self.stack.economy)

    def test_one_store_is_shared(self):
        """A second store would fork the learning state, which is the bug the comment warns about."""
        economy_store = self.stack.economy.experience
        self.assertIs(economy_store, self.store)
        self.assertIs(self.stack.economy.cost.experience, economy_store)
        self.assertIs(self.stack.ranker.store, economy_store)

    def test_live_event_moves_the_posterior(self):
        """A real farm session is the only thing that moves kills-per-minute."""
        self.stack.economy.cost.record_farm(monster_id=31, player_level=60, kills=40,
                                            minutes=10.0, source="live")
        posterior = self.store.get(CostModel.kpm_key(31, 60))
        self.assertIsNotNone(posterior)
        # Gamma(shape=1, rate=1) prior, so 40 kills in 10 minutes gives (1+40)/(1+10): the
        # conjugate arithmetic, not the raw ratio. A test asserting 4.0 would be asserting that
        # the prior does not exist.
        self.assertAlmostEqual(posterior.mean(), 41.0 / 11.0, places=9)
        self.assertGreater(posterior.confidence(), 0.0)
        self.assertEqual(self.store.events_seen, 1)

    def test_simulated_event_is_refused(self):
        """A replay or a unit test must never teach the production posteriors."""
        before = self.store.events_seen
        self.stack.economy.cost.record_farm(monster_id=31, player_level=60, kills=40,
                                            minutes=10.0, source="sim")
        self.assertEqual(self.store.events_seen, before)
        self.assertEqual(self.store.rejected_events, 1)

    def test_log_rebuilds_the_snapshot(self):
        """The append-only log is the source of truth, so replay must reproduce the estimate."""
        for kills, minutes in ((40, 10.0), (12, 3.0), (25, 5.0)):
            self.stack.economy.cost.record_farm(monster_id=31, player_level=60, kills=kills,
                                                minutes=minutes, source="live")
        key = CostModel.kpm_key(31, 60)
        live_mean = self.store.get(key).mean()
        self.store.save()
        rebuilt = ExperienceStore(log_path=self.store.log_path,
                                  snapshot_path=pathlib.Path(self.tmp.name) / "rebuilt.json")
        replayed = rebuilt.replay()
        self.assertEqual(replayed, 3)
        self.assertAlmostEqual(rebuilt.get(key).mean(), live_mean, places=9)

    def test_travel_estimate_is_recorded_and_read_back(self):
        """`record_travel` writes a log-normal; the estimator reads it once confident enough."""

        class P:  # the two fields record_travel reads
            x, y = 12, 40

        key = "travel:12,40->20,44"
        self.stack.economy.cost.record_travel(P(), 20, 44, minutes=6.0)
        thin = self.store.get(key).mean()
        for _ in range(40):
            self.stack.economy.cost.record_travel(P(), 20, 44, minutes=6.0)
        thick = self.store.get(key).mean()
        # The prior is 2 minutes; evidence pulls the estimate toward 6 and never overshoots it.
        self.assertLess(thin, thick)
        self.assertGreater(thick, 5.0)
        self.assertLess(thick, 6.0)


class ControllerMemory(unittest.TestCase):
    """The loop's own learner: per-cause repair stats, and whether they outlive a run."""

    class Decision:
        cause = "incomplete"
        strategy = "retry"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name) / "controller"

    def tearDown(self):
        self.tmp.cleanup()

    def test_state_survives_a_new_session(self):
        """A new session id must not mean a new, empty brain.

        This is the defect the state directory showed: 803 files, 378 of them a single step,
        because the file was named after a uuid that changes every run.
        """
        from agent.controller import Controller

        first = Controller(state_dir=self.dir, session="session-a", state_key="dofus-04")
        first.observe_outcome(self.Decision(), "failed")
        first.save(force=True)
        second = Controller(state_dir=self.dir, session="session-b", state_key="dofus-04")
        second.load()
        stat = second._strategies.get("incomplete:retry")
        self.assertIsNotNone(stat, "the second session loaded nothing")
        self.assertEqual(stat.attempts, 1)
        self.assertEqual(stat.successes, 0)
        self.assertEqual(second.session, "session-b")

    def test_two_instances_do_not_share_a_brain(self):
        from agent.controller import Controller

        Controller(state_dir=self.dir, session="a", state_key="dofus-04").__class__
        one = Controller(state_dir=self.dir, session="a", state_key="dofus-04")
        one.observe_outcome(self.Decision(), "failed")
        one.save(force=True)
        other = Controller(state_dir=self.dir, session="a", state_key="dofus-05")
        other.load()
        self.assertNotIn("incomplete:retry", other._strategies)


if __name__ == "__main__":
    unittest.main(verbosity=2)
