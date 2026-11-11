"""Fill the durable memory from the sessions that already happened.

`agent.persistent_memory` starts empty on a fresh install, which means the first run
after this change knows nothing -- even though the logs on this machine hold tens of
thousands of decided steps. This tool reads them back and writes the same two facts the
live loop writes: which call advanced a context, and which call failed there.

Why a backfill and not "let it accumulate". The operator's requirement is that a learned
thing is not solved twice. Waiting for the live loop to rediscover every wall is exactly
the cost being removed, and the evidence for those walls is already on disk.

What is deliberately excluded:

  * read-only tools. `state.snapshot` scored `advanced` on 3,005 of 3,010 baseline steps
    -- it always "works", and recording it would teach the memory that looking is the way
    past everything. Reads are not solutions.
  * rows with no observation. A context needs the map and the objective; a row without
    `obs_before` cannot be keyed and would land in the empty context, where it would
    veto or replay calls for every objective at once.

The output is the same file the loop reads, so `--dry-run` is the safe way to see what
would be claimed before anything is written.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.action_memory import FAIL_VERDICTS, context_signature  # noqa: E402
from agent.persistent_memory import DEFAULT_PATH, PersistentMemory  # noqa: E402
from agent.verifier import READ_ONLY_TOOLS  # noqa: E402


def default_sources() -> List[str]:
    """Every step log this machine has: the repo's own, then the installed app's."""
    roots: List[str] = []
    roots.extend(sorted(glob.glob(str(REPO_ROOT / "trajectories" / "*.jsonl"))))
    state = os.environ.get("LOCALAPPDATA")
    if state:
        roots.extend(sorted(glob.glob(os.path.join(state, "DofusFarm", "autopilot*.jsonl"))))
    return roots


def iter_rows(paths: Iterable[str]) -> Iterator[Dict[str, Any]]:
    for path in paths:
        try:
            handle = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line or line[0] != "{":
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row


def step_of(row: Dict[str, Any]) -> Optional[Tuple[Tuple, str, Dict[str, Any], str]]:
    """(context, tool, args, verdict) for a decided step, or None for anything else.

    A step row is the one shape with no `kind`: `_emit` writes the tool, the arguments,
    the verdict and the observation it judged, and nothing else in the log has all four.
    """
    if row.get("kind") is not None:
        return None
    tool = row.get("tool")
    verdict = row.get("verdict")
    before = row.get("obs_before")
    if not isinstance(tool, str) or not tool or tool == "-":
        return None
    if verdict != "advanced" and verdict not in FAIL_VERDICTS:
        return None
    if not isinstance(before, dict) or not before:
        return None
    args = row.get("args") if isinstance(row.get("args"), dict) else {}
    context = context_signature(before.get("current_objective"), before)
    if context[3] is None:
        # No map in the observation: the row cannot be keyed to a place, and an
        # unkeyed wall would veto the same call everywhere.
        return None
    return context, tool, args, verdict


def seed(paths: List[str], memory: PersistentMemory, skip_reads: bool = True) -> Dict[str, Any]:
    seen = Counter()
    skipped = Counter()
    for row in iter_rows(paths):
        step = step_of(row)
        if step is None:
            skipped["not a decided step"] += 1
            continue
        context, tool, args, verdict = step
        if skip_reads and tool in READ_ONLY_TOOLS:
            skipped["read-only tool"] += 1
            continue
        memory.observe(context, tool, args, verdict)
        seen[verdict] += 1
    return {"applied": dict(seen), "skipped": dict(skipped)}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_PATH,
                        help="durable memory file to write (default: %s)" % DEFAULT_PATH)
    parser.add_argument("--source", action="append", default=None,
                        help="a log to read; repeatable (default: every step log found)")
    parser.add_argument("--keep-reads", action="store_true",
                        help="also record read-only tools as solutions (not recommended: "
                             "a read always succeeds and teaches nothing)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be recorded without writing the file")
    parser.add_argument("--top", type=int, default=8,
                        help="how many contexts to print as examples")
    args = parser.parse_args(list(argv) if argv is not None else None)

    paths = args.source or default_sources()
    if not paths:
        print("no step logs found; nothing to seed")
        return 1
    # Start from a blank store rather than merging into whatever is there: re-running
    # this tool must not double every count, and the logs are the complete record.
    memory = PersistentMemory(args.out if not args.dry_run else Path(os.devnull))
    memory._solutions.clear()
    memory._dead.clear()

    report = seed(paths, memory, skip_reads=not args.keep_reads)
    stats = memory.stats()
    print("read %d log(s)" % len(paths))
    print("applied: %s" % report["applied"])
    print("skipped: %s" % report["skipped"])
    print("memory: %s" % stats)

    ranked = sorted(memory._solutions.items(),
                    key=lambda kv: -sum(int(v.get("wins", 0)) for v in kv[1].values()))
    for key, bucket in ranked[:max(0, args.top)]:
        best = max(bucket.values(), key=lambda v: int(v.get("wins", 0)))
        print("  %-34s -> %s %s x%d" % (
            key.replace("\x1f", "/"), best.get("tool"),
            json.dumps(best.get("args") or {}, sort_keys=True)[:60], int(best.get("wins", 0))))

    if args.dry_run:
        print("dry run: nothing written")
        return 0
    written = memory.save()
    if written is None:
        print("could not write %s" % args.out)
        return 1
    print("wrote %s" % written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
