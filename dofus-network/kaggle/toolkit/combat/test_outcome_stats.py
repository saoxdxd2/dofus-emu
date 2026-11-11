"""The arithmetic behind an honest win rate, checked against known values.

A Wilson interval is easy to get subtly wrong, and a wrong interval is worse
than none: it is the part of the report that looks authoritative. The reference
values below are computed by hand from the formula in the docstring, not copied
from this implementation.
"""

from __future__ import annotations

import unittest

from combat.outcome_stats import (Tally, brier, brier_skill_score,
                                  calibration_table, wilson_interval)


class TestWilson(unittest.TestCase):
    def test_zero_wins_in_twenty_still_has_an_upper_bound(self):
        """The normal interval returns [0, 0] here, which claims certainty from
        twenty games. Wilson does not."""
        low, high = wilson_interval(0, 20)
        self.assertAlmostEqual(low, 0.0, places=6)
        self.assertAlmostEqual(high, 0.1611, places=4)

    def test_one_win_in_one_game_is_not_certainty(self):
        low, high = wilson_interval(1, 1)
        self.assertAlmostEqual(low, 0.2065, places=4)
        self.assertAlmostEqual(high, 1.0, places=6)

    def test_the_interval_narrows_as_the_sample_grows(self):
        widths = []
        for n in (10, 100, 1000):
            low, high = wilson_interval(n // 2, n)
            widths.append(high - low)
        self.assertGreater(widths[0], widths[1])
        self.assertGreater(widths[1], widths[2])

    def test_the_interval_contains_the_point_estimate(self):
        for wins, total in ((1, 43), (17, 43), (42, 43), (0, 5)):
            low, high = wilson_interval(wins, total)
            self.assertLessEqual(low, wins / total + 1e-9)
            self.assertGreaterEqual(high, wins / total - 1e-9)

    def test_no_games_is_the_whole_range(self):
        self.assertEqual(wilson_interval(0, 0), (0.0, 1.0))


class TestTally(unittest.TestCase):
    def test_draws_are_not_half_wins(self):
        """The recorded fight head: 1 win, 157 draws, 42 losses."""
        tally = Tally(wins=1, draws=157, losses=42)
        self.assertEqual(tally.games, 200)
        self.assertEqual(tally.decided, 43)
        self.assertAlmostEqual(tally.win_rate, 1 / 43, places=6)
        self.assertAlmostEqual(tally.draw_rate, 157 / 200, places=6)
        # The score convention this replaces would have said 0.3975.
        self.assertAlmostEqual(tally.score, 0.3975, places=4)

    def test_a_policy_that_only_draws_scores_half_and_wins_nothing(self):
        tally = Tally(wins=0, draws=10, losses=0)
        self.assertAlmostEqual(tally.score, 0.5)
        self.assertIsNone(tally.win_rate, "no game was decided")

    def test_the_four_level_score_is_classified_like_the_file_does(self):
        """0.75 is a partial win, 0.25 a partial loss -- not a draw."""
        tally = Tally.from_scores([0.75, 0.5, 0.25, 0.0])
        self.assertEqual((tally.wins, tally.draws, tally.losses), (1, 1, 2))

    def test_the_summary_carries_its_own_interval(self):
        summary = Tally(wins=1, draws=157, losses=42).summary()
        self.assertEqual(summary["decided"], 43)
        low, high = summary["win_rate_ci95"]
        self.assertLess(low, summary["win_rate_decided"])
        self.assertGreater(high, summary["win_rate_decided"])


class TestProperScoring(unittest.TestCase):
    def test_a_coin_flip_has_a_brier_score_of_a_quarter(self):
        self.assertAlmostEqual(brier([0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1]), 0.25)

    def test_a_perfect_forecast_scores_zero(self):
        self.assertAlmostEqual(brier([1.0, 0.0, 1.0], [1, 0, 1]), 0.0)

    def test_the_base_rate_predictor_has_no_skill(self):
        outcomes = [1, 1, 0, 0, 1, 0]
        base = sum(outcomes) / len(outcomes)
        self.assertAlmostEqual(brier_skill_score([base] * len(outcomes), outcomes), 0.0)

    def test_a_better_than_base_rate_forecast_has_positive_skill(self):
        outcomes = [1, 1, 1, 0, 0, 0]
        self.assertGreater(brier_skill_score([0.9, 0.8, 0.7, 0.2, 0.1, 0.05], outcomes), 0.0)

    def test_mismatched_input_is_none_not_a_number(self):
        self.assertIsNone(brier([0.5], [0, 1]))
        self.assertIsNone(brier([], []))
        self.assertIsNone(brier_skill_score([0.5], []))


class TestCalibration(unittest.TestCase):
    def test_a_calibrated_forecast_sits_on_the_diagonal(self):
        """Two buckets whose outcomes average exactly to their predictions.

        Two items at 0.5 with one win, and ten at 0.9 with nine wins. A bucket
        cannot be checked against a single item, so the cases are built to have
        an exact frequency rather than an approximate one.
        """
        probabilities = [0.5, 0.5] + [0.9] * 10
        outcomes = [0, 1] + [1] * 9 + [0]
        table = calibration_table(probabilities, outcomes, bins=5)
        self.assertEqual(sum(row["n"] for row in table), 12)
        self.assertEqual(len(table), 2)
        for row in table:
            self.assertAlmostEqual(row["predicted"], row["observed"], places=4)

    def test_an_overconfident_forecast_does_not(self):
        table = calibration_table([0.9, 0.9, 0.9, 0.9], [0, 0, 1, 0], bins=5)
        self.assertEqual(len(table), 1)
        self.assertAlmostEqual(table[0]["predicted"], 0.9, places=4)
        self.assertAlmostEqual(table[0]["observed"], 0.25, places=4)


class TestLiveOutcomeRows(unittest.TestCase):
    """`fight_outcomes.jsonl` has a fourth bucket, and it is not a draw.

    The live log records `won: None` with `status: session_end` for a fight the
    session walked away from. Treating that as a draw inflates the draw rate with
    fights that may have been won; treating it as a win is what the old xp-delta
    verdict did, and that is how a loss was counted as a victory.
    """

    def rows(self, *pairs):
        return [{"kind": "fight_outcome", "won": won, "status": status}
                for won, status in pairs]

    def test_the_four_buckets_are_kept_apart(self):
        tally = Tally.from_outcome_rows(self.rows((True, None), (True, None),
                                                  (False, None), (None, "session_end")))
        self.assertEqual((tally.wins, tally.losses, tally.draws, tally.unresolved),
                         (2, 1, 0, 1))

    def test_an_open_fight_is_not_a_game(self):
        tally = Tally.from_outcome_rows(self.rows((True, None), (None, "session_end")))
        self.assertEqual(tally.games, 1)
        self.assertEqual(tally.decided, 1)
        self.assertEqual(tally.summary()["unresolved"], 1)
        self.assertEqual(tally.win_rate, 1.0)

    def test_start_rows_do_not_double_count(self):
        rows = [{"kind": "fight_start", "won": None},
                {"kind": "fight_outcome", "won": True}]
        self.assertEqual(Tally.from_outcome_rows(rows).games, 1)

    def test_a_bare_ratio_is_not_reported(self):
        """13 of 13 must carry its interval, because 13/13 is not 100%."""
        tally = Tally.from_outcome_rows(self.rows(*[(True, None)] * 13))
        line = tally.line()
        self.assertIn("13/13", line)
        self.assertIn("95% CI", line)
        self.assertIn("77.2", line)      # the Wilson lower bound
        self.assertIn("100.0", line)     # and the point estimate is still shown

    def test_an_undecided_log_says_so_instead_of_printing_zero_percent(self):
        tally = Tally.from_outcome_rows(self.rows((None, "session_end")))
        self.assertIsNone(tally.win_rate)
        self.assertIn("no decided fight", tally.line())


if __name__ == "__main__":
    unittest.main()
