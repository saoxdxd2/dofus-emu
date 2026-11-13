"""Side-swapped cross-play must report seat bias instead of inventing a winner."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from combat.side_swap import (binomial_two_sided, damage_split, efficiency,
                              play_side_swapped, score_games,
                              value_head_spread)  # noqa: E402

#: A high bin of the two-hot support, used by the risk tests to build a wide
#: distribution against a narrow one with the same centre.
BINS_HIGH = 45


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


# ---------------------------------------------------------------------------
# The paired segregation: seat must be a constant within a pair, not a signal
# ---------------------------------------------------------------------------


def _paired_rows(games=14, seed=11):
    """A small paired corpus, built once and shared by the tests below."""
    from combat import duel_value as dv

    X, groups, returns, seats, pairs, meta = dv.generate_paired(
        games, k=4, rollouts=1, seed=seed, workers=1, snapshots=2)
    return dv, X, groups, returns, seats, pairs, meta


class PairedCorpusTests(unittest.TestCase):
    """`generate_paired` is the part that can silently produce a lie.

    Three ways it did while this was being written, all of them invisible in a
    total and all of them visible in the pair structure:

    * the swap was applied to the walking order rather than to the builds, so
      both halves rebuilt an identical fight and every pair was seat 1 twice;
    * a half that ended a turn early shifted every later index, so slot `k` of
      one game was compared with slot `k-1` of the other;
    * swapping the builds swaps initiative with them, so when B has the higher
      initiative the same build acts first in *both* halves and a turn-number
      pair is one seat with itself.
    """

    @classmethod
    def setUpClass(cls):
        cls.rows = _paired_rows()

    def test_the_corpus_is_not_empty(self):
        """No skip here on purpose.

        When the swap was broken the corpus came back with *zero* rows, and the
        structural tests below all skipped on `len(X) == 0` -- so a suite meant
        to catch the bug passed against it. An empty paired corpus is a failure
        of this generator, not a condition of the environment: the flat-only and
        unpaired counters in the meta distinguish "the table had nothing" from
        "the pairing code never produced a pair", and only the former is a skip.
        """
        _dv, X, _groups, _returns, _seats, _pairs, meta = self.rows
        if meta.get("built", 0) == 0:
            self.skipTest("no duel could be built from the encounter table")
        self.assertGreater(len(X), 0,
                           "%d boards built but no paired rows: %r"
                           % (meta.get("built", 0), meta))
        self.assertGreater(meta.get("pairs", 0), 0, meta)

    def test_every_pair_holds_exactly_one_row_set_per_seat(self):
        _, X, groups, returns, seats, pairs, _meta = self.rows
        self.assertGreater(len(X), 0, "cannot check a pairing that is empty")
        by_pair = {}
        for group, seat, pair in zip(groups.tolist(), seats.tolist(),
                                     pairs.tolist()):
            by_pair.setdefault(pair, set()).add(seat)
        self.assertTrue(by_pair, "no pairs at all")
        for pair, seen in by_pair.items():
            self.assertEqual(sorted(seen), [1, 2],
                             "pair %d holds seats %s" % (pair, sorted(seen)))

    def test_a_pair_is_two_decision_points_one_per_seat(self):
        """Both halves are emitted or neither -- never a half-pair."""
        _, X, groups, returns, seats, pairs, _meta = self.rows
        self.assertGreater(len(X), 0, "cannot check a pairing that is empty")
        for pair in set(pairs.tolist()):
            rows = np.flatnonzero(pairs == pair)
            self.assertEqual(len(set(groups[rows].tolist())), 2,
                             "pair %d has %d groups"
                             % (pair, len(set(groups[rows].tolist()))))

    def test_the_returns_are_seat_relative(self):
        """`outcome` is measured from the actor's side, so a pair's two halves
        carry opposite-signed seeings of one result rather than one number."""
        _, X, groups, returns, seats, pairs, _meta = self.rows
        self.assertGreater(len(X), 0, "cannot check a pairing that is empty")
        self.assertTrue(np.all(np.abs(returns) <= 1.0))
        self.assertLess(float(returns.min()), 0.0)
        self.assertGreater(float(returns.max()), 0.0)


class DuelingHeadTests(unittest.TestCase):
    """The decomposition, tested as a property rather than as a shape."""

    def test_an_untrained_dueling_head_ties_every_plan(self):
        """A head that has learned nothing must be inert, not arbitrary.

        Its advantage projection is zero, so `Q` is exactly `V_seat` and every
        plan on a board receives the same value. That is measurable: an inert
        head is caught, while an arbitrary one silently reorders plans under a
        policy that thinks it is reading a model.
        """
        from combat import duel_value as dv

        params = dv.init_params(0, hidden=16, layers=1, dueling_head=True)
        rng = np.random.default_rng(0)
        X = rng.random((40, dv.FEATURES)).astype(np.float32)
        seats = np.tile(np.asarray([1, 2], np.int32), 20)
        first = dv.predict(params, X, seat=seats)
        np.testing.assert_allclose(first, first[0], atol=1e-6)

    def test_the_advantage_cannot_reach_the_support_edges(self):
        """`A` is bounded, so `V + A` stays inside the bins the target occupies."""
        from combat import duel_value as dv

        params = dv.init_params(0, hidden=16, layers=1, dueling_head=True)
        # Force the advantage branch to be enormous.
        params["adv_w"] = np.ones_like(params["adv_w"]) * 50.0
        rng = np.random.default_rng(0)
        X = rng.random((8, dv.FEATURES)).astype(np.float32)
        seats = np.ones(8, np.int32)
        logits = np.asarray(dv.forward(params, dv.XP.asarray(X),
                                       seat=dv.XP.asarray(seats)))
        # V is the zero table, so every row is pure advantage; the widest row
        # may therefore not exceed the bound on either side.
        self.assertTrue(np.isfinite(logits).all())
        self.assertLessEqual(float(np.abs(logits).max()),
                             dv.ADVANTAGE_BOUND + 1e-3, logits)

    def test_the_seat_is_required_and_cannot_be_defaulted(self):
        from combat import duel_value as dv

        params = dv.init_params(0, hidden=16, layers=1, dueling_head=True)
        X = np.zeros((2, dv.FEATURES), np.float32)
        with self.assertRaises(ValueError):
            dv.forward(params, dv.XP.asarray(X))

    def test_the_two_seat_rows_are_the_only_seat_dependent_parameter(self):
        """The seat must not be expressible anywhere a plan could read it."""
        from combat import duel_value as dv

        params = dv.init_params(0, hidden=16, layers=1, dueling_head=True)
        # V_seat is the only parameter whose shape begins with the two seats.
        tables = [key for key, value in params.items()
                  if np.ndim(value) == 2 and np.shape(value)[0] == 2]
        self.assertEqual(tables, ["v_seat"])
        # And the encoder carries no seat column: the last feature is the HP
        # differential, and FEATURES is otherwise a pure function of the board.
        self.assertEqual(dv.FEATURES, 2 * dv.BLOCK + dv.GEOMETRY + dv.GLOBAL)


class CVaRTests(unittest.TestCase):
    """The lower-tail decoder, against an independent implementation."""

    def test_cvar_of_a_point_mass_is_the_worst_bin(self):
        from combat import duel_value as dv

        probabilities = np.zeros((1, dv.BINS), np.float32)
        probabilities[0, 0] = 1.0
        logits = np.log(probabilities + 1e-30)
        worst = float(np.asarray(dv.symexp(dv.support()))[0])
        self.assertAlmostEqual(float(dv.cvar_of(logits, 0.15)[0]), worst,
                               places=4)

    def test_an_uniform_distribution_has_cvar_below_its_mean(self):
        from combat import duel_value as dv

        logits = np.zeros((1, dv.BINS), np.float32)
        mean = float(dv.value_of(logits)[0])
        tail = float(dv.cvar_of(logits, 0.15)[0])
        self.assertLess(tail, mean,
                        "a lower tail above the mean is not a lower tail")

    def test_a_known_straddle_matches_the_hand_computation(self):
        from combat import duel_value as dv

        probabilities = np.zeros((1, dv.BINS), np.float32)
        probabilities[0, 0] = 0.4
        probabilities[0, 1] = 0.4
        probabilities[0, 2] = 0.2
        logits = np.log(probabilities + 1e-30)
        centres = np.asarray(dv.symexp(dv.support()))
        want = float(0.4 * centres[0] + 0.2 * centres[1]) / 0.6
        self.assertAlmostEqual(float(dv.cvar_of(logits, 0.6)[0]), want, places=4)

    def test_a_degenerate_alpha_is_rejected_rather_than_dividing_by_zero(self):
        from combat import duel_value as dv

        logits = np.zeros((1, dv.BINS), np.float32)
        for alpha in (0.0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                dv.cvar_of(logits, alpha)
        # alpha = 1 is the whole distribution, i.e. the mean, and is legal.
        self.assertAlmostEqual(float(dv.cvar_of(logits, 1.0)[0]),
                               float(dv.value_of(logits)[0]), places=4)

    def test_cvar_ranks_a_safe_plan_over_a_gamble_with_the_same_mean(self):
        """The reason the policy ranks by CVaR at all.

        Wide and narrow distributions with the same mean: the mean cannot tell
        them apart, and the tail must prefer the narrow one.
        """
        from combat import duel_value as dv

        wide = np.zeros((1, dv.BINS), np.float32)
        wide[0, 5], wide[0, BINS_HIGH] = 0.5, 0.5
        narrow = np.zeros((1, dv.BINS), np.float32)
        narrow[0, (5 + BINS_HIGH) // 2 - 1] = 0.5
        narrow[0, (5 + BINS_HIGH) // 2 + 1] = 0.5
        # Sanity: the two really do share a centre, so the mean cannot separate
        # them and only the tail can.
        self.assertLess(abs(float(dv.value_of(np.log(wide + 1e-30))[0])
                            - float(dv.value_of(np.log(narrow + 1e-30))[0])),
                        0.2)
        narrow_logits = np.log(narrow + 1e-30)
        wide_logits = np.log(wide + 1e-30)
        safe = float(dv.cvar_of(narrow_logits, 0.5)[0])
        gamble = float(dv.cvar_of(wide_logits, 0.5)[0])
        self.assertGreater(safe, gamble,
                           "the tail preferred the wider gamble: %r vs %r"
                           % (safe, gamble))


class PairedFitTests(unittest.TestCase):
    """An end-to-end fit on a real paired corpus, small but not trivial."""

    @classmethod
    def setUpClass(cls):
        dv, X, groups, returns, seats, pairs, meta = _paired_rows(games=20)
        if len(set(groups.tolist())) < 8:
            raise unittest.SkipTest("not enough decision points to fit")
        cls.fitted = dv.train_dueling(X, groups, returns, seats, pairs=pairs,
                                      epochs=30, batch=12, hidden=64, layers=2,
                                      seed=0, verbose=False)
        cls.data = (X, groups, returns, seats, pairs)

    def test_the_fit_reports_the_pair_structure_it_was_given(self):
        report = self.fitted["report"]
        self.assertEqual(report["objective"], "duel")
        self.assertEqual(report["seat1_rows"] + report["seat2_rows"],
                         report["samples"])
        self.assertEqual(report["train_boards"] + report["val_boards"],
                         report["boards"])

    def test_the_head_ranks_within_a_board(self):
        report = self.fitted["report"]
        self.assertGreater(report["val_top1"], 0.5, report)
        self.assertGreater(report["val_spearman"], 0.2, report)

    def test_the_cvar_ranking_is_not_worse_than_chance(self):
        """The tail statistic must order plans, not merely exist.

        `val_top1` can be high while the tail is uninformative, because the mean
        is fitted twice over. This is the number the policy actually reads.
        """
        report = self.fitted["report"]
        self.assertGreater(report["val_cvar_top1"], 0.5, report)

    def test_the_seat_moves_the_measured_return_more_than_the_ranking(self):
        """The property the decomposition exists for, on held-out pairs.

        `measured_gap` is how much the true return differs between the two seats
        of a pair; `plan_rank_spread` is how much the model's own value for the
        same plans differs. If the seat term absorbed the asymmetry, the second
        is the smaller -- the seat changed what happened without changing what
        the model thinks of the plan.
        """
        paired = self.fitted["report"]["paired"]
        self.assertGreater(paired["pairs"], 0)
        self.assertIsNotNone(paired["measured_gap"])
        self.assertLessEqual(paired["plan_rank_spread"],
                             paired["measured_gap"] + 1e-6,
                             "the seat reordered plans more than it moved the "
                             "return: %r" % (paired,))

    def test_a_dueling_head_cannot_be_served_by_a_cvar_on_a_scalar_head(self):
        from combat import duel_value as dv

        scalar = dv.init_params(0, hidden=16, layers=1, score_head=True)
        with self.assertRaises(ValueError):
            dv.make_policy_for_params(scalar, trust=0.0, risk="cvar")
        # ... but the default 'auto' resolves to the mean, which it can honour.
        dv.make_policy_for_params(scalar, trust=0.0)

    def test_the_policy_ranks_by_the_tail_and_still_returns_a_candidate(self):
        import random

        from combat import duel_value as dv
        from combat.battle_solver import BattleSolver
        from combat.duel import DEFAULT_DATA, make_duel, plans_for
        from combat.duel_benchmark import load_data, sample_pair
        from combat.normal_build_duel import DEFAULT_MAP

        cells = dv._load_cells(DEFAULT_MAP)
        data, levels = load_data(DEFAULT_DATA)
        rng = random.Random(5)
        board = None
        for _ in range(40):
            row_a, row_b = sample_pair(levels, list(range(4096)), rng)
            board = make_duel(data, row_a, row_b, cells, rng)
            if board is not None:
                break
        if board is None:
            self.skipTest("no duel could be built")
        plans = plans_for(board, BattleSolver(beam_width=4, max_actions=2), 5)
        if len(plans) < 2:
            self.skipTest("no candidate plans on this board")
        policy = dv.make_policy_for_params(self.fitted["params"], trust=0.0,
                                           alpha=0.15)
        picked = policy(board, board.actor, plans, random.Random(0))
        self.assertTrue(any(picked is plan for plan in plans))
