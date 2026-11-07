"""The close must be event-driven, isolated per step, and auditable.

The two failure modes worth a test are the ones a naive pipeline has: rebuilding
the world on every close even when nothing happened, and one broken step taking
the ledger (or the session) down with it.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import session_close  # noqa: E402


def step(index: int, objectives: int = 0):
    return {
        "kind": "step", "ts": 1000.0 + index, "iso": "2026-10-31T00:00:00",
        "instance": "unit", "goal": "unit goal", "index": index,
        "tool": "quest.advance", "args": {}, "ok": True, "latency_ms": 20,
        "observation": {"objectives_completed": objectives,
                        "position": {"map_id": 10898},
                        "character": {"level": 3}, "kamas": 100},
    }


def write(path: Path, rows) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


class CloseTests(unittest.TestCase):

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.move_out = self.root / "models"
        self.move_out.mkdir()
        (self.move_out / "foresight.json").write_bytes(b'{"head": 1}')
        write(self.logs / "default.jsonl", [step(0), step(1)])
        write(self.logs / "play_0.jsonl", [step(0)])
        self.calls = {"builder": 0, "janitor": 0, "move": 0, "learner": 0}

        def builder():
            self.calls["builder"] += 1
            return {"rows": 2, "segments": 1, "split_rows": {"train": 2},
                    "labeler_sha": "abc123def456"}

        def janitor():
            self.calls["janitor"] += 1
            return {"kept": {"total": 2, "actions": 2},
                    "quarantine": {"total": 0, "by_reason": {}},
                    "fatal": [], "in_coverage": True}

        def move():
            self.calls["move"] += 1
            return {"promoted": False, "reasons": ["test_rows 123 < 200"],
                    "test_rows": 123, "test_auc": 0.787, "test_ece": 0.315,
                    "seed_aucs": [0.787, 0.688, 0.790], "artifact": None}

        def learner():
            self.calls["learner"] += 1
            return {"action": "skipped_not_due", "status": "collecting",
                    "n": 2, "need": 40,
                    "filters": {"kept_n": 2,
                                "dropped": {"tutorial_authorised": 7},
                                "heuristic_members": 2}}

        self.builder, self.janitor = builder, janitor
        self.move, self.learner = move, learner

    def tearDown(self):
        self.temporary.cleanup()

    def config(self, **over) -> session_close.CloseConfig:
        values = dict(
            logs=self.logs, transitions_out=self.root / "transitions",
            clean_out=self.root / "clean", reference=self.root / "ref",
            ledger=self.root / "ledger.jsonl", state=self.root / "state.json",
            lock=self.root / "close.lock", move_out=self.move_out,
            rebuild_interval_s=0.0, learn=True, now=1000.0,
            evidence=None, evidence_out=self.root / "evidence.jsonl",
            builder=self.builder, janitor=self.janitor, move_trainer=self.move,
            learner=self.learner)
        values.update(over)
        return session_close.CloseConfig(**values)

    def rows(self, path: Path):
        # The shared salvage reader, so this suite reads a capture the way the reports do.
        from agent import jsonio
        return list(jsonio.iter_rows(path))

    def ledger(self):
        return self.rows(self.root / "ledger.jsonl")

    def test_a_close_runs_every_step_and_records_it(self):
        record = session_close.close(self.config())
        self.assertEqual(record["action"], "closed")
        self.assertEqual(record["logs"], ["default.jsonl"],
                         "a wire capture must not be summarised as a session")
        self.assertEqual(self.calls, {"builder": 1, "janitor": 1, "move": 1,
                                      "learner": 1})
        self.assertTrue(record["steps"]["report"]["ok"])
        move = record["steps"]["move"]
        self.assertTrue(move["ok"])
        self.assertFalse(move["promoted"])
        self.assertEqual(move["test_rows"], 123)
        self.assertEqual(move["seed_aucs"], [0.787, 0.688, 0.790])
        self.assertTrue(move["reasons"])
        learn = record["steps"]["learn"]
        self.assertEqual(learn["filters"]["dropped"]["tutorial_authorised"], 7)
        delta = record["steps"]["report"]["deltas"]["default.jsonl"]
        self.assertTrue(delta["baseline"])
        self.assertEqual(delta["steps"], 2)
        self.assertEqual(delta.get("objectives_done"), 0.0)
        self.assertEqual(record["steps"]["rebuild"]["labeler_sha"], "abc123def456")
        self.assertEqual(record["steps"]["report"]["baseline_files"], 1)
        self.assertEqual(record["steps"]["report"]["rotated_files"], 0)
        self.assertEqual(record["steps"]["learn"]["action"], "skipped_not_due")
        ledger = self.ledger()
        self.assertEqual(len(ledger), 1)
        state = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["closes"], 1)
        self.assertTrue(state["last_rebuild_at"])
        self.assertIn("not yet measured", record["verdict"])
        self.assertEqual(record["progress"]["sessions_with_new_rows"], 0)
        self.assertFalse(record["progress"]["objective_progress"])

    def test_nothing_new_writes_no_work(self):
        session_close.close(self.config())
        record = session_close.close(self.config(now=1001.0))
        self.assertEqual(record["action"], "no_new_rows")
        self.assertEqual(self.calls, {"builder": 1, "janitor": 1, "move": 1,
                                      "learner": 1})
        self.assertEqual(len(self.ledger()), 2)
        self.assertEqual(record["progress"]["sessions_with_new_rows"], 0)
        self.assertIn("not yet measured", record["verdict"])

    def test_the_scorecard_reads_the_ledger_not_only_the_new_row(self):
        history = {"at": "A", "steps": {"report": {
            "deltas": {"a.jsonl": {"steps": 2, "objectives_completed": 0,
                                   "objectives_done": 0, "quests_completed": 0,
                                   "failed": 0}},
            "baseline_files": 0}}}
        write(self.root / "ledger.jsonl", [history])
        scored = session_close._scorecard(
            {"at": "B", "steps": {}}, self.root / "ledger.jsonl")
        self.assertEqual(scored["progress"]["sessions_with_new_rows"], 1)
        self.assertIn("over 1 session(s) with new rows", scored["verdict"])

    def test_a_broken_scorecard_does_not_fail_the_close(self):
        from tools import progress_report
        original = progress_report.summarise

        def broken(records):
            raise RuntimeError("scoreboard exploded")

        progress_report.summarise = broken
        try:
            record = session_close.close(self.config())
        finally:
            progress_report.summarise = original
        self.assertEqual(record["action"], "closed")
        self.assertIsNone(record["verdict"])
        self.assertIn("scoreboard exploded", record["progress"]["error"])

    def test_appended_rows_become_a_delta_not_a_total(self):
        session_close.close(self.config())
        write(self.logs / "default.jsonl", [step(2, objectives=1)])
        record = session_close.close(self.config(now=1002.0))
        delta = record["steps"]["report"]["deltas"]["default.jsonl"]
        self.assertFalse(delta.get("baseline"))
        self.assertEqual(delta["steps"], 1)
        self.assertEqual(delta["objectives_completed"], 1)

    def test_the_rebuild_is_throttled_but_learning_still_runs(self):
        session_close.close(self.config())
        write(self.logs / "default.jsonl", [step(3)])
        record = session_close.close(self.config(now=1003.0, rebuild_interval_s=300.0))
        self.assertEqual(record["steps"]["rebuild"]["skipped"], "interval")
        self.assertEqual(record["steps"]["janitor"]["skipped"], "no_rebuild")
        self.assertTrue(record["steps"]["learn"]["ok"])
        self.assertEqual(self.calls["builder"], 1)
        self.assertEqual(self.calls["learner"], 2)

    def test_a_concurrent_close_stands_down(self):
        lock = self.root / "close.lock"
        lock.write_text(json.dumps({"pid": 999, "at": 1000.0}), encoding="utf-8")
        record = session_close.close(self.config())
        self.assertEqual(record["action"], "busy")
        self.assertEqual(record["holder"]["pid"], 999)
        self.assertEqual(self.calls, {"builder": 0, "janitor": 0, "move": 0,
                                      "learner": 0})
        self.assertFalse((self.root / "ledger.jsonl").exists())

    def test_a_stale_lock_is_reclaimed(self):
        lock = self.root / "close.lock"
        lock.write_text(json.dumps({"pid": 999, "at": 1000.0 - 3600.0}),
                        encoding="utf-8")
        record = session_close.close(self.config())
        self.assertEqual(record["action"], "closed")
        self.assertFalse(lock.exists(), "the close must release the lock it took")

    def test_the_move_head_can_be_disabled(self):
        record = session_close.close(self.config(move=False))
        self.assertEqual(record["steps"]["move"]["skipped"], "disabled")
        self.assertEqual(self.calls["move"], 0)
        self.assertEqual(self.calls["learner"], 1)

    def test_one_failing_step_does_not_take_the_close_down(self):
        def broken():
            raise RuntimeError("learner exploded")

        record = session_close.close(self.config(learner=broken))
        self.assertFalse(record["steps"]["learn"]["ok"])
        self.assertIn("learner exploded", record["steps"]["learn"]["error"])
        self.assertTrue(record["steps"]["janitor"]["ok"])
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(record["action"], "closed")

    def test_the_close_names_the_artifacts_that_drove_the_session(self):
        def promoting_move():
            (self.move_out / "foresight.json").write_bytes(b'{"head": 2}')
            return {"promoted": True, "test_rows": 400, "test_auc": 0.7,
                    "test_ece": 0.05, "seed_aucs": [0.7, 0.7, 0.7],
                    "artifact": "promoted.json"}

        record = session_close.close(self.config(move_trainer=promoting_move))
        before = record["artifacts"]["before"]["move_head"]
        after = record["artifacts"]["after"]["move_head"]
        self.assertEqual(before["sha256"],
                         hashlib.sha256(b'{"head": 1}').hexdigest()[:16])
        self.assertEqual(after["sha256"],
                         hashlib.sha256(b'{"head": 2}').hexdigest()[:16])
        self.assertIn("fight_calibrator", record["artifacts"]["before"])
        self.assertIn("foresight", record["artifacts"]["before"])
        self.assertIn("tool_stack", record["artifacts"]["after"])

    def test_the_rebuild_record_carries_the_manifest_labeler(self):
        fields = session_close._manifest_fields(
            {"rows": 9, "segments": 2, "split_rows": {"train": 7},
             "split_policy": "temporal", "actions_moved": 3, "actions": 11,
             "labeler_sha": "cbd39ce2dd57"})
        self.assertEqual(fields["labeler_sha"], "cbd39ce2dd57")
        self.assertEqual(fields["rows"], 9)
        self.assertEqual(fields["split_policy"], "temporal")

    def test_a_shrunk_file_is_rotation_not_a_negative_session(self):
        rotated = session_close._delta({"steps": 5, "wall_minutes": 10.0,
                                        "failed": 1, "ok": 4, "maps": 2,
                                        "ready_steps": 0, "manual_interventions": 0,
                                        "objectives_completed": 0,
                                        "quests_completed": 0},
                                       {"steps": 9, "wall_minutes": 20.0,
                                        "failed": 1, "ok": 8, "maps": 2,
                                        "ready_steps": 0, "manual_interventions": 0,
                                        "objectives_completed": 0,
                                        "quests_completed": 0})
        self.assertTrue(rotated.get("rotated"))
        # The quest counter can fall without the file shrinking; that is data.
        reset = session_close._delta({"steps": 12, "wall_minutes": 22.0,
                                      "failed": 1, "ok": 10, "maps": 2,
                                      "ready_steps": 0, "manual_interventions": 0,
                                      "objectives_completed": -2,
                                      "quests_completed": 1},
                                     {"steps": 10, "wall_minutes": 20.0,
                                      "failed": 1, "ok": 8, "maps": 2,
                                      "ready_steps": 0, "manual_interventions": 0,
                                      "objectives_completed": 3,
                                      "quests_completed": 0})
        self.assertFalse(reset.get("rotated"))
        self.assertEqual(reset["objectives_completed"], -5)
        self.assertEqual(reset["steps"], 2)

    # The close injects the learner and the trainer, so the stubs above never
    # exercise the real `_learn`/`_train_move` row builders. Both dropped fields
    # are what a close uses to explain an ineligible corpus and a lucky seed
    # draw; a 2026-10-31 mutation that dropped them went unnoticed until the
    # sweep ran through the real functions.
    def test_the_learn_step_keeps_why_the_evidence_was_dropped(self):
        from combat import online_learn
        original = online_learn.run

        def learner_run(*args, **kwargs):
            return {"action": "skipped_not_due", "status": "collecting",
                    "n": 2, "need": 40,
                    "filters": {"kept_n": 2,
                                "dropped": {"tutorial_authorised": 7}}}

        online_learn.run = learner_run
        try:
            step = session_close._learn()
        finally:
            online_learn.run = original
        self.assertEqual(step["filters"]["dropped"]["tutorial_authorised"], 7)

    def test_the_move_step_keeps_every_seed_spread(self):
        from types import SimpleNamespace

        payload = {"gate": {"promoted": False,
                            "reasons": ["test_rows 123 < 200"], "seeds": None,
                            "test_rows": 123, "median_auc": 0.7,
                            "worst_ece": 0.2},
                   "seeds": [{"test": {"auc_moved": 0.7, "ece_moved": 0.1}},
                             {"test": {"auc_moved": 0.6, "ece_moved": 0.2}}]}

        def fake_run(cmd, **kwargs):
            (self.move_out / "metrics_move_head.json").write_text(
                json.dumps(payload), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        import subprocess
        original = subprocess.run
        subprocess.run = fake_run
        try:
            step = session_close._train_move(self.root / "data", self.move_out)
        finally:
            subprocess.run = original
        self.assertEqual(step["seed_aucs"], [0.7, 0.6])
        self.assertEqual(step["seed_eces"], [0.1, 0.2])
        self.assertEqual(step["test_auc"], 0.7)

    def live(self, directory: Path):
        """A stand-in for %LOCALAPPDATA%/DofusFarm, with canned per-slot summaries."""
        directory.mkdir(parents=True, exist_ok=True)
        canned = {
            "autopilot.dofus-01.jsonl": {
                "file": "autopilot.dofus-01.jsonl", "sessions": 11, "wall_minutes": 138.7,
                "steps": 40, "ok": 36, "failed": 4, "manual_interventions": 12,
                "intervention_rows": 9,
                "recoveries": {"recover": 20, "replan": 11, "retry": 3}},
            "autopilot.dofus-02.jsonl": {
                "file": "autopilot.dofus-02.jsonl", "sessions": 12, "wall_minutes": 212.6,
                "steps": 17, "ok": 15, "failed": 2, "manual_interventions": 3,
                "intervention_rows": 2,
                "recoveries": {"recover": 5, "replan": 2, "retry": 1}},
        }
        for name in canned:
            (directory / name).write_text('{"at": 1}\n', encoding="utf-8")
        # The pre-daemon single log is not a slot, and a wire capture has no step rows.
        (directory / "autopilot.jsonl").write_text('{"at": 1}\n', encoding="utf-8")
        (directory / "play_dofus-01.jsonl").write_text('{"at": 1}\n', encoding="utf-8")
        return canned

    def test_a_failing_evidence_capture_does_not_fail_the_close(self):
        """The live logs are an outside input; reading them may fail, the ledger may not."""
        live_dir = self.root / "live-failing"
        self.live(live_dir)

        def broken(path):
            raise RuntimeError("the live log is on a disk that vanished")

        record = session_close.close(self.config(
            evidence=live_dir, evidence_out=self.root / "evidence.jsonl",
            evidence_reporter=broken))
        step = record["steps"]["evidence"]
        self.assertEqual(record["action"], "closed",
                         "a close must still book its own session")
        self.assertFalse(step["ok"])
        self.assertIn("disk that vanished", step["error"])
        self.assertFalse((self.root / "evidence.jsonl").exists(),
                         "a failed capture writes no row")
        self.assertEqual(len(self.ledger()), 1,
                         "the ledger row survives its failing step")

    def test_the_close_copies_the_live_totals_where_a_clone_can_read_them(self):
        """G96: §12.2's budget numbers are measured from logs that are not in the repo."""
        live_dir = self.root / "live"
        canned = self.live(live_dir)
        seen = []

        def reporter(path):
            seen.append(path.name)
            return canned[path.name]

        record = session_close.close(self.config(
            evidence=live_dir, evidence_out=self.root / "evidence.jsonl",
            evidence_reporter=reporter))
        step = record["steps"]["evidence"]
        self.assertTrue(step["ok"], step.get("error"))
        self.assertEqual(seen, ["autopilot.dofus-01.jsonl", "autopilot.dofus-02.jsonl"],
                         "only the per-slot autopilot logs are evidence")
        self.assertEqual(step["totals"]["steps"], 57)
        self.assertEqual(step["totals"]["manual_interventions"], 15)
        self.assertEqual(step["totals"]["recoveries"],
                         {"recover": 25, "replan": 13, "retry": 4})
        self.assertEqual(step["totals"]["recovery_rows"], 42)
        self.assertAlmostEqual(step["totals"]["wall_minutes"], 351.3)
        rows = self.rows(self.root / "evidence.jsonl")
        self.assertEqual(len(rows), 1)
        self.assertEqual([instance["instance"] for instance in rows[0]["instances"]],
                         ["autopilot.dofus-01.jsonl", "autopilot.dofus-02.jsonl"])
        self.assertIn("size", rows[0]["instances"][0]["signature"],
                      "a capture without the source size cannot be re-checked")
        self.assertIsInstance(rows[0].get("capture_seconds"), float,
                              "the loop kills the close after a timeout, so the read's own "
                              "cost has to be in the row that says the read happened")
        self.assertIn("evidence:", session_close.summary(record))

        # Second close over the same bytes: the totals have not moved, so the 82 MB read
        # must not happen again and no second row is appended.
        again = session_close.close(self.config(
            evidence=live_dir, evidence_out=self.root / "evidence.jsonl",
            evidence_reporter=reporter))
        self.assertEqual(again["steps"]["evidence"]["files"], 2)
        self.assertIsNone(again["steps"]["evidence"]["written"])
        self.assertEqual(len(seen), 2, "an unchanged live log must not be re-summarised")
        with (live_dir / "autopilot.dofus-01.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"at": 2}\n')
        grown = session_close.close(self.config(
            evidence=live_dir, evidence_out=self.root / "evidence.jsonl",
            evidence_reporter=reporter))
        self.assertEqual(grown["steps"]["evidence"]["changed"],
                         ["autopilot.dofus-01.jsonl"])
        rows = self.rows(self.root / "evidence.jsonl")
        self.assertEqual(len(rows), 2, "a grown log is a new capture")

    def test_an_absent_live_directory_is_a_stated_skip(self):
        """A close on a machine without the daemon must say so, not write an empty capture."""
        out = self.root / "evidence.jsonl"
        record = session_close.close(self.config(
            evidence=self.root / "no-such-dir", evidence_out=out))
        step = record["steps"]["evidence"]
        self.assertTrue(step["ok"], step.get("error"))
        self.assertEqual(step["files"], 0)
        self.assertIn("autopilot.dofus-", step["skipped"])
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main(verbosity=1)
