"""Why does contract_from_board disagree with the contract the page recorded?

64 of 333 boards: mine actionable=True, recorded actionable=False. Either the
instrument is wrong, the payload is not the one the contract was computed from, or
the recorded contract is stale. This prints the differences instead of guessing.
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
sys.path.insert(0, str(ROOT))
from combat.battle_solver import Board  # noqa: E402
from combat.observation import contract_from_board  # noqa: E402

SRC = Path(os.environ["LOCALAPPDATA"]) / "DofusFarm" / "fight_boards.jsonl"

diff_keys = Counter()
shown = 0
warn_delta = Counter()
err_delta = Counter()
with SRC.open(encoding="utf-8", errors="replace") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") != "fight_board":
            continue
        raw = rec.get("board") or {}
        board = Board.from_fight_state(raw)
        mine = contract_from_board(board)
        theirs = raw.get("contract") or {}
        if not theirs:
            continue
        if (mine.get("actionable") == theirs.get("actionable")
                and mine.get("value_ready") == theirs.get("value_ready")):
            continue
        for k in sorted(set(mine) | set(theirs)):
            if mine.get(k) != theirs.get(k):
                diff_keys[k] += 1
        for w in set(mine.get("warnings") or []) - set(theirs.get("warnings") or []):
            warn_delta["mine_only:" + w.split(":")[0]] += 1
        for w in set(theirs.get("warnings") or []) - set(mine.get("warnings") or []):
            warn_delta["theirs_only:" + w.split(":")[0]] += 1
        for e in set(mine.get("errors") or []) - set(theirs.get("errors") or []):
            err_delta["mine_only:" + e.split(":")[0]] += 1
        for e in set(theirs.get("errors") or []) - set(mine.get("errors") or []):
            err_delta["theirs_only:" + e.split(":")[0]] += 1
        if shown < 2:
            shown += 1
            print("=== fight %s round %s ===" % (raw.get("fight_id"), raw.get("round")))
            print(" recorded:", json.dumps(theirs, sort_keys=True)[:600])
            print(" mine    :", json.dumps(mine, sort_keys=True)[:600])
            print(" payload is_my_turn=%r in_fight=%r my_id=%r"
                  % (raw.get("is_my_turn"), raw.get("in_fight"), raw.get("my_id")))
            print(" board   is_my_turn=%r in_fight=%r my_id=%r me=%r"
                  % (board.is_my_turn, board.in_fight, board.my_id,
                     None if board.me is None else board.me.alive))
            fs = raw.get("fight_state")
            if isinstance(fs, dict):
                print(" fight_state is_my_turn=%r in_fight=%r"
                      % (fs.get("is_my_turn"), fs.get("in_fight")))
            print()

print("keys that differ:", dict(diff_keys))
print("warning deltas:", dict(warn_delta))
print("error deltas:", dict(err_delta))