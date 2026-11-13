"""Tests for split-conformal abstention.

The important one is `CoverageTests`: it checks the actual distribution-free guarantee
on a deliberately miscalibrated model, because that is the only claim worth making. The
rest pin the exact quantile rank, which is where an off-by-one silently voids the
promise at small calibration sizes.
"""

from __future__ import annotations

import json
import unittest

import numpy as np

from ai.conformal import (
    ConformalCalibration,
    accept,
    calibrate,
    gate,
    nonconformity,
    prediction_set,
    quantile_index,
    report,
)


def one_hot_row(label: int, prob: float, classes: int = 3) -> list:
    """A row where the true class has ``prob`` and the rest share the remainder."""
    rest = (1.0 - prob) / (classes - 1)
    return [prob if j == label else rest for j in range(classes)]


class QuantileRankTests(unittest.TestCase):
    def test_the_rank_is_ceil_n_plus_one_times_one_minus_alpha(self):
        self.assertEqual(quantile_index(9, 0.1), 9)      # ceil(10 * 0.9) = 9
        self.assertEqual(quantile_index(10, 0.1), 10)    # ceil(11 * 0.9) = 10
        self.assertEqual(quantile_index(100, 0.1), 91)   # ceil(101 * 0.9) = 91
        self.assertEqual(quantile_index(5, 0.2), 5)      # ceil(6 * 0.8) = 5
        self.assertEqual(quantile_index(5, 0.4), 4)      # ceil(6 * 0.6) = 4

    def test_one_point_cannot_support_a_ninety_percent_guarantee(self):
        self.assertEqual(quantile_index(1, 0.1), 0)
        self.assertEqual(quantile_index(0, 0.1), 0)

    def test_the_naive_rank_would_be_wrong_here(self):
        """ceil(n(1-a)) is the classic off-by-one; on n=9 it gives 9 vs 9 but on
        n=10, a=0.1 it gives 9 instead of 10, which voids the guarantee."""
        n, alpha = 10, 0.1
        self.assertEqual(quantile_index(n, alpha), 10)
        self.assertEqual(int(np.ceil(n * (1.0 - alpha))), 9)


class NonconformityTests(unittest.TestCase):
    def test_the_score_is_one_minus_the_probability_of_the_truth(self):
        probs = np.array([[0.7, 0.2, 0.1], [0.1, 0.1, 0.8]])
        scores = nonconformity(probs, [0, 2])
        self.assertAlmostEqual(float(scores[0]), 0.3, places=9)
        self.assertAlmostEqual(float(scores[1]), 0.2, places=9)

    def test_a_label_outside_the_columns_is_refused(self):
        with self.assertRaises(ValueError):
            nonconformity(np.array([[0.5, 0.5]]), [2])

    def test_a_row_count_mismatch_is_refused(self):
        with self.assertRaises(ValueError):
            nonconformity(np.array([[0.5, 0.5], [0.5, 0.5]]), [0])


class ExactQuantileTests(unittest.TestCase):
    def test_the_quantile_is_the_exact_sorted_score_at_the_rank(self):
        # True-class probabilities 0.9, 0.8, 0.7, 0.6, 0.5 -> scores .1,.2,.3,.4,.5
        probs = np.array([one_hot_row(0, p) for p in (0.9, 0.8, 0.7, 0.6, 0.5)])
        labels = [0, 0, 0, 0, 0]

        loose = calibrate(probs, labels, alpha=0.4)      # rank 4 -> 0.4
        self.assertEqual(loose.rank, 4)
        self.assertAlmostEqual(loose.q_hat, 0.4, places=9)
        self.assertAlmostEqual(loose.probability_floor, 0.6, places=9)

        tight = calibrate(probs, labels, alpha=0.2)      # rank 5 -> 0.5
        self.assertEqual(tight.rank, 5)
        self.assertAlmostEqual(tight.q_hat, 0.5, places=9)
        self.assertAlmostEqual(tight.probability_floor, 0.5, places=9)

    def test_too_few_points_puts_every_class_in_the_set(self):
        probs = np.array([one_hot_row(0, 0.99)])
        calibration = calibrate(probs, [0], alpha=0.1)
        self.assertEqual(calibration.rank, 0)
        self.assertEqual(calibration.q_hat, 1.0)
        self.assertEqual(len(prediction_set(probs[0], calibration)), 3)
        self.assertFalse(accept(probs[0], calibration)[0])

    def test_an_impossible_alpha_is_refused(self):
        with self.assertRaises(ValueError):
            calibrate(np.array([[1.0, 0.0]]), [0], alpha=0.0)
        with self.assertRaises(ValueError):
            calibrate(np.array([[1.0, 0.0]]), [0], alpha=1.0)


class SetTests(unittest.TestCase):
    """Calibrated so the floor is exactly 0.3, which makes the three cases constructible.

    Nine calibration points with true-class probabilities 0.9 down to 0.1 give scores
    0.1..0.9; alpha=0.3 puts the rank at ceil(10 * 0.7) = 7, so q_hat = 0.7 and the
    probability floor is 0.3.
    """

    def setUp(self) -> None:
        probs = np.array([one_hot_row(0, p) for p in
                          (0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1)])
        self.calibration = calibrate(probs, [0] * 9, alpha=0.3)
        self.assertAlmostEqual(self.calibration.probability_floor, 0.3, places=9)

    def test_a_confident_row_is_a_singleton_and_is_accepted(self):
        row = np.array([0.95, 0.03, 0.02])
        self.assertEqual(prediction_set(row, self.calibration), [0])
        accepted, size = accept(row, self.calibration)
        self.assertTrue(accepted)
        self.assertEqual(size, 1)

    def test_two_classes_above_the_floor_is_ambiguous_and_abstains(self):
        row = np.array([0.62, 0.36, 0.02])
        self.assertEqual(prediction_set(row, self.calibration), [0, 1])
        accepted, size = accept(row, self.calibration)
        self.assertFalse(accepted)
        self.assertEqual(size, 2)

    def test_nothing_above_the_floor_is_an_empty_set_and_abstains(self):
        # Six classes at 1/6 cannot all clear 0.3 -- this is what an observation the
        # model has no confident opinion about looks like.
        row = np.full(6, 1.0 / 6.0)
        self.assertEqual(prediction_set(row, self.calibration), [])
        accepted, size = accept(row, self.calibration)
        self.assertFalse(accepted)
        self.assertEqual(size, 0)


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        probs = np.array([one_hot_row(0, p) for p in
                          (0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1)])
        self.calibration = calibrate(probs, [0] * 9, alpha=0.3)   # floor 0.3
        self.tools = ["farm.start", "nav.goto", "quest.advance"]

    def test_no_calibration_accepts_everything_which_is_todays_behaviour(self):
        decision = gate("nav.goto", self.tools, np.array([0.1, 0.8, 0.1]), None)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.tool, "nav.goto")
        self.assertEqual(decision.reason, "no calibration loaded")

    def test_a_singleton_set_accepts_and_names_the_class(self):
        decision = gate("farm.start", self.tools, np.array([0.95, 0.03, 0.02]),
                        self.calibration)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.tool, "farm.start")
        self.assertEqual(decision.set_members, ["farm.start"])

    def test_an_ambiguous_set_rejects_and_says_which_classes(self):
        decision = gate("farm.start", self.tools, np.array([0.62, 0.36, 0.02]),
                        self.calibration)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.set_size, 2)
        self.assertIn("nav.goto", decision.set_members)

    def test_no_tool_proposed_is_a_rejection_not_a_crash(self):
        decision = gate(None, self.tools, np.array([0.4, 0.3, 0.3]), self.calibration)
        self.assertFalse(decision.accepted)
        self.assertIsNone(decision.tool)

    def test_the_reason_is_always_human_readable(self):
        for row in (np.array([0.95, 0.03, 0.02]), np.array([0.62, 0.36, 0.02]),
                    np.full(3, 1.0 / 3.0)):
            decision = gate("farm.start", self.tools, row, self.calibration)
            self.assertTrue(decision.reason)
            self.assertIn("set", decision.reason)


class CoverageTests(unittest.TestCase):
    """The only claim that matters: P(Y in C(X)) >= 1 - alpha, distribution-free."""

    CLASSES = 6

    def miscalibrated(self, rng, n: int, temperature: float = 3.0):
        """A model that is deliberately over-confident by ``temperature``."""
        truth_probs = rng.dirichlet(np.full(self.CLASSES, 0.4), size=n)
        labels = np.array([rng.choice(self.CLASSES, p=row) for row in truth_probs])
        logits = np.log(truth_probs + 1e-12) * temperature
        logits -= logits.max(axis=1, keepdims=True)
        exp = np.exp(logits)
        probs = exp / exp.sum(axis=1, keepdims=True)
        return probs, labels

    def test_coverage_holds_on_a_model_that_is_over_confident_by_3x(self):
        rng = np.random.default_rng(20261113)
        cal_probs, cal_labels = self.miscalibrated(rng, 2000)
        calibration = calibrate(cal_probs, cal_labels, alpha=0.1)

        test_probs, test_labels = self.miscalibrated(rng, 20000)
        measured = report(calibration, test_probs, test_labels)

        self.assertTrue(measured["holds"], measured)
        self.assertGreaterEqual(measured["measured_coverage"], 0.88, measured)
        # A 3x over-confident model cannot be trusted on its own argmax: the point is
        # that the set grows to absorb the miscalibration instead of the promise breaking.
        self.assertGreater(measured["mean_set_size"], 1.0, measured)

    def test_a_smaller_alpha_buys_a_bigger_set(self):
        rng = np.random.default_rng(7)
        probs, labels = self.miscalibrated(rng, 1500)
        sizes = [calibrate(probs, labels, alpha=a).mean_set_size
                 for a in (0.4, 0.2, 0.1, 0.05)]
        self.assertEqual(sizes, sorted(sizes))

    def test_a_well_calibrated_model_needs_a_smaller_set(self):
        rng = np.random.default_rng(11)
        sharp_probs, sharp_labels = self.miscalibrated(rng, 3000, temperature=1.0)
        dull_probs, dull_labels = self.miscalibrated(rng, 3000, temperature=6.0)
        sharp = calibrate(sharp_probs, sharp_labels, alpha=0.1)
        dull = calibrate(dull_probs, dull_labels, alpha=0.1)
        self.assertLess(sharp.mean_set_size, dull.mean_set_size)

    def test_per_class_calibration_gives_a_rare_class_its_own_quantile(self):
        rng = np.random.default_rng(3)
        probs, labels = self.miscalibrated(rng, 1200)
        calibration = calibrate(probs, labels, alpha=0.1, per_class=True)
        self.assertTrue(calibration.per_class)
        # Every class that appeared has a quantile, and they are not all the same.
        self.assertEqual(set(calibration.per_class), set(int(x) for x in np.unique(labels)))
        self.assertGreater(len(set(calibration.per_class.values())), 1)


class ArtifactTests(unittest.TestCase):
    def test_a_calibration_survives_its_own_json(self):
        rng = np.random.default_rng(5)
        probs = rng.dirichlet(np.ones(4), size=200)
        labels = np.array([int(np.argmax(row)) for row in probs])
        original = calibrate(probs, labels, alpha=0.1)
        again = ConformalCalibration.from_dict(original.as_dict())
        self.assertAlmostEqual(again.q_hat, original.q_hat, places=12)
        self.assertEqual(again.alpha, original.alpha)
        self.assertEqual(again.n, original.n)

    def test_a_missing_artifact_loads_as_none_rather_than_raising(self):
        import tempfile
        from pathlib import Path

        self.assertIsNone(ConformalCalibration.load(
            Path(tempfile.mkdtemp()) / "nothing.json"))

    def test_a_corrupt_artifact_loads_as_none_rather_than_raising(self):
        import tempfile
        from pathlib import Path

        path = Path(tempfile.mkdtemp()) / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(ConformalCalibration.load(path))

    def test_save_then_load_round_trips_through_disk(self):
        import tempfile
        from pathlib import Path

        rng = np.random.default_rng(9)
        probs = rng.dirichlet(np.ones(3), size=100)
        labels = np.array([int(np.argmax(row)) for row in probs])
        original = calibrate(probs, labels, alpha=0.2)
        path = Path(tempfile.mkdtemp()) / "conformal.json"
        original.save(path)
        again = ConformalCalibration.load(path)
        self.assertIsNotNone(again)
        self.assertAlmostEqual(again.q_hat, original.q_hat, places=12)
        self.assertAlmostEqual(again.probability_floor, original.probability_floor, places=12)
        # The artifact records the method it was fitted with, so a future reader cannot
        # mistake it for a tuned threshold.
        self.assertIn("split-conformal", json.loads(path.read_text(encoding="utf-8"))["method"])


class GuardPolicyWiringTests(unittest.TestCase):
    """The gate is additive: with no calibration the head is byte-for-byte unchanged."""

    TOOLS = ["farm.start", "nav.goto", "quest.advance"]

    def setUp(self) -> None:
        from ai.guard_policy import GuardPolicy

        probs = np.array([one_hot_row(0, p) for p in
                          (0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1)])
        self.calibration = calibrate(probs, [0] * 9, alpha=0.3)   # floor 0.3
        self.policy = GuardPolicy()

    def test_without_a_calibration_the_ungated_path_is_unchanged(self):
        # `choose_from_logits` takes logits, not probabilities: it softmaxes them itself
        # after applying the legality mask. softmax([0.1, 0.8, 0.1])[1] is 0.5017.
        tool, probs = self.policy.choose_from_logits(
            np.array([[0.1, 0.8, 0.1]]), self.TOOLS)
        self.assertEqual(tool, "nav.goto")
        self.assertAlmostEqual(float(np.sum(probs)), 1.0, places=6)
        self.assertAlmostEqual(float(probs[1]), 0.5017, places=3)

    def test_without_a_calibration_the_gated_path_accepts_everything(self):
        tool, _probs, decision = self.policy.choose_gated(
            np.array([[0.1, 0.8, 0.1]]), self.TOOLS)
        self.assertEqual(tool, "nav.goto")
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reason, "no calibration loaded")

    def test_with_a_calibration_an_ambiguous_head_abstains(self):
        self.policy.set_conformal(self.calibration)
        # Two classes clear the 0.3 floor, so the gate refuses to pick.
        tool, _probs, decision = self.policy.choose_gated(
            np.array([[0.62, 0.36, 0.02]]), self.TOOLS)
        self.assertIsNone(tool)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.set_size, 2)
        self.assertEqual(self.policy.last["conformal"]["set_size"], 2)

    def test_with_a_calibration_a_confident_head_still_acts(self):
        self.policy.set_conformal(self.calibration)
        tool, _probs, decision = self.policy.choose_gated(
            np.array([[0.95, 0.03, 0.02]]), self.TOOLS)
        self.assertEqual(tool, "farm.start")
        self.assertTrue(decision.accepted)

    def test_the_gate_can_be_removed_again(self):
        self.policy.set_conformal(self.calibration)
        self.policy.set_conformal(None)
        tool, _probs, decision = self.policy.choose_gated(
            np.array([[0.62, 0.36, 0.02]]), self.TOOLS)
        self.assertIsNotNone(tool)
        self.assertTrue(decision.accepted)


if __name__ == "__main__":
    unittest.main()
