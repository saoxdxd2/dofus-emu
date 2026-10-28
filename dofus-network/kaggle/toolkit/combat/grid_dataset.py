"""Build 560-cell combat tensors from recorded boards.

The model consumes three arrays per board:

    cells    [560, 12]   flags, occupancy, distance, line of sight, coordinates
    fighters [16, 20]    HP/AP/MP, cell, team, resists, boss flags
    plans    [16, 16]    solver plan features, bucket, score

The label is the solver's best legal plan. A board is only usable when the
controlled fighter has AP > 0, it is the controlled turn, and the solver returns
more than the pass plan. The recorded corpus fails that gate; the builder reports
it and writes nothing rather than creating a pass-only dataset.

    python -m combat.grid_dataset
    python -m combat.grid_dataset --boards <file> --out data/combat_grid
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from combat.battle_solver import (NB_CELLS, BattleSolver, Board, Fighter,
                                  cell_point)
from combat.grid_model import (CELL_FEATS, FIGHTER_FEATS, MAX_FIGHTERS,
                               MAX_PLANS, PLAN_FEATS)

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "combat_grid"
BUCKETS = ("damage", "displacement", "setup", "boss", "pass")


def _board_rows(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("board"), dict):
                yield row["board"]


def _resist(fighter: Fighter, name: str) -> float:
    rows = getattr(fighter, name) or ()
    for key, value in rows:
        if key == "neutral":
            try:
                return float(value) / 100.0
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _cells(board: Board, me: Fighter) -> np.ndarray:
    out = np.zeros((NB_CELLS, CELL_FEATS), dtype=np.float32)
    by_cell: Dict[int, int] = {}
    for fighter in board.fighters:
        if fighter.cell is None:
            continue
        by_cell[fighter.cell] = 1 if fighter.team == me.team else -1
    occupied = frozenset(by_cell)
    for cell in range(NB_CELLS):
        flags = board.cells.flags[cell] if board.cells.flags else 0
        walkable = board.cells.walkable(cell)
        los = board.cells.has_los(me.cell, cell, occupied) if me.cell is not None else False
        distance = board.cells.distance(me.cell, cell) if me.cell is not None else None
        x, y = cell_point(cell)
        out[cell] = np.asarray([
            1.0 if flags & 1 else 0.0,
            1.0 if flags & 2 else 0.0,
            1.0 if walkable else 0.0,
            1.0 if los else 0.0,
            float(by_cell.get(cell, 0)),
            1.0 if cell == me.cell else 0.0,
            0.0 if distance is None else min(1.0, distance / 30.0),
            x / 20.0,
            y / 20.0,
            1.0 if walkable and not by_cell.get(cell) else 0.0,
            1.0 if flags & 4 else 0.0,
            1.0 if flags & 8 else 0.0,
        ], dtype=np.float32)
    return out


def _fighters(board: Board, me: Fighter) -> Tuple[np.ndarray, np.ndarray]:
    out = np.zeros((MAX_FIGHTERS, FIGHTER_FEATS), dtype=np.float32)
    mask = np.zeros((MAX_FIGHTERS,), dtype=bool)
    for index, fighter in enumerate(board.fighters[:MAX_FIGHTERS]):
        mask[index] = True
        x, y = cell_point(fighter.cell) if fighter.cell is not None else (0, 0)
        distance = (board.cells.distance(me.cell, fighter.cell)
                    if me.cell is not None and fighter.cell is not None else None)
        out[index] = np.asarray([
            1.0 if fighter.team == me.team else 0.0,
            1.0 if fighter.alive else 0.0,
            min(1.0, (fighter.hp or 0) / max(1, fighter.max_hp or 1)),
            min(1.0, (fighter.ap or 0) / max(1, fighter.max_ap or 1)),
            min(1.0, (fighter.mp or 0) / max(1, fighter.max_mp or 1)),
            min(1.0, (fighter.shield or 0) / max(1, fighter.max_hp or 1)),
            min(1.0, (fighter.level or 1) / 200.0),
            1.0 if fighter.id == me.id else 0.0,
            1.0 if fighter.is_boss else 0.0,
            1.0 if getattr(fighter, "is_monster", False) else 0.0,
            1.0 if fighter.is_summon else 0.0,
            1.0 if fighter.is_companion else 0.0,
            _resist(fighter, "resist_percent"),
            _resist(fighter, "resist_fixed"),
            x / 20.0,
            y / 20.0,
            0.0 if distance is None else min(1.0, distance / 30.0),
            1.0 if fighter.team == me.team and fighter.id == me.id else 0.0,
            0.0,
            1.0 if fighter.team != me.team and fighter.alive else 0.0,
        ], dtype=np.float32)
    return out, mask


def _plans(board: Board, plans: Optional[List[Any]] = None
           ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, int, List[str]]:
    if plans is None:
        plans = BattleSolver(beam_width=16).plan_candidates(board, MAX_PLANS)
    plans = list(plans)
    action_plans = [plan for plan in plans
                    if plan.actions and plan.actions[0].kind != "pass"]
    if len(action_plans) <= 1:
        return np.zeros((0, PLAN_FEATS), np.float32), np.zeros((0,), bool), \
            np.zeros((0,), np.float32), 0, ["pass_only"]
    out = np.zeros((MAX_PLANS, PLAN_FEATS), dtype=np.float32)
    mask = np.zeros((MAX_PLANS,), dtype=bool)
    scores = np.zeros((MAX_PLANS,), dtype=np.float32)
    notes: List[str] = []
    for index, plan in enumerate(plans[:MAX_PLANS]):
        mask[index] = True
        bucket = plan.bucket if plan.bucket in BUCKETS else "setup"
        kinds = Counter(action.kind for action in plan.actions)
        first = plan.actions[0] if plan.actions else None
        target_cells = [action.target_cell for action in plan.actions
                        if getattr(action, "target_cell", None) is not None]
        target_cell = float(np.mean(target_cells)) / NB_CELLS if target_cells else 0.0
        out[index] = np.asarray([
            min(1.0, plan.expected_damage / 1000.0),
            min(1.0, plan.expected_healing / 1000.0),
            min(1.0, len(plan.kills) / float(max(1, len(board.fighters)))),
            min(1.0, len(plan.actions) / 8.0),
            min(1.0, sum(action.ap_cost for action in plan.actions) / 12.0),
            min(1.0, sum(action.mp_cost for action in plan.actions) / 8.0),
            1.0 if any(action.moved for action in plan.actions) else 0.0,
            1.0 if bucket == "damage" else 0.0,
            1.0 if bucket == "displacement" else 0.0,
            1.0 if bucket == "setup" else 0.0,
            1.0 if bucket == "boss" else 0.0,
            1.0 if bucket == "pass" else 0.0,
            1.0 if first is not None and first.kind == "cast" else 0.0,
            1.0 if first is not None and first.kind == "move" else 0.0,
            target_cell,
            min(1.0, (plan.remaining_ap or 0) / 12.0),
        ], dtype=np.float32)
        scores[index] = float(plan.score)
        notes.extend(plan.notes)
    return out, mask, scores, int(np.argmax(scores[:len(plans)])), notes


def build(rows: Iterable[Dict[str, Any]]) -> Tuple[Dict[str, np.ndarray], Dict[str, Any]]:
    cells: List[np.ndarray] = []
    cell_masks: List[np.ndarray] = []
    fighters: List[np.ndarray] = []
    fighter_masks: List[np.ndarray] = []
    plans: List[np.ndarray] = []
    plan_masks: List[np.ndarray] = []
    scores: List[np.ndarray] = []
    labels: List[int] = []
    groups: List[str] = []
    stats = Counter()
    for payload in rows:
        stats["boards"] += 1
        try:
            board = Board.from_fight_state(payload)
        except Exception:
            stats["parse_error"] += 1
            continue
        me = board.me
        if me is None or me.ap is None or me.ap <= 0:
            stats["no_positive_ap"] += 1
            continue
        if not board.in_fight or board.in_preparation or not board.is_my_turn:
            stats["not_my_turn"] += 1
            continue
        plan_rows, plan_mask, plan_scores, label, notes = _plans(board)
        if plan_rows.shape[0] == 0:
            stats["pass_only"] += 1
            continue
        cell_rows = _cells(board, me)
        fighter_rows, fighter_mask = _fighters(board, me)
        pad = MAX_PLANS - plan_rows.shape[0]
        if pad > 0:
            plan_rows = np.vstack([plan_rows, np.zeros((pad, PLAN_FEATS), np.float32)])
            plan_mask = np.concatenate([plan_mask, np.zeros((pad,), bool)])
            plan_scores = np.concatenate([plan_scores, np.zeros((pad,), np.float32)])
        cells.append(cell_rows)
        cell_masks.append(np.ones((NB_CELLS,), dtype=bool))
        fighters.append(fighter_rows)
        fighter_masks.append(fighter_mask)
        plans.append(plan_rows)
        plan_masks.append(plan_mask)
        scores.append(plan_scores)
        labels.append(int(label))
        group = "%s:%s:%s" % (payload.get("fight_id") or "fight",
                              payload.get("round") or 0,
                              payload.get("my_cell") or payload.get("my_id") or "cell")
        groups.append(group)
        stats["actionable"] += 1
        if notes:
            stats["notes"] += 1
    if not cells:
        return {}, {
            "rows": 0,
            **dict(stats),
            "verdict": "no actionable boards; the 30M grid core was not trained",
        }
    return ({
        "cells": np.stack(cells).astype(np.float32),
        "cell_mask": np.stack(cell_masks),
        "fighters": np.stack(fighters).astype(np.float32),
        "fighter_mask": np.stack(fighter_masks),
        "plans": np.stack(plans).astype(np.float32),
        "plan_mask": np.stack(plan_masks),
        "scores": np.stack(scores).astype(np.float32),
        "labels": np.asarray(labels, dtype=np.int64),
        "groups": np.asarray(groups),
    }, {
        "rows": len(cells),
        **dict(stats),
        "verdict": "actionable",
    })


def write_splits(arrays: Dict[str, np.ndarray], manifest: Dict[str, Any],
                 out: Path) -> Dict[str, Any]:
    """Split by fight/group hash so a board and its next turn do not straddle."""
    out.mkdir(parents=True, exist_ok=True)
    buckets = {"train": [], "val": [], "test": []}
    for index, group in enumerate(arrays["groups"]):
        digest = int(hashlib.sha256(str(group).encode()).hexdigest()[:8], 16) % 10
        key = "train" if digest < 7 else ("val" if digest < 9 else "test")
        buckets[key].append(index)
    for key, indices in buckets.items():
        subset = {name: value[indices] for name, value in arrays.items()
                  if name != "groups"}
        np.savez_compressed(out / (key + ".npz"), **subset)
        manifest[key] = len(indices)
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--boards", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--allow-empty", action="store_true",
                        help="write the manifest even when no board is actionable")
    args = parser.parse_args(argv)
    path = args.boards
    if path is None:
        path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm" / "fight_boards.jsonl"
    if not path.is_file():
        print("boards file not found:", path)
        return 2
    arrays, manifest = build(_board_rows(path))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    if not arrays:
        print("no actionable boards:", manifest)
        if not args.allow_empty:
            return 1
        return 0
    manifest = write_splits(arrays, manifest, args.out)
    print("actionable boards", len(arrays["labels"]), "split", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
