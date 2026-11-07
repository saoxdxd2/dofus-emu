"""Capacity or coverage? A compute-matched ladder over the candidate-conditioned foresight net.

The shipped model is 31,865 parameters and it already matches the rule teacher inside the harness
(gain capture 0.9553 against the teacher's own 0.9433 on held-out episodes) while scoring *below
chance* on 223 real logged actions. Two explanations compete, and they imply opposite work:

* capacity — a bigger net would fit the harness counterfactuals better and generalise to real
  states; this kernel tests it by training identical optimisers, identical data order, identical
  steps, and only changing the width (32K / 0.3M / 4.4M);
* coverage — the harness's state distribution never contains a level-3 tutorial character with 0
  kamas, so no amount of width can transfer (the local skew meter reports 100% of real rows
  outside the harness range on n_params / quest_level / level / kamas).

Read the arms' harness capture against each other: if it barely moves with 100x the parameters,
capacity is not the bottleneck and the effort belongs in data. Read the live AUC of every arm
against 0.5: a big arm that still cannot beat chance on real outcomes proves the transfer gap is
distributional, not optimisation.

Labels here are the harness's own forks and the client's own observable deltas — never the rule's
verdict of its own action.
"""

import json
import os
import time

import numpy as np
import torch

SEED = 0
STEPS = 4000
BATCH = 1024
LR = 3e-4
ARMS = (128, 512, 2048)
DEPTH = 5


def find_root():
    # Kaggle has mounted a dataset as both /kaggle/input/<slug>/ and
    # /kaggle/input/<slug>/<slug>/, so walk every depth instead of guessing one; a run
    # that cannot find the corpus must say what it did see.
    seen = []
    for base in ("/kaggle/input", "/mnt/data"):
        for dirpath, dirnames, filenames in os.walk(base):
            seen.append(os.path.join(dirpath, ",".join(sorted(dirnames)[:6]) or "-"))
            if "meta.json" in filenames:
                return dirpath
    raise SystemExit("the foresight corpus is not mounted; saw %s" % seen[:12])


ROOT = find_root()
META = json.load(open(os.path.join(ROOT, "meta.json"), encoding="utf-8"))
TOOLS, WIDTH = META["tools"], META["feature_count"]
SLOT = {name: i for i, name in enumerate(TOOLS)}
print("root %s  tools %d  features %d" % (ROOT, len(TOOLS), WIDTH))


def curve_of(raw, depth=DEPTH):
    values = [float(c) for c in str(raw or "").split(",") if c.strip()] or [0.0]
    values = values[:depth]
    while len(values) < depth:
        values.append(values[-1])
    return values


def load_pairs(path):
    """Harness forks: one example per (state, candidate)."""
    xs, tools, gains, curves, rids, weights, teacher, winner = [], [], [], [], [], [], [], []
    state = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            x, candidates = row.get("x"), row.get("gains") or {}
            if not isinstance(x, list) or len(x) != WIDTH or not candidates:
                continue
            margin = float(row.get("margin") or 0.0)
            weight = (0.20 if margin <= 0.0 else (0.60 if margin <= 0.10 else 1.0))
            weight *= 0.35 if row.get("trivial") else 1.0
            added = 0
            for tool, gain in candidates.items():
                slot = SLOT.get(tool)
                if slot is None:
                    continue
                xs.append([float(v) for v in x])
                tools.append(slot)
                gains.append(float(gain))
                curves.append(curve_of((row.get("curves") or {}).get(tool)))
                rids.append(state)
                weights.append(weight)
                teacher.append(1.0 if tool == row.get("teacher") else 0.0)
                winner.append(1.0 if tool == row.get("tool") else 0.0)
                added += 1
            if added:
                state += 1
    return {"x": np.asarray(xs, dtype=np.float32), "tool": np.asarray(tools, dtype=np.int64),
            "gain": np.asarray(gains, dtype=np.float32), "curve": np.asarray(curves, dtype=np.float32),
            "rid": np.asarray(rids, dtype=np.int64), "weight": np.asarray(weights, dtype=np.float32),
            "teacher": np.asarray(teacher, dtype=np.float32), "winner": np.asarray(winner, dtype=np.float32),
            "states": state}


def load_live(paths):
    """Real transitions: the state, the tool that was actually issued, what the client showed next."""
    rows = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if not row.get("has_pair") or row.get("was_read") or not row.get("x"):
                    continue
                if len(row["x"]) != WIDTH or row.get("tool") not in SLOT:
                    continue
                rows.append(row)
    if not rows:
        return None
    return {"x": np.asarray([r["x"] for r in rows], dtype=np.float32),
            "tool": np.asarray([SLOT[r["tool"]] for r in rows], dtype=np.int64),
            "moved": np.asarray([1.0 if r.get("moved") else 0.0 for r in rows], dtype=np.float32),
            "progress": np.asarray([1.0 if r.get("progress") else 0.0 for r in rows],
                                   dtype=np.float32)}


def auc(score, label):
    label = np.asarray(label).astype(int)
    pos, neg = int(label.sum()), int(len(label) - label.sum())
    if pos == 0 or neg == 0:
        return None
    order = np.argsort(np.asarray(score, dtype=np.float64), kind="mergesort")
    ranks = np.empty(len(score), dtype=np.float64)
    values = np.asarray(score, dtype=np.float64)[order]
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[j + 1] == values[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return float((ranks[label == 1].sum() - pos * (pos + 1) / 2.0) / (pos * neg))


def ece(score, label, bins=10):
    score = np.clip(np.asarray(score, dtype=np.float64), 0.0, 1.0)
    label = np.asarray(label).astype(int)
    if len(label) == 0:
        return None
    total = 0.0
    for index in range(bins):
        lo, hi = index / bins, (index + 1) / bins
        mask = (score >= lo) & (score < hi if hi < 1.0 else score <= hi)
        if not mask.sum():
            continue
        total += float(mask.mean()) * abs(float(score[mask].mean()) - float(label[mask].mean()))
    return float(total)


def harness_metrics(corpus, pred_gain, pred_move):
    by_state = {}
    for index, rid in enumerate(corpus["rid"]):
        by_state.setdefault(int(rid), []).append(index)
    oracle = model = teacher = 0.0
    states = top1 = flat_model = 0
    for indices in by_state.values():
        true = corpus["gain"][indices]
        if float(true.max()) <= 0.0:
            continue
        states += 1
        oracle += float(true.max())
        model += float(true[int(np.argmax(pred_gain[indices]))])
        tindex = [i for i in indices if corpus["teacher"][i] > 0]
        if tindex:
            teacher += float(corpus["gain"][tindex[0]])
        win = [i for i in indices if corpus["winner"][i] > 0]
        if win and int(np.argmax(pred_gain[indices])) == indices.index(win[0]):
            top1 += 1
        if float(pred_gain[indices].max()) <= 0.0:
            flat_model += 1
    label = (corpus["gain"] > 0).astype(np.int64)
    return {"states": states,
            "capture_model": round(model / max(1e-9, oracle), 4),
            "capture_teacher": round(teacher / max(1e-9, oracle), 4),
            "top1_vs_fork_winner": round(top1 / max(1, states), 4),
            "move_auc": (round(auc(pred_move, label), 4) if auc(pred_move, label) is not None else None),
            "move_ece": (round(ece(pred_move, label), 4) if ece(pred_move, label) is not None else None),
            "flat_model_states": flat_model}


def live_metrics(live, prob, gain):
    moved, progress = live["moved"], live["progress"]
    order = np.argsort(-prob)[: max(1, int(0.2 * len(prob)))]
    areauc = auc(prob, moved)
    err = ece(prob, moved)
    return {"rows": int(len(moved)), "base_moved": round(float(moved.mean()), 4),
            "base_progress": round(float(progress.mean()), 4),
            "auc_moved": (round(areauc, 4) if areauc is not None else None),
            "ece_moved": (round(err, 4) if err is not None else None),
            "auc_progress": (round(auc(gain, progress), 4)
                             if auc(gain, progress) is not None else None),
            "top20pct_moved": round(float(moved[order].mean()), 4)}


train = load_pairs(os.path.join(ROOT, "train.jsonl"))
val = load_pairs(os.path.join(ROOT, "val.jsonl"))
live = load_live([os.path.join(ROOT, name) for name in ("live_val.jsonl", "live_test.jsonl")])
print("pairs: train %d/%d states, val %d/%d states, live %s"
      % (len(train["x"]), train["states"], len(val["x"]), val["states"],
         "none" if live is None else len(live["x"])))

device = "cuda" if torch.cuda.is_available() else "cpu"
mean = train["x"].mean(0)
raw_std = train["x"].std(0)
# a harness curriculum pins several features to a constant; dividing by that zero turned constants
# into 1e6-magnitude inputs and destroyed the first transfer measurement
std = np.where(raw_std < 1e-3, 1.0, raw_std)
n_features = 2 * WIDTH + len(TOOLS)


def tensors(corpus):
    xn = torch.from_numpy(((corpus["x"] - mean) / std).astype(np.float32)).to(device)
    ti = torch.from_numpy(corpus["tool"]).to(device)
    onehot = torch.zeros((len(xn), len(TOOLS)), device=device)
    onehot[torch.arange(len(xn), device=device), ti] = 1.0
    return xn, ti, onehot


txn, tti, tone = tensors(train)
vxn, vti, vone = tensors(val)
results = []
for hidden in ARMS:
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    gate = torch.nn.Parameter(torch.randn((len(TOOLS), WIDTH), device=device) * 0.05)
    net = torch.nn.Sequential(
        torch.nn.Linear(n_features, hidden), torch.nn.ReLU(),
        torch.nn.Linear(hidden, hidden), torch.nn.ReLU(),
        torch.nn.Linear(hidden, 2 + DEPTH)).to(device)
    params = int(sum(p.numel() for p in list(net.parameters()) + [gate]))
    opt = torch.optim.AdamW(list(net.parameters()) + [gate], lr=LR, weight_decay=1e-4)
    tg = torch.from_numpy(train["gain"]).to(device)
    tc = torch.from_numpy(train["curve"]).to(device)
    tm = (train["gain"] > 0).astype(np.float32)
    tm = torch.from_numpy(tm).to(device)
    tw = torch.from_numpy(train["weight"]).to(device)
    steps = max(1, (len(txn) + BATCH - 1) // BATCH)
    started = time.time()

    def pair(idx):
        # rebuilt per step: the tool gate is a trained parameter, so a frozen pair matrix would
        # train on a random projection
        return torch.cat([txn[idx], tone[idx], txn[idx] * gate[tti[idx]]], dim=1)

    for step in range(STEPS):
        net.train()
        idx = torch.randint(0, len(txn), (BATCH,), device=device)
        out = net(pair(idx))
        loss_move = torch.nn.functional.binary_cross_entropy_with_logits(
            out[:, 0], tm[idx], reduction="none")
        loss_gain = torch.nn.functional.smooth_l1_loss(out[:, 1], tg[idx], reduction="none")
        loss_curve = torch.nn.functional.mse_loss(out[:, 2:], tc[idx], reduction="mean")
        loss = (tw[idx] * (loss_move + loss_gain)).mean() + loss_curve
        opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(list(net.parameters()) + [gate], 5.0)
        opt.step()

    net.eval()
    with torch.no_grad():
        vall = torch.arange(len(vxn), device=device)
        vo = net(torch.cat([vxn[vall], vone[vall],
                           vxn[vall] * gate[vti[vall]]], dim=1))
        vp_gain, vp_move = vo[:, 1].cpu().numpy(), torch.sigmoid(vo[:, 0]).cpu().numpy()
    arm = {"hidden": hidden, "params": params, "seconds": round(time.time() - started, 1),
           "harness_val": harness_metrics(val, vp_gain, vp_move)}
    if live is not None:
        # score the real states through the same path, in chunks to keep memory bounded
        probs, gains = [], []
        with torch.no_grad():
            lx = torch.from_numpy(((live["x"] - mean) / std).astype(np.float32)).to(device)
            lt = torch.from_numpy(live["tool"]).to(device)
            for start in range(0, len(lx), 4096):
                lo, hi = start, min(start + 4096, len(lx))
                onehot = torch.zeros((hi - lo, len(TOOLS)), device=device)
                onehot[torch.arange(hi - lo, device=device), lt[lo:hi]] = 1.0
                out = net(torch.cat([lx[lo:hi], onehot, lx[lo:hi] * gate[lt[lo:hi]]], dim=1))
                probs.append(torch.sigmoid(out[:, 0]).cpu().numpy())
                gains.append(out[:, 1].cpu().numpy())
        arm["live_transfer"] = live_metrics(live, np.concatenate(probs), np.concatenate(gains))
    results.append(arm)
    print(json.dumps(arm))

summary = {"arms": results, "matched_compute": {"steps": STEPS, "batch": BATCH, "lr": LR,
                                                "seed": SEED, "data_order": "randint per step"},
           "teacher_capture": results[0]["harness_val"]["capture_teacher"],
           "question": "does harness capture or live transfer improve with 100x parameters?"}
with open("/kaggle/working/metrics_capacity.json", "w", encoding="utf-8") as handle:
    json.dump(summary, handle, indent=1)
print(json.dumps(summary, indent=1))
