"""The ruler behind the headline number: what counts as a state where the choice decides the fight.

`tools/wsp_program_target.py` measured that only ~3% of labelled player states on real boss boards
are ones where the legal (spell, target) pair changes the terminal outcome, and that number is what
bounds every action-head write: if the choice does not change the fight, authoring weights that
make a better choice cannot raise the win rate. So the counting rule needs to be able to fail.

Two traps it must not fall into, both of which it nearly did:

  * pooling over *pairs* instead of states. A state where all four pairs win is counted as a
    ranking success by any scorer, which is how an early arm printed `solved 1.000 vs chance 1.000`.
    Such states are now excluded from the ranking and reported separately, because the fight was
    already won before the decision.
  * a permissive denominator. `chance` inside a mixed state is the winning share of its pairs, so
    a state with three winning pairs and one losing pair is beaten by chance 75% of the time. The
    test pins that number exactly rather than trusting the formula.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

import numpy as np

TOOL = pathlib.Path(__file__).resolve().parent.parent / "tools" / "wsp_program_target.py"
spec = importlib.util.spec_from_file_location("wsp_program_target", TOOL)
target = importlib.util.module_from_spec(spec)
sys.modules["wsp_program_target"] = target
spec.loader.exec_module(target)


class MixedStateTests(unittest.TestCase):
    def test_a_state_where_every_pair_wins_is_not_a_ranking_problem(self):
        board = np.array([0, 0, 0, 1, 1, 1])
        step = np.array([0, 0, 0, 0, 0, 0])
        score = np.array([1.0, 0.0, -1.0, 1.0, 0.0, -1.0])
        label = np.array([1.0, 1.0, 1.0, -1.0, -1.0, -1.0])
        out = target.group_metrics(board, step, score, label)
        self.assertEqual(out["states_where_the_choice_decides_count"], 0)
        self.assertIsNone(out["solved_picks_a_winning_pair"],
                          "an all-winnable state was scored as if the ranking had been tested")
        self.assertEqual(out["states_where_a_pair_wins"], 0.5)

    def test_chance_inside_a_mixed_state_is_the_winning_share_not_one_over_pairs(self):
        board = np.zeros(4, dtype=int)
        step = np.zeros(4, dtype=int)
        label = np.array([1.0, -1.0, -1.0, -1.0])
        score = np.array([0.0, 3.0, 2.0, 1.0])          # the scorer likes a loser best
        out = target.group_metrics(board, step, score, label)
        self.assertAlmostEqual(out["chance_picks_a_winning_pair"], 0.25, places=6)
        self.assertAlmostEqual(out["solved_picks_a_winning_pair"], 0.0, places=6)
        self.assertEqual(out["states_where_the_choice_decides_count"], 1)

    def test_a_solved_ranking_that_picks_the_winning_pair_scores_one(self):
        board = np.zeros(4, dtype=int)
        step = np.zeros(4, dtype=int)
        label = np.array([1.0, -1.0, -1.0, -1.0])
        score = np.array([5.0, -1.0, -2.0, -3.0])
        out = target.group_metrics(board, step, score, label)
        self.assertEqual(out["solved_picks_a_winning_pair"], 1.0)
        self.assertGreater(out["solved_picks_a_winning_pair"],
                           out["chance_picks_a_winning_pair"])

    def test_states_are_separated_by_board_and_step_not_by_row(self):
        """Two boards' states must not merge into one group, or the denominator silently halves."""
        board = np.array([0, 0, 1, 1])
        step = np.array([0, 0, 0, 0])
        label = np.array([1.0, -1.0, 1.0, -1.0])
        score = np.array([1.0, 2.0, 1.0, 2.0])
        out = target.group_metrics(board, step, score, label)
        self.assertEqual(out["states"], 2.0)
        self.assertEqual(out["states_where_the_choice_decides_count"], 2)

    def test_the_same_step_of_two_boards_is_two_states(self):
        """`step` alone cannot key a state; that would pool every board's opening decision."""
        board = np.arange(6) // 3
        step = np.zeros(6, dtype=int)
        label = np.array([1.0, -1.0, -1.0, 1.0, 1.0, -1.0])
        score = np.array([0.0, 1.0, 2.0, 2.0, 1.0, 0.0])
        out = target.group_metrics(board, step, score, label)
        self.assertEqual(out["states"], 2.0)
        self.assertEqual(out["states_where_the_choice_decides_count"], 2)

    def test_paired_delta_uses_boards_not_seeds(self):
        """The interval must shrink with more boards. Averaging the seed spread instead is what
        printed +-0.0 on 512 boards in the first pass of this line of work."""
        wins_a = np.array([1.0, 0.0, 0.0, 0.0] * 64)
        wins_b = np.array([0.0, 0.0, 0.0, 0.0] * 64)
        first = target.paired(wins_a, wins_b)
        doubled = target.paired(np.tile(wins_a, 2), np.tile(wins_b, 2))
        self.assertLess(abs(doubled["ci95"][1]), abs(first["ci95"][1]))
        self.assertAlmostEqual(first["delta"], 0.25, places=6)


class LiveStateTests(unittest.TestCase):
    """A board the fight already decided is not a state where the player has a decision.

    Measured 2026-11-06: on 192 real mechanic boards this inflated the denominator from ~400 to
    2,408 states and pushed the pair-decided share from 9.3% down to 1.8%, because `env.step`
    freezes `acting_team` on a done board and the selector asked only for `acting == 0`. Every
    ceiling in this project's docs was quoted from that diluted share.
    """

    def _corpus(self):
        import jax.numpy as jnp
        from combat.encounter.env import Config
        cfg = Config(allies=1, enemies=4, spells=4, max_rounds=4)
        n, s = cfg.n_actors, cfg.spells
        spec = {
            # Board 0 has exactly one living monster, at 1 HP, and the player's spell does 200. That
            # is deliberate: `step` repeats a cast against the *same* target while AP remains, so a
            # four-monster wipe takes four player turns and the fight ends on the monsters' turn --
            # frozen at `acting == 1`, where `acting == 0` had already excluded it and the `~done`
            # half of the selector had nothing to do. One monster dies to one cast, on the player's
            # own turn, which is the only way this board freezes with `acting == 0`.
            "hp": np.array([[50.0, 1.0, 1.0, 1.0, 1.0], [80.0, 400.0, 400.0, 400.0, 400.0]]),
            "max_hp": np.array([[50.0, 1.0, 1.0, 1.0, 1.0], [80.0, 400.0, 400.0, 400.0, 400.0]]),
            "ap_max": np.full((2, n), 12.0),
            "mp_max": np.full((2, n), 6.0),
            "power": np.zeros((2, n)),
            "res": np.zeros((2, n, 5)),
            "dist": np.full((2, n), 2.0),
            "initiative": np.array([[9.0, 1.0, 1.0, 1.0, 1.0]] * 2),
            "alive": np.array([[True, True, False, False, False],
                               [True, True, True, True, True]]),
            "first_team": np.zeros((2,), dtype=np.int32),
        }
        tables = {
            "spell_ap": np.full((2, n, s), 3.0, dtype=np.float32),
            "spell_rmin": np.full((2, n, s), 1.0, dtype=np.float32),
            "spell_rmax": np.full((2, n, s), 6.0, dtype=np.float32),
            # monster spells do 5 damage: enough to threaten board 1, never enough to end it
            "spell_damage": np.where(np.arange(n)[None, :, None] == 0,
                                     np.full((2, n, s), 200.0, np.float32),
                                     np.full((2, n, s), 5.0, np.float32)),
            "spell_heal": np.where(np.arange(s)[None, None, :] == 3,
                                   np.full((2, n, s), 50.0, np.float32),
                                   np.zeros((2, n, s), np.float32)),
            "spell_element": np.zeros((2, n, s), np.int32),
            # Slot 3 is an ally heal, and it is the whole reason this fixture can witness the fix.
            # Measured 2026-11-06 by `check_frozen_candidate_filter.py`: on a board the player has
            # already won, every hostile (spell, target) pair is illegal because no monster is alive,
            # so a mask-only reading of "still has a decision" says no -- and the `~done` half of the
            # selector looked like a no-op, which is exactly why an earlier version of this test was
            # inert. What survives on a decided board is the friendly cast: 527 of the 2,479 frozen
            # player rows on 256 real mechanic boards. A fixture without an ally spell cannot show
            # that, and so cannot fail.
            "spell_ally": np.where(np.arange(s)[None, None, :] == 3,
                                   np.ones((2, n, s), bool), np.zeros((2, n, s), bool)),
        }
        # jax arrays, not numpy: `env.step` writes through `.at[...]`, and numpy has no such
        # attribute -- the real loader converts every array, so the fixture has to look like it
        return cfg, ({k: jnp.asarray(v) for k, v in spec.items()},
                    {k: jnp.asarray(v) for k, v in tables.items()})

    def test_a_decided_board_in_this_fixture_still_offers_a_friendly_cast(self):
        """The non-vacuity witness for the arm below.

        Without a legal cast on the frozen board, removing the `~done` half of the selector changes
        nothing, and the assertion above becomes a description of a fixture instead of a test of the
        tool -- which is how the first version of this class passed while proving nothing. Real
        boards do have such casts: 527 of the 2,479 frozen player rows on 256 mechanic boards carry a
        candidate, and every one of them is a friendly-target spell (`heal`, or now `summon`),
        measured by `dofus-evidence/check_frozen_candidate_filter.py`.
        """
        from combat.encounter.env import action_masks, initial_state, step
        from combat.encounter.heuristic import heuristic_actions
        cfg, (spec, tables) = self._corpus()
        state = initial_state(spec, tables, cfg)
        decided = step(state, heuristic_actions(state, tables, cfg), tables, cfg)
        self.assertTrue(bool(np.asarray(decided["done"])[0]),
                        "the wipeout board never finished, so it cannot be the frozen case")
        self.assertEqual(int(np.asarray(decided["acting_team"])[0]), 0,
                         "the acting team did not freeze on the player, so `acting == 0` alone "
                         "would have excluded it and the filter has nothing to do here")
        masks = action_masks(decided, tables, cfg)
        spell = np.asarray(masks["spell"])[0, 0, :cfg.spells]
        target = np.asarray(masks["target"])[0, 0, :cfg.spells, :].any(axis=-1)
        self.assertTrue(bool(np.any(spell & target)),
                        "no (spell, target) survives on the decided board, so the arm below is "
                        "inert again -- this fixture needs a friendly cast to be a witness")

    def test_a_frozen_board_contributes_one_state_not_one_per_remaining_step(self):
        cfg, (spec, tables) = self._corpus()
        params = target.feature_token_params(13, 9, 9, 16, cfg, 11)
        continuation = target.make_baseline_rollout(cfg, 2 * cfg.max_rounds)
        cache = target.collect_labels(params, spec, tables, cfg, continuation, 16,
                                      moves=(1,), policy=None)
        groups = {}
        for b, st in zip(cache["board"].tolist(), cache["step"].tolist()):
            groups.setdefault(b, set()).add(st)
        self.assertLessEqual(len(groups[0]), 1,
                             "the wipeout board still reported %d decision states"
                             % len(groups[0]))
        self.assertGreater(len(groups[1]), 0,
                           "the live board reported nothing, so the test cannot see the fix")

    def test_an_old_cache_is_refused_because_the_ruler_changed(self):
        """The whole point of hashing the ruler: a label set made before this fix must not be
        accepted by the tool that fixed it, whatever its seed and corpus digests say."""
        import json
        import tempfile
        old_meta = json.dumps({"seed": 11, "hidden": 16, "tokens": "identity",
                               "moves": [1], "boards": 4, "heads": 4,
                               "continuation": "heuristic", "dev": target.digest(target.DEV)},
                              sort_keys=True)
        self.assertNotIn("ruler", json.loads(old_meta))
        with tempfile.TemporaryDirectory() as d:
            cache = d + "/old.npz"
            np.savez(cache, meta=np.asarray(old_meta), label=np.zeros(1))
            with self.assertRaises(SystemExit) as caught:
                target.main(["--boards", "4", "--holdout", "4", "--tokens", "identity",
                             "--labels", cache, "--score-only"])
            self.assertIn("different run", str(caught.exception))


class HeadCountTests(unittest.TestCase):
    def test_attention_heads_divide_the_solved_width(self):
        """A width that does not divide by the head count crashes inside the attention reshape,
        which is how a closed-form arm with hidden=6 died the first time it was run."""
        self.assertEqual(target.pick_heads(6), 2)
        self.assertEqual(target.pick_heads(12), 4)
        self.assertEqual(target.pick_heads(5), 1)


if __name__ == "__main__":
    unittest.main()
