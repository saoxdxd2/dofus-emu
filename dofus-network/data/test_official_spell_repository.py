import unittest

from data.official_spell_repository import iter_official_spells, load_official_database, spell_level_to_info
from tools.build_official_spell_database import _normalise_effect


class OfficialSpellAdapter(unittest.TestCase):
    def test_pursuit_level_one(self):
        database = load_official_database()
        pursuit = next(spell for spell in iter_official_spells(database, breed_id=8) if spell["id"] == 8105)

        info = spell_level_to_info(pursuit, level=1)

        self.assertEqual((info.ap_cost, info.min_range, info.max_range), (3, 1, 2))
        self.assertEqual(info.crit_rate_percent, 15)
        self.assertTrue(info.cast_in_line)
        self.assertEqual(len(info.effects), 1)
        self.assertEqual(
            (info.effects[0].element, info.effects[0].min_roll, info.effects[0].max_roll),
            ("AIR", 18, 21),
        )
        self.assertEqual(
            (info.effects[0].crit_min_roll, info.effects[0].crit_max_roll),
            (21, 24),
        )

    def test_single_roll_damage_and_healing(self):
        damage = _normalise_effect({"text": "20 (Fire damage)"})
        healing = _normalise_effect({"text": "18 to 21 (HP restored)"})

        self.assertEqual(damage["damage"], {"element": "FIRE", "min_roll": 20, "max_roll": 20})
        self.assertEqual(healing["heal"], {"min_roll": 18, "max_roll": 21})


if __name__ == "__main__":
    unittest.main()
