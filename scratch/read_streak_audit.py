"""How often a read-only call interrupts a run of identical action calls.

The verdict is not in the recorded step row (recorder.step never writes it), but
for a read-only tool it is deterministic: verifier._rule returns `advanced` for
any ok read, and label() forbids the model from overruling a read. So every ok
read is a `_recent.clear()` under the current loop, and that is countable exactly.
"""
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
sys.path.insert(0, str(ROOT))
from agent.autopilot import _call_key, SETTLE_TOOLS  # noqa: E402
from agent.verifier import READ_ONLY_TOOLS  # noqa: E402

LOGS = [ROOT / "trajectories" / "dofus-01.jsonl",
        ROOT / "trajectories" / "dofus-02.jsonl",
        ROOT / "trajectories" / "dofus-03.jsonl",
        ROOT / "trajectories" / "dofus-04.jsonl"]

grand = Counter()
for path in LOGS:
    if not path.is_file():
        continue
    steps = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("kind") == "step":
                steps.append(row)
    reads_ok = 0
    reads_all = 0
    acts = 0
    # Walk the sequence. An ok read clears the streak; count how many identical
    # action keys were adjacent across that clear (i.e. the read split a repeat).
    split_repeats = 0
    pending = []          # action keys since the last clear
    interrupted = []      # runs broken by a read
    for row in steps:
        tool = row.get("tool")
        key = _call_key(SimpleNamespace(tool=tool, args=row.get("args") or {}))
        if tool in READ_ONLY_TOOLS:
            reads_all += 1
            if row.get("ok"):
                reads_ok += 1
                if len(pending) >= 2 and len(set(pending[-2:])) == 1:
                    split_repeats += 1
                    interrupted.append(pending[-1])
                pending = []
            continue
        acts += 1
        pending.append(key)
    print("%-22s steps=%5d actions=%5d reads=%4d ok_reads=%4d "
          "reads_that_split_an_identical_action_pair=%3d"
          % (path.name, len(steps), acts, reads_all, reads_ok, split_repeats))
    for k, n in Counter(interrupted).most_common(4):
        print("      split %-70s %d" % (k[:70], n))
    grand["steps"] += len(steps)
    grand["actions"] += acts
    grand["reads"] += reads_all
    grand["ok_reads"] += reads_ok
    grand["split"] += split_repeats

print("\nTOTAL", dict(grand))
print("ok reads as a share of all steps: %.1f%%"
      % (100.0 * grand["ok_reads"] / max(1, grand["steps"])))