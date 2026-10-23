"""The reality check: does the simulator's advice survive contact with the client?

The simulator says "after a failure, retrying is the worst option" (continue 0.396 vs
retry 0.302 mean objectives). That number decides the whole design, so it has to be
checked against the game rather than trusted because it came out of a tidy table.

This reads what the real agent actually did — `autopilot.jsonl` plus the client log —
and builds the same table from real outcomes:

    after a failure, what did the next step do, and did it work?

It reports how much evidence exists, and refuses to draw a conclusion below a minimum.
A verdict from four samples is worse than no verdict, because it gets quoted.

    python ai/reality_check.py
    python ai/reality_check.py --min-samples 30
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

STATE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm"
LOG = STATE_DIR / "autopilot.jsonl"
CLIENT_LOG = Path(__file__).resolve().parent.parent.parent / "DofusFarm.log"

MIN_SAMPLES = 30


def read_steps(path: Path) -> List[Dict[str, Any]]:
    """Step rows only. The same file now also carries `kind: decision` rows."""
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("kind") == "decision":
                    continue          # a decision, not a step; see ai/export_decision_log.py
                rows.append(row)
    return rows


def analyse(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Classify what followed each failure, and whether it worked."""
    failures = [s for s in steps if not s.get("ok")]
    outcomes = {"retry": [], "continue": [], "recover": [], "stop": []}

    for i, step in enumerate(steps):
        if step.get("ok"):
            continue
        nxt = steps[i + 1] if i + 1 < len(steps) else None
        if nxt is None:
            outcomes["stop"].append(False)
            continue
        if nxt.get("tool") == step.get("tool"):
            kind = "retry"
        elif nxt.get("tool") == "state.snapshot":
            kind = "recover"
        else:
            kind = "continue"
        outcomes[kind].append(bool(nxt.get("ok")))

    table = {}
    for kind, results in outcomes.items():
        if not results:
            continue
        table[kind] = {"n": len(results),
                       "success_rate": round(sum(results) / len(results), 3)}

    # Why did they fail? A structural failure (no client, unknown tool) makes the
    # whole retry-vs-continue comparison meaningless: retrying an absent client can
    # never work, so it would look like evidence that retry is bad.
    from agent.controller import error_class

    classes: Dict[str, int] = {}
    for step in failures:
        kind = error_class(step.get("error"))
        classes[kind] = classes.get(kind, 0) + 1

    total = len(steps)
    return {"steps": total, "failures": len(failures),
            "failure_rate": round(len(failures) / max(1, total), 3),
            "failure_classes": classes,
            "after_failure": table}


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-samples", type=int, default=MIN_SAMPLES)
    args = ap.parse_args(argv)

    steps = read_steps(LOG)
    if not steps:
        print("no real steps recorded yet: %s" % LOG)
        print("run the loop against a live client first; the simulator cannot answer this.")
        return 1

    result = analyse(steps)
    print(json.dumps(result, indent=1))

    n_fail = result["failures"]
    print("\n%d real steps, %d failures (%.0f%%)"
          % (result["steps"], n_fail, 100 * result["failure_rate"]))
    for kind, row in result["after_failure"].items():
        print("  after failure -> %-9s n=%-3d success %.0f%%"
              % (kind, row["n"], 100 * row["success_rate"]))

    structural = result["failure_classes"].get("structural", 0)
    if structural and structural >= n_fail * 0.8:
        print("\nNOTE: %d of %d failures are structural (no client / unknown tool). "
              "Retrying those cannot work, so this data cannot rank recovery "
              "strategies -- it only proves the controller was retrying a loop." % (structural, n_fail))

    if n_fail < args.min_samples:
        print("\nVERDICT: not enough real failures (%d of %d). The simulator's ranking "
              "stays unverified." % (n_fail, args.min_samples))
        print("What it takes: a real session where actions actually fail — gathering, "
              "fights, quest advances — not just reads.")
        return 2

    best = max(result["after_failure"].items(), key=lambda kv: kv[1]["success_rate"])
    print("\nVERDICT: in the real client, %r recovered best (%.0f%% of %d attempts)."
          % (best[0], 100 * best[1]["success_rate"], best[1]["n"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
