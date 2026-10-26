"""Stream the live logs and aggregate what session_report does not: why runs
ended, what the recovery layer was repairing, and how the sessions died.

Read-only. Usage: python tools/recovery_report.py [jsonl ...]
"""
from __future__ import annotations
import json, os, sys, time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = Path(os.environ.get("LOCALAPPDATA", "")) / "DofusFarm"
DEFAULTS = [REPO / "trajectories" / "default.jsonl", APP / "autopilot.jsonl"]


def rows(paths):
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if isinstance(r, dict):
                    r["_file"] = p.name
                    yield r


def main(argv):
    paths = argv or [str(x) for x in DEFAULTS if x.exists()]
    verdicts, actions, causes, dr, tools_fail, err_top = (Counter(), Counter(), Counter(),
                                                          Counter(), Counter(), Counter())
    steps = decs = 0
    inter_events = inter_rows = 0
    sessions = set()
    span = {}
    for r in rows(paths):
        if r.get("kind") == "intervention":
            inter_rows += 1
            inter_events += int(r.get("events") or 0)
        ts = r.get("at") or r.get("ts")
        if isinstance(ts, (int, float)):
            sid = r.get("session") or r["_file"]
            sessions.add(sid)
            a, b = span.get(sid, (ts, ts))
            span[sid] = (min(a, ts), max(b, ts))
        if r.get("kind") == "decision":
            decs += 1
            actions[str(r.get("action"))] += 1
            causes[str(r.get("cause"))] += 1
        if r.get("verdict"):
            verdicts[str(r["verdict"])] += 1
        obs = r.get("obs") or r.get("observation") or {}
        if isinstance(obs, dict):
            d = obs.get("last_disconnect")
            if isinstance(d, dict) and d.get("reason"):
                dr[str(d["reason"])[:60]] += 1
            elif obs.get("in_world") is False:
                dr["(in_world:false, no reason given)"] += 1
        if r.get("ok") is False and r.get("tool"):
            tools_fail[str(r["tool"])] += 1
            e = str(r.get("error") or r.get("note") or "")[:80]
            if e:
                err_top[e] += 1
    total_min = sum((b - a) / 60.0 for a, b in span.values())
    print("files:", ", ".join(Path(p).name for p in paths))
    print("sessions %d, span %.1f min total, longest %.1f min"
          % (len(span), total_min, max(((b - a) / 60.0 for a, b in span.values()), default=0)))
    print("manual interventions: %d events in %d row(s)  "
          "(0 with no rows means the instrument existed; rows absent means it did not)"
          % (inter_events, inter_rows))
    print("\nverdicts:      ", dict(verdicts.most_common()))
    print("decision acts: ", dict(actions.most_common()))
    print("causes:        ", dict(causes.most_common(10)))
    print("\ndisconnect reasons seen in observations:")
    for k, v in dr.most_common(12):
        print("   %6d  %s" % (v, k))
    print("\nfailed steps by tool:", dict(tools_fail.most_common(10)))
    print("\ntop failure texts:")
    for k, v in err_top.most_common(10):
        print("   %6d  %s" % (v, k))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
