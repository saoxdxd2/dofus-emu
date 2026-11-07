"""Close a finished session: report it, rebuild the corpus, clean it, learn, record.

This is the loop the project was missing: every artefact existed (a session reporter,
a transition builder, a janitor, a gated fight learner) but nothing ran them in
order after a run, so "what did the last session teach the bot?" had no answer that
could be checked. One close writes one ledger row; every step is isolated, so a
failure in one is recorded and the next still runs.

Steps, in this order because each one feeds the next:

1. **report** - `tools.session_report.summarise` over the logs that changed since
   the last close. The delta against the previous close is what belongs to this
   session; a whole-file total would repeat every earlier session forever.
2. **rebuild** - `tools.build_live_transitions` over all live logs, temporal split.
   Skipped when nothing changed, throttled to `--rebuild-interval-s` so a burst of
   short sessions does not rescan 230k rows each time.
3. **janitor** - `tools.data_janitor` in write mode: the clean corpus is what a
   trainer may read, and the report names every exclusion and the drift.
4. **move** - `ai/train_move_head_gated.py --data data/transitions_clean`: the live
   move head is retrained over three seeds on the cleaned corpus. The gate uses the
   **median** seed's temporal-test AUC and the **worst** seed's ECE (five seeds span
   AUC 0.603-0.820 on this corpus, so one draw is not evidence), and requires 200
   test actions; only then is an artifact published. Measured 2026-10-31: 123 test
   actions, median AUC 0.787 over seeds 0-2, worst ECE 0.315 -- refused on rows and
   calibration, and the refusal lands in the ledger.
5. **learn** - `combat.online_learn.run`, which has its own event-driven cadence and
   gate; this step only gives it the chance to run, it never forces a promotion.
6. **ledger** - one JSON line in `data/session_closes.jsonl`, plus per-log offsets
   and the last summary in `data/session_close_state.json`.
7. **evidence** - the same summariser over the *live* per-instance logs under
   `%LOCALAPPDATA%/DofusFarm`. Those four files are what §12.2's budget numbers are read
   from and they are not in the repository (G96), so one row per capture -- whole-file
   totals per instance, the aggregate, and the size/mtime each number came from -- is
   appended to `data/evidence/live_sessions.jsonl`. This runs before the no-new-rows
   short-circuit, because a session can grow only the live logs, and it stays silent while
   no live log has moved rather than re-reading 82 MB for the same answer.

Fail-open is explicit: every step returns `{ok, seconds, ...}` and an exception is
captured as `ok: false` with the traceback's last line. The exit code is 0 unless
the ledger itself cannot be written, because a session must not fail over its own
bookkeeping.

    python -m tools.session_close              # close once, run what is due
    python -m tools.session_close --force      # ignore the no-new-rows check
    python -m tools.session_close --no-learn   # report/rebuild/clean only
    python -m tools.session_close --no-evidence  # do not touch the live log directory
    python -m tools.session_close --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VERSION = 1
DEFAULT_LOGS = REPO_ROOT / "trajectories"
DEFAULT_TRANSITIONS = REPO_ROOT / "data" / "transitions"
DEFAULT_CLEAN = REPO_ROOT / "data" / "transitions_clean"
DEFAULT_REFERENCE = REPO_ROOT / "data" / "tool_sft_v3"
DEFAULT_LEDGER = REPO_ROOT / "data" / "session_closes.jsonl"
DEFAULT_STATE = REPO_ROOT / "data" / "session_close_state.json"
DEFAULT_MOVE_OUT = REPO_ROOT / "models" / "foresight_live_v1"
DEFAULT_LOCK = REPO_ROOT / "data" / "session_close.lock"
# G96: the numbers §12.2 is built from come from the per-instance logs the loop writes into
# %LOCALAPPDATA%, which is outside the repository and untracked, so a disk cleanup makes the
# headline budget unfalsifiable. Each close copies the *derived* totals -- not 82 MB of rows --
# into a tracked file, and the size/mtime signature says which capture they came from.
DEFAULT_EVIDENCE = (Path(os.environ["LOCALAPPDATA"]) / "DofusFarm"
                    if os.environ.get("LOCALAPPDATA") else None)
DEFAULT_EVIDENCE_OUT = REPO_ROOT / "data" / "evidence" / "live_sessions.jsonl"
EVIDENCE_PATTERN = "autopilot.dofus-*.jsonl"
EVIDENCE_FIELDS = ("sessions", "wall_minutes", "steps", "ok", "failed",
                   "manual_interventions", "intervention_rows")
LOCK_STALE_S = 900.0
DEFAULT_REBUILD_INTERVAL_S = 300.0
SUMMARY_FIELDS = ("wall_minutes", "steps", "ok", "failed", "objectives_completed",
                  "objectives_done", "quests_completed", "maps", "ready_steps",
                  "manual_interventions")


@dataclass
class CloseConfig:
    logs: Path = DEFAULT_LOGS
    transitions_out: Path = DEFAULT_TRANSITIONS
    clean_out: Path = DEFAULT_CLEAN
    reference: Path = DEFAULT_REFERENCE
    ledger: Path = DEFAULT_LEDGER
    state: Path = DEFAULT_STATE
    rebuild_interval_s: float = DEFAULT_REBUILD_INTERVAL_S
    learn: bool = True
    move: bool = True
    move_out: Path = DEFAULT_MOVE_OUT
    lock: Optional[Path] = DEFAULT_LOCK
    force: bool = False
    now: Optional[float] = None
    evidence: Optional[Path] = DEFAULT_EVIDENCE
    evidence_out: Path = DEFAULT_EVIDENCE_OUT
    evidence_force: bool = False
    # Injectable for tests: (rows_path, calibrator_path) -> result dict, and a
    # report function so a close can be exercised without 230k real log rows.
    learner: Optional[Callable[[], Dict[str, Any]]] = None
    move_trainer: Optional[Callable[[], Dict[str, Any]]] = None
    reporter: Optional[Callable[[Path], Dict[str, Any]]] = None
    evidence_reporter: Optional[Callable[[Path], Dict[str, Any]]] = None
    builder: Optional[Callable[[], Dict[str, Any]]] = None
    janitor: Optional[Callable[[], Dict[str, Any]]] = None


def _acquire_lock(path: Path, now: float,
                  stale_s: float = LOCK_STALE_S) -> Optional[Dict[str, Any]]:
    """Take the close lock; return None on success, the holder's info when busy.

    One daemon runs one Autopilot per instance, and two instances can finish at the
    same moment; both would rebuild the same corpus and append to the same ledger.
    The lock is a file because the closes are separate processes. A holder older
    than `stale_s` is a crash, not a running close, and is reclaimed.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in (0, 1):
        try:
            descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                holder = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                holder = {}
            age = now - float(holder.get("at") or 0)
            if 0 <= age < stale_s:
                return holder if isinstance(holder, dict) else {}
            try:
                path.unlink()
            except OSError:
                return holder if isinstance(holder, dict) else {}
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "at": now}, handle)
        return None
    return {}


def log_paths(directory: Path) -> List[Path]:
    """Live logs only. `play_*.jsonl` are wire captures with no step rows; a close
    that summarised them would report sessions with `steps 0` every time."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.jsonl")
                  if not path.name.startswith("play_"))


def _read_state(path: Path) -> Dict[str, Any]:
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _signature(path: Path) -> Dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {}
    return {"size": stat.st_size, "mtime": round(stat.st_mtime, 3)}


def changed_logs(paths: Sequence[Path], state: Dict[str, Any]) -> List[Path]:
    known = state.get("logs") if isinstance(state.get("logs"), dict) else {}
    out: List[Path] = []
    for path in paths:
        signature = _signature(path)
        if not signature:
            continue
        previous = known.get(str(path)) or {}
        if previous.get("size") != signature["size"] or \
                previous.get("mtime") != signature["mtime"]:
            out.append(path)
    return out


def _relative(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def artifact_census(move_out: Path,
                    models_dir: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Which learned artifacts were in effect, so a session can be paired later.

    `move_head` is the head this close trains; it is measured but not consumed by the
    live loop (`resume.md`: "off, measured, not wired"), and `foresight` is the
    deployed ranking model the stacker actually loads. Taking this before the
    learning steps and after them keeps "before" as the state that drove the session.
    """
    models = Path(models_dir) if models_dir is not None else REPO_ROOT / "models"
    files = {"move_head": Path(move_out) / "foresight.json",
             "foresight": models / "foresight_v1" / "foresight.json",
             "fight_calibrator": models / "fight_calibrator.json",
             "tool_stack": models / "tool_stack.json"}
    out: Dict[str, Dict[str, Any]] = {}
    for name, path in files.items():
        entry: Dict[str, Any] = {"path": _relative(path)}
        try:
            data = path.read_bytes()
        except OSError:
            entry["exists"] = False
        else:
            entry["exists"] = True
            entry["sha256"] = hashlib.sha256(data).hexdigest()[:16]
            entry["bytes"] = len(data)
        out[name] = entry
    return out


# Counters that only ever grow inside one growing file. A negative change means the
# file shrank (rotation/cleanup), not that a session un-happened. The quest counters
# are deliberately absent: `objectives_completed` can fall when the character or the
# counter's meaning changes, and that is a real reading, not a rotation.
MONOTONIC_FIELDS = ("wall_minutes", "steps", "ok", "failed", "maps", "ready_steps",
                    "manual_interventions", "objectives_done")


def _delta(current: Dict[str, Any], previous: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """This session's share of a growing log, or `rotated` when the file shrank."""
    if not previous:
        out = {key: current.get(key) for key in SUMMARY_FIELDS}
        out["baseline"] = True
        return out
    out: Dict[str, Any] = {}
    for key in SUMMARY_FIELDS:
        now_value, was_value = current.get(key), previous.get(key)
        if isinstance(now_value, (int, float)) and isinstance(was_value, (int, float)):
            out[key] = round(float(now_value) - float(was_value), 2)
        else:
            out[key] = None
    if any(isinstance(out.get(key), (int, float)) and out[key] < 0
           for key in MONOTONIC_FIELDS):
        return {"rotated": True}
    return out


def _step(name: str, fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        result = fn()
        if not isinstance(result, dict):
            result = {"value": result}
        result.setdefault("ok", True)
    except Exception as exc:  # noqa: BLE001 - a close must never fail a session
        result = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc),
                  "trace": traceback.format_exc(limit=2).strip().splitlines()[-1]}
    result["seconds"] = round(time.perf_counter() - started, 2)
    result["step"] = name
    return result


def _report(path: Path) -> Dict[str, Any]:
    from tools import session_report
    return session_report.summarise(path)


def evidence_paths(directory: Optional[Path]) -> List[Path]:
    """The live per-instance autopilot logs, newest name order.

    Only `autopilot.dofus-*.jsonl`: the single `autopilot.jsonl` is the pre-daemon log and
    the `play_*.jsonl` captures hold no step rows, so either would add a total nobody
    quotes to §12.2.
    """
    if not directory:
        return []
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(directory.glob(EVIDENCE_PATTERN))


def _evidence(paths: Sequence[Path],
              reporter: Callable[[Path], Dict[str, Any]]) -> Dict[str, Any]:
    """Whole-file totals per instance, plus the aggregate the snapshot quotes.

    Whole-file, not delta: §12.2 answers "what do these four logs say in total", and a row
    that only carried this close's share could not be added up by a reader of a clone.
    """
    rows: List[Dict[str, Any]] = []
    for path in paths:
        summary = reporter(path) or {}
        row: Dict[str, Any] = {"instance": path.name, "source": str(path),
                               "signature": _signature(path)}
        row.update({key: summary.get(key) for key in EVIDENCE_FIELDS})
        row["recoveries"] = dict(summary.get("recoveries") or {})
        rows.append(row)
    totals: Dict[str, Any] = {}
    for key in EVIDENCE_FIELDS:
        values = [row.get(key) for row in rows]
        totals[key] = (round(sum(float(v or 0) for v in values), 1)
                       if key == "wall_minutes" else sum(int(v or 0) for v in values))
    actions: Dict[str, int] = {}
    for row in rows:
        for action, count in (row.get("recoveries") or {}).items():
            actions[action] = int(actions.get(action, 0)) + int(count or 0)
    totals["recoveries"] = actions
    totals["recovery_rows"] = sum(actions.values())
    return {"files": len(rows), "instances": rows, "totals": totals}


def _last_evidence_signatures(path: Path) -> Dict[str, Any]:
    """The size/mtime of every instance log in the most recent capture."""
    from agent import jsonio

    last: Optional[Dict[str, Any]] = None
    for row in jsonio.iter_rows(Path(path)):
        last = row
    if not isinstance(last, dict):
        return {}
    return {row.get("instance"): row.get("signature")
            for row in (last.get("instances") or []) if isinstance(row, dict)}


def _capture_evidence(config: "CloseConfig", at: str) -> Dict[str, Any]:
    """Copy the live logs' derived totals into the tracked evidence file.

    Only when a log's signature moved: summarising these four files means reading 82 MB, and
    an idle close has no business paying that to write the same numbers again. The cost of the
    read is written into the row, because the loop kills this process after a timeout and the
    only honest way to know the capture still fits is to have measured it.
    """
    paths = evidence_paths(config.evidence)
    if not paths:
        return {"files": 0, "skipped": "no %s under %s" % (EVIDENCE_PATTERN, config.evidence)}
    seen = _last_evidence_signatures(config.evidence_out)
    changed = [path.name for path in paths if seen.get(path.name) != _signature(path)]
    if not changed and not config.evidence_force:
        return {"files": len(paths), "changed": [], "written": None,
                "skipped": "every live log matches the last capture"}
    started = time.perf_counter()
    out = _evidence(paths, config.evidence_reporter or _report)
    out["changed"] = changed
    out["capture_seconds"] = round(time.perf_counter() - started, 2)
    _append_ledger(config.evidence_out, dict(at=at, **out))
    out["written"] = str(_relative(config.evidence_out))
    return out


def _manifest_fields(manifest: Dict[str, Any]) -> Dict[str, Any]:
    return {"rows": manifest.get("rows"), "segments": manifest.get("segments"),
            "split_rows": manifest.get("split_rows"),
            "split_policy": manifest.get("split_policy"),
            "actions_moved": manifest.get("actions_moved"),
            "actions": manifest.get("actions"),
            "labeler_sha": manifest.get("labeler_sha")}


def _build(transitions_out: Path, logs_dir: Path) -> Dict[str, Any]:
    import contextlib
    import io
    from tools import build_live_transitions
    paths = log_paths(logs_dir)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = build_live_transitions.main([
            "--log", *[str(path) for path in paths],
            "--out", str(transitions_out), "--split", "temporal"])
    if code != 0:
        raise RuntimeError("build_live_transitions exited %d" % code)
    manifest = json.loads((Path(transitions_out) / "manifest.json").read_text(
        encoding="utf-8"))
    return _manifest_fields(manifest)


def _clean(transitions_out: Path, clean_out: Path,
           reference: Path) -> Dict[str, Any]:
    from tools import data_janitor
    report = data_janitor.run(transitions_out, clean_out, reference, write_out=True)
    return {"kept": report["kept"], "quarantine": report["quarantine"],
            "fatal": report["schema"]["fatal"],
            "warnings": report["schema"]["warnings"],
            "in_coverage": report["drift"].get("in_coverage"),
            "drift_flags": len(report["drift"].get("flags") or []),
            "written": report.get("written")}


def _learn() -> Dict[str, Any]:
    from combat import online_learn
    result = online_learn.run(online_learn.Paths(), write=True, force=False,
                              filters=online_learn.Filters())
    return {"action": result.get("action"), "status": result.get("status"),
            "n": result.get("n"), "need": result.get("need"),
            "cadence": result.get("cadence"),
            # Why the evidence was ineligible belongs in the same row as the count.
            "filters": result.get("filters") or {}}


def _train_move(data_dir: Path, out_dir: Path, seeds: int = 3,
                epochs: int = 30, timeout_s: int = 300) -> Dict[str, Any]:
    """Retrain the live move head over seeds; read back the aggregate verdict.

    Out of process because the trainer imports torch and the close runs at the end
    of a session inside a host process that should not grow a training stack.
    Measured 2026-10-31: five seeds over the same 351 actions spanned test AUC
    0.603-0.820, so the gate uses the median AUC and the worst calibration.
    """
    import subprocess
    metrics_path = Path(out_dir) / "metrics_move_head.json"
    try:
        metrics_path.unlink()
    except OSError:
        pass
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "ai" / "train_move_head_gated.py"),
         "--data", str(data_dir), "--out", str(out_dir), "--seeds", str(seeds),
         "--epochs", str(epochs)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=timeout_s)
    if proc.returncode != 0:
        raise RuntimeError("train_move_head_gated exited %d: %s"
                           % (proc.returncode,
                              (proc.stderr or proc.stdout).strip()[-300:]))
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    verdict = payload.get("gate") or {}
    runs = payload.get("seeds") or []
    return {"promoted": bool(verdict.get("promoted")),
            "reasons": verdict.get("reasons") or [],
            "seeds": verdict.get("seeds"),
            "test_rows": verdict.get("test_rows"),
            "test_auc": verdict.get("median_auc"),
            "test_ece": verdict.get("worst_ece"),
            "seed_aucs": [(run.get("test") or {}).get("auc_moved") for run in runs],
            "seed_eces": [(run.get("test") or {}).get("ece_moved") for run in runs],
            "artifact": payload.get("artifact")}


def _append_ledger(path: Path, record: Dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _scorecard(record: Dict[str, Any], ledger: Path) -> Dict[str, Any]:
    """Attach the goal's scoreboard to the row, fail-open.

    The verdict answers the two objective questions over the whole ledger; computing
    it inside the close makes the loop measure itself instead of waiting for someone
    to run `tools.progress_report.py` later.
    """
    try:
        from tools import progress_report
        report = progress_report.summarise(
            progress_report.read_ledger(Path(ledger)) + [record])
        record["verdict"] = progress_report.verdict(report)
        record["progress"] = {
            "sessions_with_new_rows": report.get("sessions_with_new_rows"),
            "objective_progress": report.get("objective_progress"),
            "corpus_rate": (report.get("corpus") or {}).get("rate"),
            "trend": report.get("trend")}
    except Exception as exc:  # noqa: BLE001 - measuring must not fail a close
        record["verdict"] = None
        record["progress"] = {"error": str(exc)[:160]}
    return record


def _write_state(path: Path, state: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def close(config: CloseConfig = CloseConfig()) -> Dict[str, Any]:
    """Serialize closes, then run the pipeline; a concurrent close stands down."""
    now = time.time() if config.now is None else float(config.now)
    holder = _acquire_lock(config.lock, now) if config.lock else None
    if holder is not None:
        return {"version": VERSION,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "ts": round(now, 3), "action": "busy", "holder": holder,
                "seconds": 0.0, "steps": {}}
    try:
        return _close_body(config, now)
    finally:
        if config.lock:
            Path(config.lock).unlink(missing_ok=True)


def _close_body(config: CloseConfig, now: float) -> Dict[str, Any]:
    started = time.perf_counter()
    state = _read_state(config.state)
    paths = log_paths(config.logs)
    fresh = changed_logs(paths, state) if not config.force else list(paths)
    record: Dict[str, Any] = {
        "version": VERSION,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "ts": round(now, 3),
        "logs": [path.name for path in fresh],
        "new_bytes": sum(max(0, _signature(path).get("size", 0)
                             - ((state.get("logs") or {}).get(str(path)) or {}).get(
                                 "size", 0))
                         for path in fresh),
        "steps": {},
    }
    # Runs before the no_new_rows short-circuit: a session can grow only the live logs, and
    # the tracked capture is the point of the step (G96).
    record["steps"]["evidence"] = _step(
        "evidence", lambda: _capture_evidence(config, record["at"]))
    if not fresh and not config.force:
        record["action"] = "no_new_rows"
        record["seconds"] = round(time.perf_counter() - started, 2)
        _append_ledger(config.ledger, _scorecard(record, config.ledger))
        return record

    census_before = artifact_census(config.move_out)

    # 1. report: only the changed files, and the delta against their last close.
    reporter = config.reporter or _report
    last_logs = state.get("logs") if isinstance(state.get("logs"), dict) else {}
    summaries: Dict[str, Any] = {}
    deltas: Dict[str, Any] = {}
    report_step = _step("report", lambda: {
        "files": len(fresh),
        "summaries": {path.name: (reporter(path) or {}) for path in fresh},
    })
    if report_step.get("ok"):
        by_name = {path.name: path for path in fresh}
        for name, summary in (report_step.get("summaries") or {}).items():
            prior = (last_logs.get(str(by_name.get(name))) or {}).get("report")
            summaries[name] = summary
            deltas[name] = _delta(summary, prior)
        report_step["deltas"] = deltas
        # Self-describing rows: a later reader can tell a whole-file baseline from
        # a session delta without guessing from the values.
        report_step["baseline_files"] = sum(
            1 for delta in deltas.values() if delta.get("baseline"))
        report_step["rotated_files"] = sum(
            1 for delta in deltas.values() if delta.get("rotated"))
        report_step.pop("summaries", None)
    record["steps"]["report"] = report_step

    # 2. rebuild: throttled, and skipped for a close caused only by bookkeeping.
    last_rebuild = state.get("last_rebuild_at")
    due = (config.force or last_rebuild is None
           or now - float(last_rebuild) >= config.rebuild_interval_s)
    if not due:
        build_step = {"ok": True, "skipped": "interval",
                      "seconds": 0.0, "step": "rebuild"}
    elif config.builder is not None:
        build_step = _step("rebuild", config.builder)
    else:
        build_step = _step("rebuild", lambda: _build(config.transitions_out,
                                                      config.logs))
    record["steps"]["rebuild"] = build_step

    # 3. janitor: clean exactly the corpus that was rebuilt.
    if build_step.get("skipped") or not due:
        clean_step = {"ok": True, "skipped": "no_rebuild",
                      "seconds": 0.0, "step": "janitor"}
    elif config.janitor is not None:
        clean_step = _step("janitor", config.janitor)
    elif build_step.get("ok"):
        clean_step = _step("janitor", lambda: _clean(
            config.transitions_out, config.clean_out, config.reference))
    else:
        clean_step = {"ok": True, "skipped": "build_failed",
                      "seconds": 0.0, "step": "janitor"}
    record["steps"]["janitor"] = clean_step

    # 4. move: retrain on the cleaned corpus. Only after a real rebuild, so the
    #    verdict always describes the corpus the ledger just recorded.
    if not config.move:
        move_step = {"ok": True, "skipped": "disabled", "seconds": 0.0,
                     "step": "move"}
    elif clean_step.get("skipped"):
        move_step = {"ok": True, "skipped": "no_clean_rebuild", "seconds": 0.0,
                     "step": "move"}
    elif not clean_step.get("ok"):
        move_step = {"ok": True, "skipped": "janitor_failed", "seconds": 0.0,
                     "step": "move"}
    elif config.move_trainer is not None:
        move_step = _step("move", config.move_trainer)
    elif (Path(config.clean_out) / "train.jsonl").is_file():
        move_step = _step("move", lambda: _train_move(config.clean_out,
                                                      config.move_out))
    else:
        move_step = {"ok": True, "skipped": "no_clean_corpus", "seconds": 0.0,
                     "step": "move"}
    record["steps"]["move"] = move_step

    # 5. learn: the cadence and the gate live in the learner.
    if config.learn:
        learner = config.learner or _learn
        record["steps"]["learn"] = _step("learn", learner)
    else:
        record["steps"]["learn"] = {"ok": True, "skipped": "disabled",
                                    "seconds": 0.0, "step": "learn"}

    record["artifacts"] = {"before": census_before,
                           "after": artifact_census(config.move_out)}

    # 6. persist: per-log offsets and last summaries, then the ledger.
    new_logs: Dict[str, Any] = {}
    for path in paths:
        entry = dict(last_logs.get(str(path)) or {})
        entry.update(_signature(path))
        if path.name in summaries:
            entry["report"] = {key: summaries[path.name].get(key)
                               for key in SUMMARY_FIELDS}
        new_logs[str(path)] = entry
    state = {"version": VERSION, "logs": new_logs,
             "last_close_at": now,
             "last_rebuild_at": now if (due and build_step.get("ok")
                                        and not build_step.get("skipped"))
             else last_rebuild,
             "closes": int(state.get("closes") or 0) + 1}
    record["action"] = "closed"
    record["seconds"] = round(time.perf_counter() - started, 2)
    try:
        _write_state(config.state, state)
        _append_ledger(config.ledger, _scorecard(record, config.ledger))
        record["ledger"] = str(config.ledger)
    except OSError as exc:
        record["ledger_error"] = "%s: %s" % (type(exc).__name__, exc)
    return record


def summary(record: Dict[str, Any]) -> str:
    if record.get("action") == "busy":
        return ("another close holds the lock (%s); this one stood down"
                % record.get("holder"))
    if record.get("action") == "no_new_rows":
        return "no new rows since the last close (%s)" % record.get("at")
    steps = record.get("steps") or {}
    lines = ["session close %s (%.2fs) logs %s"
             % (record.get("at"), record.get("seconds") or 0.0,
                ", ".join(record.get("logs") or []) or "-")]
    report = steps.get("report") or {}
    evidence = steps.get("evidence") or {}
    if evidence.get("written"):
        totals = evidence.get("totals") or {}
        lines.append("  evidence: %s live log(s) -> %s: %s steps, %s ok, %s interventions, "
                     "%s recovery rows, %.1f min"
                     % (evidence.get("files"), evidence.get("written"),
                        totals.get("steps"), totals.get("ok"),
                        totals.get("manual_interventions"),
                        totals.get("recovery_rows"), totals.get("wall_minutes") or 0.0))
    elif evidence.get("skipped"):
        lines.append("  evidence: skipped (%s)" % evidence["skipped"])
    elif evidence and not evidence.get("ok"):
        lines.append("  evidence: FAILED %s" % evidence.get("error"))
    if report.get("ok") and report.get("deltas"):
        for name, delta in (report.get("deltas") or {}).items():
            if delta.get("rotated"):
                lines.append("  report %s: rotated -- delta unavailable" % name)
            else:
                lines.append(
                    "  report %s%s: steps %s, objectives %s, quests %s, failed %s, "
                    "maps %s, %.1f min"
                    % (name, " (baseline)" if delta.get("baseline") else "",
                       delta.get("steps"), delta.get("objectives_completed"),
                       delta.get("quests_completed"), delta.get("failed"),
                       delta.get("maps"), delta.get("wall_minutes") or 0.0))
    elif report:
        lines.append("  report: FAILED %s" % report.get("error"))
    build = steps.get("rebuild") or {}
    if build.get("skipped"):
        lines.append("  rebuild: skipped (%s)" % build["skipped"])
    elif build.get("ok"):
        lines.append("  rebuild: %s rows, %s segments, split %s"
                     % (build.get("rows"), build.get("segments"), build.get("split_rows")))
    else:
        lines.append("  rebuild: FAILED %s" % build.get("error"))
    clean = steps.get("janitor") or {}
    if clean.get("skipped"):
        lines.append("  janitor: skipped (%s)" % clean["skipped"])
    elif clean.get("ok"):
        kept = clean.get("kept") or {}
        lines.append("  janitor: kept %s (actions %s), quarantined %s %s, fatal %s, "
                     "in coverage %s"
                     % (kept.get("total"), kept.get("actions"),
                        (clean.get("quarantine") or {}).get("total"),
                        (clean.get("quarantine") or {}).get("by_reason"),
                        clean.get("fatal") or "none", clean.get("in_coverage")))
    else:
        lines.append("  janitor: FAILED %s" % clean.get("error"))
    move = steps.get("move") or {}
    if move.get("skipped"):
        lines.append("  move: skipped (%s)" % move["skipped"])
    elif move.get("ok"):
        lines.append("  move: promoted %s, test %s rows AUC %s ECE %s%s"
                     % (move.get("promoted"), move.get("test_rows"),
                        move.get("test_auc"), move.get("test_ece"),
                        (" (" + "; ".join(move.get("reasons") or []) + ")")
                        if move.get("reasons") else ""))
    else:
        lines.append("  move: FAILED %s" % move.get("error"))
    learn = steps.get("learn") or {}
    if learn.get("skipped"):
        lines.append("  learn: skipped (%s)" % learn["skipped"])
    elif learn.get("ok"):
        filters = learn.get("filters") or {}
        dropped = filters.get("dropped") or {}
        lines.append("  learn: %s, gate %s, n %s/%s%s"
                     % (learn.get("action"), learn.get("status"),
                        learn.get("n"), learn.get("need"),
                        (" (dropped %s)" % dropped) if dropped else ""))
    else:
        lines.append("  learn: FAILED %s" % learn.get("error"))
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Close a finished session: report, rebuild, clean, learn, record.")
    parser.add_argument("--logs", type=Path, default=DEFAULT_LOGS,
                        help="directory of trajectory JSONL logs")
    parser.add_argument("--out", type=Path, default=DEFAULT_TRANSITIONS)
    parser.add_argument("--clean", type=Path, default=DEFAULT_CLEAN)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--rebuild-interval-s", type=float,
                        default=DEFAULT_REBUILD_INTERVAL_S)
    parser.add_argument("--move-out", type=Path, default=DEFAULT_MOVE_OUT)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE,
                        help="directory holding the live autopilot.dofus-*.jsonl logs")
    parser.add_argument("--evidence-out", type=Path, default=DEFAULT_EVIDENCE_OUT,
                        help="tracked file the derived live totals are copied into")
    parser.add_argument("--no-evidence", action="store_true",
                        help="do not read or copy the live logs at all")
    parser.add_argument("--evidence-force", action="store_true",
                        help="re-read the live logs even when nothing moved (measures the capture cost)")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-learn", action="store_true")
    parser.add_argument("--no-move", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    config = CloseConfig(
        logs=args.logs, transitions_out=args.out, clean_out=args.clean,
        reference=args.reference, ledger=args.ledger, state=args.state,
        rebuild_interval_s=args.rebuild_interval_s, learn=not args.no_learn,
        move=not args.no_move, move_out=args.move_out, force=args.force,
        evidence=None if args.no_evidence else args.evidence,
        evidence_out=args.evidence_out, evidence_force=args.evidence_force)
    record = close(config)
    if args.json:
        print(json.dumps(record, indent=1, sort_keys=True))
    elif not args.quiet:
        print(summary(record))
    return 1 if record.get("ledger_error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
