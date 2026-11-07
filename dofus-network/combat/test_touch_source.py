"""Witnesses for the real Touch data source: id-payload dice, live-kit parity, non-vacuity."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
EVIDENCE = pathlib.Path(os.environ.get(
    "DOFUS_EVIDENCE", pathlib.Path.home() / "Documents" / "dofus-evidence"))
TOUCH = EVIDENCE / "touch-data"
BOARDS = pathlib.Path(os.environ.get(
    "DOFUS_BOARDS", pathlib.Path.home() / "AppData" / "Local" / "DofusFarm"
    / "fight_boards.jsonl"))

from combat.encounter import build_dataset
from combat.encounter.touch_source import TouchData, _expected

GARBAGE_LEVEL = {"spellId": 4610, "apCost": 6, "minRange": 0, "range": 0, "effects": [
    {"effectId": 99, "diceNum": 10001, "diceSide": 15000, "duration": 0, "triggers": "I"}]}
REAL_LEVEL = {"spellId": 7129, "apCost": 3, "minRange": 1, "range": 2, "effects": [
    {"effectId": 91, "diceNum": 5, "diceSide": 6, "duration": 0, "triggers": "I", "value": 0},
    {"effectId": 111, "diceNum": 1, "diceSide": 0, "duration": 1, "triggers": "I"}]}


class ExpectedTests(unittest.TestCase):
    def test_id_payload_dice_is_not_damage(self):
        self.assertEqual(_expected(GARBAGE_LEVEL["effects"][0]), 0.0)

    def test_real_die_is_expected_value(self):
        self.assertEqual(_expected(REAL_LEVEL["effects"][0]), 5 * 7 / 2.0)

    def test_over_time_effect_is_not_this_casts_damage(self):
        data = {"spellId": 1, "apCost": 2, "minRange": 0, "range": 0, "effects": [
            {"effectId": 108, "diceNum": 5, "diceSide": 6, "duration": 3, "triggers": "I"}]}
        self.assertTrue(TouchData.__dict__["slot"](TouchData.__new__(TouchData), data) is None)


class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (TOUCH / "monsters_fr.json").is_file():
            raise unittest.SkipTest("touch tables not fetched")
        cls.touch = TouchData(TOUCH)

    def test_kit_is_padded_to_slots_and_ordered_by_damage(self):
        kit = self.touch.kit(31, 4, 1)
        self.assertEqual(len(kit), 4)
        damage = [row["damage"] for row in kit]
        self.assertEqual(damage, sorted(damage, reverse=True))
        self.assertGreater(damage[0], 0.0)

    def test_monster_rows_keep_the_cache_key_names(self):
        rows = self.touch.monster_rows()
        self.assertTrue(rows)
        keys = set(rows[0]["grades"][0])
        for needed in ("level", "life_points", "action_points", "movement_points",
                       "neutral_res", "earth_res", "fire_res", "water_res", "air_res"):
            self.assertIn(needed, keys)

    def test_bosses_survive_the_translation(self):
        rows = self.touch.monster_rows()
        self.assertGreater(sum(1 for r in rows if r["is_boss"]), 100)

    def test_live_kits_match_the_fetched_tables_exactly(self):
        """The monster side's own spells must reproduce, row for row.

        Monster spell ids carry one level row each; player spells on the same boards carry
        six, and the client casts them at a level of its own choosing. Only the monster side
        can pin the row the corpus should use, so that is what this checks.
        """
        if not BOARDS.is_file():
            self.skipTest("no live fight boards on this machine")
        kits = {}
        for line in BOARDS.read_text(encoding="utf-8").splitlines():
            for f in (json.loads(line).get("board") or {}).get("fighters", []):
                if not f.get("is_monster"):
                    continue
                for s in (f.get("spells") or []):
                    if isinstance(s, dict):
                        kits.setdefault(s["id"], s)
        checked = 0
        for spell_id, live in kits.items():
            rows = self.touch.by_spell.get(spell_id) or []
            if not rows:
                continue
            chosen = self.touch._level_for(spell_id)
            self.assertIsNotNone(chosen, "no level row for live spell %s" % spell_id)
            self.assertEqual((chosen.get("apCost"), chosen.get("minRange"),
                              chosen.get("range")),
                             (live.get("ap_cost"), live.get("min_range"), live.get("range")),
                             "the chosen spell level is not what the client casts for"
                             " %s (%s)" % (spell_id, live.get("name")))
            checked += 1
        self.assertGreaterEqual(checked, 12, "expected at least the 12 captured kits")

    def test_slot_uses_only_the_immediate_damage_effects(self):
        slot = self.touch.slot(dict(REAL_LEVEL))
        self.assertEqual(slot["damage"], 5 * 7 / 2.0)
        self.assertEqual(slot["heal"], 0.0)
        self.assertEqual(slot["rmax"], 2.0)
        self.assertFalse(slot["ally"])


class GeneratorTests(unittest.TestCase):
    def test_real_source_produces_nonzero_enemy_damage(self):
        if not (TOUCH / "monsters_fr.json").is_file():
            self.skipTest("touch tables not fetched")
        stats = {}
        data = build_dataset.build(64, 11, touch=TouchData(TOUCH), stats=stats)
        damage = data["spell_damage"]
        self.assertEqual(damage.ndim, 3, "tables are [samples, actors, slots]; a max over "
                                          "the wrong axis silently counts padding as enemies")
        occupied = data["hp"][:, 1:] > 0
        damaging = (damage[:, 1:].max(axis=2) > 0) & occupied
        self.assertGreater(int(occupied.sum()), 0)
        self.assertTrue(damaging.any(axis=1).all(), "a sample whose enemies cannot hurt you")
        self.assertEqual(int(damaging.sum()), int(occupied.sum()),
                         "every real enemy must carry a damaging line, not just the sample")
        self.assertGreater(float(damage[damage > 0].mean()), 0.0)
        self.assertGreater(stats["real_kit_slots"], 0)
        self.assertEqual(stats["source"], "dofus touch client data tables")

    def test_invented_path_still_works_and_says_so(self):
        stats = {}
        data = build_dataset.build(32, 5, stats=stats)
        self.assertEqual(stats["source"],
                         "data/cache (community database + invented enemy loadouts)")
        self.assertGreater(float(data["spell_damage"][:, 1:].max()), 0.0)

    def test_a_missing_table_refuses_rather_than_inventing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                TouchData(pathlib.Path(tmp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
