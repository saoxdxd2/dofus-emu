"""Linear transition probe over the tool policy's pre-logit activations.

Phase 1B.  The tool head is trained on the teacher's label, so it can learn
that a *read* is an acceptable action: 22% of the corpus is a read loop, and
the live loop spends most of its steps there.  A reward-hack is not a policy
that says "read"; it is a hidden state in which the read is the highest-value
continuation.  This probe asks the hidden state one question:

    did the step this state produced change the world?

The label is the server-validated transition built by
``tools/build_live_transitions.py``: positive means ``moved`` and not a read,
negative means no movement or a read.  The probe is deliberately linear and
tiny (one weight per hidden unit): it is a diagnostic and a runtime veto, not
another policy.  The mechanism is the linear-probe construction used to read
reward-hack states out of activations (From Reward-Hack Activations to Agentic
Risk States; Proxy Reward Internalization and Mechanistic Exploitation).

Training is offline; the runtime artifact is a JSON vector.  No torch import
reaches ``agent/`` or ``combat/``.

    python ai/train_transition_probe.py \
        --model models/tool_policy.onnx \
        --data data/transitions \
        --out models/tool_policy_probe.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.features import (FEATURE_COUNT, HISTORY_DIM, HISTORY_STEPS,  # noqa: E402
                            _err_slot, history_tokens)
from ai.onnx_hidden import export_hidden, run_hidden  # noqa: E402

OBS_STALE = 38


def load_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not isinstance(row.get("x"), list):
                continue
            if len(row["x"]) != FEATURE_COUNT:
                raise SystemExit("%s: row is %d wide, featuriser says %d"
                                 % (path, len(row["x"]), FEATURE_COUNT))
            rows.append(row)
    return rows


def build_split(path: Path) -> Tuple[np.ndarray, np.ndarray, Dict[str, np.ndarray]]:
    """Rebuild the 266-wide serving input for every transition row.

    The transition log stores the 42-wide state at decision time, not the
    history window.  Grouping by ``segment`` and sorting by ``t`` recovers the
    same window ``ObsTracker`` would have held: the actions before this row,
    each recorded after it ran, with the error class derived from its own
    ``error`` string.  The current row's action is appended only after the row
    is featurised, exactly as ``dedicated_policy.build`` does for episodes.
    """
    by_segment: Dict[str, List[Dict[str, Any]]] = {}
    for row in load_rows(path):
        by_segment.setdefault(str(row.get("segment") or row.get("instance") or ""),
                              []).append(row)
    X: List[List[float]] = []
    Y: List[int] = []
    meta: Dict[str, List[Any]] = {"segment": [], "t": [], "tool": [], "moved": [],
                                  "was_read": [], "obs_stale": []}
    for segment in sorted(by_segment):
        rows = sorted(by_segment[segment], key=lambda r: (float(r.get("t") or 0),
                                                          str(r.get("id") or "")))
        window: List[Dict[str, Any]] = []
        for row in rows:
            state = [float(v) for v in row["x"]]
            if not row.get("source_identified") or row.get("credit_eligible"):
                X.append(state + history_tokens(window))
                moved = bool(row.get("moved"))
                read = bool(row.get("was_read"))
                Y.append(1 if (moved and not read) else 0)
                meta["segment"].append(segment)
                meta["t"].append(float(row.get("t") or 0))
                meta["tool"].append(str(row.get("tool") or ""))
                meta["moved"].append(moved)
                meta["was_read"].append(read)
                meta["obs_stale"].append(float(state[OBS_STALE]))
            window.append({"tool": str(row.get("tool") or ""),
                           "ok": bool(row.get("ok")),
                           "err": _err_slot(row.get("error"))})
            if len(window) > HISTORY_STEPS:
                window = window[-HISTORY_STEPS:]
    return (np.asarray(X, dtype=np.float32), np.asarray(Y, dtype=np.float32),
            {key: np.asarray(value) for key, value in meta.items()})


def _sigmoid(z: np.ndarray) -> np.ndarray:
    z = np.clip(z, -60.0, 60.0)
    return 1.0 / (1.0 + np.exp(-z))


def _auc(scores: np.ndarray, labels: np.ndarray) -> float:
    pos = scores[labels > 0.5]
    neg = scores[labels <= 0.5]
    if not pos.size or not neg.size:
        return 0.0
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(order.size, dtype=np.float64)
    ranks[order] = np.arange(1, order.size + 1)
    return float((ranks[:pos.size].sum() - pos.size * (pos.size + 1) / 2)
                 / (pos.size * neg.size))


def fit_logistic(hidden: np.ndarray, labels: np.ndarray, *,
                 l2: float = 1e-3, epochs: int = 1500,
                 lr: float = 0.5) -> Dict[str, Any]:
    """Balanced L2 logistic regression, started at the difference of means.

    The difference-of-means direction is the probe the paper describes; the
    logistic fit rotates it just enough to calibrate a threshold.  Class
    weights are balanced because a read loop is the majority class in the live
    log, and an unweighted fit would score "always negative" well.
    """
    h = np.asarray(hidden, dtype=np.float64)
    y = np.asarray(labels, dtype=np.float64)
    mean = h.mean(axis=0)
    std = h.std(axis=0)
    std = np.where(std < 1e-6, 1.0, std)
    hs = (h - mean) / std
    pos = max(float(y.sum()), 1.0)
    neg = max(float(len(y) - y.sum()), 1.0)
    weights = np.where(y > 0.5, 0.5 / pos, 0.5 / neg)
    w = (hs[y > 0.5].mean(axis=0) - hs[y <= 0.5].mean(axis=0)) * 0.1
    b = 0.0
    n = max(len(y), 1)
    for _ in range(max(1, epochs)):
        p = _sigmoid(hs @ w + b)
        err = (p - y) * weights
        grad_w = hs.T @ err / n + l2 * w
        grad_b = float(err.mean())
        w -= lr * grad_w
        b -= lr * grad_b
    return {"w": w.astype(np.float64), "b": float(b), "mean": mean,
            "std": std}


def score(model: Dict[str, Any], hidden: np.ndarray) -> np.ndarray:
    hs = (np.asarray(hidden, dtype=np.float64) - model["mean"]) / model["std"]
    return _sigmoid(hs @ model["w"] + model["b"])


def choose_tau(scores: np.ndarray, labels: np.ndarray) -> Dict[str, float]:
    """Threshold that maximises balanced accuracy, then F1."""
    best = (-1.0, -1.0, 0.5)
    for tau in np.unique(np.quantile(scores, np.linspace(0.01, 0.99, 197))):
        pred = scores >= tau
        tp = float(((pred == 1) & (labels == 1)).sum())
        tn = float(((pred == 0) & (labels == 0)).sum())
        fp = float(((pred == 1) & (labels == 0)).sum())
        fn = float(((pred == 0) & (labels == 1)).sum())
        tpr = tp / max(tp + fn, 1.0)
        tnr = tn / max(tn + fp, 1.0)
        precision = tp / max(tp + fp, 1.0)
        recall = tpr
        f1 = 2 * precision * recall / max(precision + recall, 1e-9)
        key = ((tpr + tnr) / 2.0, f1, float(tau))
        if key > best:
            best = key
    tau = best[2]
    pred = scores >= tau
    tp = int(((pred == 1) & (labels == 1)).sum())
    tn = int(((pred == 0) & (labels == 0)).sum())
    fp = int(((pred == 1) & (labels == 0)).sum())
    fn = int(((pred == 0) & (labels == 1)).sum())
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    return {
        "tau": round(float(tau), 6),
        "balanced_accuracy": round(float((recall + tn / max(tn + fp, 1)) / 2), 4),
        "accuracy": round(float((pred == labels).mean()), 4),
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "f1": round(float(2 * precision * recall / max(precision + recall, 1e-9)), 4),
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
    }


def pairwise_direction(hidden: np.ndarray, labels: np.ndarray,
                       segments: np.ndarray, tools: np.ndarray) -> np.ndarray:
    """Difference of means computed inside each (segment, tool) cell.

    The row-wise probe learns the base move rate of a segment, which is exactly
    what changes between folds.  Differencing inside a cell removes that base
    rate and asks which hidden directions separate a move from a no-move with
    the same tool in the same episode.
    """
    groups: Dict[Tuple[str, str], List[int]] = {}
    for index, key in enumerate(zip(segments.tolist(), tools.tolist())):
        groups.setdefault((str(key[0]), str(key[1])), []).append(index)
    diffs = []
    for indices in groups.values():
        pos = [i for i in indices if labels[i] > 0.5]
        neg = [i for i in indices if labels[i] <= 0.5]
        if not pos or not neg:
            continue
        diffs.append(hidden[pos].mean(axis=0) - hidden[neg].mean(axis=0))
    if not diffs:
        return np.zeros(hidden.shape[1], dtype=np.float64)
    direction = np.mean(np.asarray(diffs, dtype=np.float64), axis=0)
    return direction / max(float(np.linalg.norm(direction)), 1e-9)


def report(model: Dict[str, Any], hidden: np.ndarray, labels: np.ndarray,
           tau: float) -> Dict[str, Any]:
    scores = score(model, hidden)
    pred = scores >= tau
    tp = int(((pred == 1) & (labels == 1)).sum())
    tn = int(((pred == 0) & (labels == 0)).sum())
    fp = int(((pred == 1) & (labels == 0)).sum())
    fn = int(((pred == 0) & (labels == 1)).sum())
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    return {
        "rows": int(len(labels)),
        "positives": int(labels.sum()),
        "negatives": int(len(labels) - labels.sum()),
        "auc": round(_auc(scores, labels), 4),
        "brier": round(float(((scores - labels) ** 2).mean()), 4),
        "at_tau": {
            "tau": round(float(tau), 6),
            "accuracy": round(float((pred == labels).mean()), 4),
            "balanced_accuracy": round(float((recall + tn / max(tn + fp, 1)) / 2), 4),
            "precision": round(float(precision), 4),
            "recall": round(float(recall), 4),
            "f1": round(float(2 * precision * recall / max(precision + recall, 1e-9)), 4),
            "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        },
        "score_mean": round(float(scores.mean()), 4),
        "score_p10": round(float(np.quantile(scores, 0.10)), 4),
        "score_p50": round(float(np.quantile(scores, 0.50)), 4),
        "score_p90": round(float(np.quantile(scores, 0.90)), 4),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path,
                        default=REPO_ROOT / "models" / "tool_policy.onnx")
    parser.add_argument("--data", type=Path,
                        default=REPO_ROOT / "data" / "transitions")
    parser.add_argument("--out", type=Path,
                        default=REPO_ROOT / "models" / "tool_policy_probe.json")
    parser.add_argument("--hidden-model", type=Path, default=None)
    parser.add_argument("--l2", type=float, default=1e-3)
    parser.add_argument("--epochs", type=int, default=1500)
    parser.add_argument("--lr", type=float, default=0.5)
    parser.add_argument("--penalty", type=float, default=0.25,
                        help="logit penalty applied to non-read tools when the "
                             "probe score is below tau")
    args = parser.parse_args(argv)

    splits = {}
    for name in ("train", "val", "test"):
        path = args.data / ("%s.jsonl" % name)
        if not path.exists():
            raise SystemExit("missing %s" % path)
        splits[name] = build_split(path)
    hidden_model = args.hidden_model or args.out.with_name("tool_policy_hidden.onnx")
    hidden_meta = export_hidden(args.model, hidden_model)
    print("hidden graph: %s -> %s (%d wide)"
          % (hidden_meta["hidden_output"], hidden_model, hidden_meta["hidden_dim"]))

    hidden = {}
    for name, (x, y, meta) in splits.items():
        hidden[name] = run_hidden(hidden_model, x, hidden_meta["hidden_output"])
        print("%s: %d rows, %d positive, %d stale"
              % (name, len(y), int(y.sum()), int((meta["obs_stale"] >= 0.5).sum())))

    model = fit_logistic(hidden["train"], splits["train"][1],
                         l2=args.l2, epochs=args.epochs, lr=args.lr)
    val_scores = score(model, hidden["val"])
    chosen = choose_tau(val_scores, splits["val"][1])
    tau = float(chosen["tau"])
    print("probe tau %.4f  val balanced accuracy %.4f  AUC %.4f"
          % (tau, chosen["balanced_accuracy"], _auc(val_scores, splits["val"][1])))

    metrics = {}
    for name in ("train", "val", "test"):
        metrics[name] = report(model, hidden[name], splits[name][1], tau)
        print("%-5s AUC %.4f  brier %.4f  at tau: precision %.3f recall %.3f "
              "balanced %.3f" % (name, metrics[name]["auc"], metrics[name]["brier"],
                                 metrics[name]["at_tau"]["precision"],
                                 metrics[name]["at_tau"]["recall"],
                                 metrics[name]["at_tau"]["balanced_accuracy"]))

    pairwise = pairwise_direction(hidden["train"], splits["train"][1],
                                  splits["train"][2]["segment"],
                                  splits["train"][2]["tool"])
    pairwise_auc = {}
    for name in ("train", "val", "test"):
        pairwise_auc[name] = round(
            _auc(hidden[name] @ pairwise, splits[name][1]), 4)
    print("pairwise (segment, tool) direction AUC: train %.4f val %.4f test %.4f"
          % (pairwise_auc["train"], pairwise_auc["val"], pairwise_auc["test"]))
    enabled = (metrics["val"]["auc"] >= 0.65 and metrics["test"]["auc"] >= 0.65)
    print("probe enabled: %s (requires val and test AUC >= 0.65)" % enabled)

    from agent.verifier import READ_ONLY_TOOLS
    payload = {
        "kind": "tool-policy-transition-probe",
        "model": str(args.model),
        "model_sha256": hidden_meta["source_sha256"],
        "data": str(args.data),
        "hidden_model": hidden_model.name,
        "hidden_output": hidden_meta["hidden_output"],
        "hidden_dim": int(hidden_meta["hidden_dim"]),
        "w": [round(float(v), 8) for v in model["w"].tolist()],
        "b": round(float(model["b"]), 8),
        "mean": [round(float(v), 8) for v in model["mean"].tolist()],
        "std": [round(float(v), 8) for v in model["std"].tolist()],
        "tau": tau,
        "penalty": float(args.penalty),
        "enabled": bool(enabled),
        "enable_rule": "val_auc >= 0.65 and test_auc >= 0.65",
        "pairwise": {
            "direction": [round(float(v), 8) for v in pairwise.tolist()],
            "auc": pairwise_auc,
            "definition": "difference of means inside each (segment, tool) cell",
        },
        "read_only_tools": sorted(READ_ONLY_TOOLS),
        "label": "1 = moved and not was_read; 0 = no move or read-only",
        "metrics": metrics,
        "val_threshold": chosen,
        "train_rows": int(len(splits["train"][1])),
        "val_rows": int(len(splits["val"][1])),
        "test_rows": int(len(splits["test"][1])),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print("wrote %s (%.1f KB)" % (args.out, args.out.stat().st_size / 1024.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
