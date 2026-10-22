"""Crawl the Dofus Touch map graph from the asset CDN.

Why this exists
---------------
A non-vision agent has to know three things about a map before it can move on it:
which cells can be walked on, which map lies on the other side of each edge, and
what static objects (doors, zaaps, resource spots) are baked into the map. All
three are in the client's own map JSON, one file per map id, served without auth:

    https://dofustouch.cdn.ankama.com/assets/<build>/maps/<mapId>.json

The map JSON also carries ``topNeighbourId`` / ``bottomNeighbourId`` /
``leftNeighbourId`` / ``rightNeighbourId``, so the world graph is discovered by
crawling from a handful of seeds instead of needing a map-id list. Every map is
reachable from every other map through neighbours (that is what walking means),
so the crawl closes on the whole world.

Output: ``data/maps/<mapId>.json`` (raw, verbatim -- never rewrite the source)
        ``data/maps/_index.json`` (compact graph: id -> neighbours, cell count,
        walkable count, static-object count)

Usage::

    python scripts/fetch_maps.py --limit 200          # bounded probe
    python scripts/fetch_maps.py                      # full crawl, resumable
    python scripts/fetch_maps.py --report             # summarise what is on disk
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSET_ROOT = REPO_ROOT / "asset_cache" / "files" / "dofustouch.cdn.ankama.com" / "assets"
OUT_DIR = REPO_ROOT / "dofus-network" / "data" / "maps"
CDN = "https://dofustouch.cdn.ankama.com/assets/{build}/maps/{map_id}.json"

NEIGHBOUR_KEYS = ("topNeighbourId", "bottomNeighbourId",
                  "leftNeighbourId", "rightNeighbourId")

# Cell byte `l` is a bitfield: 0x40 (64) is set on every cell in the corpus, and
# bit 0 is what separates the playable diamond from the void. Measured, not
# assumed: on map 145489920 the cells with bit 0 set form one connected region
# with obstacle-shaped holes, while every cell in the surrounding rows reads 66
# (bit 0 clear). Values seen across 40 maps: 64, 66, 67, 69, 71 -- i.e. 64 | {0,2,3,5,7}.
WALKABLE_BIT = 1


def detect_build() -> str:
    """The build hash the local asset cache was populated from.

    Only directories that actually contain a ``maps/`` folder are candidates --
    the assets root also holds ``ui``, ``bones`` and friends, and picking one of
    those silently yields zero seeds.
    """
    if not ASSET_ROOT.exists():
        raise SystemExit(f"no asset cache at {ASSET_ROOT}")
    candidates = sorted(p.name for p in ASSET_ROOT.iterdir()
                        if p.is_dir() and (p / "maps").is_dir())
    if not candidates:
        raise SystemExit(f"no build with a maps/ folder under {ASSET_ROOT}")
    return candidates[-1]


def seed_ids() -> List[int]:
    """Every map id we know about: cached assets, crawled output, and the
    neighbours of everything already on disk.

    Expanding neighbours is what makes the crawl resumable. Without it, a run
    that stops with a non-empty frontier loses those ids and the next run
    starts from the same handful of seeds again.
    """
    seeds: Set[int] = set()
    for path in (ASSET_ROOT / detect_build() / "maps").glob("*.json"):
        try:
            seeds.add(int(path.stem))
        except ValueError:
            continue
    for path in OUT_DIR.glob("*.json"):
        if path.stem.startswith("_"):
            continue
        try:
            map_id = int(path.stem)
        except ValueError:
            continue
        seeds.add(map_id)
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key in NEIGHBOUR_KEYS:
            neighbour = blob.get(key)
            if isinstance(neighbour, int) and neighbour > 0:
                seeds.add(neighbour)
    return sorted(seeds)


def summarise(map_id: int, blob: dict) -> dict:
    cells = blob.get("cells") or []
    walkable = 0
    mask = []
    for cell in cells:
        level = cell.get("l")
        can_walk = isinstance(level, int) and bool(level & WALKABLE_BIT)
        mask.append("1" if can_walk else "0")
        if can_walk:
            walkable += 1
    static_objects = 0
    for layer in (blob.get("midgroundLayer") or {}).values():
        if isinstance(layer, list):
            static_objects += len(layer)
    return {
        "id": map_id,
        "neighbours": {key: blob.get(key) for key in NEIGHBOUR_KEYS},
        "cells": len(cells),
        "walkable": walkable,
        # The navigator needs the mask, not the count: 560 bytes of '0'/'1' in
        # cell order is far cheaper than shipping the raw map JSON to the agent.
        "walk": "".join(mask),
        "static_objects": static_objects,
        "has_triggers": bool(blob.get("triggers")),
    }


def fetch(build: str, map_id: int, timeout: float = 20.0) -> Optional[dict]:
    url = CDN.format(build=build, map_id=map_id)
    request = urllib.request.Request(url, headers={"User-Agent": "dofus-agent/map-crawl"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None            # not a real map id; the graph has holes
            time.sleep(0.6 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(0.6 * (attempt + 1))
    return None


def crawl(limit: int, workers: int, delay: float) -> int:
    build = detect_build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    on_disk = {int(p.stem) for p in OUT_DIR.glob("*.json") if not p.stem.startswith("_")}
    seeds = seed_ids()
    # Seeds are queued for this run, so they must count as known -- otherwise a
    # seed that is also a neighbour of another seed gets queued a second time
    # and the "fetched" counter stops matching the files on disk.
    known = set(on_disk) | set(seeds)
    frontier: List[int] = [i for i in seeds if i not in on_disk]
    if not frontier:
        print("nothing new to fetch; every seed is already on disk")
        return 0

    fetched = 0
    missing = 0
    started = time.time()
    index: Dict[str, dict] = {}

    def work(map_id: int) -> Tuple[int, Optional[dict]]:
        if delay:
            time.sleep(delay)
        return map_id, fetch(build, map_id)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        while frontier and fetched < limit:
            batch = frontier[: max(1, min(workers * 4, limit - fetched))]
            frontier = frontier[len(batch):]
            for map_id, blob in pool.map(work, batch):
                if blob is None:
                    missing += 1
                    continue
                (OUT_DIR / f"{map_id}.json").write_text(
                    json.dumps(blob, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
                entry = summarise(map_id, blob)
                index[str(map_id)] = entry
                fetched += 1
                for key in NEIGHBOUR_KEYS:
                    neighbour = blob.get(key)
                    if isinstance(neighbour, int) and neighbour > 0 and neighbour not in known:
                        known.add(neighbour)
                        frontier.append(neighbour)
            elapsed = time.time() - started
            rate = fetched / elapsed if elapsed else 0.0
            print(f"  {fetched} maps  frontier {len(frontier)}  "
                  f"missing {missing}  {rate:.1f}/s", flush=True)

    if index:
        merged: Dict[str, dict] = {}
        index_path = OUT_DIR / "_index.json"
        if index_path.exists():
            try:
                merged = json.loads(index_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                merged = {}
        merged.update(index)
        index_path.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    print(f"fetched {fetched} maps in {time.time() - started:.0f}s "
          f"({missing} ids returned 404, frontier left {len(frontier)})")
    return 0


def reindex() -> int:
    """Rebuild ``_index.json`` from the raw maps already on disk.

    Separate from the crawl so a change to what the index carries (the walk
    mask, for instance) does not require re-downloading 2,700 files.
    """
    files = [p for p in OUT_DIR.glob("*.json") if not p.stem.startswith("_")]
    if not files:
        print("no maps on disk to index")
        return 1
    index = {}
    for path in files:
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        index[str(path.stem)] = summarise(int(path.stem), blob)
    (OUT_DIR / "_index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8")
    print(f"reindexed {len(index)} maps -> {OUT_DIR / '_index.json'}")
    return report()


def report() -> int:
    files = [p for p in OUT_DIR.glob("*.json") if not p.stem.startswith("_")]
    if not files:
        print("no maps on disk yet")
        return 1
    cells = walkable = objects = 0
    edges: Set[Tuple[int, int]] = set()
    for path in files:
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entry = summarise(int(path.stem), blob)
        cells += entry["cells"]
        walkable += entry["walkable"]
        objects += entry["static_objects"]
        for neighbour in entry["neighbours"].values():
            if isinstance(neighbour, int) and neighbour > 0:
                edges.add((entry["id"], neighbour))
    print(f"maps      : {len(files)}")
    print(f"cells     : {cells:,} ({walkable:,} walkable, "
          f"{walkable / max(cells, 1):.1%})")
    print(f"static obj: {objects:,}")
    print(f"edges     : {len(edges):,} (directed)")
    print(f"disk      : {sum(p.stat().st_size for p in files) / 1e6:.1f} MB")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=100000,
                        help="stop after N new maps (default: no limit)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--delay", type=float, default=0.0,
                        help="seconds to sleep per request (politeness)")
    parser.add_argument("--report", action="store_true", help="summarise what is on disk")
    parser.add_argument("--reindex", action="store_true",
                        help="rebuild _index.json from the raw maps on disk")
    args = parser.parse_args(argv)
    if args.reindex:
        return reindex()
    if args.report:
        return report()
    return crawl(args.limit, args.workers, args.delay)


if __name__ == "__main__":
    raise SystemExit(main())
