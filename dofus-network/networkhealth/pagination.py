"""Shared RIPE Atlas measurement-index pagination."""

from __future__ import annotations

from typing import Any, Callable, Optional


def collect_ping_ids(fetch: Callable[[str, Optional[dict]], Any], url: Optional[str],
                     params: Optional[dict], limit: int,
                     log: Optional[Callable[[str], None]] = None) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    while url and len(ids) < limit:
        if log is not None:
            log(f"Querying active measurements index (found {len(ids)}/{limit})...")
        payload = fetch(url, params)
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
