"""Stateful property fuzzer for the battle solver.

The existing solver tests are examples: one board, one expected answer. They say
nothing about the boards nobody wrote down. This file generates boards instead --
from the payload the client actually sends, because ``Board.from_fight_state`` is
the real input surface and a hand-built ``Board`` skips the parser that has broken
twice already (the ``mp: 0`` re-pricing bug and the 106-of-160 ``ap: 0`` boards).

Two kinds of property:

* **Invariants** that must hold on every board the generator can produce: the
  solver never spends more AP or MP than the character has, never reports negative
  damage or healing, never projects an HP outside ``[0, max_hp]``, always offers a
  pass, and never raises.
* **Determinism**, which is the property a benchmark depends on: two solvers given
  the same board must return byte-identical plans. The solver is documented as "a
  pure function of its board"; this is what checks it.

Every case is seeded, so a failure names the seed that reproduces it.
"""

from __future__ import annotations

import json
import math
import random
import unittest

from combat.battle_solver import (
    NB_CELLS,
    BattleSolver,
    Board,
    _POINTS,
)

ALL_POINTS = sorted(_POINTS)
# cell id -> (x, y), so the generator can place a fighter at a real distance
# instead of at a random index in the grid.
_POINTS_BY_CELL = {cell_id: point for point, cell_id in _POINTS.items()}
ELEMENTS = ("neutral", "earth", "fire", "water", "air")
EFFECT_IDS = (96, 97, 98, 99, 100, 101, 108, 143, 144, 145, 161, 162, 165, 181, 200, 666)


def open_flags() -> list:
    return [3] * NB_CELLS


def random_flags(rng: random.Random) -> list:
    """A map with walls, no-sight cells and the odd hole in the grid."""
    flags = []
    for _ in range(NB_CELLS):
        roll = rng.random()
        if roll < 0.08:
            flags.append(0)          # unwalkable, blocks sight
        elif roll < 0.16:
            flags.append(1)          # walkable, blocks sight
        elif roll < 0.20:
            flags.append(2)          # unwalkable, transparent
        else:
            flags.append(3)
    return flags


class BoardFuzzer:
    """Deterministic generator of structurally valid fight payloads."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.rng = random.Random(seed)

    # -- pieces --------------------------------------------------------------

    def a_cell(self) -> int:
        return _POINTS[self.rng.choice(ALL_POINTS)]

    def stats(self) -> dict:
        rng = self.rng
        stats = {}
        for key in ("strength", "intelligence", "chance", "agility"):
            stats[key] = rng.choice([0, 1, 50, 200, 400, 800, 1500])
        stats["power"] = rng.choice([0, 7, 40, 200])
        stats["damage"] = rng.choice([0, 4, 30, 120])
        stats["damage_earth"] = rng.choice([0, 2, 20])
        stats["critical_hit"] = rng.choice([0, 5, 30, 100])
        stats["damage_critical"] = rng.choice([0, 3, 25])
        stats["heals"] = rng.choice([0, 11, 80])
        if rng.random() < 0.15:
            # The client does not always publish every key.
            stats.pop(rng.choice(list(stats)))
        return stats

    def fighter(self, ident: int, team: str, cell: int, must_live: bool = False) -> dict:
        rng = self.rng
        max_hp = rng.choice([1, 10, 250, 1000, 4000])
        hp = rng.choice([max_hp, max(0, max_hp // 2), 1, 0])
        if must_live and hp <= 0:
            # A dead enemy is a legal board and the solver must pass on it -- but a
            # corpus of dead enemies tests the early-out 60 times and the search
            # never. Kept alive in the actionable mode, exercised deliberately in
            # `test_a_finished_fight_plans_a_pass_rather_than_raising`.
            hp = max(1, max_hp // 2)
        return {
            "id": ident, "name": "F%d" % ident, "cell": cell, "team": team,
            "hp": hp, "max_hp": max_hp,
            "ap": rng.choice([0, 1, 4, 6, 10, 12, None]),
            "mp": rng.choice([0, 1, 3, 6, None]),
            "shield": rng.choice([0, 0, 5, 50, 900]),
            "level": rng.choice([1, 50, 100, 200]),
            "alive": hp > 0,
            "resist_percent": {e: rng.choice([0, 10, 35, 60]) for e in ELEMENTS
                               if rng.random() < 0.5},
            "resist_fixed": {e: rng.choice([0, 3, 40]) for e in ELEMENTS
                             if rng.random() < 0.3},
            "critical_resistance": rng.choice([0, 2, 15]),
        }

    def effect(self) -> dict:
        rng = self.rng
        effect = {"effect_id": rng.choice(EFFECT_IDS)}
        if rng.random() < 0.2:
            effect["value"] = rng.choice([0, 3, 30, 500])
        else:
            effect["dice_num"] = rng.choice([0, 1, 10, 40])
            effect["dice_side"] = rng.choice([0, 1, 15, 60])
        if rng.random() < 0.1:
            effect["target"] = rng.choice(["self", "ally", "enemy", "all"])
        return effect

    def spell(self, ident: int) -> dict:
        rng = self.rng
        low = rng.choice([0, 1, 2, 5])
        spell = {
            "id": ident, "name": "S%d" % ident,
            "ap_cost": rng.choice([0, 1, 2, 3, 4, 6, 12]),
            "min_range": low,
            "range": max(low, rng.choice([0, 1, 3, 6, 12])),
            "castable": rng.random() < 0.9,
            "requires_los": rng.random() < 0.7,
            "max_cast_per_turn": rng.choice([0, 1, 2, 4]),
            "max_cast_per_target": rng.choice([0, 1, 2, 3]),
            "critical_hit_probability": rng.choice([0, 5, 100]),
            "effects": [self.effect() for _ in range(rng.choice([0, 1, 1, 2, 3]))],
        }
        if rng.random() < 0.6:
            spell["critical_effects"] = [self.effect() for _ in range(rng.choice([1, 2]))]
        if rng.random() < 0.1:
            spell["min_cast_interval"] = rng.choice([1, 2])
        return spell

    # -- the payload ---------------------------------------------------------

    def near(self, origin: int, cells: list) -> int:
        """A cell a spell can plausibly reach from ``origin``.

        Uniform placement over 560 cells put the enemy out of range almost every
        time, and the solver -- correctly -- passed. Measured: 57 of 60 boards
        produced a pass, so the fuzzer was exercising the early-out and nothing
        else. An actionable board has to be reachable.
        """
        point = _POINTS_BY_CELL[origin]
        candidates = [c for c in cells
                      if abs(_POINTS_BY_CELL[c][0] - point[0])
                      + abs(_POINTS_BY_CELL[c][1] - point[1]) <= 6]
        return self.rng.choice(candidates) if candidates else self.rng.choice(cells)

    def payload(self, actionable: bool = True) -> dict:
        rng = self.rng
        flags = open_flags() if actionable else random_flags(rng)
        cells = [i for i in range(NB_CELLS) if flags[i] & 1] or [0]
        my_cell = rng.choice(cells)
        fighters = [{"id": 1, "name": "Me", "cell": my_cell, "team": "player",
                     "hp": rng.choice([500, 1000]), "max_hp": 1000,
                     "ap": rng.choice([3, 6, 10, 10, 12]) if actionable
                     else rng.choice([0, 3, 6, 10]),
                     "mp": rng.choice([3, 6, 6]) if actionable else rng.choice([0, 3, 6]),
                     "shield": 0, "level": 100, "alive": True, "is_me": True}]
        used = {my_cell}
        for ident in range(2, rng.randint(2, 6) + 1):
            if actionable and rng.random() < 0.85:
                placed = self.near(my_cell, cells)
            else:
                placed = rng.choice(cells)
            if placed in used and rng.random() < 0.8:
                placed = rng.choice(cells)
            used.add(placed)
            team = "enemy" if (rng.random() < 0.6 if actionable else rng.random() < 0.6) \
                else "player"
            fighters.append(self.fighter(ident, team, placed,
                                         must_live=actionable and team == "enemy"))
        if rng.random() < 0.1:
            # The client sends actors with no cell during placement.
            fighters.append({"id": 99, "name": "Unplaced", "cell": None,
                             "team": "enemy", "hp": 10, "max_hp": 10, "alive": True})
        spells = [self.spell(ident) for ident in
                  rng.sample(range(10, 200), rng.randint(1, 5) if actionable
                             else rng.randint(0, 5))]
        if actionable:
            # At least one spell has to be a real, reachable damage line or the
            # board is a pass board wearing a spell list.
            spells[0].update({
                "ap_cost": rng.choice([1, 2, 3]),
                "min_range": 1,
                "range": rng.choice([3, 6, 9]),
                "castable": True,
                "requires_los": False,
                "max_cast_per_turn": rng.choice([0, 2, 4]),
                "effects": [{"effect_id": 97, "dice_num": 10, "dice_side": 15}],
            })
        payload = {
            "map_id": 100139008,
            "in_fight": True if actionable else rng.random() < 0.95,
            "in_preparation": False if actionable else rng.random() < 0.1,
            "is_my_turn": True if actionable else rng.random() < 0.9,
            "my_id": 1,
            "my_cell": my_cell,
            "cells": flags,
            "my_stats": self.stats(),
            "fighters": fighters,
            "spells": spells,
        }
        return payload


class InvariantMixin:
    """The properties that must hold on any board, checked in one place."""

    def assert_plan_is_sane(self, board: Board, plan, where: str) -> None:
        actions = plan.actions

        # -- AP/MP conservation ---------------------------------------------
        spent_ap = sum(int(a.ap_cost) for a in actions)
        spent_mp = sum(float(a.mp_cost) for a in actions)
        self.assertGreaterEqual(spent_ap, 0, where)
        self.assertGreaterEqual(spent_mp, 0.0, where)
        self.assertLessEqual(spent_ap, board.my_ap,
                             "%s: plan spends %d AP of %d" % (where, spent_ap, board.my_ap))
        self.assertLessEqual(spent_mp, board.my_mp + 1e-6,
                             "%s: plan spends %r MP of %r" % (where, spent_mp, board.my_mp))
        self.assertGreaterEqual(plan.remaining_ap, 0, where)
        self.assertGreaterEqual(plan.remaining_mp, -1e-6, where)
        self.assertLessEqual(plan.remaining_ap, board.my_ap, where)
        self.assertLessEqual(plan.remaining_mp, board.my_mp + 1e-6, where)

        # -- monotone, finite, non-negative value ---------------------------
        for name, value in (("damage", plan.expected_damage),
                            ("healing", plan.expected_healing)):
            self.assertTrue(math.isfinite(value), "%s: %s is %r" % (where, name, value))
            self.assertGreaterEqual(value, 0.0, "%s: %s is %r" % (where, name, value))
        self.assertTrue(math.isfinite(plan.score), "%s: score is %r" % (where, plan.score))

        # -- HP stays inside the bar ----------------------------------------
        max_hp = {f.id: f.max_hp for f in board.fighters}
        for fid, hp in plan.final_hp.items():
            self.assertIn(fid, max_hp, "%s: final_hp names an unknown fighter" % where)
            self.assertGreaterEqual(hp, 0, "%s: fighter %d ends at %d hp" % (where, fid, hp))
            self.assertLessEqual(hp, max_hp[fid],
                                 "%s: fighter %d ends above its max" % (where, fid))

        # -- a cast that happens must be one the spell allows ----------------
        by_id = {s.id: s for s in board.spells}
        per_spell: dict = {}
        per_target: dict = {}
        for action in actions:
            if action.kind != "cast":
                continue
            spell = by_id.get(action.spell_id)
            self.assertIsNotNone(spell, "%s: cast of an unknown spell" % where)
            per_spell[action.spell_id] = per_spell.get(action.spell_id, 0) + 1
            per_target[(action.spell_id, action.target_id)] = \
                per_target.get((action.spell_id, action.target_id), 0) + 1
            if spell.max_cast_per_turn:
                self.assertLessEqual(per_spell[action.spell_id], spell.max_cast_per_turn,
                                     "%s: spell %d cast %d times, limit %d"
                                     % (where, spell.id, per_spell[action.spell_id],
                                        spell.max_cast_per_turn))
            if spell.max_cast_per_target:
                self.assertLessEqual(per_target[(action.spell_id, action.target_id)],
                                     spell.max_cast_per_target,
                                     "%s: spell %d on target %s over its limit"
                                     % (where, spell.id, action.target_id))

        # -- a walk spends movement, not AP ---------------------------------
        for action in actions:
            if action.kind == "move":
                self.assertEqual(int(action.ap_cost), 0,
                                 "%s: a move charged AP" % where)
                self.assertGreater(action.mp_cost, 0.0,
                                   "%s: a move charged no MP" % where)


class SingleBoardTests(InvariantMixin, unittest.TestCase):
    CASES = 60

    def test_the_corpus_actually_reaches_the_search(self):
        """A property test that only ever sees passes is a test of the early-out.

        The first version of this generator placed enemies uniformly over 560
        cells and produced 57 passes in 60 boards: every invariant held and none of
        them was about the search. This pins the corpus so the same degeneration
        fails loudly instead of quietly.
        """
        casts = multi = damaged = 0
        for seed in range(self.CASES):
            board = Board.from_fight_state(BoardFuzzer(seed).payload())
            plan = BattleSolver().plan_turn(board)
            if plan.actions and plan.actions[0].kind == "cast":
                casts += 1
            if len(plan.actions) >= 2:
                multi += 1
            if plan.expected_damage > 0:
                damaged += 1
        self.assertGreaterEqual(casts, self.CASES * 0.5,
                                "%d of %d boards opened with a cast" % (casts, self.CASES))
        self.assertGreaterEqual(multi, self.CASES * 0.15,
                                "%d of %d boards had a multi-action turn" % (multi, self.CASES))
        self.assertGreaterEqual(damaged, self.CASES * 0.5,
                                "%d of %d boards projected damage" % (damaged, self.CASES))

    def test_no_generated_board_raises_and_every_plan_is_sane(self):
        for seed in range(self.CASES):
            fuzzer = BoardFuzzer(seed)
            payload = fuzzer.payload()
            where = "seed %d" % seed
            try:
                board = Board.from_fight_state(payload)
            except Exception as error:                    # noqa: BLE001
                self.fail("%s: parsing raised %s: %s\npayload=%s"
                          % (where, type(error).__name__, error, json.dumps(payload)))
            try:
                plan = BattleSolver().plan_turn(board)
            except Exception as error:                    # noqa: BLE001
                self.fail("%s: solving raised %s: %s\npayload=%s"
                          % (where, type(error).__name__, error, json.dumps(payload)))
            self.assert_plan_is_sane(board, plan, where)

    def test_the_solver_is_a_pure_function_of_its_board(self):
        """Two solvers, same board, identical plan -- the benchmark depends on it."""
        for seed in range(0, self.CASES, 2):
            board = Board.from_fight_state(BoardFuzzer(seed).payload())
            first = BattleSolver().plan_turn(board).as_dict()
            second = BattleSolver().plan_turn(board).as_dict()
            self.assertEqual(json.dumps(first, sort_keys=True),
                             json.dumps(second, sort_keys=True),
                             "seed %d: the same board produced two plans" % seed)

    def test_re_parsing_the_same_payload_gives_the_same_plan(self):
        for seed in range(1, self.CASES, 4):
            payload = BoardFuzzer(seed).payload()
            one = BattleSolver().plan_turn(Board.from_fight_state(payload)).as_dict()
            two = BattleSolver().plan_turn(Board.from_fight_state(payload)).as_dict()
            self.assertEqual(json.dumps(one, sort_keys=True),
                             json.dumps(two, sort_keys=True),
                             "seed %d: the payload is not a stable input" % seed)

    def test_a_pass_is_always_on_the_table(self):
        for seed in range(0, self.CASES, 3):
            board = Board.from_fight_state(BoardFuzzer(seed).payload())
            solver = BattleSolver()
            candidates = solver.plan_candidates(board, k=4)
            self.assertTrue(candidates, "seed %d: no candidate at all" % seed)
            self.assertTrue(
                any(p.actions and p.actions[0].kind == "pass" for p in candidates),
                "seed %d: no pass among the candidates" % seed)

    def test_candidates_are_distinct(self):
        for seed in range(0, self.CASES, 5):
            board = Board.from_fight_state(BoardFuzzer(seed).payload())
            solver = BattleSolver()
            candidates = solver.plan_candidates(board, k=5)
            shapes = [json.dumps([a.as_dict() for a in p.actions], sort_keys=True)
                      for p in candidates]
            self.assertEqual(len(shapes), len(set(shapes)),
                             "seed %d: the candidate list repeats a plan" % seed)


class StatefulFightTests(InvariantMixin, unittest.TestCase):
    """A fight, not a turn: apply the plan, hand the result back, plan again."""

    ROUNDS = 5
    CASES = 12

    def apply(self, payload: dict, plan) -> dict:
        """Advance the payload by one turn of the plan the solver chose."""
        after = json.loads(json.dumps(payload))
        ap = payload.get("my_cell")
        me = next((f for f in after["fighters"] if f.get("id") == 1), None)
        if me is None:
            return after
        me["ap"] = max(0, plan.remaining_ap)
        me["mp"] = max(0, int(plan.remaining_mp))
        if plan.final_cell is not None and ap is not None:
            me["cell"] = plan.final_cell
            after["my_cell"] = plan.final_cell
        for fighter in after["fighters"]:
            hp = plan.final_hp.get(fighter.get("id"))
            if hp is None:
                continue
            fighter["hp"] = max(0, int(hp))
            fighter["alive"] = fighter["hp"] > 0
        return after

    def test_a_multi_turn_fight_keeps_every_invariant(self):
        for case in range(self.CASES):
            payload = BoardFuzzer(1000 + case).payload()
            for round_index in range(self.ROUNDS):
                where = "case %d round %d" % (case, round_index)
                board = Board.from_fight_state(payload)
                plan = BattleSolver().plan_turn(board)
                self.assert_plan_is_sane(board, plan, where)
                payload = self.apply(payload, plan)
                # The character cannot gain AP or MP from being planned for.
                me = next(f for f in payload["fighters"] if f.get("id") == 1)
                self.assertGreaterEqual(me.get("ap") or 0, 0, where)
                self.assertGreaterEqual(me.get("mp") or 0, 0, where)

    def test_a_finished_fight_plans_a_pass_rather_than_raising(self):
        payload = BoardFuzzer(7).payload()
        for fighter in payload["fighters"]:
            fighter["hp"] = 0
            fighter["alive"] = False
        payload["fighters"][0]["hp"] = 100
        payload["fighters"][0]["alive"] = True
        board = Board.from_fight_state(payload)
        plan = BattleSolver().plan_turn(board)
        self.assert_plan_is_sane(board, plan, "all enemies dead")

    def test_an_unwalkable_map_is_a_pass_not_a_crash(self):
        payload = BoardFuzzer(11).payload()
        payload["cells"] = [0] * NB_CELLS
        board = Board.from_fight_state(payload)
        plan = BattleSolver().plan_turn(board)
        self.assertTrue(plan.actions, "a pass is still a turn")
        self.assertEqual(plan.actions[0].kind, "pass")

    def test_a_board_with_no_spells_still_moves_or_passes(self):
        payload = BoardFuzzer(13).payload()
        payload["spells"] = []
        board = Board.from_fight_state(payload)
        plan = BattleSolver().plan_turn(board)
        self.assert_plan_is_sane(board, plan, "no spells")
        self.assertTrue(all(a.kind in ("move", "pass") for a in plan.actions))


class ChaosPayloadTests(unittest.TestCase):
    """Payloads the client is allowed to send and a parser must survive.

    Measured 2026-11-13: 1,000 seeds in this mode, no raise. Kept because the
    parser has already had to learn that ``mp: null`` means "not published" and
    ``ap: 0`` on our own row means "not published either", and both were found by
    a board that looked malformed rather than by a test.
    """

    CASES = 40

    def test_no_malformed_board_raises_out_of_the_parser_or_the_solver(self):
        for seed in range(self.CASES):
            payload = BoardFuzzer(seed).payload(actionable=False)
            where = "chaos seed %d" % seed
            try:
                board = Board.from_fight_state(payload)
                BattleSolver().plan_turn(board)
            except Exception as error:                    # noqa: BLE001
                self.fail("%s: %s: %s\npayload=%s"
                          % (where, type(error).__name__, error, json.dumps(payload)))


if __name__ == "__main__":
    unittest.main()
