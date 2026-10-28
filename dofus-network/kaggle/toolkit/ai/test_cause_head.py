import contextlib
import copy
import io
import unittest
from unittest.mock import patch

from agent.controller import CAUSES
from ai.train_cause_head import audit_splits, main, require_disjoint_splits


def disjoint_rows():
    return tuple([
        {"tool": "quest.advance", "error": cause + " " + split,
         "family": cause + ":" + split, "expected_cause": cause,
         "ok": False, "verdict": "failed", "repeat": 1,
         "before": {"map_id": 1}, "after": {"map_id": 2}}
        for cause in CAUSES
    ] for split in ("fit", "calibration", "validation"))


class SplitAuditTests(unittest.TestCase):
    def test_disjoint_families_and_inputs_pass(self):
        self.assertTrue(require_disjoint_splits(*disjoint_rows())["valid"])

    def test_renamed_family_cannot_hide_identical_features(self):
        fit, calib, val = disjoint_rows()
        val[0] = dict(fit[0], family="none:different", note="not a feature")
        audit = audit_splits(fit, calib, val)
        self.assertFalse(audit["valid"])
        self.assertEqual(audit["overlap"]["fit_val"]["input_rows"], 1)
        self.assertEqual(audit["overlap"]["fit_val"]["families"], [])

    def test_normalized_ids_do_not_make_new_examples(self):
        fit, calib, val = disjoint_rows()
        fit[0]["error"] = "missing npc 1"
        val[0]["error"] = "missing npc 900"
        audit = audit_splits(fit, calib, val)
        self.assertEqual(audit["overlap"]["fit_val"]["input_rows"], 1)

    def test_calibration_leak_is_also_rejected(self):
        fit, calib, val = disjoint_rows()
        val[0] = dict(calib[0], family="none:different")
        self.assertFalse(audit_splits(fit, calib, val)["valid"])
        with self.assertRaises(ValueError):
            require_disjoint_splits(fit, calib, val)

    def test_shared_family_is_rejected_despite_different_inputs(self):
        fit, calib, val = disjoint_rows()
        val[0]["family"] = fit[0]["family"]
        audit = audit_splits(fit, calib, val)
        self.assertFalse(audit["valid"])
        self.assertEqual(audit["overlap"]["fit_val"]["families"], [fit[0]["family"]])

    def test_missing_cause_is_not_a_passing_split(self):
        fit, calib, val = disjoint_rows()
        val.pop()
        audit = audit_splits(fit, calib, val)
        self.assertEqual(audit["missing_causes"]["val"], [CAUSES[-1]])
        self.assertFalse(audit["valid"])

    def test_audit_does_not_mutate_the_rows(self):
        splits = disjoint_rows()
        before = copy.deepcopy(splits)
        audit_splits(*splits)
        self.assertEqual(splits, before)

    def test_invalid_split_stops_before_training_or_export(self):
        fit, calib, val = disjoint_rows()
        val[0] = dict(fit[0], family="none:different")
        with patch("ai.train_cause_head.read_jsonl", return_value=fit + calib + val), \
             patch("ai.train_cause_head.split_rows", return_value=(fit, calib, val)), \
             patch("ai.train_cause_head.to_arrays") as arrays, \
             patch("ai.train_cause_head.build_model") as model, \
             patch("pathlib.Path.write_text") as write, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([]), 1)
        arrays.assert_not_called()
        model.assert_not_called()
        write.assert_not_called()
        self.assertIn("existing artifacts unchanged", output.getvalue())


if __name__ == "__main__":
    unittest.main()
