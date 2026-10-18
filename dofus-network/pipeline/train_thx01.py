"""Production-grade RLCD fine-tuning of doofz/THX-01 for Dofus decision tasks.

THX-01 is an encoder-only decision model (mmBERT-base + decision head, 322M).
Fine-tuning uses the thx01 library's RLCD objective:
  - state + typed questions -> calibrated answers
  - strictly proper scoring rule reward (honest probabilities maximise score)
  - 8-bit AdamW, bf16, length-bucketed batches, frozen embeddings

Usage:
    python pipeline/train_thx01.py \
        --data data/processed/*.jsonl \
        --base-model doofz/THX-01 \
        --output models/finetuned/thx01-dofus \
        --epochs 3 \
        --push-to-hub your-username/thx01-dofus
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import thx01
    from thx01.training import RLCdTrainer, RLCdConfig
except ImportError:
    thx01 = None
    RLCdTrainer = None
    RLCdConfig = None


DOFUS_QUESTION_TYPES: dict[str, dict[str, Any]] = {
    "combat_action": {
        "type": "choice",
        "question": "What combat action should the character take next?",
        "criteria": {
            "attack": "Use an offensive spell or weapon",
            "defend": "Use a defensive or healing action",
            "move": "Change position on the map",
            "item": "Use an item from inventory",
            "pass": "Skip turn / wait",
        },
    },
    "item_lookup": {
        "type": "choice",
        "question": "Which item ID matches the description?",
        "criteria": {},
    },
    "map_coordinate": {
        "type": "number",
        "question": "What is the map coordinate (x, y) for this location?",
        "criteria": {"min": -100, "max": 100, "precision": 0},
    },
    "npc_dialogue": {
        "type": "choice",
        "question": "Which dialogue option should the player choose?",
        "criteria": {},
    },
    "item_id_extraction": {
        "type": "number",
        "question": "Extract the numeric item ID from the state.",
        "criteria": {"min": 0, "max": 999999, "precision": 0},
    },
    "spell_name_excerpt": {
        "type": "excerpt",
        "question": "What is the exact spell name used in this combat turn?",
        "criteria": {},
    },
}


def load_jsonl_records(path: str | Path) -> list[dict[str, Any]]:
    """Load THX-01 compatible JSONL records."""
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
def convert_to_thx01_format(
    records: list[dict[str, Any]],
    question_type: str = "combat_action",
) -> list[dict[str, Any]]:
    """Normalise raw filtered records into thx01 training format."""
    converted: list[dict[str, Any]] = []
    qt = DOFUS_QUESTION_TYPES.get(question_type, DOFUS_QUESTION_TYPES["combat_action"])

    for r in records:
        state = r.get("state", "")
        if not state:
            continue
        answer = r.get("answers", {}).get(question_type)
        if answer is None:
            continue
        converted.append({
            "id": r.get("id", ""),
            "state": state,
            "questions": {question_type: qt},
            "answers": {question_type: answer},
            "metadata": r.get("metadata", {}),
        })
    return converted


def train(
    data_files: list[str],
    base_model: str = "doofz/THX-01",
    output_dir: str = "models/finetuned/thx01-dofus",
    epochs: int = 3,
    batch_size: int = 16,
    learning_rate: float = 1e-5,
    push_to_hub: str | None = None,
) -> None:
    """Run RLCD fine-tuning on Dofus decision data."""
    if RLCdTrainer is None:
        raise RuntimeError(
            "thx01 library not installed. Install with: pip install thx01[train]"
        )

    all_records: list[dict[str, Any]] = []
    for f in data_files:
        raw = load_jsonl_records(f)
        all_records.extend(convert_to_thx01_format(raw))

    print(f"Loaded {len(all_records)} Dofus training records")

    config = RLCdConfig(
        base_model=base_model,
        output_dir=output_dir,
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        learning_rate=learning_rate,
        bf16=True,
        length_bucketing=True,
        freeze_embeddings=True,
        push_to_hub=push_to_hub,
    )

    trainer = RLCdTrainer(config=config, train_dataset=all_records)
    trainer.train()
    trainer.save_model(output_dir)

    if push_to_hub:
        trainer.push_to_hub(push_to_hub)

    print(f"Fine-tuned model saved to {output_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune doofz/THX-01 on Dofus data")
    parser.add_argument("--data", nargs="+", required=True, help="JSONL data files (glob supported)")
    parser.add_argument("--base-model", default="doofz/THX-01")
    parser.add_argument("--output-dir", default="models/finetuned/thx01-dofus")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=1e-5)
    parser.add_argument("--push-to-hub", default=None)
    args = parser.parse_args()

    expanded: list[str] = []
    for pat in args.data:
        expanded.extend(glob.glob(pat))

    train(
        data_files=expanded,
        base_model=args.base_model,
        output_dir=args.output_dir,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        push_to_hub=args.push_to_hub,
    )


if __name__ == "__main__":
    main()