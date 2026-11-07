"""Data-floor tests for solver-plan distillation."""
from types import SimpleNamespace
import unittest

import numpy as np

from combat import grid_dataset


def plans(scores):
    return [SimpleNamespace(
        actions=[SimpleNamespace(kind="cast", target_cell=10 + index, ap_cost=2, mp_cost=1, moved=0)],
        expected_damage=score * 10, expected_healing=0, kills=[], remaining_ap=4,
        bucket="damage", score=score, notes=[])
        for index, score in enumerate(scores)]


class CandidateOrderTests(unittest.TestCase):
    def board(self):
        fighter = SimpleNamespace(alive=True, team="player", id=1)
        return SimpleNamespace(fighters=[fighter])

    def test_score_sorted_candidates_are_stably_permuted(self):
        source = plans([90, 80, 70, 60])
        labels, orders = [], []
        for key in range(30):
            _rows, mask, scores, label, _notes, permuted, skip = grid_dataset._plans(
                self.board(), plans=source, permutation_key=str(key))
            self.assertEqual(skip, "")
            self.assertTrue(permuted)
            self.assertTrue(mask[:4].all() and not mask[4:].any())
            self.assertEqual(label, int(np.argmax(scores[:4])))
            labels.append(label)
            orders.append(tuple(scores[:4].tolist()))
        self.assertGreater(len(set(labels)), 2, labels)
        self.assertGreater(len(set(orders)), 2, orders)

    def test_plan_features_follow_their_scores(self):
        rows, _mask, scores, label, _notes, _permuted, skip = grid_dataset._plans(
            self.board(), plans=plans([90, 10]), permutation_key="ordered")
        self.assertEqual(skip, "")
        self.assertEqual(sorted(scores[:2].tolist()), [10.0, 90.0])
        best = rows[label]
        self.assertAlmostEqual(float(best[0]), 0.9, places=5)
        paired = sorted(zip(np.asarray(scores[:2]).tolist(), (rows[:2, 0] * 100).tolist()))
        self.assertEqual([(round(s), round(f)) for s, f in paired], [(10, 10), (90, 90)])

    def test_single_action_candidate_is_not_permuted(self):
        result = grid_dataset._plans(self.board(), plans=plans([90]), permutation_key="single")
        self.assertEqual(len(result), 7)
        self.assertFalse(result[5])
        self.assertEqual(result[6], "pass_only")

    def test_tied_best_candidate_is_excluded_not_randomly_labelled(self):
        result = grid_dataset._plans(self.board(), plans=plans([90, 90, 10]), permutation_key="tie")
        self.assertEqual(result[6], "tied_top_plan")
        self.assertEqual(result[0].shape[0], 0)


if __name__ == "__main__":
    unittest.main()
