"""One measurement, many documents: every quoted count is a reference, not a retyped number.

A document that quotes a measured number by hand drifts the moment the tree moves, and
no reader can tell which numbers are still true. This tool removes the number from the
prose: the docs carry `{{fact}}`, and the fact's value is computed here.

    python -m tools.doc_facts --facts     # every fact, its value and how it was measured
    python -m tools.doc_facts --adopt     # dry-run: which literals would become references
    python -m tools.doc_facts --adopt --write
    python -m tools.doc_facts --render    # materialise references as `value<!--f:name-->`
    python -m tools.doc_facts --check     # fail if a tagged number no longer equals its measurement

One rule is enforced: a tagged number must equal its measurement. Untagged prose is not
examined - a number nobody measured is not a claim this tool can settle. A fact with no
measurement yet (the gate has never run on this checkout) is listed, never guessed.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    # The docs are UTF-8 and full of arrows; a cp1252 console must not kill the sweep.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BRAIN = Path(__file__).resolve().parents[1]
REPO = BRAIN.parent
# `python tools/x.py` puts tools/ on sys.path and the repo root nowhere, so the shared log
# reader below is only importable once the root is inserted explicitly.
if str(BRAIN) not in sys.path:
    sys.path.insert(0, str(BRAIN))

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
NUMBER = re.compile(r"(?<![\w.,:/\-→])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+)(?![\w,])")
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


def _fight_observation() -> Optional[Dict[str, Any]]:
    """What tools/observation_contract_probe.py last measured (§L.34).

    The slim live observation is only safe while it is field-identical to the full one, so the
    parity count and its timings belong on disk, not in prose. Absent file means never measured --
    reported as absent, never guessed.
    """
    path = BRAIN / "data" / "fight_observation_baseline.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def fight_obs_field(key: str, as_int: bool = False):
    def compute() -> Optional[Any]:
        row = _fight_observation()
        if row is None or row.get(key) is None:
            return None
        return int(row[key]) if as_int else row[key]
    return compute


def fight_obs_mismatches() -> Optional[int]:
    row = _fight_observation()
    if row is None:
        return None
    keys = ("contract_mismatches", "board_block_mismatches",
            "slim_view_mismatches", "full_view_mismatches")
    return int(sum(row.get(key) or 0 for key in keys))


# The head's win rate, its abstain ceiling and the paired delta between them. These were prose for
# six sections: the number anyone would re-derive in about a minute with tools/wsp_fight.py, and the
# one most likely to be quoted after the tree moved under it. Each reads a document the harness
# itself wrote -- the delta comes from `wsp_fight.py compare`, not from a re-derivation of it.
def _fight_run(name: str) -> Optional[Dict[str, Any]]:
    path = BRAIN / "data" / name
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def fight_run_field(name: str, *keys: str):
    """One value out of a run document: `summary.network_win_rate`, or top-level `mean_score_delta`."""
    def compute() -> Optional[Any]:
        data = _fight_run(name)
        if data is None:
            return None
        cursor: Any = data
        for key in keys:
            if not isinstance(cursor, dict):
                return None
            cursor = cursor.get(key)
        return cursor
    return compute


def fight_run_ci(index: int):
    def compute() -> Optional[Any]:
        summary = (_fight_run("fight_head_production.json") or {}).get("summary") or {}
        ci = summary.get("win_rate_bootstrap_ci95") or []
        return ci[index] if len(ci) > index else None
    return compute


def _abs_fact(compute):
    """Magnitude of a signed measurement, for prose that must not carry the sign."""
    def wrapped() -> Optional[Any]:
        value = compute()
        return None if value is None else abs(value)
    return wrapped


def _brain_ram(key: str):
    def compute() -> Optional[Any]:
        path = BRAIN / "data" / "brain_ram_baseline.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        return data.get(key) if isinstance(data, dict) else None
    return compute


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


def snippet_tests() -> Optional[int]:
    """How many gate tests the mutation sweep runs per mutation, read from the driver.

    The count sits in prose in two places, and it moves whenever a check is registered.
    Counted on the assembled SNIPPET string, not by a regex over the source: the driver
    also contains `r._run_test(_t)`, which is one call, not one per test.
    """
    path = BRAIN / "tools" / "mutate_cause_gate.py"
    if not path.exists():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SNIPPET":
                    try:
                        value = ast.literal_eval(node.value)
                    except ValueError:
                        return None
                    return value.count("r.test_") if isinstance(value, str) else None
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


LIVE_EVIDENCE = BRAIN / "data" / "evidence" / "live_sessions.jsonl"


def _last_evidence_row() -> Optional[Dict[str, Any]]:
    from agent import jsonio

    last: Optional[Dict[str, Any]] = None
    for row in jsonio.iter_rows(LIVE_EVIDENCE):
        last = row
    return last


def live_evidence_totals() -> Optional[Dict[str, Any]]:
    """The last tracked capture of the live per-instance logs (G96).

    §12.2's budget numbers were measured from `%LOCALAPPDATA%` files that are not in the
    repository; `tools/session_close.py` now copies their derived totals into
    `data/evidence/live_sessions.jsonl`, so the docs can quote the capture instead of a
    number nobody can re-measure after one disk cleanup.
    """
    totals = (_last_evidence_row() or {}).get("totals")
    return totals if isinstance(totals, dict) else None


def _live_total(key: str) -> Optional[Any]:
    totals = live_evidence_totals()
    return None if totals is None else totals.get(key)


def live_budget_share() -> Optional[float]:
    """Recover + replan as a share of the step budget -- §12.2's headline ratio."""
    totals = live_evidence_totals() or {}
    steps = int(totals.get("steps") or 0)
    actions = totals.get("recoveries") or {}
    if not steps:
        return None
    return round(100.0 * (int(actions.get("recover") or 0)
                          + int(actions.get("replan") or 0)) / steps, 1)


def live_top_slot_interventions() -> Optional[int]:
    """The worst slot's intervention count, read from the capture's per-instance rows."""
    rows = (_last_evidence_row() or {}).get("instances") or []
    values = [int(row.get("manual_interventions") or 0) for row in rows
              if isinstance(row, dict)]
    return max(values) if values else None


def trajectory_rows() -> Optional[Tuple[int, int]]:
    root = BRAIN / "trajectories"
    if not root.exists():
        return None
    files = [p for p in root.rglob("*.jsonl") if p.is_file()]
    return len(files), _lines(files)


def patched_bundle_chars() -> Optional[int]:
    """UTF-16 code units, because that is what `--bundle-selftest` prints ("patched N chars").

    C# `string.Length` counts code units; Python `len(str)` counts code points. The bundle
    contains one astral character, so the two differ by exactly one - enough to make a
    correct sentence look wrong.
    """
    path = REPO / "desktop" / "client" / "www" / "build" / "script.js"
    if not path.exists():
        return None
    try:
        return len(path.read_bytes().decode("utf-8").encode("utf-16-le")) // 2
    except (OSError, UnicodeDecodeError):
        return path.stat().st_size


def source_constant(relative: str, name: str, cast=float):
    """A threshold the code declares, read from the code - the docs argue about these."""
    path = BRAIN / relative
    if not path.exists():
        return None
    found = re.search(r"^%s\s*=\s*([\d.]+)" % re.escape(name),
                      path.read_text(encoding="utf-8"), re.M)
    if not found:
        return None
    try:
        return cast(found.group(1))
    except ValueError:
        return None


def manifest(field: str):
    path = BRAIN / "data" / "transitions_temporal" / "manifest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get(field)


def payload_inventory():
    """(file entries, unpacked bytes) read out of `setup.exe` itself.

    Sharing `tools/payload_audit.py` rather than re-counting the staging tree is the point: the
    README quoted 619 files and 245.4 MB unpacked long after the artifact had become 509 and 242.7,
    because those numbers were typed from a build log instead of measured from the binary.
    """
    exe = REPO / "setup.exe"
    if not exe.exists():
        return None, None
    import importlib.util
    spec = importlib.util.spec_from_file_location("payload_audit", REPO / "tools" / "payload_audit.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    blob = exe.read_bytes()
    found = audit.find_zip(blob)
    if found is None:
        return None, None
    base, entries, cd_off, cd_size = found
    rows = audit.central_directory(blob, base, cd_off, cd_size, entries)
    files = [r for r in rows if not r[0].endswith(("/", "\\"))]
    return len(files), sum(r[2] for r in files)


# name -> (compute, how it was measured, anchors that identify its sentence during --adopt)
FACTS: Dict[str, Tuple[object, str, Tuple[str, ...]]] = {
    "tools": (capabilities_count, "len(data/capabilities.json capabilities)", (r"\btools?\b",)),
    "gate_checks": (gate_checks, "data/last_gate_run.json, written by agent.regression.run()", (r"checks",)),
    "fight_obs_boards": (fight_obs_field("boards", as_int=True),
                         "data/fight_observation_baseline.json boards, written by tools/observation_contract_probe.py",
                         (r"recorded boards", r"own-turn boards")),
    "fight_obs_mismatches": (fight_obs_mismatches,
                             "sum of the four parity mismatch counts in data/fight_observation_baseline.json",
                             (r"mismatch",)),
    "fight_obs_removed_pct": (fight_obs_field("removed_pct_of_observation_path"),
                              "data/fight_observation_baseline.json removed_pct_of_observation_path",
                              (r"observation path",)),
    "fight_obs_full_ms": (fight_obs_field("full_ms_per_board"),
                          "data/fight_observation_baseline.json full_ms_per_board (shipped interpreter)",
                          (r"build\+validate",)),
    "fight_obs_slim_ms": (fight_obs_field("slim_ms_per_board"),
                          "data/fight_observation_baseline.json slim_ms_per_board (shipped interpreter)",
                          (r"live_view",)),
    "brain_resident_mb": (_brain_ram("resident_mb"),
                          "data/brain_ram_baseline.json resident_mb "
                          "(tools/brain_ram_probe.py on runtimes/python)",
                          (r"resident",)),
    "brain_peak_mb": (_brain_ram("peak_mb"),
                      "data/brain_ram_baseline.json peak_mb (tools/brain_ram_probe.py)",
                      (r"peak",)),
    "brain_commit_mb": (_brain_ram("commit_mb"),
                        "data/brain_ram_baseline.json commit_mb (tools/brain_ram_probe.py)",
                        (r"commit",)),
    "brain_decide_ms": (_brain_ram("ms_per_decide"),
                        "data/brain_ram_baseline.json ms_per_decide, decide() only -- the loop's "
                        "wall clock is ~2.7x that because it includes reading and filling the payload",
                        (r"ms/turn", r"per decide",)),
    "fight_head_win_rate": (fight_run_field("fight_head_production.json", "summary",
                                            "network_win_rate"),
                            "data/fight_head_production.json summary.network_win_rate "
                            "(tools/wsp_fight.py base --matchups 200 --policy production)",
                            (r"win rate",)),
    "fight_head_ci_low": (fight_run_ci(0),
                          "data/fight_head_production.json summary.win_rate_bootstrap_ci95[0]",
                          (r"CI ",)),
    "fight_head_ci_high": (fight_run_ci(1),
                           "data/fight_head_production.json summary.win_rate_bootstrap_ci95[1]",
                           (r"CI ",)),
    "fight_head_wins": (fight_run_field("fight_head_production.json", "summary", "wins"),
                        "data/fight_head_production.json summary.wins", (r"win",)),
    "fight_head_draws": (fight_run_field("fight_head_production.json", "summary", "draws"),
                         "data/fight_head_production.json summary.draws", (r"draw",)),
    "fight_head_losses": (fight_run_field("fight_head_production.json", "summary", "losses"),
                          "data/fight_head_production.json summary.losses", (r"loss",)),
    "fight_ceiling_win_rate": (fight_run_field("fight_head_ceiling.json", "summary",
                                               "network_win_rate"),
                               "data/fight_head_ceiling.json summary.network_win_rate "
                               "(--abstain 1.01, so both sides play the solver)",
                               (r"ceiling",)),
    "fight_head_cost_vs_solver": (_abs_fact(
        fight_run_field("fight_head_vs_solver.json", "mean_score_delta")),        "abs(data/fight_head_vs_solver.json mean_score_delta) -- the head is BELOW the ceiling, and "
        "a negative literal both renders and re-parses badly, so the direction is prose",
        (r"cost", r"paired delta")),
    "mutations": (mutation_count, "len(tools/mutate_cause_gate.py MUTATIONS)", (r"mutations?",)),
    "snippet_tests": (snippet_tests, "r.test_ references in tools/mutate_cause_gate.py SNIPPET", (r"snippet", r"gate tests")),
    "py_lines": (lambda: brain_python()[1], "dofus-network/**/*.py minus caches, models, artifacts and the kaggle mirror", (r"Python",)),
    "py_files": (lambda: brain_python()[0], "same scope as py_lines", (r"Python",)),
    "cs_lines": (lambda: _code((".cs",), (REPO / "desktop", REPO / "builder"))[1], "desktop/**/*.cs + builder/**/*.cs minus bin/obj", (r"C#",)),
    "cs_files": (lambda: _code((".cs",), (REPO / "desktop", REPO / "builder"))[0], "same scope as cs_lines", (r"C#",)),
    "go_lines": (lambda: _code((".go",), (REPO / "scripts", REPO / "tools"))[1], "scripts/*.go + tools/*.go", (r"Go\b",)),
    "js_lines": (lambda: _code((".js",), (REPO / "scripts", REPO / "tools"))[1], "scripts/*.js + tools/*.js minus the client bundle", (r"JS\b", r"JavaScript")),
    "script_lines": (lambda: _code((".go",), (REPO / "scripts", REPO / "tools"))[1] + _code((".js",), (REPO / "scripts", REPO / "tools"))[1], "go_lines + js_lines", (r"Go/JS", r"Go and JS")),
    "setup_bytes": (lambda: file_size(REPO / "setup.exe"), "stat(setup.exe), bytes", (r"setup\.exe",)),
    "payload_files": (lambda: payload_inventory()[0],
                      "file entries in setup.exe's central directory (tools/payload_audit.py)",
                      (r"files",)),
    "payload_unpacked": (lambda: payload_inventory()[1],
                         "sum of uncompressed sizes in setup.exe's central directory",
                         (r"unpacked",)),
    "bundle_chars": (patched_bundle_chars, "UTF-16 chars of build/script.js, what --bundle-selftest prints", (r"script\.js", r"bundle", r"chars")),
    "gaps_max": (gaps_max, "highest `# G<n>` heading in docs/KNOWN_GAPS.md", ()),
    "known_gaps_lines": (lambda: doc_lines("KNOWN_GAPS.md"), "line count of docs/KNOWN_GAPS.md", (r"KNOWN_GAPS\.md",)),
    "resume_lines": (lambda: doc_lines("resume.md"), "line count of resume.md", (r"resume\.md",)),
    "readme_lines": (lambda: doc_lines("README.md"), "line count of README.md", (r"README\.md",)),
    "trajectory_files": (lambda: (trajectory_rows() or (None, None))[0], "count of trajectories/**/*.jsonl", (r"trajector",)),
    "trajectory_rows": (lambda: (trajectory_rows() or (None, None))[1], "line count of trajectories/**/*.jsonl", (r"trajector",)),
    # What the datasets and the policy constants say - the numbers the analysis argues about.
    # The live budget (§12.2), read from the capture session_close copies into the repo (G96).
    "live_logs": (lambda: len((_last_evidence_row() or {}).get("instances") or []), "instances in the last data/evidence/live_sessions.jsonl row", (r"live log",)),
    "live_sessions": (lambda: _live_total("sessions"), "capture totals sessions", (r"sessions",)),
    "live_wall_minutes": (lambda: _live_total("wall_minutes"), "capture totals wall_minutes", (r"wall", r"min wall")),
    "live_steps": (lambda: _live_total("steps"), "capture totals steps", (r"steps",)),
    "live_ok": (lambda: _live_total("ok"), "capture totals ok", (r"\bok\b",)),
    "live_failed": (lambda: _live_total("failed"), "capture totals failed", (r"failed",)),
    "live_interventions": (lambda: _live_total("manual_interventions"), "capture totals manual_interventions", (r"intervention",)),
    "live_top_slot_interventions": (live_top_slot_interventions, "max manual_interventions over the capture's instances", (r"one slot",)),
    "live_recover": (lambda: (live_evidence_totals() or {}).get("recoveries", {}).get("recover"), "capture totals recoveries.recover", (r"recover",)),
    "live_replan": (lambda: (live_evidence_totals() or {}).get("recoveries", {}).get("replan"), "capture totals recoveries.replan", (r"replan",)),
    "live_retry": (lambda: (live_evidence_totals() or {}).get("recoveries", {}).get("retry"), "capture totals recoveries.retry", (r"retry",)),
    "live_recovery_rows": (lambda: _live_total("recovery_rows"), "sum of the capture's recoveries", (r"recovery rows",)),
    "live_budget_share": (live_budget_share, "100 x (recover + replan) / steps in the capture", (r"budget", r"% of the step")),
    "transition_rows": (lambda: manifest("rows"), "data/transitions_temporal/manifest.json rows", (r"\brows\b",)),
    "transition_segments": (lambda: manifest("segments"), "manifest segments", (r"segments",)),
    "transition_actions": (lambda: manifest("actions"), "manifest actions", (r"\bactions\b",)),
    "transition_claims_met": (lambda: manifest("claims_met"), "manifest claims_met", (r"claims[ -]met", r"\bclaims\b")),
    "label_version": (lambda: manifest("label_version"), "manifest label_version", (r"label_version", r"label version")),
    "veto_at": (lambda: source_constant("agent/gate.py", "VETO_AT", int), "agent/gate.py VETO_AT", (r"VETO_AT",)),
    "promotion_retention": (lambda: source_constant("tools/live_outcome_probe.py", "PROMOTION_RETENTION"),
                            "tools/live_outcome_probe.py PROMOTION_RETENTION", (r"retention",)),
    "promotion_delta_points": (lambda: source_constant("tools/live_outcome_probe.py", "PROMOTION_DELTA_POINTS"),
                               "tools/live_outcome_probe.py PROMOTION_DELTA_POINTS", (r"PROMOTION_DELTA_POINTS",)),
    "promotion_min_counter_vetoes": (lambda: source_constant("tools/live_outcome_probe.py", "PROMOTION_MIN_COUNTER_VETOES", int),
                                     "tools/live_outcome_probe.py PROMOTION_MIN_COUNTER_VETOES", (r"MIN_COUNTER_VETOES", r"counter vetoes")),
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
    if isinstance(value, float):
        return format(int(value), ",") if value.is_integer() else ("%r" % value)
    return format(value, ",") if isinstance(value, int) else "unmeasured"


# --------------------------------------------------------------------------- rendering

def render(text: str, measured: Optional[Dict[str, Optional[int]]] = None) -> str:
    """Materialise every measurement as `value<!--f:name-->`.

    A bare number cannot be checked and a bare placeholder cannot be read. The tag is the
    join: the reader sees the number (the comment is invisible in rendered markdown), and
    the tool can find and repair it without understanding the sentence around it.
    `code spans` are left alone so the docs can describe the syntax without being rewritten.
    """
    measured = measured if measured is not None else values()

    def rewrite(line: str) -> str:
        spans = [m.span() for m in CODE_SPAN.finditer(line)]

        def inside(pos: int) -> bool:
            return any(a <= pos < b for a, b in spans)

        def expand(match: re.Match) -> str:
            value = measured.get(match.group(1))
            return "%s<!--f:%s-->" % (fmt(value), match.group(1)) if value is not None else match.group(0)

        def repair(match: re.Match) -> str:
            value = measured.get(match.group(1))
            return "%s<!--f:%s-->" % (fmt(value), match.group(1)) if value is not None else match.group(0)

        line = re.sub(r"\{\{([a-z_]+)\}\}", lambda m: m.group(0) if inside(m.start()) else expand(m), line)
        return re.sub(r"[\d,.]+<!--f:([a-z_]+)-->", lambda m: m.group(0) if inside(m.start()) else repair(m), line)

    return "".join(rewrite(line) for line in text.splitlines(keepends=True))


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
        if line[m.end():m.end() + 5] == "<!--f":
            continue                      # already tagged; re-adopting would nest the tag
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
            raw = token.replace(",", "")
            try:
                got = float(raw) if "." in raw else int(raw)
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
    """One question per tagged number: does it still equal its measurement?

    Untagged prose is not examined - a number nobody measured is not a claim this tool
    can settle, and pretending otherwise is how the hand-written rule tables came back.
    """
    measured = values()
    seen = history()
    problems: List[str] = []
    for path in DOCS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            spans = [m.span() for m in CODE_SPAN.finditer(line)]   # `{{fact}}` documents the syntax
            inside = lambda pos: any(a <= pos < b for a, b in spans)   # noqa: E731
            if verbose:
                _unused, adoptable = adopt_line(line, measured, seen)
                if adoptable:
                    print("  note: %s:%d has an untagged measured number (%s) - run --adopt"
                          % (path.name, lineno, ", ".join(adoptable)))
            for match in re.finditer(r"([\d,.]+)<!--f:([a-z_]+)-->", line):
                if inside(match.start()):
                    continue
                token, name = match.group(1), match.group(2)
                if name not in FACTS:
                    problems.append("%s:%d unknown fact %r" % (path.name, lineno, name))
                    continue
                want = measured.get(name)
                if want is None:
                    if verbose:
                        print("  note: %s:%d tags %s, which has no measurement yet"
                              % (path.name, lineno, name))
                    continue
                raw = token.replace(",", "")
                try:
                    got = float(raw) if "." in raw else int(raw)
                except ValueError:
                    got = None
                if got != want:
                    problems.append("%s:%d [%s] says %s, measured %s"
                                    % (path.name, lineno, name, token, fmt(want)))
            for match in TOKEN.finditer(line):
                if inside(match.start()):
                    continue
                name = match.group(1)
                if name not in FACTS:
                    problems.append("%s:%d unknown reference {{%s}}" % (path.name, lineno, name))
                elif measured.get(name) is not None:
                    problems.append("%s:%d {{%s}} was never rendered - run --render"
                                    % (path.name, lineno, name))
                elif verbose:
                    print("  note: %s:%d references {{%s}}, which has no measurement yet"
                          % (path.name, lineno, name))
    if verbose:
        if problems:
            print("doc facts: %d problem(s)" % len(problems))
            for p in problems:
                print("  " + p)
        else:
            print("doc facts: every tagged number matches its measurement")
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
