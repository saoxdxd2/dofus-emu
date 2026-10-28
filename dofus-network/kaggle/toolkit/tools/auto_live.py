"""Run the agent live, by itself, and bring back the logs as evidence.

This exists because "we need a live session" was the step that always needed a human: start the
host, launch an instance, wait for the login, run the loop, notice it died, repeat. Everything
here is the part a script can decide, and nothing here is a decision the agent should own: it
launches, waits, runs `agent.autopilot`, reads what came back.

What it will not do (measured, 2026-10-27):

* **launch into a dead egress.** `network.ini` said `mode=custom` with
  `upstream=socks5://127.0.0.1:40000` while nothing listened there, and `fail_closed=1` rightly
  refused direct. Six instances booted anyway, each raised the page's native `dofus.local says:
  Server busy` dialog — a modal that freezes the injected thread, so every directive timed out.
  Now the release preflight's `egress` check runs first and a FAIL stops the run (`--force` is the
  deliberate override, because the operator may know the tunnel is about to come up);
* **assume a click worked.** `host_control.launch_all` verifies the panel's own `N / M Running`
  count changed, and the loop waits for the *slot* to answer with `in_world` before spending a
  single step on it;
* **quietly produce an empty dataset.** Every round writes its log, its transition rebuild and a
  manifest row with the real move rate, so a round that achieved nothing says so in the numbers.

    python tools/auto_live.py --slot 1 --steps 120 --minutes 10
    python tools/auto_live.py --slot 4 --rounds 3 --shadow
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.bridge import client_count, directive_url  # noqa: E402

LOGS = REPO_ROOT / "trajectories"
STATE = REPO_ROOT / "data" / "auto_live"
BLOCKING = ("egress", "stop file", "page build")


def _load(name: str):
    path = REPO_ROOT / "tools" / ("%s.py" % name)
    spec = importlib.util.spec_from_file_location(name + "_autolive", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def preflight(force: bool = False) -> Tuple[bool, List[Dict[str, str]]]:
    """The release preflight, used as a gate rather than a report."""
    ready = _load("live_ready")
    entries = ready.checks()
    blocking = [entry for entry in entries
                if entry.get("level") == "FAIL" and entry.get("name") in BLOCKING]
    return (not blocking or force), blocking


def call(slot: int, tool: str, args: Optional[Dict[str, Any]] = None,
         timeout: float = 20.0) -> Dict[str, Any]:
    """One directive against one instance slot, straight through the bridge."""
    body = json.dumps({"id": "autolive-%s-%d" % (tool, slot), "ts": round(time.time(), 3),
                       "directive": tool, "tool": tool, "args": args or {},
                       "source": "auto_live", "target_slot": slot}).encode()
    request = urllib.request.Request(directive_url(), data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 - an unanswered page is a state, not a crash
        return {"slot": slot, "error": str(exc)[:120]}


def probe(slot: int, timeout: float = 12.0) -> Dict[str, Any]:
    """One state.snapshot against one instance slot."""
    data = call(slot, "state.snapshot", timeout=timeout)
    snapshot = data.get("snapshot") if isinstance(data, dict) else None
    if not isinstance(snapshot, dict):
        return {"slot": slot, "error": str(data)[:120]}
    character = snapshot.get("character") if isinstance(snapshot.get("character"), dict) else {}
    return {"slot": slot, "in_world": bool(snapshot.get("in_world")),
            "connected": bool(snapshot.get("connected")),
            "name": character.get("name"), "level": character.get("level"),
            "agent_build": snapshot.get("agent_build"),
            "map_id": (snapshot.get("position") or {}).get("map_id")}


def auto_login(slot: int) -> Dict[str, Any]:
    """Press the client's own login button when it is connected but not in a world.

    The page exposes `session.login`, which emits the tap the game registered on its own guest
    button and reports what it saw. It is not an agent decision: nothing here picks a character,
    a server or a goal — it only gets the client off the login screen, which is the one step that
    used to need a person. `target:"guest"` is the path the operator authorised; a saved Google
    session opens an OAuth popup no script may press.
    """
    data = call(slot, "session.login", {"target": "guest"}, timeout=20.0)
    result = data.get("result") if isinstance(data.get("result"), dict) else data
    payload = result.get("data") if isinstance(result.get("data"), dict) else {}
    return {"slot": slot, "ok": bool(result.get("ok")), "pressed": payload.get("pressed"),
            "emit_error": payload.get("emit_error"),
            "error": str(result.get("error") or "")[:160] or None}


def wait_for_slot(slot: int, deadline_s: float, stop: Path, dismiss=None,
                  login: Optional[Callable[[int], Dict[str, Any]]] = None,
                  every_s: float = 25.0) -> Dict[str, Any]:
    """Wait until the slot reports a world, or the operator's stop file appears.

    Event-driven: the only exit conditions are the page answering `in_world`, a stop file, and the
    clock the caller set — never "we guessed the game needed this long".
    """
    deadline = time.time() + deadline_s
    last: Dict[str, Any] = {}
    tried_login = 0.0
    logins = 0
    while time.time() < deadline:
        if stop.exists():
            return {"ok": False, "reason": "stop file", "last": last}
        if dismiss is not None:
            try:
                dismiss(limit=2)
            except Exception:  # noqa: BLE001 - a dialog that will not close is reported, not fatal
                pass
        last = probe(slot)
        if last.get("in_world"):
            return {"ok": True, "wait_s": round(deadline_s - (deadline - time.time()), 1),
                    "logins": logins, "state": last}
        if login is not None and last.get("connected") and time.time() - tried_login >= every_s:
            tried_login = time.time()
            logins += 1
            print("[auto_live] slot %d is at the login screen; pressing its own play button "
                  "(attempt %d)" % (slot, logins), flush=True)
            try:
                print("[auto_live] session.login -> %s" % json.dumps(login(slot))[:240],
                      flush=True)
            except Exception as exc:  # noqa: BLE001 - a page that will not log in keeps waiting
                print("[auto_live] session.login failed: %s" % str(exc)[:120], file=sys.stderr,
                      flush=True)
        time.sleep(5.0)
    return {"ok": False, "reason": "the slot never reported a world", "logins": logins,
            "last": last}


def run_round(args: argparse.Namespace, goal: List[str], log: Path, stop: Path,
              instance: str) -> Dict[str, Any]:
    """One `agent.autopilot` child, with its output kept next to its log."""
    command = [sys.executable, str(REPO_ROOT / "run_autopilot.py"),
               "--steps", str(args.steps), "--policy", args.policy,
               "--instance", instance, "--log", str(log), "--stop-file", str(stop)]
    if args.minutes:
        command += ["--minutes", str(args.minutes)]
    for text in goal:
        command += ["--goal", text]
    if args.shadow:
        command += ["--command-shadow", "auto"]
    if args.brain != "auto":
        command += ["--brain", args.brain]
    started = time.time()
    transcript = log.with_suffix(".out.txt")
    with transcript.open("w", encoding="utf-8") as handle:
        proc = subprocess.run(command, cwd=str(REPO_ROOT), stdout=handle,
                              stderr=subprocess.STDOUT, text=True, timeout=args.hard_minutes * 60,
                              env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return {"command": command[1:], "exit": proc.returncode,
            "seconds": round(time.time() - started, 1), "log": str(log)}


def _is_step_row(row: Dict[str, Any]) -> bool:
    """What an executed step looks like in the loop's own log.

    Measured against a live round on 2026-10-27 rather than assumed: the step rows
    carry an `index` and a `tool` and their `kind` is **null** -- `kind` is set only
    on the other rows (decision / wait / identity). The first version of this filter
    asked for `kind == "step"`, a value no row has ever had, so every manifest row
    this tool wrote reported `rows: 0` about a round that had taken steps.
    """
    return row.get("index") is not None and bool(row.get("tool"))


def _map_of(value: Any):
    """The map a row observed, from the reduced vector the loop actually stores."""
    if not isinstance(value, dict):
        return None
    if "map_id" in value:
        return value.get("map_id")
    position = value.get("position")
    return position.get("map_id") if isinstance(position, dict) else None


def summarise(log: Path) -> Dict[str, Any]:
    """The measured outcome of a round, from the rows themselves."""
    if not log.is_file():
        return {"rows": 0}
    steps, tools, ok, moved = 0, {}, 0, 0
    with log.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or not _is_step_row(row):
                continue    # a torn write can put anything on the last line
            steps += 1
            tools[row.get("tool")] = tools.get(row.get("tool"), 0) + 1
            ok += 1 if row.get("ok") else 0
            moved += 1 if _map_of(row.get("obs_before")) != _map_of(row.get("obs_after")) else 0
    return {"rows": steps, "ok": ok, "map_changed": moved,
            "tools": dict(sorted(tools.items(), key=lambda kv: -kv[1])[:8])}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slot", type=int, default=1, help="instance slot to drive (1-based)")
    ap.add_argument("--goal", action="append", default=None)
    ap.add_argument("--steps", type=int, default=150)
    ap.add_argument("--minutes", type=float, default=0.0)
    ap.add_argument("--hard-minutes", type=float, default=45.0,
                    help="kill the child if it exceeds this; a hung page is not a run")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--policy", choices=("rule", "encoder", "planner"), default="planner")
    ap.add_argument("--brain", choices=("auto", "off"), default="auto")
    ap.add_argument("--shadow", action="store_true", help="log rule-vs-model per goal")
    ap.add_argument("--login-wait", type=float, default=420.0)
    ap.add_argument("--force", action="store_true",
                    help="run even when the preflight says the path to Ankama is dead")
    ap.add_argument("--stop-file", type=Path, default=STATE / "stop")
    args = ap.parse_args(argv)

    stamp = time.strftime("%Y%m%dT%H%M%S")
    args.outdir = STATE / stamp
    args.outdir.mkdir(parents=True, exist_ok=True)
    goals = args.goal or ["do all the quests"]
    instance = "dofus-%02d" % args.slot

    ok, blocking = preflight(force=args.force)
    if not ok:
        for entry in blocking:
            print("[auto_live] %s: %s" % (entry["name"], entry["detail"]), file=sys.stderr)
        print("[auto_live] nothing was launched. Fix the path or pass --force.", file=sys.stderr)
        return 2
    host = _load("host_control")
    if not host.host_running():
        print("[auto_live] starting the host", flush=True)
        if not host.start_host():
            print("[auto_live] the host did not come up", file=sys.stderr)
            return 2
    if (client_count() or 0) <= 0:
        print("[auto_live] no client window; asking the panel to launch instances", flush=True)
        result = host.launch_all(force=args.force)
        print("[auto_live] launch_all -> %s" % json.dumps(result)[:300], flush=True)

    manifest: List[Dict[str, Any]] = []
    for round_index in range(max(1, args.rounds)):
        log = args.outdir / ("auto_%d_%s.jsonl" % (round_index, stamp))
        if args.stop_file.exists():
            print("[auto_live] stop file present, ending", file=sys.stderr)
            break
        ready = wait_for_slot(args.slot, args.login_wait, args.stop_file,
                              dismiss=getattr(host, "dismiss_alerts", None),
                              login=auto_login)
        row: Dict[str, Any] = {"round": round_index, "slot": args.slot, "log": str(log),
                               "ready": ready}
        if not ready.get("ok"):
            row["outcome"] = "no world"
            manifest.append(row)
            print("[auto_live] round %d: %s (%s)" % (round_index, ready.get("reason"),
                                                     json.dumps(ready.get("last") or {})),
                  file=sys.stderr)
            break
        print("[auto_live] round %d on slot %d as %s: %s"
              % (round_index, args.slot, ready["state"].get("name") or instance,
                 json.dumps(ready["state"])), flush=True)
        try:
            run = run_round(args, goals, log, args.stop_file, instance)
        except subprocess.TimeoutExpired:
            run = {"exit": "timeout", "log": str(log)}
        row["run"] = run
        row["summary"] = summarise(log)
        row["outcome"] = "ran"
        manifest.append(row)
        print("[auto_live] round %d: %s" % (round_index, json.dumps(row["summary"])), flush=True)
        if (client_count() or 0) <= 0:
            print("[auto_live] the client window is gone; relaunching", flush=True)
            host.launch_all(force=True)

    # the transitions rebuild is what makes a run usable as training data
    transitions = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "build_live_transitions.py"),
         "--out", str(args.outdir / "transitions")]
        + [str(path) for path in sorted(args.outdir.glob("auto_*.jsonl"))],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=900)
    report = {"started": stamp, "slot": args.slot, "goals": goals, "rounds": manifest,
              "transitions_rc": transitions.returncode,
              "transitions_stdout": (transitions.stdout or "")[-800:]}
    (args.outdir / "manifest.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("[auto_live] wrote %s" % (args.outdir / "manifest.json"))
    return 0 if any(row.get("outcome") == "ran" for row in manifest) else 1


if __name__ == "__main__":
    sys.exit(main())
