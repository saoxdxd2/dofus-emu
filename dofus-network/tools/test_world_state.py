"""The world-state encoder: closed schema, named blocks, no silent collapse.

The tests that matter are the ones that would have caught the incumbent's
failure — a monster group encoded as one float, and a field arriving from the
client that nothing reads.
"""

import json
import unittest

try:
    from agent import world_state as ws
except ImportError:  # Also support: python tools/test_world_state.py
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from agent import world_state as ws


def obs(**overrides):
    """A minimal observation that the closed schema accepts without unknowns."""
    base = {
        "level": 12,
        "hp": {"life": 50, "max": 100, "ap": 6, "mp": 3},
        "kamas": 1500,
        "inventory": [{"gid": 1}, {"gid": 2}],
        "in_dialog": False,
        "in_fight": False,
        "alive": True,
        "tutorial": False,
        "ready_objectives": [],
        "quests_active": {},
        "position": {"cell": 100, "x": 5, "y": 7, "map_id": 147062786},
        "map": {"map_id": 147062786, "cell": 100, "npcs": [], "interactives": [],
                "groups": [], "exits": [], "doors": []},
    }
    base.update(overrides)
    return base


class NumberTests(unittest.TestCase):
    def test_numeric_strings_count(self):
        # Quest ids arrive as strings; reading them as 0 zeroed a whole feature.
        self.assertEqual(ws._num("3841"), 3841.0)
        self.assertEqual(ws._num(" 12 "), 12.0)

    def test_junk_is_zero(self):
        for value in (None, "abc", [], {}, object()):
            with self.subTest(value=value):
                self.assertEqual(ws._num(value), 0.0)

    def test_bool_is_not_a_number(self):
        self.assertEqual(ws._num(True), 1.0)
        self.assertEqual(ws._num(False), 0.0)


class RecordTests(unittest.TestCase):
    def test_position_wins_over_the_map_block(self):
        state = ws.from_observation(obs(
            position={"cell": 42, "x": 3, "y": 3, "map_id": 9},
            map={"cell": 100, "map_id": 1}))
        self.assertEqual((state.cell, state.map_id), (42.0, 9.0))

    def test_the_map_block_is_the_fallback(self):
        # 9 observations carry one carrier and not the other.
        state = ws.from_observation(obs(position={}, map={"cell": 77, "map_id": 5}))
        self.assertEqual((state.cell, state.map_id), (77.0, 5.0))

    def test_a_malformed_observation_never_raises(self):
        for value in (None, [], "x", 3, {"map": "not a dict"}):
            with self.subTest(value=value):
                state = ws.from_observation(value)
                self.assertEqual(state.npcs, [])
                self.assertEqual(ws.encode(state)["counts"]["npc"], 0)

    def test_extra_keeps_unrecognised_keys(self):
        state = ws.from_observation(obs(something_new={"a": 1}))
        self.assertEqual(state.extra, {"something_new": {"a": 1}})

    def test_to_dict_is_json_serialisable(self):
        state = ws.from_observation(obs(
            map={"map_id": 1, "npcs": [{"npc_id": 3888, "cell": 5}],
                 "exits": [{"direction": "left", "cells": [1, 2], "blocked": True}]}))
        text = json.dumps(state.to_dict(), ensure_ascii=False)
        self.assertIn("3888", text)


class EncodingTests(unittest.TestCase):
    def test_every_block_matches_its_feature_names(self):
        # The invariant that keeps a name list honest: a block whose width drifts
        # from its names makes every consumer's index wrong.
        encoded = ws.encode(ws.from_observation(obs()))
        self.assertEqual(len(encoded["global"]), len(ws.GLOBAL_FEATURES))
        for kind, features in ws.BLOCK_FEATURES.items():
            self.assertEqual(encoded["dims"][kind], len(features))
            for row in encoded["blocks"][kind]:
                self.assertEqual(len(row), len(features))

    def test_a_group_is_more_than_its_distance(self):
        """The incumbent encoded a group as (count, id % 7, first level).

        Two groups with the same cell but different composition must not encode
        identically -- that is the failure this module exists to fix.
        """
        weak = ws.from_observation(obs(map={
            "groups": [{"cell": 100, "member_count": 1, "total_level": 20,
                        "max_level": 20, "is_boss": False,
                        "monster_ids": [10]}]}))
        strong = ws.from_observation(obs(map={
            "groups": [{"cell": 100, "member_count": 4, "total_level": 240,
                        "max_level": 80, "is_boss": True,
                        "monster_ids": [10, 11, 12, 13]}]}))
        a = ws.encode(weak)["blocks"]["group"][0]
        b = ws.encode(strong)["blocks"]["group"][0]
        self.assertNotEqual(a, b)
        names = ws.GROUP_FEATURES
        self.assertGreater(b[names.index("members_over_8")], a[names.index("members_over_8")])
        self.assertGreater(b[names.index("total_level_over_400")],
                           a[names.index("total_level_over_400")])
        self.assertEqual(b[names.index("is_boss")], 1.0)
        self.assertEqual(a[names.index("is_boss")], 0.0)

    def test_levels_come_from_members_when_totals_are_absent(self):
        state = ws.from_observation(obs(map={"groups": [{
            "cell": 1, "members": [{"level": 40}, {"level": 60}]}]}))
        row = ws.encode(state)["blocks"]["group"][0]
        names = ws.GROUP_FEATURES
        self.assertAlmostEqual(row[names.index("total_level_over_400")], 100.0 / 400.0)
        self.assertAlmostEqual(row[names.index("max_level_over_200")], 60.0 / 200.0)

    def test_a_quest_id_from_a_string_key_survives(self):
        state = ws.from_observation(obs(quests_active={"3841": 0},
                                        quest_steps={"3841": 4531}))
        row = ws.encode(state)["blocks"]["quest"][0]
        names = ws.QUEST_FEATURES
        self.assertGreater(row[names.index("quest_id_log")], 0.0)
        self.assertGreater(row[names.index("current_step_id_log")], 0.0)

    def test_the_exit_anchor_is_the_nearest_walk_cell(self):
        # Player at cell 100 -> (2, 7). Cell 3 -> (3, 0); cell 86 -> (2, 6).
        state = ws.from_observation(obs(position={"cell": 100}, map={
            "exits": [{"direction": "top", "cells": [3, 86], "blocked": False}]}))
        row = ws.encode(state)["blocks"]["exit"][0]
        names = ws.EXIT_FEATURES
        # (2,6) is one row away; (3,0) is seven. The anchor must be the near one.
        self.assertLess(row[names.index("manhattan")], 0.2)
        self.assertEqual(row[names.index("dir_top")], 1.0)

    def test_dropped_counts_what_did_not_fit(self):
        many = [{"npc_id": i, "cell": i} for i in range(ws.SLOTS["npc"] + 3)]
        encoded = ws.encode(ws.from_observation(obs(map={"npcs": many})))
        self.assertEqual(encoded["dropped"]["npc"], 3)
        self.assertEqual(len(encoded["blocks"]["npc"]), ws.SLOTS["npc"])

    def test_nothing_is_dropped_for_the_measured_maximum(self):
        many = [{"npc_id": i, "cell": i} for i in range(4)]
        encoded = ws.encode(ws.from_observation(obs(map={"npcs": many})))
        self.assertEqual(encoded["dropped"]["npc"], 0)


class TerrainTests(unittest.TestCase):
    def test_the_packing_round_trips(self):
        # Four cells per hex digit, low bit first: walkable at 0 and 3 -> 0b1001.
        cells = [True, False, False, True]
        packed = "%x" % sum(1 << i for i, cell in enumerate(cells) if cell)
        self.assertEqual(packed, "9")
        self.assertEqual(ws.walkable_cells(packed), cells)

    def test_a_full_map_packs_into_one_string(self):
        cells = [i % 3 == 0 for i in range(560)]
        packed = "".join(
            "%x" % sum(1 << k for k in range(4)
                       if start + k < len(cells) and cells[start + k])
            for start in range(0, len(cells), 4))
        self.assertEqual(len(packed), 140)
        self.assertEqual(ws.walkable_cells(packed), cells)

    def test_malformed_terrain_yields_no_grid(self):
        for value in (None, "", "xyz", 5, []):
            with self.subTest(value=value):
                self.assertEqual(ws.walkable_cells(value), [])

    def test_the_cell_walkable_feature_reads_the_players_cell(self):
        # cell 4 is in the second hex digit; make it the only walkable one.
        packed = "01"  # cells 0-3 all blocked, cell 4 walkable
        state = ws.from_observation(obs(position={"cell": 4},
                                        map={"terrain": packed}))
        row = ws.encode(state)["global"]
        names = ws.GLOBAL_FEATURES
        self.assertEqual(row[names.index("cell_walkable")], 1.0)
        self.assertAlmostEqual(row[names.index("walkable_fraction")], 0.125)


class ClosedSchemaTests(unittest.TestCase):
    def test_a_new_client_field_is_reported_unknown(self):
        report = ws.coverage(obs(something_new=1))
        self.assertIn("something_new", report["unknown"])

    def test_a_new_entity_field_is_reported_unknown(self):
        report = ws.coverage(obs(map={"npcs": [{"npc_id": 1, "cell": 2, "mood": 3}]}))
        self.assertIn("npc.mood", report["unknown_entity"])

    def test_known_keys_are_not_unknown(self):
        report = ws.coverage(obs(map={"npcs": [{"npc_id": 1, "cell": 2}]}))
        self.assertEqual(report["unknown"], [])
        self.assertEqual(report["unknown_entity"], [])

    def test_ignored_keys_carry_a_reason(self):
        for key, reason in ws.IGNORED_TOP.items():
            with self.subTest(key=key):
                self.assertTrue(reason and len(reason) > 10, key)

    def test_actions_are_not_state(self):
        # Feeding the policy its own estimate back is a loop; user_input is
        # provenance for the label, not a property of the world.
        self.assertIn("fight_estimates", ws.IGNORED_TOP)
        self.assertIn("user_input", ws.IGNORED_TOP)
        self.assertNotIn("fight_estimates", ws.ENCODED_TOP)

    def test_audit_folds_counts(self):
        report = ws.audit([obs(), obs(something_new=1), obs(map={"npcs": [
            {"npc_id": 1, "cell": 1, "mood": 2}]})])
        self.assertEqual(report["observations"], 3)
        self.assertEqual(report["unknown"]["something_new"], 1)
        self.assertEqual(report["unknown_entity"]["npc.mood"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
