"""`plan_candidates` must spend its budget across buckets, not on the top scores.

A flat `plans[:k]` returns the k highest-scoring plans. On a board where a
direct hit exists -- which is most boards -- the score is a direct hit, so the
menu becomes four ways to hit the same monster and a setup turn is never
offered. The ranker downstream cannot pick a plan it was never given.

These tests drive the selection directly with a synthetic candidate list,
because the *search* in this repository genuinely cannot produce a trap or a
summon (see `_templates`: a spell with no damage, no heal and no planned
mechanic is dropped, and the encounter table's only effect kinds are damage and
heal). That is a separate gap from this one. What is pinned here is the budget
policy: when the buckets are there, they survive the slice.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from combat.battle_solver import (BUCKET_ORDER, BattleSolver, PlannedAction,
                                  TurnPlan)  # noqa: E402


def _plan(score: float, bucket: str, tag: str, spell_id: int) -> TurnPlan:
    plan = TurnPlan(
        actions=[PlannedAction(kind="cast", move_to=None, spell_id=spell_id,
                               target_id=1, moved=0, moved_target=None)],
        final_cell=100, remaining_ap=0, remaining_mp=0.0,
        expected_damage=score, expected_healing=0.0, kills=[], score=score,
        notes=[tag])
    # The buckets are supplied rather than classified: this file tests the
    # budget policy, not `_classify_plan`.
    plan.bucket = bucket
    return plan


def _solver() -> BattleSolver:
    solver = BattleSolver.__new__(BattleSolver)
    solver.last_buckets = {}
    solver._classify_plan = lambda plan, board: plan.bucket
    return solver


def _diverse():
    return [
        _plan(100, "damage", "hit1", 1),
        _plan(99, "damage", "hit2", 2),
        _plan(98, "damage", "hit3", 3),
        _plan(97, "damage", "hit4", 4),
        _plan(40, "setup", "trap", 5),
        _plan(30, "displacement", "push", 6),
        _plan(10, "boss", "bossbreak", 7),
    ]


class BudgetPolicyTests(unittest.TestCase):

    def test_every_bucket_survives_the_budget(self):
        chosen = _solver()._select_by_bucket(_diverse(), board=None, budget=5)
        tags = [plan.notes[0] for plan in chosen]
        self.assertEqual(tags, ["hit1", "hit2", "trap", "push", "bossbreak"])

    def test_the_flat_slice_is_what_this_replaces(self):
        """The failure being fixed, asserted so the difference stays visible."""
        plans = _diverse()
        flat = sorted(plans, key=lambda plan: plan.score, reverse=True)[:5]
        self.assertEqual([plan.notes[0] for plan in flat],
                         ["hit1", "hit2", "hit3", "hit4", "trap"],
                         "the fixture no longer demonstrates the collapse")
        self.assertNotIn("push", [plan.notes[0] for plan in flat])

    def test_the_best_plan_is_still_first(self):
        """The menu changes; the default answer does not."""
        chosen = _solver()._select_by_bucket(_diverse(), board=None, budget=5)
        self.assertEqual(chosen[0].notes[0], "hit1")
        self.assertGreaterEqual(chosen[0].score, chosen[1].score)

    def test_a_small_budget_still_takes_a_setup_turn(self):
        chosen = _solver()._select_by_bucket(_diverse(), board=None, budget=2)
        self.assertIn("trap", [plan.notes[0] for plan in chosen])

    def test_the_result_is_sorted_by_score(self):
        chosen = _solver()._select_by_bucket(_diverse(), board=None, budget=5)
        scores = [plan.score for plan in chosen]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_two_rounds_before_duplicating_a_bucket(self):
        """With budget for all four buckets, no bucket may take two slots."""
        chosen = _solver()._select_by_bucket(_diverse(), board=None, budget=4)
        buckets = [plan.bucket for plan in chosen]
        self.assertEqual(len(buckets), len(set(buckets)),
                         "a bucket took a second slot before a bucket had none: %r"
                         % (buckets,))

    def test_identical_plans_are_not_duplicated(self):
        """Two plans with the same actions are one option, not two."""
        plans = [_plan(50, "damage", "a", 9), _plan(49, "damage", "a", 9),
                 _plan(10, "setup", "s", 8)]
        chosen = _solver()._select_by_bucket(plans, board=None, budget=3)
        self.assertEqual(len(chosen), 2)

    def test_an_empty_or_zero_budget_returns_nothing(self):
        solver = _solver()
        self.assertEqual(solver._select_by_bucket(_diverse(), board=None,
                                                  budget=0), [])
        self.assertEqual(solver._select_by_bucket([], board=None, budget=5), [])

    def test_last_buckets_reports_what_was_chosen(self):
        solver = _solver()
        solver._select_by_bucket(_diverse(), board=None, budget=5)
        self.assertEqual(solver.last_buckets,
                         {"damage": 2, "setup": 1, "displacement": 1, "boss": 1})

    def test_the_order_is_a_module_constant(self):
        self.assertEqual(BUCKET_ORDER[0], "damage")
        self.assertIn("setup", BUCKET_ORDER)


class RealSolverTests(unittest.TestCase):
    """The real search on shipped data, judged only on what it can produce."""

    @classmethod
    def setUpClass(cls):
        import random

        from combat import duel_value as dv
        from combat.duel import DEFAULT_DATA, make_duel, plans_for
        from combat.duel_benchmark import load_data, sample_pair
        from combat.normal_build_duel import DEFAULT_MAP

        cells = dv._load_cells(DEFAULT_MAP)
        data, levels = load_data(DEFAULT_DATA)
        rng = random.Random(3)
        solver = BattleSolver(beam_width=16, max_actions=4)
        cls.samples = []
        for _ in range(20):
            row_a, row_b = sample_pair(levels, list(range(4096)), rng)
            state = make_duel(data, row_a, row_b, cells, rng)
            if state is None or state.done:
                continue
            plans = plans_for(state, solver, 6)
            if plans:
                cls.samples.append(plans)

    def test_candidates_are_still_produced_and_best_first(self):
        if not self.samples:
            self.skipTest("no board could be generated")
        for plans in self.samples:
            self.assertLessEqual(len(plans), 6)
            scores = [plan.score for plan in plans]
            self.assertEqual(scores, sorted(scores, reverse=True))

    def test_there_is_at_most_one_pass_plan(self):
        for plans in self.samples:
            passes = [plan for plan in plans
                      if plan.actions and plan.actions[0].kind == "pass"]
            self.assertLessEqual(len(passes), 1)

    def test_no_plan_appears_twice(self):
        for plans in self.samples:
            signatures = [tuple((a.kind, a.move_to, a.spell_id, a.target_id)
                                for a in plan.actions) for plan in plans]
            self.assertEqual(len(signatures), len(set(signatures)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
