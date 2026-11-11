"""Contract tests for the grid trainer's input and loss.

Two things here are load-bearing and neither had a test:

* the width guard in `_load`, because a corpus built for an older encoder fails
  deep inside a jitted matmul with a message that names neither the file nor the
  constant;
* the soft-target loss, because it replaced a hard index and must still be
  exactly `-log p(label)` when the solver has a unique best plan. If that
  equivalence ever breaks, every previously reported metric becomes
  incomparable and nothing would say so.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from combat import grid_dataset, grid_model, grid_train


def _corpus(path: Path, rows: int, cell_feats: int, with_targets: bool = True,
            labels=None) -> None:
    rng = np.random.default_rng(3)
    arrays = {
        "cells": rng.random((rows, 560, cell_feats), dtype=np.float32),
        "cell_mask": np.ones((rows, 560), bool),
        "fighters": rng.random((rows, grid_model.MAX_FIGHTERS,
                                grid_model.FIGHTER_FEATS), dtype=np.float32),
        "fighter_mask": np.ones((rows, grid_model.MAX_FIGHTERS), bool),
        "plans": rng.random((rows, grid_model.MAX_PLANS,
                             grid_model.PLAN_FEATS), dtype=np.float32),
        "plan_mask": np.ones((rows, grid_model.MAX_PLANS), bool),
        "scores": rng.random((rows, grid_model.MAX_PLANS), dtype=np.float32),
        "labels": np.asarray(labels if labels is not None else [0] * rows,
                             dtype=np.int64),
    }
    if with_targets:
        targets = np.zeros((rows, grid_model.MAX_PLANS), dtype=np.float32)
        targets[np.arange(rows), arrays["labels"]] = 1.0
        arrays["targets"] = targets
    np.savez(path, **arrays)


class WidthGuardTests(unittest.TestCase):
    """A corpus from a different encoder must fail loudly, not deep."""

    def test_a_narrower_corpus_is_refused_with_the_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "train.npz"
            _corpus(path, 3, cell_feats=grid_model.CELL_FEATS - 2)
            with self.assertRaises(SystemExit) as caught:
                grid_train._load(path)
            message = str(caught.exception)
            # It has to name the file, the constant, and the command that fixes it.
            self.assertIn(str(path), message)
            self.assertIn("CELL_FEATS", message)
            self.assertIn("combat.grid_synth", message)

    def test_a_wider_corpus_is_refused_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "train.npz"
            _corpus(path, 3, cell_feats=grid_model.CELL_FEATS + 1)
            with self.assertRaises(SystemExit):
                grid_train._load(path)

    def test_the_matching_width_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "train.npz"
            _corpus(path, 3, cell_feats=grid_model.CELL_FEATS)
            loaded = grid_train._load(path)
            self.assertEqual(loaded["cells"].shape[2], grid_model.CELL_FEATS)
            self.assertEqual(loaded["targets"].shape, (3, grid_model.MAX_PLANS))

    def test_a_corpus_without_soft_targets_gets_a_one_hot(self):
        """The corpus written before this change carried only `labels`."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "train.npz"
            _corpus(path, 2, cell_feats=grid_model.CELL_FEATS,
                    with_targets=False, labels=[2, 5])
            loaded = grid_train._load(path)
            targets = np.asarray(loaded["targets"])
            self.assertEqual(float(targets.sum()), 2.0)
            self.assertEqual(targets[0, 2], 1.0)
            self.assertEqual(targets[1, 5], 1.0)

    def test_the_limit_is_applied_to_targets_as_well(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "train.npz"
            _corpus(path, 6, cell_feats=grid_model.CELL_FEATS)
            loaded = grid_train._load(path, limit=2)
            self.assertEqual(loaded["cells"].shape[0], 2)
            self.assertEqual(loaded["targets"].shape[0], 2)


class SoftTargetLossTests(unittest.TestCase):
    """The loss is the KL to the solver's indifference set."""

    def setUp(self):
        import jax
        import jax.numpy as jnp
        self.jax = jax
        self.jnp = jnp
        self.params = grid_model.init_params(
            jax.random.PRNGKey(0), hidden=8, cell_layers=1,
            fighter_layers=1, plan_layers=1)
        rng = np.random.default_rng(11)
        self.base = {
            "cells": jnp.asarray(rng.random((2, 560, grid_model.CELL_FEATS),
                                            dtype=np.float32)),
            "cell_mask": jnp.ones((2, 560), bool),
            "fighters": jnp.asarray(rng.random(
                (2, grid_model.MAX_FIGHTERS, grid_model.FIGHTER_FEATS),
                dtype=np.float32)),
            "fighter_mask": jnp.ones((2, grid_model.MAX_FIGHTERS), bool),
            "plans": jnp.asarray(rng.random(
                (2, grid_model.MAX_PLANS, grid_model.PLAN_FEATS),
                dtype=np.float32)),
            "plan_mask": jnp.ones((2, grid_model.MAX_PLANS), bool),
            "scores": jnp.asarray(rng.random((2, grid_model.MAX_PLANS),
                                             dtype=np.float32)),
        }

    def _scores(self, batch):
        out = grid_model.forward(
            self.params, batch["cells"], batch["cell_mask"],
            batch["fighters"], batch["fighter_mask"],
            batch["plans"], batch["plan_mask"])
        return out["scores"]

    def test_a_one_hot_target_is_the_old_hard_label_loss(self):
        """Equivalence with the previous objective, so metrics stay comparable."""
        jnp = self.jnp
        batch = dict(self.base)
        labels = jnp.asarray(np.asarray([3, 7], dtype=np.int32))
        targets = np.zeros((2, grid_model.MAX_PLANS), np.float32)
        targets[0, 3] = 1.0
        targets[1, 7] = 1.0
        batch["targets"] = jnp.asarray(targets)
        batch["labels"] = labels

        _value, aux = grid_train._loss(self.params, batch)
        logp = self.jax.nn.log_softmax(self._scores(batch), axis=-1)
        hard = -jnp.mean(logp[jnp.arange(2), labels])
        self.assertAlmostEqual(float(aux["ce"]), float(hard), places=5)

    def test_a_tied_target_is_the_mean_of_its_winners(self):
        jnp = self.jnp
        batch = dict(self.base)
        targets = np.zeros((2, grid_model.MAX_PLANS), np.float32)
        targets[0, [1, 4]] = 0.5
        targets[1, [0, 2, 6]] = 1.0 / 3.0
        batch["targets"] = jnp.asarray(targets)
        batch["labels"] = jnp.asarray(np.asarray([1, 0], dtype=np.int32))

        _value, aux = grid_train._loss(self.params, batch)
        logp = self.jax.nn.log_softmax(self._scores(batch), axis=-1)
        manual = -jnp.mean(jnp.sum(batch["targets"] * logp, axis=-1))
        self.assertAlmostEqual(float(aux["ce"]), float(manual), places=5)
        # And it differs from a hard label on the same rows: the tie is carried.
        hard = -jnp.mean(logp[jnp.arange(2), batch["labels"]])
        self.assertGreater(abs(float(aux["ce"]) - float(hard)), 1e-4)

    def test_top1_counts_a_win_anywhere_in_the_tied_set(self):
        jnp = self.jnp
        batch = dict(self.base)
        scores = np.asarray(self._scores(batch))
        best = int(np.argmax(scores[0]))
        targets = np.zeros((2, grid_model.MAX_PLANS), np.float32)
        # Board 0: the model's own top pick is in the winning set.
        targets[0, best] = 1.0
        # Board 1: the winning set excludes the model's pick by construction.
        targets[1, (best + 1) % grid_model.MAX_PLANS] = 1.0
        batch["targets"] = jnp.asarray(targets)
        batch["labels"] = jnp.asarray(np.asarray([best, best], dtype=np.int32))

        metrics = grid_train.evaluate(self.params, batch, 2)
        self.assertAlmostEqual(metrics["top1"], 0.5, places=6)


if __name__ == "__main__":
    unittest.main()
