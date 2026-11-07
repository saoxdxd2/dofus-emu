"""Find fields the code reads and the data never carries.

The bug class this exists for: a consumer asks for `group["monsters"]` while the
client sends `group["members"]`, `.get()` returns None, `_num(None)` returns 0.0,
and the feature is silently constant for the whole corpus. It has happened at
least three times in this repository and each time it looked like working code:

* `agent/observe.py` — the planner asked for `map_id`/`level`/`inventory_capacity`
  and got None on a live client, against a snapshot shape that no longer existed;
* the client's `__PriceCache` — read `prices`/`objectGID`/`objectUID` and "never
  cached a single server number";
* `agent/state_features.py` — read `monsters` for a group's members, so 2,430
  groups encoded to 7 distinct vectors.

Grepping for suspicious names cannot find these, because a wrong name looks
exactly like a right one. So this replays the recorded corpus through the real
consumers with every dict wrapped in a tracker, and reports each key that was
requested and never present.

    python -m tools.field_audit trajectories/dofus-01.jsonl --limit 200
    python -m tools.field_audit trajectories/dofus-*.jsonl --limit 0
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Which consumer is asking, so a miss can be attributed. The tracker is shared by
# every consumer in one pass, and without this the report says "something asked".
_CURRENT = "?"


class Tracked(dict):
    """A dict that records every key asked for, and whether it was there."""

    __slots__ = ("_path", "_log")

    def __init__(self, data: Dict[Any, Any], path: Tuple[str, ...],
                 log: Callable[[Tuple[str, ...], str, bool, str], None]) -> None:
        super().__init__()
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_log", log)
        for key, value in data.items():
            super().__setitem__(key, _wrap(value, path + (str(key),), log))

    def get(self, key, default=None):  # noqa: D401 - dict protocol
        self._log(self._path, str(key), key in self, "get")
        return super().get(key, default)

    def __getitem__(self, key):
        self._log(self._path, str(key), key in self, "[]")
        return super().__getitem__(key)

    def __contains__(self, key):
        self._log(self._path, str(key), dict.__contains__(self, key), "in")
        return dict.__contains__(self, key)


def _wrap(value: Any, path: Tuple[str, ...],
          log: Callable[[Tuple[str, ...], str, bool, str], None]) -> Any:
    if isinstance(value, dict) and not isinstance(value, Tracked):
        return Tracked(value, path, log)
    if isinstance(value, list):
        return [_wrap(item, path + ("[]",), log) for item in value]
    return value


class Audit:
    def __init__(self) -> None:
        self.hits: Counter = Counter()
        self.misses: Counter = Counter()
        self.asked: Counter = Counter()
        self.consumers: Dict[Tuple[str, ...], str] = {}

    def record(self, path: Tuple[str, ...], key: str, present: bool,
               how: str) -> None:
        route = ".".join(path + (key,)) if path else key
        self.consumers.setdefault((route, _CURRENT), _CURRENT)
        self.asked[(route, _CURRENT)] += 1
        if present:
            self.hits[(route, _CURRENT)] += 1
        else:
            self.misses[(route, _CURRENT)] += 1

    def report(self) -> List[Dict[str, Any]]:
        rows = []
        for (route, consumer), misses in self.misses.items():
            rows.append({"route": route, "consumer": consumer, "misses": misses,
                         "hits": self.hits[(route, consumer)],
                         "asked": self.asked[(route, consumer)]})
        rows.sort(key=lambda row: (-row["misses"], row["route"]))
        return rows


# ---------------------------------------------------------------------------
# consumers
#
# Each one is called with the same observation and must only read from it. A
# consumer that raises is reported: a crash on a real observation is a finding
# too, not something to swallow.


def _consumer_state_features(obs: Dict[str, Any]) -> None:
    from agent.state_features import entity_vectors, global_vector

    global_vector(obs)
    entity_vectors(obs)


def _consumer_world_state(obs: Dict[str, Any]) -> None:
    from agent.world_state import encode, from_observation

    encode(from_observation(obs))


def _consumer_flatten(obs: Dict[str, Any]) -> None:
    from agent.observe import flatten

    flatten(obs)


CONSUMERS: Dict[str, Callable[[Dict[str, Any]], None]] = {
    "state_features": _consumer_state_features,
    "world_state": _consumer_world_state,
    "flatten": _consumer_flatten,
}


# ---------------------------------------------------------------------------


def iter_observations(paths: Sequence[str]) -> Iterable[Dict[str, Any]]:
    for path in paths:
        try:
            handle = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and isinstance(record.get("observation"), dict):
                    yield record["observation"]


def run(paths: Sequence[str], limit: int,
        consumers: Sequence[str]) -> Tuple[Audit, Counter, int]:
    audit = Audit()
    errors: Counter = Counter()
    seen = 0
    global _CURRENT
    for obs in iter_observations(paths):
        if limit and seen >= limit:
            break
        seen += 1
        for name in consumers:
            _CURRENT = name
            wrapped = _wrap(obs, (), audit.record)
            try:
                CONSUMERS[name](wrapped)
            except Exception as exc:  # noqa: BLE001 - a crash is a finding
                errors["%s: %s: %s" % (name, type(exc).__name__, exc)] += 1
    return audit, errors, seen


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--limit", type=int, default=200,
                        help="observations per file group (0 = all)")
    parser.add_argument("--consumers", default="state_features,world_state,flatten")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--misses-only", action="store_true", default=True)
    parser.add_argument("--all", action="store_true",
                        help="show routes that were always present too")
    args = parser.parse_args(argv)

    paths = args.paths or sorted(glob.glob(str(REPO_ROOT / "trajectories" / "dofus-*.jsonl")))
    if not paths:
        print("no trajectory files", file=sys.stderr)
        return 2

    names = [name.strip() for name in args.consumers.split(",") if name.strip()]
    unknown = [name for name in names if name not in CONSUMERS]
    if unknown:
        print("unknown consumer(s): %s" % ", ".join(unknown), file=sys.stderr)
        return 2

    audit, errors, seen = run(paths, args.limit, names)
    rows = audit.report()
    if not args.all:
        rows = [row for row in rows if row["misses"] and not row["hits"]]

    if args.json:
        print(json.dumps({"observations": seen, "rows": rows,
                          "errors": dict(errors)}, ensure_ascii=False, indent=1))
        return 0

    print("observations  %d" % seen)
    print("consumers     %s" % ", ".join(names))
    print()
    if not rows:
        print("no key was requested and never found")
    else:
        print("%-46s %-16s %7s %7s %7s" % ("route", "consumer", "asked", "hit", "miss"))
        for row in rows:
            print("%-46s %-16s %7d %7d %7d"
                  % (row["route"][:46], row["consumer"][:16], row["asked"],
                     row["hits"], row["misses"]))
    if errors:
        print()
        print("consumer errors:")
        for message, count in errors.most_common():
            print("  %5d  %s" % (count, message))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
