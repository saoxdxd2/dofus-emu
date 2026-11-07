"""The objective ledger: confirmation at the level of the objective, not the world.

The session this comes from (`KNOWN_GAPS.md` G98) waited 306 passes on one tutorial objective while
the bag held two items and nothing was equipped. The loop's per-call counters could not say "I tried
that, it did not move this objective, so that belief is wrong"; its give-up trigger was a wait count.
These checks pin the replacement: a hypothesis is refuted by evidence, the next one is generated from
the observation, and a blocked objective is re-opened when the world changes rather than retired.

    python tools/test_objective_ledger.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "dofus-network"))

from agent.objective_ledger import ObjectiveLedger  # noqa: E402


def stuck_obs(**over):
    """The shape the client actually sent at 21:04, trimmed to what the ledger reads."""
    obs = {
        "in_world": True,
        "character": {"level": 2},
        "map": {"map_id": 147064834,
                "npcs": [{"npc_id": 3978, "cell": 248}, {"npc_id": 3888, "cell": 258}]},
        "ready_objectives": [{
            "quest_id": "3841", "objective_id": 13507, "type_id": 0,
            "type": "QuestObjectiveFreeForm",
            "text": "[!] Ouvrez votre inventaire depuis le menu."}],
        "current_objective": {"quest_id": "3841", "objective_id": 13507, "status": 2},
        "objectives_completed": 8,
        "inventory": {"kamas": 0, "equipped": [], "presets": [],
                      "items": [{"gid": 18974, "qty": 1, "object_uid": 144784272},
                                {"gid": 18844, "qty": 1, "object_uid": 144861732}]},
        "dialog": {"open": False},
    }
    obs.update(over)
    return obs


def is_claim(hyp) -> bool:
    """The claim form of `quest.advance`: it names an objective, not an NPC.

    Both are the same tool, so `hyp.tool` cannot tell them apart -- which is exactly the mistake
    the autopilot's attribution guard had to fix too.
    """
    return bool(hyp is not None and hyp.tool == "quest.advance"
                and "objective_id" in (hyp.args or {}))


class ObjectiveLedgerChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "ledger.json"
        self.ledger = ObjectiveLedger(self.path, instance="dofus-04")
        self.obs = stuck_obs()

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_claim_is_offered_until_the_page_refuses_it(self):
        """Nothing is asserted about objective types; one refusal teaches the type.

        The page answers a claim with "objective 13507 (QuestObjectiveFreeForm) is completed by
        the server; perform the action instead of claiming it". That sentence names the type, so
        it is evidence about every objective of that type -- and it is kept, instead of Python
        carrying `type_id == 0` as a rule.
        """
        rec = self.ledger.sync(self.obs)
        self.assertEqual(rec.status, "ready")
        first = self.ledger.next_hypothesis(rec, self.obs)
        self.assertIsNotNone(first)
        self.assertTrue(is_claim(first),
                        "a fresh ledger must try the claim before believing anything")
        self.assertEqual(self.ledger.server_counted_types, set())

        self.ledger.record(rec, first, self.obs, self.obs, verdict="failed",
                           reason="objective 13507 (QuestObjectiveFreeForm) is completed by "
                                  "the server; perform the action instead of claiming it")
        self.assertEqual(self.ledger.server_counted_types, {0},
                         "the refusal named the type and was not recorded")

        # The same objective: the claim is not offered again.
        claims = []
        for _ in range(3):
            hyp = self.ledger.next_hypothesis(rec, self.obs)
            self.assertIsNotNone(hyp)
            claims.append(is_claim(hyp))
            hyp.tries = 99
        self.assertNotIn(True, claims, "the refused claim came back")

    def test_the_learned_type_covers_objectives_it_never_saw(self):
        """One refusal generalises to the type, which is the whole point of learning it."""
        rec = self.ledger.sync(self.obs)
        claim = self.ledger.next_hypothesis(rec, self.obs)
        self.ledger.record(rec, claim, self.obs, self.obs, verdict="failed",
                           reason="objective 13507 (QuestObjectiveFreeForm) is completed by "
                                  "the server")

        unseen = stuck_obs()
        unseen["ready_objectives"][0].update({"objective_id": 99999, "text": "Parlez a Ayin."})
        unseen["current_objective"].update({"objective_id": 99999})
        other = self.ledger.sync(unseen)
        hyp = self.ledger.next_hypothesis(other, unseen)
        self.assertIsNotNone(hyp)
        self.assertFalse(is_claim(hyp),
                         "a type the page refused was claimed again on a new objective")

    def test_a_different_refusal_teaches_nothing(self):
        """Only the server-counted sentence is evidence for this fact."""
        rec = self.ledger.sync(self.obs)
        claim = self.ledger.next_hypothesis(rec, self.obs)
        self.ledger.record(rec, claim, self.obs, self.obs, verdict="failed",
                           reason="window grimoire is not open")
        self.assertEqual(self.ledger.server_counted_types, set(),
                         "an unrelated refusal was read as a type fact")

    def test_the_learned_type_survives_a_restart(self):
        rec = self.ledger.sync(self.obs)
        claim = self.ledger.next_hypothesis(rec, self.obs)
        self.ledger.record(rec, claim, self.obs, self.obs, verdict="failed",
                           reason="objective 13507 is completed by the server")
        self.ledger.save()
        again = ObjectiveLedger(self.path, instance="dofus-04")
        self.assertEqual(again.server_counted_types, {0},
                         "the fact was not persisted")

    def test_an_objective_of_another_type_is_still_claimed(self):
        """The rule is about what the page refused, not about claims in general."""
        obs = stuck_obs()
        obs["ready_objectives"][0]["type_id"] = 4
        obs["current_objective"]["type_id"] = 4
        rec = self.ledger.sync(obs)
        hyp = self.ledger.next_hypothesis(rec, obs)
        self.assertIsNotNone(hyp)
        self.assertEqual(hyp.tool, "quest.advance")
        self.assertIn("objective_id", hyp.args)

    def test_confirmation_is_about_the_objective_not_the_world(self):
        """A world change that leaves the objective alone is not progress."""
        rec = self.ledger.sync(self.obs)
        moved_map = dict(self.obs, map={"map_id": 999, "npcs": []})
        self.assertFalse(self.ledger.confirm(rec, self.obs, moved_map))
        closed = dict(self.obs, ready_objectives=[], current_objective={},
                      objectives_completed=9)
        self.assertTrue(self.ledger.confirm(rec, self.obs, closed))
        self.assertEqual(rec.status, "closed")

    def test_blocked_is_not_retired_and_reopens_on_a_new_item(self):
        """Every hypothesis refuted is a block with evidence, not a quest skipped for the run."""
        rec = self.ledger.sync(self.obs)
        for _ in range(40):
            hyp = self.ledger.next_hypothesis(rec, self.obs)
            if hyp is None:
                break
            self.ledger.record(rec, hyp, self.obs, self.obs, verdict="noop")
        self.assertEqual(rec.status, "blocked")
        self.assertIn("refuted", rec.block_reason)
        grown = stuck_obs()
        grown["inventory"]["items"].append({"gid": 18900, "qty": 1, "object_uid": 144999999})
        again = self.ledger.sync(grown)
        self.assertNotEqual(again.status, "blocked", "a new item must reopen the objective")

    def test_belief_survives_a_restart(self):
        rec = self.ledger.sync(self.obs)
        hyp = self.ledger.next_hypothesis(rec, self.obs)
        self.ledger.record(rec, hyp, self.obs, self.obs, verdict="noop")
        self.ledger.save()
        again = ObjectiveLedger(self.path, instance="dofus-04")
        rec2 = again.sync(self.obs)
        self.assertEqual(rec2.attempts, 1)
        self.assertEqual(rec2.hypotheses[0].tries, 1)
        self.assertEqual(rec2.hypotheses[0].verdicts, ["noop"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
