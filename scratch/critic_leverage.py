"""Does opening the critic's gate change any live decision?

The pre-fix code set `_value_ready` after the solver-first return, so on the default
path `_value_choice` saw the `__init__` False and returned None on every turn. The
faithful "before" arm is therefore `_value_choice -> None`, with the same
grid -> hp -> plans[0] fallback behind it.

One LivePolicy instance per arm, walked over the session's boards in recorded order,
through decide() -- the entry point the combat server calls.
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
sys.path.insert(0, str(ROOT))

from combat.live_policy import LivePolicy  # noqa: E402

SRC = Path(os.environ["LOCALAPPDATA"]) / "DofusFarm" / "fight_boards.jsonl"
if not SRC.is_file():
    SRC = ROOT / "trajectories" / "fight_boards.jsonl"
print("source:", SRC, "exists:", SRC.is_file(), flush=True)

rows = []
with SRC.open("r", encoding="utf-8", errors="replace") as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") == "fight_board" and isinstance(row.get("board"), dict):
            rows.append(row["board"])
print("board rows:", len(rows), flush=True)

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


def signature(answer):
    a = answer.get("action")
    if not isinstance(a, dict):
        return None
    # The emitter writes camelCase (`spellId`, `targetId`) and a move carries a
    # `path`. Reading snake_case here compared only `type` and reported 0 changes.
    return json.dumps(a, sort_keys=True)


def plan_signature(plan):
    if plan is None:
        return None
    return json.dumps({
        "final_cell": plan.final_cell,
        "actions": [[a.kind, getattr(a, "spell_id", None),
                     getattr(a, "target_id", None), getattr(a, "from_cell", None),
                     getattr(a, "cell", None)] for a in plan.actions],
    }, sort_keys=True)


def walk(open_gate):
    policy = LivePolicy()
    if not open_gate:
        policy._value_choice = lambda board, plans: None
    out = []
    for b in boards:
        policy._last_plan = None
        try:
            answer = policy.decide(b)
        except Exception as exc:
            out.append({"sig": None, "plan": None, "err": type(exc).__name__,
                        "ready": None, "actionable": None})
            continue
        contract = answer.get("contract") or {}
        out.append({"sig": signature(answer), "err": None,
                    "plan": plan_signature(getattr(policy, "_last_plan", None)),
                    "ready": bool(contract.get("value_ready")),
                    "actionable": bool(contract.get("actionable")),
                    "reason": answer.get("reason")})
    stats = dict(getattr(policy, "value_choice_stats", {}) or {})
    return out, stats


before, s_before = walk(False)
print("BEFORE stats:", s_before, flush=True)
after, s_after = walk(True)
print("AFTER  stats:", s_after, flush=True)

changed = []
plan_changed = []
both_actionable = 0
ready_and_actionable = 0
for i, (b, a) in enumerate(zip(before, after)):
    if b["actionable"] and a["actionable"]:
        both_actionable += 1
        if a["ready"]:
            ready_and_actionable += 1
    if b["sig"] != a["sig"]:
        changed.append((i, b["sig"], a["sig"], a["ready"]))
    if b["plan"] != a["plan"]:
        plan_changed.append((i, b["plan"], a["plan"], a["ready"]))

print("actionable boards:", both_actionable)
print("actionable AND value_ready:", ready_and_actionable)
print("emitted actions changed:", len(changed))
for i, sb, sa, ready in changed[:12]:
    print("  board %d ready=%s\n    before %s\n    after  %s" % (i, ready, sb, sa))
print("whole plans changed:", len(plan_changed))
for i, sb, sa, ready in plan_changed[:12]:
    print("  board %d ready=%s\n    before %s\n    after  %s" % (i, ready, sb, sa))
print("reasons after:", Counter(r.get("reason") for r in after if r["actionable"]))