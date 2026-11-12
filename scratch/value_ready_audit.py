"""Is the trained critic's gate ever open on a live board?

`_value_ready` is set from contract["value_ready"], and value_ready requires no
errors and no critical warning -- notably enemy_spells_missing. If it is False on
every recorded board, moving the assignment above the solver return changes
nothing, and the honest finding is "the critic cannot run", not "the critic runs".
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
sys.path.insert(0, str(ROOT))

from combat.battle_solver import Board  # noqa: E402
from combat.observation import live_view  # noqa: E402

SRC = Path(os.environ["LOCALAPPDATA"]) / "DofusFarm" / "fight_boards.jsonl"
print("source:", SRC, "exists:", SRC.is_file())

boards = []
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
            boards.append(row["board"])
print("boards:", len(boards))
if boards:
    print("board keys:", sorted(boards[0].keys()))

ready = Counter()
actionable = Counter()
warn = Counter()
err = Counter()
seen = set()
unique = 0
for b in boards:
    key = json.dumps({"r": b.get("round"), "t": b.get("current_fighter_id"),
                      "f": [[f.get("id"), f.get("hp"), f.get("cell")]
                            for f in (b.get("fighters") or [])]}, sort_keys=True)
    if key in seen:
        continue
    seen.add(key)
    unique += 1
    try:
        board = Board.from_fight_state(b)
        obs, contract = live_view(board)
    except Exception as exc:
        ready["EXC:%s" % type(exc).__name__] += 1
        continue
    ready[repr(contract.get("value_ready"))] += 1
    actionable[repr(contract.get("actionable"))] += 1
    for w in contract.get("warnings") or []:
        warn[w.split(":")[0]] += 1
    for e in contract.get("errors") or []:
        err[e.split(":")[0]] += 1

print("unique boards:", unique)
print("value_ready:", dict(ready))
print("actionable:", dict(actionable))
print("\nwarning families:")
for k, n in warn.most_common(15):
    print("  %-40s %d" % (k, n))
print("\nerror families:")
for k, n in err.most_common(15):
    print("  %-40s %d" % (k, n))