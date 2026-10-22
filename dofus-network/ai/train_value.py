"""Learn what a decision is worth, instead of deciding by threshold.

The rule controller says: first failure -> retry, third -> replan. Those numbers were
chosen once and never measured. `sim/decisions.py` measured them on 1,500 post-failure
states and found retrying is the *worst* option there — but replacing one constant with
another constant is not progress.

So this trains a small regressor:

    features(state, history) -> [gain(continue), gain(retry), gain(recover), gain(replan)]

and the policy picks the argmax. Three things make it honest:

* the metric is **regret** (best achievable gain minus the gain of the chosen action),
  not accuracy — a decision model that is right 95% of the time on ties is worthless;
* it is scored on states it never saw, split by episode;
* the baseline is the rule policy's own decision, computed on the same rows.

    python ai/train_value.py --epochs 300
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DECISIONS = ["continue", "retry", "recover", "replan"]


def read(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=REPO_ROOT / "data" / "decision_sft")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "models" / "decision_value.onnx")
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    import numpy as np
    import torch
    import torch.nn as nn

    train = read(args.data / "train.jsonl")
    val = read(args.data / "val.jsonl")
    if len(train) < 100 or not val:
        print("corpus too small: %d train / %d val" % (len(train), len(val)))
        return 1

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    x = np.asarray([r["x"] for r in train], dtype="float32")
    y = np.asarray([r["gains"] for r in train], dtype="float32")
    vx = np.asarray([r["x"] for r in val], dtype="float32")
    vy = np.asarray([r["gains"] for r in val], dtype="float32")

    tx, ty = torch.from_numpy(x), torch.from_numpy(y)
    wx, wy = torch.from_numpy(vx), torch.from_numpy(vy)

    model = nn.Sequential(
        nn.Linear(x.shape[1], args.hidden), nn.GELU(),
        nn.Linear(args.hidden, args.hidden), nn.GELU(),
        nn.Linear(args.hidden, len(DECISIONS)),
    )
    opt = torch.optim.AdamW(model.parameters(), lr=3e-3, weight_decay=1e-4)
    loss_fn = nn.SmoothL1Loss()

    for epoch in range(1, args.epochs + 1):
        model.train()
        opt.zero_grad()
        loss = loss_fn(model(tx), ty)
        loss.backward()
        opt.step()
        if epoch % 100 == 0:
            print("epoch %3d  loss %.4f" % (epoch, float(loss)))

    model.eval()
    with torch.no_grad():
        pred = model(wx).numpy()

    # Regret: how much gain the chosen action leaves on the table.
    def regret_of(index: int) -> float:
        return float(np.mean(vy.max(1) - vy[:, index]))

    chosen = pred.argmax(1)
    regret_model = float(np.mean(vy.max(1) - vy[np.arange(len(vy)), chosen]))
    # The two constants worth beating. Index 1 is what the shipped controller does on
    # a first failure ("retry"); index 0 is what a nervous engineer would do
    # ("ignore the failure and carry on"). Both are free to compute, so both are
    # baselines -- a model that loses to either has not earned its file.
    regret_retry = regret_of(DECISIONS.index("retry"))
    regret_continue = regret_of(DECISIONS.index("continue"))
    oracle = float(vy.max(1).mean())

    print("\nval states: %d" % len(vy))
    print("  mean best achievable gain : %.3f" % oracle)
    print("  always continue  regret   : %.3f" % regret_continue)
    print("  always retry     regret   : %.3f  (the shipped rule)" % regret_retry)
    print("  model            regret   : %.3f" % regret_model)
    best_baseline = min(regret_continue, regret_retry)
    if regret_model < best_baseline:
        print("  -> the model beats both constants by %.3f" % (best_baseline - regret_model))
    else:
        print("  -> the model does NOT beat the best constant (%.3f); do not ship it"
              % best_baseline)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(model, torch.zeros(1, x.shape[1]), str(args.out),
                      input_names=["features"], output_names=["gains"],
                      opset_version=17, dynamo=False,
                      dynamic_axes={"features": {0: "batch"}, "gains": {0: "batch"}})
    meta = {"decisions": DECISIONS, "features": int(x.shape[1]),
            "val_regret": round(regret_model, 4),
            "retry_regret": round(regret_retry, 4),
            "continue_regret": round(regret_continue, 4),
            "oracle_gain": round(oracle, 4),
            "rows": len(train)}
    args.out.with_suffix(".json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
