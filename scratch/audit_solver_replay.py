"""Replay the shipped solver over the captured live boards. Read-only."""
import json, os, sys, collections
sys.path.insert(0, r"C:\Users\sao\Documents\dofus-emu\dofus-network")
from combat.battle_solver import BattleSolver, Board
from combat.observation import contract_from_board
from combat import threat

P = os.path.expandvars(r"%LOCALAPPDATA%/DofusFarm/fight_boards.jsonl")
rows = []
for line in open(P, encoding="utf-8", errors="ignore"):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except Exception:
        continue
    if r.get("kind") == "fight_board":
        rows.append(r)
print("boards", len(rows))

solver = BattleSolver(beam_width=8, max_actions=4, memory=threat.default_memory)
stats = collections.Counter()
apmp = collections.Counter()
noaction_apmp = collections.Counter()
seen = set()
uniq = 0
for r in rows:
    payload = r.get("board") or {}
    key = json.dumps({"f": payload.get("fight_id"), "rd": payload.get("round"),
                      "t": payload.get("current_fighter_id"),
                      "c": payload.get("my_cell"),
                      "h": [[f.get("id"), f.get("hp"), f.get("cell")]
                            for f in payload.get("fighters") or []]}, sort_keys=True)
    if key in seen:
        continue
    seen.add(key)
    uniq += 1
    try:
        b = Board.from_fight_state(payload)
    except Exception as e:
        stats["board_error"] += 1
        continue
    c = contract_from_board(b)
    stats["actionable"] += bool(c["actionable"])
    stats["value_ready"] += bool(c["value_ready"])
    if not c["actionable"]:
        continue
    try:
        plans = solver.plan_candidates(b, 8)
    except Exception as e:
        stats["solver_error:" + type(e).__name__] += 1
        continue
    act = [p for p in plans if p.actions and p.actions[0].kind != "pass"]
    stats["turns"] += 1
    apmp[(b.my_ap, b.my_mp)] += 1
    if act:
        stats["has_action_plan"] += 1
        stats["first_kind_" + act[0].actions[0].kind] += 1
    else:
        stats["NO_ACTION_PLAN"] += 1
        noaction_apmp[(b.my_ap, b.my_mp)] += 1
        stats["nspells_" + str(len(b.spells))] += 1
        dmg = 0
        for s in b.spells:
            if s.castable and s.has_damage and 0 < s.ap_cost <= b.my_ap:
                dmg += 1
        stats["affordable_damage_spells_%d" % min(dmg, 5)] += 1
print("unique boards", uniq)
print("stats", json.dumps(dict(stats), indent=1))
print("ap,mp all actionable turns:", apmp.most_common(12))
print("ap,mp NO_ACTION_PLAN:", noaction_apmp.most_common(12))