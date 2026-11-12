"""Does the board->outcome join key exist on both sides, and is it unique?"""
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

SRC = Path(os.environ["LOCALAPPDATA"]) / "DofusFarm" / "fight_boards.jsonl"
board_runs = Counter()
board_pairs = Counter()
outcome_runs = Counter()
outcome_fights = Counter()
rows = []
with SRC.open("r", encoding="utf-8", errors="replace") as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass

for r in rows:
    if r.get("kind") == "fight_board":
        b = r.get("board") or {}
        board_runs[r.get("run")] += 1
        board_pairs[(r.get("run"), b.get("fight_id"))] += 1
    elif r.get("kind") == "fight_outcome":
        outcome_runs[r.get("run")] += 1
        inner = r.get("outcome") or {}
        outcome_fights[(r.get("run"), inner.get("fight_id"))] += 1

print("board runs:", dict(board_runs))
print("outcome runs:", dict(outcome_runs))
print("board (run, fight_id) pairs:", len(board_pairs))
print("outcome (run, fight_id) pairs:", len(outcome_fights))
print("outcome pairs:", dict(outcome_fights))
shared = set(board_pairs) & set(outcome_fights)
print("pairs present on BOTH sides:", len(shared), sorted(shared)[:10])
print("board-only pairs:", sorted(set(board_pairs) - set(outcome_fights))[:10])

# How many outcome rows a run-keyed dict would keep.
by_run = defaultdict(list)
for r in rows:
    if r.get("kind") == "fight_outcome":
        by_run[r.get("run")].append((r.get("outcome") or {}).get("fight_id"))
for run, fights in by_run.items():
    print("run %s carries %d outcomes, fight_ids %s -> a run-keyed dict keeps 1"
          % (run, len(fights), fights))