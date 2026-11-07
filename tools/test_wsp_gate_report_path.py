"""The gate's positional argument is a destination in one mode and a source in another. Prove the
guard refuses to write onto something that is not a report, without refusing the real use.

Why this exists: on 2026-11-06 `python tools/wsp_gate.py score <corpus>.npz` was run to "check a
corpus", and `score` read its argument as an output path -- `write_text(json.dumps(report))` -- over
two encounter corpora in the evidence directory. One was rebuilt from the knobs its own meta records;
the other had been generated before the summon columns existed and could not be. The CLI gave no
signal: the mode decides where the same argument points, and the file it clobbered was a binary that a
JSON writer has no business touching.

Three arms, in this order:
  refuse-a-corpus      score onto an existing .npz -> SystemExit, and the corpus's bytes are
                       byte-for-byte what they were before the call. This is the case that has to
                       fail loudly; if the guard only complains after `collect()` ran, the harness
                       is slow rather than wrong, so the test also asserts it returns *before* any
                       scoring work happens.
  refuse-a-binary-json score onto a file named .json whose first byte is a zip magic -> refused,
                       because a suffix rule alone would have accepted `data.npz.json`.
  accept-a-report      score onto a fresh .json -> allowed (the guard is not a blanket block, and a
                       rule that never lets the real use through gets routed around).

    python tools/test_wsp_gate_report_path.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parent.parent / "tools" / "wsp_gate.py"
_spec = importlib.util.spec_from_file_location("wsp_gate", TOOL)
gate = importlib.util.module_from_spec(_spec)
sys.modules["wsp_gate"] = gate
_spec.loader.exec_module(gate)

ZIP_MAGIC = b"PK\x03\x04"


class ReportPathTests(unittest.TestCase):
    def test_score_refuses_a_corpus_path_and_leaves_its_bytes_alone(self):
        with tempfile.TemporaryDirectory() as d:
            victim = pathlib.Path(d) / "encounters_real_2048.npz"
            victim.write_bytes(ZIP_MAGIC + b"the corpus, 200 KB in real life")
            before = victim.read_bytes()
            with self.assertRaises(SystemExit) as ctx:
                gate.main(["score", str(victim)])
            self.assertIn("corpus", str(ctx.exception).lower())
            self.assertEqual(victim.read_bytes(), before,
                             "the guard complained and wrote anyway")

    def test_the_refusal_happens_before_any_scoring_work(self):
        # `collect()` is the expensive part; a guard that runs after it costs minutes instead of
        # destroying a corpus, which is not a fix. The checkpoint is deliberately a path that cannot
        # load, so if the code reached collect this arm would fail with something other than the
        # guard's own message.
        with tempfile.TemporaryDirectory() as d:
            victim = pathlib.Path(d) / "boards.npz"
            victim.write_bytes(ZIP_MAGIC + b"x")
            with self.assertRaises(SystemExit) as ctx:
                gate.main(["score", str(victim), "--rows", "8",
                           "--checkpoint", str(pathlib.Path(d) / "no-such-checkpoint.pkl")])
            self.assertIn("out.json", str(ctx.exception))

    def test_score_refuses_a_json_named_file_that_is_not_json(self):
        with tempfile.TemporaryDirectory() as d:
            binary = pathlib.Path(d) / "report.json"
            binary.write_bytes(ZIP_MAGIC + b"not a report")
            before = binary.read_bytes()
            with self.assertRaises(SystemExit) as ctx:
                gate.report_path(str(binary), "score")
            self.assertIn("not a JSON report", str(ctx.exception))
            self.assertEqual(binary.read_bytes(), before)

    def test_a_fresh_report_path_is_accepted(self):
        """The control: without this arm the guard would pass by refusing everything."""
        with tempfile.TemporaryDirectory() as d:
            fresh = pathlib.Path(d) / "report.json"
            self.assertEqual(gate.report_path(str(fresh), "score"), fresh)
            existing = pathlib.Path(d) / "old.json"
            existing.write_text('{"backend": "numpy"}', encoding="utf-8")
            self.assertEqual(gate.report_path(str(existing), "score"), existing)
            # `compare` reads the same argument, so it must not be held to the output rule
            corpus = pathlib.Path(d) / "boards.npz"
            corpus.write_bytes(ZIP_MAGIC + b"x")
            self.assertEqual(gate.report_path(str(corpus), "compare"), corpus)


if __name__ == "__main__":
    unittest.main(verbosity=2)
