"""Local regression witnesses for first-action distillation (no remote dependencies)."""
from __future__ import annotations

import copy
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import pickle
import random
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import distill_beam as db
import jax
import jax.numpy as jnp
import numpy as np
from combat.battle_solver import Board, PlannedAction, _POINTS
from combat.encounter import backend, model
from combat.encounter.env import Config
from combat.live_policy import LivePolicy
from combat.plan_value import board_encounter


def payload(hp=100):
    def spell(ident, damage, heal=0):
        return {"id": ident, "name": str(ident), "ap_cost": 2,
                "min_range": 0 if heal else 1, "range": 6,
                "requires_los": False, "castable": True,
                "effects": [{"effect_id": 108 if heal else 100,
                             "dice_num": heal or damage, "dice_side": heal or damage}]}

    spells = [spell(0, 6), spell(10, 12), spell(20, 0, 8)]
    fighters = []
    for ident, point in ((1, (10, 10)), (2, (11, 10)), (3, (10, 12))):
        fighters.append({"id": ident, "name": str(ident), "cell": _POINTS[point],
                         "team": "player" if ident == 1 else "enemy", "is_me": ident == 1,
                         "hp": hp if ident == 1 else 80, "max_hp": 100,
                         "ap": 4, "max_ap": 6, "mp": 2, "max_mp": 3,
                         "level": 10, "alive": True, "spells": spells})
    return {"in_fight": True, "is_my_turn": True, "my_id": 1,
            "my_cell": fighters[0]["cell"], "round": 3, "cells": [3] * 560,
            "fighters": fighters, "spells": spells, "my_stats": {}}


def policy_stub(seed=4):
    policy = LivePolicy.__new__(LivePolicy)
    policy.cfg = Config(max_rounds=24)
    # Toy network only. The trainer never initializes or changes model width.
    policy.params = model.init_params(jax.random.PRNGKey(seed), 49, 9, policy.cfg,
                                      hidden=16, layers=1)
    return policy


def row(action=None, ids=(10, 11), hp=100, winner=1):
    return {"matchup": f"{ids[0]}-{ids[1]}", "combatant_ids": list(ids),
            "actor_id": 1, "winner_id": winner, "outcome": db.actor_outcome(winner, 1),
            "payload": payload(hp), "first_action": None,
            "action": action or {"type": "cast", "spellId": 10, "targetId": 2,
                                  "cell": _POINTS[(11, 10)]}}


def toy_data(policy):
    rows = [row(hp=100), row(hp=90),
            row({"type": "cast", "spellId": 0, "targetId": 3}, hp=20, winner=2),
            row({"type": "cast", "spellId": 0, "targetId": 3}, hp=30, winner=2)]
    dataset, reasons = db.prepare_dataset(policy, rows)
    assert not reasons, reasons
    return dataset


def corpus_fixture():
    rows = [row(ids=(2 * i, 2 * i + 1), hp=100 - i) for i in range(6)]
    return {"version": db.CORPUS_VERSION, "rows": rows, "duels": [],
            "manifest": {"teacher": {"type": "beam", "beam_width": 8,
                                      "max_actions": 2, "plan_k": 6},
                         "splits": db.make_splits(rows, 11)}}


class LabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = policy_stub()

    def setUp(self):
        self.board = Board.from_fight_state(payload())
        _, self.masks, self.slots, self.targets = db.board_inputs(self.policy, self.board)

    def label(self, action):
        return db.action_label(self.policy, self.board, action,
                               self.masks, self.slots, self.targets)

    def test_first_move_not_later_cast_or_final_cell(self):
        actions = [PlannedAction("move", from_cell=self.board.my_cell, move_to=123),
                   PlannedAction("cast", spell_id=10, target_id=2, from_cell=123)]
        plan = SimpleNamespace(actions=actions, final_cell=456)
        self.assertEqual(db.first_action(self.board, plan), {"type": "move", "cell": 123})
        plan.actions = [PlannedAction("pass"), actions[1]]
        self.assertEqual(db.first_action(self.board, plan), {"type": "pass"})
        plan.actions = []
        self.assertEqual(db.first_action(self.board, plan), {"type": "pass"})

    def test_embedded_walk_cast_normalizes_to_first_client_move(self):
        first = PlannedAction("cast", spell_id=10, target_id=2, from_cell=123)
        plan = SimpleNamespace(actions=[first], final_cell=456)
        self.assertEqual(db.first_action(self.board, plan), {"type": "move", "cell": 123})
        plan.actions = [replace(first, from_cell=self.board.my_cell)]
        self.assertEqual(db.first_action(self.board, plan)["type"], "cast")

    def test_hold_pass_semantics(self):
        for kind in ("hold", "pass"):
            label, reasons = self.label({"type": kind})
            self.assertFalse(reasons)
            self.assertEqual([label[k] for k in db.COMPONENTS], [1, 4, 0])
            self.assertEqual([label[k + "_weight"] for k in db.COMPONENTS], [1, 1, 0])

    def test_move_directions_require_exact_walk_destination(self):
        # Move the player farther away so both approach and retreat exist.
        self.board.my_cell = _POINTS[(10, 15)]
        self.board.fighters[0] = replace(self.board.me, cell=self.board.my_cell)
        reference = self.policy._reference_target(self.board, self.targets[0])
        for direction in (0, 2):
            walk = self.policy._walk_action(self.board, reference, direction)
            self.assertIsNotNone(walk)
            label, reasons = self.label(walk)
            self.assertFalse(reasons)
            self.assertEqual([label[k] for k in db.COMPONENTS], [direction, 4, 0])
            self.assertEqual(label["move_weight"], 1)
        label, reasons = self.label({"type": "move", "cell": self.board.my_cell})
        self.assertEqual(reasons, ["unrepresentable_move"])
        self.assertTrue(all(label[k + "_weight"] == 0 for k in db.COMPONENTS))

    def test_direct_cast_binds_weapon_zero_and_does_not_supervise_move(self):
        label, reasons = self.label({"type": "cast", "spellId": 0, "targetId": 3})
        self.assertFalse(reasons)
        self.assertEqual(self.slots[label["spell"]]["spell_id"], 0)
        self.assertFalse(self.slots[label["spell"]]["empty"])
        self.assertEqual(label["target"], 2)
        self.assertEqual([label[k + "_weight"] for k in db.COMPONENTS], [0, 1, 1])

    def test_unbound_ids_and_illegal_masks_are_explicit(self):
        label, reasons = self.label({"type": "cast", "spellId": 999, "targetId": 999})
        self.assertEqual(set(reasons), {"unbound_spell_id", "unbound_target_id"})
        self.assertEqual(label["spell_weight"], 0)
        for component in ("spell", "target"):
            with self.subTest(component=component):
                saved = self.masks[component]
                self.masks[component] = np.zeros_like(saved)
                label, reasons = self.label({"type": "cast", "spellId": 10, "targetId": 2})
                self.assertIn("illegal_" + component + "_mask", reasons)
                self.assertEqual(label["spell_weight"], 0)
                self.masks[component] = saved

    def test_heal_target_and_actual_cell_legality(self):
        label, reasons = self.label({"type": "cast", "spellId": 20, "targetId": 1})
        self.assertFalse(reasons)
        self.assertEqual(label["target"], 0)
        _, reasons = self.label({"type": "cast", "spellId": 20, "targetId": 2})
        self.assertIn("illegal_target_mask", reasons)
        _, reasons = self.label({"type": "cast", "spellId": 10, "targetId": 2, "cell": 123})
        self.assertEqual(reasons, ["unrepresentable_direct_cast"])

    def test_unrepresentable_actions_keep_outcome_training(self):
        data, reasons = db.prepare_dataset(self.policy, [
            row({"type": "move", "cell": -123}, winner=2),
            row({"type": "cast", "spellId": 999, "targetId": 999}, winner=None)])
        self.assertEqual(reasons, {"unrepresentable_move": 1, "unbound_spell_id": 1,
                                   "unbound_target_id": 1})
        np.testing.assert_array_equal(data["outcome"], [-1, 0])
        for key in db.COMPONENTS:
            np.testing.assert_array_equal(data[key + "_weight"], [0, 0])
        batch = {k: jnp.asarray(v) for k, v in data.items()}
        (loss, aux), grads = jax.value_and_grad(db.make_loss(), has_aux=True)(self.policy.params, batch)
        self.assertEqual(float(loss), float(aux["value_mse"]))
        self.assertGreater(db.gradient_stats(grads)["group_norms"]["value"], 0)

    def test_input_forward_matches_live_bundle_current_resources_and_round(self):
        inputs, masks, _, _ = db.board_inputs(self.policy, self.board)
        _, player_rows = self.policy._bind_slots(self.board)
        tables, spec = board_encounter(self.board, self.policy._current_plan(self.board),
                                       self.policy.cfg, first_team=0,
                                       player_rows=player_rows, as_jax=False)
        expected, live_masks = self.policy._forward_bundle(self.policy.params, spec, tables,
                                                          4.0, 2.0, 3)
        actual = model.forward(self.policy.params, inputs["obs"][None], inputs["glob"][None],
                               inputs["attention"][None])
        for key in expected:
            np.testing.assert_allclose(actual[key], expected[key], atol=1e-6)
        for key in db.COMPONENTS:
            np.testing.assert_array_equal(masks[key], live_masks[key][0, 0])
        self.assertAlmostEqual(float(inputs["obs"][0, 3]), 4 / 6)
        self.assertAlmostEqual(float(inputs["obs"][0, 4]), 2 / 3)
        self.assertAlmostEqual(float(inputs["glob"][2]), 3 / 24)


class GradientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = policy_stub()
        cls.data = toy_data(cls.policy)
        cls.batch = {k: jnp.asarray(v) for k, v in cls.data.items()}

    def witness(self):
        (value, _), grads = jax.value_and_grad(db.make_loss(), has_aux=True)(
            self.policy.params, self.batch)
        self.assertTrue(np.isfinite(float(value)))
        stats = db.gradient_stats(grads)
        for group in ("trunk", "spell", "value"):
            self.assertGreater(stats["group_norms"][group], 1e-7, group)
        self.assertGreater(float(jnp.linalg.norm(grads["enc1"][0])), 1e-7)
        return grads

    def test_nonzero_gradients_reach_trunk_spell_and_value(self):
        self.witness()

    def test_cached_and_stop_gradient_mutations_fail_same_witness(self):
        original = model.forward
        cached = original(self.policy.params, self.batch["obs"], self.batch["glob"],
                          self.batch["attention"])
        for mutation in (lambda *a, **k: cached,
                         lambda *a, **k: jax.tree_util.tree_map(jax.lax.stop_gradient, original(*a, **k))):
            with patch.object(model, "forward", side_effect=mutation):
                with self.assertRaisesRegex(RuntimeError, "zero.*gradients"):
                    self.witness()

    def test_fit_rejects_cached_logits_before_any_update(self):
        cached = model.forward(self.policy.params, self.batch["obs"], self.batch["glob"],
                               self.batch["attention"])
        with patch.object(model, "forward", return_value=cached):
            with self.assertRaisesRegex(RuntimeError, "zero.*gradients"):
                db.fit(self.policy.params, self.data, iters=2, batch_size=4, lr=.001, seed=4)

    def test_small_real_observation_batch_overfits(self):
        before = db.evaluate(self.policy.params, self.data)
        with redirect_stdout(io.StringIO()):
            params, info = db.fit(self.policy.params, self.data, iters=150,
                                  batch_size=4, lr=.003, seed=4)
        after = db.evaluate(params, self.data)
        self.assertGreater(info["changed_leaves"], 0)
        self.assertLess(after["spell_ce"], before["spell_ce"] * .15)
        self.assertLess(after["value_mse"], .04)
        self.assertEqual(after["spell_accuracy"], 1)
        self.assertEqual(after["target_accuracy"], 1)

    def test_parameter_change_and_nonfinite_guards(self):
        with self.assertRaisesRegex(RuntimeError, "no parameter change"):
            db.changed_leaves(self.policy.params, self.policy.params)
        grads = jax.tree_util.tree_map(jnp.ones_like, self.policy.params)
        grads["t_bias"] = grads["t_bias"].at[0, 0].set(jnp.nan)
        with self.assertRaisesRegex(RuntimeError, "nonfinite gradients"):
            db.gradient_stats(grads)
        with patch.object(backend, "BACKEND", "numpy"):
            with self.assertRaisesRegex(RuntimeError, "JAX backend"):
                db.make_loss()

    def test_target_ce_is_spell_conditioned_and_masks_block_illegal_gradients(self):
        batch = {k: v[:1] for k, v in self.batch.items()}
        selected = int(batch["spell"][0])
        target = int(batch["target"][0])
        output = model.forward(self.policy.params, batch["obs"], batch["glob"], batch["attention"])
        # Different values per spell expose accidental flattening over spell+target.
        output["target"] = jnp.arange(output["target"].size, dtype=jnp.float32).reshape(
            output["target"].shape) / 7
        batch["move_weight"] = jnp.ones((1,))
        batch["move_mask"] = jnp.array([[True, True, False]])

        def objective(logits):
            with patch.object(model, "forward", return_value=logits):
                return db.make_loss()(self.policy.params, batch)

        (_, aux), grads = jax.value_and_grad(objective, has_aux=True)(output)
        mask = np.asarray(batch["target_mask"][0, selected])
        legal = np.asarray(output["target"][0, 0, selected])[mask]
        expected = np.log(np.exp(legal - legal.max()).sum()) + legal.max()
        expected -= float(output["target"][0, 0, selected, target])
        self.assertAlmostEqual(float(aux["target_ce"]), expected, places=5)
        tg = np.asarray(grads["target"])
        self.assertTrue(np.all(tg[:, 1:] == 0))  # actor zero only
        for spell in range(5):
            if spell != selected:
                self.assertTrue(np.all(tg[:, 0, spell] == 0))
        self.assertTrue(np.all(tg[0, 0, selected, ~mask] == 0))
        self.assertEqual(float(grads["move"][0, 0, 2]), 0)
        self.assertTrue(np.all(np.asarray(grads["spell"])[0, 0, ~batch["spell_mask"][0]] == 0))

    def test_pass_target_has_no_loss_even_with_adversarial_target_logits(self):
        data, _ = db.prepare_dataset(self.policy, [row({"type": "pass"})])
        batch = {k: jnp.asarray(v) for k, v in data.items()}
        output = model.forward(self.policy.params, batch["obs"], batch["glob"], batch["attention"])
        output["target"] = jnp.full_like(output["target"], 10000)
        with patch.object(model, "forward", return_value=output):
            _, aux = db.make_loss()(self.policy.params, batch)
        self.assertEqual(float(aux["target_ce"]), 0)

    def test_neural_kl_uses_teacher_params_same_inputs_and_retains_outcomes(self):
        teacher = policy_stub(seed=9)
        original = model.forward
        with patch.object(model, "forward", wraps=original) as forward:
            _, aux = db.make_loss(teacher.params, 2.0)(self.policy.params, self.batch)
        self.assertEqual(forward.call_count, 2)
        self.assertIs(forward.call_args_list[0].args[0], self.policy.params)
        self.assertIs(forward.call_args_list[1].args[0], teacher.params)
        for i in (1, 2, 3):
            self.assertIs(forward.call_args_list[0].args[i], forward.call_args_list[1].args[i])
        _, hard = db.make_loss()(self.policy.params, self.batch)
        self.assertEqual(float(hard["value_mse"]), float(aux["value_mse"]))
        self.assertGreater(float(aux["spell_kl"]), 0)
        self.assertGreater(float(aux["target_kl"]), 0)
        _, identical = db.make_loss(self.policy.params, 2.0)(self.policy.params, self.batch)
        for component in db.COMPONENTS:
            self.assertAlmostEqual(float(identical[component + "_kl"]), 0, places=6)

    def test_neural_target_kl_is_conditional_legal_and_temperature_scaled(self):
        batch = {k: v[:1] for k, v in self.batch.items()}
        student = model.forward(self.policy.params, batch["obs"], batch["glob"], batch["attention"])
        teacher = {k: jnp.zeros_like(v) for k, v in student.items()}
        student = {k: jnp.zeros_like(v) for k, v in student.items()}
        student["target"] = student["target"].at[0, 0, 0, 1].set(2.0)
        # Masked padding, self-targets, and pass must not enter target KL.
        student["target"] = student["target"].at[0, 0, 0, 4].set(1e6)
        student["target"] = student["target"].at[0, 0, 4, :].set(-1e6)
        teacher_params = policy_stub(9).params
        with patch.object(model, "forward", side_effect=[student, teacher]):
            _, aux = db.make_loss(teacher_params, 2.0)(self.policy.params, batch)
        p = np.array([.5, .5])
        q = np.exp([1.0, 0.0]) / np.exp([1.0, 0.0]).sum()
        spell_prob = 1.0 / int(batch["spell_mask"][0].sum())
        expected = float(np.sum(p * np.log(p / q))) * spell_prob * 4
        self.assertAlmostEqual(float(aux["target_kl"]), expected, places=5)

    def test_save_reload_forward_parity(self):
        with redirect_stdout(io.StringIO()):
            params, _ = db.fit(self.policy.params, self.data, iters=2, batch_size=4,
                                lr=.001, seed=3)
        source = {"params": self.policy.params, "config": asdict(self.policy.cfg),
                  "metrics": [{"old": True}], "feat_dim": 49, "global_dim": 9}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "student.pkl"
            db.save_checkpoint(path, source, params, {"test": True})
            restored = LivePolicy(path)
            for key, expected in model.forward(params, self.batch["obs"], self.batch["glob"],
                                                self.batch["attention"]).items():
                actual = model.forward(restored.params, self.batch["obs"], self.batch["glob"],
                                       self.batch["attention"])[key]
                np.testing.assert_array_equal(actual, expected)
            saved = pickle.loads(path.read_bytes())
            self.assertEqual(saved["metrics"], source["metrics"])
            self.assertEqual(saved["distillation"], {"test": True})
            with self.assertRaises(FileExistsError):
                db.save_checkpoint(path, source, params, {})


class CorpusAndCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = policy_stub()

    def test_harvest_records_first_action_then_assigns_final_actor_outcomes(self):
        from combat import duel as du
        board = Board.from_fight_state(payload())
        plan = SimpleNamespace(actions=[PlannedAction("move", move_to=123),
                                        PlannedAction("cast", spell_id=10, target_id=2)],
                               final_cell=456)
        data = unittest.mock.MagicMock()
        data.__enter__.return_value = {"hp": np.zeros((6, 1))}

        def build(*args, **kwargs):
            return SimpleNamespace(round=7, done=False, winner=None, turns=0,
                                   fighters=[SimpleNamespace(id=1, hp=10), SimpleNamespace(id=2, hp=0)])

        def play(state, policies, solver, rng, k):
            self.assertEqual((solver.beam_width, solver.max_actions, k), (3, 1, 2))
            for actor_id in (1, 2):
                policies[actor_id](state, SimpleNamespace(id=actor_id), [plan], rng)
            state.done, state.winner, state.turns = True, 1, 2
            return state, []

        def board_for(state, actor):
            result = copy.deepcopy(board)
            result.my_id = actor.id
            return result

        with patch("numpy.load", return_value=data), patch.object(du, "load_map", return_value=board.cells), \
                patch.object(du, "make_duel", side_effect=build), patch.object(du, "play_game", side_effect=play), \
                patch.object(du, "board_for", side_effect=board_for), patch.object(db, "sha256", return_value="test"):
            corpus = db.harvest(3, 4, beam_width=3, max_actions=1, plan_k=2)
        db.validate_corpus(corpus)
        self.assertEqual(len(corpus["rows"]), 6)
        self.assertEqual([r["outcome"] for r in corpus["rows"]], [1, -1] * 3)
        for item in corpus["rows"]:
            self.assertEqual(item["action"], {"type": "move", "cell": 123})
            self.assertEqual(item["first_action"]["kind"], "move")
            self.assertEqual(item["payload"]["round"], 7)
        self.assertEqual([d["winner_id"] for d in corpus["duels"]], [1] * 3)

    def test_harvest_does_not_swallow_engine_errors(self):
        from combat import duel as du
        data = unittest.mock.MagicMock()
        data.__enter__.return_value = {"hp": np.zeros((6, 1))}
        with patch("numpy.load", return_value=data), patch.object(du, "load_map"), \
                patch.object(du, "make_duel", return_value=object()), \
                patch.object(du, "play_game", side_effect=RuntimeError("broken duel")):
            with self.assertRaisesRegex(RuntimeError, "broken duel"):
                db.harvest(3, 4)

    def test_split_is_disjoint_by_ids_even_for_connected_matchups(self):
        rows = [row(ids=ids) for ids in ((0, 1), (1, 2), (10, 11), (20, 21), (30, 31))]
        splits = db.make_splits(rows, 5)
        ids = [set(s["combatant_ids"]) for s in splits.values()]
        for i in range(3):
            for j in range(i):
                self.assertFalse(ids[i] & ids[j])
        owner = {key: name for name, split in splits.items() for key in split["matchups"]}
        self.assertEqual(owner["0-1"], owner["1-2"])
        self.assertEqual(splits, db.make_splits(rows, 5))

    def test_corpus_roundtrip_outcome_validation_and_leak_rejection(self):
        corpus = corpus_fixture()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corpus.json"
            db.save_corpus(path, corpus)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded, corpus)
            db.validate_corpus(loaded)
        loaded["rows"][0]["outcome"] = -1
        with self.assertRaisesRegex(ValueError, "outcome"):
            db.validate_corpus(loaded)
        bad = copy.deepcopy(corpus)
        bad["manifest"]["splits"]["test"] = bad["manifest"]["splits"]["train"]
        with self.assertRaisesRegex(ValueError, "overlapping"):
            db.validate_corpus(bad)
        self.assertEqual([db.actor_outcome(w, 2) for w in (1, 2, None)], [-1, 1, 0])

    def test_safe_paths_and_checkpoint_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init, teacher, corpus, out = [root / k for k in ("init", "teacher", "corpus", "out")]
            for destination in (init, teacher, corpus):
                with self.assertRaisesRegex(ValueError, "separate"):
                    db.safe_paths(init, teacher, corpus, destination)
            with self.assertRaisesRegex(ValueError, "separate"):
                db.safe_paths(init, teacher, init, out)
        db.validate_checkpoint(self.policy, {})
        with self.assertRaisesRegex(ValueError, "feat_dim"):
            db.validate_checkpoint(self.policy, {"feat_dim": 99})
        wrong = copy.copy(self.policy)
        wrong.params = dict(wrong.params, t_bias=jnp.zeros((5, 2)))
        with self.assertRaises((ValueError, TypeError)):
            db.validate_checkpoint(wrong, {})

    def test_cli_harvest_only_saves_then_training_reuses_permanent_splits(self):
        corpus = corpus_fixture()
        source = {"params": jax.tree_util.tree_map(np.asarray, self.policy.params),
                  "config": asdict(self.policy.cfg), "feat_dim": 49, "global_dim": 9}
        teacher = policy_stub(9)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init, teacher_path, path, out, table = [root / k for k in
                                                    ("init.pkl", "teacher.pkl", "corpus.json",
                                                     "out.pkl", "train.npz")]
            np.savez_compressed(table, hp=np.zeros((6, 1), dtype=np.int32))
            init.write_bytes(pickle.dumps(source))
            teacher_path.write_bytes(pickle.dumps(dict(source, params=teacher.params)))
            corpus["manifest"]["sources"] = {"data_sha256": db.sha256(table)}
            argv = ["--checkpoint", str(init), "--corpus", str(path), "--out", str(out),
                    "--data", str(table), "--games", "6", "--beam-width", "3",
                    "--max-actions", "1", "--plan-k", "2"]
            with patch.object(db, "harvest", return_value=corpus) as harvest, redirect_stdout(io.StringIO()):
                self.assertEqual(db.main(argv + ["--harvest-only"]), 0)
            harvest.assert_called_once_with(6, 11, 3, 1, 2, table)
            self.assertTrue(path.exists())
            self.assertFalse(out.exists())
            old_hash, init_hash = db.sha256(path), db.sha256(init)
            train_keys = set(corpus["manifest"]["splits"]["train"]["matchups"])
            expected, _ = db.prepare_dataset(self.policy, [r for r in corpus["rows"]
                                                          if r["matchup"] in train_keys])
            real_fit = db.fit
            events = []

            def fit(params, dataset, **kwargs):
                events.append("fit")
                np.testing.assert_array_equal(dataset["obs"], expected["obs"])
                return real_fit(params, dataset, **kwargs)

            real_eval = db.evaluate

            def evaluate(*args, **kwargs):
                self.assertEqual(events[0], "fit")
                events.append("evaluate")
                return real_eval(*args, **kwargs)

            with patch.object(db, "harvest", side_effect=AssertionError("must reuse corpus")), \
                    patch.object(db, "fit", side_effect=fit), \
                    patch.object(db, "evaluate", side_effect=evaluate), redirect_stdout(io.StringIO()):
                self.assertEqual(db.main(argv + ["--iters", "2", "--batch", "2", "--lr", ".001",
                                                 "--seed", "99", "--teacher-checkpoint", str(teacher_path),
                                                 "--temperature", "2"]), 0)
            self.assertEqual(events, ["fit", "evaluate", "evaluate", "evaluate"])
            manifest = pickle.loads(out.read_bytes())["distillation"]
            self.assertEqual(manifest["splits"], corpus["manifest"]["splits"])
            self.assertEqual(manifest["source_checkpoint"]["sha256"], init_hash)
            self.assertEqual(manifest["neural_teacher"]["sha256"], db.sha256(teacher_path))
            self.assertGreater(manifest["gradients"]["changed_leaves"], 0)
            self.assertIn("value_mse", manifest["metrics"]["test"])
            self.assertEqual(manifest["selection"], "fixed_final_iteration_no_selection")
            self.assertEqual(db.sha256(path), old_hash)
            self.assertEqual(db.sha256(init), init_hash)


class BackendDiagnosisTests(unittest.TestCase):
    """A machine that has jax but cannot load it must not be reported as inference-only.

    The Kaggle control run died on "numpy is inference-only" while jax was installed and
    importable in the process before it; the message named a policy, not the cause.
    """

    CHILD = """
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name == "jax" or name.startswith("jax."):
            raise ImportError("injected: jaxlib would not load")
        return None
sys.meta_path.insert(0, Block())
from combat.encounter import backend
print("BACKEND", backend.BACKEND)
print("REASON", backend.JAX_IMPORT_ERROR)
sys.path.insert(0, "tools")
import distill_beam
try:
    distill_beam.require_jax()
except RuntimeError as error:
    print("REFUSAL", error)
else:
    raise AssertionError("require_jax accepted a numpy backend")
"""

    def run_child(self, env=None):
        import os
        settings = dict(os.environ)
        settings.pop("DOFUS_FIGHT_BACKEND", None)
        settings.update(env or {})
        done = subprocess.run([sys.executable, "-c", self.CHILD], cwd=str(db.REPO_ROOT),
                              capture_output=True, text=True, env=settings)
        self.assertEqual(done.returncode, 0, done.stderr[-500:])
        return done.stdout

    def test_broken_jax_names_the_import_error(self):
        output = self.run_child()
        self.assertIn("BACKEND numpy", output)
        self.assertIn("injected: jaxlib would not load", output)
        self.assertIn("REFUSAL", output)
        refusal = [line for line in output.splitlines() if line.startswith("REFUSAL")][0]
        self.assertIn("injected: jaxlib would not load", refusal)
        self.assertIn("backend=numpy", refusal)

    def test_forced_numpy_names_the_switch_not_a_broken_install(self):
        output = self.run_child({"DOFUS_FIGHT_BACKEND": "numpy"})
        refusal = [line for line in output.splitlines() if line.startswith("REFUSAL")][0]
        self.assertIn("DOFUS_FIGHT_BACKEND=numpy", refusal)

    def test_healthy_import_leaves_no_error_recorded(self):
        self.assertEqual(backend.BACKEND, "jax")
        self.assertIsNone(backend.JAX_IMPORT_ERROR)


if __name__ == "__main__":
    unittest.main()
