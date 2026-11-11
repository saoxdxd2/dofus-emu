"""Side-swapped cross-play must report seat bias instead of inventing a winner."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from combat.side_swap import (binomial_two_sided, damage_split, efficiency,
                              play_side_swapped, score_games,
                              value_head_spread)  # noqa: E402


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


class _Fighter:
    def __init__(self, ident, hp, max_hp):
        self.id = ident
        self.hp = hp
        self.max_hp = max_hp


class _Final:
    def __init__(self, first, second):
        self.fighters = (first, second)


class DamageSplitTests(unittest.TestCase):

    def test_damage_is_the_opponent_s_hp_loss(self):
        # seat 2 lost 900 of 4825, so the policy in seat 1 dealt 900.
        final = _Final(_Fighter(1, 4825, 4825), _Fighter(2, 3925, 4825))
        self.assertEqual(damage_split(final, a_seat=1), (900.0, 0.0))
        self.assertEqual(damage_split(final, a_seat=2), (0.0, 900.0))

    def test_a_dead_fighter_caps_damage_at_its_maximum(self):
        # Fighter 1 is dead and fighter 2 untouched, so the seat-2 policy did all
        # of the damage and the seat-1 policy did none of it.
        final = _Final(_Fighter(1, 0, 4825), _Fighter(2, 4825, 4825))
        self.assertEqual(damage_split(final, a_seat=2), (4825.0, 0.0))
        self.assertEqual(damage_split(final, a_seat=1), (0.0, 4825.0))


class EfficiencyTests(unittest.TestCase):

    def games(self, damage_a, damage_b, turns=10, boards=8):
        out = []
        for index in range(boards):
            for half, seat in ((0, 1), (1, 2)):
                out.append({"seed": index, "half": half, "a_seat": seat,
                            "winner": "a", "winner_seat": seat, "turns": turns,
                            "damage_a": damage_a, "damage_b": damage_b})
        return out

    def test_a_policy_dealing_more_damage_per_turn_wins_the_paired_test(self):
        eff = efficiency(self.games(800, 500))
        self.assertAlmostEqual(eff["a_damage_per_turn"], 80.0)
        self.assertAlmostEqual(eff["b_damage_per_turn"], 50.0)
        self.assertGreater(eff["diff"], 0)
        self.assertEqual(eff["boards_favouring_a"], 8)
        self.assertEqual(eff["boards_favouring_b"], 0)
        self.assertLess(eff["p_value"], 0.05)

    def test_identical_damage_is_not_a_difference(self):
        eff = efficiency(self.games(700, 700))
        self.assertEqual(eff["diff"], 0.0)
        self.assertEqual(eff["p_value"], 1.0)

    def test_no_games_reports_nothing_rather_than_zero(self):
        eff = efficiency([])
        self.assertEqual(eff["boards"], 0)
        self.assertIsNone(eff["diff"])


class _ConstantHead:
    def predict_proba(self, rows):
        return [[0.5, 0.5] for _ in rows]


class _RampHead:
    def predict_proba(self, rows):
        return [[0.5, index / max(1, len(rows))]
                for index in range(len(rows))]


class ValueHeadSpreadTests(unittest.TestCase):

    def test_a_constant_head_is_reported_as_inert(self):
        # A constant head reproduces solver_policy exactly, because plan_scores
        # adds the solver's score as a 1e-4 tiebreak. It must not read as a tie.
        from combat.battle_solver import BattleSolver
        from combat.duel import load_map
        from combat.normal_build_duel import DEFAULT_MAP

        spread = value_head_spread(_ConstantHead(),
                                   BattleSolver(beam_width=16, max_actions=5),
                                   load_map(DEFAULT_MAP), "dana_ouro", boards=2)
        self.assertEqual(spread, 0.0)

    def test_a_head_that_ranks_plans_is_not_inert(self):
        from combat.battle_solver import BattleSolver
        from combat.duel import load_map
        from combat.normal_build_duel import DEFAULT_MAP

        spread = value_head_spread(_RampHead(),
                                   BattleSolver(beam_width=16, max_actions=5),
                                   load_map(DEFAULT_MAP), "dana_ouro", boards=2)
        self.assertIsNotNone(spread)
        self.assertGreater(spread, 0.0)


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
