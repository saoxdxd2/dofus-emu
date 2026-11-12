"""Why does the solver find no action plan on a full-AP/MP live board? Read-only."""
import json, os, sys, collections
sys.path.insert(0, r"C:\Users\sao\Documents\dofus-emu\dofus-network")
from combat.battle_solver import BattleSolver, Board
from combat.observation import contract_from_board
from combat import threat

P = os.path.expandvars(r"%LOCALAPPDATA%/DofusFarm/fight_boards.jsonl")
rows = [json.loads(l) for l in open(P, encoding="utf-8", errors="ignore") if l.strip()]
rows = [r for r in rows if r.get("kind") == "fight_board"]
solver = BattleSolver(beam_width=8, max_actions=4, memory=threat.default_memory)
seen = set()
shown = 0
spellflag = collections.Counter()
apcost = collections.Counter()
cellcount = collections.Counter()
reachstats = collections.Counter()
for r in rows:
    payload = r.get("board") or {}
    key = json.dumps({"f": payload.get("fight_id"), "rd": payload.get("round"),
                      "t": payload.get("current_fighter_id"), "c": payload.get("my_cell"),
                      "h": [[f.get("id"), f.get("hp"), f.get("cell")]
                            for f in payload.get("fighters") or []]}, sort_keys=True)
    if key in seen:
        continue
    seen.add(key)
    b = Board.from_fight_state(payload)
    c = contract_from_board(b)
    if not c["actionable"]:
        continue
    plans = solver.plan_candidates(b, 8)
    act = [p for p in plans if p.actions and p.actions[0].kind != "pass"]
    if act:
        continue
    cellcount[len(b.cells.flags)] += 1
    for s in b.spells:
        spellflag[(bool(s.castable), bool(s.has_damage), s.ap_cost > 0)] += 1
        apcost[s.ap_cost] += 1
    occ = frozenset(f.cell for f in b.fighters if f.alive and f.id != b.my_id)
    reach = []
    try:
        reach = b.cells.reachable(b.my_cell, float(b.my_mp), occ)
        reachstats["reachable_n"] += len(reach)
        reachstats["boards"] += 1
        reachstats["valid_mycell"] += bool(b.cells.valid(b.my_cell))
    except Exception as e:
        reachstats["err_" + type(e).__name__] += 1
    if shown < 2 and b.my_ap >= 6 and b.my_mp >= 3:
        shown += 1
        print("=== NO-AP board fight", b.fight_id, "round", b.round,
              "ap", b.my_ap, "mp", b.my_mp, "mycell", b.my_cell,
              "cells", len(b.cells.flags))
        print("  contract errors", c["errors"])
        print("  contract warnings", c["warnings"][:12])
        print("  enemies", [(f.id, f.name, f.cell, f.hp, f.alive, len(f.spells))
                            for f in b.enemies])
        print("  reachable cells", len(reach))
        for s in b.spells[:26]:
            print("   spell id=%-6s ap=%-3s castable=%-5s dmg=%-5s heal=%-5s rmin=%s rmax=%s los=%s effects=%d name=%s"
                  % (s.id, s.ap_cost, s.castable, s.has_damage, s.has_heal,
                     s.min_range, s.max_range, s.requires_los, len(s.effects), s.name))
print("cell_count on no-action boards:", cellcount.most_common(6))
print("spell (castable,has_damage,ap>0) counts:", spellflag.most_common(10))
print("ap_cost distribution:", apcost.most_common(12))
print("reach stats:", dict(reachstats))