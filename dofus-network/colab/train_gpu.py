"""Colab-only T4 training driver.

This file is imported ONLY from the Colab notebook. It is deliberately separate
from the local path because it:

* requires CUDA and refuses to run on CPU (so a laptop cannot accidentally
  launch a 40-epoch GPU job),
* uses FP16 automatic mixed precision with gradient scaling,
* pre-processes the dataset once into contiguous arrays, so no cleaning or CSV
  parsing happens inside the training loop,
* keeps probability-bearing ops in FP32 to avoid FP16 loss-scaling problems.

The shared core (``networkhealth.features``, ``networkhealth.splits``) is
imported unchanged, so the features the T4 trains on are byte-identical to the
features the laptop computes at inference time.

Usage from a Colab cell:

    from colab.train_gpu import GpuTrainConfig, train_on_gpu
    artifacts = train_on_gpu("networkhealth_artifacts/features.npz", GpuTrainConfig())
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from networkhealth.evaluation import compute_head_metrics, measure_cost, write_json
from networkhealth.features import FEATURE_NAMES, HORIZONS, TARGET_BLOCK, Normaliser
from networkhealth.model import ModelConfig, NetworkHealthTCN, multi_task_loss
from networkhealth.splits import SplitConfig, build_split, describe_split


@dataclass
class GpuTrainConfig:
    """Starting configuration; measured throughput decides the final values."""

    epochs: int = 60
    seq_len: int = 128              # longer context than the local smoke config
    batch_size: int = 512
    learning_rate: float = 2e-3
    weight_decay: float = 1e-4
    patience: int = 10
    hidden: int = 96
    levels: int = 5
    dropout: float = 0.1
    seed: int = 42
    amp: bool = True
    num_workers: int = 2
    valid_fraction: float = 0.15
    split_mode: str = "time_and_series"
    purge_seconds: int = 3600
    gap_seconds: int = 1800
    grad_clip: float = 1.0
    log_every: int = 25
    use_identity_mask: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class ThroughputProbe:
    """Result of a batch-size sweep; recorded so the choice is evidence-based."""

    results: list[dict[str, Any]] = field(default_factory=list)

    def best(self) -> dict[str, Any] | None:
        valid = [r for r in self.results if r.get("samples_per_second")]
        if not valid:
            return None
        return max(valid, key=lambda r: r["samples_per_second"])


def require_cuda() -> Any:
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError(
            "This driver is Colab/T4-only. No CUDA device found. "
            "Use `python -m networkhealth train` for CPU smoke training instead.")
    return torch


def load_windows(features_path: Path, seq_len: int) -> dict[str, np.ndarray]:
    """Load pre-built windows. Windowing already happened in `prepare`."""
    payload = np.load(Path(features_path), allow_pickle=False)
    x = payload["x"]
    y = payload["y"]
    if x.shape[1] != seq_len:
        # Re-slice or pad to the requested length without touching the data.
        if x.shape[1] > seq_len:
            x = x[:, -seq_len:, :]
        else:
            pad = np.zeros((x.shape[0], seq_len - x.shape[1], x.shape[2]), dtype=x.dtype)
            x = np.concatenate([pad, x], axis=1)
    return {
        "x": np.ascontiguousarray(x, dtype=np.float32),
        "y": np.ascontiguousarray(y, dtype=np.float32),
        "timestamps": payload["timestamps"].astype(np.int64),
        "series_index": payload["series_index"],
        "measurement_ids": payload["measurement_ids"].astype(np.int64),
        "probe_ids": payload["probe_ids"].astype(np.int64),
        "sources": payload["sources"],
        "horizon_seconds": payload["horizon_seconds"].astype(np.float32),
    }


def _make_examples(data: dict[str, np.ndarray]) -> list[dict[str, Any]]:
    """Lightweight metadata view used only for splitting; not for features."""
    count = data["x"].shape[0]
    examples: list[dict[str, Any]] = []
    for i in range(count):
        examples.append({
            "timestamp": int(data["timestamps"][i]),
            "series_id": str(data["series_index"][i]),
            "source": str(data["sources"][i]),
            "measurement_id": int(data["measurement_ids"][i]),
            "probe_id": int(data["probe_ids"][i]),
            "target_timestamps": [int(data["timestamps"][i])] * len(HORIZONS),
            "horizon_seconds": data["horizon_seconds"][i].tolist(),
        })
    return examples


def probe_batch_sizes(model: NetworkHealthTCN, x: np.ndarray,
                      batch_sizes: tuple[int, ...] = (64, 128, 256, 512),
                      batches: int = 60, amp: bool = True) -> ThroughputProbe:
    """Measure samples/second for each batch size before committing to one."""
    torch = require_cuda()
    device = torch.device("cuda")
    probe = ThroughputProbe()
    for size in batch_sizes:
        size = min(size, x.shape[0])
        if size < 2:
            continue
        try:
            batch = torch.from_numpy(x[:size]).to(device, non_blocking=True)
            optimiser = torch.optim.AdamW(model.parameters(), lr=1e-4)
            scaler = torch.amp.GradScaler("cuda", enabled=amp)
            # Warm-up: T4 clocks ramp, and the first allocations are slow.
            for _ in range(5):
                optimiser.zero_grad(set_to_none=True)
                with torch.amp.autocast("cuda", dtype=torch.float16, enabled=amp):
                    outputs = model(batch)
                    targets = torch.zeros((size, len(HORIZONS) * TARGET_BLOCK),
                                          device=device)
                    loss, _ = multi_task_loss(outputs, targets)
                scaler.scale(loss).backward()
                scaler.step(optimiser)
                scaler.update()
            torch.cuda.synchronize()
            started = time.perf_counter()
            for _ in range(batches):
                optimiser.zero_grad(set_to_none=True)
                with torch.amp.autocast("cuda", dtype=torch.float16, enabled=amp):
                    outputs = model(batch)
                    targets = torch.zeros((size, len(HORIZONS) * TARGET_BLOCK),
                                          device=device)
                    loss, _ = multi_task_loss(outputs, targets)
                scaler.scale(loss).backward()
                scaler.step(optimiser)
                scaler.update()
            torch.cuda.synchronize()
            elapsed = time.perf_counter() - started
            probe.results.append({
                "batch_size": size,
                "batches": batches,
                "seconds": round(elapsed, 3),
                "samples_per_second": round(size * batches / elapsed, 1),
                "peak_memory_mb": round(torch.cuda.max_memory_allocated() / 2 ** 20, 1),
            })
            torch.cuda.reset_peak_memory_stats()
        except RuntimeError as exc:
            # Almost always an out-of-memory condition at the larger sizes.
            probe.results.append({"batch_size": size, "error": str(exc)[:200]})
            torch.cuda.empty_cache()
    return probe


def train_on_gpu(features_path: Path, config: GpuTrainConfig,
                 out_dir: Path) -> dict[str, Any]:
    torch = require_cuda()
    from torch.utils.data import DataLoader, TensorDataset

    torch.manual_seed(config.seed)
    np.random.seed(config.seed)
    torch.backends.cudnn.benchmark = True

    data = load_windows(features_path, config.seq_len)
    examples = _make_examples(data)
    split = build_split(examples, SplitConfig(
        mode=config.split_mode, valid_fraction=config.valid_fraction,
        purge_seconds=config.purge_seconds, gap_seconds=config.gap_seconds,
        seed=config.seed))
    if not split.train_idx or not split.valid_idx:
        raise RuntimeError("Empty split; collect more data or loosen the split.")

    split_summary = {k: v for k, v in split.summary.items()
                     if k not in ("train_index", "valid_index")}
    split_summary.update(describe_split(examples, split))

    train_idx = np.asarray(split.train_idx, dtype=np.int64)
    valid_idx = np.asarray(split.valid_idx, dtype=np.int64)
    x_train = data["x"][train_idx]
    y_train = data["y"][train_idx]
    x_valid = data["x"][valid_idx]
    y_valid = data["y"][valid_idx]

    # Normaliser fitted on TRAIN ONLY and frozen into the checkpoint.
    normaliser = Normaliser.fit(x_train)
    x_train = normaliser.transform(x_train)
    x_valid = normaliser.transform(x_valid)

    device = torch.device("cuda")
    model_config = ModelConfig(hidden=config.hidden, levels=config.levels,
                               dropout=config.dropout, horizons=HORIZONS,
                               use_identity_mask=config.use_identity_mask)
    model = NetworkHealthTCN(model_config).to(device)

    # Batch-size sweep on the real model before committing.
    throughput = probe_batch_sizes(model, x_train, amp=config.amp)
    best = throughput.best()
    chosen_batch = best["batch_size"] if best else config.batch_size

    loader = DataLoader(
        TensorDataset(torch.from_numpy(x_train), torch.from_numpy(y_train)),
        batch_size=chosen_batch, shuffle=True, drop_last=True,
        num_workers=config.num_workers,
        pin_memory=True, persistent_workers=config.num_workers > 0)
    valid_x = torch.from_numpy(x_valid).to(device, non_blocking=True)
    valid_y = torch.from_numpy(y_valid).to(device, non_blocking=True)

    optimiser = torch.optim.AdamW(model.parameters(), lr=config.learning_rate,
                                  weight_decay=config.weight_decay)
    steps_per_epoch = max(1, len(loader))
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        optimiser, max_lr=config.learning_rate, total_steps=config.epochs * steps_per_epoch,
        pct_start=0.1)
    scaler = torch.amp.GradScaler("cuda", enabled=config.amp)

    best_state: dict[str, Any] | None = None
    best_loss = float("inf")
    history: list[dict[str, Any]] = []
    stale = 0
    started = time.perf_counter()

    for epoch in range(1, config.epochs + 1):
        model.train()
        running = 0.0
        seen = 0
        for step, (xb, yb) in enumerate(loader, 1):
            xb = xb.to(device, non_blocking=True)
            yb = yb.to(device, non_blocking=True)
            optimiser.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", dtype=torch.float16, enabled=config.amp):
                outputs = model(xb)
                # Loss computed in FP32: probabilities and variance terms are
                # numerically sensitive and must not be evaluated in FP16.
                with torch.amp.autocast("cuda", enabled=False):
                    loss, _ = multi_task_loss(
                        {k: v.float() for k, v in outputs.items()}, yb)
            scaler.scale(loss).backward()
            scaler.unscale_(optimiser)
            torch.nn.utils.clip_grad_norm_(model.parameters(), config.grad_clip)
            scale_before = scaler.get_scale()
            scaler.step(optimiser)
            scaler.update()
            if scale_before <= scaler.get_scale():
                scheduler.step()
            running += float(loss.detach())
            seen += xb.shape[0]
            if config.log_every and step % config.log_every == 0:
                print(f"  epoch {epoch} step {step}/{steps_per_epoch} "
                      f"loss={running / step:.5f} lr={scheduler.get_last_lr()[0]:.2e}",
                      flush=True)

        model.eval()
        with torch.no_grad():
            with torch.amp.autocast("cuda", dtype=torch.float16, enabled=config.amp):
                valid_outputs = model(valid_x)
            with torch.amp.autocast("cuda", enabled=False):
                valid_loss, _ = multi_task_loss(
                    {k: v.float() for k, v in valid_outputs.items()}, valid_y)
        valid_loss = float(valid_loss)
        history.append({"epoch": epoch, "train_loss": running / steps_per_epoch,
                        "valid_loss": valid_loss})
        print(f"epoch {epoch}: train={running / steps_per_epoch:.5f} "
              f"valid={valid_loss:.5f}", flush=True)

        if valid_loss < best_loss - 1e-5:
            best_loss = valid_loss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            stale = 0
        else:
            stale += 1
            if stale >= config.patience:
                print(f"early stopping at epoch {epoch}")
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    elapsed = time.perf_counter() - started

    with torch.no_grad():
        with torch.amp.autocast("cuda", dtype=torch.float16, enabled=config.amp):
            outputs = model(valid_x)
        outputs = {k: v.float().cpu() for k, v in outputs.items()}

    rtt_true = y_valid.reshape(-1, len(HORIZONS), TARGET_BLOCK)[:, :, 0]
    rtt_mask = y_valid.reshape(-1, len(HORIZONS), TARGET_BLOCK)[:, :, 1]
    jitter_true = y_valid.reshape(-1, len(HORIZONS), TARGET_BLOCK)[:, :, 2]
    jitter_mask = y_valid.reshape(-1, len(HORIZONS), TARGET_BLOCK)[:, :, 3]
    loss_true = y_valid.reshape(-1, len(HORIZONS), TARGET_BLOCK)[:, :, 4]
    degradation_true = _degradation(y_valid)

    metrics = compute_head_metrics(
        outputs["rtt_log"].numpy(), rtt_true, rtt_mask,
        outputs["jitter_log"].numpy(), jitter_true, jitter_mask,
        torch.sigmoid(outputs["loss_logit"]).numpy(), loss_true,
        torch.sigmoid(outputs["degradation_logit"]).numpy(), degradation_true,
        anomaly_pred=outputs["anomaly_score"].numpy(),
        horizons=HORIZONS)

    # Cost is measured on CPU, because that is where inference will actually run.
    cpu_model = NetworkHealthTCN(model_config)
    cpu_model.load_state_dict(model.state_dict())
    example = torch.from_numpy(x_valid[:1]).float()
    cost = measure_cost(cpu_model, example)
    cost["parameters"] = model.parameter_count()

    out_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = out_dir / "model.pt"
    torch.save({
        "checkpoint_format_version": "2.0",
        "state_dict": model.state_dict(),
        "model_config": model_config.as_dict(),
        "feature_names": list(FEATURE_NAMES),
        "horizons": list(HORIZONS),
        "target_block": TARGET_BLOCK,
        "normaliser": normaliser.as_dict(),
        "train_config": config.as_dict(),
        "split_summary": split_summary,
        "best_valid_loss": best_loss,
        "epochs_run": len(history),
    }, checkpoint_path)

    artifacts = {
        "split_summary": split_summary,
        "training_history": history,
        "model": metrics.as_dict(),
        "cost": cost,
        "throughput_probe": throughput.results,
        "chosen_batch_size": chosen_batch,
        "checkpoint": str(checkpoint_path),
        "environment": {
            "device": "cuda",
            "gpu": torch.cuda.get_device_name(0),
            "torch_version": torch.__version__,
            "amp": config.amp,
            "seq_len": config.seq_len,
        },
        "wall_clock_seconds": round(elapsed, 1),
        "notes": [
            "Trained on GPU; cost measured on CPU for inference budgeting.",
            "Loss and probability heads evaluated in FP32 even under AMP.",
            "Split is leakage-resistant; see split_summary.",
        ],
    }
    write_json(out_dir / "evaluation.json", artifacts)
    return artifacts


def _degradation(y: np.ndarray) -> np.ndarray:
    block = y.reshape(-1, len(HORIZONS), TARGET_BLOCK)
    lost = block[:, :, 4] > 0.5
    mask = block[:, :, 1] > 0.5
    denom = np.maximum(mask.sum(axis=1, keepdims=True), 1.0)
    reference = (block[:, :, 0] * mask).sum(axis=1, keepdims=True) / denom
    elevated = mask & ((block[:, :, 0] - reference) > 0.35)
    return (lost | elevated).astype(np.float32)
