"""Contract tests for the outcome-trained duel value.

The failure modes these guard against are the ones that already happened once in
this project: a feature-width constant that drifts from the encoder (which
surfaced as a broadcast error three frames inside a jitted matmul), and a value
head with no spread across candidates -- "inert" -- which made every
value-vs-solver comparison measure the solver against itself.
"""
from __future__ import annotations

import random
import unittest

import numpy as np

from combat import duel_value as dv


def _state(seed: int = 0):
    from combat.duel import DEFAULT_DATA, make_duel
    from combat.duel_benchmark import load_data, sample_pair
    from combat.normal_build_duel import DEFAULT_MAP

    cells = dv._load_cells(DEFAULT_MAP)
    data, levels = load_data(DEFAULT_DATA)
    rng = random.Random(seed)
    for _ in range(40):
        row_a, row_b = sample_pair(levels, list(range(4096)), rng)
        state = make_duel(data, row_a, row_b, cells, rng)
        if state is not None:
            return state
    raise unittest.SkipTest("no duel could be built from the encounter table")


class EncodingTests(unittest.TestCase):
    def test_the_encoder_matches_the_declared_width(self):
        state = _state(3)
        row = dv.encode(state, state.actor)
        self.assertEqual(row.shape, (dv.FEATURES,))
        self.assertTrue(np.isfinite(row).all(), row)
        self.assertAlmostEqual(dv.BLOCK * 2 + dv.GEOMETRY + dv.GLOBAL,
                               dv.FEATURES)

    def test_the_encoder_is_antisymmetric_in_the_hp_differential(self):
        """Swapping the reference fighter negates the differential."""
        state = _state(5)
        a = state.fighters[0]
        b = state.fighters[1]
        first = dv.encode(state, a)[-1]
        second = dv.encode(state, b)[-1]
        self.assertAlmostEqual(float(first), -float(second), places=6)


class TwoHotTests(unittest.TestCase):
    # Targets are float32 because they feed a jitted loss directly. A value that
    # "is" a bin can therefore land a few 1e-6 of mass off it -- the support is a
    # float32 linspace, so 0.04 per step is not exactly representable. That slop
    # is irrelevant to a cross-entropy measured in millinats; the tests assert
    # structure exactly and mass to float32 precision.
    TOL = 1e-4

    def test_mass_sits_on_the_two_neighbouring_bins(self):
        edges = dv.support()
        midpoint = float(edges[10] + 0.5 * (edges[11] - edges[10]))
        encoded = dv.two_hot(np.asarray([midpoint]))[0]
        self.assertAlmostEqual(float(encoded.sum()), 1.0, delta=self.TOL)
        self.assertEqual(int((encoded > 0).sum()), 2)
        self.assertAlmostEqual(float(encoded[10]), 0.5, delta=self.TOL)
        self.assertAlmostEqual(float(encoded[11]), 0.5, delta=self.TOL)

    def test_a_target_on_a_bin_is_one_hot(self):
        edges = dv.support()
        encoded = dv.two_hot(np.asarray([float(edges[7])]))[0]
        self.assertAlmostEqual(float(encoded[7]), 1.0, delta=self.TOL)
        # Not "exactly one non-zero": the neighbour keeps ~1e-6 of mass from the
        # float32 support. What matters is that nothing else carries weight.
        rest = np.delete(np.asarray(encoded, dtype=np.float64), 7)
        self.assertLess(float(rest.max()), self.TOL)

    def test_a_target_beyond_the_support_is_clamped_not_dropped(self):
        encoded = dv.two_hot(np.asarray([5.0, -5.0]))
        self.assertAlmostEqual(float(encoded[0].sum()), 1.0, delta=self.TOL)
        self.assertAlmostEqual(float(encoded[1].sum()), 1.0, delta=self.TOL)
        self.assertAlmostEqual(float(encoded[0][-1]), 1.0, delta=self.TOL)
        self.assertAlmostEqual(float(encoded[1][0]), 1.0, delta=self.TOL)


class TransformTests(unittest.TestCase):
    def test_symlog_round_trips(self):
        values = np.asarray([-1.0, -0.25, 0.0, 0.25, 1.0], dtype=np.float32)
        back = dv.symexp(dv.symlog(values))
        np.testing.assert_allclose(np.asarray(back), values, atol=1e-5)

    def test_symlog_is_odd_and_monotone(self):
        values = np.asarray([-2.0, -0.5, 0.0, 0.5, 2.0], dtype=np.float32)
        forward = np.asarray(dv.symlog(values))
        self.assertAlmostEqual(float(forward[0]), -float(forward[-1]), places=5)
        self.assertTrue(np.all(np.diff(forward) > 0))


class FitTests(unittest.TestCase):
    def test_a_fitted_head_has_spread(self):
        """The property the old forest failed: predictions must vary."""
        rng = np.random.default_rng(0)
        features = rng.random((600, dv.FEATURES)).astype(np.float32)
        signal = features[:, -1] * 2.0 - features[:, 0]
        labels = np.clip(signal + 0.05 * rng.standard_normal(600), -1, 1)
        fitted = dv.train(features, labels, epochs=25, batch=64, hidden=32,
                          layers=2, verbose=False)
        values = dv.predict(fitted["params"], features)
        self.assertEqual(values.shape, (600,))
        self.assertTrue(np.isfinite(values).all())
        self.assertGreater(float(np.std(values)), 1e-3)
        self.assertLess(fitted["report"]["val_ce"],
                        fitted["report"]["uniform_ce"])
        self.assertGreaterEqual(fitted["report"]["best_epoch"], 1)


class ListwiseTests(unittest.TestCase):
    """The conditional-logit objective, whose point is what it ignores.

    93.5% of the variance of a board's outcome is the board's own offset (measured
    on 60 boards). A ranker can only act on the rest, so the objective has to be
    blind to the offset by construction. Regressing the absolute return is not,
    and that model scored a regret of 0.0350 against the one-ply score's 0.0177.
    """

    def test_a_board_offset_leaves_the_targets_unchanged(self):
        truth = np.asarray([[0.1, -0.2, 0.05]], dtype=np.float32)
        mask = np.ones_like(truth)
        first = dv._soft_targets(truth, mask, 0.08)
        shifted = dv._soft_targets(truth + 0.37, mask, 0.08)
        np.testing.assert_allclose(first, shifted, atol=1e-6)

    def test_a_flat_board_gets_a_uniform_target(self):
        truth = np.full((1, 4), 0.2, dtype=np.float32)
        mask = np.ones_like(truth)
        target = dv._soft_targets(truth, mask, 0.08)[0]
        np.testing.assert_allclose(target, np.full(4, 0.25), atol=1e-6)

    def test_padding_carries_no_target_mass(self):
        truth = np.asarray([[0.3, 0.1, 0.0, 0.0]], dtype=np.float32)
        mask = np.asarray([[1.0, 1.0, 0.0, 0.0]], dtype=np.float32)
        target = dv._soft_targets(truth, mask, 0.08)[0]
        self.assertAlmostEqual(float(target[2:].sum()), 0.0, places=8)
        self.assertAlmostEqual(float(target.sum()), 1.0, places=6)

    def test_dense_groups_packs_every_group_and_masks_the_rest(self):
        X = np.arange(12 * 3, dtype=np.float32).reshape(12, 3)
        groups = np.asarray([5, 5, 5, 9, 9, 2, 2, 2, 2, 7, 7, 7], np.int32)
        returns = np.arange(12, dtype=np.float32)
        Xd, mask, td, ids = dv.dense_groups(groups, X, returns)
        self.assertEqual(list(ids), [2, 5, 7, 9])
        for slot, group in enumerate(ids):
            rows = np.flatnonzero(groups == group)
            np.testing.assert_allclose(Xd[slot, :len(rows)], X[rows])
            np.testing.assert_allclose(td[slot, :len(rows)], returns[rows])
            self.assertEqual(float(mask[slot, :len(rows)].sum()), float(len(rows)))
            self.assertEqual(float(mask[slot, len(rows):].sum()), 0.0)

    def test_an_untrained_score_head_reproduces_the_hp_differential(self):
        """The model must start *at* the greedy incumbent, not near it.

        The residual path carries the encoder's last feature through with a gain
        of 1, and the network's last layer starts at zero. Anything else and the
        untrained model is an arbitrary function: with He-scaled output weights
        its branch had a standard deviation of ~0.5 against the residual's ~0.3,
        and the ranker's Spearman fell from 0.84 to 0.61 at epoch 1.
        """
        params = dv.init_params(0, hidden=16, layers=1, score_head=True)
        rng = np.random.default_rng(1)
        X = rng.random((50, dv.FEATURES)).astype(np.float32)
        np.testing.assert_allclose(dv.predict(params, X), X[:, -1], atol=1e-6)

    def test_the_fit_learns_the_within_board_signal_not_the_offset(self):
        """A large per-board offset must not distract the fit.

        This is the failure that killed the absolute-value model, made explicit:
        the offset here has twice the spread of the signal it is added to.
        """
        rng = np.random.default_rng(0)
        boards, width = 400, 4
        X = rng.random((boards * width, dv.FEATURES)).astype(np.float32)
        signal = X[:, 0] * 2.0 - X[:, 1]
        offset = np.repeat(rng.normal(0.0, 1.0, size=boards), width)
        returns = signal + offset
        groups = np.repeat(np.arange(boards), width)
        fitted = dv.train_listwise(X, groups, returns, epochs=40, batch=32,
                                   hidden=32, layers=2, temp=0.08, verbose=False)
        self.assertGreater(fitted["report"]["val_top1"], 0.6)
        self.assertGreater(fitted["report"]["val_spearman"], 0.3)
        # A row-level split would count every board twice.
        self.assertEqual(fitted["report"]["train_boards"]
                         + fitted["report"]["val_boards"],
                         fitted["report"]["boards"])


class BenchmarkTests(unittest.TestCase):
    def test_the_benchmark_sees_a_policy_that_only_passes(self):
        """The instrument check the original estimator would have failed.

        The first version of `evaluate` took ``m1 - m2`` -- both of them ``a``'s
        own margin -- which measures seat bias and is zero by construction, so it
        reported ``p 1.0`` for every comparison. A policy that passes every turn
        must lose to the solver by a wide margin; if this returns ~0, the
        estimator is measuring the wrong thing again.
        """
        from combat.duel import solver_policy

        def always_pass(state, actor, plans, rng, model=None):
            return plans[-1]

        result = dv.evaluate(solver_policy, always_pass, games=10, seed=3,
                             name_a="solver", name_b="pass")
        self.assertEqual(result["boards"], 10)
        self.assertGreater(result["mean_hp_diff"], 0.2, result)
        self.assertEqual(result["verdict"], "a_better", result)
        self.assertGreater(result["boards_differed"], 0)
        # The swap did its job: no systematic seat gap survives it.
        self.assertLess(abs(result["mean_seat_gap"]), 0.2, result)


class PolicyTests(unittest.TestCase):
    def test_gumbel_topk_returns_distinct_indices(self):
        rng = random.Random(0)
        picked = dv.gumbel_topk(np.zeros(8), 4, rng)
        self.assertEqual(len(set(int(i) for i in picked)), 4)
        self.assertEqual(len(picked), 4)

    def test_the_policy_returns_one_of_the_offered_plans(self):
        from combat.battle_solver import BattleSolver
        from combat.duel import plans_for

        state = _state(9)
        plans = plans_for(state, BattleSolver(beam_width=4, max_actions=2), 6)
        if len(plans) < 2:
            self.skipTest("no candidate plans on this board")
        params = dv.init_params(0, hidden=16, layers=1)
        policy = dv.make_policy_for_params(params, trust=0.0)
        picked = policy(state, state.actor, plans, random.Random(0))
        self.assertTrue(any(picked is plan for plan in plans))


    def test_a_large_margin_returns_the_solver_choice(self):
        """The conservative-improvement gate must be able to switch itself off.

        With the margin set beyond any reachable advantage the policy is the
        solver: same plan, by identity, not merely the same score.
        """
        from combat.battle_solver import BattleSolver
        from combat.duel import plans_for

        state = _state(11)
        plans = plans_for(state, BattleSolver(beam_width=4, max_actions=2), 6)
        if len(plans) < 2:
            self.skipTest("no candidate plans on this board")
        params = dv.init_params(0, hidden=16, layers=1, score_head=True)
        policy = dv.make_policy_for_params(params, trust=0.0, margin=1e9)
        picked = policy(state, state.actor, plans, random.Random(0))
        incumbent = plans[int(np.argmax([p.score for p in plans]))]
        self.assertIs(picked, incumbent)


if __name__ == "__main__":
    unittest.main()
