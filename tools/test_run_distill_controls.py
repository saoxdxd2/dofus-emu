"""Witnesses for check_resume: only the job that owns a directory may continue into it."""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from argparse import Namespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import numpy as np
import run_distill_controls as driver

ZED = "0" * 64


def build(partitions, out, teacher, *, seed=1, games=10, settings=(200, 71, 8, 2, 6),
          record=None):
    """A job directory that really belongs to `teacher`, with knobs to corrupt one artifact."""
    record = record or {}
    for name in ("train", "edit", "dev", "test"):
        (partitions / ("%s.npz" % name)).write_bytes(b"table-" + name.encode())
    teacher.write_bytes(b"checkpoint-bytes")
    real = {name: driver.sha256(partitions / ("%s.npz" % name))
            for name in ("train", "edit", "dev")}
    np.savez_compressed(out / "teacher_cache.npz",
                        checkpoint_sha256=np.asarray(record.get("teacher", driver.sha256(teacher))),
                        data_sha256=np.asarray(record.get("edit", real["edit"])))
    (out / "shared.corpus.json").write_text(json.dumps({"manifest": {
        "seed": record.get("seed", seed), "requested_games": games,
        "sources": {"data_sha256": record.get("train", real["train"])}, "teacher": {}}}),
        encoding="utf-8")
    for key in ("duel_shipped", "duel_arm"):
        beam = record.get("settings", settings)
        (out / ("%s.json" % key)).write_text(json.dumps({"config": {
            "data_sha256": record.get("dev", real["dev"]), "matchups": beam[0],
            "matchup_seed": beam[1], "beam_width": beam[2], "max_actions": beam[3],
            "plan_k": beam[4]}}), encoding="utf-8")


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="resume-"))
        self.partitions = self.tmp / "partitions"
        self.out = self.tmp / "job"
        self.teacher = self.tmp / "teacher.pkl"
        self.args = Namespace(teacher=self.teacher, seed=1, games=10, matchups=200,
                              matchup_seed=71)
        self.partitions.mkdir()
        self.out.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rebuild(self, **changes):
        for path in list(self.partitions.glob("*")) + list(self.out.glob("*")):
            path.unlink()
        build(self.partitions, self.out, self.teacher, **changes)

    def check(self):
        driver.check_resume(self.out, self.args, self.partitions / "train.npz",
                            self.partitions / "edit.npz", self.partitions / "dev.npz")

    def test_owning_job_continues(self):
        self.rebuild()
        self.check()

    def test_empty_directory_is_not_a_conflict(self):
        self.rebuild()
        for path in self.out.glob("*"):
            path.unlink()
        self.check()

    def test_different_teacher_refuses_and_names_both(self):
        self.rebuild(record={"teacher": ZED})
        with self.assertRaises(SystemExit) as caught:
            self.check()
        message = str(caught.exception)
        self.assertIn("teacher_cache belongs to checkpoint", message)
        self.assertIn(ZED[:12], message)
        self.assertIn(driver.sha256(self.teacher)[:12], message)

    def test_different_edit_table_refuses(self):
        self.rebuild(record={"edit": ZED})
        with self.assertRaises(SystemExit) as caught:
            self.check()
        self.assertIn("different edit table", str(caught.exception))

    def test_different_train_table_refuses(self):
        self.rebuild(record={"train": ZED})
        with self.assertRaises(SystemExit) as caught:
            self.check()
        self.assertIn("different train table", str(caught.exception))

    def test_different_budget_or_seed_refuses(self):
        self.rebuild(record={"seed": 7})
        with self.assertRaises(SystemExit) as caught:
            self.check()
        self.assertIn("not 1 / 10", str(caught.exception))

    def test_duels_on_a_different_dev_table_refuse(self):
        self.rebuild(record={"dev": ZED})
        with self.assertRaises(SystemExit) as caught:
            self.check()
        self.assertIn("different dev table", str(caught.exception))

    def test_two_duels_that_disagree_on_search_refuse(self):
        self.rebuild(record={"settings": (200, 71, 4, 2, 6)})
        path = self.out / "duel_arm.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["config"]["beam_width"] = 8
        path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            self.check()
        self.assertIn("disagree on search settings", str(caught.exception))

    def test_missing_optional_artifacts_do_not_invent_a_refusal(self):
        self.rebuild()
        (self.out / "teacher_cache.npz").unlink()
        (self.out / "shared.corpus.json").unlink()
        self.check()


if __name__ == "__main__":
    unittest.main(verbosity=2)
