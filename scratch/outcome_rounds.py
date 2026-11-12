"""Round counts, ring scope, and whether end.won agrees with the enemy results."""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
SOURCES = [ROOT / "trajectories" / "fight_outcomes.jsonl",
           ROOT / "data" / "live_evidence" / "fight_outcomes.jsonl"]

out = []
for src in SOURCES:
    if not src.is_file():
        continue
    for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") == "fight_outcome":
            out.append(r)

print("%-7s %7s %6s %6s %6s %6s %6s  %s" % (
    "fid", "rows", "rounds", "fturns", "join", "end", "logids", "results alive (me / others)"))
fid_mismatch = 0
contradictions = 0
for r in out:
    end = r.get("end") if isinstance(r.get("end"), dict) else None
    if not end:
        continue
    log = end.get("battle_log") or []
    if not isinstance(log, list):
        continue
    ids = Counter(str(x.get("fight_id")) for x in log if isinstance(x, dict))
    rounds = sum(1 for x in log if isinstance(x, dict)
                 and x.get("type") == "GameFightNewRoundMessage")
    fturns = sum(1 for x in log if isinstance(x, dict)
                 and x.get("type") == "GameFightTurnStartMessage")
    joins = sum(1 for x in log if isinstance(x, dict)
                and x.get("type") == "GameFightJoinMessage")
    ends = sum(1 for x in log if isinstance(x, dict)
               and x.get("type") == "GameFightEndMessage")
    me = end.get("my_id")
    results = end.get("results") or []
    mine_alive = None
    others = []
    for rr in results:
        if not isinstance(rr, dict):
            continue
        if str(rr.get("id")) == str(me):
            mine_alive = rr.get("alive")
        else:
            others.append(rr.get("alive"))
    # A win means every enemy is dead. end.won is only "I am alive".
    enemies_alive = sum(1 for a in others if a is True)
    flag = ""
    if end.get("won") is True and enemies_alive > 0:
        contradictions += 1
        flag = "  <-- won=True but %d enemy alive" % enemies_alive
    if len(ids) > 1:
        fid_mismatch += 1
    print("%-7s %7d %6d %6d %6d %6d %6d  me=%s others=%s%s" % (
        r.get("fight_id"), len(log), rounds, fturns, joins, ends, len(ids),
        mine_alive, others, flag))
    if len(ids) > 1:
        print("        fight_ids in ring:", dict(ids), " end.fight_id:", end.get("fight_id"))

print("\nrings spanning >1 fight_id: %d" % fid_mismatch)
print("end.won=True while an enemy is still alive: %d" % contradictions)

# How many resolved rows would lose their verdict if the HP fallback were removed?
no_verdict = 0
for r in out:
    if r.get("won") is None:
        continue
    end = r.get("end") if isinstance(r.get("end"), dict) else None
    if not (end and isinstance(end.get("won"), bool)):
        no_verdict += 1
        print("row relying on a non-server verdict:", r.get("fight_id"),
              "won=", r.get("won"), "alive=", r.get("alive"),
              "hp_after=", r.get("hp_after"), "end=", bool(end))
print("resolved rows whose won did NOT come from a server end verdict: %d" % no_verdict)