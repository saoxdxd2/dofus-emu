"""Window the rollouts into plan chunks: "these three calls, in this order".

The one-step corpus teaches "which tool for this state". That is not a plan — a plan
is an *ordered sequence* whose later steps depend on earlier ones (travel, then talk;
gather, then deliver). A model that only sees one state cannot represent that, no
matter how deep it recurses.

So this rebuilds episodes from the rollout rows and emits windows:

    [state(t-2), state(t-1), state(t)]  ->  [tool(t), tool(t+1), tool(t+2)]

with a mask for the tail of an episode, so a short episode does not teach the model
to predict padding.

    python ai/build_sequence_dataset.py --in data/rollout_sft --out data/seq_sft
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

HISTORY = 3        # states fed to the model
HORIZON = 3        # tool ids predicted ahead


def read_rows(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def group_episodes(rows: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Split by the explicit `ep` tag, falling back to a heuristic.

    Older corpora have no `ep`/`t`; there a new episode is where the quest changes or
    the step index goes backwards. Guessing is acceptable for training data as long
    as the guess is written down — silently mixing episodes would teach the model
    transitions that never happened.
    """
    if rows and "ep" in rows[0]:
        episodes: Dict[Any, List[Dict[str, Any]]] = {}
        for row in rows:
            episodes.setdefault(row["ep"], []).append(row)
        return [sorted(v, key=lambda r: r.get("t", 0)) for v in episodes.values()]

    episodes = []
    current: List[Dict[str, Any]] = []
    last_quest = None
    last_step = -1
    for row in rows:
        if current and (row.get("quest_id") != last_quest or row.get("step", 0) < last_step):
            episodes.append(current)
            current = []
        current.append(row)
        last_quest = row.get("quest_id")
        last_step = row.get("step", 0)
    if current:
        episodes.append(current)
    return episodes


def windows(episodes: Sequence[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for episode in episodes:
        if len(episode) < HISTORY + 1:
            continue
        for t in range(HISTORY - 1, len(episode)):
            hist = episode[t - HISTORY + 1: t + 1]
            xs = []
            ok = True
            for row in hist:
                x = row.get("x")
                if not isinstance(x, list):
                    ok = False
                    break
                xs.append(x)
            if not ok:
                continue

            targets, mask = [], []
            for k in range(1, HORIZON + 1):
                idx = t + k
                if idx < len(episode) and isinstance(episode[idx].get("y"), int):
                    targets.append(int(episode[idx]["y"]))
                    mask.append(1)
                else:
                    targets.append(0)
                    mask.append(0)
            if not any(mask):
                continue                      # nothing to learn from this window

            out.append({
                "x": [v for row in xs for v in row],   # flattened history
                "y": targets,
                "mask": mask,
                "advanced": bool(episode[t].get("advanced", True)),
            })
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="src", type=Path,
                    default=REPO_ROOT / "data" / "rollout_sft")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "seq_sft")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    stats: Dict[str, Any] = {"history": HISTORY, "horizon": HORIZON}
    for name in ("train", "val"):
        src = args.src / (name + ".jsonl")
        if not src.exists():
            print("missing %s" % src)
            return 1
        rows = read_rows(src)
        episodes = group_episodes(rows)
        data = windows(episodes)
        dst = args.out / (name + ".jsonl")
        with dst.open("w", encoding="utf-8") as fh:
            for row in data:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        stats[name] = {"episodes": len(episodes), "windows": len(data),
                       "bytes": dst.stat().st_size}

    stats["features"] = HISTORY * 26
    (args.out / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
