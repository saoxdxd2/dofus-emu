"""Build a leakage-free SFT corpus for the 30M encoder policy.

The existing corpora (``core_sft`` / ``core_sft_v2`` / ``core_sft_v3``) were
split by hashing the row id, which puts the *same sentence frame* on both sides
of the split — and several benchmark cases verbatim. Training on them and
reporting the val number is therefore not a measurement. See
``ai/data_splits.py`` for the evidence.

This script produces ``data/core_sft_v4``:

    train.jsonl      frame-disjoint training rows
    val.jsonl        frame-held-out rows (the honest generalisation test)
    val_indist.jsonl small random sample of train frames, for the memorisation
                     ceiling only — never use it for early stopping
    gold.jsonl       the GOLD cases that survive decontamination, in the flat
                     row format the encoder trainer consumes
    stats.json       counts, dropped-row accounting, per-tool distribution

Usage (from ``dofus-network``)::

    python -m ai.prepare_encoder_sft \
        --input data/core_sft_v3/train.jsonl data/core_sft_v3/val.jsonl \
        --out data/core_sft_v4

Then::

    python ai/encoder_policy.py train --sft-dir data/core_sft_v4 \
        --tokenizer data/core_sft_v4/tokenizer.json --out models/encoder30
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.data_splits import (  # noqa: E402
    bounded_match,
    decontaminate,
    describe,
    gold_rows,
    load_jsonl,
    normalize,
    split_by_skeleton,
    write_jsonl,
)
from ai.lang_detect import detect  # noqa: E402


def sanitize_labels(
    tool: str, goal: str, args: Dict[str, Any]
) -> Tuple[Dict[str, Any], bool]:
    """Make a row's label learnable, or drop the row.

    Three defects, and the third is the one that hid for longest:

    1. **Canonicalise.** ``validate_call`` materialises every optional field's
       default, so ``vends Cadeau`` carried ``all=False, min_stock=1,
       price_rule=median_minus_1`` -- three constants the player never said. The
       model was scored wrong for correctly omitting them.
    2. **Never label a number the goal does not contain.** The generators drew
       ``quantity`` from ``(1,5,10,20,50)`` and ``quest_id`` from
       ``randint(50,9999)`` even when the sentence frame had no placeholder to
       render them. An optional field with no value in the text is dropped; a
       *required* field with no value in the text makes the whole row
       unlearnable, so the row is dropped.
    3. **Never label a string the goal does not contain.** The teacher-distilled
       corpus and ``synthetic_tools`` are two independent generators for the same
       capability, and both invented free text: ``safe.stop`` emitted
       ``{"reason": "entité hostile en approche"}`` for the goal ``"Arrête tout,
       un danger approche rapidement."``. Numbers were guarded; strings were not,
       so this family survived every earlier audit.

    Returns ``(args, drop_row)``. Dropping a row is a last resort -- it costs a
    training example -- but keeping it teaches the model to invent values.
    """
    from agent.schema import TOOLS_BY_NAME, DERIVED_FIELDS, canonicalize_args

    args = canonicalize_args(tool, args)
    spec = TOOLS_BY_NAME.get(tool)
    if spec is None:
        return args, False
    numbers = {int(n) for n in re.findall(r"-?\d+", goal)}
    folded = normalize(goal)
    for key, param in spec.params.items():
        value = args.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int):
            if value in numbers:
                continue
            if param.get("required"):
                return args, True
            args = {k: v for k, v in args.items() if k != key}
            continue
        if isinstance(value, str):
            if not value or key in DERIVED_FIELDS:
                continue
            if bounded_match(folded, normalize(value)):
                continue
            if param.get("required"):
                return args, True
            args = {k: v for k, v in args.items() if k != key}
            continue
        if isinstance(value, (list, tuple)):
            keep = [item for item in value
                    if isinstance(item, str) and (not item or key in DERIVED_FIELDS
                                                  or bounded_match(folded, normalize(item)))]
            if len(keep) == len(value):
                continue
            if param.get("required"):
                return args, True
            if keep:
                args = {**args, key: keep}
            else:
                args = {k: v for k, v in args.items() if k != key}
    return args, False


def _flat(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise a corpus row to the flat ``goal/tool/args/lang`` shape."""
    goal = str(row.get("goal") or "").strip()
    tool = row.get("tool")
    if not goal or not isinstance(tool, str) or not tool:
        return None
    args = row.get("args")
    if not isinstance(args, dict):
        args = {}
    args, drop = sanitize_labels(tool, goal, args)
    if drop:
        return None
    row_id = str(row.get("id") or hashlib.sha1(
        f"{goal}\x1f{tool}\x1f{json.dumps(args, sort_keys=True)}".encode("utf-8")
    ).hexdigest()[:16])
    # The source corpora all say "auto". Label deterministically so per-language
    # accuracy is measurable; the detector abstains rather than guessing.
    lang = row.get("lang")
    if not lang or lang == "auto":
        lang = detect(goal).lang
    return {
        "id": row_id,
        "goal": goal,
        "tool": tool,
        "args": args,
        "lang": lang,
    }


def build(
    inputs: Sequence[Path],
    out: Path,
    val_fraction: float,
    seed: str,
    drop_same_frame: bool,
    indist_rows: int,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    dropped_invalid = 0
    for path in inputs:
        for row in load_jsonl(path):
            flat = _flat(row)
            if flat is None:
                dropped_invalid += 1
                continue
            rows.append(flat)

    # De-duplicate on (goal, tool, args) before splitting so a duplicate cannot
    # sit on both sides.
    seen: set[tuple[str, str, str]] = set()
    unique: List[Dict[str, Any]] = []
    duplicates = 0
    for row in rows:
        key = (row["goal"].casefold().strip(), row["tool"],
               json.dumps(row["args"], sort_keys=True))
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique.append(row)

    clean, gold_stats = decontaminate(unique, drop_same_frame=drop_same_frame)
    train, val, split_info = split_by_skeleton(
        clean, val_fraction=val_fraction, seed=seed, stratify_by="lang")

    rng = random.Random(seed)
    indist = list(train)
    rng.shuffle(indist)
    indist = indist[:max(0, indist_rows)]

    gold: List[Dict[str, Any]] = []
    for case in gold_rows():
        gold.append({
            "id": case["id"],
            "goal": case["goal"],
            "tool": case["tool"],
            "args": case.get("args") or {},
            "lang": case.get("lang") or "auto",
        })

    out.mkdir(parents=True, exist_ok=True)
    counts = {
        "train.jsonl": write_jsonl(out / "train.jsonl", train),
        "val.jsonl": write_jsonl(out / "val.jsonl", val),
        "val_indist.jsonl": write_jsonl(out / "val_indist.jsonl", indist),
        "gold.jsonl": write_jsonl(out / "gold.jsonl", gold),
    }

    from collections import Counter

    def lang_mix(rows):
        return dict(sorted(Counter(str(r.get("lang") or "auto") for r in rows).items()))

    report = {
        "inputs": [str(path) for path in inputs],
        "out": str(out),
        "dropped_invalid_rows": dropped_invalid,
        "dropped_duplicates": duplicates,
        "gold_decontamination": gold_stats,
        "split": split_info,
        "written": counts,
        "train": describe(train, "train"),
        "val": describe(val, "val"),
        "gold": describe(gold, "gold"),
        "languages": {"train": lang_mix(train), "val": lang_mix(val)},
    }
    (out / "stats.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, nargs="+", required=True,
                        help="corpus jsonl files (flat or messages format)")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "core_sft_v4")
    parser.add_argument("--val-fraction", type=float, default=0.12)
    parser.add_argument("--seed", default="dofus-encoder-v4")
    parser.add_argument("--keep-gold-frames", action="store_true",
                        help="only drop rows identical to a GOLD goal, not whole frames")
    parser.add_argument("--indist-rows", type=int, default=800)
    args = parser.parse_args(argv)

    report = build(
        args.input, args.out, args.val_fraction, args.seed,
        drop_same_frame=not args.keep_gold_frames, indist_rows=args.indist_rows,
    )
    print(json.dumps({
        "written": report["written"],
        "split": report["split"],
        "gold_decontamination": report["gold_decontamination"],
        "train_tools": report["train"]["tools"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
