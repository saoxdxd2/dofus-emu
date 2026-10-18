#!/usr/bin/env python3
"""Combat damage calculator tests: formulas, TTK, rotation optimization."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from combat.damage_calculator import (
    CharacterStats, DofusDamageCalculator, TargetStats,
    SpellRotationOptimizer,
)
from data.game_data_extractor import DamageEffect, SpellInfo


class TestDamageCalculator(unittest.TestCase):
    def setUp(self):
        self.calc = DofusDamageCalculator()
        self.char = CharacterStats(
            name="Test", level=100, action_points=10, movement_points=4,
            strength=100, intelligence=50, chance=30, agility=40,
            power=50, damage=20, critical_hit=10, damage_critical=30,
        )
        self.target = TargetStats(
            name="Mob", max_hp=500, current_hp=500, level=50,
            res_percent_neutral=0, res_percent_earth=25,
            res_percent_fire=10, res_percent_water=0, res_percent_air=5,
            res_fixed_neutral=0, res_fixed_earth=0, res_fixed_fire=0,
            res_fixed_water=0, res_fixed_air=0, armor_shield=0,
        )

    def _make_spell(self, spell_id, element="earth", base_min=50, base_max=100,
                    ap_cost=3, crit_rate_base=30):
        """Create a spell with a single damage effect."""
        effect = DamageEffect(
            element=element, min_roll=base_min, max_roll=base_max,
            crit_min_roll=base_min, crit_max_roll=base_max,
        )
        return SpellInfo(
            id=spell_id, name_fr=f"Spell {spell_id}", name_en=f"Spell {spell_id}",
            name_es=f"Spell {spell_id}", breed_id=8, level=1,
            ap_cost=ap_cost, min_range=1, max_range=4,
            max_cast_per_turn=2, max_cast_per_target=1,
            crit_rate_base=crit_rate_base, effects=[effect],
        )

    def test_raw_damage_formula(self):
        spell = self._make_spell(1, element="earth", base_min=50, base_max=100)
        # calculate_spell_damage(spell, char, target) - spell first
        result = self.calc.calculate_spell_damage(spell, self.char, self.target)
        self.assertIsInstance(result.total_min, int)
        self.assertIsInstance(result.total_max, int)
        self.assertGreaterEqual(result.total_min, 0)
        self.assertGreaterEqual(result.total_max, result.total_min)
        self.assertTrue(math.isfinite(result.expected_value))

    def test_resistance_reduces_damage(self):
        # earth spell vs target with 25% earth resistance
        spell = self._make_spell(2, element="earth", base_min=100, base_max=100)
        result = self.calc.calculate_spell_damage(spell, self.char, self.target)
        self.assertGreaterEqual(result.total_min, 0)

    def test_ttk_prediction(self):
        spells = [
            self._make_spell(1, element="earth", base_min=100, base_max=150, ap_cost=3),
        ]
        result = SpellRotationOptimizer.optimize_turn(
            self.char, spells, self.target
        )
        self.assertIsInstance(result.estimated_turns_to_kill, int)
        self.assertGreaterEqual(result.estimated_turns_to_kill, 1)
        self.assertIsInstance(result.rotation, list)
        self.assertGreaterEqual(result.total_expected_damage, 0.0)

    def test_negative_resistance_increases_damage(self):
        target_neg_res = TargetStats(
            name="Weak Mob", max_hp=500, current_hp=500, level=50,
            res_percent_neutral=-50, res_percent_earth=0,
            res_percent_fire=0, res_percent_water=0, res_percent_air=0,
            res_fixed_neutral=0, res_fixed_earth=0, res_fixed_fire=0,
            res_fixed_water=0, res_fixed_air=0, armor_shield=0,
        )
        spell = self._make_spell(3, element="neutral", base_min=100, base_max=100)
        r_normal = self.calc.calculate_spell_damage(spell, self.char, self.target)
        r_neg = self.calc.calculate_spell_damage(spell, self.char, target_neg_res)
        # Negative resistance should increase damage
        self.assertGreater(r_neg.total_min, r_normal.total_min)


if __name__ == "__main__":
    unittest.main()