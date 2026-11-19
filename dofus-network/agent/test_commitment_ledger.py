"""Pillar 2 gate: delayed, duplicated, reordered and colliding outcomes.

The live loop's old attribution was one step long. These tests inject the six
fault classes the commitment ledger exists for -- delay, duplication,
reordering, collision, missing answers and the ten-bounce retreat-return cycle
-- and assert the contract:

* no commitment is failed while ``t <= T_exp``;
* an event credits at most one commitment, at most once;
* an event that two commitments promised credits neither;
* a closed retreat-return cycle has zero net progress;
* only CREDITED/EXPIRED rows may teach (``w_learn``).

    python -m unittest agent.test_commitment_ledger
"""
from __future__ import annotations

import unittest

from agent.commitment_ledger import (
    AMBIGUOUS, CREDITED, EXPIRED, PENDING,
    CommitmentLedger, goal_potential,
)


def state(map_id=1, step=0, s_max=10, objectives=0, c_max=5, hops=None, **extra):
    out = {
        "map_id": map_id,
        "current_objective": {"quest_id": 3841, "step": step, "objective_id": 9},
        "objectives_completed": objectives,
        "S_max": s_max, "C_max": c_max,
    }
    if hops is not None:
        out["hops_to_target"] = hops
    out.update(extra)
    return out


def event(ledger, before, after, ts, verdict=None, payload=None, kind="observation"):
    return ledger.ingest(before, after, ts=ts, verdict=verdict,
                         payload=payload, kind=kind)


class DelayTests(unittest.TestCase):
    def test_credit_survives_a_three_step_delay(self):
        ledger = CommitmentLedger()
        c = ledger.register("nav.goto", {"map_id": 100}, now=0.0)
        before = state(map_id=1)
        for i, t in enumerate((1.0, 2.0, 3.0)):
            if i < 2:
                rep = event(ledger, state(map_id=1), state(map_id=1), t)
                self.assertEqual(rep.status, "unclaimed")
                self.assertEqual(c.sigma, PENDING)
            else:
                rep = event(ledger, before, state(map_id=100), t)
                self.assertEqual(rep.status, "credited")
                self.assertEqual(c.sigma, CREDITED)

    def test_no_failure_before_the_deadline(self):
        ledger = CommitmentLedger()
        c = ledger.register("nav.goto", {"map_id": 100}, now=0.0)
        ledger.advance(c.t_exp - 1e-6)
        self.assertEqual(c.sigma, PENDING, "expiry before T_exp is a premature no-op")
        ledger.advance(c.t_exp + 1e-6)
        self.assertEqual(c.sigma, EXPIRED)
        self.assertEqual(c.reward, -ledger.c_noop)

    def test_an_event_at_the_exact_deadline_still_counts(self):
        ledger = CommitmentLedger()
        c = ledger.register("nav.goto", {"map_id": 100}, now=0.0)
        rep = event(ledger, state(map_id=1), state(map_id=100), c.t_exp)
        self.assertEqual(rep.status, "credited")


class DuplicateTests(unittest.TestCase):
    def test_two_to_five_copies_of_one_event_credit_once(self):
        ledger = CommitmentLedger(dedup_s=1.0)
        ledger.register("nav.goto", {"map_id": 100}, now=0.0)
        before, after = state(map_id=1), state(map_id=100)
        first = event(ledger, before, after, 1.0)
        self.assertEqual(first.status, "credited")
        for copy in range(4):
            rep = event(ledger, before, after, 1.0 + 0.05 * copy)
            self.assertEqual(rep.status, "duplicate")
        credited = [c for c in ledger.history if c.sigma == CREDITED]
        self.assertEqual(len(credited), 1, "one outcome was credited twice")

    def test_different_events_in_the_same_second_are_not_duplicates(self):
        ledger = CommitmentLedger(dedup_s=1.0)
        ledger.register("nav.goto", {"map_id": 100}, now=0.0)
        ledger.register("nav.goto", {"map_id": 200}, now=0.0)
        a = event(ledger, state(map_id=1), state(map_id=100), 1.0)
        b = event(ledger, state(map_id=1), state(map_id=200), 1.0)
        self.assertEqual((a.status, b.status), ("credited", "credited"))


class ReorderTests(unittest.TestCase):
    def test_events_arriving_out_of_order_credit_the_right_commitment(self):
        ledger = CommitmentLedger()
        to_100 = ledger.register("nav.goto", {"map_id": 100}, now=10.0)
        to_200 = ledger.register("nav.goto", {"map_id": 200}, now=11.0)
        second = event(ledger, state(map_id=1), state(map_id=200), 12.0)
        first = event(ledger, state(map_id=1), state(map_id=100), 13.0)
        self.assertEqual(second.commitment_id, to_200.id)
        self.assertEqual(first.commitment_id, to_100.id)
        self.assertEqual((to_100.sigma, to_200.sigma), (CREDITED, CREDITED))

    def test_an_earlier_event_cannot_credit_a_later_commitment(self):
        ledger = CommitmentLedger()
        later = ledger.register("nav.goto", {"map_id": 100}, now=10.0)
        rep = event(ledger, state(map_id=1), state(map_id=100), 5.0)
        self.assertEqual(rep.status, "unclaimed")
        self.assertEqual(later.sigma, PENDING)


class CollisionTests(unittest.TestCase):
    def test_two_candidates_make_every_candidate_ambiguous(self):
        ledger = CommitmentLedger()
        a = ledger.register("nav.goto", {"map_id": 100}, now=10.0)
        b = ledger.register("nav.goto", {"map_id": 100}, now=11.0)
        rep = event(ledger, state(map_id=1), state(map_id=100), 12.0)
        self.assertEqual(rep.status, "ambiguous")
        self.assertEqual((a.sigma, b.sigma), (AMBIGUOUS, AMBIGUOUS))
        self.assertEqual(ledger.teaching_rows(), [],
                         "an ambiguous outcome must not teach")

    def test_an_unpromised_event_is_not_cross_attributed(self):
        ledger = CommitmentLedger()
        c = ledger.register("nav.goto", {"map_id": 100}, now=10.0)
        rep = event(ledger, state(map_id=1), state(map_id=200), 11.0)
        self.assertEqual(rep.status, "unclaimed")
        self.assertEqual(c.sigma, PENDING)


class MissingAnswerTests(unittest.TestCase):
    def test_a_missing_answer_expires_as_a_real_negative(self):
        ledger = CommitmentLedger()
        c = ledger.register("quest.advance", {"quest_id": 3841}, now=0.0)
        ledger.advance(c.t_exp + 1.0)
        self.assertEqual(c.sigma, EXPIRED)
        self.assertTrue(c.w_learn)
        self.assertLess(c.reward, 0.0)

    def test_an_expired_commitment_cannot_be_credited_later(self):
        ledger = CommitmentLedger()
        c = ledger.register("nav.goto", {"map_id": 100}, now=0.0)
        ledger.advance(c.t_exp + 1.0)
        rep = event(ledger, state(map_id=1), state(map_id=100), c.t_exp + 2.0)
        self.assertEqual(rep.status, "unclaimed")
        self.assertEqual(c.sigma, EXPIRED)


class RetreatReturnTests(unittest.TestCase):
    def test_ten_map_bounces_earn_zero_progress(self):
        ledger = CommitmentLedger()
        before = state(map_id=1)
        for index in range(10):
            ledger.register("nav.goto", {"map_id": 100}, now=0.0)
            # The crossing was refused: the character is still on map 1 when the
            # poll answers. Each bounce is its own event (distinct ts), and
            # `promise_state` says "unmet", so no credit exists.
            rep = event(ledger, before, state(map_id=1), 5.0 + 2.0 * index)
            self.assertEqual(rep.status, "unclaimed")
        self.assertEqual([c for c in ledger.history if c.sigma == CREDITED], [])
        self.assertLessEqual(sum(c.reward or 0.0 for c in ledger.history), 0.0,
                             "a bouncing cycle must never earn progress")
        ledger.advance(10_000.0)
        expired = [c for c in ledger.history if c.sigma == EXPIRED]
        self.assertEqual(len(expired), 10,
                         "every bounce ends as an explicit no-op, not silence")
        self.assertTrue(all(c.reward == -ledger.c_noop for c in expired),
                        "an unanswered bounce is a no-op negative, never progress")

    def test_a_closed_cycle_has_zero_telescoping_potential(self):
        ledger = CommitmentLedger()
        start = dict(state(map_id=1, step=3, objectives=2, hops=4),
                     inventory_size=1)
        commitment = ledger.register("inv.equip", {"item_id": 77},
                                     state=start, now=0.0)
        # The character returned to the same macro position; only a micro
        # inventory fact moved, so Phi is identical on both sides.
        back = dict(state(map_id=1, step=3, objectives=2, hops=4),
                    inventory_size=2)
        rep = event(ledger, start, back, 5.0)
        self.assertEqual(rep.status, "credited")
        self.assertAlmostEqual(commitment.reward, 0.0, places=9,
                               msg="Phi(resolve) must cancel Phi(dispatch)")

    def test_potential_is_signed_and_bounded(self):
        worst = goal_potential(state(step=0, objectives=0, hops=10, s_max=10,
                                     c_max=5), {"H_max": 10})
        best = goal_potential(state(step=10, objectives=5, hops=0, s_max=10,
                                    c_max=5), {"H_max": 10})
        self.assertAlmostEqual(worst, -0.2, places=6)
        self.assertAlmostEqual(best, 0.8, places=6)
        self.assertLessEqual(best - worst, 2.0)


class LedgerDisciplineTests(unittest.TestCase):
    def test_the_ledger_is_bounded_and_evicts_the_nearest_deadline(self):
        ledger = CommitmentLedger(max_items=8)
        for i in range(9):
            ledger.register("nav.goto", {"map_id": i}, now=float(i))
        self.assertEqual(len(ledger.items), 8)
        self.assertEqual(ledger.evicted, 1)
        self.assertEqual(ledger.history[-1].sigma, EXPIRED)
        self.assertTrue(ledger.history[-1].w_learn)

    def test_only_final_states_teach(self):
        ledger = CommitmentLedger()
        ledger.register("nav.goto", {"map_id": 100}, now=0.0)
        ledger.register("nav.goto", {"map_id": 100}, now=1.0)
        event(ledger, state(map_id=1), state(map_id=100), 2.0)
        self.assertEqual(ledger.teaching_rows(), [])
        self.assertEqual(ledger.stats()["by_sigma"].get(AMBIGUOUS), 2)

    def test_stats_name_what_happened(self):
        ledger = CommitmentLedger()
        ledger.register("nav.goto", {"map_id": 100}, now=0.0)
        event(ledger, state(map_id=1), state(map_id=1), 1.0)
        event(ledger, state(map_id=1), state(map_id=100), 2.0)
        stats = ledger.stats()
        self.assertEqual(stats["registered"], 1)
        self.assertEqual(stats["by_sigma"].get(CREDITED), 1)
        self.assertEqual(stats["unclaimed"], 1)


if __name__ == "__main__":
    unittest.main()
