"""The goal's own scoreboard: did successive sessions move the world?

`tools/session_close.py` writes one ledger row per close. This reads those rows and
answers the two questions the objective is judged on:

* **objectives_completed > 0** -- the sum of the non-baseline report deltas;
* **world-changing rate** -- actions that moved the world over clean actions. The clean
  corpus is cumulative, so a session's rate is the difference between consecutive
  corpus snapshots, and the trend compares those session rates. A rebuild that loses
  rows (a relabel, a dedupe change) is marked `shifted` rather than counted as a
  negative session.

A baseline row (the first close of a log, whose "delta" is the whole file) and a
rotated row (the file shrank) are not session progress and are excluded from the
totals, but they stay in `per_close` so nothing silently disappears. The move-head
verdict and the fight learner's eligible count ride along, because those are the two
learning steps the close actually performs today.

    python -m tools.progress_report
    python -m tools.progress_report --json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = REPO_ROOT / "data" / "session_closes.jsonl"
DELTA_FIELDS = ("steps", "objectives_completed", "objectives_done",
                "quests_completed", "failed")


def read_ledger(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        handle = Path(path).open("r", encoding="utf-8", errors="replace")
    except OSError:
        return rows
    with handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _has_change(deltas: Dict[str, Dict[str, Any]]) -> bool:
    """A close that re-read an unchanged log is not a session, even with --force."""
    for delta in deltas.values():
        for key in DELTA_FIELDS:
            value = delta.get(key)
            if isinstance(value, (int, float)) and float(value) != 0.0:
                return True
    return False


def _real_deltas(record: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    report = (record.get("steps") or {}).get("report") or {}
    out: Dict[str, Dict[str, Any]] = {}
    for name, delta in (report.get("deltas") or {}).items():
        if not isinstance(delta, dict):
            continue
        if delta.get("rotated") or delta.get("baseline"):
            continue
        out[str(name)] = delta
    return out


def summarise(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    closes = [row for row in records if isinstance(row, dict)]
    totals = {key: 0.0 for key in DELTA_FIELDS}
    sessions = 0
    legacy = 0
    corpus: Dict[str, Any] = {"actions": None, "moved": None, "at": None}
    per_close: List[Dict[str, Any]] = []
    prev_snapshot: Optional[Dict[str, int]] = None
    prev_labeler: Optional[str] = None
    relabeled_closes = 0
    session_rates: List[float] = []
    for record in closes:
        steps = record.get("steps") or {}
        report = steps.get("report") or {}
        # A row written before the close stamped `baseline_files` cannot say which
        # of its "deltas" were whole-file totals (the migration closes were both).
        # Counting them as session progress would invent -124 objectives, so they
        # are reported as legacy and left out of the totals.
        is_legacy = "baseline_files" not in report
        if is_legacy:
            legacy += 1
        deltas = {} if is_legacy else _real_deltas(record)
        row: Dict[str, Any] = {"at": record.get("at"), "files": len(deltas),
                               "legacy": is_legacy}
        for key in DELTA_FIELDS:
            value = sum(float(delta.get(key) or 0) for delta in deltas.values()
                        if isinstance(delta.get(key), (int, float)))
            row[key] = round(value, 2)
            totals[key] += value
        if deltas and _has_change(deltas):
            sessions += 1
        kept = (steps.get("janitor") or {}).get("kept") or {}
        row["corpus_actions"] = kept.get("actions")
        row["corpus_moved"] = kept.get("actions_moved")
        # The clean corpus is cumulative, so the latest close is the current rate;
        # summing every close would count the same actions again and again.
        row["session_actions"] = None
        row["session_moved"] = None
        row["session_rate"] = None
        row["shifted"] = False
        # A rebuild by different labelling code is not a session's work: the corpus
        # can gain or lose rows without the bot doing anything (G92's stale-label
        # corpus). The builder stamps its labelling code, so the delta across a
        # change is named and kept out of the rate trend.
        labeler = (steps.get("rebuild") or {}).get("labeler_sha")
        row["relabeled"] = bool(prev_labeler and labeler and prev_labeler != labeler)
        if row["relabeled"]:
            relabeled_closes += 1
        if labeler:
            prev_labeler = labeler
        if isinstance(row["corpus_actions"], int) and isinstance(row["corpus_moved"], int):
            corpus = {"actions": row["corpus_actions"],
                      "moved": row["corpus_moved"], "at": record.get("at")}
            if prev_snapshot is not None:
                d_actions = row["corpus_actions"] - prev_snapshot["actions"]
                d_moved = row["corpus_moved"] - prev_snapshot["moved"]
                row["session_actions"] = d_actions
                row["session_moved"] = d_moved
                if d_actions > 0 and d_moved >= 0:
                    row["session_rate"] = round(d_moved / d_actions, 4)
                    # A legacy close's snapshot is a real corpus state, but its
                    # whole-file "delta" is not a session, so it never enters the trend.
                    if not is_legacy and not row["relabeled"]:
                        session_rates.append(row["session_rate"])
                elif d_actions < 0 or d_moved < 0:
                    row["shifted"] = True
            prev_snapshot = {"actions": row["corpus_actions"],
                             "moved": row["corpus_moved"]}
        move = steps.get("move") or {}
        row["move_promoted"] = move.get("promoted")
        row["move_auc"] = move.get("test_auc")
        learn = steps.get("learn") or {}
        row["fight_eligible"] = learn.get("n")
        per_close.append(row)
    rate = (round(corpus["moved"] / corpus["actions"], 4)
            if isinstance(corpus.get("actions"), int) and corpus["actions"] else None)
    trend = "unmeasured"
    if len(session_rates) >= 2:
        trend = ("rising" if session_rates[-1] > session_rates[-2]
                 else "falling" if session_rates[-1] < session_rates[-2] else "flat")
    return {
        "closes": len(closes),
        "legacy_closes": legacy,
        "sessions_with_new_rows": sessions,
        "totals": {key: round(value, 2) for key, value in totals.items()},
        "corpus": {**corpus, "rate": rate},
        "session_rates": session_rates,
        "relabeled_closes": relabeled_closes,
        "trend": trend,
        "objective_progress": (totals["objectives_completed"] > 0
                               or totals["objectives_done"] > 0),
        "per_close": per_close,
    }


def verdict(report: Dict[str, Any]) -> str:
    if report["sessions_with_new_rows"] == 0:
        return ("objective progress not yet measured: %d close(s), none with a "
                "non-baseline delta" % report["closes"])
    corpus = report["corpus"]
    rate = corpus.get("rate")
    last = next((row for row in reversed(report["per_close"])
                 if not row.get("legacy") and row.get("session_rate") is not None), None)
    session = ("" if last is None else
               "; last session +%d clean action(s), %d moved (%.1f%%), trend %s"
               % (last["session_actions"], last["session_moved"],
                  100.0 * last["session_rate"], report["trend"]))
    return ("objectives %+g (%g newly done) over %d session(s) with new rows; "
            "world-changing rate %s over %s clean action(s) in the latest corpus%s"
            % (report["totals"]["objectives_completed"],
               report["totals"]["objectives_done"],
               report["sessions_with_new_rows"],
               "%.1f%%" % (100.0 * rate) if rate is not None else "unmeasured",
               corpus.get("actions"), session))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = summarise(read_ledger(args.ledger))
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return 0
    print("%-20s %5s %5s %7s %6s %6s %6s %8s %8s %10s %7s"
          % ("close", "files", "steps", "objs", "quests", "failed", "actions",
             "moved", "rate", "session", "move"))
    for row in report["per_close"]:
        rate = (round(100.0 * row["corpus_moved"] / row["corpus_actions"], 1)
                if isinstance(row.get("corpus_actions"), int) and row["corpus_actions"]
                and isinstance(row.get("corpus_moved"), int) else None)
        if row.get("shifted"):
            session = "shifted"
        elif row.get("session_rate") is not None:
            session = "+%d/%d %.0f%%" % (row["session_actions"], row["session_moved"],
                                         100.0 * row["session_rate"])
        else:
            session = "-"
        print("%-20s %5s %5s %7s %6s %6s %6s %8s %8s %10s %7s"
              % (str(row.get("at"))[:19] + (" (legacy)" if row.get("legacy") else ""),
                 row.get("files"), row.get("steps"),
                 row.get("objectives_completed"), row.get("quests_completed"),
                 row.get("failed"), row.get("corpus_actions"), row.get("corpus_moved"),
                 "" if rate is None else "%.1f%%" % rate, session,
                 row.get("move_promoted")))
    print(verdict(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
