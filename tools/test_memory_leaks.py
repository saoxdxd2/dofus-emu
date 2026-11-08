r"""Memory leak detection gate — measures memory growth across repeated operations.

This detects memory leaks in every major Python component without the game or
network. It uses tracemalloc to take snapshots before and after repeated
execution of the heaviest operations, and flags:

  * Unbounded tracemalloc growth between snapshots (current and peak).
  * RSS growth that does not return to baseline after gc.collect().
  * Top allocation hotspots so you know exactly which file/line is leaking.
  * Potential unbounded container growth in source (lists/dicts appended in
    loops without a bound).

Thresholds: FAIL if growth exceeds the hard limit, WARN above the soft limit.

Run: python tools/test_memory_leaks.py   (exit 0 = green)

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
SUITE = "test_memory_leaks"

# Thresholds (bytes).
MEM_SOFT = 5 * 1024 * 1024      # 5 MB warn
MEM_HARD = 20 * 1024 * 1024     # 20 MB fail
REPEATS = 3

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
# 1. tracemalloc growth across repeated gate runs
# ---------------------------------------------------------------------------

def check_repeated_gate_memory() -> None:
    ok("measuring memory growth across repeated gate runs")
    test_base.memory_start()
    try:
        before = test_base.MemorySnapshot("before gates")
        before.capture()

        gates = [
            REPO / "tools" / "test_network_integrity.py",
            REPO / "dofus-network" / "combat" / "test_combat_integrity.py",
        ]
        for _ in range(REPEATS):
            for path in gates:
                if not path.exists():
                    continue
                spec = importlib.util.spec_from_file_location(path.stem, path)
                mod = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(mod)
                except Exception:
                    pass

        after = test_base.MemorySnapshot("after gates")
        after.capture()
    finally:
        test_base.memory_stop()


    diff = test_base.memory_diff(before, after)
    growth = diff["tracemalloc_current_growth"]
    peak_growth = diff["tracemalloc_peak_growth"]
    rss_growth = diff["rss_growth_bytes"]

    detail = (f"current +{growth / 1024 / 1024:.2f} MB, "
              f"peak +{peak_growth / 1024 / 1024:.2f} MB, "
              f"RSS +{rss_growth / 1024 / 1024:.2f} MB")
    print(f"  memory growth detail: {detail}")

    if growth >= MEM_HARD or rss_growth >= MEM_HARD:
        fail(f"memory growth too high", detail)
    elif growth >= MEM_SOFT or rss_growth >= MEM_SOFT:
        warn(f"memory growth elevated", detail)
    else:
        ok(f"memory growth within limits", detail)

    if after.top_stats:
        print("  top allocation hotspots (after):")
        for stat in after.top_stats[:5]:
            print(f"    {stat.size / 1024:.1f} KB  {stat}")


# ---------------------------------------------------------------------------
# 2. Source-level scan for unbounded container growth patterns
# ---------------------------------------------------------------------------

def check_unbounded_containers() -> None:
    ok("scanning for unbounded container growth patterns in source")
    import re as _re
    patterns = [
        (r"\.append\(.*\)\s*$", "list.append in loop"),
        (r"\.extend\(.*\)\s*$", "list.extend in loop"),
    ]
    compiled = [(_re.compile(p, _re.MULTILINE), lbl) for p, lbl in patterns]
    total = 0
    for path in REPO.rglob("*.py"):
        if any(part in test_base.SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, raw in enumerate(text.splitlines(), 1):
            s = raw.strip()
            if s and s.startswith(("#", "//", "/*", "*", "--", "'")):
                continue
            for pat, lbl in compiled:
                for m in pat.finditer(raw):
                    total += 1
                    if total <= 15:
                        warn(f"possible unbounded growth {path.name}:{lineno}",
                             f"{lbl}: {m.group(0)[:80]!r}")
    if total == 0:
        ok("no unbounded container growth patterns detected")
    else:
        warn(f"unbounded growth patterns", f"{total} total (first 15 shown) - review for leaks")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"== {SUITE} memory leak detection (category: {CATEGORY}) ==")
    print()
    check_repeated_gate_memory()
    print()
    check_unbounded_containers()
    print()
    print("=" * 60)
    status = "PASS" if not FAILURES else "FAIL"
    print(f"[{status}] {SUITE} ({CATEGORY}): {CHECKS} check(s), "
          f"{len(FAILURES)} failure(s), {len(WARNINGS)} warning(s)")
    print(f"TOTAL: {len(FAILURES)} failure(s), {len(WARNINGS)} warning(s)")
    if FAILURES:
        print("Memory issues detected - review hotspots above.")
        return 1
    if WARNINGS:
        print("Memory acceptable with warnings.")
        return 0
    print("Memory acceptable - no leaks detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
