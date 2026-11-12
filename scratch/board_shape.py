"""What does a live fight board actually carry, and what do the outcome rows look like?

The reader joins on `last_actions` / `action_id` / `spell_id` / `run` / `outcome`.
Every one of those names has to be checked against the writer's real output before
the reader is changed.
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
SRC = Path(os.environ["LOCALAPPDATA"]) / "DofusFarm" / "fight_boards.jsonl"
print("source:", SRC, "exists:", SRC.is_file())

kinds = Counter()
board_keys = Counter()
last_actions_len = Counter()
battle_log_len = Counter()
la_keys = Counter()
bl_keys = Counter()
bl_types = Counter()
outcome_keys = Counter()
outcome_samples = []
n_boards = 0

with SRC.open("r", encoding="utf-8", errors="replace") as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        kinds[row.get("kind")] += 1
        if row.get("kind") == "fight_board":
            n_boards += 1
            b = row.get("board") or {}
            board_keys.update(b.keys())
            la = b.get("last_actions")
            bl = b.get("battle_log")
            last_actions_len[len(la) if isinstance(la, list) else repr(la)] += 1
            battle_log_len[len(bl) if isinstance(bl, list) else repr(bl)] += 1
            for r in (la or []):
                if isinstance(r, dict):
                    la_keys.update(r.keys())
            for r in (bl or []):
                if isinstance(r, dict):
                    bl_keys.update(r.keys())
                    bl_types[r.get("type")] += 1
        elif row.get("kind") == "fight_outcome":
            outcome_keys.update(row.keys())
            if len(outcome_samples) < 2:
                outcome_samples.append({k: row.get(k) for k in
                                        ("kind", "run", "session", "fight_id",
                                         "won", "outcome", "hp_loss", "turns")})

print("kinds:", dict(kinds))
print("boards:", n_boards)
print("board keys:", dict(board_keys))
print("last_actions lengths:", dict(last_actions_len))
print("battle_log lengths:", dict(battle_log_len))
print("last_actions row keys:", dict(la_keys))
print("battle_log row keys:", dict(bl_keys))
print("battle_log types:", dict(bl_types))
print("outcome row keys:", dict(outcome_keys))
for s in outcome_samples:
    print("  outcome sample:", json.dumps(s))