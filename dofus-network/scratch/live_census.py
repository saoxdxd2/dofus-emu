"""Census of one live autopilot log: what ran, what failed, what looped.

`tools/session_report.py` reads `trajectories/default.jsonl` and `autopilot.jsonl`; the brain
daemon writes `autopilot.<instance>.jsonl` instead, so the live run is invisible to it. This
reads one of those directly and answers three questions a fix needs:

  * which tools ran and what verdict they got
  * which refusals repeat, grouped by their own text with the volatile numbers masked
  * whether the map sequence is a walk or a ping-pong

    python scratch/live_census.py                     # newest autopilot.<instance>.jsonl
    python scratch/live_census.py --path ... --tail 400
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "DofusFarm"


def rows(path: Path) -> List[Dict[str, Any]]:
    """Every JSON object in the file, tolerating two objects on one line."""
    decoder = json.JSONDecoder()
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            text = line.strip()
            index = 0
            while index < len(text):
                try:
                    obj, end = decoder.raw_decode(text, index)
                except ValueError:
                    break
                if isinstance(obj, dict):
                    out.append(obj)
                index = end
                while index < len(text) and text[index] in " \t":
                    index += 1
    return out


def newest() -> Optional[Path]:
    candidates = [p for p in LOG_DIR.glob("autopilot.*.jsonl") if ".2026" not in p.name]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def mask(text: str) -> str:
    """Collapse the numbers that make two instances of one refusal look different."""
    return re.sub(r"\b\d{4,}\b", "#", text or "")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=None)
    parser.add_argument("--tail", type=int, default=0,
                        help="only the last N steps (0 = the whole file)")
    args = parser.parse_args(argv)

    path = args.path or newest()
    if path is None:
        print("no autopilot.<instance>.jsonl under %s" % LOG_DIR)
        return 2
    everything = rows(path)
    steps = [r for r in everything if r.get("index") is not None and r.get("tool")]
    if args.tail:
        steps = steps[-args.tail:]
    print("log %s" % path.name)
    print("rows %d, steps %d, decisions %d"
          % (len(everything), len(steps),
             len([r for r in everything if r.get("kind") == "decision"])))

    verdicts = Counter(str(r.get("verdict")) for r in steps)
    tools = Counter(str(r.get("tool")) for r in steps)
    print("\nverdicts: %s" % dict(verdicts))
    print("tools:    %s" % dict(tools))

    errors = Counter()
    for row in steps:
        if row.get("ok") is False or row.get("error"):
            errors[mask(str(row.get("error") or row.get("why") or "?"))] += 1
    if errors:
        print("\nrefusals, most repeated first:")
        for text, count in errors.most_common(12):
            print("  %4d  %s" % (count, text[:150]))

    # A ping-pong is the same two maps alternating; a walk visits each map once.
    sequence: List[int] = []
    for row in steps:
        map_id = row.get("observation_map")
        if map_id and (not sequence or sequence[-1] != map_id):
            sequence.append(int(map_id))
    visits = Counter(sequence)
    repeats = {m: c for m, c in visits.items() if c > 3}
    print("\nmap sequence (%d changes): %s" % (len(sequence), sequence[-14:]))
    if repeats:
        print("maps visited more than 3 times: %s" % repeats)

    # The same (tool, args) asked twice in a row is a decision loop, not progress.
    loops: "OrderedDict[str, int]" = OrderedDict()
    previous = None
    for row in steps:
        key = "%s %s" % (row.get("tool"), json.dumps(row.get("args") or {}, sort_keys=True))
        loops[key] = loops.get(key, 0) + 1 if key == previous else 1
        previous = key
    worst = sorted(loops.items(), key=lambda kv: -kv[1])[:5]
    if worst and worst[0][1] > 1:
        print("\nlongest identical repeats:")
        for key, count in worst:
            if count > 1:
                print("  %3d x %s" % (count, key[:120]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
