"""Compact causal TCN for multi-task network telemetry.

Design constraints from the plan:

* ~1M parameters or fewer, measured rather than assumed.
* Strictly causal convolutions so that inference on a live stream cannot look at
  the future — the original script's dilated blocks used left padding without
  trimming, which silently mixed future timesteps into the receptive field.
* Shared temporal backbone with separate output heads per task:
  latency (with uncertainty), jitter, loss probability, degradation probability,
  and an anomaly score.
* Security classification is deliberately NOT a head here. Phase 6 keeps attack
  detection in deterministic, separately testable components; this model only
  produces suspicion scores that those components may consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from .features import FEATURE_NAMES, HORIZONS, TARGET_BLOCK
from .identity_mask import InstanceIdentityMaskLayer


class CausalBlock(nn.Module):
    """Depthwise-style causal conv block with residual connection."""

    def __init__(self, channels: int, kernel_size: int, dilation: int, dropout: float) -> None:
        super().__init__()
        self.pad = (kernel_size - 1) * dilation
        self.conv = nn.Conv1d(channels, channels, kernel_size,
                              dilation=dilation, padding=self.pad)
        self.activation = nn.GELU()
        self.norm = nn.GroupNorm(1, channels)
        self.dropout = nn.Dropout(dropout) if dropout > 0 else nn.Identity()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.conv(x)
        if self.pad:
            # Trim the trailing future steps introduced by left padding.
            out = out[:, :, :-self.pad]
        out = self.activation(self.norm(out))
        return x + self.dropout(out)


@dataclass
class ModelConfig:
    features: int = len(FEATURE_NAMES)
    hidden: int = 96
    levels: int = 5
    kernel_size: int = 3
    dropout: float = 0.1
    horizons: tuple[int, ...] = HORIZONS
    use_identity_mask: bool = True
    architecture: str = "tcn"  # "tcn" or "trm_moe"
    num_experts: int = 4
    top_k: int = 2
    aux_loss_weight: float = 0.01

    def as_dict(self) -> dict[str, Any]:
        return {
            "features": self.features,
            "hidden": self.hidden,
            "levels": self.levels,
            "kernel_size": self.kernel_size,
            "dropout": self.dropout,
            "horizons": list(self.horizons),
            "use_identity_mask": self.use_identity_mask,
            "architecture": self.architecture,
            "num_experts": self.num_experts,
            "top_k": self.top_k,
            "aux_loss_weight": self.aux_loss_weight,
        }


class NetworkHealthTCN(nn.Module):
    """Shared backbone with task-specific heads.

    Head outputs per horizon ``h``, in the order defined by ``features``'s
    ``TARGET_BLOCK`` layout:
        rtt_log (h), rtt_logvar (h), jitter_log (h), loss_logit (h),
        degradation_logit (h), anomaly_score (shared across horizons).
    """

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        self.input_proj = nn.Conv1d(config.features, config.hidden, kernel_size=1)
        self.identity_mask = (InstanceIdentityMaskLayer(config.hidden)
                              if getattr(config, "use_identity_mask", True)
                              else nn.Identity())
        self.blocks = nn.ModuleList([
            CausalBlock(config.hidden, config.kernel_size, 2 ** i, config.dropout)
            for i in range(config.levels)
        ])
        pooled = config.hidden * 2
        n_horizons = len(config.horizons)
        self.rtt_head = nn.Linear(pooled, n_horizons)
        self.rtt_logvar_head = nn.Linear(pooled, n_horizons)
        self.jitter_head = nn.Linear(pooled, n_horizons)
        self.loss_head = nn.Linear(pooled, n_horizons)
        self.degradation_head = nn.Linear(pooled, n_horizons)
        self.anomaly_head = nn.Linear(pooled, 1)

    def _pool(self, hidden: torch.Tensor) -> torch.Tensor:
        return torch.cat([hidden[:, :, -1], hidden.mean(dim=-1)], dim=-1)

    def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        hidden = self.input_proj(x.transpose(1, 2))
        hidden = self.identity_mask(hidden)
        for block in self.blocks:
            hidden = block(hidden)
        pooled = self._pool(hidden)
        return {
            "rtt_log": self.rtt_head(pooled),
            "rtt_logvar": self.rtt_logvar_head(pooled),
            "jitter_log": self.jitter_head(pooled),
            "loss_logit": self.loss_head(pooled),
            "degradation_logit": self.degradation_head(pooled),
            "anomaly_score": self.anomaly_head(pooled).squeeze(-1),
        }

    def parameter_count(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


def build_model(config: ModelConfig | None = None, **kwargs: Any) -> nn.Module:
    cfg = config or ModelConfig(**kwargs)
    if getattr(cfg, "architecture", "tcn") == "trm_moe":
        from .trm_moe import NetworkHealthTRMMoE, TRMMoEConfig
        trm_cfg = TRMMoEConfig(
            in_features=cfg.features,
            hidden=cfg.hidden,
            num_experts=getattr(cfg, "num_experts", 4),
            top_k=getattr(cfg, "top_k", 2),
            dropout=cfg.dropout,
            kernel_size=cfg.kernel_size,
            horizons=cfg.horizons,
            use_identity_mask=cfg.use_identity_mask,
            aux_loss_weight=getattr(cfg, "aux_loss_weight", 0.01),
        )
        return NetworkHealthTRMMoE(trm_cfg)
    return NetworkHealthTCN(cfg)


def _split_targets(targets: torch.Tensor, n_horizons: int) -> dict[str, torch.Tensor]:
    """Unpack the flat target vector into named tensors."""
    block = targets.view(-1, n_horizons, TARGET_BLOCK)
    return {
        "rtt_log": block[:, :, 0],
        "rtt_mask": block[:, :, 1],
        "jitter_log": block[:, :, 2],
        "jitter_mask": block[:, :, 3],
        "loss": block[:, :, 4],
    }


def multi_task_loss(outputs: dict[str, torch.Tensor], targets: torch.Tensor,
                    loss_cfg: dict[str, float] | None = None) -> tuple[torch.Tensor, dict[str, float]]:
    """Masked multi-task loss; losses on unavailable targets are not counted.

    Masking matters: a packet-loss observation has no RTT, so regressing toward
    an imputed zero would teach the model to predict a value that never occurred.
    """
    cfg = {"rtt": 1.0, "jitter": 0.3, "loss": 0.7, "degradation": 0.7, "anomaly": 0.05}
    if loss_cfg:
        cfg.update(loss_cfg)
    n_horizons = targets.shape[1] // TARGET_BLOCK
    t = _split_targets(targets, n_horizons)

    rtt_pred = outputs["rtt_log"]
    rtt_logvar = outputs["rtt_logvar"].clamp(-8.0, 4.0)
    rtt_mask = t["rtt_mask"]
    rtt_err = (rtt_pred - t["rtt_log"]) ** 2
    rtt_nll = 0.5 * (rtt_err / rtt_logvar.exp() + rtt_logvar)
    rtt_loss = (rtt_nll * rtt_mask).sum() / rtt_mask.sum().clamp(min=1.0)

    jitter_mask = t["jitter_mask"]
    jitter_err = (outputs["jitter_log"] - t["jitter_log"]) ** 2
    jitter_loss = (jitter_err * jitter_mask).sum() / jitter_mask.sum().clamp(min=1.0)

    loss_loss = nn.functional.binary_cross_entropy_with_logits(
        outputs["loss_logit"], t["loss"], reduction="mean")

    # Degradation is defined from the observed horizon only: a link counts as
    # degraded when the future observation is lost OR its RTT rises materially
    # above the recent baseline. Both signals come from the target window, so
    # the label is honest and reproducible.
    degradation = _degradation_label(t)
    degradation_loss = nn.functional.binary_cross_entropy_with_logits(
        outputs["degradation_logit"], degradation, reduction="mean")

    anomaly_target = _anomaly_label(t)
    anomaly_loss = nn.functional.mse_loss(outputs["anomaly_score"], anomaly_target)

    total = (cfg["rtt"] * rtt_loss + cfg["jitter"] * jitter_loss
             + cfg["loss"] * loss_loss + cfg["degradation"] * degradation_loss
             + cfg["anomaly"] * anomaly_loss)
    aux_loss = outputs.get("aux_loss")
    if aux_loss is not None and torch.is_tensor(aux_loss) and aux_loss.numel() > 0:
        total = total + aux_loss
    parts = {
        "rtt_nll": float(rtt_loss.detach()),
        "jitter_mse": float(jitter_loss.detach()),
        "loss_bce": float(loss_loss.detach()),
        "degradation_bce": float(degradation_loss.detach()),
        "anomaly_mse": float(anomaly_loss.detach()),
        "aux_loss": float(aux_loss.detach()) if (aux_loss is not None and torch.is_tensor(aux_loss)) else 0.0,
        "total": float(total.detach()),
    }
    return total, parts


def _degradation_label(t: dict[str, torch.Tensor]) -> torch.Tensor:
    """1 when the future observation is lost, or RTT exceeds baseline + margin."""
    lost = t["loss"] > 0.5
    mask = t["rtt_mask"] > 0.5
    # Compare against the mean of the horizon block as a local reference.
    reference = (t["rtt_log"] * mask).sum(dim=1, keepdim=True) / mask.sum(dim=1, keepdim=True).clamp(min=1.0)
    elevated = mask & ((t["rtt_log"] - reference) > 0.35)
    return (lost | elevated).float()


def _anomaly_label(t: dict[str, torch.Tensor]) -> torch.Tensor:
    """Continuous suspicion label in [0, 1]; NOT an attack classification."""
    lost_rate = t["loss"].mean(dim=1)
    mask = t["rtt_mask"] > 0.5
    rtt = torch.where(mask, t["rtt_log"], torch.zeros_like(t["rtt_log"]))
    mean = rtt.sum(dim=1) / mask.sum(dim=1).clamp(min=1.0)
    var = (((rtt - mean.unsqueeze(1)) * mask) ** 2).sum(dim=1) / mask.sum(dim=1).clamp(min=1.0)
    deviation = torch.sqrt(var.clamp(min=0.0))
    return torch.clamp(0.7 * lost_rate + 0.3 * torch.tanh(deviation), 0.0, 1.0)
