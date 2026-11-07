"""Import the shipped closure under the interpreter that ships, and report what cannot load.

Why this exists: `runtimes/python` is the interpreter setup.exe installs, and it is not the one the
repo's tests run under. Measured 2026-11-03 -- the development interpreter imports jax 0.12.0.1 and
the embedded one raises ModuleNotFoundError; `requirements.txt` declared four distributions while
the closure needed ten. Every check in the tree ran under the interpreter that had everything, so a
module that the installed app cannot import at all (`combat/grid_infer.py`, `ai/onnx_policy.py`,
`ai/lexical_policy.py`) looked perfectly healthy. A dependency that only the shipped interpreter is
missing is invisible until a user hits it.

The sweep takes its module list from `tools/shipped_deps.py` (the import closure of the real entry
points) and hands the embedded interpreter a generated script with the list *inside* it, because a
couple of shipped modules parse `sys.argv` at import time and an extra CLI argument makes one of
them exit with an argparse error instead of importing.

    python tools/embed_imports.py                  # embedded interpreter, whole closure
    python tools/embed_imports.py --interpreter X  # any other interpreter
    python tools/embed_imports.py --quiet          # one summary line, for the gate
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
NETWORK = REPO_ROOT / "dofus-network"
EMBEDDED = REPO_ROOT / "runtimes" / "python" / "python.exe"

# Entry scripts, not importable modules: they bind loopback ports on import in some paths and the
# installed app only ever runs them as programs.
SKIP = {"run_brain", "run_autopilot", "agent.serve", "networkhealth.sentinel"}
# `advisor.__main__` parses argv at import time (measured: SystemExit 2), and a __main__ module is
# never imported by the app -- running it as a script is the only supported use.
SKIP_SUFFIX = ".__main__"

# Modules that need jax, which the interpreter inside setup.exe does not have (measured 2026-11-03:
# the development interpreter imports jax 0.12.0.1, `runtimes/python/python.exe` raises
# ModuleNotFoundError). Listing them here is a recorded gap, not a fix: the live paths that reach
# them have to refuse instead of raising -- agent/executor.py now reports value_runtime_missing for
# combat.plan_value, combat/grid_infer.py returns None for the grid ranker, and
# combat/combat_ai_server.py used to import combat.live_policy at module level, which is the
# documented reason the host could not start :8891 on an installed copy. Since 2026-11-03 the fight
# models run on numpy when jax is absent (combat/encounter/backend.py), so these names are no
# longer jax-blocked -- they fail here because the runtime in the repo tree has *no* site-packages
# at all (site-packages never crosses the installer; `requirements.txt` is the installed copy's
# only source of numpy). The jax-free path itself is asserted by agent/regression.py, which scores
# the fight benchmark twice, once per backend, and requires the reports to agree. The gate's job
# here stays the same: keep the list from growing silently.
JAX_ONLY = {
    "combat.board_pca", "combat.grid_dataset", "combat.grid_model", "combat.grid_synth",
    "combat.grid_train", "combat.live_policy", "combat.plan_benchmark", "combat.plan_value",
    "combat.test_grid_model", "combat.test_live_policy",
}


def closure_modules() -> List[str]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import shipped_deps  # noqa: WPS433 - local tool

    files = shipped_deps.closure(list(shipped_deps.ROOTS))
    names = sorted({key[:-3].replace("/", ".") for key in files
                    if not key.startswith("_") and key.endswith(".py")})
    return [n for n in names
            if n not in SKIP and not n.startswith("run_") and not n.rsplit(".", 1)[-1].startswith("test_")]


def sweep(interpreter: Path, modules: List[str], timeout: int) -> Dict[str, object]:
    body = ("import importlib, json, sys\n"
            "sys.path.insert(0, %r)\n"
            "MODULES = json.loads(%r)\n"
            "bad, other = [], []\n"
            "for name in MODULES:\n"
            "    try:\n"
            "        importlib.import_module(name)\n"
            "    except (ImportError, ModuleNotFoundError) as exc:\n"
            "        bad.append([name, type(exc).__name__, str(exc)[:160]])\n"
            "    except BaseException as exc:\n"
            "        other.append([name, type(exc).__name__, str(exc)[:140]])\n"
            "print(json.dumps({'interpreter': sys.executable, 'python': sys.version.split()[0],\n"
            "                  'asked': len(MODULES), 'import_errors': bad, 'other_errors': other}))\n"
            % (str(NETWORK), json.dumps(modules)))
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as handle:
        handle.write(body)
        script = Path(handle.name)
    try:
        proc = subprocess.run([str(interpreter), str(script)], capture_output=True,
                              text=True, timeout=timeout, cwd=str(NETWORK))
    finally:
        script.unlink(missing_ok=True)
    lines = [l for l in proc.stdout.splitlines() if l.startswith("{")]
    if not lines:
        return {"ok": False, "reason": "no verdict line",
                "stderr": (proc.stderr or "")[-500:], "exit": proc.returncode}
    verdict = json.loads(lines[-1])
    verdict["ok"] = not verdict["import_errors"]
    verdict["exit"] = proc.returncode
    return verdict


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--interpreter", default=str(EMBEDDED))
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    modules = closure_modules()
    verdict = sweep(Path(args.interpreter), modules, args.timeout)
    if not verdict.get("ok") and "import_errors" not in verdict:
        print("sweep did not complete:", json.dumps(verdict)[:400])
        return 2
    errors = verdict["import_errors"]
    other = verdict["other_errors"]
    unexpected = [e for e in errors if e[0] not in JAX_ONLY]
    known = [e for e in errors if e[0] in JAX_ONLY]
    verdict["unexpected"] = unexpected
    verdict["known_jax_only"] = known
    if args.json:
        print(json.dumps(verdict))
    elif args.quiet:
        print("embedded import sweep: %d modules under %s (%s) -> %d unexpected import errors, "
              "%d known jax-only, %d other"
              % (verdict["asked"], verdict["interpreter"], verdict["python"],
                 len(unexpected), len(known), len(other)))
    else:
        print("asked %d modules to import under %s (Python %s)"
              % (verdict["asked"], verdict["interpreter"], verdict["python"]))
        for name, kind, detail in unexpected:
            print("  IMPORT FAIL %-42s %s: %s" % (name, kind, detail))
        for name, kind, detail in known:
            print("  known jax-only %-38s %s" % (name, kind))
        for name, kind, detail in other[:10]:
            print("  other            %-40s %s: %s" % (name, kind, detail))
    if unexpected:
        print("embedded import sweep FAILED: %d module(s) the shipped interpreter cannot load "
              "and that are not on the recorded jax-only list" % len(unexpected))
        return 1
    if not args.quiet and not args.json:
        print("embedded import sweep PASS (%d known jax-only gaps recorded in JAX_ONLY)" % len(known))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
