"""Weight-space substrate tests (Phase 0 + 1). Offline, no live loop."""
from __future__ import annotations
import random, sys, tempfile, unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.weight_space import (  # noqa: E402
    BASE_ONNX, DEFAULT_VAL, EditRejected, apply_edits, build_unit_manifest,
    check_pin, corpus_identity, evaluate, export_manifest, gate_decision,
    inventory, manifest_weights, paired_bootstrap, pin_provenance,
    publish_candidate, read_val_rows, rollback_candidate, sha256_file,
    validate_manifest, validate_skill_spec, editor_prompt, forward,
)


def fixture_manifest(features: int = 4, hidden: int = 3, n_tools: int = 3) -> dict:
    """A small dense stack, so edit tests do not depend on what is shipped.

    These tests exercise the *editing substrate* -- coordinate addressing, budgets,
    L2 limits -- not the current artifact. Pinning them to the shipped model meant
    every retrain broke them, and the fix would have been to edit the expected
    numbers, which is how a test stops testing anything.
    """
    rng = random.Random(7)

    def matrix(name, rows, cols):
        return {"name": name, "shape": [rows, cols], "dtype": "float32",
                "values": [[round(rng.uniform(-0.6, 0.6), 4) for _ in range(cols)]
                           for _ in range(rows)]}

    def vector(name, n):
        return {"name": name, "shape": [n], "dtype": "float32",
                "values": [round(rng.uniform(-0.3, 0.3), 4) for _ in range(n)]}

    return {
        "kind": "weight-space-manifest", "version": 1,
        "base": {"sha256": "f" * 64, "arch": "mlp", "features": features,
                 "tools": ["t%d" % i for i in range(n_tools)],
                 "params": features * hidden + hidden + hidden * n_tools + n_tools,
                 "feature_names": ["f%d" % i for i in range(features)]},
        "tensors": [matrix("0.weight", hidden, features), vector("0.bias", hidden),
                    matrix("2.weight", n_tools, hidden), vector("2.bias", n_tools)],
    }


class WeightSpaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = export_manifest()
        cls.weights = manifest_weights(cls.manifest)
        cls.tools = cls.manifest["base"]["tools"]
        cls.rows = read_val_rows()

    def test_inventory_matches_sidecar(self):
        inv = inventory()
        # The contract, not a literal: params must equal what the sidecar claims and
        # the reported names must cover the width the artifact actually reads.
        self.assertEqual(inv["params"], inv["sidecar_params"])
        self.assertTrue(inv["params_match_sidecar"])
        self.assertEqual(len(inv["feature_names"]), inv["features"])
        self.assertEqual(len(inv["tools"]), 21)

    def test_export_validates(self):
        self.assertEqual(validate_manifest(self.manifest), [])
        self.assertEqual(validate_manifest(fixture_manifest()), [])

    def test_export_validates_a_fixture_with_the_wrong_width(self):
        broken = fixture_manifest()
        broken["base"]["features"] = 99
        self.assertTrue(any("features" in p for p in validate_manifest(broken)))

    def test_empty_edit_is_identity(self):
        manifest = fixture_manifest()
        candidate, report = apply_edits(manifest, [])
        self.assertEqual(report["changes"], 0)
        self.assertEqual(manifest_weights(candidate), manifest_weights(manifest))

    def test_single_bias_edit(self):
        manifest = fixture_manifest()
        bias = manifest_weights(manifest)["2.bias"]
        candidate, report = apply_edits(manifest, [
            {"tensor": "2.bias", "index": [1], "old_value": bias[1],
             "new_value": bias[1] + 0.1, "reason": "test"}])
        self.assertEqual(report["changes"], 1)
        changed = manifest_weights(candidate)["2.bias"]
        self.assertAlmostEqual(changed[1], bias[1] + 0.1)
        self.assertEqual(changed[:1], bias[:1])

    def test_corpus_identity_counts_rows(self):
        ident = corpus_identity()
        self.assertEqual(ident["rows"], len(self.rows))
        self.assertEqual(len(ident["sha256"]), 64)

    def _gate_metrics(self, **over):
        metrics = {"rows": 100, "val_accuracy": 0.90, "macro_f1": 0.40,
                   "macro_f1_common": 0.90, "ece": 0.02,
                   "decision": {"expected_gain_model": 1.0},
                   "corpus_sha256": "a" * 64}
        metrics.update(over)
        return metrics

    def test_gate_rejects_corpus_mismatch(self):
        """Same row count, different bytes -- the old gate passed this."""
        base = self._gate_metrics()
        cand = self._gate_metrics(val_accuracy=0.95, corpus_sha256="b" * 64,
                                  decision={"expected_gain_model": 1.1})
        gate = gate_decision(base, cand)
        self.assertFalse(gate["promote"])
        self.assertTrue(any("different corpus" in r for r in gate["reasons"]),
                        gate["reasons"])

    def test_gate_rejects_macro_f1_collapse(self):
        """Buying common accuracy by abandoning the rare tail must not promote."""
        base = self._gate_metrics()
        cand = self._gate_metrics(val_accuracy=0.95, macro_f1=0.06,
                                  decision={"expected_gain_model": 1.1})
        gate = gate_decision(base, cand)
        self.assertFalse(gate["promote"])
        self.assertTrue(any("below floor" in r for r in gate["reasons"]),
                        gate["reasons"])

    def test_gate_floor_is_optional(self):
        """The same collapse promotes when the floor is explicitly disabled."""
        base = self._gate_metrics()
        cand = self._gate_metrics(val_accuracy=0.95, macro_f1=0.06,
                                  decision={"expected_gain_model": 1.1})
        self.assertTrue(gate_decision(base, cand, min_macro_f1=0.0)["promote"])

    def test_pin_roundtrip_and_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            pin_path = Path(tmp) / "pin.json"
            record = pin_provenance(out=pin_path)
            self.assertEqual(record["corpus"]["rows"], len(self.rows))
            self.assertEqual(record["metrics"]["rows"], len(self.rows))
            self.assertTrue(check_pin(pin_path=pin_path)["match"])

            drifted = Path(tmp) / "val.jsonl"
            body = DEFAULT_VAL.read_text(encoding="utf-8").rstrip("\n")
            extra = body.splitlines()[0]
            drifted.write_text(body + "\n" + extra + "\n", encoding="utf-8")
            status = check_pin(drifted, pin_path=pin_path)
            self.assertFalse(status["match"])
            self.assertTrue(any("rows drifted" in r for r in status["reasons"]),
                            status["reasons"])

    def test_check_pin_without_record(self):
        status = check_pin(pin_path=Path(tempfile.gettempdir()) / "no_such_pin.json")
        self.assertFalse(status["pinned"])
        self.assertFalse(status["match"])

    def test_reachability_partitions_the_head(self):
        """Reachability is not accuracy: a tool ranked first nowhere is unselectable.

        Record of a measured gap, updated deliberately. The 42-wide head emitted 5 of
        21 tools and left 16 unselectable. The dedicated sequence policy emits 8 and
        leaves 13: it recovered `quest.list`, `inv.list` and `state.snapshot` without
        losing any. The remaining 13 are still a data-coverage problem, not a
        threshold one -- the corpus barely contains them.
        """
        from agent.weight_space import score_artifact
        metrics = score_artifact(self.manifest, self.rows)
        reach = set(metrics["reachable_tools"])
        unreach = set(metrics["unreachable_tools"])
        self.assertEqual(reach | unreach, set(self.tools))
        self.assertEqual(reach & unreach, set())
        self.assertIn("nav.goto", reach)
        self.assertEqual(len(reach), 8)
        self.assertEqual(len(unreach), 13)
        for recovered in ("quest.list", "inv.list", "state.snapshot"):
            self.assertIn(recovered, reach)

    def test_gate_rejects_lost_reachability(self):
        """A candidate that stops emitting a tool regresses capability, not accuracy."""
        base = self._gate_metrics(reachable_tools=["a", "b"])
        cand = self._gate_metrics(val_accuracy=0.95, reachable_tools=["a"],
                                  decision={"expected_gain_model": 1.1})
        gate = gate_decision(base, cand)
        self.assertFalse(gate["promote"])
        self.assertTrue(any("unreachable" in r for r in gate["reasons"]),
                        gate["reasons"])

    def test_gate_allows_gained_reachability(self):
        base = self._gate_metrics(reachable_tools=["a"])
        cand = self._gate_metrics(val_accuracy=0.95, reachable_tools=["a", "b"],
                                  decision={"expected_gain_model": 1.1})
        self.assertTrue(gate_decision(base, cand)["promote"])

    def test_paired_bootstrap_separates_noise_from_gain(self):
        """40 flips each way is not an improvement; 100 recovered rows is."""
        base = [True] * 900 + [False] * 100
        noise = list(base)
        for i in range(40):
            noise[i] = False
            noise[900 + i] = True
        self.assertFalse(paired_bootstrap(base, noise)["significant"])

        gain = list(base)
        for i in range(100):
            gain[900 + i] = True
        stats = paired_bootstrap(base, gain)
        self.assertTrue(stats["significant"])
        self.assertGreater(stats["lo"], 0.0)
        self.assertEqual(stats["gained"], 100)
        self.assertEqual(stats["lost"], 0)

    def test_gate_rejects_insignificant_gain(self):
        """The exact shape that promoted a retrain: +0.0011, CI straddling zero."""
        base = self._gate_metrics()
        cand = self._gate_metrics(val_accuracy=0.905,
                                  decision={"expected_gain_model": 1.1})
        paired = {"comparable": True, "rows": 3719, "delta": 0.00108,
                  "lo": -0.00538, "hi": 0.00753, "significant": False}
        gate = gate_decision(base, cand, paired=paired)
        self.assertFalse(gate["promote"])
        self.assertTrue(any("no significant improvement" in r
                            for r in gate["reasons"]), gate["reasons"])

    def test_gate_accepts_significant_task_optimality_without_label_gain(self):
        """A model trained on outcomes can win the task and lose the tie-break.

        This is the dedicated policy's actual shape: label accuracy inside noise,
        task optimality clearly outside it. Rejecting it would mean the gate can
        only ever admit models that imitate the teacher more closely.
        """
        base = self._gate_metrics()
        cand = self._gate_metrics(val_accuracy=0.9055, optimal=0.9166,
                                  decision={"expected_gain_model": 1.1})
        paired = {"comparable": True, "rows": 3719, "delta": 0.00323,
                  "lo": -0.00161, "hi": 0.00834, "significant": False}
        paired_optimal = {"comparable": True, "rows": 3719, "delta": 0.01049,
                          "lo": 0.00565, "hi": 0.01533, "significant": True}
        gate = gate_decision(base, cand, paired=paired,
                             paired_optimal=paired_optimal)
        self.assertTrue(gate["promote"], gate["reasons"])
        self.assertEqual(gate["significant"], {"accuracy": False, "optimal": True})

    def test_gate_still_rejects_when_neither_metric_is_significant(self):
        base = self._gate_metrics()
        cand = self._gate_metrics(val_accuracy=0.9055, optimal=0.9064,
                                  decision={"expected_gain_model": 1.1})
        paired = {"comparable": True, "rows": 3719, "delta": 0.0012,
                  "lo": -0.004, "hi": 0.006, "significant": False}
        paired_optimal = {"comparable": True, "rows": 3719, "delta": 0.0002,
                          "lo": -0.003, "hi": 0.004, "significant": False}
        gate = gate_decision(base, cand, paired=paired,
                             paired_optimal=paired_optimal)
        self.assertFalse(gate["promote"])


if __name__ == "__main__":
    unittest.main()
