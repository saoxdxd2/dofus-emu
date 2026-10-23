"""Turn a real session log into a bandit dataset, and say whether it is learnable.

The simulator cannot teach this decision — every failure it produces is transient, so
"continue" is always right and always the same answer (now.md §3). Real failures are
different: some are persistent, and which repair works is a fact about the game that
nothing here can predict.

What a real log gives you is *not* counterfactuals. It gives (state, action, reward):
what was done and what happened. That is enough to rank repairs only if the alternatives
were actually taken sometimes, which is why the controller has `--explore`. This script
therefore does two things, in this order:

1. **Reports coverage.** For each (cause, action) pair, how many samples, and how often
   it worked. If an action was never taken for a cause, no amount of modelling can rank
   it, and the honest output is "go collect more", not a trained model.
2. **Writes the dataset** in the same row format the trainers use, with the action and
   the observed reward, so a model can be fitted once coverage exists.

The feature extractor is deliberately tolerant: the live observation's schema is not
fully known here, so it takes what it finds, records which keys it actually saw, and
prints them. That print is how the schema gets discovered instead of assumed.

    python ai/export_decision_log.py --log "%LOCALAPPDATA%\\DofusFarm\\autopilot.jsonl"
    python ai/export_decision_log.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ACTIONS = ["continue", "retry", "recover", "replan"]
ACTION_INDEX = {a: i for i, a in enumerate(ACTIONS)}

# Minimum samples per (cause, action) before a comparison is worth making. Below this
# the "winner" is a coin flip, and a table built on four samples gets quoted anyway.
MIN_PER_CELL = 8


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def features(obs: Dict[str, Any], error: Optional[str],
             last_tool: Optional[str]) -> Tuple[List[float], Dict[str, bool]]:
    """24 numbers from a logged observation, plus which inputs were actually present.

    The second return value is the point. A featuriser that silently substitutes 0.0
    for a missing field trains a model on a state it never saw; knowing which fields
    were found is the difference between a feature and a placeholder.
    """
    inv = obs.get("inventory") if isinstance(obs.get("inventory"), dict) else {}
    active = obs.get("quests_active") if isinstance(obs.get("quests_active"), dict) else {}
    tracker = obs.get("current_objective") if isinstance(obs.get("current_objective"), dict) else {}
    capacity = _num(obs.get("inventory_capacity"))
    seen = {
        "inventory": isinstance(obs.get("inventory"), dict),
        "quests_active": isinstance(obs.get("quests_active"), dict),
        "current_objective": bool(tracker),
        "map_id": obs.get("map_id") is not None,
        "level": obs.get("level") is not None,
        "inventory_capacity": bool(capacity),
    }

    from agent.controller import error_class
    cls = error_class(error) if error else "none"
    vec = [
        min(1.0, _num(obs.get("level"), 60) / 200.0),
        1.0 if obs.get("map_id") is not None else 0.0,
        min(1.0, len(inv) / 20.0),
        min(1.0, _num(obs.get("objectives_completed")) / 50.0),
        min(1.0, _num(obs.get("quests_completed")) / 20.0),
        1.0 if tracker else 0.0,
        min(1.0, _num(tracker.get("type_id"), -1) / 20.0),
        min(1.0, len(active) / 5.0),
    ]
    # streak / last_ok / steps are not in the observation; they are per-session
    # history. Filled by the caller below.
    vec += [0.0, 0.0, 0.0]
    vec.append((["knowledge.search", "nav.goto", "nav.follow", "quest.list",
                 "quest.advance", "farm.start", "farm.stop", "fight.join",
                 "fight.pass", "inv.list", "inv.sell", "hdv.scan", "hdv.list",
                 "chat.say", "state.snapshot", "safe.stop", "knowledge.remember",
                 "knowledge.forget"].index(last_tool) / 17.0) if last_tool else 0.0)
    vec.append(min(1.0, _num(obs.get("hops_to_target")) / 3.0))
    vec.append(1.0 if capacity and len(inv) >= capacity else 0.0)
    for slot in ("none", "structural", "transport", "action", "other"):
        vec.append(1.0 if cls == slot else 0.0)
    vec.append(1.0 if obs.get("_obs_stale") else 0.0)
    vec.append(1.0 if obs.get("_obs_partial") else 0.0)
    vec.append(1.0 if inv else 0.0)
    vec.append(min(1.0, _num(obs.get("kamas")) / 10000.0))
    vec.append(1.0 if tracker and tracker.get("map_id") == obs.get("map_id") else 0.0)
    return vec, seen


FEATURE_NAMES = [
    "level", "map_known", "inv_stacks", "objectives", "quests", "has_objective",
    "obj_type", "active", "streak", "last_ok", "steps", "last_tool", "hops",
    "inv_full", "err_none", "err_structural", "err_transport", "err_action",
    "err_other", "obs_stale", "obs_partial", "has_items", "kamas", "on_target",
]


def read_log(path: Path) -> List[Dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def build(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Pair each decision with the outcome of the step that followed it."""
    dataset: List[Dict[str, Any]] = []
    seen_keys: Counter = Counter()
    coverage: Dict[Tuple[str, str], List[bool]] = defaultdict(list)
    streak = 0
    last_ok: Optional[bool] = None
    last_tool: Optional[str] = None

    for i, row in enumerate(rows):
        if row.get("kind") != "decision":
            continue
        action = row.get("action")
        if action not in ACTION_INDEX:
            continue
        # Reward: did the next recorded step advance? A decision whose consequence was
        # never observed is not a sample.
        nxt = None
        for later in rows[i + 1:]:
            if later.get("kind") == "decision":
                break
            if "tool" in later and "ok" in later:
                nxt = later
                break
        if nxt is None:
            continue
        reward = 1.0 if nxt.get("ok") else 0.0
        x, seen = features(row.get("obs") or {}, row.get("error"), last_tool)
        x[8] = min(1.0, streak / 5.0)
        x[9] = 1.0 if last_ok else 0.0
        x[10] = min(1.0, (i % 200) / 200.0)
        for k, present in seen.items():
            if present:
                seen_keys[k] += 1
        dataset.append({"x": x, "y": ACTION_INDEX[action], "action": action,
                        "cause": row.get("cause"), "reward": reward,
                        "explored": bool(row.get("explored")),
                        "next_tool": nxt.get("tool"), "next_ok": bool(nxt.get("ok"))})
        coverage[(str(row.get("cause")), action)].append(bool(reward))

        last_ok = bool(nxt.get("ok"))
        last_tool = nxt.get("tool")
        streak = 0 if last_ok else streak + 1

    table = {}
    for (cause, action), results in sorted(coverage.items()):
        table["%s/%s" % (cause, action)] = {
            "n": len(results), "success": round(sum(results) / len(results), 3)}
    return dataset, {"cells": table, "fields_seen": dict(seen_keys),
                     "rows": len(dataset)}


def report(stats: Dict[str, Any]) -> bool:
    """Print the coverage table and say whether a comparison is possible."""
    print("rows: %d" % stats["rows"])
    print("fields found in the live observation:", stats["fields_seen"] or "none")
    cells = stats["cells"]
    if not cells:
        print("\nno decision rows: run the loop with the current code so decisions "
              "are logged, and with --explore so the alternatives are taken")
        return False
    print("\n%-34s %6s %9s" % ("cause/action", "n", "success"))
    causes = defaultdict(dict)
    for key, cell in cells.items():
        cause, action = key.split("/", 1)
        causes[cause][action] = cell
        print("%-34s %6d %9.3f" % (key, cell["n"], cell["success"]))

    comparable = [c for c, acts in causes.items()
                  if sum(1 for a in acts.values() if a["n"] >= MIN_PER_CELL) >= 2]
    print("\ncauses with >=2 actions at n>=%d: %d of %d"
          % (MIN_PER_CELL, len(comparable), len(causes)))
    if not comparable:
        print("-> NOT learnable yet: no cause has two repairs observed often enough.")
        print("   Run again with --explore 0.3; a deterministic rule logs one action "
              "per state and cannot rank anything.")
        return False
    print("-> comparable causes:", ", ".join(sorted(comparable)))
    return True


def selftest() -> int:
    """Build a fake session log and check the exporter's arithmetic end to end."""
    rows = []
    obs = {"level": 60, "map_id": 123, "inventory": {"1": 2}, "quests_active": {"5": 0},
           "current_objective": {"quest_id": 5, "type_id": 1, "map_id": 123},
           "inventory_capacity": 40, "kamas": 5000}
    for i in range(12):
        action = ACTIONS[i % 4]
        rows.append({"kind": "decision", "action": action, "cause": "plan_wrong",
                     "explored": i % 2 == 0, "tool": "farm.start",
                     "error": "timed out", "obs": obs})
        rows.append({"tool": "farm.start", "ok": i % 3 != 0, "error": None})
    dataset, stats = build(rows)
    ok = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and cond
        print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))

    check("every decision became a row", len(dataset) == 12, "%d" % len(dataset))
    check("features are 24 wide", all(len(r["x"]) == 24 for r in dataset))
    check("reward follows the next step", sum(r["reward"] for r in dataset) == 8,
          "%d successes of 12" % sum(r["reward"] for r in dataset))
    check("the observation's fields were detected",
          stats["fields_seen"].get("current_objective", 0) == 12,
          str(stats["fields_seen"]))
    check("coverage is counted per (cause, action)", len(stats["cells"]) == 4,
          "%d cells" % len(stats["cells"]))
    print("\ncoverage report:")
    learnable = report(stats)
    check("the report refuses to call 3 samples per action learnable", not learnable)
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", type=Path, default=Path(
        os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm" / "autopilot.jsonl")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "decision_live")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    rows = read_log(args.log)
    if not rows:
        print("no log at %s" % args.log)
        return 1
    dataset, stats = build(rows)
    learnable = report(stats)

    if dataset:
        args.out.mkdir(parents=True, exist_ok=True)
        split = int(len(dataset) * 0.8)
        for name, part in (("train", dataset[:split]), ("val", dataset[split:])):
            with (args.out / (name + ".jsonl")).open("w", encoding="utf-8") as fh:
                for row in part:
                    fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        (args.out / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
        print("\nwrote %d rows to %s" % (len(dataset), args.out))
    return 0 if learnable else 2


if __name__ == "__main__":
    raise SystemExit(main())
