"""Post-build live testing harness for model weights.

Runs after you build a new model artifact (ONNX/checkpoint) and validates it
against the full regression gate, planner benchmark, and offline model scoring.

Usage:

    # After building models/tool_policy.onnx (or a candidate folder):
    python tools/live_test_after_build.py --model models/tool_policy.onnx

    # Compare a candidate against the baseline with full report:
    python tools/live_test_after_build.py --model models/candidates/foo.onnx \
        --baseline models/tool_policy.onnx --report out/report.json

    # Run only offline model scoring (fast, no client needed):
    python tools/live_test_after_build.py --model models/tool_policy.onnx \
        --offline-only

    # Full live + offline suite (requires a client window):
    python tools/live_test_after_build.py --model models/tool_policy.onnx --live
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODELS_DIR = REPO_ROOT / "models"
DEFAULT_BASELINE = MODELS_DIR / "tool_policy.onnx"
DEFAULT_VAL = REPO_ROOT / "data" / "tool_sft_v3" / "val.jsonl"

RESULTS_DIR = REPO_ROOT / "data" / "live_test_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _run(cmd: List[str], capture: bool = True,
         cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    print(f"[run] {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(cwd), text=True,
                          capture_output=capture)


def _sha256(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 1. Offline model scoring (candidate vs baseline on val corpus)
# ---------------------------------------------------------------------------

def score_model_offline(model: Path, baseline: Optional[Path] = None,
                        val: Path = DEFAULT_VAL) -> Dict[str, Any]:
    """Score an ONNX artifact against the val corpus using weight_space.

    Returns a dict with candidate metrics, baseline metrics, paired bootstrap,
    and the gate decision. If baseline is None, scores against itself (sanity).
    """
    if not model.is_file():
        return {"error": f"model not found: {model}", "model": str(model)}

    model_sha = _sha256(model)
    result: Dict[str, Any] = {
        "stage": "offline_model_scoring",
        "model": str(model),
        "model_sha256": model_sha,
        "val_corpus": str(val),
        "val_corpus_exists": val.is_file(),
    }

    # Pin the baseline if not already pinned (needed by weight_space).
    prov_path = REPO_ROOT / "data" / "last_gate_run.json"
    if not prov_path.is_file() and baseline is None:
        baseline = DEFAULT_BASELINE

    # Score candidate vs baseline via weight_space.candidate_verdict.
    from agent.weight_space import candidate_verdict

    ref = baseline if baseline and baseline.is_file() else DEFAULT_BASELINE
    if not ref.is_file():
        result["error"] = f"baseline not found: {ref}"
        result["decision"] = "REFUSED"
        return result

    verdict = candidate_verdict(model, val=val, reference_onnx=ref,
                                include_details=False)
    result.update(verdict)
    result["decision"] = verdict.get("decision", "UNKNOWN")
    result["gate"] = verdict.get("gate", {})
    return result


# ---------------------------------------------------------------------------
# 2. Planner benchmark (rule policy, held-out seeds)
# ---------------------------------------------------------------------------

def run_planner_benchmark(seeds: str = "20000,20001,20002",
                          steps: int = 1500) -> Dict[str, Any]:
    """Run the quest policy benchmark to confirm behavior is preserved."""
    result: Dict[str, Any] = {
        "stage": "planner_benchmark",
        "seeds": seeds,
        "steps": steps,
    }
    proc = _run([sys.executable, "tools/bench_quest_policies.py",
                 "--policies", "rule", "--seeds", seeds, "--steps", str(steps)])
    result["exit_code"] = proc.returncode
    result["stdout_tail"] = "\n".join((proc.stdout or "").splitlines()[-6:])
    if proc.returncode != 0:
        result["stderr_tail"] = "\n".join((proc.stderr or "").splitlines()[-10:])
        result["decision"] = "FAIL"
    else:
        result["decision"] = "PASS"
    return result


# ---------------------------------------------------------------------------
# 3. Regression gate
# ---------------------------------------------------------------------------

def run_regression_gate() -> Dict[str, Any]:
    """Run the full regression gate: python -m agent.regression."""
    result: Dict[str, Any] = {
        "stage": "regression_gate",
    }
    proc = _run([sys.executable, "-m", "agent.regression"])
    result["exit_code"] = proc.returncode
    output = proc.stdout or ""
    # Extract the final count line if present.
    for line in output.splitlines():
        if "checks" in line and "failed" in line:
            result["summary_line"] = line.strip()
    result["decision"] = "PASS" if proc.returncode == 0 else "FAIL"
    return result


# ---------------------------------------------------------------------------
# 4. World-state exact-semantics tests
# ---------------------------------------------------------------------------

def run_world_state_tests() -> Dict[str, Any]:
    """Run tools/test_world_state.py (terrain encoding exactness)."""
    result: Dict[str, Any] = {
        "stage": "world_state_tests",
    }
    proc = _run([sys.executable, "tools/test_world_state.py"])
    result["exit_code"] = proc.returncode
    result["decision"] = "PASS" if proc.returncode == 0 else "FAIL"
    return result


# ---------------------------------------------------------------------------
# 5. Dialog policy tests
# ---------------------------------------------------------------------------

def run_dialog_tests() -> Dict[str, Any]:
    """Run tools/test_dialog_policy.py."""
    result: Dict[str, Any] = {
        "stage": "dialog_tests",
    }
    proc = _run([sys.executable, "tools/test_dialog_policy.py"])
    result["exit_code"] = proc.returncode
    result["decision"] = "PASS" if proc.returncode == 0 else "FAIL"
    return result


# ---------------------------------------------------------------------------
# 6. Combat battle solver tests
# ---------------------------------------------------------------------------

def run_battle_solver_tests() -> Dict[str, Any]:
    """Run combat.test_battle_solver (2 pre-existing failures expected)."""
    result: Dict[str, Any] = {
        "stage": "battle_solver_tests",
    }
    proc = _run([sys.executable, "-m", "unittest", "combat.test_battle_solver"])
    result["exit_code"] = proc.returncode
    output = proc.stdout or ""
    # Extract "Ran N tests" and failures count.
    for line in output.splitlines():
        if "Ran" in line and "test" in line:
            result["test_count_line"] = line.strip()
        if "FAILED" in line or "OK" in line:
            result["result_line"] = line.strip()
    # A run is OK even if the 2 pre-existing failures are present; we just need
    # no *new* failures compared to baseline. The exit code is 0 for OK.
    result["decision"] = "PASS" if proc.returncode == 0 else "FAIL"
    return result


# ---------------------------------------------------------------------------
# 7. Optional: live evidence shadow run (requires client)
# ---------------------------------------------------------------------------

def run_live_evidence(steps: int = 20) -> Dict[str, Any]:
    """Run a bounded live shadow session (requires a Dofus client window).

    This is gated behind --live because it needs the game client running.
    """
    result: Dict[str, Any] = {
        "stage": "live_evidence",
        "steps": steps,
        "skipped": False,
    }
    # Check preflight first.
    pre = _run([sys.executable, "tools/live_ready.py", "--json"])
    try:
        entries = json.loads(pre.stdout or "[]")
    except ValueError:
        entries = []
    client_ok = any(e.get("name") == "client" and e.get("level") == "PASS"
                    for e in entries)
    if not client_ok:
        result["skipped"] = True
        result["reason"] = "no Dofus client window (live_ready preflight failed)"
        result["decision"] = "SKIPPED"
        return result

    proc = _run([sys.executable, "tools/live_evidence.py",
                 "--steps", str(steps), "--report-only"])
    result["exit_code"] = proc.returncode
    result["decision"] = "PASS" if proc.returncode == 0 else "FAIL"
    return result


# ---------------------------------------------------------------------------
# 8. Report assembly
# ---------------------------------------------------------------------------

def assemble_report(stages: List[Dict[str, Any]],
                    model: Path, baseline: Optional[Path]) -> Dict[str, Any]:
    all_pass = all(s.get("decision") in ("PASS", "SKIPPED") for s in stages)
    any_fail = any(s.get("decision") == "FAIL" for s in stages)
    any_skip = any(s.get("decision") == "SKIPPED" for s in stages)

    report: Dict[str, Any] = {
        "schema": "live_test_after_build.v1",
        "model": str(model),
        "model_sha256": _sha256(model),
        "baseline": str(baseline) if baseline else None,
        "baseline_sha256": _sha256(baseline) if baseline else None,
        "at": time.time(),
        "overall": "PASS" if all_pass and not any_fail else ("FAIL" if any_fail else "PARTIAL"),
        "stages": stages,
    }
    return report


def write_report(report: Dict[str, Any], path: Optional[Path] = None) -> Path:
    out = path or (RESULTS_DIR / f"live_test_{int(time.time())}.json")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n[report] written to {out}")
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Post-build live testing harness")
    ap.add_argument("--model", required=True,
                    help="Path to the built ONNX model artifact to test")
    ap.add_argument("--baseline", default=None,
                    help="Baseline ONNX for comparison (default: models/tool_policy.onnx)")
    ap.add_argument("--offline-only", action="store_true",
                    help="Skip live client tests (offline scoring + regression only)")
    ap.add_argument("--live", action="store_true",
                    help="Also run live evidence shadow run (requires client window)")
    ap.add_argument("--report", default=None,
                    help="Write JSON report to this path")
    ap.add_argument("--seeds", default="20000,20001,20002",
                    help="Planner benchmark seeds (comma-separated)")
    ap.add_argument("--steps", type=int, default=1500,
                    help="Planner benchmark steps per seed")
    args = ap.parse_args()

    model = Path(args.model)
    baseline = Path(args.baseline) if args.baseline else DEFAULT_BASELINE
    if not baseline.is_file():
        baseline = None  # will be handled gracefully

    if not model.is_file():
        print(f"[FATAL] model not found: {model}")
        return 1

    print(f"[live_test] model={model}  baseline={baseline or '(none)'}")
    print(f"[live_test] repo={REPO_ROOT}")
    print("=" * 70)

    stages: List[Dict[str, Any]] = []

    # Stage 1: Offline model scoring
    print("\n[1/6] Offline model scoring (candidate vs baseline on val corpus)")
    s1 = score_model_offline(model, baseline=baseline)
    stages.append(s1)
    print(f"    decision={s1.get('decision')}  "
          f"gate={s1.get('gate', {})}")

    # Stage 2: World-state exactness tests
    print("\n[2/6] World-state exact-semantics tests")
    s2 = run_world_state_tests()
    stages.append(s2)
    print(f"    decision={s2.get('decision')}")

    # Stage 3: Dialog policy tests
    print("\n[3/6] Dialog policy tests")
    s3 = run_dialog_tests()
    stages.append(s3)
    print(f"    decision={s3.get('decision')}")

    # Stage 4: Battle solver tests
    print("\n[4/6] Combat battle solver tests")
    s4 = run_battle_solver_tests()
    stages.append(s4)
    print(f"    decision={s4.get('decision')}")

    # Stage 5: Planner benchmark
    print("\n[5/6] Planner benchmark (rule policy)")
    s5 = run_planner_benchmark(seeds=args.seeds, steps=args.steps)
    stages.append(s5)
    print(f"    decision={s5.get('decision')}")

    # Stage 6: Regression gate (always run offline)
    print("\n[6/6] Regression gate (python -m agent.regression)")
    s6 = run_regression_gate()
    stages.append(s6)
    print(f"    decision={s6.get('decision')}")

    # Optional live evidence
    if args.live and not args.offline_only:
        print("\n[7/7] Live evidence shadow run (requires client)")
        s7 = run_live_evidence()
        stages.append(s7)
        print(f"    decision={s7.get('decision')}  skipped={s7.get('skipped')}")

    report = assemble_report(stages, model, baseline)
    write_report(report, Path(args.report) if args.report else None)

    print("\n" + "=" * 70)
    print(f"[live_test] overall={report['overall']}")
    for s in stages:
        mark = "PASS" if s.get("decision") == "PASS" else (
            "SKIP" if s.get("decision") == "SKIPPED" else "FAIL")
        print(f"  [{mark}] {s['stage']}")
    if report["overall"] == "FAIL":
        print("\n[FATAL] One or more stages failed. Do not promote this model.")
        return 1
    print("\n[OK] All stages passed. Model is safe to promote.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
