from __future__ import annotations

import itertools
import unittest
from dataclasses import replace

import numpy as np

from tools.delayed_belief_probe import (
    FixedLagBelief, Observation, attribution_controls, full_history_belief, run_probe,
)


class FixedLagBeliefTests(unittest.TestCase):
    def setUp(self):
        self.identity = np.eye(2)
        self.flip = np.array([[0.0, 1.0], [1.0, 0.0]])

    def observation(self, step, likelihood=(0.2, 0.8), sequence=None):
        return Observation("test", step if sequence is None else sequence, step, likelihood)

    def test_late_evidence_is_replayed_through_intervening_action(self):
        model = FixedLagBelief([0.5, 0.5], epoch="test")
        model.advance(self.identity)
        model.advance(self.flip)
        self.assertEqual(model.ingest(self.observation(1, (0.0, 1.0))), "accepted")
        np.testing.assert_array_equal(model.belief, [1.0, 0.0])
        arrival_order = np.array([0.5, 0.5]) * np.array([0.0, 1.0])
        arrival_order /= arrival_order.sum()
        self.assertNotEqual(int(np.argmax(model.belief)), int(np.argmax(arrival_order)))

    def test_filter_matches_independent_exhaustive_hidden_paths(self):
        prior = np.array([0.3, 0.7])
        transitions = [np.array([[0.8, 0.2], [0.1, 0.9]]), self.flip,
                       np.array([[0.6, 0.4], [0.3, 0.7]]), self.identity]
        observations = [self.observation(0), self.observation(1, (0.9, 0.1)),
                        self.observation(3, (0.6, 0.4))]
        expected = np.zeros(2)
        for path in itertools.product(range(2), repeat=5):
            probability = prior[path[0]]
            for step, transition in enumerate(transitions, 1):
                probability *= transition[path[step - 1], path[step]]
            for event in observations:
                probability *= event.likelihood[path[event.source_step]]
            expected[path[-1]] += probability
        expected /= expected.sum()
        for order in itertools.permutations(observations):
            model = FixedLagBelief(prior, epoch="test")
            for transition in transitions:
                model.advance(transition)
            for event in order:
                self.assertEqual(model.ingest(event), "accepted")
            np.testing.assert_allclose(model.belief, expected, atol=1e-14)

    def test_zero_delay_reproduces_standard_filter_exactly(self):
        model = FixedLagBelief([0.5, 0.5], epoch="test", capacity=3)
        expected = np.array([0.5, 0.5])
        for step in range(20):
            if step:
                model.advance(self.flip)
                expected = expected @ self.flip
            event = self.observation(step)
            self.assertEqual(model.ingest(event), "accepted")
            expected *= event.likelihood
            expected /= expected.sum()
            np.testing.assert_allclose(model.belief, expected, atol=1e-14)

    def test_missing_observations_are_prediction_only_not_failure_labels(self):
        model = FixedLagBelief([0.5, 0.5], epoch="test", capacity=3)
        for _ in range(20):
            model.advance(self.flip)
        np.testing.assert_allclose(model.belief, [0.5, 0.5])
        self.assertTrue(model.all_received_evidence_used)
        self.assertEqual(len(model.buffered_steps), 3)

    def test_duplicate_does_not_strengthen_evidence(self):
        model = FixedLagBelief([0.5, 0.5], epoch="test")
        event = self.observation(0)
        model.ingest(event)
        once = model.belief
        for _ in range(5):
            self.assertEqual(model.ingest(event), "duplicate")
        np.testing.assert_array_equal(model.belief, once)
        np.testing.assert_allclose(once, [0.2, 0.8])

    def test_conflicting_source_identity_is_not_overwritten(self):
        model = FixedLagBelief([0.5, 0.5], epoch="test")
        event = self.observation(0)
        model.ingest(event)
        once = model.belief
        self.assertEqual(model.ingest(replace(event, likelihood=(0.8, 0.2))), "conflict")
        np.testing.assert_array_equal(model.belief, once)
        self.assertFalse(model.all_received_evidence_used)

    def test_distinct_id_at_same_source_tick_is_not_a_second_measurement(self):
        model = FixedLagBelief([0.5, 0.5], epoch="test")
        model.ingest(self.observation(0))
        once = model.belief
        self.assertEqual(model.ingest(self.observation(0, sequence=5)), "conflict")
        np.testing.assert_array_equal(model.belief, once)

    def test_identity_cannot_move_to_another_source_time(self):
        model = FixedLagBelief([0.5, 0.5], epoch="test")
        model.advance(self.identity)
        model.ingest(self.observation(0))
        once = model.belief
        self.assertEqual(model.ingest(self.observation(1, sequence=0)), "conflict")
        np.testing.assert_array_equal(model.belief, once)

    def test_wrong_epoch_is_ignored(self):
        model = FixedLagBelief([0.5, 0.5], epoch="test")
        event = replace(self.observation(0), epoch="previous-session")
        self.assertEqual(model.ingest(event), "wrong_epoch")
        np.testing.assert_array_equal(model.belief, [0.5, 0.5])
        self.assertTrue(model.all_received_evidence_used)

    def test_missing_source_time_is_not_filled_with_receipt_time(self):
        model = FixedLagBelief([0.5, 0.5], epoch="test")
        model.advance(self.flip)
        event = replace(self.observation(0), source_step=None)
        self.assertEqual(model.ingest(event), "unknown_source_time")
        np.testing.assert_array_equal(model.belief, [0.5, 0.5])
        self.assertFalse(model.all_received_evidence_used)

    def test_future_source_time_does_not_mutate_belief(self):
        model = FixedLagBelief([0.5, 0.5], epoch="test")
        self.assertEqual(model.ingest(self.observation(1)), "future_source_time")
        np.testing.assert_array_equal(model.belief, [0.5, 0.5])
        self.assertFalse(model.all_received_evidence_used)

    def test_evidence_outside_window_is_not_applied_to_present(self):
        model = FixedLagBelief([0.5, 0.5], epoch="test", capacity=2)
        model.advance(self.flip)
        model.advance(self.flip)
        self.assertEqual(model.ingest(self.observation(0)), "outside_window")
        self.assertEqual(model.buffered_steps, (1, 2))
        np.testing.assert_array_equal(model.belief, [0.5, 0.5])
        self.assertFalse(model.all_received_evidence_used)

    def test_checkpoint_preserves_old_evidence_without_recounting_it(self):
        model = FixedLagBelief([0.5, 0.5], epoch="test", capacity=2)
        model.ingest(self.observation(0))
        for _ in range(50):
            model.advance(self.flip)
            self.assertLessEqual(len(model.buffered_steps), 2)
        np.testing.assert_allclose(model.belief, [0.2, 0.8])
        model.ingest(self.observation(49, (0.9, 0.1)))
        full = full_history_belief([0.5, 0.5], [self.flip] * 50,
                                  {0: self.observation(0), 49: self.observation(49, (0.9, 0.1))})
        np.testing.assert_allclose(model.belief, full, atol=1e-14)

    def test_capacity_one_handles_immediate_observations(self):
        model = FixedLagBelief([0.5, 0.5], epoch="test", capacity=1)
        model.ingest(self.observation(0))
        model.advance(self.flip)
        self.assertEqual(model.ingest(self.observation(1, (0.9, 0.1))), "accepted")
        np.testing.assert_allclose(model.belief, [0.72 / 0.74, 0.02 / 0.74])

    def test_impossible_evidence_rolls_back_all_partial_recomputation(self):
        model = FixedLagBelief([0.5, 0.5], epoch="test")
        model.advance(self.identity)
        model.ingest(self.observation(1, (1.0, 0.0)))
        once = model.belief
        self.assertEqual(model.ingest(self.observation(0, (0.0, 1.0))), "inconsistent_evidence")
        np.testing.assert_array_equal(model.belief, once)
        self.assertFalse(model.all_received_evidence_used)
        self.assertEqual(model.ingest(self.observation(0, (0.9, 0.1))), "accepted")

    def test_returned_belief_is_not_mutable_internal_state(self):
        model = FixedLagBelief([0.5, 0.5], epoch="test")
        value = model.belief
        value[:] = 0
        np.testing.assert_array_equal(model.belief, [0.5, 0.5])

    def test_invalid_boundary_inputs_are_rejected(self):
        for prior in ([0.5, -0.5], [1.0, 1.0], [float("nan"), 0], []):
            with self.subTest(prior=prior), self.assertRaises(ValueError):
                FixedLagBelief(prior, epoch="test")
        with self.assertRaises(ValueError):
            FixedLagBelief([0.5, 0.5], epoch="test", capacity=0)
        model = FixedLagBelief([0.5, 0.5], epoch="test")
        for matrix in ([[1, 1], [0, 1]], [[1]], [[1, 0], [-1, 2]]):
            with self.subTest(matrix=matrix), self.assertRaises(ValueError):
                model.advance(matrix)
        for likelihood in ((0.0, 0.0), (float("nan"), 1), (-1, 1), (2, 1), (1,)):
            with self.subTest(likelihood=likelihood), self.assertRaises(ValueError):
                model.ingest(self.observation(0, likelihood))


class ProbeTests(unittest.TestCase):
    def test_builder_abstains_without_inventing_causal_ownership(self):
        report = attribution_controls()
        self.assertEqual(report["clean_credited"], [])
        self.assertEqual(report["delayed_credited"], [])
        self.assertIsNone(report["causal_owner_from_belief_replay"])

    def test_probe_beats_a_control_that_ignores_source_time(self):
        report = run_probe(episodes=24, horizon=24, seed=731)
        self.assertTrue(report["passed"], report)
        self.assertLess(report["maximum_full_history_error"], 1e-12)
        self.assertLess(report["paired_episode_nll_ci95"][1], 0)
        self.assertEqual(report["decisions"], 576)
        self.assertGreater(report["ingest_statuses"]["duplicate"], 0)
        self.assertEqual(set(report["ingest_statuses"]), {"accepted", "duplicate"})
        self.assertIn("not gameplay", report["scope"])

    def test_generation_and_scoring_are_seed_reproducible(self):
        a = run_probe(episodes=4, horizon=12, seed=1)
        b = run_probe(episodes=4, horizon=12, seed=1)
        self.assertEqual(a["trace_sha256"], b["trace_sha256"])
        self.assertEqual(a["metrics"], b["metrics"])
        self.assertEqual(a["paired_episode_nll_ci95"], b["paired_episode_nll_ci95"])
        c = run_probe(episodes=4, horizon=12, seed=2)
        self.assertNotEqual(a["trace_sha256"], c["trace_sha256"])


if __name__ == "__main__":
    unittest.main()
