"""Client-side manager for the combat brain service (``combat/combat_ai_server.py``).

Why this exists. The page asks ``http://127.0.0.1:8891/v1/combat/decide`` for every fight turn
(``mobile-disguise.js`` ``_askBrain``). If nothing answers, the page records ``_brainDownAt`` and
falls back to a hardcoded heuristic for 30 s at a time. Measured over the recorded corpus: 388
in-fight observations, all of them played by that heuristic, and ``live_policy_decisions.jsonl``
holds one warm-up row -- the trained policy has never taken a live turn.

Nothing starts the server. The host spawns the sentinel from
``desktop/mainform.cs:TryStartSingletonSentinel`` with the *embedded* interpreter, and the
embedded runtime has no ``jax`` (``import jax`` -> ModuleNotFoundError) while the server needs it.
So a spawn with the wrong interpreter exits immediately, which is indistinguishable from "not
started" on a machine where nobody is watching. Hence: probe the port, pick an interpreter that can
actually import the dependency, and report which one was used instead of assuming.

This module is deliberately import-cheap: it does not import jax, the server, or the policy, so
the stdlib-only regression gate can test it.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent          # .../dofus-network
BRAIN_SCRIPT = REPO_ROOT / "combat" / "combat_ai_server.py"
BRAIN_PORT = 8891
# The checkpoint declares 4 spell slots and loads a JAX graph; measured cold start on this
# machine (import jax + load params + warm-up) was ~10 s. The bound is not a "give up" clock:
# past it the loop continues either way, because the page's own fallback is still a decision.
BRAIN_READY_TIMEOUT_S = 90.0
_PROBE_TIMEOUT_S = 30.0


def port_is_open(port: int = BRAIN_PORT, host: str = "127.0.0.1",
                 timeout: float = 0.35) -> bool:
    """True if something is accepting on the brain's port."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def brain_is_up(port: int = BRAIN_PORT, timeout: float = 1.0) -> Optional[str]:
    """Ask /health on this port. Returns the model name when the brain answers, else None.

    A port can be held by a half-started server, so the health body -- not the socket -- is
    what proves the brain will answer a decide request. The first argument is a port, not a
    URL: `ensure_brain` threads the port it was given through here, and handing an int to
    `urlopen` raises AttributeError rather than reporting a dead brain.
    """
    target = "http://127.0.0.1:%d/health" % int(port)
    try:
        with urllib.request.urlopen(target, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not isinstance(body, dict) or body.get("status") != "ok":
        return None
    return str(body.get("model") or "ok")


def candidate_interceptors(env: Optional[Dict[str, str]] = None) -> List[str]:
    """Interpreters worth trying, cheapest-first, deduped.

    ``DOFUS_BRAIN_PYTHON`` wins because an operator who knows the answer should not have to
    watch the loop probe. The current interpreter goes first: in a dev checkout it usually *is*
    the one with jax. In the installed layout it is the embedded runtime, which is not, and the
    probe below is what discovers that in seconds instead of at the first fight.
    """
    environ = os.environ if env is None else env
    from shutil import which
    raw: List[str] = []
    override = str(environ.get("DOFUS_BRAIN_PYTHON") or "").strip()
    if override:
        raw.append(override)
    raw.append(sys.executable)
    # PATH names only, not a guessed install dir: the launcher (`py -3`) needs two tokens, and
    # a hardcoded C:\\Python314 would be a stale fact the moment the interpreter is upgraded.
    raw += [which(name) or "" for name in ("python", "python3")]
    # Deduped on the resolved path, case-normalised: on Windows `python.exe`, `python.EXE`
    # and a redirector's own path all name one interpreter, and each probe costs a process
    # spawn. Without this the loop paid twice for the same answer.
    out: List[str] = []
    seen: set = set()
    for item in raw:
        if not item:
            continue
        try:
            key = os.path.normcase(os.path.realpath(item))
        except OSError:
            key = os.path.normcase(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def interpreter_can_load_brain(exe: str,
                               probe: Optional[Callable[[str], bool]] = None) -> bool:
    """Does this interpreter import what the brain server imports?"""
    if probe is not None:
        return bool(probe(exe))
    if not exe or not Path(exe).exists() and not _on_path(exe):
        return False
    try:
        done = subprocess.run([exe, "-c", "import jax, numpy"],
                              capture_output=True, timeout=_PROBE_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _on_path(exe: str) -> bool:
    from shutil import which
    return which(exe) is not None


def pick_brain_interpreter(candidates: Optional[Sequence[str]] = None,
                           probe: Optional[Callable[[str], bool]] = None) -> Optional[str]:
    """First interpreter that can load the brain, or None when there is no capable one."""
    for exe in (candidates if candidates is not None else candidate_interceptors()):
        if interpreter_can_load_brain(exe, probe=probe):
            return exe
    return None


def spawn_brain(exe: str, port: int = BRAIN_PORT,
                launcher: Optional[Callable[[Sequence[str]], object]] = None) -> Dict[str, object]:
    """Start the brain detached. Returns a record of what happened; never raises."""
    cmd = [exe, str(BRAIN_SCRIPT), str(port)]
    if launcher is not None:
        try:
            handle = launcher(cmd)
        except Exception as exc:                    # noqa: BLE001 - a failed spawn is a fact
            return {"spawned": False, "reason": "launcher raised: %s" % exc, "cmd": cmd}
        return {"spawned": True, "reason": "spawned", "cmd": cmd,
                "pid": getattr(handle, "pid", None)}
    flags = 0
    if os.name == "nt":                              # detach so the loop's exit does not kill it
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        handle = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  stdin=subprocess.DEVNULL, creationflags=flags)
    except OSError as exc:
        return {"spawned": False, "reason": str(exc), "cmd": cmd}
    return {"spawned": True, "reason": "spawned", "cmd": cmd, "pid": handle.pid}


def ensure_brain(port: int = BRAIN_PORT, dry_run: bool = False,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 is_up: Callable[[int], Optional[str]] = brain_is_up,
                 open_port: Callable[[int], bool] = port_is_open,
                 pick: Callable[[], Optional[str]] = pick_brain_interpreter,
                 spawn: Callable[[str, int], Dict[str, object]] = spawn_brain,
                 timeout_s: float = BRAIN_READY_TIMEOUT_S) -> Tuple[str, Dict[str, object]]:
    """Make the brain reachable, or say precisely why not.

    Returns ``(state, detail)`` with state in:
      ``dry_run``      -- asked not to touch processes.
      ``reused``       -- a healthy brain is already answering (normal second run).
      ``port_held``    -- something holds 8891 but does not answer /health; the page's fallback
                          will keep fighting, and the operator needs to see who held it.
      ``no_interpreter`` -- no candidate could import the brain's deps; the fix is an install,
                          not a retry, so the loop must not spin here.
      ``spawn_failed``   -- the spawn itself errored.
      ``not_ready``      -- spawned, but /health never answered inside ``timeout_s``.
      ``started``        -- we spawned it and it is answering.
    """
    if dry_run:
        return "dry_run", {"reason": "dry run does not start services"}
    model = is_up(port)
    if model:
        return "reused", {"model": model}
    if open_port(port):
        return "port_held", {"port": port,
                             "reason": "port answers TCP but not /health"}
    exe = pick()
    if not exe:
        return "no_interpreter", {"reason": "no interpreter can import jax",
                                 "candidates": candidate_interceptors()}
    record = spawn(exe, port)
    if not record.get("spawned"):
        return "spawn_failed", record
    deadline = clock() + max(1.0, float(timeout_s))
    while clock() < deadline:
        model = is_up(port)
        if model:
            record["model"] = model
            return "started", record
        sleep(0.5)
    return "not_ready", {**record, "reason": "health never answered",
                         "waited_s": round(timeout_s, 1)}
