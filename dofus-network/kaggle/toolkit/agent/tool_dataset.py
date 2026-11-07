"""Tool-use SFT dataset builder.

Turns trajectory JSONL (``agent/recorder.py``) into supervised examples for the
100M policy: a system prompt carrying the tool contract, the player goal (plus
the observation when one was captured), and the assistant completion — a single
canonical tool call::

    {"tool": "quest.list", "args": {"area": "Astrub", "limit": 10}}

Rules:

* only successful ``step`` records with a real tool and a non-empty goal become
  targets; failures and unresolved goals are counted (they are eval/GRPO
  material, not SFT targets);
* the assistant target is tool-first JSON (``{"tool": ..., "args": ...}``) so
  the student commits to an action before generating arguments; dedup ids use
  sorted JSON so the corpus is byte-stable;
* ``SYSTEM_PROMPT`` is the full contract; ``SYSTEM_PROMPT_COMPACT`` is the
  shorter contract the 100M student trains and is served with;
* deduplication is on (normalised goal, tool, canonical args);
* the train/val split is a deterministic hash of the example id, so re-running
  with the same seed never reshuffles an existing training set.

Usage (from ``dofus-network``)::

    python agent/tool_dataset.py --trajectories trajectories/collector.jsonl
    python agent/tool_dataset.py --out data/agent_sft --val-ratio 0.1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.schema import TOOL_SCHEMA, validate_call  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "data" / "agent_sft"
DEFAULT_TRAJECTORIES = REPO_ROOT / "trajectories"


def _param_signature(name: str, params: Dict[str, Dict[str, Any]]) -> str:
    parts: List[str] = []
    for key, spec in params.items():
        label = f"{key}:{spec.get('type', 'str')}"
        enum = spec.get("enum")
        if enum:
            label += "[" + "|".join(str(item) for item in enum) + "]"
        if spec.get("required"):
            label += "*"
        elif spec.get("default") is not None:
            default = spec["default"]
            if isinstance(default, bool):
                default = str(default).lower()
            label += f"={default}"
        parts.append(label)
    return ", ".join(parts)


def system_prompt() -> str:
    """The exact system prompt the student is trained and served with."""
    lines = [
        "You are the Dofus Touch farm agent. Translate the player's goal into "
        "exactly one tool call.",
        "",
        "Rules:",
        '- Reply with one JSON object and nothing else: {"tool": "<name>", "args": {...}}.',
        "- Use only the tools below, only their documented arguments, and always "
        "include required arguments (marked *).",
        "- Understand English, French, Spanish and German; answer in the player's language.",
        "- Never invent game facts: use knowledge.search before stating one.",
        "- safe.stop is always allowed when the player asks to stop.",
        "",
        "Tools:",
    ]
    for spec in TOOL_SCHEMA["tools"]:
        signature = _param_signature(spec["name"], spec.get("params") or {})
        description = " ".join(str(spec.get("description", "")).split())
        lines.append(f"- {spec['name']}({signature}) — {description}")
    return "\n".join(lines)


SYSTEM_PROMPT = system_prompt()

# Compact contract for the 100M student: same tool names/arguments, shorter
# prose so sequences stay small and the model spends its capacity on the goal.
SYSTEM_PROMPT_COMPACT = (
    "You are the Dofus Touch farm agent. Convert the player's goal into exactly one tool call.\n"
    'Reply with one JSON object and nothing else: {"tool":"<name>","args":{...}}.\n'
    "Use only these tools and arguments; required args are marked *. Understand en/fr/es/de.\n"
    "Never invent game facts; use knowledge.search first. safe.stop is always allowed.\n\n"
    "Tools:\n"
    "knowledge.search(query*, kind=any, limit=5) - DofusDB lookup.\n"
    "nav.goto(x, y, zaap, npc, map_id, cell, direction, npc_id, lang=fr, from_x, from_y) "
    "- travel; map_id or direction reaches another map, cell is a spot on this one.\n"
    "nav.follow(name) - follow a character; empty = order giver.\n"
    "quest.list(area, level_min, level_max, dungeon_only=false, limit=10) - quests.\n"
    "quest.advance(quest_id*, step_id) - next actionable quest step.\n"
    "farm.start(monster, resource, zone, map_id, kill_count=100, duration_min, "
    "loot_filter, auto_sell=false) - farm loop.\n"
    "farm.stop() - stop the farm loop.\n"
    "fight.join(target) - join a fight.\n"
    "fight.pass() - pass the turn.\n"
    "inv.list(kind=all) - inventory.\n"
    "inv.sell(item, all=false, quantity, min_stock=1, price_rule=median_minus_1, "
    "price) - sell at market.\n"
    "hdv.scan(item*) - market prices.\n"
    "hdv.list(item*, quantity=1, price_rule=median_minus_1, price) - list on market.\n"
    "chat.say(text*, channel=say, target) - chat.\n"
    "state.snapshot() - state summary.\n"
    "safe.stop(reason) - emergency stop.\n"
)

SYSTEM_PROMPTS = {"full": SYSTEM_PROMPT, "compact": SYSTEM_PROMPT_COMPACT}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _norm_goal(goal: str) -> str:
    return " ".join(str(goal).lower().split())


def _example_id(goal: str, tool: str, args: Dict[str, Any]) -> str:
    raw = f"{_norm_goal(goal)}\x1f{tool}\x1f{_canonical(args)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _obs_first(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _obs_brief(value: Any, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return text if len(text) <= limit else text[:max(limit - 1, 0)] + "…"


def _obs_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].replace('"', "'")


def _obs_objective(entry: Dict[str, Any], detail: int = 200) -> str:
    """One objective as `quest Q objective O type T status S map M params "text"`.

    Step objectives carry `id` and inherit their quest from the current objective,
    so both spellings are read here rather than at each call site.
    """
    label = []
    if entry.get("quest_id") is not None:
        label.append(f"quest {entry.get('quest_id')}")
    objective_id = _obs_first(entry.get("objective_id"), entry.get("id"))
    if objective_id is not None:
        label.append(f"objective {objective_id}")
    bits = [" ".join(label) if label else "objective"]
    if entry.get("type_id") is not None:
        bits.append(f"type {entry.get('type_id')}")
    if entry.get("status") is not None:
        bits.append(f"status {entry.get('status')}")
    if entry.get("map_id") is not None:
        bits.append(f"map {entry.get('map_id')}")
    params = entry.get("params")
    if isinstance(params, dict) and params:
        bits.append("params " + _obs_brief(params, max(detail // 2, 60)))
    if entry.get("text"):
        bits.append(f'text "{_obs_text(entry.get("text"), detail)}"')
    return " ".join(bits)


def _observation_text(observation: Any, limit: int = 1200) -> Optional[str]:
    """Render the decision-relevant parts of an observation first.

    The snapshot is a wide document: the map, the exits, the NPCs, every quest.
    JSON-dumping it and cutting at a character budget put `quests_active` and
    `inventory` ahead of the objective text and the exits — the two things a policy
    actually needs to pick an action — so they fell off the end. Here the order is
    deliberate: where the character is, what the objective asks for, what the map
    offers, and only then everything else.
    """
    if not isinstance(observation, dict) or not observation:
        return None

    lines: List[str] = []
    rendered: set = set()

    position = observation.get("position") if isinstance(observation.get("position"), dict) else {}
    character = observation.get("character") if isinstance(observation.get("character"), dict) else {}
    map_block = observation.get("map") if isinstance(observation.get("map"), dict) else {}

    map_id = _obs_first(observation.get("map_id"), position.get("map_id"), map_block.get("map_id"))
    cell = _obs_first(observation.get("cell"), position.get("cell"), map_block.get("cell"))
    x = _obs_first(position.get("x"), map_block.get("x"))
    y = _obs_first(position.get("y"), map_block.get("y"))
    where = [f"map {map_id}" if map_id is not None else "map unknown"]
    if cell is not None:
        where.append(f"cell {cell}")
    if x is not None and y is not None:
        where.append(f"at [{x},{y}]")
    if observation.get("in_world") is not None:
        where.append(f"in_world {bool(observation.get('in_world'))}")
    lines.append("where: " + " ".join(where))
    rendered.update(("map_id", "cell", "position", "map", "in_world"))

    level = _obs_first(observation.get("level"), character.get("level"))
    hp = observation.get("hp") if isinstance(observation.get("hp"), dict) else {}
    status = []
    if level is not None:
        status.append(f"level {level}")
    if hp:
        status.append(f"hp {hp.get('life')}/{hp.get('max')}")
        if hp.get("ap") is not None:
            status.append(f"ap {hp.get('ap')}")
        if hp.get("mp") is not None:
            status.append(f"mp {hp.get('mp')}")
    for key in ("in_fight", "in_dialog"):
        if observation.get(key) is not None:
            status.append(f"{key} {bool(observation.get(key))}")
    if observation.get("kamas") is not None:
        status.append(f"kamas {observation.get('kamas')}")
    if status:
        lines.append("character: " + " ".join(status))
    rendered.update(("level", "character", "hp", "in_fight", "in_dialog", "kamas"))

    objective = observation.get("current_objective")
    if isinstance(objective, dict) and objective:
        lines.append("objective: " + _obs_objective(objective))
        rendered.add("current_objective")

    ready = [entry for entry in (observation.get("ready_objectives") or [])
             if isinstance(entry, dict)]
    if ready:
        shown = "; ".join(_obs_objective(entry, 120) for entry in ready[:2])
        if len(ready) > 2:
            shown += f"; +{len(ready) - 2} more"
        lines.append("ready to hand in: " + shown)
    rendered.add("ready_objectives")

    step_objectives = [entry for entry in (observation.get("step_objectives") or [])
                       if isinstance(entry, dict)]
    if step_objectives:
        shown = "; ".join(_obs_objective(entry, 100) for entry in step_objectives[:3])
        if len(step_objectives) > 3:
            shown += f"; +{len(step_objectives) - 3} more"
        lines.append("step objectives: " + shown)
    rendered.add("step_objectives")

    exits = [entry for entry in (map_block.get("exits") or []) if isinstance(entry, dict)]
    if exits:
        parts = []
        for entry in exits[:6]:
            cells = entry.get("cells") or []
            label = f"{entry.get('direction')}->{entry.get('map_id')}"
            if cells:
                label += " @[" + ",".join(str(c) for c in cells[:4]) + "]"
            parts.append(label)
        lines.append("exits: " + "; ".join(parts))
    elif isinstance(map_block.get("neighbours"), dict) and map_block["neighbours"]:
        lines.append("neighbours: " + _obs_brief(map_block["neighbours"], 160))

    npcs = [entry for entry in (map_block.get("npcs") or []) if isinstance(entry, dict)]
    if npcs:
        shown = "; ".join(f"npc {entry.get('npc_id')}@cell {entry.get('cell')}"
                          for entry in npcs[:8])
        if len(npcs) > 8:
            shown += f"; +{len(npcs) - 8} more"
        lines.append("npcs here: " + shown)

    interactives = [entry for entry in (map_block.get("interactives") or [])
                    if isinstance(entry, dict)]
    if interactives:
        shown = "; ".join(
            f"elem {entry.get('elem_id')}@cell {entry.get('cell')}"
            + (f" skill {entry.get('skill_uid')}" if entry.get("skill_uid") is not None else "")
            for entry in interactives[:6])
        if len(interactives) > 6:
            shown += f"; +{len(interactives) - 6} more"
        lines.append("interactives here: " + shown)

    last_nav = observation.get("last_nav")
    if isinstance(last_nav, dict) and last_nav:
        lines.append("last nav: " + _obs_brief(last_nav, 180))
        rendered.add("last_nav")

    inventory = observation.get("inventory")
    if isinstance(inventory, dict):
        items = [entry for entry in (inventory.get("items") or []) if isinstance(entry, dict)]
        if items:
            shown = ", ".join(f"{entry.get('gid')}x{entry.get('qty', 1)}"
                              for entry in items[:12])
            if len(items) > 12:
                shown += f", +{len(items) - 12} more"
            lines.append("inventory: " + shown)
        rendered.add("inventory")

    if observation.get("auto_fight") is not None:
        lines.append(f"auto_fight: {bool(observation.get('auto_fight'))}")
        rendered.add("auto_fight")

    # The quest graph is a wide document (G29): JSON-dumping it into the "other"
    # line would spend the whole 320-character budget on the catalogue and hide
    # knowledge, quests_active and the build stamp behind it. A compact census is
    # what a policy needs; the raw graph stays in the observation for the readers.
    graph = observation.get("quests_catalog")
    census = observation.get("quest_catalog_status")
    quests = objectives = 0
    if isinstance(graph, list):
        quests = len(graph)
        for quest in graph:
            if not isinstance(quest, dict):
                continue
            for step in quest.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                objectives += sum(1 for obj in (step.get("objectives") or [])
                                  if isinstance(obj, dict))
    elif isinstance(census, dict):
        try:
            quests = int(census.get("quests") or 0)
            objectives = int(census.get("objectives") or 0)
        except (TypeError, ValueError):
            quests = objectives = 0
    if quests or objectives:
        lines.append(f"quest graph: {quests} active quest(s), {objectives} objective(s)")
    rendered.add("quests_catalog")
    rendered.add("quest_catalog_status")

    rest = {key: value for key, value in observation.items() if key not in rendered}
    if rest:
        lines.append("other: " + _obs_brief(rest, 320))

    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def build_example(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One trajectory step -> one SFT example, or None when unusable."""
    if record.get("kind") != "step" or not record.get("ok"):
        return None
    tool = str(record.get("tool") or "")
    goal = str(record.get("goal") or "").strip()
    if not tool or tool == "none" or not goal:
        return None
    ok, validated = validate_call(tool, record.get("args") or {})
    if not ok:
        return None

    user = f"Goal: {goal}"
    observation = _observation_text(record.get("observation"))
    if observation:
        user += f"\nCurrent state: {observation}"

    # Tool first: the student commits to an action before generating arguments.
    # (Sorted keys are still used for dedup ids, not for the target text.)
    completion = json.dumps(
        {"tool": tool, "args": validated},
        ensure_ascii=False, separators=(",", ":"),
    )
    return {
        "id": _example_id(goal, tool, validated),
        "goal": goal,
        "tool": tool,
        "args": validated,
        "source": record.get("source") or "live",
        "instance": record.get("instance"),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
            {"role": "assistant", "content": completion},
        ],
    }


def build_examples(
    records: Iterable[Dict[str, Any]],
    sources: Sequence[str] = ("scripted", "live"),
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    allowed = {str(source) for source in sources}
    stats: Dict[str, Any] = Counter(
        records=0, steps=0, ok_steps=0, failed_steps=0, unresolved=0,
        skipped_source=0, skipped_goalless=0, invalid=0, duplicates=0,
    )
    tool_counts: Counter = Counter()
    examples: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for record in records:
        stats["records"] += 1
        kind = record.get("kind")
        if kind != "step":
            if kind == "goal_unresolved":
                stats["unresolved"] += 1
            continue
        stats["steps"] += 1
        if not record.get("ok"):
            stats["failed_steps"] += 1
            continue
        stats["ok_steps"] += 1
        if str(record.get("source") or "live") not in allowed:
            stats["skipped_source"] += 1
            continue
        if not str(record.get("goal") or "").strip():
            stats["skipped_goalless"] += 1
            continue
        example = build_example(record)
        if example is None:
            stats["invalid"] += 1
            continue
        if example["id"] in seen:
            stats["duplicates"] += 1
            continue
        seen.add(example["id"])
        examples.append(example)
        tool_counts[example["tool"]] += 1

    stats = dict(stats)
    stats["examples"] = len(examples)
    stats["tools"] = dict(sorted(tool_counts.items()))
    return examples, stats


def split_examples(
    examples: Sequence[Dict[str, Any]],
    val_ratio: float = 0.1,
    seed: int = 7,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Deterministic split: hash(id + seed), no reshuffling across runs."""
    ratio = min(max(float(val_ratio), 0.0), 1.0)
    ordered = sorted(
        examples,
        key=lambda item: hashlib.sha1(
            f"{seed}:{item['id']}".encode("utf-8")).hexdigest())
    val_count = int(round(len(ordered) * ratio))
    if ratio > 0 and len(ordered) >= 2 and val_count == 0:
        val_count = 1
    if val_count >= len(ordered) and len(ordered) >= 2:
        val_count = len(ordered) - 1
    return ordered[val_count:], ordered[:val_count]


def write_jsonl(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def trajectory_files(paths: Optional[Sequence[str]]) -> List[Path]:
    if paths:
        return [Path(path) for path in paths]
    return sorted(DEFAULT_TRAJECTORIES.glob("*.jsonl"))


def write_dataset(
    records: Iterable[Dict[str, Any]],
    out_dir: Path = DEFAULT_OUT,
    val_ratio: float = 0.1,
    seed: int = 7,
    sources: Sequence[str] = ("scripted", "live"),
) -> Dict[str, Any]:
    examples, stats = build_examples(records, sources=sources)
    train, val = split_examples(examples, val_ratio=val_ratio, seed=seed)
    out_dir = Path(out_dir)
    write_jsonl(out_dir / "train.jsonl", train)
    write_jsonl(out_dir / "val.jsonl", val)
    stats.update({
        "out_dir": str(out_dir),
        "train": len(train),
        "val": len(val),
        "val_ratio": val_ratio,
        "seed": seed,
        "sources": list(sources),
        "system_prompt_chars": len(SYSTEM_PROMPT),
    })
    (out_dir / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the tool-use SFT dataset")
    parser.add_argument("--trajectories", nargs="*", default=None,
                        help="trajectory JSONL files (default: trajectories/*.jsonl)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--sources", default="scripted,live",
                        help="comma-separated trajectory sources to keep")
    parser.add_argument("--samples", type=int, default=0,
                        help="print N example goals after building")
    args = parser.parse_args(argv)

    files = trajectory_files(args.trajectories)
    if not files:
        print(f"no trajectory files found under {DEFAULT_TRAJECTORIES}")
        return 1
    records: List[Dict[str, Any]] = []
    for path in files:
        if not path.exists():
            print(f"missing trajectory file: {path}")
            return 1
        records.extend(read_jsonl(path))

    sources = tuple(part.strip() for part in args.sources.split(",") if part.strip())
    stats = write_dataset(records, Path(args.out), args.val_ratio, args.seed, sources)
    print(f"trajectory files : {len(files)} ({len(records)} records)")
    print(f"steps ok/failed  : {stats['ok_steps']}/{stats['failed_steps']}")
    print(f"unresolved goals : {stats['unresolved']}")
    print(f"examples         : {stats['examples']} "
          f"(train {stats['train']} / val {stats['val']})")
    print(f"duplicates       : {stats['duplicates']}  invalid: {stats['invalid']}"
          f"  skipped source: {stats['skipped_source']}")
    print(f"tools            : {json.dumps(stats['tools'], ensure_ascii=True)}")
    print(f"written          : {stats['out_dir']}")

    if args.samples:
        for row in read_jsonl(Path(args.out) / "train.jsonl")[:args.samples]:
            print(f"  - [{row['tool']}] {row['goal']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
