"""Press the guest login once, when the page's own lockout has expired.

A refused guest login arms a cooldown of 10 min, then 60 min, then 3 h; the page
refuses every press inside it and the autopilot now ends its run with the reason
instead of parking. What was still missing was the other half of that story: once
the cooldown is over, somebody has to press again. This tool waits for the stated
expiry (it never shortens it), spends exactly one guarded press, and reports the
answer the page and the client give back.

It is an operator tool, not product behaviour: the run it wakes is the same one
`POST /brain/start` would start, and a banned slot is never pressed at all.
"""
import argparse
import json
import os
import sys
import time
import urllib.request


def state_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "DofusFarm")


def bridge_port() -> int:
    path = os.path.join(state_dir(), "bridge_port.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def call(port: int, payload: dict, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(
        "http://127.0.0.1:%d/directive" % port,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def snapshot(port: int, instance: str) -> dict:
    out = call(port, {"id": "wake-%d" % int(time.time() * 1000), "ts": 0,
                      "directive": "STATE_SNAPSHOT", "tool": "state.snapshot",
                      "args": {}, "source": "wake_login",
                      "target_instance": instance})
    return ((out or {}).get("result") or {}).get("data") or {}


def login_block(snap: dict) -> dict:
    block = snap.get("login")
    return block if isinstance(block, dict) else {}


def cooldown_s(snap: dict) -> float:
    cool = snap.get("login_cooldown")
    if not isinstance(cool, dict):
        return 0.0
    try:
        return max(0.0, float(cool.get("remaining_s") or 0))
    except (TypeError, ValueError):
        return 0.0


def in_world(snap: dict) -> bool:
    pos = snap.get("position") if isinstance(snap.get("position"), dict) else {}
    return bool(snap.get("in_world")) and pos.get("map_id") not in (None, 0)


def wait_for_slot(port: int, instance: str, max_wait_s: float) -> dict:
    started = time.time()
    while True:
        snap = snapshot(port, instance)
        if not snap:
            print("[wake] the bridge answered nothing; is the client running?")
            return {}
        if in_world(snap):
            print("[wake] already in the world on map %s"
                  % (snap.get("position") or {}).get("map_id"))
            return snap
        if login_block(snap).get("banned"):
            print("[wake] the login screen reports a permanent ban (%s); not pressing "
                  "-- reset this slot for a fresh guest"
                  % str(login_block(snap).get("text") or "no text")[:120])
            return snap
        left = cooldown_s(snap)
        if left <= 0:
            return snap
        waited = time.time() - started
        if waited + min(left, 60.0) > max_wait_s:
            print("[wake] %.0f s left on the cooldown and --max-wait %.0f s; stopping"
                  % (left, max_wait_s))
            return {}
        nap = min(60.0, left + 2.0)
        print("[wake] cooldown %s: %.0f s left; waiting %.0f s"
              % (str((snap.get("login_cooldown") or {}).get("reason"))[:60], left, nap))
        time.sleep(nap)


def press_once(port: int, instance: str) -> dict:
    out = call(port, {"id": "press-%d" % int(time.time() * 1000), "ts": 0,
                      "directive": "SESSION_LOGIN", "tool": "session.login",
                      "args": {"target": "guest"}, "source": "wake_login",
                      "target_instance": instance})
    result = (out or {}).get("result") or {}
    print("[wake] login press -> ok=%s %s"
          % (result.get("ok"), str(result.get("error") or "")[:160]))
    return result


def start_loop(port: int, instance: str, goal: str) -> bool:
    """Hand the recovered world to the brain daemon, as /brain/start would."""
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8895/brain/start",
            data=json.dumps({"instances": [instance], "goals": [goal],
                             "policy": "auto"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - a dead daemon is a report, not a crash
        print("[wake] the brain daemon did not accept /brain/start: %s" % exc)
        return False
    rows = body.get("instances") if isinstance(body, dict) else None
    state = (rows or [{}])[0].get("state") if rows else None
    print("[wake] brain start -> ok=%s state=%s goal=%s"
          % (bool(body.get("ok")) if isinstance(body, dict) else False, state, goal))
    return bool(isinstance(body, dict) and body.get("ok"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--instance", default="dofus-02")
    ap.add_argument("--max-wait", type=float, default=3600.0,
                    help="seconds this tool may wait for the cooldown to end")
    ap.add_argument("--settle", type=float, default=20.0,
                    help="seconds to watch the client after the press")
    ap.add_argument("--start-loop", action="store_true",
                    help="hand the recovered world to the brain daemon")
    ap.add_argument("--goal", default="do all the quests",
                    help="goal passed to /brain/start with --start-loop")
    args = ap.parse_args(argv)

    port = bridge_port()
    if not port:
        print("[wake] no bridge_port.txt under %s" % state_dir())
        return 2
    snap = wait_for_slot(port, args.instance, args.max_wait)
    if not snap:
        return 1
    if login_block(snap).get("banned"):
        return 0
    if in_world(snap):
        if args.start_loop:
            start_loop(port, args.instance, args.goal)
        return 0
    press_once(port, args.instance)
    deadline = time.time() + max(0.0, args.settle)
    while time.time() < deadline:
        time.sleep(2.0)
        snap = snapshot(port, args.instance)
        if in_world(snap):
            print("[wake] in the world on map %s"
                  % (snap.get("position") or {}).get("map_id"))
            if args.start_loop:
                start_loop(port, args.instance, args.goal)
            return 0
        if login_block(snap).get("banned"):
            print("[wake] the press answered with a ban; reset this slot")
            return 1
        left = cooldown_s(snap)
        if left > 0:
            print("[wake] the press was refused; %.0f s of cooldown left (%s)"
                  % (left, str((snap.get("login_cooldown") or {}).get("reason"))[:80]))
            return 1
    print("[wake] the press was sent; the client has not reached the world yet")
    return 1


if __name__ == "__main__":
    sys.exit(main())
