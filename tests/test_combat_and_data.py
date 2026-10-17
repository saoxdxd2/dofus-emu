"""Comprehensive Test Suite for Dofus Touch Game Data & Combat Calculation Engine.

Tests:
1. Trilingual Game Data Repository (FR, EN, ES)
2. Mathematical Damage Calculation Benchmark Precision
3. Spell Rotation Optimizer & Guaranteed Lethal AP Conservation
4. Central Singleton AI Sentinel HTTP Endpoints
"""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "dofus-network"))

from data.game_data_extractor import DamageEffect, GameDataRepository, MonsterGrade, MonsterInfo, SpellInfo
from combat.damage_calculator import (
    CharacterStats,
    DofusDamageCalculator,
    OptimalRotationResult,
    SpellDamageResult,
    SpellRotationOptimizer,
    TargetStats,
)
from combat.tactical_engine import TacticalCombatEngine, TacticalStance
from networkhealth.sentinel import SentinelHTTPHandler, ThreadedHTTPServer


class TestGameDataAndCombat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = GameDataRepository.get_instance()

    def test_01_game_data_loaded(self):
        self.assertGreater(len(self.repo.spells_by_id), 20)
        self.assertGreater(len(self.repo.monsters_by_id), 5)

    def test_02_trilingual_monster_lookup(self):
        # French
        mob_fr = self.repo.find_monster("Chef de Guerre Bouftou")
        self.assertIsNotNone(mob_fr)
        self.assertIn(mob_fr.id, [102, 148])

        # English
        mob_en = self.repo.find_monster("Gobball War Chief")
        self.assertIsNotNone(mob_en)
        self.assertEqual(mob_en.id, mob_fr.id)

        # Spanish
        mob_es = self.repo.find_monster("Jefe de Guerra Jalato")
        self.assertIsNotNone(mob_es)
        self.assertEqual(mob_es.id, mob_fr.id)

    def test_03_trilingual_spell_lookup(self):
        # FR: Épée d'Iop
        sp_fr = self.repo.find_spell("Epee d'Iop")
        self.assertIsNotNone(sp_fr)
        self.assertEqual(sp_fr.id, 170)

        # EN: Iop's Sword
        sp_en = self.repo.find_spell("Iop's Sword")
        self.assertIsNotNone(sp_en)
        self.assertEqual(sp_en.id, 170)

        # ES: Espada de Yopuka
        sp_es = self.repo.find_spell("Espada de Yopuka")
        self.assertIsNotNone(sp_es)
        self.assertEqual(sp_es.id, 170)

    def test_04_damage_calculation_benchmark(self):
        """Official benchmark:
        400 Strength, 50 Power, 20 Flat Earth Damage
        Pressure: 14 to 18 base roll
        Target: 20% Earth res, 0 fixed res
        Expected min: floor(14 * 5.5) + 20 = 97 -> floor(97 * 0.80) = 77
        Expected max: floor(18 * 5.5) + 20 = 119 -> floor(119 * 0.80) = 95
        """
        pressure = self.repo.find_spell("Pression")
        self.assertIsNotNone(pressure)

        char = CharacterStats(strength=400, power=50, damage_earth=20)
        target = TargetStats(name="Dummy", max_hp=500, current_hp=500, res_percent_earth=20)

        res = DofusDamageCalculator.calculate_spell_damage(pressure, char, target)
        self.assertEqual(res.total_min, 77)
        self.assertEqual(res.total_max, 95)
        self.assertEqual(res.total_avg, 86.0)
        self.assertGreater(res.crit_total_min, res.total_min)
        self.assertGreater(res.expected_value, res.total_min)

    def test_05_rotation_optimizer_lethal_ap_saving(self):
        """Verify that when a target can be killed with lower AP,
        the optimizer saves the remaining AP for movement or defense.
        """
        pression = self.repo.find_spell("Pression")          # 3 AP
        concentration = self.repo.find_spell("Concentration")  # 2 AP
        epee_iop = self.repo.find_spell("Epee d'Iop")        # 4 AP
        spells = [pression, concentration, epee_iop]

        # Target has 160 HP
        mob = self.repo.find_monster("Chef de Guerre Bouftou")
        target = TargetStats.from_monster_grade(mob, 1)  # 160 HP, 20% earth res

        char = CharacterStats(action_points=10, strength=400, power=50, damage_earth=20)
        opt = SpellRotationOptimizer.optimize_turn(char, spells, target)

        self.assertTrue(opt.is_guaranteed_lethal)
        self.assertTrue(opt.is_expected_lethal)
        self.assertEqual(opt.estimated_turns_to_kill, 1)
        # Should spend only 4 AP (2x Concentration = ~208 dmg) rather than all 10 AP!
        self.assertLessEqual(opt.total_ap_spent, 6)
        self.assertGreater(opt.ap_remaining, 0)

    def test_06_rotation_optimizer_max_damage_poutch(self):
        """Against high HP target (10k), maximize total damage within AP budget."""
        pression = self.repo.find_spell("Pression")          # 3 AP
        concentration = self.repo.find_spell("Concentration")  # 2 AP
        epee_iop = self.repo.find_spell("Epee d'Iop")        # 4 AP
        spells = [pression, concentration, epee_iop]

        poutch = self.repo.find_monster("Poutch Ingball")
        target = TargetStats.from_monster_grade(poutch, 5)

        char = CharacterStats(action_points=10, strength=400, power=50, damage_earth=20)
        opt = SpellRotationOptimizer.optimize_turn(char, spells, target)

        self.assertFalse(opt.is_guaranteed_lethal)
        # 10 AP budget -> e.g. Concentration (2) + Concentration (2) + Epee (4) + Concentration (2) = 10 AP
        self.assertGreaterEqual(opt.total_ap_spent, 8)
        self.assertGreater(opt.total_expected_damage, 400.0)

    def test_07_tactical_buff_and_engage_stance(self):
        """Against high HP target, TacticalCombatEngine chooses BUFF_AND_ENGAGE stance."""
        pression = self.repo.find_spell("Pression")
        divine = self.repo.find_spell("Epee Divine")
        spells = [pression, divine]

        poutch = self.repo.find_monster("Poutch Ingball")
        target = TargetStats.from_monster_grade(poutch, 5)

        char = CharacterStats(action_points=10, strength=300, agility=100)
        plan = TacticalCombatEngine.plan_tactical_turn(char, target, spells)

        self.assertEqual(plan.stance, TacticalStance.BUFF_AND_ENGAGE)
        self.assertGreater(len(plan.action_sequence), 0)
        self.assertEqual(plan.action_sequence[0]["type"], "CAST_BUFF")

    def test_08_emergency_shield_defense_on_low_hp(self):
        """When HP is critical (<35%), prioritize healing and disengagement."""
        mot_interdit = self.repo.find_spell("Mot Interdit")
        mot_soignant = SpellInfo(
            id=125, name_fr="Mot Soignant", name_en="Healing Word", name_es="Palabra Sanadora",
            breed_id=7, ap_cost=3, effects=[DamageEffect("HEAL", 15, 25)]
        )
        spells = [mot_interdit, mot_soignant]

        mob = self.repo.find_monster("Bouftou")
        target = TargetStats.from_monster_grade(mob, 1)

        char = CharacterStats(action_points=9, intelligence=200, breed_id=7)
        plan = TacticalCombatEngine.plan_tactical_turn(char, target, spells, char_hp_percent=0.25)

        self.assertEqual(plan.stance, TacticalStance.SHIELD_DEFENSE)
        self.assertEqual(plan.action_sequence[0]["type"], "CAST_HEAL")


class TestSentinelCombatEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadedHTTPServer(("127.0.0.1", 8895), SentinelHTTPHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"http://127.0.0.1:8895{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(f"http://127.0.0.1:8895{path}")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_01_status_reports_combat_and_counts(self):
        data = self._get("/sentinel/status")
        self.assertEqual(data["status"], "online")
        models = data["singleton_models"]
        self.assertTrue(models["combat_calculator"])
        self.assertGreater(models["total_spells"], 20)
        self.assertGreater(models["total_monsters"], 5)

    def test_02_get_mob_endpoint(self):
        data = self._get("/sentinel/combat/mob?name=Gobball&lang=es")
        self.assertEqual(data["status"], "ok")
        self.assertIn(data["id"], [101, 3332])
        self.assertEqual(data["name"], "Jalató")
        self.assertIn("hp", data["grade"])

    def test_03_get_spells_endpoint(self):
        data = self._get("/sentinel/combat/spells?breed=8&lang=fr")
        self.assertEqual(data["status"], "ok")
        self.assertGreater(len(data["spells"]), 5)

    def test_04_calculate_damage_endpoint(self):
        payload = {
            "spell": "Pression",
            "character": {
                "strength": 400,
                "power": 50,
                "damage_earth": 20,
            },
            "target": {
                "name": "BenchmarkTarget",
                "res_percent_earth": 20,
            },
        }
        data = self._post("/sentinel/combat/calculate_damage", payload)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["damage_min"], 77)
        self.assertEqual(data["damage_max"], 95)
        self.assertGreater(data["expected_damage"], 80)

    def test_05_best_combo_endpoint(self):
        payload = {
            "character": {
                "action_points": 9,
                "strength": 400,
                "power": 50,
                "damage_earth": 20,
                "breed_id": 8,
            },
            "monster": "Chef de Guerre Bouftou",
            "monster_grade": 1,
        }
        data = self._post("/sentinel/combat/best_combo", payload)
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["is_guaranteed_lethal"])
        self.assertEqual(data["estimated_turns_to_kill"], 1)
        self.assertGreater(len(data["rotation"]), 0)

    def test_06_parse_command_combat_directive(self):
        payload = {
            "command": "Attaque le chef de guerre bouftou",
            "target_slot": "slot-1",
            "character": {
                "action_points": 10,
                "strength": 400,
                "power": 50,
                "damage_earth": 20,
                "breed_id": 8,
            }
        }
        data = self._post("/sentinel/parse_command", payload)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["directive"], "EXECUTE_TACTICAL_PLAN")
        self.assertIn("metadata", data)
        self.assertIn("stance", data["metadata"])

    def test_07_tactical_plan_endpoint(self):
        payload = {
            "character": {
                "action_points": 10,
                "strength": 400,
                "power": 50,
                "damage_earth": 20,
                "breed_id": 8,
            },
            "monster": "Craqueleur des Plaines",
            "monster_grade": 1,
            "distance_cells": 4,
            "hp_percent": 1.0,
        }
        data = self._post("/sentinel/combat/tactical_plan", payload)
        self.assertEqual(data["status"], "ok")
        self.assertIn("stance", data)
        self.assertGreater(len(data["action_sequence"]), 0)

    def test_08_teacher_distillation_pipeline(self):
        from data.teacher_distillation import TeacherKnowledgeDistiller
        import torch

        prompt = TeacherKnowledgeDistiller.get_teacher_generation_prompt("tactical_combat", "fr", 5)
        self.assertIn("Qwen", TeacherKnowledgeDistiller.DEFAULT_TEACHER_MODEL)
        self.assertIn("5", prompt)

        # Test Hinton-style KL distillation loss calculation
        student_logits = torch.randn(4, 6)
        teacher_logits = torch.randn(4, 6)
        labels = torch.tensor([0, 1, 2, 3])
        loss = TeacherKnowledgeDistiller.compute_distillation_loss(student_logits, teacher_logits, labels)
        self.assertTrue(torch.is_tensor(loss))
        self.assertGreater(loss.item(), 0)

        # Test synthetic seed traces
        traces = TeacherKnowledgeDistiller.synthesize_local_seed_traces()
        self.assertGreater(len(traces), 3)
        self.assertEqual(traces[0]["type"], "DISTILLED_COT")


if __name__ == "__main__":
    unittest.main()
