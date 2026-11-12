"""What `castable` holds in the live corpus, and who reads it.

Read-only. The question is whether `castable: false` means "the client refused"
or "the call threw" -- the page cannot tell them apart, so neither can a reader.
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

LIVE = Path(os.environ["LOCALAPPDATA"]) / "DofusFarm" / "fight_boards.jsonl"
BOARDS = Path(sys.argv[1]) if len(sys.argv) > 1 else LIVE

vals = Counter()
mine_vals = Counter()
per_fighter = Counter()
rows = 0
mine_rows = 0
keys = Counter()
with BOARDS.open(encoding="utf-8", errors="replace") as fh:
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
        board = rec.get("board") or {}
        rows += 1
        my_id = board.get("my_id")
        for f in board.get("fighters") or []:
            spells = f.get("spells") or []
            if spells:
                per_fighter[f.get("team")] += 1
            for s in spells:
                if not isinstance(s, dict):
                    continue
                keys.update(s.keys())
                v = repr(s.get("castable", "<absent>"))
                vals[v] += 1
                if f.get("id") == my_id:
                    mine_rows += 1
                    mine_vals[v] += 1

print("file:", BOARDS)
print("boards:", rows)
print("fighters carrying a spell list, by team:", dict(per_fighter))
print("castable over every spell row:", dict(vals))
print("my own spell rows:", mine_rows, dict(mine_vals))
total = sum(vals.values())
if total:
    print("false share: %.1f%% of %d rows" % (100.0 * vals.get("False", 0) / total, total))
print("has 'castable' key:", "castable" in keys, "| distinct keys:", len(keys))
print("keys:", sorted(keys))