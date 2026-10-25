"""Tests for projecting live board plans into the encounter value head."""

from __future__ import annotations

import unittest

import jax
import jax.numpy as jnp

from combat.battle_solver import BattleSolver, LiveEffect
from combat.encounter import model
from combat.encounter.env import Config, initial_state, observe
from combat.plan_value import board_encounter, rank_plans, score_plan
from combat.test_battle_solver import board_with, cell, live_spell


def zero_params(feat_dim: int, global_dim: int, cfg: Config):
    params = model.init_params(jax.random.PRNGKey(0), feat_dim, global_dim, cfg)
    return jax.tree_util.tree_map(jnp.zeros_like, params)


class PlanValueTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(max_rounds=24)
        self.board = board_with(
            cell(20, 20), cell(21, 20), foe_hp=10, foe_max_hp=100,
            my_ap=6, my_mp=0,
            spells=[live_spell(max_range=4, max_cast_per_turn=2,
                               max_cast_per_target=2)])
        self.plans = BattleSolver(beam_width=8, max_actions=2) \
            .plan_candidates(self.board, 4)

    def test_board_projection_matches_the_trained_observation_shape(self):
        tables, spec = board_encounter(self.board, self.plans[0], self.cfg)
        state = initial_state(spec, tables, self.cfg)
        obs, glob, mask = observe(state, tables, self.cfg,
                                  acting_team=jnp.ones((1,), dtype=jnp.int32))
        self.assertEqual(obs.shape, (1, self.cfg.n_actors, 49))
        self.assertEqual(glob.shape, (1, 9))
        self.assertEqual(mask.shape, (1, self.cfg.n_actors))
        self.assertGreater(float(tables["spell_damage"][0, 0, 0]), 0.0)
        self.assertEqual(
            int(spec["first_team"][0]), 1,
            "the monsters act immediately after the player's plan")

    def test_the_chosen_live_spell_and_heal_survive_the_projection(self):
        heal = live_spell(id=2002, name="Heal", ap_cost=3, min_range=0,
                          max_range=1, max_cast_per_turn=1,
                          effects=(LiveEffect(108, 20, 20),),
                          critical_effects=())
        damage = live_spell(id=2001, name="Hit", ap_cost=3, max_range=4,
                            effects=(LiveEffect(100, 10, 10),),
                            critical_effects=())
        board = board_with(cell(20, 20), cell(21, 20), my_ap=6, my_mp=0,
                           spells=[damage, heal])
        plans = BattleSolver(beam_width=8, max_actions=2) \
            .plan_candidates(board, 4)
        tables, _spec = board_encounter(board, plans[0], self.cfg)
        heal_slot = next(
            slot for slot in range(self.cfg.spells)
            if bool(tables["spell_ally"][0, 0, slot]))
        self.assertGreater(float(tables["spell_heal"][0, 0, heal_slot]), 0.0)

    def test_enemy_rows_come_from_the_board_not_a_synthetic_loadout(self):
        from dataclasses import replace

        foe = replace(
            self.board.fighters[1],
            spells=(live_spell(id=3001, effects=(LiveEffect(97, 5, 9),)),))
        self.board.fighters[1] = foe
        tables, _spec = board_encounter(self.board, self.plans[0], self.cfg)
        self.assertGreater(float(tables["spell_damage"][0, 1, 0]), 0.0)

    def test_zero_value_head_falls_back_to_the_lethal_tactical_bonus(self):
        tables, spec = board_encounter(self.board, self.plans[0], self.cfg)
        state = initial_state(spec, tables, self.cfg)
        obs, glob, _mask = observe(state, tables, self.cfg,
                                   acting_team=jnp.ones((1,), dtype=jnp.int32))
        params = zero_params(obs.shape[-1], glob.shape[-1], self.cfg)
        self.assertAlmostEqual(
            score_plan(params, self.board, self.plans[0], self.cfg), 0.0,
            places=6)
        ranked = rank_plans(params, self.board, self.plans, self.cfg)
        self.assertEqual(len(ranked), len(self.plans))
        self.assertEqual(ranked[0][1].actions[0].kind, "cast")
        self.assertTrue(ranked[0][1].kills)
        scores = [score for score, _plan in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))


if __name__ == "__main__":
    unittest.main()
