"""Knowledge-base coverage for quest objectives: can the planner resolve what it is told to do?

Why this exists: a live session reported "the AI does nothing" while the game was fine. The
planner's own reason was `quest mode: nothing plannable -- 0 ready objective(s), 0 active
quest(s), 49 retired` -- it examined every quest and retired all of them in one call. Retirement
is not a mood, it is a verdict about resolution, so the question became arithmetic: for each
objective the client actually has, does the knowledge base answer "who/where is this"?

The answer decides where the fix belongs. An objective whose entity id is missing from the KB
cannot be improved by any weight edit, prompt or policy: the data is not there. An objective that
resolves and is still mis-ranked IS a model problem. This script separates the two, in counts.

    python tools/kb_objective_coverage.py            # live client + KB
    python tools/kb_objective_coverage.py --offline  # KB only, from the observed quest log
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.knowledge_context import CanonicalStore, load_default_store  # noqa: E402

# Free-form objectives carry the interesting id in parameter0: an NPC, an interactive element or
# a creature. Everything else the planner needs (maps, items, monsters) is typed.
POINTER_PARAMS = ("parameter0", "parameter1", "param0", "npc_id", "object_id", "item_id")


def _iter_objectives(catalog: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Accept both shapes the quest data really comes in.

    The client's `quests_catalog` is nested (quest -> steps -> objectives); the recorded
    `observed_quests.jsonl` is FLAT, one line per objective already decoded. Reading only the
    nested form made this script report `0 objectives` and exit 0 -- a coverage report that
    covered nothing, which is the exact way a measurement tool becomes decoration.
    """
    out: List[Dict[str, Any]] = []
    for quest in catalog or []:
        if quest.get("objective_id") is not None or quest.get("params"):
            out.append({"quest_id": quest.get("quest_id"),
                        "objective_id": quest.get("objective_id"),
                        "type": quest.get("type") or "",
                        "params": quest.get("params") or {}})
            continue
        qid = quest.get("id") or quest.get("quest_id")
        for step in quest.get("steps") or []:
            for obj in step.get("objectives") or []:
                out.append({"quest_id": qid,
                            "objective_id": obj.get("id"),
                            "type": obj.get("type") or "",
                            "params": obj.get("params") or {}})
    return out


def _pointers(obj: Dict[str, Any]) -> List[Any]:
    params = obj.get("params") or {}
    return [params[k] for k in POINTER_PARAMS if params.get(k) not in (None, "", 0)]


def classify(store: CanonicalStore, objectives: List[Dict[str, Any]],
             live_ids: Optional[set] = None) -> Dict[str, Any]:
    """Resolve every objective and bucket the failures by what is actually missing."""
    rows: List[Dict[str, Any]] = []
    buckets: Counter = Counter()
    missing_entities: Counter = Counter()
    for obj in objectives:
        oid = obj.get("objective_id")
        try:
            resolved = store.resolve_objective(int(oid)) if oid is not None else None
        except (TypeError, ValueError):
            resolved = None
        pointers = _pointers(obj)
        entry: Dict[str, Any] = {
            "quest_id": obj.get("quest_id"),
            "objective_id": oid,
            "type": obj.get("type"),
            "pointers": pointers,
            "objective_resolved": bool(resolved),
            "pointers_resolved": {},
            "live": (live_ids is None) or (oid in live_ids),
        }
        if not resolved:
            buckets["objective_row_missing"] += 1
        for p in pointers:
            found: Any = None
            for kind in ("npc", "interactive", "creature", "object", "item"):
                try:
                    found = store.entity(kind, p)
                except TypeError:
                    try:
                        found = store.entity(p)
                    except Exception:  # noqa: BLE001 - a miss is a finding, not a crash
                        found = None
                    break
                except Exception:  # noqa: BLE001
                    found = None
                    continue
                if found:
                    break
            entry["pointers_resolved"][str(p)] = bool(found)
            if not found:
                buckets["pointer_unresolvable"] += 1
                missing_entities[str(p)] += 1
        if entry["objective_resolved"] and all(entry["pointers_resolved"].values()):
            buckets["fully_resolvable"] += 1
        rows.append(entry)
    return {
        "objectives": len(objectives),
        "buckets": dict(buckets),
        "rows": rows,
        "top_missing_pointers": missing_entities.most_common(12),
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--offline", action="store_true",
                      help="do not touch the live client; use data/knowledge/observed_quests.jsonl")
    args.add_argument("--instance", default="dofus-01")
    args.add_argument("--out", type=Path, default=None)
    ns = args.parse_args(argv)

    store = load_default_store()
    if store is None:
        print(json.dumps({"error": "no knowledge store at the default path"}))
        return 2

    objectives: List[Dict[str, Any]] = []
    live_ids: Optional[set] = None
    source = "offline"
    if not ns.offline:
        try:
            from agent.executor import ToolExecutor
            snap = ToolExecutor(recorder=None).snapshot(instance=ns.instance)
            if isinstance(snap, dict) and snap.get("quests_catalog"):
                objectives = _iter_objectives(snap["quests_catalog"])
                live_ids = {o.get("objective_id") for o in (snap.get("ready_objectives") or [])}
                live_ids |= {(snap.get("current_objective") or {}).get("objective_id")}
                source = "live:%s" % ns.instance
        except Exception as exc:  # noqa: BLE001 - fall back to the recorded log
            print("[coverage] live read failed (%s: %s); using the observed log"
                  % (type(exc).__name__, exc), file=sys.stderr)
    if not objectives:
        log = Path(__file__).resolve().parents[1] / "data" / "knowledge" / "observed_quests.jsonl"
        seen: set = set()
        if log.exists():
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                # Each recorded line IS one objective; the nested catalog form is also accepted
                # so a dump of either shape gives the same arithmetic.
                for obj in _iter_objectives([rec]) or _iter_objectives(rec.get("quests") or []):
                    key = (obj.get("quest_id"), obj.get("objective_id"))
                    if key in seen or obj.get("objective_id") is None:
                        continue
                    seen.add(key)
                    objectives.append(obj)
            source = "observed_quests.jsonl"

    # A coverage report that measured nothing is not a pass, and it must never be read as one.
    if not objectives:
        print(json.dumps({"error": "no objectives to score", "source": source,
                          "hint": "launch a client, or check data/knowledge/observed_quests.jsonl"},
                         indent=1))
        return 2

    report = classify(store, objectives, live_ids)
    report["source"] = source
    report["store"] = str(getattr(store, "path", "default"))
    if ns.out:
        ns.out.parent.mkdir(parents=True, exist_ok=True)
        ns.out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    slim = {k: report[k] for k in ("source", "objectives", "buckets", "top_missing_pointers")}
    print(json.dumps(slim, indent=1, default=str))
    # A coverage report that always exits 0 is a poster. Unresolvable pointers are the bug this
    # script was written to find, so they fail a gate that asks "can the planner act at all".
    return 1 if report["buckets"].get("pointer_unresolvable") else 0


if __name__ == "__main__":
    raise SystemExit(main())
