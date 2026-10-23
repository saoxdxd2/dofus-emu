"""Check the live client, one primitive at a time, and say which ones work.

The agent has never completed a quest. That is not a planning problem: three of the
four primitives a quest needs are unverified, and until each one is checked against the
real game, every model result is unfalsifiable. This script checks them in order and
stops being clever about it.

    python ai/verify_live.py

Each line is PASS, FAIL or SKIP with the reason. SKIP means the check could not be run
(no NPC on this map, no window open) — never that it worked.

Order matters: a failure early makes the later ones meaningless, so the script stops
after a failed prerequisite.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.bridge import bridge_port, reachable, local_opener  # noqa: E402

RESULTS: List[Tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    RESULTS.append((name, status, detail))
    print("  [%-4s] %-38s %s" % (status, name, detail))


def directive(tool: str, args: Optional[Dict[str, Any]] = None,
              timeout: float = 15.0) -> Dict[str, Any]:
    """One tool call. Returns the client's result object, or {'ok': False, 'error': ...}."""
    import urllib.request
    body = json.dumps({"id": "verify-%d" % int(time.time() * 1000), "ts": 0,
                       "tool": tool, "args": args or {}, "source": "verify"}).encode()
    req = urllib.request.Request("http://127.0.0.1:%d/directive" % bridge_port(),
                                 data=body, headers={"Content-Type": "application/json"})
    try:
        outer = json.loads(local_opener().open(req, timeout=timeout).read().decode())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "transport: %s" % exc}
    return outer.get("result") or outer


def snapshot() -> Dict[str, Any]:
    r = directive("state.snapshot")
    if not r.get("ok"):
        return {}
    data = r.get("data") or {}
    return data.get("snapshot") or data


def health() -> Dict[str, Any]:
    import urllib.request
    try:
        return json.loads(local_opener().open(
            "http://127.0.0.1:%d/agent/health" % bridge_port(), timeout=4).read().decode())
    except Exception:  # noqa: BLE001
        return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quest", type=int, default=584,
                    help="quest id to try talking about")
    ap.add_argument("--skip-move", action="store_true")
    args = ap.parse_args(argv)

    print("== live client ==")
    print("  bridge port: %d" % bridge_port())

    # 1. bridge
    if not reachable():
        record("bridge answers", "FAIL", "nothing answering on %d - is the app running?"
               % bridge_port())
        return _summary()
    record("bridge answers", "PASS", "port %d" % bridge_port())

    # 2. an instance
    h = health()
    count = h.get("instances")
    if not count:
        record("a client window is open", "FAIL",
               "instances=%s - launch an instance in DofusFarm" % count)
        return _summary()
    record("a client window is open", "PASS", "instances=%s" % count)

    # 3. the script version, so a stale page is visible
    v = directive("debug.version")
    if v.get("ok"):
        record("agent script is current", "PASS", str((v.get("data") or {}).get("build")))
    else:
        record("agent script is current", "FAIL",
               "debug.version missing - the page is running an older script; reload it "
               "with debug.reload (or restart the app)")

    # 4. in-world
    snap = snapshot()
    if not snap:
        record("snapshot returns state", "FAIL", "no data")
        return _summary()
    record("snapshot returns state", "PASS", "%d fields" % len(snap))
    pos = snap.get("position") or {}
    char = snap.get("character") or {}
    if snap.get("in_world") and pos.get("map_id"):
        record("character is in the world", "PASS",
               "%s level %s on map %s cell %s" % (char.get("name"), char.get("level"),
                                                  pos.get("map_id"), pos.get("cell")))
    else:
        record("character is in the world", "FAIL",
               "map_id=%s - the client is at character selection" % pos.get("map_id"))
        return _summary()

    # 4b. health. A dead character can walk but cannot talk, so a FAIL here explains
    # every later talk failure rather than leaving it a mystery.
    hp = snap.get("hp") or {}
    life, top = hp.get("life"), hp.get("max")
    alive = isinstance(life, (int, float)) and life > 0
    if alive:
        record("the character has health", "PASS", "%s/%s" % (life, top))
    elif life == 0 and top == 0:
        record("the character has health", "FAIL",
               "0/0 - either dead, or the stats are not loaded in this client state; "
               "revive before expecting talk to work")
    else:
        record("the character has health", "SKIP", "life=%s max=%s" % (life, top))

    if not alive:
        # A dead character can walk but cannot talk, so attempting the rest would
        # produce three misleading failures instead of one true one.
        record("talk opens a dialog", "SKIP", "not attempted: no health")
        record("a dialog reply is accepted", "SKIP", "not attempted: no health")
        record("the character moves", "SKIP", "not attempted: no health")
        for tool, label in (("debug.npcs", "NPC actors are visible"),
                            ("debug.groups", "monster groups are visible")):
            r = directive(tool)
            record(label, "PASS" if r.get("ok") else "FAIL",
                   str((r.get("data") or {}).get("count")) if r.get("ok")
                   else str(r.get("error")))
        return _summary()

    # 5. talk: open a dialog with an NPC on this map
    before_dialog = bool(snap.get("in_dialog"))
    r = directive("quest.advance", {"quest_id": args.quest})
    data = r.get("data") or {}
    time.sleep(1.2)
    after = snapshot()
    opened = (not before_dialog) and bool(after.get("in_dialog"))
    if r.get("ok") and opened:
        record("talk opens a dialog", "PASS",
               "npc template %s (actor %s)" % (data.get("npc_template"),
                                               data.get("npc_contextual_id")))
    elif r.get("ok") and before_dialog:
        record("talk opens a dialog", "SKIP", "a dialog was already open")
    else:
        record("talk opens a dialog", "FAIL", str(r.get("error") or "no dialog appeared"))

    # 6. answer it
    if after.get("in_dialog"):
        r2 = directive("quest.advance", {"quest_id": args.quest, "reply": 0})
        time.sleep(1.0)
        closed = not bool(snapshot().get("in_dialog"))
        record("a dialog reply is accepted", "PASS" if closed else "FAIL",
               str(r2.get("error") or ("replied" if closed else "still open")))
    else:
        record("a dialog reply is accepted", "SKIP", "no dialog open to answer")

    # 6b. where the talk chain stopped, in the client's own words.
    #
    # walk -> talk -> dialog -> reply is four steps across two messages, and every way it
    # breaks looks identical from here: "nothing advanced". The client records which stage
    # it reached, so this prints a reading instead of a suspicion.
    talk = directive("debug.talk_state")
    talk_data = (talk.get("data") or {}) if talk.get("ok") else {}
    last = talk_data.get("last") or {}
    if not talk.get("ok"):
        record("the talk chain reports its stage", "FAIL", str(talk.get("error")))
    else:
        stage = last.get("stage") or "not attempted"
        detail = []
        if last.get("npc_template"):
            detail.append("npc %s" % last["npc_template"])
        if last.get("replies") is not None:
            detail.append("%s replies" % last["replies"])
        if last.get("error"):
            detail.append(str(last["error"])[:60])
        record("the talk chain reports its stage", "PASS",
               "%s%s  (in_dialog=%s, reply_handler=%s, reply_ids=%s)"
               % (stage, (" " + ", ".join(detail)) if detail else "",
                  talk_data.get("in_dialog"), talk_data.get("reply_handler"),
                  (talk_data.get("reply_ids") or [])[:4] or None))

    # 7. movement: walk to the NPC's cell if one is on this map
    if args.skip_move:
        record("the character moves", "SKIP", "--skip-move")
    else:
        cell_before = (snapshot().get("position") or {}).get("cell")
        listed = directive("debug.npcs")
        npcs = (listed.get("data") or {}).get("npcs") or []
        if npcs:
            r3 = directive("nav.goto", {"npc_id": npcs[0].get("templateId")})
            time.sleep(2.0)
            cell_after = (snapshot().get("position") or {}).get("cell")
            moved = cell_before != cell_after
            record("the character moves", "PASS" if moved else "FAIL",
                   "cell %s -> %s (%s)" % (cell_before, cell_after,
                                           r3.get("error") or "ok"))
        else:
            record("the character moves", "SKIP",
                   "no NPC on this map to walk to; needs a known walkable cell")

    # 7b. what the client knows about this map, which is what cross-map routing needs
    m = directive("debug.map")
    if m.get("ok"):
        data = m.get("data") or {}
        print("  map info: %s" % json.dumps({k: data.get(k) for k in
                                             ("map_id", "candidates", "exit_cells")})[:400])
    else:
        print("  map info: unavailable (%s)" % m.get("error"))

    # 8. combat plumbing, read-only: can we see monster groups and NPCs at all?
    for tool, label in (("debug.npcs", "NPC actors are visible"),
                        ("debug.groups", "monster groups are visible")):
        r = directive(tool)
        data = r.get("data") or {}
        if r.get("ok"):
            record(label, "PASS", "%s found" % data.get("count"))
        else:
            record(label, "FAIL", str(r.get("error") or "no data"))

    return _summary()


def _summary() -> int:
    print("\n== verdict ==")
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for _, status, _ in RESULTS:
        counts[status] = counts.get(status, 0) + 1
    for name, status, detail in RESULTS:
        if status != "PASS":
            print("  %-4s %s: %s" % (status, name, detail))
    print("  %d pass, %d fail, %d skip" % (counts["PASS"], counts["FAIL"], counts["SKIP"]))
    if counts["FAIL"]:
        print("\n  The first FAIL is the one to fix; later checks depend on it.")
    return 0 if counts["FAIL"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
