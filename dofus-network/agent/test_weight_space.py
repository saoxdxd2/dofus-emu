"""Weight-space substrate tests (Phase 0 + 1). Offline, no live loop."""
from __future__ import annotations
import sys, tempfile, unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.weight_space import (  # noqa: E402
    BASE_ONNX, EditRejected, apply_edits, build_unit_manifest, evaluate,
    export_manifest, gate_decision, inventory, manifest_weights,
    publish_candidate, read_val_rows, rollback_candidate, sha256_file,
    validate_manifest, validate_skill_spec, editor_prompt, forward,
)


class WeightSpaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = export_manifest()
        cls.weights = manifest_weights(cls.manifest)
        cls.tools = cls.manifest["base"]["tools"]
        cls.rows = read_val_rows()

    def test_inventory_matches_sidecar(self):
        inv = inventory()
        self.assertEqual(inv["params"], 49365)
        self.assertTrue(inv["params_match_sidecar"])
        self.assertEqual(len(inv["feature_names"]), 42)
        self.assertEqual(len(inv["tools"]), 21)

    def test_export_validates(self):
        self.assertEqual(validate_manifest(self.manifest), [])

    def test_empty_edit_is_identity(self):
        candidate, report = apply_edits(self.manifest, [])
        self.assertEqual(report["changes"], 0)
        self.assertEqual(manifest_weights(candidate), self.weights)

    def test_single_bias_edit(self):
        bias = self.weights["4.bias"]
        candidate, report = apply_edits(self.manifest, [
            {"tensor": "4.bias", "index": [3], "old_value": bias[3],
             "new_value": bias[3] + 0.1, "reason": "test"}])
        self.assertEqual(report["changes"], 1)
        changed = manifest_weights(candidate)["4.bias"]
        self.assertAlmostEqual(changed[3], bias[3] + 0.1)
        self.assertEqual(changed[:3], bias[:3])
