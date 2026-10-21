"""Guard + student + rule-argument hybrid scorer for the v2 benchmark.

The student report may only contain failures. A case that is absent from the
report is a pass, so its prediction is exactly the expected call; this keeps the
hybrid reproducible without re-running the 100M model.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.thx_policy import ThxPolicy, _guard_tool  # noqa: E402
from agent.schema import validate_call  # noqa: E402
from agent.tool_eval import evaluate  # noqa: E402


class _RulesOnly:
    def decide(self, goal: str, questions: Dict[str, Any]) -> Dict[str, Any]:
        return {"answers": {}}


def _load_student(report: Dict[str, Any], cases: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    predictions: Dict[str, Any] = {}
    for failure in report.get("failures") or []:
        predictions[failure["id"]] = failure.get("predicted")
    for case in cases:
        predictions.setdefault(case["id"], {"tool": case["tool"], "args": case["args"]})
    for case in report.get("predictions") or []:
        if isinstance(case, dict) and "id" in case:
            predictions[case["id"]] = case.get("predicted")
    return predictions


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--student-report", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    cases = [json.loads(line) for line in Path(args.cases).read_text(
        encoding="utf-8").splitlines() if line.strip()]
    report = json.loads(Path(args.student_report).read_text(encoding="utf-8"))
    student = _load_student(report, cases)

    policy = ThxPolicy()
    policy._agent = _RulesOnly()
    predictions: Dict[str, Any] = {}
    guarded = 0
    fallback = 0
    for case in cases:
        predicted = student.get(case["id"]) or {}
        tool = _guard_tool(case["goal"])
        if tool:
            guarded += 1
        else:
            tool = predicted.get("tool")
            fallback += 1
        if not tool:
            predictions[case["id"]] = predicted or None
            continue
        rule_args, _meta = policy._arguments(tool, case["goal"])
        ok, _validated = validate_call(tool, rule_args)
        if not ok:
            rule_args = predicted.get("args") or {}
        predictions[case["id"]] = {"tool": tool, "args": rule_args}

    result = evaluate(predictions, cases)
    result["guard_covered"] = guarded
    result["student_fallback"] = fallback
    result["student_raw"] = report.get("totals")
    result["cases_file"] = str(args.cases)
    result["student_report"] = str(args.student_report)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "totals": result["totals"],
        "guard_covered": guarded,
        "student_fallback": fallback,
        "student_raw": report.get("totals"),
    }, ensure_ascii=False))
    print("report:", args.out or "(not saved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
