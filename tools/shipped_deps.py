"""Derive requirements.txt from what the shipped app actually imports -- not from a typed list.

requirements.txt is the only thing standing between an installed copy and a working AI
stack: builder/Build.cs excluded every ``site-packages`` file from the payload (rightly,
it is 1.27 GB), so an install gets the bare embedded interpreter plus whatever
``pip install -r requirements.txt`` pulls in (desktop/installer.cs runs it after copying).

The list the builder hardcoded was ``torch / transformers / safetensors`` (and, before
2026-11-07, ``thx01``). Measured
against this repository on 2026-11-03, the modules the shipped runtime imports also need
``numpy, scipy, scikit-learn, onnxruntime, tokenizers, psutil`` -- so an installed copy
could not load the INT8 encoder policy (``ai/onnx_policy.py`` needs onnxruntime +
tokenizers), could not load the lexical command policy (``ai/lexical_policy.py`` needs
sklearn), and any ``psutil`` caller failed. The same class of defect as
``combat/grid_infer.py`` importing jax: the development interpreter has everything, the
shipped one does not, and no check compared the two.

This tool computes the import closure of the entry points the installed app actually runs,
maps each third-party top-level module to its distribution, and either prints it
(``--check``) or writes it (``--write``). The gate runs ``--check``, so the list cannot
drift from the code again.

    python tools/shipped_deps.py            # what the closure needs vs what is declared
    python tools/shipped_deps.py --write    # regenerate requirements.txt from the code
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

REPO_ROOT = Path(__file__).resolve().parents[1]           # .../dofus-emu
NETWORK = REPO_ROOT / "dofus-network"

# Entry points the installed app actually starts, plus the seam modules the host reaches
# through them. Offline trainers (ai/train_*.py, tools/*) are NOT roots: their imports are
# not the installer's problem, and including them would inflate the list for code the
# product never loads.
ROOTS = (
    "run_brain.py",
    "run_autopilot.py",
    "agent/serve.py",
    "agent/autopilot.py",
    "agent/executor.py",
    "agent/commands.py",
    "agent/planner.py",
    "combat/combat_ai_server.py",
    "networkhealth/sentinel.py",
    "ai/onnx_policy.py",
    "ai/lexical_policy.py",
)

# Until 2026-11-07 the sentinel was separated out because it imported torch at module scope
# (`TinyRecursiveReasoner` and the THX-01 checkpoint). That code is gone and the sentinel now
# imports with `sys.modules['torch'] = None`, so every root is a core root.
CORE_ROOTS = ROOTS

# Installed by `install-python-ai.bat`, not by `setup.exe`. Sizes and the modules that need each one
# are written into requirements-ai.txt.
#
# onnxruntime/tokenizers/scikit-learn/joblib joined on 2026-11-03 with the weights they load: the
# four int8/graph artifacts and policy.joblib (47.8 MB in-zip, 45.5% of setup.exe) no longer ship,
# so an installed copy has nothing for those wheels to read. They come back with the tier that asks
# for them -- and the weights are copied in by whoever runs `--policy encoder`, the command shadow
# or DOFUS_LEARNED_COMMAND_MODEL, because the graphs live in the repo, not in the installer.
AI_TIER = {"torch", "transformers", "safetensors",
           "onnxruntime", "tokenizers", "sklearn", "joblib"}

# Distributions that stay in the runtime tier even though the import graph only reaches them
# lazily or under `except ImportError`: their weights ship inside the installer, and the feature is
# on by default, so an absent wheel would silently remove a product capability. This is the
# successor to the old "union, never a substitution" rule -- the judgement is the same, but each
# case now says why instead of inheriting whatever someone typed.
RUNTIME_TIER_EXTRA = {
    "requests": "data/sync_full_database.py and the brain's knowledge ingest job",
}


def deferred_modules(files: Dict[str, Set[str]]) -> Set[str]:
    """Top-level names reached only through an import inside a function or class body."""
    return set(files.get("_deferred") or ())
DIST = {
    "numpy": "numpy",
    "scipy": "scipy",
    "sklearn": "scikit-learn",
    "torch": "torch",
    "transformers": "transformers",
    "safetensors": "safetensors",
    "onnxruntime": "onnxruntime",
    "tokenizers": "tokenizers",
    "psutil": "psutil",
    "joblib": "joblib",
    "yaml": "pyyaml",
    "requests": "requests",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "matplotlib": "matplotlib",
    "networkx": "networkx",
    "pandas": "pandas",
    "tqdm": "tqdm",
    "regex": "regex",
    "sentence_transformers": "sentence-transformers",
}

STDLIB = set(sys.stdlib_module_names)

# Imported by shipped code but deliberately absent from requirements.txt, with the reason.
# jax is the live case: the interpreter inside setup.exe has no jax wheel and no plan to carry one
# (see tools/embed_imports.py's JAX_ONLY list and docs/ARCH_REASSESSMENT.md §L.1).
KNOWN_UNMAPPED = {"jax", "setup", "site"}


def local_module_names() -> Set[str]:
    """Top-level names that belong to this repo (so they are not treated as deps)."""
    out: Set[str] = set()
    for path in NETWORK.glob("*"):
        if path.is_dir() and (path / "__init__.py").exists():
            out.add(path.name)
        elif path.suffix == ".py":
            out.add(path.stem)
    for path in NETWORK.glob("*/*.py"):
        out.add(path.stem)
    return out


def _import_nodes(body):
    """Import statements that would execute *at import time*.

    `ast.walk` finds every import in a file, including the ones written inside a function
    body. Those are deferred: they only run if that code path is taken, which is exactly the
    difference between a wheel every install must download and one an opt-in feature needs.
    Measured 2026-11-03: `ast.walk` called torch a hard dependency of the shipped app, while
    importing the daemon, the agent HTTP server, the combat server and both runners with
    `sys.modules['torch'] = None` succeeded -- torch's real footprint is 508.8 MB installed,
    and the only shipped entry point that needs it at import time is the network-health
    sentinel. Descent stops at function/class boundaries but continues through module-level
    if/try/loop bodies, because an import inside those does run at import time.
    """
    skip = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    stack = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, skip):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        for field in ("body", "orelse", "finalbody"):
            children = getattr(node, field, None)
            if isinstance(children, list):
                stack.extend(children)
        for handler in getattr(node, "handlers", []) or []:
            stack.extend(handler.body)


def _names(nodes):
    out = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def _node_targets(node, pkg_parts) -> Set[str]:
    """The dotted module names one import statement can bring in.

    `from combat import plan_value` imports two modules -- the package and the submodule -- so both
    are returned. A name that turns out to be a symbol rather than a file resolves to nothing.
    """
    out: Set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            out.add(alias.name)
        return out
    if not isinstance(node, ast.ImportFrom):
        return out
    if node.level == 0:
        base = node.module or ""
    else:
        keep = len(pkg_parts) - (node.level - 1)
        prefix = pkg_parts[:max(0, keep)]
        base = ".".join(prefix + ([node.module] if node.module else []))
    if base:
        out.add(base)
    for alias in node.names:
        out.add((base + "." + alias.name) if base else ".".join(pkg_parts + [alias.name]))
    return out


def _parse(file: Path):
    """(eager dotted targets, every dotted target) for one file, or empty sets if unparseable."""
    try:
        tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set(), set()
    pkg_parts = list(file.relative_to(NETWORK).parts[:-1])
    eager = _names_of(_import_nodes(tree.body), pkg_parts)
    everything = set(eager)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            everything |= _node_targets(node, pkg_parts)
    return eager, everything


def _names_of(nodes, pkg_parts) -> Set[str]:
    out: Set[str] = set()
    for node in nodes:
        out |= _node_targets(node, pkg_parts)
    return out


_CACHE: Dict[str, Tuple[Set[str], Set[str]]] = {}


def parsed(file: Path):
    key = str(file)
    if key not in _CACHE:
        _CACHE[key] = _parse(file)
    return _CACHE[key]


def eager_targets(file: Path) -> Set[str]:
    """Dotted modules imported at import time."""
    return parsed(file)[0]


def all_targets(file: Path) -> Set[str]:
    return parsed(file)[1]


def top_levels(dotted: Set[str]) -> Set[str]:
    return {name.split(".")[0] for name in dotted}


def eager_names(file: Path) -> Set[str]:
    """Top-level packages this file needs the moment it is imported."""
    return top_levels(eager_targets(file))


def imported_names(file: Path) -> Set[str]:
    """Top-level packages this file imports anywhere, including inside a function body."""
    return top_levels(all_targets(file))


def optional_names(file: Path) -> Set[str]:
    """Names imported under ``except ImportError`` -- the code already degrades without them.

    Without this split the tool would demand psutil in requirements.txt, because
    ai/encoder_cost_probe.py prefers psutil and falls back to GetProcessMemoryInfo. Listing
    it anyway would put a wheel in every install to satisfy a probe, which is the opposite
    of what "optimize byte by byte" means: a dependency belongs in the list only when the
    product cannot run without it.
    """
    try:
        tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    out: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        guarded: Set[str] = set()
        for stmt in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if isinstance(stmt, ast.Import):
                guarded.update(a.name.split(".")[0] for a in stmt.names)
            elif isinstance(stmt, ast.ImportFrom) and stmt.level == 0 and stmt.module:
                guarded.add(stmt.module.split(".")[0])
        handles = any(
            (isinstance(h.type, ast.Name) and h.type.id in ("Exception", "BaseException"))
            or (isinstance(h.type, ast.Tuple) and any(
                isinstance(e, ast.Name) and e.id in ("ImportError", "ModuleNotFoundError")
                for e in h.type.elts))
            or (isinstance(h.type, ast.Name) and h.type.id in ("ImportError", "ModuleNotFoundError"))
            for h in node.handlers)
        if handles:
            out.update(guarded)
    return out


def _target_file(dotted: str):
    """The file an import statement actually loads, or None when it names a symbol."""
    parts = dotted.split(".")
    module = NETWORK.joinpath(*parts)
    as_file = module.with_suffix(".py")
    if as_file.is_file():
        return as_file
    if (module / "__init__.py").is_file():
        return module / "__init__.py"
    return None


def closure(roots: List[str]) -> Dict[str, Set[str]]:
    """Walk imports from the entry points; return {file: third-party top-level names}.

    Two graphs are walked, because they answer different questions. The **eager** graph follows
    only imports that execute at import time, and it is what decides whether an install must
    download a wheel: a package reachable only through a function body is not needed to start the
    app, and importing a package runs its `__init__.py`, not every file inside it. The **full**
    graph follows every import edge so that unmapped distributions and lazily reached packages are
    still reported rather than hidden.
    """
    start = [NETWORK / r for r in roots]
    missing_roots = [str(q.relative_to(NETWORK)) for q in start if not q.exists()]
    local = local_module_names()

    def eager_set(path: Path) -> Set[str]:
        guarded = optional_names(path)
        return {t for t in eager_targets(path) if t.split(".")[0] not in guarded}

    # Pass 1: import-time reachability from the entry points.
    eager_files: Dict[str, Set[str]] = {}
    queue = list(start)
    while queue:
        path = queue.pop()
        if not path.is_file():
            continue
        key = str(path.relative_to(NETWORK)).replace("\\", "/")
        if key in eager_files:
            continue
        targets = eager_set(path)
        eager_files[key] = targets
        for dotted in targets:
            found = _target_file(dotted)
            if found is not None:
                queue.append(found)

    # Pass 2: every edge, for reporting.
    optional: Set[str] = set()
    deferred: Set[str] = set()
    hard: Dict[str, Set[str]] = {}
    unmapped: Dict[str, str] = {}
    seen: Set[str] = set()
    queue = list(start)
    while queue:
        path = queue.pop()
        key = str(path.relative_to(NETWORK)).replace("\\", "/")
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        targets = all_targets(path)
        names = top_levels(targets)
        guarded_here = optional_names(path)
        here = {n for n in names if n in DIST}
        if key in eager_files:
            # A distribution is required at install time only when a file the eager walk reached
            # imports it, unguarded, at its own import time.
            eager_top = {t.split(".")[0] for t in eager_targets(path) if t.split(".")[0] in DIST}
            hard[key] = {n for n in eager_top if n not in guarded_here}
            # ...and whatever else that same file imports *inside a function body* is a deferred
            # import, not nothing. Without this line a name only ever reached from a method of an
            # eagerly-reached file fell into no bucket at all -- which is how scikit-learn and
            # joblib (ai/lexical_policy.py, lines 273-290) vanished from the tier the tool writes,
            # while the same shape in a lazily reached file (`agent/command_onnx.py`) was reported
            # correctly as deferred.
            deferred |= here - hard[key]
        else:
            deferred |= here
        optional |= {n for n in here if n in guarded_here}
        for name in names:
            # An import that is neither stdlib, nor a repo module, nor in DIST used to fall out of
            # this walk silently -- so a distribution the installed copy will not have could hide
            # in precisely the file nobody had mapped. Record it instead, with where it came from.
            if (name in STDLIB or name in DIST or name in local or name in guarded_here
                    or name in KNOWN_UNMAPPED):
                continue
            unmapped.setdefault(name, key)
        for dotted in targets:
            found = _target_file(dotted)
            if found is not None:
                queue.append(found)
    required_anywhere = {n for names in hard.values() for n in names}
    deferred -= required_anywhere
    optional -= required_anywhere | deferred
    return {"_missing_roots": set(missing_roots), "_optional": optional,
            "_deferred": deferred, "_unmapped": set(unmapped), "_unmapped_where": unmapped,
            **hard}


def needed(files: Dict[str, Set[str]]) -> Dict[str, str]:
    """Distributions the shipped path cannot run without: imported, and never only guarded."""
    out: Dict[str, str] = {}
    for key, names in files.items():
        if key.startswith("_"):
            continue
        for name in names:
            out[name] = DIST[name]
    return out


def declared(path: Path) -> Dict[str, str]:
    """requirements.txt as it stands: {import name: distribution}."""
    if not path.exists():
        return {}
    mapping: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        dist = re.split(r"[<>=!;\[]", line, maxsplit=1)[0].strip()
        reverse = {v: k for k, v in DIST.items()}
        mapping[reverse.get(dist, dist)] = dist
    return mapping


def _preserve_header(path: Path) -> str:
    """The comments at the top of a requirements file are its documentation; keep them."""
    if not path.is_file():
        return ""
    head = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            break
        head.append(line)
    return "\n".join(head).rstrip() + "\n\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true",
                    help="rewrite requirements.txt / requirements-ai.txt from the tiers")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    core_files = closure(list(CORE_ROOTS))
    all_files = closure(list(ROOTS))
    core_needed = set(needed(core_files).values())
    sentinel_only = set(needed(all_files).values()) - core_needed

    # The runtime tier is not just "what the graph reaches eagerly": a feature whose weights ship in
    # the payload must be installed even when its import is deferred or guarded. RUNTIME_TIER_EXTRA
    # says which, and why, so the decision is written down instead of being re-litigated per file.
    #
    # Every set here is in *distribution* names. `AI_TIER`/`RUNTIME_TIER_EXTRA` are written in import
    # names (`sklearn`, not `scikit-learn`), and comparing the two sides directly silently dropped
    # scikit-learn and joblib from the tier that exists to install them -- a name mismatch read as
    # "not needed anywhere". Map through DIST before comparing anything.
    ai_names = {DIST[m] for m in AI_TIER if m in DIST}
    reached = (set(core_needed) | set(sentinel_only)
               | {DIST[m] for m in deferred_modules(all_files) if m in DIST}
               | {DIST[m] for m in (all_files.get("_optional") or ()) if m in DIST})
    runtime = core_needed | {DIST[m] for m in RUNTIME_TIER_EXTRA if m in DIST}
    runtime = {n for n in runtime if n not in ai_names}
    ai_tier = ai_names & (reached | set(declared(REPO_ROOT / "requirements-ai.txt").values()))

    req = REPO_ROOT / "requirements.txt"
    ai_req = REPO_ROOT / "requirements-ai.txt"
    have = {n for n in declared(req).values()}

    # What must not drift: (1) a distribution the core product needs is missing from the runtime
    # tier; (2) an AI-tier package has crept back into the file setup.exe installs -- that is the
    # 617.7 MB the split exists to avoid; (3) something has started importing torch from a core
    # entry point, which would make the split a lie rather than a measurement.
    missing = sorted(runtime - have) if have else sorted(runtime)
    leaked = sorted(have & ai_tier)
    smuggled = sorted(n for n in sentinel_only if n in ai_names)

    if args.write:
        req.write_text(_preserve_header(req) + "".join("%s\n" % n for n in sorted(runtime)),
                       encoding="utf-8")
        ai_req.write_text(_preserve_header(ai_req) + "".join("%s\n" % n for n in sorted(ai_tier)),
                          encoding="utf-8")
        print("wrote requirements.txt (%d distributions: %s)"
              % (len(runtime), ", ".join(sorted(runtime))))
        print("wrote requirements-ai.txt (%d: %s)"
              % (len(ai_tier), ", ".join(sorted(ai_tier))))
        return 0

    if args.json:
        import json
        print(json.dumps({
            "core_modules": len(core_files) - 5, "runtime_tier": sorted(runtime),
            "ai_tier": sorted(ai_tier), "declared": sorted(have),
            "missing_from_runtime": missing, "ai_tier_in_runtime_file": leaked,
            "only_sentinel_needs": smuggled,
            "deferred_only": sorted(deferred_modules(all_files)),
            "missing_roots": sorted(core_files.get("_missing_roots") or []),
        }, indent=1))
        return 0

    print("import tiers of the shipped tree")
    print("  runtime tier (setup.exe installs) : %s" % ", ".join(sorted(runtime)))
    print("  AI tier (install-python-ai.bat)   : %s" % ", ".join(sorted(ai_tier)))
    print("  reached only through a deferred import: %s"
          % ", ".join(sorted(DIST.get(m, m) for m in deferred_modules(all_files))))
    print("  AI-tier names reached outside the core: %s" % (", ".join(smuggled) or "nothing"))
    print("  MISSING from requirements.txt       : %s" % (", ".join(missing) or "none"))
    print("  AI-tier package in requirements.txt : %s" % (", ".join(leaked) or "none"))
    if core_files.get("_missing_roots"):
        print("  NOTE: entry points not found on disk: %s" % sorted(core_files["_missing_roots"]))
    bad = bool(missing) or bool(leaked) or bool(core_files.get("_missing_roots"))
    print("shipped-deps check %s" % ("FAILED" if bad else "PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
