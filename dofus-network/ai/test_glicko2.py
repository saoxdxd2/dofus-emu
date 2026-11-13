"""Tests for `ai/glicko2.py`.

The first test is the published Glicko-2 worked example, verbatim. Everything else
pins the properties a release gate depends on: unplayed archetypes must become *less*
certain, more matches must become *more* certain, half-points must be accepted, and
the lower confidence bound must be the thing that decides a release.
"""

from __future__ import annotations

import unittest

from ai.glicko2 import (
    DEFAULT_RATING,
    DEFAULT_RD,
    Rating,
    expected,
    rate,
    rate_periods,
    win_rate,
)


class PublishedExampleTests(unittest.TestCase):
    def test_the_published_example(self):
        """Glickman, 'Example of the Glicko-2 system': 1464.06 / 151.52 / 0.05999."""
        out = rate(Rating(1500.0, 200.0, 0.06),
                   [(1400.0, 30.0, 1.0), (1550.0, 100.0, 0.0), (1700.0, 300.0, 0.0)],
                   tau=0.5)
        self.assertAlmostEqual(out.r, 1464.06, delta=0.02)
        self.assertAlmostEqual(out.rd, 151.52, delta=0.02)
        self.assertAlmostEqual(out.sigma, 0.05999, delta=1e-4)

    def test_the_example_does_not_mutate_the_prior(self):
        prior = Rating(1500.0, 200.0, 0.06)
        rate(prior, [(1400.0, 30.0, 1.0)])
        self.assertEqual(prior, Rating(1500.0, 200.0, 0.06))


class ExpectedScoreTests(unittest.TestCase):
    def test_equal_ratings_are_a_coin_flip(self):
        self.assertAlmostEqual(expected(0.0, 0.0, 200.0 / 173.7178), 0.5, places=12)

    def test_a_stronger_player_is_favoured(self):
        self.assertGreater(expected(1.0, 0.0, 0.6), 0.5)
        self.assertLess(expected(-1.0, 0.0, 0.6), 0.5)

    def test_opponent_uncertainty_pulls_the_expectation_towards_a_half(self):
        certain = expected(1.0, 0.0, 0.01)
        unsure = expected(1.0, 0.0, 2.0)
        self.assertGreater(certain, unsure)
        self.assertGreater(unsure, 0.5)

    def test_the_expectation_is_bounded(self):
        for mu in (-10, 0, 10):
            for phi in (0.01, 0.6, 3.0):
                self.assertTrue(0.0 < expected(mu, 0.0, phi) < 1.0)


class PeriodTests(unittest.TestCase):
    def test_an_unplayed_period_inflates_the_deviation(self):
        out = rate(Rating(), [])
        self.assertEqual(out.r, DEFAULT_RATING)
        self.assertGreater(out.rd, DEFAULT_RD)

    def test_an_all_draw_period_stays_near_the_prior(self):
        matches = [(1500.0, 50.0, 0.5)] * 200
        out = rate(Rating(), matches)
        self.assertAlmostEqual(out.r, DEFAULT_RATING, delta=1.0)
        self.assertLess(out.rd, DEFAULT_RD)

    def test_winning_every_match_raises_the_rating(self):
        out = rate(Rating(), [(1500.0, 50.0, 1.0)] * 100)
        self.assertGreater(out.r, DEFAULT_RATING)
        self.assertGreater(out.lower(), DEFAULT_RATING)

    def test_losing_every_match_lowers_the_rating(self):
        out = rate(Rating(), [(1500.0, 50.0, 0.0)] * 100)
        self.assertLess(out.r, DEFAULT_RATING)

    def test_more_matches_mean_less_deviation(self):
        ten = rate(Rating(), [(1500.0, 50.0, 1.0)] * 10)
        thousand = rate(Rating(), [(1500.0, 50.0, 1.0)] * 1000)
        self.assertLess(thousand.rd, ten.rd)

    def test_half_points_are_accepted(self):
        out = rate(Rating(), [(1500.0, 50.0, 0.5)])
        self.assertAlmostEqual(out.r, DEFAULT_RATING, delta=1.0)

    def test_a_score_outside_the_unit_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            rate(Rating(), [(1500.0, 50.0, 1.5)])

    def test_a_saturated_expectation_does_not_divide_by_zero(self):
        # an enormous rating gap makes E (1 - E) underflow to zero
        out = rate(Rating(1500.0, 350.0, 0.06), [(100000.0, 1.0, 0.0)])
        self.assertGreater(out.rd, 0.0)
        self.assertEqual(out.r, DEFAULT_RATING)

    def test_a_large_tau_stays_finite(self):
        out = rate(Rating(), [(1500.0, 50.0, 1.0)] * 50, tau=5.0)
        self.assertTrue(-10000.0 < out.r < 10000.0)
        self.assertGreater(out.rd, 0.0)


class GatePropertyTests(unittest.TestCase):
    def test_periods_are_independent(self):
        periods = [[(1500.0, 50.0, 1.0)] * 20, [(1500.0, 50.0, 0.0)] * 20]
        wins, losses = rate_periods(Rating(), periods)
        self.assertGreater(wins.r, DEFAULT_RATING)
        self.assertLess(losses.r, DEFAULT_RATING)

    def test_a_small_archetype_keeps_a_large_deviation(self):
        small = rate(Rating(), [(1500.0, 50.0, 1.0)] * 10)
        large = rate(Rating(), [(1500.0, 50.0, 1.0)] * 1000)
        self.assertGreater(small.rd, large.rd)
        # the same winning record can pass on a big family and fail on a small one
        self.assertLess(small.lower(), large.lower())

    def test_the_lower_bound_is_the_gate_not_the_point_estimate(self):
        # three straight wins give r = 1763 but RD = 176, so r - 2*RD = 1412 < 1500.
        # A gate on the point estimate would release an archetype with three cases of
        # evidence; the bound refuses it. (Ten wins are enough: lower = 1607.)
        small = rate(Rating(), [(1500.0, 50.0, 1.0)] * 3)
        self.assertGreater(small.r, DEFAULT_RATING)
        self.assertLess(small.lower(), DEFAULT_RATING)
        enough = rate(Rating(), [(1500.0, 50.0, 1.0)] * 10)
        self.assertGreater(enough.lower(), DEFAULT_RATING)

    def test_k_controls_how_strict_the_bound_is(self):
        r = Rating(1600.0, 100.0, 0.06)
        self.assertGreater(r.lower(1.0), r.lower(2.0))

    def test_win_rate_is_the_score_share(self):
        self.assertAlmostEqual(win_rate([(1500, 50, 1.0), (1500, 50, 0.0),
                                         (1500, 50, 0.5), (1500, 50, 1.0)]), 0.625)
        self.assertEqual(win_rate([]), 0.5)


class ReleaseGateIntegrationTests(unittest.TestCase):
    """The wiring in `ai/release_gate.py`: off by default, strict when enabled."""

    @classmethod
    def setUpClass(cls):
        from ai.release_gate import archetype, glicko_archetypes
        cls.archetype = staticmethod(archetype)
        cls.glicko_archetypes = staticmethod(glicko_archetypes)

    @staticmethod
    def _cases(n: int, tool: str = "hdv.scan"):
        return [{"id": "%s%d" % (tool, i), "tool": tool, "lang": "fr",
                 "goal": "scan it", "args": {}} for i in range(n)]

    @staticmethod
    def _preds(cases, ok):
        return {c["id"]: ({"tool": c["tool"], "args": {}} if c["id"] in ok else None)
                for c in cases}

    def test_the_family_is_the_archetype_not_the_language(self):
        self.assertEqual(self.archetype({"tool": "hdv.scan", "lang": "de"}), "hdv")
        self.assertEqual(self.archetype({"tool": "fight.join"}), "fight")
        self.assertEqual(self.archetype({"tool": ""}), "unknown")

    def test_half_points_are_retained(self):
        cases = self._cases(60)
        both_right = self._preds(cases, {c["id"] for c in cases})
        rep = self.glicko_archetypes(both_right, both_right, cases, min_support=20)
        row = rep["rows"][0]
        self.assertEqual(row["matches"], 60)
        self.assertAlmostEqual(row["score_rate"], 0.5)
        self.assertAlmostEqual(row["r"], 1500.0, delta=1.0)
        self.assertLess(row["rd"], 350.0)  # agreement still shrinks the deviation

    def test_a_large_winning_family_passes(self):
        cases = self._cases(200)
        ref = self._preds(cases, set())
        win = self._preds(cases, {c["id"] for c in cases})
        rep = self.glicko_archetypes(win, ref, cases, min_support=20)
        self.assertTrue(rep["passed"])
        self.assertGreater(rep["rows"][0]["lower"], 1500.0)

    def test_a_three_case_family_cannot_pass_even_at_full_support(self):
        cases = self._cases(3, tool="fight.join")
        ref = self._preds(cases, set())
        win = self._preds(cases, {c["id"] for c in cases})
        rep = self.glicko_archetypes(win, ref, cases, min_support=3)
        self.assertFalse(rep["passed"])
        self.assertGreater(rep["rows"][0]["r"], 1500.0)
        self.assertLess(rep["rows"][0]["lower"], 1500.0)

    def test_a_family_below_min_support_is_reported_but_not_gated(self):
        big, small = self._cases(40), self._cases(2, tool="fight.join")
        cases = big + small
        ref = self._preds(cases, set())
        win = self._preds(cases, {c["id"] for c in cases})
        rep = self.glicko_archetypes(win, ref, cases, min_support=20)
        self.assertEqual(rep["supported_families"], 1)
        self.assertTrue(rep["passed"])
        self.assertEqual(len(rep["rows"]), 2)

    def test_no_supported_family_is_not_a_pass(self):
        cases = self._cases(5)
        rep = self.glicko_archetypes(self._preds(cases, {c["id"] for c in cases}),
                                     self._preds(cases, set()), cases, min_support=20)
        self.assertFalse(rep["passed"])

    def test_a_margin_and_a_larger_k_both_stricten_the_gate(self):
        cases = self._cases(60)
        ref = self._preds(cases, set(c["id"] for c in cases[:30]))
        win = self._preds(cases, set(c["id"] for c in cases))
        base = self.glicko_archetypes(win, ref, cases, min_support=20)
        self.assertTrue(base["passed"])
        self.assertFalse(self.glicko_archetypes(win, ref, cases, min_support=20,
                                                margin=200.0)["passed"])
        self.assertFalse(self.glicko_archetypes(win, ref, cases, min_support=20,
                                                k=8.0)["passed"])

    def test_missing_predictions_do_not_raise(self):
        cases = self._cases(25)
        rep = self.glicko_archetypes({}, {}, cases, min_support=20)
        self.assertEqual(rep["rows"][0]["matches"], 25)

    def test_the_default_gate_is_unchanged_by_the_glicko_wiring(self):
        import io
        import json
        from contextlib import redirect_stdout
        from ai.release_gate import main
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--limit", "10", "--splits", "val", "--json"])
        self.assertEqual(code, 0)
        # `--json` appends the payload after the human-readable table
        text = buf.getvalue()
        start = text.find("\n{\n")
        self.assertGreaterEqual(start, 0)
        payload = json.loads(text[start + 1:])
        self.assertIn("val", payload)
        self.assertNotIn("glicko", payload)


if __name__ == "__main__":
    unittest.main()
