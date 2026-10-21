"""Merge teacher, SFT, and synthetic rows into one student SFT corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.tool_dataset import build_example, read_jsonl, validate_call  # noqa: E402


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row_key(row: Dict[str, Any]) -> str:
    return hashlib.sha1(
        f"{str(row.get('goal', '')).strip().lower()}\x1f{row.get('tool')}\x1f"
        f"{_canonical(row.get('args') or {})}".encode("utf-8")
    ).hexdigest()


def _from_record(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if record.get("kind") not in (None, "step"):
        return None
    if record.get("ok") is False:
        return None
    if not record.get("goal") or not record.get("tool"):
        return None
    example = build_example({**record, "kind": "step", "ok": True})
    if example is None:
        return None
    example["source"] = record.get("source") or example.get("source") or "teacher"
    return example


def _from_sft(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    messages = row.get("messages") or []
    user = next((m.get("content") for m in messages if m.get("role") == "user"), None)
    assistant = next(
        (m.get("content") for m in messages if m.get("role") == "assistant"), None)
    if not user or not assistant:
        return None
    try:
        payload = json.loads(assistant)
    except json.JSONDecodeError:
        return None
    tool = payload.get("tool")
    args = payload.get("args") or {}
    if not isinstance(tool, str):
        return None
    ok, validated = validate_call(tool, args)
    if not ok:
        return None
    goal = str(user).split("\n", 1)[0]
    if goal.startswith("Goal: "):
        goal = goal[6:]
    return {
        "id": row.get("id") or _row_key({"goal": goal, "tool": tool, "args": validated}),
        "goal": goal,
        "tool": tool,
        "args": validated,
        "source": row.get("source") or "sft",
        "instance": row.get("instance"),
        "messages": [
            {"role": "system", "content": (messages[0].get("content") if messages else "")},
            {"role": "user", "content": str(user)},
            {"role": "assistant", "content": _canonical({"tool": tool, "args": validated})},
        ],
    }


def _from_case(case: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    expected = case.get("expected") or {
        "tool": case.get("tool"), "args": case.get("args") or {}}
    return _from_record({
        "kind": "step",
        "ok": True,
        "goal": case.get("goal"),
        "tool": expected.get("tool"),
        "args": expected.get("args") or {},
        "source": case.get("source") or "bench_variant",
    })


def _load_rows(path: Path, kind: str) -> Iterable[Dict[str, Any]]:
    for row in read_jsonl(path):
        example = _from_sft(row) if kind == "sft" else (
            _from_case(row) if kind == "case" else _from_record(row))
        if example:
            yield example


def build(args: argparse.Namespace) -> Dict[str, Any]:
    examples: List[Dict[str, Any]] = []
    seen: set[str] = set()
    stats: Counter = Counter()
    for kind, path in args.input:
        if not path.exists():
            print(f"skip missing {path}")
            continue
        for example in _load_rows(path, kind):
            key = _row_key(example)
            if key in seen:
                stats["duplicates"] += 1
                continue
            seen.add(key)
            examples.append(example)
            stats[f"kept_{kind}"] += 1
            stats[f"source_{example.get('source') or 'unknown'}"] += 1

    if args.shuffle_seed:
        examples.sort(key=lambda row: hashlib.sha1(
            (str(row["id"]) + str(args.shuffle_seed)).encode("utf-8")).hexdigest())
    else:
        examples.sort(key=lambda row: row["id"])

    train: List[Dict[str, Any]] = []
    val: List[Dict[str, Any]] = []
    for example in examples:
        bucket = int(hashlib.sha1(str(example["id"]).encode("utf-8")).hexdigest(), 16) % 100
        (val if bucket < args.val_percent else train).append(example)

    args.out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("train.jsonl", train), ("val.jsonl", val)):
        path = args.out / name
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    tool_counts = Counter(row["tool"] for row in examples)
    report = {
        "inputs": [{"kind": kind, "path": str(path)} for kind, path in args.input],
        "examples": len(examples),
        "train": len(train),
        "val": len(val),
        "val_percent": args.val_percent,
        "tools": dict(sorted(tool_counts.items())),
        "stats": dict(sorted(stats.items())),
    }
    (args.out / "stats.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("examples", "train", "val", "tools")},
                     ensure_ascii=False, indent=2))
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "core_sft")
    parser.add_argument("--val-percent", type=int, default=10)
    parser.add_argument("--shuffle-seed", default="core-skill-v1")
    parser.add_argument("--distill", type=Path,
                        default=ROOT / "data" / "distill" / "core_skill_v1.jsonl")
    parser.add_argument("--sft", type=Path, nargs="*", default=[
        ROOT / "data" / "agent_sft" / "train.jsonl",
        ROOT / "data" / "agent_sft" / "val.jsonl",
    ])
    parser.add_argument("--trajectories", type=Path, nargs="*", default=[
        ROOT / "trajectories" / "synthetic.jsonl",
    ])
    parser.add_argument("--cases", type=Path, nargs="*", default=[])
    args = parser.parse_args(argv)
    args.input = [("distill", args.distill)]
    args.input += [("sft", path) for path in args.sft]
    args.input += [("trajectory", path) for path in args.trajectories]
    args.input += [("case", path) for path in args.cases]
    build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
