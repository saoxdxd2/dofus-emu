"""Keep the map cache's geometry from the observations the agent already records.

Every live observation carries `map.terrain`: one bit per cell, four cells per hex
digit, so 140 characters for a 560-cell map. `data/maps/_index.json` stores the same
fact as `walk`, 560 `0`/`1` characters, and nothing wrote it from a live session.

Measured 2026-11-12:

* the two encodings agree bit for bit on all 5 maps present in both (0 disagreements),
  so an observation is a faithful source for the cache;
* 5 of the 10 maps in `trajectories/dofus-01.jsonl` were absent from the cache;
* the map the character was standing on (146277376) was one of them, and it is a
  building interior whose whole border ring is wall. The planner could not know that,
  so it asked the server for four neighbours and got nothing seven times (G112).

So this is the other half of G112: the cache can answer "does this map have a
walkable border cell to the north" for any map it holds, and it held none of the maps
the agent was actually walking on.

    python -m tools.ingest_map_terrain trajectories/dofus-*.jsonl
    python -m tools.ingest_map_terrain trajectories/*.jsonl --write

Reads every `observation.map` in the given files and reports what it would add. With
`--write` it adds the maps the cache does not have, and by default never overwrites a
`walk` that is already there: the cached value may come from the client bundle and a
live observation can carry a transient obstacle update.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

INDEX = REPO_ROOT / "data" / "maps" / "_index.json"

# The client's MAP_WIDTH. Every map in this build is 560 cells, 14 to a row.
MAP_WIDTH = 14
NEIGHBOUR_KEYS = {"left": "leftNeighbourId", "right": "rightNeighbourId",
                  "top": "topNeighbourId", "bottom": "bottomNeighbourId"}


def terrain_to_walk(terrain: str) -> str:
    """Expand the observation's hex nibbles into the cache's one-char-per-cell form.

    Bit `k` of a nibble is cell `4 * index + k`, which is the packing the page uses
    (`scripts/mobile-disguise.js`, `__agentTerrainCache`). Verified against the cache
    on 5 maps, 0 disagreements.
    """
    out: List[str] = []
    for char in str(terrain or ""):
        try:
            nibble = int(char, 16)
        except ValueError:
            return ""
        out.extend("1" if (nibble >> bit) & 1 else "0" for bit in range(4))
    return "".join(out)


def _rows(paths: Iterable[str]) -> Iterator[Dict[str, Any]]:
    for pattern in paths:
        for name in sorted(glob.glob(pattern)):
            with open(name, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue


def collect(paths: Iterable[str]) -> Dict[int, Dict[str, Any]]:
    """The newest terrain and neighbour set seen for each map id."""
    found: Dict[int, Dict[str, Any]] = {}
    for row in _rows(paths):
        block = ((row.get("observation") or {}).get("map")
                 or (row.get("obs_after") or {}).get("map") or {})
        if not isinstance(block, dict):
            continue
        terrain = block.get("terrain")
        map_id = block.get("map_id")
        if not terrain or map_id is None:
            continue
        try:
            map_id = int(map_id)
        except (TypeError, ValueError):
            continue
        walk = terrain_to_walk(terrain)
        if not walk:
            continue
        neighbours: Dict[str, int] = {}
        raw = block.get("neighbours")
        if isinstance(raw, dict):
            for direction, key in NEIGHBOUR_KEYS.items():
                value = raw.get(direction)
                if value is None:
                    continue
                try:
                    if int(value) > 0:
                        neighbours[key] = int(value)
                except (TypeError, ValueError):
                    continue
        found[map_id] = {"walk": walk, "neighbours": neighbours}
    return found


def plan(index: Dict[str, Any], found: Dict[int, Dict[str, Any]]
         ) -> Tuple[List[int], List[int], List[int]]:
    """(new, agree, disagree) map ids, so a dry run can be read before it writes."""
    new: List[int] = []
    agree: List[int] = []
    disagree: List[int] = []
    for map_id, row in sorted(found.items()):
        cached = index.get(str(map_id))
        if not isinstance(cached, dict) or not cached.get("walk"):
            new.append(map_id)
            continue
        cached_walk = str(cached.get("walk") or "")
        seen = row["walk"]
        if seen[:len(cached_walk)] == cached_walk:
            agree.append(map_id)
        else:
            disagree.append(map_id)
    return new, agree, disagree


def apply(index: Dict[str, Any], found: Dict[int, Dict[str, Any]],
          update: bool = False) -> Dict[str, int]:
    counts = {"added": 0, "updated": 0, "kept": 0}
    for map_id, row in sorted(found.items()):
        entry = index.get(str(map_id))
        walk = row["walk"]
        if not isinstance(entry, dict):
            index[str(map_id)] = {
                "id": map_id,
                "neighbours": dict(row["neighbours"]),
                "cells": len(walk),
                "walkable": walk.count("1"),
                "walk": walk,
                # Where this row came from, because a later disagreement needs to know
                # whether it is arguing with the client bundle or with a live session.
                "source": "terrain",
            }
            counts["added"] += 1
            continue
        changed = False
        if not entry.get("walk"):
            entry["walk"] = walk
            entry["cells"] = len(walk)
            entry["walkable"] = walk.count("1")
            changed = True
        elif update and walk[:len(str(entry.get("walk")))] != str(entry.get("walk")):
            entry["walk"] = walk
            entry["cells"] = len(walk)
            entry["walkable"] = walk.count("1")
            changed = True
        if not entry.get("neighbours") and row["neighbours"]:
            entry["neighbours"] = dict(row["neighbours"])
            changed = True
        counts["updated" if changed else "kept"] += 1
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.ingest_map_terrain",
                                     description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*",
                        default=[str(REPO_ROOT / "trajectories" / "dofus-*.jsonl")],
                        help="jsonl files or globs to read observations from")
    parser.add_argument("--write", action="store_true", help="write the cache")
    parser.add_argument("--update", action="store_true",
                        help="also replace a `walk` the cache already has")
    parser.add_argument("--index", default=str(INDEX), help="cache to read and write")
    args = parser.parse_args(argv)

    index_path = Path(args.index)
    index: Dict[str, Any] = {}
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except ValueError:
            print("the cache is not valid JSON; refusing to touch it", file=sys.stderr)
            return 2
    if not isinstance(index, dict):
        print("the cache is not a mapping; refusing to touch it", file=sys.stderr)
        return 2

    found = collect(args.paths or [str(REPO_ROOT / "trajectories" / "dofus-*.jsonl")])
    if not found:
        print("no observation carried map.terrain in those files")
        return 1
    new, agree, disagree = plan(index, found)
    print("maps with terrain: %d | cache holds %d" % (len(found), len(index)))
    print("  not cached      : %d %s" % (len(new), new[:8]))
    print("  already agree   : %d" % len(agree))
    print("  disagree        : %d %s" % (len(disagree), disagree[:8]))
    if disagree and not args.update:
        print("  (a disagreement is a transient obstacle update or a different map "
              "revision; --update replaces it)")
    if not args.write:
        print("dry run: pass --write to add the %d missing map(s)" % len(new))
        return 0
    counts = apply(index, found, update=bool(args.update))
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, sort_keys=True), encoding="utf-8")
    print("wrote %s: %s" % (index_path, counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
