"""Turn recorded kicks into safety examples instead of leaving them in the log.

A `QuestObjectiveValidationMessage` is a claim about a quest objective. When the
server answers it with `serverDisconnecting` a few hundred milliseconds later,
that is ground truth: this claim, in this state, is illegal. This script finds
those pairs in `trajectories/*.jsonl` and writes two artefacts:

* ``data/safety/gate_examples.jsonl`` -- one row per validation, with the ages of
  the last map change and quest sync at the moment it was sent, and whether a
  disconnect followed. Both classes are present, so it can train a gate later.
* ``data/safety/kick_signatures.json`` -- the ``quest/objective`` pairs that were
  followed by a disconnect. The client seeds this list into
  ``window.__agentKnownKickSignatures`` before the page loads, so an objective
  that kicked once is not claimed again in a later session.

The 3 s attribution window is deliberately narrow: the measured gaps are
210-235 ms, and a wider window starts blaming a network drop on whatever was
claimed an hour earlier. ``--window-ms`` exposes it for re-measurement.

    python ai/safety_examples.py
    python ai/safety_examples.py --window-ms 1500 --logs trajectories
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]

QUEST_SYNC_MESSAGES = (
    "QuestObjectiveValidatedMessage",
    "QuestStepInfoMessage",
    "QuestsUpdatedMessage",
    "QuestStepStartedMessage",
)
KICK_MESSAGES = ("serverDisconnecting", "disconnect")


def _event(row: Dict[str, Any]) -> Dict[str, Any]:
    event = row.get("event")
    return event if isinstance(event, dict) else {}


def iter_rows(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def scan_file(path: Path, window_ms: float) -> Dict[str, Any]:
    last_map: Dict[str, Dict[str, Any]] = {}
    last_sync: Dict[str, float] = {}
    validations: List[Dict[str, Any]] = []
    kicks = 0

    for row in iter_rows(path):
        event = _event(row)
        kind = event.get("kind")
        name = event.get("name")
        at = row.get("at")
        if not isinstance(at, (int, float)):
            continue
        session = str(row.get("session") or "")

        if kind == "recv" and name == "CurrentMapMessage":
            args = event.get("args") if isinstance(event.get("args"), dict) else {}
            last_map[session] = {"at": float(at), "map_id": args.get("mapId")}
            continue

        if kind == "recv" and name in QUEST_SYNC_MESSAGES:
            last_sync[session] = float(at)
            continue

        if kind == "send" and name == "QuestObjectiveValidationMessage":
            args = event.get("args") if isinstance(event.get("args"), dict) else {}
            quest_id = args.get("questId")
            objective_id = args.get("objectiveId")
            if quest_id is None or objective_id is None:
                continue
            changed = last_map.get(session)
            validations.append({
                "file": path.name,
                "session": session,
                "at": float(at),
                "quest_id": int(quest_id),
                "objective_id": int(objective_id),
                "map_id": changed.get("map_id") if changed else None,
                "map_age_ms": round(float(at) - changed["at"], 1) if changed else None,
                "quest_sync_age_ms": round(float(at) - last_sync[session], 1)
                                     if session in last_sync else None,
                "outcome": "ok",
                "gap_ms": None,
            })
            continue

        if kind == "recv" and name in KICK_MESSAGES:
            kicks += 1
            reason = None
            args = event.get("args")
            if isinstance(args, dict):
                reason = args.get("reason")
            elif isinstance(args, str):
                reason = args
            # The most recent unmatched claim in the same session inside the
            # window is the one the server answered. Older claims are left safe.
            for item in reversed(validations):
                if item["session"] != session:
                    continue
                gap = float(at) - item["at"]
                if gap < 0 or gap > window_ms:
                    break
                if item["outcome"] != "ok":
                    continue
                item["outcome"] = "kick"
                item["gap_ms"] = round(gap, 1)
                item["disconnect_reason"] = reason
                break

    return {"file": path.name, "validations": validations, "kicks": kicks}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", type=Path, default=REPO_ROOT / "trajectories",
                    help="directory of recorded *.jsonl sessions")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "safety")
    ap.add_argument("--window-ms", type=float, default=3000.0,
                    help="a disconnect this soon after a claim is attributed to it")
    args = ap.parse_args(argv)

    files = sorted(args.logs.glob("*.jsonl"))
    if not files:
        raise SystemExit("no *.jsonl under %s" % args.logs)

    examples: List[Dict[str, Any]] = []
    kicks = 0
    for path in files:
        scanned = scan_file(path, args.window_ms)
        examples.extend(scanned["validations"])
        kicks += scanned["kicks"]
    examples.sort(key=lambda item: item["at"])

    kicked = [item for item in examples if item["outcome"] == "kick"]
    signature_counts = Counter("%d/%d" % (item["quest_id"], item["objective_id"])
                               for item in kicked)
    signatures = sorted(signature_counts)

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "gate_examples.jsonl").open("w", encoding="utf-8") as fh:
        for item in examples:
            fh.write(json.dumps(item, separators=(",", ":")) + "\n")
    (args.out / "kick_signatures.json").write_text(
        json.dumps(signatures, indent=1), encoding="utf-8")

    gaps = [item["gap_ms"] for item in kicked if item["gap_ms"] is not None]
    map_ages = [item["map_age_ms"] for item in kicked
                if item["map_age_ms"] is not None]
    report = {
        "files": [path.name for path in files],
        "validations": len(examples),
        "kicks_in_log": kicks,
        "kicks_attributed": len(kicked),
        "safe_examples": len(examples) - len(kicked),
        "signatures": {sig: signature_counts[sig] for sig in signatures},
        "gap_ms": {"median": statistics.median(gaps) if gaps else None,
                   "max": max(gaps) if gaps else None},
        "map_age_ms_at_kick": {"median": statistics.median(map_ages) if map_ages else None,
                               "max": max(map_ages) if map_ages else None},
        "window_ms": args.window_ms,
        "out": str(args.out),
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=1),
                                          encoding="utf-8")

    print("scanned %d file(s): %d validations, %d kicks, %d attributed"
          % (len(files), len(examples), kicks, len(kicked)))
    if gaps:
        print("attribution gap: median %.1f ms, max %.1f ms"
              % (statistics.median(gaps), max(gaps)))
    print("signatures (%d):" % len(signatures))
    for sig in signatures:
        print("  %-12s kicked %d time(s)" % (sig, signature_counts[sig]))
    print("wrote %s and %s"
          % (args.out / "gate_examples.jsonl", args.out / "kick_signatures.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
