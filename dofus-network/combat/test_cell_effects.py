"""Trap and summon valuation, and the three gates that hid them.

The code that prices a trap and a summon was already in `battle_solver` -- the
`is_trap`/`is_summon` branches, the `is_trap_placement` flag, the beam's
handling of both. None of it could run, for three separate reasons found by
handing `plan_candidates` a board that actually had such a spell:

1. `_templates` dropped any spell with no damage, no heal and no *planned*
   mechanic -- and `PLANNED_MECHANIC_KINDS` is movement and AP/MP only. A trap
   produced zero templates on every board.
2. The beam then indexed `node.hp[template.target_id]`, and a placement carries
   `target_id=0` because it names a cell. That was a `KeyError`, so a trap on
   the board took the whole search down.
3. Both were priced with a constant (1.0 and 15.0), so even once reachable they
   lost the sort to any direct hit and were pruned before the candidate list.

Each of those is pinned here, plus the geometry the valuation rests on.
"""

from __future__ import annotations

import random
import sys
import unittest
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from combat import cell_effects as ce  # noqa: E402
from combat.battle_solver import Board, BattleSolver  # noqa: E402
from combat.map_field import UNREACHABLE, field_for  # noqa: E402

MAP_ID = 100139008


def _board():
    from combat import duel_value as dv
    from combat.duel import DEFAULT_DATA, board_for, make_duel
    from combat.duel_benchmark import load_data, sample_pair
    from combat.normal_build_duel import DEFAULT_MAP

    cells = dv._load_cells(DEFAULT_MAP)
    data, levels = load_data(DEFAULT_DATA)
    rng = random.Random(3)
    for _ in range(30):
        row_a, row_b = sample_pair(levels, list(range(4096)), rng)
        state = make_duel(data, row_a, row_b, cells, rng)
        if state is not None and not state.done:
            return board_for(state, state.actor)
    raise unittest.SkipTest("no duel could be built")


def _spell(board, spell_id, name, effect_id, ap=3, span=6):
    """A synthetic spell parsed the way the live payload is parsed.

    `range`, not `max_range`: `Board._spell` reads the client's own key name, and
    a synthetic dict using the wrong one yields `max_range=0`, no range cells and
    no templates -- which reads exactly like the gate still dropping traps.
    """
    raw = {
        "id": spell_id, "name": name, "ap_cost": ap, "min_range": 1,
        "range": span, "cast_in_line": False, "cast_in_diagonal": False,
        "requires_los": True, "need_free_cell": False, "need_taken_cell": False,
        "max_cast_per_turn": None, "max_cast_per_target": None,
        "min_cast_interval": 0, "casts_this_turn": 0, "castable": True,
        "critical_hit_probability": None,
        "effects": [{"effect_id": effect_id, "min": 40, "max": 40}],
        "critical_effects": [],
    }
    base = board.spells[0]
    return replace(Board._spell(raw), zone_size=base.zone_size,
                   zone_shape=base.zone_shape, is_weapon=False)


class GeometryTests(unittest.TestCase):
    """`cell_effects` on a real map, not on a fixture."""

    @classmethod
    def setUpClass(cls):
        cls.field = field_for(MAP_ID)
        cls.walk = [c for c in range(cls.field.walkable.shape[0])
                    if cls.field.walkable[c]]

    def test_a_path_has_the_bfs_length_and_is_contiguous(self):
        a, b = self.walk[0], self.walk[200]
        path = ce._path_cells(self.field, a, b)
        self.assertTrue(path, "no path between two walkable cells")
        self.assertEqual(path[0], a)
        self.assertEqual(path[-1], b)
        self.assertEqual(len(path) - 1, int(self.field.dist[a, b]))
        for first, second in zip(path, path[1:]):
            self.assertEqual(int(self.field.dist[first, second]), 1,
                             "%d -> %d is not one step" % (first, second))

    def test_a_cell_with_no_path_returns_nothing_rather_than_a_straight_line(self):
        off = [c for c in range(self.field.walkable.shape[0])
               if not self.field.walkable[c]]
        if not off:
            self.skipTest("every cell on this map is walkable")
        self.assertEqual(ce._path_cells(self.field, off[0], self.walk[0]), ())

    def test_traffic_is_one_on_the_route_and_zero_off_it(self):
        enemies, allies = [self.walk[50]], [self.walk[400]]
        path = ce._path_cells(self.field, enemies[0], allies[0])
        if not path:
            self.skipTest("no path")
        weights = dict(zip(self.walk,
                           ce.corridor_weights(self.field, self.walk,
                                               enemies, allies)))
        for cell in path:
            self.assertAlmostEqual(weights[cell], 1.0, places=6)
        off_path = [c for c in self.walk if c not in set(path)]
        self.assertTrue(all(weights[c] == 0.0 for c in off_path))

    def test_the_normaliser_is_per_pair_not_per_cell(self):
        """One pair puts 1.0 on its route; the same pair twice is still 1.0.

        This is the property that makes the number a *ratio* rather than a
        count: two enemies instead of one must not double every cell's score,
        or a bigger fight would always look like a better trap site. The
        overlap case below is the one where the ratio really exceeds 1.
        """
        enemy, ally = self.walk[50], self.walk[400]
        path = set(ce._path_cells(self.field, enemy, ally))
        if not path:
            self.skipTest("no path on this map")
        once = dict(zip(self.walk, ce.corridor_weights(
            self.field, self.walk, [enemy], [ally])))
        twice = dict(zip(self.walk, ce.corridor_weights(
            self.field, self.walk, [enemy, enemy], [ally])))
        self.assertEqual(once, twice)
        cell = sorted(path)[len(path) // 2]
        self.assertAlmostEqual(once[cell], 1.0, places=6)
        off_path = [c for c in self.walk if c not in path]
        self.assertTrue(all(once[c] == 0.0 for c in off_path))

    def test_choke_prefers_a_cell_between_the_two(self):
        field = self.field
        enemy = self.walk[100]
        ally = self.walk[400]
        path = ce._path_cells(field, enemy, ally)
        if len(path) < 6:
            self.skipTest("path too short to have an interior")
        middle = path[len(path) // 2]
        far = self.walk[0]
        between = ce.choke_bonus(field, middle, [ally], [enemy])
        away = ce.choke_bonus(field, far, [ally], [enemy])
        self.assertGreaterEqual(between, away)

    def test_choke_is_zero_on_a_cell_somebody_occupies(self):
        self.assertEqual(
            ce.choke_bonus(self.field, self.walk[0], [self.walk[0]],
                           [self.walk[200]]), 0.0)


class TrapIsReachableTests(unittest.TestCase):
    """Gate 1: a trap spell must produce templates at all."""

    @staticmethod
    def _templates_for(spell, board=None):
        """Templates for a board that *has* `spell` on it.

        The spell has to be added to the board before `_templates` is asked:
        passing the original board measured an ordinary board and found no
        traps for the trivial reason that there were none.
        """
        board = board or _board()
        armed = replace(board, spells=list(board.spells) + [spell])
        solver = BattleSolver(beam_width=8, max_actions=3)
        occupied = frozenset(f.cell for f in armed.fighters
                             if f.alive and f.id != armed.me.id)
        return solver._templates(armed, armed.me.cell, armed.me, occupied)

    def test_a_trap_spell_produces_templates(self):
        board = _board()
        trap = _spell(board, 9001, "Piege Test", 400)
        self.assertTrue(trap.is_trap, "the synthetic spell is not a trap")
        traps = [t for t in self._templates_for(trap)
                 if t.is_trap_placement]
        self.assertTrue(traps, "a trap on the board produced no template")

    def test_a_summon_spell_produces_templates(self):
        board = _board()
        summon = _spell(board, 9002, "Invocation Test", 180)
        self.assertTrue(summon.is_summon)
        summons = [t for t in self._templates_for(summon) if t.is_summon]
        self.assertTrue(summons, "a summon on the board produced no template")

    def test_trap_utility_varies_with_the_cell(self):
        """Gate 3: a flat constant cannot rank two placements."""
        board = _board()
        trap = _spell(board, 9001, "Piege Test", 400)
        traps = [t for t in self._templates_for(trap) if t.is_trap_placement]
        self.assertGreater(len({round(t.utility, 3) for t in traps}), 1,
                           "every trap cell scored the same: %r"
                           % [t.utility for t in traps])
        self.assertGreater(max(t.utility for t in traps), 1.0,
                           "the best trap cell is at the floor, so traffic is 0")


class PlacementSurvivesTheBeamTests(unittest.TestCase):
    """Gate 2: `target_id=0` must not crash the expansion, and the plan must
    be offered to the ranker."""

    def _plans_with(self, spell):
        board = _board()
        armed = replace(board, spells=list(board.spells) + [spell])
        solver = BattleSolver(beam_width=8, max_actions=3)
        return solver.plan_candidates(armed, 6)

    def test_a_trap_plan_is_offered_without_raising(self):
        board = _board()
        trap = _spell(board, 9001, "Piege Test", 400)
        plans = self._plans_with(trap)
        used = [p for p in plans if any(a.spell_id == 9001 for a in p.actions)]
        self.assertTrue(used, "the trap never reached the candidate list")

    def test_a_summon_plan_is_offered_without_raising(self):
        board = _board()
        summon = _spell(board, 9002, "Invocation Test", 180)
        plans = self._plans_with(summon)
        used = [p for p in plans if any(a.spell_id == 9002 for a in p.actions)]
        self.assertTrue(used, "the summon never reached the candidate list")

    def test_the_placement_plan_is_bucketed_as_setup(self):
        """A trap/summon is a setup turn, and the ranker reads `bucket`."""
        board = _board()
        trap = _spell(board, 9001, "Piege Test", 400)
        plans = self._plans_with(trap)
        used = [p for p in plans if any(a.spell_id == 9001 for a in p.actions)]
        if not used:
            self.skipTest("trap not reached")
        self.assertIn("setup", {p.bucket for p in used})

    def test_the_damage_plans_are_not_displaced(self):
        """Offering a setup turn must not cost the best plan its first place."""
        board = _board()
        plain = BattleSolver(beam_width=8, max_actions=3).plan_candidates(board, 6)
        trap = _spell(board, 9001, "Piege Test", 400)
        with_trap = self._plans_with(trap)
        self.assertAlmostEqual(with_trap[0].score, plain[0].score, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
