"""The conjugate store finally has a caller in the loop that actually runs.

Before this, `planning/experience.py` was reachable only through `serve.py --schedule`, which nothing
launches: `ExperienceStore` appeared **zero** times in `agent/autopilot.py`, so every live session's
repair outcomes went into the controller's in-memory table and the JSONL and never into a posterior.
`data/kb/experience.jsonl` did not exist, which is the visible symptom.

These checks drive the real `Autopilot` object (a rule policy and a dead bridge are enough -- the
credit path needs no client) and assert the store is written, survives a save, and reloads.

    python tools/test_experience_producer.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "dofus-network"))

from agent.autopilot import Autopilot  # noqa: E402
from agent.controller import Decision  # noqa: E402
from agent.runtime import AgentRuntime, RulePolicy  # noqa: E402
from planning.experience import ExperienceStore  # noqa: E402


def decision(cause="incomplete", strategy="retry"):
    return Decision("continue", cause, "retrying", None, strategy, 0.5)


class ExperienceProducer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.log = root / "experience.jsonl"
        self.snap = root / "posteriors.json"
        runtime = AgentRuntime(policy=RulePolicy(),
                               directive_url="http://127.0.0.1:1/directive",
                               instance="audit")
        self.pilot = Autopilot(runtime=runtime, goals=["do all the quests"],
                               log_path=root / "a.jsonl", stop_file=root / "stop",
                               max_steps=1)
        self.pilot.experience = ExperienceStore(log_path=self.log, snapshot_path=self.snap)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_verdict_writes_a_posterior(self):
        """The label the controller already computes must land in the store."""
        self.pilot._note_experience(decision(), "advanced")
        posterior = self.pilot.experience.get("repair:incomplete:retry")
        self.assertIsNotNone(posterior, "the producer wrote nothing")
        self.assertEqual(posterior.n, 1)
        self.assertEqual(self.pilot.experience.events_seen, 1)

    def test_progress_and_failure_move_it_the_right_way(self):
        """A success must raise the mean, a noop must lower it, from the same prior."""
        self.pilot._note_experience(decision(), "advanced")
        self.pilot._note_experience(decision(), "advanced")
        good = self.pilot.experience.get("repair:incomplete:retry").mean()
        self.pilot._note_experience(decision(cause="plan_wrong", strategy="replan"), "noop")
        bad = self.pilot.experience.get("repair:plan_wrong:replan").mean()
        self.assertGreater(good, bad, "success and failure must not score the same")

    def test_save_flushes_and_reloads(self):
        """A short session must not lose its evidence: the store buffers until 25 events."""
        self.pilot._note_experience(decision(), "advanced")
        self.assertFalse(self.log.exists(), "the store should still be buffering")
        self.pilot.experience.save()
        self.assertTrue(self.log.exists(), "save did not flush the event log")
        again = ExperienceStore(log_path=self.log, snapshot_path=self.snap)
        self.assertEqual(again.events_seen, 1)
        self.assertAlmostEqual(again.get("repair:incomplete:retry").mean(),
                               self.pilot.experience.get("repair:incomplete:retry").mean(),
                               places=9)

    def test_a_new_session_adopts_what_the_last_one_learned(self):
        """The store is only worth writing if something reads it back."""
        from agent.controller import Controller

        for _ in range(6):
            self.pilot._note_experience(decision("incomplete", "retry"), "advanced")
        self.pilot._note_experience(decision("incomplete", "recover"), "noop")
        self.pilot.experience.save()

        store = ExperienceStore(log_path=self.log, snapshot_path=self.snap)
        fresh = Controller(state_dir=pathlib.Path(self.tmp.name) / "c", session="new",
                           state_key="new")
        seeded = fresh.seed_from_store(store)
        self.assertGreaterEqual(seeded, 2, "nothing was adopted from the store")
        stat = fresh._strategies.get("incomplete:retry")
        self.assertIsNotNone(stat)
        self.assertEqual(stat.attempts, 6)
        self.assertEqual(stat.successes, 6)
        best, score = fresh._best_strategy("incomplete")
        self.assertEqual(best, "retry", "the seeded table did not change the choice")
        self.assertGreater(score, 0.0)

    def test_seeding_keeps_the_larger_evidence(self):
        """Per-instance JSON and the global store disagree after a restart; keep the more evidence."""
        from agent.controller import Controller, StrategyStat

        for _ in range(4):
            self.pilot._note_experience(decision(), "advanced")
        self.pilot.experience.save()
        store = ExperienceStore(log_path=self.log, snapshot_path=self.snap)
        fresh = Controller(state_dir=pathlib.Path(self.tmp.name) / "c2", session="s",
                           state_key="s")
        fresh._strategies["incomplete:retry"] = StrategyStat(attempts=2, successes=1)
        fresh.seed_from_store(store)
        self.assertEqual(fresh._strategies["incomplete:retry"].attempts, 4)

    def test_a_missing_store_is_harmless(self):
        """The loop must run with no store at all (the default), not crash."""
        self.pilot.experience = None
        self.pilot._note_experience(decision(), "advanced")   # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
