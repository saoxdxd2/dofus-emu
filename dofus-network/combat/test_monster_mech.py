"""The monster database has to answer the one question the board does not.

A live board hands the decision layer an enemy with an empty spell list, so the
only way to know how far that enemy reaches is to look it up.

The arithmetic is tested against fixtures and always runs. The harvested-data
tests skip when the cache is absent, because a missing cache is not a
regression -- but a suite that only ever skips is a suite that never checks
anything, so the logic half does not depend on it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from combat.monster_mech import (CACHE_FILE, DAMAGE_EFFECTS, GLOBAL_RANGE,
                                 MonsterMechanics, _key, _live_damage, threat_for)

BLUE_LARVA = 31  # 2 MP, casts Disabling Splutter (3 AP, range 1-6) at grade 1

FIXTURE = {
    "monsters": {
        "31": {
            "name": "Blue Larva", "is_boss": False,
            "grades": [
                {"grade": 1, "level": 16, "life": 90, "ap": 5, "mp": 2,
                 "neutral_res": 1, "earth_res": 6, "fire_res": 6,
                 "water_res": -9, "air_res": -9},
                {"grade": 2, "level": 17, "life": 99, "ap": 5, "mp": 3,
                 "neutral_res": 2, "earth_res": 7, "fire_res": 7,
                 "water_res": -8, "air_res": -8},
            ],
            "spells": [10198, 10199],
        },
        "77": {  # a monster whose only spell is a buff
            "name": "Buffer", "is_boss": False,
            "grades": [{"grade": 1, "level": 10, "life": 50, "ap": 4, "mp": 1,
                        "neutral_res": 0, "earth_res": 0, "fire_res": 0,
                        "water_res": 0, "air_res": 0}],
            "spells": [5],
        },
    },
    "spell_levels": {
        "10198": [{"grade": 1, "ap_cost": 3, "min_range": 1, "max_range": 6,
                   "damage": [{"element": "earth", "min": 5, "max": 7}]}],
        "10199": [{"grade": 1, "ap_cost": 2, "min_range": 0, "max_range": 0,
                   "damage": []}],
        "5": [{"grade": 1, "ap_cost": 2, "min_range": 0, "max_range": 3,
               "damage": []}],
    },
}


def _fixture() -> MonsterMechanics:
    return MonsterMechanics(FIXTURE["monsters"], FIXTURE["spell_levels"])


class TestArithmetic(unittest.TestCase):
    """Always runs: no cache, no network."""

    def setUp(self):
        self.engine = _fixture()

    def test_reach_is_mp_plus_the_longest_damaging_range(self):
        self.assertEqual(self.engine.reach(BLUE_LARVA, 16), 2 + 6)

    def test_reach_uses_the_grade_that_matches_the_level(self):
        self.assertEqual(self.engine.reach(BLUE_LARVA, 16), 8)   # mp 2
        self.assertEqual(self.engine.reach(BLUE_LARVA, 17), 9)   # mp 3

    def test_a_monster_with_only_buffs_still_reports_a_reach(self):
        """Not `None`: its range is real even though it deals no damage."""
        self.assertEqual(self.engine.reach(77, 10), 1 + 3)

    def test_an_unknown_monster_is_none_not_zero(self):
        """Reading an unknown reach as zero marks every cell safe against it."""
        self.assertIsNone(self.engine.reach(-999999, 10))
        self.assertIsNone(threat_for(-999999, 10, 100, engine=self.engine))
        self.assertEqual(self.engine.spells(-999999, 10), [])
        self.assertEqual(self.engine.grades(-999999), [])

    def test_a_buff_only_monster_is_not_a_threat(self):
        """A monster that cannot damage you contributes no threat entry."""
        self.assertIsNone(threat_for(77, 10, 100, engine=self.engine))

    def test_a_threat_carries_the_monsters_own_mp_and_ranges(self):
        threat = threat_for(BLUE_LARVA, 16, 200, engine=self.engine)
        self.assertIsNotNone(threat)
        self.assertEqual(threat.cell, 200)
        self.assertEqual(threat.mp, 2)
        # Only the damaging spell survives the filter.
        self.assertEqual(threat.spells, ((1, 6, 6.0),))

    def test_damage_per_turn_respects_the_ap_budget(self):
        # 5 AP, a 3-AP spell for 6 damage, and a 2-AP spell for nothing.
        self.assertAlmostEqual(self.engine.damage_per_turn(BLUE_LARVA, 16), 6.0)

    def test_resistance_reduces_a_spell_that_has_damage(self):
        spell = self.engine.spells(BLUE_LARVA, 16)[0]
        plain = spell.damage_against({})
        self.assertAlmostEqual(plain, 6.0)
        self.assertLess(spell.damage_against({"earth": 50}), plain)
        self.assertEqual(spell.damage_against({"earth": 100}), 0.0)


class TestHarvestedData(unittest.TestCase):
    """Runs only when the harvest has produced a cache."""

    @classmethod
    def setUpClass(cls):
        if not CACHE_FILE.exists():
            raise unittest.SkipTest(
                "monster_spells.json not harvested; run data.harvest_monster_spells --write")
        cls.engine = MonsterMechanics.load()

    def test_the_blue_larva_reaches_eight_cells(self):
        """2 MP plus a 1-6 spell. The character's longest spell reaches five.

        This is the concrete fact the system was missing: there is no distance
        at which this monster cannot answer, so "keep away from it" is not a
        strategy and the policy had no way to know that.
        """
        self.assertEqual(self.engine.name(BLUE_LARVA), "Blue Larva")
        grade = self.engine.grade_at_level(BLUE_LARVA, 16)
        self.assertIsNotNone(grade)
        self.assertEqual(grade.mp, 2)
        spells = self.engine.spells(BLUE_LARVA, 16)
        self.assertTrue(spells, "no spells resolved for the blue larva")
        self.assertEqual(max(s.max_range for s in spells), 6)
        self.assertEqual(self.engine.reach(BLUE_LARVA, 16), 8)

    def test_reach_is_mp_plus_the_longest_range(self):
        for monster_id, level in ((BLUE_LARVA, 16), (BLUE_LARVA, 20)):
            grade = self.engine.grade_at_level(monster_id, level)
            spells = self.engine.spells(monster_id, level)
            if not spells:
                continue
            pool = [s for s in spells if s.damage] or [
                s for s in spells if s.max_range < GLOBAL_RANGE]
            if not pool:
                continue
            self.assertEqual(self.engine.reach(monster_id, level),
                             grade.mp + max(s.max_range for s in pool))


# ---------------------------------------------------------------------------
# the client's own spellbook, read back from the recorded fights
# ---------------------------------------------------------------------------

def _board(fighters) -> str:
    return json.dumps({"kind": "board", "board": {"fighters": fighters}})


def _enemy(name, level, spells, mp=0, ap=6, hp=100, team="enemy"):
    return {"name": name, "level": level, "mp": mp, "ap": ap, "max_hp": hp,
            "team": team, "is_monster": True, "spells": spells}


def _spell(spell_id, ap_cost, low, high, effects, requires_los=True):
    return {"id": spell_id, "name": "S%d" % spell_id, "level": 1,
            "ap_cost": ap_cost, "min_range": 1, "range": high,
            "requires_los": requires_los, "max_cast_per_turn": 1,
            "effects": effects}


def _damage_effect(effect_id, dice_num, dice_side):
    return {"effect_id": effect_id, "dice_num": dice_num, "dice_side": dice_side,
            "value": 0, "element_id": None, "target_id": 0, "duration": 0}


class TestLiveCorpus(unittest.TestCase):
    """The loader that makes the recorded fights a bestiary. No cache needed."""

    def _write(self, lines) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                             encoding="utf-8")
        handle.write("\n".join(lines) + "\n")
        handle.close()
        return Path(handle.name)

    def test_a_named_monster_gets_its_reach_from_its_own_spellbook(self):
        path = self._write([_board([_enemy("Thwacker", 5, [
            _spell(6908, 1, 1, 10, [_damage_effect(97, 12, 14)])])])])
        engine = MonsterMechanics.from_live_corpus([path])
        self.assertEqual(engine.name("Thwacker"), "Thwacker")
        self.assertEqual(engine.reach("Thwacker", 5), 10)

    def test_reach_adds_the_monsters_mp_to_the_spell_range(self):
        path = self._write([_board([_enemy("Runner", 8, [
            _spell(1, 3, 1, 6, [_damage_effect(97, 5, 7)])], mp=3)])])
        self.assertEqual(MonsterMechanics.from_live_corpus([path]).reach("Runner", 8), 9)

    def test_a_global_range_buff_does_not_become_a_reach(self):
        """The bug this pins: `Burst of Motivation` is a range-63 ally buff.

        Read as a damage range it gives a 3-MP caster a reach of 66 and marks
        every cell on the map threatened, so cover and distance price as nothing.
        """
        path = self._write([_board([_enemy("Buffer", 50, [
            _spell(7119, 4, 1, GLOBAL_RANGE, [{"effect_id": 138, "dice_num": 3000,
                                               "dice_side": 0, "value": 0}],
                   requires_los=False)], mp=3)])])
        engine = MonsterMechanics.from_live_corpus([path])
        self.assertEqual(engine.spells("Buffer", 50)[0].max_range, GLOBAL_RANGE)
        self.assertIsNone(engine.reach("Buffer", 50))

    def test_a_monster_with_no_spellbook_is_absent_rather_than_harmless(self):
        path = self._write([_board([_enemy("Silent", 5, [])])])
        engine = MonsterMechanics.from_live_corpus([path])
        self.assertIsNone(engine.reach("Silent", 5))
        self.assertEqual(engine.grades("Silent"), [])

    def test_a_player_fighter_does_not_become_a_monster(self):
        hero = dict(_enemy("Hero", 12, [_spell(1, 3, 1, 5, [_damage_effect(97, 5, 7)])]))
        hero.update({"team": "player", "is_monster": False})
        path = self._write([_board([hero])])
        self.assertEqual(len(MonsterMechanics.from_live_corpus([path])), 0)

    def test_a_missing_corpus_is_an_empty_table_not_an_error(self):
        engine = MonsterMechanics.from_live_corpus([Path(tempfile.gettempdir()) / "nope.jsonl"])
        self.assertEqual(len(engine), 0)

    def test_multi_element_lines_are_one_cast_not_a_sum(self):
        """`Deviousness` carries earth 12-14, 15-17, 18-20 -- one element, one cast."""
        damage = _live_damage([_damage_effect(97, 12, 14),
                               _damage_effect(97, 15, 17),
                               _damage_effect(97, 18, 20)])
        # Three lines of the *same* element are three hits and do add.
        self.assertEqual(damage, [("earth", 45, 51)])

    def test_two_different_elements_keep_only_the_stronger(self):
        damage = _live_damage([_damage_effect(97, 10, 10),    # earth
                               _damage_effect(99, 30, 30)])   # fire
        self.assertEqual(damage, [("fire", 30, 30)])

    def test_a_non_damage_effect_is_not_damage(self):
        self.assertEqual(_live_damage([{"effect_id": 138, "dice_num": 3000,
                                        "dice_side": 0, "value": 0}]), [])

    def test_a_fixed_roll_with_no_dice_pair_uses_its_value(self):
        self.assertEqual(_live_damage([{"effect_id": 97, "dice_num": 0,
                                        "dice_side": 0, "value": 7}]),
                         [("earth", 7, 7)])

    def test_a_name_key_cannot_shadow_a_numeric_id(self):
        """A monster literally named "31" is stored apart from monster id 31."""
        self.assertEqual(_key(31), "31")
        self.assertEqual(_key("31"), "31")          # a decimal string is an id
        self.assertEqual(_key(" 31 "), "31")
        self.assertEqual(_key("Blue Larva"), "name:blue larva")
        # The live loader always prefixes, so the digit-named monster is not an id.
        path = self._write([_board([_enemy("31", 5, [
            _spell(1, 3, 1, 5, [_damage_effect(97, 5, 7)])])])])
        engine = MonsterMechanics.from_live_corpus([path])
        self.assertEqual(sorted(engine._monsters), ["name:31"])
        # The id namespace is untouched, and the name row is not readable *as* an id.
        # A digit-only monster name is genuinely ambiguous with an id, so it is stored
        # without collision and looked up as an id (absent) rather than guessed at.
        self.assertIsNone(engine.reach("31", 5))

    def test_the_damage_effects_agree_with_the_solver(self):
        """The mapping is duplicated to keep `battle_solver` out of the import cycle.

        `battle_solver` imports `combat.threat`, so `monster_mech` may not import it
        back. This test is what makes the copy safe: if either table is edited, the
        two stop agreeing and the suite says so.
        """
        from combat.battle_solver import ELEMENT_BY_EFFECT

        solver = {effect: element.lower()
                  for effect, (kind, element) in ELEMENT_BY_EFFECT.items()
                  if kind in ("damage", "steal")}
        self.assertEqual(solver, DAMAGE_EFFECTS)


class TestRealCorpus(unittest.TestCase):
    """Against the boards actually recorded. Skips when they are not there."""

    @classmethod
    def setUpClass(cls):
        from combat.monster_mech import LIVE_CORPUS

        if not LIVE_CORPUS.exists():
            raise unittest.SkipTest("%s is not on this machine" % LIVE_CORPUS.name)
        cls.engine = MonsterMechanics.from_live_corpus()

    def test_the_corpus_yields_a_bestiary_at_all(self):
        self.assertGreater(len(self.engine), 0,
                           "the recorded boards carry no resolvable spellbook")

    def test_merkaptan_leader_reaches_ten_cells_not_five(self):
        """Measured 2026-11-11 on fight 2659: level 5, 0 MP, `Thwack` at range 10.

        The level prior this module shipped with assumed 5, so the policy was
        told the fight was safe at a distance the monster can hit from.
        """
        self.assertEqual(self.engine.reach("Merkaptan Leader", 5), 10)

    def test_a_monster_whose_only_spell_is_a_global_buff_has_no_reach(self):
        self.assertIsNone(self.engine.reach("Ayin Spyr", 50))


if __name__ == "__main__":
    unittest.main()
