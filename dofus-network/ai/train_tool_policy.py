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

    python ai/train_tool_policy.py --data data/tool_sft_v3 --out models/tool_policy.onnx
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

# Registry order, and the label encoding. This used to be an 18-entry copy of the
# live action space; `fight.state`, `fight.plan` and `fight.estimate` were added to
# the registry later and never here, so a regenerated corpus produced labels the
# model had no output for. The training script must name every action the agent can
# take, even the ones that are rare in the corpus.
DEFAULT_TOOLS = [
    "knowledge.search", "nav.goto", "nav.follow", "quest.list", "quest.advance",
    "farm.start", "farm.stop", "fight.join", "fight.pass", "inv.list", "inv.sell",
    "hdv.scan", "hdv.list", "chat.say", "state.snapshot", "safe.stop",
    "knowledge.remember", "knowledge.forget", "fight.state", "fight.plan",
    "fight.estimate",
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
    here = Path("data/tool_sft_v3") / name
    if here.exists():
        return here
    tree = [str(p) for p in sorted(root.rglob("*"))[:60]] if root.exists() else []
    raise SystemExit("could not find %s under /kaggle/input; saw: %s" % (name, tree))


def load_split(path: Path, feature_count: Optional[int] = None
               ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
    """Returns x, y, teacher, weight, gains, width.

    `gains` is the full fork result: for every row, what each candidate tool actually
    scored. It is the reason this corpus is worth more than its labels. Forty percent
    of states map to more than one tool, so an argmax target throws away the only
    information that distinguishes them — by how much the alternatives lost.
    """
    xs: List[List[float]] = []
    ys: List[int] = []
    teacher: List[int] = []
    trivial: List[float] = []
    gains: List[List[float]] = []
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
            # Two reasons a row carries less signal than it appears to:
            #  * every candidate scored zero (trivial): the label is the tie-break;
            #  * the top two are within noise (margin): the label is *also* the
            #    tie-break, and 23% of this corpus is an exact tie. Measured on rows
            #    the fork could actually separate, the model is at 0.969; the whole
            #    gap to 0.90 overall lives in these rows, so they are down-weighted
            #    rather than deleted (they still teach preconditions).
            margin = float(row.get("margin") or 0.0)
            mw = 0.20 if margin <= 0.0 else (0.60 if margin <= 0.10 else 1.0)
            trivial.append((0.35 if row.get("trivial") else 1.0) * mw)
            vec = [0.0] * len(DEFAULT_TOOLS)
            for name, value in (row.get("gains") or {}).items():
                i = tool_index.get(name)
                if i is not None:
                    vec[i] = float(value)
            gains.append(vec)
    if not xs:
        raise SystemExit("no rows in %s" % path)
    return (np.asarray(xs, dtype="float32"), np.asarray(ys, dtype="int64"),
            np.asarray(teacher, dtype="int64"), np.asarray(trivial, dtype="float32"),
            np.asarray(gains, dtype="float32"), width)


def soft_targets(gains: np.ndarray, teacher: np.ndarray, temperature: float = 0.08
                 ) -> np.ndarray:
    """A distribution over tools from the fork's own scores.

    Hard argmax says "this tool, and the others are equally wrong". The fork says
    "this tool won by 0.5 to 0.33" — which is a much weaker claim, and training on the
    stronger one teaches a confidence the data does not support. Rows where nothing
    scored keep the teacher as a one-hot: there is no counterfactual there, only the
    teacher's opinion, and a uniform target over options that all failed would teach
    noise.
    """
    out = np.zeros_like(gains)
    for i in range(len(gains)):
        row = gains[i]
        if row.max() <= 0:
            t = teacher[i]
            if t >= 0:
                out[i, t] = 1.0
            continue
        z = np.exp((row - row.max()) / temperature)
        out[i] = z / z.sum()
    return out


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

def class_weights(labels: np.ndarray, mode: str) -> Optional[np.ndarray]:
    """Per-class loss multipliers from the training split.

    `sqrt` is the usual compromise: plain inverse frequency makes the eight rows of
    a rare tool worth as much as nine hundred rows of `farm.start`, which trades a
    class the runtime barely needs for the one it uses every step. Square-root
    inverse frequency lifts rare recalls without letting them dominate the gradient.
    Weights are normalised to mean 1, so the effective learning rate does not change
    with the class balance.
    """
    if mode == "none":
        return None
    counts = np.bincount(labels, minlength=len(DEFAULT_TOOLS)).astype("float64")
    seen = counts > 0
    power = 1.0 if mode == "inv" else 0.5
    w = np.zeros_like(counts)
    w[seen] = (counts[seen].sum() / counts[seen]) ** power
    w[seen] *= len(labels) / max(1e-9, (w[seen] * counts[seen]).sum())
    return w.astype("float32")


def train_one(hidden: int, depth: int, epochs: int, batch: int, lr: float,
              x: np.ndarray, y: np.ndarray, weight: np.ndarray,
              vx: np.ndarray, vy: np.ndarray, vteacher: np.ndarray,
              device: str, seed: int, quiet: bool = False,
              target: Optional[np.ndarray] = None,
              gains: Optional[np.ndarray] = None,
              vgains: Optional[np.ndarray] = None,
              dropout: float = 0.0, weight_decay: float = 1e-4,
              cw: Optional[np.ndarray] = None,
              select: str = "gain") -> Dict[str, Any]:
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
            if dropout > 0:
                layers.append(nn.Dropout(dropout))
    model = nn.Sequential(*layers).to(device)

    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    tx = torch.from_numpy(x).to(device)
    ty = torch.from_numpy(y).to(device)
    tw = torch.from_numpy(weight).to(device)
    tcw = torch.from_numpy(cw).to(device) if cw is not None else None
    ttarget = torch.from_numpy(target).to(device) if target is not None else None
    vxt = torch.from_numpy(vx).to(device)
    vyt = torch.from_numpy(vy).to(device)
    vgt = torch.from_numpy(vgains).to(device) if vgains is not None else None
    steps = max(1, math.ceil(len(y) / batch))
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr,
                                                total_steps=epochs * steps)

    # Model selection is on the fork's own objective, not on accuracy. The current
    # live planner already agrees with the teacher on most states; the rows that
    # separate a better policy from a teacher-copy are the ones where a different
    # tool wins. Choosing the checkpoint by accuracy selects for copying.
    best = {"score": -1.0, "state": None, "epoch": 0, "acc": 0.0, "gain": -1.0}
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
            if ttarget is not None:
                # Soft targets: cross-entropy against the fork's own distribution.
                logp = torch.log_softmax(logits, dim=1)
                if tcw is not None:
                    loss = -(ttarget[idx] * logp * tcw.unsqueeze(0)).sum(1)
                else:
                    loss = -(ttarget[idx] * logp).sum(1)
            else:
                loss = nn.functional.cross_entropy(
                    logits, ty[idx], reduction="none",
                    weight=tcw)
            loss = (loss * tw[idx]).mean()
            loss.backward()
            opt.step()
            sched.step()
            total += float(loss)

        model.eval()
        with torch.no_grad():
            logits = model(vxt)
            vacc = float((logits.argmax(1) == vyt).float().mean())
            vgain = 0.0
            if vgt is not None:
                vgain = float(vgt[torch.arange(len(vyt)), logits.argmax(1)].mean())
        if select == "accuracy":
            score = vacc
        elif select == "composite":
            score = 0.5 * vacc + 0.5 * (vgain / 2.0)   # gains are in [0, 2]
        else:
            score = vgain
        if not quiet and (epoch % 10 == 0 or epoch == epochs):
            print("  h%-5d d%d  epoch %3d  loss %.4f  val_acc %.4f  val_gain %.4f"
                  % (hidden, depth, epoch, total / steps, vacc, vgain))
        if score > best["score"] + 1e-12:
            best = {"score": score, "epoch": epoch, "acc": vacc, "gain": vgain,
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
    common = [r["f1"] for r in table if r["support"] >= 50]
    macro_f1_common = float(np.mean(common)) if common else 0.0
    params = int(sum(p.numel() for p in model.parameters()))

    # Decision quality, which is the number that matters: how much progress does the
    # chosen tool actually buy, against what the best candidate would have bought?
    decision = {}
    if vgains is not None:
        chosen = vgains[np.arange(len(pred)), pred]
        teacher_gain = (vgains[np.arange(len(pred)), np.clip(vteacher, 0, None)]
                        if known.any() else np.zeros(1))
        decision = {
            "expected_gain_model": round(float(chosen.mean()), 4),
            "expected_gain_teacher": round(float(teacher_gain.mean()), 4),
            "expected_gain_oracle": round(float(vgains.max(1).mean()), 4),
            "gain_capture": round(float(chosen.sum() / max(1e-9, vgains.max(1).sum())), 4),
        }

    return {
        "hidden": hidden, "depth": depth, "params": params,
        "val_accuracy": round(acc, 4), "train_accuracy": round(train_acc, 4),
        "teacher_agreement_on_val": round(teacher_acc, 4),
        "lift_over_teacher": round(acc - teacher_acc, 4),
        "macro_f1": round(macro_f1, 4), "macro_f1_common": round(macro_f1_common, 4),
        "ece": round(ece(probs, vy), 4),
        "best_epoch": best["epoch"],
        "lr": lr, "batch": batch, "dropout": dropout,
        "weight_decay": weight_decay, "class_weight": "none" if cw is None else "custom",
        "select": select,
        "per_class": table, "confusion_top": top_confusions(probs, vy, DEFAULT_TOOLS),
        "decision": decision,
        "model": model, "probs": probs,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path("data/tool_sft_v3"))
    ap.add_argument("--out", type=Path, default=Path("models/tool_policy.onnx"))
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--hidden", type=int, default=192)
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--batch", type=int, default=2048)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--dropout", type=float, default=0.0)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--class-weight", choices=("none", "sqrt", "inv"), default="none",
                    help="rare-class loss multiplier. Measured on the aligned "
                         "episode-held-out corpus (2026-10-26): none wins — sqrt "
                         "cost 1.8pp accuracy and 0.22 ECE, inv collapsed to half")
    ap.add_argument("--select", choices=("gain", "accuracy", "composite"), default="gain",
                    help="checkpoint criterion. gain is the fork's own score and is "
                         "the only one that prefers a better policy over a teacher copy")
    ap.add_argument("--sweep", action="store_true",
                    help="train several capacities and report which one, if any, wins")
    ap.add_argument("--targets", choices=("soft", "hard"), default="hard",
                    help="hard trains on the argmax label. soft trains on the fork's "
                         "gain distribution instead: measured 2026-10-22, it buys "
                         "+0.0003 decision gain and costs 0.16 ECE, so it is off by "
                         "default and kept only to make that comparison reproducible")
    ap.add_argument("--temperature", type=float, default=0.08)
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
    x, y, teacher, weight, gains, width = load_split(train_path)
    vx, vy, vteacher, _, vgains, _ = load_split(val_path, width)
    print("train %d  val %d  features %d  classes %d  device %s"
          % (len(y), len(vy), width, len(DEFAULT_TOOLS), device))
    support = {DEFAULT_TOOLS[i]: int((y == i).sum())
               for i in range(len(DEFAULT_TOOLS)) if (y == i).sum()}
    print("class support:", support)

    target = soft_targets(gains, teacher, args.temperature) if args.targets == "soft" else None
    if target is not None:
        print("soft targets from fork gains (temperature %.3f); %.1f%% of rows are "
              "one-hot because every candidate scored zero"
              % (args.temperature, 100.0 * float((gains.max(1) <= 0).mean())))
    cw = class_weights(y, args.class_weight)
    if cw is not None:
        print("class weights (%s): %s" % (args.class_weight,
              {DEFAULT_TOOLS[i]: round(float(w), 2) for i, w in enumerate(cw) if w}))

    configs = [(args.hidden, args.depth)]
    if args.sweep:
        configs = [(64, 2), (192, 2), (512, 2), (1024, 2), (512, 4)]

    def rank(r: Dict[str, Any]) -> float:
        gain = r["decision"].get("gain_capture", 0.0) if r["decision"] else 0.0
        if args.select == "accuracy":
            return r["val_accuracy"]
        if args.select == "composite":
            return 0.5 * r["val_accuracy"] + 0.5 * gain
        return gain

    results = []
    for hidden, depth in configs:
        print("== hidden %d depth %d ==" % (hidden, depth))
        r = train_one(hidden, depth, args.epochs, args.batch, args.lr,
                      x, y, weight, vx, vy, vteacher, device, args.seed,
                      quiet=args.sweep, target=target, gains=gains, vgains=vgains,
                      dropout=args.dropout, weight_decay=args.weight_decay,
                      cw=cw, select=args.select)
        results.append(r)
        print("   val_acc %.4f  teacher %.4f  lift %+.4f  macroF1 %.4f  ECE %.4f  params %d"
              % (r["val_accuracy"], r["teacher_agreement_on_val"],
                 r["lift_over_teacher"], r["macro_f1"], r["ece"], r["params"]))
        if r["decision"]:
            print("   gain: model %.4f  teacher %.4f  oracle %.4f  capture %.3f"
                  % (r["decision"]["expected_gain_model"],
                     r["decision"]["expected_gain_teacher"],
                     r["decision"]["expected_gain_oracle"],
                     r["decision"]["gain_capture"]))

    best = max(results, key=rank)
    if args.sweep:
        print("\ncapacity vs %s:" % args.select)
        for r in results:
            print("  h%-5d d%d  params %8d  acc %.4f  macroF1 %.4f  ECE %.4f  gain %.4f"
                  % (r["hidden"], r["depth"], r["params"], r["val_accuracy"],
                     r["macro_f1"], r["ece"],
                     r["decision"].get("gain_capture", 0.0) if r["decision"] else 0.0))
        print("  best-to-worst spread: %+.4f"
              % (rank(best) - rank(results[0])))

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
            "lr": args.lr, "batch": args.batch, "epochs": args.epochs,
            "dropout": args.dropout, "weight_decay": args.weight_decay,
            "class_weight": args.class_weight, "select": args.select,
            "data": str(args.data), "train_rows": int(len(y)),
            "val_rows": int(len(vy)), "val_split": "whole episodes",
            "rows": int(len(y) + len(vy))}
    out.with_suffix(".json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    (out.parent / "tool_policy_report.json").write_text(
        json.dumps(report, indent=1), encoding="utf-8")
    print("wrote %s (%.1f KB)" % (out, out.stat().st_size / 1024.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
