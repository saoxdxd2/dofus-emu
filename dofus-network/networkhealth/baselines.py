"""Simple, honest baselines the neural model must beat before adoption.

Phase 2's gate is explicit: a more complex model is only adopted if it improves
relevant metrics without unacceptable latency or resource use. These baselines
are computed from the *same* examples the model sees, so the comparison is
apples-to-apples:

* ``persistence``     — last observation carried forward (the strongest naive RTT
                        baseline for bursty telemetry).
* ``moving_average``  — mean of the trailing window.
* ``ewma``            — exponentially weighted mean; smooths but stays reactive.
* ``seasonal_naive``  — same clock hour on the previous day, when history allows.
* ``statistical_anomaly`` — rolling z-score / MAD detector for anomaly scoring.
* ``loss_persistence``    — predicts future loss as "same as the recent loss rate".

All baselines operate in the *same log1p RTT space* as the model, so MAE numbers
are directly comparable. Metrics are produced per horizon, with the failure
observations masked exactly as in training, and are written to
``baselines.json`` so the comparison is reproducible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .features import HORIZONS, SEQ_LEN


def _mean(values: list[float]) -> float | None:
    return float(np.mean(values)) if values else None


@dataclass
class BaselineForecasts:
    """Container for per-example, per-horizon baseline predictions."""

    rtt_log: dict[str, list[float]] = field(default_factory=dict)
    rtt_mask: dict[str, list[float]] = field(default_factory=dict)
    jitter_log: dict[str, list[float]] = field(default_factory=dict)
    loss_prob: dict[str, list[float]] = field(default_factory=dict)

    def _ensure(self, key: str) -> None:
        self.rtt_log.setdefault(key, [])
        self.rtt_mask.setdefault(key, [])
        self.jitter_log.setdefault(key, [])
        self.loss_prob.setdefault(key, [])


def _trailing_log_rtts(series: list[dict], end: int, window: int) -> list[float]:
    values = []
    for row in series[max(0, end - window + 1): end + 1]:
        rtt = row.get("rtt_ms")
        if rtt is not None:
            values.append(math.log1p(rtt))
    return values


def _trailing_loss(series: list[dict], end: int, window: int) -> float:
    rows = series[max(0, end - window + 1): end + 1]
    if not rows:
        return 0.0
    return float(np.mean([1.0 if r.get("rtt_ms") is None else 0.0 for r in rows]))


def _seasonal_log_rtt(series: list[dict], end: int, period_seconds: int = 86400) -> float | None:
    """Last observation within one period of the anchor's time-of-day."""
    anchor_ts = series[end]["timestamp"]
    target = anchor_ts - period_seconds
    best, best_delta = None, None
    for row in series[: end + 1]:
        delta = abs(int(row["timestamp"]) - target)
        if best_delta is None or delta < best_delta:
            best, best_delta = row, delta
    if best is None or best_delta is None or best_delta > period_seconds / 4:
        return None
    rtt = best.get("rtt_ms")
    return math.log1p(rtt) if rtt is not None else None


def compute_baselines(series_map: dict[tuple, dict[str, Any]], split_index: list[int],
                      examples: list[dict[str, Any]], *,
                      horizons: tuple[int, ...] = HORIZONS) -> dict[str, BaselineForecasts]:
    """Compute baseline forecasts for the examples selected by ``split_index``."""
    baselines: dict[str, BaselineForecasts] = {
        "persistence": BaselineForecasts(),
        "moving_average": BaselineForecasts(),
        "ewma": BaselineForecasts(),
        "seasonal_naive": BaselineForecasts(),
        "loss_persistence": BaselineForecasts(),
    }
    for example_index in split_index:
        example = examples[example_index]
        # Recover the series payload from the map using the stored identity.
        series_key = (example["source"], example["measurement_id"], example["probe_id"])
        payload = series_map.get(series_key)
        if payload is None:
            continue
        rows = payload["rows"]
        anchor_ts = example["timestamp"]
        end = next((i for i in range(len(rows)) if int(rows[i]["timestamp"]) == anchor_ts), None)
        if end is None or end < SEQ_LEN - 1:
            continue

        trailing = _trailing_log_rtts(rows, end, SEQ_LEN)
        last_valid = next((math.log1p(r["rtt_ms"]) for r in reversed(rows[: end + 1])
                           if r.get("rtt_ms") is not None), 0.0)
        moving = _mean(trailing) if trailing else last_valid
        ewma = last_valid
        for value in trailing[:-1]:
            ewma = 0.3 * value + 0.7 * ewma
        seasonal = _seasonal_log_rtt(rows, end)
        recent_loss = _trailing_loss(rows, end, 32)

        for horizon in horizons:
            if end + horizon >= len(rows):
                continue
            future = rows[end + horizon]
            mask = 0.0 if future.get("rtt_ms") is None else 1.0
            prior_rtt = rows[end].get("rtt_ms")
            jitter = (math.log1p(abs(future["rtt_ms"] - prior_rtt))
                      if future.get("rtt_ms") is not None and prior_rtt is not None else 0.0)
            hkey = str(horizon)
            for name in baselines:
                baselines[name]._ensure(hkey)
            baselines["persistence"].rtt_log[hkey].append(last_valid)
            baselines["persistence"].rtt_mask[hkey].append(mask)
            baselines["persistence"].jitter_log[hkey].append(jitter)
            baselines["persistence"].loss_prob[hkey].append(recent_loss)
            baselines["moving_average"].rtt_log[hkey].append(moving)
            baselines["moving_average"].rtt_mask[hkey].append(mask)
            baselines["moving_average"].jitter_log[hkey].append(jitter)
            baselines["moving_average"].loss_prob[hkey].append(recent_loss)
            baselines["ewma"].rtt_log[hkey].append(ewma)
            baselines["ewma"].rtt_mask[hkey].append(mask)
            baselines["ewma"].jitter_log[hkey].append(jitter)
            baselines["ewma"].loss_prob[hkey].append(recent_loss)
            value = seasonal if seasonal is not None else last_valid
            baselines["seasonal_naive"].rtt_log[hkey].append(value)
            baselines["seasonal_naive"].rtt_mask[hkey].append(mask)
            baselines["seasonal_naive"].jitter_log[hkey].append(jitter)
            baselines["seasonal_naive"].loss_prob[hkey].append(recent_loss)
            baselines["loss_persistence"].rtt_log[hkey].append(last_valid)
            baselines["loss_persistence"].rtt_mask[hkey].append(mask)
            baselines["loss_persistence"].jitter_log[hkey].append(jitter)
            baselines["loss_persistence"].loss_prob[hkey].append(recent_loss)
    return baselines


def rolling_zscore_anomaly(series: list[dict], window: int = 32,
                           threshold: float = 3.0) -> list[float]:
    """Robust rolling anomaly score: |z| using median/MAD of the trailing window.

    Returns a score per observation (0.0 when insufficient history). This is the
    "simple statistical anomaly detection" comparison and also the deterministic
    fallback used in Phase 6 — a score is only ever a *flag*, never an attack
    verdict.
    """
    scores: list[float] = []
    history: list[float] = []
    for row in series:
        rtt = row.get("rtt_ms")
        if rtt is None or not history:
            scores.append(0.0)
        else:
            med = float(np.median(history))
            mad = float(np.median([abs(v - med) for v in history]))
            scale = mad * 1.4826 if mad > 1e-9 else float(np.std(history)) or 1.0
            scores.append(abs(math.log1p(rtt) - med) / scale)
        if rtt is not None:
            history.append(math.log1p(rtt))
            if len(history) > window:
                history.pop(0)
    return scores


def _safe_mae(pred: list[float], true: list[float], mask: list[float]) -> float | None:
    pairs = [(p, t) for p, t, m in zip(pred, true, mask) if m > 0.5]
    if not pairs:
        return None
    return float(np.mean([abs(p - t) for p, t in pairs]))


def _safe_rmse(pred: list[float], true: list[float], mask: list[float]) -> float | None:
    pairs = [(p, t) for p, t, m in zip(pred, true, mask) if m > 0.5]
    if not pairs:
        return None
    return float(np.sqrt(np.mean([(p - t) ** 2 for p, t in pairs])))


def _safe_bias(pred: list[float], true: list[float], mask: list[float]) -> float | None:
    pairs = [(p, t) for p, t, m in zip(pred, true, mask) if m > 0.5]
    if not pairs:
        return None
    return float(np.mean([p - t for p, t in pairs]))


def _safe_accuracy(pred: list[float], true: list[float]) -> float | None:
    if not true:
        return None
    return float(np.mean([(1.0 if p >= 0.5 else 0.0) == t for p, t in zip(pred, true)]))


@dataclass
class MetricBundle:
    """Per-horizon metric container shared by baselines and the neural model."""

    per_horizon: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"horizons": self.per_horizon}


def compute_metrics(rtt_log: dict[str, list[float]], rtt_true: dict[str, list[float]],
                    rtt_mask: dict[str, list[float]], jitter_log: dict[str, list[float]],
                    loss_prob: dict[str, list[float]], loss_true: dict[str, list[float]],
                    *, horizon_seconds: dict[str, list[float]] | None = None,
                    horizons: tuple[int, ...] = HORIZONS) -> MetricBundle:
    bundle = MetricBundle()
    for horizon in horizons:
        hkey = str(horizon)
        rtt_pred = rtt_log.get(hkey, [])
        true = rtt_true.get(hkey, [])
        mask = rtt_mask.get(hkey, [])
        jitter = jitter_log.get(hkey, [])
        prob = loss_prob.get(hkey, [])
        truth = loss_true.get(hkey, [])
        seconds = horizon_seconds.get(hkey, []) if horizon_seconds else []
        rtt_mae = _safe_mae(rtt_pred, true, mask)
        loss_accuracy = _safe_accuracy(prob, truth)
        event_rate = _mean(truth)
        bundle.per_horizon[hkey] = {
            "horizon_observations": horizon,
            "horizon_seconds_median": _mean(seconds) if seconds else None,
            "n_rtt_targets": int(sum(1 for m in mask if m > 0.5)),
            "n_loss_targets": len(truth),
            "rtt_log_mae": rtt_mae,
            "rtt_log_rmse": _safe_rmse(rtt_pred, true, mask),
            "rtt_log_bias": _safe_bias(rtt_pred, true, mask),
            "jitter_log_mae": _safe_mae(jitter, true, mask),
            "loss_accuracy": loss_accuracy,
            "loss_event_rate": event_rate,
        }
    return bundle
