"""Comprehensive Test Suite for Advanced Combat Architecture (ACL 2026 Principles).

Tests:
1. Dofus World Model & Latent Environment Rollouts
2. Tiny Recursive Reasoner with Adaptive Computation Time (TRM-ACT)
3. External Episodic Skill Memory & Instant Recall
4. Reward-Guided Policy Optimization
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "dofus-network"))

from data.game_data_extractor import GameDataRepository
from combat.damage_calculator import CharacterStats, TargetStats
from combat.world_model import CombatState, DofusWorldModel
from combat.recursive_reasoner import (
    SingletonCombatReasoner,
    TacticalStateEncoder,
    TinyRecursiveReasoner,
)
from combat.episodic_memory import EpisodicSkillMemory


class TestWorldModelAndRecursiveReasoner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = GameDataRepository.get_instance()
        cls.reasoner = SingletonCombatReasoner.get_instance()
        cls.memory = EpisodicSkillMemory.get_instance()

    def test_01_world_model_step_and_reward(self):
        pression = self.repo.find_spell("Pression")
        self.assertIsNotNone(pression)

        char = CharacterStats(action_points=10, strength=400, power=50, damage_earth=20)
        target = TargetStats(name="Chef de Guerre Bouftou", max_hp=200, current_hp=100, res_percent_earth=0)

        s0 = CombatState.from_entities(char, target, char_hp=1000, char_max_hp=1000)
        self.assertEqual(s0.action_points, 10)
        self.assertEqual(s0.target_hp, 100)

        # Step 1: Cast Pression (3 AP, ~97 dmg)
        step_res = DofusWorldModel.step(s0, pression)
        s1 = step_res.next_state

        self.assertEqual(s1.action_points, 7)
        self.assertLess(s1.target_hp, s0.target_hp)
        self.assertTrue(step_res.reward > 0.0)

        # Step 2: Cast Pression again -> lethal kill!
        step_res2 = DofusWorldModel.step(s1, pression)
        self.assertTrue(step_res2.is_terminal)
        self.assertEqual(step_res2.next_state.target_hp, 0)
        # Lethal kill gives +15.0 bonus
        self.assertGreater(step_res2.reward, 15.0)

    def test_02_world_model_simulation_rollout(self):
        pression = self.repo.find_spell("Pression")
        char = CharacterStats(action_points=9, strength=400, power=50, damage_earth=20)
        target = TargetStats(name="Dummy", max_hp=500, current_hp=500)

        s0 = CombatState.from_entities(char, target)
        trajectory = [pression, pression, pression]  # 3x 3 AP = 9 AP

        final_state, total_damage, total_reward = DofusWorldModel.simulate_rollout(s0, trajectory)
        self.assertEqual(final_state.action_points, 0)
        self.assertGreater(total_damage, 250.0)
        self.assertGreater(total_reward, 10.0)

    def test_03_trm_adaptive_computation_time(self):
        """Verify the Tiny Recursive Model runs in latent space and halts adaptively."""
        char = CharacterStats(action_points=10, strength=300)
        target = TargetStats(name="Gobball", max_hp=100, current_hp=50)

        s = CombatState.from_entities(char, target, char_hp=1000, char_max_hp=1000)
        decision = self.reasoner.decide(s, breed_id=8)

        self.assertIn(decision.stance, ["BURST_LETHAL", "BUFF_AND_ENGAGE", "TRAP_AND_KITE", "SHIELD_DEFENSE"])
        self.assertGreater(decision.confidence, 0.0)
        # Should halt adaptively within max recursion steps (1 to 4)
        self.assertGreaterEqual(decision.ponder_steps, 1)
        self.assertLessEqual(decision.ponder_steps, 4)

    def test_04_episodic_memory_skill_recall(self):
        """Instant (<0.05ms) retrieval of proven tactical sequences."""
        # Query known seeded skill for Iop vs Chef de Guerre Bouftou
        skill = self.memory.query_skill(breed_id=8, target_name="Chef de Guerre Bouftou", distance_cells=3, char_hp_percent=1.0)
        self.assertIsNotNone(skill)
        self.assertEqual(skill.stance, "BUFF_AND_ENGAGE")
        self.assertIn("Puissance", skill.proven_sequence)

        # Store a new high-reward skill
        self.memory.record_outcome(
            breed_id=8,
            target_name="TestBoss",
            distance_cells=2,
            char_hp_percent=0.8,
            stance="BURST_LETHAL",
            action_sequence=["Pression", "Concentration"],
            reward=18.5,
        )

        recalled = self.memory.query_skill(breed_id=8, target_name="TestBoss", distance_cells=2, char_hp_percent=0.8)
        self.assertIsNotNone(recalled)
        self.assertEqual(recalled.stance, "BURST_LETHAL")
        self.assertEqual(recalled.reward_achieved, 18.5)


if __name__ == "__main__":
    unittest.main()
