"""One bounded live session -> entity-pointer corpus -> training gates.

This is the orchestration layer, not a login tool. It refuses to start unless the
client bridge is reachable and the egress preflight passes; the user starts the
app, tunnel and clients, then runs:

    python tools/collect_live_data.py --instances dofus-01,dofus-02 --steps 200

It starts/reuses the single brain daemon on :8895, runs the selected instances,
stops them, rebuilds `data/entity_pointer`, and prints `combat.grid_probe` so the
next training run knows whether actionable boards exist.
"""

from __future__ import annotations

import argparse
import json
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


def _preflight() -> bool:
    print("== live_ready ==")
    code = _run([sys.executable, "tools/live_ready.py"])
    if code != 0:
        print("live_ready refused the run; fix the FAIL lines first")
        return False
    return True


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


def _wait(status: Dict[str, Any], timeout_s: float) -> List[Dict[str, Any]]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        current = _get("/brain/status") or {}
        rows = current.get("instances") or []
        active = [row for row in rows if row.get("alive")]
        if not active:
            return rows
        time.sleep(2.0)
    return (_get("/brain/status") or {}).get("instances") or []


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instances", default="dofus-01",
                        help="comma-separated instance ids")
    parser.add_argument("--goal", default="do all the quests")
    parser.add_argument("--steps", type=int, default=200)
    parser.add_argument("--minutes", type=float, default=20.0)
    parser.add_argument("--timeout", type=float, default=1800.0,
                        help="hard socket timeout for the orchestration wait")
    parser.add_argument("--command-shadow", choices=("auto", "off"), default="auto")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    instances = [item.strip() for item in args.instances.split(",") if item.strip()]
    request = {
        "instances": instances,
        "goals": [args.goal],
        "steps": args.steps,
        "minutes": args.minutes,
        "policy": "planner",
        "command_shadow": args.command_shadow,
        "brain": "auto",
    }
    print(json.dumps(request, indent=1))
    if args.dry_run:
        print("dry run: not starting the daemon or the loop")
        return 0
    if not _preflight():
        return 2
    if not _ensure_brain():
        return 2
    started = _post("/brain/start", request)
    if not started or not started.get("ok"):
        print("brain start refused:", started)
        return 2
    rows = _wait(started, min(args.timeout, max(60.0, args.minutes * 60.0 + 60.0)))
    _post("/brain/stop", {"all": True, "why": "collect_live_data finished"})
    for row in rows:
        print(json.dumps({key: row.get(key) for key in
                          ("instance", "state", "step_total", "waits",
                           "recoveries", "error")}, sort_keys=True))
    print("== entity-pointer dataset ==")
    _run([sys.executable, "tools/build_entity_pointer_dataset.py"])
    print("== combat grid gate ==")
    _run([sys.executable, "-m", "combat.grid_probe"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
