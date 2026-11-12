"""Why do 4 of the 5 changed plans not change the emitted action?

`_emit` sends a walk when the plan's first cast starts on a cell the character is
not standing on. If that is the reason, the changed spell is behind a walk and the
client never sees it on this call.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
sys.path.insert(0, str(ROOT))

from combat.battle_solver import Board  # noqa: E402
from combat.live_policy import LivePolicy  # noqa: E402

SRC = Path(os.environ["LOCALAPPDATA"]) / "DofusFarm" / "fight_boards.jsonl"
rows = []
with SRC.open("r", encoding="utf-8", errors="replace") as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") == "fight_board" and isinstance(row.get("board"), dict):
            rows.append(row["board"])
seen, boards = set(), []
for b in rows:
    key = json.dumps({"r": b.get("round"), "t": b.get("current_fighter_id"),
                      "f": [[f.get("id"), f.get("hp"), f.get("cell")]
                            for f in (b.get("fighters") or [])]}, sort_keys=True)
    if key not in seen:
        seen.add(key)
        boards.append(b)

INTERESTING = [188, 252, 266, 267, 278]


def walk(open_gate):
    policy = LivePolicy()
    if not open_gate:
        policy._value_choice = lambda board, plans: None
    out = {}
    for i, b in enumerate(boards):
        if i not in INTERESTING:
            continue
        policy._last_plan = None
        answer = policy.decide(b)
        out[i] = (answer.get("action"), getattr(policy, "_last_plan", None))
    return out


before, after = walk(False), walk(True)
for i in INTERESTING:
    board = Board.from_fight_state(boards[i])
    a_plan, b_plan = before[i][1], after[i][1]
    a_act, b_act = before[i][0], after[i][0]
    first_cells = [p.actions[0].from_cell if p and p.actions else None
                   for p in (a_plan, b_plan)]
    print("board %3d my_cell=%s first_cast_from=%s emitted=%s"
          % (i, board.my_cell, first_cells,
             "SAME" if a_act == b_act else "CHANGED"))
    print("    before action %s" % json.dumps(a_act, sort_keys=True))
    print("    after  action %s" % json.dumps(b_act, sort_keys=True))