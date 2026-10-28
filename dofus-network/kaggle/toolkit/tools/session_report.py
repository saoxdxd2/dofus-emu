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

try:
    from tools.quest_advance_shapes import _page_answer, shape as quest_shape
except Exception:      # run as a script: tools/ is sys.path[0], not the repo root
    from quest_advance_shapes import _page_answer, shape as quest_shape

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


def _observations(rows: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    """One observation per row, in the order the row carries it."""
    for row in rows:
        for key in ("observation", "obs_before", "obs_after", "obs", "snapshot"):
            node = row.get(key)
            if isinstance(node, dict):
                yield node
                break


def witnesses(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """The live readouts for the instruments added on 2026-10-27, in one place.

    Each of G9 (identity), G29 (observed quest graph), G32/G33 (the offer and the
    accept) writes a different row, and a run that predates one of them must say so
    instead of printing a zero that reads like success. `verdicts` is the sentence
    the next baseline quotes. The page build is counted first: a client that was not
    restarted keeps the previous script, and no absence verdict means anything until
    the build says the new page is loaded.
    """
    knowledge: Counter = Counter()
    observed_from: Counter = Counter()
    builds: Counter = Counter()
    catalog = {"rows": 0, "quests": 0, "steps": 0, "objectives": 0,
               "status_rows": 0, "truncated": 0, "dialog_offers": 0}
    identity: Counter = Counter()
    identity_names: List[str] = []
    accept = {"rows": 0, "ok": 0, "start_rows": 0, "start_in_dialog": 0}
    shadow = {"status": None, "rows": 0, "usable": 0, "agree_intent": 0,
              "agree_command": 0, "would_override": 0, "examples": []}
    page: Counter = Counter()
    rows = list(rows)

    for row in rows:
        if row.get("kind") == "command_shadow_status":
            shadow["status"] = {key: row.get(key) for key in
                                ("available", "error", "model", "mode")}
        elif row.get("kind") == "command_shadow":
            shadow["rows"] += 1
            shadow["usable"] += bool(row.get("usable"))
            shadow["agree_intent"] += bool(row.get("agree_intent"))
            shadow["agree_command"] += bool(row.get("agree_command"))
            if row.get("would_override"):
                shadow["would_override"] += 1
                if len(shadow["examples"]) < 3:
                    shadow["examples"].append(str(row.get("text"))[:60])
        if row.get("kind") != "identity":
            continue
        event = str(row.get("event") or "unknown")
        identity[event] += 1
        name = row.get("name")
        seen = row.get("seen")
        if not name and isinstance(seen, dict):
            name = seen.get("name")
        if name and len(identity_names) < 8 and str(name) not in identity_names:
            identity_names.append(str(name))

    for obs in _observations(rows):
        build = obs.get("agent_build")
        builds[str(build) if build else "missing"] += 1
        kn = obs.get("knowledge")
        status = kn.get("status") if isinstance(kn, dict) else None
        knowledge[str(status or "missing")] += 1
        if status == "observed" and isinstance(kn, dict):
            observed_from[str(kn.get("observed_from") or "unknown")] += 1
        cat = obs.get("quests_catalog")
        if isinstance(cat, list):
            catalog["rows"] += 1
            for quest in cat:
                if not isinstance(quest, dict):
                    continue
                catalog["quests"] += 1
                if quest.get("truncated"):
                    catalog["truncated"] += 1
                for step in quest.get("steps") or []:
                    if not isinstance(step, dict):
                        continue
                    catalog["steps"] += 1
                    catalog["objectives"] += sum(
                        1 for obj in (step.get("objectives") or [])
                        if isinstance(obj, dict))
        state = obs.get("quest_catalog_status")
        if isinstance(state, dict):
            catalog["status_rows"] += 1
            if state.get("truncated"):
                catalog["truncated"] += 1
        dialog = obs.get("dialog")
        if isinstance(dialog, dict) and dialog.get("start_quest") is not None:
            catalog["dialog_offers"] += 1

    for row in rows:
        if row.get("tool") != "quest.advance":
            continue
        args = row.get("args") if isinstance(row.get("args"), dict) else {}
        if args.get("accept") is True:
            accept["rows"] += 1
            if row.get("ok") is not False:
                accept["ok"] += 1
        else:
            shp = quest_shape(args)
            if shp == "start":
                accept["start_rows"] += 1
                obs = _obs(row)
                dialog = obs.get("dialog")
                if obs.get("in_dialog") is True or (
                        isinstance(dialog, dict) and dialog.get("open")):
                    accept["start_in_dialog"] += 1
        page[str(_page_answer(row)[1])[:60]] += 1

    verdicts: List[str] = []
    if not builds:
        verdicts.append("page build: no observation carried agent_build")
    else:
        verdicts.append("page build: %s" % ", ".join(
            "%s=%d" % (name, count) for name, count in builds.most_common(4)))
    if not knowledge:
        verdicts.append("knowledge: no observation carried a knowledge block")
    else:
        verdicts.append(
            "knowledge: %d observed (catalog %d, file %d); %d not in store, "
            "%d collision"
            % (knowledge.get("observed", 0), observed_from.get("catalog", 0),
               observed_from.get("file", 0),
               knowledge.get("objective_not_in_store", 0),
               knowledge.get("objective_id_collision", 0)))
    if catalog["rows"] == 0:
        verdicts.append("quests_catalog: none in these rows -- the page build "
                        "predates G29")
    else:
        verdicts.append(
            "quests_catalog: %d row(s), %d quest(s), %d objective(s)%s"
            % (catalog["rows"], catalog["quests"], catalog["objectives"],
               " (truncated)" if catalog["truncated"] else ""))
    if identity.get("bound", 0) == 0 and identity.get("drift", 0) == 0:
        verdicts.append("identity: no bound/drift rows -- the run predates the guard "
                        "or never bound")
    else:
        verdicts.append(
            "identity: bound %d, drift %d%s"
            % (identity.get("bound", 0), identity.get("drift", 0),
               (" (" + ", ".join(identity_names) + ")") if identity_names else ""))
    if accept["rows"] == 0 and catalog["dialog_offers"] == 0:
        verdicts.append("accept: no explicit accept rows and no dialog carried "
                        "start_quest")
    else:
        verdicts.append(
            "accept: %d row(s) (ok %d), %d dialog offer(s) seen, %d start-shaped "
            "inside a dialog; page %s"
            % (accept["rows"], accept["ok"], catalog["dialog_offers"],
               accept["start_in_dialog"], dict(page.most_common(4))))
    status = shadow["status"]
    if status is None and shadow["rows"] == 0:
        verdicts.append("command_shadow: no status/row -- run did not request "
                        "--command-shadow")
    elif status is not None and not status.get("available"):
        verdicts.append("command_shadow: unavailable (%s)"
                        % (status.get("error") or "unknown"))
    else:
        verdicts.append(
            "command_shadow: %d row(s), usable %d, agree I/C %d/%d, would override %d%s"
            % (shadow["rows"], shadow["usable"], shadow["agree_intent"],
               shadow["agree_command"], shadow["would_override"],
               (" (" + "; ".join(shadow["examples"]) + ")")
               if shadow["examples"] else ""))
    return {
        "knowledge": dict(knowledge),
        "observed_from": dict(observed_from),
        "builds": dict(builds),
        "catalog": catalog,
        "identity": dict(identity),
        "identity_names": identity_names,
        "accept": accept,
        "command_shadow": shadow,
        "page": dict(page.most_common(8)),
        "verdicts": verdicts,
    }


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
    # Quests the character has finished, read from the client's finished list. Unlike
    # `objectives_completed` (which was 0 in every recorded row because the page derived it
    # from a field the client only sets on finished quests) this one can only go up.
    first_finished = last_finished = None
    xp_values: List[float] = []
    kamas_values: List[float] = []
    levels: List[float] = []
    farm_kills = farm_xp = farm_kamas = 0.0
    # The ready-objective census. `objectives_completed` says nothing about *why* it
    # is zero: the world may have had nothing to claim, or the loop may have looked at
    # one entry and never reached the rest. Those two read the same in the total and
    # need opposite fixes, so the shape of the list is recorded next to it.
    ready_steps = 0
    ready_behind = 0
    ready_heads: Counter = Counter()
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
        ready = obs.get("ready_objectives")
        if isinstance(ready, list) and ready:
            ready_steps += 1
            if len(ready) > 1:
                ready_behind += 1
            head = ready[0] if isinstance(ready[0], dict) else {}
            ready_heads[str(head.get("text") or head.get("objective_id") or "?")] += 1
        completed = obs.get("objectives_completed")
        finished = obs.get("quests_finished")
        if isinstance(finished, (int, float)):
            if first_finished is None:
                first_finished = finished
            last_finished = max(last_finished or 0, finished)
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
        # Whether the counter was readable at all. A log that never carried it cannot
        # support a claim about what it did not do.
        "objectives_observed": first_objectives is not None,
        "quests_completed": (last_finished - first_finished
                             if first_finished is not None and last_finished is not None
                             else None),
        "ready_steps": ready_steps,
        "ready_steps_with_another_entry": ready_behind,
        "ready_head": (ready_heads.most_common(1)[0][0] if ready_heads else None),
        "ready_head_count": (ready_heads.most_common(1)[0][1] if ready_heads else 0),
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
    heads: Counter = Counter()
    for s in summaries:
        if s.get("ready_head"):
            heads[str(s["ready_head"])] += int(s.get("ready_head_count") or 0)
    top_head = heads.most_common(1)
    return {
        "file": str(path),
        "sessions": len(summaries),
        "wall_minutes": round(total_minutes, 1),
        "steps": sum(s["steps"] for s in summaries),
        "ok": sum(s["ok"] for s in summaries),
        "failed": sum(s["failed"] for s in summaries),
        "objectives_completed": sum(s["objectives_completed"] or 0
                                    for s in summaries),
        "quests_completed": sum(s.get("quests_completed") or 0 for s in summaries),
        "ready_steps": sum(int(s.get("ready_steps") or 0) for s in summaries),
        "ready_steps_with_another_entry": sum(
            int(s.get("ready_steps_with_another_entry") or 0) for s in summaries),
        "ready_head": (top_head[0][0] if top_head else None),
        "ready_head_count": (top_head[0][1] if top_head else 0),
        "objectives_measured": sum(1 for s in summaries if s.get("objectives_observed")),
        "maps": sum(s["maps"] for s in summaries),
        "manual_interventions": manual_interventions,
        "intervention_rows": len(interventions),
        "recoveries": {k: v for k, v in decision_actions.items()
                       if k in RECOVERY_ACTIONS},
        "witnesses": witnesses(rows),
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
        print("  objectives +%d, quests completed +%d, maps %d, manual interventions %d, "
              "recoveries %s"
              % (report["objectives_completed"], report["quests_completed"],
                 report["maps"],
                 report.get("manual_interventions", 0),
                 report["recoveries"] or "{}"))
        for line in (report.get("witnesses") or {}).get("verdicts") or []:
            print("  " + line)
        if (report["objectives_completed"] == 0 and report["ready_steps"]
                and report["objectives_measured"]):
            # The two readings that a total cannot tell apart: nothing was ever ready,
            # or something was ready and the plan never reached it.
            print('  ! %d step(s) carried a ready objective and none was completed; '
                  'the head of that list was "%s" in %d of them, and %d step(s) had '
                  'another entry behind it'
                  % (report["ready_steps"], report["ready_head"],
                     report["ready_head_count"],
                     report["ready_steps_with_another_entry"]))
        for row in report["per_session"]:
            print("    %s  %5s min  steps %4d  ok %4d  fail %3d  obj %s  maps %s  lvl %s"
                  % (row["start"] or "?", row["minutes"], row["steps"], row["ok"],
                     row["failed"], row["objectives_completed"], row["maps"],
                     row["level"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
