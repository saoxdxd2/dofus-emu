"""The growable head and the pre-softmax legality mask.

The property that matters is not "the head scores 28 tools"; it is "appending
tool 29 changes nothing except that tool 29 now has a row". These tests assert
that property directly, and that an illegal tool cannot win by probability
mass left over after the mask.
"""

from __future__ import annotations

import unittest

import numpy as np

from ai.capability_head import (
    DESCRIPTOR_DIM,
    descriptor_table,
    encode_descriptor,
    load_capability_records,
)
from ai.guard_policy import GuardPolicy, legality_mask, masked_softmax, tool_is_legal


def _record(name: str, description: str = "", **extra):
    row = {"id": name, "description": description or name, "backend": "local",
           "params": {}, "read_only": False, "moves": False, "lists": False}
    row.update(extra)
    return row


class TestCapabilityHead(unittest.TestCase):
    def test_descriptor_is_deterministic_and_unit_norm(self) -> None:
        record = _record("inv.sell", "Sell inventory items on the market at a price rule.")
        first = encode_descriptor(record)
        second = encode_descriptor(dict(record))
        self.assertEqual(first.shape, (DESCRIPTOR_DIM,))
        np.testing.assert_array_equal(first, second)
        self.assertAlmostEqual(float(np.linalg.norm(first)), 1.0, places=5)

    def test_appending_a_tool_does_not_move_existing_rows(self) -> None:
        base = [_record("farm.start", "Start a farm loop."),
                _record("farm.stop", "Stop the farm loop.")]
        names, before = descriptor_table(["farm.start", "farm.stop"], base)
        names_after, after = descriptor_table(
            ["farm.start", "farm.stop", "shop.buy"], base + [_record("shop.buy", "Buy an item.")])
        self.assertEqual(names, ["farm.start", "farm.stop"])
        self.assertEqual(names_after[:2], names)
        np.testing.assert_array_equal(before, after[:2])
        self.assertFalse(np.array_equal(after[2], after[0]))

    def test_registry_encodes_every_capability(self) -> None:
        records = load_capability_records()
        if not records:
            self.skipTest("capability registry not present")
        names, matrix = descriptor_table(None, records)
        self.assertEqual(len(names), len(records))
        self.assertEqual(matrix.shape, (len(names), DESCRIPTOR_DIM))
        self.assertTrue(np.isfinite(matrix).all())

    def test_capability_head_grows_without_changing_weights(self) -> None:
        torch = __import__("torch")
        from ai.capability_torch import build_head

        torch.manual_seed(3)
        head = build_head(feature_dim=12, hidden=16, depth=1, d_model=8)
        before = {key: value.detach().clone() for key, value in head.state_dict().items()}
        features = torch.randn(4, 12)
        records = [_record("a.one", "first action"), _record("a.two", "second action")]
        _, descriptors = descriptor_table(["a.one", "a.two"], records)
        logits_two = head(features, torch.from_numpy(descriptors)).detach()

        _, descriptors_three = descriptor_table(
            ["a.one", "a.two", "a.three"],
            records + [_record("a.three", "third action")])
        logits_three = head(features, torch.from_numpy(descriptors_three)).detach()

        self.assertEqual(logits_three.shape, (4, 3))
        # Every parameter is untouched by the growth, and the first two columns
        # are bit-identical: the new row did not perturb the old scores.
        after = head.state_dict()
        for key in before:
            self.assertTrue(torch.equal(before[key], after[key]))
        np.testing.assert_allclose(logits_two, logits_three[:, :2], atol=1e-6)

    def test_legality_mask_removes_illegal_mass_before_softmax(self) -> None:
        tools = ["fight.pass", "farm.stop", "nav.goto", "state.snapshot"]
        logits = np.array([9.0, 8.0, 7.0, 0.0], dtype=np.float32)
        observation = {"in_fight": False, "farm": {"running": False}}
        masked = legality_mask(logits, tools, observation)
        self.assertLess(masked[0], -1e8)      # fight.pass outside a fight
        self.assertLess(masked[1], -1e8)      # farm.stop with no farm
        probs = masked_softmax(logits, tools, observation)
        self.assertEqual(probs[0], 0.0)
        self.assertEqual(probs[1], 0.0)
        self.assertAlmostEqual(float(probs.sum()), 1.0)
        self.assertEqual(int(np.argmax(probs)), 2)

    def test_legality_fails_open_when_nothing_is_legal(self) -> None:
        tools = ["fight.pass"]
        observation = {"in_fight": False}
        masked = legality_mask(np.array([1.0], dtype=np.float32), tools, observation)
        # No legal action must not become an all -inf row.
        self.assertTrue(np.isfinite(masked).all())

    def test_guard_choice_uses_the_same_table(self) -> None:
        policy = GuardPolicy()
        tools = ["fight.pass", "state.snapshot"]
        tool, probs = policy.choose_from_logits(
            np.array([5.0, 1.0], dtype=np.float32), tools, {"in_fight": False})
        self.assertEqual(tool, "state.snapshot")
        self.assertEqual(probs[0], 0.0)

    def test_fight_tools_are_legal_in_a_fight(self) -> None:
        observation = {"fight": {"active": True}}
        self.assertTrue(tool_is_legal("fight.pass", observation))
        self.assertTrue(tool_is_legal("fight.plan", observation))
        self.assertFalse(tool_is_legal("nav.goto", observation))
        self.assertFalse(tool_is_legal("hdv.buy", observation))
        self.assertTrue(tool_is_legal("safe.stop", observation))


if __name__ == "__main__":
    unittest.main()