"""Unit and Integration Tests for Dofus Spatial World Graph, Resources, Quests, and NLP Engine.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "dofus-network"))

from navigation.world_graph import (
    CellPathfinder,
    Direction,
    DofusWorldGraph,
    MapCoord,
)
from navigation.resource_graph import ResourceKnowledgeGraph
from navigation.quest_graph import QuestKnowledgeGraph
from navigation.nlp_intent_engine import DofusNLPEngine, NLPIntent


class TestDofusWorldGraph(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = DofusWorldGraph()

    def test_direct_walk_adjacent_maps(self) -> None:
        path = self.graph.find_path((4, -19), (4, -18), allow_zaap=False)
        self.assertFalse(path.uses_zaap)
        self.assertEqual(path.total_steps, 1)
        self.assertEqual(path.steps[0].action, "WALK_DOWN")
        self.assertEqual(path.steps[0].to_coord, (4, -18))

    def test_zaap_shortcut_long_distance(self) -> None:
        # Distance from Astrub (4, -19) to Frigost (-77, -73) is huge (>100 maps)
        path = self.graph.find_path((4, -19), (-77, -72), allow_zaap=True)
        self.assertTrue(path.uses_zaap)
        self.assertIsNotNone(path.zaap_start_name)
        self.assertIsNotNone(path.zaap_target_name)
        self.assertTrue(any(s.action == "ZAAP_TELEPORT" for s in path.steps))

    def test_find_zaap_by_fuzzy_name(self) -> None:
        z1 = self.graph.find_zaap_by_name("astrub")
        self.assertIsNotNone(z1)
        self.assertEqual(z1.coord, MapCoord(4, -19))

        z2 = self.graph.find_zaap_by_name("bonta")
        self.assertIsNotNone(z2)
        self.assertEqual(z2.coord, MapCoord(-32, -56))

        z3 = self.graph.find_zaap_by_name("madrestam")
        self.assertIsNotNone(z3)
        self.assertEqual(z3.coord, MapCoord(7, -4))

    def test_cell_pathfinder_math_and_borders(self) -> None:
        # Cell 0 isometric coordinates
        x0, y0 = CellPathfinder.cell_to_iso_coords(0)
        self.assertIsInstance(x0, int)
        self.assertIsInstance(y0, int)

        # Distance between cell 0 and cell 28
        dist = CellPathfinder.cell_distance(0, 28)
        self.assertGreater(dist, 0)

        # Top border cells
        top_borders = CellPathfinder.get_border_cells_for_direction(Direction.UP)
        self.assertIn(0, top_borders)
        self.assertIn(13, top_borders)

        # Bottom border cells
        bottom_borders = CellPathfinder.get_border_cells_for_direction(Direction.DOWN)
        self.assertIn(559, bottom_borders)


class TestResourceKnowledgeGraph(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = ResourceKnowledgeGraph()

    def test_resource_trilingual_lookup(self) -> None:
        r_fr = self.graph.find_resource_by_name("fer")
        r_en = self.graph.find_resource_by_name("iron")
        r_es = self.graph.find_resource_by_name("hierro")
        self.assertIsNotNone(r_fr)
        self.assertEqual(r_fr.id, 312)
        self.assertEqual(r_en.id, 312)
        self.assertEqual(r_es.id, 312)

        # Peasant: Blé / Wheat / Trigo
        ble = self.graph.find_resource_by_name("blé")
        wheat = self.graph.find_resource_by_name("wheat")
        self.assertEqual(ble.id, 289)
        self.assertEqual(wheat.id, 289)

        # Lumberjack: Chêne / Oak
        chene = self.graph.find_resource_by_name("chêne")
        oak = self.graph.find_resource_by_name("oak wood")
        self.assertEqual(chene.id, 460)
        self.assertEqual(oak.id, 460)

    def test_search_dense_spots_near_player(self) -> None:
        spots = self.graph.search_spots("blé", near_pos=(4, -19), max_results=5)
        self.assertGreaterEqual(len(spots), 1)
        self.assertEqual(spots[0].resource.id, 289)
        self.assertIsNotNone(spots[0].distance_to_player)

    def test_generate_circular_farm_loop(self) -> None:
        loop = self.graph.generate_farm_loop("blé", start_coord=(4, -19), max_maps=4)
        self.assertIsNotNone(loop)
        self.assertEqual(loop.profession, "PEASANT")
        self.assertGreater(loop.total_nodes_est, 0)
        self.assertGreater(len(loop.cycle_steps), 0)


class TestQuestKnowledgeGraph(unittest.TestCase):
    def setUp(self) -> None:
        self.quests = QuestKnowledgeGraph()

    def test_builtin_cawotte_guide(self) -> None:
        guides = self.quests.search_guides("cawotte")
        self.assertGreaterEqual(len(guides), 1)
        g = guides[0]
        self.assertEqual(g.id, "dofus_cawotte")
        self.assertEqual(g.level_req, 50)
        self.assertIn("Panoplie du Wa Wabbit (Couronne, Cape, Bâton)", g.required_items)
        self.assertGreater(len(g.steps), 2)

    def test_island_access_guides(self) -> None:
        oto = self.quests.get_guide("acces_otomai")
        self.assertIsNotNone(oto)
        self.assertEqual(oto.start_npc, "Capitaine Kradoc")
        self.assertEqual(oto.start_coord, (15, 28))

        moon = self.quests.get_guide("acces_moon")
        self.assertIsNotNone(moon)
        self.assertEqual(moon.start_coord, (11, 10))


class TestDofusNLPEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.nlp = DofusNLPEngine()

    def test_coordinate_navigation_french(self) -> None:
        res = self.nlp.process_query("va en [4, -18]", current_pos=(4, -19))
        self.assertEqual(res.intent, NLPIntent.NAVIGATE_COORDS)
        self.assertEqual(res.detected_lang, "fr")
        self.assertIn("navigation_path", res.execution_payload)

    def test_coordinate_navigation_english(self) -> None:
        res = self.nlp.process_query("go to 15, 28", current_pos=(4, -19))
        self.assertEqual(res.intent, NLPIntent.NAVIGATE_COORDS)
        self.assertEqual(res.detected_lang, "en")

    def test_coordinate_navigation_spanish(self) -> None:
        res = self.nlp.process_query("ve a [-2, 0]", current_pos=(4, -19))
        self.assertEqual(res.intent, NLPIntent.NAVIGATE_COORDS)
        self.assertEqual(res.detected_lang, "es")

    def test_zaap_navigation(self) -> None:
        res = self.nlp.process_query("emmène-moi au zaap d'astrub", current_pos=(1, -15))
        self.assertEqual(res.intent, NLPIntent.NAVIGATE_ZAAP)
        self.assertIn("zaap", res.execution_payload)

    def test_resource_search_and_farm_loop(self) -> None:
        # Search spots
        res_find = self.nlp.process_query("où trouver du fer ?", current_pos=(4, -19))
        self.assertEqual(res_find.intent, NLPIntent.FIND_RESOURCE)
        self.assertIn("spots", res_find.execution_payload)

        # Farm loop
        res_loop = self.nlp.process_query("farm du blé autour d'astrub", current_pos=(4, -19))
        self.assertEqual(res_loop.intent, NLPIntent.START_FARM_LOOP)
        self.assertIn("farm_loop", res_loop.execution_payload)

    def test_level_requirement_query(self) -> None:
        res = self.nlp.process_query("quel niveau pour miner le kobalte ?")
        self.assertEqual(res.intent, NLPIntent.RESOURCE_LEVEL_REQ)
        self.assertEqual(res.execution_payload.get("level_req"), 50)
        self.assertEqual(res.execution_payload.get("profession"), "MINER")

    def test_quest_guide_advice(self) -> None:
        res = self.nlp.process_query("comment aller sur l'île d'otomaï ?")
        self.assertEqual(res.intent, NLPIntent.QUEST_GUIDE)
        self.assertIn("Capitaine Kradoc", res.message_reply)
        self.assertIn("guide", res.execution_payload)

    def test_executive_bot_orders(self) -> None:
        self.assertEqual(self.nlp.process_query("passe ton tour").intent, NLPIntent.COMBAT_ORDER)
        self.assertEqual(self.nlp.process_query("stop tout").intent, NLPIntent.BOT_CONTROL)
        self.assertEqual(self.nlp.process_query("suis-moi").intent, NLPIntent.BOT_CONTROL)


if __name__ == "__main__":
    unittest.main()
