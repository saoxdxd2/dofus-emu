"""Summarise recorded live sessions for the Full Auto baseline.

Reads either trajectory JSONL (observation/step rows) or the autopilot JSONL
(index/verdict/decision rows), splits it into sessions on a time gap, and
reports the numbers the goal is measured by: quest objectives completed, maps
visited, failures, recoveries, farm output and step latency.

Usage:
    python -m tools.session_report [paths...] [--gap 180] [--json]

With no path it reads dofus-network/trajectories/default.jsonl and
%LOCALAPPDATA%/DofusFarm/autopilot.jsonl when they exist. Each file is
summarised separately so the two views of one run are never double-counted.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRAJECTORY = REPO_ROOT / "trajectories" / "default.jsonl"
DEFAULT_AUTOPILOT = Path(os.environ.get("LOCALAPPDATA", "")) / "DofusFarm" / "autopilot.jsonl"
RECOVERY_ACTIONS = {"recover", "replan", "retry"}


def _ts(row: Dict[str, Any]) -> Optional[float]:
    for key in ("ts", "at"):
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    iso = row.get("iso")
    if isinstance(iso, str):
        try:
            return datetime.fromisoformat(iso).timestamp()
        except ValueError:
            return None
    return None


def _is_step(row: Dict[str, Any]) -> bool:
    if row.get("kind") == "step" and "tool" in row:
        return True
    return "index" in row and "tool" in row and ("verdict" in row or "ok" in row)


def _is_decision(row: Dict[str, Any]) -> bool:
    return row.get("kind") == "decision" and "action" in row


def _position(row: Dict[str, Any]) -> Dict[str, Any]:
    for candidate in (row.get("observation"), row.get("obs_before"), row.get("obs")):
        if isinstance(candidate, dict):
            pos = candidate.get("position")
            if isinstance(pos, dict):
                return pos
    return {}


def _obs(row: Dict[str, Any]) -> Dict[str, Any]:
    for candidate in (row.get("observation"), row.get("obs_before"), row.get("obs")):
        if isinstance(candidate, dict):
            return candidate
    return {}


def _pct(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * pct)))
    return round(ordered[index], 2)


def _summarise_session(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    steps = [r for r in rows if _is_step(r)]
    maps = set()
    goals: Counter = Counter()
    tools: Counter = Counter()
    errors: Counter = Counter()
    latency: List[float] = []
    first_objectives = last_objectives = None
    xp_values: List[float] = []
    kamas_values: List[float] = []
    levels: List[float] = []
    farm_kills = farm_xp = farm_kamas = 0.0
    for row in steps:
        goal = row.get("goal")
        if goal:
            goals[str(goal)] += 1
        tool = row.get("tool")
        if tool:
            tools[str(tool)] += 1
        if row.get("ok") is False or row.get("verdict") in ("failed", "blocked"):
            error = str(row.get("error") or row.get("note") or row.get("verdict") or "")[:120]
            errors[error] += 1
        if isinstance(row.get("latency_ms"), (int, float)):
            latency.append(float(row["latency_ms"]))
        pos = _position(row)
        if pos.get("map_id"):
            maps.add(pos["map_id"])
        elif row.get("observation_map"):
            maps.add(row["observation_map"])
        obs = _obs(row)
        completed = obs.get("objectives_completed")
        if isinstance(completed, (int, float)):
            if first_objectives is None:
                first_objectives = completed
            last_objectives = completed
        xp = obs.get("xp")
        if isinstance(xp, (int, float)):
            xp_values.append(float(xp))
        kamas = obs.get("kamas")
        if isinstance(kamas, (int, float)):
            kamas_values.append(float(kamas))
        char = obs.get("character") or {}
        level = char.get("level")
        if isinstance(level, (int, float)):
            levels.append(float(level))
        session = obs.get("farm_session") or {}
        for key, target in (("kills", "kills"), ("xp", "xp"), ("kamas", "kamas")):
            value = session.get(key)
            if isinstance(value, (int, float)):
                if target == "kills":
                    farm_kills = max(farm_kills, float(value))
                elif target == "xp":
                    farm_xp = max(farm_xp, float(value))
                else:
                    farm_kamas = max(farm_kamas, float(value))
    timestamps = [t for t in (_ts(r) for r in rows) if t is not None]
    start = min(timestamps) if timestamps else None
    end = max(timestamps) if timestamps else None
    return {
        "start": datetime.fromtimestamp(start, timezone.utc).isoformat() if start else None,
        "minutes": round((end - start) / 60.0, 2) if start is not None and end is not None else None,
        "steps": len(steps),
        "ok": sum(1 for r in steps if r.get("ok") is not False),
        "failed": sum(1 for r in steps if r.get("ok") is False
                      or r.get("verdict") in ("failed", "blocked")),
        "objectives_completed": (last_objectives - first_objectives
                                 if first_objectives is not None and last_objectives is not None
                                 else None),
        "maps": len(maps),
        "level": (max(levels) if levels else None),
        "xp_delta": (max(xp_values) - min(xp_values)) if xp_values else None,
        "kamas_delta": (max(kamas_values) - min(kamas_values)) if kamas_values else None,
        "farm": {"kills": farm_kills, "xp": farm_xp, "kamas": farm_kamas},
        "goals": dict(goals.most_common(4)),
        "tools": dict(tools.most_common(8)),
        "errors": dict(errors.most_common(5)),
        "latency_p50": _pct(latency, 0.50),
        "latency_p95": _pct(latency, 0.95),
    }


def summarise(path: Path, gap_s: float = 180.0) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    timed = [(t, r) for r in rows for t in [_ts(r)] if t is not None]
    timed.sort(key=lambda pair: pair[0])
    sessions: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    last: Optional[float] = None
    for t, row in timed:
        if last is not None and t - last > gap_s:
            if current:
                sessions.append(current)
            current = []
        current.append(row)
        last = t
    if current:
        sessions.append(current)
    summaries = [_summarise_session(session) for session in sessions]
    decisions = [r for r in rows if _is_decision(r)]
    decision_actions = Counter(str(r.get("action")) for r in decisions)
    # Rows the client's trusted-input instrument wrote: a human touched the game
    # while the loop was running. "Zero manual interventions" is only measurable
    # if this is counted, so it is reported even when it is zero.
    interventions = [r for r in rows if r.get("kind") == "intervention"]
    manual_interventions = sum(int(r.get("events") or 0) for r in interventions)
    total_minutes = sum(s["minutes"] or 0.0 for s in summaries)
    return {
        "file": str(path),
        "sessions": len(summaries),
        "wall_minutes": round(total_minutes, 1),
        "steps": sum(s["steps"] for s in summaries),
        "ok": sum(s["ok"] for s in summaries),
        "failed": sum(s["failed"] for s in summaries),
        "objectives_completed": sum(s["objectives_completed"] or 0
                                    for s in summaries),
        "maps": sum(s["maps"] for s in summaries),
        "manual_interventions": manual_interventions,
        "intervention_rows": len(interventions),
        "recoveries": {k: v for k, v in decision_actions.items()
                       if k in RECOVERY_ACTIONS},
        "per_session": summaries,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--gap", type=float, default=180.0,
                        help="seconds without a row that start a new session")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    paths = [Path(p) for p in args.paths]
    if not paths:
        paths = [p for p in (DEFAULT_TRAJECTORY, DEFAULT_AUTOPILOT) if p.exists()]
    reports = [summarise(path, args.gap) for path in paths if path.exists()]
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return 0
    for report in reports:
        print("== %s" % report["file"])
        print("  sessions %d, wall %.1f min, steps %d, ok %d, failed %d"
              % (report["sessions"], report["wall_minutes"], report["steps"],
                 report["ok"], report["failed"]))
        print("  objectives +%d, maps %d, manual interventions %d, recoveries %s"
              % (report["objectives_completed"], report["maps"],
                 report.get("manual_interventions", 0),
                 report["recoveries"] or "{}"))
        for row in report["per_session"]:
            print("    %s  %5s min  steps %4d  ok %4d  fail %3d  obj %s  maps %s  lvl %s"
                  % (row["start"] or "?", row["minutes"], row["steps"], row["ok"],
                     row["failed"], row["objectives_completed"], row["maps"],
                     row["level"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
