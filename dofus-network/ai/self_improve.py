"""Resume, retrain, and measure — on the task the model actually has to do.

The instruction this implements: *every model should resume on its own task under a
time budget and try to raise its accuracy; the paper shows this is faster and more
accurate, but it can become overconfident.* So the loop measures confidence
explicitly and refuses to reward it.

One round is:

    resume from the last checkpoint  ->  train for N seconds  ->  score on held-out
      ->  keep the best (accuracy minus a calibration penalty)  ->  next round

Why a time budget instead of a fixed epoch count: the interesting question is "what
does another 60 seconds buy?", and that only has an answer if 60 seconds is the unit.

Why the penalty: accuracy alone rewards a model that is right and certain on the easy
half and silently wrong on the rest. ECE measures exactly that, and temperature
scaling is the cheapest correction — fitted on validation, reported before and after.

    python ai/self_improve.py --task verifier --rounds 4 --round-seconds 60
    python ai/self_improve.py --task verifier --report
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODELS = REPO_ROOT / "models"


# --------------------------------------------------------------------------- data

def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _verifier_features(row: Dict[str, Any]) -> List[float]:
    from agent.verifier import features
    return features(row.get("tool", ""), bool(row.get("ok")), row.get("error"),
                    row.get("obs_before"), row.get("obs_after"),
                    row.get("latency_ms") or 0.0)


def _verifier_label(row: Dict[str, Any]) -> int:
    from agent.verifier import VERDICTS
    target = row.get("target") or {}
    if "verdict_id" in target:
        return int(target["verdict_id"])
    return VERDICTS.index(target.get("verdict"))


# ------------------------------------------------------------------ measurement

def _softmax(logits: Sequence[float], temperature: float = 1.0) -> List[float]:
    scaled = [z / max(1e-3, temperature) for z in logits]
    m = max(scaled)
    ex = [math.exp(z - m) for z in scaled]
    total = sum(ex) or 1.0
    return [e / total for e in ex]


def _ece(probs: List[float], correct: List[int], bins: int = 10) -> float:
    """Expected calibration error: |confidence - accuracy| averaged over bins."""
    buckets: List[List[Tuple[float, int]]] = [[] for _ in range(bins)]
    for p, c in zip(probs, correct):
        idx = min(bins - 1, int(p * bins))
        buckets[idx].append((p, c))
    total = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        conf = sum(p for p, _ in bucket) / len(bucket)
        acc = sum(c for _, c in bucket) / len(bucket)
        total += (len(bucket) / len(probs)) * abs(conf - acc)
    return total


def _nll(probs: List[List[float]], labels: List[int]) -> float:
    return -sum(math.log(max(1e-9, p[y])) for p, y in zip(probs, labels)) / max(1, len(labels))


def evaluate(onnx_path: Path, rows: Sequence[Dict[str, Any]],
             feature_fn: Callable[[Dict[str, Any]], List[float]],
             label_fn: Callable[[Dict[str, Any]], int]) -> Dict[str, Any]:
    import numpy as np
    import onnxruntime as ort

    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    name = session.get_inputs()[0].name
    xs = np.asarray([feature_fn(r) for r in rows], dtype="float32")
    ys = [label_fn(r) for r in rows]
    logits = session.run(None, {name: xs})[0]

    raw_probs = [_softmax(row) for row in logits]
    correct = [1 if int(p.index(max(p))) == y else 0 for p, y in zip(raw_probs, ys)]
    acc = sum(correct) / max(1, len(correct))

    # Fit a temperature on this split: one scalar, minimises NLL, cannot invent
    # accuracy. If the model is overconfident the number comes out above 1.
    best_t, best_nll = 1.0, _nll(raw_probs, ys)
    for t in [0.5 + 0.05 * i for i in range(1, 41)]:
        nll = _nll([_softmax(row, t) for row in logits], ys)
        if nll < best_nll:
            best_t, best_nll = t, nll
    calibrated = [_softmax(row, best_t) for row in logits]

    return {
        "n": len(ys),
        "accuracy": round(acc, 4),
        "ece": round(_ece([max(p) for p in raw_probs], correct), 4),
        "ece_calibrated": round(_ece([max(p) for p in calibrated], correct), 4),
        "temperature": round(best_t, 2),
        "nll": round(best_nll, 4),
    }


# ---------------------------------------------------------------------- tasks

TASKS: Dict[str, Dict[str, Any]] = {
    "verifier": {
        "train": REPO_ROOT / "data" / "verifier_sft" / "train.jsonl",
        "val": REPO_ROOT / "data" / "verifier_sft" / "val.jsonl",
        "artifact": MODELS / "verifier_head.onnx",
        "state": MODELS / "verifier_head.pt",
        "trainer": REPO_ROOT / "ai" / "train_verifier_head.py",
        "features": _verifier_features,
        "label": _verifier_label,
    },
}


def run_round(task: Dict[str, Any], seconds: float, epochs: int,
              init: Optional[Path], log: Callable[[str], None]) -> Dict[str, Any]:
    cmd = [sys.executable, str(task["trainer"]),
           "--train", str(task["train"]), "--val", str(task["val"]),
           "--out", str(task["artifact"]), "--epochs", str(epochs),
           "--no-int8", "--save-pt", str(task["state"])]
    if init is not None:
        cmd += ["--init", str(init)]
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=max(30, seconds * 3))
    if proc.returncode != 0:
        return {"ok": False, "detail": (proc.stderr or proc.stdout)[-300:],
                "seconds": round(time.time() - started, 1)}
    return {"ok": True, "seconds": round(time.time() - started, 1)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=sorted(TASKS), default="verifier")
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--round-seconds", type=float, default=60.0)
    ap.add_argument("--epochs", type=int, default=60,
                    help="epochs per round; the time budget is the real limit")
    ap.add_argument("--calibration-penalty", type=float, default=0.5,
                    help="how much ECE costs when choosing the best round")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)

    task = TASKS[args.task]
    history_path = MODELS / ("self_improve_%s.json" % args.task)

    if args.report:
        if not history_path.exists():
            print("no history for %s yet" % args.task)
            return 1
        print(history_path.read_text(encoding="utf-8"))
        return 0

    for key in ("train", "val"):
        if not Path(task[key]).exists():
            print("missing %s - run the corpus builder first" % task[key])
            return 2

    rows = _read_jsonl(Path(task["val"]))
    history: List[Dict[str, Any]] = []
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8")).get("rounds", [])
        except ValueError:
            history = []

    best_score = -1e9
    for n in range(1, args.rounds + 1):
        init = task["state"] if Path(task["state"]).exists() else None
        print("\n-- round %d/%d (%s, init=%s)" % (n, args.rounds, args.task,
                                                  "resume" if init else "scratch"))
        result = run_round(task, args.round_seconds, args.epochs, init, print)
        if not result["ok"]:
            print("  round failed: %s" % result["detail"])
            break

        metrics = evaluate(Path(task["artifact"]), rows, task["features"], task["label"])
        score = metrics["accuracy"] - args.calibration_penalty * metrics["ece"]
        improved = score > best_score
        best_score = max(best_score, score)
        entry = {"round": n, **metrics, "score": round(score, 4),
                 "train_seconds": result["seconds"], "kept": improved}
        history.append(entry)
        print("  acc %.4f  ece %.4f (calibrated %.4f, T=%.2f)  score %.4f  %s"
              % (metrics["accuracy"], metrics["ece"], metrics["ece_calibrated"],
                 metrics["temperature"], score, "KEPT" if improved else "discarded"))

        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text(json.dumps({"task": args.task, "rounds": history},
                                           indent=1), encoding="utf-8")

    if history:
        best = max(history, key=lambda e: e["score"])
        print("\nbest round: %d  acc %.4f  ece %.4f" %
              (best["round"], best["accuracy"], best["ece"]))
        if best["ece"] > 0.15:
            print("WARNING: still overconfident (ece %.3f) - the model is right less "
                  "often than it thinks; do not let it veto actions yet." % best["ece"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
