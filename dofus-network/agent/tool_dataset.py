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
    "nav.goto(x, y, zaap, npc, map_id, lang=fr, from_x, from_y) - travel; exactly one target.\n"
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


def _observation_text(observation: Any, limit: int = 1200) -> Optional[str]:
    if not isinstance(observation, dict) or not observation:
        return None
    text = json.dumps(observation, ensure_ascii=False, sort_keys=True)
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
