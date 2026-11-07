"""Shared transformer trunk for the encoder and tool-policy models."""

from __future__ import annotations

from typing import Any, Tuple

from torch import nn


def build_trunk(config: Any) -> Tuple[nn.Embedding, nn.Embedding,
                                      nn.TransformerEncoder, nn.LayerNorm]:
    tok_emb = nn.Embedding(config.vocab_size, config.d_model)
    pos_emb = nn.Embedding(config.max_seq_len, config.d_model)
    layer = nn.TransformerEncoderLayer(
        d_model=config.d_model,
        nhead=config.n_heads,
        dim_feedforward=config.ffn,
        dropout=config.dropout,
        activation="gelu",
        batch_first=True,
        norm_first=True,
    )
    encoder = nn.TransformerEncoder(
        layer, num_layers=config.n_layers, enable_nested_tensor=False)
    norm = nn.LayerNorm(config.d_model)
    return tok_emb, pos_emb, encoder, norm


def init_weights(module: nn.Module) -> None:
    if isinstance(module, nn.Linear):
        nn.init.normal_(module.weight, mean=0.0, std=0.02)
        if module.bias is not None:
            nn.init.zeros_(module.bias)
    elif isinstance(module, nn.Embedding):
        nn.init.normal_(module.weight, mean=0.0, std=0.02)
