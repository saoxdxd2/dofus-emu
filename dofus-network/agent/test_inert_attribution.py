"""Tests for `agent/inert_attribution.py`.

Two kinds of test live here. The synthetic ones pin the *logic* (layer precedence,
differential statistics, era partitioning) so a future edit cannot silently turn a
cause into a tautology. One corpus-level test pins the *finding*: the inert rate is
not a constant and `obs_source=carried` is not a cause. It is guarded by a row-count
check because the corpus is rebuilt by live sessions, and an empty or tiny corpus is
not evidence against anything.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from agent.inert_attribution import (
    LAYERS,
    _gate,
    _num,
    attribute,
    first_failure,
    is_actionable,
    is_inert,
    load_rows,
    risk_ratio,
    signals,
    veto_candidates,
)


def row(**kw):
    base = {
        "has_pair": True,
        "was_read": False,
        "x": [0.0] * 42,
        "tool": "quest.advance",
        "moved": False,
        "progress": False,
        "at": 1000.0,
        "ok": True,
        "repeat_streak": 0,
        "miss_streak": 0,
        "obs_age_ms": 10.0,
        "obs_source": "carried",
        "claim": "at:1",
        "claim_met": False,
        "gate": {},
    }
    base.update(kw)
    return base


class CoercionTests(unittest.TestCase):
    def test_numbers_arrive_as_strings_from_the_jsonl_writer(self):
        self.assertEqual(_num("3"), 3.0)
        self.assertEqual(_num(None), 0.0)
        self.assertEqual(_num(""), 0.0)
        self.assertEqual(_num("not a number", -1.0), -1.0)

    def test_a_reprd_gate_is_not_treated_as_a_dict(self):
        self.assertEqual(_gate(row(gate="{'rule': 'x'}")), {})
        self.assertEqual(_gate(row(gate={"would_veto": True}))["would_veto"], True)

    def test_nan_is_finite_but_not_a_valid_number_here(self):
        # a NaN streak must not count as "repeating"; float('nan') > 0 is False,
        # which is the behaviour we rely on, so pin it rather than assume it
        self.assertFalse(_num("nan") > 0)

    def test_reads_are_not_actions(self):
        self.assertFalse(is_actionable(row(was_read=True)))
        self.assertFalse(is_actionable(row(has_pair=False)))
        self.assertFalse(is_actionable(row(x=[])))
        self.assertTrue(is_actionable(row()))

    def test_inert_means_neither_the_world_nor_a_quest_counter_changed(self):
        self.assertTrue(is_inert(row()))
        self.assertFalse(is_inert(row(moved=True)))
        self.assertFalse(is_inert(row(progress=True)))
        self.assertFalse(is_inert(row(moved=True, progress=True)))


class SignalTests(unittest.TestCase):
    def test_every_signal_is_a_boolean(self):
        for value in signals(row()).values():
            self.assertIsInstance(value, bool)

    def test_refused_and_error_are_recorded_separately(self):
        s = signals(row(refused="npc_id_required", error="client directive failed"))
        self.assertTrue(s["refused"] and s["error"])

    def test_carried_and_claim_unmet_are_recorded_but_not_used_as_layers(self):
        s = signals(row())
        self.assertTrue(s["carried"] and s["claim_unmet"])
        self.assertNotIn("carried", LAYERS)
        self.assertNotIn("claim_unmet", LAYERS)

    def test_stale_threshold_is_two_seconds(self):
        self.assertFalse(signals(row(obs_age_ms="1999"))["stale"])
        self.assertTrue(signals(row(obs_age_ms="2001"))["stale"])

    def test_veto_reads_the_gate_dict_without_raising(self):
        self.assertTrue(signals(row(gate={"would_veto": True}))["veto"])
        self.assertFalse(signals(row(gate="{'would_veto': True}"))["veto"])


class LayerPrecedenceTests(unittest.TestCase):
    def test_a_concrete_refusal_beats_a_repeat_streak(self):
        self.assertEqual(
            first_failure(row(refused="known_kick_signature: objective 14070", repeat_streak=9)),
            "decision:known_kick")

    def test_each_decision_reason_is_classified(self):
        cases = {
            "known_kick_signature: x": "decision:known_kick",
            "npc_id_required: quest is active": "decision:missing_arg",
            "nav.goto needs one of: cell, npc_id": "decision:nav_args",
            "equipItem sent nothing; pass slot": "decision:nav_args",
            "map 1 is not adjacent to 2; take one of the exits": "decision:not_adjacent",
        }
        for text, expected in cases.items():
            with self.subTest(text=text[:30]):
                self.assertEqual(first_failure(row(refused=text)), expected)

    def test_dialog_refusals_are_client_side_not_planner_side(self):
        self.assertEqual(first_failure(row(refused="dialog close failed: TypeError")),
                         "client:dialog")
        self.assertEqual(first_failure(row(refused="quest_state_syncing")),
                         "client:other_refusal")

    def test_no_client_is_a_dispatch_failure(self):
        self.assertEqual(
            first_failure(row(error="client directive failed: no client instance is running")),
            "dispatch:no_client")

    def test_refused_and_error_are_deduplicated(self):
        r = row(refused="npc_id_required: x")
        r["error"] = r["refused"]
        self.assertEqual(first_failure(r), "decision:missing_arg")

    def test_error_without_refusal_is_a_client_error(self):
        self.assertEqual(first_failure(row(error="something exploded")),
                         "client:other_error")

    def test_ok_false_is_a_dispatch_failure(self):
        self.assertEqual(first_failure(row(ok=False)), "dispatch:failed")

    def test_a_pre_action_repeat_is_the_residual_layer(self):
        self.assertEqual(first_failure(row(repeat_streak=2)), "policy:repeat")
        self.assertEqual(first_failure(row(miss_streak=2)), "policy:repeat")
        self.assertEqual(first_failure(row(gate={"would_veto": True})), "policy:repeat")

    def test_stale_is_only_reached_when_nothing_else_fired(self):
        self.assertEqual(first_failure(row(obs_age_ms=5000)), "sensing:stale")

    def test_an_unexplained_inert_row_is_not_forced_into_a_layer(self):
        self.assertEqual(first_failure(row()), "unexplained")

    def test_every_layer_name_is_reachable(self):
        reached = {first_failure(r) for r in (
            row(refused="known_kick_signature"), row(refused="npc_id_required"),
            row(refused="nav.goto needs one of"), row(refused="not adjacent"),
            row(refused="dialog still open"), row(refused="objective_server_counted"),
            row(error="no client instance is running"), row(ok=False),
            row(error="mystery"), row(repeat_streak=1), row(obs_age_ms=5000), row(),
        )}
        self.assertEqual(reached, set(LAYERS))


class StatisticsTests(unittest.TestCase):
    def test_lift_is_the_difference_of_two_rates(self):
        r = risk_ratio(25, 100, 10, 200)
        self.assertAlmostEqual(r["inert"], 0.25)
        self.assertAlmostEqual(r["effective"], 0.05)
        self.assertAlmostEqual(r["lift_pp"], 20.0)
        self.assertAlmostEqual(r["ratio"], 5.0)

    def test_a_signal_absent_from_effective_rows_has_an_infinite_point_estimate(self):
        r = risk_ratio(30, 100, 0, 200)
        self.assertIsNone(r["ratio"])

    def test_the_corrected_interval_stays_finite_when_a_cell_is_empty(self):
        r = risk_ratio(30, 100, 0, 200)
        lo, hi = r["corrected_ci95"]
        self.assertTrue(0 < lo < hi < float("inf"))
        self.assertGreater(r["corrected_ratio"], 1.0)

    def test_a_signal_that_is_more_common_on_effective_rows_is_not_a_cause(self):
        r = risk_ratio(96, 100, 99, 100)
        self.assertLess(r["corrected_ratio"], 1.0)
        self.assertLess(r["lift_pp"], 0.0)

    def test_an_empty_side_does_not_divide_by_zero(self):
        self.assertIsNone(risk_ratio(0, 0, 1, 1)["ratio"])
        self.assertIsNone(risk_ratio(1, 1, 0, 0)["ratio"])


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            row(at=1.0, refused="known_kick_signature"),
            row(at=2.0, error="client directive failed: no client instance is running"),
            row(at=3.0, repeat_streak=2),
            row(at=4.0),
            row(at=5.0, moved=True, claim_met=True),
            row(at=6.0, progress=True, claim_met=True),
            row(at=7.0, moved=True, claim_met=True, repeat_streak=1),
        ]

    def test_exclusive_shares_sum_to_one(self):
        rep = attribute(self.rows, eras=2)
        self.assertEqual(sum(rep["exclusive"]["counts"].values()), rep["inert"])
        self.assertAlmostEqual(sum(rep["exclusive"]["shares"].values()), 1.0, places=6)

    def test_each_inert_row_is_counted_exactly_once(self):
        rep = attribute(self.rows, eras=2)
        self.assertEqual(rep["inert"], 4)
        self.assertEqual(rep["effective"], 3)

    def test_eras_partition_the_rows_in_time_order(self):
        rep = attribute(self.rows, eras=3)
        self.assertEqual(sum(e["rows"] for e in rep["eras"]), 7)
        for a, b in zip(rep["eras"], rep["eras"][1:]):
            self.assertLess(a["at_to"], b["at_from"])

    def test_a_refusal_is_differential_in_the_synthetic_fixture(self):
        rep = attribute(self.rows, eras=2)
        s = rep["specificity"]["refused"]
        self.assertGreater(s["inert"], s["effective"])
        self.assertIsNone(s["ratio"])

    def test_carried_is_reported_as_not_differential(self):
        rep = attribute(self.rows, eras=2)
        self.assertLessEqual(rep["specificity"]["carried"]["corrected_ratio"], 1.2)

    def test_the_report_states_that_it_is_not_an_intervention(self):
        self.assertIn("No intervention", attribute(self.rows)["caveat"])

    def test_an_empty_corpus_does_not_raise_or_divide(self):
        rep = attribute([], eras=4)
        self.assertEqual(rep["actionable"], 0)
        self.assertIsNone(rep["inert_rate"])

    def test_non_actionable_rows_are_excluded(self):
        rep = attribute(self.rows + [row(was_read=True), row(has_pair=False)])
        self.assertEqual(rep["actionable"], 7)

    def test_by_tool_counts_partition_the_corpus(self):
        rep = attribute(self.rows)
        self.assertEqual(sum(t["rows"] for t in rep["by_tool"]), rep["actionable"])


class VetoTests(unittest.TestCase):
    def test_min_support_filters_small_groups(self):
        rows = [row(tool="nav.goto", repeat_streak=1) for _ in range(10)]
        self.assertEqual(veto_candidates(rows, min_support=50), [])
        self.assertEqual(len(veto_candidates(rows, min_support=1)), 1)

    def test_precision_is_the_share_of_flagged_rows_that_were_inert(self):
        rows = ([row(tool="nav.goto", repeat_streak=1) for _ in range(9)]
                + [row(tool="nav.goto", repeat_streak=1, moved=True) for _ in range(1)])
        cand = veto_candidates(rows, min_support=1)[0]
        self.assertAlmostEqual(cand["precision"], 0.9)
        self.assertEqual(cand["inert_removed_upper_bound"], 9)
        self.assertEqual(cand["effective_cost_upper_bound"], 1)

    def test_a_veto_uses_repeat_only_and_never_miss_streak(self):
        rows = ([row(miss_streak=5) for _ in range(100)]
                + [row(repeat_streak=1) for _ in range(100)])
        cand = veto_candidates(rows, min_support=1)
        self.assertEqual(sum(c["flagged"] for c in cand), 100)

    def test_candidates_are_sorted_by_net_upper_bound(self):
        rows = ([row(tool="a", repeat_streak=1) for _ in range(60)]
                + [row(tool="b", repeat_streak=1) for _ in range(120)])
        cand = veto_candidates(rows, min_support=1)
        self.assertEqual([c["tool"] for c in cand], ["b", "a"])

    def test_a_veto_never_flags_a_read(self):
        rows = [row(was_read=True, repeat_streak=1) for _ in range(100)]
        self.assertEqual(veto_candidates(rows, min_support=1), [])


class CorpusFindingTests(unittest.TestCase):
    """One guarded live-data test, so the headline claim cannot silently rot."""

    @classmethod
    def setUpClass(cls):
        cls.rep = None
        if Path("data/transitions/train.jsonl").exists():
            cls.rep = attribute(load_rows(), eras=4)

    def setUp(self):
        if self.rep is None or self.rep["actionable"] < 2000:
            self.skipTest("the transition corpus is not present at this size")

    def test_the_inert_rate_is_not_a_single_number(self):
        rates = [e["inert_rate"] for e in self.rep["eras"]]
        self.assertGreater(max(rates) - min(rates), 0.20)

    def test_the_newest_era_is_better_than_the_worst_era(self):
        self.assertLess(self.rep["eras"][-1]["inert_rate"],
                        self.rep["eras"][2]["inert_rate"])

    def test_carried_observations_are_not_a_cause(self):
        s = self.rep["specificity"]["carried"]
        self.assertLess(s["corrected_ratio"], 1.2)

    def test_refusals_never_appear_on_effective_rows(self):
        self.assertEqual(self.rep["specificity"]["refused"]["effective"], 0.0)

    def test_exclusive_layers_cover_every_inert_row(self):
        self.assertEqual(sum(self.rep["exclusive"]["counts"].values()), self.rep["inert"])


if __name__ == "__main__":
    unittest.main()
