"""Tests for ProblemNet: the supervisor's judgement about what to try next.

The net has to be safe to ship *before* it has been trained, so most of these are
about the "no opinion" path: a fresh model must return None, a stale file must load
as None, and a remedy the component cannot perform must never come back as a hint.
"""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path

import numpy as np

from agent import problem_nn as P


def situation(**overrides):
    fields = {"component": "farming", "detail": "idle for 100s",
              "failures": 1, "seconds_since_progress": 100.0,
              "last_remedy": None, "last_outcome": None, "dry_run": False}
    fields.update(overrides)
    return fields


def world(rows: int = 240, seed: int = 3):
    """A made-up world where the detail marker decides which remedy works.

    Nothing here is a claim about the game. It is the shape of the signal the
    supervisor produces -- a situation, a remedy, an outcome -- and the test is
    whether the net can recover the mapping from it.
    """
    rng = random.Random(seed)
    by_marker = {"stayed on": "travel", "no monster": "walk",
                 "not found": "restart", "refused": "rearm"}
    out = []
    for _ in range(rows):
        marker = rng.choice(sorted(by_marker))
        right = by_marker[marker]
        worked = rng.random() < 0.85
        wrong = [name for name in P.REMEDIES if name != right]
        out.append({
            "component": "farming", "detail": "failed: " + marker,
            "failures": rng.randint(1, 5),
            "seconds_since_progress": rng.randint(20, 300),
            "last_remedy": rng.choice(P.REMEDIES), "last_outcome": "failed",
            "remedy": right if worked else rng.choice(wrong),
            "outcome": "worked" if worked else "failed",
        })
    return out, by_marker


class FeatureTests(unittest.TestCase):
    def test_the_vector_is_a_fixed_width(self):
        self.assertEqual(len(P.features_of(situation())), P.FEATURE_WIDTH)
        self.assertEqual(len(P.features_of({})), P.FEATURE_WIDTH)

    def test_every_field_has_a_defined_meaning_when_missing(self):
        empty = P.features_of({})
        self.assertEqual(len(empty), P.FEATURE_WIDTH)
        # The one deliberate non-zero: "no previous outcome" is its own slot, so a
        # first attempt is distinguishable from an attempt that just failed.
        outcome_offset = P.FEATURE_WIDTH - len(P.OUTCOMES)
        self.assertEqual(empty[outcome_offset:], [1.0, 0.0, 0.0])
        self.assertEqual(sum(empty[:outcome_offset]), 0.0)

    def test_garbage_does_not_raise(self):
        features = P.features_of({"component": None, "detail": 12,
                                  "failures": "many", "seconds_since_progress": [],
                                  "last_remedy": 3, "last_outcome": {}})
        self.assertEqual(len(features), P.FEATURE_WIDTH)

    def test_the_component_lands_in_its_own_slot(self):
        for index, name in enumerate(P.COMPONENTS):
            features = P.features_of(situation(component=name))
            self.assertEqual(features[:len(P.COMPONENTS)].index(1.0), index)

    def test_the_detail_markers_are_read_from_the_text(self):
        offset = len(P.COMPONENTS) + 3
        features = P.features_of(situation(detail="nav.goto stayed on map 1"))
        self.assertEqual(features[offset + P.DETAIL_MARKERS.index("stayed on")], 1.0)
        self.assertEqual(sum(features[offset:offset + len(P.DETAIL_MARKERS)]), 1.0)

    def test_the_attempt_count_is_log_scaled_and_bounded(self):
        index = len(P.COMPONENTS)
        self.assertLess(P.features_of(situation(failures=1))[index], 0.5)
        self.assertLessEqual(P.features_of(situation(failures=10 ** 6))[index], 1.0)


class FreshNetTests(unittest.TestCase):
    def test_a_fresh_net_has_no_opinion(self):
        net = P.ProblemNet.fresh()
        self.assertFalse(net.ready())
        self.assertIsNone(net.suggest(situation(), ["engage", "wait"]))

    def test_a_fresh_net_still_answers_with_probabilities(self):
        answer = P.ProblemNet.fresh().predict(situation())
        self.assertEqual(len(answer["remedies"]), len(P.REMEDIES))
        self.assertAlmostEqual(sum(row["p"] for row in answer["remedies"]), 1.0, places=4)
        self.assertGreaterEqual(answer["p_fail"], 0.0)
        self.assertLessEqual(answer["p_fail"], 1.0)

    def test_the_remedy_order_is_stable_on_ties(self):
        net = P.ProblemNet.fresh()
        first = [row["remedy"] for row in net.predict(situation())["remedies"]]
        second = [row["remedy"] for row in net.predict(situation())["remedies"]]
        self.assertEqual(first, second)


class TrainingTests(unittest.TestCase):
    def test_it_recovers_the_mapping_it_was_shown(self):
        rows, by_marker = world()
        net = P.train(rows, epochs=600)
        self.assertTrue(net.ready())
        for marker, expected in by_marker.items():
            answer = net.predict(situation(detail=marker, failures=2))
            self.assertEqual(answer["best"], expected,
                             "%s should call for %s, got %s" % (marker, expected,
                                                                answer["best"]))

    def test_the_escalation_head_separates_the_hard_situations(self):
        # Everything with "refused" in it fails; everything else works.
        rows = []
        for _ in range(120):
            hard = _ % 2 == 0
            rows.append({"component": "brain", "detail": "refused" if hard else "fine",
                         "failures": 1, "seconds_since_progress": 10,
                         "remedy": "restart", "outcome": "failed" if hard else "worked"})
        net = P.train(rows, epochs=400)
        hard = net.predict(situation(component="brain", detail="refused"))["p_fail"]
        easy = net.predict(situation(component="brain", detail="fine"))["p_fail"]
        self.assertGreater(hard, easy + 0.2)

    def test_failed_rows_do_not_vote_against_the_remedy_that_was_tried(self):
        # Every attempt failed, so there is nothing to learn about *which* remedy
        # works -- but the trunk still sees them and must not raise.
        rows = [{"component": "farming", "detail": "idle", "failures": 3,
                 "remedy": "engage", "outcome": "failed"} for _ in range(50)]
        net = P.train(rows, epochs=50)
        self.assertEqual(net.rows, 50)
        self.assertEqual(len(net.predict(situation())["remedies"]), len(P.REMEDIES))

    def test_training_with_no_usable_rows_returns_the_net_unchanged(self):
        net = P.ProblemNet.fresh()
        before = np.array(net.params["w1"], copy=True)
        again = P.train([{"component": "x"}], epochs=10, net=net)
        self.assertTrue(np.array_equal(before, again.params["w1"]))
        self.assertEqual(again.rows, 0)

    def test_training_is_deterministic_for_the_same_rows(self):
        rows, _ = world(rows=80)
        first = P.train(rows, epochs=60, seed=1)
        second = P.train(rows, epochs=60, seed=1)
        self.assertTrue(np.allclose(first.params["w1"], second.params["w1"]))


class SuggestTests(unittest.TestCase):
    def _trained(self):
        rows, _ = world()
        return P.train(rows, epochs=600)

    def test_it_never_suggests_a_remedy_the_component_cannot_perform(self):
        net = self._trained()
        pick = net.suggest(situation(detail="stayed on"), ["engage", "wait"])
        self.assertIn(pick, ("engage", "wait"))

    def test_it_returns_none_when_nothing_offered_is_known(self):
        net = self._trained()
        self.assertIsNone(net.suggest(situation(), ["nonsense"]))

    def test_it_returns_none_when_nothing_is_offered(self):
        self.assertIsNone(self._trained().suggest(situation(), []))


class PersistenceTests(unittest.TestCase):
    def test_a_model_round_trips(self):
        rows, by_marker = world(rows=120)
        net = P.train(rows, epochs=200)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "problem_net.json"
            net.save(path)
            again = P.ProblemNet.load(path)
        self.assertIsNotNone(again)
        self.assertEqual(again.rows, net.rows)
        self.assertEqual(again.predict(situation(detail="stayed on"))["best"],
                         net.predict(situation(detail="stayed on"))["best"])

    def test_a_missing_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(P.ProblemNet.load(Path(folder) / "absent.json"))

    def test_a_file_from_another_build_loads_as_none(self):
        rows, _ = world(rows=60)
        net = P.train(rows, epochs=10)
        payload = net.as_dict()
        payload["version"] = P.FEATURE_VERSION + 1
        self.assertIsNone(P.ProblemNet.from_dict(payload))
        payload = net.as_dict()
        payload["remedies"] = ["somethingelse"]
        self.assertIsNone(P.ProblemNet.from_dict(payload))
        payload = net.as_dict()
        payload["params"]["w1"] = [[1.0]]
        self.assertIsNone(P.ProblemNet.from_dict(payload))

    def test_a_broken_file_loads_as_none(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "problem_net.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(P.ProblemNet.load(path))


class MemoryTests(unittest.TestCase):
    def test_a_row_is_written_only_when_its_outcome_is_known(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = P.ProblemMemory(Path(folder) / "rows.jsonl")
            memory.record(situation(), "engage")
            self.assertEqual(memory.rows(), [])
            row = memory.label("farming", "worked")
            self.assertIsNotNone(row)
            self.assertEqual(row["remedy"], "engage")
            rows = memory.rows()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["outcome"], "worked")
            # The label carries the situation, not just the verdict.
            self.assertEqual(rows[0]["detail"], "idle for 100s")

    def test_labelling_without_an_attempt_does_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = P.ProblemMemory(Path(folder) / "rows.jsonl")
            self.assertIsNone(memory.label("farming", "worked"))
            self.assertEqual(memory.rows(), [])

    def test_an_unknown_outcome_is_not_written(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = P.ProblemMemory(Path(folder) / "rows.jsonl")
            memory.record(situation(), "engage")
            self.assertIsNone(memory.label("farming", "pending"))
            self.assertEqual(memory.rows(), [])

    def test_a_corrupt_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rows.jsonl"
            path.write_text('{"remedy": "engage", "outcome": "worked"}\nnot json\n',
                            encoding="utf-8")
            self.assertEqual(len(P.ProblemMemory(path).rows()), 1)

    def test_fit_refuses_below_the_minimum(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = P.ProblemMemory(Path(folder) / "rows.jsonl")
            for _ in range(P.MIN_ROWS - 1):
                memory.record(situation(), "engage")
                memory.label("farming", "worked")
            self.assertIsNone(memory.fit(Path(folder) / "net.json"))
            self.assertFalse((Path(folder) / "net.json").exists())

    def test_fit_writes_a_model_once_there_are_enough_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = P.ProblemMemory(Path(folder) / "rows.jsonl")
            for index in range(P.MIN_ROWS + 10):
                memory.record(situation(detail="stayed on"), "travel")
                memory.label("farming", "worked" if index % 4 else "failed")
            model = Path(folder) / "net.json"
            net = memory.fit(model, epochs=50)
            self.assertIsNotNone(net)
            self.assertTrue(model.exists())


class AdvisorTests(unittest.TestCase):
    def test_without_a_model_it_has_no_opinion_and_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            advisor = P.ProblemAdvisor(Path(folder) / "absent.json",
                                       Path(folder) / "rows.jsonl")
            self.assertIsNone(advisor.choose(situation(), ["engage", "wait"]))
            self.assertEqual(advisor.asked, 1)
            self.assertEqual(advisor.advised, 0)
            self.assertFalse(advisor.report()["ready"])

    def test_learning_never_raises(self):
        # A path under a file, not a directory: the write must fail, and the
        # supervisor must not notice anything but a missing row.
        with tempfile.TemporaryDirectory() as folder:
            blocker = Path(folder) / "blocker"
            blocker.write_text("not a directory", encoding="utf-8")
            advisor = P.ProblemAdvisor(blocker / "absent.json",
                                       blocker / "rows.jsonl")
            advisor.learn(situation(), "engage")
            self.assertIsNone(advisor.label("farming", "worked"))
            self.assertIsNone(advisor.choose(situation(), ["engage"]))

    def test_a_trained_model_advises_and_counts_it(self):
        rows, _ = world(rows=200)
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / "net.json"
            P.train(rows, epochs=400).save(model)
            advisor = P.ProblemAdvisor(model, Path(folder) / "rows.jsonl")
            pick = advisor.choose(situation(detail="stayed on"), ["engage", "travel"])
            self.assertEqual(pick, "travel")
            self.assertEqual(advisor.advised, 1)
            self.assertTrue(advisor.report()["ready"])


if __name__ == "__main__":
    unittest.main()
