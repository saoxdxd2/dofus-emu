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
    # The report reads the loop's own constants, so a threshold here cannot drift.
    sys.path.insert(0, str(REPO))
    paths = argv or [str(x) for x in DEFAULTS if x.exists()]
    verdicts, actions, causes, dr, tools_fail, err_top = (Counter(), Counter(), Counter(),
                                                          Counter(), Counter(), Counter())
    rec, rec_ok, rec_wait = Counter(), Counter(), Counter()
    waits = runs = 0
    wait_reasons = Counter()
    escalations = Counter()
    longest = {}
    steps = decs = 0
    inter_events = inter_rows = 0
    sessions = set()
    span = {}
    prev_step = None
    for r in rows(paths):
        if r.get("kind") == "intervention":
            inter_rows += 1
            inter_events += int(r.get("events") or 0)
        if r.get("kind") == "recovery":
            trig = str(r.get("trigger") or "?")
            rec[trig] += 1
            rec_wait[trig] += float(r.get("waited_s") or 0.0)
            if r.get("recovered"):
                rec_ok[trig] += 1
        if r.get("kind") == "wait":
            waits += 1
            wait_reasons[str(r.get("reason") or "")[:52]] += 1
            if r.get("escalated"):
                escalations[str(r["escalated"])] += 1
            sid = r.get("session") or r["_file"]
            if r.get("consecutive") == 1:
                runs += 1
            # `consecutive` restarts with a fresh budget after a rescue, which can
            # split one physical wait; taking the max per session is the safe read.
            longest[sid] = max(longest.get(sid, 0), int(r.get("consecutive") or 1))
        ts = r.get("at") or r.get("ts")
        if isinstance(ts, (int, float)):
            sid = r.get("session") or r["_file"]
            sessions.add(sid)
            a, b = span.get(sid, (ts, ts))
            span[sid] = (min(a, ts), max(b, ts))
        if r.get("kind") == "decision":
            decs += 1
            actions[str(r.get("action"))] += 1
            # Decision rows written before G14 recorded the *transport* result, so
            # their error is null even for a game refusal and their cause is the one
            # the classifier produced from it. Pairing each decision with the step
            # that provoked it recovers the real text either way; prefer the row's
            # own error when it has one, because that is the newer format.
            err = r.get("error") or (prev_step or {}).get("error")
            causes["%s|%s" % (r.get("cause"), str(err or "")[:46])] += 1
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
        if r.get("kind") != "decision":
            prev_step = r
    total_min = sum((b - a) / 60.0 for a, b in span.values())
    print("files:", ", ".join(Path(p).name for p in paths))
    print("sessions %d, span %.1f min total, longest %.1f min"
          % (len(span), total_min, max(((b - a) / 60.0 for a, b in span.values()), default=0)))
    print("manual interventions: %d events in %d row(s)  "
          "(0 with no rows means the instrument existed; rows absent means it did not)"
          % (inter_events, inter_rows))
    if rec:
        print("\nrecovery waits (a rescue is counted, not assumed):")
        for trig, n in rec.most_common():
            print("   %6d  %-14s %d came back, %.1f s waited"
                  % (n, trig, rec_ok[trig], rec_wait[trig]))
    else:
        print("\nrecovery waits: no rows -- this log predates the recovery instrument")

    # G18: a wait is a pass with no directive, no verdict and no reward. Before it,
    # 3,010 of 4,287 steps were `state.snapshot` reads and 3,005 were scored as
    # success, so the run's productivity was mostly a count of looking.
    if waits:
        from agent.autopilot import MAX_CONSECUTIVE_WAITS
        counted = sum(verdicts.values())
        print("\nidle waits (G18: looking is not a step): %d passes in %d run(s), "
              "longest %d, over %d counted step(s)"
              % (waits, runs, max(longest.values()) if longest else 0, counted))
        for reason, n in wait_reasons.most_common(8):
            print("   %6d  %s" % (n, reason or "(no reason given)"))
        if escalations:
            print("   escalated to a real action at the bound: %d -- %s"
                  % (sum(escalations.values()), dict(escalations.most_common(6))))
        overflow = max(longest.values()) if longest else 0
        if overflow >= MAX_CONSECUTIVE_WAITS:
            print("   NOTE: a wait reached %d passes -- the goal had no action left "
                  "in it, so reads were dispatched again (see KNOWN_GAPS G18)" % overflow)
    else:
        print("\nidle waits: no rows -- this log predates the wait instrument")

    print("\nverdicts:      ", dict(verdicts.most_common()))
    print("decision acts: ", dict(actions.most_common()))
    print("causes:        ", dict((k, v) for k, v in causes.most_common(10)
                                 if "|" not in k))
    print("cause x real error text (paired with the provoking step):")
    for k, v in causes.most_common(14):
        if "|" in k:
            cause, err = k.split("|", 1)
            print("   %6d  %-20s %s" % (v, cause, err))
    print("\ndisconnect reasons seen in observations:")
    for k, v in dr.most_common(12):
        print("   %6d  %s" % (v, k))
    print("\nfailed steps by tool:", dict(tools_fail.most_common(10)))

    # The number that actually measures criterion (2): of the steps that could change
    # the world, how many did. A successful read also scores `advanced` -- 3,005 of the
    # baseline's 3,063 `advanced` rows are `state.snapshot` -- so counting every one of
    # them reports an agent that is productive when all it did was look.
    from agent.verifier import READ_ONLY_TOOLS

    queries = set(READ_ONLY_TOOLS) | {"hdv.list", "inv.sell"}
    prod = {}
    for r in rows(paths):
        if r.get("kind") == "decision" or not r.get("verdict"):
            continue
        tool = str(r.get("tool"))
        if tool in queries or tool == "-":
            continue
        slot = prod.setdefault(tool, [0, 0, 0])
        slot[0] += 1
        if r["verdict"] == "advanced":
            slot[1] += 1
        elif r.get("ok") is False:
            slot[2] += 1
    print("\nproductive-action success (reads and listings excluded):")
    print("   %-16s %7s %7s %8s %8s" % ("tool", "steps", "moved", "failed", "rate"))
    for tool, (n, adv, fail) in sorted(prod.items(), key=lambda kv: -kv[1][0]):
        print("   %-16s %7d %7d %8d %7.1f%%" % (tool, n, adv, fail, 100.0 * adv / n))
    moved = sum(v[1] for v in prod.values())
    steps = sum(v[0] for v in prod.values())
    print("   %-16s %7d %7d %8s %7.1f%%"
          % ("TOTAL", steps, moved, "-", 100.0 * moved / steps if steps else 0.0))
    print("\ntop failure texts:")
    for k, v in err_top.most_common(10):
        print("   %6d  %s" % (v, k))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
