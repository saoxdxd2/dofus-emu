"""Metrics and artifact writing for honest Phase 2 benchmarks.

Everything reported here is computed on a **held-out** split and is written to
disk with the split definition, so a number in a README can always be traced to
the exact rows, model version, and code that produced it.

Metrics are deliberately separated by concern:

* **Accuracy** — RTT/jitter error in log space, plus loss probability scores.
* **Calibration** — Brier score and expected calibration error (ECE), because a
  "70% chance of degradation" claim is only useful if it is true 70% of the time.
* **Detection** — precision/recall/F1 and false-alarm rate at an explicit
  threshold, with the threshold recorded rather than tuned on the test set.
* **Cost** — parameters, model size on disk, CPU inference latency, RAM.
"""

from __future__ import annotations

import json
import math
import numbers
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .features import HORIZONS


def masked_mae(pred: np.ndarray, true: np.ndarray, mask: np.ndarray) -> float | None:
    valid = mask > 0.5
    if not np.any(valid):
        return None
    return float(np.mean(np.abs(pred[valid] - true[valid])))


def masked_rmse(pred: np.ndarray, true: np.ndarray, mask: np.ndarray) -> float | None:
    valid = mask > 0.5
    if not np.any(valid):
        return None
    return float(np.sqrt(np.mean((pred[valid] - true[valid]) ** 2)))


def masked_bias(pred: np.ndarray, true: np.ndarray, mask: np.ndarray) -> float | None:
    valid = mask > 0.5
    if not np.any(valid):
        return None
    return float(np.mean(pred[valid] - true[valid]))


def brier_score(prob: np.ndarray, truth: np.ndarray) -> float | None:
    if prob.size == 0:
        return None
    return float(np.mean((prob - truth) ** 2))


def expected_calibration_error(prob: np.ndarray, truth: np.ndarray, bins: int = 10) -> float | None:
    """Standard ECE: weighted gap between predicted confidence and observed rate."""
    if prob.size == 0:
        return None
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = prob.size
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        if hi == 1.0:
            in_bin = (prob >= lo) & (prob <= hi)
        else:
            in_bin = (prob >= lo) & (prob < hi)
        count = int(in_bin.sum())
        if count == 0:
            continue
        confidence = float(prob[in_bin].mean())
        accuracy = float(truth[in_bin].mean())
        ece += (count / total) * abs(confidence - accuracy)
    return float(ece)


def detection_metrics(prob: np.ndarray, truth: np.ndarray,
                      threshold: float = 0.5) -> dict[str, Any]:
    """Precision/recall/F1 and false-alarm rate at a fixed, pre-declared threshold."""
    predicted = prob >= threshold
    actual = truth >= 0.5
    tp = int(np.sum(predicted & actual))
    fp = int(np.sum(predicted & ~actual))
    tn = int(np.sum(~predicted & ~actual))
    fn = int(np.sum(~predicted & actual))
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None and (precision + recall) else None)
    false_alarm_rate = fp / (fp + tn) if (fp + tn) else None
    return {
        "threshold": threshold,
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_alarm_rate": false_alarm_rate,
        "base_rate": float(np.mean(actual)) if actual.size else None,
    }


@dataclass
class HeadMetrics:
    per_horizon: dict[str, dict[str, Any]] = field(default_factory=dict)
    overall: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"per_horizon": self.per_horizon, "overall": self.overall}


def compute_head_metrics(rtt_pred: np.ndarray, rtt_true: np.ndarray, rtt_mask: np.ndarray,
                         jitter_pred: np.ndarray, jitter_true: np.ndarray,
                         jitter_mask: np.ndarray, loss_prob: np.ndarray, loss_true: np.ndarray,
                         degradation_prob: np.ndarray, degradation_true: np.ndarray,
                         anomaly_pred: np.ndarray | None = None,
                         anomaly_true: np.ndarray | None = None,
                         horizons: tuple[int, ...] = HORIZONS,
                         threshold: float = 0.5) -> HeadMetrics:
    """Metrics where axis 1 indexes horizons."""
    metrics = HeadMetrics()
    for h_index, horizon in enumerate(horizons):
        key = str(horizon)
        metrics.per_horizon[key] = {
            "rtt_log_mae": masked_mae(rtt_pred[:, h_index], rtt_true[:, h_index], rtt_mask[:, h_index]),
            "rtt_log_rmse": masked_rmse(rtt_pred[:, h_index], rtt_true[:, h_index], rtt_mask[:, h_index]),
            "rtt_log_bias": masked_bias(rtt_pred[:, h_index], rtt_true[:, h_index], rtt_mask[:, h_index]),
            "jitter_log_mae": masked_mae(jitter_pred[:, h_index], jitter_true[:, h_index],
                                         jitter_mask[:, h_index]),
            "loss_brier": brier_score(loss_prob[:, h_index], loss_true[:, h_index]),
            "loss_ece": expected_calibration_error(loss_prob[:, h_index], loss_true[:, h_index]),
            "loss_detection": detection_metrics(loss_prob[:, h_index], loss_true[:, h_index], threshold),
            "degradation_brier": brier_score(degradation_prob[:, h_index], degradation_true[:, h_index]),
            "degradation_ece": expected_calibration_error(degradation_prob[:, h_index],
                                                          degradation_true[:, h_index]),
            "degradation_detection": detection_metrics(degradation_prob[:, h_index],
                                                       degradation_true[:, h_index], threshold),
            "n_rtt_targets": int(np.sum(rtt_mask[:, h_index] > 0.5)),
            "n_loss_targets": int(loss_true[:, h_index].size),
        }
    if anomaly_pred is not None and anomaly_true is not None and anomaly_pred.size:
        metrics.overall["anomaly_mse"] = float(np.mean((anomaly_pred - anomaly_true) ** 2))
        metrics.overall["anomaly_pearson"] = _safe_corr(anomaly_pred, anomaly_true)
    return metrics


def _safe_corr(a: np.ndarray, b: np.ndarray) -> float | None:
    if a.size < 2 or float(np.std(a)) < 1e-9 or float(np.std(b)) < 1e-9:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def measure_cost(model, example_input, *, warmup: int = 5, runs: int = 30) -> dict[str, Any]:
    """CPU latency and memory footprint. Uses torch only if available."""
    try:
        import torch
    except ImportError:
        return {"error": "torch unavailable; cost measurement skipped"}

    parameters = sum(p.numel() for p in model.parameters() if p.requires_grad)
    size_bytes = sum(p.numel() * p.element_size() for p in model.state_dict().values())
    model.eval()
    timings: list[float] = []
    with torch.no_grad():
        for _ in range(warmup):
            model(example_input)
        for _ in range(runs):
            start = time.perf_counter()
            model(example_input)
            timings.append((time.perf_counter() - start) * 1000.0)
    timings.sort()
    return {
        "parameters": int(parameters),
        "state_dict_bytes": int(size_bytes),
        "state_dict_mb": round(size_bytes / (1024 * 1024), 3),
        "latency_ms_median": round(float(np.median(timings)), 3),
        "latency_ms_p95": round(float(np.quantile(timings, 0.95)), 3),
        "latency_ms_mean": round(float(np.mean(timings)), 3),
        "batch_size": int(example_input.shape[0]),
        "sequence_length": int(example_input.shape[1]),
        "device": "cpu",
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, numbers.Number):
        return float(value)
    raise TypeError(f"Not JSON serialisable: {type(value)!r}")


def comparison_table(baseline_metrics: dict[str, HeadMetrics],
                     model_metrics: HeadMetrics) -> dict[str, Any]:
    """Model-vs-baseline deltas, per horizon, on the metric that matters."""
    rows: dict[str, Any] = {}
    for horizon, model_block in model_metrics.per_horizon.items():
        row: dict[str, Any] = {"model": model_block}
        model_mae = model_block.get("rtt_log_mae")
        for name, metrics in baseline_metrics.items():
            block = metrics.per_horizon.get(horizon, {})
            base_mae = block.get("rtt_log_mae")
            entry = {
                "rtt_log_mae": base_mae,
                "loss_brier": block.get("loss_brier"),
                "loss_detection_f1": (block.get("loss_detection") or {}).get("f1"),
            }
            if base_mae is not None and model_mae is not None:
                entry["mae_improvement_pct"] = round(100.0 * (base_mae - model_mae) / base_mae, 2)
                entry["beaten_by_model"] = bool(model_mae < base_mae)
            row[name] = entry
        rows[horizon] = row
    return rows
