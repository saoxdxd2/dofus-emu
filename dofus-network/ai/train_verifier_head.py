"""Train the verifier's neural head (33 features -> verdict), export ONNX + INT8.

The baseline in `train_verifier_baseline.py` is a logistic regression: it exists
so the loop can be tested without torch. This is the next rung: the same features,
a small MLP, and a real ONNX artifact that `Verifier._load_model` can load.

What this is and is not
-----------------------
It is a *real* trained artifact: torch -> ONNX -> onnxruntime, with a parity check
and a quantised variant. It is **not** evidence that the verifier has learned the
game: the labels it trains on today come from the rule verifier, so the head
learns the rule function. Replace the labels with live + human ones and the same
command produces a model whose numbers mean something.

Run with the embedded runtime (it has torch, onnx, onnxruntime):

    runtimes\\python\\python.exe -m ai.train_verifier_head ^
        --train data/verifier_sft/train.jsonl ^
        --val   data/verifier_sft/val.jsonl ^
        --out   models/verifier_head.onnx
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.verifier import VERDICTS, features  # noqa: E402


def read_rows(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def to_arrays(rows: Sequence[Dict[str, Any]]):
    import numpy as np

    xs, ys, ws = [], [], []
    for r in rows:
        t = r.get("target") or {}
        vid = t.get("verdict_id")
        if vid is None:
            v = t.get("verdict")
            if v not in VERDICTS:
                continue
            vid = VERDICTS.index(v)
        xs.append(features(r.get("tool", ""), bool(r.get("ok")), r.get("error"),
                           r.get("obs_before"), r.get("obs_after"),
                           r.get("latency_ms") or 0.0))
        ys.append(int(vid))
        ws.append(float(r.get("label_weight", 0.3)))
    return (np.asarray(xs, dtype="float32"),
            np.asarray(ys, dtype="int64"),
            np.asarray(ws, dtype="float32"))


def build_model(n_features: int, hidden: int, dropout: float):
    import torch.nn as nn

    return nn.Sequential(
        nn.Linear(n_features, hidden),
        nn.GELU(),
        nn.Dropout(dropout),
        nn.Linear(hidden, hidden // 2),
        nn.GELU(),
        nn.Dropout(dropout),
        nn.Linear(hidden // 2, len(VERDICTS)),
    )


def evaluate(model, x, y) -> Dict[str, Any]:
    import numpy as np
    import torch

    with torch.no_grad():
        logits = model(torch.from_numpy(x))
        pred = logits.argmax(dim=1).numpy()
    n = len(y)
    correct = int((pred == y).sum())
    per_class = {}
    confusion = [[0] * len(VERDICTS) for _ in VERDICTS]
    for t, p in zip(y, pred):
        confusion[int(t)][int(p)] += 1
    for c, name in enumerate(VERDICTS):
        support = sum(confusion[c])
        per_class[name] = {"support": support,
                           "recall": round(confusion[c][c] / support, 3) if support else None}
    return {"accuracy": round(correct / max(1, n), 4), "n": n,
            "per_class": per_class, "confusion": confusion}


def veto_sweep(probs, y) -> List[Dict[str, Any]]:
    import numpy as np

    failed = {VERDICTS.index("failed"), VERDICTS.index("blocked")}
    advanced = VERDICTS.index("advanced")
    total_failed = int(np.isin(y, list(failed)).sum()) or 1
    total_advanced = int((y == advanced).sum()) or 1
    out = []
    for step in range(5, 20):
        t = step / 20.0
        best = probs.argmax(axis=1)
        conf = probs.max(axis=1)
        veto = np.isin(best, list(failed)) & (conf >= t)
        prevented = int((veto & np.isin(y, list(failed))).sum())
        blocked_good = int((veto & (y == advanced)).sum())
        out.append({"threshold": round(t, 2),
                    "failures_prevented_pct": round(100.0 * prevented / total_failed, 1),
                    "good_actions_blocked_pct": round(100.0 * blocked_good / total_advanced, 1)})
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", type=Path, required=True)
    ap.add_argument("--val", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "models" / "verifier_head.onnx")
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--dropout", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-int8", action="store_true", help="skip the quantised export")
    args = ap.parse_args(argv)

    try:
        import numpy as np
        import torch
    except Exception as exc:  # noqa: BLE001
        print("torch/numpy required (use runtimes\\python): %s" % exc)
        return 2

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    if not args.train.exists():
        print("missing %s - run ai/build_verifier_sft first" % args.train)
        return 2

    x, y, w = to_arrays(read_rows(args.train))
    if len(x) == 0:
        print("no usable training rows")
        return 1
    print("train: %d examples, %d features" % (len(x), x.shape[1]))

    # Class weights so the rare labels are not drowned by "advanced", multiplied
    # by the per-row label weight (human 1.0 / model 0.6 / rule 0.3).
    counts = np.bincount(y, minlength=len(VERDICTS)).astype("float32")
    class_w = np.where(counts > 0, len(y) / (len(VERDICTS) * np.maximum(counts, 1)), 1.0)
    class_w = np.clip(class_w, 0.5, 8.0)
    # CrossEntropyLoss's `weight` is per *class*; the weighting we need is per
    # *sample* (label source x class rarity), so it is applied to the unreduced
    # loss instead. Mixing the two is the classic way to get a silently wrong
    # gradient.
    sample_w = torch.from_numpy((w * class_w[y]).astype("float32"))

    model = build_model(x.shape[1], args.hidden, args.dropout)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    loss_fn = torch.nn.CrossEntropyLoss(reduction="none")
    tx = torch.from_numpy(x)
    ty = torch.from_numpy(y)

    best = {"acc": -1.0, "epoch": -1, "state": None}
    for epoch in range(1, args.epochs + 1):
        model.train()
        opt.zero_grad()
        raw = loss_fn(model(tx), ty)
        loss = (raw * sample_w).mean()
        loss.backward()
        opt.step()

        if epoch % 10 == 0 or epoch == args.epochs:
            model.eval()
            train_metrics = evaluate(model, x, y)
            line = "epoch %3d  loss %.4f  train acc %.4f" % (epoch, float(loss), train_metrics["accuracy"])
            if args.val and args.val.exists():
                vx, vy, _ = to_arrays(read_rows(args.val))
                if len(vx):
                    vm = evaluate(model, vx, vy)
                    line += "  val acc %.4f" % vm["accuracy"]
                    if vm["accuracy"] > best["acc"]:
                        best = {"acc": vm["accuracy"], "epoch": epoch,
                                "state": {k: v.clone() for k, v in model.state_dict().items()}}
            print(line)
            model.train()

    metrics: Dict[str, Any] = {"train": evaluate(model, x, y)}
    if best["state"] is not None:
        model.load_state_dict(best["state"])
        metrics["best_val_epoch"] = best["epoch"]

    if args.val and args.val.exists():
        vx, vy, _ = to_arrays(read_rows(args.val))
        if len(vx):
            metrics["val"] = evaluate(model, vx, vy)
            print("val accuracy: %.4f on %d examples (epoch %s)"
                  % (metrics["val"]["accuracy"], len(vx), metrics.get("best_val_epoch", "final")))
            for name, row in metrics["val"]["per_class"].items():
                print("  %-9s support %3d  recall %s" % (name, row["support"], row["recall"]))
            with torch.no_grad():
                probs = torch.softmax(model(torch.from_numpy(vx)), dim=1).numpy()
            metrics["veto_sweep"] = veto_sweep(probs, vy)
            print("veto trade-off (threshold / failures prevented / good actions blocked):")
            for row in metrics["veto_sweep"]:
                print("  %.2f  %5.1f%%  %5.1f%%" % (row["threshold"],
                                                    row["failures_prevented_pct"],
                                                    row["good_actions_blocked_pct"]))

    # ---- export ---------------------------------------------------------
    model.eval()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.zeros(1, x.shape[1], dtype=torch.float32)
    # Dynamic batch axis: the runtime sends one row per step, but a fixed batch
    # of 1 also breaks the parity check and any future batching.
    torch.onnx.export(model, dummy, str(args.out), input_names=["features"],
                      output_names=["logits"], opset_version=17, dynamo=False,
                      dynamic_axes={"features": {0: "batch"}, "logits": {0: "batch"}})
    print("wrote %s (%.1f KB)" % (args.out, args.out.stat().st_size / 1024.0))

    # Parity: ONNX must agree with torch, or the artifact is not the model.
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(str(args.out), providers=["CPUExecutionProvider"])
        with torch.no_grad():
            ref = model(torch.from_numpy(x[:16])).numpy()
        got = sess.run(None, {"features": x[:16]})[0]
        diff = float(np.abs(ref - got).max())
        print("onnx parity max|diff| = %.6f" % diff)
        metrics["onnx_parity_max_abs_diff"] = round(diff, 8)
    except Exception as exc:  # noqa: BLE001
        print("parity check skipped: %s" % exc)

    if not args.no_int8:
        try:
            from onnxruntime.quantization import quantize_dynamic, QuantType
            int8 = args.out.with_name(args.out.stem + "_int8.onnx")
            quantize_dynamic(str(args.out), str(int8), weight_type=QuantType.QInt8)
            print("wrote %s (%.1f KB)" % (int8, int8.stat().st_size / 1024.0))
            metrics["int8_path"] = str(int8)
        except Exception as exc:  # noqa: BLE001
            print("int8 export skipped: %s" % exc)

    sidecar = args.out.with_suffix(".json")
    sidecar.write_text(json.dumps({"kind": "verifier-head", "classes": list(VERDICTS),
                                   "features": int(x.shape[1]), "metrics": metrics},
                                  indent=1), encoding="utf-8")
    print("wrote %s" % sidecar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
