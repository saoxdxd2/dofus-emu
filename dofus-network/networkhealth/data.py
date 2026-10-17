"""RIPE Atlas downloader with provenance metadata.

Phase 1 requirements handled here:

* Reproducible downloader with a polite client and bounded requests.
* Provenance: source name, measurement ids, probe ids, fetch time, API params,
  dataset version, and observation interval statistics are all recorded so a
  checkpoint can be traced back to the exact data that produced it.
* Duplicate and malformed observation handling without erasing genuine failures:
  a row with ``rtt_ms = None`` is a *real* packet-loss observation and is kept,
  while rows that cannot be parsed at all are dropped and counted.

RIPE Atlas is one source among several. ``SOURCE`` is stored on every row so
adding DNS/TCP/TLS datasets later cannot silently merge probes across sources.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests

API = "https://atlas.ripe.net/api/v2"
SOURCE_NAME = "ripe_atlas_ping"
USER_AGENT = "NetworkHealthResearch/1.0 (public RIPE Atlas data; polite client)"


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


class RateLimiter:
    """Simple client-side pacing so we never depend on hitting HTTP 429.

    RIPE Atlas asks clients to be polite. Sleeping only after a completed request
    (the previous behaviour) still allowed bursts; pacing every request keeps the
    average rate near ``qps`` and reduces back-off waits in the long run.
    """

    def __init__(self, qps: float = 2.0):
        self.min_interval = 0.0 if qps <= 0 else 1.0 / float(qps)
        # Start in the past so the first request is not delayed; subsequent
        # requests are then spaced by ``min_interval`` from the previous one.
        self._last = -self.min_interval

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        remaining = self.min_interval - (time.monotonic() - self._last)
        if remaining > 0:
            time.sleep(remaining)
        self._last = time.monotonic()


def get_json(session: requests.Session, url: str, params: dict | None = None,
             retries: int = 5, limiter: RateLimiter | None = None,
             timeout: tuple[float, float] = (10, 45)) -> Any:
    last_error: str | None = None
    for attempt in range(retries):
        try:
            if limiter is not None:
                limiter.wait()
            response = session.get(url, params=params, timeout=timeout)
            if response.status_code in (429,) or response.status_code >= 500:
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


def measurement_has_recent_results(session: requests.Session, measurement_id: int,
                                  start: int, stop: int,
                                  limiter: RateLimiter | None = None,
                                  timeout: tuple[float, float] = (5.0, 15.0)) -> bool:
    """Cheap bounded probe: does this measurement have any results in the window?

    A single record is enough to answer the question, so we request one row and
    cap the read timeout. This is what keeps discovery from spending minutes per
    long-lived measurement that simply has no data in the requested period.
    """
    try:
        payload = get_json(
            session,
            f"{API}/measurements/{measurement_id}/results/",
            {"start": start, "stop": stop, "format": "json"},
            retries=2,
            limiter=limiter,
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 - a failed probe means "treat as empty"
        return False
    records = payload.get("results", []) if isinstance(payload, dict) else payload
    if not isinstance(records, list) or not records:
        return False
    for row in records:
        if isinstance(row, dict):
            try:
                ts = int(row["timestamp"])
            except (KeyError, TypeError, ValueError):
                continue
            if start <= ts <= stop:
                return True
    return False


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def discover_measurements(session: requests.Session, limit: int,
                          limiter: RateLimiter | None = None) -> list[int]:
    """Discover public active ping measurement ids, bounded by ``limit``."""
    url: str | None = f"{API}/measurements/"
    params: dict | None = {
        "type": "ping",
        "is_public": "true",
        "status": 2,
        "is_oneoff": "false",
        "sort": "-id",
        "page_size": 500,
        "format": "json",
    }
    ids: list[int] = []
    seen: set[int] = set()
    while url and len(ids) < limit:
        payload = get_json(session, url, params=params, limiter=limiter)
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
    return ids


def row_rtt_ms(row: dict) -> float | None:
    """Median RTT of successful replies, or ``None`` when every reply failed.

    ``None`` is a meaningful value: it is a packet-loss observation. Callers must
    keep it rather than imputing zero.
    """
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
                  measurement_ids: list[int], skip_empty: bool = False,
                  request_qps: float = 2.0,
                  probe_timeout: tuple[float, float] = (5.0, 15.0),
                  ) -> tuple[list[dict], dict[str, Any]]:
    """Download ping observations and return ``(rows, provenance)``.

    Public discovery returns long-lived measurement ids, and many of them have
    no results in the requested window. Querying every one with the full read
    timeout costs minutes per dead id. ``skip_empty`` runs a cheap bounded
    probe first and skips ids with no data in the window.
    """
    session = new_session()
    limiter = RateLimiter(request_qps)
    now = int(datetime.now(timezone.utc).timestamp())
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    ids = (measurement_ids[:max_measurements] if measurement_ids
           else discover_measurements(session, max_measurements, limiter=limiter))
    if not ids:
        raise RuntimeError("No measurements discovered. Try --measurement-ids with known public IDs.")

    all_rows: list[dict] = []
    dropped_malformed = 0
    measurements_empty = 0
    per_measurement: dict[str, Any] = {}
    for index, mid in enumerate(ids, 1):
        # A cheap bounded probe first: long-lived public measurements often have
        # no data in the requested window, and a full read timeout per dead id
        # is what makes collection appear to hang. ``skip_empty`` bypasses the
        # probe for measurement ids the caller supplied explicitly.
        if not skip_empty and not measurement_has_recent_results(
                session, mid, start, now, limiter=limiter, timeout=probe_timeout):
            measurements_empty += 1
            per_measurement[str(mid)] = {"observations": 0, "skipped": "no results in window"}
            log(f"[{index}/{len(ids)}] measurement {mid}: no results in window, skipped")
            continue
        log(f"Fetching measurement {mid} ({index}/{len(ids)})")
        fetched = 0
        try:
            payload = get_json(
                session,
                f"{API}/measurements/{mid}/results/",
                {"start": start, "stop": now, "format": "json"},
                limiter=limiter,
            )
            records = payload.get("results", []) if isinstance(payload, dict) else payload
            if not isinstance(records, list):
                records = []
            if len(records) > max_results:
                # Evenly spaced subsampling over the returned period rather than
                # truncating one end of the time series.
                take = np.linspace(0, len(records) - 1, max_results, dtype=int)
                records = [records[i] for i in take]
            for row in records:
                if not isinstance(row, dict):
                    dropped_malformed += 1
                    continue
                try:
                    ts = int(row["timestamp"])
                    probe_id = int(row["prb_id"])
                except (KeyError, TypeError, ValueError):
                    dropped_malformed += 1
                    continue
                if not (start <= ts <= now):
                    dropped_malformed += 1
                    continue
                all_rows.append({
                    "source": SOURCE_NAME,
                    "measurement_id": mid,
                    "probe_id": probe_id,
                    "timestamp": ts,
                    "rtt_ms": row_rtt_ms(row),
                })
                fetched += 1
        except Exception as exc:  # noqa: BLE001 - one bad measurement must not abort the run
            log(f"Skipping measurement {mid}: {exc}")
        per_measurement[str(mid)] = {"observations": fetched}
        limiter.wait()

    unique: dict[tuple, dict] = {}
    for row in all_rows:
        unique[(row["source"], row["measurement_id"], row["probe_id"], row["timestamp"])] = row
    rows = sorted(unique.values(),
                  key=lambda r: (r["measurement_id"], r["probe_id"], r["timestamp"]))

    intervals = _interval_summary(rows)
    provenance = {
        "source": SOURCE_NAME,
        "api": API,
        "requested_days": days,
        "window_start_epoch": start,
        "window_stop_epoch": now,
        "requested_measurements": len(ids),
        "measurement_ids": ids,
        "measurements": per_measurement,
        "measurements_with_no_recent_results": measurements_empty,
        "raw_rows_before_dedup": len(all_rows),
        "unique_rows": len(rows),
        "dropped_malformed_rows": dropped_malformed,
        "missing_rtt_rows": int(sum(1 for r in rows if r["rtt_ms"] is None)),
        "distinct_probes": len({r["probe_id"] for r in rows}),
        "interval_seconds": intervals,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "None RTT means no successful reply and is retained as a loss observation.",
            "Public ping data has no DNS/TCP/TLS fields and no spoofing labels.",
        ],
    }
    log(f"Downloaded {len(rows):,} unique ping observations "
        f"({provenance['missing_rtt_rows']:,} loss observations).")
    return rows, provenance


def _interval_summary(rows: list[dict]) -> dict[str, Any]:
    per_series: dict[tuple, list[int]] = defaultdict(list)
    for row in rows:
        per_series[(row["measurement_id"], row["probe_id"])].append(int(row["timestamp"]))
    deltas: list[float] = []
    for timestamps in per_series.values():
        timestamps.sort()
        diffs = np.diff(timestamps)
        diffs = diffs[diffs > 0]
        if diffs.size:
            deltas.extend(diffs.astype(float).tolist())
    if not deltas:
        return {"count": 0}
    arr = np.asarray(deltas)
    return {
        "count": int(arr.size),
        "median_seconds": float(np.median(arr)),
        "p10_seconds": float(np.quantile(arr, 0.1)),
        "p90_seconds": float(np.quantile(arr, 0.9)),
        "note": "Intervals vary per measurement; horizons are reported in both steps and seconds.",
    }


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
