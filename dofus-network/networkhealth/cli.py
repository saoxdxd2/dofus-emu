"""Command line interface for the NetworkHealth Phase 1/2 pipeline.

Stages are separate commands so each one produces a reviewable artifact:

    python -m networkhealth collect  --days 7 --measurements 25
    python -m networkhealth clean    --input networkhealth_artifacts/raw.jsonl
    python -m networkhealth prepare  --input networkhealth_artifacts/clean.jsonl
    python -m networkhealth baselines --input networkhealth_artifacts/features.npz
    python -m networkhealth train    --input networkhealth_artifacts/features.npz
    python -m networkhealth all      --days 7 --measurements 25

Every command writes a manifest next to its output so results are reproducible.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .data import load_jsonl, log, save_jsonl
from .provenance import write_manifest

ARTIFACTS = Path("networkhealth_artifacts")


def _parse_ids(value: str) -> list[int]:
    if not value:
        return []
    ids = []
    for chunk in value.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            ids.append(int(chunk))
    return ids


def cmd_collect(args: argparse.Namespace) -> Path:
    from .collectors.atlas import CollectorConfig, collect_ripe_atlas

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    cfg = CollectorConfig(
        days=args.days,
        max_measurements=args.measurements,
        max_results_per_measurement=args.max_results,
        measurement_ids=tuple(_parse_ids(args.measurement_ids)),
        skip_empty=args.skip_empty,
        request_qps=args.request_qps,
        workers=getattr(args, "workers", 8),
        probe_timeout=(3.0, getattr(args, "probe_timeout", 8.0)),
    )
    cache_dir = ARTIFACTS / "cache"
    rows, provenance = collect_ripe_atlas(cfg, cache_dir)
    raw_path = ARTIFACTS / "raw.jsonl"
    save_jsonl(rows, raw_path)
    provenance_path = ARTIFACTS / "raw_provenance.json"
    provenance_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    write_manifest(
        ARTIFACTS / "manifest_collect.json",
        stage="collect",
        parameters={"days": args.days, "measurements": args.measurements,
                    "max_results": args.max_results,
                    "workers": getattr(args, "workers", 8),
                    "skip_empty": bool(args.skip_empty),
                    "request_qps": args.request_qps,
                    "probe_timeout_seconds": getattr(args, "probe_timeout", 8.0)},
        inputs=[],
        outputs={"raw": raw_path, "provenance": provenance_path},
        extra={"unique_rows": len(rows), "source": provenance.get("source")},
    )
    log(f"Wrote {raw_path} and {provenance_path}")
    return raw_path


def cmd_clean(args: argparse.Namespace) -> Path:
    from .clean import clean_rows

    rows = load_jsonl(Path(args.input))
    cleaned, report = clean_rows(rows)
    out_path = ARTIFACTS / "clean.jsonl"
    save_jsonl(cleaned, out_path)
    report_path = ARTIFACTS / "clean_report.json"
    report_path.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
    write_manifest(
        ARTIFACTS / "manifest_clean.json",
        stage="clean",
        parameters={},
        inputs=[Path(args.input)],
        outputs={"clean": out_path, "report": report_path},
        extra={"report": report.as_dict()},
    )
    log(f"Cleaned {len(rows):,} -> {len(cleaned):,} rows; report at {report_path}")
    return out_path


def cmd_prepare(args: argparse.Namespace) -> Path:
    import numpy as np

    from .features import HORIZONS, SEQ_LEN, build_examples, build_series_features
    from .identity_mask import InstanceIdentityMasker

    rows = load_jsonl(Path(args.input))
    if getattr(args, "mask_identity", True):
        masker = InstanceIdentityMasker()
        rows = masker.mask_series(rows)
        log(f"Applied per-instance identity mask across {len(rows):,} observations.")

    seq_len = getattr(args, "seq_len", SEQ_LEN)
    series_map = build_series_features(rows)
    examples = build_examples(series_map, seq_len=seq_len)
    if not examples:
        raise SystemExit(f"No training windows produced for seq_len={seq_len}. Collect more data or lower seq_len.")

    x = np.stack([e["x"] for e in examples]).astype(np.float32)
    y = np.stack([e["targets"] for e in examples]).astype(np.float32)
    features_path = Path(args.out) if getattr(args, "out", "") else (ARTIFACTS / "features.npz")
    np.savez_compressed(
        features_path,
        x=x,
        y=y,
        timestamps=np.asarray([e["timestamp"] for e in examples], dtype=np.int64),
        series_index=np.asarray([e["series_id"] for e in examples]),
        measurement_ids=np.asarray([e["measurement_id"] for e in examples], dtype=np.int64),
        probe_ids=np.asarray([e["probe_id"] for e in examples], dtype=np.int64),
        sources=np.asarray([e["source"] for e in examples]),
        horizon_seconds=np.stack([e["horizon_seconds"] for e in examples]).astype(np.float32),
        last_rtt=np.asarray([e["last_rtt"] if e["last_rtt"] is not None else -1.0
                             for e in examples], dtype=np.float32),
        seq_len=np.asarray([seq_len]),
        horizons=np.asarray(HORIZONS),
    )
    write_manifest(
        ARTIFACTS / "manifest_prepare.json",
        stage="prepare",
        parameters={"seq_len": seq_len, "horizons": list(HORIZONS)},
        inputs=[Path(args.input)],
        outputs={"features": features_path},
        extra={"examples": len(examples),
               "series": len(series_map),
               "distinct_probes": len({e["probe_id"] for e in examples})},
    )
    log(f"Built {len(examples):,} windows from {len(series_map):,} series -> {features_path}")
    return features_path


def _load_examples(features_path: Path) -> tuple[list[dict[str, Any]], Any]:
    import numpy as np

    payload = np.load(features_path, allow_pickle=False)
    x = payload["x"]
    y = payload["y"]
    series_ids = payload["series_index"]
    sources = payload["sources"]
    measurement_ids = payload["measurement_ids"]
    probe_ids = payload["probe_ids"]
    timestamps = payload["timestamps"]
    horizon_seconds = payload["horizon_seconds"]
    examples = []
    for i in range(x.shape[0]):
        examples.append({
            "x": x[i],
            "targets": y[i],
            "timestamp": int(timestamps[i]),
            "series_id": str(series_ids[i]),
            "source": str(sources[i]),
            "measurement_id": int(measurement_ids[i]),
            "probe_id": int(probe_ids[i]),
            "horizon_seconds": horizon_seconds[i].tolist(),
        })
    return examples, payload


def cmd_baselines(args: argparse.Namespace) -> Path:
    """Baseline-only comparison, runnable without PyTorch."""
    import numpy as np

    from .evaluation import write_json
    from .features import HORIZONS, TARGET_BLOCK
    from .splits import SplitConfig, build_split, compact_summary, split_indices

    features_path = Path(args.input)
    examples, payload = _load_examples(features_path)
    split_config = SplitConfig(mode=args.split_mode, valid_fraction=args.valid_fraction)
    split = build_split(examples, split_config)
    summary = compact_summary(split, examples)
    indices_path = ARTIFACTS / "baseline_split_indices.json"
    write_json(indices_path, split_indices(split))

    # Rebuild series structure needed by the baselines from the saved windows.
    series_map: dict[tuple, dict[str, Any]] = {}
    for example in examples:
        key = (example["source"], example["measurement_id"], example["probe_id"])
        series_map.setdefault(key, {"rows": [], "features": None, "series_id": example["series_id"]})

    # Baselines need raw rows; without them we fall back to persistence over the
    # feature windows, which is still an honest, clearly-labelled baseline.
    y = payload["y"]
    block = y.reshape(y.shape[0], len(HORIZONS), TARGET_BLOCK)
    results: dict[str, Any] = {}
    for name, extractor in (
        ("persistence", lambda i: block[i, :, 0] if False else None),
    ):
        del name, extractor

    valid_idx = split.valid_idx
    if not valid_idx:
        raise SystemExit("Empty validation split; cannot compute baselines.")

    x_all = payload["x"]
    # log_rtt_ms is feature 0 and already log1p-transformed.
    last_feature = x_all[:, -1, 0]
    persistence = np.repeat(last_feature[:, None], len(HORIZONS), axis=1)
    moving = np.repeat(x_all[:, -8:, 0].mean(axis=1)[:, None], len(HORIZONS), axis=1)
    ewma = np.repeat(x_all[:, :, 0].mean(axis=1)[:, None], len(HORIZONS), axis=1)

    target = block[:, :, 0]
    mask = block[:, :, 1]
    loss_true = block[:, :, 4]
    loss_prob = np.repeat(x_all[:, -32:, 1].mean(axis=1)[:, None], len(HORIZONS), axis=1)
    for name, prediction in (("persistence", persistence),
                             ("moving_average", moving),
                             ("ewma", ewma)):
        block_metrics: dict[str, Any] = {}
        for h_index, horizon in enumerate(HORIZONS):
            valid = mask[valid_idx, h_index] > 0.5
            if np.any(valid):
                errors = np.abs(prediction[valid_idx, h_index][valid] - target[valid_idx, h_index][valid])
                mae = float(np.mean(errors))
            else:
                mae = None
            prob = loss_prob[valid_idx, h_index]
            truth = loss_true[valid_idx, h_index]
            block_metrics[str(horizon)] = {
                "rtt_log_mae": mae,
                "loss_brier": float(np.mean((prob - truth) ** 2)) if prob.size else None,
                "loss_detection": _detection(prob, truth),
                "n_rtt_targets": int(np.sum(valid)),
            }
        results[name] = {"per_horizon": block_metrics}
    output = {"split_summary": summary, "baselines": results,
              "note": "Baseline-only run; no neural model involved."}
    out_path = ARTIFACTS / "baselines.json"
    write_json(out_path, output)
    write_manifest(
        ARTIFACTS / "manifest_baselines.json",
        stage="baselines",
        parameters=split_config.as_dict(),
        inputs=[features_path],
        outputs={"baselines": out_path, "split_indices": indices_path},
        extra={"split_summary": summary},
    )
    log(f"Baselines written to {out_path}")
    return out_path


def _detection(prob, truth, threshold: float = 0.5) -> dict[str, Any]:
    import numpy as np

    predicted = prob >= threshold
    actual = truth >= 0.5
    tp = int(np.sum(predicted & actual))
    fp = int(np.sum(predicted & ~actual))
    tn = int(np.sum(~predicted & ~actual))
    fn = int(np.sum(~predicted & actual))
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return {
        "threshold": threshold,
        "precision": precision, "recall": recall,
        "f1": (2 * precision * recall / (precision + recall)
               if precision and recall and (precision + recall) else None),
        "false_alarm_rate": (fp / (fp + tn)) if (fp + tn) else None,
        "true_positive": tp, "false_positive": fp,
        "true_negative": tn, "false_negative": fn,
    }


def cmd_train(args: argparse.Namespace) -> Path:

    from .features import build_series_features
    from .splits import SplitConfig
    from .training import TrainConfig, train_model

    features_path = Path(args.input)
    examples, _ = _load_examples(features_path)

    # Reconstruct series_map from clean rows when available, so baselines can use
    # the raw observations rather than only the pre-computed windows.
    clean_path = ARTIFACTS / "clean.jsonl"
    if clean_path.exists():
        rows = load_jsonl(clean_path)
        series_map = build_series_features(rows)
    else:
        log("clean.jsonl not found; baselines will use window-derived series only.")
        series_map = {}
        for example in examples:
            key = (example["source"], example["measurement_id"], example["probe_id"])
            payload = series_map.setdefault(key, {
                "series_id": example["series_id"], "rows": [], "features": None})
            payload["rows"].append({
                "timestamp": example["timestamp"],
                "rtt_ms": None if example.get("last_rtt", -1) < 0 else example["last_rtt"],
            })
        for payload in series_map.values():
            payload["rows"].sort(key=lambda r: r["timestamp"])

    config = TrainConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        patience=args.patience,
        hidden=args.hidden,
        levels=args.levels,
        dropout=args.dropout,
        seed=args.seed,
        use_identity_mask=getattr(args, "use_identity_mask", True),
        split=SplitConfig(mode=args.split_mode, valid_fraction=args.valid_fraction,
                          purge_seconds=args.purge_seconds, gap_seconds=args.gap_seconds,
                          seed=args.seed),
    )
    out_dir = Path(args.out_dir) if getattr(args, "out_dir", "") else ARTIFACTS
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = train_model(examples, series_map, config, out_dir)
    write_manifest(
        ARTIFACTS / "manifest_train.json",
        stage="train",
        parameters=config.as_dict(),
        inputs=[features_path],
        outputs={"checkpoint": Path(artifacts["checkpoint"]),
                 "evaluation": ARTIFACTS / "evaluation.json"},
        extra={"split_summary": artifacts["split_summary"],
               "cost": artifacts["cost"]},
    )
    log("Training complete.")
    return ARTIFACTS / "evaluation.json"


def cmd_inference(args: argparse.Namespace) -> None:
    """Validate a checkpoint and optionally replay a JSONL stream through it."""
    import json as _json

    from .inference import NetworkHealthInference, Observation

    checkpoint = Path(args.checkpoint)
    if not checkpoint.exists():
        raise SystemExit(f"Checkpoint not found: {checkpoint}")

    engine = NetworkHealthInference(checkpoint, threads=args.threads)
    bundle = engine.bundle
    print(f"Checkpoint loaded: {checkpoint}")
    print(f"  parameters        : {bundle.metadata.get('best_valid_loss') is not None}")
    print(f"  features          : {len(bundle.feature_names)}")
    print(f"  horizons          : {list(bundle.horizons)}")
    print(f"  model parameters  : {engine.model.parameter_count():,}")
    print(f"  split summary     : {bundle.metadata.get('split_summary', {}).get('mode', 'n/a')}")

    if not args.input:
        print("\nNo --input stream given; checkpoint validated only.")
        return

    stream_path = Path(args.input)
    count = 0
    forecast = None
    with stream_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            observation = Observation(
                timestamp=float(record.get("timestamp", 0.0)),
                rtt_ms=(None if record.get("rtt_ms") is None else float(record["rtt_ms"])),
                received=int(record.get("received", 0) or 0),
                sent=int(record.get("sent", 0) or 0),
                stale=bool(record.get("stale", False)),
            )
            count += 1
            result = engine.observe(observation)
            if result is not None:
                forecast = result

    print(f"\nReplayed {count:,} observations from {stream_path}")
    if forecast is None:
        print(f"Window not filled: need {engine.window.seq_len} observations.")
        return
    print(_json.dumps(forecast.as_dict(), indent=2))
    print("\nLatency:", _json.dumps(engine.cost_report(), indent=2))


def cmd_all(args: argparse.Namespace) -> None:
    namespace = argparse.Namespace(**vars(args))
    raw = cmd_collect(namespace)
    namespace.input = str(raw)
    clean = cmd_clean(namespace)
    namespace.input = str(clean)
    features = cmd_prepare(namespace)
    namespace.input = str(features)
    cmd_baselines(namespace)
    cmd_train(namespace)
    log(f"All artifacts under {ARTIFACTS.resolve()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="networkhealth",
        description="NetworkHealth AI Phase 1/2 pipeline (collection, cleaning, "
                    "honest baselines, compact TCN training).",
    )
    parser.add_argument("--version", action="version", version=f"networkhealth {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="Download public RIPE Atlas ping data")
    collect.add_argument("--days", type=int, default=7)
    collect.add_argument("--measurements", type=int, default=25)
    collect.add_argument("--max-results", type=int, default=50000)
    collect.add_argument("--workers", type=int, default=8, help="Parallel download workers")
    collect.add_argument("--measurement-ids", default="",
                         help="Comma-separated explicit ids; skips discovery")
    collect.add_argument("--request-qps", type=float, default=10.0,
                         help="Client-side request pacing (0 disables pacing)")
    collect.add_argument("--no-skip-empty", dest="skip_empty", action="store_false",
                         default=True,
                         help="Query every id with the full read timeout instead of "
                              "skipping ids that have no results in the window")
    collect.add_argument("--probe-timeout", type=float, default=8.0,
                         help="Read timeout in seconds for the bounded activity probe")
    collect.set_defaults(func=cmd_collect)

    clean = sub.add_parser("clean", help="Clean collected rows without erasing failures")
    clean.add_argument("--input", default=str(ARTIFACTS / "raw.jsonl"))
    clean.set_defaults(func=cmd_clean)

    prepare = sub.add_parser("prepare", help="Build causal feature windows")
    prepare.add_argument("--input", default=str(ARTIFACTS / "clean.jsonl"))
    prepare.add_argument("--seq-len", type=int, default=128,
                         help="Context length (number of historical observations per window)")
    prepare.add_argument("--mask-identity", action="store_true", default=True,
                         help="Apply per-instance identity masking (pseudonymization, desynchronization)")
    prepare.add_argument("--no-mask-identity", dest="mask_identity", action="store_false",
                         help="Disable identity masking")
    prepare.add_argument("--out", default="",
                         help="Where to write the feature windows. Default: "
                              "networkhealth_artifacts/features.npz. Pass a different path "
                              "when the corpus is local telemetry, so the public-probe "
                              "features are not overwritten.")
    prepare.set_defaults(func=cmd_prepare)

    baselines = sub.add_parser("baselines", help="Compute baselines only (no PyTorch needed)")
    baselines.add_argument("--input", default=str(ARTIFACTS / "features.npz"))
    baselines.add_argument("--split-mode", default="time",
                           choices=["time", "series", "time_and_series"])
    baselines.add_argument("--valid-fraction", type=float, default=0.2)
    baselines.set_defaults(func=cmd_baselines)

    train = sub.add_parser("train", help="Train the compact TCN and evaluate")
    train.add_argument("--input", default=str(ARTIFACTS / "features.npz"))
    train.add_argument("--epochs", type=int, default=40)
    train.add_argument("--batch-size", type=int, default=128)
    train.add_argument("--learning-rate", type=float, default=2e-3)
    train.add_argument("--patience", type=int, default=8)
    train.add_argument("--hidden", type=int, default=64)
    train.add_argument("--levels", type=int, default=4)
    train.add_argument("--dropout", type=float, default=0.1)
    train.add_argument("--seed", type=int, default=42)
    train.add_argument("--use-identity-mask", action="store_true", default=True,
                       help="Enable neural InstanceNorm1d identity-masking layer in TCN")
    train.add_argument("--no-identity-mask", dest="use_identity_mask", action="store_false",
                       help="Disable neural identity-masking layer")
    train.add_argument("--split-mode", default="time_and_series",
                       choices=["time", "series", "time_and_series"])
    train.add_argument("--valid-fraction", type=float, default=0.2)
    train.add_argument("--purge-seconds", type=int, default=3600)
    train.add_argument("--gap-seconds", type=int, default=1800)
    train.add_argument("--out-dir", default="",
                       help="Where to write the checkpoint and evaluation. Default: "
                            "networkhealth_artifacts. Point this elsewhere when training on "
                            "a different corpus, so the shipped model is not replaced by "
                            "whatever was trained last.")
    train.set_defaults(func=cmd_train)

    inference = sub.add_parser(
        "inference",
        help="Validate a checkpoint and replay a telemetry stream on CPU")
    inference.add_argument("--checkpoint", default=str(ARTIFACTS / "model.pt"))
    inference.add_argument("--input", default="",
                           help="Optional JSONL stream with timestamp/rtt_ms per line")
    inference.add_argument("--threads", type=int, default=None,
                           help="Limit CPU threads (leave unset to use all cores)")
    inference.set_defaults(func=cmd_inference)

    everything = sub.add_parser("all", help="Run collect -> clean -> prepare -> baselines -> train")
    everything.add_argument("--days", type=int, default=7)
    everything.add_argument("--measurements", type=int, default=25)
    everything.add_argument("--max-results", type=int, default=50000)
    everything.add_argument("--workers", type=int, default=8, help="Parallel download workers")
    everything.add_argument("--measurement-ids", default="")
    everything.add_argument("--mask-identity", action="store_true", default=True)
    everything.add_argument("--no-mask-identity", dest="mask_identity", action="store_false")
    everything.add_argument("--epochs", type=int, default=40)
    everything.add_argument("--batch-size", type=int, default=128)
    everything.add_argument("--learning-rate", type=float, default=2e-3)
    everything.add_argument("--patience", type=int, default=8)
    everything.add_argument("--hidden", type=int, default=64)
    everything.add_argument("--levels", type=int, default=4)
    everything.add_argument("--dropout", type=float, default=0.1)
    everything.add_argument("--seed", type=int, default=42)
    everything.add_argument("--use-identity-mask", action="store_true", default=True)
    everything.add_argument("--no-identity-mask", dest="use_identity_mask", action="store_false")
    everything.add_argument("--split-mode", default="time_and_series",
                            choices=["time", "series", "time_and_series"])
    everything.add_argument("--valid-fraction", type=float, default=0.2)
    everything.add_argument("--purge-seconds", type=int, default=3600)
    everything.add_argument("--gap-seconds", type=int, default=1800)
    everything.set_defaults(func=cmd_all)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
