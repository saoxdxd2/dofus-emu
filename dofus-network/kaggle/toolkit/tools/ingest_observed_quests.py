"""Persist the client's own quest graph from recorded observations (G29).

The live page publishes `quests_catalog` from each active quest's own
`dbQuest`/`dbSteps`/`dbObjectives` (KNOWN_GAPS G29). This reads recorded logs, flattens
every observed objective into one row, merges it with what was captured before -- a
richer row is never replaced by a thinner one -- and writes
`data/knowledge/observed_quests.jsonl` atomically.

The downloaded store is never modified: observed rows are a separate, licence-clean
artifact. `agent/knowledge_context.py` loads this file when an observation does not carry
the catalogue itself, so a page that has not reloaded still resolves from what the client
already said.

Usage:
    python tools/ingest_observed_quests.py                 # default logs, write + census
    python tools/ingest_observed_quests.py --no-write      # census only
    python tools/ingest_observed_quests.py LOG... --out PATH
    python tools/ingest_observed_quests.py --coverage      # compare with the store
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRAJECTORY = ROOT / "trajectories" / "default.jsonl"
DEFAULT_AUTOPILOT = Path(os.environ.get("LOCALAPPDATA", "")) / "DofusFarm" / "autopilot.jsonl"
DEFAULT_OUT = ROOT / "data" / "knowledge" / "observed_quests.jsonl"
DEFAULT_STORE = ROOT / "data" / "knowledge" / "dofus_knowledge.sqlite"

# The keys under which a recorded row can carry an observation. `snapshot` is the
# `result.data` envelope field the bridge returns; the rest are the trajectory/autopilot
# names. The walk is bounded, so a deeply nested log cannot turn the scan quadratic.
OBS_KEYS = ("observation", "obs_before", "obs_after", "obs", "snapshot")
MAX_DEPTH = 3

# A row that names the objective's class, text, map or step is richer than one that only
# names the id. Parameters count as one more field, not as N, so a three-parameter fight
# objective does not outrank an objective with text and a class.
RICH_FIELDS = ("type_id", "type", "text", "map_id", "step_id")


def _ts(row: Dict[str, Any]) -> float:
    for key in ("at", "ts"):
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    try:
        return float(row.get("observed_at") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _coerce_int(value: Any) -> Optional[int]:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out


def _walk_catalogs(node: Any, depth: int = 0, root: Optional[Dict[str, Any]] = None
                   ) -> Iterator[Tuple[List[Any], Dict[str, Any], Dict[str, Any]]]:
    if depth > MAX_DEPTH or not isinstance(node, dict):
        return
    root = node if root is None else root
    catalog = node.get("quests_catalog")
    if isinstance(catalog, list):
        yield catalog, node, root
    for key in OBS_KEYS:
        child = node.get(key)
        if isinstance(child, dict):
            yield from _walk_catalogs(child, depth + 1, root)


def flatten_catalog(catalog: List[Any], row: Dict[str, Any], log: str,
                    root: Optional[Dict[str, Any]] = None
                    ) -> Iterator[Dict[str, Any]]:
    """One flat row per observed objective, carrying the quest/step context.

    Nothing is invented: a field the page did not capture stays None. The flat shape is
    what makes dedup, coverage and the runtime index simple. Timestamps and the session
    come from the log row; an observation that carries its own win over the outer row.
    """
    at = _ts(row) or _ts(root or {})
    session = row.get("session")
    if session is None:
        session = (root or {}).get("session")
    for quest in catalog:
        if not isinstance(quest, dict):
            continue
        qid = _coerce_int(quest.get("id"))
        if qid is None:
            continue
        qname = quest.get("name")
        for step in quest.get("steps") or []:
            if not isinstance(step, dict):
                continue
            sid = _coerce_int(step.get("id"))
            sname = step.get("name")
            sdesc = step.get("description")
            for obj in step.get("objectives") or []:
                if not isinstance(obj, dict):
                    continue
                oid = _coerce_int(obj.get("id"))
                if oid is None:
                    continue
                params = obj.get("params")
                yield {
                    "source": "observed",
                    "quest_id": qid,
                    "quest_name": qname,
                    "step_id": sid,
                    "step_name": sname,
                    "step_description": sdesc,
                    "objective_id": oid,
                    "type_id": obj.get("type_id"),
                    "type": obj.get("type"),
                    "map_id": obj.get("map_id"),
                    "params": dict(params) if isinstance(params, dict) else {},
                    "text": obj.get("text"),
                    "cur_completion": obj.get("cur_completion"),
                    "max_completion": obj.get("max_completion"),
                    "observed_at": at,
                    "session": session,
                    "log": log,
                }


def richness(row: Dict[str, Any]) -> int:
    score = sum(1 for key in RICH_FIELDS if row.get(key) not in (None, ""))
    if row.get("params"):
        score += 1
    return score


def merge(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the richer row; between equals the newer one wins.

    The async `dbObjectives` table is filled after the quest is active, so an early
    capture can be a bare id and a later one carries the class/params. Losing the later
    row loses the only data the ingest exists for; losing the earlier rich row to a
    transient empty read is the same mistake in reverse.
    """
    if old is None:
        return new
    old_rich = richness(old)
    new_rich = richness(new)
    if new_rich > old_rich:
        return new
    if new_rich < old_rich:
        return old
    return new if float(new.get("observed_at") or 0.0) >= float(old.get("observed_at") or 0.0) \
        else old


def read_rows(path: Path) -> Dict[Tuple[int, int], Dict[str, Any]]:
    rows: Dict[Tuple[int, int], Dict[str, Any]] = {}
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            qid = _coerce_int(row.get("quest_id"))
            oid = _coerce_int(row.get("objective_id"))
            if qid is None or oid is None:
                continue
            rows[(qid, oid)] = merge(rows.get((qid, oid)), row)
    return rows


def write_rows(path: Path, rows: Dict[Tuple[int, int], Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows.values(),
                     key=lambda row: (int(row["quest_id"]),
                                      int(row.get("step_id") or 0),
                                      int(row["objective_id"])))
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in ordered:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)


def collect(paths: Iterable[Path]) -> Tuple[Dict[Tuple[int, int], Dict[str, Any]], int, int]:
    """Read every log, return (rows, log_lines_with_a_catalogue, rows_seen)."""
    rows: Dict[Tuple[int, int], Dict[str, Any]] = {}
    catalog_lines = 0
    seen = 0
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or '"quests_catalog"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                found = False
                for catalog, obs, root in _walk_catalogs(row):
                    found = True
                    for flat in flatten_catalog(catalog, obs, path.name, root):
                        key = (flat["quest_id"], flat["objective_id"])
                        rows[key] = merge(rows.get(key), flat)
                        seen += 1
                if found:
                    catalog_lines += 1
    return rows, catalog_lines, seen


def coverage(rows: Dict[Tuple[int, int], Dict[str, Any]],
             store: Any = None) -> Dict[str, Any]:
    stats: Dict[str, Any] = {
        "rows": len(rows),
        "quests": len({qid for qid, _ in rows}),
        "objectives": len({oid for _, oid in rows}),
        "types": Counter(),
        "new": 0,
        "agrees": 0,
        "collision": 0,
    }
    for (qid, oid), row in rows.items():
        type_id = row.get("type_id")
        if type_id is not None:
            stats["types"][str(type_id)] += 1
        if store is None:
            continue
        try:
            resolved = store.resolve_objective(oid, "fr")
        except Exception:      # noqa: BLE001 - a broken store is "unmeasured", not a crash
            resolved = None
        if resolved is None:
            stats["new"] += 1
            continue
        same_quest = _coerce_int(resolved.get("quest_id")) == qid
        store_type = resolved.get("type_id")
        same_type = (store_type is None or type_id is None
                     or _coerce_int(store_type) == _coerce_int(type_id))
        if same_quest and same_type:
            stats["agrees"] += 1
        else:
            stats["collision"] += 1
    return stats


def verdict(stats: Dict[str, Any], catalogs: int) -> str:
    if not stats["rows"]:
        return ("no observed quest rows: the recorded logs predate the capture. Run this "
                "after a live session whose page build publishes quests_catalog.")
    line = ("%d observed objective rows across %d quest(s); %d log line(s) carried a "
            "catalogue" % (stats["rows"], stats["quests"], catalogs))
    if stats["new"] or stats["agrees"] or stats["collision"]:
        line += ("; store: %d new, %d agrees, %d collision"
                 % (stats["new"], stats["agrees"], stats["collision"]))
        if stats["collision"]:
            line += (" -- a collision is the G30 foreign-id case; the observed row is the "
                     "live truth")
    return line


def _default_paths() -> List[Path]:
    paths: List[Path] = []
    if DEFAULT_TRAJECTORY.is_file():
        paths.append(DEFAULT_TRAJECTORY)
    rotation = sorted((ROOT / "trajectories").glob("*.jsonl"))
    paths.extend(path for path in rotation if path != DEFAULT_TRAJECTORY)
    if DEFAULT_AUTOPILOT.is_file():
        paths.append(DEFAULT_AUTOPILOT)
    return paths


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path,
                        help="recorded JSONL logs (default: trajectories/*.jsonl + autopilot)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="observed rows file (default: data/knowledge/observed_quests.jsonl)")
    parser.add_argument("--no-write", action="store_true", help="census only")
    parser.add_argument("--coverage", action="store_true",
                        help="compare observed objectives with the downloaded store")
    args = parser.parse_args(argv)

    paths = args.paths or _default_paths()
    rows = read_rows(args.out)
    before = len(rows)
    fresh, catalogs, seen = collect(paths)
    for key, row in fresh.items():
        rows[key] = merge(rows.get(key), row)
    stats = coverage(rows, None)
    store = None
    if args.coverage:
        if DEFAULT_STORE.is_file():
            try:
                # `python tools/x.py` puts tools/ on sys.path, not the repo root: the
                # store package is only importable once ROOT is added explicitly.
                if str(ROOT) not in sys.path:
                    sys.path.insert(0, str(ROOT))
                from knowledge.store import CanonicalStore
                store = CanonicalStore(DEFAULT_STORE)
            except Exception as exc:      # noqa: BLE001 - print the reason, do not hide it
                print("store unavailable: %s" % exc)
        else:
            print("store absent: %s" % DEFAULT_STORE)
    if store is not None:
        stats = coverage(rows, store)

    print("ingest: %d log(s), %d catalogue line(s), %d observed row(s) read"
          % (sum(1 for path in paths if path.is_file()), catalogs, seen))
    print("rows: %d (was %d, merged %d)" % (len(rows), before, len(fresh)))
    print(verdict(stats, catalogs))
    if stats["types"]:
        print("types: %s" % ", ".join("%s=%d" % item
                                      for item in sorted(stats["types"].items())))
    if not args.no_write and rows:
        write_rows(args.out, rows)
        print("wrote %s" % args.out)
    elif not args.no_write:
        print("nothing written: no observed rows")
    if store is not None:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
