"""Train the verifier's baseline head — pure Python, no numpy, no torch.

Why a logistic regression and not the encoder head: the loop has to be *linked
and tested* before a GPU run exists, and a 33-feature multinomial model trains in
under a second on a few hundred rows. It is also the honest baseline the neural
head has to beat — if a linear model on the same features gets 80%, a 16 MB
transformer that gets 82% is not worth the RAM.

Features come from ``agent.verifier.features``, which the runtime calls too, so
train and serve cannot skew.

Usage (from ``dofus-network``)::

    python -m ai.build_verifier_sft --selfcheck --out data/verifier_sft
    python ai/train_verifier_baseline.py \
        --train data/verifier_sft/train.jsonl --val data/verifier_sft/val.jsonl \
        --out models/verifier_baseline.json

    # then, in the loop
    python run_autopilot.py --goals-file goals.txt --verifier-model models/verifier_baseline.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.verifier import TOOL_ORDER, VERDICTS, features  # noqa: E402


def read_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def vectorise(rows: Sequence[Dict[str, Any]]) -> Tuple[List[List[float]], List[int]]:
    xs: List[List[float]] = []
    ys: List[int] = []
    for r in rows:
        t = r.get("target") or {}
        vid = t.get("verdict_id")
        if vid is None:
            verdict = t.get("verdict")
            if verdict not in VERDICTS:
                continue
            vid = VERDICTS.index(verdict)
        xs.append(features(r.get("tool", ""), bool(r.get("ok")), r.get("error"),
                           r.get("obs_before"), r.get("obs_after"),
                           r.get("latency_ms") or 0.0))
        ys.append(int(vid))
    return xs, ys


def softmax(logits: Sequence[float]) -> List[float]:
    m = max(logits)
    ex = [math.exp(z - m) for z in logits]
    s = sum(ex)
    return [e / s for e in ex]


def predict(w: List[List[float]], b: List[float], x: Sequence[float]) -> List[float]:
    return softmax([sum(wi * xi for wi, xi in zip(w[c], x)) + b[c] for c in range(len(w))])


def train(xs: List[List[float]], ys: List[int], epochs: int, lr: float,
          l2: float, seed: int) -> Tuple[List[List[float]], List[float]]:
    rng = random.Random(seed)
    n_feat = len(xs[0])
    n_cls = len(VERDICTS)

    # Class weights: "advanced" dominates, and a model that always says
    # "advanced" is useless. Inverse frequency, capped so one rare class cannot
    # own the loss.
    counts = Counter(ys)
    weights = []
    for c in range(n_cls):
        n_c = max(1, counts.get(c, 0))
        weights.append(min(8.0, len(ys) / (n_cls * n_c)))

    w = [[0.0] * n_feat for _ in range(n_cls)]
    b = [0.0] * n_cls
    order = list(range(len(xs)))

    for epoch in range(epochs):
        rng.shuffle(order)
        total = 0.0
        for i in order:
            x, y = xs[i], ys[i]
            p = predict(w, b, x)
            total += -math.log(max(1e-9, p[y])) * weights[y]
            for c in range(n_cls):
                g = (p[c] - (1.0 if c == y else 0.0)) * weights[y]
                if g == 0.0:
                    continue
                wc = w[c]
                for k in range(n_feat):
                    if x[k]:
                        wc[k] -= lr * (g * x[k] + l2 * wc[k])
                b[c] -= lr * g
        if epoch % max(1, epochs // 10) == 0:
            print("  epoch %3d  loss %.4f" % (epoch, total / max(1, len(xs))))
    return w, b


def evaluate(w: List[List[float]], b: List[float],
             xs: List[List[float]], ys: List[int]) -> Dict[str, Any]:
    confusion = [[0] * len(VERDICTS) for _ in VERDICTS]
    correct = 0
    for x, y in zip(xs, ys):
        p = predict(w, b, x)
        pred = p.index(max(p))
        confusion[y][pred] += 1
        correct += 1 if pred == y else 0
    per_class = {}
    for c, name in enumerate(VERDICTS):
        tp = confusion[c][c]
        support = sum(confusion[c])
        recall = tp / support if support else None
        per_class[name] = {"support": support, "recall": None if recall is None else round(recall, 3)}
    return {
        "accuracy": round(correct / max(1, len(ys)), 4),
        "n": len(ys),
        "per_class": per_class,
        "confusion": confusion,
        "confusion_labels": list(VERDICTS),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", type=Path, required=True)
    ap.add_argument("--val", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "models" / "verifier_baseline.json")
    ap.add_argument("--epochs", type=int, default=400)
    ap.add_argument("--lr", type=float, default=0.5)
    ap.add_argument("--l2", type=float, default=0.001)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--note", default="",
                    help="free-text provenance recorded in the artifact")
    args = ap.parse_args(argv)

    if not args.train.exists():
        print("missing %s - run ai/build_verifier_sft first" % args.train)
        return 2

    xs, ys = vectorise(read_rows(args.train))
    if not xs:
        print("no usable training rows")
        return 1
    print("train: %d examples, %d features, classes %s" % (len(xs), len(xs[0]), list(VERDICTS)))

    w, b = train(xs, ys, args.epochs, args.lr, args.l2, args.seed)
    metrics = {"train": evaluate(w, b, xs, ys)}

    if args.val and args.val.exists():
        vxs, vys = vectorise(read_rows(args.val))
        if vxs:
            metrics["val"] = evaluate(w, b, vxs, vys)
            print("val accuracy: %.4f on %d examples" % (metrics["val"]["accuracy"], len(vxs)))
            for name, row in metrics["val"]["per_class"].items():
                print("  %-9s support %3d  recall %s" % (name, row["support"], row["recall"]))

    payload = {
        "kind": "verifier-baseline",
        "version": 1,
        "features": len(w[0]),
        "classes": list(VERDICTS),
        "tools": list(TOOL_ORDER),
        "w": [[round(v, 6) for v in row] for row in w],
        "b": [round(v, 6) for v in b],
        "metrics": metrics,
        "note": args.note,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print("wrote %s (%.1f KB)" % (args.out, args.out.stat().st_size / 1024.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
