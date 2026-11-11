"""Why does farm.start return ok=True while no fight starts?

Reads the page's own view of the map: the groups it can see, whether the farm
loop is enabled, and what it last decided. Read-only.
"""

import json
import sys
import time
import urllib.request

sys.path.insert(0, r"C:\Users\sao\Documents\dofus-emu\dofus-network")
from agent.bridge import directive_url, local_headers  # noqa: E402


def call(slot, tool, args=None, timeout=20.0):
    body = json.dumps({"id": "probe-%s-%d" % (tool, slot), "ts": round(time.time(), 3),
                       "directive": tool, "tool": tool, "args": args or {},
                       "source": "probe", "target_slot": slot}).encode()
    headers = {"Content-Type": "application/json"}
    headers.update(local_headers())
    req = urllib.request.Request(directive_url(), data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def payload(resp):
    """Directive responses wrap the tool result in .result.data for local tools."""
    if not isinstance(resp, dict):
        return {}
    r = resp.get("result")
    if isinstance(r, dict) and isinstance(r.get("data"), dict):
        return r["data"]
    if isinstance(resp.get("data"), dict):
        return resp["data"]
    return resp


def main():
    slot = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    snap = payload(call(slot, "state.snapshot"))
    pos = snap.get("position") or {}
    print("in_world   =", snap.get("in_world"))
    print("in_fight   =", snap.get("in_fight"))
    print("auto_fight =", snap.get("auto_fight"))
    print("map_id     =", pos.get("map_id"))
    print("cell       =", pos.get("cell"))
    print("farm_state =", json.dumps(snap.get("farm_state"))[:300])
    print("farm_sess  =", json.dumps(snap.get("farm_session"))[:300])

    groups = payload(call(slot, "debug.groups"))
    gl = groups.get("groups") if isinstance(groups, dict) else None
    print()
    print("debug.groups count =", groups.get("count"), "err =", groups.get("error"))
    if isinstance(gl, list):
        for g in gl[:20]:
            if not isinstance(g, dict):
                continue
            print("  ", json.dumps({k: g.get(k) for k in
                                    ("cell", "size", "monsters", "monster_ids",
                                     "distance", "reachable", "id")})[:220])

    for tool in ("farm.status", "fight.status"):
        r = payload(call(slot, tool))
        print()
        print(tool, "->", json.dumps(r)[:600])


if __name__ == "__main__":
    main()
