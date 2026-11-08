r"""Global check — runs the ENTIRE automated testing mechanism in one shot.

This is the master gate. It runs every test category and produces ONE
consolidated, human-readable report plus a machine-readable JSON report for
CI/automation. It answers the question: "what is broken, and what do I fix
first?"

Runs (offline, no client/network/torch):
  * upgrade_readiness   — machine paths, secrets, Ankama hosts, frozen config,
                           knowledge-cache coverage, relay fail-closed
  * network_integrity   — proxy fail-closed, -allow-direct, admin token, ports
  * performance_latency — import times, gate execution times, scan speed
  * memory_leaks        — tracemalloc growth, unbounded container patterns
  * component_health    — Python/Go/C# imports & parsing, config files
  * combat_integrity    — combat game constants (grid, weights, caches)

Output:
  * Human summary printed to stdout (exit code = number of failing suites)
  * --json writes a machine-readable report to stdout for CI

Run: python tools/test_global_check.py            # full global check
     python tools/test_global_check.py --json > report.json   # CI report
     python tools/test_global_check.py --fail-fast            # stop at first red suite

No client, no network, no torch.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import test_base  # noqa: E402

TEST_MATRIX = [
    (test_base.CATEGORY_UPGRADE, REPO / "tools" / "test_upgrade_readiness.py"),
    (test_base.CATEGORY_NETWORK, REPO / "tools" / "test_network_integrity.py"),
    (test_base.CATEGORY_UPGRADE, REPO / "tools" / "test_performance_latency.py"),
    (test_base.CATEGORY_UPGRADE, REPO / "tools" / "test_memory_leaks.py"),
    (test_base.CATEGORY_HOST, REPO / "tools" / "test_component_health.py"),
    (test_base.CATEGORY_COMBAT, REPO / "dofus-network" / "combat" / "test_combat_integrity.py"),
]

TIMEOUT_S = 300


def run_suite(path: Path) -> dict:
    """Run one test file in a subprocess and parse its summary line."""
    import re as _re
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


def build_bug_list(results: list) -> list:
    """Flatten every FAIL line across all suites into a ranked bug list."""
    bugs = []
    for r in results:
        for line in r.get("output", []):
            if line.startswith("FAIL "):
                body = line[len("FAIL "):]
                if " | " in body:
                    label, detail = body.split(" | ", 1)
                else:
                    label, detail = body, ""
                bugs.append({
                    "suite": r["name"],
                    "category": r["category"],
                    "label": label.strip(),
                    "detail": detail.strip(),
                    "file": r["file"],
                })
    return bugs


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="DofusFarm global automated check",
    )
    parser.add_argument("--json", action="store_true",
                        help="emit a JSON report to stdout")
    parser.add_argument("--fail-fast", action="store_true",
                        help="stop at the first failing suite")
    args = parser.parse_args(argv)

    results = []
    total_checks = 0
    total_failures = 0
    total_warnings = 0
    failing_suites = 0

    for category, path in TEST_MATRIX:
        if not path.exists():
            print(f"[SKIP] {path.stem} [{category}] — file not found")
            continue
        r = run_suite(path)
        results.append(r)
        total_checks += r["checks"]
        total_failures += r["failures"]
        total_warnings += r["warnings"]
        if r["failures"] > 0:
            failing_suites += 1
            status = "FAIL"
        elif r.get("error"):
            status = "ERROR"
        else:
            status = "PASS"
        print(f"[{status}] {r['name']} [{r['category']}]: "
              f"{r['checks']} check(s), {r['failures']} failure(s), "
              f"{r['warnings']} warning(s)"
              + (f" ({r['error']})" if r.get("error") else "")
              + f"  {r['duration_s']}s")
        if args.fail_fast and r["failures"] > 0:
            print(f"fail-fast: stopping after {r['name']}")
            break

    print()
    print("=" * 70)
    print("GLOBAL CHECK SUMMARY")
    print("=" * 70)
    print(f"Suites run:     {len(results)}")
    print(f"Suites failing: {failing_suites}")
    print(f"Total checks:   {total_checks}")
    print(f"Total failures: {total_failures}")
    print(f"Total warnings: {total_warnings}")
    print()

    bugs = build_bug_list(results)
    if bugs:
        print("-" * 70)
        print("CONSOLIDATED BUG LIST (everything to fix)")
        print("-" * 70)
        for i, b in enumerate(bugs, 1):
            print(f"  {i:3d}. [{b['category']}] {b['suite']}: {b['label']}")
            if b['detail']:
                print(f"        {b['detail']}")
        print()
        print(f"{len(bugs)} issue(s) to fix across {failing_suites} failing suite(s).")
    else:
        print("No issues detected — everything is green.")

    overall = "PASS" if failing_suites == 0 else "FAIL"
    print(f"\nOVERALL RESULT: {overall}")

    if args.json:
        report = {
            "overall": overall,
            "suites_run": len(results),
            "suites_failing": failing_suites,
            "total_checks": total_checks,
            "total_failures": total_failures,
            "total_warnings": total_warnings,
            "bug_count": len(bugs),
            "bugs": bugs,
            "results": results,
        }
        print(json.dumps(report, indent=2))

    return min(failing_suites, 255)


if __name__ == "__main__":
    sys.exit(main())