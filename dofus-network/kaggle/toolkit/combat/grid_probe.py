"""Is the recorded fight-board corpus actionable for the 30M grid core?

The grid model needs boards where the controlled fighter has AP/MP, it is the
controlled turn, and the solver can generate more than the pass plan. The first
156 boards were recorded before the page exposed the bundle's
`actionPoints`/`movementPoints` fields, so they parse but cannot produce a legal
cast. This tool counts that explicitly and prints the gate instead of letting a
trainer overfit a pass-only corpus.

    python -m combat.grid_probe
    python -m combat.grid_probe --boards "%LOCALAPPDATA%/DofusFarm/fight_boards.jsonl"
    python -m combat.grid_probe --decisions trajectories/live_policy_decisions.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from combat.battle_solver import BattleSolver, Board


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
            if not isinstance(row, dict):
                continue
            if row.get("kind") == "fight_board" and isinstance(row.get("board"), dict):
                yield row["board"]
            elif isinstance(row.get("board"), dict):
                yield row["board"]


def _decision_rows(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            board = (((row.get("decision") or {}).get("observation") or {})
                     .get("board"))
            if isinstance(board, dict):
                yield board


def probe(boards: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    total = parsed = in_fight = my_turn = ap_mp = multi_plan = 0
    plan_counts: List[int] = []
    errors: Dict[str, int] = {}
    for payload in boards:
        total += 1
        try:
            board = Board.from_fight_state(payload)
        except Exception as exc:  # noqa: BLE001 - a parse failure is data, not a crash
            errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
            continue
        parsed += 1
        if board.in_fight:
            in_fight += 1
        if board.is_my_turn:
            my_turn += 1
        me = board.me
        # The pre-fix boards decode a null AP/MP as 0. Zero AP is not an
        # actionable turn, even though it is not None.
        has_ap_mp = bool(me is not None and me.ap is not None and me.ap > 0)
        if has_ap_mp:
            ap_mp += 1
        if board.in_fight and board.is_my_turn and not board.in_preparation and has_ap_mp:
            try:
                plans = BattleSolver(beam_width=16).plan_candidates(board, 16)
            except Exception as exc:  # noqa: BLE001
                errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
                plans = []
            action_plans = [plan for plan in plans
                            if plan.actions and plan.actions[0].kind != "pass"]
            plan_counts.append(len(action_plans))
            if len(action_plans) > 1:
                multi_plan += 1
    verdict = ("actionable" if multi_plan > 0 else
               "no actionable boards: self AP/MP is null or only pass is legal")
    return {
        "rows": total,
        "parsed": parsed,
        "in_fight": in_fight,
        "my_turn": my_turn,
        "self_ap_mp": ap_mp,
        "multi_plan_boards": multi_plan,
        "plan_counts": plan_counts[:20],
        "errors": errors,
        "verdict": verdict,
        "required": "one live fight on page build 2026-10-27s with a real turn and AP/MP",
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--boards", type=Path, default=None,
                        help="fight_boards.jsonl (default: LOCALAPPDATA/DofusFarm)")
    parser.add_argument("--decisions", type=Path, default=None,
                        help="live_policy_decisions.jsonl")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    rows: List[Dict[str, Any]] = []
    if args.boards:
        rows.extend(_board_rows(args.boards))
    elif args.decisions is None:
        default = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm" / "fight_boards.jsonl"
        if default.is_file():
            rows.extend(_board_rows(default))
    if args.decisions and args.decisions.is_file():
        rows.extend(_decision_rows(args.decisions))
    if not rows:
        print("no board rows found")
        return 2
    result = probe(rows)
    if args.json:
        print(json.dumps(result, indent=1, sort_keys=True))
    else:
        print("boards %d  parsed %d  in_fight %d  my_turn %d  self_ap_mp %d  multi_plan %d"
              % (result["rows"], result["parsed"], result["in_fight"],
                 result["my_turn"], result["self_ap_mp"],
                 result["multi_plan_boards"]))
        print("verdict:", result["verdict"])
        print("required:", result["required"])
        if result["errors"]:
            print("errors:", result["errors"])
    return 0 if result["multi_plan_boards"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
