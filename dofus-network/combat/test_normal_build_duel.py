"""The duel benchmark must isolate the policy from the build.

Measured 2026-10-31 on the asymmetric pair (4825 HP Ecaflip vs 7500 HP Cra):
all five 20-game matchups ended 100% for side 2 with an identical 6.0 mean
turns, random and solver alike. The build decided that block, so it cannot
judge a decision. Mirror mode puts one build on both sides; the only remaining
asymmetry is the seat.
"""
import random
import unittest

from combat.duel import load_map
from combat.normal_build_duel import DEFAULT_MAP, make_normal_duel


class MirrorDuelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cells = load_map(DEFAULT_MAP)

    def test_mirror_builds_are_identical(self):
        state = make_normal_duel(self.cells, random.Random(3),
                                 mirror_key="dana_ouro")
        self.assertIsNotNone(state)
        a, b = state.fighters
        self.assertNotEqual(a.id, b.id)
        self.assertNotEqual(a.cell, b.cell)
        self.assertEqual(
            (a.hp, a.ap, a.mp, a.class_name, a.level, a.resist,
             tuple(s.ap_cost for s in a.spells)),
            (b.hp, b.ap, b.mp, b.class_name, b.level, b.resist,
             tuple(s.ap_cost for s in b.spells)))

    def test_default_pair_stays_asymmetric(self):
        state = make_normal_duel(self.cells, random.Random(3))
        self.assertIsNotNone(state)
        a, b = state.fighters
        self.assertNotEqual(a.hp, b.hp)


if __name__ == "__main__":
    unittest.main()
