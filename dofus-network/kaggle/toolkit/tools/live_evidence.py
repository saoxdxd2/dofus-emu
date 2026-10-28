"""One command from an open client to a combined live-shadow evidence report.

Preflight (fail closed on a stop file/stale page, refuse to run without a client
window), run a bounded autopilot with ``--command-shadow auto``, then write a
combined JSON with the session report, the live command-shadow summary and the
replay. The rule still acts; this only collects the evidence.

    python tools/live_evidence.py --steps 20
    python tools/live_evidence.py --report-only --log trajectories/live_shadow.jsonl

Close the panel-managed loop first: two autopilots would compete for the bridge.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
LIVE_READY = ROOT / "tools" / "live_ready.py"
SESSION_REPORT = ROOT / "tools" / "session_report.py"
SHADOW_REPORT = ROOT / "tools" / "command_shadow_report.py"
REPLAY = ROOT / "tools" / "command_replay.py"


def _run(command: List[str], capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=str(ROOT), text=True,
                          capture_output=capture)


def preflight() -> List[Dict[str, Any]]:
    proc = _run([sys.executable, str(LIVE_READY), "--json"])
    try:
        return json.loads(proc.stdout or "[]")
    except ValueError:
        return [{"level": "FAIL", "name": "preflight",
                 "detail": (proc.stdout or proc.stderr or "unparseable")[:300]}]


def client_ready(entries: Iterable[Dict[str, Any]]) -> bool:
    entries = list(entries)
    if not entries or any(entry.get("level") == "FAIL" for entry in entries):
        return False
    return any(entry.get("name") == "client" and entry.get("level") == "PASS"
               for entry in entries)


def shadow_rows_summary(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    shadow = [row for row in rows if row.get("kind") == "command_shadow"]
    status = [row for row in rows if row.get("kind") == "command_shadow_status"]
    return {
        "status": status[-1] if status else None,
        "rows": len(shadow),
        "usable": sum(1 for row in shadow if row.get("usable")),
        "agree_intent": sum(1 for row in shadow if row.get("agree_intent")),
        "agree_command": sum(1 for row in shadow if row.get("agree_command")),
        "would_override": sum(1 for row in shadow if row.get("would_override")),
        "would_change_mode": sum(1 for row in shadow
                                 if row.get("would_change_mode")),
        "override_examples": [row.get("text") for row in shadow
                              if row.get("would_override")][:5],
    }


def resolve_log(path: Path) -> Path:
    """Child reports run with cwd=ROOT; keep the log independent of that cwd."""
    return Path(path).expanduser().resolve()


def read_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _json_from(proc: subprocess.CompletedProcess) -> Any:
    try:
        return json.loads(proc.stdout or "null")
    except ValueError:
        return {"error": (proc.stderr or proc.stdout or "")[:400]}


def parse_autopilot_summary(stdout: str) -> Optional[Dict[str, Any]]:
    """The autopilot prints many lines then one JSON summary as its last object."""
    for line in reversed(str(stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--goal", default="do all the quests")
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--instance", default="default")
    parser.add_argument("--log", type=Path,
                        default=ROOT / "trajectories" / "live_shadow.jsonl")
    parser.add_argument("--report-only", action="store_true",
                        help="skip preflight/run; report on an existing --log")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)
    # Child reports run with cwd=ROOT; a relative log would resolve against the
    # installed root and silently report an empty session. Resolve it once here.
    args.log = resolve_log(args.log)
    if args.json_out is not None:
        args.json_out = args.json_out.resolve()

    entries: List[Dict[str, Any]] = []
    returncode: Optional[int] = None
    autopilot_summary: Optional[Dict[str, Any]] = None
    if not args.report_only:
        entries = preflight()
        for entry in entries:
            print("%-5s %-18s %s" % (entry.get("level"), entry.get("name"),
                                     entry.get("detail")))
        if not client_ready(entries):
            print("live evidence: refusing to run without a healthy client; "
                  "start DofusFarm (and close any panel-managed loop) first.")
            return 1
        command = [sys.executable, "-m", "agent.autopilot",
                   "--goal", args.goal, "--steps", str(args.steps),
                   "--command-shadow", "auto", "--log", str(args.log),
                   "--instance", args.instance]
        print("live evidence: running", " ".join(command))
        proc = _run(command, capture=True)
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)
        returncode = proc.returncode
        autopilot_summary = parse_autopilot_summary(proc.stdout)

    rows = read_rows(args.log)
    if not rows:
        print("live evidence: no rows at %s" % args.log)
        return 1
    shadow = shadow_rows_summary(rows)
    session = _json_from(_run([sys.executable, str(SESSION_REPORT),
                               str(args.log), "--json"]))
    shadow_out = args.log.with_suffix(".shadow.json")
    shadow_proc = _run([sys.executable, str(SHADOW_REPORT),
                        "--logs", str(args.log), "--out", str(shadow_out)])
    shadow_report = _json_file(shadow_out) or _json_from(shadow_proc)
    replay = _json_from(_run([sys.executable, str(REPLAY),
                              "--logs", str(args.log),
                              "--out", str(args.log.with_suffix(".replay.jsonl"))]))
    report = {
        "kind": "command_live_evidence",
        "at": time.time(),
        "goal": args.goal,
        "log": str(args.log),
        "autopilot_returncode": returncode,
        "autopilot_summary": autopilot_summary,
        "preflight": entries,
        "shadow": shadow,
        "session": session,
        "shadow_report": shadow_report,
        "replay": replay,
    }
    out = args.json_out or (ROOT / "reports" /
                            ("live_evidence_%s.json"
                             % time.strftime("%Y%m%d-%H%M%S")))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print("live evidence: %d shadow rows, %d overrides, %d mode changes -> %s"
          % (shadow["rows"], shadow["would_override"],
             shadow["would_change_mode"], out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
