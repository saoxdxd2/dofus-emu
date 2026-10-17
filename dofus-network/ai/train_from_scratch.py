"""Domain-Native AI Training Pipeline (From Scratch).

Trains the DofusBrainTransformer on 100% domain data:
- Zero generalist weights downloaded from Hugging Face
- Self-supervised vocabulary built from DofusDB + Papycha + logs
- Multi-task supervised training (Combat Policy + Trilingual Intent Classification)
- Self-distillation into TRM-ACT
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

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


class DofusNativeDataset(Dataset):
    """PyTorch Dataset for domain-native multi-task training with cross-task masking."""

    def __init__(self, samples: List[Dict[str, Any]], tokenizer: DofusDomainTokenizer, max_len: int = 64):
        self.data = []
        for s in samples:
            text = s.get("text", "").strip()
            s_type = s.get("type", "")

            if s_type == "TACTICAL_COMBAT":
                lbl_intent = -100  # Mask out combat states from intent classification
                lbl_stance = STANCE_MAP.get(s.get("stance", "BURST_LETHAL"), 0)
                val_target = 15.0 if s.get("stance") == "BURST_LETHAL" else 10.0
            else:
                lbl_intent = INTENT_MAP.get(s.get("label", "NONE"), 0)
                lbl_stance = -100  # Mask out chat commands from combat policy
                val_target = 0.0

            vec = s.get("state_vector", [])
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


def train_dofus_ai_from_scratch(
    epochs: int = 15,
    batch_size: int = 32,
    lr: float = 1e-3,
    device: Optional[str] = None,
) -> Tuple[DofusBrainTransformer, DofusDomainTokenizer]:
    """Trains DofusBrainTransformer completely from scratch using domain data."""
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
    device_obj = torch.device(device)

    # 1. Load custom domain corpus
    samples: List[Dict[str, Any]] = []
    if CORPUS_FILE.exists():
        samples = json.loads(CORPUS_FILE.read_text(encoding="utf-8"))
    logger.info(f"Loaded {len(samples)} training samples from {CORPUS_FILE}")

    # 2. Build domain vocabulary from scratch
    tokenizer = DofusDomainTokenizer()
    all_texts = [s.get("text", "") for s in samples if s.get("text")]
    tokenizer.build_vocab_from_corpus(all_texts, max_vocab_size=4096)
    tokenizer.save(OUTPUT_DIR / "vocab.json")
    logger.info(f"Built domain vocabulary with {len(tokenizer.vocab)} tokens.")

    # 3. Create Dataset & DataLoader
    dataset = DofusNativeDataset(samples, tokenizer)
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    # 4. Initialize Domain Specialist Transformer (From Scratch!)
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

    # 5. Multi-task Training Loop with Ignore Index Masking
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-5)
    crit_ce_intent = nn.CrossEntropyLoss(ignore_index=-100)
    crit_ce_stance = nn.CrossEntropyLoss(ignore_index=-100)

    model.train()
    for epoch in range(1, epochs + 1):
        total_loss = 0.0
        correct_intents, total_intents = 0, 0
        correct_stances, total_stances = 0, 0
        total_items = 0

        for batch in dataloader:
            input_ids = batch["input_ids"].to(device_obj)
            intent_lbl = batch["intent_label"].to(device_obj)
            stance_lbl = batch["stance_label"].to(device_obj)
            val_target = batch["value_target"].to(device_obj)

            optimizer.zero_grad()
            out = model(input_ids)

            loss = torch.tensor(0.0, device=device_obj)
            if (intent_lbl != -100).sum() > 0:
                loss_intent = crit_ce_intent(out["intent_logits"], intent_lbl)
                loss = loss + loss_intent
                intent_mask = (intent_lbl != -100)
                preds = torch.argmax(out["intent_logits"][intent_mask], dim=-1)
                correct_intents += (preds == intent_lbl[intent_mask]).sum().item()
                total_intents += intent_mask.sum().item()

            if (stance_lbl != -100).sum() > 0:
                loss_stance = crit_ce_stance(out["policy_logits"], stance_lbl)
                combat_mask = (stance_lbl != -100).float().unsqueeze(-1)
                loss_val = (F.mse_loss(out["value_pred"], val_target, reduction="none") * combat_mask).sum() / combat_mask.sum()
                loss = loss + 0.5 * loss_stance + 0.05 * loss_val

                stance_mask = (stance_lbl != -100)
                st_preds = torch.argmax(out["policy_logits"][stance_mask], dim=-1)
                correct_stances += (st_preds == stance_lbl[stance_mask]).sum().item()
                total_stances += stance_mask.sum().item()

            loss.backward()
            optimizer.step()

            total_loss += loss.item() * len(input_ids)
            total_items += len(input_ids)

        scheduler.step()
        avg_loss = total_loss / max(total_items, 1)
        acc_int = (correct_intents / max(total_intents, 1)) * 100
        acc_st = (correct_stances / max(total_stances, 1)) * 100
        logger.info(f"Epoch {epoch:02d}/{epochs:02d} | Loss: {avg_loss:.4f} | Intent Acc: {acc_int:.1f}% | Stance Acc: {acc_st:.1f}%")

    # 6. Save Model Checkpoint
    torch.save(model.state_dict(), OUTPUT_DIR / "dofus_brain.pt")
    with open(OUTPUT_DIR / "config.json", "w", encoding="utf-8") as f:
        json.dump({
            "d_model": 256,
            "nhead": 8,
            "num_layers": 6,
            "vocab_size": len(tokenizer.vocab) + 10,
            "intent_map": INTENT_MAP,
            "stance_map": STANCE_MAP,
            "total_params": total_params,
        }, f, indent=2)

    logger.info(f"Model trained from scratch and saved to: {OUTPUT_DIR}")

    # 7. Evaluate on Dofus Knowledge Certification Benchmark
    from .dofus_knowledge_benchmark import DofusKnowledgeBenchmark
    bench_result = DofusKnowledgeBenchmark.evaluate_teacher(model, tokenizer, device=device_obj)
    bench_result.print_summary()
    if not bench_result.is_certified:
        logger.warning(f"Certification failed ({bench_result.aggregate_score:.1f}%). Model weights saved but distillation restricted.")
    else:
        logger.info(f"Certification passed ({bench_result.aggregate_score:.1f}%). Model certified for distillation.")

    return model, tokenizer


if __name__ == "__main__":
    train_dofus_ai_from_scratch(epochs=5, batch_size=64)
