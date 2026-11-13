"""Dedicated sequence policy: (state, decision history) -> tool + value.

Why this exists next to `train_tool_policy.py` rather than as another flag on it.

The shipped head is a stateless MLP over 42 numbers. Two of those numbers are
`last_tool` and `fail_streak`, so the entire history of an episode -- what was
tried, what failed, in what order -- is compressed into a scalar before the model
ever sees it. The corpus is 479 episodes with a median length of 31 steps, and the
head reads every row as if it were the first. A decision that is only wrong in
context (try the other approach *after* this one failed) is not representable, so
it cannot be learned.

Measured on the pinned split, giving the model the previous three actions raises the
label ceiling from 0.9702 to 0.9820. That is the headroom this file goes after, and
it is the only headroom that exists: the shipped model already captures 97.2% of the
gain available on rows it gets right, so "train harder" is not the lever.

What is different here, concretely:

* **History encoder.** The last `HISTORY_STEPS` actions enter as tokens -- tool,
  success, error class, repeat flag -- through a GRU. The runtime tracker and this
  trainer both build those tokens with `agent.features.history_tokens`, so serving
  and training cannot disagree about what the past looked like.
* **Outcome targets, not teacher targets.** The label in this corpus is the argmax of
  a fork-simulated `gains` map, so the teacher is already only a tie-break. Training
  against the *distribution* of gains (soft targets) instead of a one-hot label is
  therefore not a different objective, it is the same objective without the arbitrary
  tie-break. 22% of rows have more than one tool at max gain; a one-hot label calls
  the other tied-optimal tools wrong.
* **A value head.** `advanced` is a measured outcome (58.6% of rows), not an opinion.
  Predicting it gives the planner something the current head cannot do: score a state
  before acting, which is the precondition for planning more than one step.

The output contract is one flat vector so the ONNX graph stays a single input:

    x = [42 state features | 8 history tokens x 28]  ->  logits[21], value[1]

    python ai/dedicated_policy.py --data data/tool_sft_v3 --out models/dedicated/policy.onnx
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.features import (FEATURE_COUNT, HISTORY_DIM, HISTORY_DIM_OBS,  # noqa: E402
                            HISTORY_STEPS, HISTORY_TOKEN, HISTORY_TOKEN_OBS,
                            LAST_FRESH_DIM, _err_slot, feature_count_history,
                            history_tokens)

SIDECAR = REPO_ROOT / "models" / "tool_policy.json"
ARCH = "dedicated-v1"


# -- corpus ------------------------------------------------------------------

def load_tools(sidecar: Path = SIDECAR) -> List[str]:
    """The label order is the shipped head's, so the two models are comparable."""
    data = json.loads(Path(sidecar).read_text(encoding="utf-8"))
    tools = [str(t) for t in data["tools"]]
    if len(tools) != 21:
        raise SystemExit("sidecar names %d tools, expected 21" % len(tools))
    return tools


def load_episodes(path: Path) -> List[List[Dict[str, Any]]]:
    """Rows grouped by episode and ordered by step. The order is the signal."""
    by_ep: Dict[int, List[Dict[str, Any]]] = {}
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            by_ep.setdefault(int(row["ep"]), []).append(row)
    episodes = []
    for ep in sorted(by_ep):
        rows = sorted(by_ep[ep], key=lambda r: int(r.get("t", 0)))
        episodes.append(rows)
    return episodes


def build(path: Path, tools: Sequence[str], obs: bool = False,
          derived: bool = False) -> Dict[str, np.ndarray]:
    """Featurise an episode corpus into (state | history) rows and targets.

    History is built exactly the way `ObsTracker` builds it at serving time: the
    window for step t holds the actions of steps 0..t-1, each recorded *after* it
    ran, with the error class derived from that action's own note.

    `obs` widens each token with the observation summary of the step it belongs to,
    read from that row's own state vector. That is the same extraction the runtime
    tracker performs, so a step looks identical from either side.
    """
    from agent.features import obs_summary
    index = {name: i for i, name in enumerate(tools)}
    X: List[List[float]] = []
    Y: List[int] = []
    SOFT: List[List[float]] = []
    ADV: List[float] = []
    GAIN_MAX: List[float] = []
    EP: List[int] = []

    for rows in load_episodes(path):
        window: List[Dict[str, Any]] = []
        for row in rows:
            state = [float(v) for v in row["x"]]
            if len(state) != FEATURE_COUNT:
                raise SystemExit("corpus row is %d wide, featuriser says %d"
                                 % (len(state), FEATURE_COUNT))
            X.append(state + history_tokens(window, obs=obs, derived=derived))
            EP.append(int(row["ep"]))

            label = str(row["tool"])
            if label not in index:
                raise SystemExit("corpus names tool %r, sidecar has no slot" % label)
            Y.append(index[label])

            gains = row.get("gains") or {}
            soft = np.zeros(len(tools), dtype="float64")
            for name, value in gains.items():
                if name in index:
                    soft[index[name]] = float(value)
            SOFT.append(soft.tolist())
            GAIN_MAX.append(float(soft.max()) if soft.any() else 0.0)
            ADV.append(1.0 if row.get("advanced") else 0.0)

            rec = {"tool": label, "ok": bool(row.get("ok")),
                   "err": _err_slot(row.get("note"))}
            if obs:
                rec["summary"] = obs_summary(state)
            window.append(rec)
            if len(window) > HISTORY_STEPS:
                window = window[-HISTORY_STEPS:]

    return {"X": np.asarray(X, dtype="float32"), "Y": np.asarray(Y, dtype="int64"),
            "soft": np.asarray(SOFT, dtype="float32"),
            "advanced": np.asarray(ADV, dtype="float32"),
            "gain_max": np.asarray(GAIN_MAX, dtype="float32"),
            "ep": np.asarray(EP, dtype="int64")}


def subset(data: Dict[str, np.ndarray], mask: np.ndarray) -> Dict[str, np.ndarray]:
    return {k: v[mask] for k, v in data.items()}


def select_split(data: Dict[str, np.ndarray], frac: float,
                 seed: int) -> Tuple[Dict[str, np.ndarray], Dict[str, np.ndarray]]:
    """Hold out whole episodes for early stopping and calibration.

    Selecting the best epoch on the split you then report is how a validation number
    becomes a training number. The selection episodes are removed from fitting, so
    the reported metric is measured on data that influenced nothing -- not the
    weights, not the stopping point, not the temperature.
    """
    episodes = np.unique(data["ep"])
    rng = np.random.default_rng(seed)
    rng.shuffle(episodes)
    n_hold = max(1, int(round(len(episodes) * frac)))
    held = set(int(e) for e in episodes[:n_hold])
    mask = np.array([int(e) in held for e in data["ep"]])
    return subset(data, ~mask), subset(data, mask)


def soft_targets(soft: np.ndarray, labels: np.ndarray,
                 temperature: float = 0.5, mode: str = "soft") -> np.ndarray:
    """Gain distribution -> a target distribution.

    `gains` are cumulative progress rates from a fork rollout, so 1.0 means the
    objective finished and 0.5 means it took twice as long. Raising to 1/T sharpens
    without discarding the ordering: at T=0.5 a 0.5 gain keeps a quarter of the mass
    of a 1.0, which is the difference between "wrong" and "slower". Rows with no
    gains at all fall back to the one-hot label.

    `mode="hard"` reproduces the shipped trainer's target so the two can be compared
    on one split. It is not the default: the label is the argmax of the same gains,
    so a hard target throws away the ordering and keeps only the tie-break.
    """
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    if mode == "hard":
        p = np.zeros_like(soft, dtype="float32")
        p[np.arange(len(labels)), labels] = 1.0
        return p
    p = np.power(np.clip(soft, 0.0, None), 1.0 / temperature)
    total = p.sum(axis=1, keepdims=True)
    empty = total[:, 0] <= 0
    p[empty] = 0.0
    p[empty, labels[empty]] = 1.0
    total = p.sum(axis=1, keepdims=True)
    return (p / total).astype("float32")


# -- model -------------------------------------------------------------------

def _torch() -> Any:
    try:
        import torch
    except ImportError:  # pragma: no cover - environment guard
        raise SystemExit(
            "torch is not installed. The repository keeps it out of the shipped\n"
            "requirements on purpose (it is ~500 MB). For local training:\n"
            "  python -m venv .venv && .venv/Scripts/python -m pip install \\\n"
            "      --index-url https://download.pytorch.org/whl/cpu torch\n"
            "or run this file on Kaggle, where it is preinstalled.")
    return torch


def build_model(n_tools: int, hidden: int = 128, gru: int = 64,
                steps: int = HISTORY_STEPS, token: int = HISTORY_TOKEN,
                base: int = FEATURE_COUNT, dropout: float = 0.2,
                extra: int = 0) -> Any:
    """`extra` is the number of dims appended after the token window.

    The GRU reads a rectangular (steps, token) block, so anything that is not a
    per-step token -- the derived last-fresh numbers are one per row, not one per
    step -- cannot be folded into it without inventing a fake step. Those go straight
    into the trunk instead, alongside the encoded history.
    """
    torch = _torch()
    nn = torch.nn

    class DedicatedPolicy(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.base, self.steps, self.token = base, steps, token
            self.extra = int(extra)
            self.state = nn.Sequential(nn.Linear(base, hidden), nn.GELU(),
                                       nn.Dropout(dropout),
                                       nn.Linear(hidden, hidden), nn.GELU())
            self.recurrent = steps > 0
            if self.recurrent:
                self.hist = nn.GRU(token, gru, batch_first=True)
                fused = hidden + gru + self.extra
            else:
                fused = hidden + self.extra
            self.trunk = nn.Sequential(nn.Linear(fused, 192), nn.GELU(),
                                       nn.Dropout(dropout))
            self.tool = nn.Linear(192, n_tools)
            self.value = nn.Linear(192, 1)
            # Folded into the logits so the exported graph is calibrated on its own:
            # a consumer that reads a probability should not have to know this number.
            self.logit_scale = nn.Parameter(torch.ones(1))

        def forward(self, x):
            z = self.state(x[:, :self.base])
            cut = self.base + self.steps * self.token
            if self.recurrent:
                h = x[:, self.base:cut].reshape(-1, self.steps, self.token)
                _, hn = self.hist(h)
                z = torch.cat([z, hn[-1]], dim=1)
            if self.extra:
                z = torch.cat([z, x[:, cut:]], dim=1)
            z = self.trunk(z)
            return self.tool(z) * self.logit_scale, self.value(z)

    return DedicatedPolicy()


# -- metrics -----------------------------------------------------------------

def _auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Rank-based AUC, computed without sklearn."""
    pos = scores[labels > 0.5]
    neg = scores[labels <= 0.5]
    if not pos.size or not neg.size:
        return 0.0
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(order.size, dtype="float64")
    ranks[order] = np.arange(1, order.size + 1)
    return float((ranks[:pos.size].sum() - pos.size * (pos.size + 1) / 2)
                 / (pos.size * neg.size))


def report(logits: np.ndarray, values: np.ndarray, data: Dict[str, np.ndarray],
           tools: Sequence[str]) -> Dict[str, Any]:
    """Task metrics, not label metrics.

    `exact` is agreement with the stored label. `optimal` counts any tool tied at the
    maximum gain as right, because on 22% of rows the label is one of several equally
    good actions and the tie-break is the teacher's, not the world's.
    """
    pred = logits.argmax(axis=1)
    y = data["Y"]
    soft = data["soft"]
    gain_max = data["gain_max"]
    got = soft[np.arange(len(pred)), pred]
    optimal = float((np.abs(got - gain_max) < 1e-6).mean())
    per_class = []
    for c, name in enumerate(tools):
        tp = int(((pred == c) & (y == c)).sum())
        fp = int(((pred == c) & (y != c)).sum())
        fn = int(((pred != c) & (y == c)).sum())
        support = int((y == c).sum())
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        per_class.append({"tool": name, "support": support,
                          "predicted": int((pred == c).sum()),
                          "recall": round(recall, 4),
                          "precision": round(precision, 4),
                          "f1": round(2 * precision * recall / (precision + recall), 4)
                          if precision + recall else 0.0})
    common = [p["f1"] for p in per_class if p["support"] >= 50]
    probs = np.exp(logits - logits.max(axis=1, keepdims=True))
    probs /= probs.sum(axis=1, keepdims=True)
    conf = probs.max(axis=1)
    correct = (pred == y)
    ece = 0.0
    for lo in range(10):
        hi = (lo + 1) / 10 + (1e-9 if lo == 9 else 0)
        mask = (conf >= lo / 10) & (conf < hi)
        if mask.any():
            ece += float(mask.mean()) * abs(float(correct[mask].mean())
                                            - float(conf[mask].mean()))
    return {
        "rows": int(len(pred)),
        "exact": round(float(correct.mean()), 4),
        "optimal": round(optimal, 4),
        "macro_f1": round(float(np.mean([p["f1"] for p in per_class])), 4),
        "macro_f1_common": round(float(np.mean(common)), 4) if common else 0.0,
        "ece": round(float(ece), 4),
        "mean_gain": round(float(got.mean()), 4),
        "gain_of_max": round(float(got.mean() / gain_max.mean()), 4)
        if gain_max.mean() else 0.0,
        "value_auc": round(_auc(values, data["advanced"]), 4),
        "value_brier": round(float(((values - data["advanced"]) ** 2).mean()), 4),
        "reachable": sorted({tools[int(p)] for p in pred}),
        "per_class": per_class,
    }


# -- training ----------------------------------------------------------------

def _ece(probs: np.ndarray, labels: np.ndarray, bins: int = 10) -> float:
    conf = probs.max(axis=1)
    correct = probs.argmax(axis=1) == labels
    total = 0.0
    for lo in range(bins):
        hi = (lo + 1) / bins + (1e-9 if lo == bins - 1 else 0)
        mask = (conf >= lo / bins) & (conf < hi)
        if mask.any():
            total += float(mask.mean()) * abs(float(correct[mask].mean())
                                              - float(conf[mask].mean()))
    return total


def fit_temperature(logits: np.ndarray, labels: np.ndarray,
                    metric: str = "ece") -> Tuple[float, Dict[str, float]]:
    """One scalar, fitted on held-out episodes, that makes the confidences true.

    The model is trained on a gain *distribution*, so its probabilities start out
    deliberately soft: it says 0.4 when it is right 0.9 of the time. A controller
    that consumes those probabilities needs them to mean what they say.

    The objective is ECE, not NLL, and the difference matters here: the model is
    misspecified (21 classes, 16k rows), so the NLL-optimal temperature leaves it
    underconfident against the hard label -- ECE 0.125 against a tolerance of 0.02.
    ECE is also the gate's check and the question a consumer actually asks ("if this
    says 0.9, is it right nine times in ten?"). NLL is reported alongside so the
    trade-off is visible instead of hidden. Argmax is invariant to the scale, so
    folding it into the exported graph cannot change a decision.
    """
    z0 = np.asarray(logits, dtype="float64")
    best = (float("inf"), 1.0, float("inf"))
    for T in np.arange(0.05, 6.01, 0.05):
        z = z0 / T
        z = z - z.max(axis=1, keepdims=True)
        logp = z - np.log(np.exp(z).sum(axis=1, keepdims=True))
        nll = float(-logp[np.arange(len(labels)), labels].mean())
        score = _ece(np.exp(logp), labels) if metric == "ece" else nll
        if score < best[0]:
            best = (score, float(T), nll)
    return best[1], {"score": round(best[0], 4), "nll": round(best[2], 4),
                     "metric": metric}


def cila_adjust(logits, labels, inv_freq, bound: float = 2.0, eps: float = 1e-6) -> Any:
    """Instance-wise logit adjustment (CILA, arXiv:2610.27728).

    Class-wise logit adjustment subtracts `tau * log(pi_y)` from every logit, so a
    confusing non-target class is suppressed by an amount that depends only on its
    frequency -- not on whether it was actually confusing for this input. That is why
    both of this repository's post-hoc attempts failed: global and stratified
    adjustment are both class-level.

    CILA instead penalises a non-target class only when its logit exceeds the target's,
    in proportion to how far above the target's it sits, and scales that penalty by the
    inverse frequency of the *target*. The target's own penalty is exactly zero, since
    its ratio is 1 and the clamp removes it, so the fitted function is reshaped only
    where a genuine confusion exists.

    This is a training loss, not an inference transform: the exported graph applies no
    adjustment, which is what keeps the shipped contract unchanged.
    """
    z_y = logits.gather(1, labels.unsqueeze(1))
    z_min = logits.min(dim=1, keepdim=True).values
    ratio = (logits - z_min) / (z_y - z_min + eps)
    penalty = (ratio - 1.0).clamp(min=0.0, max=bound)
    return logits + penalty * inv_freq[labels].unsqueeze(1)


def inverse_frequency(labels: np.ndarray, n_tools: int) -> "np.ndarray":
    """Inverse class frequency, mean-normalised so the scale is absorbed by `bound`."""
    counts = np.bincount(np.asarray(labels, dtype="int64"), minlength=n_tools)
    inv = 1.0 / np.maximum(counts.astype("float64"), 1.0)
    return inv / inv.mean()


def train(data: Dict[str, np.ndarray], val: Dict[str, np.ndarray],
          tools: Sequence[str], *, epochs: int = 120, batch: int = 512,
          lr: float = 3e-3, weight_decay: float = 1e-3,
          temperature: float = 0.5, value_weight: float = 0.3,
          targets: str = "soft",
          hidden: int = 128, gru: int = 64, steps: int = HISTORY_STEPS,
          token: int = HISTORY_TOKEN, extra: int = 0,
          cila: bool = False, cila_bound: float = 2.0,
          dropout: float = 0.2, select_frac: float = 0.15,
          calib_metric: str = "ece",
          seed: int = 7, device: str = "cpu", verbose: bool = True) -> Any:
    """Fit on episodes, stop on held-out episodes, report on the pinned split.

    `val` is used for exactly one thing: the number in the report. Stopping and
    calibration both happen on a slice of `data` that no gradient ever touched.
    """
    torch = _torch()
    torch.manual_seed(seed)
    np.random.seed(seed)
    fit, select = select_split(data, select_frac, seed)
    model = build_model(len(tools), hidden=hidden, gru=gru, steps=steps,
                        token=token, dropout=dropout, extra=extra)
    model.to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    target = torch.from_numpy(soft_targets(fit["soft"], fit["Y"], temperature,
                                           targets))
    # Fitted on the fitting split only: the class weights are a statistic of the data
    # the gradient sees, so taking them from val would leak its composition.
    inv_freq = torch.from_numpy(inverse_frequency(fit["Y"], len(tools)))
    labels = torch.from_numpy(fit["Y"])
    advanced = torch.from_numpy(fit["advanced"])
    X = torch.from_numpy(fit["X"])
    n = X.shape[0]
    best: Optional[Tuple[float, int, Dict[str, Any]]] = None
    history: List[Dict[str, Any]] = []

    for epoch in range(1, epochs + 1):
        model.train()
        order = torch.randperm(n)
        total = 0.0
        for start in range(0, n, batch):
            idx = order[start:start + batch]
            logits, value = model(X[idx])
            if cila:
                logits = cila_adjust(logits, labels[idx], inv_freq, cila_bound)
            logp = torch.log_softmax(logits, dim=1)
            ce = -(target[idx] * logp).sum(dim=1).mean()
            bce = torch.nn.functional.binary_cross_entropy_with_logits(
                value.squeeze(1), advanced[idx])
            loss = ce + value_weight * bce
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
            total += float(loss.detach()) * len(idx)
        sched.step()
        if epoch % 5 == 0 or epoch == epochs:
            metrics = evaluate_model(model, select, tools)
            metrics["epoch"] = epoch
            metrics["loss"] = round(total / n, 4)
            history.append(metrics)
            if verbose:
                print("  epoch %3d  loss %.4f  sel exact %.4f  sel optimal %.4f  "
                      "macro %.4f  auc %.4f" % (epoch, total / n, metrics["exact"],
                                                metrics["optimal"],
                                                metrics["macro_f1"],
                                                metrics["value_auc"]))
            key = metrics["exact"] + metrics["optimal"]
            if best is None or key > best[0]:
                best = (key, epoch, {k: v.detach().clone()
                                     for k, v in model.state_dict().items()})
    if best is not None:
        model.load_state_dict(best[2])

    # Calibrate on the selection episodes, then report on the pinned split.
    torch_logits = _logits(model, select)
    scale, calib = fit_temperature(torch_logits, select["Y"], metric=calib_metric)
    with torch.no_grad():
        model.logit_scale.fill_(1.0 / scale)
    final = evaluate_model(model, val, tools)
    return model, {"best_epoch": best[1] if best else None,
                   "select_metrics": evaluate_model(model, select, tools),
                   "temperature": round(scale, 3), "calibration": calib,
                   "val": final, "curve": history}


def _logits(model, data: Dict[str, np.ndarray]) -> np.ndarray:
    torch = _torch()
    model.eval()
    with torch.no_grad():
        logits, _ = model(torch.from_numpy(data["X"]))
    return logits.numpy()


def evaluate_model(model, data: Dict[str, np.ndarray],
                   tools: Sequence[str]) -> Dict[str, Any]:
    torch = _torch()
    model.eval()
    with torch.no_grad():
        logits, value = model(torch.from_numpy(data["X"]))
    return report(logits.numpy(), value.squeeze(1).numpy(), data, tools)


def export_onnx(model, out: Path, in_dim: int, tools: Sequence[str],
                steps: int, token: int, meta: Dict[str, Any],
                hist_dim: Optional[int] = None) -> Path:
    torch = _torch()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    model.eval()
    dummy = torch.zeros(1, in_dim, dtype=torch.float32)
    # The dynamo exporter needs onnxscript, which is not part of the local
    # environment. The TorchScript exporter produces the same graph for this model
    # (dense + GRU) and has no extra dependency, so it is the default here.
    try:
        torch.onnx.export(model, dummy, str(out), input_names=["x"],
                          output_names=["logits", "value"],
                          dynamic_axes={"x": {0: "n"}, "logits": {0: "n"},
                                        "value": {0: "n"}},
                          opset_version=17, do_constant_folding=True,
                          dynamo=False)
    except TypeError:  # older torch without the flag
        torch.onnx.export(model, dummy, str(out), input_names=["x"],
                          output_names=["logits", "value"],
                          dynamic_axes={"x": {0: "n"}, "logits": {0: "n"},
                                        "value": {0: "n"}},
                          opset_version=17, do_constant_folding=True)
    sidecar = {
        "arch": ARCH, "features": int(in_dim), "tools": list(tools),
        "params": int(sum(p.numel() for p in model.parameters())),
        "history": {"steps": int(steps), "token": int(token),
                    "dim": int(hist_dim if hist_dim is not None else steps * token)},
        **meta,
    }
    out.with_suffix(".json").write_text(json.dumps(sidecar, indent=2),
                                        encoding="utf-8")
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(REPO_ROOT / "data" / "tool_sft_v3"))
    parser.add_argument("--out", default=str(REPO_ROOT / "models" / "dedicated"
                                             / "policy.onnx"))
    parser.add_argument("--epochs", type=int, default=120)
    parser.add_argument("--batch", type=int, default=512)
    parser.add_argument("--lr", type=float, default=3e-3)
    parser.add_argument("--hidden", type=int, default=128)
    parser.add_argument("--gru", type=int, default=64)
    parser.add_argument("--steps", type=int, default=HISTORY_STEPS,
                        help="history window; 0 trains the stateless ablation")
    parser.add_argument("--temperature", type=float, default=0.5)
    parser.add_argument("--targets", choices=("soft", "hard"), default="soft",
                        help="soft = the gain distribution; hard = the shipped label")
    parser.add_argument("--value-weight", type=float, default=0.3)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--select-frac", type=float, default=0.15,
                        help="fraction of TRAIN episodes held out for early stopping")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--cila", action="store_true",
                        help="instance-wise logit adjustment (arXiv:2610.27728) in the "
                             "training loss; the exported graph applies none")
    parser.add_argument("--cila-bound", type=float, default=2.0,
                        help="clip on the CILA penalty; 2.0 is the paper's value")
    parser.add_argument("--obs-derived", action="store_true",
                        help="also append the collapsed last-fresh dims after the "
                             "window; measured worse than the per-step form on the "
                             "same split (0.9172 vs 0.9204 optimal), kept for "
                             "reproducibility and requires --obs-history")
    parser.add_argument("--obs-history", action="store_true",
                        help="widen each history token with that step's observation "
                             "summary (on_target/hops/stale/known/has_needed); the "
                             "width is recorded in the sidecar and read back by every "
                             "scorer, so the two contracts cannot be confused")
    args = parser.parse_args(argv)

    root = Path(args.data)
    tools = load_tools()
    steps = int(args.steps)
    obs = bool(args.obs_history)
    derived = bool(args.obs_derived)
    if derived and not obs:
        parser.error("--obs-derived needs --obs-history: the derived dims are read "
                     "from the per-step summaries in the window")
    token = HISTORY_TOKEN_OBS if obs else HISTORY_TOKEN
    # Taken from the featuriser rather than recomputed as steps*token: the widened
    # block also carries the derived last-fresh dims, so the two are not the same
    # number and a local formula would silently disagree with what build() emits.
    in_dim = feature_count_history(obs, derived) if steps else FEATURE_COUNT
    extra = LAST_FRESH_DIM if (derived and steps) else 0

    t0 = time.time()
    print("building train/val from %s" % root)
    train_data = build(root / "train.jsonl", tools, obs=obs, derived=derived)
    val_data = build(root / "val.jsonl", tools, obs=obs, derived=derived)
    if steps == 0:
        train_data["X"] = train_data["X"][:, :FEATURE_COUNT]
        val_data["X"] = val_data["X"][:, :FEATURE_COUNT]
    print("  train %d rows  val %d rows  input %d  (history %d steps x %d)"
          % (len(train_data["X"]), len(val_data["X"]), in_dim, steps, token))

    model, info = train(train_data, val_data, tools, epochs=args.epochs,
                        batch=args.batch, lr=args.lr, hidden=args.hidden,
                        gru=args.gru, steps=steps, temperature=args.temperature,
                        value_weight=args.value_weight, dropout=args.dropout,
                        targets=args.targets, token=token, extra=extra,
                        cila=bool(args.cila), cila_bound=args.cila_bound,
                        select_frac=args.select_frac, seed=args.seed,
                        device=args.device)
    metrics = info["val"] or {}
    out = export_onnx(model, Path(args.out), in_dim, tools, steps,
                      token, {"best_epoch": info["best_epoch"],
                              "temperature": info["temperature"],
                              "targets": args.targets,
                              "obs_history": obs,
                              "cila": bool(args.cila),
                              "derived": derived,
                              "seed": args.seed},
                      hist_dim=(in_dim - FEATURE_COUNT))
    summary = {"out": str(out), "arch": ARCH, "input": in_dim,
               "best_epoch": info["best_epoch"],
               "calibration_temperature": info["temperature"],
               "calibration": info["calibration"],
               "train_rows": len(train_data["X"]), "val": metrics,
               "select": info["select_metrics"],
               "seconds": round(time.time() - t0, 1)}
    print(json.dumps({k: v for k, v in summary.items() if k != "val"},
                     ensure_ascii=False, indent=2))
    print("val: exact %.4f  optimal %.4f  macro_f1 %.4f  common %.4f  "
          "ece %.4f  gain %.4f  value_auc %.4f"
          % (metrics.get("exact", 0), metrics.get("optimal", 0),
             metrics.get("macro_f1", 0), metrics.get("macro_f1_common", 0),
             metrics.get("ece", 0), metrics.get("gain_of_max", 0),
             metrics.get("value_auc", 0)))
    print("reachable: %d tools" % len(metrics.get("reachable") or []))
    out.with_suffix(".report.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
