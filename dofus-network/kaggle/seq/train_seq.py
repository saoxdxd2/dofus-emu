"""Plan chunks on Kaggle: predict the next three calls, not the next one.

A one-step classifier cannot represent a plan. "Travel, then talk" and "gather, then
deliver" are ordered pairs where the second action only makes sense after the first,
and a model that sees a single state has no way to express that ordering.

This trains a small GRU over the last three states and predicts three tool ids ahead,
with a mask so a short episode does not teach it to predict padding. Two things are
reported that matter more than top-1:

* **per-horizon accuracy** — does the third call degrade? A plan that is right for one
  step and wrong for three is not a plan.
* **exact-match rate** — all predicted steps correct. That is the number that decides
  whether a chunk can be executed without a replan in the middle.

Self-contained: a Kaggle script kernel cannot import the project.
"""

import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

DATA = Path("/kaggle/input/thx-02-planner-corpus")
OUT = Path("/kaggle/working")
HISTORY = 3
HORIZON = 3
HIDDEN = 384
LAYERS = 2
EPOCHS = 40
BATCH = 2048
SEED = 7


def find_input(name):
    """Locate a corpus file without assuming the mount layout.

    Kaggle mounts a dataset at /kaggle/input/<slug>/ but the layout inside can
    change (a version upload can nest the folder, and a run that starts during a
    version switch can see an empty mount). A hardcoded path turned that into a
    FileNotFoundError at second 9; a search turns it into a message.
    """
    root = Path("/kaggle/input")
    direct = DATA / name
    if direct.exists():
        return direct
    hits = sorted(root.rglob(name))
    if hits:
        return hits[0]
    print("missing %s; mount contains:" % name)
    for p in sorted(root.rglob("*"))[:40]:
        print("  ", p)
    raise SystemExit("corpus file not found: %s" % name)


def read_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


class PlanChunk(nn.Module):
    def __init__(self, d_in, hidden=HIDDEN, layers=LAYERS, horizon=HORIZON, n_cls=5):
        super().__init__()
        self.proj = nn.Linear(d_in, hidden)
        self.gru = nn.GRU(hidden, hidden, num_layers=layers, batch_first=True)
        self.head = nn.Linear(hidden, horizon * n_cls)
        self.horizon = horizon
        self.n_cls = n_cls

    def forward(self, x):
        # x is the flattened history; rebuild the time axis.
        b = x.size(0)
        seq = x.view(b, HISTORY, -1)
        h, _ = self.gru(torch.tanh(self.proj(seq)))
        out = self.head(h[:, -1]).view(b, self.horizon, self.n_cls)
        return out


def masked_loss(logits, y, mask):
    b, h, c = logits.shape
    loss = F.cross_entropy(logits.reshape(-1, c), y.reshape(-1), reduction="none")
    loss = loss.view(b, h) * mask
    return loss.sum() / mask.sum().clamp(min=1.0)


@torch.no_grad()
def evaluate(model, x, y, mask, device):
    model.eval()
    logits = model(x.to(device))
    pred = logits.argmax(-1).cpu()
    y = y.cpu()
    m = mask.cpu().bool()
    per_h = []
    for k in range(HORIZON):
        sel = m[:, k]
        if sel.sum() == 0:
            per_h.append(0.0)
            continue
        per_h.append(float((pred[sel, k] == y[sel, k]).float().mean()))
    exact = float(((pred == y) | ~m).all(dim=1).float().mean())
    return per_h, exact


def main():
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    print("torch", torch.__version__, "cuda", torch.cuda.is_available())

    train = read_jsonl(find_input("seq_train.jsonl"))
    val = read_jsonl(find_input("seq_val.jsonl"))
    print("windows: train %d  val %d" % (len(train), len(val)))

    x = torch.tensor([r["x"] for r in train], dtype=torch.float32)
    y = torch.tensor([r["y"] for r in train], dtype=torch.long)
    m = torch.tensor([r["mask"] for r in train], dtype=torch.float32)
    vx = torch.tensor([r["x"] for r in val], dtype=torch.float32)
    vy = torch.tensor([r["y"] for r in val], dtype=torch.long)
    vm = torch.tensor([r["mask"] for r in val], dtype=torch.float32)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = PlanChunk(x.shape[1]).to(device)
    print("parameters: %.2fM" % (sum(p.numel() for p in model.parameters()) / 1e6))

    opt = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=EPOCHS)

    best = {"exact": -1.0, "state": None, "epoch": 0}
    history = []
    started = time.time()

    for epoch in range(1, EPOCHS + 1):
        model.train()
        perm = torch.randperm(x.size(0))
        total = 0.0
        for start in range(0, x.size(0), BATCH):
            idx = perm[start:start + BATCH]
            opt.zero_grad()
            logits = model(x[idx].to(device))
            loss = masked_loss(logits, y[idx].to(device), m[idx].to(device))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += float(loss) * len(idx)
        sched.step()

        if epoch % 5 == 0 or epoch == EPOCHS:
            per_h, exact = evaluate(model, vx, vy, vm, device)
            print("epoch %3d  loss %.4f  per-step %s  exact %.4f"
                  % (epoch, total / x.size(0),
                     " ".join("%.3f" % v for v in per_h), exact))
            history.append({"epoch": epoch, "per_horizon": [round(v, 4) for v in per_h],
                            "exact": round(exact, 4)})
            if exact > best["exact"]:
                best = {"exact": exact, "epoch": epoch,
                        "state": {k: v.detach().clone() for k, v in model.state_dict().items()}}

    if best["state"] is not None:
        model.load_state_dict(best["state"])
    print("\nbest exact-match %.4f at epoch %d (%.1fs)"
          % (best["exact"], best["epoch"], time.time() - started))

    model.eval().cpu()
    onnx_path = OUT / "plan_chunk.onnx"
    torch.onnx.export(model, torch.zeros(1, x.shape[1]), str(onnx_path),
                      input_names=["features"], output_names=["logits"],
                      opset_version=17, dynamo=False,
                      dynamic_axes={"features": {0: "batch"}, "logits": {0: "batch"}})
    print("wrote %s (%.1f KB)" % (onnx_path, onnx_path.stat().st_size / 1024))

    per_h, exact = evaluate(model, vx, vy, vm, torch.device("cpu"))
    metrics = {
        "history": HISTORY, "horizon": HORIZON,
        "params": sum(p.numel() for p in model.parameters()),
        "windows": {"train": len(train), "val": len(val)},
        "per_horizon_accuracy": [round(v, 4) for v in per_h],
        "exact_match": round(exact, 4),
        "best_epoch": best["epoch"],
        "history_curve": history,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
    }
    (OUT / "plan_chunk_metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    print(json.dumps(metrics["per_horizon_accuracy"]), "exact", metrics["exact_match"])


if __name__ == "__main__":
    main()
