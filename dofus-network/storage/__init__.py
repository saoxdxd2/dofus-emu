"""Crash-safe trajectory storage: SQLite WAL now, compressed shards later.

    from storage import AppendStore, shard_from_store, iter_dicts

    with AppendStore("trajectories/live.db") as store:
        store.append({"kind": "step", "tool": "nav.goto"}, kind="step")
        store.retain(max_rows=200_000, max_bytes=512 * 1024 * 1024)
        shard_from_store(store, "trajectories/shards", kind="step",
                         retention_keep=8)

Readers never care which form a path is in:

    for row in iter_dicts("trajectories/live.db"):
        ...
"""

from __future__ import annotations

from storage.append_store import AppendStore, StoreStats, open_store
from storage.rows import describe, is_shard, is_store, iter_dicts, load_dicts
from storage.sharder import (
    available_codec,
    enforce_retention,
    iter_shard,
    read_index,
    read_slice,
    shard_from_store,
    verify_shard,
    write_shard,
)

__all__ = [
    "AppendStore", "StoreStats", "open_store",
    "iter_dicts", "load_dicts", "describe", "is_store", "is_shard",
    "write_shard", "read_slice", "read_index", "iter_shard",
    "shard_from_store", "enforce_retention", "verify_shard", "available_codec",
]
