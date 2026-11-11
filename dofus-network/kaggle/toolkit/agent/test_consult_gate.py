"""The deferral gate must only speak for the model it was fitted on.

A floor is a property of one model's logit scale. The failure this guards is quiet:
carry 0.75 from the old head onto a new architecture and the gate either never fires
(the head always decides, and the gate is decoration) or always fires (the rules
always decide, and the retrain is invisible). Neither raises an error.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent import consult_gate


class AppliesToTest(unittest.TestCase):
    def _write(self, payload) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8")
        with handle as fh:
            json.dump(payload, fh)
        return Path(handle.name)

    def test_no_artifact_means_no_gate(self):
        missing = Path(tempfile.gettempdir()) / "definitely_no_gate.json"
        self.assertFalse(consult_gate.applies_to("abc", "mlp", path=missing))

    def test_legacy_artifact_applies_to_the_mlp_only(self):
        path = self._write({"floor": 0.75})
        self.assertTrue(consult_gate.applies_to("whatever", "mlp", path=path))
        self.assertFalse(consult_gate.applies_to("whatever", "dedicated-v1", path=path))

    def test_bound_artifact_requires_a_matching_hash(self):
        path = self._write({"floor": 1.05, "model_sha256": "a" * 64})
        self.assertTrue(consult_gate.applies_to("a" * 64, "dedicated-v1", path=path))
        self.assertFalse(consult_gate.applies_to("b" * 64, "dedicated-v1", path=path))
        self.assertFalse(consult_gate.applies_to(None, "dedicated-v1", path=path))

    def test_should_defer_still_reads_the_floor(self):
        path = self._write({"floor": 1.0})
        gate = consult_gate.load(path)
        self.assertTrue(consult_gate.should_defer(gap=0.4, gate=gate))
        self.assertFalse(consult_gate.should_defer(gap=1.4, gate=gate))


if __name__ == "__main__":
    unittest.main()
