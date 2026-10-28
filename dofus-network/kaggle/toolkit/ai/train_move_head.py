"""Can the move label be learned from the *real* game at all? Separate question, separate fit.

`ai/train_foresight.py` answers "does a 32K net reproduce the harness's counterfactual ranking?" —
measured 2026-10-27: gain capture **0.9553 vs the rule teacher's 0.9433** on held-out harness
episodes. It then scored that model on 223 real actions and got **AUC 0.336, ECE 0.92**: trained
inside the harness, useless on the client. The two candidate explanations are different fixes, so
they must not be conflated:

* the *states* are out of distribution (the skew meter says 13.5% of feature cells and 100% of rows
  fall outside the harness range on `n_params`, `quest_level`, `level`, `kamas`);
* the *label* is a different event — the harness target is "progress within a 5-step fork", the
  real target is "the next snapshot from the client looked different".

This script tests the second explanation by fitting the same architecture on the real transitions
(`tools/build_live_transitions.py`, split by segment) and scoring held-out real segments. Reads are
excluded, `moved` comes only from observable deltas, and the curve heads are supervised on step 1
only — the client log shows one outcome per action, so pretending to know five is a fabrication.

Reported: AUC, ECE and the base rate side by side, because a model that always says "it will move"
has ECE ≈ the base rate and AUC ≈ 0.5, and that is exactly what the transfer run produced.

    python ai/train_move_head.py --epochs 40
    python ai/train_move_head.py --score-only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from agent.features import FEATURE_COUNT, TOOLS  # noqa: E402
from agent.foresight import DEPTH, ForesightNet  # noqa: E402
from ai.train_foresight import TOOL_SLOT, auc, ece  # noqa: E402

SPLITS = ("train", "val", "test")


def load_transitions(data: Path, split: str) -> Dict[str, np.ndarray]:
    path = data / ("%s.jsonl" % split)
    xs: List[List[float]] = []
    tools: List[int] = []
    moved: List[int] = []
    progress: List[int] = []
    segments: List[str] = []
    if not path.is_file():
        raise SystemExit("no %s — run tools/build_live_transitions.py first" % path)
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            # A read is not an action, and an action with no vector is not an example.
            if row.get("was_read") or not row.get("has_pair") or not row.get("x"):
                continue
            if row.get("tool") not in TOOL_SLOT:
                continue
            if len(row["x"]) != FEATURE_COUNT:
                continue
            xs.append([float(v) for v in row["x"]])
            tools.append(TOOL_SLOT[row["tool"]])
            moved.append(1 if row.get("moved") else 0)
            progress.append(1 if row.get("progress") else 0)
            segments.append(str(row.get("segment")))
    return {"x": np.asarray(xs, dtype=np.float32),
            "tool": np.asarray(tools, dtype=np.int64),
            "moved": np.asarray(moved, dtype=np.float32),
            "progress": np.asarray(progress, dtype=np.float32),
            "segment": segments}


def report(rows: Dict[str, np.ndarray], prob: np.ndarray, gain: np.ndarray,
           label: str) -> Dict[str, Any]:
    moved = rows["moved"]
    progress = rows["progress"]
    areauc = auc(prob, moved)
    err = ece(prob, moved)
    out = {
        "set": label,
        "rows": int(len(moved)),
        "segments": len(set(rows["segment"])),
        "base_rate_moved": round(float(moved.mean()), 4),
        "base_rate_progress": round(float(progress.mean()), 4),
        "mean_predicted_prob": round(float(prob.mean()), 4),
        "auc_moved": (round(areauc, 4) if areauc is not None else None),
        "ece_moved": (round(err, 4) if err is not None else None),
        "auc_progress": None,
    }
    areauc_p = auc(gain, progress)
    if areauc_p is not None:
        out["auc_progress"] = round(areauc_p, 4)
    # the lift that matters: what does the top decile of predictions actually achieve?
    order = np.argsort(-prob)
    top = order[:max(1, int(0.2 * len(order)))]
    out["top20pct_moved_rate"] = round(float(moved[top].mean()), 4)
    out["top20pct_progress_rate"] = round(float(progress[top].mean()), 4)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=REPO_ROOT / "data" / "transitions")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "models" / "foresight_live_v1")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--weight-decay", type=float, default=1e-3)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    train = load_transitions(args.data, "train")
    val = load_transitions(args.data, "val")
    test = load_transitions(args.data, "test")
    print("rows: train %d (%d segments), val %d, test %d"
          % (len(train["x"]), len(set(train["segment"])), len(val["x"]), len(test["x"])))
    if len(train["x"]) < 50:
        print("too few real labelled actions to fit anything", file=sys.stderr)
        return 2

    import torch

    device = "cpu"
    n_tools = len(TOOLS)
    width = FEATURE_COUNT
    mean = train["x"].mean(0)
    raw = train["x"].std(0)
    std = np.where(raw < 1e-3, 1.0, raw)
    gate = torch.nn.Parameter(torch.randn((n_tools, width)) * 0.05)
    model = torch.nn.Sequential(
        torch.nn.Linear(2 * width + n_tools, args.hidden),
        torch.nn.ReLU(),
        torch.nn.Linear(args.hidden, args.hidden),
        torch.nn.ReLU(),
        torch.nn.Linear(args.hidden, 2 + DEPTH),
    )

    def design(rows: Dict[str, np.ndarray]):
        xn = torch.from_numpy(((rows["x"] - mean) / std).astype(np.float32))
        ti = torch.from_numpy(rows["tool"])
        onehot = torch.zeros((len(xn), n_tools))
        onehot[torch.arange(len(xn)), ti] = 1.0
        return xn, ti, onehot

    txn, tti, tone = design(train)
    vxn, vti, vone = design(val)
    sxn, sti, sone = design(test)

    def pair(xn, ti, onehot, idx):
        # rebuilt per step so the gate is actually trained, not frozen at its initial values
        return torch.cat([xn[idx], onehot[idx], xn[idx] * gate[ti[idx]]], dim=1)

    ym = torch.from_numpy(train["moved"])
    yp = torch.from_numpy(train["progress"])
    opt = torch.optim.AdamW(list(model.parameters()) + [gate], lr=args.lr,
                            weight_decay=args.weight_decay)
    # the harness has ~8 classes worth of positives; the real corpus is 14% / 1.5%
    pos = float(ym.mean())
    scale = max(1.0, min(6.0, (1.0 - pos) / max(pos, 1e-3)))
    print("positive rate %.3f -> move loss weight %.2f" % (pos, scale))
    steps = max(1, (len(txn) + args.batch - 1) // args.batch)
    allv = torch.arange(len(vxn))
    best = (-1.0, None)
    started = time.time()
    for epoch in range(args.epochs):
        model.train()
        order = torch.randperm(len(txn))
        for b in range(steps):
            idx = order[b * args.batch:(b + 1) * args.batch]
            out = model(pair(txn, tti, tone, idx))
            loss_move = torch.nn.functional.binary_cross_entropy_with_logits(
                out[:, 0], ym[idx], pos_weight=torch.tensor(scale))
            # step 1 is the only horizon a real log can show; the rest stay unsupervised
            loss_curve = torch.nn.functional.mse_loss(out[:, 2], ym[idx], reduction="mean")
            loss_prog = torch.nn.functional.binary_cross_entropy_with_logits(
                out[:, 1], yp[idx], pos_weight=torch.tensor(min(6.0, scale * 4)))
            loss = loss_move.mean() + loss_curve + 0.5 * loss_prog.mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            vo = model(pair(vxn, vti, vone, allv))
            vp = torch.sigmoid(vo[:, 0]).numpy()
            vg = vo[:, 1].numpy()
        metrics = report(val, vp, vg, "val")
        score = metrics["auc_moved"] or 0.0
        print("epoch %d val auc %.4f ece %.4f top20 %.3f (base %.3f)"
              % (epoch, score, metrics["ece_moved"] or -1,
                 metrics["top20pct_moved_rate"], metrics["base_rate_moved"]))
        if score > best[0]:
            best = (score, {"net": {k: v.clone() for k, v in model.state_dict().items()},
                            "gate": gate.detach().clone(), "val": metrics})

    if best[1] is None:
        print("nothing was scored", file=sys.stderr)
        return 2
    model.load_state_dict(best[1]["net"])
    with torch.no_grad():
        gate.copy_(best[1]["gate"])
    # one fitted scalar, on val only: the class weight that makes the rare positive reachable
    # also makes every prediction over-confident, and an abstention signal must mean what it says
    with torch.no_grad():
        vout_all = model(pair(vxn, vti, vone, allv))
        vlogits = vout_all[:, 0].numpy()
        vgain = vout_all[:, 1].numpy()
    vlabel = val["moved"]
    temperature, best_nll = 1.0, None
    for candidate in np.arange(0.05, 30.01, 0.05):
        p = np.clip(1.0 / (1.0 + np.exp(-vlogits / float(candidate))), 1e-6, 1 - 1e-6)
        nll = float(-(vlabel * np.log(p) + (1 - vlabel) * np.log(1 - p)).mean())
        if best_nll is None or nll < best_nll:
            best_nll, temperature = nll, float(candidate)
    calibrated = 1.0 / (1.0 + np.exp(-vlogits / temperature))
    arrays = {
        "w1": model[0].weight.detach().numpy().T, "b1": model[0].bias.detach().numpy(),
        "w2": model[2].weight.detach().numpy().T, "b2": model[2].bias.detach().numpy(),
        "w3": model[4].weight.detach().numpy().T, "b3": model[4].bias.detach().numpy(),
        "x_mean": mean, "x_std": std, "tool_gate": gate.detach().numpy(),
    }
    with torch.no_grad():
        to = model(pair(sxn, sti, sone, torch.arange(len(sxn))))
        # the shipped path applies the temperature, so the reported numbers and the parity check
        # must use the same probability the runtime will produce
        tp = (1.0 / (1.0 + np.exp(-to[:, 0].numpy() / temperature))).astype(np.float32)
        tg = to[:, 1].numpy()
    net_np = ForesightNet({k: np.asarray(v) for k, v in arrays.items()},
                          tools=list(TOOLS),
                          meta={"depth": DEPTH, "move_temperature": temperature})
    # the shipped form is scored through agent.foresight, so parity is checked, not assumed
    scored = [net_np.score(row, TOOLS[int(tool)])
              for row, tool in zip(test["x"], test["tool"])]
    parity_move = float(np.max(np.abs(np.asarray([s["move_prob"] for s in scored]) - tp)))
    results = {"val": best[1]["val"],
               "val_calibrated": report(val, calibrated, vgain, "val"),
               "test": report(test, tp, tg, "test"),
               "move_temperature": round(temperature, 3),
               "params": int(sum(p.numel() for p in list(model.parameters()) + [gate])),
               "seconds": round(time.time() - started, 1),
               "numpy_parity_move_max_diff": round(parity_move, 8),
               "note": "trained on real labels; harness labels never entered this fit"}
    args.out.mkdir(parents=True, exist_ok=True)
    payload = {"meta": {"depth": DEPTH, "hidden": args.hidden, "params": results["params"],
                        "move_temperature": temperature,
                        "trained": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "source": "data/transitions", "labels": "real observable deltas"},
               "tools": list(TOOLS),
               "arrays": {k: np.asarray(v).tolist() for k, v in arrays.items()}}
    (args.out / "foresight.json").write_text(json.dumps(payload), encoding="utf-8")
    (args.out / "metrics_move_head.json").write_text(json.dumps(results, indent=1),
                                                     encoding="utf-8")
    print(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
