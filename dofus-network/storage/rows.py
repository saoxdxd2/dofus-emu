"""Read trajectory rows from whichever storage form is on disk.

Callers used to open ``.jsonl`` directly (`ai/train_stacker.load_rows`,
`combat/live_learn`, the calibration tools). That made the storage change a
reader change in every one of them. This module is the single read seam:

    path.jsonl              line-delimited JSON, torn tail tolerated
    path.db / .sqlite       the WAL AppendStore
    path.shard              a compressed shard, whole

so a trainer gains the new formats by calling ``iter_dicts`` instead of ``open``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from agent.jsonio import iter_rows as iter_jsonl_rows
from storage.append_store import AppendStore
from storage import sharder

__all__ = ["is_store", "is_shard", "iter_dicts", "load_dicts", "describe"]

_STORE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_SHARD_SUFFIXES = (".shard",)


def is_store(path: Any) -> bool:
    return Path(path).suffix.lower() in _STORE_SUFFIXES


def is_shard(path: Any) -> bool:
    return Path(path).suffix.lower() in _SHARD_SUFFIXES


def iter_dicts(path: Any, start_seq: Optional[int] = None, stop_seq: Optional[int] = None,
               kind: Optional[str] = None, limit: Optional[int] = None) -> Iterator[Dict[str, Any]]:
    """Every row, from any supported form.

    A missing file yields nothing: readers treat an absent log as "no data yet",
    which is what `jsonio.iter_rows` already did and what the trainers rely on.
    """
    path = Path(path)
    if not path.exists():
        return
    if is_store(path):
        with AppendStore(path) as store:
            first, last = store.bounds()
            if first is None:
                return
            low = first if start_seq is None else max(first, int(start_seq))
            high = (last + 1) if stop_seq is None else min(last + 1, int(stop_seq))
            for _seq, _ts, _kind, record in store.iter_records(low, high, kind=kind, limit=limit):
                yield record
        return
    if is_shard(path):
        emitted = 0
        for record in sharder.iter_shard(path):
            if limit is not None and emitted >= limit:
                return
            emitted += 1
            yield record
        return
    for index, record in enumerate(iter_jsonl_rows(path)):
        if limit is not None and index >= limit:
            return
        if start_seq is not None and index < start_seq:
            continue
        yield record


def load_dicts(path: Any, **kwargs: Any) -> List[Dict[str, Any]]:
    return list(iter_dicts(path, **kwargs))


def describe(path: Any) -> Dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {"path": str(path), "missing": True}
    if is_store(path):
        with AppendStore(path) as store:
            return store.stats().as_dict()
    if is_shard(path):
        return sharder.read_index(path)
    return {"path": str(path), "format": "jsonl", "bytes": path.stat().st_size}
