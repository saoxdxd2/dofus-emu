"""Cleaning for network telemetry that must NOT erase genuine failures.

The guiding rule for Phase 1: a missing ping reply, a timeout, or a spike is
often the most informative event in the series. Cleaning therefore removes only
*provably wrong* records and *flags* suspicious ones; it never imputes a
failure into a healthy-looking number.

Cleaning steps, each counted in a report:

1. Structural validity — types, finiteness, non-negative epoch timestamps.
2. Duplicate collapse — same (source, measurement, probe, timestamp).
3. Timestamp normalisation — epoch seconds/milliseconds, monotonicity,
   and detection of clock-skewed rows against the collection window.
4. Outlier handling — physiologically impossible RTTs are dropped; merely
   extreme but plausible RTTs are kept and *flagged* as ``is_outlier``.
5. Failure preservation — rows with no successful reply keep ``rtt_ms=None``
   and ``received=0``; they are marked ``is_failure`` and never dropped.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

RTT_MIN_MS, RTT_MAX_MS = 0.01, 60000.0
# A ping RTT beyond this is implausible for a real host and is almost always a
# parsing artefact. Plausible-but-large values are flagged, not removed.
RTT_PLAUSIBLE_MS = 5000.0
# Timestamps more than this far outside the declared window are treated as
# clock skew and dropped (they cannot be placed in time for leakage-safe splits).
MAX_SKEW_SECONDS = 86400
MAX_INTERVAL_SECONDS = 86400


@dataclass
class CleaningReport:
    """Auditable record of what cleaning did; written next to the dataset."""

    rows_in: int = 0
    rows_out: int = 0
    dropped_invalid_structure: int = 0
    dropped_bad_timestamp: int = 0
    dropped_out_of_window: int = 0
    dropped_implausible_rtt: int = 0
    dropped_duplicate: int = 0
    flagged_outliers: int = 0
    failures_preserved: int = 0
    timestamp_ms_rescaled: int = 0
    non_monotonic_groups: int = 0
    interval_change_groups: int = 0
    groups_total: int = 0
    groups_kept: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "dropped_invalid_structure": self.dropped_invalid_structure,
            "dropped_bad_timestamp": self.dropped_bad_timestamp,
            "dropped_out_of_window": self.dropped_out_of_window,
            "dropped_implausible_rtt": self.dropped_implausible_rtt,
            "dropped_duplicate": self.dropped_duplicate,
            "flagged_outliers": self.flagged_outliers,
            "failures_preserved": self.failures_preserved,
            "timestamp_ms_rescaled": self.timestamp_ms_rescaled,
            "non_monotonic_groups": self.non_monotonic_groups,
            "interval_change_groups": self.interval_change_groups,
            "groups_total": self.groups_total,
            "groups_kept": self.groups_kept,
            "notes": self.notes,
        }


def _as_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalise_timestamp(raw: Any, report: CleaningReport) -> int | None:
    """Return epoch seconds, rescaling obvious millisecond timestamps."""
    value = _as_int(raw)
    if value is None:
        return None
    # Public telemetry sometimes arrives in milliseconds. Anything past year
    # 33658 in seconds is implausible, so treat large values as milliseconds.
    if value > 10 ** 11:
        value = value // 1000
        report.timestamp_ms_rescaled += 1
    if value < 0 or value > 10 ** 11:
        return None
    return value


def clean_rows(rows: list[dict], *, window_start: int | None = None,
               window_stop: int | None = None,
               plausible_rtt_ms: float = RTT_PLAUSIBLE_MS) -> tuple[list[dict], CleaningReport]:
    """Clean raw collector rows. Returns ``(cleaned_rows, report)``."""
    report = CleaningReport(rows_in=len(rows))
    stage_one: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            report.dropped_invalid_structure += 1
            continue
        mid = _as_int(row.get("measurement_id"))
        pid = _as_int(row.get("probe_id"))
        raw_ts = row.get("timestamp")
        ts = _normalise_timestamp(raw_ts, report)
        if ts is None:
            # Distinguish "no usable timestamp" from "missing identifiers" so the
            # cleaning report says what actually went wrong.
            if _as_int(raw_ts) is None and _as_float(raw_ts) is None:
                report.dropped_bad_timestamp += 1
            else:
                report.dropped_out_of_window += 1
            continue
        if mid is None or pid is None:
            report.dropped_invalid_structure += 1
            continue
        if window_start is not None and ts < window_start - MAX_SKEW_SECONDS:
            report.dropped_out_of_window += 1
            continue
        if window_stop is not None and ts > window_stop + MAX_SKEW_SECONDS:
            report.dropped_out_of_window += 1
            continue

        rtt = _as_float(row.get("rtt_ms"))
        received = _as_int(row.get("received")) or 0
        sent = _as_int(row.get("sent")) or 0
        if rtt is not None and not (RTT_MIN_MS <= rtt <= RTT_MAX_MS):
            # Physically impossible measurement artefact: drop the value but
            # keep the row as a failure observation so the timeline is intact.
            report.dropped_implausible_rtt += 1
            rtt = None
            received = 0
        cleaned = {
            "source": str(row.get("source", "unknown")),
            "measurement_id": mid,
            "probe_id": pid,
            "timestamp": ts,
            "rtt_ms": rtt,
            "received": received,
            "sent": sent,
            "is_failure": rtt is None,
            "is_outlier": bool(rtt is not None and rtt >= plausible_rtt_ms),
            "fetched_utc": row.get("fetched_utc"),
        }
        if cleaned["is_failure"]:
            report.failures_preserved += 1
        if cleaned["is_outlier"]:
            report.flagged_outliers += 1
        stage_one.append(cleaned)

    # Duplicate collapse on the natural key.
    unique: dict[tuple, dict] = {}
    for row in stage_one:
        key = (row["source"], row["measurement_id"], row["probe_id"], row["timestamp"])
        if key in unique:
            report.dropped_duplicate += 1
        unique[key] = row

    # Per-series ordering and interval diagnostics.
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in unique.values():
        groups[(row["source"], row["measurement_id"], row["probe_id"])].append(row)

    cleaned_rows: list[dict] = []
    for key, series in groups.items():
        report.groups_total += 1
        series.sort(key=lambda r: r["timestamp"])
        timestamps = [row["timestamp"] for row in series]
        if any(b < a for a, b in zip(timestamps, timestamps[1:])):
            report.non_monotonic_groups += 1
        if len(timestamps) >= 3:
            gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
            positive = [g for g in gaps if g > 0]
            if positive and (max(positive) > 4 * min(positive)
                             or max(positive) > MAX_INTERVAL_SECONDS):
                report.interval_change_groups += 1
        report.groups_kept += 1
        cleaned_rows.extend(series)

    cleaned_rows.sort(key=lambda r: (r["measurement_id"], r["probe_id"], r["timestamp"]))
    report.rows_out = len(cleaned_rows)
    return cleaned_rows, report
