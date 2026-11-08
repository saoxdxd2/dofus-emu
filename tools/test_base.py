"""Shared base helpers for the DofusFarm automated test suite.

Every test file in this project imports from here so that:
  * failures are reported the same way (`FAIL file:line: message`)
  * checks are counted per file (so a shrinking total is visible)
  * scanning for literals/paths/secrets is consistent and fast
  * every test is offline-only (no client, no network, no torch)

Conventions:
  * module-level `CATEGORY` string (one of the CATEGORY_* constants below)
  * module-level `SUITE` short name used in the aggregated report
  * run as `python tools/test_*.py` -> exit 0 green / non-zero red
  * safe to import without running (guards live under __main__)

Run all tests through the central runner:

    python tools/test_runner.py          # full suite, exit code = failures
    python tools/test_runner.py --json   # machine-readable report for CI
    python tools/test_runner.py --category upgrade

No client, no network, no torch.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Pattern, Set, Tuple

REPO = Path(__file__).resolve().parent.parent# ---------------------------------------------------------------------------
# Categories (a test file sets CATEGORY = one of these)
# ---------------------------------------------------------------------------
CATEGORY_UPGRADE = "upgrade"            # upgrade-blocking hardcodes / machine-specifics
CATEGORY_NETWORK = "network"            # proxy, egress, fail-closed, endpoints
CATEGORY_SECURITY = "security"          # secrets, tokens, credentials, auth
CATEGORY_COMBAT = "combat"              # combat constants, grid, weights
CATEGORY_CONFIG = "config"              # ini / ai_settings neutral defaults
CATEGORY_DATA = "data"                  # data files, manifests, knowledge cache
CATEGORY_HOST = "host"                  # C# host, WinForms, bridge
CATEGORY_AGENT = "agent"                # Python brain, planner, controller

ALL_CATEGORIES = (
    CATEGORY_UPGRADE, CATEGORY_NETWORK, CATEGORY_SECURITY, CATEGORY_COMBAT,
    CATEGORY_CONFIG, CATEGORY_DATA, CATEGORY_HOST, CATEGORY_AGENT,
)

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    label: str
    passed: bool
    detail: str = ""


@dataclass
class SuiteResult:
    name: str
    file: str
    category: str
    checks: int = 0
    failures: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    duration_s: float = 0.0
    skipped: bool = False
    skip_reason: str = ""

    @property
    def passed(self) -> bool:
        return not self.failures and not self.skipped

# ---------------------------------------------------------------------------
# Reporting helpers (each test file defines its own fail/ok/warn bound to a
# SuiteResult instance)
# ---------------------------------------------------------------------------


class SuiteReporter:
    """Bound to one test file; records checks and prints FAIL/ok/WARN lines."""

    def __init__(self, name: str, file: Path, category: str) -> None:
        self.name = name
        self.file = str(file)
        self.category = category
        self.result = SuiteResult(name=name, file=str(file), category=category)

    def fail(self, label: str, detail: str = "") -> None:
        self.result.checks += 1
        self.result.failures.append(f"{label}: {detail}" if detail else label)
        print(f"FAIL {self.file}: {label}" + (f" | {detail}" if detail else ""))

    def warn(self, label: str, detail: str = "") -> None:
        self.result.checks += 1
        self.result.warnings.append(f"{label}: {detail}" if detail else label)
        print(f"WARN {self.file}: {label}" + (f" | {detail}" if detail else ""))

    def ok(self, label: str, detail: str = "") -> None:
        self.result.checks += 1
        print(f"ok   {self.file}: {label}" + (f" | {detail}" if detail else ""))

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.ok(label, detail)
        else:
            self.fail(label, detail)

# ---------------------------------------------------------------------------
# Scanning helpers
# ---------------------------------------------------------------------------

IPV4 = re.compile(
    r"\b(?!(?:127\.0\.0\.1|0\.0\.0\.0|255\.255\.255\.255))\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"
)
PORT_LITERAL = re.compile(
    r"\b(8880|8890|8891|8893|8894|8895|8896|8897|8898|8899|40000|5554)\b"
)
ANKAMA_HOST = re.compile(
    r"dt-proxy-production-login\.ankama-games\.com|auth\.ankama\.com"
)
UA_VERSION = re.compile(r"Chrome/\d+\.\d+\.\d+\.\d+")
VK_CODE = re.compile(r"\b(116|117|118|119|123)\b")
LOCALAPPDATA_PATH = re.compile(r"%LOCALAPPDATA%|\\DofusFarm\\webview2_profiles\\")
USER_PROFILE = re.compile(r"C:\\Users\\[A-Za-z0-9_.-]+\\")
CONCRETE_USER = re.compile(r"C:\\Users\\sao\\")
SECRET_LITERAL = re.compile(
    r"(?<!\w)(?:(password|passwd|pwd|secret|api[_-]?key|apikey|private[_-]?key|access[_-]?token)|(token))\s*[:=]\s*[\"']([^\"']{3,})[\"']",
    re.IGNORECASE,
)
GUEST_CRED = re.compile(r"\[GUEST\]|guest_credentials", re.IGNORECASE)

OK_SECRET_NAMES = {"secret_filename", "local_secret", "admin_token"}
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "asset_cache", "proxy_cache",
    ".vscode", ".cloud-attachments", ".freebuff", ".workbuddy-ai",
    "runtimes", "site-packages", "venv", ".venv", "build", "dist", "plugins",
    "scratch",
}
SKIP_NAMES = set()
MAX_BYTES = 5 * 1024 * 1024  # skip multi-MB generated/bundle files


def should_scan(path: Path, extra_skip_names: Optional[Set[str]] = None) -> bool:
    if path.is_dir():
        return False
    if path.name in SKIP_NAMES or path.name == Path(__file__).name:
        return False
    if extra_skip_names and path.name in extra_skip_names:
        return False
    if path.suffix.lower() not in {".py", ".go", ".js", ".cs", ".ini", ".json", ".bat", ".sh"}:
        return False
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    try:
        if path.stat().st_size > MAX_BYTES:
            return False
    except OSError:
        return False
    return True


def scan_text(path: Path, patterns: List[Tuple[Pattern, str]],
              reporter: Optional[SuiteReporter] = None,
              skip_comment_lines: bool = True,
              skip_names: Optional[Set[str]] = None) -> int:
    """Scan one file for regex patterns. Returns number of matches found.

    Each pattern is (compiled_regex, label). When a match is found, the
    reporter (if given) records a FAIL; otherwise the match is just counted.
    """
    if not should_scan(path, skip_names):
        return 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    lines = text.splitlines()
    count = 0
    for lineno, raw in enumerate(lines, 1):
        if skip_comment_lines:
            s = raw.strip()
            if s and any(s.startswith(p) for p in ("#", "//", "/*", "*", "--", "'")):
                continue
        for pat, label in patterns:
            for m in pat.finditer(raw):
                count += 1
                if reporter is not None:
                    snippet = raw[max(0, m.start() - 30): m.end() + 30].replace("\n", "\\n")
                    reporter.fail(f"{label} in {path.name}:{lineno}",
                                f"{m.group(0)!r} in {snippet!r}")
    return count


def scan_tree(roots: List[Path], patterns: List[Tuple[Pattern, str]],
              reporter: Optional[SuiteReporter] = None,
              globs: tuple = ("*.py", "*.go", "*.js", "*.cs", "*.ini")) -> int:
    """Scan every file under roots matching globs. Returns total matches."""
    total = 0
    for root in roots:
        if not root.exists():
            continue
        for globpat in globs:
            for path in root.rglob(globpat):
                total += scan_text(path, patterns, reporter)
    return total


def read_lines(path: Path) -> List[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def line_of(text: str, index: int) -> int:
    return text[: index].count("\n") + 1


def exit_with(result: SuiteResult, extra_summary: str = "") -> int:
    """Print a one-line summary for a suite and return its exit code."""
    status = "PASS" if result.passed else "FAIL"
    print(f"[{status}] {result.name} ({result.category}): "
          f"{result.checks} check(s), {len(result.failures)} failure(s), "
          f"{len(result.warnings)} warning(s)" + (f" | {extra_summary}" if extra_summary else ""))
    return 1 if result.failures else 0


# ===========================================================================
# Performance / latency helpers
# ===========================================================================

import time as _time  # noqa: E402


def measure(label: str, fn: Callable, *args, **kwargs) -> tuple:
    """Run ``fn`` and return ``(result, elapsed_seconds)``."""
    t0 = _time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = _time.perf_counter() - t0
    return result, elapsed


def measure_repeated(label: str, fn: Callable, repeats: int = 5,
                     *args, **kwargs) -> dict:
    """Run ``fn`` ``repeats`` times and return timing stats."""
    import statistics as _stats
    times = []
    for _ in range(repeats):
        t0 = _time.perf_counter()
        fn(*args, **kwargs)
        times.append(_time.perf_counter() - t0)
    return {
        "label": label,
        "min": min(times),
        "max": max(times),
        "mean": _stats.mean(times),
        "median": _stats.median(times),
        "p95": sorted(times)[int(len(times) * 0.95)] if times else 0.0,
    }


# ===========================================================================
# Memory helpers (tracemalloc + RSS)
# ===========================================================================

import tracemalloc as _tracemalloc  # noqa: E402
import gc as _gc  # noqa: E402


class MemorySnapshot:
    """A point-in-time memory snapshot with tracemalloc + process RSS."""

    def __init__(self, label: str = "") -> None:
        self.label = label
        self.tracemalloc_current = 0
        self.tracemalloc_peak = 0
        self.rss_bytes = 0
        self.top_stats = []

    def capture(self) -> None:
        _gc.collect()
        if _tracemalloc.is_tracing():
            current, peak = _tracemalloc.get_traced_memory()
            self.tracemalloc_current = current
            self.tracemalloc_peak = peak
            self.top_stats = _tracemalloc.take_snapshot().statistics("lineno")[:10]
        try:
            import resource as _resource  # type: ignore
            self.rss_bytes = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss * 1024
        except Exception:
            self.rss_bytes = 0


def memory_start() -> None:
    """Start tracemalloc for leak detection."""
    if not _tracemalloc.is_tracing():
        _tracemalloc.start()


def memory_stop() -> None:
    """Stop tracemalloc."""
    if _tracemalloc.is_tracing():
        _tracemalloc.stop()


def memory_diff(before: MemorySnapshot, after: MemorySnapshot) -> dict:
    """Return growth between two snapshots."""
    return {
        "tracemalloc_current_growth": after.tracemalloc_current - before.tracemalloc_current,
        "tracemalloc_peak_growth": after.tracemalloc_peak - before.tracemalloc_peak,
        "rss_growth_bytes": after.rss_bytes - before.rss_bytes,
        "top_growth_stats": after.top_stats,
    }
