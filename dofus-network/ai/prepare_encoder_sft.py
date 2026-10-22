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
from typing import Any, Dict, List, Optional, Sequence, Tuple

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


REFERENCE_FIELDS = frozenset({"quest_id", "map_id"})

_KB: List[Any] = []


def _knowledge_base() -> Any:
    """The KB is game data (``data/cache``), not corpus-derived, so consulting it
    is not leakage -- it is the same deterministic resolver the serving path
    uses. Loaded lazily and cached; a missing KB simply disables the check."""
    if not _KB:
        try:
            from planning.kb import KnowledgeBase
            _KB.append(KnowledgeBase())
        except Exception:
            _KB.append(None)
    return _KB[0]


def _reference_resolves(field: str, value: int, goal: str) -> bool:
    """True when the goal's entity reference resolves to exactly ``value``.

    Only *unique* resolutions count. A goal that names a quest the KB matches to
    several quests (or to a different one) is a label the model cannot derive,
    and must still cost the row.

    The resolver is ``ai.postprocess.resolve_quest_id`` -- the same function the
    serving path calls -- so a label the corpus builder accepts is exactly a
    label the agent can reproduce at run time. An earlier version stripped lead
    verbs and the quest noun here instead, and recovered 263 of 695 name-bearing
    rows against 622 for the resolver, because the quest noun sits mid-sentence
    in half the templates.
    """
    if field not in REFERENCE_FIELDS:
        return False
    kb = _knowledge_base()
    if kb is None or field != "quest_id":
        return False
    try:
        from ai.lang_detect import detect
        from ai.postprocess import resolve_quest_id
        try:
            lang = detect(goal).lang
        except Exception:
            lang = "fr"
        return resolve_quest_id(goal, kb, lang) == int(value)
    except Exception:
        return False


def sanitize_labels(
    tool: str, goal: str, args: Dict[str, Any]
) -> Tuple[Dict[str, Any], bool]:
    """Make a row's label learnable, or drop the row.

    Four defects, and the fourth is the one that hid for longest:

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
    4. **An entity reference is solvable when a resolver can map it.** Rule 2 as
       written dropped every row whose goal *names* a quest instead of numbering
       it -- 1,986 of them in the v3 parent, which is the realistic half of
       ``quest.advance``. Those labels are not invented; they are recoverable
       from the goal through the knowledge base, which is game data and not part
       of the corpus. The row is kept only when the KB resolves the goal's
       reference to *exactly this id*, so a name that maps to several quests (or
       to a different one) still costs the row. That check also disposes of the
       noisy rows for free: the distill generator paired names with random ids,
       and a random id is not what the KB resolves the name to.

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
            if _reference_resolves(key, value, goal):
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


def _write_tokenizer(rows: Sequence[Dict[str, Any]], out: Path,
                     vocab_size: int = 4096) -> Optional[int]:
    """Train the byte-level BPE the trainer expects and save it beside the rows.

    The builder's own docstring told the reader to pass
    ``--tokenizer data/core_sft_v4/tokenizer.json``, but nothing ever wrote that
    file -- it was copied in by hand from v3, so a corpus that added vocabulary
    silently trained against a tokenizer that could not see it. Every quest name
    the supplement introduces is a new word, and an unseen word is UNK.

    Same BPE configuration as ``ai.tool_policy.train_tokenizer`` so a checkpoint
    stays loadable by either entry point. Returns the vocab size, or ``None``
    when the ``tokenizers`` package is unavailable -- the corpus itself is still
    written, and only the tokenizer is missing.
    """
    try:
        from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
    except Exception:  # noqa: BLE001 - tokenizers is an optional dependency
        return None

    texts: List[str] = []
    # The system prompt is part of every training sequence, so it has to be in
    # the tokenizer's training text. Left out, it tokenises as unseen byte
    # fragments -- 270 tokens for a 585-character prompt against a 256-token
    # limit -- and every row is then dropped as "too long" before training
    # starts. ``ai.tool_policy.train_tokenizer`` takes the prompt for the same
    # reason.
    #
    # It is emitted once per row, not once overall: the v3 corpus was in
    # messages format, so the old tokenizer saw the prompt thousands of times
    # and merged it down to 218 tokens. Three copies merge it to 252, which
    # still overflows 256 once BOS/EOS and the goal are added.
    try:
        from ai.encoder_policy import SYSTEM_PROMPTS
        prompts = list(SYSTEM_PROMPTS.values())
    except Exception:  # noqa: BLE001 - the prompts are a nicety, not a requirement
        prompts = []
    texts.extend(prompts)
    for row in rows:
        goal = str(row.get("goal") or "")
        texts.extend(f"{prompt}\nGoal: {goal}" for prompt in prompts[:1])
        # The label side matters too: the decoder has to emit tool names and
        # argument values, so both go into the vocabulary.
        texts.append(json.dumps({"tool": row.get("tool"),
                                 "args": row.get("args") or {}},
                                ensure_ascii=False, sort_keys=True))
    try:
        from agent.tool_eval import GOLD
        for case in GOLD:
            texts.append(f"Goal: {case['goal']}")
    except Exception:  # noqa: BLE001 - GOLD is a nicety, not a requirement
        pass
    texts = [text for text in texts if text]
    if not texts:
        return None

    tokenizer = Tokenizer(models.BPE(unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(
        vocab_size=int(vocab_size),
        special_tokens=["[PAD]", "[BOS]", "[EOS]", "[UNK]", "[ASSISTANT]"],
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
        show_progress=False,
    )
    tokenizer.train_from_iterator(texts, trainer=trainer)
    tokenizer.save(str(out / "tokenizer.json"))
    return tokenizer.get_vocab_size()


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
    vocab_size = _write_tokenizer(train + val, out)
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
        "vocab_size": vocab_size,
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
        "vocab_size": report["vocab_size"],
        "written": report["written"],
        "split": report["split"],
        "gold_decontamination": report["gold_decontamination"],
        "train_tools": report["train"]["tools"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
