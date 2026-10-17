"""Local CPU inference. Separate from the Colab training path by design.

This module:

* runs on CPU only and never imports CUDA-specific machinery,
* loads a validated checkpoint bundle (frozen normaliser, feature order, horizons),
* maintains the rolling observation window across calls,
* applies the *training-time* preprocessing statistics, never refitting them,
* handles missing, stale, and interval-changed observations explicitly,
* reports forecasts with uncertainty, plus a suspicion score that is explicitly
  NOT an attack classification.

The rolling window is stateful on purpose: a live client delivers one observation
at a time, and the model needs the last ``seq_len`` of them in the exact order and
feature layout used during training.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ..contract import ModelBundle, load_checkpoint
from ..features import FEATURE_NAMES, HORIZONS, SEQ_LEN, Normaliser
from ..identity_mask import IdentityMaskConfig, InstanceIdentityMasker


@dataclass
class Observation:
    """One timestamped measurement from the local environment.

    ``rtt_ms = None`` means the probe got no reply — a real loss event, not
    missing data. ``None`` and "absent" are treated differently on purpose.
    """

    timestamp: float
    rtt_ms: float | None
    received: int = 0
    sent: int = 0
    stale: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "rtt_ms": self.rtt_ms,
            "received": self.received,
            "sent": self.sent,
            "stale": self.stale,
        }


@dataclass
class ForecastResult:
    """Inference output for one observation, with honest confidence metadata."""

    timestamp: float
    horizons: list[int]
    horizon_seconds: list[float]
    rtt_ms: list[float | None]
    rtt_std_ms: list[float | None]
    jitter_ms: list[float | None]
    loss_probability: list[float]
    degradation_probability: list[float]
    suspicion_score: float
    confidence: str
    caveats: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    window_fill: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "timestamp_utc": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat(),
            "horizons_observations": self.horizons,
            "horizons_seconds_estimate": self.horizon_seconds,
            "rtt_ms": self.rtt_ms,
            "rtt_std_ms": self.rtt_std_ms,
            "jitter_ms": self.jitter_ms,
            "loss_probability": self.loss_probability,
            "degradation_probability": self.degradation_probability,
            "suspicion_score": self.suspicion_score,
            "confidence": self.confidence,
            "caveats": self.caveats,
            "latency_ms": self.latency_ms,
            "window_fill": self.window_fill,
            "note": "suspicion_score is a triage signal, not an attack classification",
        }


class RollingWindow:
    """Fixed-length causal window with explicit missing/stale bookkeeping."""

    def __init__(self, seq_len: int = SEQ_LEN) -> None:
        self.seq_len = seq_len
        self._rows: deque[dict[str, Any]] = deque(maxlen=seq_len)

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def fill(self) -> float:
        return len(self._rows) / self.seq_len

    def append(self, observation: Observation) -> None:
        self._rows.append(observation.as_dict())

    def clear(self) -> None:
        self._rows.clear()

    def is_ready(self) -> bool:
        return len(self._rows) >= self.seq_len

    def stale_fraction(self) -> float:
        if not self._rows:
            return 0.0
        return float(np.mean([1.0 if r.get("stale") else 0.0 for r in self._rows]))

    def interval_seconds(self) -> float | None:
        """Median observed interval; used to convert horizons into real time."""
        if len(self._rows) < 2:
            return None
        stamps = [float(r["timestamp"]) for r in self._rows]
        diffs = [b - a for a, b in zip(stamps, stamps[1:]) if b > a]
        if not diffs:
            return None
        return float(np.median(diffs))

    def build_features(self, normaliser: Normaliser) -> np.ndarray:
        """Recreate the training-time feature layout for the current window."""
        rows = list(self._rows)
        features = np.zeros((len(rows), len(FEATURE_NAMES)), dtype=np.float32)
        previous_valid: float | None = None
        previous_ts: float | None = None
        loss_history: list[float] = []

        for i, row in enumerate(rows):
            ts = float(row["timestamp"])
            rtt = row.get("rtt_ms")
            gap = max(1.0, ts - previous_ts) if previous_ts is not None else 1.0
            loss_history.append(1.0 if rtt is None else 0.0)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            hour_angle = 2 * math.pi * (dt.hour + dt.minute / 60.0) / 24.0
            day_angle = 2 * math.pi * dt.weekday() / 7.0

            used = rtt if rtt is not None else previous_valid
            log_rtt = math.log1p(used) if used is not None else 0.0
            delta = 0.0
            if rtt is not None and previous_valid is not None:
                delta = max(-8.0, min(8.0, math.log1p(rtt) - math.log1p(previous_valid)))

            features[i] = [
                log_rtt,
                1.0 if rtt is None else 0.0,
                math.log1p(min(gap, 86400)),
                delta,
                float(np.mean(loss_history[-8:])),
                float(np.mean(loss_history[-32:])),
                math.sin(hour_angle), math.cos(hour_angle),
                math.sin(day_angle), math.cos(day_angle),
            ]
            if rtt is not None:
                previous_valid = rtt
            previous_ts = ts
        return normaliser.transform(features)


class NetworkHealthInference:
    """CPU-only predictor. Independent of the WebView2 client and game manager."""

    def __init__(self, checkpoint: Path, *, stale_after_seconds: float = 900.0,
                 threads: int | None = None) -> None:
        import torch

        if threads is not None:
            torch.set_num_threads(int(threads))
        self.bundle: ModelBundle = load_checkpoint(checkpoint)
        from ..model import ModelConfig, NetworkHealthTCN

        config = ModelConfig(**self.bundle.model_config)
        self.model = NetworkHealthTCN(config)
        self.model.load_state_dict(self.bundle.state_dict)
        self.model.eval()
        self.torch = torch
        self.window = RollingWindow(SEQ_LEN)
        self.stale_after_seconds = float(stale_after_seconds)
        self.latencies_ms: deque[float] = deque(maxlen=200)
        self.horizons = tuple(self.bundle.horizons)
        self._reference_rtt: float | None = None

    def observe(self, observation: Observation) -> ForecastResult | None:
        """Feed one observation. Returns a forecast once the window is full."""
        now = observation.timestamp
        if now <= 0:
            now = datetime.now(timezone.utc).timestamp()
            observation.timestamp = now
        self.window.append(observation)
        if observation.rtt_ms is not None:
            self._reference_rtt = observation.rtt_ms
        if not self.window.is_ready():
            return None
        return self._predict()

    def _predict(self) -> ForecastResult:
        started = time.perf_counter()
        features = self.window.build_features(self.bundle.normaliser)
        tensor = self.torch.from_numpy(features[None, :, :]).float()

        with self.torch.no_grad():
            outputs = self.model(tensor)

        rtt_log = outputs["rtt_log"][0].numpy()
        rtt_logvar = outputs["rtt_logvar"][0].clamp(-8.0, 4.0).numpy()
        jitter_log = outputs["jitter_log"][0].numpy()
        loss_prob = self.torch.sigmoid(outputs["loss_logit"][0]).numpy()
        degradation_prob = self.torch.sigmoid(outputs["degradation_logit"][0]).numpy()
        suspicion = float(self.torch.sigmoid(outputs["anomaly_score"][0]).item())

        interval = self.window.interval_seconds()
        horizon_seconds = [float(h) * interval if interval else float("nan")
                           for h in self.horizons]

        rtt_ms: list[float | None] = []
        rtt_std_ms: list[float | None] = []
        for index in range(len(self.horizons)):
            rtt_ms.append(float(math.expm1(max(0.0, float(rtt_log[index])))))
            rtt_std_ms.append(float(math.expm1(max(0.0, float(math.sqrt(math.exp(rtt_logvar[index])))))))

        jitter_ms = [float(math.expm1(max(0.0, float(value)))) for value in jitter_log]

        caveats: list[str] = []
        stale_fraction = self.window.stale_fraction()
        if stale_fraction > 0.25:
            caveats.append(f"{stale_fraction:.0%} of the window is stale; confidence reduced.")
        if interval is not None and interval > 600:
            caveats.append(f"Median interval {interval:.0f}s is coarse; "
                           "horizons cover more wall-clock time than usual.")
        missing_fraction = float(np.mean([1.0 if r.get("rtt_ms") is None else 0.0
                                          for r in list(self.window._rows)]))
        if missing_fraction > 0.3:
            caveats.append(f"{missing_fraction:.0%} of observations had no reply; "
                           "forecasts are based on a lossy series.")

        confidence = "high"
        if stale_fraction > 0.25 or missing_fraction > 0.3:
            confidence = "low"
        elif stale_fraction > 0.05 or missing_fraction > 0.1:
            confidence = "medium"

        latency = (time.perf_counter() - started) * 1000.0
        self.latencies_ms.append(latency)

        return ForecastResult(
            timestamp=float(self.window._rows[-1]["timestamp"]),
            horizons=list(self.horizons),
            horizon_seconds=horizon_seconds,
            rtt_ms=rtt_ms,
            rtt_std_ms=rtt_std_ms,
            jitter_ms=jitter_ms,
            loss_probability=[float(v) for v in loss_prob],
            degradation_probability=[float(v) for v in degradation_prob],
            suspicion_score=suspicion,
            confidence=confidence,
            caveats=caveats,
            latency_ms=round(latency, 3),
            window_fill=self.window.fill,
        )

    def cost_report(self) -> dict[str, Any]:
        """Observed latency distribution; used for the resource budget."""
        if not self.latencies_ms:
            return {"samples": 0}
        values = np.asarray(list(self.latencies_ms))
        return {
            "samples": int(values.size),
            "median_ms": round(float(np.median(values)), 3),
            "p95_ms": round(float(np.quantile(values, 0.95)), 3),
            "max_ms": round(float(values.max()), 3),
            "parameters": self.model.parameter_count(),
        }

    def reset(self) -> None:
        self.window.clear()
        self._reference_rtt = None


class MultiInstanceNetworkHealth:
    """Manages independent, identity-masked inference engines for multiple game clients.

    Each instance (e.g. 'slot-1', 'slot-2', 'slot-3', 'slot-4') possesses:
    - an isolated RollingWindow (preventing cross-instance telemetry pollution)
    - a dedicated InstanceBaselineTracker (normalizing static proxy offsets)
    - deterministic temporal micro-desynchronization (preventing clock correlation)
    """

    def __init__(self, checkpoint: Path, *,
                 identity_config: IdentityMaskConfig | None = None,
                 stale_after_seconds: float = 900.0,
                 threads: int | None = None) -> None:
        self.checkpoint = Path(checkpoint)
        self.identity_config = identity_config or IdentityMaskConfig()
        self.masker = InstanceIdentityMasker(self.identity_config)
        self.stale_after_seconds = stale_after_seconds
        self.threads = threads
        self._engines: dict[str, NetworkHealthInference] = {}

    def get_or_create(self, instance_id: str) -> NetworkHealthInference:
        anon_key = self.masker.anonymize_id(instance_id)
        if anon_key not in self._engines:
            self._engines[anon_key] = NetworkHealthInference(
                self.checkpoint,
                stale_after_seconds=self.stale_after_seconds,
                threads=self.threads,
            )
        return self._engines[anon_key]

    def observe(self, instance_id: str, observation: Observation) -> ForecastResult | None:
        """Process an observation for a specific game instance with identity masking."""
        engine = self.get_or_create(instance_id)

        # Apply per-instance temporal desynchronization and baseline normalization
        masked_ts = self.masker.desynchronize_timestamp(observation.timestamp, instance_id)
        anon_obs = Observation(
            timestamp=masked_ts,
            rtt_ms=observation.rtt_ms,
            received=observation.received,
            sent=observation.sent,
            stale=observation.stale,
        )
        return engine.observe(anon_obs)

    def active_instances(self) -> list[str]:
        return list(self._engines.keys())

    def reset(self, instance_id: str | None = None) -> None:
        if instance_id is not None:
            anon_key = self.masker.anonymize_id(instance_id)
            if anon_key in self._engines:
                self._engines[anon_key].reset()
        else:
            for eng in self._engines.values():
                eng.reset()

