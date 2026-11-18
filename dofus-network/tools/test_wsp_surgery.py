from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np
import onnx
from onnx import helper, numpy_helper, TensorProto

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import weight_space as ws
from tools import wsp_surgery as surgery


def manifest():
    return {"base": {"params": 4000}, "tensors": [
        {"name": "tool_fc.weight", "shape": [2, 1000], "dtype": "float32", "values": np.ones((2, 1000)).tolist()},
        {"name": "tool_fc.bias", "shape": [2], "dtype": "float32", "values": [0., 0.]},
        {"name": "hidden.weight", "shape": [1, 1998], "dtype": "float32", "values": np.ones((1, 1998)).tolist()}]}


def notes(base, candidate):
    result = []
    for before, after in zip(base["tensors"], candidate["tensors"]):
        b, c = np.asarray(before["values"], dtype="float32"), np.asarray(after["values"], dtype="float32")
        for index in np.argwhere(b != c):
            key = tuple(int(i) for i in index)
            result.append({"tensor": before["name"], "index": list(key), "old_value": float(b[key]),
                           "new_value": float(c[key]), "feature_index": 38, "feature_name": "obs_stale",
                           "predicted_tool": "nav.goto", "true_tool": "quest.advance", "reason": "fixture"})
    return result


def rows_and_tools(n=200):
    tools, rows = ["nav.goto", "quest.advance"], []
    for i in range(n):
        x = [0.] * 42
        x[surgery.STALE] = i % 2
        y = (i // 2) % 2
        rows.append({"ep": i // 4, "t": i % 4, "x": x, "y": y, "tool": tools[y],
                     "teacher": tools[1-y], "gains": {tools[y]: 1., tools[1-y]: 0.}})
    return rows, tools


def metrics(rows, tools, wrong):
    logits = np.zeros((len(rows), len(tools)))
    for i, row in enumerate(rows):
        logits[i, 1-row["y"] if i in wrong else row["y"]] = 1.
    return ws.metrics_from_logits(logits, rows, tools, return_correct=True, corpus_sha256="fixture")


def verdict(bm, cm):
    return {"_base_eval": bm, "_candidate_eval": cm, "gate": {"promote": True},
            "paired_optimal": ws.paired_bootstrap(bm["_optimal_correct"], cm["_optimal_correct"])}


def toy_onnx(folder):
    w, b = np.zeros((2, 42), dtype=np.float32), np.array([1., 0.], dtype=np.float32)
    graph = helper.make_graph([helper.make_node("Gemm", ["x", "tool_fc.weight", "tool_fc.bias"], ["logits"], transB=1)],
                              "toy", [helper.make_tensor_value_info("x", TensorProto.FLOAT, [None, 42])],
                              [helper.make_tensor_value_info("logits", TensorProto.FLOAT, [None, 2])],
                              [numpy_helper.from_array(w, "tool_fc.weight"), numpy_helper.from_array(b, "tool_fc.bias")])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    path, sidecar = folder / "base.onnx", folder / "base.json"
    onnx.save(model, str(path))
    sidecar.write_text(json.dumps({"arch": "mlp", "features": 42, "tools": ["nav.goto", "quest.advance"]}))
    return path, sidecar


class MicroscopeScoreboardTests(unittest.TestCase):
    def test_raw_dump_preserves_numbers_row_order_and_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            model, sidecar = toy_onnx(folder)
            before = model.read_bytes()
            rows, _ = rows_and_tools(20)
            source = folder / "source.jsonl"
            source.write_text("\n".join(json.dumps(r) for r in rows))
            lock = surgery.split_episodes(source, folder)
            original_diag = ws.read_val_rows(Path(lock["diagnose_errors"]["path"]))
            result = surgery.dump_raw(folder, model, sidecar)
            raw = json.loads(Path(result["tensors"]).read_text())
            self.assertEqual(raw["tensors"][0]["values"], np.zeros((2, 42)).tolist())
            failures = ws.read_val_rows(Path(result["failing_rows"]))
            expected = [i for i, r in enumerate(original_diag) if r["y"] == 1]
            self.assertEqual([r["row_index"] for r in failures], expected)
            for row in failures:
                self.assertEqual(row["row"], original_diag[row["row_index"]])
                self.assertEqual(row["model_input"], row["row"]["x"])
            self.assertEqual(model.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, "already exists"):
                surgery.dump_raw(folder, model, sidecar)

    def test_split_disjoint_complete_and_ordered(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            rows, _ = rows_and_tools()
            source = folder / "source.jsonl"
            source.write_text("\n".join(json.dumps(r) for r in reversed(rows)))
            lock = surgery.split_episodes(source, folder)
            groups = [lock[n] for n in ("diagnose_errors", "search_val", "frozen_temporal_test")]
            sets = [set(g["episodes"]) for g in groups]
            self.assertFalse(sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])
            self.assertLess(max(sets[0]), min(sets[1]))
            self.assertLess(max(sets[1]), min(sets[2]))
            self.assertEqual(sum(g["rows"] for g in groups), len(rows))

    def test_used_holdout_cannot_be_resealed_or_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            folder = archive / "old"
            folder.mkdir()
            source = archive / "source.jsonl"
            source.write_text("{}\n")
            (folder / "unsealed_once.json").write_text("{}")
            (folder / "split_lock.json").write_text(json.dumps({"source_sha256": ws.sha256_file(source)}))
            with self.assertRaises(ValueError):
                surgery.assert_unused_source(source, archive)
            with patch.object(ws, "candidate_verdict") as scorer:
                with self.assertRaises(ValueError):
                    surgery.score(folder, archive / "missing.onnx", final=True)
                scorer.assert_not_called()

    def test_valid_weight_check_does_not_change_candidate(self):
        base = manifest()
        candidate = copy.deepcopy(base)
        candidate["tensors"][0]["values"][0][0] = 1.01
        before = copy.deepcopy(candidate)
        touched, cumulative = surgery.verify_weights(base, base, candidate, notes(base, candidate), set(), 0)
        self.assertEqual(len(touched), 1)
        self.assertEqual(cumulative, 1)
        self.assertEqual(candidate, before)

    def test_hidden_edits_and_seventeen_floats_fail(self):
        base = manifest()
        for hidden in (True, False):
            candidate = copy.deepcopy(base)
            if hidden:
                candidate["tensors"][2]["values"][0][0] = 1.01
            else:
                candidate["tensors"][0]["values"][0][:17] = [1.01] * 17
            with self.assertRaises(ValueError):
                surgery.verify_weights(base, base, candidate, notes(base, candidate), set(), 0)

    def test_cumulative_and_l2_limits_fail(self):
        base = manifest()
        for amount, prior in ((.01, 20), (10., 0)):
            candidate = copy.deepcopy(base)
            candidate["tensors"][0]["values"][0][0] += amount
            with self.assertRaisesRegex(ValueError, "budget"):
                surgery.verify_weights(base, base, candidate, notes(base, candidate), set(), prior)

    def test_missing_false_or_duplicate_causal_notes_fail(self):
        base = manifest()
        candidate = copy.deepcopy(base)
        candidate["tensors"][0]["values"][0][0] = 1.01
        valid = notes(base, candidate)
        false_name = copy.deepcopy(valid)
        false_name[0]["feature_name"] = "invented_feature"
        false_value = copy.deepcopy(valid)
        false_value[0]["new_value"] = 7.
        for supplied in ([], valid + valid, false_name, false_value):
            with self.assertRaises(ValueError):
                surgery.verify_weights(base, base, candidate, supplied, set(), 0)

    def test_noop_and_sub_float32_edits_fail(self):
        base = manifest()
        for delta in (0., 1e-12):
            candidate = copy.deepcopy(base)
            candidate["tensors"][0]["values"][0][0] += delta
            with self.assertRaises(ValueError):
                surgery.verify_weights(base, base, candidate, [], set(), 0)

    def test_graph_change_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            base, _ = toy_onnx(folder)
            candidate = folder / "candidate.onnx"
            model = onnx.load(str(base))
            model.graph.node[0].attribute[0].i = 0
            onnx.save(model, str(candidate))
            with self.assertRaisesRegex(ValueError, "graph changed"):
                surgery.verify_graph(base, candidate)

    def test_scoreboard_passes_improvement_but_not_noop(self):
        rows, tools = rows_and_tools()
        bm, cm = metrics(rows, tools, set(range(0, 200, 8))), metrics(rows, tools, set())
        self.assertTrue(surgery.scoreboard_pass(verdict(bm, cm), rows, tools))
        self.assertFalse(surgery.scoreboard_pass(verdict(bm, bm), rows, tools))

    def test_stale_and_common_recall_tradeoffs_fail(self):
        rows, tools = rows_and_tools()
        bm, cm = metrics(rows, tools, set(range(0, 200, 4))), metrics(rows, tools, {3})
        self.assertFalse(surgery.scoreboard_pass(verdict(bm, cm), rows, tools))

    def test_single_episode_gain_fails_significance(self):
        rows, tools = rows_and_tools()
        bm, cm = metrics(rows, tools, {0, 1, 2, 3}), metrics(rows, tools, set())
        self.assertFalse(surgery.scoreboard_pass(verdict(bm, cm), rows, tools))

    def test_flips_are_unranked_and_contain_no_features_or_hints(self):
        rows, tools = rows_and_tools()
        bm, cm = metrics(rows, tools, {0, 3}), metrics(rows, tools, set())
        flips = surgery.flipped_rows(verdict(bm, cm), rows, tools)
        self.assertEqual([r["row_index"] for r in flips], [0, 3])
        self.assertEqual(set(flips[0]), {"row_index", "ep", "t", "before", "after", "target"})

    def test_score_command_outputs_only_status_and_flips(self):
        output = io.StringIO()
        with patch.object(surgery, "score", side_effect=ValueError("no hints")), redirect_stdout(output):
            code = surgery.main(["score", "--session", "unused", "--candidate", "unused.onnx"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue()), {"status": "FAIL", "flipped_rows": []})

    def test_custom_split_verdict_does_not_use_full_corpus_pin_metrics(self):
        rows, tools = rows_and_tools()
        bm, cm = metrics(rows, tools, set(range(0, 200, 8))), metrics(rows, tools, set())
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            split, base, reference, candidate, pin = [folder / n for n in ("split.jsonl", "base.onnx", "reference.onnx", "candidate.onnx", "pin.json")]
            split.write_text("\n".join(json.dumps(r) for r in rows))
            for path in (base, reference, candidate):
                path.write_bytes(path.name.encode())
            pin.write_text(json.dumps({"base": {"onnx_sha256": ws.sha256_file(base)}, "metrics": {"rows": 1000}}))
            def export(path=base, sidecar=None):
                return {"base": {"tools": tools, "arch": "mlp", "sha256": ws.sha256_file(path)}}
            with patch.object(ws, "BASE_ONNX", base), patch.object(ws, "PROVENANCE_PATH", pin), \
                    patch.object(ws, "export_manifest", side_effect=export), \
                    patch.object(ws, "score_artifact", side_effect=[cm, bm]) as scorer:
                result = ws.candidate_verdict(candidate, val=split, reference_onnx=reference, include_details=True)
            self.assertEqual(result["base"]["rows"], 200)
            self.assertEqual(scorer.call_args_list[1].args[2], reference)


if __name__ == "__main__":
    unittest.main()
