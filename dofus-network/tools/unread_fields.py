"""Find fields the data carries and no consumer ever asks for.

The complement of `tools/field_audit.py`. That one finds *asked-and-absent* -- a consumer
reading a key the corpus never carries, which silently becomes a zero. This one finds
*present-and-unasked*: recorded data that nothing reads, which is the shape that costs
nothing until the day you need it and it is not there -- or, worse, it is there and was
being thrown away.

Measured 2026-11-12: every observation carried `map.terrain` (140 hex characters, one per
cell, the walkability geometry the page itself uses) and no consumer read it, while
`data/maps/_index.json` held `walk` for 2,696 maps that `map_graph()` also skipped. The
agent spent seven live steps asking the server a question its own logs answered (G112,
G113). Grepping for `terrain` finds the writer, not the missing reader, so this is a
measurement, not a search.

    python -m tools.unread_fields trajectories/dofus-01.jsonl --min 5
    python -m tools.unread_fields trajectories/*.jsonl --min 0 --top 60

Both sides are normalised the same way -- a list index becomes `[]` -- so `map.cells.0.l`
and `map.cells.17.l` are one path and the answer is about *shape*, not about one row.

Read the answer with its scope in view: "never read" means never read *by the consumers this
run replayed*, not by the whole tree. The planner reads the observation directly and is not one
of them, so a path it uses will still be listed. The report is a shortlist to check, not a
verdict -- `--consumers` narrows or widens it, and the counts (how many rows carry the path) are
what makes a candidate worth looking at.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.field_audit import CONSUMERS, _wrap, iter_observations  # noqa: E402

Path_ = Tuple[str, ...]


def _is_index(part: str) -> bool:
    return part.isdigit()


def _normalise(path: Path_) -> Path_:
    return tuple("[]" if _is_index(part) else part for part in path)


def _join(path: Path_) -> str:
    out = ""
    for part in path:
        out += part if part == "[]" else (("." if out else "") + part)
    return out


def asked_paths(paths: Sequence[str], limit: int,
                consumers: Sequence[str]) -> Tuple[Counter, Counter]:
    """Every path a consumer asked for, and every path it found there."""
    asked: Counter = Counter()
    found: Counter = Counter()

    def record(path: Path_, key: str, present: bool, how: str) -> None:
        full = _normalise(path + (key,))
        asked[full] += 1
        if present:
            found[full] += 1

    rows = 0
    for obs in iter_observations(paths):
        rows += 1
        if limit and rows > limit:
            break
        for name in consumers:
            try:
                CONSUMERS[name](_wrap(obs, (), record))
            except Exception:  # noqa: BLE001 - a crashing consumer is field_audit's job
                pass
    return asked, found


def present_paths(paths: Sequence[str], limit: int) -> Tuple[Counter, int]:
    """Every leaf-ish path the corpus actually carries, and how many rows carry it."""
    present: Counter = Counter()
    rows = 0
    for obs in iter_observations(paths):
        rows += 1
        if limit and rows > limit:
            break
        _walk(obs, (), present)
    return present, rows


def _walk(value: Any, path: Path_, out: Counter) -> None:
    """Count *leaves* only: a container that is never asked for is not a finding if
    every value inside it is read, and counting containers buries the answer."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + (str(key),)
            if isinstance(child, (dict, list)):
                _walk(child, child_path, out)
            else:
                out[child_path] += 1
    elif isinstance(value, list):
        child_path = path + ("[]",)
        for child in value:
            if isinstance(child, (dict, list)):
                _walk(child, child_path, out)
            else:
                out[child_path] += 1


def report(paths: Sequence[str], limit: int, consumers: Sequence[str],
           minimum: int, top: int) -> int:
    asked, found = asked_paths(paths, limit, consumers)
    present, rows = present_paths(paths, limit)
    asked_norm = {_normalise(p) for p in asked}
    # A path counts as read only if a consumer *found* it: asking and missing is
    # field_audit's finding, and reporting it here as "read" would hide both.
    read = {_normalise(p) for p in found}
    print("observations: %d | consumers: %s" % (rows, ", ".join(consumers)))
    print("paths present: %d | paths asked: %d | paths found: %d"
          % (len(present), len(asked_norm), len(read)))
    unread = [(p, n) for p, n in present.items()
              if _normalise(p) not in read and n >= minimum]
    unread.sort(key=lambda row: (-row[1], _join(row[0])))
    print("carried and never read: %d path(s) at --min %d" % (len(unread), minimum))
    for path, count in unread[:top]:
        print("  %6d  %s" % (count, _join(path)))
    if len(unread) > top:
        print("  ... %d more (raise --top)" % (len(unread) - top))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many observations (0 = all)")
    parser.add_argument("--min", type=int, default=5,
                        help="only report paths carried by at least this many rows")
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument("--consumers", default=",".join(CONSUMERS))
    args = parser.parse_args(argv)
    names = [n for n in args.consumers.split(",") if n]
    unknown = [n for n in names if n not in CONSUMERS]
    if unknown:
        parser.error("unknown consumer(s): %s" % ", ".join(unknown))
    return report(args.paths, args.limit, names, args.min, args.top)


if __name__ == "__main__":
    raise SystemExit(main())
