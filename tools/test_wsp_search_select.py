"""The search instrument's control arm, pinned by the bug it replaced.

`wsp_search_value.py` compares a model-based search against the plain argmax head, and the whole
comparison rests on a third arm that must be able to lose. The first control written for it permuted
the candidate axis of the value table and took the argmax of the permuted row. That is the same
column the unpermuted argmax picks, on every board, because a permutation is a bijection over the
same set of columns -- so the "control" was the treatment, and the two arms landing within 0.4 pt
of each other (which is what they did, at 0.2227 and 0.2188 on 256 boards) was not a finding about
values at all. Only the tie-break differed.

These arms pin the replacement and the trap:

* `choose_random_legal` never returns an illegal column, is roughly uniform over the legal ones,
  and disagrees with the value argmax most of the time -- so it can lose, which is what makes the
  search arm's win a measurement.
* the permutation trick is asserted to be a no-op on argmax, so the shape of the mistake is on the
  record in code that runs.
* the row with nothing legal is named: it returns column 0, which the caller only reaches on boards
  it has already excluded, and an arm that quietly acted on an illegal column would look like
  foresight instead of a broken mask.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wsp_search_value import choose_random_legal  # noqa: E402


class ControlArmTests(unittest.TestCase):
    def test_it_never_picks_an_illegal_column(self):
        rng = np.random.default_rng(0)
        legal = np.array([[True, False, True, False, False],
                          [False, False, False, False, True],
                          [True, True, True, True, True],
                          [False, True, False, True, False]])
        for _ in range(200):
            pick = choose_random_legal(legal, rng)
            self.assertTrue(all(legal[i, pick[i]] for i in range(len(pick))),
                            "the control acted on a column the mask refused: %s" % pick)

    def test_it_is_uniform_over_the_legal_columns(self):
        legal = np.tile(np.array([True, True, True, False, False]), (4000, 1))
        rng = np.random.default_rng(3)
        counts = np.bincount(choose_random_legal(legal, rng), minlength=5)[:5]
        for col in (0, 1, 2):
            self.assertTrue(1100 < counts[col] < 1550, str(counts))
        self.assertEqual(int(counts[3] + counts[4]), 0, str(counts))

    def test_it_disagrees_with_the_value_argmax(self):
        # Non-vacuity: a control that agreed with the search would make `values_buy` empty, and the
        # instrument would report a difference that was not a difference.
        rng = np.random.default_rng(11)
        scores = np.array([[0.0, 1.0, 0.5, -1.0, 1.0]] * 5000)
        legal = np.array([[True, True, True, False, True]] * 5000)
        agree = float((scores.argmax(axis=1) == choose_random_legal(legal, rng)).mean())
        self.assertLess(agree, 0.4, "the control agrees with the treatment on %.1f%% of boards"
                        % (100 * agree))

    def test_a_permutation_control_would_have_been_the_treatment(self):
        # The bug, expressed as an assertion: permuting the columns and un-permuting the argmax
        # returns the same column, so the old control could not have disagreed. If someone writes
        # that arm again, this is the arm that says why it is empty.
        rng = np.random.default_rng(7)
        scores = rng.random((400, 5))
        perm = rng.permutation(5)
        back = perm[np.argmax(scores[:, perm], axis=1)]
        np.testing.assert_array_equal(back, scores.argmax(axis=1))

    def test_a_row_with_nothing_legal_falls_to_column_zero(self):
        # Named, not hidden: `play` only reads this arm's pick on boards it has already established
        # the player can act on, so the fallback never reaches a decision. It is pinned here so a
        # future caller cannot mistake the fallback for a legal action.
        legal = np.array([[False, False, False, False, True],
                          [False, False, False, False, False]])
        pick = choose_random_legal(legal, np.random.default_rng(2))
        self.assertEqual(int(pick[1]), 0)
        self.assertEqual(int(pick[0]), 4)


if __name__ == "__main__":
    unittest.main()
