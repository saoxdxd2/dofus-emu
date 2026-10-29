"""Rebuild the live-outcome dataset from every instance trajectory on disk.

The 2026-10-29 audit found `data/transitions` two days old while the daemon's
trajectory for one instance alone had grown to 40 MB. `tools/auto_live.py` does
rebuild transitions, but only for the logs *it* launched; sessions started from
the brain daemon were never ingested. This closes that step for both:

  trajectories/dofus-*.jsonl, default*.jsonl  (not play_*, not stats)
      -> build_live_transitions (segment split, live features)
      -> data/transitions_v2/{train,val,test}.jsonl + manifest.json

It is deterministic (the builder hashes segments into splits), so running it
twice on the same logs is a no-op in content. It reads files only and never
touches a running session.

    python tools/refresh_live_dataset.py
    python tools/refresh_live_dataset.py --out data/transitions_v3 --json
"""

from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
TRAJ = REPO_ROOT / "trajectories"


def instance_logs() -> List[Path]:
    out: List[Path] = []
    for pattern in ("dofus-*.jsonl", "default*.jsonl"):
        for path in sorted(TRAJ.glob(pattern)):
            name = path.name
            if name.startswith("play_"):
                continue
            if name.endswith(".stats.json"):
                continue
            out.append(path)
    return out


def refresh(out: Path, logs: Optional[List[Path]] = None,
            quiet: bool = False, split: str = "hash") -> Dict[str, Any]:
    logs = logs if logs is not None else instance_logs()
    if not logs:
        return {"ok": False, "error": "no instance trajectories under %s" % TRAJ}
    cmd = [sys.executable, str(REPO_ROOT / "tools" / "build_live_transitions.py"),
           "--out", str(out), "--json", "--split", split, "--log"] + [str(p) for p in logs]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT),
                          timeout=1800, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return {"ok": False, "returncode": proc.returncode,
                "error": (proc.stderr or "")[-600:]}
    try:
        start = proc.stdout.find("{")
        manifest = json.loads(proc.stdout[start:])
    except ValueError:
        return {"ok": False, "error": "builder printed no manifest",
                "stdout": (proc.stdout or "")[-600:]}
    result = {
        "ok": True,
        "out": str(out),
        "logs": [str(p) for p in logs],
        "rows": manifest.get("rows"),
        "segments": manifest.get("segments"),
        "moved": manifest.get("actions_moved"),
        "progress": manifest.get("actions_progress"),
        "move_rate": manifest.get("action_move_rate"),
        "reads": manifest.get("reads"),
        "x_coverage_pct": manifest.get("x_coverage_pct"),
        "split_actions": manifest.get("split_actions"),
    }
    if not quiet:
        print(json.dumps(result, indent=1))
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "transitions_v2")
    ap.add_argument("--split", choices=("hash", "temporal"), default="hash",
                    help="temporal holds out the latest segments as test")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    result = refresh(args.out, quiet=args.json, split=args.split)
    if args.json:
        print(json.dumps(result, indent=1))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
