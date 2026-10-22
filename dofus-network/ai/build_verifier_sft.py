"""Build the verifier's training corpus from agent step logs.

Input: any ``*.jsonl`` produced by ``agent/autopilot.py`` (one step per line), or
``--selfcheck`` to generate a synthetic session with the real verifier.

Output, in ``--out`` (default ``data/verifier_sft``):

    train.jsonl   examples with a full before/after observation
    val.jsonl     held-out *sessions*, never held-out steps
    stats.json    counts, class balance, drop accounting, per-tool distribution

Why session-level splits: consecutive steps in one session share the map, the
inventory and the same mistake. Splitting by step leaks the answer into val and
turns the score into a memory test — the same mistake the policy corpus made
before ``ai/data_splits.py``.

Why class balance matters here: ``advanced`` dominates a healthy run. A head
trained on the raw distribution predicts "advanced" and is useless, because the
only interesting label is the rare one (``noop`` / ``failed`` / ``blocked``).
``--oversample`` (default 3) repeats the rare classes in *train* only.

Usage (from ``dofus-network``)::

    python -m ai.build_verifier_sft --input %LOCALAPPDATA%/DofusFarm/autopilot.jsonl
    python -m ai.build_verifier_sft --selfcheck --out /tmp/verifier_sft
    python -m ai.build_verifier_sft --stats --input trajectories/autopilot.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VERDICTS = ("advanced", "noop", "failed", "blocked")

# How much a label from each source is worth when training. A rule verdict is a
# heuristic; a human verdict is the only real ground truth. Without this, a
# thousand confident rule labels drown the ten corrections that actually matter.
SOURCE_WEIGHT = {"human": 1.0, "model": 0.6, "rule": 0.3}
DEFAULT_OUT = REPO_ROOT / "data" / "verifier_sft"


# --------------------------------------------------------------------------- io

def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def write_jsonl(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------- shaping

def _target(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    verdict = row.get("verdict")
    if verdict not in VERDICTS:
        return None
    residual = row.get("residual_s")
    return {
        "verdict": verdict,
        "verdict_id": VERDICTS.index(verdict),
        "residual_s": residual if isinstance(residual, (int, float)) else None,
        "drift": bool(row.get("drift")),
    }


def build_example(row: Dict[str, Any], idx: int,
                  min_confidence: float = 0.0) -> Tuple[Optional[Dict[str, Any]], str]:
    """One step -> one example, or (None, reason) when it cannot be used."""
    conf = row.get("confidence")
    if min_confidence > 0 and isinstance(conf, (int, float)) and conf < min_confidence:
        return None, "below confidence floor"
    target = _target(row)
    if target is None:
        return None, "no verdict"

    before = row.get("obs_before") or {}
    after = row.get("obs_after") or {}
    if not before and not after:
        return None, "no observation"
    if row.get("tool") in (None, "", "-"):
        return None, "no tool"

    session = str(row.get("session") or row.get("session_id") or "")
    if not session:
        # Old rows predate the session field: fall back to the file-level id so
        # they still split by origin instead of leaking across the boundary.
        session = str(row.get("_source_file", "legacy"))

    return {
        "id": "%s:%s" % (session, row.get("index", idx)),
        "session": session,
        "index": row.get("index", idx),
        "goal": row.get("goal", ""),
        "tool": row.get("tool"),
        "args": row.get("args") or {},
        "ok": bool(row.get("ok")),
        "error": row.get("error"),
        "latency_ms": row.get("latency_ms"),
        "obs_before": before,
        "obs_after": after,
        "label_source": row.get("source", "rule"),
        "label_weight": SOURCE_WEIGHT.get(row.get("source", "rule"), 0.3),
        "confidence": row.get("confidence"),
        "target": target,
    }, ""


def collect(paths: Sequence[Path], min_confidence: float = 0.0) -> Tuple[List[Dict[str, Any]], Counter]:
    examples: List[Dict[str, Any]] = []
    dropped: Counter = Counter()
    for path in paths:
        rows = read_jsonl(path)
        for i, row in enumerate(rows):
            row.setdefault("_source_file", path.stem)
            ex, why = build_example(row, i, min_confidence)
            if ex is None:
                dropped[why] += 1
                continue
            examples.append(ex)
    return examples, dropped


def split_sessions(examples: Sequence[Dict[str, Any]], val_fraction: float,
                   seed: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    sessions = sorted({e["session"] for e in examples})
    rng = random.Random(seed)
    rng.shuffle(sessions)
    n_val = max(1, int(round(len(sessions) * val_fraction))) if len(sessions) > 1 else 0
    val_sessions = set(sessions[:n_val])
    train = [e for e in examples if e["session"] not in val_sessions]
    val = [e for e in examples if e["session"] in val_sessions]
    return train, val


def oversample(rows: List[Dict[str, Any]], factor: int) -> List[Dict[str, Any]]:
    """Repeat the rare classes in *train* only, so the head is not just a prior.

    ``advanced`` is left alone: it is the majority and duplicating it only makes
    the imbalance worse. Each rare class is repeated ``factor - 1`` times, capped
    at its own size so a class with 3 examples does not become 300.
    """
    if factor <= 1:
        return list(rows)
    by_verdict: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_verdict[r["target"]["verdict"]].append(r)

    out: List[Dict[str, Any]] = []
    for verdict, items in by_verdict.items():
        out.extend(items)
        if verdict == "advanced" or not items:
            continue
        extra = items[: len(items) * (factor - 1)]
        out.extend(extra)
    random.shuffle(out)
    return out


# -------------------------------------------------------------------- synthetic

def selfcheck_steps(count: int = 240, seed: int = 7, sessions: int = 4) -> List[Dict[str, Any]]:
    """Generate step rows through the *real* verifier, with no game involved.

    This is the point of the whole exercise: the label pipeline can be exercised
    before the first live session, so "we have no data yet" stops being a reason
    to leave the second agent untested.

    Several sessions, not one: the split is by session, and a single session
    would produce an empty validation set and a meaningless score.
    """
    from agent.verifier import Verifier

    rng = random.Random(seed)
    verifier = Verifier(human_path=Path("/nonexistent/verifier-selfcheck.jsonl"))
    tools = ["farm.step", "hdv.list", "hdv.scan", "inv.list", "quest.advance",
             "nav.walk", "fight.join", "state.snapshot"]
    per_session = max(1, count // max(1, sessions))
    rows: List[Dict[str, Any]] = []
    index = 0

    for s_idx in range(max(1, sessions)):
        session = "selfcheck%02d-%02d" % (seed % 100, s_idx)
        cell, kamas, hp, inv = 100 + s_idx, 1000 + 100 * s_idx, 100, 5

        for _ in range(per_session):
            tool = rng.choice(tools)
            before = {"cell": cell, "map_id": 7, "kamas": kamas, "xp": index,
                      "hp": hp, "inventory_size": inv}
            roll = rng.random()

            if roll < 0.10:                                    # failure
                ok, error, after = False, "connection reset", None
            elif roll < 0.15:                                  # blocked
                ok, error, after = False, "not_mapped: npc 4211", None
            elif roll < 0.32:                                  # noop
                ok, error, after = True, None, dict(before)
            elif roll < 0.38:                                  # drift: kamas leak
                ok, error = True, None
                after = dict(before)
                after["kamas"] = max(0, kamas - 50)
                kamas = after["kamas"]
            else:                                              # advanced
                ok, error = True, None
                after = dict(before)
                if tool in ("farm.step", "nav.walk"):
                    after["cell"] = cell + 1
                elif tool == "hdv.list":
                    after["inventory_size"] = max(0, inv - 1)
                else:
                    after["xp"] = before["xp"] + 3
                cell, inv = after["cell"], after["inventory_size"]

            latency = rng.choice([90.0, 140.0, 400.0, 3200.0])
            v = verifier.label(tool, {}, ok, error, before, after, latency,
                               expected_s=2.0, session=session, index=index)
            rows.append({
                "index": index, "session": session, "goal": "selfcheck",
                "tool": tool, "args": {}, "ok": ok, "error": error,
                "latency_ms": latency, "verdict": v.verdict, "residual_s": v.residual_s,
                "drift": v.drift, "source": v.source,
                "obs_before": before, "obs_after": after or {},
            })
            index += 1
    return rows


# ----------------------------------------------------------------------- stats

def describe(name: str, rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    verdicts = Counter(r["target"]["verdict"] for r in rows)
    tools = Counter(r["tool"] for r in rows)
    sources = Counter(r["label_source"] for r in rows)
    drift = sum(1 for r in rows if r["target"]["drift"])
    total = max(1, len(rows))
    return {
        "name": name,
        "examples": len(rows),
        "verdicts": dict(verdicts),
        "verdict_pct": {k: round(100.0 * v / total, 1) for k, v in verdicts.items()},
        "tools": dict(tools.most_common()),
        "label_sources": dict(sources),
        "label_weight_sum": round(sum(r.get("label_weight", 0.0) for r in rows), 2),
        "drift_rate": round(drift / total, 3),
        "sessions": len({r["session"] for r in rows}),
    }


# ------------------------------------------------------------------------ main

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", nargs="*", default=None,
                    help="step jsonl files (default: %%LOCALAPPDATA%%/DofusFarm/autopilot.jsonl)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--val-fraction", type=float, default=0.25)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--oversample", type=int, default=3,
                    help="repeat rare classes in train by this factor (1 = off)")
    ap.add_argument("--min-confidence", type=float, default=0.0,
                    help="drop rows whose label confidence is below this (rule labels carry one)")
    ap.add_argument("--selfcheck", action="store_true",
                    help="generate a synthetic session with the real verifier and use it")
    ap.add_argument("--selfcheck-count", type=int, default=240)
    ap.add_argument("--stats", action="store_true", help="report only, write nothing")
    args = ap.parse_args(argv)

    if args.selfcheck:
        rows = selfcheck_steps(args.selfcheck_count, args.seed)
        examples, dropped = collect_from_rows(rows, "selfcheck", args.min_confidence)
    else:
        paths: List[Path] = []
        if args.input:
            paths = [Path(p) for p in args.input]
        else:
            import os
            default = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm" / "autopilot.jsonl"
            paths = [default]
        paths = [p for p in paths if p.exists()]
        if not paths:
            print("no input found (looked for %s). Run the loop, or use --selfcheck."
                  % ", ".join(str(p) for p in (args.input or ["%LOCALAPPDATA%\\DofusFarm\\autopilot.jsonl"])))
            return 2
        examples, dropped = collect(paths, args.min_confidence)

    if not examples:
        print("no usable examples. dropped: %s" % dict(dropped))
        return 1

    train, val = split_sessions(examples, args.val_fraction, args.seed)
    train_bal = oversample(list(train), args.oversample)

    stats = {
        "source": "selfcheck" if args.selfcheck else "logs",
        "dropped": dict(dropped),
        "all": describe("all", examples),
        "train": describe("train", train_bal),
        "val": describe("val", val),
        "oversample": args.oversample,
        "min_confidence": args.min_confidence,
        "val_fraction": args.val_fraction,
        "seed": args.seed,
    }
    print(json.dumps(stats, indent=1))

    if args.stats:
        return 0

    write_jsonl(args.out / "train.jsonl", train_bal)
    write_jsonl(args.out / "val.jsonl", val)
    (args.out / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print("\nwrote %s/{train,val}.jsonl + stats.json  (%d train / %d val)"
          % (args.out, len(train_bal), len(val)))
    return 0


def collect_from_rows(rows: Sequence[Dict[str, Any]], source: str,
                      min_confidence: float = 0.0) -> Tuple[List[Dict[str, Any]], Counter]:
    examples: List[Dict[str, Any]] = []
    dropped: Counter = Counter()
    for i, row in enumerate(rows):
        row.setdefault("_source_file", source)
        ex, why = build_example(row, i, min_confidence)
        if ex is None:
            dropped[why] += 1
            continue
        examples.append(ex)
    return examples, dropped


if __name__ == "__main__":
    raise SystemExit(main())
