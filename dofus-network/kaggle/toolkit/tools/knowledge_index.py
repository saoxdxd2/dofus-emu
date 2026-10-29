"""Index the knowledge the client itself reported, and prove every field of it.

What "100% accuracy" can mean here, stated exactly:

  * every indexed field is a literal value the running client sent in an
    observation - never inferred, never filled from a downloaded cache;
  * if the client sent two different values for the same field, the field is
    recorded as a conflict and left out of the verified row, never picked;
  * every row carries its provenance (which sources, how many captures, first and
    last sighting, the logs it came from), and `field_sources` names the source
    that supplied each individual field, so any value can be traced back;
  * `step_objectives` rows without a quest id are bound to their step's unique
    owner *after* the whole scan, so the answer does not depend on whether a
    file's catalogue arrived before its step lists. Measured when the owner map
    is reset per file: 10,843 of 19,527 such observations bound in-pass, 19,310
    bind with the deferred map; 217 have a step id no catalogue names;
  * `--check` rebuilds the index from the logs and fails if the file on disk
    differs, so a stale or hand-edited index cannot pass as verified;
  * writes are atomic (temp file + rename): the runtime reads this file on every
    snapshot and a truncated index would drop it back to the downloaded store.

What it cannot mean: completeness. The index describes what the character's own
client has shown - today 5 quests and a few dozen objectives. Everything not
captured is reported as absent, not invented. That distinction is the whole point:
the licence-unclean 44 MB store describes quests 18..2546 and none of the live ones,
and a foreign id resolved from it is how G30 happened.

The four client sources, and what each contributes:

  quests_catalog     quest/step/objective ids, names, type, params, map_id
  ready_objectives   text, type, params, step id, quest id (fully bound)
  current_objective  text, type, params, step id, quest id (fully bound)
  step_objectives    text, type, params, step id - quest is inferred by step id,
                     and only when exactly one quest owns that step

    python tools/knowledge_index.py --write
    python tools/knowledge_index.py --check
    python tools/knowledge_index.py --json --logs trajectories
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "knowledge" / "client_index.json"
DEFAULT_MANIFEST = REPO_ROOT / "data" / "knowledge" / "client_index.manifest.json"
DEFAULT_LOGS = REPO_ROOT / "trajectories"

OBS_KEYS = ("observation", "obs_before", "obs_after", "obs", "snapshot")
MAX_DEPTH = 3
FIELDS = ("type_id", "type", "map_id", "params", "text", "step_id",
          "quest_name", "step_name", "step_description",
          "cur_completion", "max_completion")
# These two count progress and are *supposed* to differ between captures. A
# disagreement there is elapsed play, not an identity conflict: keep the latest.
TIME_VARYING = ("cur_completion", "max_completion")


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stamp(row: Dict[str, Any]) -> float:
    for key in ("at", "ts"):
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _observations(node: Any, depth: int = 0) -> Iterator[Dict[str, Any]]:
    if depth > MAX_DEPTH or not isinstance(node, dict):
        return
    yield node
    for key in OBS_KEYS:
        child = node.get(key)
        if isinstance(child, dict):
            yield from _observations(child, depth + 1)


def log_paths(target: Path) -> List[Path]:
    if target.is_file():
        return [target]
    out = []
    for pattern in ("dofus-*.jsonl", "default*.jsonl"):
        for path in sorted(target.glob(pattern)):
            if path.name.startswith("play_") or path.name.endswith(".stats.json"):
                continue
            out.append(path)
    return out


def scan(paths: List[Path]) -> Dict[str, Any]:
    """All client-reported objective values, with provenance, and conflicts.

    `step_objectives` rows carry a step id but often no quest id, so the step
    owner is needed to bind them. The owner map is only complete once every
    catalogue has been read; binding during the pass made the answer depend on
    file order (a file whose step lists precede its catalogue lost those rows).
    The parse stays single-pass and only the binding is deferred.
    """
    values: Dict[Tuple[int, int], Dict[str, Dict[str, Any]]] = {}
    seen_at: Dict[Tuple[int, int], List[float]] = {}
    sources: Dict[Tuple[int, int], set] = {}
    logs: Dict[Tuple[int, int], set] = {}
    step_owner: Dict[int, set] = {}
    pending: List[Tuple[int, str, Dict[str, Any], float, str]] = []
    unbound = 0

    def note(key: Tuple[int, int], source: str, payload: Dict[str, Any],
             stamp: float, log: str) -> None:
        bucket = values.setdefault(key, {})
        for field in FIELDS:
            if field not in payload:
                continue
            value = payload.get(field)
            if value in (None, "", {}):
                continue
            text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
            entry = bucket.setdefault(field, {}).setdefault(
                text, {"value": value, "sources": set(), "last_at": stamp})
            entry["sources"].add(source)
            entry["last_at"] = max(entry["last_at"], stamp)
        seen_at.setdefault(key, []).append(stamp)
        sources.setdefault(key, set()).add(source)
        logs.setdefault(key, set()).add(log)

    for path in paths:
        log = path.name
        with path.open("r", encoding="utf-8", errors="replace") as handle:
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
                stamp = _stamp(row)
                per_row = set()
                for obs in _observations(row):
                    if id(obs) in per_row:
                        continue
                    per_row.add(id(obs))
                    catalog = obs.get("quests_catalog")
                    if isinstance(catalog, list):
                        for quest in catalog:
                            if not isinstance(quest, dict):
                                continue
                            qid = _int(quest.get("id"))
                            if qid is None:
                                continue
                            for step in quest.get("steps") or []:
                                if not isinstance(step, dict):
                                    continue
                                sid = _int(step.get("id"))
                                if sid is not None:
                                    step_owner.setdefault(sid, set()).add(qid)
                                for obj in step.get("objectives") or []:
                                    if not isinstance(obj, dict):
                                        continue
                                    oid = _int(obj.get("id"))
                                    if oid is None:
                                        continue
                                    note((qid, oid), "catalog",
                                         {"type_id": obj.get("type_id"),
                                          "type": obj.get("type"),
                                          "map_id": obj.get("map_id"),
                                          "params": obj.get("params"),
                                          "text": obj.get("text"),
                                          "step_id": obj.get("step_id", sid),
                                          "quest_name": quest.get("name"),
                                          "step_name": step.get("name"),
                                          "step_description": step.get("description"),
                                          "cur_completion": obj.get("cur_completion"),
                                          "max_completion": obj.get("max_completion")},
                                         stamp, log)
                    for source, field_name in (("ready", "ready_objectives"),
                                               ("current", "current_objective"),
                                               ("step", "step_objectives")):
                        block = obs.get(field_name)
                        items = block if isinstance(block, list) else [block]
                        for obj in items:
                            if not isinstance(obj, dict):
                                continue
                            oid = _int(obj.get("objective_id") or obj.get("id"))
                            if oid is None:
                                continue
                            qid = _int(obj.get("quest_id"))
                            sid = _int(obj.get("step_id"))
                            if qid is None and sid is not None:
                                pending.append((oid, source, obj, stamp, log))
                                continue
                            if qid is None:
                                unbound += 1
                                continue
                            note((qid, oid), source, obj, stamp, log)

    for oid, source, obj, stamp, log in pending:
        owners = step_owner.get(_int(obj.get("step_id"))) or set()
        if len(owners) == 1:
            note((next(iter(owners)), oid), source, obj, stamp, log)
        else:
            unbound += 1

    rows: Dict[str, Dict[str, Any]] = {}
    conflicts: Dict[str, int] = {}
    for (qid, oid), fields in sorted(values.items()):
        row: Dict[str, Any] = {"quest_id": qid, "objective_id": oid,
                               "sources": sorted(sources.get((qid, oid), ())),
                               "captures": len(seen_at.get((qid, oid), [])),
                               "logs": sorted(logs.get((qid, oid), ()))[:6]}
        stamps = seen_at.get((qid, oid)) or [0.0]
        row["first_at"] = round(min(stamps), 3)
        row["last_at"] = round(max(stamps), 3)
        row_conflicts: Dict[str, List[Any]] = {}
        field_sources: Dict[str, List[str]] = {}
        for field, options in fields.items():
            if field in TIME_VARYING:
                latest = max(options.values(), key=lambda entry: entry["last_at"])
                row[field] = latest["value"]
                newest = [entry for entry in options.values()
                          if entry["last_at"] == latest["last_at"]]
                field_sources[field] = sorted(set().union(
                    *(entry["sources"] for entry in newest)))
                if len(options) > 1:
                    row.setdefault("completion_captures", {})[field] = len(options)
                continue
            if len(options) > 1:
                row_conflicts[field] = [entry["value"] for entry in options.values()]
                conflicts[field] = conflicts.get(field, 0) + 1
                continue
            entry = next(iter(options.values()))
            row[field] = entry["value"]
            field_sources[field] = sorted(entry["sources"])
        row["field_sources"] = field_sources
        if row_conflicts:
            row["conflicts"] = row_conflicts
        rows["%d:%d" % (qid, oid)] = row
    return {"rows": rows, "conflicts": conflicts, "unbound": unbound,
            "quests": sorted({row["quest_id"] for row in rows.values()})}


def summarise(index: Dict[str, Any], paths: List[Path]) -> Dict[str, Any]:
    rows = list(index["rows"].values())
    total = len(rows)
    def share(field: str) -> Dict[str, Any]:
        have = sum(1 for row in rows if row.get(field) not in (None, "", {}))
        return {"present": have, "total": total,
                "pct": round(100.0 * have / total, 1) if total else 0.0}
    manifest = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "logs": [{"name": path.name, "size": path.stat().st_size,
                  "mtime": int(path.stat().st_mtime)} for path in paths],
        "rows": total,
        "quests": index["quests"],
        "fields": {field: share(field) for field in
                   ("type_id", "map_id", "params", "text", "step_id", "quest_name")},
        "conflicts": index["conflicts"],
        "unbound_step_objectives_seen": index["unbound"],
        "sources": {
            "catalog": sum(1 for row in rows if "catalog" in row["sources"]),
            "ready": sum(1 for row in rows if "ready" in row["sources"]),
            "current": sum(1 for row in rows if "current" in row["sources"]),
            "step": sum(1 for row in rows if "step" in row["sources"]),
        },
    }
    return manifest


def digest(rows: Dict[str, Any]) -> str:
    blob = json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    """The runtime reads this file on every snapshot; a half-written index is not.

    A reader that catches the parse error would fall back to the downloaded store,
    and falling back to the store is exactly the G30 failure. Rename is atomic on
    NTFS and POSIX, so a reader sees either the old index or the new one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.tmp-%d" % (path.name, os.getpid()))
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", type=Path, default=DEFAULT_LOGS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--write", action="store_true", help="write the index and manifest")
    ap.add_argument("--check", action="store_true",
                    help="fail if the on-disk index differs from the logs")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    paths = log_paths(args.logs)
    if not paths:
        print("no logs under %s" % args.logs)
        return 2
    index = scan(paths)
    manifest = summarise(index, paths)
    manifest["index_sha256"] = digest(index["rows"])
    if args.write:
        payload = json.dumps({"rows": index["rows"],
                              "manifest": manifest}, indent=1, sort_keys=True,
                             ensure_ascii=False)
        _write_atomic(args.out, payload)
        _write_atomic(args.manifest, json.dumps(manifest, indent=1))
    if args.check:
        try:
            stored = json.loads(args.out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("cannot read the index at %s: %s" % (args.out, exc))
            return 1
        stored_rows = stored.get("rows") if isinstance(stored, dict) else None
        if digest(stored_rows or {}) != manifest["index_sha256"]:
            print("index is stale: the logs say %d rows, the file says %s"
                  % (manifest["rows"], len(stored_rows or {})))
            return 1
        stored_manifest = stored.get("manifest") if isinstance(stored, dict) else None
        if isinstance(stored_manifest, dict):
            if stored_manifest.get("index_sha256") != manifest["index_sha256"]:
                print("manifest is stale: it belongs to a different index")
                return 1
            if stored_manifest.get("rows") != manifest["rows"]:
                print("manifest is stale: it counts %s rows, the logs say %d"
                      % (stored_manifest.get("rows"), manifest["rows"]))
                return 1
        print("index verified: %d rows match the logs" % manifest["rows"])
    if args.json:
        print(json.dumps(manifest, indent=1))
    else:
        print("index: %d objectives / %d quests %s"
              % (manifest["rows"], len(manifest["quests"]), manifest["quests"]))
        for field, stat in manifest["fields"].items():
            print("   %-12s %4d/%-4d %5.1f%%" % (field, stat["present"],
                                                 stat["total"], stat["pct"]))
        print("   sources %s" % manifest["sources"])
        print("   conflicts %s   unbound step objectives %d"
              % (manifest["conflicts"] or "none",
                 manifest["unbound_step_objectives_seen"]))
        if args.write:
            print("wrote %s" % args.out)
    return 1 if manifest["conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
