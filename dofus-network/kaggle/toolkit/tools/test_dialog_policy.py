"""Unit tests for dialog_policy ranking and dynamic elemental build selection."""

import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.dialog_policy import rank_dialog, _character_element, _reply_element


class TestDialogPolicy(unittest.TestCase):
    def test_reply_element_detection(self):
        self.assertEqual(_reply_element("Voie de la Terre"), "earth")
        self.assertEqual(_reply_element("Choisir la Force"), "earth")
        self.assertEqual(_reply_element("Path of Earth"), "earth")
        self.assertEqual(_reply_element("Voie du Feu"), "fire")
        self.assertEqual(_reply_element("Choisir l'Intelligence"), "fire")
        self.assertEqual(_reply_element("Voie de l'Eau"), "water")
        self.assertEqual(_reply_element("Choisir la Chance"), "water")
        self.assertEqual(_reply_element("Voie de l'Air"), "air")
        self.assertEqual(_reply_element("Choisir l'Agilité"), "air")
        self.assertIsNone(_reply_element("Continuer la conversation"))

    def test_character_element_dynamic_detection(self):
        # Earth dominant
        obs_earth = {
            "character": {
                "stats": {"strength": 80, "intelligence": 0, "chance": 10, "agility": 0}
            }
        }
        self.assertEqual(_character_element(obs_earth), "earth")

        # Fire dominant
        obs_fire = {
            "character": {
                "stats": {"strength": 0, "intelligence": 60, "chance": 0, "agility": 0}
            }
        }
        self.assertEqual(_character_element(obs_fire), "fire")

        # Water dominant
        obs_water = {
            "character": {
                "stats": {"strength": 0, "intelligence": 0, "chance": 50, "agility": 0}
            }
        }
        self.assertEqual(_character_element(obs_water), "water")

        # Air dominant
        obs_air = {
            "character": {
                "stats": {"strength": 0, "intelligence": 0, "chance": 0, "agility": 75}
            }
        }
        self.assertEqual(_character_element(obs_air), "air")

        # Direct element field
        obs_annotated = {"character": {"element": "water"}}
        self.assertEqual(_character_element(obs_annotated), "water")

        # Top-level stats
        obs_top_stats = {"stats": {"intelligence": 100}}
        self.assertEqual(_character_element(obs_top_stats), "fire")

        # Breed fallback
        obs_breed_iop = {"character": {"breed": "Iop"}}
        self.assertEqual(_character_element(obs_breed_iop), "earth")
        obs_breed_eni = {"character": {"breed": "Eniripsa"}}
        self.assertEqual(_character_element(obs_breed_eni), "fire")

    def test_elemental_build_choice_ranking(self):
        # NPC presents 4 elemental choices
        replies = [
            {"index": 0, "text": "Voie de la Terre"},
            {"index": 1, "text": "Voie du Feu"},
            {"index": 2, "text": "Voie de l'Eau"},
            {"index": 3, "text": "Voie de l'Air"},
        ]
        dialog_base = {
            "open": True,
            "text_ready": True,
            "ids_ready": True,
            "replies": replies,
            "message": "Quelle voie souhaitez-vous emprunter ?",
        }

        # Case 1: Character is Earth / Strength
        obs_earth = {
            "dialog": dialog_base,
            "character": {
                "stats": {"strength": 50, "intelligence": 0, "chance": 0, "agility": 0}
            },
        }
        dec_earth = rank_dialog(obs_earth)
        self.assertIsNotNone(dec_earth)
        self.assertEqual(dec_earth["status"], "choose")
        self.assertEqual(dec_earth["reply"]["index"], 0)
        self.assertIn("earth", dec_earth["reason"])

        # Case 2: Character is Fire / Intelligence
        obs_fire = {
            "dialog": dialog_base,
            "character": {
                "stats": {"strength": 0, "intelligence": 70, "chance": 0, "agility": 0}
            },
        }
        dec_fire = rank_dialog(obs_fire)
        self.assertIsNotNone(dec_fire)
        self.assertEqual(dec_fire["status"], "choose")
        self.assertEqual(dec_fire["reply"]["index"], 1)
        self.assertIn("fire", dec_fire["reason"])

        # Case 3: Character is Water / Chance
        obs_water = {
            "dialog": dialog_base,
            "character": {
                "stats": {"strength": 0, "intelligence": 0, "chance": 40, "agility": 0}
            },
        }
        dec_water = rank_dialog(obs_water)
        self.assertIsNotNone(dec_water)
        self.assertEqual(dec_water["status"], "choose")
        self.assertEqual(dec_water["reply"]["index"], 2)
        self.assertIn("water", dec_water["reason"])

        # Case 4: Character is Air / Agility
        obs_air = {
            "dialog": dialog_base,
            "character": {
                "stats": {"strength": 0, "intelligence": 0, "chance": 0, "agility": 30}
            },
        }
        dec_air = rank_dialog(obs_air)
        self.assertIsNotNone(dec_air)
        self.assertEqual(dec_air["status"], "choose")
        self.assertEqual(dec_air["reply"]["index"], 3)
        self.assertIn("air", dec_air["reason"])

    def test_open_ended_tie_never_abstains(self):
        # Two non-elemental tied replies should choose index 0 instead of abstaining
        replies = [
            {"index": 0, "text": "Demander des informations complémentaires."},
            {"index": 1, "text": "Poursuivre la discussion."},
        ]
        obs = {
            "dialog": {
                "open": True,
                "text_ready": True,
                "ids_ready": True,
                "replies": replies,
                "message": "Que puis-je faire pour vous ?",
            }
        }
        dec = rank_dialog(obs)
        self.assertIsNotNone(dec)
        self.assertEqual(dec["status"], "choose")
        self.assertEqual(dec["reply"]["index"], 0)
        self.assertEqual(dec["reason"], "first open-ended reply in tie")

    def test_planner_dialog_fallback_never_closes_when_replies_exist(self):
        from agent.planner import QuestPlanner
        planner = QuestPlanner()

        # Dialog has replies, but status was somehow wait/abstain
        dialog = {
            "open": True,
            "text_ready": False,  # triggers status wait in rank_dialog
            "ids_ready": True,
            "replies": [{"index": 0, "text": "Voie de la Terre"}],
            "npc_id": 1234,
            "message": "Bienvenue",
            "reply_count": 1,
        }
        planner._dialog_key = (dialog.get("npc_id"), dialog.get("message"))
        planner._dialog_waits = 5  # next wait makes it 6
        obs = {"dialog": dialog, "map_id": 100}
        call = planner._dialog_call(obs, goal="quest")
        self.assertIsNotNone(call)
        self.assertEqual(call.tool, "quest.advance")
        # Must answer the reply rather than closing!
        self.assertEqual(call.args.get("reply_index"), 0)
        self.assertNotIn("close", call.args)

    def test_planner_dialog_closes_when_definitive_empty(self):
        from agent.planner import QuestPlanner
        planner = QuestPlanner()

        # Definitive empty dialog (0 replies and text is present)
        dialog = {
            "open": True,
            "text_ready": True,
            "ids_ready": True,
            "text_present": True,
            "replies": [],
            "npc_id": 1234,
            "message": "Je n'ai rien pour toi.",
            "reply_count": 0,
        }
        planner._dialog_key = (dialog.get("npc_id"), dialog.get("message"))
        planner._dialog_waits = 1  # next wait makes it 2
        obs = {"dialog": dialog, "map_id": 100}
        call = planner._dialog_call(obs, goal="quest")
        self.assertIsNotNone(call)
        self.assertEqual(call.tool, "quest.advance")
        self.assertTrue(call.args.get("close"))


if __name__ == "__main__":
    unittest.main()
