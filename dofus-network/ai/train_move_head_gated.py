"""Train the live move head over several seeds and promote only on the aggregate.

`ai/train_move_head.py` fits one draw. Measured 2026-10-31 over the same 351 training
actions, five seeds produced temporal-test AUC **0.603, 0.688, 0.787, 0.790, 0.820**
(ECE 0.228-0.458): a single draw is not evidence, and a gate scored on one is a
coin-flip with paperwork. This wrapper runs `--seeds` draws, each into its own
directory, and applies the aggregate rule:

* rows: the test fold is the same for every seed, so it is checked once;
* AUC: the **median** seed's AUC must clear the bar (one lucky high draw cannot carry it);
* calibration: the **worst** seed's ECE must clear the bar (every draw must be honest);
* only then is the median seed's artifact published to `--out`.

The aggregate verdict is what `tools/session_close.py` records; the per-seed metrics
stay beside it under `seeds/`. Exit 2 means the corpus was too small to fit at all,
not that the gate refused -- a refusal is a successful measurement.

    python ai/train_move_head_gated.py --data data/transitions_clean --seeds 3
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.artifacts import publish, restore_previous  # noqa: E402
from ai.train_move_head import (PROMOTE_MAX_ECE, PROMOTE_MIN_AUC,  # noqa: E402
                                PROMOTE_MIN_ROWS)


def aggregate(runs: Sequence[Dict[str, Any]],
              min_rows: int = PROMOTE_MIN_ROWS,
              min_auc: float = PROMOTE_MIN_AUC,
              max_ece: float = PROMOTE_MAX_ECE) -> Dict[str, Any]:
    """The promotion rule over per-seed metrics: median AUC, worst ECE, shared rows."""
    if not runs:
        return {"promoted": False, "reasons": ["no seed produced metrics"],
                "seeds": 0, "median_auc": None, "worst_ece": None,
                "test_rows": 0, "chosen_seed": None}
    rows = [int((run.get("test") or {}).get("rows") or 0) for run in runs]
    aucs = sorted(float((run.get("test") or {}).get("auc_moved"))
                  for run in runs
                  if isinstance((run.get("test") or {}).get("auc_moved"),
                                (int, float)))
    eces = [float((run.get("test") or {}).get("ece_moved"))
            for run in runs
            if isinstance((run.get("test") or {}).get("ece_moved"), (int, float))]
    median_auc = aucs[len(aucs) // 2] if aucs else None
    worst_ece = max(eces) if eces else None
    reasons: List[str] = []
    if min(rows) < min_rows:
        reasons.append("test_rows %d < %d" % (min(rows), min_rows))
    if median_auc is None or median_auc < min_auc:
        reasons.append("median auc_moved %s < %.2f" % (median_auc, min_auc))
    if worst_ece is None or worst_ece > max_ece:
        reasons.append("worst ece_moved %s > %.2f" % (worst_ece, max_ece))
    chosen = None
    if median_auc is not None:
        for run in runs:
            value = (run.get("test") or {}).get("auc_moved")
            if isinstance(value, (int, float)) and abs(float(value) - median_auc) < 1e-9:
                chosen = run.get("seed")
                break
    return {"promoted": not reasons, "reasons": reasons, "seeds": len(runs),
            "median_auc": None if median_auc is None else round(median_auc, 4),
            "worst_ece": None if worst_ece is None else round(worst_ece, 4),
            "test_rows": min(rows), "chosen_seed": chosen,
            "thresholds": {"min_rows": min_rows, "min_auc": min_auc,
                           "max_ece": max_ece}}


def _run_seed(seed: int, data: Path, out: Path, epochs: int, timeout_s: int) -> Dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "ai" / "train_move_head.py"),
         "--data", str(data), "--out", str(out), "--epochs", str(epochs),
         "--seed", str(seed)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=timeout_s)
    if proc.returncode != 0:
        raise RuntimeError("seed %d exited %d: %s"
                           % (seed, proc.returncode,
                              (proc.stderr or proc.stdout).strip()[-300:]))
    payload = json.loads((Path(out) / "metrics_move_head.json").read_text(
        encoding="utf-8"))
    payload["seed"] = seed
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=REPO_ROOT / "data" / "transitions_clean")
    parser.add_argument("--out", type=Path,
                        default=REPO_ROOT / "models" / "foresight_live_v1")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--seed-timeout-s", type=int, default=120)
    parser.add_argument("--min-rows", type=int, default=PROMOTE_MIN_ROWS)
    parser.add_argument("--min-auc", type=float, default=PROMOTE_MIN_AUC)
    parser.add_argument("--max-ece", type=float, default=PROMOTE_MAX_ECE)
    parser.add_argument("--rollback", action="store_true",
                        help="restore the previous promoted artifact and exit")
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.rollback:
        result = restore_previous(out / "foresight.json")
        print(json.dumps(result, indent=1))
        return 0 if result["restored"] else 2
    work = out / "seeds"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    started = time.time()
    runs: List[Dict[str, Any]] = []
    try:
        for seed in range(max(1, args.seeds)):
            seed_dir = work / ("seed%d" % seed)
            runs.append(_run_seed(seed, args.data, seed_dir, args.epochs,
                                  args.seed_timeout_s))
        verdict = aggregate(runs, args.min_rows, args.min_auc, args.max_ece)
        artifact = None
        if verdict["promoted"]:
            source = work / ("seed%d" % verdict["chosen_seed"]) / "foresight.json"
            if not source.is_file():
                raise RuntimeError("the chosen seed wrote no artifact: %s" % source)
            publish(source, out / "foresight.json")
            artifact = str(out / "foresight.json")
        payload = {
            "gate": verdict,
            "artifact": artifact,
            "seeds": [{"seed": run.get("seed"),
                       "test": run.get("test"),
                       "val": run.get("val"),
                       "promotion": run.get("promotion")} for run in runs],
            "seconds": round(time.time() - started, 1),
            "note": "median-AUC seed, worst-case ECE, real observable labels only",
        }
        # The shared metrics file is the aggregate; per-seed files remain in seeds/.
        (out / "metrics_move_head.json").write_text(
            json.dumps(payload, indent=1), encoding="utf-8")
        print("promoted %s over %d seed(s)%s"
              % (verdict["promoted"], verdict["seeds"],
                 "" if verdict["promoted"] else ": " + "; ".join(verdict["reasons"])))
        print(json.dumps({"gate": verdict, "artifact": artifact}, indent=1))
        return 0
    finally:
        # Keep the per-seed metrics, drop the per-seed model payloads (the chosen
        # one was copied above, and an unpromoted run must not leave weights that
        # look installable).
        if work.exists():
            for seed_dir in work.iterdir():
                candidate = seed_dir / "foresight.json"
                if candidate.is_file():
                    candidate.unlink()


if __name__ == "__main__":
    sys.exit(main())
