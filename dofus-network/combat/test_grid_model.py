"""Tests for the 30M 560-cell combat encoder and its data gate."""

from __future__ import annotations

import unittest

import jax
import jax.numpy as jnp

from combat import grid_dataset, grid_model


class GridModelTests(unittest.TestCase):
    def test_default_parameter_count_is_about_30m(self):
        params = grid_model.init_params(jax.random.PRNGKey(0))
        count = grid_model.param_count(params)
        self.assertGreaterEqual(count, 28_000_000)
        self.assertLessEqual(count, 34_000_000)
        self.assertEqual(count, 30_766_081)

    def test_forward_shapes_are_finite(self):
        params = grid_model.init_params(
            jax.random.PRNGKey(1), hidden=64, cell_layers=1,
            fighter_layers=1, plan_layers=1)
        cells = jnp.zeros((2, 560, grid_model.CELL_FEATS), dtype=jnp.float32)
        cell_mask = jnp.ones((2, 560), dtype=bool)
        fighters = jnp.zeros(
            (2, grid_model.MAX_FIGHTERS, grid_model.FIGHTER_FEATS),
            dtype=jnp.float32)
        fighter_mask = jnp.ones((2, grid_model.MAX_FIGHTERS), dtype=bool)
        plans = jnp.zeros((2, grid_model.MAX_PLANS, grid_model.PLAN_FEATS),
                          dtype=jnp.float32)
        plan_mask = jnp.ones((2, grid_model.MAX_PLANS), dtype=bool)
        out = grid_model.forward(params, cells, cell_mask, fighters,
                                 fighter_mask, plans, plan_mask)
        self.assertEqual(out["scores"].shape, (2, grid_model.MAX_PLANS))
        self.assertEqual(out["value"].shape, (2,))
        self.assertTrue(bool(jnp.isfinite(out["scores"]).all()))
        self.assertTrue(bool(jnp.isfinite(out["value"]).all()))

    def test_empty_corpus_is_refused_not_trained(self):
        arrays, manifest = grid_dataset.build([])
        self.assertEqual(arrays, {})
        self.assertEqual(manifest["rows"], 0)
        self.assertIn("not trained", manifest["verdict"])


if __name__ == "__main__":
    unittest.main()
