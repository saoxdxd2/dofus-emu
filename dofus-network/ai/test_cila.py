"""The CILA penalty: the target must be untouched, and only confusion is penalised.

This loss was tried against the val split and rejected (`models/cila.candidate.json`),
but the code stays because the negative result is only trustworthy if the
implementation was faithful. These tests pin the two properties that make it CILA
rather than an ordinary class-weighted loss: a correctly-classified row gets no
adjustment at all, and the adjustment is scaled by the *target's* inverse frequency --
which is exactly why it could not help a head-vs-head confusion pair.
"""

from __future__ import annotations

import unittest

import numpy as np

from ai.dedicated_policy import cila_adjust, inverse_frequency

try:
    import torch
except ImportError:  # torch is deliberately not in the shipped requirements
    torch = None


@unittest.skipIf(torch is None, "torch is not installed")
class CilaTest(unittest.TestCase):
    def setUp(self):
        # 90 rows of class 0, 10 of class 1: the weights must differ by 9x before
        # normalisation, and normalisation must keep their mean at 1.
        self.inv = torch.from_numpy(inverse_frequency(np.array([0] * 90 + [1] * 10), 2))

    def test_inverse_frequency_is_mean_normalised(self):
        self.assertAlmostEqual(float(self.inv.mean()), 1.0, places=6)
        self.assertAlmostEqual(float(self.inv[0]), 0.2, places=6)
        self.assertAlmostEqual(float(self.inv[1]), 1.8, places=6)

    def test_correct_prediction_is_not_adjusted(self):
        """No confusion, no penalty -- otherwise this is just class weighting."""
        z = torch.tensor([[3.0, 1.0, 0.0]])
        delta = cila_adjust(z, torch.tensor([0]), self.inv) - z
        self.assertEqual(delta.abs().max().item(), 0.0)

    def test_target_logit_never_moves(self):
        z = torch.tensor([[1.0, 3.0, 0.0]])
        delta = cila_adjust(z, torch.tensor([0]), self.inv) - z
        self.assertEqual(float(delta[0, 0]), 0.0)

    def test_only_classes_above_the_target_are_penalised(self):
        z = torch.tensor([[1.0, 3.0, 0.0]])
        delta = cila_adjust(z, torch.tensor([0]), self.inv) - z
        self.assertGreater(float(delta[0, 1]), 0.0)
        self.assertEqual(float(delta[0, 2]), 0.0)

    def test_penalty_scales_with_how_far_above_the_target(self):
        near = cila_adjust(torch.tensor([[1.0, 1.5, 0.0]]), torch.tensor([0]), self.inv)
        far = cila_adjust(torch.tensor([[1.0, 5.0, 0.0]]), torch.tensor([0]), self.inv)
        self.assertGreater(float(far[0, 1]), float(near[0, 1]))

    def test_bound_is_respected(self):
        # ratio = (400 - -100) / (0 - -100) = 5, so the unclipped penalty would be 4;
        # the bound of 2 is what it is clipped to. Times the frequent target's 0.2.
        z = torch.tensor([[0.0, 400.0, -100.0]])
        delta = cila_adjust(z, torch.tensor([0]), self.inv, bound=2.0) - z
        self.assertAlmostEqual(float(delta[0, 1]), 2.0 * 0.2, places=4)

    def test_a_rare_target_gets_a_stronger_penalty_than_a_frequent_one(self):
        """The property that decided the outcome: weight is the target's, not the rival's.

        Both rows have the same shape -- a rival exactly 1.0 above the target -- and
        differ only in which class is the target. The penalty on the rival is then the
        same ratio times a different inverse frequency, so it is 9x larger when the
        target is the rare class. This is why CILA could not help a head-vs-head pair:
        for a frequent target the penalty is at its weakest.
        """
        as_frequent = cila_adjust(torch.tensor([[1.0, 2.0, 0.0]]), torch.tensor([0]), self.inv)
        as_rare = cila_adjust(torch.tensor([[2.0, 1.0, 0.0]]), torch.tensor([1]), self.inv)
        pen_frequent = float(as_frequent[0, 1] - 2.0)   # rival is class 1
        pen_rare = float(as_rare[0, 0] - 2.0)           # rival is class 0
        self.assertAlmostEqual(pen_frequent, 0.2, places=5)
        self.assertAlmostEqual(pen_rare, 1.8, places=5)
        self.assertGreater(pen_rare, pen_frequent)


if __name__ == "__main__":
    unittest.main()
