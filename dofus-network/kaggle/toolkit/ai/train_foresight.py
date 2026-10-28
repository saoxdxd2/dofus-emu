"""Train the foresight net: (state, candidate tool) -> does the world move, and how much.

Why this and not another tool classifier. The shipped tool policy maps a state to *one* tool and
is scored on whether that agrees with a teacher. The live logs say the interesting question is
different: of 1,827 actions actually issued against the real client, 14.0% changed the world and
1.5% changed a quest counter (`data/transitions/manifest.json`). "Would this candidate move this
state?" is the judgement that predicts that, and it is the one the planner cannot currently make
— which is why a stalled run keeps issuing the same inert action.

Supervision is the harness's own counterfactual forks (`data/tool_sft`: per-candidate `gains`,
per-candidate cumulative `curves`, `margin`/`trivial` row weights already recorded), so the label
is a fact about the world, not the rules' opinion. The state vector is `objective_features`' 42
numbers — the same call the sim corpus, `agent/planner_nn.py` and `tools/build_live_transitions.py`
use, so the two sides of the transfer test cannot drift apart.

What it reports, in order of how much it matters:
1. `gain_capture` on held-out harness episodes, against the **teacher's own** capture on the same
   rows (the rule's score, not a human's guess);
2. AUC and ECE of `move_prob` on **real** logged transitions — the sim-to-live number;
3. the flat-candidate rate, because an escalation decision is only as good as its abstention.

The weights are exported as JSON arrays and scored by `agent/foresight.py` with numpy only.

    python ai/train_foresight.py --epochs 12 --hidden 128
    python ai/train_foresight.py --live-only      # re-score an existing artifact
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from agent.features import FEATURE_COUNT, TOOLS  # noqa: E402
from agent.foresight import DEPTH, ForesightNet  # noqa: E402

TOOL_SLOT = {name: i for i, name in enumerate(TOOLS)}


# -- corpus ----------------------------------------------------------------

def _curve(raw: Any, depth: int) -> List[float]:
    values = [float(c) for c in str(raw or "").split(",") if c.strip()]
    if not values:
        values = [0.0]
    values = values[:depth]
    while len(values) < depth:
        values.append(values[-1])
    return values


def load_corpus(path: Path, depth: int = DEPTH, limit: int = 0) -> Dict[str, Any]:
    """One row per (state, candidate) pair, keeping the state's id so ranking can be scored.

    The feature width is *inferred and enforced*, not assumed. The corpus on disk was generated on
    2026-10-22 with 41 numbers while today's extractor emits 42; training across that seam would
    report a sim-to-live transfer number that means nothing, so a ragged file raises and a stale
    width is refused by the caller rather than silently dropped row by row.
    """
    xs: List[List[float]] = []
    tools: List[int] = []
    gains: List[float] = []
    curves: List[List[float]] = []
    rids: List[int] = []
    weights: List[float] = []
    is_teacher: List[int] = []
    is_winner: List[int] = []
    states = 0
    width = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            x = row.get("x")
            candidates = row.get("gains") or {}
            if not isinstance(x, list) or not candidates:
                continue
            if width and len(x) != width:
                raise ValueError("ragged corpus %s: row %d has %d features, expected %d"
                                 % (path, states, len(x), width))
            width = len(x)
            margin = float(row.get("margin") or 0.0)
            weight = (0.20 if margin <= 0.0 else (0.60 if margin <= 0.10 else 1.0))
            weight *= 0.35 if row.get("trivial") else 1.0
            added = 0
            for tool, gain in candidates.items():
                slot = TOOL_SLOT.get(tool)
                if slot is None:
                    continue
                xs.append([float(v) for v in x])
                tools.append(slot)
                gains.append(float(gain))
                curves.append(_curve((row.get("curves") or {}).get(tool), depth))
                rids.append(states)
                weights.append(weight)
                is_teacher.append(1 if tool == row.get("teacher") else 0)
                is_winner.append(1 if tool == row.get("tool") else 0)
                added += 1
            if added:
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
        "teacher": np.asarray(is_teacher, dtype=np.float32),
        "winner": np.asarray(is_winner, dtype=np.float32),
        "states": states,
        "width": width,
    }


def load_live(paths: Sequence[Path]) -> Dict[str, Any]:
    """Real transitions with a rebuilt 42-vector: the only honest test of transfer."""
    rows: List[Dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if not row.get("has_pair") or row.get("was_read") or not row.get("x"):
                    continue
                if row.get("tool") not in TOOL_SLOT:
                    continue
                rows.append(row)
    return {"rows": rows}


# -- metrics ---------------------------------------------------------------

def auc(score: np.ndarray, label: np.ndarray) -> Optional[float]:
    """Rank-based AUC; None when one class is absent (a number here would be theatre)."""
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


def ece(score: np.ndarray, label: np.ndarray, bins: int = 10) -> Optional[float]:
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


def ranking_metrics(corpus: Dict[str, np.ndarray], pred_gain: np.ndarray,
                    pred_move: np.ndarray) -> Dict[str, Any]:
    """Score the ranker per state, and always against the teacher's own pick."""
    by_state: Dict[int, List[int]] = defaultdict(list)
    for index, rid in enumerate(corpus["rid"]):
        by_state[int(rid)].append(index)
    gain = corpus["gain"]
    oracle_sum = 0.0
    model_sum = 0.0
    teacher_sum = 0.0
    states = 0
    top1 = 0
    flat_model = 0
    flat_true = 0
    for indices in by_state.values():
        true = gain[indices]
        pred = pred_gain[indices]
        best_true = float(true.max())
        if best_true <= 0.0:
            flat_true += 1
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
        if float(pred.max()) <= 0.0:
            flat_model += 1
    label = (gain > 0).astype(np.int64)
    return {
        "states_ranked": states,
        "gain_capture_model": round(model_sum / max(1e-9, oracle_sum), 4),
        "gain_capture_teacher": round(teacher_sum / max(1e-9, oracle_sum), 4),
        "top1_vs_fork_winner": round(top1 / max(1, states), 4),
        "move_auc": (round(auc(pred_move, label), 4) if auc(pred_move, label) is not None else None),
        "flat_true_states": flat_true,
        "flat_model_states": flat_model,
    }


def feature_skew(corpus_x: np.ndarray, live_x: np.ndarray, mean: np.ndarray,
                 std: np.ndarray) -> Dict[str, Any]:
    """How far the real states sit outside the harness's own feature range.

    This is the number that decides whether a sim-trained score on live rows means anything: a
    model evaluated on inputs it never saw is not transferring, it is extrapolating.
    """
    from agent.features import FEATURE_NAMES

    if len(live_x) == 0:
        return {"live_rows": 0}
    lo, hi = corpus_x.min(0), corpus_x.max(0)
    # A column the harness pins to a constant has no range to fall outside; counting it would
    # report 100% skew while telling you nothing.
    varying = (hi - lo) > 1e-9
    outside = ((live_x < lo) | (live_x > hi)) & varying[None, :]
    z = np.abs((live_x - mean) / std)
    counts = outside.sum(0)
    worst = sorted(zip(FEATURE_NAMES, counts), key=lambda kv: -kv[1])[:6]
    return {
        "live_rows": int(len(live_x)),
        "varying_features": int(varying.sum()),
        "rows_outside_harness_range_pct": round(100.0 * float(outside.any(1).mean()), 1),
        "cells_outside_range_pct": round(100.0 * float(outside.sum()
                                                       / max(1, int(varying.sum()) * len(live_x))), 1),
        "max_abs_z": round(float(z.max()), 1),
        "p99_abs_z": round(float(np.percentile(z, 99)), 1),
        "worst_features": [(name, int(count)) for name, count in worst if count],
    }


def live_metrics(rows: List[Dict[str, Any]], net: ForesightNet) -> Dict[str, Any]:
    """Score the artifact against real logged outcomes.

    The real logs contain only the tool that was actually issued, so no counterfactual ranking is
    observable here; what *is* observable is whether the predicted "this will move the world"
    tracks what really moved. That is the transfer test, and it is reported whatever it says.
    """
    scored = [(net.score(row["x"], row["tool"]), row) for row in rows]
    scored = [(cell, row) for cell, row in scored if cell is not None]
    if not scored:
        return {"live_rows": 0}
    moved = np.asarray([1.0 if row["moved"] else 0.0 for _, row in scored], dtype=np.int64)
    progress = np.asarray([1.0 if row["progress"] else 0.0 for _, row in scored], dtype=np.int64)
    prob = np.asarray([cell["move_prob"] for cell, _ in scored], dtype=np.float64)
    gain = np.asarray([cell["gain"] for cell, _ in scored], dtype=np.float64)
    move_auc = auc(prob, moved)
    move_ece = ece(prob, moved)
    per_tool: Dict[str, Dict[str, float]] = {}
    for cell, row in scored:
        bucket = per_tool.setdefault(row["tool"], {"n": 0.0, "moved": 0.0, "mean_prob": 0.0})
        bucket["n"] += 1
        bucket["moved"] += float(row["moved"])
        bucket["mean_prob"] += float(cell["move_prob"])
    return {
        "live_rows": len(scored),
        "live_move_base_rate": round(float(moved.mean()), 4),
        "live_progress_base_rate": round(float(progress.mean()), 4),
        "live_move_auc": (round(move_auc, 4) if move_auc is not None else None),
        "live_move_ece": (round(move_ece, 4) if move_ece is not None else None),
        "mean_predicted_gain": round(float(gain.mean()), 4),
        "flat_predictions_pct": round(100.0 * float(np.mean([c["flat"] for c, _ in scored])), 1),
        "per_tool": {tool: {"n": int(cell["n"]),
                            "moved_pct": round(100.0 * cell["moved"] / max(1, cell["n"]), 1),
                            "mean_prob": round(cell["mean_prob"] / max(1, cell["n"]), 3)}
                     for tool, cell in sorted(per_tool.items())},
    }


# -- training --------------------------------------------------------------

def train(args: argparse.Namespace) -> int:
    import torch

    train_corpus = load_corpus(args.data / "train.jsonl", args.depth, args.limit)
    val_corpus = load_corpus(args.val or (args.data / "val.jsonl"), args.depth, args.val_limit)
    print("corpus: train %d pairs / %d states, val %d pairs / %d states"
          % (len(train_corpus["x"]), train_corpus["states"],
             len(val_corpus["x"]), val_corpus["states"]))
    if len(train_corpus["x"]) == 0:
        print("no usable pairs; is data/tool_sft generated?", file=sys.stderr)
        return 2
    if train_corpus["width"] != FEATURE_COUNT or val_corpus["width"] != FEATURE_COUNT:
        print("corpus width %d, live extractor emits %d — the features moved. Regenerate:\n"
              "    python sim/tool_choice.py --episodes 120 --steps 90 --noise --travel-cost "
              "--out data/tool_sft_v2" % (train_corpus["width"], FEATURE_COUNT), file=sys.stderr)
        return 2

    x_all = train_corpus["x"]
    mean = x_all.mean(0)
    raw_std = x_all.std(0)
    # `objective_features` already scales its outputs, and the harness pins many features to a
    # constant (a level-20..100 curriculum never has `level` < 0.1, `err_none` is on or off).
    # Dividing by that zero std turned a constant feature into a 1e6-magnitude input, which is
    # what made the first transfer run predict gains of 95,000 on real states.
    std = np.where(raw_std < 1e-3, 1.0, raw_std)
    n_tools = len(TOOLS)
    width = FEATURE_COUNT

    # A learned per-tool gate over the state: the pair vector is [x | onehot | x*gate(tool)].
    torch.manual_seed(args.seed)
    gate = (torch.randn((n_tools, width), device=args.device) * 0.05)
    gate = torch.nn.Parameter(gate)
    net = torch.nn.Sequential(
        torch.nn.Linear(2 * width + n_tools, args.hidden),
        torch.nn.ReLU(),
        torch.nn.Linear(args.hidden, args.hidden),
        torch.nn.ReLU(),
        torch.nn.Linear(args.hidden, 2 + args.depth),
    ).to(args.device)
    params = sum(p.numel() for p in list(net.parameters()) + [gate])
    print("model: %d params (hidden %d, width %d, tools %d)" % (params, args.hidden, width, n_tools))

    def norm(corpus: Dict[str, np.ndarray]):
        xn = torch.from_numpy(((corpus["x"] - mean) / std).astype(np.float32)).to(args.device)
        ti = torch.from_numpy(corpus["tool"]).to(args.device)
        onehot = torch.zeros((len(xn), n_tools), device=args.device)
        onehot[torch.arange(len(xn), device=args.device), ti] = 1.0
        return xn, ti, onehot

    def pair(xn: Any, ti: Any, onehot: Any, idx: Any) -> Any:
        # rebuilt every step: the gate is a trained parameter, so freezing the pair vector here
        # would silently train on a random projection and never update the tool interaction
        return torch.cat([xn[idx], onehot[idx], xn[idx] * gate[ti[idx]]], dim=1)

    txn, tti, tone = norm(train_corpus)
    vvn, vti, vone = norm(val_corpus)
    tg = torch.from_numpy(train_corpus["gain"]).to(args.device)
    tc = torch.from_numpy(train_corpus["curve"]).to(args.device)
    tm = (torch.from_numpy(train_corpus["gain"]) > 0).float().to(args.device)
    tw = torch.from_numpy(train_corpus["weight"]).to(args.device)

    opt = torch.optim.AdamW(list(net.parameters()) + [gate], lr=args.lr, weight_decay=1e-4)
    steps = max(1, (len(txn) + args.batch - 1) // args.batch)
    allv = torch.arange(len(vvn), device=args.device)
    best_score = -1.0
    best_state: Optional[Dict[str, Any]] = None
    started = time.time()
    for epoch in range(args.epochs):
        net.train()
        order = torch.randperm(len(txn), device=args.device)
        running = 0.0
        for b in range(steps):
            idx = order[b * args.batch:(b + 1) * args.batch]
            out = net(pair(txn, tti, tone, idx))
            loss_move = torch.nn.functional.binary_cross_entropy_with_logits(
                out[:, 0], tm[idx], reduction="none")
            loss_gain = torch.nn.functional.smooth_l1_loss(out[:, 1], tg[idx], reduction="none")
            loss_curve = torch.nn.functional.mse_loss(out[:, 2:], tc[idx], reduction="mean")
            batch_loss = (tw[idx] * (loss_move + loss_gain)).mean() + loss_curve
            opt.zero_grad()
            batch_loss.backward()
            torch.nn.utils.clip_grad_norm_(list(net.parameters()) + [gate], 5.0)
            opt.step()
            running += float(batch_loss.item())
        # validation is a ranking score, and the checkpoint is chosen on it: the last epoch is
        # not the best epoch, and a policy that cannot say which one it shipped is not trustworthy
        net.eval()
        with torch.no_grad():
            vout = net(pair(vvn, vti, vone, allv))
            vp_gain = vout[:, 1].cpu().numpy()
            vp_move = torch.sigmoid(vout[:, 0]).cpu().numpy()
        vrank = ranking_metrics(val_corpus, vp_gain, vp_move)
        score = vrank["gain_capture_model"]
        print("epoch %d loss %.4f capture %.4f (teacher %.4f) top1 %.4f auc %s"
              % (epoch, running / steps, score, vrank["gain_capture_teacher"],
                 vrank["top1_vs_fork_winner"], vrank["move_auc"]))
        if score > best_score:
            best_score = score
            best_state = {"net": {k: v.detach().cpu().clone()
                                  for k, v in net.state_dict().items()},
                          "gate": gate.detach().cpu().clone(),
                          "rank": vrank, "gain": vp_gain, "move": vp_move}

    if best_state is None:
        print("no epoch produced a measurable ranking", file=sys.stderr)
        return 2
    net.load_state_dict({k: v.to(args.device) for k, v in best_state["net"].items()})
    with torch.no_grad():
        gate.copy_(best_state["gate"].to(args.device))
    vrank = best_state["rank"]
    arrays = {
        "w1": net[0].weight.detach().cpu().numpy().T,
        "b1": net[0].bias.detach().cpu().numpy(),
        "w2": net[2].weight.detach().cpu().numpy().T,
        "b2": net[2].bias.detach().cpu().numpy(),
        "w3": net[4].weight.detach().cpu().numpy().T,
        "b3": net[4].bias.detach().cpu().numpy(),
        "x_mean": mean, "x_std": std,
        "tool_gate": gate.detach().cpu().numpy(),
    }
    net_np = ForesightNet({k: np.asarray(v) for k, v in arrays.items()},
                          tools=list(TOOLS),
                          meta={"depth": args.depth, "hidden": args.hidden})
    with torch.no_grad():
        vout = net(pair(vvn, vti, vone, allv))
        vp_gain = vout[:, 1].cpu().numpy()
        vp_move = torch.sigmoid(vout[:, 0]).cpu().numpy()
    # the artifact that ships is the JSON array form, so it is scored here in numpy and compared
    # against the torch pass — a parity gap means the exporter and the runtime disagree
    xnv = ((val_corpus["x"] - mean) / std).astype(np.float32)
    oneh = np.zeros((len(xnv), n_tools), dtype=np.float32)
    oneh[np.arange(len(xnv)), val_corpus["tool"]] = 1.0
    pairs_np = np.concatenate([xnv, oneh,
                               xnv * np.asarray(arrays["tool_gate"], dtype=np.float32)[
                                   val_corpus["tool"]]], axis=1)
    rows_np = net_np.score_vector(pairs_np)
    parity = {
        "gain": float(np.max(np.abs(np.asarray([r["gain"] for r in rows_np]) - vp_gain))),
        "move": float(np.max(np.abs(np.asarray([r["move_prob"] for r in rows_np]) - vp_move))),
    }
    metrics = {"sim_val": vrank, "params": params,
               "train_seconds": round(time.time() - started, 1),
               "best_val_capture": round(best_score, 4),
               "numpy_parity_max_diff": parity}
    if args.live:
        live = load_live(args.live)
        metrics["live"] = live_metrics(live["rows"], net_np)
        live_x = (np.asarray([row["x"] for row in live["rows"]], dtype=np.float32)
                  if live["rows"] else np.zeros((0, FEATURE_COUNT), dtype=np.float32))
        metrics["live"]["feature_skew"] = feature_skew(train_corpus["x"], live_x, mean, std)
    args.out.mkdir(parents=True, exist_ok=True)
    if not args.live_only:
        payload = {"meta": {"depth": args.depth, "hidden": args.hidden,
                            "feature_count": int(FEATURE_COUNT), "params": params,
                            "trained": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "corpus": str(args.data)},
                   "tools": list(TOOLS),
                   "arrays": {k: np.asarray(v).tolist() for k, v in arrays.items()}}
        (args.out / "foresight.json").write_text(json.dumps(payload), encoding="utf-8")
        print("wrote %s" % (args.out / "foresight.json"))
    (args.out / "metrics_foresight.json").write_text(json.dumps(metrics, indent=1),
                                                     encoding="utf-8")
    print(json.dumps(metrics, indent=1))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=REPO_ROOT / "data" / "tool_sft")
    ap.add_argument("--val", type=Path, default=None)
    ap.add_argument("--live", type=Path, nargs="*",
                    default=[REPO_ROOT / "data" / "transitions" / "val.jsonl",
                             REPO_ROOT / "data" / "transitions" / "test.jsonl"])
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "models" / "foresight_v1")
    ap.add_argument("--depth", type=int, default=DEPTH)
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--val-limit", type=int, default=0)
    ap.add_argument("--eval-every", type=int, default=0)
    ap.add_argument("--live-only", action="store_true",
                    help="score the existing artifact on the real transitions and exit")
    args = ap.parse_args(argv)
    if args.live_only:
        from agent.foresight import load as load_net
        net = load_net(args.out / "foresight.json")
        if net is None:
            print("no artifact at %s" % (args.out / "foresight.json"), file=sys.stderr)
            return 2
        live = load_live(args.live)
        print(json.dumps(live_metrics(live["rows"], net), indent=1))
        return 0
    try:
        return train(args)
    except ModuleNotFoundError:
        print("foresight training needs the python environment with torch", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
