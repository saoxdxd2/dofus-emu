"""Report the learned command shadow over recorded live goals.

Reads the autopilot trajectory logs, extracts every distinct goal string with its
frequency, and runs the rule and the learned command parser on each via
``agent.command_shadow.shadow_row``. It takes no decisions: this is the offline
counterpart of ``--command-shadow auto`` for a machine that already has logs.

    python tools/command_shadow_report.py
    python tools/command_shadow_report.py --logs "%LOCALAPPDATA%/DofusFarm/autopilot*.jsonl" \
        --out /tmp/command_shadow.json

The goal strings are real user prompts; they are not in the command dataset, so
this is the closest thing to a live prompt benchmark without a running client.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.command_shadow import DEFAULT_MODEL, load_command_model, shadow_row  # noqa: E402


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


def extract_goals(paths: Sequence[Path]) -> Counter:
    """Distinct goal strings and their recorded uses. Malformed lines are skipped."""
    goals: Counter = Counter()
    for path in paths:
        try:
            lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            goal = row.get("goal")
            if not isinstance(goal, str):
                continue
            goal = " ".join(goal.split())
            if goal and goal.lower() != "none":
                goals[goal] += 1
    return goals


def build_report(goals: Counter, model: Any, threshold: float = 0.55) -> Dict[str, Any]:
    rows = [shadow_row(goal, model, threshold) for goal in goals]
    rows.sort(key=lambda row: (-goals[row["text"]], row["text"]))
    for row in rows:
        row["uses"] = goals[row["text"]]
    usable = [row for row in rows if row["usable"]]
    return {
        "logs": True,
        "distinct_goals": len(rows),
        "total_uses": sum(goals.values()),
        "model_available": model is not None,
        "summary": {
            "usable_goals": len(usable),
            "agree_intent_goals": sum(1 for row in rows if row["agree_intent"]),
            "agree_command_goals": sum(1 for row in rows if row["agree_command"]),
            "override_goals": sum(1 for row in rows if row["would_override"]),
            "override_uses": sum(row["uses"] for row in rows if row["would_override"]),
            "mode_agree_goals": sum(1 for row in rows if row["mode_agree"]),
            "mode_disagreement_goals": sum(1 for row in rows
                                           if row["mode_agree"] is False),
            "would_change_mode_goals": sum(1 for row in rows
                                           if row["would_change_mode"]),
            "would_change_mode_uses": sum(row["uses"] for row in rows
                                          if row["would_change_mode"]),
            "rule_intents": dict(Counter(row["rule"]["intent"] for row in rows)),
            "model_intents": dict(Counter(
                row["model"]["intent"] for row in rows if row["model"])),
        },
        "goals": rows,
    }


def print_report(report: Dict[str, Any]) -> None:
    summary = report["summary"]
    print("command shadow over %d distinct goals (%d recorded uses); model %s"
          % (report["distinct_goals"], report["total_uses"],
             "available" if report["model_available"] else "unavailable"))
    print("  usable %d, agree-intent %d, agree-command %d, would-override %d goals "
          "(%d uses); mode agree %d, disagree %d, would-change %d goals (%d uses)"
          % (summary["usable_goals"], summary["agree_intent_goals"],
             summary["agree_command_goals"], summary["override_goals"],
             summary["override_uses"], summary["mode_agree_goals"],
             summary["mode_disagreement_goals"],
             summary["would_change_mode_goals"], summary["would_change_mode_uses"]))
    for row in report["goals"]:
        model = row["model"]
        model_view = ("-" if not model else
                      "%s %s %.3f" % (model["intent"], model["slots"],
                                      model["confidence"]))
        flag = " ".join(name for name, on in
                        (("OVERRIDE", row["would_override"]),
                         ("MODE", row["would_change_mode"])) if on)
        print("  %5d  %-38r rule %-8s %-28s model %-34s %s"
              % (row["uses"], row["text"], row["rule"]["intent"],
                 str(row["rule"]["slots"]), model_view, flag))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--logs", nargs="*", default=None,
                        help="autopilot JSONL files/globs (default: local logs)")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.logs:
        paths = [path for pattern in args.logs
                 for path in (glob.glob(pattern) or [pattern])]
        paths = [Path(path) for path in paths if Path(path).is_file()]
    else:
        paths = default_logs()
    if not paths:
        print("command shadow: no autopilot logs found")
        return 0

    goals = extract_goals(paths)
    if not goals:
        print("command shadow: %d log(s), no goal strings" % len(paths))
        return 0
    model, error = load_command_model(args.model)
    if model is None:
        print("command shadow: model unavailable (%s)" % error)
    report = build_report(goals, model, args.threshold)
    report["paths"] = [str(path) for path in paths]
    report["model_error"] = error
    print_report(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
