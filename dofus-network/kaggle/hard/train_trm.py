"""THX-02: a recursive plan-repair model, trained on Kaggle.

The mechanism is the one from *Less is More: Recursive Reasoning with Tiny Networks*
(Samsung SAIL, arXiv:2510.04871): one small network, a latent answer that is refined
over K steps, and a loss at **every** step rather than only at the end. The claim
worth testing is not "small models are good" — it is that **recursion with deep
supervision buys accuracy that depth alone does not**.

Applied here to the second agent's actual job: given an objective and the state it
must be met in, choose the tool. The same input can need different tools depending on
state (carry the item or not, already on the map or not), which is exactly the
"repair" the planner keeps getting wrong.

Reported per recursion step, because that is the question: does thinking longer help,
and does it start lying more confidently while it does?

Self-contained on purpose: a Kaggle script kernel cannot import the project.
"""

import json
import math
import os
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

DATA = Path("/kaggle/input/thx-02-planner-corpus")
OUT = Path("/kaggle/working")
STEPS = 12          # recursion depth: the paper's "think longer", now 12 steps
HIDDEN = 1024
LATENT = 512
EPOCHS = 60
SEED = 7
BATCH = 4096


def write_diagnostics():
    """Leave a file behind, because the log is the only thing the CLI cannot fetch.

    A failing Kaggle script that prints a traceback gives you a message and no
    evidence. Writing the mount tree to /kaggle/working means `kaggle kernels
    output` returns the answer even when the run dies.
    """
    try:
        root = Path("/kaggle/input")
        tree = []
        if root.exists():
            for p in sorted(root.rglob("*"))[:200]:
                tree.append({"path": str(p), "dir": p.is_dir(),
                             "size": p.stat().st_size if p.is_file() else None})
        payload = {"input_exists": root.exists(), "entries": tree,
                   "cwd": str(Path.cwd()), "argv": __import__("sys").argv}
        (OUT / "diagnostics.json").write_text(json.dumps(payload, indent=1),
                                              encoding="utf-8")
        print("mount entries:", len(tree))
        for row in tree[:10]:
            print("  ", row["path"])
    except Exception as exc:  # noqa: BLE001
        print("diagnostics failed:", exc)


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


def load():
    # Prefer the rollout corpus (behaviour with outcomes); fall back to the
    # teacher corpus. Same feature layout, so the model does not care.
    has_rollout = bool(list(Path("/kaggle/input").rglob("rollout_train.jsonl")))
    train_name = "rollout_train.jsonl" if has_rollout else "planner_train.jsonl"
    val_name = "rollout_val.jsonl" if has_rollout else "planner_val.jsonl"
    print("corpus:", train_name, val_name)
    train = read_jsonl(find_input(train_name))
    val = read_jsonl(find_input(val_name))
    train = [r for r in train if r.get("y", -1) >= 0]
    val = [r for r in val if r.get("y", -1) >= 0]
    x = np.asarray([r["x"] for r in train], dtype="float32")
    y = np.asarray([r["y"] for r in train], dtype="int64")
    # Behavioural rows carry the outcome. An action that completed the objective is
    # a better label than one that did not, and this is the cheapest way to use
    # that: weight, don't filter. Filtering would throw away exactly the states
    # where the teacher is wrong, which are the ones worth learning.
    w = np.asarray([1.0 if r.get("advanced", True) else 0.5 for r in train], dtype="float32")
    vx = np.asarray([r["x"] for r in val], dtype="float32")
    vy = np.asarray([r["y"] for r in val], dtype="int64")
    adv_rate = float(np.mean([1.0 if r.get("advanced", True) else 0.0 for r in train]))
    print("outcome-weighted: %.1f%% of training actions advanced" % (100 * adv_rate))
    return x, y, w, vx, vy


class TRM(nn.Module):
    """One cell, applied K times, with a head at every step."""

    def __init__(self, d_in, d_h=HIDDEN, d_z=LATENT, steps=STEPS, n_cls=5):
        super().__init__()
        self.steps = steps
        self.embed = nn.Sequential(nn.Linear(d_in, d_h), nn.GELU(), nn.Linear(d_h, d_h))
        self.z0 = nn.Parameter(torch.zeros(d_z))
        self.cell = nn.GRUCell(d_h + d_z, d_z)
        self.head = nn.Linear(d_z, n_cls)

    def forward(self, x):
        h = self.embed(x)
        z = self.z0.unsqueeze(0).expand(x.size(0), -1)
        outs = []
        for _ in range(self.steps):
            z = self.cell(torch.cat([h, z], dim=-1), z)
            outs.append(self.head(z))          # deep supervision: every step counts
        return outs


def ece(probs, labels, bins=10):
    conf = probs.max(axis=1)
    pred = probs.argmax(axis=1)
    correct = (pred == labels).astype("float32")
    total = 0.0
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        mask = (conf > lo) & (conf <= hi)
        if mask.sum() == 0:
            continue
        total += (mask.sum() / len(conf)) * abs(conf[mask].mean() - correct[mask].mean())
    return float(total)


def main():
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    print("torch", torch.__version__, "cuda", torch.cuda.is_available())
    write_diagnostics()

    x, y, w, vx, vy = load()
    print("train %d  val %d  features %d  classes %d" % (len(y), len(vy), x.shape[1], y.max() + 1))

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tx, ty = torch.from_numpy(x).to(device), torch.from_numpy(y).to(device)
    tw = torch.from_numpy(w).to(device)
    wx, wy = torch.from_numpy(vx).to(device), torch.from_numpy(vy).to(device)

    model = TRM(x.shape[1]).to(device)
    params = sum(p.numel() for p in model.parameters())
    print("parameters: %.2fM  (fp32 ~ %.1f MB)" % (params / 1e6, params * 4 / 1e6))

    opt = torch.optim.AdamW(model.parameters(), lr=3e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=EPOCHS)

    best = {"acc": -1.0, "state": None, "step": None}
    history = []
    started = time.time()

    n = tx.size(0)
    for epoch in range(1, EPOCHS + 1):
        model.train()
        perm = torch.randperm(n, device=device)
        epoch_loss = 0.0
        for start in range(0, n, BATCH):
            idx = perm[start:start + BATCH]
            opt.zero_grad()
            outs = model(tx[idx])
            # Deep supervision: every recursion step is a training signal, so early
            # steps learn to be useful rather than waiting for the last one.
            loss = sum(F.cross_entropy(o, ty[idx], reduction="none") for o in outs) / len(outs)
            loss = (loss * tw[idx]).mean()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            epoch_loss += float(loss.detach()) * len(idx)
            last_logits, last_targets = outs[-1], ty[idx]
        sched.step()
        loss = torch.tensor(epoch_loss / max(1, n))

        if epoch % 5 == 0 or epoch == EPOCHS:
            model.eval()
            with torch.no_grad():
                vouts = model(wx)
                per_step = []
                for k, o in enumerate(vouts):
                    acc = float((o.argmax(1) == wy).float().mean())
                    p = torch.softmax(o, dim=1).cpu().numpy()
                    per_step.append((k + 1, acc, ece(p, vy)))
                # `outs` is the last *batch* now, not the whole dataset. Comparing it
                # with the full target tensor is a shape error, and a loud one only
                # because the batch happened to be smaller than the corpus.
                train_acc = float((last_logits.argmax(1) == last_targets).float().mean())

            line = "epoch %3d  loss %.4f  train %.4f  |  " % (epoch, float(loss), train_acc)
            line += "  ".join("k%d %.4f/%.3f" % (k, a, e) for k, a, e in per_step)
            print(line)

            k_best, acc_best, ece_best = max(per_step, key=lambda t: t[1])
            if acc_best > best["acc"]:
                best = {"acc": acc_best, "step": k_best,
                        "state": {k: v.detach().clone() for k, v in model.state_dict().items()}}
            history.append({"epoch": epoch, "train_acc": round(train_acc, 4),
                            "per_step": [{"k": k, "acc": round(a, 4), "ece": round(e, 4)}
                                         for k, a, e in per_step]})
            model.train()

    if best["state"] is not None:
        model.load_state_dict(best["state"])
    model.eval()
    print("\nbest val accuracy %.4f at recursion step %s  (%.1fs)"
          % (best["acc"], best["step"], time.time() - started))

    # Export: the recursion is unrolled, so the runtime sees a plain graph.
    onnx_path = OUT / "trm_repair.onnx"
    torch.onnx.export(model.cpu(), torch.zeros(1, x.shape[1]), str(onnx_path),
                      input_names=["features"], output_names=["step_outputs"],
                      opset_version=17, dynamo=False,
                      dynamic_axes={"features": {0: "batch"}, "step_outputs": {0: "batch"}})
    print("wrote %s (%.1f KB)" % (onnx_path, onnx_path.stat().st_size / 1024))

    metrics = {
        "parameters": params,
        "recursion_steps": STEPS,
        "best_val_accuracy": round(best["acc"], 4),
        "best_step": best["step"],
        "epochs": EPOCHS,
        "train_rows": int(len(y)),
        "val_rows": int(len(vy)),
        "history": history,
        "torch": torch.__version__,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
    }
    (OUT / "trm_repair_metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    print("wrote trm_repair_metrics.json")

    # Does thinking longer help? The whole point of the paper, on our data.
    if history:
        last = history[-1]["per_step"]
        print("\nrecursion profile (last epoch):")
        for row in last:
            print("  k=%d  acc %.4f  ece %.4f" % (row["k"], row["acc"], row["ece"]))
        gain = last[-1]["acc"] - last[0]["acc"]
        print("  accuracy gained from step 1 to %d: %+.4f" % (STEPS, gain))
        if last[-1]["ece"] > 0.15:
            print("  WARNING: overconfident at the last step (ece %.3f)" % last[-1]["ece"])


if __name__ == "__main__":
    main()
