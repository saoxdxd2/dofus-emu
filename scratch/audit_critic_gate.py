"""Does the trained critic ever fire on the solver-first path? Read-only."""
import json, os, sys
sys.path.insert(0, r"C:\Users\sao\Documents\dofus-emu\dofus-network")
from combat.live_policy import LivePolicy, GRID_RANKER_ENABLED, HP_RANK_ENABLED
from combat.battle_solver import Board
from combat.observation import contract_from_board

print("GRID_RANKER_ENABLED", GRID_RANKER_ENABLED, "HP_RANK_ENABLED", HP_RANK_ENABLED)
P = os.path.expandvars(r"%LOCALAPPDATA%/DofusFarm/fight_boards.jsonl")
rows = [json.loads(l) for l in open(P, encoding="utf-8", errors="ignore") if l.strip()]
rows = [r for r in rows if r.get("kind") == "fight_board"]

pol = LivePolicy(beam_width=8, max_actions=4)
print("solver_first()", pol._solver_first())
print("cfg", pol.cfg.__dict__)
found = 0
for r in rows:
    payload = r["board"]
    b = Board.from_fight_state(payload)
    c = contract_from_board(b)
    if not (c["actionable"] and c["value_ready"]):
        continue
    found += 1
    print("--- value_ready board fight", b.fight_id, "round", b.round,
          "ap", b.my_ap, "mp", b.my_mp)
    print("    _value_ready BEFORE any decide:", pol._value_ready)
    d1 = pol.decide(payload)
    print("    decide#1 reason", d1.get("reason"), "action", (d1.get("action") or {}).get("type"),
          "policy.value", (d1.get("policy") or {}).get("value"))
    print("    value_choice_stats after #1:", pol.value_choice_stats,
          "hp_choice_stats:", pol.hp_choice_stats)
    print("    _value_ready AFTER #1:", pol._value_ready)
    d2 = pol.decide(payload)
    print("    decide#2 reason", d2.get("reason"),
          "value_choice_stats:", pol.value_choice_stats,
          "hp:", pol.hp_choice_stats)
    if found >= 3:
        break
print("value_ready+actionable boards found:", found)