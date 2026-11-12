"""Are reads a bounded preamble or an unbounded spin?

#3 stopped reads from being *counted* as progress. It did not change how often the
planner *chooses* one. If most steps are reads, the throughput problem is the choice,
not the counter -- so this measures consecutive read runs, per session, in recorded
order, using the same tool sets the autopilot uses.
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
sys.path.insert(0, str(ROOT))
from agent.autopilot import READ_ONLY_TOOLS, SETTLE_TOOLS  # noqa: E402

APP = Path(os.environ["LOCALAPPDATA"]) / "DofusFarm"
files = [ROOT / "trajectories" / "default.jsonl",
         ROOT / "trajectories" / "autopilot.jsonl"]
files += sorted(APP.glob("autopilot*.jsonl"))

total = reads = 0
runs = Counter()
per_tool = Counter()
read_tool = Counter()
sessions = 0
longest = []
for path in files:
    if not path.is_file():
        continue
    cur_session = None
    run = 0
    run_start = None
    n_file = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            tool = row.get("tool")
            if not tool:
                continue
            n_file += 1
            sess = row.get("session")
            if sess != cur_session:
                if run:
                    runs[run] += 1
                cur_session, run = sess, 0
                sessions += 1
            total += 1
            per_tool[tool] += 1
            if tool in READ_ONLY_TOOLS:
                reads += 1
                read_tool[tool] += 1
                if run == 0:
                    run_start = (path.name, sess, row.get("at"))
                run += 1
            else:
                if run:
                    runs[run] += 1
                    if run >= 8:
                        longest.append((run, run_start))
                    run = 0
    if run:
        runs[run] += 1
        if run >= 8:
            longest.append((run, run_start))
    print("%-46s %d tool rows" % (path.name, n_file))

print()
print("sessions: %d | tool steps: %d | reads: %d (%.1f%%)"
      % (sessions, total, reads, 100.0 * reads / max(1, total)))
print("read tools:", dict(read_tool.most_common(8)))
print("top tools overall:", dict(per_tool.most_common(8)))
print()
print("consecutive-read run lengths (run -> how many runs):")
for length in sorted(runs):
    print("  %3d -> %d" % (length, runs[length]))
tot_runs = sum(runs.values())
long_runs = sum(v for k, v in runs.items() if k >= 5)
print("runs total %d | runs of 5+ reads: %d (%.1f%% of runs)"
      % (tot_runs, long_runs, 100.0 * long_runs / max(1, tot_runs)))
steps_in_long = sum(k * v for k, v in runs.items() if k >= 5)
print("steps spent inside runs of 5+: %d (%.1f%% of all steps)"
      % (steps_in_long, 100.0 * steps_in_long / max(1, total)))
print("longest runs:", sorted(longest, reverse=True)[:6])