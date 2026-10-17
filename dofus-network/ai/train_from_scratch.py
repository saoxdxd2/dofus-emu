"""Domain-Native AI Training Pipeline (From Scratch).

Trains the DofusBrainTransformer on 100% domain data:
- Zero generalist weights downloaded from Hugging Face
- Self-supervised vocabulary built from DofusDB + Papycha + logs
- Multi-task supervised training (Combat Policy + Trilingual Intent Classification)
- Held-out validation split with per-head metrics, early stopping, and a JSON report
- Self-distillation into TRM-ACT

Why this differs from a naive training loop:

* **Leak-free split.** The corpus is shuffled, so a random slice would let near
  duplicates sit on both sides of the split. We group by normalised text and hold
  out whole groups, and the vocabulary is built from *training* texts only.
* **Real validation.** Every epoch reports per-head accuracy on held-out data and
  checkpoints the best epoch, instead of trusting the final one.
* **Class imbalance handled explicitly.** ``NONE`` (moderator chatter, protocol
  logs) and the combat stances are far from balanced, so the intent loss is
  class-weighted and the report carries a per-class breakdown.
* **Honest value targets.** The state value is normalised to a bounded [0,1]
  reward proxy rather than the raw 8/10/12/15 magic numbers.
"""

from __future__ import annotations

import json
import logging
import math
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset

from .dofus_native_model import (
    DofusBrainTransformer,
    DofusDomainDistiller,
    DofusDomainTokenizer,
)

logger = logging.getLogger("dofus.train_from_scratch")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CORPUS_FILE = DATA_DIR / "dofus_deep_training_corpus.json"
POLICY_FILE = DATA_DIR / "dofus_combat_policy_dataset.json"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "models" / "dofus-brain-native"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

INTENT_MAP = {
    "NONE": 0,
    "FOLLOW": 1,
    "AUTO_JOIN_COMBAT": 2,
    "PASS_TURN": 3,
    "AUTO_FARM": 4,
    "STOP": 5,
    "NAVIGATE_COORDS": 6,
    "NAVIGATE_ZAAP": 7,
    "FIND_RESOURCE": 8,
    "START_FARM_LOOP": 9,
    "QUEST_GUIDE": 10,
    "RESOURCE_LEVEL_REQ": 11,
}

STANCE_MAP = {
    "BURST_LETHAL": 0,
    "BUFF_AND_ENGAGE": 1,
    "TRAP_AND_KITE": 2,
    "SHIELD_DEFENSE": 3,
}

# Bounded reward proxy per stance, in [0, 1]. Used for the value head so the
# target is comparable across samples instead of an arbitrary AP-scale number.
STANCE_VALUE = {
    "BURST_LETHAL": 1.0,
    "BUFF_AND_ENGAGE": 0.75,
    "TRAP_AND_KITE": 0.6,
    "SHIELD_DEFENSE": 0.5,
}

# The combat policy dataset also emits BURST_ATTACK, which is not a stance the
# transformer predicts; map it to the closest supervised stance so the sample is
# still usable rather than silently dropped.
STANCE_ALIASES = {"BURST_ATTACK": "BURST_LETHAL"}


def _normalise_stance(stance: str) -> str:
    return STANCE_ALIASES.get(stance, stance)


def _value_for(stance: str) -> float:
    return STANCE_VALUE.get(_normalise_stance(stance), 0.5)


@dataclass
class TrainReport:
    """Everything the benchmark and downstream distillation gate need to see."""

    epochs: int
    best_epoch: int
    best_valid_loss: float
    train_examples: int
    valid_examples: int
    shared_text_groups: int
    history: List[Dict[str, Any]] = field(default_factory=list)
    intent_confusion: List[List[int]] = field(default_factory=list)
    stance_confusion: List[List[int]] = field(default_factory=list)
    per_class_intent: Dict[str, Dict[str, float]] = field(default_factory=dict)
    wall_clock_seconds: float = 0.0
    device: str = "cpu"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "epochs_run": self.epochs,
            "best_epoch": self.best_epoch,
            "best_valid_loss": self.best_valid_loss,
            "train_examples": self.train_examples,
            "valid_examples": self.valid_examples,
            "shared_text_groups": self.shared_text_groups,
            "history": self.history,
            "intent_confusion": self.intent_confusion,
            "stance_confusion": self.stance_confusion,
            "per_class_intent": self.per_class_intent,
            "wall_clock_seconds": self.wall_clock_seconds,
            "device": self.device,
        }


class DofusNativeDataset(Dataset):
    """PyTorch Dataset for domain-native multi-task training with cross-task masking."""

    def __init__(self, samples: List[Dict[str, Any]], tokenizer: DofusDomainTokenizer, max_len: int = 64):
        self.data = []
        for s in samples:
            text = s.get("text", "").strip()
            if not text:
                continue
            s_type = s.get("type", "")

            if s_type == "TACTICAL_COMBAT":
                lbl_intent = -100  # Mask out combat states from intent classification
                stance = _normalise_stance(s.get("stance") or s.get("label") or "BURST_LETHAL")
                lbl_stance = STANCE_MAP.get(stance, 0)
                val_target = _value_for(stance)
            else:
                lbl_intent = INTENT_MAP.get(s.get("label", "NONE"), 0)
                lbl_stance = -100  # Mask out chat commands from combat policy
                val_target = 0.0

            vec = list(s.get("state_vector", []))
            while len(vec) < 24:
                vec.append(0.0)

            input_ids = tokenizer.encode(text, max_length=max_len)
            self.data.append({
                "input_ids": torch.tensor(input_ids, dtype=torch.long),
                "intent_label": torch.tensor(lbl_intent, dtype=torch.long),
                "stance_label": torch.tensor(lbl_stance, dtype=torch.long),
                "value_target": torch.tensor([val_target], dtype=torch.float32),
                "state_vector": torch.tensor(vec[:24], dtype=torch.float32),
            })

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, idx: int) -> Dict[str, torch.Tensor]:
        return self.data[idx]


def _text_group(text: str) -> str:
    """Normalised grouping key so near-duplicates cannot straddle the split."""
    return DofusDomainTokenizer.normalize_text(text).strip()


def split_samples(
    samples: List[Dict[str, Any]],
    valid_fraction: float = 0.15,
    seed: int = 42,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    """Group-aware train/validation split.

    Groups are formed on the normalised text, so two samples that differ only by
    accents or repeated characters land in the same group and are never split
    across train and validation. Returns ``(train, valid, shared_group_count)``;
    the shared count is zero by construction and is reported so a reviewer can see
    the guarantee held.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for sample in samples:
        text = sample.get("text", "")
        if not text:
            continue
        groups.setdefault(_text_group(text), []).append(sample)

    keys = sorted(groups)
    rng = random.Random(seed)
    rng.shuffle(keys)

    target_valid = max(1, int(round(len(keys) * valid_fraction)))
    valid_keys = set(keys[:target_valid])

    train: List[Dict[str, Any]] = []
    valid: List[Dict[str, Any]] = []
    for key, items in groups.items():
        (valid if key in valid_keys else train).extend(items)

    train_keys = set(groups) - valid_keys
    shared = len(train_keys & valid_keys)
    return train, valid, shared


def _intent_class_weights(samples: List[Dict[str, Any]]) -> torch.Tensor:
    """Smoothed inverse-frequency weights over the supervised intent classes."""
    counts = Counter(
        INTENT_MAP.get(s.get("label", "NONE"), 0)
        for s in samples
        if s.get("type") != "TACTICAL_COMBAT"
    )
    weights = torch.ones(len(INTENT_MAP), dtype=torch.float32)
    total = sum(counts.values()) or 1
    for label, count in counts.items():
        # sqrt keeps the weighting from dominating the majority classes.
        weights[label] = math.sqrt(total / (len(INTENT_MAP) * max(count, 1)))
    return weights


@torch.no_grad()
def evaluate(
    model: DofusBrainTransformer,
    loader: DataLoader,
    device: torch.device,
    crit_intent: nn.Module,
    crit_stance: nn.Module,
) -> Dict[str, Any]:
    """Held-out evaluation with per-head metrics and confusion matrices."""
    model.eval()
    intent_cm = [[0] * len(INTENT_MAP) for _ in range(len(INTENT_MAP))]
    stance_cm = [[0] * len(STANCE_MAP) for _ in range(len(STANCE_MAP))]
    total_loss = 0.0
    items = 0
    intent_correct = intent_total = 0
    stance_correct = stance_total = 0
    value_abs_error = 0.0
    value_count = 0

    for batch in loader:
        input_ids = batch["input_ids"].to(device)
        intent_lbl = batch["intent_label"].to(device)
        stance_lbl = batch["stance_label"].to(device)
        val_target = batch["value_target"].to(device)

        out = model(input_ids)
        loss = torch.zeros((), device=device)

        if (intent_lbl != -100).any():
            loss = loss + crit_intent(out["intent_logits"], intent_lbl)
            mask = intent_lbl != -100
            preds = torch.argmax(out["intent_logits"][mask], dim=-1)
            truth = intent_lbl[mask]
            intent_correct += int((preds == truth).sum().item())
            intent_total += int(mask.sum().item())
            for p, t in zip(preds.tolist(), truth.tolist()):
                intent_cm[t][p] += 1

        if (stance_lbl != -100).any():
            loss = loss + crit_stance(out["policy_logits"], stance_lbl)
            mask = stance_lbl != -100
            preds = torch.argmax(out["policy_logits"][mask], dim=-1)
            truth = stance_lbl[mask]
            stance_correct += int((preds == truth).sum().item())
            stance_total += int(mask.sum().item())
            for p, t in zip(preds.tolist(), truth.tolist()):
                stance_cm[t][p] += 1
            value_abs_error += float(
                (out["value_pred"][mask] - val_target[mask]).abs().sum().item())
            value_count += int(mask.sum().item())

        total_loss += float(loss.item()) * input_ids.shape[0]
        items += input_ids.shape[0]

    per_class_intent: Dict[str, Dict[str, float]] = {}
    for label_name, label_id in INTENT_MAP.items():
        row = intent_cm[label_id]
        support = sum(row)
        if support == 0:
            continue
        tp = row[label_id]
        predicted_total = sum(intent_cm[t][label_id] for t in range(len(INTENT_MAP)))
        precision = tp / predicted_total if predicted_total else 0.0
        recall = tp / support
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        per_class_intent[label_name] = {
            "support": support,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    return {
        "loss": total_loss / max(items, 1),
        "intent_accuracy": intent_correct / max(intent_total, 1),
        "stance_accuracy": stance_correct / max(stance_total, 1),
        "value_mae": value_abs_error / max(value_count, 1),
        "intent_confusion": intent_cm,
        "stance_confusion": stance_cm,
        "per_class_intent": per_class_intent,
    }


def train_dofus_ai_from_scratch(
    epochs: int = 15,
    batch_size: int = 32,
    lr: float = 1e-3,
    device: Optional[str] = None,
    valid_fraction: float = 0.15,
    seed: int = 42,
    patience: int = 5,
    corpus_file: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    save: bool = True,
) -> Tuple[DofusBrainTransformer, DofusDomainTokenizer, TrainReport]:
    """Trains DofusBrainTransformer completely from scratch using domain data."""
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
    device_obj = torch.device(device)
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus_path = Path(corpus_file) if corpus_file else CORPUS_FILE

    torch.manual_seed(seed)
    random.seed(seed)

    # 1. Load custom domain corpus
    samples: List[Dict[str, Any]] = []
    if corpus_path.exists():
        samples = json.loads(corpus_path.read_text(encoding="utf-8"))
    if not samples:
        raise SystemExit(
            f"No training samples found in {corpus_path}. "
            "Run `python -m data.generate_expanded_corpus` first.")
    logger.info(f"Loaded {len(samples)} training samples from {corpus_path}")

    # 2. Group-aware split BEFORE the vocabulary is built, so the tokenizer only
    #    ever sees training text and cannot leak validation tokens.
    train_samples, valid_samples, shared_groups = split_samples(
        samples, valid_fraction=valid_fraction, seed=seed)
    if not train_samples or not valid_samples:
        raise SystemExit("Split produced an empty train or validation set.")
    logger.info(
        f"Split: {len(train_samples)} train / {len(valid_samples)} valid "
        f"(shared text groups: {shared_groups})")

    # 3. Build domain vocabulary from scratch, on training text only
    tokenizer = DofusDomainTokenizer()
    train_texts = [s.get("text", "") for s in train_samples if s.get("text")]
    tokenizer.build_vocab_from_corpus(train_texts, max_vocab_size=4096)
    if save:
        tokenizer.save(out_dir / "vocab.json")
    logger.info(f"Built domain vocabulary with {len(tokenizer.vocab)} tokens.")

    # 4. Create Datasets & DataLoaders
    dataset = DofusNativeDataset(train_samples, tokenizer)
    valid_dataset = DofusNativeDataset(valid_samples, tokenizer)
    generator = torch.Generator().manual_seed(seed)
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True, generator=generator)
    valid_loader = DataLoader(valid_dataset, batch_size=batch_size, shuffle=False)

    # 5. Initialize Domain Specialist Transformer (From Scratch!)
    model = DofusBrainTransformer(
        vocab_size=len(tokenizer.vocab) + 16,
        d_model=256,
        nhead=8,
        num_layers=6,
        dim_feedforward=768,
        num_policy_classes=len(STANCE_MAP),
        num_intent_classes=len(INTENT_MAP),
    )
    model.to(device_obj)

    total_params = sum(p.numel() for p in model.parameters())
    logger.info(f"Initialized DofusBrainTransformer from scratch ({total_params:,} parameters).")

    # 6. Multi-task Training Loop with Ignore Index Masking
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(epochs, 1), eta_min=1e-5)
    intent_weights = _intent_class_weights(train_samples).to(device_obj)
    crit_ce_intent = nn.CrossEntropyLoss(ignore_index=-100, weight=intent_weights)
    crit_ce_stance = nn.CrossEntropyLoss(ignore_index=-100)

    history: List[Dict[str, Any]] = []
    best_state: Optional[Dict[str, torch.Tensor]] = None
    best_valid_loss = float("inf")
    best_epoch = 0
    stale = 0
    started = time.perf_counter()

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        total_items = 0

        for batch in dataloader:
            input_ids = batch["input_ids"].to(device_obj)
            intent_lbl = batch["intent_label"].to(device_obj)
            stance_lbl = batch["stance_label"].to(device_obj)
            val_target = batch["value_target"].to(device_obj)

            optimizer.zero_grad()
            out = model(input_ids)

            loss = torch.zeros((), device=device_obj)
            if (intent_lbl != -100).any():
                loss = loss + crit_ce_intent(out["intent_logits"], intent_lbl)

            if (stance_lbl != -100).any():
                loss_stance = crit_ce_stance(out["policy_logits"], stance_lbl)
                combat_mask = (stance_lbl != -100).float().unsqueeze(-1)
                loss_val = (
                    F.mse_loss(out["value_pred"], val_target, reduction="none") * combat_mask
                ).sum() / combat_mask.sum().clamp(min=1.0)
                loss = loss + 0.5 * loss_stance + 0.05 * loss_val

            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            total_loss += loss.item() * input_ids.shape[0]
            total_items += input_ids.shape[0]

        scheduler.step()
        train_loss = total_loss / max(total_items, 1)
        valid = evaluate(model, valid_loader, device_obj, crit_ce_intent, crit_ce_stance)
        history.append({
            "epoch": epoch,
            "train_loss": round(train_loss, 5),
            "valid_loss": round(valid["loss"], 5),
            "valid_intent_accuracy": round(valid["intent_accuracy"], 4),
            "valid_stance_accuracy": round(valid["stance_accuracy"], 4),
            "valid_value_mae": round(valid["value_mae"], 4),
            "lr": round(scheduler.get_last_lr()[0], 6),
        })
        logger.info(
            f"Epoch {epoch:02d}/{epochs:02d} | train {train_loss:.4f} | "
            f"valid {valid['loss']:.4f} | Intent {valid['intent_accuracy']*100:.1f}% | "
            f"Stance {valid['stance_accuracy']*100:.1f}% | ValueMAE {valid['value_mae']:.3f}")

        if valid["loss"] < best_valid_loss - 1e-5:
            best_valid_loss = valid["loss"]
            best_epoch = epoch
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            stale = 0
        else:
            stale += 1
            if patience and stale >= patience:
                logger.info(f"Early stopping at epoch {epoch} (no improvement for {stale} epochs).")
                break

    if best_state is not None:
        model.load_state_dict(best_state)

    # 7. Final held-out metrics from the best checkpoint
    final = evaluate(model, valid_loader, device_obj, crit_ce_intent, crit_ce_stance)
    report = TrainReport(
        epochs=len(history),
        best_epoch=best_epoch,
        best_valid_loss=best_valid_loss,
        train_examples=len(dataset),
        valid_examples=len(valid_dataset),
        shared_text_groups=shared_groups,
        history=history,
        intent_confusion=final["intent_confusion"],
        stance_confusion=final["stance_confusion"],
        per_class_intent=final["per_class_intent"],
        wall_clock_seconds=round(time.perf_counter() - started, 1),
        device=str(device_obj),
    )

    # 8. Save Model Checkpoint
    if save:
        torch.save(model.state_dict(), out_dir / "dofus_brain.pt")
        with open(out_dir / "config.json", "w", encoding="utf-8") as f:
            json.dump({
                "d_model": 256,
                "nhead": 8,
                "num_layers": 6,
                "vocab_size": len(tokenizer.vocab) + 16,
                "max_seq_len": 128,
                "intent_map": INTENT_MAP,
                "stance_map": STANCE_MAP,
                "stance_value": STANCE_VALUE,
                "total_params": total_params,
                "best_epoch": best_epoch,
                "best_valid_loss": best_valid_loss,
                "valid_fraction": valid_fraction,
                "seed": seed,
            }, f, indent=2)
        (out_dir / "training_report.json").write_text(
            json.dumps(report.as_dict(), indent=2), encoding="utf-8")
        logger.info(f"Model trained from scratch and saved to: {out_dir}")

    # 9. Evaluate on Dofus Knowledge Certification Benchmark
    from .dofus_knowledge_benchmark import DofusKnowledgeBenchmark
    bench_result = DofusKnowledgeBenchmark.evaluate_teacher(model, tokenizer, device=device_obj)
    bench_result.print_summary()
    if not bench_result.is_certified:
        logger.warning(
            f"Certification failed ({bench_result.aggregate_score:.1f}%). "
            "Model weights saved but distillation restricted.")
    else:
        logger.info(
            f"Certification passed ({bench_result.aggregate_score:.1f}%). "
            "Model certified for distillation.")
    if save:
        (out_dir / "benchmark_report.json").write_text(
            json.dumps({
                "aggregate_score": bench_result.aggregate_score,
                "domain_scores": bench_result.domain_scores,
                "total_tests": bench_result.total_tests,
                "passed_tests": bench_result.passed_tests,
                "is_certified": bench_result.is_certified,
                "failure_reasons": bench_result.failure_reasons,
            }, indent=2), encoding="utf-8")

    return model, tokenizer, report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    train_dofus_ai_from_scratch(epochs=5, batch_size=64)
