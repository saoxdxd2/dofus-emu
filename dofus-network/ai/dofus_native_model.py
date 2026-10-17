"""Domain-Native Dofus Transformer & Self-Distillation Architecture.

Built 100% from scratch on our custom Dofus datasets (DofusDB, Papycha.fr, bot logs).
Zero reliance on external generalist 7B foundation models.

Features:
1. Domain-Specific Tokenizer (`DofusDomainTokenizer`) with specialized vocabulary
   covering spells, monsters, coordinates, quest entities, and trilingual game chat.
2. Lightweight Domain-Native Specialist Transformer (`DofusBrainTransformer` ~12M params)
   with Multi-Head Attention, Pre-LN Transformer blocks, and Multi-Task heads:
   - MLM Pre-training head (unsupervised learning on game encyclopedia)
   - Policy head (combat stance & spell rotation)
   - Intent head (NLP navigation, resource farming, quest advice)
   - Value head (continuous state evaluation)
3. Domain Self-Distillation Engine (`DofusDomainDistiller`)
   Transferring representations directly from the domain specialist into the 150k-weight TRM-ACT.
"""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
VOCAB_FILE = DATA_DIR / "cache" / "dofus_domain_vocab.json"


class DofusDomainTokenizer:
    """Domain-native subword/wordpiece tokenizer trained on Dofus encyclopedia and logs."""

    SPECIAL_TOKENS = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "[COORD]", "[SPELL]", "[MOB]", "[QUEST]"]

    def __init__(self, vocab: Optional[Dict[str, int]] = None) -> None:
        self.vocab = vocab or {}
        self.inv_vocab = {v: k for k, v in self.vocab.items()}
        if not self.vocab:
            self._init_default_vocab()

    @staticmethod
    def normalize_text(text: str) -> str:
        """Strips accents and diacritics for uniform multilingual token matching."""
        nfkd = unicodedata.normalize("NFKD", text.lower())
        return "".join([c for c in nfkd if not unicodedata.combining(c)])

    def _init_default_vocab(self) -> None:
        for idx, tok in enumerate(self.SPECIAL_TOKENS):
            self.vocab[tok] = idx

        # Seed high-frequency keywords
        seed_words = [
            # Combat & Stats
            "pa", "pm", "hp", "pv", "degats", "damage", "feu", "terre", "eau", "air", "neutre",
            "bouftou", "iop", "cra", "feca", "sram", "eniripsa", "sacrieur", "osamodas", "sadida",
            "enutrof", "ecaflip", "pandawa", "xelor", "puissance", "colere", "epee", "fleche",
            "rempart", "invisibilite", "piege", "ronce", "mot", "pression", "tempete",
            # Spatial & Navigation
            "zaap", "astrub", "amakna", "bonta", "brakmar", "otomai", "moon", "frigost", "wabbit",
            "madrestam", "pandala", "cania", "marche", "teleporte", "carte", "cellule", "nord", "sud",
            # Resources & Professions
            "ble", "orge", "fer", "cuivre", "frene", "chene", "ortie", "trefle", "goujon", "paysan",
            "mineur", "bucheron", "alchimiste", "pecheur", "farm", "recolte", "boucle", "spot",
            # Quests & Papycha
            "quete", "succes", "dofus", "cawotte", "ocre", "argente", "koulosse", "skeunk", "panoplie",
            "capitaine", "kradoc", "lily", "wanald", "terrier", "donjon",
            # Commands & Control
            "suis", "passe", "tour", "arrete", "stop", "rejoins", "combat", "oui", "salut", "merci",
        ]
        for w in seed_words:
            norm_w = self.normalize_text(w)
            if norm_w not in self.vocab:
                self.vocab[norm_w] = len(self.vocab)
        self.inv_vocab = {v: k for k, v in self.vocab.items()}

    def build_vocab_from_corpus(self, texts: List[str], max_vocab_size: int = 4000) -> None:
        """Learns domain vocabulary directly from raw game texts."""
        word_freq: Dict[str, int] = {}
        for t in texts:
            clean = self.normalize_text(t)
            tokens = re.findall(r"\[-?\d+,\s*-?\d+\]|[a-zA-Z0-9_]+", clean)
            for tok in tokens:
                word_freq[tok] = word_freq.get(tok, 0) + 1

        sorted_words = sorted(word_freq.items(), key=lambda x: -x[1])
        for w, _ in sorted_words:
            if len(self.vocab) >= max_vocab_size:
                break
            if w not in self.vocab:
                self.vocab[w] = len(self.vocab)

        self.inv_vocab = {v: k for k, v in self.vocab.items()}

    def encode(self, text: str, max_length: int = 64) -> List[int]:
        """Encodes string into token IDs with [CLS] and [SEP] padding/truncation."""
        clean = self.normalize_text(text)
        tokens = re.findall(r"\[-?\d+,\s*-?\d+\]|[a-zA-Z0-9_]+", clean)
        ids = [self.vocab["[CLS]"]]
        for tok in tokens:
            ids.append(self.vocab.get(tok, self.vocab["[UNK]"]))
        ids.append(self.vocab["[SEP]"])

        if len(ids) > max_length:
            ids = ids[:max_length - 1] + [self.vocab["[SEP]"]]
        else:
            ids = ids + [self.vocab["[PAD]"]] * (max_length - len(ids))
        return ids
        ids = [self.vocab["[CLS]"]]
        for tok in tokens:
            ids.append(self.vocab.get(tok, self.vocab["[UNK]"]))
        ids.append(self.vocab["[SEP]"])

        if len(ids) > max_length:
            ids = ids[:max_length - 1] + [self.vocab["[SEP]"]]
        else:
            ids = ids + [self.vocab["[PAD]"]] * (max_length - len(ids))
        return ids

    def decode(self, token_ids: List[int]) -> str:
        words = []
        for tid in token_ids:
            tok = self.inv_vocab.get(tid, "[UNK]")
            if tok in ["[PAD]", "[CLS]", "[SEP]"]:
                continue
            words.append(tok)
        return " ".join(words)

    def save(self, path: Path = VOCAB_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.vocab, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = VOCAB_FILE) -> DofusDomainTokenizer:
        if path.exists():
            v = json.loads(path.read_text(encoding="utf-8"))
            return cls(vocab=v)
        return cls()


class DofusTransformerBlock(nn.Module):
    """Pre-LayerNorm Transformer Encoder block with SwiGLU / GELU feedforward."""

    def __init__(self, d_model: int = 256, nhead: int = 8, dim_feedforward: int = 768, dropout: float = 0.1):
        super().__init__()
        self.ln1 = nn.LayerNorm(d_model)
        self.attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout, batch_first=True)
        self.ln2 = nn.LayerNorm(d_model)
        self.ffn = nn.Sequential(
            nn.Linear(d_model, dim_feedforward),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(dim_feedforward, d_model),
            nn.Dropout(dropout),
        )

    def forward(self, x: torch.Tensor, key_padding_mask: Optional[torch.Tensor] = None) -> torch.Tensor:
        # Pre-LN Self-Attention
        norm_x = self.ln1(x)
        attn_out, _ = self.attn(norm_x, norm_x, norm_x, key_padding_mask=key_padding_mask)
        x = x + attn_out

        # Pre-LN Feedforward
        x = x + self.ffn(self.ln2(x))
        return x


class DofusBrainTransformer(nn.Module):
    """Domain-Native Specialist Transformer (~12M parameters).

    Trained from scratch on DofusDB, Papycha guides, and gameplay logs.
    Serves as the domain teacher and multi-task executive.
    """

    def __init__(
        self,
        vocab_size: int = 4096,
        d_model: int = 256,
        nhead: int = 8,
        num_layers: int = 6,
        dim_feedforward: int = 768,
        max_seq_len: int = 128,
        num_policy_classes: int = 4,   # Stances: BURST_LETHAL, BUFF_AND_ENGAGE, TRAP_AND_KITE, SHIELD_DEFENSE
        num_intent_classes: int = 12,  # Navigation, Farm, Quests, Bot controls
        dropout: float = 0.1,
    ):
        super().__init__()
        self.d_model = d_model
        self.token_emb = nn.Embedding(vocab_size, d_model, padding_idx=0)
        self.pos_emb = nn.Embedding(max_seq_len, d_model)

        self.blocks = nn.ModuleList([
            DofusTransformerBlock(d_model, nhead, dim_feedforward, dropout)
            for _ in range(num_layers)
        ])
        self.ln_f = nn.LayerNorm(d_model)

        # Multi-task heads
        self.mlm_head = nn.Linear(d_model, vocab_size)           # Self-supervised pretraining
        self.policy_head = nn.Linear(d_model, num_policy_classes) # Combat policy
        self.intent_head = nn.Linear(d_model, num_intent_classes) # NLP commands
        self.value_head = nn.Linear(d_model, 1)                   # State value estimation

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> Dict[str, torch.Tensor]:
        batch_size, seq_len = input_ids.shape
        pos = torch.arange(0, seq_len, dtype=torch.long, device=input_ids.device).unsqueeze(0)

        x = self.token_emb(input_ids) + self.pos_emb(pos)

        key_padding_mask = (input_ids == 0) if attention_mask is None else (attention_mask == 0)

        for blk in self.blocks:
            x = blk(x, key_padding_mask=key_padding_mask)

        x = self.ln_f(x)

        # Pooled representation from [CLS] token at position 0
        cls_rep = x[:, 0, :]

        return {
            "mlm_logits": self.mlm_head(x),
            "policy_logits": self.policy_head(cls_rep),
            "intent_logits": self.intent_head(cls_rep),
            "value_pred": self.value_head(cls_rep),
            "cls_embedding": cls_rep,
        }


class DofusDomainDistiller:
    """Self-distills domain representations from DofusBrainTransformer into THX-01 and TRM-ACT.

    1. THX-01 Distillation: Transfers deep linguistic, quest, and navigation intent knowledge
       from the domain-native teacher into THX-01 for peak client inference performance.
    2. TRM-ACT Distillation: Transfers tactical combat stance and continuous value representations.
    """

    @classmethod
    def distill_to_thx01(
        cls,
        teacher_model: DofusBrainTransformer,
        student_model: Any,  # THX-01 / ModernBERT AutoModelForSequenceClassification
        teacher_tokens: torch.Tensor,
        student_inputs: Dict[str, torch.Tensor],
        labels: torch.Tensor,
        temperature: float = 2.5,
        alpha: float = 0.65,
    ) -> torch.Tensor:
        """Distills domain-native Dofus knowledge into THX-01 for fast, zero-lag inference.

        Computes:
        L_total = (1 - alpha) * CE(student_logits, labels) + alpha * (T^2) * KL(student/T, teacher/T)
        """
        teacher_model.eval()
        with torch.no_grad():
            t_out = teacher_model(teacher_tokens)
            t_logits = t_out["intent_logits"]

        s_out = student_model(**student_inputs)
        s_logits = s_out.logits

        # Hard supervised loss
        loss_ce = F.cross_entropy(s_logits, labels)

        # Soft teacher distribution matching (dark knowledge)
        p_teacher = F.softmax(t_logits / temperature, dim=-1)
        log_p_student = F.log_softmax(s_logits / temperature, dim=-1)
        loss_kl = F.kl_div(log_p_student, p_teacher, reduction="batchmean") * (temperature ** 2)

        return (1.0 - alpha) * loss_ce + alpha * loss_kl

    @classmethod
    def distill_to_trm_combat(
        cls,
        teacher_model: DofusBrainTransformer,
        student_model: Any,  # TinyRecursiveReasoner (TRM-ACT)
        token_inputs: torch.Tensor,
        state_vectors: torch.Tensor,
        policy_labels: torch.Tensor,
        value_targets: torch.Tensor,
        temperature: float = 2.0,
        alpha: float = 0.6,
    ) -> torch.Tensor:
        """Transfers domain knowledge from DofusBrainTransformer to TRM-ACT."""
        teacher_model.eval()
        with torch.no_grad():
            t_out = teacher_model(token_inputs)
            t_policy_logits = t_out["policy_logits"]
            t_val_pred = t_out["value_pred"]

        # Student TRM-ACT batch forward pass
        if hasattr(student_model, "in_proj") and hasattr(student_model, "gru"):
            h = student_model.ln_in(F.gelu(student_model.in_proj(state_vectors)))
            for _ in range(2):
                h = student_model.ln_rec(student_model.gru(h, h))
            s_logits = student_model.stance_head(h)
            s_val_pred = student_model.value_head(h)
        else:
            s_logits, _, s_val_pred = student_model(state_vectors)

        # Hard loss
        loss_ce = F.cross_entropy(s_logits, policy_labels)
        loss_mse = F.mse_loss(s_val_pred, value_targets)

        # Soft distillation loss
        p_teacher = F.softmax(t_policy_logits / temperature, dim=-1)
        log_p_student = F.log_softmax(s_logits / temperature, dim=-1)
        loss_kl = F.kl_div(log_p_student, p_teacher, reduction="batchmean") * (temperature ** 2)

        total_loss = (1.0 - alpha) * (loss_ce + 0.05 * loss_mse) + alpha * loss_kl
        return total_loss

    # Backward compatibility alias
    distill_step = distill_to_trm_combat
