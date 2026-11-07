"""One place that answers: who owns each network port, is it *this* run, and what did we do?

WHY THIS EXISTS. On 2026-11-04 the fight brain answering :8891 was a process started 50 minutes
and three app restarts earlier, on the dev interpreter rather than the shipped one, while the
current run believed it had started its own. Nothing could see that, because every component was
checked by a different owner with a different question: the C# supervisor asked "is the proxy
running", `brain_service` asked "does /health answer", the page asked "did my request work". A
healthy answer from a leftover process satisfies all three and is still wrong. `HTTPServer`'s
default SO_REUSEADDR makes it worse: on Windows it permits a second listener on the same port, so
traffic splits between two instance tables instead of failing loudly (`agent/serve.py:65` and
`aipanel.cs:1501` recorded the same shape on :8895).

WHAT THIS IS. A declared list of components with a probe, an owner test, and an action -- walked in
dependency order, once per ensure, producing one report. It does not invent a supervisor: the C#
host still starts the Go relay and WARP, and those components are **advisory** here, meaning this
pipeline reports them and never kills what it does not own. Components marked `owned` (the python
brain) are the ones it reaps and restarts.

The line that matters in the report is `run`, not `healthy`: `up-foreign` means something answers
and it is not this session's process.

    python -m net.orchestrator status
    python -m net.orchestrator ensure --json
    python -m net.orchestrator plan          # what it would do, touches nothing
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent          # .../dofus-network
TOKEN_FILE = REPO_ROOT / "data" / "run_token.json"
TOKEN_MAX_AGE_S = 6 * 3600.0


def run_token(path: Path = TOKEN_FILE, now: Optional[float] = None,
              write: bool = True) -> str:
    """This session's token: reused if fresh, created otherwise, and put in the environment.

    Children inherit `DOFUS_RUN_TOKEN`, so a server's self-reported token is proof it was started
    by this run. A token older than TOKEN_MAX_AGE_S is treated as a dead session, not a long-lived
    one: the failure mode we are guarding against is precisely a process that outlived its parent.
    """
    stamp = float(now if now is not None else time.time())
    token = ""
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if stamp - float(payload.get("created", 0)) < TOKEN_MAX_AGE_S:
                token = str(payload.get("token") or "")
    except (OSError, ValueError):
        token = ""
    if not token:
        token = secrets.token_hex(8)
        if write:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"token": token, "created": stamp,
                                            "pid": os.getpid()}), encoding="utf-8")
            except OSError:
                pass
    os.environ["DOFUS_RUN_TOKEN"] = token
    return token


def owner_pid(port: int, parse: Optional[Callable[[str], List[str]]] = None) -> Optional[int]:
    """The PID listening on 127.0.0.1:<port>, from `netstat -ano`, or None.

    Deliberately not psutil: nothing new has to be installed on the shipped runtime for this to
    work, and the answer we need is one column of a command that already exists on every Windows.
    """
    if parse is None:
        try:
            text = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                                  timeout=8).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        lines = text.splitlines()
    else:
        lines = parse("")
    needle = "127.0.0.1:%d" % int(port)
    for line in lines:
        if needle in line and "LISTENING" in line.upper():
            parts = line.split()
            try:
                return int(parts[-1])
            except (IndexError, ValueError):
                return None
    return None


def process_name(pid: int) -> str:
    """Image name for a PID, best effort; "" when it cannot be had."""
    try:
        text = subprocess.run(["tasklist", "/fi", "PID eq %d" % int(pid), "/fo", "csv", "/nh"],
                              capture_output=True, text=True, timeout=8).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first.split('","')[0].strip('"') if first else ""


def process_command_line(pid: int) -> str:
    """Full command line for a PID, "" when it cannot be had.

    Needed because a leftover process from before the identity field exists cannot prove itself
    over /health -- but the operating system still knows what script it was started with, and the
    orphan on :8891 was visibly `combat_ai_server.py 8891` in `wmic` output.
    """
    try:
        text = subprocess.run(["wmic", "process", "where", "ProcessId=%d" % int(pid),
                               "get", "CommandLine", "/value"],
                              capture_output=True, text=True, timeout=8).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in text.splitlines():
        if line.startswith("CommandLine="):
            return line[len("CommandLine="):].strip()
    return ""


def _http_json(url: str, timeout: float = 1.5) -> Optional[Dict[str, Any]]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _tcp_open(port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


# -- probes -------------------------------------------------------------------------------

def probe_brain(port: int = 8891, timeout: float = 1.5) -> Dict[str, Any]:
    """The combat brain: /health plus the identity it volunteers."""
    body = _http_json("http://127.0.0.1:%d/health" % int(port), timeout=timeout)
    if not isinstance(body, dict):
        return {"answering": False, "port_open": _tcp_open(port)}
    return {"answering": True, "port_open": True, "status": body.get("status"),
            "model": body.get("model"), "backend": body.get("backend"),
            "identity": body.get("identity") or {}}


def cors_contract(port: int = 8880, origin: str = "https://dofus.local",
                  path: str = "/__cdn/contract-probe", timeout: float = 3.0) -> Dict[str, Any]:
    """Ask the relay the way the game's own credentialed XHR does, and judge the answer.

    This exists because the same class of failure has now recurred across vendor updates: the relay
    answered `Access-Control-Allow-Origin: *`, Chromium discards a wildcard on a credentialed
    request, and the page reports `status 0` for a response the relay had already served with 200.
    Nothing on the server side looks wrong -- the request never arrives -- so the only way to catch
    it is to ask a question in the browser's shape and read the headers back.

    Returns {ok, checked, detail}. `ok` is False when the relay answers with a wildcard while an
    Origin was supplied, or omits credentials -- either is enough to black-screen the map loader.
    """
    request = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (int(port), path),
        headers={"Origin": origin, "Access-Control-Request-Method": "GET",
                 "Access-Control-Request-Headers": "x-requested-with"})
    method = "OPTIONS"
    try:
        request.method = method
        with urllib.request.urlopen(request, timeout=timeout) as response:
            headers = {k.lower(): v for k, v in response.getheaders()}
            code = response.status
    except urllib.error.HTTPError as exc:          # a 4xx still carries the CORS headers
        headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
        code = exc.code
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "checked": method, "detail": "no answer: %s" % exc}
    allow = str(headers.get("access-control-allow-origin") or "")
    creds = str(headers.get("access-control-allow-credentials") or "").lower()
    problems = []
    if allow == "*":
        problems.append("Allow-Origin is a wildcard; a credentialed request is discarded by "
                        "Chromium even when the body is 200 OK")
    elif allow and allow != origin:
        problems.append("Allow-Origin echoes %r, not the request's origin" % allow)
    if not allow:
        problems.append("no Access-Control-Allow-Origin header at all")
    if creds != "true":
        problems.append("Access-Control-Allow-Credentials is %r; the map XHR sends credentials"
                        % (creds or "absent"))
    return {"ok": not problems, "checked": method, "status": code,
            "allow_origin": allow, "allow_credentials": creds,
            "vary": headers.get("vary", ""), "problems": problems}


def probe_relay(port: int = 8880, timeout: float = 1.5) -> Dict[str, Any]:
    """The Go asset relay: reachable, and able to serve a credentialed browser request.

    Advisory for *actions* (the host starts and stops this process, so this pipeline reports it and
    never kills it) -- but it is still a problem when the CORS contract fails, because that failure
    shows up in the game as a black screen while every server-side counter stays green.
    """
    if not _tcp_open(port):
        return {"answering": False, "port_open": False}
    code = None
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/__cdn/health" % int(port),
                                    timeout=timeout) as response:
            code = response.status
    except urllib.error.HTTPError as exc:      # 404 still proves an HTTP server is there
        code = exc.code
    except (urllib.error.URLError, OSError):
        code = None
    contract = cors_contract(port=port, timeout=timeout)
    leak = relay_leak_risk(port=port)
    row = {"answering": code is not None, "port_open": True, "http_status": code,
           "cors": contract, "cors_ok": contract.get("ok")}
    if leak:
        # The host only adds `-allow-direct` when FailClosed is off (farmengine.cs:1320, and the
        # default is fail-closed at netsettings.cs:57). If it is present, someone turned the safety
        # off, and the relay would fall back to the machine's real IP on the first tunnel hiccup.
        row["problem"] = "relay-allows-direct"
    elif contract.get("ok") is False:
        row["problem"] = "cors-contract-violation"
    return row


def socks_fetch(url: str, port: int = 40000, timeout: float = 8.0) -> Optional[str]:
    """Fetch a URL THROUGH the tunnel and nothing else.

    There is deliberately no direct fallback in this function. The user's rule is that the real IP
    must never leave the machine, so a probe that could answer directly would be the very thing it
    is trying to detect. No answer through the tunnel is reported as "unreachable", never retried
    naked.
    """
    try:
        done = subprocess.run(["curl", "-s", "--max-time", str(int(timeout)),
                               "--socks5-hostname", "127.0.0.1:%d" % int(port), url],
                              capture_output=True, text=True, timeout=timeout + 3)
    except (OSError, subprocess.SubprocessError):
        return None
    body = (done.stdout or "").strip()
    return body or None


def start_tunnel(port: int = 40000) -> Dict[str, Any]:
    """Bring wireproxy up on the tunnel port, the way the host does, if it is not already.

    Only the egress endpoint is started here -- no routing, no fallback. `bin/warp` holds this
    machine's WireGuard private key, so this function never reads or prints the config, it only
    points wireproxy at it. The host kills wireproxy by name before starting its own
    (farmengine.cs:949), so a takeover here cannot leave two of them behind.
    """
    base = REPO_ROOT.parent / "bin" / "warp"
    exe = base / "wireproxy.exe"
    conf = base / "wireproxy.runtime.conf"
    if not conf.exists():
        conf = base / "wireproxy.conf"
    if not exe.exists() or not conf.exists():
        return {"started": False, "reason": "wireproxy.exe or its conf is missing under bin/warp"}
    if _tcp_open(port):
        return {"started": False, "reason": "already listening", "port": port}
    try:
        handle = subprocess.Popen([str(exe), "-c", str(conf)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return {"started": False, "reason": str(exc)}
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if _tcp_open(port):
            return {"started": True, "pid": handle.pid, "port": port}
        time.sleep(0.5)
    return {"started": False, "reason": "wireproxy started but :%d never listened" % port,
            "pid": handle.pid}


def attest_exit(port: int = 40000, fetch: Optional[Callable[[str], Optional[str]]] = None,
                store: Path = TOKEN_FILE.parent / "egress_attestation.json") -> Dict[str, Any]:
    """Ask an IP echo THROUGH the tunnel. Answering proves the tunnel carries traffic.

    Honest limit, stated because it is the kind of claim that gets over-read: this cannot prove a
    negative about the real IP without asking for it directly, which would itself be a request that
    must never happen. What it does prove is that the exit in use is the tunnel's, and that the
    tunnel is alive; the "never leak" guarantee itself lives in two other places -- the relay is
    started without `-allow-direct` (checked by `relay_leak_risk`) and nothing here fetches
    without the socks argument.
    """
    fetch = fetch or socks_fetch
    body = fetch("https://api.ipify.org")
    out: Dict[str, Any] = {"ok": bool(body), "exit": body}
    if not body:
        out["problem"] = "egress-unreachable"
        return out
    previous = None
    try:
        if store.exists():
            previous = json.loads(store.read_text(encoding="utf-8")).get("exit")
    except (OSError, ValueError):
        previous = None
    out["changed"] = bool(previous and previous != body)
    if previous and out["changed"]:
        # A different WARP exit is not a leak, but it does change the account's apparent geography,
        # so it is surfaced rather than treated as normal.
        out["note"] = "exit changed from %s" % previous
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps({"exit": body, "at": round(time.time(), 3)}), encoding="utf-8")
    except OSError:
        pass
    return out


def relay_leak_risk(port: int = 8880) -> Optional[str]:
    """The relay's own command line, read from the OS, if it says it may go direct."""
    pid = owner_pid(port)
    if not pid:
        return None
    line = process_command_line(pid)
    return "-allow-direct" if "-allow-direct" in line else None


def probe_egress(port: int = 40000) -> Dict[str, Any]:
    """The tunnel endpoint: is it listening, and does traffic actually leave through it?"""
    open_it = _tcp_open(port)
    if not open_it:
        return {"answering": False, "port_open": False, "problem": "down"}
    attested = attest_exit(port=port)
    row = {"answering": True, "port_open": True, "attestation": attested}
    if not attested.get("ok"):
        row["problem"] = attested.get("problem", "egress-unreachable")
    return row


def probe_tcp(port: int) -> Dict[str, Any]:
    """Liveness only, for components that do not speak /health here."""
    open_it = _tcp_open(port)
    return {"answering": open_it, "port_open": open_it}


# -- components --------------------------------------------------------------------------

COMPONENTS: List[Dict[str, Any]] = [
    {"name": "egress", "port": 40000, "probe": probe_egress, "owned": False,
     "starter": start_tunnel,
     "expect": "wireproxy.exe", "depends": [],
     "note": "the only road out; without it the relay is a cache with no upstream, and nothing "
             "may substitute a direct connection for it"},
    {"name": "relay", "port": 8880, "probe": probe_relay, "owned": False,
     "expect": "dofus-net-proxy.exe", "depends": ["egress"],
     "note": "host-owned; two of these on 8880 is the failure this file was written for"},
    {"name": "brain", "port": 8891, "probe": probe_brain, "owned": True,
     "expect": "combat_ai_server.py", "depends": ["relay"],
     "note": "the fight decisions; owned here, so a foreign answer is reaped and restarted"},
    {"name": "agent", "port": 8895, "probe": probe_tcp, "owned": False,
     "expect": "python.exe", "depends": [],
     "note": "this process answers it when run_brain.py is the one running"},
]


def classify(component: Dict[str, Any], token: str,
             identity_key: str = "token") -> Dict[str, Any]:
    """one row of the report: state is about *ownership*, not just liveness."""
    probe = component.get("probe")
    got = probe(component["port"]) if probe else {"answering": None, "port_open": None}
    row = {"component": component["name"], "port": component["port"],
           "owned_by_this_pipeline": component["owned"],
           "expect": component["expect"], "note": component["note"]}
    pid = owner_pid(component["port"])
    row["owner_pid"] = pid
    row["owner_image"] = process_name(pid) if pid else ""
    if not got.get("port_open"):
        row["state"] = str(got.get("problem") or "down")
    elif got.get("problem"):
        # A probe can name its own failure: a listener that answers TCP but cannot attest an exit,
        # or a relay started with direct fallback allowed. Both are "up and wrong".
        row["state"] = str(got["problem"])
        if got.get("attestation"):
            row["attestation"] = got["attestation"]
    elif not got.get("answering"):
        row["state"] = "bound-not-answering"
    elif got.get("cors_ok") is False:
        # Reachable, answering, and still wrong: the browser will throw this response away. Only a
        # contract check in the browser's shape can tell that, so the state is named for it.
        row["state"] = "cors-contract-violation"
        row["cors"] = got.get("cors")
    else:
        row["state"] = "answering"
        for key in ("status", "model", "backend", "http_status"):
            if got.get(key) is not None:
                row[key] = got[key]
        identity = got.get("identity") or {}
        if identity:
            row["identity"] = identity
            seen = str(identity.get(identity_key) or "")
            if not seen:
                # No token at all means it was started by hand or by an older build. That is not
                # "mine, unluckily unlabelled" -- an unlabelled process is exactly the leftover we
                # are trying to catch, so it is called foreign and the owner decides.
                row["run"] = "foreign-unlabelled"
            elif seen == token:
                row["run"] = "this-run"
            else:
                row["run"] = "foreign"
        elif component.get("owned"):
            # An owned server answering without any identity at all is also foreign. Caught live:
            # the orphan brain on :8891 was healthy, model-loaded, answering -- and reported no
            # identity, because it predates the field. Treating "cannot tell" as reusable would
            # have re-run the incident this file exists to close.
            row["run"] = "foreign-unlabelled"
    return row


def status(token: Optional[str] = None) -> Dict[str, Any]:
    token = token or os.environ.get("DOFUS_RUN_TOKEN") or run_token(write=False)
    rows = [classify(c, token) for c in COMPONENTS]
    return {"token": token, "checked_at": round(time.time(), 3),
            "components": rows,
            "problems": [r["component"] for r in rows
                         if r["state"] != "answering" or r.get("run") in ("foreign",
                                                                          "foreign-unlabelled")]}


def plan(token: Optional[str] = None) -> List[Dict[str, Any]]:
    """What `ensure` would do, without touching a process."""
    report = status(token)
    actions = []
    for row in report["components"]:
        component = next(c for c in COMPONENTS if c["name"] == row["component"])
        actionable = component["owned"] or component.get("starter")
        if not actionable:
            actions.append({"component": row["component"], "action": "report-only",
                             "reason": "owned by the host, not this pipeline"})
        elif component["name"] == "egress" and row["state"] != "answering":
            actions.append({"component": row["component"], "action": "start-tunnel",
                            "reason": "state %s; wireproxy from bin/warp, never a direct route"
                                      % row["state"]})
        elif row["state"] == "down":
            actions.append({"component": row["component"], "action": "start",
                            "reason": "nothing answering :%d" % row["port"]})
        elif row["state"] == "bound-not-answering":
            actions.append({"component": row["component"], "action": "reap-then-start",
                            "reason": "socket answers, /health does not"})
        elif row.get("run") in ("foreign", "foreign-unlabelled"):
            actions.append({"component": row["component"], "action": "reap-then-start",
                            "reason": "answers /health but is not this run (owner pid %s)"
                                      % row.get("owner_pid")})
        else:
            actions.append({"component": row["component"], "action": "reuse",
                            "reason": "this run answers it"})
    return actions


def ensure(token: Optional[str] = None, sleep: Callable[[float], None] = time.sleep,
           actor: Optional[Callable[[Dict[str, Any], str], Dict[str, Any]]] = None,
           timeout_s: float = 60.0) -> Dict[str, Any]:
    """Walk the components in dependency order, act on the owned ones, verify, and report.

    `actor` defaults to `combat.brain_service.ensure_brain`: keeping one brain-ensure path means
    the orchestrator cannot drift from what the autopilot does when it starts a brain itself.
    """
    token = token or os.environ.get("DOFUS_RUN_TOKEN") or run_token(write=False)
    if actor is None:
        from combat.brain_service import ensure_brain as default_actor

        def actor(comp, tok):
            # Bound to the name actually imported: an earlier version referenced `ensure_brain`,
            # which no test reached because every suite passed its own actor -- so the default
            # path died with a NameError the first time it ran for real.
            return default_actor(run_token=tok)
    before = status(token)
    rows = {r["component"]: r for r in before["components"]}
    actions = []
    for component in COMPONENTS:
        row = rows[component["name"]]
        starter = component.get("starter")
        if not (component["owned"] or starter):
            actions.append({"component": component["name"], "action": "report-only",
                            "state": row["state"],
                            "problems": (row.get("cors") or {}).get("problems")
                            or ([row.get("attestation", {}).get("problem")]
                                if row.get("attestation") else [])})
            continue
        if starter and not component["owned"]:
            # Egress: the tunnel is the one thing that may not be substituted, so the only action
            # here is to start wireproxy when it is missing and re-attest through it. Never direct.
            did = None if row["state"] == "answering" else starter()
            verified = classify(component, token)
            actions.append({"component": component["name"],
                            "action": "start-tunnel" if did else "re-attest",
                            "started": did, "verified": verified})
            continue
        if row.get("run") in ("foreign", "foreign-unlabelled"):
            # brain_service refuses to kill a server it cannot identify -- right, because guessing
            # there means starting a second brain over one that is answering fine. Ownership is
            # decided here instead, so this is where a proven stale server is reaped: the owning PID
            # comes from the OS and the proof from its command line, because a leftover that
            # predates the identity field cannot name itself any other way.
            try:
                from combat.brain_service import reap_foreign_brain
                verdict = reap_foreign_brain(None, pid=row.get("owner_pid"))
            except Exception as exc:                          # noqa: BLE001 - reported, not fatal
                verdict = {"reaped": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
            actions.append({"component": component["name"], "action": "reap", "reap": verdict})
            if verdict.get("reaped"):
                deadline = time.monotonic() + 15.0
                while time.monotonic() < deadline and _tcp_open(component["port"]):
                    time.sleep(0.4)
        state, detail = actor(component, token)
        actions.append({"component": component["name"], "action": state, "detail": detail})
        deadline = time.monotonic() + max(1.0, timeout_s)
        verified = None
        while time.monotonic() < deadline:
            verified = classify(component, token)
            if verified["state"] == "answering" and verified.get("run") == "this-run":
                break
            sleep(0.5)
        actions[-1]["verified"] = verified
    after = status(token)
    return {"token": token, "before": before, "actions": actions, "after": after,
            # One key, whatever the mode: a caller reading `problems` should never have to know
            # whether it got a `status` report or an `ensure` one.
            "problems": after["problems"], "clean": not after["problems"]}


STATUS_PATH = REPO_ROOT / "data" / "net_status.json"


def write_status(report: Dict[str, Any]) -> None:
    """The last report, on disk, so the GUI and any external tool read one file."""
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps(report, default=str), encoding="utf-8")
    except OSError:
        pass


def report_now(refresh: bool = False) -> Dict[str, Any]:
    """The stored report if there is one, else a fresh check (never silently empty).

    One shape for every reader. A watchdog write stores an `ensure` report, whose live view is
    nested under `after` and carries no top-level timestamp, while a manual check stores a
    `status` report that does. A GUI reading the file or the route should not have to know which
    one it got -- and a reader that cannot tell staleness treats a three-hour-old green as live.
    """
    if not refresh:
        try:
            if STATUS_PATH.exists():
                report = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
                after = report.get("after") or {}
                if "components" not in report and "components" in after:
                    report["components"] = after["components"]
                if not report.get("checked_at") and after.get("checked_at"):
                    report["checked_at"] = after["checked_at"]
                report.setdefault("problems", [r["component"] for r in report["components"]
                                               if r.get("state") != "answering"])
                return report
        except (OSError, ValueError):
            pass
    try:
        report = status()
    except Exception as exc:                                # noqa: BLE001 - the route must answer
        report = {"error": "%s: %s" % (type(exc).__name__, exc), "components": [], "problems": ["probe"]}
    report.setdefault("checked_at", round(time.time(), 3))
    return report


def start_watchdog(interval_s: float = 120.0, log: Optional[Callable[[str], None]] = None,
                   stop: Optional["threading.Event"] = None) -> "threading.Thread":
    """Check, repair, remember and say it out loud -- on a timer, not when someone remembers.

    Every failure this module was written for presented as a black screen while some counter
    somewhere still said green: a stale helper holding the port, a relay whose 200 OK the browser
    throws away, a listener that routes nothing. A check that only runs when a human thinks of it
    is documentation, not protection. Nothing here may reach the machine's real address: every
    fetch in this file goes through the tunnel, and a failure to attest is reported, not retried
    direct.
    """
    import threading

    stop = stop or threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                report = ensure()
                report["kind"] = "ensure"
                write_status(report)
                if log and report.get("problems"):
                    log("[NET] pipeline: " + ", ".join(
                        "%s=%s" % (r["component"], r["state"])
                        for r in report["after"]["components"]))
                elif log:
                    log("[NET] pipeline clean")
            except Exception as exc:                        # noqa: BLE001 - a broken check must speak
                if log:
                    log("[NET] pipeline check failed: %s: %s" % (type(exc).__name__, exc))
            stop.wait(max(15.0, float(interval_s)))

    thread = threading.Thread(target=loop, name="net-watchdog", daemon=True)
    thread.start()
    return thread


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", nargs="?", default="status",
                    choices=("status", "plan", "ensure", "token"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.mode == "token":
        print(json.dumps({"token": run_token()}))
        return 0
    out = {"status": status, "plan": plan, "ensure": ensure}[args.mode]()
    print(json.dumps(out, indent=1 if args.json else None, default=str))
    # Only a live check decides whether to fail the caller. `plan` is advisory: it touches
    # nothing, so a foreign process must not turn a dry run into a non-zero exit. (An earlier
    # version called .get on the plan list and crashed *after* printing the answer.)
    if args.mode == "status":
        return 1 if out.get("problems") else 0
    if args.mode == "ensure":
        return 1 if out.get("after", {}).get("problems") else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
