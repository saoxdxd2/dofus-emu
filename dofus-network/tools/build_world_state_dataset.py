"""Build the typed world-state dataset from trajectory logs.

The incumbent transition dataset (`data/transitions`) relies on 42-float hand-built
vectors that discard entity attributes, terrain walkability, NPC identity, interactive
skills, group composition, and quest structure.

This builder produces a dataset grounded in the closed-schema typed representation
(`agent.world_state.encode`), capturing:
- Global features (34 floats, including terrain walkability and player cell walkability)
- Typed entity/environment blocks (NPCs, interactives, groups, exits, doors, objectives, quests)
- Flattened 482-dimension substrate layout (`agent.world_state.flatten`)
- Action metadata (tool, args, repeat_streak)
- Supervised outcome labels (claim_met)
- Self-supervised transition targets (world_delta, 10 watched keys)

Output is split strictly by segment using temporal ordering (earliest segments -> train,
middle -> val, latest held-out slice -> test) to prevent future leakage.

    python tools/build_world_state_dataset.py --out data/world_state_dataset
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

from agent import world_state as ws
from agent.jsonio import utf8_console
from agent.state_features import world_delta

try:
    from build_live_transitions import _claim_of
except ImportError:
    from tools.build_live_transitions import _claim_of  # type: ignore


DEFAULT_OUT = REPO_ROOT / "data" / "world_state_dataset"
DEFAULT_TRAJ = REPO_ROOT / "trajectories"


def load_steps(paths: Sequence[Path]) -> List[Dict[str, Any]]:
    """Load action steps with valid observation dictionaries."""
    out: List[Dict[str, Any]] = []
    fingerprints = set()
    for path in sorted(paths):
        if not path.is_file():
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
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
                    if not row.get("tool") or not isinstance(row.get("observation"), dict):
                        continue
                    fp = (str(row.get("instance") or ""),
                          float(row.get("ts") or 0.0),
                          str(row.get("tool") or ""),
                          json.dumps(row.get("args") or {}, sort_keys=True))
                    if fp in fingerprints:
                        continue
                    fingerprints.add(fp)
                    out.append(row)
        except OSError:
            continue
    out.sort(key=lambda r: (str(r.get("instance") or ""), float(r.get("ts") or 0.0)))
    return out


def build_segments(steps: List[Dict[str, Any]], gap_s: float = 120.0) -> List[List[Dict[str, Any]]]:
    """Segment steps by instance boundary or silence gap."""
    segments: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    last_ts: Optional[float] = None
    for step in steps:
        ts = float(step.get("ts") or 0.0)
        inst = str(step.get("instance") or "default")
        if current:
            prev_inst = str(current[-1].get("instance") or "default")
            if inst != prev_inst or (last_ts is not None and ts - last_ts > gap_s):
                segments.append(current)
                current = []
        current.append(step)
        last_ts = ts
    if current:
        segments.append(current)
    return segments


def build_transition_rows(segments: List[List[Dict[str, Any]]],
                          split_policy: str = "temporal") -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build transition rows and assign temporal splits by segment."""
    raw_rows: List[Dict[str, Any]] = []
    seg_starts: Dict[str, float] = {}

    for seg_idx, seg in enumerate(segments):
        inst = str(seg[0].get("instance") or "default")
        start_ts = float(seg[0].get("ts") or 0.0)
        seg_id = f"{inst}:{int(start_ts)}:{seg_idx}"
        seg_starts[seg_id] = start_ts
        rules: Dict[str, int] = {}

        for i in range(len(seg) - 1):
            r = seg[i]
            nxt = seg[i + 1]
            tool = str(r.get("tool") or "")
            args = r.get("args") if isinstance(r.get("args"), dict) else {}
            obs_before = r.get("observation")
            obs_after = nxt.get("observation")
            if not isinstance(obs_before, dict) or not isinstance(obs_after, dict):
                continue

            claim, met = _claim_of(tool, args, obs_before, obs_after)
            if met is None:
                continue

            rule_key = tool + "|" + json.dumps(args, sort_keys=True)
            streak = rules.get(rule_key, 0)
            rules[rule_key] = 0 if met else streak + 1

            ts = float(r.get("ts") or 0.0)
            day = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).date().isoformat() if ts > 0 else "unknown"

            encoded_state = ws.encode(ws.from_observation(obs_before))
            delta = world_delta(obs_before, obs_after)

            raw_rows.append({
                "id": f"{seg_id}:{i}",
                "segment": seg_id,
                "ts": ts,
                "day": day,
                "tool": tool,
                "args": args,
                "claim_met": bool(met),
                "repeat_streak": streak,
                "world_state": encoded_state,
                "world_delta": delta,
            })

    # Temporal split assignment across segments
    ordered_segs = sorted(seg_starts.keys(), key=lambda k: (seg_starts[k], k))
    n_segs = len(ordered_segs)
    seg_split: Dict[str, str] = {}

    if split_policy == "temporal" and n_segs >= 3:
        n_test = max(1, int(round(0.15 * n_segs)))
        n_val = max(1, int(round(0.15 * n_segs)))
        if n_test + n_val >= n_segs:
            n_val = max(0, n_segs - n_test - 1)
        test_set = set(ordered_segs[n_segs - n_test:])
        val_set = set(ordered_segs[n_segs - n_test - n_val:n_segs - n_test])
        for s in ordered_segs:
            if s in test_set:
                seg_split[s] = "test"
            elif s in val_set:
                seg_split[s] = "val"
            else:
                seg_split[s] = "train"
    else:
        for s in ordered_segs:
            val = int(hashlib.sha1(s.encode("utf-8")).hexdigest()[:4], 16) % 1000
            if val < 700:
                seg_split[s] = "train"
            elif val < 850:
                seg_split[s] = "val"
            else:
                seg_split[s] = "test"

    for row in raw_rows:
        row["split"] = seg_split.get(row["segment"], "train")

    split_counts = Counter(r["split"] for r in raw_rows)
    tool_counts = Counter(r["tool"] for r in raw_rows)
    tool_met = Counter(r["tool"] for r in raw_rows if r["claim_met"])
    day_counts = Counter(r["day"] for r in raw_rows)

    manifest: Dict[str, Any] = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "schema_version": ws.SCHEMA_VERSION,
        "split_policy": split_policy,
        "rows": len(raw_rows),
        "segments": n_segs,
        "splits": dict(split_counts),
        "claims_met": sum(1 for r in raw_rows if r["claim_met"]),
        "claim_met_pct": round(100.0 * sum(1 for r in raw_rows if r["claim_met"]) / max(1, len(raw_rows)), 2),
        "days": dict(sorted(day_counts.items())),
        "tools": {
            t: {
                "n": tool_counts[t],
                "met": tool_met[t],
                "met_pct": round(100.0 * tool_met[t] / max(1, tool_counts[t]), 1),
            }
            for t in sorted(tool_counts)
        },
        "dims": {
            "global": len(ws.GLOBAL_FEATURES),
            "flat": ws.FLAT_DIM,
            "blocks": {k: len(v) for k, v in ws.BLOCK_FEATURES.items()},
            "slots": ws.SLOTS,
            "world_delta": 10,
        },
        "features": {
            "global": list(ws.GLOBAL_FEATURES),
            "flat": ws.flat_names(),
        },
    }

    return raw_rows, manifest


def write_dataset(rows: List[Dict[str, Any]], manifest: Dict[str, Any], out_dir: Path) -> None:
    """Atomically write train, val, test folds and manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    folds = {"train": [], "val": [], "test": []}
    for r in rows:
        split = r.get("split", "train")
        if split in folds:
            folds[split].append(json.dumps(r, ensure_ascii=False, separators=(",", ":")))

    for name, lines in folds.items():
        target = out_dir / f"{name}.jsonl"
        tmp = target.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        if target.exists():
            target.unlink()
        tmp.rename(target)

    manifest_target = out_dir / "manifest.json"
    manifest_tmp = manifest_target.with_suffix(".tmp")
    manifest_tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if manifest_target.exists():
        manifest_target.unlink()
    manifest_tmp.rename(manifest_target)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--logs", nargs="*", default=None,
                        help="path(s) to trajectory jsonl logs (default: trajectories/dofus-*.jsonl)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="output directory (default: data/world_state_dataset)")
    parser.add_argument("--segment-gap-s", type=float, default=120.0,
                        help="gap in seconds to start a new segment (default: 120.0)")
    parser.add_argument("--split", choices=("temporal", "hash"), default="temporal",
                        help="split policy across segments (default: temporal)")
    parser.add_argument("--check", action="store_true",
                        help="check and report statistics without writing to disk")
    parser.add_argument("--json", action="store_true",
                        help="print manifest as JSON")
    args = parser.parse_args(argv)

    if args.logs:
        paths = [Path(p) for p in args.logs]
    else:
        paths = sorted(DEFAULT_TRAJ.glob("dofus-*.jsonl"))

    if not paths:
        print("no trajectory logs found to process", file=sys.stderr)
        return 1

    steps = load_steps(paths)
    if not steps:
        print("no steps with observations found in trajectories", file=sys.stderr)
        return 1

    segs = build_segments(steps, gap_s=args.segment_gap_s)
    rows, manifest = build_transition_rows(segs, split_policy=args.split)
    manifest["sources"] = [p.name for p in paths]

    if args.json:
        utf8_console()
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
    else:
        print(f"World State Dataset: {len(rows)} transition pairs across {manifest['segments']} segments")
        print(f"Splits: train={manifest['splits'].get('train', 0)}, "
              f"val={manifest['splits'].get('val', 0)}, "
              f"test={manifest['splits'].get('test', 0)}")
        print(f"Successes: {manifest['claims_met']} ({manifest['claim_met_pct']}%)")
        print(f"Dimensions: global={manifest['dims']['global']}, flat={manifest['dims']['flat']}")

    if not args.check:
        write_dataset(rows, manifest, args.out)
        if not args.json:
            print(f"Wrote dataset to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
