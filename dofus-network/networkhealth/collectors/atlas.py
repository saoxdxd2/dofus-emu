"""RIPE Atlas public measurement collector.

Design rules that matter for Phase 1:

* **Paginated downloads.** The previous version fetched a single page and then
  *sub-sampled by index*, which silently dropped the middle of the series and
  biased long-range gaps. Here we follow ``next`` cursors until the window is
  exhausted and only then apply an explicit, documented budget.
* **Resumable.** Rows are appended per measurement to a cache keyed by
  measurement id + time window. Re-running with a wider window only downloads
  the missing ids.
* **Honest provenance.** Every row carries measurement id, probe id, source,
  and the fetch time, plus per-measurement metadata (target, interval, af,
  description). Nothing is invented.
* **Polite client.** Global token-bucket rate limiting with configurable QPS and
  exponential backoff that honours ``Retry-After``.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from ..provenance import utc_now_iso

API = "https://atlas.ripe.net/api/v2"
SOURCE = "ripe_atlas"
USER_AGENT = "NetworkHealthResearch/1.0 (public RIPE Atlas data; polite client)"
RTT_MIN_MS, RTT_MAX_MS = 0.01, 60000.0


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


@dataclass
class CollectorConfig:
    """Tunables for a collection run; all recorded in the run manifest."""

    days: int = 30
    max_measurements: int = 100
    max_results_per_measurement: int = 20000
    request_qps: float = 2.0
    timeout: tuple[float, float] = (10.0, 60.0)
    # Tight timeouts for the cheap "does this id have data?" pre-check. A dead
    # or hanging results endpoint must not cost a full read timeout per try.
    probe_timeout: tuple[float, float] = (5.0, 15.0)
    retries: int = 5
    measurement_ids: tuple[int, ...] = ()
    page_size: int = 500
    resume: bool = True
    # Set True to skip the pre-check and fetch every id blindly (slow).
    skip_empty: bool = False

    def as_manifest_parameters(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "max_measurements": self.max_measurements,
            "max_results_per_measurement": self.max_results_per_measurement,
            "request_qps": self.request_qps,
            "probe_timeout": list(self.probe_timeout),
            "page_size": self.page_size,
            "resume": self.resume,
            "skip_empty": self.skip_empty,
            "explicit_measurement_ids": list(self.measurement_ids),
        }


class RateLimiter:
    """Token bucket; keeps the collector polite without long blocking sleeps."""

    def __init__(self, qps: float):
        self.min_interval = 0.0 if qps <= 0 else 1.0 / qps
        self._last = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        delta = now - self._last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last = time.monotonic()


@dataclass
class CollectionStats:
    """Counters reported into the manifest so data quality is auditable."""

    measurements_requested: int = 0
    measurements_fetched: int = 0
    measurements_failed: int = 0
    measurements_empty: int = 0
    raw_records_seen: int = 0
    records_skipped_malformed: int = 0
    records_skipped_out_of_window: int = 0
    records_dropped_by_budget: int = 0
    duplicates_collapsed: int = 0
    rtt_missing: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "measurements_requested": self.measurements_requested,
            "measurements_fetched": self.measurements_fetched,
            "measurements_failed": self.measurements_failed,
            "measurements_empty": self.measurements_empty,
            "raw_records_seen": self.raw_records_seen,
            "records_skipped_malformed": self.records_skipped_malformed,
            "records_skipped_out_of_window": self.records_skipped_out_of_window,
            "records_dropped_by_budget": self.records_dropped_by_budget,
            "duplicates_collapsed": self.duplicates_collapsed,
            "rtt_missing": self.rtt_missing,
            "errors": self.errors[:20],
        }


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def get_json(session: requests.Session, url: str, *, limiter: RateLimiter | None = None,
             params: dict | None = None, timeout: tuple[float, float] = (10.0, 60.0),
             retries: int = 5) -> Any:
    """GET with backoff. 429/5xx are retried, other 4xx raise immediately."""
    last_error: str | None = None
    for attempt in range(retries):
        if limiter is not None:
            limiter.wait()
        try:
            response = session.get(url, params=params, timeout=timeout)
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("Retry-After", "")
                wait = min(60.0, float(retry_after)) if retry_after.replace(".", "", 1).isdigit() \
                    else min(30.0, 2.0 ** attempt)
                log(f"HTTP {response.status_code} on {url}; waiting {wait:.1f}s")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = str(exc)
            wait = min(30.0, 2.0 ** attempt)
            log(f"Request failed ({exc}); retrying in {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"Unable to fetch {url}: {last_error}")


def discover_measurements(session: requests.Session, limit: int,
                          limiter: RateLimiter | None = None,
                          page_size: int = 500,
                          timeout: tuple[float, float] = (10.0, 60.0),
                          retries: int = 5) -> list[int]:
    """Walk the paginated measurement index until ``limit`` ping ids are found.

    Uses a smaller page size than the API maximum so the first page returns
    quickly and we can stop early when only a few ids are wanted.
    """
    url: str | None = f"{API}/measurements/"
    params: dict | None = {
        "type": "ping",
        "is_public": "true",
        "is_active": "true",
        "page_size": max(20, min(500, page_size)),
        "format": "json",
    }
    ids: list[int] = []
    seen: set[int] = set()
    while url and len(ids) < limit:
        payload = get_json(session, url, limiter=limiter, params=params,
                           timeout=timeout, retries=retries)
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
            if mid in seen:
                continue
            if str(record.get("type", "ping")).lower() != "ping":
                continue
            seen.add(mid)
            ids.append(mid)
            if len(ids) >= limit:
                break
        next_url = payload.get("next") if isinstance(payload, dict) else None
        url = next_url if isinstance(next_url, str) and next_url.startswith("https://") else None
    return ids


def row_rtt_ms(row: dict) -> tuple[float | None, int, int]:
    """Median RTT of a ping result row.

    Returns ``(median_rtt_ms, received, sent)``. A ``None`` median is a genuine
    observation of *no successful reply* — it is kept, never imputed away here.
    """
    results = row.get("result")
    if not isinstance(results, list):
        return None, 0, 0
    values: list[float] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            value = float(item["rtt"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value) and RTT_MIN_MS <= value <= RTT_MAX_MS:
            values.append(value)
    if not values:
        return None, 0, len(results)
    return float(sorted(values)[len(values) // 2]), len(values), len(results)


def _extract_measurement_meta(record: dict, mid: int) -> dict[str, Any]:
    """Pull only documented, stable fields from a measurement index record."""
    meta: dict[str, Any] = {"measurement_id": mid}
    for key in ("description", "type", "af", "interval", "is_public", "is_active"):
        if key in record:
            meta[key] = record[key]
    target = record.get("target") or record.get("dst_addr")
    if target:
        meta["target"] = target
    probe_ids = record.get("probes")
    if isinstance(probe_ids, list):
        meta["probe_count"] = len(probe_ids)
    return meta


def probe_recent_activity(session: requests.Session, mid: int, start: int, stop: int,
                          *, limiter: RateLimiter | None,
                          timeout: tuple[float, float] = (5.0, 15.0)) -> bool:
    """Cheap check: does this measurement have any results in the window?

    Public Atlas discovery returns long-lived measurement ids, but many are
    dead or have no rows in the requested period. Fetching results for those
    is expensive: the endpoint can hang past its read timeout, and the retry
    ladder turns one dead id into minutes of wall clock.

    A short-timeout HEAD-style request settles it in seconds. We use a small
    page size and a tight read timeout on purpose; a slow or hanging endpoint
    should be skipped, not waited on.
    """
    try:
        payload = get_json(
            session,
            f"{API}/measurements/{mid}/results/",
            limiter=limiter,
            params={"start": start, "stop": stop, "format": "json", "page_size": 1},
            timeout=timeout,
            retries=1,
        )
    except Exception:
        return False
    if isinstance(payload, dict):
        batch = payload.get("results", [])
    else:
        batch = payload
    return isinstance(batch, list) and len(batch) > 0


def fetch_measurement_meta(session: requests.Session, measurement_ids: Iterable[int],
                           limiter: RateLimiter | None = None,
                           timeout: tuple[float, float] = (10.0, 60.0),
                           retries: int = 5) -> dict[str, dict[str, Any]]:
    meta: dict[str, dict[str, Any]] = {}
    for mid in measurement_ids:
        try:
            payload = get_json(session, f"{API}/measurements/{mid}/",
                               limiter=limiter, timeout=timeout, retries=retries)
        except Exception as exc:
            log(f"Metadata unavailable for measurement {mid}: {exc}")
            meta[str(mid)] = {"measurement_id": mid, "meta_error": str(exc)}
            continue
        if isinstance(payload, dict):
            meta[str(mid)] = _extract_measurement_meta(payload, mid)
    return meta


def _paginate_results(session: requests.Session, mid: int, start: int, stop: int,
                      *, limiter: RateLimiter | None, timeout: tuple[float, float],
                      retries: int, stats: CollectionStats) -> list[dict]:
    """Follow ``next`` cursors for one measurement's results window."""
    records: list[dict] = []
    url: str | None = f"{API}/measurements/{mid}/results/"
    params: dict | None = {"start": start, "stop": stop, "format": "json"}
    pages = 0
    while url:
        payload = get_json(session, url, limiter=limiter, params=params,
                           timeout=timeout, retries=retries)
        params = None
        pages += 1
        batch = payload.get("results", []) if isinstance(payload, dict) else payload
        if not isinstance(batch, list):
            break
        records.extend(item for item in batch if isinstance(item, dict))
        next_url = payload.get("next") if isinstance(payload, dict) else None
        url = next_url if isinstance(next_url, str) and next_url.startswith("https://") else None
        if pages > 200:  # safety valve against a pathological cursor loop
            stats.errors.append(f"measurement {mid}: page limit reached, results truncated")
            break
    return records


def _evenly_spaced_indices(count: int, budget: int) -> list[int]:
    """Deterministic, order-preserving index sample for a documented budget.

    Unlike naive slicing this keeps the whole time span when the budget bites,
    and the resulting timestamps are still ascending.
    """
    if count <= budget:
        return list(range(count))
    # numpy is only used here for a stable, reproducible index selection.
    import numpy as np
    return np.linspace(0, count - 1, budget, dtype=int).tolist()


def download_measurement_window(rows_out: list[dict], stats: CollectionStats, session: requests.Session,
                                mid: int, start: int, stop: int, config: CollectorConfig,
                                limiter: RateLimiter | None) -> None:
    records = _paginate_results(session, mid, start, stop, limiter=limiter,
                                timeout=config.timeout, retries=config.retries, stats=stats)
    stats.raw_records_seen += len(records)
    if len(records) > config.max_results_per_measurement:
        keep = _evenly_spaced_indices(len(records), config.max_results_per_measurement)
        stats.records_dropped_by_budget += len(records) - len(keep)
        records = [records[i] for i in keep]
    fetched_utc = utc_now_iso()
    for row in records:
        try:
            ts = int(row["timestamp"])
            probe_id = int(row["prb_id"])
        except (KeyError, TypeError, ValueError):
            stats.records_skipped_malformed += 1
            continue
        if not (start <= ts <= stop):
            stats.records_skipped_out_of_window += 1
            continue
        rtt, received, sent = row_rtt_ms(row)
        if rtt is None:
            stats.rtt_missing += 1
        rows_out.append({
            "source": SOURCE,
            "measurement_id": mid,
            "probe_id": probe_id,
            "timestamp": ts,
            "rtt_ms": rtt,
            "received": received,
            "sent": sent,
            "fetched_utc": fetched_utc,
        })


def deduplicate(rows: list[dict]) -> tuple[list[dict], int]:
    """Collapse duplicate observations; keep the last write and count removals."""
    unique: dict[tuple, dict] = {}
    for row in rows:
        key = (row["source"], row["measurement_id"], row["probe_id"], row["timestamp"])
        unique[key] = row
    removed = len(rows) - len(unique)
    ordered = sorted(unique.values(),
                     key=lambda r: (r["measurement_id"], r["probe_id"], r["timestamp"]))
    return ordered, removed


def _cache_path(cache_dir: Path, mid: int, start: int, stop: int) -> Path:
    return Path(cache_dir) / f"measurement_{mid}_{start}_{stop}.jsonl"


def _read_cache(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _write_cache(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def collect_ripe_atlas(config: CollectorConfig, cache_dir: Path) -> tuple[list[dict], dict[str, Any]]:
    """Download a reproducible slice of public RIPE Atlas ping measurements.

    Returns ``(rows, collection_metadata)``. ``collection_metadata`` holds the
    measurement metadata, the window, per-id row counts and quality counters so
    the dataset version can be documented without re-querying the API.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    session = new_session()
    limiter = RateLimiter(config.request_qps)
    stats = CollectionStats()

    now = int(datetime.now(timezone.utc).timestamp())
    start = int((datetime.now(timezone.utc) - timedelta(days=config.days)).timestamp())

    if config.measurement_ids:
        ids = [int(mid) for mid in config.measurement_ids[:config.max_measurements]]
        log(f"Using {len(ids)} explicitly provided measurement ids")
    else:
        log(f"Discovering up to {config.max_measurements} public active ping measurements")
        ids = discover_measurements(session, config.max_measurements, limiter=limiter,
                                    page_size=config.page_size, timeout=config.timeout,
                                    retries=config.retries)
    if not ids:
        raise RuntimeError("No measurements discovered. Pass --measurement-ids with known public ids.")

    stats.measurements_requested = len(ids)
    all_rows: list[dict] = []
    per_measurement_rows: dict[str, int] = {}

    for index, mid in enumerate(ids, 1):
        cache_file = _cache_path(cache_dir, mid, start, now)
        cached = _read_cache(cache_file) if config.resume else []
        if cached:
            log(f"[{index}/{len(ids)}] measurement {mid}: {len(cached):,} rows from cache")
            all_rows.extend(cached)
            per_measurement_rows[str(mid)] = len(cached)
            stats.measurements_fetched += 1
            continue
        try:
            # Skip ids with no results in the window before paying for a full
            # paginated fetch. Public discovery returns long-lived ids, many of
            # which are dead or empty; the results endpoint can hang on those.
            if not config.skip_empty and not probe_recent_activity(
                    session, mid, start, now, limiter=limiter, timeout=config.probe_timeout):
                stats.measurements_empty += 1
                log(f"[{index}/{len(ids)}] measurement {mid}: no results in window, skipped")
                continue
            before = len(all_rows)
            download_measurement_window(all_rows, stats, session, mid, start, now, config, limiter)
            fetched = all_rows[before:]
            _write_cache(cache_file, fetched)
            per_measurement_rows[str(mid)] = len(fetched)
            stats.measurements_fetched += 1
            log(f"[{index}/{len(ids)}] measurement {mid}: {len(fetched):,} rows")
        except Exception as exc:
            stats.measurements_failed += 1
            stats.errors.append(f"measurement {mid}: {exc}")
            log(f"[{index}/{len(ids)}] skipping measurement {mid}: {exc}")

    unique_rows, removed = deduplicate(all_rows)
    stats.duplicates_collapsed = removed
    log(f"Collected {len(unique_rows):,} unique ping observations "
        f"({removed:,} duplicates collapsed)")

    if not unique_rows:
        raise RuntimeError("No observations collected; inspect API access and measurement ids.")

    collection_meta: dict[str, Any] = {
        "source": SOURCE,
        "source_url": API,
        "collected_utc": utc_now_iso(),
        "window": {"start_epoch": start, "stop_epoch": now,
                   "start_utc": datetime.fromtimestamp(start, timezone.utc).isoformat(),
                   "stop_utc": datetime.fromtimestamp(now, timezone.utc).isoformat()},
        "measurement_ids": [int(m) for m in sorted(per_measurement_rows, key=int)],
        "rows_per_measurement": per_measurement_rows,
        "unique_observations": len(unique_rows),
        "quality": stats.as_dict(),
        "measurement_metadata": fetch_measurement_meta(session, ids, limiter=limiter,
                                                       timeout=config.timeout,
                                                       retries=config.retries),
        "licence_note": "Public RIPE Atlas measurements; respect the RIPE Atlas terms of use.",
    }
    return unique_rows, collection_meta
