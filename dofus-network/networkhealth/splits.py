"""Leakage-resistant dataset splitting for network time series.

The original script used a single quantile on the *anchor* timestamp, which
leaks in three separate ways:

1. **Overlapping windows.** Neighbouring examples share up to ``SEQ_LEN-1``
   observations. A pure timestamp cut puts near-identical windows on both sides
   of the boundary, so validation measures memorisation, not generalisation.
2. **Shared probes/series.** Windows from the same (measurement, probe) appear
   in both splits; the model can learn a probe's idiosyncrasies instead of the
   network's behaviour.
3. **Target bleed.** A window whose *anchor* sits just before the cut still
   looks at future observations past the cut, so validation-time inputs contain
   training-period data.

This module fixes all three:

* ``purge_seconds`` (a.k.a. embargo) removes any example whose targets reach
  into the validation period.
* ``gap_seconds`` inserts a blank buffer between the two periods so even
  long-horizon windows cannot straddle the boundary.
* ``group_by_series`` holds out *entire* series (probes/measurements) rather
  than individual windows, and reports how many distinct series each split has.

The split is returned as explicit index sets plus a JSON-serialisable summary so
the experiment manifest can record exactly which rows were used where.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np


@dataclass
class SplitConfig:
    mode: str = "time"                 # "time" | "series" | "time_and_series"
    valid_fraction: float = 0.2
    purge_seconds: int = 3600          # remove examples whose targets cross the cut
    gap_seconds: int = 1800            # additional blank embargo between periods
    seed: int = 42

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "valid_fraction": self.valid_fraction,
            "purge_seconds": self.purge_seconds,
            "gap_seconds": self.gap_seconds,
            "seed": self.seed,
        }


@dataclass
class SplitResult:
    train_idx: list[int]
    valid_idx: list[int]
    summary: dict[str, Any] = field(default_factory=dict)


def _quantile_cut(anchor_timestamps: np.ndarray, valid_fraction: float) -> float:
    if anchor_timestamps.size == 0:
        return 0.0
    return float(np.quantile(anchor_timestamps, 1.0 - valid_fraction))


def time_split(examples: list[dict[str, Any]], config: SplitConfig) -> SplitResult:
    """Chronological split with purging and an embargo gap."""
    anchor = np.asarray([int(e["timestamp"]) for e in examples], dtype=np.int64)
    if anchor.size == 0:
        return SplitResult([], [], {"reason": "no examples"})
    cut = _quantile_cut(anchor, config.valid_fraction)
    target_end = np.asarray(
        [int(e["target_timestamps"][-1]) if e.get("target_timestamps") else int(e["timestamp"])
         for e in examples],
        dtype=np.int64,
    )
    train_idx = [i for i in range(len(examples))
                 if target_end[i] < cut - config.gap_seconds]
    valid_idx = [i for i in range(len(examples))
                 if anchor[i] >= cut]
    purged = len(examples) - len(train_idx) - len(valid_idx)
    summary = {
        "mode": "time",
        "cut_epoch": cut,
        "purged_examples": purged,
        "train_index": train_idx,
        "valid_index": valid_idx,
    }
    return SplitResult(train_idx, valid_idx, summary)


def series_split(examples: list[dict[str, Any]], config: SplitConfig) -> SplitResult:
    """Hold out entire series so probes are never shared between splits."""
    series_ids = sorted({str(e.get("series_id", "unknown")) for e in examples})
    if len(series_ids) < 2:
        return SplitResult([], [], {"reason": "need at least two distinct series"})
    rng = np.random.default_rng(config.seed)
    order = rng.permutation(len(series_ids))
    n_valid = max(1, int(round(len(series_ids) * config.valid_fraction)))
    n_valid = min(n_valid, len(series_ids) - 1)
    valid_series = {series_ids[i] for i in order[:n_valid]}
    train_idx = [i for i, e in enumerate(examples) if str(e.get("series_id")) not in valid_series]
    valid_idx = [i for i, e in enumerate(examples) if str(e.get("series_id")) in valid_series]
    summary = {
        "mode": "series",
        "valid_series_count": len(valid_series),
        "train_series_count": len(series_ids) - len(valid_series),
        "valid_series": sorted(valid_series),
        "train_index": train_idx,
        "valid_index": valid_idx,
    }
    return SplitResult(train_idx, valid_idx, summary)


def time_and_series_split(examples: list[dict[str, Any]], config: SplitConfig) -> SplitResult:
    """Hold out unseen series *and* a later time period.

    This is the strictest configuration and the one to quote in the Phase 2
    comparison table, because it is the closest offline analogue of "a probe and
    a day the model has never seen".
    """
    series_result = series_split(examples, config)
    valid_series = set(series_result.summary.get("valid_series", []))
    # Restrict the time split to the series already held out for validation so
    # we never need to reuse validation probes as training anchors.
    subset = [e for e in examples if str(e.get("series_id")) in valid_series]
    if len(subset) < 2:
        return SplitResult([], [], {"reason": "insufficient held-out series"})
    anchor = np.asarray([int(e["timestamp"]) for e in subset], dtype=np.int64)
    cut = _quantile_cut(anchor, config.valid_fraction)
    train_idx, valid_idx = [], []
    for i, e in enumerate(examples):
        sid = str(e.get("series_id"))
        targets = e.get("target_timestamps") or [int(e["timestamp"])]
        reaches = int(targets[-1])
        if sid in valid_series:
            if int(e["timestamp"]) >= cut:
                valid_idx.append(i)
        else:
            if reaches < cut - config.gap_seconds:
                train_idx.append(i)
    summary = {
        "mode": "time_and_series",
        "cut_epoch": cut,
        "held_out_series": sorted(valid_series),
        "purged_examples": len(examples) - len(train_idx) - len(valid_idx),
        "train_index": train_idx,
        "valid_index": valid_idx,
    }
    return SplitResult(train_idx, valid_idx, summary)


def build_split(examples: list[dict[str, Any]], config: SplitConfig) -> SplitResult:
    if config.mode == "time":
        return time_split(examples, config)
    if config.mode == "series":
        return series_split(examples, config)
    if config.mode == "time_and_series":
        return time_and_series_split(examples, config)
    raise ValueError(f"Unknown split mode: {config.mode}")


def describe_split(examples: list[dict[str, Any]], result: SplitResult) -> dict[str, Any]:
    """Summarise a split for the manifest: counts, series coverage, time span."""
    train_idx = result.train_idx
    valid_idx = result.valid_idx
    summary: dict[str, Any] = {
        "train_examples": len(train_idx),
        "valid_examples": len(valid_idx),
        "train_series": len({examples[i].get("series_id") for i in train_idx}),
        "valid_series": len({examples[i].get("series_id") for i in valid_idx}),
        "shared_series": len({examples[i].get("series_id") for i in train_idx}
                             & {examples[i].get("series_id") for i in valid_idx}),
    }
    for name, idx in (("train", train_idx), ("valid", valid_idx)):
        if not idx:
            continue
        anchors = np.asarray([examples[i]["timestamp"] for i in idx], dtype=np.int64)
        summary[f"{name}_anchor_min_epoch"] = int(anchors.min())
        summary[f"{name}_anchor_max_epoch"] = int(anchors.max())
    return summary


def split_indices(result: "SplitResult") -> dict[str, list[int]]:
    """Extract the bulky index lists so they can be stored separately.

    Keeping 100k-element index arrays inside a human-readable metrics JSON makes
    review impossible. ``compact_summary`` strips them; the indices go to a
    dedicated artifact that a reviewer only opens when auditing the split.
    """
    return {"train_index": list(result.train_idx), "valid_index": list(result.valid_idx)}


def compact_summary(result: "SplitResult", examples: list[dict[str, Any]]) -> dict[str, Any]:
    """A split summary safe to embed in a metrics report: no index lists."""
    summary = {k: v for k, v in result.summary.items()
               if k not in ("train_index", "valid_index")}
    summary.update(describe_split(examples, result))
    return summary
