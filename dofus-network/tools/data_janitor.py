"""Keep the live-transition corpus honest before anything learns from it.

`tools/build_live_transitions.py` writes what the logs say. This tool decides what
may be trained on, and it names every exclusion instead of silently dropping it:

* **schema** - a corpus written by an older builder is detected row by row. The
  on-disk `data/transitions` from 2026-10-27 carries neither `at`, `advance`,
  `claim`, `refused`, `context` nor `dt_ms`; a trainer reading it would report a
  number about a corpus the current code cannot even produce. A wholly stale
  corpus is `fatal`, with the rebuild command in the report.
* **feature width** - every `x` must be `FEATURE_COUNT` wide. A 41-wide row in a
  42-wide corpus is the same failure that made the harness transfer number
  meaningless (AI_ARCHITECTURE section 8.3), so it is quarantined and fatal.
* **leaks** - one `segment` may live in exactly one split. A segment that appears
  in train and test is a leak even when no `id` repeats, because consecutive
  steps of one goal are not independent.
* **duplicates** - the same row `id` is kept once; later copies are quarantined.
* **scripted rows** - `context.tutorial` true means a tutorial-authorised world;
  the same rule that drops those fights in `combat/online_learn.py` applies here.
  Measured 2026-10-31: 7 of 9 resolved live fights were tutorial-authorised.
* **unpaired / featureless** - a row without an after-state or without `x` cannot
  teach an outcome; it is quarantined, not zero-filled.
* **drift** - live feature means are compared with a reference corpus (the
  harness `data/tool_sft_v3` by default) per `FEATURE_NAMES`. This is the check
  that catches "levels 20-100 in the corpus, level 3 live" without waiting for a
  model to fail: it reports |z| and the share of live values outside the
  reference range, and flags a feature at |z| >= 3 or out-of-range >= 50%.

Nothing is written without `--write`, and the output is a *separate* directory
(`data/transitions_clean` by default) so the raw corpus stays replayable. The
write is atomic per file: a crash leaves the previous clean corpus intact.

    python -m tools.data_janitor --check
    python -m tools.data_janitor --write
    python -m tools.data_janitor --write --strict     # exit 3 on a fatal finding
    python -m tools.data_janitor --check --json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.features import FEATURE_COUNT, FEATURE_NAMES  # noqa: E402

DEFAULT_CORPUS = REPO_ROOT / "data" / "transitions"
DEFAULT_OUT = REPO_ROOT / "data" / "transitions_clean"
DEFAULT_REFERENCE = REPO_ROOT / "data" / "tool_sft_v3"

SPLITS = ("train", "val", "test")
LABEL_VERSION = 3
# Keys the current builder always writes. A row missing all of them came from an
# older label rule, and training on it would mix two definitions of "progress".
SCHEMA_KEYS = ("advance", "claim", "refused", "at", "context")
DRIFT_Z = 3.0
DRIFT_RANGE_SHARE = 0.5
VERSION = 1


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _load_split(path: Path) -> Tuple[List[Tuple[Dict[str, Any], int]], int]:
    """Rows with their line number, and the count that could not be parsed."""
    rows: List[Tuple[Dict[str, Any], int]] = []
    bad = 0
    if not path.is_file():
        return rows, bad
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(row, dict):
                rows.append((row, number))
            else:
                bad += 1
    return rows, bad


def load(corpus: Path) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    """Every split in file order, plus what could not be read."""
    by_split: Dict[str, List[Dict[str, Any]]] = {}
    parse_errors: Dict[str, int] = {}
    for split in SPLITS:
        rows, bad = _load_split(Path(corpus) / ("%s.jsonl" % split))
        by_split[split] = [row for row, _ in rows]
        parse_errors[split] = bad
    manifest: Dict[str, Any] = {}
    path = Path(corpus) / "manifest.json"
    if path.is_file():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            manifest = {}
    return by_split, {"parse_errors": parse_errors, "manifest": manifest}


def _segment_splits(by_split: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Counter]:
    """How many rows of each segment landed in each split."""
    out: Dict[str, Counter] = defaultdict(Counter)
    for split in SPLITS:
        for row in by_split.get(split) or []:
            segment = row.get("segment")
            if isinstance(segment, str) and segment:
                out[segment][split] += 1
    return out


def _keeper(segments: Dict[str, Counter], segment: str) -> Optional[str]:
    counts = segments.get(segment)
    if not counts:
        return None
    return min(counts, key=lambda split: (-counts[split], SPLITS.index(split)
                                          if split in SPLITS else len(SPLITS)))


def _provenance(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    characters: Counter = Counter()
    levels: Counter = Counter()
    breeds: Counter = Counter()
    days: Counter = Counter()
    instances: Counter = Counter()
    sources: Counter = Counter()
    goals: Counter = Counter()
    types: Counter = Counter()
    sessions = set()
    for row in rows:
        context = row.get("context") if isinstance(row.get("context"), dict) else {}
        who = context.get("character") if isinstance(context.get("character"), dict) else {}
        if who.get("id") is not None:
            characters[str(who.get("id"))] += 1
        if who.get("level") is not None:
            levels[str(who.get("level"))] += 1
        if who.get("breed") is not None:
            breeds[str(who.get("breed"))] += 1
        at = _number(row.get("at"))
        day = time.strftime("%Y-%m-%d", time.gmtime(at)) if at else None
        if day:
            days[day] += 1
        instance = str(row.get("instance") or "?")
        source = str(row.get("source") or "?")
        instances[instance] += 1
        sources[source] += 1
        if row.get("goal"):
            goals[str(row.get("goal"))[:60]] += 1
        if row.get("objective_type") is not None:
            types[str(row.get("objective_type"))] += 1
        if day:
            sessions.add((source, instance, day))
    return {
        "characters": dict(characters.most_common(8)),
        "levels": dict(levels.most_common(12)),
        "breeds": dict(breeds.most_common(8)),
        "days": dict(sorted(days.items())),
        "instances": dict(instances.most_common(8)),
        "sources": dict(sources.most_common(8)),
        "goals": dict(goals.most_common(6)),
        "objective_types": dict(types.most_common(12)),
        "sessions": len(sessions),
        "characters_n": len(characters),
        "levels_n": len(levels),
        "days_n": len(days),
    }


def _stats(vectors: Sequence[Sequence[float]]) -> List[Dict[str, Any]]:
    """Per-column count, mean, std, min, max; constant columns say so."""
    width = FEATURE_COUNT
    out = [{"n": 0, "mean": 0.0, "std": 0.0, "min": None, "max": None}
           for _ in range(width)]
    sums = [0.0] * width
    squares = [0.0] * width
    for vector in vectors:
        if not isinstance(vector, (list, tuple)) or len(vector) != width:
            continue
        for index, raw in enumerate(vector):
            value = _number(raw)
            if value is None:
                continue
            cell = out[index]
            cell["n"] += 1
            sums[index] += value
            squares[index] += value * value
            cell["min"] = value if cell["min"] is None else min(cell["min"], value)
            cell["max"] = value if cell["max"] is None else max(cell["max"], value)
    for index, cell in enumerate(out):
        n = cell["n"]
        if n:
            cell["mean"] = sums[index] / n
            variance = max(0.0, squares[index] / n - cell["mean"] ** 2)
            cell["std"] = math.sqrt(variance)
    return out


def _read_reference(reference: Optional[Path]) -> Tuple[List[List[float]], Dict[str, Any]]:
    vectors: List[List[float]] = []
    meta: Dict[str, Any] = {"path": str(reference) if reference else None,
                            "rows": 0, "files": []}
    if reference is None:
        return vectors, meta
    reference = Path(reference)
    paths = [reference / "train.jsonl", reference / "val.jsonl"]
    if reference.is_file():
        paths = [reference]
    if not any(path.is_file() for path in paths):
        return vectors, meta
    for path in paths:
        if not path.is_file():
            continue
        meta["files"].append(path.name)
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                vector = row.get("x") if isinstance(row, dict) else None
                if (isinstance(vector, list) and len(vector) == FEATURE_COUNT):
                    vectors.append(vector)
                    meta["rows"] += 1
    return vectors, meta


def drift(live_vectors: Sequence[Sequence[float]],
          reference_vectors: Sequence[Sequence[float]],
          reference_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Where the live feature distribution leaves the reference's range.

    The arithmetic is deliberately dull: per-column z of the means (reference
    std < 1e-3 means "constant", so z is undefined and is reported as such) and
    the share of live values outside the reference min/max. Measured failure it
    is built to catch: the harness corpus ran levels 20-100 and the live
    character is level 3, so the transfer run was scoring states the model had
    never been normalised on.
    """
    meta = dict(reference_meta or {})
    if not reference_vectors:
        return {"status": "no_reference", **meta, "in_coverage": None,
                "flags": [], "features": FEATURE_COUNT}
    live = _stats(live_vectors)
    ref = _stats(reference_vectors)
    flags: List[Dict[str, Any]] = []
    z_values: List[float] = []
    per_feature: List[Dict[str, Any]] = []
    for index, name in enumerate(FEATURE_NAMES):
        l_cell, r_cell = live[index], ref[index]
        if not l_cell["n"] or not r_cell["n"]:
            continue
        z: Optional[float]
        if r_cell["std"] < 1e-3:
            z = None
            constant = True
        else:
            z = (l_cell["mean"] - r_cell["mean"]) / r_cell["std"]
            z_values.append(abs(z))
            constant = False
        low, high = r_cell["min"], r_cell["max"]
        outside = total = 0
        for vector in live_vectors:
            value = _number(vector[index]) if index < len(vector) else None
            if value is None:
                continue
            total += 1
            if (low is not None and value < low - 1e-9) or (
                    high is not None and value > high + 1e-9):
                outside += 1
        share = outside / total if total else 0.0
        flagged = (z is not None and abs(z) >= DRIFT_Z) or share >= DRIFT_RANGE_SHARE
        if constant and l_cell["std"] >= 1e-3:
            flagged = True
        cell = {"feature": index, "name": name, "live_mean": round(l_cell["mean"], 4),
                "ref_mean": round(r_cell["mean"], 4), "z": None if z is None else round(z, 2),
                "out_of_range_share": round(share, 3), "flagged": flagged,
                "reference_constant": constant}
        per_feature.append(cell)
        if flagged:
            flags.append(cell)
    flags.sort(key=lambda cell: (-(abs(cell["z"]) if cell["z"] is not None else 99.0),
                                 -cell["out_of_range_share"]))
    return {
        "status": "compared",
        **meta,
        "features": FEATURE_COUNT,
        "features_compared": len(per_feature),
        "median_abs_z": round(sorted(z_values)[len(z_values) // 2], 2) if z_values else None,
        "max_abs_z": round(max(z_values), 2) if z_values else None,
        "flags": flags[:8],
        "in_coverage": not flags,
        "per_feature": per_feature,
    }


def clean(by_split: Dict[str, List[Dict[str, Any]]],
          reference: Optional[Path] = None,
          now: Optional[float] = None) -> Dict[str, Any]:
    """Decide what is trainable; return the kept rows and the full report.

    A row can carry more than one reason. The first reason that makes it
    unusable quarantines it; all of them are recorded so a fix can be aimed at
    the real cause rather than the first symptom.
    """
    now = time.time() if now is None else now
    segments = _segment_splits(by_split)
    leaked = {segment: [split for split in SPLITS if counts.get(split)]
              for segment, counts in segments.items()
              if sum(1 for split in SPLITS if counts.get(split)) > 1}
    kept: Dict[str, List[Dict[str, Any]]] = {split: [] for split in SPLITS}
    quarantine: List[Dict[str, Any]] = []
    reasons: Counter = Counter()
    seen_ids: set = set()
    width_bad = 0
    schema_ok = 0
    total = 0

    def drop(split: str, row: Dict[str, Any], why: List[str]) -> None:
        reasons.update(why)
        quarantine.append({"split": split, "id": row.get("id"),
                           "segment": row.get("segment"), "tool": row.get("tool"),
                           "reasons": why, "row": row})

    for split in SPLITS:
        for row in by_split.get(split) or []:
            total += 1
            why: List[str] = []
            row_id = row.get("id")
            if not isinstance(row_id, str) or not row_id:
                why.append("missing_id")
            segment = row.get("segment")
            if not isinstance(segment, str) or not segment:
                why.append("missing_segment")
            if not row.get("tool"):
                why.append("missing_tool")
            if all(key in row for key in SCHEMA_KEYS):
                schema_ok += 1
            else:
                why.append("stale_schema")
            if isinstance(row_id, str) and row_id:
                if row_id in seen_ids:
                    why.append("duplicate_id")
                seen_ids.add(row_id)
            if isinstance(segment, str) and segment in leaked:
                keeper = _keeper(segments, segment)
                if keeper is not None and split != keeper:
                    why.append("split_leak")
            vector = row.get("x")
            if not row.get("has_pair"):
                why.append("unpaired")
            if isinstance(vector, list) and vector:
                if len(vector) != FEATURE_COUNT:
                    why.append("feature_width")
                    width_bad += 1
            else:
                why.append("no_features")
            context = row.get("context") if isinstance(row.get("context"), dict) else {}
            if context.get("tutorial") is True:
                why.append("tutorial")
            at = _number(row.get("at"))
            if at is not None and at > now + 86400:
                why.append("future_timestamp")
            if why:
                drop(split, row, why)
            else:
                kept[split].append(row)

    kept_rows = [row for split in SPLITS for row in kept[split]]
    actions = [row for row in kept_rows if not row.get("was_read")]
    paired = [row for row in kept_rows if row.get("has_pair")]
    vectors = [row["x"] for row in kept_rows
               if isinstance(row.get("x"), list) and len(row["x"]) == FEATURE_COUNT]
    dead_keys = [key for key in SCHEMA_KEYS if not any(key in row for row in kept_rows)]
    fatal: List[str] = []
    warnings: List[str] = []
    if total and schema_ok == 0:
        fatal.append(
            "stale_corpus: none of %d row(s) carries the current label keys (%s); "
            "rebuild with `python -m tools.build_live_transitions`"
            % (total, ", ".join(SCHEMA_KEYS)))
    if width_bad:
        fatal.append(
            "feature_width: %d row(s) are not %d wide; FEATURE_COUNT changed since "
            "the corpus was written - rebuild rather than pad"
            % (width_bad, FEATURE_COUNT))
    if leaked:
        warnings.append(
            "split_leak: %d segment(s) appeared in more than one split; the rows "
            "outside the majority split were quarantined" % len(leaked))
    if dead_keys:
        warnings.append("missing_keys: %s absent from every kept row" % ", ".join(dead_keys))
    provenance = _provenance(kept_rows)
    if provenance["characters_n"] > 1:
        warnings.append("multi_character: %d character(s) in one corpus"
                        % provenance["characters_n"])
    if not kept_rows:
        warnings.append("empty: no row survived the checks")
    reference_vectors, reference_meta = _read_reference(reference)
    drift_report = drift(vectors, reference_vectors, reference_meta)
    return {
        "version": VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "label_version": LABEL_VERSION,
        "rows": {"total": total,
                 **{split: len(by_split.get(split) or []) for split in SPLITS}},
        "kept": {
            "total": len(kept_rows),
            **{split: len(kept[split]) for split in SPLITS},
            "actions": len(actions),
            "reads": len(kept_rows) - len(actions),
            "paired": len(paired),
            "with_features": len(vectors),
            "actions_moved": sum(1 for row in actions if row.get("moved")),
            "actions_advance": sum(1 for row in actions if row.get("advance")),
        },
        "quarantine": {
            "total": len(quarantine),
            "by_reason": dict(reasons.most_common()),
            "examples": [{"id": item["id"], "split": item["split"],
                          "reasons": item["reasons"], "tool": item["tool"]}
                         for item in quarantine[:6]],
        },
        "leaks": {"segments": len(leaked),
                  "detail": {key: value for key, value in list(leaked.items())[:12]}},
        "schema": {"label_version": LABEL_VERSION, "v3_rows": schema_ok,
                   "stale_rows": total - schema_ok, "fatal": fatal,
                   "warnings": warnings},
        "provenance": provenance,
        "drift": drift_report,
        "kept_rows": kept,
        # Full rows are kept out of the public report (they are the corpus) and
        # written to their own file by `write()`, so an exclusion is auditable.
        "_quarantine": quarantine,
    }


def write(report: Dict[str, Any], out: Path,
          quarantine_rows: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Atomically replace each split, then the manifest; never touch the source."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    staging = out / (".staging-%d" % os.getpid())
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    kept = report["kept_rows"]
    written = {}
    try:
        for split in SPLITS:
            target = staging / ("%s.jsonl" % split)
            with target.open("w", encoding="utf-8") as handle:
                for row in kept[split]:
                    handle.write(json.dumps(row, ensure_ascii=False,
                                            sort_keys=True) + "\n")
            written[split] = len(kept[split])
        manifest = {key: value for key, value in report.items()
                    if key not in ("kept_rows", "_quarantine")}
        manifest["written"] = written
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
        with (staging / "quarantine.jsonl").open("w", encoding="utf-8") as handle:
            for item in quarantine_rows:
                handle.write(json.dumps(item, ensure_ascii=False,
                                        sort_keys=True) + "\n")
        # Replace files, manifest last: a reader that sees the new manifest also
        # sees the new splits.
        for name in ("%s.jsonl" % SPLITS[0], "%s.jsonl" % SPLITS[1],
                     "%s.jsonl" % SPLITS[2], "quarantine.jsonl", "manifest.json"):
            os.replace(staging / name, out / name)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return written


def run(corpus: Path = DEFAULT_CORPUS, out: Path = DEFAULT_OUT,
        reference: Optional[Path] = DEFAULT_REFERENCE, write_out: bool = False,
        now: Optional[float] = None) -> Dict[str, Any]:
    by_split, read_meta = load(corpus)
    report = clean(by_split, reference, now=now)
    quarantine_rows = report.pop("_quarantine", [])
    report["corpus"] = str(corpus)
    report["reference"] = report["drift"].get("path")
    report["parse_errors"] = read_meta["parse_errors"]
    report["source_manifest"] = {
        key: read_meta["manifest"].get(key) for key in
        ("label_version", "rows", "split_policy", "x_coverage_pct")}
    # The identity of the code that labelled these rows has to survive cleaning: a trainer
    # reads the clean directory, and without the sha it cannot tell whether its rows came
    # from the labeller that is in the tree today (G92 was exactly that staleness).
    report["labeler_sha"] = read_meta["manifest"].get("labeler_sha")
    report["label_rule"] = read_meta["manifest"].get("label_rule")
    if write_out:
        report["written"] = write(report, out, quarantine_rows)
    report.pop("kept_rows", None)
    return report


def history(ledger: Path) -> Dict[str, Any]:
    """Per-close drift and volume, from the session-close ledger.

    One close is a measurement; the trend is the thing that says whether the live
    distribution is moving toward the training corpus. Rows whose janitor step was
    skipped are not measurements and are left out.
    """
    closes: List[Dict[str, Any]] = []
    path = Path(ledger)
    if path.is_file():
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                step = ((row.get("steps") or {}).get("janitor") or {})
                if not isinstance(step, dict) or not step.get("ok") or step.get("skipped"):
                    continue
                kept = step.get("kept") or {}
                quarantine = step.get("quarantine") or {}
                closes.append({
                    "at": row.get("at"),
                    "kept": kept.get("total"),
                    "actions": kept.get("actions"),
                    "quarantined": quarantine.get("total"),
                    "by_reason": quarantine.get("by_reason") or {},
                    "in_coverage": step.get("in_coverage"),
                    "drift_flags": step.get("drift_flags"),
                })
    trend: Dict[str, Any] = {"closes": len(closes)}
    if closes:
        first, last = closes[0], closes[-1]
        trend["first"] = first["at"]
        trend["last"] = last["at"]
        if isinstance(first.get("kept"), int) and isinstance(last.get("kept"), int):
            trend["kept_delta"] = last["kept"] - first["kept"]
        if isinstance(first.get("drift_flags"), int) and \
                isinstance(last.get("drift_flags"), int):
            trend["drift_delta"] = last["drift_flags"] - first["drift_flags"]
        trend["coverage_now"] = last.get("in_coverage")
    return {"rows": closes, "trend": trend}


def _summary(report: Dict[str, Any]) -> str:
    kept = report["kept"]
    quar = report["quarantine"]
    drift_report = report["drift"]
    lines = [
        "rows %d -> kept %d (train %d / val %d / test %d), actions %d, reads %d"
        % (report["rows"]["total"], kept["total"], kept["train"], kept["val"],
           kept["test"], kept["actions"], kept["reads"]),
        "quarantined %d %s" % (quar["total"], quar["by_reason"]),
        "schema: v3 rows %d, stale %d%s"
        % (report["schema"]["v3_rows"], report["schema"]["stale_rows"],
           ("; FATAL " + " | ".join(report["schema"]["fatal"]))
           if report["schema"]["fatal"] else ""),
        "provenance: %d character(s) %s, levels %s, %d day(s), %d session(s)"
        % (report["provenance"]["characters_n"],
           report["provenance"]["characters"], report["provenance"]["levels"],
           report["provenance"]["days_n"], report["provenance"]["sessions"]),
    ]
    if drift_report.get("status") == "compared":
        lines.append(
            "drift vs %s: median |z| %s, %d/%d feature(s) flagged, in coverage %s"
            % (drift_report.get("path"), drift_report.get("median_abs_z"),
               len(drift_report.get("flags") or []), drift_report.get("features_compared"),
               drift_report.get("in_coverage")))
        for cell in drift_report.get("flags") or []:
            lines.append("  ! %-22s z %s, outside range %.1f%%"
                         % (cell["name"], cell["z"],
                            100.0 * cell["out_of_range_share"]))
    else:
        lines.append("drift: no reference corpus (%s)" % drift_report.get("path"))
    for warning in report["schema"]["warnings"]:
        lines.append("warning: " + warning)
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check and clean the live-transition corpus before training.")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--check", action="store_true",
                        help="report only; this is the default")
    parser.add_argument("--write", action="store_true",
                        help="write the cleaned corpus to --out")
    parser.add_argument("--strict", action="store_true",
                        help="exit 3 when a fatal finding is reported")
    parser.add_argument("--history", type=Path, default=None,
                        help="read the session-close ledger and trend drift/volume")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.history is not None:
        trend = history(args.history)
        if args.json:
            print(json.dumps(trend, indent=1, sort_keys=True))
        else:
            for row in trend["rows"]:
                print("%s  kept %s (actions %s), quarantined %s, drift flags %s, "
                      "in coverage %s"
                      % (row.get("at"), row.get("kept"), row.get("actions"),
                         row.get("quarantined"), row.get("drift_flags"),
                         row.get("in_coverage")))
            print("trend: %s" % trend["trend"])
        return 0
    report = run(args.corpus, args.out, args.reference, write_out=args.write)
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
    else:
        print("== corpus %s" % args.corpus)
        print(_summary(report))
        if report.get("written"):
            print("written: %s" % report["written"])
    if args.strict and report["schema"]["fatal"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
