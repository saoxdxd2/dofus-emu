"""Shared, causal feature construction used by BOTH training and inference.

This module is the single source of truth for feature engineering. Phase 4's
real-time module imports these exact functions so that offline and online
preprocessing cannot drift apart.

Critical honesty points carried over from the Phase 1 review:

* Features at index ``i`` use only information available at or before ``i``.
  Missing RTTs are forward-filled from the last valid value, and a companion
  ``is_missing`` indicator records the imputation so the model can distinguish
  "carried forward" from "observed".
* Horizons are expressed in **future observations**, because public measurement
  intervals differ per measurement. We additionally expose the *elapsed seconds*
  to each target so reports never imply a fixed-second forecast that the data
  cannot support.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np

FEATURE_NAMES: tuple[str, ...] = (
    "log_rtt_ms",        # log1p of RTT, forward-filled when missing
    "is_missing",        # 1.0 when this observation had no successful reply
    "log_gap_seconds",   # log1p of seconds since the previous observation
    "log_rtt_delta",     # clipped change in log RTT vs previous valid value
    "loss_rate_8",       # recent failure rate, short window
    "loss_rate_32",      # recent failure rate, long window
    "hour_sin", "hour_cos",
    "weekday_sin", "weekday_cos",
)
SEQ_LEN = 32
HORIZONS: tuple[int, ...] = (1, 3, 6)          # in future OBSERVATIONS
TARGET_BLOCK = 5                                # rtt, rtt_mask, jitter, jitter_mask, loss


@dataclass
class Normaliser:
    """Feature standardisation statistics frozen at training time."""

    mean: list[float]
    std: list[float]
    feature_names: tuple[str, ...] = FEATURE_NAMES

    @classmethod
    def fit(cls, features: np.ndarray) -> "Normaliser":
        flat = features.reshape(-1, features.shape[-1])
        mean = flat.mean(axis=0)
        std = flat.std(axis=0)
        std = np.where(std < 1e-6, 1.0, std)
        return cls(mean=mean.astype(float).tolist(), std=std.astype(float).tolist())

    def transform(self, features: np.ndarray) -> np.ndarray:
        mean = np.asarray(self.mean, dtype=np.float32)
        std = np.asarray(self.std, dtype=np.float32)
        return ((features - mean) / std).astype(np.float32)

    def as_dict(self) -> dict[str, Any]:
        return {"feature_names": list(self.feature_names),
                "mean": self.mean, "std": self.std}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Normaliser":
        return cls(mean=list(payload["mean"]), std=list(payload["std"]),
                   feature_names=tuple(payload.get("feature_names", FEATURE_NAMES)))


def _features_for_series(series: list[dict]) -> tuple[list[list[float]], list[float]]:
    """Build causal features for one (measurement, probe) series.

    Returns ``(feature_rows, gap_seconds_by_step)`` where the gap list records
    the raw elapsed seconds since the previous observation at each step. This
    lets target builders express horizons in real time as well as in steps.
    """
    feature_rows: list[list[float]] = []
    gap_seconds: list[float] = []
    previous_valid: float | None = None
    previous_ts: int | None = None
    loss_history: list[float] = []

    for row in series:
        ts = int(row["timestamp"])
        rtt = row.get("rtt_ms")
        gap = max(1, ts - previous_ts) if previous_ts is not None else 1
        gap_seconds.append(float(gap))
        loss_history.append(1.0 if rtt is None else 0.0)

        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        hour_angle = 2 * math.pi * (dt.hour + dt.minute / 60.0) / 24.0
        day_angle = 2 * math.pi * dt.weekday() / 7.0

        used_rtt = rtt if rtt is not None else previous_valid
        log_rtt = math.log1p(used_rtt) if used_rtt is not None else 0.0
        delta = 0.0
        if rtt is not None and previous_valid is not None:
            delta = max(-8.0, min(8.0, math.log1p(rtt) - math.log1p(previous_valid)))

        feature_rows.append([
            log_rtt,
            1.0 if rtt is None else 0.0,
            math.log1p(min(gap, 86400)),
            delta,
            float(np.mean(loss_history[-8:])),
            float(np.mean(loss_history[-32:])),
            math.sin(hour_angle), math.cos(hour_angle),
            math.sin(day_angle), math.cos(day_angle),
        ])
        if rtt is not None:
            previous_valid = rtt
        previous_ts = ts

    return feature_rows, gap_seconds


def build_series_features(rows: list[dict]) -> dict[tuple, dict[str, Any]]:
    """Group rows into series and build causal features for each.

    Series identity is ``(source, measurement_id, probe_id)`` — the previous
    version keyed on ``(measurement_id, probe_id)`` and could merge probes from
    different sources silently.
    """
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        source = str(row.get("source", "unknown"))
        mid_raw = row.get("measurement_id", 0)
        pid_raw = row.get("probe_id", 0)
        try:
            mid = int(mid_raw)
        except (ValueError, TypeError):
            mid = str(mid_raw)
        try:
            pid = int(pid_raw)
        except (ValueError, TypeError):
            pid = str(pid_raw)
        key = (source, mid, pid)
        groups.setdefault(key, []).append(row)

    series_map: dict[tuple, dict[str, Any]] = {}
    for key, series in groups.items():
        series.sort(key=lambda r: r["timestamp"])
        features, gaps = _features_for_series(series)
        series_map[key] = {
            "series_id": f"{key[0]}:{key[1]}:{key[2]}",
            "rows": series,
            "features": np.asarray(features, dtype=np.float32),
            "gap_seconds": np.asarray(gaps, dtype=np.float32),
        }
    return series_map


def _log1p_or_none(value: float | None) -> float:
    return math.log1p(value) if value is not None else 0.0


def build_targets(series: list[dict], end: int, horizons: tuple[int, ...] = HORIZONS) -> list[float]:
    """Targets for the window ending at index ``end``.

    Per horizon we emit five numbers:
        log_rtt, rtt_mask, log_jitter, jitter_mask, loss
    ``rtt_mask`` is 1 when the future observation actually had a reply, so
    RTT/jitter losses are masked instead of being trained against imputed
    zeros. ``loss`` is always defined: 1 means "no successful reply".
    """
    targets: list[float] = []
    prior = series[end]
    prior_rtt = prior.get("rtt_ms")
    for horizon in horizons:
        future = series[end + horizon]
        rtt = future.get("rtt_ms")
        jitter = abs(rtt - prior_rtt) if (rtt is not None and prior_rtt is not None) else None
        targets.extend([
            _log1p_or_none(rtt),
            1.0 if rtt is not None else 0.0,
            _log1p_or_none(jitter),
            1.0 if jitter is not None else 0.0,
            1.0 if rtt is None else 0.0,
        ])
    return targets


def build_examples(series_map: dict[tuple, dict[str, Any]], *, seq_len: int = SEQ_LEN,
                   horizons: tuple[int, ...] = HORIZONS) -> list[dict[str, Any]]:
    """Turn series into supervised windows with time-aware metadata."""
    max_horizon = max(horizons)
    examples: list[dict[str, Any]] = []
    for key, payload in series_map.items():
        rows = payload["rows"]
        features = payload["features"]
        gaps = payload["gap_seconds"]
        if len(rows) < seq_len + max_horizon:
            continue
        for end in range(seq_len - 1, len(rows) - max_horizon):
            start = end - seq_len + 1
            # Elapsed seconds from the anchor observation to each horizon target,
            # so reports can quote real time as well as step counts.
            horizon_seconds = [float(gaps[end + 1: end + h + 1].sum()) for h in horizons]
            examples.append({
                "x": features[start:end + 1],
                "targets": build_targets(rows, end, horizons),
                "timestamp": int(rows[end]["timestamp"]),
                "series_id": payload["series_id"],
                "source": key[0],
                "measurement_id": key[1],
                "probe_id": key[2],
                "last_rtt": rows[end].get("rtt_ms"),
                "target_timestamps": [int(rows[end + h]["timestamp"]) for h in horizons],
                "horizon_seconds": horizon_seconds,
                "interval_seconds": float(gaps[end]),
            })
    return examples
