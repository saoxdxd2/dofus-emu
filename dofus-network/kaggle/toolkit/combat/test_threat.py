"""Tests for the positional threat model.

The fixtures are literal: cell flags, monster levels and AP are written the way
the client sends them. The numbers that matter are the *relative* ones -- a
covered cell must price below an open one, and a monster out of range must not
be counted at all -- because the solver only ever compares two cells on the same
board.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from combat import threat
from combat.battle_solver import (
    NB_CELLS,
    BattleSolver,
    Board,
    CellMap,
    Fighter,
    LiveEffect,
    LiveSpell,
    _POINTS,
    cell_point,
)

OPEN_FLAGS = tuple([3] * NB_CELLS)
# A wall that blocks the line but not the walk: bit 1 is line of sight, and the
# client's `has_los` requires it on every cell of the line.
WALL_FLAGS = tuple([1] * NB_CELLS)


def cell(x: int, y: int) -> int:
    return _POINTS[(x, y)]


def away(start: int, distance: int) -> int:
    """A real cell ``distance`` steps from ``start``.

    The grid spans x 0-32 and y 0-33 but not every pair, so a test that writes
    ``cell(x + 9, y)`` is a test that fails on the fixture rather than on the
    code. This picks whichever of the four cardinal offsets exists.
    """
    x, y = cell_point(start)
    for dx, dy in ((distance, 0), (0, distance), (-distance, 0), (0, -distance)):
        found = _POINTS.get((x + dx, y + dy))
        if found is not None:
            return found
    raise AssertionError("no cell %d steps from %d" % (distance, start))


def monster(**overrides) -> Fighter:
    fields = dict(
        id=-1, name="Clobberstone", cell=0, team="enemy", hp=120, max_hp=120,
        ap=6, mp=3, shield=0, level=8, alive=True, by_me=False,
        resist_percent=(), resist_fixed=(), critical_resistance=0,
    )
    fields.update(overrides)
    return Fighter(**fields)


def me(**overrides) -> Fighter:
    fields = dict(
        id=4404438, name="Myai", cell=0, team="player", hp=140, max_hp=140,
        ap=6, mp=2, shield=0, level=12, alive=True, by_me=True,
        resist_percent=(), resist_fixed=(), critical_resistance=0,
    )
    fields.update(overrides)
    return Fighter(**fields)


def board_with(fighters, cells=None, my_cell=None) -> Board:
    mine = next(f for f in fighters if f.team == "player")
    return Board(
        map_id=1, in_fight=True, in_preparation=False, is_my_turn=True,
        my_id=mine.id, my_cell=my_cell if my_cell is not None else mine.cell,
        my_ap=6, my_mp=2, fighters=list(fighters), spells=[],
        my_stats={}, cells=cells if cells is not None else CellMap(OPEN_FLAGS))


def spell(**overrides) -> LiveSpell:
    fields = dict(
        id=61, name="Strike", ap_cost=3, min_range=1, max_range=5,
        cast_in_line=False, cast_in_diagonal=False, requires_los=True,
        need_free_cell=False, need_taken_cell=False, max_cast_per_turn=2,
        max_cast_per_target=2, min_cast_interval=0, casts_this_turn=0,
        castable=True, critical_hit_probability=0.0,
        effects=(LiveEffect(97, 20, 20),), critical_effects=(), is_weapon=False,
        zone_size=None, zone_shape=None,
    )
    fields.update(overrides)
    return LiveSpell(**fields)


class PriorTests(unittest.TestCase):
    def test_a_monster_without_published_spells_uses_the_level_prior(self):
        model = threat.build(board_with([me(cell=cell(20, 20)),
                                         monster(cell=cell(24, 20))]))
        profile = model.enemies[0]
        self.assertEqual(profile.source, "prior")
        self.assertAlmostEqual(profile.damage, 8 * threat.PRIOR_DAMAGE_PER_LEVEL)
        self.assertEqual(profile.reach, threat.PRIOR_RANGE + 3)

    def test_a_monster_with_a_published_spell_is_priced_from_it(self):
        foe = monster(cell=cell(24, 20), spells=(spell(max_range=3),))
        model = threat.build(board_with([me(cell=cell(20, 20)), foe]))
        profile = model.enemies[0]
        self.assertEqual(profile.source, "spells")
        self.assertGreater(profile.damage, 0)
        # 3 AP spell with 6 AP is two casts, and two casts is the cap.
        self.assertEqual(profile.hits, 2)
        self.assertEqual(profile.reach, 3 + 3)

    def test_the_cast_count_is_bounded_by_ap(self):
        foe = monster(cell=cell(24, 20), ap=3, spells=(spell(ap_cost=3),))
        model = threat.build(board_with([me(cell=cell(20, 20)), foe]))
        self.assertEqual(model.enemies[0].hits, 1)

    def test_dead_and_friendly_fighters_are_not_threats(self):
        board = board_with([
            me(cell=cell(20, 20)),
            monster(id=-1, cell=cell(21, 20), alive=False),
            monster(id=-2, cell=cell(19, 20), team="player"),
        ])
        self.assertEqual(threat.build(board).enemies, [])


class RangeTests(unittest.TestCase):
    def test_a_monster_out_of_reach_prices_nothing(self):
        base = cell(20, 20)
        near = away(base, 2)
        far = away(base, 10)
        model = threat.build(board_with([me(cell=far), monster(cell=near)]))
        self.assertEqual(model.at(far), 0.0)

    def test_mp_extends_the_reach_of_a_monster(self):
        # Level 8 with 3 MP reaches PRIOR_RANGE + 3 = 8 cells, so 9 is out.
        base = cell(20, 20)
        edge = away(base, threat.PRIOR_RANGE + 3)
        beyond = away(base, threat.PRIOR_RANGE + 4)
        foe = monster(cell=base)
        self.assertGreater(threat.build(board_with([me(cell=edge), foe])).at(edge), 0.0)
        self.assertEqual(
            threat.build(board_with([me(cell=beyond), foe])).at(beyond), 0.0)

    def test_exposure_stacks_with_the_number_of_reachable_enemies(self):
        mine = me(cell=cell(20, 20))
        one = board_with([mine, monster(id=-1, cell=cell(23, 20))])
        two = board_with([mine, monster(id=-1, cell=cell(23, 20)),
                          monster(id=-2, cell=cell(20, 23))])
        target = cell(21, 20)
        self.assertAlmostEqual(threat.build(two).at(target),
                               2 * threat.build(one).at(target))


class CoverTests(unittest.TestCase):
    def test_cover_prices_below_the_open_for_a_monster_that_needs_a_line(self):
        # A walled map: `has_los` requires bit 1 on every cell of the line, so
        # nothing can see anything. Only a monster with no MP left is bound by
        # that -- one that can walk repositions first, which is why the test
        # pins mp to zero.
        foe = monster(cell=cell(20, 20), mp=0, spells=(spell(requires_los=True),))
        board = board_with([me(cell=cell(24, 20)), foe], cells=CellMap(WALL_FLAGS))
        model = threat.build(board)
        self.assertEqual(model.at(cell(24, 20)), 0.0)

    def test_a_monster_with_movement_ignores_cover(self):
        foe = monster(cell=cell(20, 20), mp=3, spells=(spell(requires_los=True),))
        board = board_with([me(cell=cell(24, 20)), foe], cells=CellMap(WALL_FLAGS))
        self.assertGreater(threat.build(board).at(cell(24, 20)), 0.0)

    def test_a_los_free_monster_is_priced_through_the_wall(self):
        foe = monster(cell=cell(20, 20), mp=0, spells=(spell(requires_los=False),))
        board = board_with([me(cell=cell(24, 20)), foe], cells=CellMap(WALL_FLAGS))
        self.assertGreater(threat.build(board).at(cell(24, 20)), 0.0)

    def test_open_ground_prices_every_cell_the_same(self):
        model = threat.build(board_with([me(cell=cell(20, 20)),
                                         monster(cell=cell(20, 20), mp=0)]))
        self.assertEqual(model.at(cell(21, 20)), model.at(cell(20, 21)))


class SwingTests(unittest.TestCase):
    def test_walking_in_is_priced_positive_and_walking_out_negative(self):
        base = cell(20, 20)
        outside = away(base, threat.PRIOR_RANGE + 4)
        inside = away(base, 2)
        model = threat.build(board_with([me(cell=outside), monster(cell=base)]))
        self.assertGreater(model.swing(outside, inside), 0.0)
        self.assertLess(model.swing(inside, outside), 0.0)

    def test_the_swing_is_capped_so_a_crowd_cannot_veto_attacking(self):
        base = cell(20, 20)
        crowd = [monster(id=-i, cell=away(base, i), level=200, ap=12)
                 for i in range(1, 6)]
        board = board_with([me(cell=base)] + crowd)
        model = threat.build(board)
        self.assertEqual(model.swing(base, away(base, 12)),
                         -threat.MAX_PRICED_SWING)

    def test_staying_put_costs_nothing(self):
        model = threat.build(board_with([me(cell=cell(20, 20)),
                                         monster(cell=cell(21, 20))]))
        self.assertEqual(model.swing(cell(20, 20), cell(20, 20)), 0.0)

    def test_no_model_means_no_price(self):
        self.assertEqual(threat.risk(cell(20, 20), cell(21, 20), None), 0.0)


class MemoryTests(unittest.TestCase):
    def test_a_learned_row_replaces_the_level_prior(self):
        memory = threat.ThreatMemory()
        memory.record("Clobberstone", damage=31.0, distance=4.0)
        model = threat.build(board_with([me(cell=cell(20, 20)),
                                         monster(cell=cell(24, 20))]), memory)
        profile = model.enemies[0]
        self.assertEqual(profile.source, "learned")
        self.assertAlmostEqual(profile.damage, 31.0)

    def test_the_learned_range_is_a_maximum_not_a_mean(self):
        memory = threat.ThreatMemory()
        for distance in (2.0, 7.0, 3.0):
            memory.record("Clobberstone", damage=10.0, distance=distance)
        self.assertEqual(memory.profile("Clobberstone")["range"], 7.0)

    def test_one_long_hit_does_not_turn_a_monster_into_a_sniper(self):
        memory = threat.ThreatMemory()
        memory.record("Clobberstone", damage=10.0, distance=20.0)
        model = threat.build(board_with([me(cell=cell(20, 20)),
                                         monster(cell=cell(24, 20))]), memory)
        self.assertEqual(model.enemies[0].reach, threat.PRIOR_RANGE + 3)

    def test_a_learned_range_replaces_the_prior_once_it_has_a_pattern(self):
        memory = threat.ThreatMemory()
        for _ in range(threat.RANGE_SAMPLES):
            memory.record("Clobberstone", damage=10.0, distance=2.0)
        model = threat.build(board_with([me(cell=cell(20, 20)),
                                         monster(cell=cell(24, 20))]), memory)
        self.assertEqual(model.enemies[0].reach, 2 + 3)

    def test_a_learned_range_cannot_exceed_the_prior_by_more_than_the_slack(self):
        memory = threat.ThreatMemory()
        for _ in range(threat.RANGE_SAMPLES):
            memory.record("Clobberstone", damage=10.0, distance=25.0)
        model = threat.build(board_with([me(cell=cell(20, 20)),
                                         monster(cell=cell(24, 20))]), memory)
        self.assertEqual(model.enemies[0].reach,
                         threat.PRIOR_RANGE + threat.RANGE_SLACK + 3)

    def test_a_monster_of_the_same_name_shares_one_row(self):
        memory = threat.ThreatMemory()
        memory.record("Clobberstone", damage=10.0)
        memory.record("Clobberstone", damage=20.0)
        row = memory.profile("Clobberstone")
        self.assertEqual(row["samples"], 2.0)
        self.assertAlmostEqual(row["damage"], 15.0)

    def test_zero_damage_and_blank_names_are_not_recorded(self):
        memory = threat.ThreatMemory()
        memory.record("", damage=10.0)
        memory.record("Clobberstone", damage=0.0)
        self.assertEqual(memory.monsters, {})

    def test_a_memory_round_trips_through_json(self):
        memory = threat.ThreatMemory()
        memory.record("Clobberstone", damage=12.0, distance=5.0, hits=2)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "threat_model.json"
            memory.save(path)
            again = threat.ThreatMemory.load(path)
        self.assertAlmostEqual(again.profile("Clobberstone")["damage"], 12.0)
        self.assertEqual(again.profile("Clobberstone")["range"], 5.0)

    def test_a_missing_or_broken_file_loads_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "absent.json"
            self.assertEqual(threat.ThreatMemory.load(path).monsters, {})
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(threat.ThreatMemory.load(path).monsters, {})

    def test_observe_attributes_a_loss_to_the_last_cast_by_that_monster(self):
        memory = threat.ThreatMemory()
        log = [
            {"type": "GameActionFightSpellCastMessage", "source": -1,
             "spell": 7041, "cell": cell(20, 20), "destination_cell": cell(24, 20)},
            {"type": "GameActionFightLifePointsLostMessage", "source": -1,
             "target": 4404438, "loss": 26},
            # A second cast by the same monster, at a different range.
            {"type": "GameActionFightSpellCastMessage", "source": -1,
             "spell": 7041, "cell": cell(21, 20), "destination_cell": cell(24, 20)},
            {"type": "GameActionFightLifePointsLostMessage", "source": -1,
             "target": 4404438, "loss": 18},
        ]
        learned = memory.observe(log, 4404438, {-1: "Clobberstone"})
        self.assertEqual(learned, 2)
        row = memory.profile("Clobberstone")
        self.assertAlmostEqual(row["damage"], 22.0)
        self.assertEqual(row["range"], 4.0)

    def test_observe_ignores_damage_i_dealt(self):
        memory = threat.ThreatMemory()
        log = [{"type": "GameActionFightLifePointsLostMessage", "source": 4404438,
                "target": -1, "loss": 40}]
        self.assertEqual(memory.observe(log, 4404438, {-1: "Clobberstone"}), 0)
        self.assertEqual(memory.monsters, {})

    def test_observe_ignores_damage_to_something_that_is_not_me(self):
        memory = threat.ThreatMemory()
        log = [{"type": "GameActionFightLifePointsLostMessage", "source": -1,
                "target": -2, "loss": 40}]
        self.assertEqual(memory.observe(log, 4404438, {-1: "Clobberstone"}), 0)

    def test_the_cached_table_notices_a_write_from_the_same_process(self):
        # The combat server both writes this file (its observer) and reads it (the
        # solver). A cache that never looked again would price a fight with the
        # table from before the fight started.
        import os

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "threat_model.json"
            original = threat.DEFAULT_MEMORY
            threat.DEFAULT_MEMORY = path
            threat._MEMORY = None
            threat._MEMORY_PATH = None
            threat._MEMORY_STAMP = None
            try:
                self.assertEqual(len(threat.default_memory().monsters), 0)
                memory = threat.ThreatMemory()
                memory.record("Marbmour", 33.0)
                memory.save(path)
                os.utime(path, (0, 0))
                self.assertAlmostEqual(
                    threat.default_memory().profile("Marbmour")["damage"], 33.0)
            finally:
                threat.DEFAULT_MEMORY = original
                threat._MEMORY = None
                threat._MEMORY_PATH = None
                threat._MEMORY_STAMP = None


class SolverIntegrationTests(unittest.TestCase):
    """The price has to change the plan, not just exist."""

    def _board(self, foe_cell, cells=None, me_cell=None, spells=None):
        mine = me(cell=me_cell if me_cell is not None else cell(20, 20))
        board = board_with([mine, monster(cell=foe_cell)], cells=cells)
        board.spells = list(spells if spells is not None else [spell()])
        return board

    def test_a_plan_records_the_exposure_of_the_cell_it_ends_on(self):
        board = self._board(cell(23, 20))
        plan = BattleSolver().plan_turn(board)
        self.assertGreater(plan.exposure, 0.0)

    def test_a_plan_that_retreats_records_the_lower_exposure(self):
        # Range 8 from cell 20,20 means the monster covers everything the
        # character can walk to; the far corner of the ring is still cheaper
        # than standing next to it, and that has to show up in the record.
        base = cell(20, 20)
        board = self._board(base, me_cell=away(base, 6),
                            spells=[spell(max_range=8, ap_cost=6)])
        plan = BattleSolver().plan_turn(board)
        self.assertLess(plan.exposure, 2 * 12.0)

    def test_the_position_term_is_recorded_in_the_plan_dict(self):
        board = self._board(cell(23, 20))
        payload = BattleSolver().plan_turn(board).as_dict()
        self.assertIn("exposure", payload)
        self.assertGreater(payload["exposure"], 0.0)


class LoadBearingTests(unittest.TestCase):
    """The price has to be in the score, not only in the report."""

    @staticmethod
    def walled_column(x: int) -> CellMap:
        """Walkable everywhere, but one column blocks every line through it."""
        flags = [3] * NB_CELLS
        for (px, py), index in _POINTS.items():
            if px == x:
                flags[index] = 1  # walkable, no line of sight
        return CellMap(tuple(flags))

    def _board(self, hp: int = 145):
        # The monster is at x=20 with no movement left and a spell that needs a
        # line. The wall at x=25 means everything at x>=26 is out of its sight and
        # everything at x<=24 is in it. The character's own spell does not need a
        # line, so it can cast from either side.
        foe = monster(cell=cell(20, 20), mp=0, spells=(spell(requires_los=True),))
        mine = me(cell=cell(26, 20), hp=hp, max_hp=145)
        board = board_with([mine, foe], cells=self.walled_column(25))
        board.spells = [spell(requires_los=False, max_range=4, ap_cost=6)]
        return board

    def test_cover_is_priced_below_the_open_on_a_walled_map(self):
        model = threat.build(self._board())
        self.assertEqual(model.at(cell(26, 20)), 0.0)
        self.assertGreater(model.at(cell(24, 20)), 0.0)

    def test_the_default_weight_never_refuses_a_cast_that_lands(self):
        # Range 4 from x=26 cannot reach x=20, so casting means stepping into the
        # open. At the shipped weight the cast still wins: the term is a tie-break,
        # not a veto, and a character that refuses to fight never finishes a fight.
        plan = BattleSolver().plan_turn(self._board())
        self.assertNotEqual(plan.actions[0].kind, "pass")
        self.assertGreater(plan.expected_damage, 0.0)

    def test_with_the_price_turned_up_the_same_board_refuses(self):
        original = threat.POSITION_WEIGHT
        threat.POSITION_WEIGHT = 100.0
        try:
            plan = BattleSolver().plan_turn(self._board())
        finally:
            threat.POSITION_WEIGHT = original
        self.assertEqual(plan.actions[0].kind, "pass")

    def test_a_hurt_fighter_prices_the_same_cell_higher_than_a_healthy_one(self):
        healthy = me(cell=cell(26, 20), hp=145, max_hp=145)
        hurt = me(cell=cell(26, 20), hp=20, max_hp=145)
        self.assertAlmostEqual(threat.weight_for(healthy), threat.POSITION_WEIGHT)
        self.assertGreater(threat.weight_for(hurt), threat.weight_for(healthy) * 2)
        self.assertEqual(threat.weight_for(None), threat.POSITION_WEIGHT)

    def test_being_hurt_is_what_makes_it_trade_damage_for_cover(self):
        # The same board the healthy fighter walks into, one hit from death. The
        # trade "20 damage now, 60 incoming" is only wrong once the 60 kills.
        healthy = BattleSolver().plan_turn(self._board())
        hurt = BattleSolver().plan_turn(self._board(hp=20))
        self.assertNotEqual(healthy.actions[0].kind, "pass")
        self.assertEqual(hurt.actions[0].kind, "pass")


class LogObserverTests(unittest.TestCase):
    """The live path: the fight's own log is the only source of real damage."""

    def _payload(self, log, fight_id=1700):
        return {"fight_id": fight_id, "my_id": 4404438,
                "fighters": [{"id": -1, "name": "Marbmour"},
                             {"id": -2, "name": "Dolomanus"}],
                "battle_log": log}

    def test_only_the_new_messages_are_folded_in(self):
        from combat.combat_ai_server import _ThreatObserver

        cast = {"type": "GameActionFightSpellCastMessage", "source": -1,
                "spell": 7141, "cell": cell(20, 20), "destination_cell": cell(24, 20)}
        loss = {"type": "GameActionFightLifePointsLostMessage", "source": -1,
                "target": 4404438, "loss": 26}
        with tempfile.TemporaryDirectory() as folder:
            observer = _ThreatObserver(Path(folder) / "threat_model.json")
            self.assertEqual(observer.observe(self._payload([cast, loss])), 1)
            # The same log arrives on every turn of the fight. Counting it twice
            # would teach one hit forty times and inflate the monster.
            self.assertEqual(observer.observe(self._payload([cast, loss])), 0)
            self.assertAlmostEqual(
                observer.memory.profile("Marbmour")["damage"], 26.0)

    def test_a_rolling_log_keeps_learning(self):
        from combat.combat_ai_server import _ThreatObserver

        def hit(source, damage, at):
            return [{"type": "GameActionFightSpellCastMessage", "source": source,
                     "spell": 1, "cell": at, "destination_cell": cell(20, 20)},
                    {"type": "GameActionFightLifePointsLostMessage",
                     "source": source, "target": 4404438, "loss": damage}]

        with tempfile.TemporaryDirectory() as folder:
            observer = _ThreatObserver(Path(folder) / "threat_model.json")
            log = hit(-1, 10, cell(21, 20)) + hit(-2, 20, cell(22, 20))
            self.assertEqual(observer.observe(self._payload(log)), 2)
            # The client keeps only the tail of the log: the first two rows drop
            # off and two new ones arrive. A length comparison would say
            # "nothing new" here forever.
            log = log[2:] + hit(-1, 30, cell(23, 20))
            self.assertEqual(observer.observe(self._payload(log)), 1)
            self.assertAlmostEqual(
                observer.memory.profile("Marbmour")["damage"], 20.0)

    def test_a_new_fight_starts_with_a_clean_cursor(self):
        from combat.combat_ai_server import _ThreatObserver

        log = [{"type": "GameActionFightSpellCastMessage", "source": -1,
                "spell": 1, "cell": cell(21, 20), "destination_cell": cell(20, 20)},
               {"type": "GameActionFightLifePointsLostMessage", "source": -1,
                "target": 4404438, "loss": 12}]
        with tempfile.TemporaryDirectory() as folder:
            observer = _ThreatObserver(Path(folder) / "threat_model.json")
            observer.observe(self._payload(log, fight_id=1))
            self.assertEqual(observer.observe(self._payload(log, fight_id=2)), 1)

    def test_the_table_is_written_to_disk(self):
        from combat.combat_ai_server import _ThreatObserver

        log = [{"type": "GameActionFightLifePointsLostMessage", "source": -1,
                "target": 4404438, "loss": 9}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "threat_model.json"
            _ThreatObserver(path).observe(self._payload(log))
            self.assertTrue(path.exists())
            self.assertAlmostEqual(
                threat.ThreatMemory.load(path).profile("Marbmour")["damage"], 9.0)

    def test_a_payload_without_a_log_is_ignored(self):
        from combat.combat_ai_server import _ThreatObserver

        with tempfile.TemporaryDirectory() as folder:
            observer = _ThreatObserver(Path(folder) / "threat_model.json")
            self.assertEqual(observer.observe({"fight_id": 1, "my_id": 1}), 0)
            self.assertEqual(observer.observe(None), 0)


class CorpusReachTests(unittest.TestCase):
    """The reach the client stated, replacing the one this module assumed.

    `combat/monster_mech` can read a monster's spellbook back out of the fights
    already recorded, and `_profile` consults it between the board's own spell
    list and the level prior. These tests pin that order and the price it
    produces, because a prior that is half the real reach tells the policy a
    fight is safe at a distance the monster hits from -- which is the complaint
    this whole module exists to answer.
    """

    def setUp(self):
        # The table is a process-wide cache built on first use; reset it so a
        # test that stubs it cannot leak into the next one.
        self._saved = threat._CORPUS_REACH
        threat._CORPUS_REACH = None

    def tearDown(self):
        threat._CORPUS_REACH = self._saved

    def test_the_lookup_is_case_insensitive_and_level_exact(self):
        threat._CORPUS_REACH = {"merkaptan leader@5": 10}
        self.assertEqual(threat._corpus_reach("Merkaptan Leader", 5), 10)
        self.assertEqual(threat._corpus_reach("MERKAPTAN LEADER", 5), 10)
        self.assertIsNone(threat._corpus_reach("Merkaptan Leader", 6))
        self.assertIsNone(threat._corpus_reach("", 5))
        self.assertIsNone(threat._corpus_reach(None, 5))

    def test_a_monster_in_the_corpus_is_priced_from_its_own_spellbook(self):
        threat._CORPUS_REACH = {"merkaptan leader@5": 10}
        board = board_with([me(cell=cell(20, 20)),
                            monster(name="Merkaptan Leader", level=5, mp=0,
                                    cell=cell(20, 23))])
        model = threat.build(board, memory=threat.ThreatMemory())
        self.assertEqual(len(model.enemies), 1)
        self.assertEqual(model.enemies[0].source, "corpus")
        self.assertEqual(model.enemies[0].reach, 10)

    def test_a_monster_outside_the_corpus_keeps_the_level_prior(self):
        threat._CORPUS_REACH = {}
        board = board_with([me(cell=cell(20, 20)),
                            monster(name="Nobody", level=8, mp=3, cell=cell(20, 23))])
        model = threat.build(board, memory=threat.ThreatMemory())
        self.assertEqual(model.enemies[0].source, "prior")
        self.assertEqual(model.enemies[0].reach, threat.PRIOR_RANGE + 3)

    def test_the_corpus_reach_costs_more_than_the_prior_did(self):
        """The price, not just the field: a cell eight from the monster is not free."""
        foe_cell = cell(20, 23)
        target = away(foe_cell, 8)          # inside reach 10, outside the prior 5
        board = board_with([me(cell=cell(20, 20)),
                            monster(name="Merkaptan Leader", level=5, mp=0,
                                    cell=foe_cell)])
        threat._CORPUS_REACH = {}
        cheap = threat.build(board, memory=threat.ThreatMemory()).at(target)
        threat._CORPUS_REACH = {"merkaptan leader@5": 10}
        dear = threat.build(board, memory=threat.ThreatMemory()).at(target)
        self.assertEqual(cheap, 0.0, "the prior reach of 5 should not cover a cell 8 away")
        self.assertGreater(dear, 0.0, "a reach of 10 must cover a cell 8 away")

    def test_a_corpus_that_cannot_be_read_falls_through_to_the_prior(self):
        """A refinement of a working prior must never break the decision."""
        import builtins

        real_import = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name == "combat.monster_mech":
                raise ImportError("pretend the corpus is unreadable")
            return real_import(name, *args, **kwargs)

        threat._CORPUS_REACH = None
        builtins.__import__ = refuse
        try:
            self.assertIsNone(threat._corpus_reach("Merkaptan Leader", 5))
            self.assertEqual(threat._CORPUS_REACH, {})
        finally:
            builtins.__import__ = real_import


if __name__ == "__main__":
    unittest.main()
