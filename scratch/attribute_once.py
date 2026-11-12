"""Attribution rules, counted once per event.

The first pass deduped logs by (length, last row), which counts the same cast again
in every longer snapshot of the same fight's ring. The ring never truncated on this
corpus (max 163 rows against an 800 cap), so the longest log per fight contains every
earlier event exactly once. That is the honest denominator.
"""
import json
import os
from collections import Counter
from pathlib import Path

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

CAST = "GameActionFightSpellCastMessage"
LOSS = "GameActionFightLifePointsLostMessage"
ROUND = "GameFightNewRoundMessage"

longest = {}
for r in rows:
    if r.get("kind") != "fight_board":
        continue
    b = r.get("board") or {}
    log = b.get("battle_log")
    if not isinstance(log, list) or not log:
        continue
    key = (r.get("run"), b.get("fight_id"))
    if key not in longest or len(log) > len(longest[key][0]):
        longest[key] = (log, b)

tally = Counter()
per_spell = Counter()
for (run, fid), (log, b) in longest.items():
    fighters = {f.get("id"): f for f in (b.get("fighters") or [])}
    last_cast = {}
    rnd = 0
    for row in log:
        t = row.get("type")
        if t == ROUND:
            rnd += 1
        elif t == CAST:
            last_cast[row.get("source")] = (row.get("spell"), row.get("target"), rnd)
            tally["casts"] += 1
        elif t == LOSS and row.get("loss") is not None:
            tally["damage_rows"] += 1
            src, tgt = row.get("source"), row.get("target")
            prev = last_cast.get(src)
            if prev is None:
                tally["no_preceding_cast_by_source"] += 1
                continue
            spell, ct, cr = prev
            tally["source"] += 1
            if ct == tgt:
                tally["source+target"] += 1
            if cr == rnd:
                tally["source+round"] += 1
            if ct == tgt and cr == rnd:
                tally["source+target+round"] += 1
                f = fighters.get(src) or {}
                if f.get("team") == "enemy":
                    tally["enemy_attributed"] += 1
                    per_spell["%s|%s|%s" % (f.get("name"), f.get("level"), spell)] += 1

print("fights with a log:", len(longest))
for k in ("casts", "damage_rows", "no_preceding_cast_by_source", "source",
          "source+target", "source+round", "source+target+round", "enemy_attributed"):
    print("  %-32s %d" % (k, tally[k]))
print("distinct monster|level|spell keys:", len(per_spell))
for k, v in sorted(per_spell.items()):
    print("   %-40s %d hit(s)" % (k, v))