r"""Performance / latency gate — measures how long every major component takes.

This is the automation-friendly performance check. It does not need the game,
the network, or torch. It measures:

  * Import time of every Python module under dofus-network/
  * Execution time of the existing offline gates
  * File scan performance of test_base over the whole repo
  * Thresholds: FAIL above the hard limit, WARN above the soft limit

Run: python tools/test_performance_latency.py   (exit 0 = green)

No client, no network, no torch.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import test_base  # noqa: E402

REPO = test_base.REPO
CATEGORY = test_base.CATEGORY_UPGRADE
SUITE = "test_performance_latency"

IMPORT_SOFT = 0.5
IMPORT_HARD = 2.0
GATE_SOFT = 15.0
GATE_HARD = 60.0
SCAN_SOFT = 5.0
SCAN_HARD = 30.0

FAILURES: list = []
WARNINGS: list = []
CHECKS: int = 0

def fail(label: str, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    FAILURES.append(f"{label}: {detail}" if detail else label)
    print(f"FAIL {label}" + (f" | {detail}" if detail else ""))


def warn(label: str, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    WARNINGS.append(f"{label}: {detail}" if detail else label)
    print(f"WARN {label}" + (f" | {detail}" if detail else ""))


def ok(label: str, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    print(f"ok   {label}" + (f" | {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 1. Import-time measurement for every Python module
# ---------------------------------------------------------------------------

def check_import_times() -> None:
    ok("measuring import time of every dofus-network Python module")
    slow: list = []
    count = 0
    for path in sorted(REPO.rglob("*.py")):
        if any(part in test_base.SKIP_DIRS for part in path.parts):
            continue
        if path.name == Path(__file__).name or path.name.startswith("test_"):
            continue
        dn = REPO / "dofus-network"
        try:
            rel = path.relative_to(dn)
        except ValueError:
            continue
        # Flat module name avoids parent-package resolution issues.
        safe = "_".join(rel.parts[:-1] + (path.stem,))
        module_name = "perf_check_" + safe
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        t0 = time.perf_counter()
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            warn(f"import error {module_name}", str(exc)[:120])
            continue
        elapsed = time.perf_counter() - t0
        count += 1
        if elapsed >= IMPORT_HARD:
            slow.append((module_name, elapsed))
        elif elapsed >= IMPORT_SOFT:
            warn(f"slow import {module_name}", f"{elapsed:.3f}s")
    if slow:
        for name, elapsed in sorted(slow, key=lambda x: -x[1]):
            fail(f"import too slow {name}", f"{elapsed:.3f}s (hard limit {IMPORT_HARD}s)")
    else:
        ok(f"all {count} modules import within hard limit")


# ---------------------------------------------------------------------------
# 2. Gate execution time
# ---------------------------------------------------------------------------

def check_gate_times() -> None:
    ok("measuring offline gate execution time")
    gates = [
        ("upgrade_readiness", REPO / "tools" / "test_upgrade_readiness.py"),
        ("network_integrity", REPO / "tools" / "test_network_integrity.py"),
        ("combat_integrity", REPO / "dofus-network" / "combat" / "test_combat_integrity.py"),
    ]
    for name, path in gates:
        if not path.exists():
            warn(f"gate missing {name}", str(path))
            continue
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        t0 = time.perf_counter()
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            fail(f"gate error {name}", str(exc)[:200])
            continue
        elapsed = time.perf_counter() - t0
        if elapsed >= GATE_HARD:
            fail(f"gate too slow {name}", f"{elapsed:.2f}s (hard limit {GATE_HARD}s)")
        elif elapsed >= GATE_SOFT:
            warn(f"gate slow {name}", f"{elapsed:.2f}s (soft limit {GATE_SOFT}s)")
        else:
            ok(f"gate time {name}", f"{elapsed:.2f}s")


# ---------------------------------------------------------------------------
# 3. Full-repo scan performance
# ---------------------------------------------------------------------------

def check_scan_performance() -> None:
    ok("measuring full-repo scan performance")
    patterns = [(test_base.IPV4, "ipv4"), (test_base.PORT_LITERAL, "port")]
    roots = [REPO / "tools", REPO / "dofus-network"]
    t0 = time.perf_counter()
    total = 0
    for root in roots:
        if not root.exists():
            continue
        for globpat in ("*.py", "*.go", "*.js", "*.cs", "*.ini"):
            for p in root.rglob(globpat):
                total += test_base.scan_text(p, patterns)
    elapsed = time.perf_counter() - t0
    if elapsed >= SCAN_HARD:
        fail(f"scan too slow", f"{elapsed:.2f}s over {total} matches (hard {SCAN_HARD}s)")
    elif elapsed >= SCAN_SOFT:
        warn(f"scan slow", f"{elapsed:.2f}s over {total} matches (soft {SCAN_SOFT}s)")
    else:
        ok(f"scan time", f"{elapsed:.2f}s over {total} matches")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"== {SUITE} performance / latency gate (category: {CATEGORY}) ==")
    print()
    check_import_times()
    print()
    check_gate_times()
    print()
    check_scan_performance()
    print()
    print("=" * 60)
    status = "PASS" if not FAILURES else "FAIL"
    print(f"[{status}] {SUITE} ({CATEGORY}): {CHECKS} check(s), "
          f"{len(FAILURES)} failure(s), {len(WARNINGS)} warning(s)")
    print(f"TOTAL: {len(FAILURES)} failure(s), {len(WARNINGS)} warning(s)")
    if FAILURES:
        print("Performance NOT acceptable - slow components above.")
        return 1
    if WARNINGS:
        print("Performance acceptable with warnings.")
        return 0
    print("Performance acceptable - no latency issues detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
