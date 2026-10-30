"""Which world action earned a quest objective, from the protocol log.

The project spent two days believing quest 4262's aqueduct pages were an unknown
mechanic, because the transition corpus only sees agent steps and the successful
action was in a `play_*.jsonl` protocol capture. There it is plain: an
`InteractiveUsedMessage` for the map's element, then a `QuestObjectiveValidatedMessage`
within a second. This tool turns that into a repeatable read of any capture: every
objective validation is attributed to the last interactive use on the same map inside
the window, and the character-to-element cell distance is computed when the captured
map cache can place the element. A use from too far is what silently failed before
(G69); an unattributed validation is a victory with no measured cause.

    python tools/quest_witness.py ../trajectories/play_dofus-03.jsonl --quest 4262
    python tools/quest_witness.py ../trajectories/play_*.jsonl --json

It reads files only. `--map-cache` points at the client's captured asset tree
(`asset_cache/files/dofustouch.cdn.ankama.com/assets` by default); when a map file is
absent the distance is `None` and the report says so instead of guessing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.planner import QuestPlanner  # the same cell metric the planner walks by

DEFAULT_MAP_CACHE = (REPO_ROOT.parent / "asset_cache" / "files"
                     / "dofustouch.cdn.ankama.com" / "assets")
WINDOW_MS = 5000.0
REQUEST_LINK_MS = 10000.0


def load_events(paths: Sequence[Path]) -> List[Tuple[float, Dict[str, Any], Path]]:
    out: List[Tuple[float, Dict[str, Any], Path]] = []
    for path in paths:
        try:
            fh = path.open("r", encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                event = row.get("event")
                at = row.get("at")
                if isinstance(event, dict) and isinstance(at, (int, float)):
                    out.append((float(at), event, path))
    out.sort(key=lambda item: item[0])
    return out


def element_cells(map_cache: Optional[Path], map_ids: Sequence[int]
                  ) -> Dict[Tuple[int, int], int]:
    """(map, element id) -> cell, from the client's captured map files.

    The map's midground layer carries `{"g": sprite, "id": element, ...}` per cell;
    the `id` is the interactive element id the server names in its use messages.
    """
    cells: Dict[Tuple[int, int], int] = {}
    if not map_cache:
        return cells
    for map_id in map_ids:
        for path in Path(map_cache).glob("*/maps/%d.json" % int(map_id)):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for cell, entries in (data.get("midgroundLayer") or {}).items():
                for entry in entries or []:
                    if isinstance(entry, dict) and entry.get("id") is not None:
                        cells[(int(map_id), int(entry["id"]))] = int(cell)
    return cells


def witness(paths: Sequence[Path], quests: Optional[Sequence[int]] = None,
            window_ms: float = WINDOW_MS,
            map_cache: Optional[Path] = DEFAULT_MAP_CACHE) -> Dict[str, Any]:
    """Pair validations with the accepted use that precedes them on the same map."""
    events = load_events(paths)
    uses: List[Dict[str, Any]] = []
    requests: List[Dict[str, Any]] = []
    validations: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []
    maps_seen = set()
    map_id: Optional[int] = None
    cell: Optional[int] = None
    for at, event, path in events:
        name = event.get("name")
        args = event.get("args") or {}
        if name == "CurrentMapMessage" and args.get("mapId") is not None:
            map_id = int(args["mapId"])
            maps_seen.add(map_id)
        elif event.get("kind") == "map_change":
            if event.get("to"):
                map_id = int(event["to"])
                maps_seen.add(map_id)
            if event.get("cell") is not None:
                cell = int(event["cell"])
        elif event.get("kind") == "state_delta":
            state = event.get("state") or {}
            if state.get("map"):
                map_id = int(state["map"])
                maps_seen.add(map_id)
            if state.get("cell") is not None:
                cell = int(state["cell"])
        elif name == "InteractiveUseRequestMessage" and args.get("elemId") is not None:
            requests.append({"at": at, "elem": int(args["elemId"]),
                             "skill_instance": args.get("skillInstanceUid"),
                             "map": map_id, "log": path.name})
        elif name == "InteractiveUsedMessage" and args.get("elemId") is not None:
            uses.append({"at": at, "elem": int(args["elemId"]),
                         "skill_id": args.get("skillId"), "map": map_id,
                         "cell": cell, "log": path.name})
        elif name == "QuestObjectiveValidatedMessage":
            if args.get("objectiveId") is None:
                continue
            validations.append({"at": at, "quest": args.get("questId"),
                                "objective": args.get("objectiveId"),
                                "map": map_id, "log": path.name})
        elif name == "QuestStepValidatedMessage":
            steps.append({"at": at, "quest": args.get("questId"),
                          "step": args.get("stepId"), "map": map_id,
                          "log": path.name})
    for use in uses:
        # The request carries the instance uid, the ack the skill id; they are the
        # same interaction when the element matches and the request comes just before.
        best = None
        for request in requests:
            if request["elem"] != use["elem"]:
                continue
            if not 0.0 <= use["at"] - request["at"] <= REQUEST_LINK_MS:
                continue
            if best is None or request["at"] > best["at"]:
                best = request
        use["skill_instance"] = best["skill_instance"] if best else None
    cells = element_cells(map_cache, sorted(maps_seen))

    def distance(use: Dict[str, Any]) -> Optional[int]:
        if use.get("map") is None or use.get("cell") is None:
            return None
        element_cell = cells.get((int(use["map"]), int(use["elem"])))
        if element_cell is None:
            return None
        return QuestPlanner._cell_distance(use["cell"], element_cell)

    for use in uses:
        use["element_cell"] = cells.get((int(use["map"]), int(use["elem"]))
                                        if use.get("map") is not None else (-1, -1))
        use["distance"] = distance(use)
        use["adjacent"] = None if use["distance"] is None else use["distance"] <= 1
    for validation in validations:
        candidates = [use for use in uses
                      if 0.0 <= validation["at"] - use["at"] <= window_ms
                      and (validation["map"] is None or use["map"] is None
                           or validation["map"] == use["map"])]
        use = max(candidates, key=lambda u: u["at"]) if candidates else None
        validation["use"] = use
        validation["delay_ms"] = (round(validation["at"] - use["at"], 1)
                                  if use else None)
        validation["distance"] = use["distance"] if use else None
        validation["attributed"] = (use is not None
                                    and (use["distance"] is None
                                         or use["distance"] <= 1))
    attributed = {(v["log"], v["at"]) for v in validations if v.get("use")}
    failed = [use for use in uses if not any(
        v.get("use") is use for v in validations)]
    if quests is not None:
        wanted = {int(q) for q in quests}
        shown_validations = [v for v in validations if v["quest"] in wanted]
        shown_steps = [s for s in steps if s["quest"] in wanted]
    else:
        shown_validations = validations
        shown_steps = steps
    return {"events": len(events), "uses": uses,
            "validations": shown_validations, "steps": shown_steps,
            "failed_uses": failed,
            "unattributed": [v for v in shown_validations if not v["attributed"]],
            "attributed_count": len(attributed)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", nargs="+", type=Path,
                    help="protocol captures (trajectories/play_*.jsonl)")
    ap.add_argument("--quest", type=int, action="append",
                    help="only this quest id (repeatable)")
    ap.add_argument("--window", type=float, default=WINDOW_MS,
                    help="milliseconds from the accepted use to the validation")
    ap.add_argument("--map-cache", type=Path, default=DEFAULT_MAP_CACHE)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    result = witness(args.logs, args.quest, args.window, args.map_cache)
    if args.json:
        print(json.dumps(result, indent=1))
        return 0
    print("events %d  accepted uses %d  validations %d  attributed %d"
          % (result["events"], len(result["uses"]), len(result["validations"]),
             len([v for v in result["validations"] if v["attributed"]])))
    for v in result["validations"]:
        use = v.get("use")
        if use:
            print("  %-6s %s/%s  use %s skill %s from cell %s (element %s, dist %s) "
                  "+%sms  [%s]"
                  % ("PASS" if v["attributed"] else "FAR",
                     v["quest"], v["objective"], use["elem"],
                     use["skill_id"] if use["skill_id"] is not None
                     else use["skill_instance"], use["cell"], use["element_cell"],
                     v["distance"], v["delay_ms"], v["log"]))
        else:
            print("  NO USE %s/%s at %s  [%s]"
                  % (v["quest"], v["objective"], v["at"], v["log"]))
    for use in result["failed_uses"]:
        print("  FAILED USE %s skill %s from cell %s (element %s, dist %s)  [%s]"
              % (use["elem"], use["skill_id"] if use["skill_id"] is not None
                 else use["skill_instance"], use["cell"], use["element_cell"],
                 use["distance"], use["log"]))
    for step in result["steps"]:
        print("  step %s/%s at %s  [%s]"
              % (step["quest"], step["step"], step["at"], step["log"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
