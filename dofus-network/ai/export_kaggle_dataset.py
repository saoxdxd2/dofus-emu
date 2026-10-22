"""Export the planner corpus for Kaggle, in the shape the TRM trainer expects.

Rows are `(features, label, objective_type, quest_id)` where `features` is exactly
what `ai/train_planner_student.py` computes. Shipping the numbers rather than the
raw quest graph keeps the private dataset small (a few MB) and makes the Kaggle
script independent of the game's KB format.

    python ai/export_kaggle_dataset.py --out kaggle/trm/dataset
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

from ai.train_planner_student import TOOLS, _state_variants, build_dataset, features  # noqa: E402
from agent.features import objective_type  # noqa: E402
from agent.planner import QuestPlanner  # noqa: E402


def rows(max_quests: int = 1200) -> List[Dict[str, Any]]:
    """Rebuild the corpus with the quest id kept, so the split is by quest."""
    planner = QuestPlanner(level_hint=200, max_quest_level=200)
    out: List[Dict[str, Any]] = []
    for qid, quest in planner.quests.items():
        if len(out) >= max_quests * 8:
            break
        level = int(quest.get("level_min") or 1)
        for step_idx, step in enumerate(quest.get("steps") or []):
            for obj in step.get("objectives") or []:
                for state in _state_variants(obj, level):
                    calls = planner.plan_objective(quest, step_idx, obj, state)
                    if not calls or calls[0]["tool"] not in TOOLS:
                        continue
                    out.append({
                        "quest_id": qid,
                        "step": step_idx,
                        "objective_type": objective_type(obj),
                        "x": features(obj, level, step_idx, state),
                        "y": TOOLS.index(calls[0]["tool"]),
                        "tool": calls[0]["tool"],
                        "has_item": 1.0 if state.get("inventory") else 0.0,
                    })
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "kaggle" / "trm" / "dataset")
    ap.add_argument("--max-quests", type=int, default=1200)
    args = ap.parse_args(argv)

    data = rows(args.max_quests)
    if not data:
        print("no rows")
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    # Split by quest: the same quest in train and val would measure memorisation.
    train = [r for r in data if r["quest_id"] % 7 != 0]
    val = [r for r in data if r["quest_id"] % 7 == 0]
    for name, part in (("planner_train", train), ("planner_val", val)):
        with (args.out / (name + ".jsonl")).open("w", encoding="utf-8") as fh:
            for row in part:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")

    meta = {
        "title": "THX-02 planner corpus (private)",
        "id": "tajdinenizar/thx-02-planner-corpus",
        "licenses": [{"name": "other"}],
    }
    (args.out / "dataset-metadata.json").write_text(json.dumps(meta, indent=1),
                                                   encoding="utf-8")
    print(json.dumps({
        "rows": len(data), "train": len(train), "val": len(val),
        "features": len(data[0]["x"]), "tools": TOOLS,
        "bytes": sum((args.out / (n + ".jsonl")).stat().st_size
                     for n in ("planner_train", "planner_val")),
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
