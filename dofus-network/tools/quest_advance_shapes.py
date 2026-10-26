"""Split every recorded `quest.advance` row by what its arguments actually ask for.

Why this exists: an earlier pass counted "live quest-start requests" by looking for
`quest_id` in the arguments. `quest_id` is carried by *every* dialog and validation call
too — the page needs it to find the NPC and the guard needs it to check the claim — so the
count was the number of quest.advance rows, not the number of starts. The headline that came
out of it ("462 of 1,145 start requests named a quest this game never showed") was measuring
talk and claim traffic; the true start count in the same corpus is a small fraction of it. A
call's shape is a property of its whole argument set, so the classification lives here and the
gate tests it, instead of each report re-deriving it from one key.

Usage:
    python -m tools.quest_advance_shapes [paths...] [--window 5] [--json]

With no path it reads the recorded live corpus
(dofus-network/trajectories/default*.jsonl) and prints the split.
"""

from __future__ import annotations

import argparse
import glob
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GLOB = str(REPO_ROOT / "trajectories" / "default*.jsonl")

# Ordered: the most specific argument decides the shape. `objective_id` validates one
# objective; `reply`/`reply_text` answers an open dialog; `close` leaves one; `npc_id`
# *with* a quest id talks to a named NPC, which is a talk objective and has nothing to do
# with accepting a quest; a bare quest id is the only shape the page can read as
# "accept this quest" (QuestStartRequestMessage).
SHAPE_ORDER = ("claim", "reply", "close", "talk", "start")


def shape(args: Optional[Dict[str, Any]]) -> str:
    """One of `claim` / `reply` / `close` / `talk` / `start` / `other`."""
    if not isinstance(args, dict):
        return "other"
    if args.get("objective_id") is not None:
        return "claim"
    if args.get("reply") is not None or args.get("reply_text") is not None:
        return "reply"
    if args.get("close"):
        return "close"
    if args.get("quest_id") is None:
        return "other"
    if args.get("npc_id") is not None:
        return "talk"
    return "start"


def _client_ids(obs: Dict[str, Any]) -> set:
    """The quest ids this observation's own client reported.

    Imported from the planner so the tool and the live guard answer with one rule.
    """
    from agent.planner import client_quest_ids
    return client_quest_ids(obs)


def _active(obs: Dict[str, Any]) -> set:
    active = obs.get("quests_active")
    out = set()
    if isinstance(active, dict):
        for key in active:
            try:
                out.add(int(key))
            except (TypeError, ValueError):
                continue
    return out


def _in_dialog(obs: Dict[str, Any]) -> bool:
    """Whether the page was inside an NPC conversation when the call was made.

    This decides what a bare quest id *does*: while a dialog is open the page answers
    the first reply instead of accepting anything, so such a row is not a start attempt
    even though its arguments look like one.
    """
    if obs.get("in_dialog") is True:
        return True
    dialog = obs.get("dialog")
    return isinstance(dialog, dict) and bool(dialog.get("open"))


def _page_answer(row: Dict[str, Any]) -> Tuple[Any, str]:
    """(ok, what the page said) — `via`/`stage`/`reason` or the transport error."""
    ok = row.get("ok")
    result = row.get("result")
    inner: Dict[str, Any] = {}
    if isinstance(result, dict):
        response = result.get("response")
        if isinstance(response, dict):
            data = (response.get("result") or {})
            if isinstance(data, dict):
                inner = data.get("data") or {}
            if not inner and response.get("error"):
                return ok, "error: " + str(response["error"])[:70]
    for key in ("via", "stage", "reason"):
        if inner.get(key):
            return ok, str(inner[key])
    if row.get("error"):
        return ok, "error: " + str(row["error"])[:70]
    return ok, "silent"


def summarise(paths: Iterable[str], window: int = 5) -> Dict[str, Any]:
    shapes: Counter = Counter()
    starts: List[Dict[str, Any]] = []
    sessions: Dict[Tuple[str, Any], List[Dict[str, Any]]] = {}
    rows = 0
    for path in paths:
        try:
            handle = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                rows += 1
                key = (path, row.get("instance"))
                sessions.setdefault(key, []).append(row)
                if row.get("tool") != "quest.advance":
                    continue
                args = row.get("args")
                if not isinstance(args, dict):
                    args = {}
                shapes[shape(args)] += 1
    for (path, instance), items in sessions.items():
        for index, row in enumerate(items):
            if row.get("tool") != "quest.advance":
                continue
            args = row.get("args")
            if not isinstance(args, dict) or shape(args) != "start":
                continue
            obs = row.get("observation")
            obs = obs if isinstance(obs, dict) else {}
            try:
                qid = int(args["quest_id"])
            except (TypeError, ValueError):
                qid = args["quest_id"]
            reported = qid in _client_ids(obs) if isinstance(qid, int) else False
            honoured = False
            for later in items[index + 1:index + 1 + window]:
                lobs = later.get("observation")
                if isinstance(lobs, dict) and isinstance(qid, int) and qid in _active(lobs):
                    honoured = True
                    break
            ok, answer = _page_answer(row)
            starts.append({"session": f"{Path(path).name}/{instance}", "iso": row.get("iso"),
                           "goal": row.get("goal"), "quest_id": qid, "reported": reported,
                           "honoured": honoured, "ok": ok, "page": answer,
                           "in_dialog": _in_dialog(obs),
                           "already_active": (isinstance(qid, int)
                                              and qid in _active(obs))})
    total = sum(shapes.values())
    never = [s for s in starts if not s["reported"]]
    outside = [s for s in starts if not s["in_dialog"]]
    inside = [s for s in starts if s["in_dialog"]]
    return {
        "rows": rows,
        "quest_advance": total,
        "shapes": dict(shapes),
        "start_share_of_quest_advance_pct": round(100.0 * len(starts) / total, 2) if total else 0.0,
        "starts": {
            "count": len(starts),
            "distinct_ids": len({s["quest_id"] for s in starts}),
            "never_reported": len(never),
            "honoured": sum(1 for s in starts if s["honoured"]),
            "while_dialog_open": len(inside),
            "outside_dialog": len(outside),
            "honoured_outside_dialog": sum(1 for s in outside if s["honoured"]),
            "repeats_of_same_id": len(starts) - len({(s["session"], s["quest_id"])
                                                    for s in starts}),
            "page_answers": dict(Counter(f"{s['ok']}/{s['page']}" for s in starts)),
            "ids": dict(Counter(str(s["quest_id"]) for s in starts).most_common(10)),
            "rows": starts,
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="trajectory JSONL files (default: the live corpus)")
    ap.add_argument("--window", type=int, default=5,
                    help="steps after the request to look for the quest becoming active")
    ap.add_argument("--json", action="store_true", help="print the machine-readable summary")
    ns = ap.parse_args(argv)
    paths = ns.paths or sorted(glob.glob(DEFAULT_GLOB))
    out = summarise(paths, window=ns.window)
    if ns.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"files: {len(paths)}  rows: {out['rows']}  quest.advance: {out['quest_advance']}")
    print("shapes:")
    for name in list(SHAPE_ORDER) + ["other"]:
        if out["shapes"].get(name):
            print(f"  {name:<7} {out['shapes'][name]:>6}")
    st = out["starts"]
    print(f"\ntrue quest-start requests: {st['count']} "
          f"({out['start_share_of_quest_advance_pct']} % of quest.advance rows)")
    print(f"  distinct quest ids ........ {st['distinct_ids']}  {st['ids']}")
    print(f"  ids the client never reported {st['never_reported']}")
    print(f"  honoured (active within {ns.window} steps) {st['honoured']}"
          f"  (outside a dialog: {st['honoured_outside_dialog']})")
    print(f"  sent while a dialog was open {st['while_dialog_open']} "
          f"(the page answers reply 0 there, it does not accept)")
    print(f"  immediate repeats ......... {st['repeats_of_same_id']}")
    print(f"  page answers .............. {st['page_answers']}")
    for row in st["rows"][:12]:
        print(f"    {row['iso']} {row['session']} quest={row['quest_id']} "
              f"reported={row['reported']} honoured={row['honoured']} "
              f"dialog={row['in_dialog']} ok={row['ok']} page={row['page']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
