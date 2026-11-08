"""One command that runs every test suite, and the exit status is the answer.

The suites exist and are good; nothing ran them together, so a green tree meant
"my last run looked fine". This runs each ``test_*.py`` under ``dofus-network``
in its own process with a timeout, then ``agent.regression`` -- the harness that
carries about a thousand checks -- and returns 0 only if every suite passed.

    python tools/gate.py                  # full gate
    python tools/gate.py --list           # what would run
    python tools/gate.py --only combat    # suites whose path matches
    python tools/gate.py --timeout 300    # per-suite ceiling (default 120 s)

A suite that hangs is killed and reported as ``timeout`` rather than stalling
the gate; a suite that fails prints the tail of its output. ``agent.regression``
is an integration harness -- it drives a real client and runs minutes, not
seconds -- so it gets its own budget (``--regression-timeout``, 900 s) instead
of the per-suite ceiling. The vendored
kaggle/toolkit copy is a mirror, so it is not run twice here -- drift is caught
by ``tools/sync_kaggle_toolkit.py --check``, which is suite zero.

Every suite is started through a bootstrap that puts ``dofus-network`` on
``sys.path``: the embedded interpreter ships a ``python311._pth``, which ignores
``PYTHONPATH``, so ``-m agent.regression`` fails there unless the path is set
from inside the process. Suites are run as modules first, which keeps their
relative imports working, and via ``run_path`` only if the module cannot be
found.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                     # dofus-network
EXCLUDED_PARTS = {"kaggle", "__pycache__", ".venv", "site-packages"}

# The embedded runtime ships no jax (CURRENT.md says so: it is why the host
# cannot start the combat brain). A suite that dies importing one of these is
# reported as blocked, not failed, so the gate's exit status still means
# "something regressed" rather than "this machine lacks a wheel".
OPTIONAL_IMPORTS = ("jax",)

# Named test_* but not a suite: it is a proxy-probing CLI (`--help`, SOCKS5 tunnels)
# whose default run dials every proxy in the list, so it hangs a gate instead of
# testing one. Run it directly: python ai/test_egress.py --help
NOT_SUITES = {
    "ai/test_egress.py",
}


def missing_optional(output):
    """Name of the optional dependency whose absence caused this failure."""
    for name in OPTIONAL_IMPORTS:
        if "No module named '%s'" % name in (output or ""):
            return name
    return None


def can_import(python, name):
    """Whether an interpreter can load a module at all."""
    try:
        probe = subprocess.run([python, "-c", "import %s" % name], cwd=str(ROOT),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=45)
        return probe.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


_INTERPRETERS = {}


def alternate_for(name):
    """An interpreter on this machine that has the module the embedded one lacks."""
    if name in _INTERPRETERS:
        return _INTERPRETERS[name]
    found = None
    seen = {os.path.normcase(sys.executable)}
    for candidate in (shutil.which("python"), shutil.which("python3")):
        if not candidate or os.path.normcase(candidate) in seen:
            continue
        seen.add(os.path.normcase(candidate))
        if can_import(candidate, name):
            found = candidate
            break
    _INTERPRETERS[name] = found
    return found

BOOTSTRAP = """import runpy, sys
root, module, path = %r
sys.path.insert(0, root)
try:
    runpy.run_module(module, run_name="__main__", alter_sys=True)
except ModuleNotFoundError as exc:
    if not (exc.name or "").startswith(module.split(".")[0]):
        raise
    runpy.run_path(path, run_name="__main__")
"""


def command_for(path):
    """Run a suite with dofus-network importable and __name__ == '__main__'."""
    module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
    script = BOOTSTRAP % ((str(ROOT), module, str(path)),)
    return [sys.executable, "-c", script]


def drift_check():
    """Suite zero: the vendored toolkit must still match its source tree."""
    script = ("import sys; sys.path.insert(0, %r); "
              "from tools import sync_kaggle_toolkit as s; "
              "raise SystemExit(s.main(['--check']))" % str(ROOT))
    return [sys.executable, "-c", script]


def suites(pattern=None):
    found = []
    for path in sorted(ROOT.rglob("test_*.py")):
        parts = set(path.relative_to(ROOT).parts)
        if parts & EXCLUDED_PARTS:
            continue
        if path.relative_to(ROOT).as_posix() in NOT_SUITES:
            continue
        if pattern and pattern not in path.as_posix():
            continue
        found.append(path)
    return found


def run(command, cwd, timeout, env):
    started = time.time()
    try:
        proc = subprocess.run(command, cwd=str(cwd), env=env, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors="replace")
        return proc.returncode, time.time() - started, proc.stdout
    except subprocess.TimeoutExpired as exc:
        output = exc.output or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        return 124, time.time() - started, output + "\n[timeout after %ss]" % timeout


def tail(text, lines=12):
    rows = [row for row in (text or "").splitlines() if row.strip()]
    return "\n".join("    " + row for row in rows[-lines:])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default=None, help="substring filter on suite paths")
    parser.add_argument("--timeout", type=int, default=120, help="per-suite seconds")
    parser.add_argument("--regression-timeout", type=int, default=900,
                        help="seconds for agent.regression, which drives a real client")
    parser.add_argument("--list", action="store_true", help="print the suites and stop")
    parser.add_argument("--skip-regression", action="store_true",
                        help="run only the test_*.py suites")
    parser.add_argument("--verbose", action="store_true", help="show passing output too")
    parser.add_argument("--strict", action="store_true",
                        help="treat suites blocked by a missing optional dependency as failures")
    parser.add_argument("--no-interpreter-fallback", action="store_true",
                        help="do not retry a missing-dependency suite under another python")
    parser.add_argument("--json", default=None, help="write a report to this path")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    only = args.only or ""
    steps = []                                     # (label, command, seconds or None)
    if not only or "kaggle" in only:
        steps.append(("toolkit mirror", drift_check(), None))
    for path in suites(args.only):
        steps.append((path.relative_to(ROOT).as_posix(), command_for(path), None))
    if not args.skip_regression and (not only or "regression" in only):
        steps.append(("agent.regression", command_for(ROOT / "agent" / "regression.py"),
                      args.regression_timeout))

    if args.list:
        for label, _command, budget in steps:
            print("%s%s" % (label, "" if budget is None else "  [%ss]" % budget))
        print("\n%d step(s)" % len(steps))
        return 0

    results, failures, blocked = [], [], []
    started = time.time()
    for index, (label, command, budget) in enumerate(steps, 1):
        limit = budget or args.timeout
        code, seconds, output = run(command, ROOT, limit, env)
        gap = missing_optional(output) if code != 0 else None
        used = sys.executable
        if gap and not args.no_interpreter_fallback:
            alternate = alternate_for(gap)
            if alternate:
                # The suite is not broken: this interpreter just cannot load the
                # dependency. Re-run the same bootstrap under one that can, and
                # report which interpreter actually ran it. The retry's output is
                # kept even when it fails, so a second, different failure is not
                # disguised as "blocked by a missing dependency".
                retry_code, retry_seconds, retry_output = run([alternate] + command[1:],
                                                             ROOT, limit, env)
                seconds = seconds + retry_seconds
                output, used = retry_output, alternate
                if retry_code == 0:
                    code, gap = 0, None
                else:
                    code = retry_code
                    if missing_optional(retry_output) != gap:
                        gap = None
        if code == 0:
            verdict = "pass"
        elif code == 124:
            verdict = "timeout"
        elif gap:
            verdict = "blocked"
        else:
            verdict = "fail"
        if verdict in ("fail", "timeout") or (verdict == "blocked" and args.strict):
            failures.append((label, verdict, output))
        if verdict == "blocked":
            blocked.append((label, gap))
        results.append({"suite": label, "verdict": verdict, "exit": code,
                        "seconds": round(seconds, 2), "missing": gap,
                        "python": used})
        print("[%2d/%2d] %-8s %6.2fs  %s%s" % (
            index, len(steps), verdict, seconds, label,
            "" if used == sys.executable else "  [via %s]" % Path(used).name))
        if code != 0:
            print(tail(output))
        elif args.verbose:
            print(tail(output, 4))

    elapsed = time.time() - started
    passed = sum(1 for row in results if row["verdict"] == "pass")
    print("\n%d/%d passed in %.1fs" % (passed, len(results), elapsed))
    if blocked:
        names = sorted({name for _label, name in blocked})
        print("%d blocked: this interpreter has no %s (--strict fails on these):"
              % (len(blocked), ", ".join(names)))
        for label, _name in blocked:
            print("  " + label)
    if failures:
        print("\nFAILURES:\n  " + "\n  ".join(label for label, _v, _o in failures))

    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"results": results, "seconds": round(elapsed, 2),
                                      "failed": len(failures),
                                      "blocked": len(blocked)}, indent=2), encoding="utf-8")
        print("report: %s" % target)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
