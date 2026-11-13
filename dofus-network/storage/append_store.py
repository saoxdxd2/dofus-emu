"""Append-only SQLite WAL store for live trajectories.

What was wrong with ``.jsonl``
------------------------------
``trajectories/live_policy_decisions.jsonl`` is 30.6 MB and
``dofus-network/data/**/*.jsonl`` totals 243.5 MB. Appending is O(1) but every
reader is O(file): ``live_learn`` and the trainers open the whole thing, parse each
line, and throw away most rows. A hard process kill lands mid-line, so the tail is a
half-record and every naive ``json.loads(line)`` reader loses it silently (the
project's own ``jsonio.iter_rows`` exists because 108 such lines were measured).
And because it is text against a file, there is no way to delete an old window
without rewriting megabytes.

What this provides
------------------
* **WAL mode** -- readers do not block the writer, and a killed process leaves a
  recoverable journal instead of a torn text tail. ``synchronous=NORMAL`` is the
  WAL-appropriate setting: durable across a process crash, which is the failure this
  layer exists to survive.
* **typed rows** -- ``(seq, ts, kind, body)`` with ``seq`` monotonic. A reader asks
  for ``seq > 12_000 AND kind = 'step'`` and SQLite serves it from an index, touching
  only those pages.
* **retention** -- ``retain(max_rows, max_bytes)`` deletes the oldest window in one
  statement; the file is reused rather than rewritten.
* **torn-write immunity** -- a body is written inside a transaction. There is no
  half a row: either the transaction is in the WAL or it is not.

The body is MessagePack by default (compact, and the sharder reads the same bytes).
``format="json"`` is available for a store an operator wants to eyeball with
``sqlite3``; the format is recorded in ``meta`` so readers never have to guess.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from storage import msgpack_lite

__all__ = ["AppendStore", "StoreStats", "open_store"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    seq  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   REAL    NOT NULL,
    kind TEXT    NOT NULL DEFAULT '',
    body BLOB    NOT NULL
);
CREATE INDEX IF NOT EXISTS records_kind_seq ON records(kind, seq);
CREATE INDEX IF NOT EXISTS records_ts ON records(ts);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class StoreStats:
    path: str
    rows: int
    first_seq: Optional[int]
    last_seq: Optional[int]
    bytes: int
    journal_mode: str
    format: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path, "rows": self.rows, "first_seq": self.first_seq,
            "last_seq": self.last_seq, "bytes": self.bytes,
            "journal_mode": self.journal_mode, "format": self.format,
        }


class AppendStore:
    """One writer-safe SQLite append log. Thread-safe through an internal lock.

    Sharing a connection across threads is safe here because every statement runs
    under ``_lock``; ``check_same_thread=False`` only tells sqlite3 that the object
    will be used from more than one thread. The alternative -- a connection per
    thread -- would multiply WAL bookkeeping for a single-process logger.
    """

    def __init__(self, path: os.PathLike[str] | str, payload_format: str = "msgpack",
                 durable: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.payload_format = payload_format if payload_format in ("msgpack", "json") else "msgpack"
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(str(self.path), timeout=30.0,
                                     isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=%s" % ("NORMAL" if durable else "OFF"))
        self._conn.execute("PRAGMA wal_autocheckpoint=1000")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_SCHEMA)
        stored = self._meta_get("payload_format")
        if stored is None:
            self._meta_set("payload_format", self.payload_format)
        else:
            self.payload_format = stored
        self._meta_set("created_at", self._meta_get("created_at") or str(time.time()))

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def __enter__(self) -> "AppendStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- writing ------------------------------------------------------------

    def append(self, record: Dict[str, Any], kind: str = "", ts: Optional[float] = None) -> int:
        """Append one record and return its sequence number."""
        body = self._encode(record)
        stamp = float(record.get("time", record.get("ts", time.time())) if ts is None else ts)
        with self._lock:
            self._require_open()
            cursor = self._conn.execute(
                "INSERT INTO records(ts, kind, body) VALUES (?, ?, ?)",
                (stamp, kind or str(record.get("kind") or ""), sqlite3.Binary(body)))
            return int(cursor.lastrowid)

    def append_many(self, records: Sequence[Dict[str, Any]], kind: str = "") -> int:
        """Append a batch in one transaction: all rows or none, never a torn tail."""
        if not records:
            return 0
        rows = []
        for record in records:
            stamp = float(record.get("time", record.get("ts", time.time())))
            rows.append((stamp, kind or str(record.get("kind") or ""),
                         sqlite3.Binary(self._encode(record))))
        with self._lock:
            self._require_open()
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.executemany(
                    "INSERT INTO records(ts, kind, body) VALUES (?, ?, ?)", rows)
                self._conn.execute("COMMIT")
            except sqlite3.Error:
                self._conn.execute("ROLLBACK")
                raise
        return len(rows)

    # -- reading ------------------------------------------------------------

    def iter_records(self, start_seq: Optional[int] = None, stop_seq: Optional[int] = None,
                     kind: Optional[str] = None, limit: Optional[int] = None,
                     raw: bool = False) -> Iterator[Any]:
        """Records in seq order. ``raw=True`` yields ``(seq, ts, kind, memoryview)``.

        The memoryview is a zero-copy window on the bytes SQLite returned; decoding
        is the caller's choice, which is what makes a slice query cheap.
        """
        sql = "SELECT seq, ts, kind, body FROM records"
        clauses: List[str] = []
        params: List[Any] = []
        if start_seq is not None:
            clauses.append("seq >= ?")
            params.append(int(start_seq))
        if stop_seq is not None:
            clauses.append("seq < ?")
            params.append(int(stop_seq))
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY seq"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._lock:
            self._require_open()
            cursor = self._conn.execute(sql, params)
            for seq, ts, row_kind, body in cursor:
                if raw:
                    yield seq, ts, row_kind, memoryview(body)
                else:
                    yield seq, ts, row_kind, self._decode(body)

    def slice(self, start_seq: int, stop_seq: int, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """Decoded records in ``[start_seq, stop_seq)`` -- the trainer's read."""
        return [record for _seq, _ts, _kind, record in
                self.iter_records(start_seq, stop_seq, kind=kind)]

    def rows_since(self, ts: float, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT body FROM records WHERE ts >= ?"
        params: List[Any] = [float(ts)]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY seq"
        with self._lock:
            self._require_open()
            return [self._decode(body) for (body,) in self._conn.execute(sql, params)]

    # -- retention ----------------------------------------------------------

    def retain(self, max_rows: Optional[int] = None, max_bytes: Optional[int] = None,
               min_seq: Optional[int] = None) -> int:
        """Drop the oldest window until the store fits. Returns rows deleted.

        This is the ring buffer's tail trim: one DELETE, no rewrite of the survivors.
        A WAL checkpoint follows so the file itself shrinks instead of only the table.
        """
        if max_rows is None and max_bytes is None and min_seq is None:
            return 0
        deleted = 0
        with self._lock:
            self._require_open()
            if min_seq is not None:
                cursor = self._conn.execute("DELETE FROM records WHERE seq < ?", (int(min_seq),))
                deleted += cursor.rowcount or 0
            if max_rows is not None and max_rows >= 0:
                keep_from = self._seq_at_offset(int(max_rows))
                if keep_from is not None:
                    cursor = self._conn.execute("DELETE FROM records WHERE seq < ?", (keep_from,))
                    deleted += cursor.rowcount or 0
            if max_bytes is not None and max_bytes > 0:
                while self._file_bytes() > max_bytes:
                    batch = self._seq_after(256)
                    if batch is None:
                        break
                    cursor = self._conn.execute("DELETE FROM records WHERE seq < ?", (batch,))
                    removed = cursor.rowcount or 0
                    deleted += removed
                    if removed == 0:
                        break
            if deleted:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except sqlite3.Error:
                    pass
        return deleted

    def _seq_at_offset(self, keep: int) -> Optional[int]:
        """First seq of the last ``keep`` rows, or None when the table is smaller."""
        total = self.count()
        if total <= keep:
            return None
        cursor = self._conn.execute("SELECT seq FROM records ORDER BY seq DESC LIMIT 1 OFFSET ?",
                                    (max(0, keep - 1),))
        row = cursor.fetchone()
        return int(row[0]) if row else None

    def _seq_after(self, count: int) -> Optional[int]:
        cursor = self._conn.execute("SELECT seq FROM records ORDER BY seq LIMIT 1 OFFSET ?",
                                    (max(0, count),))
        row = cursor.fetchone()
        return int(row[0]) if row else None

    # -- stats --------------------------------------------------------------

    def count(self, kind: Optional[str] = None) -> int:
        with self._lock:
            self._require_open()
            if kind is None:
                row = self._conn.execute("SELECT COUNT(*) FROM records").fetchone()
            else:
                row = self._conn.execute("SELECT COUNT(*) FROM records WHERE kind = ?",
                                         (kind,)).fetchone()
            return int(row[0])

    def bounds(self) -> Tuple[Optional[int], Optional[int]]:
        with self._lock:
            self._require_open()
            row = self._conn.execute("SELECT MIN(seq), MAX(seq) FROM records").fetchone()
            return (int(row[0]) if row[0] is not None else None,
                    int(row[1]) if row[1] is not None else None)

    def stats(self) -> StoreStats:
        first, last = self.bounds()
        with self._lock:
            mode = self._conn.execute("PRAGMA journal_mode").fetchone()[0]
        return StoreStats(path=str(self.path), rows=self.count(), first_seq=first,
                          last_seq=last, bytes=self._file_bytes(),
                          journal_mode=str(mode).lower(), format=self.payload_format)

    def recover(self) -> List[str]:
        """Check what a killed process left behind. Returns human-readable findings."""
        findings: List[str] = []
        with self._lock:
            self._require_open()
            integrity = self._conn.execute("PRAGMA integrity_check").fetchone()[0]
            findings.append("integrity_check: %s" % integrity)
            try:
                busy, _log, checkpointed = self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                findings.append("wal_checkpoint: busy=%s checkpointed=%s" % (busy, checkpointed))
            except sqlite3.Error as exc:
                findings.append("wal_checkpoint failed: %s" % exc)
        return findings

    def checkpoint(self) -> None:
        with self._lock:
            self._require_open()
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # -- encoding -----------------------------------------------------------

    def _encode(self, record: Dict[str, Any]) -> bytes:
        if self.payload_format == "json":
            import json
            return json.dumps(record, ensure_ascii=False, default=str).encode("utf-8")
        return msgpack_lite.pack(record)

    def _decode(self, body: Any) -> Dict[str, Any]:
        raw = bytes(body)
        if self.payload_format == "json":
            import json
            try:
                value = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                return {}
        else:
            try:
                value = msgpack_lite.unpackb(raw)
            except (msgpack_lite.MsgpackError, ValueError):
                return {}
        return value if isinstance(value, dict) else {}

    # -- meta ---------------------------------------------------------------

    def _meta_get(self, key: str) -> Optional[str]:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else None

    def _meta_set(self, key: str, value: str) -> None:
        self._conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    def _file_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += Path(str(self.path) + suffix).stat().st_size
            except OSError:
                pass
        return total

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("store is closed: %s" % self.path)


def open_store(path: os.PathLike[str] | str, payload_format: str = "msgpack") -> AppendStore:
    return AppendStore(path, payload_format=payload_format)
