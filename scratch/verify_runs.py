"""Verify the longest read runs are real, not a session-splitting artifact."""
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
sys.path.insert(0, str(ROOT))
from agent.autopilot import READ_ONLY_TOOLS  # noqa: E402

APP = Path(os.environ["LOCALAPPDATA"]) / "DofusFarm"

for name, path in (("autopilot.jsonl", APP / "autopilot.jsonl"),
                   ("default.jsonl", ROOT / "trajectories" / "default.jsonl")):
    rows = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("tool"):
                rows.append(r)
    print("=== %s: %d tool rows" % (name, len(rows)))
    print("  session key present on %d rows" % sum(1 for r in rows if r.get("session")))
    # longest consecutive read run, ignoring session entirely
    best = cur = 0
    best_end = None
    for i, r in enumerate(rows):
        if r["tool"] in READ_ONLY_TOOLS:
            cur += 1
            if cur > best:
                best, best_end = cur, i
        else:
            cur = 0
    print("  longest consecutive read run (no session split): %d, ends at row %d"
          % (best, best_end))
    if best_end is not None:
        start = best_end - best + 1
        window = rows[start:best_end + 1]
        print("  tools in that run:", dict(Counter(r["tool"] for r in window)))
        print("  sessions in that run:", dict(Counter(str(r.get("session")) for r in window)))
        print("  ok values:", dict(Counter(str(r.get("ok")) for r in window)))
        ats = [r.get("at") for r in window if isinstance(r.get("at"), (int, float))]
        if len(ats) >= 2:
            print("  wall span: %.1f s over %d rows (%.3f s/row)"
                  % (ats[-1] - ats[0], len(ats), (ats[-1] - ats[0]) / max(1, len(ats) - 1)))
        print("  first row:", json.dumps({k: window[0].get(k) for k in
                                          ("tool", "ok", "cause", "error", "at")})[:220])
        print("  last row :", json.dumps({k: window[-1].get(k) for k in
                                          ("tool", "ok", "cause", "error", "at")})[:220])
        # what broke the run
        if best_end + 1 < len(rows):
            nxt = rows[best_end + 1]
            print("  run broken by:", json.dumps({k: nxt.get(k) for k in
                                                 ("tool", "ok", "cause", "error")})[:220])
    print()