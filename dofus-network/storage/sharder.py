"""Shard the WAL store into Zstd-compressed MessagePack blocks.

The store answers "the last N rows" cheaply, but a training run wants a *stable*
window it can read without competing with the live writer, and git wants a file
that does not grow by 30 MB a day. A shard is that stable window:

    DFS1 | zstd(block 0) | zstd(block 1) | ...        (shard file)
    {codec, blocks:[{start, count, offset, length, uncompressed, offsets}]}
                                                       (sidecar .index.json)

Each block is compressed on its own so a slice query decompresses only the blocks
that cover the requested sequence range, and each block carries one byte offset per
record so only the requested records are decoded. That is what makes
``read_slice(shard, 1200, 1210)`` cost ten records instead of the whole shard.

Parquet was the first choice for the columnar win, but the shipped interpreter has
no pyarrow and adding it would put a 40 MB wheel behind a log format. MessagePack is
already the store's body format, so a shard is the same bytes, blocked and indexed --
one encoding, one decoder, no second schema to drift.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from storage import msgpack_lite
from storage.append_store import AppendStore

__all__ = [
    "MAGIC", "write_shard", "read_index", "read_slice", "iter_shard",
    "shard_from_store", "enforce_retention", "verify_shard", "available_codec",
]

MAGIC = b"DFS1"
HEADER = MAGIC + struct.pack(">B", 1)
_SUFFIX = ".shard"
_INDEX_SUFFIX = ".index.json"


def available_codec() -> str:
    """zstd when the interpreter has it, zlib otherwise. Recorded in the index."""
    try:
        import zstandard  # noqa: F401
        return "zstd"
    except ImportError:
        return "zlib"


def _compress(data: bytes, codec: str, level: int = 6) -> bytes:
    if codec == "zstd":
        import zstandard
        return zstandard.ZstdCompressor(level=level).compress(data)
    import zlib
    return zlib.compress(data, level)


def _decompress(data: bytes, codec: str) -> bytes:
    if codec == "zstd":
        import zstandard
        return zstandard.ZstdDecompressor().decompress(data)
    import zlib
    return zlib.decompress(data)


# -- writing ----------------------------------------------------------------

def write_shard(path: Path, records: Sequence[Dict[str, Any]], block_records: int = 512,
                codec: Optional[str] = None, level: int = 6,
                first_seq: int = 0) -> Dict[str, Any]:
    """Write ``records`` as one shard and return its index document."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    codec = codec or available_codec()
    block_records = max(1, int(block_records))

    blocks: List[Dict[str, Any]] = []
    with path.open("wb") as handle:
        handle.write(HEADER)
        for block_start in range(0, len(records), block_records):
            chunk = records[block_start:block_start + block_records]
            offsets: List[int] = []
            cursor = 0
            pieces: List[bytes] = []
            for record in chunk:
                encoded = msgpack_lite.pack(record)
                offsets.append(cursor)
                pieces.append(encoded)
                cursor += len(encoded)
            raw = b"".join(pieces)
            compressed = _compress(raw, codec, level)
            offset = handle.tell()
            handle.write(compressed)
            blocks.append({
                "start": first_seq + block_start,
                "count": len(chunk),
                "offset": offset,
                "length": len(compressed),
                "uncompressed": len(raw),
                "offsets": offsets,
            })

    index = {
        "version": 1,
        "codec": codec,
        "block_records": block_records,
        "records": len(records),
        "first_seq": first_seq,
        "last_seq": first_seq + len(records) - 1 if records else None,
        "blocks": blocks,
    }
    _index_path(path).write_text(json.dumps(index, sort_keys=True), encoding="utf-8")
    return index


def _index_path(shard: Path) -> Path:
    return shard.with_name(shard.name + _INDEX_SUFFIX)


def read_index(shard: Path) -> Dict[str, Any]:
    shard = Path(shard)
    try:
        return json.loads(_index_path(shard).read_text(encoding="utf-8"))
    except OSError:
        raise FileNotFoundError("shard index missing for %s (run shard_from_store again)" % shard)
    except ValueError:
        raise ValueError("shard index is corrupt: %s" % _index_path(shard))


# -- reading ----------------------------------------------------------------

def read_slice(shard: Path, start: int, stop: Optional[int] = None) -> List[Dict[str, Any]]:
    """Decode only records whose sequence number is in ``[start, stop)``."""
    return list(iter_slice(shard, start, stop))


def iter_slice(shard: Path, start: int, stop: Optional[int] = None) -> Iterator[Dict[str, Any]]:
    shard = Path(shard)
    index = read_index(shard)
    first = int(index.get("first_seq") or 0)
    total = int(index.get("records") or 0)
    last = first + total
    stop = last if stop is None else min(int(stop), last)
    start = max(int(start), first)
    if start >= stop:
        return
    codec = index.get("codec") or "zlib"

    with shard.open("rb") as handle:
        header = handle.read(len(HEADER))
        if not header.startswith(MAGIC):
            raise ValueError("not a DofusFarm shard: %s" % shard)
        for block in index.get("blocks") or []:
            block_start = int(block["start"])
            block_end = block_start + int(block["count"])
            if block_end <= start or block_start >= stop:
                continue
            handle.seek(int(block["offset"]))
            raw = _decompress(handle.read(int(block["length"])), codec)
            view = memoryview(raw)
            offsets = block["offsets"]
            for record_index in range(max(start, block_start), min(stop, block_end)):
                local = record_index - block_start
                value, _next = msgpack_lite.unpack_from(view, int(offsets[local]))
                if isinstance(value, dict):
                    yield value


def iter_shard(shard: Path) -> Iterator[Dict[str, Any]]:
    index = read_index(shard)
    first = int(index.get("first_seq") or 0)
    yield from iter_slice(shard, first, first + int(index.get("records") or 0))


# -- store -> shard ---------------------------------------------------------

def shard_from_store(store: AppendStore, out_dir: Path, kind: Optional[str] = None,
                     max_records: Optional[int] = None, block_records: int = 512,
                     codec: Optional[str] = None, name: Optional[str] = None,
                     retention_keep: Optional[int] = None) -> Dict[str, Any]:
    """Snapshot a stable window out of the live store.

    ``retention_keep`` is the ring buffer: when set, shards older than the newest
    ``retention_keep`` are deleted *after* this one is written and fsynced, so a crash
    can duplicate rows into two shards but never lose the only copy.
    """
    records: List[Dict[str, Any]] = []
    for _seq, _ts, _kind, record in store.iter_records(kind=kind, limit=max_records):
        records.append(record)
    first_seq, _last_seq = store.bounds()
    if kind is not None:
        row = store._conn.execute("SELECT MIN(seq) FROM records WHERE kind = ?", (kind,)).fetchone()
        first_seq = int(row[0]) if row and row[0] is not None else 0
    label = name or (kind or "all")
    if not records:
        return {"records": 0, "path": None}
    path = Path(out_dir) / ("%s-%012d-%012d%s" % (label, first_seq, first_seq + len(records) - 1, _SUFFIX))
    index = write_shard(path, records, block_records=block_records, codec=codec,
                        first_seq=first_seq)
    index["path"] = str(path)
    if retention_keep is not None:
        enforce_retention(Path(out_dir), keep=retention_keep)
    return index


def enforce_retention(directory: Path, keep: Optional[int] = None,
                      keep_bytes: Optional[int] = None) -> List[str]:
    """Delete the oldest shards beyond the ring buffer. Returns removed paths."""
    directory = Path(directory)
    shards = sorted(directory.glob("*" + _SUFFIX), key=lambda p: p.name)
    removed: List[str] = []
    if keep is not None and keep >= 0 and len(shards) > keep:
        for shard in shards[:len(shards) - keep]:
            removed.append(str(shard))
            _remove(shard)
    if keep_bytes is not None and keep_bytes > 0:
        total = sum(p.stat().st_size for p in shards if p.exists())
        for shard in shards:
            if total <= keep_bytes:
                break
            if not shard.exists():
                continue
            total -= shard.stat().st_size
            removed.append(str(shard))
            _remove(shard)
    return removed


def _remove(shard: Path) -> None:
    for path in (shard, _index_path(shard)):
        try:
            path.unlink()
        except OSError:
            pass


def verify_shard(shard: Path) -> Dict[str, Any]:
    """Round-trip check: every record decodes and the count matches the index."""
    shard = Path(shard)
    index = read_index(shard)
    seen = 0
    for _record in iter_shard(shard):
        seen += 1
    expected = int(index.get("records") or 0)
    return {"shard": str(shard), "expected": expected, "decoded": seen,
            "ok": seen == expected, "codec": index.get("codec")}
