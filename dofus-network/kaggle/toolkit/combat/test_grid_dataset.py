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
        targets, orders = [], []
        for key in range(30):
            _rows, mask, scores, target, _notes, permuted, skip = grid_dataset._plans(
                self.board(), plans=source, permutation_key=str(key))
            self.assertEqual(skip, "")
            self.assertTrue(permuted)
            self.assertTrue(mask[:4].all() and not mask[4:].any())
            # The label is a distribution now; for a unique best plan it is a
            # one-hot on that plan's row, wherever the shuffle put it.
            self.assertEqual(float(target.sum()), 1.0)
            self.assertEqual(int(np.argmax(target)), int(np.argmax(scores[:4])))
            targets.append(int(np.argmax(target)))
            orders.append(tuple(scores[:4].tolist()))
        self.assertGreater(len(set(targets)), 2, targets)
        self.assertGreater(len(set(orders)), 2, orders)

    def test_plan_features_follow_their_scores(self):
        rows, _mask, scores, target, _notes, _permuted, skip = grid_dataset._plans(
            self.board(), plans=plans([90, 10]), permutation_key="ordered")
        self.assertEqual(skip, "")
        self.assertEqual(sorted(scores[:2].tolist()), [10.0, 90.0])
        best = rows[int(np.argmax(target))]
        self.assertAlmostEqual(float(best[0]), 0.9, places=5)
        paired = sorted(zip(np.asarray(scores[:2]).tolist(), (rows[:2, 0] * 100).tolist()))
        self.assertEqual([(round(s), round(f)) for s, f in paired], [(10, 10), (90, 90)])

    def test_single_action_candidate_is_not_permuted(self):
        result = grid_dataset._plans(self.board(), plans=plans([90]), permutation_key="single")
        self.assertEqual(len(result), 7)
        self.assertFalse(result[5])
        self.assertEqual(result[6], "pass_only")

    def test_a_tied_best_set_becomes_a_uniform_target(self):
        """A tie is a statement, not noise.

        This replaces `test_tied_best_candidate_is_excluded_not_randomly_labelled`,
        which asserted that a tied board was dropped. Dropping them was the reason
        the corpus could not be regenerated: the solver is indifferent on 96% of
        boards, so the old rule left 159 rows out of 3996. The property that must
        hold now is that the target spreads exactly over the winners and nowhere
        else, so the trainer can never be told to prefer one of them.
        """
        result = grid_dataset._plans(self.board(), plans=plans([90, 90, 10]),
                                     permutation_key="tie")
        rows, mask, scores, target, _notes, _permuted, skip = result
        self.assertEqual(skip, "")
        self.assertTrue(mask[:3].all())
        tied = np.flatnonzero(np.isclose(scores[:3], 90.0))
        self.assertEqual(len(tied), 2)
        self.assertEqual(sorted(np.flatnonzero(target > 0).tolist()), sorted(tied.tolist()))
        self.assertAlmostEqual(float(target.sum()), 1.0, places=6)
        for index in tied:
            self.assertAlmostEqual(float(target[index]), 0.5, places=6)
        self.assertEqual(float(target[np.flatnonzero(np.isclose(scores[:3], 10.0))[0]]), 0.0)
        self.assertEqual(rows.shape[0], 16)


class FighterReachChannelTests(unittest.TestCase):
    """The encoder has to be able to tell a sniper from a melee monster.

    Slot 18 of the fighter tensor was a hardcoded ``0.0`` until 2026-11-11. With
    distance as the only positional input, a monster six cells away carrying 5 MP
    and range 6 was indistinguishable from one that could not reach at all -- and
    that is the state the policy walked into enemies from.
    """

    def _board(self, foe_name, foe_mp, foe_level=5):
        from combat import threat
        from combat.battle_solver import (Board, CellMap, Fighter, NB_CELLS,
                                          _POINTS)

        flags = tuple([3] * NB_CELLS)
        me = Fighter(id=1, name="Me", cell=_POINTS[(20, 20)], team="player",
                     hp=140, max_hp=140, ap=6, mp=2, shield=0, level=12, alive=True,
                     by_me=True, resist_percent=(), resist_fixed=(),
                     critical_resistance=0)
        foe = Fighter(id=-1, name=foe_name, cell=_POINTS[(20, 23)], team="enemy",
                      hp=200, max_hp=200, ap=6, mp=foe_mp, shield=0,
                      level=foe_level, alive=True, by_me=False,
                      resist_percent=(), resist_fixed=(), critical_resistance=0)
        board = Board(map_id=1, in_fight=True, in_preparation=False, is_my_turn=True,
                      my_id=1, my_cell=me.cell, my_ap=6, my_mp=2,
                      fighters=[me, foe], spells=[], my_stats={},
                      cells=CellMap(flags))
        rows, _mask = grid_dataset._fighters(board, me)
        return rows, me, foe

    def test_the_channel_carries_the_enemys_reach(self):
        from combat import threat

        rows, _me, foe = self._board("Merkaptan Leader", 0)
        reach = threat.reach_of(foe)
        self.assertIsNotNone(reach)
        self.assertAlmostEqual(float(rows[1][18]), min(1.0, reach / 30.0), places=5)

    def test_two_monsters_at_the_same_distance_differ_by_reach(self):
        """The whole point: distance alone cannot separate these two."""
        near, _m1, _f1 = self._board("Nobody", 0)
        far, _m2, _f2 = self._board("Nobody", 6)
        self.assertAlmostEqual(float(near[0][16]), float(far[0][16]), places=6)  # same distance
        self.assertLess(float(near[1][18]), float(far[1][18]))                   # different reach

    def test_a_players_own_reach_is_not_encoded_as_a_threat(self):
        rows, me, _foe = self._board("Merkaptan Leader", 0)
        self.assertEqual(float(rows[0][18]), 0.0, "the player slot must stay zero")


class PlanRowsAreOrderedTests(unittest.TestCase):
    """Inference needs the candidates in the order it asked for them.

    `_plans` is the training featuriser: it shuffles so a label cannot be read off
    row order, and it rejects a board whose best plan is tied. Both are right for
    training and both are fatal for ranking -- a shuffle attaches each score to a
    different plan, and a rejection leaves nothing to rank. `plan_rows` is the
    order-preserving half, and it exists because `grid_infer` used `_plans` and
    unpacked a seven-tuple as five, so the ranker raised on every call.
    """

    def board(self):
        return SimpleNamespace(fighters=[SimpleNamespace(alive=True, team="player", id=1)],
                               me=SimpleNamespace(alive=True, team="player", id=1))

    def test_rows_come_back_in_the_order_given(self):
        source = plans([90, 80, 70, 60])
        _rows, mask, scores, _notes = grid_dataset.plan_rows(self.board(), source)
        self.assertTrue(mask[:4].all())
        self.assertEqual([float(s) for s in scores[:4]], [90.0, 80.0, 70.0, 60.0])

    def test_the_same_plans_give_the_same_rows_every_time(self):
        source = plans([90, 80, 70, 60])
        first = grid_dataset.plan_rows(self.board(), source)[0]
        second = grid_dataset.plan_rows(self.board(), source)[0]
        np.testing.assert_array_equal(first, second)

    def test_a_tied_best_plan_is_still_ranked(self):
        """`_plans` rejects a tie; ranking must not, it still has to choose."""
        _rows, mask, scores, _notes = grid_dataset.plan_rows(self.board(), plans([90, 90, 10]))
        self.assertTrue(mask[:3].all())
        self.assertEqual([float(s) for s in scores[:3]], [90.0, 90.0, 10.0])

    def test_plans_still_shuffles_for_training(self):
        """The training half keeps its behaviour: same rows, different order."""
        source = plans([90, 80, 70, 60])
        seen = set()
        for key in range(8):
            _rows, _mask, scores, _label, _notes, permuted, skip = grid_dataset._plans(
                self.board(), plans=source, permutation_key=str(key))
            self.assertEqual(skip, "")
            self.assertTrue(permuted)
            seen.add(tuple(float(s) for s in scores[:4]))
        self.assertGreater(len(seen), 1, "the shuffle is not happening at all")
        self.assertNotIn((90.0, 80.0, 70.0, 60.0), seen,
                         "the given order survived, so the rows were not permuted")


class RankerContractTests(unittest.TestCase):
    """`rank_plans` has to hand back plans, in a shape the caller can read.

    Run against a tiny freshly-initialised checkpoint rather than the 123 MB
    shipped one. The contract being tested is the plumbing -- arity, ordering and
    the `(list, metadata)` shape -- and that does not depend on the weights. It
    also means these tests keep running while the shipped checkpoint is stale
    against the current encoder.
    """

    @classmethod
    def setUpClass(cls):
        import pickle
        import tempfile

        from combat import grid_backend as gb
        from combat import grid_infer, grid_model

        if grid_infer._deps() is None:
            raise unittest.SkipTest("no model backend on this interpreter")
        try:
            import jax
        except ImportError:
            raise unittest.SkipTest("init_params needs a PRNG key from jax")
        params = grid_model.init_params(jax.random.PRNGKey(0), hidden=16,
                                        cell_layers=1, fighter_layers=1,
                                        plan_layers=1, heads=2)
        payload = {"params": gb.tree_map(np.asarray, params), "config": {"tiny": True}}
        handle = tempfile.NamedTemporaryFile("wb", suffix=".pkl", delete=False)
        handle.write(pickle.dumps(payload))
        handle.close()
        cls.grid_infer = grid_infer
        cls.model_path = __import__("pathlib").Path(handle.name)

    def _board(self):
        from combat.battle_solver import Board, CellMap, Fighter, NB_CELLS, _POINTS

        flags = tuple([3] * NB_CELLS)
        me = Fighter(id=1, name="Me", cell=_POINTS[(20, 20)], team="player",
                     hp=140, max_hp=140, ap=6, mp=2, shield=0, level=12, alive=True,
                     by_me=True, resist_percent=(), resist_fixed=(),
                     critical_resistance=0)
        foe = Fighter(id=-1, name="Nobody", cell=_POINTS[(20, 23)], team="enemy",
                      hp=200, max_hp=200, ap=6, mp=3, shield=0, level=8, alive=True,
                      by_me=False, resist_percent=(), resist_fixed=(),
                      critical_resistance=0)
        return Board(map_id=1, in_fight=True, in_preparation=False, is_my_turn=True,
                     my_id=1, my_cell=me.cell, my_ap=6, my_mp=2,
                     fighters=[me, foe], spells=[], my_stats={},
                     cells=CellMap(flags))

    def test_the_ranker_returns_plans_and_does_not_raise(self):
        from combat.battle_solver import BattleSolver

        board = self._board()
        candidates = BattleSolver(beam_width=16).plan_candidates(board, 16)
        self.assertGreaterEqual(len(candidates), 2)
        result = self.grid_infer.rank_plans(board, candidates, model_path=self.model_path)
        self.assertIsNotNone(result, "the ranker answered None for a valid board")
        order, meta = result
        self.assertIsInstance(meta, dict)
        self.assertEqual(len(order), len(candidates))
        # Every entry is `(score, plan)` and the plan is one it was given -- this is
        # what `ranked[0][1]` in `live_policy` used to get wrong.
        scores = []
        for score, plan in order:
            self.assertIsInstance(float(score), float)
            self.assertIn(plan, candidates)
            scores.append(float(score))
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_the_best_entry_is_a_plan_the_caller_can_realize(self):
        from combat.battle_solver import BattleSolver

        board = self._board()
        candidates = BattleSolver(beam_width=16).plan_candidates(board, 16)
        order, _meta = self.grid_infer.rank_plans(board, candidates,
                                                  model_path=self.model_path)
        best = order[0][1]
        self.assertIn(best, candidates, "the winner was not one of the candidates")
        self.assertTrue(hasattr(best, "actions"), "the winner is not a plan object")
        self.assertTrue(best.actions, "the winning plan has no actions")

    def test_a_score_stays_with_its_plan_when_the_list_is_reordered(self):
        """The shuffle in the training featuriser would break exactly this.

        The plan encoder is self-attention, so it is permutation-equivariant: the
        same candidate list in a different order must give each plan the same
        score. `_plans` would break that in one of two ways depending on the
        board -- it shuffles the rows and returns them, so the scores get zipped
        against the *original* list and every plan carries another plan's score;
        or it rejects the board outright when the best plan is tied, so there is
        nothing to rank at all. Both are the reason `plan_rows` exists.
        """
        from combat.battle_solver import BattleSolver

        board = self._board()
        candidates = BattleSolver(beam_width=16).plan_candidates(board, 16)
        if len(candidates) < 2:
            self.skipTest("need at least two candidates to reorder")
        forward, _m = self.grid_infer.rank_plans(board, candidates,
                                                 model_path=self.model_path)
        backward, _m = self.grid_infer.rank_plans(board, list(reversed(candidates)),
                                                  model_path=self.model_path)
        by_plan = {id(plan): float(score) for score, plan in forward}
        for score, plan in backward:
            self.assertAlmostEqual(float(score), by_plan[id(plan)], places=3)


class StaleCheckpointTests(unittest.TestCase):
    """A checkpoint from a narrower encoder is refused, not misread.

    `forward` would fail deep inside the first dense layer, which on a live turn
    means an exception instead of a fallback. The guard turns it into the `None`
    that `_grid_choice` already handles.
    """

    def test_a_checkpoint_of_the_wrong_width_is_refused(self):
        import pickle
        import tempfile

        from combat import grid_infer, grid_model

        wrong = grid_model.CELL_FEATS + 3
        payload = {"params": {"cell_in": (np.zeros((wrong, 8), np.float32),
                                          np.zeros((8,), np.float32))},
                   "config": {}}
        handle = tempfile.NamedTemporaryFile("wb", suffix=".pkl", delete=False)
        handle.write(pickle.dumps(payload))
        handle.close()
        self.assertIsNone(grid_infer.load_model(__import__("pathlib").Path(handle.name)))

    def test_the_shipped_encoder_width_is_what_the_dataset_writes(self):
        from combat import grid_dataset, grid_model
        from combat.battle_solver import Board, CellMap, Fighter, NB_CELLS, _POINTS

        me = Fighter(id=1, name="Me", cell=_POINTS[(20, 20)], team="player", hp=10,
                     max_hp=10, ap=6, mp=2, shield=0, level=1, alive=True, by_me=True,
                     resist_percent=(), resist_fixed=(), critical_resistance=0)
        board = Board(map_id=1, in_fight=True, in_preparation=False, is_my_turn=True,
                      my_id=1, my_cell=me.cell, my_ap=6, my_mp=2, fighters=[me],
                      spells=[], my_stats={}, cells=CellMap(tuple([3] * NB_CELLS)))
        self.assertEqual(grid_dataset._cells(board, me).shape[1], grid_model.CELL_FEATS)


if __name__ == "__main__":
    unittest.main()
