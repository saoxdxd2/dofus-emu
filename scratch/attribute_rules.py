"""Which attribution rule can tie a battle_log damage row to the cast that caused it?

`at_action` is 300 on all 240 casts and 91/96/97/98 on the damage rows -- disjoint id
spaces, so the id join cannot work. Damage rows carry `source` and `target` but never
`spell`. Three candidate rules, measured against each other:

  source        -- the most recent preceding cast by the same source
  source+target -- ... and aimed at the same target
  source+round  -- ... and in the same round

A damage row with no preceding cast by its source (a tackle, a poison tick) must stay
unattributed under every rule: that is the control that says the rule can refuse.
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

tally = Counter()
examples = []
seen_logs = set()
for r in rows:
    if r.get("kind") != "fight_board":
        continue
    b = r.get("board") or {}
    log = b.get("battle_log")
    if not isinstance(log, list) or not log:
        continue
    # The same ring is re-published on every board of a fight; count each distinct
    # log once, keyed on its length and last row, or the totals count one fight 30x.
    key = json.dumps([len(log), log[-1]], sort_keys=True)
    if key in seen_logs:
        continue
    seen_logs.add(key)
    fighters = {f.get("id"): f for f in (b.get("fighters") or [])}
    my_id = b.get("my_id")
    last_cast = {}
    rnd = 0
    for row in log:
        t = row.get("type")
        if t == ROUND:
            rnd += 1
            continue
        if t == CAST:
            last_cast[row.get("source")] = (row.get("spell"), row.get("target"), rnd)
            tally["casts"] += 1
            continue
        if t != LOSS:
            continue
        loss = row.get("loss")
        if loss is None:
            continue
        tally["damage_rows"] += 1
        src, tgt = row.get("source"), row.get("target")
        prev = last_cast.get(src)
        if prev is None:
            tally["no_preceding_cast_by_source"] += 1
            continue
        spell, cast_target, cast_round = prev
        tally["source"] += 1
        if cast_target == tgt:
            tally["source+target"] += 1
        if cast_round == rnd:
            tally["source+round"] += 1
        if cast_target == tgt and cast_round == rnd:
            tally["source+target+round"] += 1
        enemy = (fighters.get(src) or {}).get("team") == "enemy"
        if enemy:
            tally["enemy_damage_rows"] += 1
            if cast_target == tgt and cast_round == rnd:
                tally["enemy_attributed_strict"] += 1
                if str(tgt) == str(my_id) and len(examples) < 6:
                    examples.append({
                        "monster": (fighters.get(src) or {}).get("name"),
                        "level": (fighters.get(src) or {}).get("level"),
                        "spell": spell, "loss": abs(float(loss)), "round": rnd})

print("distinct logs:", len(seen_logs))
for k in ("casts", "damage_rows", "no_preceding_cast_by_source", "source",
          "source+target", "source+round", "source+target+round",
          "enemy_damage_rows", "enemy_attributed_strict"):
    print("  %-32s %d" % (k, tally[k]))
print("enemy hits on me, attributed:")
for e in examples:
    print("   ", json.dumps(e))