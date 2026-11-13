"""Storage contract: WAL appends, retention, shard slicing, torn-line tolerance."""

from __future__ import annotations

import json
import math
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage import AppendStore, iter_dicts, load_dicts, read_slice, shard_from_store, verify_shard
from storage import msgpack_lite
from storage.sharder import write_shard, read_index, iter_slice

__all__ = ["TestMsgpackLiteBoundaries", "TestSharderOffsetSlicing",
           "TestConcurrentWAL", "TestStorage"]


def _records(count: int, start: int = 0):
    return [{"kind": "step", "index": i, "tool": "nav.goto", "payload": "x" * 40}
            for i in range(start, start + count)]


def test_append_store_is_wal_and_counts(tmp_path: Path) -> None:
    path = tmp_path / "live.db"
    with AppendStore(path) as store:
        assert store.stats().journal_mode == "wal"
        for record in _records(25):
            store.append(record, kind="step")
        assert store.count() == 25
        assert store.count("step") == 25
        first, last = store.bounds()
        assert first == 1 and last == 25


def test_batch_is_all_or_nothing(tmp_path: Path) -> None:
    path = tmp_path / "live.db"
    with AppendStore(path) as store:
        store.append_many(_records(10), kind="step")
        assert store.count() == 10
        # A second handle sees the committed rows, which is the crash-recovery claim.
        with AppendStore(path) as second:
            assert second.count() == 10
            assert second.recover()[0].startswith("integrity_check: ok")


def test_slice_and_retention(tmp_path: Path) -> None:
    path = tmp_path / "live.db"
    with AppendStore(path) as store:
        store.append_many(_records(100), kind="step")
        window = store.slice(11, 21)
        assert [row["index"] for row in window] == list(range(10, 20)), window
        deleted = store.retain(max_rows=40)
        assert deleted == 60
        assert store.count() == 40
        remaining = [row["index"] for row in store.slice(0, 10_000)]
        assert remaining == list(range(60, 100))


def test_shard_roundtrip_and_zero_copy_slice(tmp_path: Path) -> None:
    path = tmp_path / "live.db"
    shard_dir = tmp_path / "shards"
    with AppendStore(path) as store:
        store.append_many(_records(1200), kind="step")
        info = shard_from_store(store, shard_dir, kind="step", block_records=100)
    assert info["records"] == 1200
    shard = Path(info["path"])
    report = verify_shard(shard)
    assert report["ok"], report
    # Shard coordinates are store sequence numbers (1-based), not list indexes:
    # joining a shard back to the WAL needs the same key on both sides.
    first = int(info["first_seq"])
    sliced = read_slice(shard, first + 500, first + 505)
    assert [row["index"] for row in sliced] == [500, 501, 502, 503, 504], sliced


def test_rows_reads_every_form_and_drops_a_torn_tail(tmp_path: Path) -> None:
    jsonl = tmp_path / "rows.jsonl"
    jsonl.write_text('{"index": 0}\n{"index": 1}\n{"index": 2, "payload": "trunc',
                     encoding="utf-8")
    assert [row["index"] for row in iter_dicts(jsonl)] == [0, 1]

    db = tmp_path / "rows.db"
    with AppendStore(db) as store:
        store.append_many([{"index": 7}, {"index": 8}])
    assert [row["index"] for row in iter_dicts(db)] == [7, 8]
    assert [row["index"] for row in load_dicts(db, limit=1)] == [7]


def test_retention_deletes_old_shards(tmp_path: Path) -> None:
    db = tmp_path / "rows.db"
    out = tmp_path / "shards"
    with AppendStore(db) as store:
        for batch in range(4):
            store.append_many(_records(10, start=batch * 10))
            shard_from_store(store, out, block_records=5, retention_keep=2)
    kept = sorted(p.name for p in out.glob("*.shard"))
    assert len(kept) == 2, kept


_INT_BOUNDARY_VALUES = [
    0, 1, 127, 128, 255, 256, 65535, 65536,
    -1, -32, -33, -128, -129,
    2**31 - 1, 2**31, -(2**31),
    2**63 - 1, -(2**63),
]

_UINT_BOUNDARY_VALUES = [
    0, 1, 255, 256, 65535, 65536, 2**32 - 1, 2**32,
    2**64 - 1,
]

_FLOAT_SPECIALS = [float("nan"), float("inf"), float("-inf"), 0.0, -0.0, 1.5, -3.25]


def _roundtrip(value):
    packed = msgpack_lite.pack(value)
    value2, offset = msgpack_lite.unpack_from(packed, 0)
    assert offset == len(packed), "unpack_from did not consume full buffer"
    return value2


class TestMsgpackLiteBoundaries(unittest.TestCase):
    def test_positive_fixint(self):
        for v in range(0, 0x80):
            self.assertEqual(_roundtrip(v), v)

    def test_negative_fixint(self):
        for v in range(-32, 0):
            self.assertEqual(_roundtrip(v), v)

    def test_uint_boundaries(self):
        for v in _UINT_BOUNDARY_VALUES:
            self.assertEqual(_roundtrip(v), v, "uint %d" % v)

    def test_int_boundaries(self):
        for v in _INT_BOUNDARY_VALUES:
            self.assertEqual(_roundtrip(v), v, "int %d" % v)

    def test_int64_max_and_min(self):
        self.assertEqual(_roundtrip(2**63 - 1), 2**63 - 1)
        self.assertEqual(_roundtrip(-(2**63)), -(2**63))

    def test_uint64_max(self):
        self.assertEqual(_roundtrip(2**64 - 1), 2**64 - 1)

    def test_float_specials(self):
        for v in _FLOAT_SPECIALS:
            rt = _roundtrip(v)
            if math.isnan(v):
                self.assertTrue(math.isnan(rt), "NaN roundtrip failed")
            elif math.isinf(v):
                self.assertEqual(rt, v, "Inf roundtrip failed: %r" % v)
            else:
                self.assertEqual(rt, v, "float roundtrip failed: %r" % v)

    def test_fixstr_boundary(self):
        s31 = "a" * 31
        s32 = "a" * 32
        self.assertEqual(_roundtrip(s31), s31)
        self.assertEqual(_roundtrip(s32), s32)

    def test_str16_boundary(self):
        s255 = "a" * 255
        s256 = "a" * 256
        s65535 = "a" * 65535
        self.assertEqual(_roundtrip(s255), s255)
        self.assertEqual(_roundtrip(s256), s256)
        self.assertEqual(_roundtrip(s65535), s65535)

    def test_str32_over_65536(self):
        n = 65536 + 100
        s = "a" * n
        self.assertEqual(_roundtrip(s), s)

    def test_array_boundaries(self):
        for n in [0, 1, 15, 16, 65535, 65536, 65536 + 1]:
            arr = [None] * n
            self.assertEqual(_roundtrip(arr), arr, "array of %d" % n)

    def test_array_of_ints_at_boundary(self):
        for n in [15, 16, 65535, 65536]:
            arr = list(range(n))
            self.assertEqual(_roundtrip(arr), arr, "int array of %d" % n)

    def test_map_boundaries(self):
        for n in [0, 1, 15, 16, 65535, 65536]:
            mp = {"k%d" % i: i for i in range(n)}
            self.assertEqual(_roundtrip(mp), mp, "map of %d" % n)

    def test_map_over_65536(self):
        n = 65536 + 10
        mp = {"k%d" % i: i for i in range(n)}
        self.assertEqual(_roundtrip(mp), mp, "map of %d" % n)

    def test_nested_boundary(self):
        inner = list(range(16))
        outer = [inner] * 16
        self.assertEqual(_roundtrip(outer), outer)

    def test_unpack_from_offset_slicing(self):
        buf = bytearray()
        offs = []
        for v in [0, 127, -32, 255, 65535, 2**31 - 1, -(2**63), "hello", 3.14]:
            offs.append(len(buf))
            msgpack_lite._write(buf, v)
        view = memoryview(bytes(buf))
        for i, v in enumerate([0, 127, -32, 255, 65535, 2**31 - 1, -(2**63), "hello", 3.14]):
            got, next_off = msgpack_lite.unpack_from(view, offs[i])
            self.assertEqual(got, v, "offset %d value %r" % (offs[i], v))
            expected_next = offs[i + 1] if i + 1 < len(offs) else len(buf)
            self.assertEqual(next_off, expected_next,
                             "offset %d next_off %d != expected %d" % (offs[i], next_off, expected_next))

    def test_compacted_shard_offset_slicing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            shard_dir = Path(tmp)
            records = [{"index": i, "kind": "step", "payload": "x" * (i % 50)}
                       for i in range(1000)]
            path = shard_dir / "test.shard"
            index = write_shard(path, records, block_records=1, codec="zlib")
            first = int(index["first_seq"])
            read_back = read_slice(path, first, first + 1000)
            self.assertEqual(len(read_back), 1000)
            for i, rec in enumerate(read_back):
                self.assertEqual(rec["index"], i, "record %d" % i)
                self.assertEqual(rec["kind"], "step")
                self.assertEqual(rec["payload"], "x" * (i % 50))
            mid = read_slice(path, first + 400, first + 410)
            self.assertEqual([r["index"] for r in mid], list(range(400, 410)))


# ---------------------------------------------------------------------------
# Concurrent WAL test
# ---------------------------------------------------------------------------

class TestConcurrentWAL(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "live.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_concurrent_readers_single_writer(self):
        errors = []
        stop = threading.Event()

        # One shared AppendStore instance across threads: the class documents
        # that sharing a connection is safe because every statement runs under
        # its internal lock.
        store = AppendStore(self.db_path)

        def writer():
            i = 0
            while not stop.is_set():
                try:
                    store.append({"index": i, "kind": "step"}, kind="step")
                    i += 1
                except Exception as exc:
                    errors.append(("writer", exc))
                    break

        def reader():
            while not stop.is_set():
                try:
                    _ = store.count()
                    _ = store.bounds()
                except Exception as exc:
                    errors.append(("reader", exc))
                    break

        threads = [threading.Thread(target=writer)]
        for _ in range(3):
            threads.append(threading.Thread(target=reader))
        for t in threads:
            t.start()
        import time as _time
        _time.sleep(0.5)
        stop.set()
        for t in threads:
            t.join(timeout=10)
        store.close()
        self.assertFalse(errors, errors)
        # After the writer stops, the store should be readable and consistent.
        with AppendStore(self.db_path) as store2:
            count = store2.count()
            self.assertGreater(count, 0)
            first, last = store2.bounds()
            self.assertEqual(last - first + 1, count)


class TestStorage(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_append_store_is_wal_and_counts(self) -> None:
        test_append_store_is_wal_and_counts(self.tmp_path)

    def test_batch_is_all_or_nothing(self) -> None:
        test_batch_is_all_or_nothing(self.tmp_path)

    def test_slice_and_retention(self) -> None:
        test_slice_and_retention(self.tmp_path)

    def test_shard_roundtrip_and_zero_copy_slice(self) -> None:
        test_shard_roundtrip_and_zero_copy_slice(self.tmp_path)

    def test_rows_reads_every_form_and_drops_a_torn_tail(self) -> None:
        test_rows_reads_every_form_and_drops_a_torn_tail(self.tmp_path)

    def test_retention_deletes_old_shards(self) -> None:
        test_retention_deletes_old_shards(self.tmp_path)
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_append_store_is_wal_and_counts(self) -> None:
        test_append_store_is_wal_and_counts(self.tmp_path)

    def test_batch_is_all_or_nothing(self) -> None:
        test_batch_is_all_or_nothing(self.tmp_path)

    def test_slice_and_retention(self) -> None:
        test_slice_and_retention(self.tmp_path)

    def test_shard_roundtrip_and_zero_copy_slice(self) -> None:
        test_shard_roundtrip_and_zero_copy_slice(self.tmp_path)

    def test_rows_reads_every_form_and_drops_a_torn_tail(self) -> None:
        test_rows_reads_every_form_and_drops_a_torn_tail(self.tmp_path)

    def test_retention_deletes_old_shards(self) -> None:
        test_retention_deletes_old_shards(self.tmp_path)


def _main() -> int:
    import tempfile

    failures = 0
    names = [name for name in sorted(globals())
             if name.startswith("test_") and callable(globals()[name])]
    with tempfile.TemporaryDirectory() as tmp:
        for name in names:
            # One directory per test: pytest hands out a fresh tmp_path, this runner
            # must too, or a store left by one test becomes the starting state of the
            # next and the failure looks like a storage bug.
            directory = Path(tmp) / name
            directory.mkdir(parents=True, exist_ok=True)
            try:
                globals()[name](directory)
            except AssertionError as exc:
                failures += 1
                print("FAIL %s: %s" % (name, exc))
            else:
                print("ok   %s" % name)
    print("%d storage checks, %d failed" % (len(names), failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
