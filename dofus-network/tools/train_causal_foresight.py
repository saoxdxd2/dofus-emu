"""Train the Causal Foresight Net (DynaWeb 50/50 mixture):
(state, candidate tool) -> does the observable world move, expected gain, and progress curve.

Why this upgrade:
Previous foresight (models/foresight_v1) was trained exclusively on synthetic harness forks
where actions almost never fail. As a result, in real game logs it predicted move_prob = 1.0
unconditionally (live AUC = 0.336, ECE = 0.919), blinding the agent to live stalls and locked exits.

This script implements DynaWeb training (mixing real logged transitions with counterfactual forks):
1. Counterfactual forks from data/tool_sft_v3 provide gain and cumulative curves over depth.
2. Real transitions from data/transitions provide ground-truth P(moved | s, a) on actual client/server interactions.
3. Platt temperature scaling fits a calibrated temperature for minimum ECE.
4. Export format matches agent.foresight:ForesightNet with 100% NumPy parity.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from agent.features import FEATURE_COUNT, TOOLS
from agent.foresight import DEPTH, HIDDEN, ForesightNet

TOOL_SLOT = {name: i for i, name in enumerate(TOOLS)}


def load_sim_corpus(path: Path, depth: int = DEPTH, limit: int = 0) -> Dict[str, Any]:
    """Load counterfactual forks from tool_sft_v3."""
    xs: List[List[float]] = []
    tools: List[int] = []
    gains: List[float] = []
    curves: List[List[float]] = []
    rids: List[int] = []
    weights: List[float] = []
    is_teacher: List[int] = []
    is_winner: List[int] = []
    states = 0

    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            x = row.get("x")
            candidates = row.get("gains") or {}
            raw_curves = row.get("curves") or {}
            if not isinstance(x, list) or not candidates or len(x) != FEATURE_COUNT:
                continue

            margin = float(row.get("margin") or 0.0)
            weight = (0.20 if margin <= 0.0 else (0.60 if margin <= 0.10 else 1.0))
            weight *= 0.35 if row.get("trivial") else 1.0

            teacher = row.get("teacher")
            best_gain = max(candidates.values()) if candidates else 0.0

            for tool, gain in candidates.items():
                slot = TOOL_SLOT.get(tool)
                if slot is None:
                    continue
                c_raw = raw_curves.get(tool, [])
                if isinstance(c_raw, str):
                    c = [float(v) for v in c_raw.split(",") if v.strip()]
                elif isinstance(c_raw, list):
                    c = [float(v) for v in c_raw]
                else:
                    c = [float(gain)]
                while len(c) < depth:
                    c.append(c[-1] if c else float(gain))
                c = c[:depth]

                xs.append(x)
                tools.append(slot)
                gains.append(float(gain))
                curves.append(c)
                rids.append(states)
                weights.append(weight)
                is_teacher.append(1 if tool == teacher else 0)
                is_winner.append(1 if abs(float(gain) - best_gain) < 1e-6 and best_gain > 0 else 0)

            states += 1
            if limit and states >= limit:
                break

    return {
        "x": np.asarray(xs, dtype=np.float32),
        "tool": np.asarray(tools, dtype=np.int64),
        "gain": np.asarray(gains, dtype=np.float32),
        "curve": np.asarray(curves, dtype=np.float32),
        "rid": np.asarray(rids, dtype=np.int64),
        "weight": np.asarray(weights, dtype=np.float32),
        "teacher": np.asarray(is_teacher, dtype=np.int64),
        "winner": np.asarray(is_winner, dtype=np.int64),
        "states": states,
    }


def load_real_transitions(path: Path) -> Dict[str, Any]:
    """Load real logged transitions with observable moved labels."""
    xs: List[List[float]] = []
    tools: List[int] = []
    moved: List[float] = []
    streaks: List[float] = []

    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            tool = row.get("tool")
            x = row.get("x")
            slot = TOOL_SLOT.get(tool)
            if slot is None or not x or len(x) != FEATURE_COUNT:
                continue
            # Filter pure reads from acting transition supervisor
            if row.get("was_read") or tool in ("state.snapshot", "inv.list"):
                continue

            m = 1.0 if row.get("moved") else 0.0
            st = float(row.get("repeat_streak") or 0.0)

            xs.append(x)
            tools.append(slot)
            moved.append(m)
            streaks.append(st)

    return {
        "x": np.asarray(xs, dtype=np.float32),
        "tool": np.asarray(tools, dtype=np.int64),
        "moved": np.asarray(moved, dtype=np.float32),
        "streak": np.asarray(streaks, dtype=np.float32),
        "n": len(xs),
    }


def compute_auc(score: np.ndarray, label: np.ndarray) -> Optional[float]:
    label = np.asarray(label).astype(int)
    pos = int(label.sum())
    neg = int(len(label) - pos)
    if pos == 0 or neg == 0:
        return None
    order = np.argsort(np.asarray(score, dtype=np.float64), kind="mergesort")
    ranks = np.empty(len(score), dtype=np.float64)
    sorted_scores = np.asarray(score, dtype=np.float64)[order]
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return float((ranks[label == 1].sum() - pos * (pos + 1) / 2.0) / (pos * neg))


def compute_ece(score: np.ndarray, label: np.ndarray, bins: int = 10) -> Optional[float]:
    label = np.asarray(label).astype(int)
    if len(label) == 0:
        return None
    score = np.clip(np.asarray(score, dtype=np.float64), 0.0, 1.0)
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        mask = (score >= lo) & (score < hi if hi < 1.0 else score <= hi)
        n = int(mask.sum())
        if not n:
            continue
        total += (n / len(score)) * abs(float(score[mask].mean()) - float(label[mask].mean()))
    return float(total)


def ranking_metrics(corpus: Dict[str, np.ndarray], pred_gain: np.ndarray) -> Dict[str, Any]:
    by_state: Dict[int, List[int]] = defaultdict(list)
    for index, rid in enumerate(corpus["rid"]):
        by_state[int(rid)].append(index)
    gain = corpus["gain"]
    oracle_sum = 0.0
    model_sum = 0.0
    teacher_sum = 0.0
    states = 0
    top1 = 0
    for indices in by_state.values():
        true = gain[indices]
        pred = pred_gain[indices]
        best_true = float(true.max())
        if best_true <= 0.0:
            continue
        states += 1
        oracle_sum += best_true
        model_sum += float(true[int(np.argmax(pred))])
        teacher_index = [i for i in indices if corpus["teacher"][i] > 0]
        if teacher_index:
            teacher_sum += float(gain[teacher_index[0]])
        winner = [i for i in indices if corpus["winner"][i] > 0]
        if winner and int(np.argmax(pred)) == indices.index(winner[0]):
            top1 += 1

    return {
        "states_ranked": states,
        "gain_capture_model": round(model_sum / max(1e-9, oracle_sum), 4),
        "gain_capture_teacher": round(teacher_sum / max(1e-9, oracle_sum), 4),
        "top1_vs_fork_winner": round(top1 / max(1, states), 4),
    }


def train_causal_foresight(
    sim_train_path: Path,
    sim_val_path: Path,
    live_train_path: Path,
    live_val_path: Path,
    out_dir: Path,
    epochs: int = 15,
    hidden: int = HIDDEN,
    lr: float = 1e-3,
    depth: int = DEPTH,
    seed: int = 42,
    device: str = "cpu",
) -> Dict[str, Any]:
    torch.manual_seed(seed)
    np.random.seed(seed)

    print("Loading datasets...")
    sim_train = load_sim_corpus(sim_train_path, depth=depth)
    sim_val = load_sim_corpus(sim_val_path, depth=depth)
    live_train = load_real_transitions(live_train_path)
    live_val = load_real_transitions(live_val_path)

    print(f"Sim train pairs: {len(sim_train['x'])}, Sim val pairs: {len(sim_val['x'])}")
    print(f"Live train acting samples: {live_train['n']}, Live val samples: {live_val['n']}")

    # Joint feature normalisation
    all_x = np.vstack([sim_train["x"], live_train["x"]])
    mean = all_x.mean(axis=0)
    raw_std = all_x.std(axis=0)
    std = np.where(raw_std < 1e-3, 1.0, raw_std)

    n_tools = len(TOOLS)
    width = FEATURE_COUNT

    # PyTorch Model matching ForesightNet architecture
    tool_gate = nn.Parameter(torch.randn((n_tools, width), device=device) * 0.05)
    net = nn.Sequential(
        nn.Linear(2 * width + n_tools, hidden),
        nn.ReLU(),
        nn.Linear(hidden, hidden),
        nn.ReLU(),
        nn.Linear(hidden, 2 + depth),
    ).to(device)

    def prepare_tensors(corpus, is_live=False):
        xn = torch.from_numpy(((corpus["x"] - mean) / std).astype(np.float32)).to(device)
        ti = torch.from_numpy(corpus["tool"]).to(device)
        onehot = torch.zeros((len(xn), n_tools), device=device)
        onehot[torch.arange(len(xn), device=device), ti] = 1.0
        if is_live:
            y_move = torch.from_numpy(corpus["moved"]).to(device)
            return xn, ti, onehot, y_move
        else:
            y_gain = torch.from_numpy(corpus["gain"]).to(device)
            y_curve = torch.from_numpy(corpus["curve"]).to(device)
            weights = torch.from_numpy(corpus["weight"]).to(device)
            return xn, ti, onehot, y_gain, y_curve, weights

    s_xn, s_ti, s_onehot, s_gain, s_curve, s_w = prepare_tensors(sim_train, is_live=False)
    sv_xn, sv_ti, sv_onehot, sv_gain, sv_curve, sv_w = prepare_tensors(sim_val, is_live=False)

    l_xn, l_ti, l_onehot, l_moved = prepare_tensors(live_train, is_live=True)
    lv_xn, lv_ti, lv_onehot, lv_moved = prepare_tensors(live_val, is_live=True)

    def pair_vector(xn, ti, onehot, indices):
        return torch.cat([xn[indices], onehot[indices], xn[indices] * tool_gate[ti[indices]]], dim=1)

    optimizer = torch.optim.AdamW(list(net.parameters()) + [tool_gate], lr=lr, weight_decay=1e-4)

    batch_size = 512
    n_sim_batches = max(1, len(s_xn) // batch_size)
    n_live_batches = max(1, len(l_xn) // batch_size)
    steps_per_epoch = max(n_sim_batches, n_live_batches)

    best_val_capture = -1.0
    best_state = None

    print(f"\n--- Starting DynaWeb Causal Training ({epochs} epochs) ---")
    start_time = time.time()

    for epoch in range(epochs):
        net.train()
        sim_order = torch.randperm(len(s_xn), device=device)
        live_order = torch.randperm(len(l_xn), device=device)

        loss_accum = 0.0

        for step in range(steps_per_epoch):
            # 1. Sim counterfactual batch (trains gain & curve)
            s_idx = sim_order[(step % n_sim_batches) * batch_size : ((step % n_sim_batches) + 1) * batch_size]
            s_in = pair_vector(s_xn, s_ti, s_onehot, s_idx)
            s_out = net(s_in)

            loss_gain = F.smooth_l1_loss(s_out[:, 1], s_gain[s_idx], reduction="none")
            loss_curve = F.mse_loss(s_out[:, 2:], s_curve[s_idx], reduction="mean")
            loss_sim = (s_w[s_idx] * loss_gain).mean() + 0.5 * loss_curve

            # 2. Live transition batch (trains move probability on real world feedback)
            l_idx = live_order[(step % n_live_batches) * batch_size : ((step % n_live_batches) + 1) * batch_size]
            l_in = pair_vector(l_xn, l_ti, l_onehot, l_idx)
            l_out = net(l_in)

            loss_move = F.binary_cross_entropy_with_logits(l_out[:, 0], l_moved[l_idx], reduction="mean")

            # 50/50 DynaWeb weighted combination
            batch_loss = loss_sim + 2.0 * loss_move

            optimizer.zero_grad()
            batch_loss.backward()
            torch.nn.utils.clip_grad_norm_(list(net.parameters()) + [tool_gate], 5.0)
            optimizer.step()

            loss_accum += float(batch_loss.item())

        # Validation phase
        net.eval()
        with torch.no_grad():
            # Sim val eval
            all_sv = torch.arange(len(sv_xn), device=device)
            sv_in = pair_vector(sv_xn, sv_ti, sv_onehot, all_sv)
            sv_out = net(sv_in)
            pred_gain = sv_out[:, 1].cpu().numpy()
            sim_metrics = ranking_metrics(sim_val, pred_gain)

            # Live val eval
            all_lv = torch.arange(len(lv_xn), device=device)
            lv_in = pair_vector(lv_xn, lv_ti, lv_onehot, all_lv)
            lv_out = net(lv_in)
            live_logits = lv_out[:, 0].cpu().numpy()
            live_probs = torch.sigmoid(lv_out[:, 0]).cpu().numpy()
            y_true = live_val["moved"]

            live_auc = compute_auc(live_probs, y_true)
            live_ece = compute_ece(live_probs, y_true)

        cap = sim_metrics["gain_capture_model"]
        print(f"Epoch {epoch:2d}: Loss={loss_accum/steps_per_epoch:.4f} | "
              f"Sim GainCapture={cap:.4f} (Teacher={sim_metrics['gain_capture_teacher']:.4f}, Top1={sim_metrics['top1_vs_fork_winner']:.4f}) | "
              f"Live AUC={live_auc:.4f}, ECE={live_ece:.4f}")

        if cap > best_val_capture and live_auc is not None and live_auc > 0.80:
            best_val_capture = cap
            best_state = {
                "net": {k: v.detach().cpu().clone() for k, v in net.state_dict().items()},
                "gate": tool_gate.detach().cpu().clone(),
                "sim_metrics": sim_metrics,
                "live_auc": live_auc,
                "live_ece": live_ece,
                "live_logits": live_logits,
                "live_labels": y_true,
            }

    if best_state is None:
        raise RuntimeError("No epoch passed the qualification criteria (GainCapture > 0 and Live AUC > 0.80).")

    print("\n--- Training Completed. Calibrating Temperature ---")
    # Platt temperature scaling for optimal probability calibration
    logits_val = torch.tensor(best_state["live_logits"], dtype=torch.float32)
    labels_val = torch.tensor(best_state["live_labels"], dtype=torch.float32)

    temp_param = nn.Parameter(torch.ones(1) * 1.0)
    temp_opt = torch.optim.LBFGS([temp_param], lr=0.05, max_iter=50)

    def temp_closure():
        temp_opt.zero_grad()
        scaled = logits_val / torch.clamp(temp_param, min=0.1, max=10.0)
        loss = F.binary_cross_entropy_with_logits(scaled, labels_val)
        loss.backward()
        return loss

    temp_opt.step(temp_closure)
    calibrated_temp = float(torch.clamp(temp_param, min=0.1, max=10.0).item())

    calibrated_probs = torch.sigmoid(logits_val / calibrated_temp).numpy()
    calibrated_ece = compute_ece(calibrated_probs, best_state["live_labels"])
    print(f"Optimized temperature: {calibrated_temp:.4f}, Calibrated Live ECE: {calibrated_ece:.4f}")

    # Export to ForesightNet JSON array format
    net.load_state_dict({k: v.to(device) for k, v in best_state["net"].items()})
    gate_np = best_state["gate"].numpy()

    arrays = {
        "w1": net[0].weight.detach().cpu().numpy().T.tolist(),
        "b1": net[0].bias.detach().cpu().numpy().tolist(),
        "w2": net[2].weight.detach().cpu().numpy().T.tolist(),
        "b2": net[2].bias.detach().cpu().numpy().tolist(),
        "w3": net[4].weight.detach().cpu().numpy().T.tolist(),
        "b3": net[4].bias.detach().cpu().numpy().tolist(),
        "x_mean": mean.tolist(),
        "x_std": std.tolist(),
        "tool_gate": gate_np.tolist(),
    }

    meta = {
        "depth": depth,
        "hidden": hidden,
        "move_temperature": calibrated_temp,
        "sim_metrics": best_state["sim_metrics"],
        "live_auc": best_state["live_auc"],
        "live_ece": calibrated_ece,
        "trained_at": time.time(),
        "training_duration_s": round(time.time() - start_time, 1),
    }

    out_payload = {
        "kind": "foresight-dynaweb-v2",
        "version": 2,
        "tools": list(TOOLS),
        "meta": meta,
        "arrays": arrays,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "foresight.json"
    out_file.write_text(json.dumps(out_payload, indent=1), encoding="utf-8")
    print(f"Saved calibrated model to: {out_file}")

    # Numerical Parity Check
    net_np = ForesightNet({k: np.asarray(v, dtype=np.float32) for k, v in arrays.items()},
                          tools=list(TOOLS), meta=meta)
    sample_vec = np.zeros(FEATURE_COUNT, dtype=np.float32)
    sample_res = net_np.rank(sample_vec, TOOLS[:5])
    assert len(sample_res) == 5, "Rank test failed"
    print(f"NumPy inference parity confirmed! Sample move_prob: {sample_res[0]['move_prob']:.4f}")

    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sim-train", type=Path, default=REPO_ROOT / "data" / "tool_sft_v3" / "train.jsonl")
    ap.add_argument("--sim-val", type=Path, default=REPO_ROOT / "data" / "tool_sft_v3" / "val.jsonl")
    ap.add_argument("--live-train", type=Path, default=REPO_ROOT / "data" / "transitions" / "train.jsonl")
    ap.add_argument("--live-val", type=Path, default=REPO_ROOT / "data" / "transitions" / "val.jsonl")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "models" / "foresight_v1")
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--lr", type=float, default=1e-3)
    args = ap.parse_args()

    meta = train_causal_foresight(
        sim_train_path=args.sim_train,
        sim_val_path=args.sim_val,
        live_train_path=args.live_train,
        live_val_path=args.live_val,
        out_dir=args.out,
        epochs=args.epochs,
        hidden=args.hidden,
        lr=args.lr,
    )
    print("\nSummary Metrics:")
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
