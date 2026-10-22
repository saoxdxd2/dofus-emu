"""Build the planner SFT corpus from the knowledge base.

The teacher is deterministic: for a quest and a state, the correct plan is derived
from the objective types (see `agent/planner.py`). That makes unlimited, verifiable
training data without a single live session — which is the point of having a
simulator at all.

    python ai/build_planner_sft.py --out data/planner_sft --levels 20 40 60 80

Rows are `(goal, state, plan, source)`. `source="sim"` because nothing here was
executed in the game; the trainer weights it 0.4 against live 0.7 and human 1.0.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.planner import QuestPlanner  # noqa: E402


def state_for(level: int, quest_id: int, quests_done: Sequence[int]) -> Dict[str, Any]:
    return {
        "level": level,
        "map_id": None,
        "kamas": 5000,
        "inventory": {},
        "quests_done": list(quests_done),
        "quests_active": {str(quest_id): 0},
    }


def build(levels: Sequence[int], max_quests: int = 400) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for level in levels:
        planner = QuestPlanner(level_hint=level)
        curriculum = planner._curriculum(level)[:max_quests]
        for qid in curriculum:
            quest = planner.quests.get(qid)
            if not quest:
                continue
            state = state_for(level, qid, [])
            plan = planner.plan_quest(qid, state)
            if not plan:
                continue                      # unsupported objective type: no teacher
            rows.append({
                "id": "q%d:L%d" % (qid, level),
                "goal": "do all the quests",
                "quest_id": qid,
                "state": state,
                "plan": plan,
                "outcome": {"completed": True, "steps": len(plan)},
                "source": "sim",
                "label_weight": 0.4,
            })
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "planner_sft")
    ap.add_argument("--levels", type=int, nargs="+", default=[20, 40, 60, 80])
    ap.add_argument("--max-quests", type=int, default=400)
    args = ap.parse_args(argv)

    rows = build(args.levels, args.max_quests)
    if not rows:
        print("no rows built")
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    # Split by quest, never by row: the same quest at two levels would otherwise
    # appear on both sides and the score would measure memorisation.
    seen = set()
    train, val = [], []
    for row in rows:
        key = row["quest_id"]
        (val if key % 7 == 0 else train).append(row)
        seen.add(key)

    for name, part in (("train", train), ("val", val)):
        with (args.out / (name + ".jsonl")).open("w", encoding="utf-8") as fh:
            for row in part:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    steps = [len(r["plan"]) for r in rows]
    stats = {
        "rows": len(rows),
        "train": len(train),
        "val": len(val),
        "quests": len(seen),
        "levels": args.levels,
        "plan_len_mean": round(sum(steps) / len(steps), 2),
        "plan_len_max": max(steps),
        "tools": sorted({c["tool"] for r in rows for c in r["plan"]}),
    }
    (args.out / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
