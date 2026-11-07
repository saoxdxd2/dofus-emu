"""A tutorial dialog reply must name the NPC it answers.

Measured live 2026-11-06 (session fd24a682): the tutorial opened a dialog with NPC 3888, the
observation carried `dialog.npc_id = 3888`, and the planner sent `quest.advance {quest_id, reply: 0}`
without it. The page refused every one:

    quest is active but no npc_id was given; npcs on this map: 3976,3982,3918,3888

The loop then tried each of those NPCs in turn, found no action, and idled out. The schema already
declares `npc_id` optional on `quest.advance`, so this is the call being under-specified, not the
contract.

    python tools/test_tutorial_dialog_reply.py
"""
from __future__ import annotations

import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "dofus-network"))

from agent.planner import QuestPlanner  # noqa: E402


def obs(dialog, in_dialog=True):
    return {
        "tutorial": {"active": True, "quest_id": 3841, "step_id": 22},
        "in_dialog": in_dialog,
        "dialog": dialog,
        "current_objective": {"quest_id": "3841", "objective_id": 13541, "status": 2,
                              "type_id": 4},
        "quests_active": {"3841": 8},
        "map": {"map_id": 147064834, "npcs": [
            {"npc_id": 3888, "cell": 258}, {"npc_id": 3976, "cell": 100}]},
    }


class TutorialDialogReply(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.planner = QuestPlanner()

    def test_reply_names_the_npc_from_the_dialog(self):
        """The real shape the client sent: one reply, ids ready, text not."""
        call = self.planner._tutorial_call(
            obs({"open": True, "npc_id": 3888, "replies": [
                {"index": 0, "text": None, "reply_id": 41675}],
                 "ids_ready": True, "text_ready": False}), "do all the quests")
        self.assertIsNotNone(call, "the tutorial produced no call for an open dialog")
        self.assertEqual(call.tool, "quest.advance")
        self.assertEqual(call.args.get("npc_id"), 3888)
        self.assertEqual(call.args.get("reply"), 0)
        self.assertEqual(call.args.get("quest_id"), 3841)

    def test_reply_without_a_named_npc_still_plans(self):
        """A dialog that names nobody must not crash or invent an id."""
        call = self.planner._tutorial_call(
            obs({"open": True, "npc_id": None, "replies": [{"index": 0, "reply_id": 1}]}),
            "do all the quests")
        self.assertIsNotNone(call)
        self.assertNotIn("npc_id", call.args)

    def test_bag_step_equips_once_the_press_is_spent(self):
        """The bag step is not done when the window opens; it is done when the item is worn."""
        bag_obs = obs({"open": False}, in_dialog=False)
        bag_obs["ready_objectives"] = [{
            "quest_id": "3841", "objective_id": 13507, "type_id": 0,
            "type": "QuestObjectiveFreeForm",
            "text": "[!] Ouvrez votre inventaire depuis le menu."}]
        bag_obs["quest_steps"] = {"3841": 22}
        bag_obs["inventory"] = {"kamas": 0, "equipped": [], "presets": [],
                                "items": [{"gid": 18974, "qty": 1, "object_uid": 144784272},
                                          {"gid": 18844, "qty": 1, "object_uid": 144861732}]}
        calls = [self.planner._tutorial_call(bag_obs, "do all the quests") for _ in range(4)]
        tools = [c.tool for c in calls if c is not None]
        self.assertIn("ui.tap", tools, "the step's own press should be tried first")
        self.assertIn("inv.equip", tools, "a spent press with an unequipped item must equip")
        equip = next(c for c in calls if c is not None and c.tool == "inv.equip")
        self.assertIn(equip.args.get("object_uid"), (144784272, 144861732))

    def test_an_open_window_skips_its_own_dead_press(self):
        """The icon is disabled while its window is open, so the press cannot work.

        Measured live 2026-11-06: `ui.tap {menu: "Bag"}` with the bag open returned
        `changed: false` and the icon's class ending in `disabled`, every time. The action the
        step wants is inside the window, and the bag row now carries the slot it needs.
        """
        planner = QuestPlanner()
        bag_obs = obs({"open": False}, in_dialog=False)
        bag_obs["ready_objectives"] = [{
            "quest_id": "3841", "objective_id": 13507, "type_id": 0,
            "type": "QuestObjectiveFreeForm",
            "text": "[!] Ouvrez votre inventaire depuis le menu."}]
        bag_obs["quest_steps"] = {"3841": 4547}
        bag_obs["open_windows"] = ["equipment"]
        bag_obs["inventory"] = {"kamas": 0, "equipped": [], "presets": [], "items": [
            {"gid": 18974, "qty": 1, "object_uid": 158323185,
             "type_id": 210, "possible_positions": []},
            {"gid": 18844, "qty": 1, "object_uid": 158323186,
             "type_id": 7, "possible_positions": [1]}]}
        call = planner._tutorial_call(bag_obs, "do all the quests")
        self.assertIsNotNone(call)
        self.assertEqual(call.tool, "inv.equip",
                         "an open bag must be equipped, not re-pressed")
        self.assertEqual(call.args.get("object_uid"), 158323186,
                         "the quest object was chosen over the weapon")
        self.assertEqual(call.args.get("slot"), 1,
                         "the template's own possiblePositions names the slot")

    def test_an_open_window_with_nothing_to_wear_is_closed(self):
        """A dead button left pressed forever is what a stall looks like from outside."""
        planner = QuestPlanner()
        bag_obs = obs({"open": False}, in_dialog=False)
        bag_obs["ready_objectives"] = [{
            "quest_id": "3841", "objective_id": 13507, "type_id": 0,
            "type": "QuestObjectiveFreeForm",
            "text": "[!] Ouvrez votre inventaire depuis le menu."}]
        bag_obs["quest_steps"] = {"3841": 4547}
        bag_obs["open_windows"] = ["equipment"]
        bag_obs["inventory"] = {"kamas": 0, "equipped": [], "presets": [], "items": [
            {"gid": 18974, "qty": 1, "object_uid": 158323185,
             "type_id": 210, "possible_positions": []}]}
        # A quest object is offered before it is written off: the client's refusal ("not usable")
        # is the only equippability evidence there is, so it costs one step per item and then
        # the window is closed. Excluding it outright would have lost a wearable on a build that
        # publishes an empty position list for one.
        calls = [planner._tutorial_call(bag_obs, "do all the quests") for _ in range(3)]
        tools = [c.tool for c in calls if c is not None]
        self.assertIn("inv.equip", tools, "the quest object was never offered")
        self.assertIn("ui.tap", tools,
                      "with nothing left to wear the window must be closed, not re-pressed")
        close = next(c for c in calls if c is not None and c.tool == "ui.tap")
        self.assertEqual(close.args.get("client_target"), "close:equipment",
                         "the window must be closed so the press can open it again")

    def test_a_conversation_that_never_moves_the_objective_is_closed(self):
        """A dialog that answers "ok" forever is the stall, not the fix.

        Measured live 2026-11-07: eight steps of `quest.advance {reply: 0}`, each accepted with a
        fresh `reply_id`, none advancing the objective, while the quest wanted "equip the recruit
        set, then talk". The existing memo keys on the dialog *message*, so a conversation that
        shows a new page for every answer never trips it.
        """
        from agent import planner as pl

        original = pl.rank_dialog
        pl.rank_dialog = lambda _obs: {"status": "choose", "quest_id": 4260,
                                       "objective_id": 13902,
                                       "reply": {"index": 0, "text": "Give your humble thanks."}}
        try:
            planner = pl.QuestPlanner()
            page = {"open": True, "npc_id": 3888, "message": "page 0",
                    "replies": [{"index": 0, "reply_id": 1}], "reply_count": 1}
            calls = []
            for i in range(6):
                page["message"] = "page %d" % i   # a new page every time, which defeats the memo
                calls.append(planner._dialog_call(obs(page), "do all the quests"))
        finally:
            pl.rank_dialog = original

        close_at = next((i for i, c in enumerate(calls)
                         if c is not None and c.tool == "quest.advance"
                         and c.args.get("close")), None)
        self.assertIsNotNone(close_at, "the conversation was never closed")
        self.assertLessEqual(close_at, pl.DIALOG_STALL_LIMIT,
                             "more replies were sent than the stall bound allows")
        # And the counter resets, so the next conversation gets its own budget rather than
        # being closed on its first page.
        self.assertIsNotNone(calls[-1])
        self.assertFalse(calls[-1].args.get("close"),
                         "the bound did not reset after closing")

    def test_the_second_half_of_an_equip_then_talk_objective_is_reached(self):
        """One wasted equip, then the NPC -- not two.

        "Equip the Albueran recruit set, then talk to Ayin Spyr" is two objectives in one string.
        With the set worn, the only thing left in the bag is the tutorial's "Landing Debris", a
        quest object whose template declares no position: it cannot be worn, so one refusal is the
        whole answer and the step belongs to the talk half. Measured 2026-11-07: it was offered
        twice before the planner moved on.
        """
        planner = QuestPlanner()
        talk_obs = {
            "tutorial": {"active": True, "quest_id": 3841, "step_id": 13},
            "in_dialog": False, "dialog": {"open": False},
            "current_objective": {"quest_id": "4260", "objective_id": 13902, "status": 2,
                                  "type_id": 0,
                                  "text": "Equip the Albueran recruit set, then talk to Ayin Spyr."},
            "ready_objectives": [{"quest_id": "4260", "objective_id": 13902, "type_id": 0,
                                  "type": "QuestObjectiveFreeForm",
                                  "text": "Equip the Albueran recruit set, then talk to Ayin Spyr."}],
            "quest_steps": {"3841": 4974, "4260": 4936},
            "quests_active": {"3841": 13, "4260": 1},
            "map": {"map_id": 145490435,
                    "npcs": [{"npc_id": 3888, "cell": 258}, {"npc_id": 3976, "cell": 100}]},
            "cell": 200, "open_windows": [], "character": {"level": 3},
            "inventory": {"kamas": 0, "presets": [],
                          "equipped": [{"slot": 1, "gid": 18844}, {"slot": 6, "gid": 18846},
                                       {"slot": 7, "gid": 18848}],
                          "items": [{"gid": 18844, "qty": 1, "object_uid": 1},
                                    {"gid": 18846, "qty": 1, "object_uid": 2},
                                    {"gid": 18848, "qty": 1, "object_uid": 3},
                                    {"gid": 18974, "qty": 1, "object_uid": 4,
                                     "type_id": 210, "possible_positions": []}]},
        }
        first = planner.choose("do all the quests", talk_obs)
        self.assertIsNotNone(first)
        self.assertEqual(first.tool, "inv.equip")
        self.assertEqual(first.args.get("object_uid"), 4)
        second = planner.choose("do all the quests", talk_obs)
        self.assertIsNotNone(second)
        self.assertEqual(second.tool, "quest.advance",
                         "the quest object was offered more than once")
        self.assertEqual(second.args.get("npc_id"), 3888,
                         "the talk half must name the quest's NPC")

    def test_no_dialog_no_reply(self):
        """Outside a dialog the tutorial must not emit a reply call at all."""
        call = self.planner._tutorial_call(
            obs({"open": False}, in_dialog=False), "do all the quests")
        self.assertTrue(call is None or "reply" not in call.args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
