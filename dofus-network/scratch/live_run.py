"""Start one live goal on the brain daemon and report what it did.

The daemon owns the instance, so a run is `POST /brain/start` and then a read of the
autopilot log the instance writes. This script exists so the loop is one command:

    python scratch/live_run.py 40
    python scratch/live_run.py 40 --tail 30
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.bridge import local_headers, local_opener  # noqa: E402

BRAIN = "http://127.0.0.1:8895"
LOG = Path.home() / "AppData" / "Local" / "DofusFarm" / "autopilot.dofus-01.jsonl"


def post(path, body):
    headers = dict(local_headers())
    headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BRAIN + path, data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
    with local_opener().open(request, timeout=20) as response:
        return json.loads(response.read())


def get(path):
    request = urllib.request.Request(BRAIN + path, headers=dict(local_headers()))
    with local_opener().open(request, timeout=20) as response:
        return json.loads(response.read())


def main(argv):
    steps = int(argv[0]) if argv else 40
    tail = int(argv[argv.index("--tail") + 1]) if "--tail" in argv else 12
    health = get("/brain/health")
    print("health:", json.dumps({k: health.get(k) for k in
                                 ("status", "code_stale", "loaded_s", "instances")})[:200])
    before = LOG.stat().st_size if LOG.exists() else 0
    started = post("/brain/start", {"instances": ["dofus-01"], "goal": "do all the quests",
                                    "steps": steps})
    print("start:", json.dumps(started)[:200])
    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(10)
        status = get("/brain/status")
        live = [i for i in status.get("instances", []) if i.get("alive")]
        print("  %ds alive=%d" % (int(time.time() - (deadline - 300)), len(live)))
        if not live and status.get("instances"):
            break
    if not LOG.exists():
        return 1
    with open(LOG, "rb") as handle:
        handle.seek(before)
        blob = handle.read().decode("utf-8", "replace")
    rows = []
    for line in blob.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    steps_rows = [r for r in rows if "kind" not in r and "index" in r]
    for row in steps_rows:
        print("%-3s %-20s %-8s %-7s %s" % (
            row.get("index"), (row.get("tool") or "")[:20], row.get("verdict"),
            row.get("source"), (row.get("note") or "")[:100]))
    end = [r for r in rows if r.get("kind") == "session_end"]
    if end:
        print("END", json.dumps({k: end[-1].get(k) for k in
                                 ("reason", "steps", "waits", "verdicts", "retired")})[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
