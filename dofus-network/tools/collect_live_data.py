"""Monitor the live fleet, or explicitly start/reuse a selected cohort.

With no --instances, continuously observe /brain/status without changing anything.
With --instances, preflight and ensure the daemon, then start/reuse only those
instances. Steps, minutes and the monitor timeout default to zero (unlimited).
This tool never stops loops: manual Stop and daemon budgets are authoritative.

An optional rebuild requires a successful, finitely budgeted cohort:

    python tools/collect_live_data.py --instances dofus-01,dofus-02 --steps 200 --rebuild

--timeout limits only monitoring; expiry or Ctrl-C leaves all loops untouched.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
BRAIN = "http://127.0.0.1:8895"


def _local_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(path: str, timeout: float = 1.5) -> Optional[Dict[str, Any]]:
    try:
        with _local_opener().open(BRAIN + path, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _post(path: str, body: Dict[str, Any], timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        BRAIN + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _local_opener().open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _run(cmd: List[str], check: bool = False) -> int:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=str(REPO_ROOT), check=check).returncode


def _preflight() -> int:
    print("== live_ready ==")
    code = _run([sys.executable, "tools/live_ready.py"])
    if code != 0:
        print("live_ready refused the run; fix the FAIL lines first")
    return code


def _ensure_brain() -> bool:
    if _get("/brain/health") is not None:
        print("brain daemon already answering")
        return True
    script = REPO_ROOT / "run_brain.py"
    if not script.is_file():
        print("missing", script)
        return False
    print("starting brain daemon")
    subprocess.Popen(
        [sys.executable, str(script), "--port", "8895", "--brain",
         "--brain-log-dir", str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm"),
         "--recorder-dir", str(REPO_ROOT / "trajectories")],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        if _get("/brain/health") is not None:
            return True
        time.sleep(0.5)
    print("brain daemon did not answer /brain/health")
    return False


class IncompleteRun(RuntimeError):
    """The selected sessions did not complete successfully."""


def _rows(status: Any) -> Optional[List[Dict[str, Any]]]:
    """Reject missing/ambiguous status rather than interpreting it as idle."""
    if not isinstance(status, dict) or status.get("ok") is not True:
        return None
    rows = status.get("instances")
    if not isinstance(rows, list):
        return None
    names = set()
    for row in rows:
        if not isinstance(row, dict):
            return None
        name = row.get("instance")
        if not isinstance(name, str) or not name.strip() or name in names:
            return None
        names.add(name)
    return rows


def _valid_session(row: Dict[str, Any]) -> bool:
    started_at = row.get("started_at")
    return (
        type(started_at) in (int, float) and math.isfinite(started_at)
        and type(row.get("alive")) is bool
        and row.get("state") in (
            "starting", "running", "stopping", "finished", "stopped", "exited", "error")
    )


def _check_failures(rows: List[Dict[str, Any]]) -> None:
    for row in rows:
        if row.get("error") or row["state"] in ("error", "stopped", "exited"):
            raise IncompleteRun("session did not finish successfully: " + json.dumps(row))


def _report(rows: List[Dict[str, Any]]) -> None:
    # One complete snapshot per poll also makes removals (and an empty fleet) visible.
    print(json.dumps({"instances": [
        {key: row.get(key) for key in
         ("instance", "started_at", "alive", "state", "step_total", "waits",
          "recoveries", "error")} for row in rows]}, sort_keys=True), flush=True)


def _wait(status: Optional[Dict[str, Any]], timeout_s: float) -> List[Dict[str, Any]]:
    """None selects read-only fleet monitoring; otherwise pin the returned sessions."""
    cohort = None
    if status is not None:
        initial = _rows(status)
        if not initial or not all(_valid_session(row) for row in initial):
            raise IncompleteRun("invalid start response")
        _check_failures(initial)
        cohort = {row["instance"]: row["started_at"] for row in initial}
    deadline = time.monotonic() + timeout_s if timeout_s > 0 else None
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("monitor timeout: incomplete; loops left untouched")
        rows = _rows(_get("/brain/status"))
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("monitor timeout: incomplete; loops left untouched")
        if rows is not None and cohort is not None:
            by_name = {row["instance"]: row for row in rows}
            rows = [by_name[name] for name in cohort if name in by_name]
            if len(rows) != len(cohort):
                rows = None
        if rows is None or not all(_valid_session(row) for row in rows):
            print("status unavailable, malformed, or missing selected instances; still monitoring")
        else:
            if cohort is not None:
                if any(row["started_at"] != cohort[row["instance"]] for row in rows):
                    raise IncompleteRun("selected session was replaced; not following its replacement")
                _check_failures(rows)
            _report(rows)
            if cohort is not None and all(
                    not row["alive"] and row["state"] == "finished" for row in rows):
                return rows
        delay = 2.0 if deadline is None else min(2.0, max(0.0, deadline - time.monotonic()))
        time.sleep(delay)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instances", default="",
                        help="comma-separated instance ids; empty means read-only fleet monitoring")
    parser.add_argument("--goal", default="do all the quests")
    parser.add_argument("--steps", type=int, default=0,
                        help="daemon step budget, 0 means unlimited")
    parser.add_argument("--minutes", type=float, default=0.0,
                        help="daemon time budget, 0 means unlimited")
    parser.add_argument("--timeout", type=float, default=0.0,
                        help="monitor-only budget in seconds, 0 means unlimited; never stops loops")
    parser.add_argument("--command-shadow", choices=("auto", "off"), default="auto")
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild/probe only after a successfully finished, finitely budgeted cohort")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if (args.steps < 0 or not math.isfinite(args.minutes) or args.minutes < 0
            or not math.isfinite(args.timeout) or args.timeout < 0):
        parser.error("budgets must be finite and nonnegative (0 means unlimited)")
    instances = list(dict.fromkeys(item.strip() for item in args.instances.split(",") if item.strip()))
    request = {
        "instances": instances,
        "goals": [args.goal],
        "steps": args.steps,
        "minutes": args.minutes,
        "policy": "planner",
        "command_shadow": args.command_shadow,
        "brain": "auto",
    }
    rebuild = bool(args.rebuild and instances and (args.steps > 0 or args.minutes > 0))
    print(json.dumps({
        "mode": "cohort" if instances else "monitor",
        "monitor_timeout_seconds": args.timeout,
        "daemon_budgets": {"steps": args.steps if instances else 0,
                           "minutes": args.minutes if instances else 0.0},
        "rebuild": rebuild,
        "request": request if instances else None,
    }, indent=1))
    if args.rebuild and not rebuild:
        print("rebuild disabled: requires explicit instances and a positive daemon budget")
    if args.dry_run:
        print("dry run: no HTTP, daemon, preflight, loops, or dataset changes")
        return 0
    try:
        if not instances:
            _wait(None, args.timeout)
            return 0  # Fleet monitoring normally ends only on timeout or interruption.
        code = _preflight()
        if code != 0:
            return code
        if not _ensure_brain():
            return 2
        started = _post("/brain/start", request)
        rows = _rows(started)
        if not rows or {row["instance"] for row in rows} != set(instances):
            raise IncompleteRun("brain start refused or returned an incomplete cohort: " + str(started))
        _wait(started, args.timeout)
        if rebuild:
            print("== entity-pointer dataset ==")
            code = _run([sys.executable, "tools/build_entity_pointer_dataset.py"])
            if code != 0:
                return code
            print("== combat grid gate ==")
            return _run([sys.executable, "-m", "combat.grid_probe"])
        return 0
    except TimeoutError as exc:
        print(exc)
        return 124
    except KeyboardInterrupt:
        print("monitor interrupted: incomplete; loops left untouched")
        return 130
    except (IncompleteRun, OSError) as exc:
        print(exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
