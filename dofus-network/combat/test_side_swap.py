"""Side-swapped cross-play must report seat bias instead of inventing a winner."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from combat.side_swap import (binomial_two_sided, play_side_swapped,
                              score_games)  # noqa: E402


def synthetic(wins_as_seat1: int, wins_as_seat2: int, n: int = 10):
    games = []
    for index in range(n):
        a_wins = index < wins_as_seat1
        games.append({"seed": index, "half": 0, "a_seat": 1,
                      "winner": "a" if a_wins else "b",
                      "winner_seat": 1 if a_wins else 2, "turns": 6})
        a_wins = index < wins_as_seat2
        games.append({"seed": index, "half": 1, "a_seat": 2,
                      "winner": "a" if a_wins else "b",
                      "winner_seat": 2 if a_wins else 1, "turns": 6})
    return games


class ScoreTests(unittest.TestCase):

    def test_a_policy_that_wins_from_both_seats_is_better(self):
        score = score_games(synthetic(9, 9))
        self.assertEqual(score["verdict"], "a_better")
        self.assertLess(score["p_value"], 0.05)
        self.assertGreater(score["a_seat1_win"], 0.5)
        self.assertGreater(score["a_seat2_win"], 0.5)

    def test_one_seat_winning_almost_everything_is_not_a_policy_result(self):
        score = score_games(synthetic(10, 0))
        self.assertEqual(score["verdict"], "seat_dominated")

    def test_an_even_split_has_no_measurable_difference(self):
        score = score_games(synthetic(5, 5))
        self.assertEqual(score["verdict"], "no_measurable_difference")

    def test_an_edge_from_one_seat_only_is_named_as_such(self):
        score = score_games(synthetic(19, 9, n=20))
        self.assertEqual(score["verdict"], "seat_dependent_edge")

    def test_an_equal_sign_test_returns_one(self):
        self.assertEqual(binomial_two_sided(10, 10), 1.0)
        self.assertEqual(binomial_two_sided(0, 0), 1.0)


class SwapTests(unittest.TestCase):

    def test_every_board_is_played_twice_with_the_seats_swapped(self):
        report = play_side_swapped("solver", "solver", games=2, seed=7)
        self.assertEqual(len(report["games"]), 4)
        seats = {}
        for game in report["games"]:
            seats.setdefault(game["seed"], []).append(game["a_seat"])
        self.assertEqual(sorted(seats), [7, 8])
        for pair in seats.values():
            self.assertEqual(sorted(pair), [1, 2])

    def test_the_same_seeds_reproduce_the_same_games(self):
        first = play_side_swapped("solver", "solver", games=1, seed=7)["games"]
        second = play_side_swapped("solver", "solver", games=1, seed=7)["games"]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main(verbosity=1)
