"""Leakage-free splits for the tool-policy SFT corpus.

Why this exists
---------------
The ``core_sft_*`` corpora were expanded from a small set of sentence frames,
and the benchmark generators (``ai/thx_bench_large.py``, the ``--cases`` inputs
fed to ``ai/build_core_sft.py``) reuse the same frames. Measured on this repo:

* 837 / 1279 goals of ``thx_bench_cases_v2.jsonl`` appear **verbatim** in
  ``data/core_sft_v3/train.jsonl``;
* 31 / 68 distinct GOLD goals also appear in that training file.

So a hash-based row split — what ``build_core_sft`` does — leaks. Every
accuracy number computed on it measures memorisation of frames, not the
ability to read a new order.

This module gives the three primitives needed to make the numbers mean
something:

``skeleton(goal, args)``
    The goal with every argument value replaced by a typed placeholder::

        "vends 4 Zorvath Wool"   -> "vends <item>"
        "quêtes de donjon à Incarnam" -> "quetes de donjon a <area>"

    Two goals with the same skeleton share a sentence frame and differ only in
    entities. Splitting on the skeleton is therefore a *frame-level* split.

``split_by_skeleton(rows, val_fraction, seed)``
    Group-wise split: no frame is ever in both train and val. The val split is
    the honest generalisation test ("same skill, unseen wording").

``decontaminate(rows)``
    Drops rows that collide with a GOLD case, by normalised goal or by frame,
    so ``agent/tool_eval.py`` stays a held-out benchmark.

Run ``python -m ai.data_splits --input ...`` for a report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

__all__ = [
    "normalize",
    "skeleton",
    "frame_key",
    "load_jsonl",
    "write_jsonl",
    "split_by_skeleton",
    "gold_rows",
    "gold_goals",
    "decontaminate",
    "describe",
]


# ---------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------


def normalize(text: Any) -> str:
    """Casefold, strip diacritics and collapse whitespace (matches tool_eval)."""
    if not isinstance(text, str):
        text = str(text)
    folded = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(stripped.split()).strip()


def _arg_values(args: Optional[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """(field name, value) pairs worth masking, longest value first."""
    pairs: List[Tuple[str, str]] = []
    for key, value in (args or {}).items():
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, list):
            pairs.extend((key, str(item)) for item in value)
        else:
            pairs.append((key, str(value)))
    pairs.sort(key=lambda pair: -len(pair[1]))
    return pairs


def skeleton(goal: Any, args: Optional[Dict[str, Any]] = None) -> str:
    """The sentence frame of a goal: argument values replaced by ``<field>``.

    Masking is word-bounded and case/diacritic-insensitive, and the longest
    value is replaced first so ``"Gold Ingot"`` is masked before ``"Gold"``.
    """
    text = normalize(goal)
    for key, value in _arg_values(args):
        needle = normalize(value)
        if len(needle) < 2:
            continue
        text = re.sub(
            r"(?<![\w<>])" + re.escape(needle) + r"(?![\w<>])",
            f"<{key}>",
            text,
        )
    return text


def frame_key(goal: Any, args: Optional[Dict[str, Any]] = None) -> str:
    """Stable short hash of a frame, used as the grouping key."""
    return hashlib.sha1(skeleton(goal, args).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# io
# ---------------------------------------------------------------------------


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not Path(path).exists():
        return rows
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


# ---------------------------------------------------------------------------
# GOLD
# ---------------------------------------------------------------------------


def gold_rows() -> List[Dict[str, Any]]:
    from agent.tool_eval import GOLD

    return list(GOLD)


def gold_goals() -> set[str]:
    return {normalize(case["goal"]) for case in gold_rows()}


def gold_frames() -> set[str]:
    return {skeleton(case["goal"], case.get("args")) for case in gold_rows()}


def decontaminate(
    rows: Sequence[Dict[str, Any]],
    drop_same_frame: bool = True,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Remove rows that would leak a GOLD answer into training.

    Two collision kinds are counted separately because they mean different
    things: an identical goal is pure memorisation, a shared frame with the
    same tool is still a leak (the model only has to learn the entity span).
    """
    goals = gold_goals()
    frames = gold_frames() if drop_same_frame else set()
    kept: List[Dict[str, Any]] = []
    stats = Counter()
    for row in rows:
        goal = normalize(row.get("goal"))
        if goal in goals:
            stats["dropped_identical_goal"] += 1
            continue
        if frames and skeleton(row.get("goal"), row.get("args")) in frames:
            stats["dropped_same_frame"] += 1
            continue
        kept.append(row)
        stats["kept"] += 1
    return kept, dict(stats)


# ---------------------------------------------------------------------------
# split
# ---------------------------------------------------------------------------


def split_by_skeleton(
    rows: Sequence[Dict[str, Any]],
    val_fraction: float = 0.12,
    seed: str = "dofus-encoder-v4",
    stratify_by: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Group-wise split on the sentence frame.

    Frames are bucketed by a salted hash, so the split is deterministic and
    independent of file order. Every row of a frame lands in the same side.

    ``stratify_by="lang"`` picks val frames *per language* so each language keeps
    its share of the val set. Without it a hash split can hand 78% of val to one
    language, which makes per-language accuracy meaningless.
    """
    if not 0.0 < val_fraction < 1.0:
        raise ValueError("val_fraction must be in (0, 1)")
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = frame_key(row.get("goal"), row.get("args"))
        buckets.setdefault(key, []).append(row)

    train: List[Dict[str, Any]] = []
    val: List[Dict[str, Any]] = []
    cutoff = int(round(val_fraction * 100))

    if stratify_by:
        groups: Dict[str, List[str]] = {}
        for key, group in buckets.items():
            label = str(group[0].get(stratify_by) or "auto")
            groups.setdefault(label, []).append(key)
        for label, keys in groups.items():
            # Deterministic order, then take the first `val_fraction` by size.
            keys.sort(key=lambda k: hashlib.sha1(f"{k}{seed}".encode("utf-8")).hexdigest())
            target = int(round(val_fraction * sum(len(buckets[k]) for k in keys)))
            taken = 0
            for key in keys:
                if taken >= target:
                    train.extend(buckets[key])
                else:
                    val.extend(buckets[key])
                    taken += len(buckets[key])
    else:
        for key, group in buckets.items():
            bucket = int(hashlib.sha1(f"{key}{seed}".encode("utf-8")).hexdigest(), 16) % 100
            (val if bucket < cutoff else train).extend(group)

    # Guard: a frame must never straddle the split.
    train_frames = {frame_key(r.get("goal"), r.get("args")) for r in train}
    val_frames = {frame_key(r.get("goal"), r.get("args")) for r in val}
    overlap = train_frames & val_frames
    if overlap:
        raise AssertionError(f"{len(overlap)} frames leaked across the split")

    info = {
        "stratified_by": stratify_by,
        "frames_total": len(buckets),
        "frames_train": len(train_frames),
        "frames_val": len(val_frames),
        "val_fraction_requested": val_fraction,
        "val_fraction_actual": round(len(val) / max(len(rows), 1), 4),
        "seed": seed,
    }
    return train, val, info


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def describe(rows: Sequence[Dict[str, Any]], label: str = "rows") -> Dict[str, Any]:
    goals = [normalize(row.get("goal")) for row in rows]
    frames = Counter(skeleton(row.get("goal"), row.get("args")) for row in rows)
    tools = Counter(str(row.get("tool")) for row in rows)
    lengths = sorted(len(goal) for goal in goals) or [0]
    return {
        "label": label,
        "rows": len(rows),
        "unique_goals": len(set(goals)),
        "frames": len(frames),
        "frames_seen_once": sum(1 for count in frames.values() if count == 1),
        "tools": dict(sorted(tools.items())),
        "goal_chars": {
            "min": lengths[0],
            "p50": lengths[len(lengths) // 2],
            "p95": lengths[min(len(lengths) - 1, int(len(lengths) * 0.95))],
            "max": lengths[-1],
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, nargs="+", required=True)
    parser.add_argument("--val-fraction", type=float, default=0.12)
    parser.add_argument("--keep-gold-frames", action="store_true",
                        help="only drop rows identical to a GOLD goal")
    args = parser.parse_args(argv)

    rows: List[Dict[str, Any]] = []
    for path in args.input:
        loaded = load_jsonl(path)
        print(f"{path}: {len(loaded)} rows")
        rows.extend(loaded)

    clean, drop_stats = decontaminate(rows, drop_same_frame=not args.keep_gold_frames)
    train, val, split_info = split_by_skeleton(clean, val_fraction=args.val_fraction)
    report = {
        "input": [str(p) for p in args.input],
        "gold_decontamination": drop_stats,
        "split": split_info,
        "train": describe(train, "train"),
        "val": describe(val, "val"),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
