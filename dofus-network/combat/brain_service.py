"""Client-side manager for the combat brain service (``combat/combat_ai_server.py``).

Why this exists. The page asks ``http://127.0.0.1:8891/v1/combat/decide`` for every fight turn
(``mobile-disguise.js`` ``_askBrain``). If nothing answers, the page records ``_brainDownAt`` and
falls back to a hardcoded heuristic for 30 s at a time. Measured over the recorded corpus: 388
observations taken while a fight was open, and ``live_policy_decisions.jsonl`` holds 8 rows -- one
server warm-up plus seven written while probing by hand today (``fight_id`` 1234 and 99, neither a
page-supplied board). The trained policy has never taken a live turn.

Starting the server is necessary and, on its own, not sufficient: all 156 recorded live
``fight.state`` snapshots fail the observation contract (``max_hp``/``ap``/``mp`` null on the self
fighter, ``initiative`` absent, enemy max hp missing), so the brain would answer
``observation_not_actionable`` and the page would play its heuristic anyway. That half is KNOWN_GAPS
G25; this module is G24, and its job is to make the availability a recorded fact rather than an
assumption.

Nothing starts the server. The host spawns the sentinel from
``desktop/mainform.cs:TryStartSingletonSentinel`` with the *embedded* interpreter, and the
embedded runtime has no ``jax`` (``import jax`` -> ModuleNotFoundError) while the probe below
demands it. That demand is a decision, not a capability: since ARCH_REASSESSMENT.md §L.24 the
trained policy loads and answers on that interpreter through the numpy backend -- bit-identically
to the jax path on 135 recorded boards -- and it is kept out of fights because §L.31 measures it
0.0875 below the deterministic solver it would displace. So a spawn with the wrong interpreter
exits immediately, which is indistinguishable from "not started" on a machine where nobody is
watching. Hence: probe the port, pick an interpreter that can actually import the dependency, and
report which one was used instead of assuming.

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
    the one with jax. In the installed layout it is the embedded runtime, which is not -- not
    because the policy cannot run there (it can, on numpy) but because this probe asks for jax,
    which is the switch that keeps the solver in charge of installed fights. The probe below is
    what discovers the answer in seconds instead of at the first fight.
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


def brain_identity(port: int = BRAIN_PORT, timeout: float = 1.0) -> Optional[Dict[str, object]]:
    """The whole /health body, or None. `brain_is_up` answers "is it alive"; this answers
    "which process is alive" -- the token, pid, interpreter and checkpoint it reports.

    Split on purpose: the existing gate asserts `brain_is_up` returns a model string, and every
    caller that only needs liveness should keep using it.
    """
    target = "http://127.0.0.1:%d/health" % int(port)
    try:
        with urllib.request.urlopen(target, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def same_run_as_us(body: Optional[Dict[str, object]], run_token: Optional[str]) -> bool:
    """True only when the answering brain carries this session's token.

    An empty or missing token is NOT a match. `combat_ai_server` stamps its token from the
    environment, so a brain with none was started by hand or by a build that predates this
    scheme -- which is precisely the leftover that misled everyone on 2026-11-04, when the brain
    answering :8891 was 50 minutes and three restarts old and ran the dev interpreter.
    """
    if not run_token or not isinstance(body, dict):
        return False
    identity = body.get("identity")
    if not isinstance(identity, dict):
        return False
    return str(identity.get("token") or "") == str(run_token)


def reap_foreign_brain(body: Optional[Dict[str, object]],
                       killer: Optional[Callable[[int], None]] = None,
                       pid: Optional[int] = None,
                       reader: Optional[Callable[[int], str]] = None) -> Dict[str, object]:
    """Kill a brain that answers our port but belongs to an older session -- only if it is ours.

    The proof requirement is the whole design: a socket on 8891 is not evidence of ownership, and
    an orchestrator that kills "whatever holds the port" is a tool that eventually kills someone's
    unrelated server. Two proofs are accepted, in order:

    1. the health body names the same script file as `BRAIN_SCRIPT` (a server built after the
       identity field existed);
    2. the operating system's command line for the owning PID names `combat_ai_server.py` -- needed
       because the leftover actually found on :8891 predates field 1 and cannot volunteer it.

    Neither proof available, or `DOFUS_NO_REAP=1`, and nothing is killed: the caller gets a refusal
    with the reason and the pipeline reports the process instead of guessing at it.
    """
    raw = (body or {}).get("identity")
    identity = raw if isinstance(raw, dict) else {}
    if os.environ.get("DOFUS_NO_REAP") == "1":
        return {"reaped": False, "reason": "reaping disabled by DOFUS_NO_REAP=1",
                "pid": pid if pid is not None else identity.get("pid")}
    target = pid if isinstance(pid, int) else identity.get("pid")
    script = str(identity.get("script") or "")
    proven_by = None
    try:
        ours = bool(script) and Path(script).resolve() == BRAIN_SCRIPT.resolve()
    except OSError:
        ours = False
    if ours:
        proven_by = "health-identity"
    elif isinstance(target, int) and target > 0:
        if reader is None:
            try:
                from net.orchestrator import process_command_line as found
                reader = found
            except Exception:                                  # noqa: BLE001 - no proof available
                reader = lambda p: ""                          # noqa: E731
        ours = BRAIN_SCRIPT.name in (reader(int(target)) or "")
        proven_by = "command-line" if ours else None
    if not ours:
        return {"reaped": False,
                "reason": "the owner is not provably this brain script; refusing to kill what we "
                          "cannot prove is ours",
                "pid": target, "script": script}
    if not isinstance(target, int) or target <= 0:
        return {"reaped": False, "reason": "no usable pid to reap", "script": script}
    try:
        if killer is not None:
            killer(target)
        else:
            subprocess.run(["taskkill", "/PID", str(target), "/F"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"reaped": False, "reason": "taskkill failed: %s" % exc, "pid": target}
    return {"reaped": True, "pid": target, "proven_by": proven_by, "script": script,
            "interpreter": identity.get("interpreter"), "token": identity.get("token")}


def ensure_brain(port: int = BRAIN_PORT, dry_run: bool = False,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 is_up: Callable[[int], Optional[str]] = brain_is_up,
                 open_port: Callable[[int], bool] = port_is_open,
                 pick: Callable[[], Optional[str]] = pick_brain_interpreter,
                 spawn: Callable[[str, int], Dict[str, object]] = spawn_brain,
                 run_token: Optional[str] = None,
                 ident: Callable[[int], Optional[Dict[str, object]]] = brain_identity,
                 reap: Callable[[Optional[Dict[str, object]]],
                                Dict[str, object]] = reap_foreign_brain,
                 timeout_s: float = BRAIN_READY_TIMEOUT_S) -> Tuple[str, Dict[str, object]]:
    """Make the brain reachable, or say precisely why not.

    Returns ``(state, detail)`` with state in:
      ``dry_run``      -- asked not to touch processes.
      ``reused``       -- a healthy brain is already answering (normal second run).
      ``reused_foreign`` -- a healthy brain answers, but its run token is not ours: it belongs to
                          an earlier session. It is reaped and replaced, and if reaping is refused
                          the state is reported instead of being called ``reused`` -- which is the
                          exact mistake of 2026-11-04, where a 50-minute-old brain on the dev
                          interpreter looked healthy.
      ``reused_unlabelled`` -- same finding for a brain that reports no token at all.
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
    token = run_token if run_token is not None else os.environ.get("DOFUS_RUN_TOKEN", "")
    model = is_up(port)
    if model:
        body = ident(port)
        if same_run_as_us(body, token):
            return "reused", {"model": model,
                              "identity": (body or {}).get("identity") if body else None}
        identity = (body or {}).get("identity") if isinstance(body, dict) else None
        if not token or not isinstance(identity, dict):
            # We cannot judge ownership -- either this process has no session token, or the
            # answering server predates the identity field. Guessing "foreign" here would start a
            # SECOND brain to replace a server that is answering fine, which is the worse bug: the
            # gate's own test of that case is older than this field. Deciding ownership is the
            # network pipeline's job (`net/orchestrator.py`), where reaping needs proof.
            return "reused", {"model": model, "labelled": False,
                              "reason": "answering, ownership not attestable here; the pipeline "
                                        "decides whether it is this run's"}
        verdict = reap(body)
        if not verdict.get("reaped"):
            # Left for the operator to decide, never silently adopted and never labelled `reused`.
            return ("reused_foreign" if token else "reused_unlabelled"), {
                "model": model, "reap": verdict,
                "identity": (body or {}).get("identity") if body else None,
                "reason": "another session answers this port and reaping did not happen: %s"
                          % verdict.get("reason")}
        # Wait for the port to actually free before binding again: the kernel releases a listening
        # socket a moment after the process dies, and racing that produced the old double-listener.
        deadline = clock() + 10.0
        while clock() < deadline and open_port(port):
            sleep(0.25)
    if open_port(port) and not model:
        return "port_held", {"port": port,
                             "reason": "port answers TCP but not /health",
                             "identity": ident(port)}
    exe = pick()
    if not exe:
        return "no_interpreter", {
            "reason": "no interpreter can import jax; the policy itself loads on the embedded "
                      "runtime through the numpy backend, so this is the solver-by-choice (#77), "
                      "not a missing capability",
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
