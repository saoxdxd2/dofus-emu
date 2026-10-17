from __future__ import annotations

import argparse
import json
import math
import random
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

API = "https://atlas.ripe.net/api/v2"
OUT = Path("networkhealth_v1_artifacts")
SEQ_LEN = 32
HORIZONS = (1, 3, 6)  # future observed samples, not fixed seconds
FEATURE_NAMES = [
    "log_rtt_ms", "is_missing", "log_gap_seconds", "log_rtt_delta",
    "loss_rate_8", "loss_rate_32", "hour_sin", "hour_cos",
    "weekday_sin", "weekday_cos",
]
USER_AGENT = "NetworkHealthResearch/1.0 (public RIPE Atlas data; polite client)"


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def get_json(session: requests.Session, url: str, params: dict | None = None,
             retries: int = 5) -> Any:
    last_error = None
    for attempt in range(retries):
        try:
            response = session.get(url, params=params, timeout=(10, 45))
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("Retry-After", "")
                wait = min(60, int(retry_after)) if retry_after.isdigit() else min(30, 2 ** attempt)
                log(f"HTTP {response.status_code}; waiting {wait}s")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = str(exc)
            wait = min(30, 2 ** attempt)
            log(f"Request failed ({exc}); retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"Unable to fetch {url}: {last_error}")


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def discover_measurements(session: requests.Session, limit: int) -> list[int]:
    """Discover public active ping measurements, bounded by limit."""
    url = f"{API}/measurements/"
    params = {
        "type": "ping",
        "is_public": "true",
        "is_active": "true",
        "page_size": 500,
        "format": "json",
    }
    ids: list[int] = []
    seen: set[int] = set()
    while url and len(ids) < limit:
        payload = get_json(session, url, params=params)
        params = None
        records = payload.get("results", []) if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            break
        for record in records:
            if not isinstance(record, dict):
                continue
            try:
                mid = int(record["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if mid not in seen and str(record.get("type", "ping")).lower() == "ping":
                seen.add(mid)
                ids.append(mid)
                if len(ids) >= limit:
                    break
        next_url = payload.get("next") if isinstance(payload, dict) else None
        url = next_url if isinstance(next_url, str) and next_url.startswith("https://") else None
        time.sleep(0.3)
    return ids


def row_rtt_ms(row: dict) -> float | None:
    results = row.get("result")
    if not isinstance(results, list):
        return None
    values = []
    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            value = float(item["rtt"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value) and 0.01 <= value <= 60000:
            values.append(value)
    return float(np.median(values)) if values else None


def download_data(days: int, max_measurements: int, max_results: int,
                  measurement_ids: list[int]) -> list[dict]:
    session = new_session()
    now = int(datetime.now(timezone.utc).timestamp())
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    ids = measurement_ids[:max_measurements] if measurement_ids else discover_measurements(session, max_measurements)
    if not ids:
        raise RuntimeError("No measurements discovered. Try --measurement-ids with known public IDs.")
    all_rows = []
    for index, mid in enumerate(ids, 1):
        log(f"Fetching measurement {mid} ({index}/{len(ids)})")
        try:
            payload = get_json(
                session,
                f"{API}/measurements/{mid}/results/",
                {"start": start, "stop": now, "format": "json"},
            )
            records = payload.get("results", []) if isinstance(payload, dict) else payload
            if not isinstance(records, list):
                records = []
            if len(records) > max_results:
                # Keep evenly spaced rows over the returned period instead of
                # keeping only one end of the time series.
                take = np.linspace(0, len(records) - 1, max_results, dtype=int)
                records = [records[i] for i in take]
            for row in records:
                if not isinstance(row, dict):
                    continue
                try:
                    ts = int(row["timestamp"])
                    probe_id = int(row["prb_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                if start <= ts <= now:
                    all_rows.append({
                        "measurement_id": mid,
                        "probe_id": probe_id,
                        "timestamp": ts,
                        "rtt_ms": row_rtt_ms(row),
                    })
        except Exception as exc:
            log(f"Skipping measurement {mid}: {exc}")
        time.sleep(0.5)
    unique = {}
    for row in all_rows:
        unique[(row["measurement_id"], row["probe_id"], row["timestamp"])] = row
    rows = sorted(unique.values(), key=lambda r: (r["measurement_id"], r["probe_id"], r["timestamp"]))
    log(f"Downloaded {len(rows):,} unique ping observations.")
    return rows


def save_jsonl(rows: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def make_examples(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[(row["measurement_id"], row["probe_id"])].append(row)
    examples = []
    for series_id, series in groups.items():
        series.sort(key=lambda r: r["timestamp"])
        if len(series) < SEQ_LEN + max(HORIZONS) + 1:
            continue

        feature_rows = []
        previous_valid = None
        loss_history = []
        previous_ts = None
        for row in series:
            ts, rtt = row["timestamp"], row["rtt_ms"]
            gap = max(1, ts - previous_ts) if previous_ts is not None else 1
            loss_history.append(1.0 if rtt is None else 0.0)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            hour_angle = 2 * math.pi * (dt.hour + dt.minute / 60) / 24
            day_angle = 2 * math.pi * dt.weekday() / 7
            used_rtt = rtt if rtt is not None else previous_valid
            log_rtt = math.log1p(used_rtt) if used_rtt is not None else 0.0
            delta = 0.0
            if rtt is not None and previous_valid is not None:
                delta = max(-8.0, min(8.0, math.log1p(rtt) - math.log1p(previous_valid)))
            features = [
                log_rtt,
                1.0 if rtt is None else 0.0,
                math.log1p(min(gap, 86400)),
                delta,
                float(np.mean(loss_history[-8:])),
                float(np.mean(loss_history[-32:])),
                math.sin(hour_angle), math.cos(hour_angle),
                math.sin(day_angle), math.cos(day_angle),
            ]
            feature_rows.append(features)
            if rtt is not None:
                previous_valid = rtt
            previous_ts = ts

        # Targets are future OBSERVATIONS, because public measurement intervals
        # differ. Metadata explicitly avoids claiming fixed-second forecasts.
        for end in range(SEQ_LEN - 1, len(series) - max(HORIZONS)):
            context = feature_rows[end - SEQ_LEN + 1:end + 1]
            targets = []
            for horizon in HORIZONS:
                future = series[end + horizon]
                prior = series[end]
                rtt = future["rtt_ms"]
                prior_rtt = prior["rtt_ms"]
                targets.extend([
                    math.log1p(rtt) if rtt is not None else 0.0,
                    1.0 if rtt is not None else 0.0,
                    math.log1p(abs(rtt - prior_rtt))
                    if rtt is not None and prior_rtt is not None else 0.0,
                    1.0 if rtt is not None and prior_rtt is not None else 0.0,
                    1.0 if rtt is None else 0.0,
                ])
            examples.append({
                "x": context,
                "targets": targets,
                "timestamp": series[end]["timestamp"],
                "series_id": f"{series_id[0]}:{series_id[1]}",
                "last_rtt": series[end]["rtt_ms"],
            })
    return examples


class CausalBlock(nn.Module):
    def __init__(self, channels: int, dilation: int, dropout: float = 0.1):
        super().__init__()
        self.conv1 = nn.Conv1d(channels, channels, kernel_size=3,
                               dilation=dilation, padding=2 * dilation)
        self.conv2 = nn.Conv1d(channels, channels, kernel_size=3,
                               dilation=dilation, padding=2 * dilation)
        self.norm1 = nn.GroupNorm(8, channels)
        self.norm2 = nn.GroupNorm(8, channels)
        self.dropout = nn.Dropout(dropout)

    @staticmethod
    def crop_right(x, length):
        return x[..., :length]

    def forward(self, x):
        length = x.shape[-1]
        y = self.crop_right(self.conv1(x), length)
        y = self.dropout(torch.nn.functional.gelu(self.norm1(y)))
        y = self.crop_right(self.conv2(y), length)
        y = self.dropout(torch.nn.functional.gelu(self.norm2(y)))
        return torch.nn.functional.gelu(x + y)


class NetworkHealthTCN(nn.Module):
    def __init__(self, feature_count: int, width: int = 96, blocks: int = 4,
                 horizons: int = len(HORIZONS)):
        super().__init__()
        self.input = nn.Conv1d(feature_count, width, kernel_size=1)
        self.blocks = nn.Sequential(*[
            CausalBlock(width, dilation=2 ** i) for i in range(blocks)
        ])
        self.head = nn.Sequential(
            nn.Linear(width, 128), nn.GELU(), nn.Dropout(0.1),
            nn.Linear(128, horizons * 5)
        )
        self.horizons = horizons

    def forward(self, x):
        # [batch, time, features] -> [batch, features, time]
        h = self.input(x.transpose(1, 2))
        h = self.blocks(h)
        return self.head(h[:, :, -1])


def masked_smooth_l1(pred, target, mask):
    loss = torch.nn.functional.smooth_l1_loss(pred, target, reduction="none")
    return (loss * mask).sum() / mask.sum().clamp_min(1.0)


def unpack_targets(y, horizon_index):
    return [y[:, horizon_index * 5 + i] for i in range(5)]


def calculate_loss(pred, y):
    total = pred.new_tensor(0.0)
    for h in range(len(HORIZONS)):
        target_rtt, mask_rtt, target_jitter, mask_jitter, target_loss = unpack_targets(y, h)
        out_rtt, out_jitter, out_loss = pred[:, h*5], pred[:, h*5+1], pred[:, h*5+2]
        total = total + masked_smooth_l1(out_rtt, target_rtt, mask_rtt)
        total = total + 0.5 * masked_smooth_l1(out_jitter, target_jitter, mask_jitter)
        total = total + 0.5 * torch.nn.functional.binary_cross_entropy_with_logits(out_loss, target_loss)
    return total / len(HORIZONS)


@torch.no_grad()
def evaluate(model, loader, device):
    model.eval()
    losses = []
    pred_rtt, true_rtt = [[] for _ in HORIZONS], [[] for _ in HORIZONS]
    pred_loss, true_loss = [[] for _ in HORIZONS], [[] for _ in HORIZONS]
    for xb, yb in loader:
        xb, yb = xb.to(device), yb.to(device)
        out = model(xb).view(-1, len(HORIZONS), 5)
        losses.append(float(calculate_loss(out.reshape(out.shape[0], -1), yb).item()))
        for h in range(len(HORIZONS)):
            tr, mr, tj, mj, tl = unpack_targets(yb, h)
            valid = mr > 0
            if valid.any():
                pred_rtt[h].extend(out[valid, h, 0].cpu().numpy().tolist())
                true_rtt[h].extend(tr[valid].cpu().numpy().tolist())
            pred_loss[h].extend((torch.sigmoid(out[:, h, 2]) >= 0.5).cpu().numpy().tolist())
            true_loss[h].extend((tl >= 0.5).cpu().numpy().tolist())
    result = {"validation_loss": float(np.mean(losses)) if losses else None, "horizons": {}}
    for i, horizon in enumerate(HORIZONS):
        result["horizons"][str(horizon)] = {
            "meaning": f"{horizon} future observations, not fixed seconds",
            "rtt_log_mae": float(np.mean(np.abs(np.array(pred_rtt[i])-np.array(true_rtt[i]))))
            if true_rtt[i] else None,
            "loss_accuracy": float(np.mean(np.array(pred_loss[i]) == np.array(true_loss[i])))
            if true_loss[i] else None,
            "loss_event_rate": float(np.mean(true_loss[i])) if true_loss[i] else None,
            "n_rtt_targets": len(true_rtt[i]),
        }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--max-measurements", type=int, default=100)
    parser.add_argument("--max-results-per-measurement", type=int, default=20000)
    parser.add_argument("--measurement-ids", default="")
    parser.add_argument("--reuse-download", action="store_true")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--patience", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if min(args.days, args.max_measurements, args.epochs, args.batch_size) < 1:
        parser.error("days, max-measurements, epochs and batch-size must be positive")

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    OUT.mkdir(parents=True, exist_ok=True)
    raw_path = OUT / "raw_rows.jsonl"
    ids = [int(x.strip()) for x in args.measurement_ids.split(",") if x.strip()]
    if args.reuse_download and raw_path.exists():
        rows = load_jsonl(raw_path)
        log(f"Loaded {len(rows):,} cached rows")
    else:
        rows = download_data(args.days, args.max_measurements,
                             args.max_results_per_measurement, ids)
        save_jsonl(rows, raw_path)
    if not rows:
        raise RuntimeError("No data downloaded; inspect API access and measurement IDs.")

    examples = make_examples(rows)
    log(f"Built {len(examples):,} sequence examples")
    if len(examples) < 500:
        raise RuntimeError(
            f"Only {len(examples)} examples. Gather more observations/measurements before training."
        )

    examples.sort(key=lambda item: item["timestamp"])
    cutoff = float(np.quantile([e["timestamp"] for e in examples], 0.8))
    train = [e for e in examples if e["timestamp"] < cutoff]
    valid = [e for e in examples if e["timestamp"] >= cutoff]
    if len(train) < 200 or len(valid) < 100:
        raise RuntimeError("Chronological train/validation split is too small.")

    x_train = np.asarray([e["x"] for e in train], dtype=np.float32)
    x_valid = np.asarray([e["x"] for e in valid], dtype=np.float32)
    mean = x_train.reshape(-1, x_train.shape[-1]).mean(0)
    std = x_train.reshape(-1, x_train.shape[-1]).std(0)
    std[std < 1e-6] = 1.0
    x_train, x_valid = (x_train-mean)/std, (x_valid-mean)/std
    y_train = np.asarray([e["targets"] for e in train], dtype=np.float32)
    y_valid = np.asarray([e["targets"] for e in valid], dtype=np.float32)
    train_loader = DataLoader(TensorDataset(torch.from_numpy(x_train), torch.from_numpy(y_train)),
                              batch_size=args.batch_size, shuffle=True)
    valid_loader = DataLoader(TensorDataset(torch.from_numpy(x_valid), torch.from_numpy(y_valid)),
                              batch_size=args.batch_size, shuffle=False)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = NetworkHealthTCN(len(FEATURE_NAMES)).to(device)
    parameter_count = sum(p.numel() for p in model.parameters() if p.requires_grad)
    log(f"Training on {device}; parameters={parameter_count:,}; train={len(train):,}; valid={len(valid):,}")
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="min", factor=0.5, patience=2)
    best, best_epoch, stale = float("inf"), 0, 0
    history = []
    checkpoint = OUT / "model.pt"

    for epoch in range(1, args.epochs+1):
        model.train()
        train_losses = []
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad(set_to_none=True)
            pred = model(xb)
            loss = calculate_loss(pred, yb)
            if not torch.isfinite(loss):
                raise RuntimeError("Non-finite loss; stopping instead of saving a corrupted model.")
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_losses.append(float(loss.detach().cpu()))
        metrics = evaluate(model, valid_loader, device)
        val_loss = metrics["validation_loss"]
        scheduler.step(val_loss)
        history.append({"epoch": epoch, "train_loss": float(np.mean(train_losses)),
                        "validation": metrics, "lr": optimizer.param_groups[0]["lr"]})
        log(f"epoch={epoch:02d} train_loss={np.mean(train_losses):.5f} "
            f"val_loss={val_loss:.5f} h1_RTT_MAE={metrics['horizons']['1']['rtt_log_mae']}")
        if val_loss < best:
            best, best_epoch, stale = val_loss, epoch, 0
            torch.save({"state_dict": model.state_dict(), "feature_names": FEATURE_NAMES,
                        "seq_len": SEQ_LEN, "horizons": HORIZONS,
                        "feature_mean": mean.tolist(), "feature_std": std.tolist(),
                        "width": 96, "blocks": 4}, checkpoint)
        else:
            stale += 1
            if stale >= args.patience:
                log("Early stopping")
                break

    ckpt = torch.load(checkpoint, map_location=device, weights_only=False)
    model.load_state_dict(ckpt["state_dict"])
    final_metrics = evaluate(model, valid_loader, device)
    (OUT / "feature_stats.json").write_text(json.dumps({
        "feature_names": FEATURE_NAMES, "mean": mean.tolist(), "std": std.tolist(),
        "sequence_length": SEQ_LEN, "horizons_in_observations": HORIZONS,
    }, indent=2), encoding="utf-8")
    (OUT / "evaluation.json").write_text(json.dumps(final_metrics, indent=2), encoding="utf-8")
    (OUT / "training_history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    metadata = {
        "model": "NetworkHealthTCN v1 research baseline",
        "trainable_parameters": parameter_count,
        "device": str(device),
        "source": "RIPE Atlas public ping measurements",
        "raw_observations": len(rows),
        "sequence_examples": len(examples),
        "train_examples": len(train),
        "validation_examples": len(valid),
        "validation_cutoff_utc": datetime.fromtimestamp(cutoff, timezone.utc).isoformat(),
        "best_epoch": best_epoch,
        "features": FEATURE_NAMES,
        "horizons": {str(h): f"{h} future observations; interval varies by measurement" for h in HORIZONS},
        "limitations": [
            "Not WebView2/Dofus-specific and not a production-ready predictor.",
            "No DNS/TCP/TLS telemetry or trustworthy attack labels in the source dataset.",
            "A missing ping response is not proof of a game outage or attack.",
            "Overlapping windows and shared measurement routes can make validation optimistic.",
            "Validation metrics must be compared with persistence/EWMA baselines and later unseen sessions.",
            "No online learning, packet modification, spoofing, or automatic system changes are implemented."
        ]
    }
    (OUT / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    log(f"Saved model and evaluation under: {OUT.resolve()}")
    log("Next milestone: implement real app telemetry collection and compare against simple baselines.")


if __name__ == "__main__":
    main()
