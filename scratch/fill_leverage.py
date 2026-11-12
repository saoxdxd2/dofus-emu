"""Does filling the enemy spellbook change a live decision?

The gate is a proxy: `value_ready` opening only means the critic is *allowed* to rank.
This measures the terminal metric -- the action the client would receive -- with the
critic fix held constant in both arms. The comparator is the current live path
(post-#4), not the un-edited arm.

Arm A: boards as recorded.
Arm B: the same boards with each enemy's missing spellbook supplied from another
       fight (leave-one-fight-out, so no board is filled from its own fight).

One LivePolicy per arm, walked in recorded order through decide().
"""
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
sys.path.insert(0, str(ROOT))
from combat.live_policy import LivePolicy  # noqa: E402

SRC = Path(os.environ["LOCALAPPDATA"]) / "DofusFarm" / "fight_boards.jsonl"

rows = []
with SRC.open(encoding="utf-8", errors="replace") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") == "fight_board" and isinstance(rec.get("board"), dict):
            rows.append(rec["board"])

seen = set()
boards = []
for b in rows:
    key = json.dumps({"r": b.get("round"), "t": b.get("current_fighter_id"),
                      "f": [[f.get("id"), f.get("hp"), f.get("cell")]
                            for f in (b.get("fighters") or [])]}, sort_keys=True)
    if key in seen:
        continue
    seen.add(key)
    boards.append(b)
print("unique boards:", len(boards), flush=True)


def enemies(b):
    my_id = b.get("my_id")
    for f in b.get("fighters") or []:
        if f.get("id") == my_id or f.get("team") == "player":
            continue
        if f.get("hp") is not None and f.get("hp") <= 0:
            continue
        yield f


def usable(spells):
    return bool(spells) and any(s.get("effects") for s in spells
                                if isinstance(s, dict))


# catalogue: fight -> name -> spell rows carrying effects
by_fight = defaultdict(dict)
for b in boards:
    for f in enemies(b):
        name = str(f.get("name") or "").strip()
        if name and usable(f.get("spells")):
            by_fight[b.get("fight_id")][name] = f["spells"]


def filled(b):
    fid = b.get("fight_id")
    lookup = {}
    for other, books in by_fight.items():
        if other == fid:
            continue
        for name, spells in books.items():
            lookup.setdefault(name, spells)
    out = json.loads(json.dumps(b))
    n = 0
    my_id = out.get("my_id")
    for f in out.get("fighters") or []:
        if f.get("id") == my_id or f.get("team") == "player":
            continue
        if usable(f.get("spells")):
            continue
        rows_ = lookup.get(str(f.get("name") or "").strip())
        if rows_:
            f["spells"] = rows_
            n += 1
    return out, n


def signature(answer):
    a = answer.get("action")
    return json.dumps(a, sort_keys=True) if isinstance(a, dict) else None


def plan_signature(plan):
    if plan is None:
        return None
    return json.dumps({
        "final_cell": plan.final_cell,
        "actions": [[a.kind, getattr(a, "spell_id", None),
                     getattr(a, "target_id", None), getattr(a, "from_cell", None),
                     getattr(a, "cell", None)] for a in plan.actions],
    }, sort_keys=True)


def walk(payloads):
    policy = LivePolicy()
    out = []
    for b in payloads:
        policy._last_plan = None
        try:
            answer = policy.decide(b)
        except Exception as exc:
            out.append({"sig": None, "plan": None, "ready": False,
                        "actionable": False, "err": type(exc).__name__})
            continue
        c = answer.get("contract") or {}
        out.append({"sig": signature(answer), "err": None,
                    "plan": plan_signature(getattr(policy, "_last_plan", None)),
                    "ready": bool(c.get("value_ready")),
                    "actionable": bool(c.get("actionable"))})
    return out, dict(getattr(policy, "value_choice_stats", {}) or {})


filled_payloads = []
fill_count = 0
for b in boards:
    fb, n = filled(b)
    filled_payloads.append(fb)
    fill_count += n
print("enemy spellbooks filled:", fill_count, flush=True)

a, sa = walk(boards)
print("A (as recorded) stats:", sa, flush=True)
b_, sb = walk(filled_payloads)
print("B (spellbook filled) stats:", sb, flush=True)

act = sum(1 for x, y in zip(a, b_) if x["actionable"] and y["actionable"])
ready_a = sum(1 for x, y in zip(a, b_) if x["actionable"] and y["actionable"] and x["ready"])
ready_b = sum(1 for x, y in zip(a, b_) if x["actionable"] and y["actionable"] and y["ready"])
changed = [(i, x["sig"], y["sig"]) for i, (x, y) in enumerate(zip(a, b_))
           if x["sig"] != y["sig"]]
plans = [(i, x["plan"], y["plan"]) for i, (x, y) in enumerate(zip(a, b_))
         if x["plan"] != y["plan"]]

print()
print("actionable in both arms:", act)
print("actionable AND value_ready: A %d -> B %d" % (ready_a, ready_b))
print("emitted actions changed:", len(changed))
for i, x, y in changed[:10]:
    print("  board %d\n    A %s\n    B %s" % (i, x, y))
print("whole plans changed:", len(plans))
for i, x, y in plans[:6]:
    print("  board %d\n    A %s\n    B %s" % (i, x, y))
print("errors:", Counter(x["err"] for x in a if x["err"]),
      Counter(y["err"] for y in b_ if y["err"]))