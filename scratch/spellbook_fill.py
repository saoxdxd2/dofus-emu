"""Would a name-keyed monster spellbook open the critic's gate?

`contract_from_board` closes `value_ready` when any live enemy has no spells or no
effects. The client publishes a spellbook for only some enemies, so the question is
whether the rest of the corpus can supply the missing one -- measured leave-one-fight-out,
because filling a board from its own fight is circular.

The instrument is `contract_from_board` itself, and it is first checked against the
`contract` block the live page recorded on the same board.
"""
import json
import dataclasses
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
sys.path.insert(0, str(ROOT))
from combat.battle_solver import Board  # noqa: E402
from combat.observation import contract_from_board  # noqa: E402

SRC = Path(os.environ["LOCALAPPDATA"]) / "DofusFarm" / "fight_boards.jsonl"

records = []
with SRC.open(encoding="utf-8", errors="replace") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") == "fight_board":
            records.append(rec)
print("board records:", len(records))

# --- does the instrument agree with what the page recorded? ---
agree = disagree = no_recorded = 0
boards = []
for rec in records:
    raw = rec.get("board") or {}
    try:
        board = Board.from_fight_state(raw)
    except Exception as exc:
        print("  unparseable:", type(exc).__name__, str(exc)[:80])
        continue
    mine = contract_from_board(board)
    theirs = raw.get("contract") or {}
    if not theirs:
        no_recorded += 1
    elif (mine.get("value_ready") == theirs.get("value_ready")
          and mine.get("actionable") == theirs.get("actionable")):
        agree += 1
    else:
        disagree += 1
        if disagree <= 3:
            print("  MISMATCH mine=%s theirs=%s" % (
                {k: mine.get(k) for k in ("actionable", "value_ready")},
                {k: theirs.get(k) for k in ("actionable", "value_ready")}))
    boards.append((rec, raw, board, mine))
print("instrument vs recorded contract: agree %d, disagree %d, absent %d"
      % (agree, disagree, no_recorded))

# --- the catalogue, keyed on the only identity a board offers ---
def enemy_rows(raw):
    my_id = raw.get("my_id")
    for f in raw.get("fighters") or []:
        if f.get("id") == my_id or f.get("team") == "player":
            continue
        yield f

by_fight = defaultdict(dict)   # fight_id -> name -> spell rows (with effects)
for rec, raw, _b, _c in boards:
    fid = raw.get("fight_id")
    for f in enemy_rows(raw):
        name = str(f.get("name") or "").strip()
        spells = f.get("spells") or []
        if not name or not spells:
            continue
        if not any(s.get("effects") for s in spells if isinstance(s, dict)):
            continue
        by_fight[fid][name] = spells

print("fights contributing a usable spellbook:", len(by_fight))
print("distinct monster names with effects:",
      len({n for books in by_fight.values() for n in books}))

# --- leave-one-fight-out fill ---
base_ready = filled_ready = actionable = 0
base_act_ready = filled_act_ready = 0
enemies_filled = enemies_still_missing = 0
for rec, raw, board, mine in boards:
    fid = raw.get("fight_id")
    ready = bool(mine.get("value_ready"))
    act = bool(mine.get("actionable"))
    actionable += act
    base_ready += ready
    base_act_ready += (act and ready)

    lookup = {}
    for other_fid, books in by_fight.items():
        if other_fid == fid:
            continue
        for name, spells in books.items():
            lookup.setdefault(name, spells)

    changed = False
    new_fighters = []
    for fighter in board.fighters:
        if fighter.id == board.my_id or fighter.team == "player" or not fighter.alive:
            new_fighters.append(fighter)
            continue
        has = bool(fighter.spells) and any(s.effects for s in fighter.spells)
        if has:
            new_fighters.append(fighter)
            continue
        name = str(getattr(fighter, "name", "") or "").strip()
        rows = lookup.get(name)
        if not rows:
            enemies_still_missing += 1
            new_fighters.append(fighter)
            continue
        new_fighters.append(dataclasses.replace(
            fighter, spells=tuple(Board._spell(s) for s in rows)))
        enemies_filled += 1
        changed = True

    after = contract_from_board(
        dataclasses.replace(board, fighters=tuple(new_fighters))) if changed else mine
    filled_ready += bool(after.get("value_ready"))
    filled_act_ready += (act and bool(after.get("value_ready")))

n = len(boards)
print()
print("boards parsed: %d | actionable: %d" % (n, actionable))
print("value_ready      before %d (%.1f%%)  after %d (%.1f%%)"
      % (base_ready, 100.0 * base_ready / max(1, n), filled_ready,
         100.0 * filled_ready / max(1, n)))
print("actionable+ready before %d (%.1f%% of actionable)  after %d (%.1f%%)"
      % (base_act_ready, 100.0 * base_act_ready / max(1, actionable),
         filled_act_ready, 100.0 * filled_act_ready / max(1, actionable)))
print("enemy spellbooks filled from another fight:", enemies_filled)
print("enemies still with no spellbook anywhere in the corpus:", enemies_still_missing)