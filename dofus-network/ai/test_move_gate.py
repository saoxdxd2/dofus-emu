"""The live move head may only be promoted on out-of-time evidence that is calibrated.

Measured 2026-10-31 on the cleaned corpus (`data/transitions_clean`): the temporal
test fold holds 123 actions, a 75.6% move base rate and the head scored **AUC 0.563,
ECE 0.290** -- above chance by 0.063 but badly overconfident, on too few rows to tell
a real signal from a lucky fold. The gate below refuses that result on all three
counts and names each one.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.train_move_head import (PROMOTE_MAX_ECE, PROMOTE_MIN_AUC,
                                PROMOTE_MIN_ROWS, promotion_verdict)
from ai.train_move_head_gated import aggregate


def metrics(rows=300, auc=0.65, ece=0.08):
    return {"rows": rows, "auc_moved": auc, "ece_moved": ece,
            "base_rate_moved": 0.3}


class MoveGateTests(unittest.TestCase):

    def test_a_calibrated_signal_on_enough_rows_promotes(self):
        verdict = promotion_verdict(metrics())
        self.assertTrue(verdict["promoted"])
        self.assertEqual(verdict["reasons"], [])

    def test_too_few_test_rows_is_named(self):
        verdict = promotion_verdict(metrics(rows=PROMOTE_MIN_ROWS - 1))
        self.assertFalse(verdict["promoted"])
        self.assertTrue(any("test_rows" in reason for reason in verdict["reasons"]),
                        verdict["reasons"])

    def test_a_chance_auc_is_named(self):
        verdict = promotion_verdict(metrics(auc=PROMOTE_MIN_AUC - 0.01))
        self.assertFalse(verdict["promoted"])
        self.assertTrue(any("auc_moved" in reason for reason in verdict["reasons"]),
                        verdict["reasons"])

    def test_miscalibration_is_named(self):
        verdict = promotion_verdict(metrics(ece=PROMOTE_MAX_ECE + 0.01))
        self.assertFalse(verdict["promoted"])
        self.assertTrue(any("ece_moved" in reason for reason in verdict["reasons"]),
                        verdict["reasons"])

    def test_a_missing_metric_never_promotes(self):
        broken = metrics()
        broken["auc_moved"] = None
        broken["ece_moved"] = None
        verdict = promotion_verdict(broken)
        self.assertFalse(verdict["promoted"])
        self.assertEqual(len(verdict["reasons"]), 2)


def seeds(aucs, eces, rows=300):
    return [{"seed": index, "test": {"rows": rows, "auc_moved": auc,
                                     "ece_moved": ece}}
            for index, (auc, ece) in enumerate(zip(aucs, eces))]


class AggregateTests(unittest.TestCase):
    """The multi-seed rule: median AUC, worst calibration, shared test fold."""

    def test_three_agreeing_seeds_promote_and_name_the_median(self):
        verdict = aggregate(seeds([0.61, 0.70, 0.65], [0.08, 0.09, 0.07]))
        self.assertTrue(verdict["promoted"], verdict["reasons"])
        self.assertEqual(verdict["median_auc"], 0.65)
        self.assertEqual(verdict["worst_ece"], 0.09)
        self.assertEqual(verdict["chosen_seed"], 2)
        self.assertEqual(verdict["seeds"], 3)

    def test_one_miscalibrated_seed_refuses_every_seed(self):
        verdict = aggregate(seeds([0.61, 0.70, 0.65], [0.08, 0.29, 0.07]))
        self.assertFalse(verdict["promoted"])
        self.assertTrue(any("worst ece_moved" in reason
                            for reason in verdict["reasons"]), verdict["reasons"])

    def test_one_lucky_high_seed_does_not_carry_the_median(self):
        verdict = aggregate(seeds([0.51, 0.55, 0.90], [0.08, 0.09, 0.07]))
        self.assertFalse(verdict["promoted"])
        self.assertTrue(any("median auc_moved" in reason
                            for reason in verdict["reasons"]), verdict["reasons"])

    def test_too_few_test_rows_refuses(self):
        verdict = aggregate(seeds([0.70, 0.71, 0.72], [0.08, 0.08, 0.08],
                                  rows=PROMOTE_MIN_ROWS - 1))
        self.assertFalse(verdict["promoted"])
        self.assertTrue(any("test_rows" in reason for reason in verdict["reasons"]))

    def test_no_seed_metrics_never_promotes(self):
        verdict = aggregate([])
        self.assertFalse(verdict["promoted"])
        self.assertEqual(verdict["seeds"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
