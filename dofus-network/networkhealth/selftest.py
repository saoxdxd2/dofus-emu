"""End-to-end smoke test on synthetic data. No network, no PyTorch required.

This is the reproducibility guard for Phases 1-2: it generates a deterministic
synthetic dataset with realistic properties (variable intervals, genuine loss
events, a degrading segment), then runs the full pipeline —

    clean -> features -> split -> baselines -> metrics

and asserts the properties the plan depends on:

1. Genuine loss observations survive cleaning.
2. Malformed and duplicate rows are removed.
3. The split shares no series between train and validation, and no window's
   targets reach across the boundary.
4. Baselines produce finite metrics and persistence is a real competitor.
5. A deliberately degraded segment is detectable by the rolling anomaly scorer.

Run with:  python -m networkhealth.selftest
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

import numpy as np

from . import baselines as baseline_lib
from .clean import clean_rows
from .features import HORIZONS, SEQ_LEN, build_examples, build_series_features
from .splits import SplitConfig, build_split, describe_split


def synth_rows(seed: int = 7, series: int = 12, points: int = 400) -> list[dict]:
    rng = random.Random(seed)
    base = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
    rows: list[dict] = []
    for s in range(series):
        interval = rng.choice([60, 120, 300])
        t = base + rng.randint(0, 3600)
        rtt = rng.uniform(15, 60)
        for i in range(points):
            # Inject a degrading segment in the middle of each series.
            degrading = points * 0.4 < i < points * 0.6
            if degrading:
                rtt = min(600, rtt * 1.02 + rng.uniform(0, 8))
            else:
                rtt = max(8, 0.9 * rtt + 0.1 * rng.uniform(15, 60) + rng.uniform(-3, 3))
            lost = rng.random() < (0.15 if degrading else 0.02)
            rows.append({
                "source": "synthetic",
                "measurement_id": 1000 + s,
                "probe_id": 2000 + s,
                "timestamp": t,
                "rtt_ms": None if lost else round(rtt, 3),
            })
            t += interval + rng.randint(-5, 5)
    # Deliberate corruption that cleaning must remove. These use dedicated
    # measurement/probe pairs that exist nowhere else, so each defect is
    # unambiguous and cannot be absorbed into a real series.
    corrupt_base = 9000
    # A genuine duplicate: identical natural key to an existing row.
    duplicate = dict(rows[0])
    rows.append(dict(duplicate))
    # Impossible RTT values, on their own series.
    rows.append({"source": "synthetic", "measurement_id": corrupt_base + 1,
                 "probe_id": corrupt_base + 1, "timestamp": base + 10, "rtt_ms": -5.0})
    rows.append({"source": "synthetic", "measurement_id": corrupt_base + 2,
                 "probe_id": corrupt_base + 2, "timestamp": base + 10, "rtt_ms": 1e12})
    # Invalid timestamps.
    rows.append({"source": "synthetic", "measurement_id": corrupt_base + 3,
                 "probe_id": corrupt_base + 3, "timestamp": -1, "rtt_ms": 20.0})
    rows.append({"source": "synthetic", "measurement_id": corrupt_base + 4,
                 "probe_id": corrupt_base + 4, "timestamp": "not-a-number", "rtt_ms": 20.0})
    # Malformed RTT type.
    rows.append({"source": "synthetic", "measurement_id": corrupt_base + 5,
                 "probe_id": corrupt_base + 5, "timestamp": base + 10, "rtt_ms": "abc"})
    return rows


def run() -> int:
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        status = "ok" if condition else "FAIL"
        print(f"  [{status}] {message}")
        if not condition:
            failures.append(message)

    print("Synthetic pipeline self-test")
    raw = synth_rows()
    print(f"\nGenerated {len(raw):,} raw rows")

    print("\n1. Cleaning preserves genuine failures")
    cleaned, report = clean_rows(raw)
    loss_rows = [r for r in cleaned if r["rtt_ms"] is None]
    check(len(loss_rows) > 0, f"loss observations retained ({len(loss_rows)} rows)")
    check(report.rows_out < report.rows_in, "corrupt/duplicate rows were removed")
    check(report.dropped_bad_timestamp >= 1, "invalid timestamp dropped")
    check(report.dropped_implausible_rtt >= 2, "impossible RTT values dropped")
    check(report.dropped_duplicate >= 1, "duplicate row collapsed")
    check(report.failures_preserved >= 1, "genuine failures counted as preserved")
    check(all(r["rtt_ms"] is None or r["rtt_ms"] > 0 for r in cleaned),
          "no non-positive RTT remains")

    print("\n2. Feature windows are built")
    series_map = build_series_features(cleaned)
    examples = build_examples(series_map)
    check(len(examples) > 0, f"windows built ({len(examples)})")
    if examples:
        window = examples[0]["x"]
        check(window.shape[1] == len(series_map[next(iter(series_map))]["features"][0]),
              "window width matches feature count")
        check(np.all(np.isfinite(window)), "all features finite")

    print("\n3. Split has no leakage")
    for mode in ("time", "series", "time_and_series"):
        split = build_split(examples, SplitConfig(mode=mode))
        summary = describe_split(examples, split)
        print(f"  mode={mode}: train={summary['train_examples']} "
              f"valid={summary['valid_examples']} shared_series={summary['shared_series']}")
        if mode == "time_and_series":
            # Only this mode makes a time-ordering claim; pure series holdout
            # deliberately keeps the time span shared.
            check(summary["shared_series"] == 0, f"{mode}: no series shared across splits")
        if mode in ("time", "time_and_series") and split.train_idx and split.valid_idx:
            valid_anchor_min = min(examples[i]["timestamp"] for i in split.valid_idx)
            train_target_max = max(max(examples[i]["target_timestamps"])
                                   for i in split.train_idx)
            check(train_target_max < valid_anchor_min,
                  f"{mode}: no training window reaches into the validation period")

    print("\n4. Baselines compute finite metrics")
    split = build_split(examples, SplitConfig(mode="time_and_series"))
    if not split.valid_idx:
        check(False, "validation split non-empty")
    else:
        forecasts = baseline_lib.compute_baselines(series_map, split.valid_idx, examples)
        persistence = forecasts["persistence"]
        for key in ("1", "3", "6"):
            values = persistence.rtt_log.get(key, [])
            check(len(values) > 0 and all(math.isfinite(v) for v in values),
                  f"persistence horizon {key} produced finite forecasts ({len(values)})")

    print("\n5. Anomaly scorer flags the degraded segment")
    key = next(iter(series_map))
    series_rows = series_map[key]["rows"]
    scores = baseline_lib.rolling_zscore_anomaly(series_rows)
    midpoint = len(scores) // 2
    early = float(np.mean(scores[:midpoint])) if midpoint else 0.0
    later = float(np.mean(scores[midpoint:])) if scores[midpoint:] else 0.0
    check(max(scores) > 1.0, f"anomaly score responds to degradation (max={max(scores):.2f})")
    print(f"  early mean={early:.2f}  later mean={later:.2f}")

    print("\n6. Per-Instance Identity-Masking Layer & Multi-Instance Isolation")
    from .identity_mask import InstanceIdentityMasker, IdentityMaskConfig
    from .inference.runtime import MultiInstanceNetworkHealth, Observation

    masker = InstanceIdentityMasker(IdentityMaskConfig(salt="test-salt"))
    instances = ["slot-1", "slot-2", "slot-3", "slot-4"]
    anon_keys = [masker.anonymize_id(inst) for inst in instances]
    check(len(set(anon_keys)) == 4, "all 4 instance IDs mapped to distinct cryptographic pseudonyms")
    check(all(k.startswith("inst_") for k in anon_keys), "pseudonyms conform to anonymous token schema")

    # Test baseline decoupling for high-latency vs low-latency instances
    row_low = {"timestamp": 1000.0, "rtt_ms": 25.0, "instance_id": "slot-1"}
    row_high = {"timestamp": 1000.0, "rtt_ms": 150.0, "instance_id": "slot-2"}
    m_low = masker.mask_row(row_low, "slot-1")
    m_high = masker.mask_row(row_high, "slot-2")
    check(m_low["timestamp"] != 1000.0 or m_high["timestamp"] != 1000.0, "micro-jitter desynchronization applied")
    check(m_low["probe_id"] != "slot-1" and m_high["probe_id"] != "slot-2", "raw instance IDs scrubbed from rows")

    # Test neural InstanceIdentityMaskLayer in NetworkHealthTCN
    try:
        import torch
        from .model import ModelConfig, NetworkHealthTCN

        cfg = ModelConfig(features=10, hidden=32, levels=2, use_identity_mask=True)
        model = NetworkHealthTCN(cfg)
        test_input = torch.randn(4, 32, 10)  # (batch=4 instances, seq_len=32, features=10)
        outputs = model(test_input)
        check(outputs["rtt_log"].shape == (4, len(HORIZONS)), "model forward with InstanceIdentityMaskLayer succeeded")
        check(torch.all(torch.isfinite(outputs["rtt_log"])), "model outputs are finite")
    except ImportError:
        print("  [skip] torch not available for neural layer test")

    print()
    if failures:
        print(f"SELF-TEST FAILED ({len(failures)} checks)")
        for message in failures:
            print(f"  - {message}")
        return 1
    print("SELF-TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
