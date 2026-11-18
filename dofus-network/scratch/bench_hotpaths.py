"""Microbenchmark for the agent hot paths touched by the optimisation pass.

    python scratch/bench_hotpaths.py [observations]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.observe import flatten, map_id_of  # noqa: E402
from agent import world_state as ws  # noqa: E402


def load(limit: int):
    path = ROOT / "trajectories" / "dofus-01.20261108-205337-063447.jsonl"
    rows = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            obs = record.get("observation") if isinstance(record, dict) else None
            if isinstance(obs, dict):
                rows.append(obs)
                if len(rows) >= limit:
                    break
    return rows


def timed(label, fn, rows, repeats=1):
    best = None
    for _ in range(repeats):
        started = time.perf_counter()
        for obs in rows:
            fn(obs)
        elapsed = time.perf_counter() - started
        best = elapsed if best is None else min(best, elapsed)
    print("%-28s %8.3f ms  (%d obs)" % (label, best * 1000.0, len(rows)))
    return best


def main(argv):
    limit = int(argv[1]) if len(argv) > 1 else 2000
    rows = load(limit)
    print("loaded %d observations" % len(rows))

    timed("from_observation", ws.from_observation, rows, repeats=3)
    states = [ws.from_observation(obs) for obs in rows]
    started = time.perf_counter()
    for state in states:
        ws.encode(state)
    print("%-28s %8.3f ms  (%d states)"
          % ("encode", (time.perf_counter() - started) * 1000.0, len(states)))

    timed("flatten", flatten, rows, repeats=3)
    timed("map_id_of", map_id_of, rows, repeats=3)

    terrain = "9" * 140
    started = time.perf_counter()
    for _ in range(200):
        ws.walkable_cells(terrain)
    print("%-28s %8.3f ms  (200 calls, 560 cells)"
          % ("walkable_cells", (time.perf_counter() - started) * 1000.0))


if __name__ == "__main__":
    main(sys.argv)
