"""Which files a test actually touches -- code that ran, files it opened, processes it spawned.

Measured 2026-11-12 (G114): the ratchet meant to prove "some test in the sweep opens this
file" matched the *text* of each test body, so a comment naming the file satisfied it. The
`castable_error` anchors against `combat/battle_solver.py` reported MISSED, and the suite
that was supposed to prevent that went green on a docstring -- text cannot tell "this test
reads the file" from "this test mentions the file".

This records what a test cannot fake:

* `exec` -- a Python frame whose code came from the file ran, via `sys.monitoring`'s
  PY_START event (3.12+, and this host is 3.13).
* `read` -- the file was opened for reading, via the `open` audit event. This is the only
  channel that can see a `.cs`, `.js` or `.json` target, because those are read as text
  and never execute.
* `spawn` -- a child process was started with that path in its argv, via the
  `subprocess.Popen` audit event. A suite delegated to `node` or to `unittest` is
  witnessed here and nowhere else.

Cost measured over the sweep's own 123-test snippet: **59.4 s plain, 60.3 s instrumented**,
so the gate pays about 1.5% to stop guessing. The hot path is deliberately a single set
insert of a raw `co_filename` string: the first draft normalised each path inside the
PY_START callback and turned the same pass into more than twelve minutes before it was
killed (2026-11-12), which is why normalisation happens once, at read time.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

# The repo root: `dofus-network/agent/witness.py` -> `dofus-network` -> the repo. Targets
# live on both sides of it (`desktop/program.cs` above, `agent/planner.py` below), so
# paths are reported relative to the widest root that holds them.
BASE = Path(__file__).resolve().parents[2]

_TOOL_ID = 3
_TOOL_NAME = "gate-witness"

_RECORDS: Dict[str, Dict[str, Set[str]]] = {}
_ACTIVE: Optional[str] = None
_CUR: Optional[Dict[str, Set[str]]] = None
_HOOK_INSTALLED = False
_MONITORING = False
_ERROR: Optional[str] = None


def _on_start(code, offset) -> None:  # noqa: ARG001 - the event's signature
    """PY_START: one Python frame began executing in `code`'s file.

    The only work done here is a set insert of the raw filename. Anything more expensive
    (resolving, relativising, even `os.path.abspath`) is paid per *call*, not per file.
    """
    cur = _CUR
    if cur is not None:
        cur["exec"].add(code.co_filename)
    return None


def _audit(event: str, args) -> None:
    """Audit hook: reads and child processes. Cannot be removed, so it must be cheap when idle."""
    if _ACTIVE is None:
        return
    cur = _CUR
    if cur is None:
        return
    try:
        if event == "open":
            path, mode, flags = args
            if isinstance(path, int):
                return
            if mode is not None:
                if not str(mode).startswith("r"):
                    return
            elif flags is not None and flags & (os.O_WRONLY | os.O_RDWR):
                return
            cur["read"].add(os.fspath(path) if not isinstance(path, bytes)
                            else path.decode("utf-8", "replace"))
        elif event == "subprocess.Popen":
            _exe, argv = args[0], args[1]
            if isinstance(argv, (list, tuple)):
                cur["spawn"].add(" ".join(str(a) for a in argv))
            elif argv:
                cur["spawn"].add(str(argv))
    except Exception:  # noqa: BLE001 - an audit hook that raises kills the process
        pass


def install() -> bool:
    """Turn the instrument on for the rest of the process. False if the host cannot do it."""
    global _HOOK_INSTALLED, _MONITORING, _ERROR
    if _HOOK_INSTALLED:
        return True
    if not hasattr(sys, "monitoring"):
        _ERROR = "sys.monitoring needs Python 3.12+ (this is %s)" % sys.version.split()[0]
        return False
    tool = sys.monitoring
    try:
        tool.use_tool_id(_TOOL_ID, _TOOL_NAME)
        tool.register_callback(_TOOL_ID, tool.events.PY_START, _on_start)
    except ValueError as exc:                      # the id is taken by a profiler
        _ERROR = "sys.monitoring tool id %d is taken: %s" % (_TOOL_ID, exc)
        return False
    sys.addaudithook(_audit)
    _HOOK_INSTALLED = True
    _MONITORING = True
    return True


def start(name: str) -> None:
    """Begin recording `name`. Nested calls keep the outer name, which is what `_run_test` wants."""
    global _ACTIVE, _CUR
    if not _MONITORING or _ACTIVE is not None:
        return
    _ACTIVE = name
    _CUR = _RECORDS.setdefault(name, {"exec": set(), "read": set(), "spawn": set()})
    sys.monitoring.set_events(_TOOL_ID, sys.monitoring.events.PY_START)


def stop() -> None:
    global _ACTIVE, _CUR
    if _ACTIVE is None:
        return
    _ACTIVE = None
    _CUR = None
    sys.monitoring.set_events(_TOOL_ID, 0)


def available() -> bool:
    return _MONITORING


def active() -> bool:
    return _ACTIVE is not None


def error() -> Optional[str]:
    return _ERROR


def recorded() -> List[str]:
    """Every test the instrument saw start, in the order it started."""
    return list(_RECORDS)


def _relative(raw: str) -> str:
    """One raw path, normalised once: absolute, case-folded on Windows, relative to the repo."""
    try:
        absolute = os.path.abspath(raw)
    except (TypeError, ValueError):
        return raw
    absolute = os.path.normcase(absolute)
    base = os.path.normcase(str(BASE))
    if absolute == base:
        return "."
    if absolute.startswith(base + os.sep):
        return absolute[len(base) + 1:].replace("\\", "/")
    return absolute.replace("\\", "/")


def files(name: str) -> Dict[str, Set[str]]:
    """What `name` executed, read and spawned, normalised.

    `exec` and `read` are single paths and are made relative to the repository. `spawn`
    holds whole command lines, so it is only separator-normalised: a child process is
    witnessed by the path appearing in its argv, not by being that argv.
    """
    record = _RECORDS.get(name)
    if record is None:
        return {}
    return {"exec": {_relative(p) for p in record["exec"]},
            "read": {_relative(p) for p in record["read"]},
            "spawn": {p.replace("\\", "/") for p in record["spawn"]}}


def seen() -> Set[str]:
    """Every normalised path any recorded test touched, in any channel."""
    out: Set[str] = set()
    for name in _RECORDS:
        for kind, paths in files(name).items():
            out.update(paths)
    return out


def normalise(path: str) -> str:
    """The same normalisation the records use, for a target path from the sweep's source."""
    return _relative(str(path))


def witnesses_of(target: str) -> Dict[str, List[str]]:
    """The recorded tests that touched `target`, each with the channels it was touched through."""
    wanted = normalise(target)
    short = wanted.rsplit("/", 1)[-1]
    hits: Dict[str, List[str]] = {}
    for name, record in ((n, files(n)) for n in _RECORDS):
        kinds = []
        for kind in ("exec", "read"):
            # The full relative path, not the basename: `tools/` exists on both sides of
            # the repository root, so a bare `doc_facts.py` would match two files.
            if any(p == wanted or p.endswith("/" + wanted) for p in record[kind]):
                kinds.append(kind)
        if any(("/" + short) in line for line in record["spawn"]):
            kinds.append("spawn")
        if kinds:
            hits[name] = kinds
    return hits
