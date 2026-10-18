#!/usr/bin/env python3
"""Test runner for dofus-network.

Runs every test module under tests/ using unittest and writes a consolidated
report (JSON + Markdown) to tests/reports/.  No source files are modified.
"""

from __future__ import annotations

import json
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REPORT_DIR = Path(__file__).resolve().parent / "reports"


def discover_and_run() -> dict:
    loader = unittest.TestLoader()
    suite = loader.discover(str(Path(__file__).resolve().parent), pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(getattr(result, "skipped", [])),
        "successful": result.testsRun - len(result.failures) - len(result.errors),
        "was_successful": result.wasSuccessful(),
        "failures_detail": [
            {"test": str(tb), "traceback": tb_str}
            for tb, tb_str in result.failures
        ],
        "errors_detail": [
            {"test": str(tb), "traceback": tb_str}
            for tb, tb_str in result.errors
        ],
    }
    return report


def write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# dofus-network Test Report",
        "",
        f"Generated: {report['generated_utc']}",
        "",
        "## Summary",
        "",
        f"- Tests run: {report['tests_run']}",
        f"- Successful: {report['successful']}",
        f"- Failures: {report['failures']}",
        f"- Errors: {report['errors']}",
        f"- Skipped: {report['skipped']}",
        f"- Overall: {'PASS' if report['was_successful'] else 'FAIL'}",
        "",
    ]
    if report["failures_detail"]:
        lines.append("## Failures")
        lines.append("")
        for item in report["failures_detail"]:
            lines.append(f"### {item['test']}")
            lines.append("```")
            lines.append(item["traceback"])
            lines.append("```")
            lines.append("")
    if report["errors_detail"]:
        lines.append("## Errors")
        lines.append("")
        for item in report["errors_detail"]:
            lines.append(f"### {item['test']}")
            lines.append("```")
            lines.append(item["traceback"])
            lines.append("```")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = discover_and_run()

    json_path = REPORT_DIR / "test_report.json"
    md_path = REPORT_DIR / "test_report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report, md_path)

    print(f"\nReport written to {json_path} and {md_path}")
    return 0 if report["was_successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())