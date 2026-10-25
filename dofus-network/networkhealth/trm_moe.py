"""Temporal Reasoning Model with Dual-Cycle Architecture and Sparse Mixture of Experts.

Architecture Overview:
----------------------
1. Instance Identity Masking Layer (temporal instance norm for instance-invariance).
2. Dual-Cycle Temporal Reasoning:
   - h_cycle (High-Frequency Micro Cycle): captures fast step-to-step packet jitter,
     instant RTT spikes, and short-term bursts (dilations 1, 2, 4).
   - L_cycle (Low-Frequency Macro Cycle): captures long-term trends, diurnal cycles,
     ISP peak traffic hours, and baseline drift (dilations 8, 16, 32, 64).
   - CrossCycleFusion: adaptive gating modulating micro dynamics with macro state.
3. Sparse Mixture of Experts (MoE):
   - Top-2 Gating Router over E specialized expert networks.
   - Load-balancing auxiliary loss to ensure uniform expert utilization.
   - High parameter capacity during training, sparse ultra-low latency on laptop CPU.
4. Multi-Horizon Prediction Heads (RTT, uncertainty, jitter, loss, degradation, anomaly).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from .features import FEATURE_NAMES, HORIZONS
from .identity_mask import InstanceIdentityMaskLayer


class Chomp1d(nn.Module):
    """Causal truncation: trims right-side padding so convolution remains causal."""

    def __init__(self, chomp_size: int):
        super().__init__()
        self.chomp_size = chomp_size

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.chomp_size <= 0:
            return x
        return x[:, :, :-self.chomp_size].contiguous()


class TemporalCycleBlock(nn.Module):
    """Causal dilated residual block for a specific temporal frequency band."""

    def __init__(self, in_channels: int, out_channels: int, kernel_size: int,
                 dilation: int, dropout: float = 0.1):
        super().__init__()
        padding = (kernel_size - 1) * dilation
        self.conv1 = nn.Conv1d(in_channels, out_channels, kernel_size,
                               padding=padding, dilation=dilation)
        self.chomp1 = Chomp1d(padding)
        self.act1 = nn.SiLU()
        self.norm1 = nn.GroupNorm(1, out_channels)
        self.drop1 = nn.Dropout(dropout)

        self.conv2 = nn.Conv1d(out_channels, out_channels, kernel_size,
                               padding=padding, dilation=dilation)
        self.chomp2 = Chomp1d(padding)
        self.act2 = nn.SiLU()
        self.norm2 = nn.GroupNorm(1, out_channels)
        self.drop2 = nn.Dropout(dropout)

        self.downsample = nn.Conv1d(in_channels, out_channels, 1) if in_channels != out_channels else None

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = x if self.downsample is None else self.downsample(x)
        out = self.drop1(self.norm1(self.act1(self.chomp1(self.conv1(x)))))
        out = self.drop2(self.norm2(self.act2(self.chomp2(self.conv2(out)))))
        return out + residual


class HighFrequencyCycle(nn.Module):
    """h_cycle: Fast inner cycle for short-range packet dynamics (dilations 1, 2, 4)."""

    def __init__(self, hidden: int, kernel_size: int = 3, dropout: float = 0.1):
        super().__init__()
        self.blocks = nn.ModuleList([
            TemporalCycleBlock(hidden, hidden, kernel_size=kernel_size, dilation=1, dropout=dropout),
            TemporalCycleBlock(hidden, hidden, kernel_size=kernel_size, dilation=2, dropout=dropout),
            TemporalCycleBlock(hidden, hidden, kernel_size=kernel_size, dilation=4, dropout=dropout),
        ])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = x
        for block in self.blocks:
            out = block(out)
        return out


class LowFrequencyCycle(nn.Module):
    """L_cycle: Slow macro cycle for long-range proxy drift and diurnal waves (dilations 8, 16, 32)."""

    def __init__(self, hidden: int, kernel_size: int = 3, dropout: float = 0.1):
        super().__init__()
        self.blocks = nn.ModuleList([
            TemporalCycleBlock(hidden, hidden, kernel_size=kernel_size, dilation=8, dropout=dropout),
            TemporalCycleBlock(hidden, hidden, kernel_size=kernel_size, dilation=16, dropout=dropout),
            TemporalCycleBlock(hidden, hidden, kernel_size=kernel_size, dilation=32, dropout=dropout),
        ])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = x
        for block in self.blocks:
            out = block(out)
        return out


class CrossCycleFusion(nn.Module):
    """Adaptive gating fusion between micro (h_cycle) and macro (L_cycle) representations."""

    def __init__(self, hidden: int):
        super().__init__()
        self.gate = nn.Conv1d(hidden * 2, hidden, kernel_size=1)
        self.norm = nn.LayerNorm(hidden)

    def forward(self, h_state: torch.Tensor, L_state: torch.Tensor) -> torch.Tensor:
        # h_state, L_state: (batch, hidden, time)
        combined = torch.cat([h_state, L_state], dim=1)
        g = torch.sigmoid(self.gate(combined))
        fused = g * h_state + (1.0 - g) * L_state
        # LayerNorm over (batch, time, hidden)
        fused_t = self.norm(fused.transpose(1, 2)).transpose(1, 2)
        return fused_t


class Expert(nn.Module):
    """Single expert MLP specializing in a specific network condition."""

    def __init__(self, hidden: int, expansion: int = 2, dropout: float = 0.1):
        super().__init__()
        self.fc1 = nn.Linear(hidden, hidden * expansion)
        self.act = nn.SiLU()
        self.drop = nn.Dropout(dropout)
        self.fc2 = nn.Linear(hidden * expansion, hidden)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc2(self.drop(self.act(self.fc1(x))))


class SparseMoELayer(nn.Module):
    """Sparse Mixture of Experts with Top-k Gating and Load Balancing Loss."""

    def __init__(self, hidden: int, num_experts: int = 4, top_k: int = 2,
                 dropout: float = 0.1, aux_loss_weight: float = 0.01):
        super().__init__()
        self.num_experts = num_experts
        self.top_k = min(top_k, num_experts)
        self.aux_loss_weight = aux_loss_weight

        self.router = nn.Linear(hidden, num_experts)
        self.experts = nn.ModuleList([
            Expert(hidden, expansion=2, dropout=dropout) for _ in range(num_experts)
        ])
        self.norm = nn.LayerNorm(hidden)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        # x: (batch, time, hidden) or (batch, hidden)
        is_3d = (x.dim() == 3)
        if is_3d:
            b, t, d = x.shape
            flat_x = x.reshape(-1, d)
        else:
            flat_x = x

        # Router logits
        logits = self.router(flat_x)
        probs = F.softmax(logits, dim=-1)

        # Top-k selection
        weights, indices = torch.topk(probs, self.top_k, dim=-1)
        weights = weights / weights.sum(dim=-1, keepdim=True).clamp(min=1e-6)

        # Compute Load Balancing Loss (auxiliary loss)
        # P_i: average probability assigned to expert i across batch
        # f_i: fraction of tokens routed to expert i
        if self.training:
            density = probs.mean(dim=0)
            mask_top1 = F.one_hot(indices[:, 0], num_classes=self.num_experts).float()
            fraction = mask_top1.mean(dim=0)
            aux_loss = self.aux_loss_weight * self.num_experts * (fraction * density).sum()
        else:
            aux_loss = torch.tensor(0.0, device=x.device)

        # Dispatch and combine across top-k experts
        out = torch.zeros_like(flat_x)
        for k_idx in range(self.top_k):
            exp_idx = indices[:, k_idx]
            exp_weight = weights[:, k_idx].unsqueeze(-1)
            for e_num in range(self.num_experts):
                mask = (exp_idx == e_num)
                if mask.any():
                    selected_x = flat_x[mask]
                    expert_out = self.experts[e_num](selected_x)
                    out[mask] += exp_weight[mask] * expert_out

        out = self.norm(flat_x + out)
        if is_3d:
            out = out.reshape(b, t, d)
        return out, aux_loss


@dataclass
class TRMMoEConfig:
    """Hyperparameters for NetworkHealthTRMMoE."""

    in_features: int = len(FEATURE_NAMES)
    hidden: int = 96
    num_experts: int = 4
    top_k: int = 2
    dropout: float = 0.1
    kernel_size: int = 3
    horizons: tuple[int, ...] = HORIZONS
    use_identity_mask: bool = True
    aux_loss_weight: float = 0.01

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


class NetworkHealthTRMMoE(nn.Module):
    """Hierarchical Dual-Cycle Reasoning Model with Sparse Mixture of Experts.

    Combines:
    - InstanceIdentityMaskLayer (HMAC & baseline normalization)
    - h_cycle (micro packet dynamics)
    - L_cycle (macro proxy drift & diurnal trends)
    - CrossCycleFusion (adaptive frequency gating)
    - SparseMoELayer (specialized routing for clean/mobile/bufferbloat/loss regimes)
    - Multi-horizon prediction heads
    """

    def __init__(self, config: TRMMoEConfig | None = None):
        super().__init__()
        self.config = config or TRMMoEConfig()
        c = self.config

        # 1. Input projection
        self.input_proj = nn.Conv1d(c.in_features, c.hidden, 1)

        # 2. Identity masking layer
        self.identity_mask = InstanceIdentityMaskLayer(c.hidden) if c.use_identity_mask else nn.Identity()

        # 3. Dual-Cycle Temporal Reasoning
        self.h_cycle = HighFrequencyCycle(c.hidden, kernel_size=c.kernel_size, dropout=c.dropout)
        self.L_cycle = LowFrequencyCycle(c.hidden, kernel_size=c.kernel_size, dropout=c.dropout)
        self.fusion = CrossCycleFusion(c.hidden)

        # 4. Sparse Mixture of Experts
        self.moe = SparseMoELayer(c.hidden, num_experts=c.num_experts, top_k=c.top_k,
                                  dropout=c.dropout, aux_loss_weight=c.aux_loss_weight)

        # 5. Multi-Horizon Prediction Heads
        n_h = len(c.horizons)
        self.rtt_head = nn.Linear(c.hidden, n_h)
        self.rtt_var_head = nn.Linear(c.hidden, n_h)
        self.jitter_head = nn.Linear(c.hidden, n_h)
        self.loss_head = nn.Linear(c.hidden, n_h)
        self.degradation_head = nn.Linear(c.hidden, n_h)
        self.anomaly_head = nn.Linear(c.hidden, 1)

        self._init_weights()

    def _init_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, (nn.Conv1d, nn.Linear)):
                nn.init.kaiming_normal_(m.weight, nonlinearity="relu")
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
        # Stable head initialisation
        nn.init.zeros_(self.rtt_head.bias)
        nn.init.constant_(self.rtt_var_head.bias, -1.0)
        nn.init.constant_(self.loss_head.bias, -2.0)
        nn.init.constant_(self.degradation_head.bias, -2.0)
        nn.init.zeros_(self.anomaly_head.bias)

    def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        """Forward pass.

        Args:
            x: Input tensor of shape (batch, seq_len, in_features).

        Returns:
            Dictionary containing head predictions and auxiliary MoE loss.
        """
        # (batch, seq_len, in_features) -> (batch, in_features, seq_len)
        x_conv = x.transpose(1, 2)
        proj = self.input_proj(x_conv)
        masked = self.identity_mask(proj)

        # Dual-cycle reasoning
        h_state = self.h_cycle(masked)
        L_state = self.L_cycle(masked)
        fused = self.fusion(h_state, L_state)

        # Take anchor observation at the final time-step: (batch, hidden)
        anchor = fused[:, :, -1]

        # Pass through Sparse MoE
        moe_out, aux_loss = self.moe(anchor)

        return {
            "rtt_log": self.rtt_head(moe_out),
            "rtt_logvar": self.rtt_var_head(moe_out),
            "jitter_log": F.softplus(self.jitter_head(moe_out)),
            "loss_logit": self.loss_head(moe_out),
            "degradation_logit": self.degradation_head(moe_out),
            "anomaly_score": F.softplus(self.anomaly_head(moe_out)).squeeze(-1),
            "aux_loss": aux_loss,
        }

    def parameter_count(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)
