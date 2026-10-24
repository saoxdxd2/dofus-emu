import unittest

from combat.damage_calculator import CharacterStats, DofusDamageCalculator, SpellRotationOptimizer, TargetStats
from data.game_data_extractor import DamageEffect, SpellInfo


def spell_with(effect):
    return SpellInfo(
        id=1, name_fr="Test", name_en="Test", name_es="Test", breed_id=1,
        ap_cost=3, crit_rate_base=0, effects=[effect],
    )


class OfficialDamageExamples(unittest.TestCase):
    def test_characteristics_and_power_scale_base_damage(self):
        spell = spell_with(DamageEffect("EARTH", 21, 40, 21, 40))
        char = CharacterStats(strength=100, power=100)

        result = DofusDamageCalculator.calculate_spell_damage(spell, char, TargetStats())

        self.assertEqual((result.total_min, result.total_max), (63, 120))

    def test_flat_and_critical_bonuses_apply_after_scaling(self):
        spell = spell_with(DamageEffect("FIRE", 200, 200, 200, 200))
        char = CharacterStats(damage=10, damage_fire=10, damage_critical=20)

        result = DofusDamageCalculator.calculate_spell_damage(spell, char, TargetStats())

        self.assertEqual(result.total_min, 220)
        self.assertEqual(result.crit_total_min, 240)

    def test_fixed_resistance_applies_before_percentage_resistance(self):
        spell = spell_with(DamageEffect("FIRE", 200, 200, 200, 200))
        target = TargetStats(res_fixed_fire=10, res_percent_fire=25)

        result = DofusDamageCalculator.calculate_spell_damage(spell, CharacterStats(), target)

        self.assertEqual(result.total_min, 142)

    def test_critical_resistance_only_reduces_critical_hits(self):
        spell = spell_with(DamageEffect("FIRE", 200, 200, 200, 200))
        target = TargetStats(res_fixed_critical=20)

        result = DofusDamageCalculator.calculate_spell_damage(spell, CharacterStats(), target)

        self.assertEqual(result.total_min, 200)
        self.assertEqual(result.crit_total_min, 180)

    def test_only_player_percentage_resistance_is_capped(self):
        spell = spell_with(DamageEffect("FIRE", 100, 100, 100, 100))

        player = DofusDamageCalculator.calculate_spell_damage(
            spell, CharacterStats(), TargetStats(res_percent_fire=80, is_player=True))
        monster = DofusDamageCalculator.calculate_spell_damage(
            spell, CharacterStats(), TargetStats(res_percent_fire=80))

        self.assertEqual(player.total_min, 65)
        self.assertEqual(monster.total_min, 20)

    def test_healing_uses_intelligence_and_heals_not_damage(self):
        spell = spell_with(DamageEffect("HEAL", 10, 10, 12, 12))
        char = CharacterStats(intelligence=100, heals=5, damage=100, damage_critical=100)

        result = DofusDamageCalculator.calculate_spell_damage(spell, char, TargetStats())

        self.assertEqual(result.total_min, 25)
        self.assertEqual(result.crit_total_min, 29)

    def test_rotation_does_not_score_healing_as_damage(self):
        healing = spell_with(DamageEffect("HEAL", 100, 100, 100, 100))
        healing.id = 2
        mixed = spell_with(DamageEffect("FIRE", 10, 10, 10, 10))
        mixed.effects.append(DamageEffect("HEAL", 100, 100, 100, 100))

        result = SpellRotationOptimizer.optimize_turn(
            CharacterStats(action_points=3), [healing, mixed], TargetStats(current_hp=200))

        self.assertEqual(result.total_min_damage, 10)
        self.assertEqual(len(result.rotation), 1)

    def test_client_critical_chance_is_percentage_plus_stat(self):
        self.assertEqual(DofusDamageCalculator.calculate_crit_probability(15, 10), 0.25)
        self.assertEqual(DofusDamageCalculator.calculate_crit_probability(15, 100), 1.0)
        self.assertEqual(DofusDamageCalculator.calculate_crit_probability(0, 100), 0.0)


if __name__ == "__main__":
    unittest.main()
