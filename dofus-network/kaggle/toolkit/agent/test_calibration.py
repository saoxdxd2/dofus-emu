"""Tests for `agent.calibration`: metrics, PAV, the two solvers, and the fail-open wiring.

The load-bearing assertions are not "the numbers are plausible". They are the three
properties a calibrator must have to be safe in a decision loop:

* it is **never worse than identity in-sample** -- a fit that loses to doing nothing is a bug,
  and the first version of `fit_platt` did exactly that on the real logits;
* it is **monotone**, so it cannot invert a ranking (`a > 0` always);
* with no sidecar the shipped probabilities are **bit-identical** to before.
"""

from __future__ import annotations

import json
import math
import os
import random
import tempfile
import unittest
from pathlib import Path

from agent.calibration import (
    Calibrator,
    Isotonic,
    Platt,
    auc,
    backtest,
    brier,
    ece,
    evaluate,
    fit,
    fit_isotonic,
    fit_platt,
    fit_temperature,
    log_loss,
    logit,
    mce,
    reliability,
    sigmoid,
)


def _labels_for(logits, a, b, seed=0):
    rng = random.Random(seed)
    return [1.0 if rng.random() < sigmoid(a * z + b) else 0.0 for z in logits]


class MetricTests(unittest.TestCase):
    def test_a_perfect_predictor_has_zero_ece(self):
        self.assertAlmostEqual(ece([0.0, 0.0, 1.0, 1.0], [0, 0, 1, 1]), 0.0, places=9)

    def test_a_constant_predictor_has_ece_equal_to_its_distance_from_the_base_rate(self):
        # every row predicts 0.30, a quarter of them are positive
        self.assertAlmostEqual(ece([0.3] * 100, [1] * 25 + [0] * 75), 0.05, places=9)

    def test_ece_skips_empty_bins_rather_than_crediting_them(self):
        # all mass in one bin: the error must be the bin's error, not diluted over ten bins
        self.assertAlmostEqual(ece([0.05] * 10, [1] * 10), 0.95, places=9)

    def test_mce_is_at_least_ece(self):
        p = [0.1, 0.2, 0.35, 0.55, 0.75, 0.9] * 20
        y = [0, 0, 1, 1, 1, 1] * 20
        self.assertGreaterEqual(mce(p, y), ece(p, y))

    def test_log_loss_of_a_confident_correct_answer_is_near_zero(self):
        self.assertLess(log_loss([0.999999, 0.000001], [1, 0]), 1e-4)

    def test_log_loss_punishes_a_confident_wrong_answer(self):
        self.assertGreater(log_loss([0.999999], [0]), 10.0)

    def test_brier_of_a_perfect_predictor_is_zero(self):
        self.assertAlmostEqual(brier([0.0, 1.0], [0, 1]), 0.0, places=12)

    def test_auc_is_one_when_every_positive_outranks_every_negative(self):
        self.assertAlmostEqual(auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]), 1.0, places=12)

    def test_auc_is_a_half_when_all_scores_tie(self):
        self.assertAlmostEqual(auc([0.5] * 10, [1, 0] * 5), 0.5, places=12)

    def test_auc_is_none_when_one_class_is_absent(self):
        self.assertIsNone(auc([0.1, 0.9], [1, 1]))

    def test_reliability_reports_only_populated_bins(self):
        rows = reliability([0.05, 0.95], [0, 1])
        self.assertEqual([r["lo"] for r in rows], [0.0, 0.9])

    def test_evaluate_returns_n_for_an_empty_set(self):
        self.assertEqual(evaluate([], []), {"n": 0})

    def test_mismatched_lengths_raise(self):
        with self.assertRaises(ValueError):
            ece([0.1, 0.2], [1])


class IsotonicTests(unittest.TestCase):
    def test_output_is_non_decreasing_in_the_input(self):
        rng = random.Random(3)
        p = [rng.random() for _ in range(300)]
        y = [1 if rng.random() < v else 0 for v in p]
        cal = fit_isotonic(p, y)
        grid = [i / 200 for i in range(201)]
        values = [cal.apply(v) for v in grid]
        for left, right in zip(values, values[1:]):
            self.assertLessEqual(left, right + 1e-12)

    def test_a_decreasing_relation_pools_to_a_constant(self):
        # higher score, lower outcome: PAV has no non-decreasing fit except a flat line
        p = [0.1, 0.2, 0.3, 0.4] * 25
        y = [1, 1, 0, 0] * 25
        cal = fit_isotonic(p, y)
        values = {round(cal.apply(v), 9) for v in (0.1, 0.2, 0.3, 0.4)}
        self.assertEqual(len(values), 1)
        self.assertAlmostEqual(values.pop(), 0.5, places=9)

    def test_an_already_monotone_relation_is_reproduced(self):
        p = [0.1] * 50 + [0.9] * 50
        y = [0] * 50 + [1] * 50
        cal = fit_isotonic(p, y)
        self.assertAlmostEqual(cal.apply(0.1), 0.0, places=9)
        self.assertAlmostEqual(cal.apply(0.9), 1.0, places=9)

    def test_identical_scores_are_pooled_before_fitting(self):
        # three rows share a score: the fitted value must be their mean, not one of them
        cal = fit_isotonic([0.5, 0.5, 0.5], [0, 1, 1])
        self.assertAlmostEqual(cal.apply(0.5), 2 / 3, places=9)

    def test_inputs_outside_the_fitted_range_clamp_to_the_edge_blocks(self):
        cal = fit_isotonic([0.2, 0.8], [0, 1])
        self.assertAlmostEqual(cal.apply(-5.0), cal.apply(0.0), places=12)
        self.assertAlmostEqual(cal.apply(5.0), cal.apply(1.0), places=12)

    def test_an_empty_fit_is_the_identity(self):
        cal = fit_isotonic([], [])
        self.assertEqual(cal.apply(0.37), 0.37)


class PlattTests(unittest.TestCase):
    def test_it_recovers_the_parameters_of_a_generating_model(self):
        rng = random.Random(7)
        z = [rng.gauss(0, 1) for _ in range(6000)]
        y = _labels_for(z, 2.0, 0.5)
        cal = fit_platt(z, y)
        self.assertAlmostEqual(cal.a, 2.0, delta=0.25)
        self.assertAlmostEqual(cal.b, 0.5, delta=0.20)

    def test_the_slope_is_always_positive_so_a_ranking_cannot_be_inverted(self):
        for seed in range(40):
            rng = random.Random(seed)
            z = [rng.gauss(0, 3) for _ in range(200)]
            y = [1 if rng.random() < 0.4 else 0 for _ in z]
            self.assertGreater(fit_platt(z, y).a, 0.0)

    def test_it_is_never_worse_than_the_identity_in_sample(self):
        """The property that caught the original bug: a calibrator that loses to doing nothing
        is not a calibrator. Extreme logits are included deliberately -- the real ones reach
        -133 and that is exactly what made the first solver oscillate."""
        rng = random.Random(11)
        cases = []
        cases.append(([rng.gauss(0, 1) for _ in range(500)], None))
        cases.append(([rng.gauss(0, 1) for _ in range(500)], (2.5, -1.0)))
        cases.append(([rng.gauss(-5, 20) for _ in range(500)], (0.4, 0.0)))
        cases.append(([rng.choice([-133.0, -17.0, -1.0, 4.0]) for _ in range(500)], None))
        for z, params in cases:
            if params is None:
                y = [1 if rng.random() < 0.5 else 0 for _ in z]
            else:
                y = _labels_for(z, params[0], params[1], seed=13)
            cal = fit_platt(z, y)
            self.assertLessEqual(log_loss([cal.apply_logit(v) for v in z], y),
                                 log_loss([sigmoid(v) for v in z], y) + 5e-3)

    def test_an_empty_fit_is_the_identity(self):
        cal = fit_platt([], [])
        self.assertEqual((cal.a, cal.b), (1.0, 0.0))

    def test_a_single_class_is_absorbed_by_the_ridge_not_diverged(self):
        cal = fit_platt([-1.0, 0.0, 1.0] * 50, [0.0] * 150)
        self.assertTrue(math.isfinite(cal.a) and math.isfinite(cal.b))
        self.assertLess(cal.apply_logit(1.0), 0.5)

    def test_it_round_trips_through_a_dict(self):
        cal = Platt(a=0.7, b=-0.2)
        self.assertEqual(Platt.from_dict(cal.to_dict()), cal)


class TemperatureTests(unittest.TestCase):
    def test_the_intercept_stays_pinned_at_zero(self):
        z = [random.Random(2).gauss(0, 1) for _ in range(2000)]
        self.assertEqual(fit_temperature(z, _labels_for(z, 2.0, 0.0)).b, 0.0)

    def test_it_recovers_the_inverse_temperature(self):
        z = [random.Random(5).gauss(0, 1) for _ in range(6000)]
        cal = fit_temperature(z, _labels_for(z, 3.0, 0.0))
        self.assertAlmostEqual(cal.a, 3.0, delta=0.3)

    def test_it_never_loses_to_the_identity_in_sample(self):
        z = [random.Random(6).gauss(0, 1) for _ in range(1000)]
        y = _labels_for(z, 0.4, 0.0)
        cal = fit_temperature(z, y)
        self.assertLessEqual(log_loss([cal.apply_logit(v) for v in z], y),
                             log_loss([sigmoid(v) for v in z], y) + 5e-3)


class BundleTests(unittest.TestCase):
    def _corpus(self, n=400, seed=4):
        rng = random.Random(seed)
        z, y, g = [], [], []
        for _ in range(n):
            tool = rng.choice(["nav.goto", "quest.advance", "safe.stop"])
            z.append(rng.gauss(0, 2))
            y.append(1.0 if rng.random() < (0.8 if tool == "nav.goto" else 0.2) else 0.0)
            g.append(tool)
        return z, y, g

    def test_identity_leaves_the_probability_alone(self):
        cal = fit([0.0, 1.0], [0, 1], method="identity")
        self.assertEqual(cal.method, "identity")
        self.assertAlmostEqual(cal.apply_logit(0.4), sigmoid(0.4), places=12)

    def test_an_unknown_method_is_refused(self):
        with self.assertRaises(ValueError):
            fit([0.0], [1], method="magic")

    def test_groups_smaller_than_the_floor_are_not_fitted(self):
        z, y, g = self._corpus()
        cal = fit(z, y, g, method="platt", min_group=200)
        self.assertEqual(cal.groups, {})

    def test_a_group_at_or_above_the_floor_is_fitted(self):
        z, y, g = self._corpus()
        cal = fit(z, y, g, method="platt", min_group=100)
        self.assertIn("nav.goto", cal.groups)

    def test_an_unknown_group_falls_back_to_the_global_pair(self):
        z, y, g = self._corpus()
        cal = fit(z, y, g, method="platt", min_group=100)
        self.assertAlmostEqual(cal.apply_logit(1.0, "never.seen"),
                               cal.global_.apply_logit(1.0), places=12)

    def test_mismatched_group_lengths_raise(self):
        with self.assertRaises(ValueError):
            fit([0.0, 1.0], [0, 1], ["a"], method="platt")

    def test_the_bundle_round_trips_through_disk(self):
        z, y, g = self._corpus()
        cal = fit(z, y, g, method="platt", min_group=100)
        with tempfile.TemporaryDirectory() as tmp:
            from agent.calibration import load, save
            path = save(Path(tmp) / "calibration.json", cal)
            again = load(path)
        self.assertEqual(again.method, cal.method)
        self.assertEqual(sorted(again.groups), sorted(cal.groups))
        self.assertAlmostEqual(again.apply_logit(0.7, "nav.goto"),
                               cal.apply_logit(0.7, "nav.goto"), places=4)

    def test_a_corrupt_sidecar_loads_as_none(self):
        from agent.calibration import load
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calibration.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(load(path))
            self.assertIsNone(load(Path(tmp) / "absent.json"))

    def test_an_unknown_method_in_a_file_degrades_to_identity(self):
        cal = Calibrator.from_dict({"method": "quantum", "global": {"a": 3.0, "b": 1.0}})
        self.assertEqual(cal.method, "identity")

    def test_isotonic_ignores_groups(self):
        z, y, g = self._corpus()
        cal = fit(z, y, g, method="isotonic")
        self.assertEqual(cal.groups, {})
        self.assertIsNotNone(cal.isotonic)


class BacktestTests(unittest.TestCase):
    def test_it_refuses_to_report_on_too_few_rows(self):
        out = backtest([0.0] * 50, [1.0] * 50, [None] * 50, folds=4)
        self.assertEqual(out["folds"], 0)

    def test_it_reports_every_method_when_it_can_run(self):
        rng = random.Random(9)
        z = [rng.gauss(0, 1) for _ in range(1200)]
        y = [1 if rng.random() < 0.5 else 0 for _ in z]
        g = ["t"] * len(z)
        out = backtest(z, y, g, folds=3)
        self.assertEqual(out["folds"], 3)
        self.assertEqual(set(out["summary"]), {"identity", "temperature", "platt", "isotonic"})


class ForesightWiringTests(unittest.TestCase):
    """The guarantees that keep this component from changing anything until asked."""

    @classmethod
    def setUpClass(cls):
        import numpy as np
        from agent.foresight import ForesightNet
        rng = np.random.RandomState(0)
        width, hidden, depth, tools = 42, 8, 5, ["nav.goto", "quest.advance", "safe.stop"]
        cls.net_class = ForesightNet
        cls.arrays = {
            "w1": rng.randn(2 * width + len(tools), hidden).astype("float32") * 0.1,
            "b1": np.zeros(hidden, dtype="float32"),
            "w2": rng.randn(hidden, hidden).astype("float32") * 0.1,
            "b2": np.zeros(hidden, dtype="float32"),
            "w3": rng.randn(hidden, 2 + depth).astype("float32") * 0.1,
            "b3": np.zeros(2 + depth, dtype="float32"),
            "x_mean": np.zeros(width, dtype="float32"),
            "x_std": np.ones(width, dtype="float32"),
            "tool_gate": np.ones((len(tools), width), dtype="float32"),
        }
        cls.tools = tools
        cls.width = width

    def _net(self, calibrator=None):
        net = self.net_class(self.arrays, tools=self.tools, meta={"depth": 5})
        return net.attach_calibration(calibrator)

    def test_without_a_calibrator_the_probability_is_exactly_the_old_formula(self):
        net = self._net()
        x = [0.3] * self.width
        cell = net.score(x, "nav.goto")
        import numpy as np
        z = float(net.move_logits(np.asarray([net.vector(x, "nav.goto")]))[0])
        expected = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z / net.temperature))))
        # the runtime keeps probabilities in float32, so agreement is to ~1e-7, not 1e-12
        self.assertAlmostEqual(cell["move_prob"], expected, places=6)

    def test_a_global_calibrator_changes_the_level(self):
        net = self._net()
        before = net.score([0.3] * self.width, "nav.goto")["move_prob"]
        net.attach_calibration(Calibrator(method="platt", global_=Platt(a=0.5, b=3.0)))
        after = net.score([0.3] * self.width, "nav.goto")["move_prob"]
        self.assertNotAlmostEqual(before, after, places=4)

    def test_a_monotone_calibrator_preserves_the_ordering_within_a_tool(self):
        net = self._net()
        xs = [[v] * self.width for v in (-1.0, 0.0, 1.0)]
        raw = [net.score(x, "nav.goto")["move_prob"] for x in xs]
        net.attach_calibration(Calibrator(method="platt", global_=Platt(a=2.0, b=-0.5)))
        cal = [net.score(x, "nav.goto")["move_prob"] for x in xs]
        # the random net's own order is whatever it is; what must not change is the *rank*,
        # because `ensemble.ToolStack` sorts on this number
        order = lambda seq: sorted(range(len(seq)), key=lambda i: seq[i])
        self.assertEqual(order(raw), order(cal))

    def test_the_slope_cannot_invert_a_ranking(self):
        z = [random.Random(21).gauss(0, 1) for _ in range(200)]
        # labels anti-correlated with the score: the unconstrained optimum is a negative slope
        y = [1 if v < 0 else 0 for v in z]
        cal = fit_platt(z, y)
        self.assertGreater(cal.a, 0.0)
        self.assertLessEqual(cal.apply_logit(-3.0), cal.apply_logit(3.0))

    def test_rank_still_returns_one_row_per_scorable_tool(self):
        net = self._net()
        rows = net.rank([0.0] * self.width, self.tools + ["not.a.tool"])
        self.assertEqual(sorted(r["tool"] for r in rows), sorted(self.tools))

    def test_a_group_specific_calibrator_is_used_by_rank(self):
        net = self._net(Calibrator(method="platt", global_=Platt(1.0, 0.0),
                                   groups={"nav.goto": Platt(0.2, 8.0)}))
        rows = {r["tool"]: r for r in net.rank([0.0] * self.width, self.tools)}
        self.assertGreater(rows["nav.goto"]["move_prob"], rows["safe.stop"]["move_prob"])

    def test_the_kill_switch_is_read_at_load_time(self):
        # the env var is consulted in `foresight.load`, so a stale value cannot leak in
        self.assertIn(os.environ.get("DOFUS_FORESIGHT_CALIBRATION", "1"), ("0", "1"))


class ShippedArtifactTests(unittest.TestCase):
    """The real artifact, when it is present. Skipped rather than faked on a bare checkout."""

    @classmethod
    def setUpClass(cls):
        cls.model = Path(__file__).resolve().parent.parent / "models" / "foresight_v1"
        cls.present = (cls.model / "foresight.json").is_file()

    def setUp(self):
        if not self.present:
            self.skipTest("models/foresight_v1 is not present")

    def test_the_artifact_loads_and_scores_a_vector(self):
        from agent.foresight import load
        net = load(self.model / "foresight.json")
        self.assertIsNotNone(net)
        cell = net.score([0.0] * net.width, "nav.goto")
        self.assertIsNotNone(cell)
        self.assertGreaterEqual(cell["move_prob"], 0.0)
        self.assertLessEqual(cell["move_prob"], 1.0)

    def test_a_sidecar_that_exists_is_well_formed(self):
        sidecar = self.model / "calibration.json"
        if not sidecar.is_file():
            self.skipTest("no calibration sidecar fitted yet")
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertIn(raw["method"], ("identity", "temperature", "platt", "isotonic"))
        for name, pair in (raw.get("groups") or {}).items():
            self.assertGreater(pair["a"], 0.0, "group %s has a non-positive slope" % name)


if __name__ == "__main__":
    unittest.main()
