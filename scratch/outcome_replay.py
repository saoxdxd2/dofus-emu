"""Replay the recorded fights through the real FightOutcomeLog.resolve().

Reconstructs each historical pending claim from the ledger row, then calls the
live entry point. Read-only apart from a temp output file.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
sys.path.insert(0, str(ROOT))

from combat.fight_estimate import FightOutcomeLog  # noqa: E402

SOURCES = [ROOT / "trajectories" / "fight_outcomes.jsonl",
           ROOT / "data" / "live_evidence" / "fight_outcomes.jsonl"]

tmp = Path(tempfile.mkdtemp()) / "replay.jsonl"
print("%-7s %6s | %-6s %-6s %-11s | %-8s %-8s %-11s | %-5s %-5s" % (
    "fid", "oldwon", "oldloss", "newloss", "newsrc", "oldturn", "newturn",
    "wonsrc", "reward", "same"))
changed_loss = 0
changed_turns = 0
changed_won = 0
total = 0
for src in SOURCES:
    if not src.is_file():
        continue
    for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") != "fight_outcome":
            continue
        if row.get("status") == "session_end":
            continue  # flush() path, not resolve()
        total += 1
        end = row.get("end") if isinstance(row.get("end"), dict) else None
        hp_before = row.get("hp_before")
        hp_after = row.get("hp_after")
        log = FightOutcomeLog(tmp)
        log.start(row.get("estimate") or {},
                  {"in_fight": True, "fight_id": row.get("fight_id"),
                   "hp": {"life": hp_before, "max": hp_before}},
                  row.get("session") or "", started_by=row.get("started_by"))
        # Restore the historical clock so the freshness test sees the real gap.
        log.pending["at"] = row.get("started_at")
        log.pending["hp_before"] = hp_before
        log.pending["fight_id"] = row.get("fight_id")
        obs = {"in_world": True, "in_fight": False, "alive": True,
               "hp": {"life": hp_after, "max": hp_after},
               "last_fight_end": end}
        new = log.resolve(obs, row.get("session") or "") or {}
        ol, nl = row.get("hp_loss"), new.get("hp_loss")
        ot, nt = row.get("turns"), new.get("turns")
        ow, nw = row.get("won"), new.get("won")
        if ol != nl:
            changed_loss += 1
        if ot != nt:
            changed_turns += 1
        if ow != nw:
            changed_won += 1
        print("%-7s %6s | %-6s %-6s %-11s | %-8s %-8s %-11s | %-5s %-5s" % (
            row.get("fight_id"), ow, ol, nl, new.get("hp_loss_source"),
            ot, nt, new.get("won_source"), new.get("reward"),
            "" if (ol == nl and ot == nt and ow == nw) else "CHANGED"))

print("\nresolved rows replayed: %d" % total)
print("hp_loss changed: %d   turns changed: %d   won changed: %d" % (
    changed_loss, changed_turns, changed_won))