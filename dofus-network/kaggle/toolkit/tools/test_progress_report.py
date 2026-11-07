"""The scoreboard must count sessions, not baselines and not rotations."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.progress_report import summarise, verdict  # noqa: E402


def close(at, deltas, actions=100, moved=20, promoted=False, fights=2,
          labeler=None):
    return {"at": at, "steps": {
        "report": {"deltas": deltas, "baseline_files": 0},
        "janitor": {"kept": {"actions": actions, "actions_moved": moved}},
        "move": {"promoted": promoted, "test_auc": 0.787},
        "learn": {"n": fights},
        "rebuild": {"labeler_sha": labeler} if labeler else {},
    }}


def legacy_close(at, deltas):
    """A row written before the close stamped `baseline_files`."""
    return {"at": at, "steps": {"report": {"deltas": deltas}}}


class ProgressTests(unittest.TestCase):

    def test_baselines_and_rotations_are_not_session_progress(self):
        rows = [
            legacy_close("old", {"a.jsonl": {"steps": 9704,
                                             "objectives_completed": -124}}),
            close("A", {"a.jsonl": {"steps": 100, "objectives_completed": 2,
                                    "quests_completed": 1, "failed": 5}}),
            close("B", {"a.jsonl": {"baseline": True, "steps": 999,
                                    "objectives_completed": 9},
                        "b.jsonl": {"rotated": True}}),
            close("C", {"a.jsonl": {"steps": 40, "objectives_completed": -1,
                                    "quests_completed": 1, "failed": 0}},
                  actions=150, moved=45),
        ]
        report = summarise(rows)
        self.assertEqual(report["closes"], 4)
        self.assertEqual(report["legacy_closes"], 1)
        self.assertEqual(report["sessions_with_new_rows"], 2)
        self.assertEqual(report["totals"],
                         {"steps": 140.0, "objectives_completed": 1.0,
                          "objectives_done": 0.0, "quests_completed": 2.0,
                          "failed": 5.0})
        self.assertTrue(report["objective_progress"])

    def test_world_change_rate_is_the_latest_corpus_not_a_sum(self):
        report = summarise([
            close("A", {"a.jsonl": {"steps": 1}}, actions=100, moved=20),
            close("B", {"a.jsonl": {"steps": 1}}, actions=150, moved=45),
        ])
        self.assertEqual(report["corpus"]["actions"], 150)
        self.assertEqual(report["corpus"]["moved"], 45)
        self.assertEqual(report["corpus"]["rate"], 0.3)
        self.assertEqual(report["corpus"]["at"], "B")
        self.assertEqual([row["move_auc"] for row in report["per_close"]],
                         [0.787, 0.787])

    def test_a_forced_close_with_zero_deltas_is_not_a_session(self):
        report = summarise([
            close("A", {"a.jsonl": {"steps": 0, "objectives_completed": 0,
                                    "quests_completed": 0, "failed": 0}}),
        ])
        self.assertEqual(report["closes"], 1)
        self.assertEqual(report["sessions_with_new_rows"], 0)
        self.assertIn("not yet measured", verdict(report))

    def test_an_empty_ledger_says_the_question_is_unmeasured(self):
        report = summarise([])
        self.assertEqual(report["closes"], 0)
        self.assertIsNone(report["corpus"]["rate"])
        self.assertFalse(report["objective_progress"])
        self.assertIn("not yet measured", verdict(report))

    def test_a_relabeled_rebuild_is_not_a_session_rate(self):
        report = summarise([
            close("A", {"a.jsonl": {"steps": 1}}, actions=100, moved=20,
                  labeler="aaa111"),
            close("B", {"a.jsonl": {"steps": 2}}, actions=150, moved=45,
                  labeler="bbb222"),
            close("C", {"a.jsonl": {"steps": 2}}, actions=200, moved=60,
                  labeler="bbb222"),
        ])
        second, third = report["per_close"][1], report["per_close"][2]
        self.assertTrue(second["relabeled"])
        self.assertEqual(second["session_rate"], 0.5)
        self.assertFalse(third["relabeled"])
        self.assertEqual(report["relabeled_closes"], 1)
        self.assertEqual(report["session_rates"], [0.3])

    def test_objectives_done_alone_is_progress(self):
        report = summarise([
            close("A", {"a.jsonl": {"steps": 1, "objectives_completed": -19,
                                    "objectives_done": 4}}),
        ])
        self.assertTrue(report["objective_progress"])
        self.assertIn("4 newly done", verdict(report))

    def test_the_verdict_names_the_objective_and_the_rate(self):
        report = summarise([
            close("A", {"a.jsonl": {"steps": 2, "objectives_completed": 3}},
                  actions=200, moved=50),
        ])
        text = verdict(report)
        self.assertIn("objectives +3", text)
        self.assertIn("25.0%", text)

    def test_the_session_rate_is_the_delta_between_snapshots(self):
        report = summarise([
            close("A", {"a.jsonl": {"steps": 1}}, actions=100, moved=20),
            close("B", {"a.jsonl": {"steps": 1}}, actions=150, moved=45),
        ])
        first, second = report["per_close"]
        self.assertIsNone(first["session_rate"])
        self.assertEqual((second["session_actions"], second["session_moved"]), (50, 25))
        self.assertEqual(second["session_rate"], 0.5)
        self.assertEqual(report["session_rates"], [0.5])
        self.assertEqual(report["trend"], "unmeasured")

    def test_a_rebuild_that_loses_rows_is_shifted_not_a_session(self):
        report = summarise([
            close("A", {"a.jsonl": {"steps": 1}}, actions=200, moved=50),
            close("B", {"a.jsonl": {"steps": 1}}, actions=190, moved=48),
        ])
        second = report["per_close"][1]
        self.assertTrue(second["shifted"])
        self.assertIsNone(second["session_rate"])
        self.assertEqual(report["session_rates"], [])

    def test_the_trend_compares_successive_session_rates(self):
        report = summarise([
            close("A", {"a.jsonl": {"steps": 1}}, actions=100, moved=20),
            close("B", {"a.jsonl": {"steps": 1}}, actions=150, moved=25),
            close("C", {"a.jsonl": {"steps": 1}}, actions=220, moved=60),
        ])
        self.assertEqual(report["session_rates"], [0.1, 0.5])
        self.assertEqual(report["trend"], "rising")
        self.assertIn("trend rising", verdict(report))

    def test_a_legacy_snapshot_anchors_the_delta_but_not_the_trend(self):
        rows = [
            legacy_close("old", {"a.jsonl": {"steps": 9704}}),
            close("B", {"a.jsonl": {"steps": 1}}, actions=150, moved=45),
        ]
        rows[0]["steps"]["janitor"] = {"kept": {"actions": 100, "actions_moved": 20}}
        report = summarise(rows)
        second = report["per_close"][1]
        self.assertTrue(second["legacy"] is False)
        self.assertEqual(second["session_rate"], 0.5)
        self.assertEqual(report["session_rates"], [0.5])
        self.assertEqual(report["trend"], "unmeasured")


if __name__ == "__main__":
    unittest.main(verbosity=1)
