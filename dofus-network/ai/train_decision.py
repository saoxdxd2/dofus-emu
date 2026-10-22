"""Train the repair decision: after a failure, continue / retry / recover / replan?

This is the only learned decision left with a reason to exist. The tool policy lost to
the teacher's exact precondition checks (now.md §3), because tool choice is mostly
preconditions and the teacher does those perfectly. After a failure the teacher has no
rule at all — it plans objectives, not recoveries — so this is where a model can add
something the rules cannot.

The metric is **regret**, not accuracy. Regret is the progress the oracle would have
extracted minus the progress the policy extracted: the value of the decision you did
not take. Accuracy is misleading here because one decision (`continue`) is right 79% of
the time, so a model that always says `continue` scores 0.79 and is worthless.

It ships only if its regret beats **every** constant, including `continue`.

    python ai/train_decision.py --data data/decision_tc
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

DECISIONS = ["continue", "retry", "recover", "replan"]


def load(path: Path) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    xs: List[List[float]] = []
    ys: List[int] = []
    gains: List[List[float]] = []
    margins: List[float] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            xs.append(r["x"])
            ys.append(int(r["y"]))
            gains.append(r["gains"])
            margins.append(float(r.get("margin") or 0.0))
    return (np.asarray(xs, dtype="float32"), np.asarray(ys, dtype="int64"),
            np.asarray(gains, dtype="float32"), np.asarray(margins, dtype="float32"))


def regret(gains: np.ndarray, chosen: np.ndarray) -> float:
    """Mean progress left on the table, per decision."""
    picked = gains[np.arange(len(gains)), chosen]
    return float((gains.max(axis=1) - picked).mean())


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path("data/decision_sft"))
    ap.add_argument("--out", type=Path, default=Path("models/decision.onnx"))
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--hidden", type=int, default=96)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--device", default="auto")
    args = ap.parse_args(argv)

    import torch
    import torch.nn as nn

    torch.manual_seed(args.seed)
    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    x, y, gains, margins = load(args.data / "train.jsonl")
    vx, vy, vgains, vmargins = load(args.data / "val.jsonl")
    print("train %d  val %d  features %d  device %s"
          % (len(y), len(vy), x.shape[1], device))
    print("label mix:", {DECISIONS[i]: int((y == i).sum()) for i in range(len(DECISIONS))})
    print("val exact ties: %.1f%%" % (100.0 * float((vmargins <= 0).mean())))

    # A tie row has no correct answer, only a tie-break; weight it down like the tool
    # corpus does, or the model spends its capacity on coin flips.
    w = np.where(margins <= 0, 0.25, 1.0).astype("float32")
    tw = torch.from_numpy(w).to(device)

    model = nn.Sequential(
        nn.Linear(x.shape[1], args.hidden), nn.GELU(),
        nn.Linear(args.hidden, args.hidden), nn.GELU(),
        nn.Linear(args.hidden, len(DECISIONS)),
    ).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    tx, ty = torch.from_numpy(x).to(device), torch.from_numpy(y).to(device)
    vxt = torch.from_numpy(vx).to(device)
    steps = max(1, math.ceil(len(y) / args.batch))
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr,
                                                total_steps=args.epochs * steps)

    for epoch in range(1, args.epochs + 1):
        model.train()
        order = torch.randperm(len(y), device=device)
        for i in range(steps):
            idx = order[i * args.batch:(i + 1) * args.batch]
            if len(idx) == 0:
                continue
            opt.zero_grad()
            logits = model(tx[idx])
            loss = nn.functional.cross_entropy(logits, ty[idx], reduction="none")
            (loss * tw[idx]).mean().backward()
            opt.step()
            sched.step()
        if epoch % 50 == 0:
            model.eval()
            with torch.no_grad():
                acc = float((model(vxt).argmax(1) == torch.from_numpy(vy).to(device)).float().mean())
            print("epoch %3d  val_acc %.4f" % (epoch, acc))

    model.eval()
    with torch.no_grad():
        probs = torch.softmax(model(vxt), dim=1).cpu().numpy()
    pred = probs.argmax(1)

    constants = {d: regret(vgains, np.full(len(vy), i)) for i, d in enumerate(DECISIONS)}
    best_constant = min(constants.values())
    model_regret = regret(vgains, pred)
    acc = float((pred == vy).mean())

    print("\nval accuracy %.4f" % acc)
    print("regret (progress left on the table, lower is better):")
    for d, r in constants.items():
        print("  always %-9s %.4f" % (d, r))
    print("  learned           %.4f" % model_regret)
    verdict = "BEATS every constant" if model_regret < best_constant else "LOSES to the best constant"
    print("  -> %s (%.4f vs %.4f)" % (verdict, model_regret, best_constant))

    print("\nper-decision recall (support>=5):")
    for i, d in enumerate(DECISIONS):
        mask = vy == i
        if mask.sum() >= 5:
            print("  %-9s n=%-5d recall %.2f  predicted %d"
                  % (d, mask.sum(), (pred[mask] == i).mean(), (pred == i).sum()))

    report = {"val_accuracy": round(acc, 4), "model_regret": round(model_regret, 4),
              "constants": {k: round(v, 4) for k, v in constants.items()},
              "best_constant": round(best_constant, 4),
              "beats_constants": bool(model_regret < best_constant),
              "rows_train": int(len(y)), "rows_val": int(len(vy)),
              "features": int(x.shape[1])}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(model, torch.zeros(1, x.shape[1], device=device), str(args.out),
                      input_names=["features"], output_names=["logits"],
                      opset_version=17, dynamo=False,
                      dynamic_axes={"features": {0: "batch"}, "logits": {0: "batch"}})
    args.out.with_suffix(".json").write_text(
        json.dumps({"kind": "decision", "decisions": DECISIONS,
                    "features": int(x.shape[1]), **report}, indent=1), encoding="utf-8")
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
