"""Central automated test runner for the DofusFarm project.

Discovers every `test_*.py` under tools/ and dofus-network/, runs each in an
isolated subprocess (offline-only, no client/network/torch), parses FAIL/ok/WARN
lines, aggregates results, and prints a human summary plus an optional JSON
report for CI automation.

Usage:

    python tools/test_runner.py                    # full suite
    python tools/test_runner.py --json > report.json   # machine-readable report
    python tools/test_runner.py --category upgrade   # only upgrade tests
    python tools/test_runner.py --category network security   # multiple categories
    python tools/test_runner.py --fail-fast          # stop at first failing file
    python tools/test_runner.py --list               # list discovered tests, no run

Exit code: number of failing test files (capped at 255); 0 if all green.

Each test file must:
  * import test_base and use SuiteReporter / check / fail / ok / warn
  * set module-level CATEGORY = one of test_base.ALL_CATEGORIES
  * set module-level SUITE = short display name
  * guard execution under `if __name__ == "__main__":`
  * be safe to import without side effects
  * run offline (no client, no network, no torch)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import test_base  # noqa: E402

DISCOVER_DIRS = [REPO / "tools", REPO / "dofus-network"]
TIMEOUT_S = 120
# test_base.py is a shared helper module, not a test suite.
EXCLUDE_NAMES = {"test_base.py"}

def discover_tests(category: Optional[str] = None) -> List[Path]:
    """Return every test_*.py file under the discover dirs, optionally filtered
    by category (read by importing the module without running it)."""
    found: List[Path] = []
    for d in DISCOVER_DIRS:
        if not d.exists():
            continue
        for path in sorted(d.rglob("test_*.py")):
            if path.name in EXCLUDE_NAMES:
                continue
            found.append(path)
    if category is None:
        return found
    matched: List[Path] = []
    for path in found:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            matched.append(path)
            continue
        if getattr(mod, "CATEGORY", None) == category:
            matched.append(path)
    return matched


def run_one(path: Path, fail_fast: bool = False) -> dict:
    """Run a single test file in a subprocess and return a result dict."""
    import subprocess as _sp
    t0 = time.time()
    result = {
        "file": str(path),
        "name": path.stem,
        "category": "unknown",
        "checks": 0,
        "failures": 0,
        "warnings": 0,
        "passed": False,
        "skipped": False,
        "skip_reason": "",
        "duration_s": 0.0,
        "output": [],
        "error": None,
    }
    try:
        proc = _sp.run(
            [sys.executable, str(path)],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            cwd=str(REPO),
        )
        lines = (proc.stdout or "").splitlines()
        result["output"] = lines
        for line in lines:
            if line.startswith("[PASS] ") or line.startswith("[FAIL] "):
                import re as _re
                m = _re.search(
                    r"\[(PASS|FAIL)\] (\S+) \((\w+)\): (\d+) check\(s\), (\d+) failure\(s\), (\d+) warning\(s\)",
                    line,
                )
                if m:
                    result["passed"] = m.group(1) == "PASS"
                    result["name"] = m.group(2)
                    result["category"] = m.group(3)
                    result["checks"] = int(m.group(4))
                    result["failures"] = int(m.group(5))
                    result["warnings"] = int(m.group(6))
                break
        if result["category"] == "unknown":
            for line in lines:
                if line.startswith("FAIL ") or line.startswith("WARN ") or line.startswith("ok   "):
                    if line.startswith("FAIL "):
                        result["failures"] += 1
                    elif line.startswith("WARN "):
                        result["warnings"] += 1
                    result["checks"] += 1
            result["passed"] = proc.returncode == 0 and result["failures"] == 0
        if proc.returncode != 0 and result["failures"] == 0:
            result["failures"] = 1
            result["passed"] = False
            result["error"] = f"exit code {proc.returncode}"
        result["skipped"] = result["checks"] == 0 and result["failures"] == 0 and result["warnings"] == 0
    except _sp.TimeoutExpired:
        result["error"] = f"timeout after {TIMEOUT_S}s"
        result["passed"] = False
        result["failures"] = 1
    except Exception as exc:
        result["error"] = str(exc)
        result["passed"] = False
        result["failures"] = 1
    result["duration_s"] = round(time.time() - t0, 3)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="DofusFarm automated test runner",
    )
    parser.add_argument("--json", action="store_true",
                        help="emit a JSON report to stdout instead of the human summary")
    parser.add_argument("--category", action="append", default=None,
                        help="only run tests of this category (repeatable)")
    parser.add_argument("--fail-fast", action="store_true",
                        help="stop at the first failing test file")
    parser.add_argument("--list", action="store_true",
                        help="list discovered tests with categories, do not run")
    parser.add_argument("files", nargs="*",
                        help="specific test files to run (default: all discovered)")
    args = parser.parse_args(argv)

    if args.files:
        tests = [Path(f) for f in args.files]
    else:
        tests = discover_tests(args.category[0] if args.category else None)

    if args.list:
        for t in tests:
            spec = importlib.util.spec_from_file_location(t.stem, t)
            cat = "unknown"
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(mod)
                    cat = getattr(mod, "CATEGORY", "unknown")
                except Exception:
                    pass
            print(f"{t}  [{cat}]")
        return 0

    results: List[dict] = []
    total_checks = 0
    total_failures = 0
    total_warnings = 0
    failing_files = 0

    for path in tests:
        r = run_one(path, fail_fast=args.fail_fast)
        results.append(r)
        total_checks += r["checks"]
        total_failures += r["failures"]
        total_warnings += r["warnings"]
        if r["failures"] > 0:
            failing_files += 1
            status = "FAIL"
        elif r.get("error"):
            status = "ERROR"
        else:
            status = "PASS"
        cat = r.get("category", "unknown")
        print(f"[{status}] {r['name']} [{cat}]: "
              f"{r['checks']} check(s), {r['failures']} failure(s), {r['warnings']} warning(s)"
              + (f"  ({r['error']})" if r.get("error") else "")
              + f"  {r['duration_s']}s")
        if args.fail_fast and r["failures"] > 0:
            print(f"fail-fast: stopping after {r['name']}")
            break

    print()
    print("=" * 60)
    print(f"TOTAL: {len(results)} test file(s), {total_checks} check(s), "
          f"{total_failures} failure(s), {total_warnings} warning(s), "
          f"{failing_files} failing file(s)")

    if args.json:
        report = {
            "total_files": len(results),
            "total_checks": total_checks,
            "total_failures": total_failures,
            "total_warnings": total_warnings,
            "failing_files": failing_files,
            "passed": failing_files == 0,
            "results": results,
        }
        print(json.dumps(report, indent=2))

    return min(failing_files, 255)


if __name__ == "__main__":
    sys.exit(main())
