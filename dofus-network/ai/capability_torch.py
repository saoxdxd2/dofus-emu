"""Torch module for the descriptor-scored tool head.

Kept out of ``capability_head.py`` so the serving path and the tests can import
the descriptor encoding without torch. The scorer has no per-tool parameters:
its only inputs are the state vector and the descriptor matrix, so the number
of tools is a runtime property of the input, not of the checkpoint.
"""

from __future__ import annotations

import math
from typing import List, Optional

import torch
import torch.nn as nn
import torch.nn.functional as F


class CapabilityScoredHead(nn.Module):
    """``score = <normalize(W_s h), normalize(W_d d)> * scale``.

    ``h`` is the state trunk output, ``d`` is a capability descriptor row. The
    descriptor projection is bias-free: a bias would be a shared offset, not a
    per-tool prior, and a per-tool bias is the fixed-width head this replaces.
    """

    def __init__(
        self,
        feature_dim: int,
        hidden: int = 192,
        depth: int = 2,
        descriptor_dim: int = 96,
        d_model: int = 128,
        dropout: float = 0.0,
        scale: float = 10.0,
    ) -> None:
        super().__init__()
        if depth < 0:
            raise ValueError("depth must be >= 0")
        layers: List[nn.Module] = []
        width = feature_dim
        for _ in range(depth):
            layers.append(nn.Linear(width, hidden))
            layers.append(nn.GELU())
            if dropout > 0:
                layers.append(nn.Dropout(dropout))
            width = hidden
        self.trunk = nn.Sequential(*layers) if layers else nn.Identity()
        self.state_proj = nn.Linear(width, d_model)
        self.desc_proj = nn.Linear(descriptor_dim, d_model, bias=False)
        # A learned temperature keeps the softmax from saturating before the
        # trunk has learned a useful geometry. Stored as a parameter so ONNX
        # export carries it.
        self.log_scale = nn.Parameter(torch.tensor(math.log(max(scale, 1e-3))))

    def forward(self, features: torch.Tensor, descriptors: torch.Tensor) -> torch.Tensor:
        state = F.normalize(self.state_proj(self.trunk(features)), dim=-1)
        desc = F.normalize(self.desc_proj(descriptors), dim=-1)
        scale = self.log_scale.exp().clamp(max=50.0)
        return (state @ desc.t()) * scale


def build_head(
    feature_dim: int,
    hidden: int = 192,
    depth: int = 2,
    descriptor_dim: int = 96,
    d_model: int = 128,
    dropout: float = 0.0,
    scale: float = 10.0,
) -> CapabilityScoredHead:
    return CapabilityScoredHead(
        feature_dim=feature_dim,
        hidden=hidden,
        depth=depth,
        descriptor_dim=descriptor_dim,
        d_model=d_model,
        dropout=dropout,
        scale=scale,
    )
