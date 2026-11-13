"""The synthetic curriculum: non-pass rows, combos, LOS blocks, pushback.

The recorded corpus has no actionable board, so the grid ranker is pre-trained on
``combat.grid_synth``. These checks pin the three properties the corpus exists to
provide and that a pass-only file cannot: a winning plan with two spells, a board
whose line of sight was actually broken by the generator, and a plan that pushes.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from combat import grid_synth  # noqa: E402
from combat.battle_solver import (Board, CellMap, Fighter, cell_point,  # noqa: E402
                                  line_points)


def _action(kind: str = "cast", spell_id: int = 1, moved_target=None):
    return SimpleNamespace(kind=kind, spell_id=spell_id, moved_target=moved_target)


def _plan(score: float, bucket: str, actions):
    return SimpleNamespace(score=score, bucket=bucket, actions=actions)


def test_combo_needs_two_distinct_spells() -> None:
    combo, push = grid_synth.classify_plans([
        _plan(5.0, "damage", [_action(spell_id=1), _action(spell_id=2)]),
        _plan(1.0, "damage", [_action(spell_id=1)]),
    ])
    assert combo and not push
    # The same spell twice is not a combo: one cast's damage, repeated.
    combo, _ = grid_synth.classify_plans([
        _plan(5.0, "damage", [_action(spell_id=1), _action(spell_id=1)]),
    ])
    assert not combo


def test_pushback_is_detected_even_inside_a_damage_plan() -> None:
    _combo, push = grid_synth.classify_plans([
        _plan(9.0, "damage", [_action(spell_id=1), _action(spell_id=9001,
                                                      moved_target=77)]),
    ])
    assert push, "a plan containing a forced move is a pushback example"
    _combo, push = grid_synth.classify_plans([
        _plan(9.0, "damage", [_action(spell_id=1)]),
    ])
    assert not push


def test_push_spell_is_a_castable_forced_move() -> None:
    spell = grid_synth._push_spell()
    assert spell.castable and spell.need_taken_cell and spell.requires_los
    assert spell.effects and spell.effects[0].kind == "push"
    assert spell.ap_cost >= 1


def _straight_pair() -> tuple[int, int]:
    points = {cell: cell_point(cell) for cell in range(560)}
    seen = {}
    for cell, (x, y) in points.items():
        seen.setdefault((x, y), cell)
    for cell, (x, y) in points.items():
        for other in range(cell + 1, 560):
            ox, oy = points[other]
            if (ox == x and abs(oy - y) >= 3) or (oy == y and abs(ox - x) >= 3):
                return cell, other
    raise AssertionError("no straight pair on the 560-cell grid")


def _board(me: int, enemy: int) -> Board:
    flags = tuple(3 for _ in range(560))  # walkable and see-through everywhere
    fighter = lambda fid, cell, team: Fighter(
        id=fid, name="f%d" % fid, cell=cell, team=team, hp=100, max_hp=100,
        ap=6, mp=3, shield=0, level=100, alive=True, by_me=(fid == 1),
        max_ap=6, max_mp=3)
    return Board(map_id=1, in_fight=True, in_preparation=False, is_my_turn=True,
                 my_id=1, my_cell=me, my_ap=6, my_mp=3,
                 fighters=[fighter(1, me, "blue"), fighter(2, enemy, "red")],
                 spells=[], my_stats={}, cells=CellMap(flags))


def test_los_variant_closes_exactly_the_line() -> None:
    me, enemy = _straight_pair()
    board = _board(me, enemy)
    assert board.cells.has_los(me, enemy)
    variant = grid_synth.los_blocked_variant(board, __import__("random").Random(1))
    assert variant is not None, "a straight open line must be blockable"
    assert variant is not board and variant.cells is not board.cells
    assert not variant.cells.has_los(me, enemy)
    # Original untouched: the variant is a copy, not a mutation.
    assert board.cells.has_los(me, enemy)
    # And the closed cell is on the line, not somewhere arbitrary.
    points = {cell_point(c): c for c in range(560)}
    ax, ay = cell_point(me)
    bx, by = cell_point(enemy)
    interior = {points[p] for p in line_points(ax, ay, bx, by)[:-1] if p in points}
    changed = [i for i, (a, b) in enumerate(zip(variant.cells.flags, board.cells.flags))
               if a != b]
    assert changed and set(changed) <= interior, changed


def test_los_variant_refuses_when_nothing_is_visible() -> None:
    me, enemy = _straight_pair()
    board = _board(me, enemy)
    flags = list(board.cells.flags)
    flags[enemy] = 1  # the enemy's own cell no longer sees
    blind = Board(**{**board.__dict__, "cells": CellMap(tuple(flags))})
    assert not blind.cells.has_los(me, enemy)
    assert grid_synth.los_blocked_variant(blind, __import__("random").Random(1)) is None


def test_curriculum_generates_non_pass_rows() -> None:
    rows, stats = grid_synth.curriculum_rows(1, seed=5, beam_width=2, plan_k=3)
    assert rows, "one duel must yield at least one board"
    assert stats["boards"] >= 1
    # Every recorded board is a fight state the dataset can parse.
    assert all(isinstance(row, dict) and row.get("fighters") for row in rows)


def _main() -> int:
    names = sorted(name for name in globals() if name.startswith("test_"))
    failed = 0
    for name in names:
        try:
            globals()[name]()
        except AssertionError as exc:
            failed += 1
            print("FAIL %s: %s" % (name, exc))
        else:
            print("ok   %s" % name)
    print("%d curriculum checks, %d failed" % (len(names), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
