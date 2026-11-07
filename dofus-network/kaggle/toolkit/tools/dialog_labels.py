"""Reconstruct (dialog page, reply sent) -> outcome labels from the recorded trajectories.

G20/G21 label the *future*: a dialog row is only written by a run that already has the
instrument. The recorded corpus predates it, yet it already contains the same pairing in
another form -- a step whose args carry `reply`, the page that was open in its observation,
and the next row's observation, which is what the answer produced. This reads that pairing
back out, so the dialog scorer has a training set without waiting for another live session.

The label is outcome-supervised, not imitation. The reply that was sent was chosen by the
heuristic under test, so copying it would teach the heuristic; only the *effect* is used as
the target: `advanced` is a positive for the reply that was sent, `same page` and a dialog
the server ended are negatives. Rows whose page offered one reply cannot teach a choice at
all, and are counted separately rather than silently mixed into the set.

Usage:
    python tools/dialog_labels.py                 # census, then write the dataset
    python tools/dialog_labels.py --no-write      # census only
    python tools/dialog_labels.py --out PATH      # where to write (default data/...)
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "trajectories")
DEFAULT_OUT = os.path.join(ROOT, "data", "dialog_labels.jsonl")

# The wire is a better witness than the observation cache: these messages are what the
# server sends when an answer actually did something. `recv` names below are taken from
# the recorded PLAY_LOG streams, not guessed.
PROGRESS = ("QuestObjectiveValidatedMessage", "QuestStepValidatedMessage",
            "QuestStepInfoMessage", "MapNpcsQuestStatusUpdateMessage")
TERMINAL = ("LeaveDialogMessage",)
RESET = ("NpcDialogReplyMessage", "NpcGenericActionRequestMessage",
         "LeaveDialogRequestMessage")

# How many wire events after the reply we are still willing to attribute to it. The
# recorded answers produce their effect within a handful of packets; past this the next
# action has probably begun and the label would be a guess.
WIRE_WINDOW = 60


def wire_outcomes(paths: List[str]) -> Dict[Any, Dict[str, Any]]:
    """Aggregate, per server `replyId`, what the wire says happened after it was sent.

    The keys are reply ids, so this can be joined to a dialog pair without trusting the
    observation that followed it -- which is exactly what the recorded corpus cannot do
    (its post-answer state is usually a carried read, G23).
    """
    agg: Dict[Any, Dict[str, Any]] = {}
    for path in paths:
        # session -> ordered (seq, kind, name) of the dialog-relevant packets only
        streams: Dict[str, List[tuple]] = {}
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"PLAY_LOG"' not in line or '"name"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ev = row.get("event")
                if not isinstance(ev, dict) or ev.get("kind") not in ("send", "recv"):
                    continue
                name = ev.get("name")
                if not isinstance(name, str):
                    continue
                if name == "NpcDialogReplyMessage":
                    reply = (ev.get("args") or {}).get("replyId")
                    streams.setdefault(str(row.get("session")), []).append(
                        (row.get("seq"), "reply", reply))
                elif name in PROGRESS or name in TERMINAL or name in RESET:
                    streams.setdefault(str(row.get("session")), []).append(
                        (row.get("seq"), "event", name))
        for events in streams.values():
            events.sort(key=lambda e: (e[0] if isinstance(e[0], int) else 0))
            for position, entry in enumerate(events):
                if entry[1] != "reply":
                    continue
                label = "no response recorded"
                for nxt in events[position + 1:position + 1 + WIRE_WINDOW]:
                    if nxt[1] == "event" and nxt[2] in PROGRESS:
                        label = "quest progress"
                        break
                    if nxt[1] == "event" and nxt[2] in TERMINAL:
                        label = "dialog ended"
                        break
                    if nxt[1] == "event" and nxt[2] in RESET:
                        break      # the next action began; this reply owns nothing past it
                bucket = agg.setdefault(entry[2], {"reply_id": entry[2],
                                                   "sends": 0, "labels": Counter()})
                bucket["sends"] += 1
                bucket["labels"][label] += 1
    for bucket in agg.values():
        bucket["labels"] = dict(bucket["labels"])
        best = max(bucket["labels"].items(), key=lambda kv: kv[1])
        bucket["wire_outcome"] = best[0]
    return agg

# How far past the dispatch to look for a state that reflects it. The loop settles on an
# effect before deciding again, so the next row is normally the answer; a run of reads with
# no dialog at all is where the search has to stop rather than guess.
LOOKAHEAD_ROWS = 8


def _rows(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if '"dialog"' not in line and '"reply"' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def _dialog(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    obs = row.get("observation")
    if not isinstance(obs, dict):
        return None
    dialog = obs.get("dialog")
    return dialog if isinstance(dialog, dict) else {}


def _objective(row: Dict[str, Any]) -> Dict[str, Any]:
    obs = row.get("observation")
    obs = obs if isinstance(obs, dict) else {}
    tracker = obs.get("current_objective")
    tracker = tracker if isinstance(tracker, dict) else {}
    return {"objective_id": tracker.get("objective_id"),
            "objective_type": tracker.get("type"),
            "objective_type_id": tracker.get("type_id"),
            "objective_text": str(tracker.get("text") or "")[:120]}


def _progress(before_obs: Dict[str, Any], row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Did the *objective* move, not just the page?

    A conversation that closes is ambiguous from the page alone: it is what a finished
    exchange looks like and also what a refused answer looks like. The objective counter is
    the tie-breaker the old logs already carry, so a label built on it is a quest-progress
    label rather than a UI label.
    """
    obs = (row or {}).get("observation")
    obs = obs if isinstance(obs, dict) else {}
    after = obs.get("current_objective")
    after = after if isinstance(after, dict) else {}
    before = before_obs.get("current_objective")
    before = before if isinstance(before, dict) else {}
    done_before = before_obs.get("objectives_completed")
    done_after = obs.get("objectives_completed")
    delta = None
    if isinstance(done_before, int) and isinstance(done_after, int):
        delta = done_after - done_before
    return {"objective_after": after.get("objective_id"),
            "completed_delta": delta,
            "objective_moved": bool(after and before.get("objective_id") is not None
                                    and str(after.get("objective_id"))
                                    != str(before.get("objective_id")))}


def _outcome(before: Dict[str, Any], after: Optional[Dict[str, Any]]) -> str:
    if after is None or not after:
        return "unknown"          # no dialog block came back: it says nothing
    if not after.get("open"):
        return "closed"
    if after.get("message") == before.get("message"):
        return "same page"
    return "advanced"


def pairs(path: str) -> List[Dict[str, Any]]:
    """Every reply/close dispatch in one log, paired with the state that followed it."""
    rows = _rows(path)
    out: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        if row.get("kind") != "step":
            continue
        args = row.get("args")
        if not isinstance(args, dict):
            continue
        asked = args.get("reply")
        closing = bool(args.get("close"))
        if asked is None and not closing:
            continue
        before = _dialog(row)
        if not before or not before.get("open"):
            continue        # cannot attribute a reply to a page we never saw
        after: Optional[Dict[str, Any]] = None
        after_row: Optional[Dict[str, Any]] = None
        # Most corpus rows *carry* an observation that a previous step already read
        # (`observation_source: carried`); only a row read for this step proves what the
        # answer produced. Pair with a fresh read when there is one, and say which kind was
        # used -- a "same page" measured against a carried observation is weaker evidence
        # than one measured against a read made after the dispatch.
        for later in rows[index + 1:index + 1 + LOOKAHEAD_ROWS]:
            candidate = _dialog(later)
            if candidate is None:
                continue
            if after_row is not None and str(later.get("observation_source")) != "call":
                continue
            after, after_row = candidate, later
            if str(later.get("observation_source")) == "call":
                break
        replies = before.get("replies")
        replies = replies if isinstance(replies, list) else []
        chosen = None
        if isinstance(asked, int):
            for entry in replies:
                if isinstance(entry, dict) and entry.get("index") == asked:
                    chosen = entry
                    break
        response = ((row.get("result") or {}).get("response") or {}) if isinstance(
            row.get("result"), dict) else {}
        record = {
            "source": os.path.basename(path),
            "ts": row.get("ts"),
            "instance": row.get("instance"),
            "quest_id": args.get("quest_id"),
            "objective_id": args.get("objective_id"),
            "page": str(before.get("message") or "")[:120],
            "npc_id": before.get("npc_id"),
            "name_id": before.get("name_id"),
            "reply_count": len(replies),
            "replies": [str(entry.get("text") or "")[:80]
                        for entry in replies[:8] if isinstance(entry, dict)],
            "reply_ids": [entry.get("reply_id") for entry in replies[:8]
                          if isinstance(entry, dict)],
            "asked": "close" if asked is None else int(asked),
            "reply_id": (chosen or {}).get("reply_id"),
            "reply_text": (chosen or {}).get("text"),
            "dispatch_ok": bool(row.get("ok", True)),
            "error": None if row.get("error") is None else str(row.get("error"))[:120],
            "via": (response.get("data") or {}).get("via")
                   if isinstance(response.get("data"), dict) else None,
            "outcome": _outcome(before, after),
            "after_source": str((after_row or {}).get("observation_source") or "none"),
        }
        # The objective is what makes a `same page` label mean "wrong reply" instead of
        # "this objective cannot be finished by a reply at all" -- keep it on every row.
        base = _objective(row)
        record.update(base)
        if record["objective_id"] is None:
            record["objective_id"] = args.get("objective_id")
        record.update(_progress(row.get("observation") or {}, after_row))
        out.append(record)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    files = [os.path.join(LOGS, name) for name in sorted(os.listdir(LOGS))
             if name.endswith(".jsonl")] if os.path.isdir(LOGS) else []
    all_pairs: List[Dict[str, Any]] = []
    for path in files:
        try:
            all_pairs.extend(pairs(path))
        except OSError as exc:      # a log that moved or is mid-rotate is not a crash
            print("skip %s: %s" % (os.path.basename(path), exc))

    play = [os.path.join(LOGS, name) for name in sorted(os.listdir(LOGS))
            if name.startswith("play_dofus") and name.endswith(".jsonl")] \
        if os.path.isdir(LOGS) else []
    wire = wire_outcomes(play)
    joined = 0
    for row in all_pairs:
        hit = wire.get(row.get("reply_id"))
        if hit is not None:
            row["wire_outcome"] = hit["wire_outcome"]
            row["wire_sends"] = hit["sends"]
            joined += 1
        else:
            row["wire_outcome"] = None
            row["wire_sends"] = None

    outcome = Counter(str(row["outcome"]) for row in all_pairs)
    kinds = Counter(str(row["objective_type"]) for row in all_pairs)
    closers = Counter("asked" if row["asked"] == "close" else "replied"
                      for row in all_pairs)
    multi = [row for row in all_pairs if int(row["reply_count"]) > 1]
    teachable = [row for row in multi if row["outcome"] == "advanced"]
    fresh_multi = [row for row in multi if row["after_source"] == "call"]
    fresh_adv = [row for row in fresh_multi if row["outcome"] == "advanced"]
    moved = [row for row in all_pairs
             if row["objective_moved"] or (row["completed_delta"] or 0) > 0]
    moved_choice = [row for row in moved if int(row["reply_count"]) > 1]
    pages = Counter((str(row["name_id"]), str(row["page"])[:48], str(row["asked"]))
                    for row in all_pairs)
    repeats = [(n, key) for key, n in pages.most_common(6) if n > 1]

    print("dialog pairs reconstructed from %d log file(s): %d" % (len(files), len(all_pairs)))
    print("  by action: ", dict(closers))
    print("  by outcome:", dict(outcome))
    print("  by objective type:", dict(kinds.most_common(6)))
    print("  pages offering >1 reply: %d of %d  (only these can teach a choice)"
          % (len(multi), len(all_pairs)))
    print("  of those, the answer moved the page: %d" % len(teachable))
    print("  choice pairs measured against a fresh read: %d, of which advanced: %d"
          % (len(fresh_multi), len(fresh_adv)))
    print("  pairs where the OBJECTIVE moved afterwards: %d" % len(moved))
    print("  ... of which the page offered a real choice: %d" % len(moved_choice))
    print("  distinct (npc, page, reply) asked more than once: %d" % len(repeats))
    for count, key in repeats:
        print("     %4d  npc=%s reply=%s page=%r" % (count, key[0], key[2], key[1]))
    positive_ids = sorted(rid for rid, value in wire.items()
                          if value["wire_outcome"] == "quest progress")
    credited = [row for row in all_pairs if row["wire_outcome"] == "quest progress"]
    credited_choice = [row for row in credited if int(row["reply_count"]) > 1]
    print("wire labels: %d distinct replyId(s) across %d play log(s)"
          % (len(wire), len(play)))
    print("  outcome per replyId:", dict(Counter(
        value["wire_outcome"] for value in wire.values())))
    print("  pairs joined to a wire label by replyId: %d of %d"
          % (joined, len(all_pairs)))
    print("  pairs the server credited with quest progress: %d" % len(credited))
    print("  ... of which the page offered more than one reply: %d" % len(credited_choice))
    print("  replyIds credited: %s"
          % ",".join(str(rid) for rid in positive_ids[:8]))
    if not teachable and not moved_choice and not fresh_adv and not credited_choice:
        print("\n  VERDICT: the recorded corpus contains no labelled example of a dialog")
        print("  choice that worked. Every multi-reply page it holds ended on the same page")
        print("  or was closed. A scorer trained on this data would be fitting noise, so")
        print("  criterion (4) for dialogs is blocked on collection, not on modelling.")
    print("  NOTE the reply that was sent was chosen by the heuristic under test; the")
    print("       dataset's target is the outcome, so training on it is not imitation.")

    if not args.no_write:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            for row in all_pairs:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print("  wrote %s" % args.out)
    return 0 if all_pairs else 1


if __name__ == "__main__":
    raise SystemExit(main())
