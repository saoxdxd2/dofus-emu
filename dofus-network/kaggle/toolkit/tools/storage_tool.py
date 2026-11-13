#!/usr/bin/env python3
"""Operator CLI for the trajectory store: import, inspect, shard, slice, replay.

    python tools/storage_tool.py import  --jsonl trajectories/live_policy_decisions.jsonl \
                                          --db trajectories/live.db --kind decision
    python tools/storage_tool.py stats   --db trajectories/live.db
    python tools/storage_tool.py shard   --db trajectories/live.db --out trajectories/shards \
                                          --kind decision --keep 8
    python tools/storage_tool.py slice   --db trajectories/live.db --start 1200 --stop 1210
    python tools/storage_tool.py verify  --shard trajectories/shards/decision-000000001200.shard
    python tools/storage_tool.py replay  --path trajectories/live.db --fail-on-stuck

`import` is resumable and idempotent per source line count: run it again after a
crash and it continues from the row count already in the store.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.jsonio import iter_rows, utf8_console        # noqa: E402
from storage import AppendStore                          # noqa: E402
from storage import sharder                              # noqa: E402
from storage.rows import iter_dicts                     # noqa: E402


def cmd_import(args: argparse.Namespace) -> int:
    source = Path(args.jsonl)
    if not source.is_file():
        print("no such file: %s" % source, file=sys.stderr)
        return 1
    with AppendStore(args.db) as store:
        already = store.count()
        imported = 0
        batch = []
        for index, row in enumerate(iter_rows(source)):
            if index < already:
                continue
            batch.append(row)
            if len(batch) >= args.batch:
                store.append_many(batch, kind=args.kind)
                imported += len(batch)
                batch = []
        if batch:
            store.append_many(batch, kind=args.kind)
            imported += len(batch)
        stats = store.stats()
    print(json.dumps({"source": str(source), "imported": imported, **stats.as_dict()},
                     indent=2, sort_keys=True))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    with AppendStore(args.db) as store:
        print(json.dumps(store.stats().as_dict(), indent=2, sort_keys=True))
        if args.recover:
            print(json.dumps(store.recover(), indent=2))
    return 0


def cmd_shard(args: argparse.Namespace) -> int:
    with AppendStore(args.db) as store:
        info = sharder.shard_from_store(
            store, Path(args.out), kind=args.kind, max_records=args.max_records or None,
            block_records=args.block_records, retention_keep=args.keep)
    print(json.dumps(info, indent=2, sort_keys=True))
    return 0 if info.get("records") else 1


def cmd_slice(args: argparse.Namespace) -> int:
    if args.shard:
        rows = sharder.read_slice(Path(args.shard), args.start, args.stop)
    else:
        with AppendStore(args.db) as store:
            rows = store.slice(args.start, args.stop, kind=args.kind)
    utf8_console()
    print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    report = sharder.verify_shard(Path(args.shard))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


def cmd_replay(args: argparse.Namespace) -> int:
    from planning.machine import main as machine_main

    forwarded = ["--window", str(args.window), "--repeat", str(args.repeat)]
    if args.limit:
        forwarded += ["--limit", str(args.limit)]
    if args.fail_on_stuck:
        forwarded.append("--fail-on-stuck")
    if args.json:
        forwarded.append("--json")
    return machine_main([args.path] + forwarded)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import", help="jsonl -> SQLite WAL store")
    p.add_argument("--jsonl", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--kind", default="row")
    p.add_argument("--batch", type=int, default=500)
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("stats", help="row count, seq bounds, size, journal mode")
    p.add_argument("--db", required=True)
    p.add_argument("--recover", action="store_true")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("shard", help="snapshot a window into a compressed shard")
    p.add_argument("--db", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--kind", default=None)
    p.add_argument("--max-records", type=int, default=0)
    p.add_argument("--block-records", type=int, default=512)
    p.add_argument("--keep", type=int, default=None,
                   help="ring buffer: delete all but the newest N shards after writing")
    p.set_defaults(func=cmd_shard)

    p = sub.add_parser("slice", help="print records in [start, stop)")
    p.add_argument("--db")
    p.add_argument("--shard")
    p.add_argument("--start", type=int, required=True)
    p.add_argument("--stop", type=int, default=None)
    p.add_argument("--kind", default=None)
    p.set_defaults(func=cmd_slice)

    p = sub.add_parser("verify", help="decode every record of a shard")
    p.add_argument("--shard", required=True)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("replay", help="run a recorded session through the FSM")
    p.add_argument("--path", required=True)
    p.add_argument("--window", type=int, default=12)
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p.add_argument("--fail-on-stuck", action="store_true")
    p.set_defaults(func=cmd_replay)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
