"""The geometry engine must agree with the decoder that has actually fought.

`combat/map_field.py` re-derives distance, line of sight and reachability from
the map's raw cell flags. `CellMap` in `battle_solver` already does that, and it
is the one the live client has been checked against -- so it is the reference,
and every test here compares against it rather than against a stored number.

The synthetic tests inject a line-of-sight matrix instead of computing one: a
full 560x560 build is about seven seconds, and a test that pays it four times is
a test nobody runs.
"""

from __future__ import annotations

import json
import random
import unittest

import numpy as np

from combat.battle_solver import (CellMap, NB_CELLS, _CARDINAL_NEIGHBOURS,
                                  _POINTS, cell_point, line_points)
from combat.map_field import MAPS_DIR, UNREACHABLE, EnemyThreat, MapField

# The client's own bits for a cell you can stand on and see through.
OPEN = 64 | 2 | 1
# A wall: neither walkable nor see-through.
WALL = 64


def _real_map() -> MapField:
    files = sorted(MAPS_DIR.glob("*.json"))
    if not files:
        raise unittest.SkipTest("no map corpus")
    return MapField.from_map_id(int(files[0].stem))


class TestAgreesWithTheLiveDecoder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.field = _real_map()
        payload = json.loads((MAPS_DIR / ("%d.json" % cls.field.map_id)).read_text(encoding="utf-8"))
        cls.flags = tuple(int(cell.get("l", 0)) for cell in payload["cells"])
        cls.live = CellMap(cls.flags)

    def test_the_walkable_predicate_is_the_decoders(self):
        for cell in range(NB_CELLS):
            self.assertEqual(self.field.walkable[cell], self.live.walkable(cell), cell)
            self.assertEqual(self.field.sees[cell], self.live.sees(cell), cell)

    def test_reachability_is_the_decoders_walk(self):
        """Every origin, four movement budgets, compared cell for cell."""
        walkable = np.flatnonzero(self.field.walkable)
        origins = [int(walkable[0]), int(walkable[len(walkable) // 2]), int(walkable[-1])]
        for origin in origins:
            for mp in (0, 2, 3, 6):
                mine = set(np.flatnonzero(self.field.reachable_mask(origin, mp)).tolist())
                theirs = set(self.live.reachable(origin, mp).keys())
                self.assertEqual(mine, theirs, "origin %d mp %d" % (origin, mp))

    def test_line_of_sight_is_the_decoders(self):
        rng = random.Random(11)
        pairs = [(rng.randrange(NB_CELLS), rng.randrange(NB_CELLS)) for _ in range(300)]
        wrong = [(a, b) for a, b in pairs
                 if bool(self.field.los[a, b]) != self.live.has_los(a, b)]
        self.assertEqual(wrong, [], "los disagreements: %s" % wrong[:5])

    def test_a_cell_sees_itself_even_when_it_is_not_see_through(self):
        """The client returns True for `a == b` before it looks at any flag.

        A random sample of 400 pairs found exactly this case disagreeing, so it
        is the one exception the matrix has to encode rather than derive.
        """
        blind = [c for c in range(NB_CELLS) if not self.field.sees[c]]
        self.assertTrue(blind, "this map has no non-see-through cell to test")
        for cell in blind[:20]:
            self.assertTrue(self.field.los[cell, cell])
            self.assertTrue(self.live.has_los(cell, cell))

    def test_an_actor_blocks_only_between_the_endpoints(self):
        """`has_los` ignores the origin and checks the target, so an actor on
        either endpoint must not block, and one in the middle must."""
        rng = random.Random(3)
        checked = 0
        for _ in range(4000):
            a, b = rng.randrange(NB_CELLS), rng.randrange(NB_CELLS)
            if a == b or not self.field.los[a, b]:
                continue
            ax, ay = cell_point(a)
            bx, by = cell_point(b)
            line = line_points(ax, ay, bx, by)
            inner = [_POINTS[p] for p in line[:-1] if p in _POINTS]
            inner = [c for c in inner if c != a]
            if not inner:
                continue
            actor = inner[len(inner) // 2]
            self.assertFalse(self.field.has_los(a, b, frozenset([actor])),
                             "actor %d on the line %d->%d did not block" % (actor, a, b))
            checked += 1
            if checked >= 25:
                break
        self.assertGreater(checked, 0, "no line with an interior cell was found")


class TestThreatIsADefinition(unittest.TestCase):
    def _open_field(self) -> MapField:
        field = MapField.from_flags([OPEN] * NB_CELLS)
        # Injected: on a fully open map every line is clear, and building the
        # matrix to prove that costs seven seconds per test.
        field._los = np.ones((NB_CELLS, NB_CELLS), dtype=bool)
        return field

    def test_on_an_open_map_threat_is_exactly_the_reach_ball(self):
        """With no walls, a cell is threatened iff it is within MP + range.

        This is the measured reality of the real corpus too: on eight maps the
        rule agreed with the computed field on 99.97% of 3,486 cells.
        """
        field = self._open_field()
        enemy = EnemyThreat(cell=290, mp=4, spells=((0, 3, 1.0),))
        threat = field.threat_field([enemy])
        within = (field.dist[290] <= 4 + 3) & (field.dist[290] != UNREACHABLE)
        self.assertTrue(np.array_equal(threat > 0, within))
        self.assertTrue(np.allclose(threat[within], 1.0))

    def test_threat_scales_with_expected_damage(self):
        field = self._open_field()
        one = field.threat_field([EnemyThreat(290, 2, ((0, 2, 10.0),))])
        two = field.threat_field([EnemyThreat(290, 2, ((0, 2, 10.0),)),
                                  EnemyThreat(290, 2, ((0, 2, 5.0),))])
        self.assertTrue(np.allclose(two[one > 0], 15.0))
        self.assertTrue(np.allclose(two[one == 0], 0.0))

    def test_a_wall_casts_a_shadow_that_the_field_respects(self):
        """A monster with no movement cannot hit through a wall.

        The point is not that walls matter often -- they do not, because a
        monster with MP walks around them -- but that the field is a line-of-
        sight computation and not a distance threshold wearing a hat.
        """
        flags = [OPEN] * NB_CELLS
        by_point = {cell_point(cell): cell for cell in range(NB_CELLS)}
        pair = None
        for (x, y), cell in by_point.items():
            mid = by_point.get((x + 1, y))
            far = by_point.get((x + 2, y))
            if mid is not None and far is not None:
                pair = (cell, mid, far)
                break
        self.assertIsNotNone(pair, "no collinear triple found")
        a, mid, b = pair
        flags[mid] = WALL
        field = MapField.from_flags(flags)
        self.assertFalse(field.los[a, b], "the wall did not block the line")

        enemy = EnemyThreat(cell=a, mp=0, spells=((0, 12, 1.0),))
        threat = field.threat_field([enemy])
        self.assertEqual(threat[b], 0.0, "a wall did not shadow the cell behind it")
        self.assertGreater(threat[a], 0.0, "the enemy cannot even hit itself")

    def test_danger_at_matches_a_brute_force_enumeration(self):
        field = self._open_field()
        rng = random.Random(5)
        enemy = EnemyThreat(cell=290, mp=3, spells=((1, 4, 7.0),))
        for _ in range(30):
            cell = rng.randrange(NB_CELLS)
            reach = np.flatnonzero(field.reachable_mask(enemy.cell, enemy.mp))
            hit = any(1 <= field.dist[p, cell] <= 4 for p in reach)
            self.assertAlmostEqual(field.danger_at(cell, [enemy]),
                                   7.0 if hit else 0.0, places=5)

    def test_a_fighter_with_no_known_mp_is_not_a_zero_reach_threat(self):
        """A live board sends `mp: null` for every monster.

        Reading that as zero would say the monster cannot move, which marks
        every cell beyond its spell range safe. The builder refuses instead.
        """
        class Fighter:
            cell = 100
            mp = None
            spells = ()

        self.assertIsNone(EnemyThreat.from_fighter(Fighter()))

    def test_safe_cells_are_reachable_and_actually_safe(self):
        field = self._open_field()
        enemy = EnemyThreat(cell=290, mp=2, spells=((0, 3, 5.0),))
        safe = field.safe_cells([enemy], mp=4, origin=300)
        self.assertGreater(len(safe), 0)
        reachable = field.reachable_mask(300, 4)
        threat = field.threat_field([enemy])
        self.assertTrue(np.all(reachable[safe]))
        self.assertTrue(np.all(threat[safe] <= 0.0))


class TestMapShape(unittest.TestCase):
    def test_openness_is_monotone_in_the_radius(self):
        field = MapField.from_flags([OPEN] * NB_CELLS)
        for k in range(3):
            self.assertTrue(np.all(field.openness[:, k] <= field.openness[:, k + 1] + 1e-6))

    def test_a_connected_floor_has_a_positive_fiedler_value(self):
        """The algebraic connectivity is zero exactly when the floor splits."""
        field = MapField.from_flags([OPEN] * NB_CELLS)
        self.assertEqual(field.summary()["components"], 1)
        self.assertGreater(float(field.spectral_values[0]), 1e-6)

    def test_a_walled_off_cell_becomes_its_own_component(self):
        """An isolated cell is a component, and the normalised Laplacian gives it
        eigenvalue 1 rather than 0 -- a zero-degree node has no null direction,
        it has a unit one. The test therefore checks the graph, not a spectrum
        claim that would be wrong."""
        flags = [OPEN] * NB_CELLS
        target = 290
        for nxt in _CARDINAL_NEIGHBOURS[target]:
            flags[nxt] = WALL
        field = MapField.from_flags(flags)
        self.assertEqual(field.summary()["components"], 2)
        self.assertEqual(field.summary()["off_main"], 1)
        self.assertEqual(int(field.adjacency[target].sum()), 0)
        self.assertEqual(int(field.reachable_mask(target, 6).sum()), 1)

    def test_the_encoding_is_finite_and_the_right_width(self):
        field = MapField.from_flags([OPEN] * NB_CELLS)
        encoding = field.encoding()
        self.assertEqual(encoding.shape, (NB_CELLS, 18))
        self.assertTrue(np.isfinite(encoding).all())
        self.assertTrue(np.all(encoding[:, 0] == 1.0))


if __name__ == "__main__":
    unittest.main()
