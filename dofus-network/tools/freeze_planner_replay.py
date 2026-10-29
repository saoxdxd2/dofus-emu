"""Freeze a slice of recorded live observations for the planner regression check.

The autopilot jsonl logs live outside the repo (%LOCALAPPDATA%\\DofusFarm) and
rotate, so a bug that only appears on a recorded live state has no test that can
see it. This writes a trimmed, deterministic corpus -- rows of {session, goal, obs}
-- that `agent.regression.test_planner_replay` replays and pins against.

What is trimmed is only noise the planner never chooses from (the packet log under
`map.transition`, geometry, keep-alive counters, user-input counters, last
navigation/disconnect). The rest of each observation is kept byte-for-byte so the
replay sees the state the live planner saw.

Usage:
    python tools/freeze_planner_replay.py             # rewrite the corpus
    python tools/freeze_planner_replay.py --check     # report drift, write nothing
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES = [
    Path(os.path.expandvars(r"%LOCALAPPDATA%\DofusFarm\autopilot.dofus-02.jsonl")),
    Path(os.path.expandvars(r"%LOCALAPPDATA%\DofusFarm\autopilot.dofus-05.jsonl")),
    Path(os.path.expandvars(r"%LOCALAPPDATA%\DofusFarm\autopilot.dofus-03.jsonl")),
]
OUT = REPO_ROOT / "data" / "replay" / "planner_obs.jsonl"
PER_SESSION = 30
MAX_ROWS = 420

DROP = ("geometry", "keep_alive", "user_input", "last_disconnect", "last_nav",
        "farm_state", "farm_session", "auto_fight", "ensemble")


def trim(obs: Dict[str, Any]) -> Dict[str, Any]:
    out = {key: value for key, value in obs.items() if key not in DROP}
    block = out.get("map")
    if isinstance(block, dict):
        # The packet log is the bulk of a large observation and no planner branch
        # reads it; it is also the only place a login ticket could hide.
        out["map"] = {key: value for key, value in block.items()
                      if key != "transition"}
    return out


def collect(paths: List[Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        by_session: Dict[str, List[Dict[str, Any]]] = {}
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("kind") != "decision":
                    continue
                obs = record.get("obs")
                if not isinstance(obs, dict):
                    continue
                by_session.setdefault(str(record.get("session")), []).append(record)
        for session in sorted(by_session):
            decisions = sorted(by_session[session], key=lambda r: r.get("at") or 0)
            stride = max(1, len(decisions) // PER_SESSION)
            for record in decisions[::stride][:PER_SESSION]:
                goal = record.get("goal")
                rows.append({
                    "session": path.stem.split("autopilot.")[-1] + ":" + session,
                    "goal": goal if isinstance(goal, str) and goal else "do all the quests",
                    "obs": trim(record["obs"]),
                })
    return rows[:MAX_ROWS]


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sources", nargs="*", type=Path, help="autopilot jsonl files")
    parser.add_argument("--check", action="store_true", help="report drift, write nothing")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    rows = collect(args.sources or DEFAULT_SOURCES)
    if not rows:
        print("no recorded decisions found; sources: %s"
              % ", ".join(str(path) for path in (args.sources or DEFAULT_SOURCES)))
        return 1
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        print("rows=%d sessions=%d bytes=%d" % (
            len(rows), len({row["session"] for row in rows}), len(text)))
        print("drift" if current != text else "matches", str(args.out))
        return 0 if current == text else 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print("wrote %d rows / %d sessions to %s" % (
        len(rows), len({row["session"] for row in rows}), args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
