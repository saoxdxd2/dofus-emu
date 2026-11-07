"""Count the explore hops that walked straight back to the map just left.

Why this exists: G83 fixed `plan_stack._direction`, which used to pick the first usable
exit without caring which map the character had just come from. On the newest recorded
session that made the loop leave a group-less fight map the way it came and return, 23
round trips in 134 steps. There is no newer live log to read, so the fix is measured
against the recordings that exposed it: every direction-shaped `nav.goto` row is replayed
through the current `_direction` on its own observation, and the rows that walked back to
`last_nav.from_map` are those the fix targets.

The replay is a counterfactual, not a live rerun: a recorded direction may have come from
the policy's own explore (which already avoided the previous map) rather than the stack.
A row counts as "the fixed explore would not do this" only when the returned direction
differs, which is why the report prints both columns.

Usage:
    python -m tools.replay_explore_returns [paths...] [--since-build 2026-10-29] [--json]

With no path it reads every `trajectories/*.jsonl`.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
# `python tools/x.py` puts tools/ on sys.path and the repo root nowhere, so the agent
# package below is unimportable unless the root is inserted first.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.observe import flatten  # noqa: E402
from agent.plan_stack import _direction  # noqa: E402

DEFAULT_GLOB = str(REPO_ROOT / "trajectories" / "*.jsonl")


def _snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    obs = row.get("observation")
    if isinstance(obs, dict) and isinstance(obs.get("snapshot"), dict):
        obs = obs["snapshot"]
    if isinstance(obs, dict) and isinstance(obs.get("observation"), dict):
        obs = obs["observation"]
    return obs if isinstance(obs, dict) else {}


def rows(paths: Iterable[str], since_build: Optional[str]) -> Iterable[Dict[str, Any]]:
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
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or row.get("kind") != "step":
                    continue
                if row.get("tool") != "nav.goto":
                    continue
                args = row.get("args") or {}
                if not isinstance(args, dict) or args.get("direction") is None:
                    continue
                obs = _snapshot(row)
                build = str(obs.get("agent_build") or "")
                if since_build and build < since_build:
                    continue
                yield {"row": row, "obs": obs, "build": build}


def measure(paths: Iterable[str], since_build: Optional[str] = None) -> Dict[str, Any]:
    calls = returns = changed = 0
    by_build: Counter = Counter()
    examples: List[Dict[str, Any]] = []
    for item in rows(paths, since_build):
        calls += 1
        obs, row = item["obs"], item["row"]
        flat = flatten(obs)
        here = flat.get("map_id")
        nav = obs.get("last_nav") if isinstance(obs.get("last_nav"), dict) else {}
        try:
            back = (int(nav.get("from_map"))
                    if nav.get("to_map") is not None and int(nav["to_map"]) == int(here)
                    else None)
        except (TypeError, ValueError):
            back = None
        exits = {}
        for row_exit in ((obs.get("map") or {}).get("exits") or []):
            if isinstance(row_exit, dict) and row_exit.get("direction"):
                exits[str(row_exit["direction"])] = row_exit.get("map_id")
        recorded = str((row.get("args") or {}).get("direction"))
        if back is None or exits.get(recorded) != back:
            continue
        returns += 1
        by_build[item["build"] or "?"] += 1
        fixed = _direction(obs)
        if fixed != recorded:
            changed += 1
            if len(examples) < 5:
                examples.append({"build": item["build"], "map": here,
                                 "recorded": recorded, "would_be": fixed})
    return {"calls": calls, "returns": returns,
            "fixed_would_change": changed, "returns_by_build": dict(by_build),
            "examples": examples}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="trajectory files (default: all)")
    parser.add_argument("--since-build", default=None,
                        help="only rows whose page build sorts at or after this stamp")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    paths = args.paths or sorted(glob.glob(DEFAULT_GLOB))
    result = measure(paths, args.since_build)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    print("direction-shaped nav.goto rows: %d" % result["calls"])
    print("walked back to the map just left: %d" % result["returns"])
    print("the current explore would choose another edge: %d" % result["fixed_would_change"])
    if result["returns_by_build"]:
        print("returns by page build: %s" % result["returns_by_build"])
    for row in result["examples"]:
        print("  %s map %s: walked %s, would walk %s"
              % (row["build"], row["map"], row["recorded"], row["would_be"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
