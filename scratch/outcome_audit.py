"""Re-derive fight_outcome labelling from the recorded ledgers. Read-only."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
SOURCES = [ROOT / "trajectories" / "fight_outcomes.jsonl",
           ROOT / "data" / "live_evidence" / "fight_outcomes.jsonl"]

LOSS_TYPES = {"GameActionFightLifePointsLostMessage",
              "GameActionFightLifePointsLostFromPermanentDamagesMessage"}
ROUND_TYPES = {"GameFightNewRoundMessage"}
TURN_TYPES = {"GameFightTurnStartMessage"}


def rows(path):
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def log_stats(end, my_id):
    log = (end or {}).get("battle_log") or []
    if not isinstance(log, list):
        return None
    loss = 0.0
    hits = 0
    rounds = 0
    turns = 0
    types = Counter()
    for r in log:
        if not isinstance(r, dict):
            continue
        t = r.get("type")
        types[t] += 1
        if t in ROUND_TYPES:
            rounds += 1
        if t in TURN_TYPES:
            turns += 1
        if t in LOSS_TYPES:
            tgt = r.get("target")
            v = r.get("loss")
            if my_id is not None and tgt is not None and str(tgt) != str(my_id):
                continue
            if isinstance(v, (int, float)):
                loss += abs(float(v))
                hits += 1
    return {"loss": loss, "hits": hits, "rounds": rounds, "turns": turns,
            "rows": len(log), "types": types}


all_outcomes = []
for src in SOURCES:
    rr = [r for r in rows(src) if r.get("kind") == "fight_outcome"]
    print("%s: %d fight_outcome rows" % (src.name, len(rr)))
    for r in rr:
        r["_src"] = src.name
    all_outcomes.extend(rr)

print("\ntotal fight_outcome rows: %d" % len(all_outcomes))
print("won value census:", Counter(repr(r.get("won")) for r in all_outcomes))
print("status census:", Counter(repr(r.get("status")) for r in all_outcomes))
print("turns census:", Counter(repr(r.get("turns")) for r in all_outcomes))
print("hp_loss census:", Counter(repr(r.get("hp_loss")) for r in all_outcomes))

with_end = [r for r in all_outcomes if isinstance(r.get("end"), dict)]
print("\nrows carrying an end dict: %d" % len(with_end))
print("end.won census:", Counter(repr((r.get("end") or {}).get("won")) for r in with_end))
print("end has battle_log:", sum(1 for r in with_end
                                if isinstance((r.get("end") or {}).get("battle_log"), list)))
print("end.my_id present:", sum(1 for r in with_end
                               if (r.get("end") or {}).get("my_id") is not None))

print("\n=== per-fight: reported hp_loss vs battle-log loss ===")
print("%-22s %6s %6s %8s %8s %6s %6s %6s %s" % (
    "src", "fid", "won", "hp_before", "hp_after", "rep_loss", "log_loss", "rounds", "end.won"))
mismatch = 0
zero_but_real = 0
for r in all_outcomes:
    end = r.get("end") if isinstance(r.get("end"), dict) else None
    st = log_stats(end, (end or {}).get("my_id"))
    if st is None:
        continue
    rep = r.get("hp_loss")
    real = st["loss"]
    flag = ""
    if isinstance(rep, (int, float)) and abs(float(rep) - real) > 0.5:
        mismatch += 1
        flag = "  <-- MISMATCH"
        if float(rep) == 0.0 and real > 0:
            zero_but_real += 1
    print("%-22s %6s %6s %8s %8s %6s %8.1f %6d %s%s" % (
        r["_src"][:22], r.get("fight_id"), r.get("won"), r.get("hp_before"),
        r.get("hp_after"), rep, real, st["rounds"], (end or {}).get("won"), flag))

print("\nmismatches: %d   reported 0.0 while log shows real damage: %d" % (mismatch, zero_but_real))

types = Counter()
for r in with_end:
    st = log_stats(r.get("end"), (r.get("end") or {}).get("my_id"))
    if st:
        types.update(st["types"])
print("\nbattle_log row types across all ends:")
for t, n in types.most_common(30):
    print("  %-58s %d" % (t, n))