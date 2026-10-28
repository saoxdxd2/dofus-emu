"""Why did the live loop never see a monster group? Read the census, do not guess.

Why this exists. Every one of the 4,782 recorded observations carries ``map.groups == []``, and
that empty list is what the planner was handed each time it wanted to join a fight -- the
recorded ``fight.join`` refusals say "no group on the map" (KNOWN_GAPS G7). An empty list has
three causes with opposite fixes: the actor collection could not be reached, the type filter
never matches what the client names an actor, or the maps genuinely had no group. The page now
records which one it was (``_listMonsterGroups`` -> ``map.groups_scan``), and this reads it.

Reads only. Rows written before the census carry no ``groups_scan`` and are counted as such --
they are evidence that the question was unanswerable, not evidence about the client.

    python tools/group_scan.py                              # default corpus
    python tools/group_scan.py path/to/default.jsonl
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_LOG = Path("trajectories/default.jsonl")
# The loop's own log holds the decision rows, and they are not in the trajectory file. Reading
# only the default corpus would answer "the census says nothing" while the run's refusals -- the
# other half of the same question -- sat unread in another directory.
AGENT_LOG = (Path(os.environ.get("LOCALAPPDATA", "")) / "DofusFarm" / "autopilot.jsonl"
             if os.environ.get("LOCALAPPDATA") else None)
GROUP_TYPE = "GameRolePlayGroupMonsterInformations"
# The two calls that ask the client to find a group. Their refusals are the other half of the
# census: the scan says what the page could see, the decision rows say what the loop did about it.
GROUP_REFUSAL_TOOLS = ("farm.start", "fight.join")


def _refusals(paths: List[Path]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if (row.get("kind") == "decision" and row.get("tool") in GROUP_REFUSAL_TOOLS
                        and row.get("ok") is False):
                    out.append(row)
    return out


def _observations(paths: List[Path]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            print("absent: %s" % path)
            continue
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                obs = row.get("observation")
                if isinstance(obs, dict) and isinstance(obs.get("map"), dict):
                    out.append(obs["map"])
    return out


def _verdict(maps: List[Dict[str, Any]],
             refusals: Optional[List[Dict[str, Any]]] = None) -> str:
    """One paragraph, decided by the census rather than by the empty list."""
    refusals = refusals or []
    # `stale_state` on a group refusal is the label the controller gives when the page walked
    # groups and read no member id: the reading broke, the map is not empty, and travelling is
    # the wrong repair. A corpus with no census can still say that much.
    unread = [r for r in refusals if r.get("cause") == "stale_state"
              and "group" in str(r.get("error") or "").lower()]
    if not maps:
        return ("VERDICT: no map blocks in these logs at all, so nothing here is evidence "
                "about a live map.")
    scanned = [m for m in maps if isinstance(m.get("groups_scan"), dict)]
    if not scanned:
        if unread:
            return ("VERDICT: no scan in this corpus, but %d group refusal(s) were labelled "
                    "`stale_state` -- the page iterated groups and read no member id, so the "
                    "blocker is the client read, not the map." % len(unread))
        return ("VERDICT: %d map observations, none carrying a scan -- this corpus predates "
                "the census, so it shows that every group list was empty and nothing about "
                "why. Only a newly captured run answers G7." % len(maps))

    no_manager = [m for m in scanned if not m["groups_scan"].get("manager")]
    no_collection = [m for m in scanned if m["groups_scan"].get("manager")
                     and m["groups_scan"].get("path") in (None, "none")]
    iterated = [m for m in scanned if m["groups_scan"].get("manager")
                and m["groups_scan"].get("path") not in (None, "none")]
    matched = [m for m in iterated if (m["groups_scan"].get("matched") or 0) > 0]
    never = [m for m in iterated if (m["groups_scan"].get("matched") or 0) == 0]
    print("  with a group: %d   without: %d" % (len(matched), len(never)))
    names: collections.Counter = collections.Counter()
    for m in iterated:
        for key, count in (m["groups_scan"].get("types") or {}).items():
            names[str(key)] += int(count or 0)
    top = ", ".join("%s x%d" % (k, v) for k, v in names.most_common(8))
    if matched:
        return ("VERDICT: the scan found %d observation(s) with a live group, so the actor "
                "path and the type filter both work. An empty `groups` is therefore a real "
                "property of the map, and a run that never farms is a routing question, not a "
                "reading one." % len(matched))
    if no_manager or no_collection:
        return ("VERDICT: %d scan(s) reached no actor manager and %d found no collection to "
                "iterate -- the collection itself is the blocker. `actors` is the name this "
                "bundle initialises, and the same manager also has `getActorsIDList()`/"
                "`getActor(id)`; `actorList` is not a name it exposes at all. Names seen where "
                "it was reachable: %s" % (len(no_manager), len(no_collection), top or "none"))
    return ("VERDICT: the collection was reached and %d actor(s) were iterated, but none was "
            "named `%s`; the client calls them: %s. The filter is the bug -- fix the name, do "
            "not conclude the map is empty." % (len(never), GROUP_TYPE, top or "none"))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, default=None)
    args = parser.parse_args(argv)
    paths = [Path(p) for p in (args.paths or [])] or [DEFAULT_LOG]
    if not args.paths and AGENT_LOG is not None and AGENT_LOG.exists():
        paths.append(AGENT_LOG)
    maps = _observations(paths)
    refusals = _refusals(paths)
    print("map observations: %d   source: %s"
          % (len(maps), ", ".join(str(p) for p in paths)))
    if refusals:
        causes = collections.Counter(str(r.get("cause")) for r in refusals)
        print("group refusals: %d   by cause: %s"
              % (len(refusals), ", ".join("%s=%d" % kv for kv in causes.most_common())))
    else:
        print("group refusals: none recorded in these logs")
    scanned = [m for m in maps if isinstance(m.get("groups_scan"), dict)]
    if scanned:
        seen = sum(int((m["groups_scan"].get("seen") or 0)) for m in scanned)
        matched = sum(int((m["groups_scan"].get("matched") or 0)) for m in scanned)
        paths_used = collections.Counter(str(m["groups_scan"].get("path")) for m in scanned)
        print("scans: %d   actors iterated: %d   groups matched: %d   path: %s"
              % (len(scanned), seen, matched,
                 ", ".join("%s=%d" % kv for kv in paths_used.most_common())))
    verdict = _verdict(maps, refusals)
    print()
    print(verdict)
    answered = bool(scanned) and bool(maps)
    return 0 if answered else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
