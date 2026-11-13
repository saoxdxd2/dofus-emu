"""Analytic checks for the risk statistics and the posterior samplers.

Every number here has a closed form, so the test is against mathematics rather than
against a previous run of the same code. Where a value is sampled, the tolerance is
stated and the seed is fixed.

The point of the module is a specific failure mode, so the last class reproduces it:
an option with a good mean and a fat bad tail, which a mean-based ranker prefers and a
CVaR-based one does not.
"""

from __future__ import annotations

import math
import unittest

import numpy as np

from planning.experience import (
    BetaRate,
    GammaRate,
    LogNormalDuration,
    NormalPosterior,
)
from planning.risk import (
    DEFAULT_LAMBDA,
    DEFAULT_TAIL,
    band_of,
    cvar,
    family_mean,
    risk_score,
    shrink,
    thompson_pick,
)


class CvarTests(unittest.TestCase):
    def test_the_upper_tail_of_a_known_grid_is_the_mean_of_its_top_tenth(self):
        # 1..1000, worst 10% = 100 values, 901..1000, mean 950.5 exactly.
        self.assertAlmostEqual(cvar(range(1, 1001), tail=0.1, upper=True), 950.5, places=6)

    def test_the_lower_tail_is_the_mean_of_the_bottom_tenth(self):
        self.assertAlmostEqual(cvar(range(1, 1001), tail=0.1, upper=False), 50.5, places=6)

    def test_a_tail_larger_than_the_sample_is_the_whole_sample(self):
        self.assertAlmostEqual(cvar([1.0, 2.0, 3.0], tail=0.9, upper=True), 2.0, places=6)

    def test_one_sample_returns_that_sample_whatever_the_tail(self):
        self.assertEqual(cvar([7.0], tail=0.1), 7.0)

    def test_no_evidence_is_not_the_same_as_no_problem(self):
        # A duration with nothing known must lose against anything, not tie at zero.
        self.assertEqual(cvar([], upper=True), float("inf"))
        self.assertEqual(cvar([], upper=False), float("-inf"))

    def test_non_finite_draws_are_ignored_rather_than_propagated(self):
        self.assertAlmostEqual(cvar([1.0, float("inf"), 3.0], tail=1.0), 2.0, places=6)

    def test_the_tail_is_coherent_it_never_falls_below_the_mean(self):
        samples = [1.0, 2.0, 3.0, 4.0, 100.0]
        self.assertGreaterEqual(cvar(samples, tail=0.2), float(np.mean(samples)))
        self.assertLessEqual(cvar(samples, tail=0.2, upper=False), float(np.mean(samples)))


class RiskScoreTests(unittest.TestCase):
    def test_a_zero_lambda_is_the_mean(self):
        samples = [1.0, 2.0, 3.0, 10.0]
        self.assertAlmostEqual(risk_score(samples, lam=0.0), float(np.mean(samples)), places=9)

    def test_a_fat_tail_costs_more_than_the_mean_says(self):
        flat = [5.0] * 100
        fat = [4.9] * 90 + [100.0] * 10
        self.assertAlmostEqual(risk_score(flat, lam=1.0), 5.0, places=6)
        self.assertGreater(risk_score(fat, lam=1.0), float(np.mean(fat)))

    def test_a_reward_is_penalised_on_its_downside(self):
        risky = [100.0] * 90 + [0.0] * 10
        score = risk_score(risky, lam=1.0, upper=False)
        self.assertLess(score, float(np.mean(risky)))

    def test_an_empty_option_is_never_preferred(self):
        self.assertEqual(risk_score([], lam=1.0), float("inf"))


class ThompsonTests(unittest.TestCase):
    def test_the_better_option_wins_most_of_the_time(self):
        good = BetaRate(alpha=20.0, beta=20.0)     # mean 0.5
        bad = BetaRate(alpha=2.0, beta=30.0)       # mean 0.0625
        rng = np.random.default_rng(11)
        picks = [thompson_pick([good, bad], rng=rng, lower_is_better=False)
                 for _ in range(500)]
        self.assertGreater(picks.count(0) / 500.0, 0.95)

    def test_an_unknown_option_still_gets_tried(self):
        """The exploration is the posterior's width, not a schedule."""
        known = NormalPosterior(prior_mean=10.0, prior_strength=50.0, prior_sigma=0.1)
        known.update(10.0, weight=200.0)
        unknown = NormalPosterior(prior_mean=10.0, prior_strength=1.0, prior_sigma=5.0)
        rng = np.random.default_rng(3)
        picks = [thompson_pick([known, unknown], rng=rng, lower_is_better=True)
                 for _ in range(400)]
        self.assertGreater(picks.count(1), 20, picks.count(1))

    def test_a_callable_is_accepted_as_well_as_a_posterior(self):
        self.assertEqual(thompson_pick([lambda: 9.0, lambda: 1.0]), 1)

    def test_no_options_is_minus_one_rather_than_an_exception(self):
        self.assertEqual(thompson_pick([]), -1)


class ShrinkageTests(unittest.TestCase):
    def test_no_observations_is_the_family_mean(self):
        self.assertAlmostEqual(shrink(100.0, 0.0, 10.0, strength=8.0), 10.0, places=9)

    def test_overwhelming_evidence_is_the_local_estimate(self):
        # 8000 observations against a strength of 8 leaves the family 0.1% of the say,
        # so this approaches 100 without ever reaching it -- hence a delta, not places.
        self.assertAlmostEqual(shrink(100.0, 8000.0, 10.0, strength=8.0), 100.0, delta=0.1)
        self.assertGreater(shrink(100.0, 8000.0, 10.0, strength=8.0), 99.9)

    def test_evidence_equal_to_the_strength_is_the_midpoint(self):
        self.assertAlmostEqual(shrink(100.0, 8.0, 20.0, strength=8.0), 60.0, places=9)

    def test_two_fights_do_not_outvote_a_family(self):
        """The exact case this exists for: a monster met twice.

        n=2 against a strength of 8 gives the local estimate a fifth of the say, so
        500 becomes 132 against a family of 40 -- still the family's neighbourhood,
        and nowhere near the number two fights produced.
        """
        local, family = 500.0, 40.0
        shrunk = shrink(local, 2.0, family, strength=8.0)
        self.assertAlmostEqual(shrunk, 132.0, places=6)
        self.assertLess(shrunk, 0.3 * local)
        self.assertGreater(shrunk, family)

    def test_an_infinite_local_estimate_falls_back_to_the_family(self):
        self.assertEqual(shrink(float("inf"), 100.0, 12.0), 12.0)

    def test_the_band_labels_are_contiguous_and_ordered(self):
        self.assertEqual(band_of(0), "0-19")
        self.assertEqual(band_of(19), "0-19")
        self.assertEqual(band_of(20), "20-39")
        self.assertEqual(band_of(199), "180-199")

    def test_a_family_mean_weights_by_evidence_and_skips_non_finite(self):
        values = {"a": 10.0, "b": 20.0, "c": float("inf")}
        self.assertAlmostEqual(family_mean(values), 15.0, places=9)
        self.assertAlmostEqual(
            family_mean(values, weights={"a": 3.0, "b": 1.0}), 12.5, places=9)
        self.assertEqual(family_mean({"a": float("nan")}), 0.0)


class PosteriorSamplingTests(unittest.TestCase):
    """Each sampler must reproduce its own closed-form mean, or Thompson is biased."""

    DRAWS = 40000

    def setUp(self) -> None:
        self.rng = np.random.default_rng(2026)

    def test_beta_draws_centre_on_alpha_over_alpha_plus_beta(self):
        posterior = BetaRate(alpha=8.0, beta=24.0)
        draws = posterior.sample(self.DRAWS, self.rng)
        self.assertAlmostEqual(float(draws.mean()), 8.0 / 32.0, places=2)
        self.assertAlmostEqual(float(draws.var()), (8 * 24) / (32 ** 2 * 33), places=3)

    def test_gamma_draws_centre_on_shape_over_rate(self):
        posterior = GammaRate(shape=4.0, rate=0.5)     # mean 8
        draws = posterior.sample(self.DRAWS, self.rng)
        self.assertAlmostEqual(float(draws.mean()), 8.0, places=2)

    def test_a_normal_draws_centre_on_its_shrunk_mean(self):
        posterior = NormalPosterior(prior_mean=0.0, prior_strength=2.0, prior_sigma=1.0)
        for _ in range(50):
            posterior.update(4.0)
        draws = posterior.sample(self.DRAWS, self.rng)
        self.assertAlmostEqual(float(draws.mean()), posterior.mean(), places=2)

    def test_a_lognormal_draws_centre_on_the_distribution_mean_not_the_median(self):
        """`mean()` returns exp(mu), the median. The draw's mean is exp(mu + sigma^2/2)."""
        posterior = LogNormalDuration(prior_median=10.0, prior_strength=3.0, prior_sigma=1.0)
        draws = posterior.sample(self.DRAWS, self.rng)
        mu, var = posterior._stats()
        self.assertAlmostEqual(float(draws.mean()), math.exp(mu + var / 2.0), delta=0.5)
        self.assertGreater(float(draws.mean()), posterior.mean())

    def test_a_thin_posterior_draws_wider_than_a_thick_one(self):
        thin = LogNormalDuration(prior_median=10.0, prior_strength=1.0, prior_sigma=2.0)
        thick = LogNormalDuration(prior_median=10.0, prior_strength=1.0, prior_sigma=2.0)
        for _ in range(60):
            thick.update(10.0)
        self.assertGreater(float(thin.sample(2000, self.rng).std()),
                           float(thick.sample(2000, self.rng).std()))

    def test_the_same_seed_gives_the_same_draws(self):
        posterior = BetaRate()
        one = posterior.sample(5, np.random.default_rng(7))
        two = posterior.sample(5, np.random.default_rng(7))
        self.assertEqual(list(one), list(two))

    def test_the_base_class_degrades_to_its_mean_rather_than_raising(self):
        from planning.experience import Posterior

        class Flat(Posterior):
            def mean(self) -> float:
                return 3.5

        draws = Flat().sample(4, self.rng)
        self.assertEqual(list(draws), [3.5] * 4)


class TheFailureModeItExistsFor(unittest.TestCase):
    """A good mean with a fat bad tail -- the plan a dry streak punishes."""

    def test_the_mean_prefers_the_option_the_tail_hates(self):
        # Option A: reliably 10 minutes, no spread.
        steady = np.full(1000, 10.0)
        # Option B: usually 8 minutes, but one time in a hundred it is 200.
        # Mean 9.92 -- the mean prefers B. The worst decile is 27.2, and the tail
        # does not: that decile is where a dry streak actually lives.
        streaky = np.concatenate([np.full(990, 8.0), np.full(10, 200.0)])

        self.assertAlmostEqual(float(streaky.mean()), 9.92, places=6)
        self.assertLess(streaky.mean(), steady.mean())          # the mean likes B
        self.assertGreater(cvar(streaky), cvar(steady))         # the tail hates B
        self.assertGreater(risk_score(streaky), risk_score(steady))

    def test_lambda_decides_where_the_crossover_is(self):
        steady = np.full(1000, 10.0)
        streaky = np.concatenate([np.full(990, 8.0), np.full(10, 200.0)])
        self.assertLess(risk_score(streaky, lam=0.0), risk_score(steady, lam=0.0))
        self.assertGreater(risk_score(streaky, lam=DEFAULT_LAMBDA),
                           risk_score(steady, lam=DEFAULT_LAMBDA))

    def test_the_defaults_are_the_documented_ones(self):
        self.assertEqual(DEFAULT_TAIL, 0.1)
        self.assertEqual(DEFAULT_LAMBDA, 1.0)


if __name__ == "__main__":
    unittest.main()
