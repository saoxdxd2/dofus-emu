"""What the current reader emits, and what battle_log could attribute instead."""
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
sys.path.insert(0, str(ROOT))
from combat.live_data import build_from_boards, ATTRIBUTION  # noqa: E402

SRC = Path(os.environ["LOCALAPPDATA"]) / "DofusFarm" / "fight_boards.jsonl"

monsters, actions, fights, observed = build_from_boards(SRC)
print("=== what the reader emits today ===")
print("monsters", len(monsters), "actions", len(actions),
      "fights", len(fights), "observed_damage", len(observed))
print("ATTRIBUTION", dict(ATTRIBUTION))
wrong = 0
for f in fights:
    inner = f.get("outcome") or {}
    got = inner.get("fight_id")
    if got is not None and got != f["fight_id"]:
        wrong += 1
        print("  MIS-ATTRIBUTED: fight %s labelled with fight %s's outcome (won=%s)"
              % (f["fight_id"], got, inner.get("won")))
    elif got is None:
        print("  no outcome: run %s fight %s" % (f["run"], f["fight_id"]))
print("fights carrying another fight's verdict:", wrong, "of", len(fights))

print()
print("=== what battle_log carries ===")
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

cast_at = Counter()
loss_at = Counter()
loss_spell = Counter()
cast_keys = Counter()
loss_keys = Counter()
n_cast = n_loss = 0
for r in rows:
    if r.get("kind") != "fight_board":
        continue
    for row in (r.get("board") or {}).get("battle_log") or []:
        t = row.get("type")
        if t == "GameActionFightSpellCastMessage":
            n_cast += 1
            cast_at[row.get("at_action")] += 1
            cast_keys.update(k for k, v in row.items() if v is not None)
        elif t == "GameActionFightLifePointsLostMessage":
            n_loss += 1
            loss_at[row.get("at_action")] += 1
            loss_spell[repr(row.get("spell"))] += 1
            loss_keys.update(k for k, v in row.items() if v is not None)
print("cast rows", n_cast, "distinct at_action", len(cast_at),
      "top", cast_at.most_common(4))
print("loss rows", n_loss, "distinct at_action", len(loss_at),
      "top", loss_at.most_common(4))
print("loss rows' spell field:", loss_spell.most_common(4))
print("non-null cast keys:", dict(cast_keys))
print("non-null loss keys:", dict(loss_keys))