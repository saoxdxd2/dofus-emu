"""One measurement, many documents: every quoted count is a reference, not a retyped number.

A document that quotes a measured number by hand drifts the moment the tree moves, and
no reader can tell which numbers are still true. This tool removes the number from the
prose: the docs carry `{{fact}}`, and the fact's value is computed here.

    python -m tools.doc_facts --facts     # every fact, its value and how it was measured
    python -m tools.doc_facts --adopt     # dry-run: which literals would become references
    python -m tools.doc_facts --adopt --write
    python -m tools.doc_facts --render    # rewrite the docs with current values
    python -m tools.doc_facts --check     # fail if render(doc) != doc, or a big count is bare

Only one rule is enforced, and it is an equation: render(doc) == doc. Everything else
is a report, not a veto. A fact with no measurement yet (the gate has never run on this
checkout) keeps its reference and is listed as unmeasured rather than guessed.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    # The docs are UTF-8 and full of arrows; a cp1252 console must not kill the sweep.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BRAIN = Path(__file__).resolve().parents[1]
REPO = BRAIN.parent

EXCLUDED_DIRS = {
    ".git", "node_modules", "asset_cache", "runtimes", "bin", "obj", "__pycache__",
    ".idea", "x64", "Release", "Debug", "models", "networkhealth_artifacts", "logs",
}
# Ankama's minified game bundle is not our code.
BUNDLE_MARKERS = ("/client/www/build/", "/node_modules/", "/staging")

GATE_RESULT = BRAIN / "data" / "last_gate_run.json"
HISTORY = BRAIN / "data" / "doc_facts_history.json"
TOKEN = re.compile(r"\{\{([a-z_]+)\}\}")
# A count is never glued to a date, a version, a path or an arrow: "2026-10-28", "v1.5",
# "0→71%" are not measurements of anything this tool knows.
NUMBER = re.compile(r"(?<![\w.,:/\-→])(\d{1,3}(?:,\d{3})+|\d+)(?![\w,])")
CODE_SPAN = re.compile(r"`[^`]*`")
YEARS = {"2024", "2025", "2026"}
ADJACENT = 16

DOCS = [
    REPO / "resume.md",
    REPO / "README.md",
    REPO / "rules.md",
    BRAIN / "docs" / "CURRENT.md",
]


# --------------------------------------------------------------------------- measuring

def _sources(root: Path, exts: Tuple[str, ...]) -> List[Path]:
    out: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith("staging")]
        for name in filenames:
            if os.path.splitext(name)[1] in exts:
                p = Path(dirpath) / name
                if any(m in p.as_posix() for m in BUNDLE_MARKERS):
                    continue
                out.append(p)
    return sorted(out)


def _lines(paths: List[Path]) -> int:
    total = 0
    for p in paths:
        try:
            with open(p, "rb") as fh:
                total += sum(1 for _ in fh)
        except OSError:
            continue
    return total


def _code(exts: Tuple[str, ...], roots: Tuple[Path, ...]) -> Tuple[int, int]:
    paths: List[Path] = []
    for root in roots:
        if root.exists():
            paths.extend(_sources(root, exts))
    return len(paths), _lines(paths)


def brain_python() -> Tuple[int, int]:
    """The brain's Python, minus the Kaggle mirror (a copy of it, not new code)."""
    n, total = _code((".py",), (BRAIN,))
    mirror = BRAIN / "kaggle"
    if mirror.exists():
        mn, ml = _code((".py",), (mirror,))
        return n - mn, total - ml
    return n, total


def capabilities_count() -> Optional[int]:
    path = BRAIN / "data" / "capabilities.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return len(data)
    return len(data.get("tools") or data.get("capabilities") or [])


def gate_checks() -> Optional[int]:
    if not GATE_RESULT.exists():
        return None
    return int(json.loads(GATE_RESULT.read_text(encoding="utf-8"))["checks"])


def mutation_count() -> Optional[int]:
    """len(MUTATIONS) read structurally; a regex over the table miscounted it once (300 vs 432)."""
    path = BRAIN / "tools" / "mutate_cause_gate.py"
    if not path.exists():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "MUTATIONS":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        return len(node.value.elts)
                    return None    # assembled at runtime; then the prose must say so
    return None


def file_size(path: Path) -> Optional[int]:
    return path.stat().st_size if path.exists() else None


def gaps_max() -> Optional[int]:
    path = BRAIN / "docs" / "KNOWN_GAPS.md"
    if not path.exists():
        return None
    nums = [int(m) for m in re.findall(r"^#+\s*G(\d{1,3})\b", path.read_text(encoding="utf-8"), re.M)]
    return max(nums) if nums else None


def doc_lines(name: str) -> Optional[int]:
    """The repo root wins, so README.md means the host README, not docs/README.md."""
    for candidate in (REPO / name, BRAIN / "docs" / name):
        if candidate.exists():
            return _lines([candidate])
    return None


def trajectory_rows() -> Optional[Tuple[int, int]]:
    root = BRAIN / "trajectories"
    if not root.exists():
        return None
    files = [p for p in root.rglob("*.jsonl") if p.is_file()]
    return len(files), _lines(files)


def patched_bundle_chars() -> Optional[int]:
    """Characters, not bytes: --bundle-selftest compares patched text, so that count travels."""
    path = REPO / "desktop" / "client" / "www" / "build" / "script.js"
    if not path.exists():
        return None
    try:
        return len(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return path.stat().st_size


# name -> (compute, how it was measured, anchors that identify its sentence during --adopt)
FACTS: Dict[str, Tuple[object, str, Tuple[str, ...]]] = {
    "tools": (capabilities_count, "len(data/capabilities.json capabilities)", (r"tools?",)),
    "gate_checks": (gate_checks, "data/last_gate_run.json, written by agent.regression.run()", (r"checks",)),
    "mutations": (mutation_count, "len(tools/mutate_cause_gate.py MUTATIONS)", (r"mutations?",)),
    "py_lines": (lambda: brain_python()[1], "dofus-network/**/*.py minus caches, models, artifacts and the kaggle mirror", (r"Python",)),
    "py_files": (lambda: brain_python()[0], "same scope as py_lines", (r"Python",)),
    "cs_lines": (lambda: _code((".cs",), (REPO / "desktop", REPO / "builder"))[1], "desktop/**/*.cs + builder/**/*.cs minus bin/obj", (r"C#",)),
    "cs_files": (lambda: _code((".cs",), (REPO / "desktop", REPO / "builder"))[0], "same scope as cs_lines", (r"C#",)),
    "go_lines": (lambda: _code((".go",), (REPO / "scripts", REPO / "tools"))[1], "scripts/*.go + tools/*.go", (r"Go\b",)),
    "js_lines": (lambda: _code((".js",), (REPO / "scripts", REPO / "tools"))[1], "scripts/*.js + tools/*.js minus the client bundle", (r"JS\b", r"JavaScript")),
    "script_lines": (lambda: _code((".go",), (REPO / "scripts", REPO / "tools"))[1] + _code((".js",), (REPO / "scripts", REPO / "tools"))[1], "go_lines + js_lines", (r"Go/JS", r"Go and JS")),
    "setup_bytes": (lambda: file_size(REPO / "setup.exe"), "stat(setup.exe), bytes", (r"setup\.exe",)),
    "bundle_chars": (patched_bundle_chars, "characters of build/script.js, what --bundle-selftest compares", (r"script\.js", r"bundle")),
    "gaps_max": (gaps_max, "highest `# G<n>` heading in docs/KNOWN_GAPS.md", ()),
    "known_gaps_lines": (lambda: doc_lines("KNOWN_GAPS.md"), "line count of docs/KNOWN_GAPS.md", (r"KNOWN_GAPS\.md",)),
    "resume_lines": (lambda: doc_lines("resume.md"), "line count of resume.md", (r"resume\.md",)),
    "readme_lines": (lambda: doc_lines("README.md"), "line count of README.md", (r"README\.md",)),
    "trajectory_files": (lambda: (trajectory_rows() or (None, None))[0], "count of trajectories/**/*.jsonl", (r"trajector",)),
    "trajectory_rows": (lambda: (trajectory_rows() or (None, None))[1], "line count of trajectories/**/*.jsonl", (r"trajector",)),
}

_VALUES: Optional[Dict[str, Optional[int]]] = None


def values(refresh: bool = False) -> Dict[str, Optional[int]]:
    global _VALUES
    if _VALUES is None or refresh:
        measured: Dict[str, Optional[int]] = {}
        for name, (compute, _scope, _anchors) in FACTS.items():
            try:
                measured[name] = compute()
            except Exception as exc:          # a broken measurement is reported, never guessed
                measured[name] = None
                print("measurement failed for %s: %s" % (name, exc), file=sys.stderr)
        _VALUES = measured
    return _VALUES


def fmt(value) -> str:
    return format(value, ",") if isinstance(value, int) else "unmeasured"


# --------------------------------------------------------------------------- rendering

def render(text: str, measured: Optional[Dict[str, Optional[int]]] = None) -> str:
    measured = measured if measured is not None else values()

    def replace(match: re.Match) -> str:
        value = measured.get(match.group(1))
        return fmt(value) if value is not None else match.group(0)

    return TOKEN.sub(replace, text)


def unmeasured_tokens(text: str) -> List[str]:
    measured = values()
    return sorted({m for m in TOKEN.findall(text) if measured.get(m) is None})


# --------------------------------------------------------------------------- history

def history() -> Dict[str, List[int]]:
    if not HISTORY.exists():
        return {}
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember() -> Dict[str, List[int]]:
    """Every measured value that has ever been seen, so --adopt recognises old numbers too."""
    seen = history()
    changed = False
    for name, value in values().items():
        if value is None:
            continue
        bucket = seen.setdefault(name, [])
        if value not in bucket:
            bucket.append(value)
            changed = True
    if changed:
        try:
            HISTORY.parent.mkdir(parents=True, exist_ok=True)
            HISTORY.write_text(json.dumps(seen, indent=1, sort_keys=True), encoding="utf-8")
        except OSError:
            pass
    return seen


# --------------------------------------------------------------------------- adoption

def _numbers(line: str) -> List[Tuple[int, int, str]]:
    spans = [m.span() for m in CODE_SPAN.finditer(line)]
    out: List[Tuple[int, int, str]] = []
    for m in NUMBER.finditer(line):
        if any(start <= m.start() < end for start, end in spans):
            continue                      # inside `code`: that is an identifier, not a count
        out.append((m.start(), m.end(), m.group(1)))
    return out


def adopt_line(line: str, measured: Dict[str, Optional[int]], seen: Dict[str, List[int]]) -> Tuple[str, List[str]]:
    """Rewrite a measured literal into a reference.

    The literal must sit within ADJACENT characters of its anchor, so "G58 ... 71%" never
    becomes a reference to gaps_max just because both numbers happen to be 71.
    """
    if "{{" in line:
        return line, []
    changes: List[str] = []
    for name, (_compute, _scope, anchors) in FACTS.items():
        current = measured.get(name)
        candidates = set(seen.get(name, []))
        if current is not None:
            candidates.add(current)
        if not candidates or not anchors:
            continue
        hits = [m.span() for m in re.finditer("|".join(anchors), line)]
        if not hits:
            continue
        for start, end, token in _numbers(line):
            if token in YEARS:
                continue
            try:
                got = int(token.replace(",", ""))
            except ValueError:
                continue
            if got not in candidates:
                continue
            near = any(start - ADJACENT <= a_end and a_start - ADJACENT <= start
                       for a_start, a_end in hits)
            if not near:
                continue
            line = line[:start] + "{{" + name + "}}" + line[end:]
            changes.append("%s = %s" % (name, token))
            break
    return line, changes


def adopt_docs(write: bool = False) -> List[str]:
    measured = values()
    seen = remember()
    report: List[str] = []
    for path in DOCS:
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        out: List[str] = []
        touched = 0
        for lineno, line in enumerate(src.splitlines(keepends=True), 1):
            body = line.rstrip("\r\n")
            new, changes = adopt_line(body, measured, seen)
            if changes:
                touched += 1
                report.append("%s:%d %s\n    - %s\n    + %s"
                              % (path.name, lineno, ", ".join(changes), body.strip(), new.strip()))
            out.append(new + line[len(body):])
        if write and touched:
            path.write_text("".join(out), encoding="utf-8")
    if write:
        print("adopted %d line(s)" % len(report))
    elif report:
        print("%d line(s) would change; rerun with --write\n" % len(report))
        for line in report:
            print(line)
    else:
        print("nothing to adopt: every anchored measurement is already a reference")
    return report


# --------------------------------------------------------------------------- checking

def check_docs(verbose: bool = True) -> List[str]:
    measured = values()
    problems: List[str] = []
    for path in DOCS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        rendered = render(text, measured)
        if rendered != text:
            for lineno, (raw, want) in enumerate(zip(text.splitlines(), rendered.splitlines()), 1):
                if raw != want:
                    problems.append("%s:%d reference drifted\n    doc:      %s\n    measured: %s"
                                    % (path.name, lineno, raw.strip()[:150], want.strip()[:150]))
        for name in unmeasured_tokens(text):
            if verbose:
                print("  note: %s references {{%s}}, which has no measurement yet" % (path.name, name))
        # A count of a thousand or more is specific enough that a bare copy is a stale one.
        for lineno, line in enumerate(rendered.splitlines(), 1):
            for name, value in measured.items():
                if not isinstance(value, int) or value < 1000:
                    continue
                literal = format(value, ",")
                if literal not in line:
                    continue
                if "{{%s}}" % name in text.splitlines()[lineno - 1]:
                    continue
                problems.append("%s:%d [%s] bare %s - write {{%s}} instead"
                                % (path.name, lineno, name, literal, name))
    if verbose:
        if problems:
            print("doc facts: %d problem(s)" % len(problems))
            for p in problems:
                print("  " + p)
        else:
            print("doc facts: every reference matches its measurement")
    return problems


# --------------------------------------------------------------------------- cli

def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    mode = args[0] if args else "--facts"
    if mode == "--facts":
        seen = history()
        for name, value in sorted(values().items()):
            scope = FACTS[name][1]
            print("%-18s %-12s  %s" % (name, fmt(value), scope))
        return 0
    if mode == "--markdown":
        print("| fact | measured | how |")
        print("|---|---|---|")
        for name, value in sorted(values().items()):
            print("| `%s` | %s | %s |" % (name, fmt(value), FACTS[name][1]))
        return 0
    if mode == "--adopt":
        adopt_docs(write="--write" in args)
        return 0
    if mode == "--render":
        measured = values()
        remember()
        changed = 0
        for path in DOCS:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            rendered = render(text, measured)
            if rendered != text:
                path.write_text(rendered, encoding="utf-8")
                changed += 1
        print("rendered %d doc(s)" % changed)
        return 0
    if mode == "--check":
        remember()
        return 1 if check_docs() else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
