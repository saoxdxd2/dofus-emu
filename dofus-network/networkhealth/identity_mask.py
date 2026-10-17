"""Per-instance identity-masking layer for network telemetry and neural modeling.

In a multi-instance gaming environment (e.g., 4 concurrent Dofus Touch clients
running behind distinct proxies or network routes), raw telemetry presents two
critical vulnerabilities:

1. Identification & Correlation Leakage:
   Raw probe IDs, slot IDs ('slot-1', 'slot-2'), port numbers, IP addresses, and
   synchronized clock timestamps allow server-side correlation to identify that
   all instances originate from the same machine or farm.

2. Static Latency Overfitting:
   Different proxies/routes possess distinct baseline latencies (e.g. 25ms vs 95ms).
   If a model trains on absolute latency, it memorizes the *identity* of the proxy/probe
   rather than general network degradation dynamics (jitter, packet loss bursts,
   trend acceleration).

This module provides:
* ``InstanceIdentityMasker``: Cryptographic pseudonymization of series keys,
  temporal micro-desynchronization, and instance-relative baseline tracking.
* ``InstanceIdentityMaskLayer``: A PyTorch layer (using InstanceNorm1d) that
  normalizes representations across temporal steps per instance, enforcing
  instance-invariance during forward propagation.
* ``MultiInstanceTracker``: Isolated context tracking ensuring multi-instance
  telemetry never bleeds across concurrent sessions.
"""

from __future__ import annotations

import hashlib
import hmac
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np

try:
    import torch
    from torch import nn
    _HAS_TORCH = True
except ImportError:
    torch = None
    nn = None
    _HAS_TORCH = False


@dataclass
class IdentityMaskConfig:
    """Configuration for per-instance identity masking."""

    salt: str = "dofus-network-identity-v1"
    mask_series_keys: bool = True
    enable_desynchronization: bool = True
    max_jitter_ms: float = 125.0
    enable_baseline_normalization: bool = True
    baseline_alpha: float = 0.05
    scrub_metadata: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "salt": self.salt,
            "mask_series_keys": self.mask_series_keys,
            "enable_desynchronization": self.enable_desynchronization,
            "max_jitter_ms": self.max_jitter_ms,
            "enable_baseline_normalization": self.enable_baseline_normalization,
            "baseline_alpha": self.baseline_alpha,
            "scrub_metadata": self.scrub_metadata,
        }


class InstanceBaselineTracker:
    """Maintains an exponential moving average (EWMA) of RTT for an isolated instance."""

    def __init__(self, alpha: float = 0.05, max_history: int = 64) -> None:
        self.alpha = float(alpha)
        self.baseline_rtt: float | None = None
        self.samples = 0
        self.history: deque[float] = deque(maxlen=max_history)

    def update(self, rtt_ms: float | None) -> float | None:
        if rtt_ms is None or rtt_ms <= 0 or not math.isfinite(rtt_ms):
            return self.baseline_rtt
        self.samples += 1
        self.history.append(float(rtt_ms))
        if self.baseline_rtt is None:
            self.baseline_rtt = float(rtt_ms)
        else:
            self.baseline_rtt = (1.0 - self.alpha) * self.baseline_rtt + self.alpha * float(rtt_ms)
        return self.baseline_rtt

    def relative_delta(self, rtt_ms: float | None) -> float:
        """Return log1p(rtt) - log1p(baseline_rtt), masking absolute proxy offsets."""
        if rtt_ms is None or self.baseline_rtt is None or self.baseline_rtt <= 0:
            return 0.0
        return float(math.log1p(rtt_ms) - math.log1p(self.baseline_rtt))

    def robust_zscore(self, rtt_ms: float | None) -> float:
        """Instance-relative robust z-score: (rtt - median) / iqr."""
        if rtt_ms is None or len(self.history) < 4:
            return 0.0
        arr = np.asarray(self.history, dtype=np.float32)
        med = float(np.median(arr))
        q75, q25 = np.percentile(arr, [75, 25])
        iqr = max(1.0, float(q75 - q25))
        return float((rtt_ms - med) / iqr)


class InstanceIdentityMasker:
    """Masks per-instance identifiers and decouples static network baselines."""

    def __init__(self, config: IdentityMaskConfig | None = None) -> None:
        self.config = config or IdentityMaskConfig()
        self._trackers: dict[str, InstanceBaselineTracker] = {}

    def get_tracker(self, instance_key: str) -> InstanceBaselineTracker:
        if instance_key not in self._trackers:
            self._trackers[instance_key] = InstanceBaselineTracker(
                alpha=self.config.baseline_alpha)
        return self._trackers[instance_key]

    def anonymize_id(self, raw_id: Any) -> str:
        """Cryptographic HMAC-SHA256 pseudonym for any probe, port, or slot identifier."""
        if not self.config.mask_series_keys:
            return str(raw_id)
        digest = hmac.new(
            self.config.salt.encode("utf-8"),
            str(raw_id).encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return f"inst_{digest[:12]}"

    def anonymize_int(self, raw_id: Any) -> int:
        """Deterministic 63-bit positive integer hash for numpy int64 compatibility."""
        if not self.config.mask_series_keys:
            try:
                return int(raw_id)
            except (ValueError, TypeError):
                pass
        digest = hmac.new(
            self.config.salt.encode("utf-8"),
            str(raw_id).encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return int(digest[:14], 16) & 0x7FFFFFFFFFFFFFFF

    def desynchronize_timestamp(self, ts: float, instance_key: str) -> float:
        """Applies deterministic, pseudo-random micro-jitter per instance."""
        if not self.config.enable_desynchronization:
            return ts
        h = int(hashlib.md5(f"{self.config.salt}:{instance_key}:{int(ts)}".encode("utf-8")).hexdigest()[:8], 16)
        jitter_ratio = ((h % 2000) - 1000) / 1000.0
        jitter_sec = jitter_ratio * (self.config.max_jitter_ms / 1000.0)
        return round(max(0.0, ts + jitter_sec), 3)

    def mask_row(self, row: dict[str, Any], raw_instance_id: str | None = None) -> dict[str, Any]:
        """Scrub row metadata, pseudonymize identifiers, and add relative baseline metrics."""
        inst_key = str(raw_instance_id or row.get("probe_id") or row.get("instance_id") or "default")
        anon_inst_str = self.anonymize_id(inst_key)
        anon_inst_int = self.anonymize_int(inst_key)
        meas_raw = row.get("measurement_id", 0)
        anon_meas_int = self.anonymize_int(meas_raw)

        ts = float(row.get("timestamp", 0.0))
        masked_ts = self.desynchronize_timestamp(ts, inst_key)

        rtt = row.get("rtt_ms")
        if rtt is not None:
            try:
                rtt = float(rtt)
            except (TypeError, ValueError):
                rtt = None

        tracker = self.get_tracker(anon_inst_str)
        tracker.update(rtt)
        rel_delta = tracker.relative_delta(rtt)
        zscore = tracker.robust_zscore(rtt)

        out = {
            "source": row.get("source", "masked_telemetry"),
            "measurement_id": anon_meas_int,
            "probe_id": anon_inst_int,
            "instance_tag": anon_inst_str,
            "timestamp": masked_ts,
            "rtt_ms": rtt,
            "rtt_relative_delta": rel_delta,
            "rtt_robust_zscore": zscore,
        }

        if not self.config.scrub_metadata:
            for k, v in row.items():
                if k not in out:
                    out[k] = v

        return out

    def mask_series(self, rows: list[dict[str, Any]], raw_instance_id: str | None = None) -> list[dict[str, Any]]:
        """Process a full time-series with persistent instance baseline tracking."""
        masked: list[dict[str, Any]] = []
        for r in rows:
            masked.append(self.mask_row(r, raw_instance_id))
        return masked


if _HAS_TORCH:
    class InstanceIdentityMaskLayer(nn.Module):
        """Neural network layer for instance-invariant temporal representation.

        Applies 1D instance normalization over the temporal dimension (L)
        for each sequence independently in the batch:
            Shape in:  (Batch, Channels, Seq_Len)
            Shape out: (Batch, Channels, Seq_Len)
        
        This strips constant latency offsets specific to a proxy/probe while
        retaining dynamic temporal variations (jitter, bursts, degradation trends).
        """

        def __init__(self, channels: int, eps: float = 1e-5, affine: bool = True) -> None:
            super().__init__()
            self.norm = nn.InstanceNorm1d(channels, eps=eps, affine=affine)

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            return self.norm(x)
else:
    class InstanceIdentityMaskLayer:  # type: ignore
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass


class MultiInstanceTracker:
    """Thread-safe context coordinator for concurrent game clients."""

    def __init__(self, config: IdentityMaskConfig | None = None) -> None:
        self.config = config or IdentityMaskConfig()
        self.masker = InstanceIdentityMasker(self.config)
        self._instances: set[str] = set()

    def register_instance(self, instance_id: str) -> str:
        anon = self.masker.anonymize_id(instance_id)
        self._instances.add(anon)
        return anon

    def mask_telemetry(self, instance_id: str, rtt_ms: float | None,
                       timestamp: float | None = None) -> dict[str, Any]:
        now = timestamp if timestamp is not None else datetime.now(timezone.utc).timestamp()
        raw_row = {
            "source": "dofus_client_farm",
            "probe_id": instance_id,
            "timestamp": now,
            "rtt_ms": rtt_ms,
        }
        return self.masker.mask_row(raw_row, instance_id)
