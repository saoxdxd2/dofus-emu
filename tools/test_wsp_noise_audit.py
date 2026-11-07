"""The noise audit must be able to fail: it has to find a dead column when one exists.

A tool that reports "nothing is inert" on every input is not measuring anything, so the primary arm
plants a column that provably cannot matter and requires the audit to name it -- and the secondary
arm breaks the cache/checkpoint pairing the audit's exactness depends on.
"""
import contextlib
import hashlib
import io
import json
import numpy as np
import os
import pathlib
import pickle
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import wsp_noise_audit as audit  # noqa: E402


def make_case(root, dead_column=None, clamp_column=None):
    """Write a toy checkpoint and the raw-outcome cache that belongs to it.

    The fixture is diagonal on purpose: hidden is 3x the identity and weight starts as the identity,
    so column c wins on row c and *every* column is load-bearing. That makes the two degradations
    unambiguous -- `dead_column` removes the weights of a column that is still close enough to be
    in contention (the class a repair could actually reach), and `clamp_column` keeps live weights
    but pushes the logit far behind (the class that looks inert and is really a clamp).
    """
    hidden = (3.0 * np.eye(4)).astype(np.float32)
    cols = 4
    weight = np.eye(cols, dtype=np.float32)
    bias = np.zeros(cols, dtype=np.float32)
    if dead_column is not None:
        weight[:, dead_column] = 0.0
    if clamp_column is not None:
        bias[clamp_column] = -500.0
    mask = np.ones((len(hidden), cols), dtype=bool)
    logits = hidden @ weight.astype(np.float64) + bias.astype(np.float64)
    pick = np.argmax(np.where(mask, logits, -1e9), axis=1).astype(np.int32)

    checkpoint = root / "toy.pkl"
    checkpoint.write_bytes(pickle.dumps({"params": {"spell": (weight, bias)}}))
    cache = root / "toy_cache.npz"
    np.savez_compressed(
        cache, hidden=hidden, head_mask=mask, head_pick=pick,
        head=np.asarray("spell"), cache_kind=np.asarray("raw_pvm_outcomes_v1"),
        checkpoint_path=np.asarray(str(checkpoint)),
        checkpoint_sha256=np.asarray(hashlib.sha256(checkpoint.read_bytes()).hexdigest()))
    return cache, pick


class NoiseAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)

    def run_audit(self, cache):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(audit.main(["--cache", str(cache), "--head", "spell"]), 0)
        return json.loads(out.getvalue())

    def column(self, report, index):
        return [c for c in report["columns"] if c["column"] == index][0]

    def test_the_untouched_fixture_reports_every_column_load_bearing(self):
        cache, _pick = make_case(self.root)
        report = self.run_audit(cache)
        self.assertEqual(report["weights_inert_columns"], [])
        self.assertEqual(report["out_of_contention_columns"], [])
        self.assertEqual(report["coord_block_flips"]["blocks_with_no_effect"], 0)

    def test_a_column_with_no_weight_but_in_contention_is_named(self):
        cache, _pick = make_case(self.root, dead_column=2)
        report = self.run_audit(cache)
        dead = self.column(report, 2)
        self.assertTrue(dead["weights_inert"])
        self.assertIn(2, report["weights_inert_columns"])
        self.assertNotIn(2, report["out_of_contention_columns"])
        self.assertEqual(dead["chosen_count"], 0)
        # the other columns stay load-bearing, so the report is discriminating, not blanket
        self.assertNotIn(0, report["weights_inert_columns"])
        self.assertNotIn(1, report["weights_inert_columns"])

    def test_a_hard_clamp_is_named_as_clamp_not_as_noise(self):
        # Live weights, logit pushed 500 behind: zeroing and negating both change nothing, so the
        # weight-level probe alone would call it dead. The margin probe says it is out of reach,
        # which is a different fact and the reason the two are reported separately.
        cache, _pick = make_case(self.root, clamp_column=3)
        report = self.run_audit(cache)
        clamped = self.column(report, 3)
        self.assertTrue(clamped["weights_inert"])
        self.assertIn(3, report["out_of_contention_columns"])
        self.assertGreater(clamped["median_margin_to_chosen"], 100.0)
        self.assertEqual(clamped["chosen_count"], 0)

    def test_audit_refuses_a_cache_that_does_not_match_its_checkpoint(self):
        cache, pick = make_case(self.root)
        with np.load(cache, allow_pickle=False) as z:
            arrays = {k: z[k] for k in z.files}
        arrays["head_pick"] = (arrays["head_pick"] + 1) % 4
        np.savez_compressed(cache, **arrays)
        with self.assertRaises(SystemExit) as caught:
            self.run_audit(cache)
        self.assertIn("argmax", str(caught.exception))

    def test_audit_refuses_the_wrong_cache_kind_and_head(self):
        cache, _ = make_case(self.root)
        with np.load(cache, allow_pickle=False) as z:
            arrays = {k: z[k] for k in z.files}
        arrays["cache_kind"] = np.asarray("beam_imitation_v1")
        np.savez_compressed(cache, **arrays)
        with self.assertRaises(SystemExit) as caught:
            self.run_audit(cache)
        self.assertIn("raw outcome", str(caught.exception).lower())

        cache2, _ = make_case(self.root)
        with np.load(cache2, allow_pickle=False) as z:
            arrays = {k: z[k] for k in z.files}
        arrays["cache_kind"] = np.asarray("raw_pvm_outcomes_v1")
        arrays["head"] = np.asarray("move")
        np.savez_compressed(cache2, **arrays)
        with self.assertRaises(SystemExit) as caught:
            self.run_audit(cache2)
        self.assertIn("move", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
