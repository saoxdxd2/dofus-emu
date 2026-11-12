"""Per-fight attributable events: every board's log vs the longest log only."""
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
sys.path.insert(0, str(ROOT))
from combat.live_data import _fight_actions  # noqa: E402

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

groups = {}
for r in rows:
    if r.get("kind") != "fight_board":
        continue
    b = r.get("board") or {}
    groups.setdefault((r.get("run"), b.get("fight_id")), []).append(r)

total_reader = Counter()
total_longest = Counter()
for key, group in groups.items():
    group.sort(key=lambda r: r.get("ts") or 0)
    seen = set()
    reader = Counter()
    for r in group:
        b = r.get("board") or {}
        fighters = {f.get("id"): f for f in (b.get("fighters") or [])}
        for a in _fight_actions(b):
            if not a.get("dealt"):
                continue
            src = fighters.get(a["source_id"]) or {}
            if src.get("team") != "enemy":
                continue
            ek = (a.get("round"), a["source_id"], a["spell_id"],
                  a.get("target_id"), a.get("destination_cell"), a.get("log_index"))
            if ek in seen:
                continue
            seen.add(ek)
            slot = "%s|%s|%s" % (src.get("name"), src.get("level"), a["spell_id"])
            reader[slot] += 1
    longest = max(group, key=lambda r: len((r.get("board") or {}).get("battle_log") or []))
    lb = longest.get("board") or {}
    lf = {f.get("id"): f for f in (lb.get("fighters") or [])}
    longest_c = Counter()
    for a in _fight_actions(lb):
        if not a.get("dealt"):
            continue
        src = lf.get(a["source_id"]) or {}
        if src.get("team") != "enemy":
            continue
        slot = "%s|%s|%s" % (src.get("name"), src.get("level"), a["spell_id"])
        longest_c[slot] += 1
    if reader != longest_c:
        print("DIFF fight", key)
        print("   every board:", dict(reader))
        print("   longest    :", dict(longest_c))
        print("   log lengths:", [len((r.get("board") or {}).get("battle_log") or [])
                                  for r in group])
    total_reader.update(reader)
    total_longest.update(longest_c)

print("reader total :", sum(total_reader.values()), dict(total_reader))
print("longest total:", sum(total_longest.values()), dict(total_longest))