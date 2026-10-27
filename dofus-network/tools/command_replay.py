"""Pair recorded goals with the outcome their executed plan produced.

The shadow says what the learned parser would have proposed; the step rows say
what the rule/planner actually did and what changed in the world. This tool joins
the two into one observational row per (session, goal):

    goal, rule/model parse, mode agreement, executed tools/verdicts,
    objectives/xp/kamas delta

It is **not** a counterfactual: the executed steps were chosen by the rules, not
by the model, so the delta measures the rule's plan. It is the replay substrate
for a later guarded adapter and a calibration set for "would an override have
routed this goal differently?" The live `--command-shadow auto` rows carry the
model proposal directly; this tool computes it offline when the run predates the
hook.

    python tools/command_replay.py --out data/command_replay.jsonl
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.command_shadow import DEFAULT_MODEL, load_command_model, shadow_row  # noqa: E402

READ_TOOLS = {"state.snapshot", "quest.list", "inv.list", "hdv.scan",
              "knowledge.search"}


def default_logs() -> List[Path]:
    patterns = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        patterns.append(str(Path(local) / "DofusFarm" / "autopilot*.jsonl"))
    patterns.append(str(ROOT / "trajectories" / "autopilot*.jsonl"))
    paths: List[Path] = []
    for pattern in patterns:
        paths.extend(Path(match) for match in glob.glob(pattern))
    return sorted({path for path in paths if path.is_file()})


def _read(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _obs_number(obs: Any, key: str) -> Optional[float]:
    if not isinstance(obs, dict):
        return None
    value = obs.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _metric(steps: List[Dict[str, Any]], obs_key: str, field: str,
            reverse: bool = False) -> Optional[float]:
    """First (or last) step whose observation actually carries ``field``.

    Some logs carry an empty observation block on early rows; taking the first
    dict rather than the first dict with the field turned a real zero into null.
    """
    ordered = sorted(steps, key=lambda row: float(row.get("at") or 0.0),
                     reverse=reverse)
    for row in ordered:
        value = _obs_number(row.get(obs_key), field)
        if value is not None:
            return value
    return None


def classify_stall(steps: int, tools: Dict[str, int], verdicts: Dict[str, int],
                   goal_outcome: str) -> Optional[str]:
    """Why a goal made no progress. The recorded corpus is not read-bound: advanced
    groups are two-thirds reads, while the stalls are short fight.join failures."""
    if goal_outcome != "stalled":
        return None
    read_steps = sum(tools.get(tool, 0) for tool in READ_TOOLS)
    if steps >= 6 and read_steps / max(1, steps) >= 0.7:
        return "read_loop"
    if tools.get("fight.join", 0) >= 2:
        return "fight_join"
    if steps <= 6:
        return "short_fail"
    return "other"


def group_steps(path: Path, rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One group per contiguous (session, goal) run of executed steps."""
    groups: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    for row in rows:
        if row.get("kind") == "decision" or not row.get("goal"):
            continue
        if not row.get("tool"):
            continue
        if not (isinstance(row.get("obs_before"), dict)
                or isinstance(row.get("obs_after"), dict)):
            continue
        goal = " ".join(str(row.get("goal")).split())
        session = str(row.get("session") or path.stem)
        key = (session, goal)
        # A goal restart is a new group even when the same text repeats.
        index = row.get("index")
        if (not current or current["key"] != key
                or (isinstance(index, int) and index == 0
                    and current["steps"])):
            current = {"key": key, "session": session, "goal": goal,
                       "steps": [], "source": str(path)}
            groups.append(current)
        current["steps"].append(row)
    return [group for group in groups if group["steps"]]


def build_replay(groups: Sequence[Dict[str, Any]], model: Any,
                 threshold: float = 0.55) -> List[Dict[str, Any]]:
    replay: List[Dict[str, Any]] = []
    for group in groups:
        steps = group["steps"]
        shadow = shadow_row(group["goal"], model, threshold)
        objectives_before = _metric(steps, "obs_before", "objectives_completed")
        objectives_after = _metric(steps, "obs_after", "objectives_completed",
                                   reverse=True)
        xp_before = _metric(steps, "obs_before", "xp")
        xp_after = _metric(steps, "obs_after", "xp", reverse=True)
        kamas_before = _metric(steps, "obs_before", "kamas")
        kamas_after = _metric(steps, "obs_after", "kamas", reverse=True)
        verdicts = dict(Counter(str(row.get("verdict")) for row in steps))
        tools = dict(Counter(str(row.get("tool")) for row in steps))
        errors = Counter(str(row.get("error"))[:80] for row in steps
                         if row.get("error"))
        expected_tool = shadow.get("expected_tool")
        advanced = int(verdicts.get("advanced", 0))
        ok_steps = sum(1 for row in steps if row.get("ok"))
        xp_delta = (None if xp_before is None or xp_after is None
                    else xp_after - xp_before)
        goal_outcome = ("advanced" if advanced else
                        ("progressed" if xp_delta and xp_delta > 0 else "stalled"))
        stall_cause = classify_stall(len(steps), tools, verdicts, goal_outcome)
        replay.append({
            "kind": "command_replay",
            "session": group["session"],
            "goal": group["goal"],
            "source": group["source"],
            "steps": len(steps),
            "rule": shadow["rule"],
            "model": shadow["model"],
            "rule_mode": shadow["rule_mode"],
            "model_mode": shadow["model_mode"],
            "mode_agree": shadow["mode_agree"],
            "would_override": shadow["would_override"],
            "would_change_mode": shadow["would_change_mode"],
            "expected_tool": expected_tool,
            "expected_tool_executed": (None if expected_tool is None
                                       else expected_tool in tools),
            "executed_by": "rule",
            "executed": {
                "tools": tools,
                "verdicts": verdicts,
                "ok": ok_steps,
                "advanced": advanced,
                "errors": dict(errors.most_common(3)),
            },
            "outcome": {
                "objectives_before": objectives_before,
                "objectives_after": objectives_after,
                "objectives_delta": (None if objectives_before is None
                                     or objectives_after is None
                                     else objectives_after - objectives_before),
                "xp_delta": xp_delta,
                "kamas_delta": (None if kamas_before is None or kamas_after is None
                                else kamas_after - kamas_before),
                "goal_outcome": goal_outcome,
                "stall_cause": stall_cause,
            },
            "at_first": min(float(row.get("at") or 0.0) for row in steps),
            "at_last": max(float(row.get("at") or 0.0) for row in steps),
        })
    return replay


def summarize(replay: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    progress = [row for row in replay
                if (row["outcome"]["objectives_delta"] or 0) > 0]
    overrides = [row for row in replay if row["would_override"]]
    return {
        "groups": len(replay),
        "steps": sum(row["steps"] for row in replay),
        "distinct_goals": len({row["goal"] for row in replay}),
        "groups_with_objective_progress": len(progress),
        "objective_delta": sum(row["outcome"]["objectives_delta"] or 0
                               for row in replay),
        # The page's verdict is a finer outcome than the objective counter: a
        # session can advance a dialog or leave a page without completing an
        # objective, and in the recorded corpus that is the signal that exists.
        "groups_advanced": sum(1 for row in replay
                               if row["outcome"]["goal_outcome"] == "advanced"),
        "groups_progressed": sum(1 for row in replay
                                 if row["outcome"]["goal_outcome"] == "progressed"),
        "groups_stalled": sum(1 for row in replay
                              if row["outcome"]["goal_outcome"] == "stalled"),
        "override_groups": len(overrides),
        "override_groups_advanced": sum(1 for row in overrides
                                        if row["outcome"]["goal_outcome"]
                                        == "advanced"),
        "override_groups_executed_advance": sum(
            1 for row in overrides
            if "quest.advance" in (row["executed"]["tools"] or {})),
        "expected_tool_groups": sum(1 for row in replay
                                    if row["expected_tool"] is not None),
        "expected_tool_executed_groups": sum(
            1 for row in replay if row["expected_tool_executed"] is True),
        "override_groups_expected_executed": sum(
            1 for row in overrides if row["expected_tool_executed"] is True),
        "stall_causes": dict(Counter(row["outcome"]["stall_cause"] for row in replay
                                     if row["outcome"]["stall_cause"]).most_common()),
        "would_change_mode_groups": sum(1 for row in replay
                                        if row["would_change_mode"]),
        "mode_disagreements": sum(1 for row in replay if row["mode_agree"] is False),
        "model_unavailable": all(row["model"] is None for row in replay),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--logs", nargs="*", default=None)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--out", type=Path,
                        default=ROOT / "data" / "command_replay.jsonl")
    args = parser.parse_args(argv)

    if args.logs:
        paths = [path for pattern in args.logs
                 for path in (glob.glob(pattern) or [pattern])]
        paths = [Path(path) for path in paths if Path(path).is_file()]
    else:
        paths = default_logs()
    if not paths:
        print("command replay: no autopilot logs found")
        return 0
    groups: List[Dict[str, Any]] = []
    for path in paths:
        groups.extend(group_steps(path, _read(path)))
    if not groups:
        print("command replay: %d log(s), no executable goal groups" % len(paths))
        return 0
    model, error = load_command_model(args.model)
    if model is None:
        print("command replay: model unavailable (%s)" % error)
    replay = build_replay(groups, model, args.threshold)
    report = summarize(replay)
    report["paths"] = [str(path) for path in paths]
    report["model_error"] = error
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as handle:
        for row in replay:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
