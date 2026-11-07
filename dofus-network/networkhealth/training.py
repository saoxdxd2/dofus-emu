"""Phase 1/2 training pipeline: collect -> clean -> split -> baselines -> train.

The order matters and is enforced by the CLI:

1. **Collect** — reproducible downloader, cached to disk, provenance recorded.
2. **Clean** — remove malformed/duplicate rows, keep genuine failures.
3. **Split** — leakage-resistant, *before* fitting any normaliser or model.
4. **Baselines** — computed on the same split, so comparison is fair.
5. **Train** — normalisation statistics fitted on TRAIN ONLY and frozen into the
   checkpoint; validation is touched for early stopping only.
6. **Evaluate** — metrics, calibration, detection, and CPU cost, written as
   artifacts with the split definition and provenance attached.

The previous script fitted its scaler on the full dataset and reported metrics
from a single time-sorted split with overlapping windows. Both are corrected here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import baselines as baseline_lib
from .evaluation import (HeadMetrics, comparison_table, compute_head_metrics,
                         measure_cost, write_json)
from .features import (FEATURE_NAMES, HORIZONS, Normaliser, TARGET_BLOCK)
from .provenance import hash_file
from .splits import SplitConfig, build_split, describe_split

# Not a default: every writer here takes an explicit `out_dir` from the caller, and the
# directory this names does not exist on this machine (KNOWN_GAPS G5 — the forecaster has
# no checkpoint to load). Kept as the historical location of the one full run.
ARTIFACT_DIR = Path("networkhealth_artifacts")


@dataclass
class TrainConfig:
    epochs: int = 40
    batch_size: int = 128
    learning_rate: float = 2e-3
    weight_decay: float = 1e-4
    patience: int = 8
    hidden: int = 64
    levels: int = 4
    dropout: float = 0.1
    seed: int = 42
    use_identity_mask: bool = True
    split: SplitConfig = field(default_factory=SplitConfig)

    def as_dict(self) -> dict[str, Any]:
        payload = {k: v for k, v in self.__dict__.items() if k != "split"}
        payload["split"] = self.split.as_dict()
        return payload


def _stack(examples: list[dict[str, Any]], index: list[int]) -> tuple[np.ndarray, np.ndarray]:
    x = np.stack([examples[i]["x"] for i in index]).astype(np.float32)
    y = np.stack([examples[i]["targets"] for i in index]).astype(np.float32)
    return x, y


def _unpack_targets(y: np.ndarray, n_horizons: int) -> dict[str, np.ndarray]:
    block = y.reshape(y.shape[0], n_horizons, TARGET_BLOCK)
    return {
        "rtt_log": block[:, :, 0],
        "rtt_mask": block[:, :, 1],
        "jitter_log": block[:, :, 2],
        "jitter_mask": block[:, :, 3],
        "loss": block[:, :, 4],
    }


def _degradation_target(y: np.ndarray, n_horizons: int) -> np.ndarray:
    t = _unpack_targets(y, n_horizons)
    lost = t["loss"] > 0.5
    mask = t["rtt_mask"] > 0.5
    denom = np.maximum(mask.sum(axis=1, keepdims=True), 1.0)
    reference = (t["rtt_log"] * mask).sum(axis=1, keepdims=True) / denom
    elevated = mask & ((t["rtt_log"] - reference) > 0.35)
    return (lost | elevated).astype(np.float32)


def train_model(examples: list[dict[str, Any]], series_map: dict[tuple, dict[str, Any]],
                config: TrainConfig, out_dir: Path) -> dict[str, Any]:
    try:
        import torch
        from torch.utils.data import DataLoader, TensorDataset
    except ImportError as exc:
        raise RuntimeError(
            "PyTorch is required for training. On this machine PyTorch may not "
            "have wheels for Python 3.15; use Google Colab or a Python 3.11-3.13 venv."
        ) from exc

    from .model import ModelConfig, NetworkHealthTCN

    torch.manual_seed(config.seed)
    np.random.seed(config.seed)

    split = build_split(examples, config.split)
    if not split.train_idx or not split.valid_idx:
        raise RuntimeError(
            f"Split produced an empty side (train={len(split.train_idx)}, "
            f"valid={len(split.valid_idx)}). Loosen the split or collect more data.")

    # Human-readable summary without multi-thousand-element index lists.
    split_summary = {k: v for k, v in split.summary.items()
                     if k not in ("train_index", "valid_index")}
    split_summary.update(describe_split(examples, split))

    x_train, y_train = _stack(examples, split.train_idx)
    x_valid, y_valid = _stack(examples, split.valid_idx)

    # Normalisation fitted on TRAIN ONLY, then frozen into the checkpoint so the
    # real-time module cannot accidentally refit on live data.
    normaliser = Normaliser.fit(x_train)
    x_train = normaliser.transform(x_train)
    x_valid = normaliser.transform(x_valid)

    n_horizons = len(HORIZONS)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Baselines on the SAME validation rows, in the same log space.
    baseline_forecasts = baseline_lib.compute_baselines(
        series_map, split.valid_idx, examples, horizons=HORIZONS)
    valid_targets = _unpack_targets(y_valid, n_horizons)
    horizon_seconds: dict[str, list[float]] = {str(h): [] for h in HORIZONS}
    for i, example_index in enumerate(split.valid_idx):
        example = examples[example_index]
        for h_index, horizon in enumerate(HORIZONS):
            seconds = example.get("horizon_seconds", [])
            if h_index < len(seconds):
                horizon_seconds[str(horizon)].append(float(seconds[h_index]))

    baseline_metrics: dict[str, HeadMetrics] = {}
    for name, forecasts in baseline_forecasts.items():
        metrics = HeadMetrics()
        for h_index, horizon in enumerate(HORIZONS):
            key = str(horizon)
            rtt_pred = np.asarray(forecasts.rtt_log.get(key, []), dtype=np.float32)
            if rtt_pred.size != valid_targets["rtt_log"].shape[0]:
                continue
            loss_prob = np.asarray(forecasts.loss_prob.get(key, []), dtype=np.float32)
            # Baselines have no degradation head; derive one from loss + elevated RTT
            # so the comparison is honest rather than flattering the model.
            degradation_true = _degradation_target(y_valid, n_horizons)[:, h_index]
            degradation_prob = np.clip(loss_prob, 0.0, 1.0)
            metrics.per_horizon[key] = {
                "rtt_log_mae": _mae(rtt_pred, valid_targets["rtt_log"][:, h_index],
                                    valid_targets["rtt_mask"][:, h_index]),
                "rtt_log_rmse": _rmse(rtt_pred, valid_targets["rtt_log"][:, h_index],
                                      valid_targets["rtt_mask"][:, h_index]),
                "rtt_log_bias": _bias(rtt_pred, valid_targets["rtt_log"][:, h_index],
                                      valid_targets["rtt_mask"][:, h_index]),
                "jitter_log_mae": _mae(np.asarray(forecasts.jitter_log.get(key, []), dtype=np.float32),
                                       valid_targets["jitter_log"][:, h_index],
                                       valid_targets["jitter_mask"][:, h_index]),
                "loss_brier": _brier(loss_prob, valid_targets["loss"][:, h_index]),
                "loss_detection": _detection(loss_prob, valid_targets["loss"][:, h_index]),
                "degradation_brier": _brier(degradation_prob, degradation_true),
                "degradation_detection": _detection(degradation_prob, degradation_true),
                "n_rtt_targets": int(np.sum(valid_targets["rtt_mask"][:, h_index] > 0.5)),
                "n_loss_targets": int(valid_targets["loss"][:, h_index].size),
                "horizon_seconds_median": (float(np.median(horizon_seconds[key]))
                                           if horizon_seconds[key] else None),
            }
        baseline_metrics[name] = metrics

    train_loader = DataLoader(
        TensorDataset(torch.from_numpy(x_train), torch.from_numpy(y_train)),
        batch_size=config.batch_size, shuffle=True, drop_last=False)
    valid_x = torch.from_numpy(x_valid).to(device)
    valid_y = torch.from_numpy(y_valid).to(device)

    model_config = ModelConfig(hidden=config.hidden, levels=config.levels,
                               dropout=config.dropout, horizons=HORIZONS,
                               use_identity_mask=config.use_identity_mask)
    model = NetworkHealthTCN(model_config).to(device)
    optimiser = torch.optim.AdamW(model.parameters(), lr=config.learning_rate,
                                  weight_decay=config.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimiser, T_max=max(1, config.epochs))

    from .model import multi_task_loss

    best_state: dict[str, Any] | None = None
    best_score = float("inf")
    history: list[dict[str, Any]] = []
    stale = 0
    for epoch in range(1, config.epochs + 1):
        model.train()
        epoch_loss = 0.0
        batches = 0
        for xb, yb in train_loader:
            xb = xb.to(device)
            yb = yb.to(device)
            optimiser.zero_grad(set_to_none=True)
            outputs = model(xb)
            loss, _ = multi_task_loss(outputs, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimiser.step()
            epoch_loss += float(loss.detach())
            batches += 1
        scheduler.step()

        model.eval()
        with torch.no_grad():
            valid_outputs = model(valid_x)
            valid_loss, _ = multi_task_loss(valid_outputs, valid_y)
        valid_loss = float(valid_loss)
        history.append({"epoch": epoch, "train_loss": epoch_loss / max(1, batches),
                        "valid_loss": valid_loss})
        if valid_loss < best_score - 1e-5:
            best_score = valid_loss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            stale = 0
        else:
            stale += 1
            if stale >= config.patience:
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()

    with torch.no_grad():
        outputs = model(valid_x)
    rtt_pred = outputs["rtt_log"].cpu().numpy()
    jitter_pred = outputs["jitter_log"].cpu().numpy()
    loss_prob = torch.sigmoid(outputs["loss_logit"]).cpu().numpy()
    degradation_prob = torch.sigmoid(outputs["degradation_logit"]).cpu().numpy()
    anomaly_pred = outputs["anomaly_score"].cpu().numpy()

    model_metrics = compute_head_metrics(
        rtt_pred, valid_targets["rtt_log"], valid_targets["rtt_mask"],
        jitter_pred, valid_targets["jitter_log"], valid_targets["jitter_mask"],
        loss_prob, valid_targets["loss"],
        degradation_prob, _degradation_target(y_valid, n_horizons),
        anomaly_pred=anomaly_pred, anomaly_true=None,
        horizons=HORIZONS)
    for key, seconds in horizon_seconds.items():
        if seconds:
            model_metrics.per_horizon[key]["horizon_seconds_median"] = float(np.median(seconds))

    example_input = torch.from_numpy(x_valid[:1]).to("cpu")
    cost = measure_cost(model.cpu(), example_input)
    cost["parameters"] = model.parameter_count()

    out_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = out_dir / "model.pt"
    torch.save({
        "state_dict": model.state_dict(),
        "model_config": model_config.as_dict(),
        "feature_names": list(FEATURE_NAMES),
        "horizons": list(HORIZONS),
        "target_block": TARGET_BLOCK,
        "normaliser": normaliser.as_dict(),
        "train_config": config.as_dict(),
        "split_summary": split_summary,
        "best_valid_loss": best_score,
        "epochs_run": len(history),
    }, checkpoint_path)

    comparison = comparison_table(baseline_metrics, model_metrics)
    artifacts = {
        "split_summary": split_summary,
        "training_history": history,
        "baselines": {name: m.as_dict() for name, m in baseline_metrics.items()},
        "model": model_metrics.as_dict(),
        "comparison": comparison,
        "cost": cost,
        "checkpoint": str(checkpoint_path),
        "checkpoint_sha256": hash_file(checkpoint_path),
        "environment": {
            "device": str(device),
            "cuda_available": bool(torch.cuda.is_available()),
            "torch_version": torch.__version__,
        },
        "notes": [
            "Metrics computed on a leakage-resistant held-out split; see split_summary.",
            "Baselines use the same validation rows and the same log1p RTT space.",
            "Anomaly head is a suspicion score, not an attack classification.",
            "Public RIPE Atlas ping data cannot characterise the game client; "
            "local authorized telemetry is required for that (Phase 5).",
        ],
    }
    write_json(out_dir / "evaluation.json", artifacts)
    _write_report(out_dir / "REPORT.md", artifacts)
    return artifacts


def _mae(pred: np.ndarray, true: np.ndarray, mask: np.ndarray) -> float | None:
    valid = mask > 0.5
    if not np.any(valid):
        return None
    return float(np.mean(np.abs(pred[valid] - true[valid])))


def _rmse(pred: np.ndarray, true: np.ndarray, mask: np.ndarray) -> float | None:
    valid = mask > 0.5
    if not np.any(valid):
        return None
    return float(np.sqrt(np.mean((pred[valid] - true[valid]) ** 2)))


def _bias(pred: np.ndarray, true: np.ndarray, mask: np.ndarray) -> float | None:
    valid = mask > 0.5
    if not np.any(valid):
        return None
    return float(np.mean(pred[valid] - true[valid]))


def _brier(prob: np.ndarray, truth: np.ndarray) -> float | None:
    if prob.size == 0:
        return None
    return float(np.mean((prob - truth) ** 2))


def _detection(prob: np.ndarray, truth: np.ndarray, threshold: float = 0.5) -> dict[str, Any]:
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
    return {
        "threshold": threshold,
        "true_positive": tp, "false_positive": fp,
        "true_negative": tn, "false_negative": fn,
        "precision": precision, "recall": recall, "f1": f1,
        "false_alarm_rate": (fp / (fp + tn)) if (fp + tn) else None,
        "base_rate": float(np.mean(actual)) if actual.size else None,
    }


def _write_report(path: Path, artifacts: dict[str, Any]) -> None:
    lines = ["# NetworkHealth Phase 1/2 evaluation", ""]
    split = artifacts["split_summary"]
    lines += [
        "## Split",
        f"- train examples: {split.get('train_examples')}",
        f"- valid examples: {split.get('valid_examples')}",
        f"- shared series between splits: {split.get('shared_series')} "
        "(0 means no probe/measurement is shared)",
        f"- mode: {split.get('mode')}",
        "",
        "## Model vs baselines (RTT log MAE, lower is better)",
        "",
        "| horizon | model | persistence | moving_average | ewma | seasonal_naive |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for horizon, row in artifacts["comparison"].items():
        model_mae = row["model"].get("rtt_log_mae")
        cells = []
        for name in ("persistence", "moving_average", "ewma", "seasonal_naive"):
            value = row.get(name, {}).get("rtt_log_mae")
            improvement = row.get(name, {}).get("mae_improvement_pct")
            if value is None:
                cells.append("n/a")
            elif improvement is None:
                cells.append(f"{value:.4f}")
            else:
                cells.append(f"{value:.4f} ({improvement:+.2f}%)")
        lines.append(f"| {horizon} | {model_mae:.4f} | " + " | ".join(cells) + " |")
    cost = artifacts["cost"]
    lines += [
        "",
        "## Cost (CPU)",
        f"- parameters: {cost.get('parameters'):,}",
        f"- state dict size: {cost.get('state_dict_mb')} MB",
        f"- median latency: {cost.get('latency_ms_median')} ms "
        f"(p95 {cost.get('latency_ms_p95')} ms)",
        "",
        "## Caveats",
    ]
    lines += [f"- {note}" for note in artifacts["notes"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
