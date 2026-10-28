"""Turn the live logs into real-outcome transitions: did the issued tool move the world?

Everything else in this repo labels decisions from the harness (`sim/tool_choice.py` forks a
model of the game) or from the rule that proposed the action. Neither is evidence about the
real client, and the measured failure is precisely that: of the world-changing steps in the
recorded baseline, only 4.5% changed anything. This builder makes the other half of the
supervision exist, without inventing a label:

* one row per executed step that has both a before observation and an after observation;
* the label is `agent.verifier.changed_keys` — the shipped definition of "the world moved",
  the same one the settle window uses, so the offline dataset and the live loop cannot drift;
* `progress` is the strict subset: a quest counter moved. That is the criterion the goal is
  judged on, and `moved` alone would count an HP tick as success;
* reads get their own flag, because a read is not allowed to look like progress;
* the 42-number state vector is rebuilt with the *live* extractor
  (`agent.features.objective_features`) and the `streak/last_ok/last_tool/last_note/obs_kind`
  context re-derived from the log, so a model trained on harness forks can be scored on these
  rows with identical features — the sim-to-live transfer number is only meaningful if the two
  sides use the same vector;
* the split is by **segment** (a contiguous run of one goal), never by row, so step *t* cannot
  appear in train while step *t+1* sits in val.

    python tools/build_live_transitions.py                 # all trajectories/*.jsonl
    python tools/build_live_transitions.py --out data/transitions --segment-gap-s 120
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.features import (FEATURE_COUNT, objective_features,  # noqa: E402
                            objective_type)
from agent.observe import flatten  # noqa: E402
from agent.verifier import (READ_ONLY_TOOLS, STATE_KEYS, changed_keys)  # noqa: E402

# The state keys that mean "the quest moved", as opposed to "something on screen ticked".
PROGRESS_KEYS = ("ready_objectives", "objectives_completed", "quests_active",
                 "quests_finished", "quest_steps")
# Anything here counts as the world having moved. hp/xp are deliberately excluded: they drift
# while a character stands still, and a label that fires on them would make every action look
# like progress.
WORLD_KEYS = ("cell", "map_id", "mapId", "in_dialog", "in_fight", "inventory_size",
              "kamas") + PROGRESS_KEYS

SPLIT_BUCKETS = (700, 150, 150)  # train, val, test, in permille of a segment id hash


def _int(value: Any) -> int:
    """The quest step index, the way `objective_features` expects it (not the log position)."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _row_ts(row: Dict[str, Any]) -> float:
    for key in ("ts",):
        value = row.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    iso = str(row.get("iso") or "")
    if iso:
        try:
            return time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
        except (ValueError, OverflowError):
            return 0.0
    return 0.0


def _after_obs(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The observation the client returned *for this directive*, if it logged one."""
    result = row.get("result")
    if isinstance(result, dict):
        snapshot = result.get("snapshot")
        if isinstance(snapshot, dict) and snapshot:
            return snapshot
    return None


def _objective_of(before: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], int]:
    quest_level = 0
    active = before.get("quests_active")
    if isinstance(active, list):
        for entry in active:
            if isinstance(entry, dict):
                try:
                    quest_level = int(entry.get("level") or 0)
                except (TypeError, ValueError):
                    quest_level = 0
                break
    obj = before.get("current_objective")
    if not isinstance(obj, dict):
        ready = before.get("ready_objectives")
        if isinstance(ready, list):
            obj = next((r for r in ready if isinstance(r, dict)), None)
    return (obj if isinstance(obj, dict) else None), quest_level


def _obs_kind(row: Dict[str, Any]) -> str:
    """Stale/partial/fresh, from the fields the page itself reports."""
    source = str(row.get("observation_source") or "").lower()
    if "stale" in source:
        return "stale"
    if "partial" in source:
        return "partial"
    return "fresh"


def segments(rows: Iterable[Dict[str, Any]], source: str,
             gap_s: float) -> List[Dict[str, Any]]:
    """Contiguous (goal, session) runs; a gap, a restart or a new goal starts one."""
    out: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    last_ts = 0.0
    for row in rows:
        if row.get("kind") != "step" or not row.get("tool"):
            continue
        goal = " ".join(str(row.get("goal") or "").split())
        index = row.get("index")
        ts = _row_ts(row)
        restart = isinstance(index, int) and index == 0 and current is not None
        broken = (current is not None and gap_s > 0 and ts and last_ts
                  and (ts - last_ts) > gap_s)
        if current is None or broken or (current["goal"] != goal) or restart:
            current = {"source": source, "goal": goal, "steps": [],
                       "instance": str(row.get("instance") or "default")}
            out.append(current)
        current["steps"].append(row)
        if ts:
            last_ts = ts
    return [segment for segment in out if segment["steps"]]


def label_steps(segment: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One labelled transition per step, with the ctx re-derived from the log itself."""
    rows = segment["steps"]
    out: List[Dict[str, Any]] = []
    streak = 0
    last_ok: Optional[bool] = None
    last_tool: Optional[str] = None
    last_note: Optional[str] = None
    carried_after: Optional[Dict[str, Any]] = None
    for position, row in enumerate(rows):
        before = row.get("observation")
        if not isinstance(before, dict):
            before = carried_after
        after = _after_obs(row) or carried_after
        tool = str(row.get("tool") or "")
        ok = bool(row.get("ok"))
        error = row.get("error") if isinstance(row.get("error"), str) else None
        ctx = {"streak": streak, "last_ok": last_ok, "last_tool": last_tool,
               "last_note": error or last_note, "obs_kind": _obs_kind(row)}
        obj, quest_level = _objective_of(before or {})
        x = None
        x_source = None
        if isinstance(before, dict) and obj is not None:
            try:
                # flatten() first, exactly as QuestPlanner.choose does: the featuriser reads the
                # flat keys (`map_id`, `inventory_size`, ...), and a live snapshot nests them.
                # Featurising the raw page shape instead produced a state vector the model had
                # never seen, and the first transfer run predicted gains of ~95,000.
                candidate = objective_features(obj, quest_level,
                                               _int(obj.get("step")),
                                               flatten(before), ctx)
                if len(candidate) == FEATURE_COUNT:
                    x = [round(float(v), 6) for v in candidate]
                    x_source = "objective_features"
            except Exception:  # noqa: BLE001 - a missing field must not lose the label
                x = None
        changed = changed_keys(before, after) if (before and after) else []
        progress = sorted(set(changed) & set(PROGRESS_KEYS))
        world = sorted(set(changed) & set(WORLD_KEYS))
        flat = flatten(after) if isinstance(after, dict) else {}
        out.append({
            "id": "%s#%d" % (segment_key(segment), position),
            "segment": segment_key(segment),
            "source": segment["source"],
            "goal": segment["goal"],
            "instance": segment["instance"],
            "t": position,
            "tool": tool,
            "args": row.get("args") if isinstance(row.get("args"), dict) else {},
            "ok": ok,
            "error": error,
            "latency_ms": row.get("latency_ms"),
            "objective_type": objective_type(obj) if obj else None,
            "was_read": tool in READ_ONLY_TOOLS,
            "has_pair": bool(before and after),
            "changed": changed,
            "moved": bool(world),
            "progress": bool(progress),
            "progress_keys": progress,
            "moved_keys": world,
            "objectives_completed": flat.get("objectives_completed"),
            "x": x,
            "x_source": x_source,
        })
        streak = streak + 1 if not ok else 0
        last_ok = ok
        last_tool = tool
        if after:
            carried_after = after
    return out


def segment_key(segment: Dict[str, Any]) -> str:
    stamp = str(segment["steps"][0].get("iso") or segment["steps"][0].get("ts") or "n")
    digest = hashlib.sha1(("%s|%s|%s|%s" % (segment["source"], segment["instance"],
                                            segment["goal"], stamp)).encode("utf-8"))
    return digest.hexdigest()[:16]


def split_of(key: str) -> str:
    value = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:4], 16) % 1000
    if value < SPLIT_BUCKETS[0]:
        return "train"
    if value < SPLIT_BUCKETS[0] + SPLIT_BUCKETS[1]:
        return "val"
    return "test"


def plan_splits(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    """Assign whole segments to splits, balanced on *labelled actions* rather than on row count.

    A hash of the segment id keeps the split leak-free but not balanced: actions concentrate in a
    few long sessions, and the first version left the test split with 10 actions at a 50% move
    rate against 6% in val — a sample that can only produce a meaningless number. Segments are
    still never split across folds.
    """
    counts: Counter = Counter()
    for row in rows:
        if row.get("has_pair") and not row.get("was_read"):
            counts[row["segment"]] += 1
    total = sum(counts.values())
    if not total:
        return {row["segment"]: "train" for row in rows}
    target = {"train": SPLIT_BUCKETS[0] / 1000.0,
              "val": SPLIT_BUCKETS[1] / 1000.0,
              "test": SPLIT_BUCKETS[2] / 1000.0}
    held = {"train": 0, "val": 0, "test": 0}
    assign: Dict[str, str] = {}
    # biggest segments first, so the greedy fill cannot strand a fold
    for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        best = min(target, key=lambda name: ((held[name] + count) / total - target[name],
                                             name))
        assign[key] = best
        held[best] += count
    for row in rows:
        assign.setdefault(row["segment"], "train")
    return assign


def build(paths: Sequence, gap_s: float) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    transitions: List[Dict[str, Any]] = []
    seen: set = set()
    for path in paths:
        path = Path(path)
        rows: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    rows.append(parsed)
        for segment in segments(rows, path.name, gap_s):
            labelled = label_steps(segment)
            for row in labelled:
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                transitions.append(row)
    stats = summarise(transitions)
    return transitions, stats


def summarise(transitions: List[Dict[str, Any]]) -> Dict[str, Any]:
    usable = [r for r in transitions if r["has_pair"]]
    actions = [r for r in usable if not r["was_read"]]
    per_tool: Dict[str, Counter] = {}
    for row in actions:
        bucket = per_tool.setdefault(row["tool"], Counter())
        bucket["n"] += 1
        bucket["moved"] += 1 if row["moved"] else 0
        bucket["progress"] += 1 if row["progress"] else 0
        bucket["failed"] += 0 if row["ok"] else 1
    moved = sum(1 for r in actions if r["moved"])
    return {
        "rows": len(transitions),
        "with_observation_pair": len(usable),
        "segments": len({r["segment"] for r in transitions}),
        "split_rows": {},
        "reads": sum(1 for r in usable if r["was_read"]),
        "actions": len(actions),
        "actions_moved": moved,
        "action_move_rate": round(100.0 * moved / max(1, len(actions)), 2),
        "actions_progress": sum(1 for r in actions if r["progress"]),
        "per_tool": {tool: {"n": int(c["n"]),
                            "moved_pct": round(100.0 * c["moved"] / max(1, c["n"]), 1),
                            "progress_pct": round(100.0 * c["progress"] / max(1, c["n"]), 1),
                            "failed_pct": round(100.0 * c["failed"] / max(1, c["n"]), 1)}
                     for tool, c in sorted(per_tool.items(),
                                           key=lambda kv: -kv[1]["n"])},
        "x_coverage_pct": round(100.0 * sum(1 for r in usable if r["x"]) / max(1, len(usable)), 1),
        "objective_types": dict(Counter(str(r["objective_type"]) for r in usable).most_common(12)),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", nargs="*", default=None,
                    help="JSONL files; default: trajectories/*.jsonl")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "transitions")
    ap.add_argument("--segment-gap-s", type=float, default=120.0,
                    help="a longer silence starts a new segment (0 disables)")
    ap.add_argument("--json", action="store_true", help="print the manifest as JSON")
    args = ap.parse_args(argv)

    paths = [Path(p) for p in (args.log or sorted((REPO_ROOT / "trajectories").glob("*.jsonl")))]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        print("no such log file: %s" % ", ".join(str(m) for m in missing), file=sys.stderr)
        return 2
    if not paths:
        print("no logs to read", file=sys.stderr)
        return 2

    transitions, stats = build(paths, args.segment_gap_s)
    plan = plan_splits(transitions)
    stats["split_rows"] = dict(Counter(plan[row["segment"]] for row in transitions))
    stats["split_actions"] = dict(Counter(
        plan[row["segment"]] for row in transitions
        if row["has_pair"] and not row["was_read"]))
    args.out.mkdir(parents=True, exist_ok=True)
    sinks = {}
    try:
        for name in ("train", "val", "test"):
            sinks[name] = (args.out / ("%s.jsonl" % name)).open("w", encoding="utf-8")
        for row in transitions:
            sinks[plan[row["segment"]]].write(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    finally:
        for handle in sinks.values():
            handle.close()
    (args.out / "manifest.json").write_text(json.dumps(stats, indent=1, sort_keys=True),
                                            encoding="utf-8")
    if args.json:
        print(json.dumps(stats, indent=1, sort_keys=True))
    else:
        print("transitions %d (observation pairs %d, segments %d) -> %s"
              % (stats["rows"], stats["with_observation_pair"], stats["segments"], args.out))
        print("split %s  features on %.1f%% of pairs" % (stats["split_rows"],
                                                         stats["x_coverage_pct"]))
        print("actions %d  moved %d (%.1f%%)  progress %d  reads excluded %d"
              % (stats["actions"], stats["actions_moved"], stats["action_move_rate"],
                 stats["actions_progress"], stats["reads"]))
        for tool, cell in list(stats["per_tool"].items())[:8]:
            print("  %-16s n=%-6d moved %5.1f%%  progress %5.1f%%  failed %5.1f%%"
                  % (tool, cell["n"], cell["moved_pct"], cell["progress_pct"],
                     cell["failed_pct"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
