"""Which Boorbear hit does the reader lose, and why?"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
sys.path.insert(0, str(ROOT))
from combat.live_data import _fight_actions, ATTRIBUTION  # noqa: E402

SRC = Path(os.environ["LOCALAPPDATA"]) / "DofusFarm" / "fight_boards.jsonl"
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

# Find the fight whose fighters include Boorbear.
target = None
for r in rows:
    if r.get("kind") != "fight_board":
        continue
    b = r.get("board") or {}
    if any(f.get("name") == "Boorbear" for f in (b.get("fighters") or [])):
        target = (r.get("run"), b.get("fight_id"))
        break
print("Boorbear fight:", target)

group = [r for r in rows if r.get("kind") == "fight_board"
         and (r.get("run"), (r.get("board") or {}).get("fight_id")) == target]
group.sort(key=lambda r: r.get("ts") or 0)
print("boards in this fight:", len(group))

seen = set()
for r in group:
    b = r.get("board") or {}
    log = b.get("battle_log")
    acts = _fight_actions(b)
    for a in acts:
        if not a.get("dealt"):
            continue
        fighters = {f.get("id"): f for f in (b.get("fighters") or [])}
        src = fighters.get(a["source_id"]) or {}
        key = (target[0], target[1], a.get("round"), a["source_id"], a["spell_id"],
               a.get("target_id"), a.get("destination_cell"), a.get("log_index"))
        fresh = key not in seen
        seen.add(key)
        print("log_len=%3s idx=%3s round=%s spell=%s dealt=%s team=%s name=%s %s"
              % (len(log) if isinstance(log, list) else None, a["log_index"],
                 a["round"], a["spell_id"], a["dealt"], src.get("team"),
                 src.get("name"), "NEW" if fresh else "dup"))
print("distinct attributed events:", len(seen))