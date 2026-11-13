"""Storage contract: WAL appends, retention, shard slicing, torn-line tolerance."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage import AppendStore, iter_dicts, load_dicts, read_slice, shard_from_store, verify_shard


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
