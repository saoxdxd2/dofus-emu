# -*- coding: utf-8 -*-
"""Polyglot-0: a one-layer, four-head attention encoder whose weights are
written by hand from a concept table.  No optimizer, no loss, no gradients.

Weight layout (d_model = 544, 4 heads x 136):
  0..127    shared concept code (orthogonal 128-dim Hadamard rows)
  128       signed polarity
  129       |polarity|
  130       content-word flag
  131       sentiment-word flag
  132..135  language one-hot (en, fr, es, de)
  136       negator flag
  137       intensifier flag
  138       word flag (any lexical token)
  139       [CLS]
  140       [SEP]
  141       punctuation
  142..157  sinusoidal position (added in forward)

Head 0 pools content-word concept+polarity features into [CLS].
Head 1 pools the language one-hot over all words.
Head 2 detects a negator.
Head 3 detects an intensifier.
"""

import math

import torch
import torch.nn as nn

from . import lexicon
from .tokenizer import build_vocab

D_MODEL = 544
N_HEADS = 4
HEAD_DIM = D_MODEL // N_HEADS  # 136
CONCEPT_DIM = 128
POL = 128
ABSPOL = 129
CONTENT = 130
SENT = 131
LANG = slice(132, 136)
NEG = 136
INT = 137
WORD = 138
CLS = 139
SEP = 140
PUNCT = 141
POS = slice(142, 158)
ALPHA = 80.0  # selector sharpness


def hadamard_codes(n_codes):
    """Rows of a 128x128 Sylvester Hadamard matrix, orthonormal."""
    h = torch.tensor([[1.0]])
    while h.shape[0] < CONCEPT_DIM:
        h = torch.cat([torch.cat([h, h], 1), torch.cat([h, -h], 1)], 0)
    h = h / math.sqrt(CONCEPT_DIM)
    assert h.shape == (CONCEPT_DIM, CONCEPT_DIM)
    if n_codes > CONCEPT_DIM:
        raise ValueError("more concepts than orthogonal codes")
    return h[:n_codes]


def build_model(seed=0):
    itos, stoi, form_to_concept, concept_to_row, form_langs = build_vocab()
    concept_ids = [c[0] for c in lexicon.CONCEPTS]
    concept_idx = {c: i for i, c in enumerate(concept_ids)}
    codes = hadamard_codes(len(concept_ids))

    model = HandProgrammedModel(len(itos), len(concept_ids))
    model.itos = itos
    model.stoi = stoi
    model.form_to_concept = form_to_concept
    model.concept_to_row = concept_to_row
    model.concept_ids = concept_ids
    model.concept_idx = concept_idx
    model.codes = codes
    model.form_langs = form_langs

    with torch.no_grad():
        E = model.embed.weight
        E.zero_()
        for tok, row in stoi.items():
            if tok in ("[PAD]", "[UNK]"):
                continue
            if tok == "[CLS]":
                E[row, CLS] = 1.0
                continue
            if tok == "[SEP]":
                E[row, SEP] = 1.0
                continue
            if tok in ". , ! ? ; : ¿ ¡ \" ( ) [ ]".split():
                E[row, PUNCT] = 1.0
                continue
            cid = form_to_concept.get(tok)
            E[row, WORD] = 1.0
            if cid is None:
                continue
            p, pol, content = concept_to_row[cid]
            E[row, :CONCEPT_DIM] = codes[concept_idx[cid]]
            E[row, POL] = pol
            E[row, ABSPOL] = abs(pol)
            E[row, CONTENT] = 1.0 if content else 0.0
            E[row, SENT] = 1.0 if abs(pol) > 1e-9 else 0.0
            if p == "neg":
                E[row, NEG] = 1.0
            if cid in ("very", "really"):
                E[row, INT] = 1.0
            counts = form_langs.get(tok, {})
            total = sum(counts.values())
            for lang_i, n in counts.items():
                E[row, 132 + lang_i] = n / total

        # --- attention projections -------------------------------------
        W = model.attn.in_proj_weight
        b = model.attn.in_proj_bias
        W.zero_()
        b.zero_()

        # Queries: only [CLS] asks; selector lives in head dim 0.
        for h in range(N_HEADS):
            W[h * HEAD_DIM, CLS] = 1.0

        # Keys: select content / words / negators / intensifiers.
        W[D_MODEL + 0 * HEAD_DIM, CONTENT] = ALPHA
        W[D_MODEL + 1 * HEAD_DIM, WORD] = ALPHA
        W[D_MODEL + 2 * HEAD_DIM, NEG] = ALPHA
        W[D_MODEL + 3 * HEAD_DIM, INT] = ALPHA

        # Values.
        for i in range(POL + 3):  # 0..130: concept, polarity, |p|, content
            W[2 * D_MODEL + 0 * HEAD_DIM + i, i] = 1.0
        W[2 * D_MODEL + 0 * HEAD_DIM + 131, SENT] = 1.0
        for i in range(4):
            W[2 * D_MODEL + 1 * HEAD_DIM + i, 132 + i] = 1.0
        W[2 * D_MODEL + 2 * HEAD_DIM, NEG] = 1.0
        W[2 * D_MODEL + 3 * HEAD_DIM, INT] = 1.0

        # Output projection routes each head to its slot in the residual.
        O = model.attn.out_proj.weight
        O.zero_()
        model.attn.out_proj.bias.zero_()
        for i in range(132):
            O[i, i] = 1.0
        for i in range(4):
            O[132 + i, HEAD_DIM + i] = 1.0
        O[NEG, 2 * HEAD_DIM] = 1.0
        O[INT, 3 * HEAD_DIM] = 1.0

        # --- task heads ------------------------------------------------
        model.lang_head.weight.copy_(4.0 * torch.eye(4))
        model.lang_head.bias.zero_()
        model.sent_scale.weight.copy_(torch.tensor([[-2.0, 1.0]]))
        model.sent_boost.weight.copy_(torch.tensor([[0.5]]))

    for p in model.parameters():
        p.requires_grad_(False)
    assert not any(p.requires_grad for p in model.parameters())
    model.eval()
    return model


class HandProgrammedModel(nn.Module):
    def __init__(self, vocab_size, n_concepts):
        super().__init__()
        self.embed = nn.Embedding(vocab_size, D_MODEL, padding_idx=0)
        self.attn = nn.MultiheadAttention(
            D_MODEL, N_HEADS, dropout=0.0, bias=True, batch_first=True
        )
        self.lang_head = nn.Linear(4, 4, bias=True)
        # scale = 1 - 2*neg  (linear gate, not a sigmoid)
        self.sent_scale = nn.Linear(2, 1, bias=False)
        # boost = 1 + relu(w * intensifier)
        self.sent_boost = nn.Linear(1, 1, bias=False)
        self.register_buffer("tau", torch.tensor(0.10))
        self.n_concepts = n_concepts

    def forward(self, ids, lengths):
        B, L = ids.shape
        x = self.embed(ids)
        pos = _positional(L, x.device)
        x = x + pos.unsqueeze(0)
        pad_mask = ids == 0
        h, _ = self.attn(x, x, x, key_padding_mask=pad_mask, need_weights=False)
        x = x + h
        cls = x[torch.arange(B, device=x.device), lengths - 1]

        lang_logits = self.lang_head(cls[:, LANG])
        pol = cls[:, POL]
        neg = cls[:, NEG:NEG + 1]
        one = torch.ones_like(neg)
        scale = self.sent_scale(torch.cat([neg, one], dim=-1)).squeeze(-1)
        inten = cls[:, INT]
        boost = 1.0 + torch.relu(self.sent_boost(inten.unsqueeze(-1))).squeeze(-1)
        score = scale * pol * boost
        zeros = torch.zeros_like(score)
        sent_logits = torch.stack(
            [-score - self.tau, zeros, score - self.tau], dim=-1
        )
        return {
            "cls": cls,
            "lang_logits": lang_logits,
            "sent_logits": sent_logits,
            "sent_score": score,
        }

    def encode(self, ids_list):
        """ids_list: list of token-id lists, each WITHOUT [CLS]."""
        cls_id = self.stoi["[CLS]"]
        seqs = [list(ids) + [cls_id] for ids in ids_list]
        lengths = torch.tensor([len(s) for s in seqs], dtype=torch.long)
        maxlen = int(lengths.max())
        ids = torch.zeros(len(seqs), maxlen, dtype=torch.long)
        for i, s in enumerate(seqs):
            ids[i, :len(s)] = torch.tensor(s, dtype=torch.long)
        with torch.no_grad():
            return self.forward(ids, lengths)

    def word_concept(self, token_id):
        return self.embed.weight[token_id, :CONCEPT_DIM]


def _positional(L, device):
    pe = torch.zeros(L, 16, device=device)
    position = torch.arange(0, L, device=device, dtype=torch.float32).unsqueeze(1)
    div = torch.exp(
        torch.arange(0, 16, 2, device=device, dtype=torch.float32)
        * (-math.log(10000.0) / 16.0)
    )
    pe[:, 0::2] = torch.sin(position * div)
    pe[:, 1::2] = torch.cos(position * div)
    out = torch.zeros(L, D_MODEL, device=device)
    out[:, POS] = pe
    return out


def canonical_forms():
    """concept -> {lang: canonical surface form} (lowercased)."""
    out = {}
    for row in lexicon.CONCEPTS:
        cid, *rest = row
        forms = rest[3:]
        out[cid] = {}
        for lang, field in zip(lexicon.LANGS, forms):
            if field:
                out[cid][lang] = field.split(",")[0].strip().lower()
    return out
