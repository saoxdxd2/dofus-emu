import contextlib
import hashlib
import io
import json
import os
import pathlib
import pickle
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import wsp_fight as wf
import wsp_program as wsp


class ReachabilityTests(unittest.TestCase):
    def test_walk_reachable_interval_intersects_spell_range(self):
        for case in ((8, 3, 1, 5, True), (8, 2, 1, 5, False),
                    (10, 2, 8, 12, True), (10, 2, 13, 15, False),
                    (4, 2, 5, 8, True), (4, 0, 5, 8, False)):
            with self.subTest(case=case):
                self.assertEqual(wsp.reachable_after_walk(*case[:4]), case[4])

    def test_invalid_geometry_is_not_reported_reachable(self):
        self.assertFalse(wsp.reachable_after_walk(8, -1, 1, 5))
        self.assertFalse(wsp.reachable_after_walk(float("nan"), 3, 1, 5))


class CacheIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.source = self.root / "source.pkl"
        self.payload = {"params": {
            "spell": (np.array([[1., 0.]], dtype=np.float32), np.zeros(2, np.float32)),
            "sentinel": np.array([37.], dtype=np.float32),
        }}
        self.source.write_bytes(pickle.dumps(self.payload))
        self.cache = self.root / "cache.npz"
        self.output = self.root / "edit.pkl"
        self.arrays = {
            "hidden": np.ones((8, 1), np.float32),
            "spell_mask": np.ones((8, 2), bool),
            "teacher_spell": np.ones(8, int),
            "head_spell": np.zeros(8, int),
            "checkpoint_path": np.asarray(str(self.source)),
            "checkpoint_sha256": np.asarray(hashlib.sha256(self.source.read_bytes()).hexdigest()),
        }
        self.write_cache()

    def write_cache(self):
        np.savez_compressed(self.cache, **self.arrays)

    def edit(self, checkpoint=None, head="spell", output=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return wsp.edit(self.cache, head, output or self.output, checkpoint, [1.])

    def test_default_uses_cache_source_and_only_edits_head(self):
        self.assertEqual(self.edit(), 0)
        result = pickle.loads(self.output.read_bytes())
        np.testing.assert_array_equal(result["params"]["sentinel"], self.payload["params"]["sentinel"])
        np.testing.assert_array_equal(result["params"]["spell"][1], self.payload["params"]["spell"][1])
        self.assertEqual(int(np.argmax(result["params"]["spell"][0])), 1)
        self.assertEqual(result["wsp_source"]["sha256"], self.arrays["checkpoint_sha256"].item())

    def test_explicit_identical_copy_is_accepted(self):
        copy = self.root / "copy.pkl"
        copy.write_bytes(self.source.read_bytes())
        self.assertEqual(self.edit(copy), 0)

    def test_wrong_checkpoint_rejected_for_every_edit_path(self):
        other = self.root / "other.pkl"
        other.write_bytes(pickle.dumps({"params": {"sentinel": 99}}))
        for head in ("spell", "t_bias", "move"):
            with self.subTest(head=head), self.assertRaisesRegex(ValueError, "SHA-256"):
                self.edit(other, head)
        self.assertFalse(self.output.exists())

    def test_modified_source_rejected(self):
        self.source.write_bytes(self.source.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.edit()
        self.assertFalse(self.output.exists())

    def test_identityless_cache_rejected(self):
        del self.arrays["checkpoint_sha256"]
        self.write_cache()
        with self.assertRaisesRegex(ValueError, "rebuild"):
            self.edit(self.source)
        self.assertFalse(self.output.exists())

    def test_source_overwrite_rejected(self):
        before = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.edit(output=self.source)
        self.assertEqual(before, self.source.read_bytes())

    def test_cli_edit_does_not_supply_default_checkpoint(self):
        with patch.object(wsp, "edit", return_value=0) as edit:
            self.assertEqual(wsp.main(["edit", "--cache", str(self.cache), "--out", str(self.output)]), 0)
        self.assertIsNone(edit.call_args.args[3])

    def test_cli_cache_retains_default_checkpoint(self):
        with patch.object(wsp, "cache_boards") as cache:
            self.assertEqual(wsp.main(["cache", "--out", str(self.cache)]), 0)
        self.assertEqual(cache.call_args.args[2], wsp.CHECKPOINT)


class BackendLeakTests(unittest.TestCase):
    """Importing a measurement tool must not reconfigure the process that imports it.

    `wsp_program` and `wsp_fight` default to the installed copy's numpy backend. Written at
    import time, that default leaked into every pipeline that imported them -- and one such
    pipeline then could not train, because it had silently forced the inference backend.
    """

    ROOT = pathlib.Path(__file__).resolve().parent.parent

    def run_child(self, code):
        settings = dict(os.environ)
        settings.pop("DOFUS_FIGHT_BACKEND", None)
        done = subprocess.run([sys.executable, "-c", code], cwd=str(self.ROOT),
                              capture_output=True, text=True, env=settings)
        self.assertEqual(done.returncode, 0, done.stderr[-400:])
        return done.stdout.strip()

    def test_importing_the_tool_does_not_set_the_backend(self):
        for module in ("wsp_program", "wsp_fight"):
            with self.subTest(module=module):
                output = self.run_child(
                    "import sys; sys.path.insert(0, 'tools');"
                    "import %s;"
                    "print(repr(__import__('os').environ.get('DOFUS_FIGHT_BACKEND')))"
                    % module)
                self.assertEqual(output, "None",
                                 "%s forced the inference backend on its importer" % module)

    def test_help_does_not_select_a_backend(self):
        output = self.run_child(
            "import os, runpy, sys; sys.argv = ['wsp_program.py', '--help']\n"
            "try:\n"
            "    runpy.run_path('tools/wsp_program.py', run_name='__main__')\n"
            "except SystemExit:\n"
            "    pass\n"
            "print(os.environ.get('DOFUS_FIGHT_BACKEND'))")
        self.assertEqual(output.splitlines()[-1], "None")


class _FakeCells:
    def __init__(self, distances):
        self._distances = distances

    def distance(self, a, b):
        return self._distances[(a, b)]


class _FakeBoard:
    def __init__(self, my_cell, distances):
        self.my_cell = my_cell
        self.cells = _FakeCells(distances)


class TeacherMoveTests(unittest.TestCase):
    """The beam's walk, read as the three directions the encounter model can express."""

    def test_walk_toward_the_aim_is_approach_and_away_is_retreat(self):
        cells = {(5, 9): 4, (3, 9): 6, (7, 9): 2}
        self.assertEqual(wsp.teacher_move_class(_FakeBoard(5, cells), 9, 7), 0)
        self.assertEqual(wsp.teacher_move_class(_FakeBoard(5, cells), 9, 3), 2)
        self.assertEqual(wsp.teacher_move_class(_FakeBoard(5, cells), 9, 5), 1)

    def test_no_aim_or_no_plan_names_no_direction(self):
        cells = {(5, 9): 4}
        self.assertIsNone(wsp.teacher_move_class(_FakeBoard(5, cells), None, 7))
        self.assertIsNone(wsp.teacher_move_class(_FakeBoard(5, cells), 9, None))


class MoveEditTests(unittest.TestCase):
    """A rank-1 move-head edit: exact on the cached logits, refused when it would not be."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.source = self.root / "source.pkl"
        # One hidden unit, three move directions: logits are [0, h, 0], so the argmax is hold.
        self.payload = {"params": {
            "move": (np.array([[0., 1., 0.]], dtype=np.float32), np.zeros(3, np.float32)),
            "sentinel": np.array([37.], dtype=np.float32),
        }}
        self.source.write_bytes(pickle.dumps(self.payload))
        self.cache = self.root / "cache.npz"
        self.output = self.root / "arm_move.pkl"
        self.arrays = {
            "hidden": np.ones((8, 1), np.float32),
            "move_mask": np.ones((8, 3), bool),
            "teacher_move": np.zeros(8, int),      # the beam walks in on every board
            "head_move": np.ones(8, int),          # the head holds on every board
            "checkpoint_path": np.asarray(str(self.source)),
            "checkpoint_sha256": np.asarray(
                hashlib.sha256(self.source.read_bytes()).hexdigest()),
        }

    def write_cache(self):
        np.savez_compressed(self.cache, **self.arrays)

    def edit(self, etas=(1.0,), out=None):
        # The tool prints its report to stdout; a test that wants the verdict has to be the one
        # holding the stream, or the helper's own redirect swallows it first.
        with contextlib.redirect_stdout(out if out is not None else io.StringIO()):
            return wsp.edit(self.cache, "move", self.output, None, list(etas))

    def test_edit_flips_the_choice_and_writes_only_the_move_leaf(self):
        self.write_cache()
        self.assertEqual(self.edit(), 0)
        result = pickle.loads(self.output.read_bytes())
        weight, bias = result["params"]["move"]
        candidate = self.arrays["hidden"].astype(np.float64) @ weight + bias
        self.assertEqual(int(np.argmax(candidate[0])), 0)
        np.testing.assert_array_equal(bias, self.payload["params"]["move"][1])
        np.testing.assert_array_equal(result["params"]["sentinel"],
                                      self.payload["params"]["sentinel"])
        self.assertEqual(result["weight_space_program"]["head"], "move")

    def test_eta_is_recorded_against_the_cache_not_the_output(self):
        self.write_cache()
        self.assertEqual(self.edit(), 0)
        program = pickle.loads(self.output.read_bytes())["weight_space_program"]
        self.assertEqual(program["cache"], str(self.cache))
        self.assertEqual(program["train_boards"], 6)

    def test_cached_choice_that_is_not_the_argmax_refuses_the_edit(self):
        # The whole counterfactual rests on the cached move being the argmax of these weights.
        # If it is not, the cache and the checkpoint disagree and every candidate is fiction.
        self.arrays["head_move"] = np.full(8, 2, int)
        self.write_cache()
        out = io.StringIO()
        self.assertEqual(self.edit(out=out), 1)
        self.assertIn("not the argmax", out.getvalue())
        self.assertFalse(self.output.exists())

    def test_no_disagreement_programs_nothing(self):
        self.arrays["teacher_move"] = np.ones(8, int)   # already walking where the beam walks
        self.write_cache()
        out = io.StringIO()
        self.assertEqual(self.edit(out=out), 1)
        self.assertIn("nothing to program", out.getvalue())
        self.assertFalse(self.output.exists())

    def test_heldout_regression_refuses_the_edit(self):
        # Fit slice agrees once pushed; the held-out boards agreed already and the push breaks
        # them. That is memorisation of the slice, and the gate has to see it.
        self.arrays["teacher_move"] = np.array([0, 0, 0, 0, 0, 0, 1, 1])
        self.write_cache()
        out = io.StringIO()
        self.assertEqual(self.edit(out=out), 1)
        self.assertIn("memorised the slice", out.getvalue())
        self.assertFalse(self.output.exists())


class TacticalMoveIntentTests(unittest.TestCase):
    """5-class tactical cell-intent basis with directed LOS and resistance."""

    def test_hold_when_final_cell_matches_my_cell(self):
        cells = {(5, 9): 4}
        board = _FakeBoard(5, cells)
        self.assertEqual(wsp.teacher_move_class(board, 9, 5, n_classes=5), 3)
        self.assertEqual(wsp.teacher_move_intent(board, 9, 5), 3)

    def test_distance_based_close_and_kite(self):
        cells = {(5, 9): 4, (7, 9): 2, (3, 9): 6}
        board = _FakeBoard(5, cells)
        # 5 -> 7 closes distance (4 -> 2)
        self.assertEqual(wsp.teacher_move_class(board, 9, 7, n_classes=5), 0)
        # 5 -> 3 opens distance (4 -> 6)
        self.assertEqual(wsp.teacher_move_class(board, 9, 3, n_classes=5), 4)

    def test_lateral_step_at_equal_distance(self):
        cells = {(5, 9): 4, (6, 9): 4}
        board = _FakeBoard(5, cells)
        # 5 -> 6 keeps distance at 4 -> lateral flank
        self.assertEqual(wsp.teacher_move_class(board, 9, 6, n_classes=5), 2)

    def test_step_to_break_los_with_mock_los(self):
        class _MockLosCells:
            def __init__(self, los_map, distances):
                self._los = los_map
                self._distances = distances
            def has_los(self, a, b):
                return self._los.get((a, b), False)
            def distance(self, a, b):
                return self._distances[(a, b)]

        class _MockBoard:
            def __init__(self, my_cell, los_map, distances):
                self.my_cell = my_cell
                self.cells = _MockLosCells(los_map, distances)
                self.enemies = []

        # Enemy at 9 had LOS to 5, but does NOT have LOS to 6
        los = {(9, 5): True, (9, 6): False}
        distances = {(5, 9): 4, (6, 9): 3}  # Even though distance decreased, breaking LOS takes priority!
        board = _MockBoard(5, los, distances)
        self.assertEqual(wsp.teacher_move_intent(board, 9, 6), 1)

    def test_lateral_flank_to_gain_los(self):
        class _MockLosCells:
            def __init__(self, los_map, distances):
                self._los = los_map
                self._distances = distances
            def has_los(self, a, b):
                return self._los.get((a, b), False)
            def distance(self, a, b):
                return self._distances[(a, b)]

        class _MockBoard:
            def __init__(self, my_cell, los_map, distances):
                self.my_cell = my_cell
                self.cells = _MockLosCells(los_map, distances)
                self.enemies = []

        # Player at 5 did NOT have LOS to 9, but at 6 HAS LOS to 9
        los = {(5, 9): False, (6, 9): True}
        distances = {(5, 9): 4, (6, 9): 4}
        board = _MockBoard(5, los, distances)
        self.assertEqual(wsp.teacher_move_intent(board, 9, 6), 2)

    def test_map_field_tactical_intent_and_resistance_gradient(self):
        from combat.map_field import MapField
        mf = MapField.from_map_id(100139008)
        self.assertEqual(mf.tactical_intent(200, 200, 250), 3)
        grad = mf.resistance_gradient(200, 201, 250)
        self.assertIsInstance(grad, float)


class MoveRidgeLinearProbeTests(unittest.TestCase):
    """Exact closed-form ridge linear-probe warm start in weight space."""

    def test_solve_move_linear_probe_exact_recovery(self):
        rng = np.random.default_rng(42)
        N, D, K = 64, 8, 5
        hidden = rng.normal(size=(N, D))
        teacher = rng.integers(0, K, size=N)
        mask = np.ones((N, K), bool)

        W, b, report = wsp.solve_move_linear_probe(
            hidden, teacher, move_mask=mask, split_share=0.75,
            alphas=(1e-4, 1e-2, 1.0, 10.0), n_classes=K
        )
        self.assertEqual(W.shape, (D, K))
        self.assertEqual(b.shape, (K,))
        self.assertEqual(report["n_classes"], K)
        self.assertGreaterEqual(report["chosen"]["train_agreement"], 0.2)

    def test_edit_move_head_ridge_method_writes_checkpoint(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = pathlib.Path(temp.name)
        source = root / "source.pkl"
        output = root / "edited_ridge.pkl"
        cache = root / "cache.npz"

        payload = {"params": {
            "move": (np.zeros((4, 5), dtype=np.float32), np.zeros(5, np.float32)),
            "sentinel": np.array([42.], dtype=np.float32),
        }}
        source.write_bytes(pickle.dumps(payload))

        rng = np.random.default_rng(7)
        N = 32
        hidden = rng.normal(size=(N, 4)).astype(np.float32)
        teacher = rng.integers(0, 5, size=N)
        mask = np.ones((N, 5), bool)

        arrays = {
            "hidden": hidden,
            "move_mask": mask,
            "teacher_move": teacher,
            "head_move": np.zeros(N, int),
            "checkpoint_path": np.asarray(str(source)),
            "checkpoint_sha256": np.asarray(hashlib.sha256(source.read_bytes()).hexdigest()),
        }
        np.savez_compressed(cache, **arrays)

        with contextlib.redirect_stdout(io.StringIO()):
            code = wsp.edit(cache, "move", output, None, [0.01, 1.0], method="ridge")
        self.assertEqual(code, 0)
        saved = pickle.loads(output.read_bytes())
        self.assertEqual(saved["weight_space_program"]["head"], "move")
        self.assertEqual(saved["weight_space_program"]["method"], "ridge")
        self.assertEqual(saved["params"]["move"][0].shape, (4, 5))


class PvmHarnessTests(unittest.TestCase):
    """`--harness pvm` is the instrument that can say a win rate; the mirror cannot."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)

    def test_summary_reports_the_rate_without_a_parity_centre(self):
        results = [{"network_score": float(i < 3), "winner": 0 if i < 3 else 1,
                    "boss": i % 2 == 0} for i in range(8)]
        out = wf.summary_pvm(results, {"harness": "pvm"}, "note")
        self.assertEqual(out["network_win_rate"], 0.375)
        self.assertEqual((out["wins"], out["losses"], out["draws"]), (3, 5, 0))
        self.assertNotIn("sign_test_p_worse_than_solver", out)

    def test_draws_receive_no_win_credit(self):
        rows = [{"winner": w, "network_score": float(w == 0), "boss": True}
                for w in (0, 1, 2, 2)]
        report = wf.summary_pvm(rows, {}, "test")
        self.assertEqual(report["network_win_rate"], 0.25)
        self.assertEqual(report["draws"], 2)
        self.assertEqual(report["boss"]["win_rate"], 0.25)
        rows[-1]["network_score"] = 0.5
        with self.assertRaisesRegex(ValueError, "only terminal"):
            wf.summary_pvm(rows, {}, "test")

    def test_empty_or_nonterminal_results_refuse(self):
        for rows in ([], [{"winner": -1, "network_score": 0.0}]):
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "terminal"):
                wf.summary_pvm(rows, {}, "test")

    def test_pvm_pairing_binds_simulator_and_metric(self):
        meta = {"score_metric": "terminal_player_win_v1", "source_sha256": {"env.py": "abc"},
                "config": {"spells": 4}, "backend": "jax", "jax_version": "test",
                "numpy_version": "test", "start_row": 5, "action_selection": "sample"}
        document = {"config": {"harness": "pvm", "data_sha256": "data"},
                    "summary": {"arm": "test", "network_win_rate": 1.0, "meta": meta},
                    "results": [{"matchup": [5, -1], "winner": 0, "network_score": 1.0}]}
        left, right = self.root / "left.json", self.root / "right.json"
        left.write_text(json.dumps(document), encoding="utf-8")
        right.write_text(json.dumps(document), encoding="utf-8")
        self.assertEqual(wf.compare(left, right)["mean_score_delta"], 0.0)
        for field in meta:
            modified = json.loads(json.dumps(document))
            del modified["summary"]["meta"][field]
            right.write_text(json.dumps(modified), encoding="utf-8")
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                wf.compare(left, right)

    def test_pvm_propagates_checkpoint_blindness(self):
        import jax
        from combat.encounter import train
        from combat.encounter.env import Config
        if not hasattr(wf, "play_pvm"):
            self.skipTest("PvM harness is unavailable")
        cfg = Config(allies=1, enemies=1, spells=1, max_rounds=1)
        p = self.root / "blind_checkpoint.pkl"
        c = self.root / "blind_corpus.npz"
        p.write_bytes(pickle.dumps({"params": {"spell": (np.zeros((1, 2), np.float32),
                                                 np.zeros(2, np.float32))},
                                      "config": cfg.__dict__, "blind": ["mechanics"]}))
        shape = (2, 2, 1)
        arrays = {"spell_ap": np.zeros(shape, np.float32), "spell_rmin": np.zeros(shape, np.float32),
                  "spell_rmax": np.ones(shape, np.float32), "spell_damage": np.ones(shape, np.float32),
                  "spell_heal": np.zeros(shape, np.float32), "spell_element": np.zeros(shape, np.int32),
                  "spell_ally": np.zeros(shape, bool), "hp": np.ones((2, 2), np.float32),
                  "max_hp": np.ones((2, 2), np.float32), "ap_max": np.ones((2, 2), np.float32),
                  "mp_max": np.ones((2, 2), np.float32), "power": np.zeros((2, 2), np.float32),
                  "res": np.zeros((2, 2, 5), np.float32), "dist": np.ones((2, 2), np.float32),
                  "initiative": np.ones((2, 2), np.float32), "alive": np.ones((2, 2), bool),
                  "first_team": np.zeros(2, np.int32)}
        np.savez_compressed(c, **arrays)
        captured = []
        def fake_rollout(config, length, opponent="random", player="learned", blind=()):
            captured.append(blind)
            return lambda params, state0, tables, key: {
                "done": jax.numpy.ones(state0["hp"].shape[0], bool),
                "winner": jax.numpy.zeros(state0["hp"].shape[0], np.int32),
                "round": jax.numpy.zeros(state0["hp"].shape[0], np.int32)}
        with patch.object(train, "make_eval_rollout", new=fake_rollout), \
                contextlib.redirect_stdout(io.StringIO()):
            results, meta = wf.play_pvm(str(p), str(c), None, 0, opponent="heuristic",
                                        action_selection="greedy")
        self.assertEqual(captured, [("mechanics",)])
        self.assertEqual(meta["blind"], ["mechanics"])
        self.assertEqual(len(results), 2)

    def test_pvm_respects_explicit_numpy_backend(self):
        code = ("import os,sys;sys.path.insert(0,'tools');import wsp_fight;"
                "os.environ['DOFUS_FIGHT_BACKEND']='numpy'\n"
                "try: wsp_fight.play_pvm('absent.pkl','absent.npz',1,7)\n"
                "except SystemExit as exc: print(str(exc))\n"
                "assert os.environ['DOFUS_FIGHT_BACKEND']=='numpy'\n")
        child = subprocess.run([sys.executable, "-c", code], cwd=str(wf.REPO),
                               capture_output=True, text=True)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertIn("needs the jax backend", child.stdout)

    def test_compare_refuses_to_pair_two_harnesses(self):
        def doc(harness, seed=7):
            path = self.root / ("%s_%d.json" % (harness, seed))
            path.write_text(json.dumps({
                "summary": {"network_win_rate": 0.2, "meta": {}},
                "config": {"data_sha256": "abc", "seed": seed, "matchup_seed": 11,
                           "beam_width": 8, "max_actions": 2, "plan_k": 6,
                           "harness": harness, "opponent": "heuristic", "samples": None},
                "results": [{"matchup": [0, -1], "network_score": 0.0}]}, indent=1),
                encoding="utf-8")
            return str(path)
        left, right = doc("duel"), doc("pvm")
        with self.assertRaisesRegex(ValueError, "harness"):
            wf.compare(left, right)

    def test_arm_seed_builds_the_arm_and_the_seed_streams_the_boards(self):
        data = self.root / "boards.npz"
        np.savez_compressed(data, hp=np.zeros((8, 5), np.float32))
        out = self.root / "run.json"
        with patch.object(wf, "load_arm",
                          return_value=(str(data), "arm")) as load_arm, \
                patch.object(wf, "play_pvm", return_value=([
                    {"winner": 2, "network_score": 0.0, "boss": True}], {"harness": "pvm"})) as play, \
                patch.object(wf, "play", return_value=([], {})) as duel, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wf.main(["random", "--head", "move", "--arm-seed", "5",
                                      "--harness", "pvm", "--data", str(data),
                                      "--out", str(out)]), 0)
        self.assertEqual(load_arm.call_args.args[4], 5)
        self.assertEqual(play.call_args.args[3], 7)   # the default --seed, unchanged by arm_seed
        duel.assert_not_called()
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["config"]["arm_seed"], 5)


class RawOutcomeEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.out = self.root / "edited.pkl"
        self.payload = {"params": {
            "move": (np.array([[1., 0., 0.]], np.float32), np.zeros(3, np.float32)),
            "sentinel": np.array([17.], np.float32)}}
        self.z = {"head": np.asarray("move"), "hidden": np.ones((8, 1)),
                  "head_mask": np.ones((8, 3), bool), "head_pick": np.zeros(8, int),
                  "teacher_pick": np.ones(8, int), "row": np.arange(8),
                  "train_mask": np.arange(8) < 6,
                  "action_win": np.tile([0., 1., 0.], (8, 1))}

    def run_edit(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return wsp.edit_outcome_head(self.z, self.out, self.payload,
                                         [0.01, 1.0], "cache.npz", "move")

    def test_strict_win_gain_edits_only_the_requested_weight(self):
        self.assertEqual(self.run_edit(), 0)
        saved = pickle.loads(self.out.read_bytes())
        np.testing.assert_array_equal(saved["params"]["sentinel"], [17.])
        np.testing.assert_array_equal(saved["params"]["move"][1], np.zeros(3))
        self.assertEqual(saved["weight_space_program"]["eta"], 1.0)
        self.assertEqual(saved["weight_space_program"]["heldout_win"], 1.0)

    def test_heldout_win_regression_refuses(self):
        self.z["action_win"][6:] = [1., 0., 0.]
        self.z["teacher_pick"][6:] = 0
        self.assertEqual(self.run_edit(), 1)
        self.assertFalse(self.out.exists())

    def test_no_better_outcome_is_not_an_edit(self):
        self.z["action_win"][:] = 1.0
        self.assertEqual(self.run_edit(), 1)
        self.assertFalse(self.out.exists())

    def test_trajectory_rows_cannot_cross_split(self):
        self.z["row"][7] = self.z["row"][0]
        with self.assertRaisesRegex(ValueError, "crosses"):
            self.run_edit()

    def test_control_matches_support_and_norm_and_keeps_other_leaves(self):
        source = self.root / "source.pkl"
        self.payload["config"] = {"spells": 2}
        source.write_bytes(pickle.dumps(self.payload))
        self.payload["wsp_source"] = {"path": str(source),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        self.assertEqual(self.run_edit(), 0)
        output = self.root / "control.pkl"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wsp.matched_control(self.out, output, 0), 0)
        original = pickle.loads(source.read_bytes())
        reference = pickle.loads(self.out.read_bytes())
        control = pickle.loads(output.read_bytes())
        base = original["params"]["move"][0]
        delta = control["params"]["move"][0] - base
        wanted = reference["params"]["move"][0] - base
        np.testing.assert_array_equal(delta != 0, wanted != 0)
        self.assertAlmostEqual(float(np.linalg.norm(delta)), float(np.linalg.norm(wanted)), places=6)
        np.testing.assert_array_equal(control["params"]["move"][1], original["params"]["move"][1])
        np.testing.assert_array_equal(control["params"]["sentinel"], original["params"]["sentinel"])
        source.write_bytes(source.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "identity"):
            wsp.matched_control(self.out, output, 0)

    def test_covariance_write_preserves_background_and_ignores_holdout(self):
        hidden = np.array([[100., 0.], [1., 1.], [8., 8.]])
        train = np.array([True, True, False])
        eligible = np.array([False, True, False])
        logits = np.zeros((3, 2))
        wanted = np.ones(3, int)
        mask = np.ones((3, 2), bool)
        delta = wsp.covariance_write(hidden, train, eligible, logits, wanted, mask, 0.001)
        np.testing.assert_allclose(hidden[eligible] @ delta, [[0., 1.]], atol=1e-8)
        uniform = np.array([[0., 0.5], [0., 0.5]])
        self.assertLess(np.linalg.norm(hidden[train] @ delta), np.linalg.norm(hidden[train] @ uniform))
        hidden[~train] = 1e12
        np.testing.assert_array_equal(
            wsp.covariance_write(hidden, train, eligible, logits, wanted, mask, 0.001), delta)

    def test_covariance_edit_obeys_outcome_gate(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = wsp.edit_outcome_head(self.z, self.out, self.payload, [0.5, 1.0],
                                         "cache.npz", "move", "covariance", 0.01)
        self.assertEqual(code, 0)
        saved = pickle.loads(self.out.read_bytes())
        self.assertEqual(saved["weight_space_program"]["method"], "covariance")
        self.assertEqual(saved["weight_space_program"]["heldout_win"], 1.0)

    def test_raw_teacher_finds_a_win_and_preserves_tensor_slots(self):
        import jax
        from combat.encounter import model, backend
        from combat.encounter.env import Config, initial_state, observe
        if not backend.HAS_JAX:
            self.skipTest("raw counterfactual teacher requires JAX")
        cfg = Config(allies=1, enemies=1, spells=2, max_rounds=3)
        shape = (4, 2, 2)
        data = {"spell_ap": np.full(shape, 3., np.float32),
                "spell_rmin": np.ones(shape, np.float32), "spell_rmax": np.ones(shape, np.float32),
                "spell_damage": np.tile([[1., 20.], [100., 100.]], (4, 1, 1)).astype(np.float32),
                "spell_heal": np.zeros(shape, np.float32),
                "spell_element": np.zeros(shape, np.int32), "spell_ally": np.zeros(shape, bool),
                "hp": np.tile([100., 10.], (4, 1)).astype(np.float32),
                "ap_max": np.full((4, 2), 3., np.float32), "mp_max": np.zeros((4, 2), np.float32),
                "power": np.zeros((4, 2), np.float32), "res": np.zeros((4, 2, 5), np.float32),
                "dist": np.ones((4, 2), np.float32), "initiative": np.ones((4, 2), np.float32),
                "alive": np.ones((4, 2), bool), "first_team": np.array([0, 0, 0, 1], np.int32)}
        data["max_hp"] = data["hp"].copy()
        tables = {k: backend.XP.asarray(v) for k, v in data.items() if k.startswith("spell_")}
        spec = {k: backend.XP.asarray(v) for k, v in data.items() if not k.startswith("spell_")}
        obs, glob, _ = observe(initial_state(spec, tables, cfg), tables, cfg)
        params = model.init_params(jax.random.PRNGKey(7), obs.shape[-1], glob.shape[-1],
                                   cfg, hidden=8, layers=0)
        params = jax.tree_util.tree_map(np.asarray, params)
        params["spell"] = (np.zeros((8, 3), np.float32), np.array([10., 0., -10.], np.float32))
        source, corpus, cache = (self.root / n for n in ("toy.pkl", "toy.npz", "toy_cache.npz"))
        source.write_bytes(pickle.dumps({"params": params, "config": cfg.__dict__, "heads": 4}))
        np.savez_compressed(corpus, **data)
        before = corpus.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            report = wsp.cache_pvm(4, 7, source, cache, corpus, "spell", turns=1)
        self.assertEqual(report["strict_improvements"], 3)
        self.assertEqual(report["distinct_rows"], 3)
        with np.load(cache) as z:
            np.testing.assert_array_equal(z["teacher_pick"], 1)
            np.testing.assert_array_equal(z["head_pick"], 0)
            np.testing.assert_array_equal(z["action_win"][:, :2], np.tile([0., 1.], (3, 1)))
            self.assertNotIn(3, z["row"].tolist())
            replay = model.forward(params, backend.XP.asarray(z["obs"]),
                                   backend.XP.asarray(z["glob"]), backend.XP.asarray(z["attention"]))
            np.testing.assert_allclose(np.asarray(replay["spell"])[:, 0], z["head_logits"], atol=1e-5)
        self.assertEqual(corpus.read_bytes(), before)

    def test_greedy_uses_spell_specific_target_masks(self):
        from combat.encounter import model
        from combat.encounter.env import Config
        cfg = Config(allies=1, enemies=1, spells=1)
        logits = {"move": np.array([[[3., 2., 1.]]]),
                  "spell": np.array([[[5., 10.]]]),
                  "target": np.array([[[[100., 0.], [0., 100.]]]])}
        masks = {"move": np.array([[[False, True, True]]]),
                 "spell": np.array([[[True, False]]]),
                 "target": np.array([[[[False, True], [True, False]]]])}
        actions = model.greedy_actions(logits, masks, cfg)
        self.assertEqual(int(actions["move"][0, 0]), 1)
        self.assertEqual(int(actions["spell"][0, 0]), 0)
        self.assertEqual(int(actions["target"][0, 0]), 1)
        masks["spell"][:] = [[[False, True]]]
        self.assertEqual(int(model.greedy_actions(logits, masks, cfg)["target"][0, 0]), 0)


if __name__ == "__main__":
    unittest.main()
