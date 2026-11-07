"""Build the entity-pointer dataset from live step logs.

The transition corpus (`data/transitions`) stores the 42-float feature vector and
the moved/progress labels, but it deliberately drops the live entity rows. The
pointer model needs those rows: NPCs, interactives, mobs, dialog replies,
inventory and prices, plus the action's chosen pointer.

This builder reuses the shipped live-transition segmentation and label rules, so
the pointer set and the 42-float set cannot disagree about what "moved" means.
Output is one JSON object per executed step with a before observation and an
after observation:

    data/entity_pointer/{train,val,test}.jsonl
    data/entity_pointer/manifest.json

Split is by segment (`build_live_transitions.split_of`), never by row.
Only source-tagged live steps with explicit observation/outcome pairs are included.
Writes require 2,000 action rows, 50 action-progress rows and 10 rows per tool;
--check reports coverage without replacing an existing dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

import build_live_transitions as blt  # noqa: E402


def _limit(rows: Any, n: int) -> List[Any]:
    if not isinstance(rows, list):
        return []
    return rows[:n]


def _quest(before: Dict[str, Any]) -> Dict[str, Any]:
    obj = before.get("current_objective")
    if not isinstance(obj, dict):
        ready = before.get("ready_objectives")
        if isinstance(ready, list):
            obj = next((row for row in ready if isinstance(row, dict)), None)
    if not isinstance(obj, dict):
        obj = {}
    active = before.get("quests_active")
    quest_id = obj.get("quest_id")
    if quest_id is None and isinstance(active, list) and active:
        first = active[0] if isinstance(active[0], dict) else {}
        quest_id = first.get("id") or first.get("quest_id")
    return {
        "quest_id": quest_id,
        "step_id": obj.get("step_id"),
        "objective_id": obj.get("objective_id"),
        "type_id": obj.get("type_id", obj.get("objective_type")),
        "text": obj.get("text") or obj.get("name") or obj.get("label"),
        "params": obj.get("params") if isinstance(obj.get("params"), dict) else {},
        "map_id": obj.get("map_id"),
        "active": _limit(active, 12) if isinstance(active, list) else [],
    }


def _observed_hints(path: Optional[Path]) -> Dict[tuple, Dict[str, Any]]:
    """Client-reported objective text keyed by (quest_id, objective_id)."""
    if path is None or not path.is_file():
        return {}
    out: Dict[tuple, Dict[str, Any]] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
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
                try:
                    key = (int(row.get("quest_id")), int(row.get("objective_id")))
                except (TypeError, ValueError):
                    continue
                out[key] = row
    except OSError:
        return {}
    return out


def _entities(before: Optional[Dict[str, Any]],
              hint: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not isinstance(before, dict):
        return {}
    map_block = before.get("map") if isinstance(before.get("map"), dict) else {}
    dialog = before.get("dialog") if isinstance(before.get("dialog"), dict) else {}
    inventory = before.get("inventory") if isinstance(before.get("inventory"), dict) else {}
    character = before.get("character") if isinstance(before.get("character"), dict) else {}
    position = before.get("position") if isinstance(before.get("position"), dict) else {}
    hp = before.get("hp") if isinstance(before.get("hp"), dict) else {}
    me = {
        "id": character.get("id"),
        "name": character.get("name"),
        "level": character.get("level"),
        "breed": character.get("breed"),
        "cell": position.get("cell"),
        "map_id": position.get("map_id"),
        "x": position.get("x"),
        "y": position.get("y"),
        "hp": hp.get("life"),
        "max_hp": hp.get("max"),
        "ap": hp.get("ap"),
        "mp": hp.get("mp"),
        "kamas": inventory.get("kamas"),
        "in_world": before.get("in_world"),
        "in_fight": before.get("in_fight"),
        "in_dialog": before.get("in_dialog"),
    }
    npcs = []
    for row in _limit(map_block.get("npcs"), 40):
        if not isinstance(row, dict):
            continue
        npcs.append({
            "actor_id": row.get("actor_id", row.get("contextual_id")),
            "template_id": row.get("template_id", row.get("npc_id")),
            "name": row.get("name"),
            "cell": row.get("cell"),
            "dialog_available": bool(row.get("dialog_available", True)),
        })
    interactives = []
    for row in _limit(map_block.get("interactives"), 40):
        if not isinstance(row, dict):
            continue
        interactives.append({
            "element_id": row.get("elem_id", row.get("element_id")),
            "skill_uid": row.get("skill_uid", row.get("skill_instance_uid")),
            "cell": row.get("cell"),
            "kind": row.get("name") or row.get("kind"),
        })
    mobs = []
    for row in _limit(map_block.get("groups"), 20):
        if not isinstance(row, dict):
            continue
        mobs.append({
            "group_id": row.get("group_id", row.get("id")),
            "main_mob_id": row.get("main_mob_id", row.get("mainCreatureGenericId")),
            "level": row.get("level", row.get("total_level")),
            "cell": row.get("cell"),
            "members": _limit(row.get("members"), 12),
        })
    replies = []
    for row in _limit(dialog.get("replies"), 20):
        if not isinstance(row, dict):
            continue
        replies.append({
            "id": row.get("id", row.get("reply_id")),
            "text": row.get("text"),
            "is_quest_offer": bool(row.get("is_quest_offer", False)),
        })
    items = []
    for row in _limit(inventory.get("items"), 120):
        if not isinstance(row, dict):
            continue
        items.append({
            "gid": row.get("gid", row.get("objectGID")),
            "qty": row.get("qty", row.get("quantity")),
            "object_uid": row.get("object_uid", row.get("objectUID")),
        })
    prices = []
    for row in _limit(before.get("prices"), 50):
        if isinstance(row, dict):
            prices.append(row)
    return {
        "self": me,
        "quest": _quest_with_hint(before, hint),
        "npcs": npcs,
        "interactives": interactives,
        "mobs": mobs,
        "dialog": {
            "open": bool(dialog.get("open")),
            "npc_id": dialog.get("npc_id"),
            "question": dialog.get("question"),
            "replies": replies,
        },
        "inventory": {
            "items": items,
            "equipped": _limit(inventory.get("equipped"), 24),
            "presets": _limit(inventory.get("presets"), 12),
        },
        "prices": prices,
        "exits": _limit(map_block.get("exits"), 12),
    }


def _quest_with_hint(before: Dict[str, Any],
                     hint: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    quest = _quest(before)
    if not isinstance(hint, dict):
        return quest
    text = (hint.get("text") or hint.get("step_description")
            or hint.get("step_name"))
    if text:
        quest["text"] = text
    for key in ("quest_name", "step_name", "step_description", "type_id",
                "params", "map_id"):
        if hint.get(key) is not None and quest.get(key) in (None, "", {}):
            quest[key] = hint.get(key)
    quest["observed_from"] = "client_catalog"
    return quest


def _chosen(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    args = args or {}
    if tool == "tap.interactive":
        return {"kind": "interactive",
                "element_id": args.get("element_id", args.get("elem_id")),
                "skill_uid": args.get("skill_id", args.get("skill_uid"))}
    if tool == "inv.equip":
        return {"kind": "preset", "preset_id": args.get("preset_id", args.get("preset")),
                "object_uid": args.get("object_uid")}
    if tool == "hdv.buy":
        return {"kind": "listing", "uid": args.get("uid", args.get("listing_uid")),
                "gid": args.get("gid"), "item": args.get("item")}
    if tool == "quest.advance":
        if args.get("npc_id") is not None:
            return {"kind": "npc", "npc_id": args.get("npc_id")}
        if args.get("reply") is not None or args.get("reply_text") is not None:
            return {"kind": "reply", "reply": args.get("reply"),
                    "text": args.get("reply_text")}
        if args.get("objective_id") is not None:
            return {"kind": "objective", "objective_id": args.get("objective_id")}
        return {"kind": "quest", "quest_id": args.get("quest_id")}
    if tool == "nav.goto":
        if args.get("map_id") is not None:
            return {"kind": "map", "map_id": args.get("map_id")}
        if args.get("direction") is not None:
            return {"kind": "direction", "direction": args.get("direction")}
        return {"kind": "npc", "npc_id": args.get("npc_id")}
    if tool == "fight.join":
        return {"kind": "group", "group_id": args.get("group_id"),
                "monster": args.get("monster")}
    if tool == "farm.start":
        return {"kind": "farm", "monster": args.get("monster"),
                "resource": args.get("resource")}
    if tool == "chat.say":
        return {"kind": "chat", "text": args.get("text")}
    return {"kind": "tool", "tool": tool}


def build(paths: Iterable[Path], gap_s: float,
          observed: Optional[Path] = None) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    hints = _observed_hints(observed)
    streams = defaultdict(list)
    fingerprints = set()
    duplicates = 0
    sources = []
    for path in sorted({Path(path).resolve() for path in paths}):
        if not path.is_file():
            continue
        sources.append(str(path))
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or row.get("source") != "live":
                    continue
                if row.get("kind") != "step" or not row.get("tool"):
                    continue
                fingerprint = hashlib.sha256(json.dumps(
                    row, sort_keys=True, separators=(",", ":")).encode("utf-8")).digest()
                if fingerprint in fingerprints:
                    duplicates += 1
                    continue
                fingerprints.add(fingerprint)
                key = (str(row.get("instance") or "default"), str(row.get("session") or ""))
                streams[key].append(row)
    out: List[Dict[str, Any]] = []
    segments = []
    for stream in streams.values():
        segments.extend(blt.segments(sorted(stream, key=blt._row_ts), "live", gap_s))
    for segment in segments:
        labels = blt.label_steps(segment)
        for step, label in zip(segment["steps"], labels):
            before = step.get("observation")
            after = blt._after_obs(step)
            if not isinstance(before, dict) or not before or not isinstance(after, dict):
                continue
            base_quest = _quest(before)
            key = None
            try:
                key = (int(base_quest.get("quest_id")),
                       int(base_quest.get("objective_id")))
            except (TypeError, ValueError):
                key = None
            hint = hints.get(key) if key is not None else None
            entities = _entities(before, hint)
            if not entities:
                continue
            split = blt.split_of(label["segment"])
            if split not in ("train", "val", "test"):
                split = "train"
            out.append({
                "id": label["id"],
                "segment": label["segment"],
                "split": split,
                "source": label["source"],
                "goal": label["goal"],
                "instance": label["instance"],
                "t": label["t"],
                "tool": label["tool"],
                "args": label["args"],
                "ok": label["ok"],
                "error": label["error"],
                "was_read": label["was_read"],
                "moved": label["moved"],
                "progress": label["progress"],
                "changed": label["changed"],
                "chosen": _chosen(label["tool"], label["args"]),
                "entities": entities,
                "observed_hint": bool(hint),
            })
    counts = Counter(row["split"] for row in out)
    pointers = Counter(row["chosen"].get("kind") for row in out)
    tools = Counter(row["tool"] for row in out)
    manifest = {
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        "sources": sources,
        "rows": len(out),
        "splits": dict(counts),
        "pointer_kinds": dict(pointers),
        "tools": dict(tools),
        "moved": sum(1 for row in out if row["moved"]),
        "progress": sum(1 for row in out if row["progress"]),
        "read_rows": sum(1 for row in out if row["was_read"]),
        "action_rows": sum(1 for row in out if not row["was_read"]),
        "action_progress_rows": sum(1 for row in out if not row["was_read"] and row["progress"]),
        "duplicate_steps": duplicates,
        "observed_hints": sum(1 for row in out if row.get("observed_hint")),
        "segments": len({row["segment"] for row in out}),
    }
    manifest["coverage_gate"] = coverage_gate(manifest)
    return out, manifest


def coverage_gate(manifest: Dict[str, Any]) -> Dict[str, Any]:
    from agent.schema import tool_names

    tools = manifest["tools"]
    undercovered = {name: tools.get(name, 0) for name in tool_names() if tools.get(name, 0) < 10}
    actions = manifest["action_rows"]
    progress = manifest["action_progress_rows"]
    return {
        "passed": actions >= 2000 and progress >= 50 and not undercovered,
        "action_rows": actions,
        "action_progress_rows": progress,
        "minimum_action_rows": 2000,
        "minimum_action_progress_rows": 50,
        "minimum_rows_per_tool": 10,
        "undercovered_tools": undercovered,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", nargs="*", default=None,
                        help="JSONL step logs; default: all per-instance trajectories/*.jsonl")
    parser.add_argument("--check", action="store_true", help="report coverage without writing a dataset")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "entity_pointer")
    parser.add_argument("--observed", type=Path,
                        default=REPO_ROOT / "data" / "entity_pointer" / "observed_quests.jsonl",
                        help="client-observed objective text used to enrich quest.text")
    parser.add_argument("--segment-gap-s", type=float, default=120.0)
    args = parser.parse_args(argv)
    if args.log:
        paths = [Path(p) for p in args.log]
    else:
        paths = sorted((REPO_ROOT / "trajectories").glob("*.jsonl"))
    if not paths:
        print("no step logs found", file=sys.stderr)
        return 2
    rows, manifest = build(paths, args.segment_gap_s, args.observed)
    gate = manifest["coverage_gate"]
    print(json.dumps(gate, sort_keys=True))
    if not gate["passed"]:
        print("coverage gate failed; existing dataset unchanged")
        return 1
    if args.check:
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    sinks = {}
    try:
        for name in ("train", "val", "test"):
            sinks[name] = (args.out / (name + ".jsonl")).open("w", encoding="utf-8")
        for row in rows:
            sinks[row["split"]].write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    finally:
        for handle in sinks.values():
            handle.close()
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True),
                                            encoding="utf-8")
    print("entity-pointer rows %d  splits %s  moved %d  progress %d  reads %d"
          % (manifest["rows"], manifest["splits"], manifest["moved"],
             manifest["progress"], manifest["read_rows"]))
    print("pointer kinds:", manifest["pointer_kinds"])
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
