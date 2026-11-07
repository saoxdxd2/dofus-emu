"""Objectives completed come from client status flips, not the step-index sum.

The published `objectives_completed` is the sum of active quest step indices: it reads
0 for single-step quests and falls when a quest is finished, so a session that did the
most good can report a negative. A `step_objectives` entry going done false -> true is
the completion event itself; one id flips once.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.session_report import summarise  # noqa: E402


def step(ts, objectives, counter=None):
    observation = {"step_objectives": objectives}
    if counter is not None:
        observation["objectives_completed"] = counter
    return {"kind": "step", "ts": ts, "ok": True, "tool": "quest.advance",
            "observation": observation}


class ObjectivesDoneTests(unittest.TestCase):

    def write(self, rows):
        directory = tempfile.mkdtemp(prefix="report-")
        path = Path(directory) / "log.jsonl"
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                        encoding="utf-8")
        return path

    def test_a_status_flip_is_one_completion(self):
        report = summarise(self.write([
            step(0, [{"id": 1, "done": False}, {"id": 2, "done": True}]),
            step(60, [{"id": 1, "done": True}]),
        ]))
        self.assertEqual(report["objectives_done"], 1)
        self.assertEqual(report["sessions"], 1)

    def test_an_objective_first_seen_done_is_not_credited(self):
        report = summarise(self.write([
            step(0, [{"id": 2, "done": True}]),
            step(60, [{"id": 2, "done": True}, {"id": 3, "done": True}]),
        ]))
        self.assertEqual(report["objectives_done"], 0)

    def test_a_flip_across_a_session_gap_is_still_counted(self):
        report = summarise(self.write([
            step(0, [{"id": 1, "done": False}]),
            step(400, [{"id": 1, "done": True}]),
        ]))
        self.assertEqual(report["sessions"], 2)
        self.assertEqual(report["objectives_done"], 1)

    def test_completions_rise_while_the_published_counter_falls(self):
        report = summarise(self.write([
            step(0, [{"id": 1, "done": False}], counter=5),
            step(60, [{"id": 1, "done": True}], counter=3),
        ]))
        self.assertEqual(report["objectives_completed"], -2)
        self.assertEqual(report["objectives_done"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
