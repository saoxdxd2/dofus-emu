"""Train the tool policy: 41 numbers -> which of the 18 tools to call next.

Deliberately self-contained — no imports from the project — because the same file is
what runs on Kaggle, and a script kernel cannot import the repository. If the two
copies could drift they would, and the drift would surface as a serving-time shape
error weeks later.

What this reports, and why each number is here:

* **accuracy** — the headline, and the least informative one. A corpus that is 36%
  `farm.start` gets 36% from a model that always says `farm.start`.
* **agreement with the teacher** — the real baseline. The label came from forking the
  world, so on the fraction of rows where the teacher was right, copying the teacher
  scores 100%. A model that cannot beat this has learned nothing the teacher did not
  already encode.
* **macro-F1 and per-class recall** — a model that predicts the four common tools and
  nothing else is fine for the simulator and useless for an agent that meets a soul
  stone quest. The per-class table is where that shows up.
* **ECE** — the controller consumes these probabilities to decide whether to trust a
  step. A confident wrong model is worse than an uncertain right one.

`--sweep` trains several capacities and reports the table, which is the question a GPU
is actually for here: does capacity buy anything on this task, or is the ceiling the
corpus? The two TRM runs already answered "no" for recursion; this asks it for width.

    python ai/train_tool_policy.py --data data/tool_sft --out models/tool_policy.onnx
    python ai/train_tool_policy.py --data /kaggle/input/... --sweep
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

DEFAULT_TOOLS = [
    "knowledge.search", "nav.goto", "nav.follow", "quest.list", "quest.advance",
    "farm.start", "farm.stop", "fight.join", "fight.pass", "inv.list", "inv.sell",
    "hdv.scan", "hdv.list", "chat.say", "state.snapshot", "safe.stop",
    "knowledge.remember", "knowledge.forget",
]


# -- input discovery ---------------------------------------------------------

def find_input(name: str = "train.jsonl") -> Path:
    """Locate a corpus file, wherever Kaggle decided to mount it.

    Kaggle mounts private datasets at /kaggle/input/datasets/<owner>/<slug>/, not at
    /kaggle/input/<slug>/ as every document says. Hardcoding either path produced two
    runs that died on FileNotFoundError while the data was present the whole time, so
    this searches, and on failure prints the mount tree instead of a traceback.
    """
    direct = Path("/kaggle/input") / name
    if direct.exists():
        return direct
    root = Path("/kaggle/input")
    if root.exists():
        for candidate in sorted(root.rglob(name)):
            return candidate
    here = Path("data/tool_sft") / name
    if here.exists():
        return here
    tree = [str(p) for p in sorted(root.rglob("*"))[:60]] if root.exists() else []
    raise SystemExit("could not find %s under /kaggle/input; saw: %s" % (name, tree))


def load_split(path: Path, feature_count: Optional[int] = None
               ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
    xs: List[List[float]] = []
    ys: List[int] = []
    teacher: List[int] = []
    trivial: List[float] = []
    width = feature_count or 0
    tool_index: Dict[str, int] = {name: i for i, name in enumerate(DEFAULT_TOOLS)}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            x = row["x"]
            if not width:
                width = len(x)
            if len(x) != width:
                raise ValueError("ragged features in %s: %d vs %d" % (path, len(x), width))
            xs.append(x)
            ys.append(int(row["y"]))
            teacher.append(tool_index.get(row.get("teacher") or "", -1))
            trivial.append(0.35 if row.get("trivial") else 1.0)
    if not xs:
        raise SystemExit("no rows in %s" % path)
    return (np.asarray(xs, dtype="float32"), np.asarray(ys, dtype="int64"),
            np.asarray(teacher, dtype="int64"), np.asarray(trivial, dtype="float32"),
            width)


# -- metrics -----------------------------------------------------------------

def ece(probs: np.ndarray, labels: np.ndarray, bins: int = 10) -> float:
    conf = probs.max(axis=1)
    pred = probs.argmax(axis=1)
    correct = (pred == labels).astype("float64")
    total = max(1, len(labels))
    err = 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (conf > lo) & (conf <= hi)
        if mask.sum() == 0:
            continue
        err += (mask.sum() / total) * abs(correct[mask].mean() - conf[mask].mean())
    return float(err)


def per_class(probs: np.ndarray, labels: np.ndarray, tools: List[str],
              minimum: int = 5) -> List[Dict[str, Any]]:
    pred = probs.argmax(axis=1)
    out = []
    for i, name in enumerate(tools):
        support = int((labels == i).sum())
        hit = int(((pred == i) & (labels == i)).sum())
        fired = int((pred == i).sum())
        if support < minimum and fired == 0:
            continue
        recall = hit / support if support else 0.0
        precision = hit / fired if fired else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        out.append({"tool": name, "support": support, "predicted": fired,
                    "recall": round(recall, 3), "precision": round(precision, 3),
                    "f1": round(f1, 3)})
    return out


def top_confusions(probs: np.ndarray, labels: np.ndarray, tools: List[str],
                   limit: int = 12) -> List[Dict[str, Any]]:
    pred = probs.argmax(1)
    counts: Dict[Tuple[int, int], int] = {}
    for t, p in zip(labels.tolist(), pred.tolist()):
        if t != p:
            counts[(t, p)] = counts.get((t, p), 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return [{"true": tools[t], "predicted": tools[p], "n": n} for (t, p), n in ranked]


# -- training ----------------------------------------------------------------

def train_one(hidden: int, depth: int, epochs: int, batch: int, lr: float,
              x: np.ndarray, y: np.ndarray, weight: np.ndarray,
              vx: np.ndarray, vy: np.ndarray, vteacher: np.ndarray,
              device: str, seed: int, quiet: bool = False) -> Dict[str, Any]:
    import torch
    import torch.nn as nn

    torch.manual_seed(seed)
    width = x.shape[1]
    layers: List[Any] = []
    dims = [width] + [hidden] * depth + [len(DEFAULT_TOOLS)]
    for i in range(len(dims) - 1):
        layers.append(nn.Linear(dims[i], dims[i + 1]))
        if i < len(dims) - 2:
            layers.append(nn.GELU())
    model = nn.Sequential(*layers).to(device)

    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    tx = torch.from_numpy(x).to(device)
    ty = torch.from_numpy(y).to(device)
    tw = torch.from_numpy(weight).to(device)
    vxt = torch.from_numpy(vx).to(device)
    vyt = torch.from_numpy(vy).to(device)
    steps = max(1, math.ceil(len(y) / batch))
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr,
                                                total_steps=epochs * steps)

    best = {"acc": -1.0, "state": None, "epoch": 0}
    started = time.time()
    for epoch in range(1, epochs + 1):
        model.train()
        order = torch.randperm(len(y), device=device)
        total = 0.0
        for i in range(steps):
            idx = order[i * batch:(i + 1) * batch]
            if len(idx) == 0:
                continue
            opt.zero_grad()
            logits = model(tx[idx])
            loss = nn.functional.cross_entropy(logits, ty[idx], reduction="none")
            loss = (loss * tw[idx]).mean()
            loss.backward()
            opt.step()
            sched.step()
            total += float(loss)
        if epoch % 10 == 0 or epoch == epochs:
            model.eval()
            with torch.no_grad():
                vacc = float((model(vxt).argmax(1) == vyt).float().mean())
            if not quiet:
                print("  h%-5d d%d  epoch %3d  loss %.4f  val_acc %.4f"
                      % (hidden, depth, epoch, total / steps, vacc))
            if vacc > best["acc"]:
                best = {"acc": vacc, "epoch": epoch,
                        "state": {k: v.detach().clone() for k, v in model.state_dict().items()}}
        if time.time() - started > 3300:
            print("  time budget reached at epoch %d" % epoch)
            break

    if best["state"] is not None:
        model.load_state_dict(best["state"])
    model.eval()
    with torch.no_grad():
        probs = torch.softmax(model(vxt), dim=1).cpu().numpy()
        train_acc = float((model(tx).argmax(1) == ty).float().mean())

    pred = probs.argmax(1)
    acc = float((pred == vy).mean())
    known = vteacher >= 0
    teacher_acc = float((vteacher[known] == vy[known]).mean()) if known.any() else 0.0
    table = per_class(probs, vy, DEFAULT_TOOLS)
    macro_f1 = float(np.mean([r["f1"] for r in table])) if table else 0.0
    params = int(sum(p.numel() for p in model.parameters()))

    return {
        "hidden": hidden, "depth": depth, "params": params,
        "val_accuracy": round(acc, 4), "train_accuracy": round(train_acc, 4),
        "teacher_agreement_on_val": round(teacher_acc, 4),
        "lift_over_teacher": round(acc - teacher_acc, 4),
        "macro_f1": round(macro_f1, 4), "ece": round(ece(probs, vy), 4),
        "best_epoch": best["epoch"],
        "per_class": table, "confusion_top": top_confusions(probs, vy, DEFAULT_TOOLS),
        "model": model, "probs": probs,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path("data/tool_sft"))
    ap.add_argument("--out", type=Path, default=Path("models/tool_policy.onnx"))
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--hidden", type=int, default=192)
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--batch", type=int, default=2048)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--sweep", action="store_true",
                    help="train several capacities and report which one, if any, wins")
    args = ap.parse_args(argv)

    import torch

    if args.threads:
        torch.set_num_threads(args.threads)
    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    train_path = args.data / "train.jsonl"
    val_path = args.data / "val.jsonl"
    if not train_path.exists():
        train_path = find_input("train.jsonl")
        val_path = train_path.parent / "val.jsonl"
    x, y, teacher, weight, width = load_split(train_path)
    vx, vy, vteacher, _, _ = load_split(val_path, width)
    print("train %d  val %d  features %d  classes %d  device %s"
          % (len(y), len(vy), width, len(DEFAULT_TOOLS), device))
    support = {DEFAULT_TOOLS[i]: int((y == i).sum())
               for i in range(len(DEFAULT_TOOLS)) if (y == i).sum()}
    print("class support:", support)

    configs = [(args.hidden, args.depth)]
    if args.sweep:
        configs = [(64, 2), (192, 2), (512, 2), (1024, 2), (512, 4)]

    results = []
    for hidden, depth in configs:
        print("== hidden %d depth %d ==" % (hidden, depth))
        r = train_one(hidden, depth, args.epochs, args.batch, args.lr,
                      x, y, weight, vx, vy, vteacher, device, args.seed,
                      quiet=args.sweep)
        results.append(r)
        print("   val_acc %.4f  teacher %.4f  lift %+.4f  macroF1 %.4f  ECE %.4f  params %d"
              % (r["val_accuracy"], r["teacher_agreement_on_val"],
                 r["lift_over_teacher"], r["macro_f1"], r["ece"], r["params"]))

    best = max(results, key=lambda r: r["val_accuracy"])
    if args.sweep:
        print("\ncapacity vs accuracy:")
        for r in results:
            print("  h%-5d d%d  params %8d  acc %.4f  macroF1 %.4f  ECE %.4f"
                  % (r["hidden"], r["depth"], r["params"], r["val_accuracy"],
                     r["macro_f1"], r["ece"]))
        gain = results[-1]["val_accuracy"] - results[0]["val_accuracy"]
        print("  best-to-worst spread: %+.4f" % (best["val_accuracy"] - results[0]["val_accuracy"]))
        print("  largest model vs smallest: %+.4f" % gain)

    report = {k: v for k, v in best.items() if k not in ("model", "probs")}
    report["sweep"] = [{k: v for k, v in r.items() if k not in ("model", "probs", "per_class", "confusion_top")}
                       for r in results]
    print("\n" + json.dumps({k: v for k, v in report.items()
                             if k not in ("per_class", "confusion_top", "sweep")}, indent=1))
    print("per-class (best):")
    for row in report["per_class"]:
        print("  %-20s support %6d  recall %.3f  precision %.3f  f1 %.3f"
              % (row["tool"], row["support"], row["recall"], row["precision"], row["f1"]))

    out = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    model = best["model"]
    torch.onnx.export(model, torch.zeros(1, width, device=device), str(out),
                      input_names=["features"], output_names=["logits"],
                      opset_version=17, dynamo=False,
                      dynamic_axes={"features": {0: "batch"}, "logits": {0: "batch"}})
    meta = {"kind": "tool-policy", "tools": DEFAULT_TOOLS, "features": int(width),
            "val_accuracy": report["val_accuracy"],
            "teacher_agreement": report["teacher_agreement_on_val"],
            "ece": report["ece"], "params": report["params"],
            "hidden": report["hidden"], "depth": report["depth"],
            "rows": int(len(y) + len(vy))}
    out.with_suffix(".json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    (out.parent / "tool_policy_report.json").write_text(
        json.dumps(report, indent=1), encoding="utf-8")
    print("wrote %s (%.1f KB)" % (out, out.stat().st_size / 1024.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
