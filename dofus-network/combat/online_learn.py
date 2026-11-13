"""Online, gated learning for the fight win probability.

The estimate in `combat/fight_estimate.py` produces `p_win` only from a fitted
calibrator; the file `models/fight_calibrator.json` is that calibrator, and it
does not exist yet, so every fight is refused unattended by design. The one-shot
fitter inside that module scores itself in sample, which is optimistic and cannot
tell whether the model is worth serving.

This loop is the deployment path. It reads the live fight outcomes, drops the ones
the estimate cannot vouch for (see `Filters`: unresolved, non-live, unstarted,
duplicates, tutorial-authorised scripted fights, and optionally unpriced monsters),
trains on an older time window, scores the newest slice out of time, and promotes
only when the candidate beats the analytic `raw_score` on that slice by a margin,
passes a permutation check, keeps its calibration error under the same bound, and
has enough resolved fights. Below the minimum it reports `collecting` and writes
nothing. A promoted model is versioned, the previous version is kept for rollback,
and every decision lands in a history ledger. With no artifact the estimate keeps
`p_win: null` -- fail-open is the existing behaviour.

Cadence is event-driven by new fights, not by a clock: a run is due when at
least `NEW_FIGHTS_FOR_RUN` resolved fights arrived since the last ledger row.

    python -m combat.online_learn --check            # evaluate, write nothing
    python -m combat.online_learn --run              # promote when due and gated
    python -m combat.online_learn --run --force      # run now, still gated
    python -m combat.online_learn --shadow-report    # incumbent vs analytic
    python -m combat.online_learn --rollback
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import random
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from combat.fight_estimate import (DEFAULT_CALIBRATOR, FEATURE_NAMES,
                                   MAX_CALIBRATION_ERROR, FightCalibrator,
                                   calibration_report, fit_calibrator)
import logging
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROWS = REPO_ROOT / "trajectories" / "fight_outcomes.jsonl"

MIN_FIGHTS = 40
MIN_EVAL = 12
NEW_FIGHTS_FOR_RUN = 20
MARGIN_AUC = 0.02
MARGIN_BRIER = 0.02
MAX_PERM_P = 0.10
PERMUTATIONS = 500


@dataclass(frozen=True)
class Filters:
    """Which resolved fights count as evidence for the live calibrator.

    Defaults drop anything the estimate cannot vouch for. Measured 2026-10-31:
    all 11 resolved outcomes pair to a start whose assumptions carry "tutorial
    authorisation", so a learner without these filters would fit p_win ~ 1 on
    scripted fights the scenario guarantees. `strict_members` additionally
    requires priced monster facts; it is off because the low-level Touch
    monsters in the current corpus have no cached rows at all.
    """
    live_only: bool = True
    require_start: bool = True
    drop_scripted: bool = True
    strict_members: bool = False


def start_index(rows: Sequence[Dict[str, Any]]) -> Dict[Tuple[str, Any], Dict[str, Any]]:
    starts: Dict[Tuple[str, Any], Dict[str, Any]] = {}
    for row in rows:
        if row.get("kind") != "fight_start":
            continue
        starts[(str(row.get("session")), row.get("group_id"))] = row
    return starts


def is_scripted(start: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(start, dict):
        return False
    estimate = start.get("estimate")
    assumptions = estimate.get("assumptions") if isinstance(estimate, dict) else None
    if not isinstance(assumptions, list):
        return False
    return any("tutorial authorisation" in str(item).lower() for item in assumptions)


def filter_samples(samples: Sequence[Dict[str, Any]],
                   rows: Sequence[Dict[str, Any]],
                   filters: Filters = Filters()) -> Dict[str, Any]:
    starts = start_index(rows)
    kept: List[Dict[str, Any]] = []
    dropped: Counter = Counter()
    seen: set = set()
    heuristic = 0
    for sample in samples:
        identity = (str(sample.get("session")), sample.get("group_id"),
                    sample.get("started_at"), sample.get("at"))
        if identity in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(identity)
        if filters.live_only and str(sample.get("source") or "") != "live":
            dropped["not_live"] += 1
            continue
        start = starts.get((str(sample.get("session")), sample.get("group_id")))
        if start is None and filters.require_start:
            dropped["no_start"] += 1
            continue
        if filters.drop_scripted and is_scripted(start):
            dropped["tutorial_authorised"] += 1
            continue
        members = (sample.get("features") or {}).get("unknown_members")
        if isinstance(members, (int, float)) and float(members) > 0:
            if filters.strict_members:
                dropped["unknown_members"] += 1
                continue
            heuristic += 1
        kept.append(sample)
    return {"kept": kept, "dropped": dict(dropped), "kept_n": len(kept),
            "heuristic_members": heuristic,
            "filters": {"live_only": filters.live_only,
                        "require_start": filters.require_start,
                        "drop_scripted": filters.drop_scripted,
                        "strict_members": filters.strict_members}}


def eligible_samples(rows: Sequence[Dict[str, Any]],
                     filters: Filters = Filters()) -> Dict[str, Any]:
    return filter_samples(terminal_samples(rows), rows, filters)


@dataclass(frozen=True)
class Paths:
    rows: Path = DEFAULT_ROWS
    calibrator: Path = DEFAULT_CALIBRATOR
    versions: Path = DEFAULT_CALIBRATOR.parent / "fight_calibrator.versions"
    history: Path = DEFAULT_CALIBRATOR.parent / "fight_calibrator.history.jsonl"
    pointer: Path = DEFAULT_CALIBRATOR.parent / "fight_calibrator.incumbent.json"
    damage_calibration: Path = (DEFAULT_CALIBRATOR.parent
                                / "fight_damage_calibration.json")


def _damage_calibration_summary(path: Path) -> Optional[Dict[str, Any]]:
    """The shadow differential, as provenance for the outcome calibrator.

    The outcome model is fitted on estimates that were produced with a damage
    price. If that price has since been corrected, the calibrator's training
    distribution is not the one it will see at serving time; recording the
    correction here makes the mismatch visible instead of silent.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None
    overall = payload.get("overall") or {}
    return {
        "path": str(path),
        "rows": int(overall.get("rows") or 0),
        "status": overall.get("status"),
        "scale": overall.get("scale"),
        "bias": overall.get("bias"),
        "spells": len(payload.get("spells") or {}),
        "effects": len(payload.get("effects") or {}),
    }


def read_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        handle = Path(path).open(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _at(row: Dict[str, Any]) -> float:
    stamp = row.get("at")
    return float(stamp) if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) else 0.0


def terminal_samples(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Resolved fights in time order, each with the estimate's own features.

    A `fight_start` is not resolved and cannot train anything; a row missing a
    feature is skipped rather than zero-filled, because a zero is a value.
    """
    out: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("kind") != "fight_outcome":
            continue
        won = row.get("won")
        features = row.get("features")
        if not isinstance(won, bool) or not isinstance(features, dict):
            continue
        usable = True
        for name in FEATURE_NAMES:
            value = features.get(name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                usable = False
                break
        if not usable:
            continue
        estimate = row.get("estimate") if isinstance(row.get("estimate"), dict) else {}
        raw = estimate.get("raw_score")
        out.append({
            **row,
            "_at": _at(row),
            "_raw": float(raw) if isinstance(raw, (int, float))
            and not isinstance(raw, bool) else None,
        })
    out.sort(key=lambda item: item["_at"])
    return out


def split(samples: Sequence[Dict[str, Any]], share: float = 0.3,
          min_eval: int = MIN_EVAL) -> Tuple[List[Dict[str, Any]],
                                             List[Dict[str, Any]]]:
    """The newest slice is the test set; earlier fights are the training set."""
    count = len(samples)
    if count == 0:
        return [], []
    eval_n = min(max(min_eval, int(round(count * share))), max(1, count // 2))
    cut = count - eval_n
    return list(samples[:cut]), list(samples[cut:])


def auc(labels: Sequence[float], scores: Sequence[float]) -> Optional[float]:
    positives = sum(1 for label in labels if label >= 0.5)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0 or len(labels) != len(scores):
        return None
    order = sorted(range(len(scores)), key=lambda index: scores[index])
    ranks = [0.0] * len(scores)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and scores[order[end + 1]] == scores[order[start]]:
            end += 1
        rank = (start + end) / 2.0 + 1.0
        for index in range(start, end + 1):
            ranks[order[index]] = rank
        start = end + 1
    positive_rank_sum = sum(ranks[index] for index in range(len(scores))
                            if labels[index] >= 0.5)
    return (positive_rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def brier(labels: Sequence[float], scores: Sequence[float]) -> Optional[float]:
    if not labels or len(labels) != len(scores):
        return None
    return sum((float(score) - float(label)) ** 2
               for label, score in zip(labels, scores)) / len(labels)


def _permutation_p(labels: Sequence[float], candidate: Sequence[float],
                   baseline: Sequence[float], observed: Optional[float],
                   reps: int = PERMUTATIONS, seed: int = 0) -> Optional[float]:
    if observed is None or len(labels) < 4:
        return None
    shuffled = list(labels)
    rng = random.Random(seed)
    hits = total = 0
    for _ in range(reps):
        rng.shuffle(shuffled)
        cand = auc(shuffled, candidate)
        base = auc(shuffled, baseline)
        if cand is None or base is None:
            continue
        total += 1
        if (cand - base) >= observed:
            hits += 1
    return (hits + 1) / (total + 1) if total else None


def evaluate_gate(samples: Sequence[Dict[str, Any]],
                  min_fights: int = MIN_FIGHTS,
                  min_eval: int = MIN_EVAL) -> Dict[str, Any]:
    count = len(samples)
    if count < min_fights:
        return {"status": "collecting", "n": count, "need": min_fights,
                "reason": "%d of %d resolved fight(s) with features; nothing to gate"
                          % (count, min_fights)}
    train, eval_rows = split(samples, min_eval=min_eval)
    if len(train) < 20:
        return {"status": "collecting", "n": count, "need": min_fights,
                "reason": "training slice has %d fight(s); 20 are required"
                          % len(train)}
    model = fit_calibrator(train)
    if model is None:
        return {"status": "refused", "n": count,
                "reason": "training slice has one class or no usable rows"}
    report = calibration_report(model, eval_rows)
    pairs: List[Tuple[Dict[str, Any], float, float]] = []
    for row in eval_rows:
        prediction = model.predict(row["features"])
        if prediction is None or row.get("_raw") is None:
            continue
        pairs.append((row, float(prediction), float(row["_raw"])))
    labels = [1.0 if row["won"] else 0.0 for row, _candidate, _baseline in pairs]
    candidate_scores = [candidate for _row, candidate, _baseline in pairs]
    baseline_scores = [baseline for _row, _candidate, baseline in pairs]
    candidate_auc = auc(labels, candidate_scores)
    baseline_auc = auc(labels, baseline_scores)
    candidate_brier = brier(labels, candidate_scores)
    baseline_brier = brier(labels, baseline_scores)
    delta = (candidate_auc - baseline_auc
             if candidate_auc is not None and baseline_auc is not None else None)
    perm_p = _permutation_p(labels, candidate_scores, baseline_scores, delta)
    reasons: List[str] = []
    if len(pairs) < min_eval:
        reasons.append("evaluation slice has %d readable fight(s); %d are required"
                       % (len(pairs), min_eval))
    if delta is None:
        reasons.append("one class in the evaluation slice; AUC is undefined")
    elif delta < MARGIN_AUC:
        reasons.append("AUC %.3f does not beat the analytic %.3f by %.2f"
                       % (candidate_auc, baseline_auc, MARGIN_AUC))
    if candidate_brier is not None and baseline_brier is not None \
            and candidate_brier > baseline_brier - MARGIN_BRIER:
        reasons.append("Brier %.3f is not %.2f below the analytic %.3f"
                       % (candidate_brier, MARGIN_BRIER, baseline_brier))
    ece = report.get("ece")
    if ece is None or float(ece) > MAX_CALIBRATION_ERROR:
        reasons.append("calibration error %s is above %.2f"
                       % (ece, MAX_CALIBRATION_ERROR))
    if perm_p is None:
        reasons.append("permutation check could not be computed")
    elif perm_p > MAX_PERM_P:
        reasons.append("permutation p %.3f is above %.2f" % (perm_p, MAX_PERM_P))
    verdict = {
        "status": "passed" if not reasons else "refused",
        "n": count,
        "train_n": len(train),
        "eval_n": len(pairs),
        "candidate": {"auc": candidate_auc, "brier": candidate_brier, "ece": ece},
        "baseline": {"auc": baseline_auc, "brier": baseline_brier},
        "delta_auc": delta,
        "perm_p": perm_p,
        "reasons": reasons,
        "thresholds": {"min_fights": min_fights, "min_eval": min_eval,
                       "margin_auc": MARGIN_AUC, "margin_brier": MARGIN_BRIER,
                       "max_perm_p": MAX_PERM_P,
                       "max_ece": MAX_CALIBRATION_ERROR},
        "at": time.time(),
    }
    return verdict


def _history_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        handle = Path(path).open(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _append_history(path: Path, row: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def cadence(paths: Paths, samples: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    last_n = 0
    for row in _history_rows(paths.history):
        if row.get("status") in ("promoted", "refused"):
            try:
                last_n = max(last_n, int(row.get("n") or 0))
            except (TypeError, ValueError):
                continue
    since = max(0, len(samples) - last_n)
    return {"due": since >= NEW_FIGHTS_FOR_RUN, "new_fights": since,
            "need": NEW_FIGHTS_FOR_RUN, "last_n": last_n}


def _stamp(payload: str) -> str:
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return now + "-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _read_pointer(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def run(paths: Paths = Paths(), write: bool = True,
        force: bool = False, filters: Filters = Filters()) -> Dict[str, Any]:
    rows = read_rows(paths.rows)
    selection = eligible_samples(rows, filters)
    samples = selection["kept"]
    cadence_state = cadence(paths, samples)
    gate = evaluate_gate(samples)
    gate["cadence"] = cadence_state
    gate["filters"] = {key: value for key, value in selection.items() if key != "kept"}
    gate["damage_calibration"] = _damage_calibration_summary(paths.damage_calibration)
    due = force or cadence_state["due"]
    if not due:
        if write:
            _append_history(paths.history, {**gate, "status": "not_due",
                                            "gate_status": gate["status"]})
        return {**gate, "action": "skipped_not_due"}
    if gate["status"] != "passed":
        if write:
            _append_history(paths.history, gate)
        return {**gate, "action": "wrote_nothing"}
    train, _eval = split(samples)
    model = fit_calibrator(train)
    if model is None:
        return {**gate, "action": "wrote_nothing",
                "reasons": list(gate.get("reasons") or []) +
                ["the training slice could not be fitted twice"]}
    model.source = "live_online"
    model.trained_at = time.time()
    model.metrics = {
        "candidate": gate["candidate"],
        "baseline": gate["baseline"],
        "delta_auc": gate["delta_auc"],
        "perm_p": gate["perm_p"],
        "eval_n": gate["eval_n"],
        "thresholds": gate["thresholds"],
        "damage_calibration": gate.get("damage_calibration"),
    }
    payload = json.dumps(model.to_dict(), indent=1, sort_keys=True)
    version = _stamp(payload)
    if write:
        paths.versions.mkdir(parents=True, exist_ok=True)
        version_path = paths.versions / (version + ".json")
        version_path.write_text(payload, encoding="utf-8")
        previous = _read_pointer(paths.pointer).get("version")
        paths.calibrator.parent.mkdir(parents=True, exist_ok=True)
        paths.calibrator.write_text(payload, encoding="utf-8")
        paths.pointer.write_text(json.dumps({
            "version": version, "previous": previous, "at": time.time(),
        }, indent=1), encoding="utf-8")
        _append_history(paths.history, {
            **gate, "status": "promoted", "version": version,
            "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        })
    return {**gate, "action": "promoted", "version": version}


def rollback(paths: Paths = Paths()) -> Dict[str, Any]:
    pointer = _read_pointer(paths.pointer)
    previous = pointer.get("previous")
    if not previous:
        return {"status": "no_previous", "current": pointer.get("version")}
    source = paths.versions / (str(previous) + ".json")
    if not source.exists():
        return {"status": "missing_version", "version": previous}
    text = source.read_text(encoding="utf-8")
    paths.calibrator.write_text(text, encoding="utf-8")
    paths.pointer.write_text(json.dumps({
        "version": previous, "previous": None, "at": time.time(),
    }, indent=1), encoding="utf-8")
    _append_history(paths.history, {"status": "rollback", "version": previous,
                                    "at": time.time()})
    return {"status": "rolled_back", "version": previous}


def shadow_report(paths: Paths = Paths(),
                  filters: Filters = Filters()) -> Dict[str, Any]:
    rows = read_rows(paths.rows)
    selection = eligible_samples(rows, filters)
    samples = selection["kept"]
    _train, eval_rows = split(samples)
    funnel = {key: value for key, value in selection.items() if key != "kept"}
    incumbent = FightCalibrator.load(paths.calibrator)
    if incumbent is None:
        labels = [1.0 if row["won"] else 0.0 for row in eval_rows
                  if row.get("_raw") is not None]
        scores = [float(row["_raw"]) for row in eval_rows
                  if row.get("_raw") is not None]
        return {"status": "no_incumbent", "filters": funnel,
                "eval_n": len(labels),
                "baseline": {"auc": auc(labels, scores), "brier": brier(labels, scores)}}
    rows: List[Dict[str, Any]] = []
    labels: List[float] = []
    predicted: List[float] = []
    base_labels: List[float] = []
    baseline: List[float] = []
    for row in eval_rows:
        p_win = incumbent.predict(row["features"])
        if p_win is None:
            continue
        rows.append({"at": row["_at"], "won": row["won"],
                     "p_win": round(float(p_win), 4),
                     "raw_score": row.get("_raw")})
        labels.append(1.0 if row["won"] else 0.0)
        predicted.append(float(p_win))
        if row.get("_raw") is not None:
            base_labels.append(1.0 if row["won"] else 0.0)
            baseline.append(float(row["_raw"]))
    report = calibration_report(incumbent, eval_rows)
    return {"status": "incumbent", "filters": funnel,
            "damage_calibration": _damage_calibration_summary(paths.damage_calibration),
            "eval_n": len(labels),
            "auc": auc(labels, predicted),
            "brier": brier(labels, predicted),
            "ece": report.get("ece"),
            "baseline": {
                "auc": auc(base_labels, baseline),
                "brier": brier(base_labels, baseline),
            } if baseline else None,
            "fights": rows[:20]}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="combat.online_learn",
        description="Online, gated training of the fight win probability.")
    parser.add_argument("--rows", default=str(DEFAULT_ROWS))
    parser.add_argument("--calibrator", default=str(DEFAULT_CALIBRATOR))
    parser.add_argument("--check", action="store_true",
                        help="evaluate the gate and print it; write nothing")
    parser.add_argument("--run", action="store_true",
                        help="promote when the cadence is due and the gate passes")
    parser.add_argument("--force", action="store_true",
                        help="ignore the cadence; the gate still applies")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--shadow-report", action="store_true")
    parser.add_argument("--strict-members", action="store_true",
                        help="also require priced monster facts (unknown_members == 0)")
    parser.add_argument("--allow-scripted", action="store_true",
                        help="include fights the estimate marks tutorial-authorised")
    args = parser.parse_args(list(argv) if argv is not None else None)
    filters = Filters(strict_members=bool(args.strict_members),
                      drop_scripted=not bool(args.allow_scripted))
    calibrator = Path(args.calibrator)
    paths = Paths(rows=Path(args.rows), calibrator=calibrator,
                  versions=calibrator.parent / "fight_calibrator.versions",
                  history=calibrator.parent / "fight_calibrator.history.jsonl",
                  pointer=calibrator.parent / "fight_calibrator.incumbent.json")
    if args.rollback:
        print(json.dumps(rollback(paths), indent=1, sort_keys=True))
        return 0
    if args.shadow_report:
        print(json.dumps(shadow_report(paths, filters), indent=1, sort_keys=True))
        return 0
    write = bool(args.run)
    verdict = run(paths, write=write, force=bool(args.force), filters=filters)
    print(json.dumps(verdict, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
