"""TopK sparse autoencoder over the tool policy's pre-logit activations.

Phase 1A of the AI upgrade.  The shipped 21-way head has two behaviours that a
single logit margin cannot separate: ``nav.goto`` and ``quest.advance`` share a
large part of their state representation, and 35% of lost gain comes from that
head-vs-head confusion.  A sparse dictionary over the 192-wide trunk gives a
monosemantic direction for "target is off-map", which can be projected out of the
hidden vector before the output projection without retraining the policy.

Why TopK rather than an L1 ReLU SAE: an L1 penalty shrinks every active feature,
so the dictionary learns a biased, lower-magnitude code.  TopK keeps the k
largest activations exactly and makes the sparsity level an explicit knob.  The
auxiliary dead-latent loss (AuxK) reconstructs the residual with the columns that
did not fire, which keeps dictionary features from dying.

Training is offline only; the runtime artifact is a small JSON steering vector
plus the exported hidden graph.  No torch import reaches ``agent/`` or
``combat/``.

    python ai/train_tool_sae.py \
        --model models/tool_policy.onnx \
        --data data/tool_sft_v3 \
        --out models/tool_policy_sae.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.dedicated_policy import build, load_tools  # noqa: E402
from ai.onnx_hidden import (export_hidden, load_classifier,  # noqa: E402
                            run_hidden)

# Feature indices from agent.features.FEATURE_NAMES.  Reading them by name would
# pull the whole featuriser into this offline script; the names are pinned by
# agent/test_tool_registry_vs_head.py and the vector is the same one the model
# was trained on.
ON_TARGET = 23
HOPS_TO_TARGET = 24
OBS_STALE = 38


def _topk(values: Any, k: int) -> Any:
    import torch
    k = min(max(int(k), 1), int(values.shape[1]))
    picked, index = torch.topk(values, k=k, dim=1)
    out = torch.zeros_like(values)
    out.scatter_(1, index, picked)
    return out


class TopKSAE:
    """Torch module factory, kept out of module import time."""

    @staticmethod
    def build(hidden: int, dictionary: int, k: int, k_aux: int) -> Any:
        import torch

        class _SAE(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.b_pre = torch.nn.Parameter(torch.zeros(hidden))
                self.w_enc = torch.nn.Parameter(torch.empty(dictionary, hidden))
                self.w_dec = torch.nn.Parameter(torch.empty(hidden, dictionary))
                torch.nn.init.kaiming_uniform_(self.w_enc, a=math.sqrt(5))
                with torch.no_grad():
                    self.w_dec.copy_(self.w_enc.t())
                    self._normalise()
                self.k = int(k)
                self.k_aux = int(k_aux)

            def _normalise(self) -> None:
                norm = self.w_dec.norm(dim=0, keepdim=True).clamp_min(1e-8)
                self.w_dec.div_(norm)

            def encode(self, h: Any) -> Any:
                return _topk((h - self.b_pre) @ self.w_enc.t(), self.k)

            def forward(self, h: Any) -> Tuple[Any, Any, Any]:
                z = self.encode(h)
                recon = z @ self.w_dec.t() + self.b_pre
                residual = h - recon
                dead = z.abs().sum(dim=0) == 0
                aux = h.new_zeros(())
                if bool(dead.any()):
                    columns = self.w_dec[:, dead]
                    aux_pre = residual @ self.w_enc[dead].t()
                    aux_z = _topk(aux_pre, min(self.k_aux, int(dead.sum())))
                    aux = ((residual - aux_z @ columns.t()) ** 2).mean()
                loss = ((h - recon) ** 2).mean() + aux / max(self.k_aux, 1)
                return loss, recon, z

        return _SAE()


def train_sae(hidden: np.ndarray, *, dictionary: int, k: int, k_aux: int,
              epochs: int, batch: int, lr: float, seed: int,
              quiet: bool = False) -> Any:
    import torch

    torch.manual_seed(seed)
    torch.set_num_threads(max(1, min(8, torch.get_num_threads())))
    h = torch.from_numpy(np.asarray(hidden, dtype=np.float32))
    model = TopKSAE.build(h.shape[1], dictionary, k, k_aux)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    n = h.shape[0]
    for epoch in range(max(1, epochs)):
        order = torch.randperm(n)
        total = 0.0
        batches = 0
        for start in range(0, n, max(1, batch)):
            idx = order[start:start + max(1, batch)]
            loss, _recon, _z = model(h[idx])
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            with torch.no_grad():
                model._normalise()
            total += float(loss.detach())
            batches += 1
        if not quiet and (epoch + 1) % 5 == 0:
            print("  sae epoch %3d  loss %.6f" % (epoch + 1, total / max(batches, 1)))
    model.eval()
    return model


def latent_codes(model: Any, hidden: np.ndarray) -> np.ndarray:
    import torch

    with torch.no_grad():
        z = model.encode(torch.from_numpy(np.asarray(hidden, dtype=np.float32)))
    return z.numpy()


def _corr(left: np.ndarray, right: np.ndarray) -> float:
    left = np.asarray(left, dtype=np.float64)
    right = np.asarray(right, dtype=np.float64)
    left = left - left.mean()
    right = right - right.mean()
    denom = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(left @ right / denom) if denom > 1e-12 else 0.0


def _macro_f1(y: np.ndarray, pred: np.ndarray, classes: int) -> float:
    scores = []
    for c in range(classes):
        tp = int(((pred == c) & (y == c)).sum())
        fp = int(((pred == c) & (y != c)).sum())
        fn = int(((pred != c) & (y == c)).sum())
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall)
                      if precision + recall else 0.0)
    return float(np.mean(scores)) if scores else 0.0


def _optimal(y_gains: np.ndarray, pred: np.ndarray) -> float:
    best = y_gains.max(axis=1)
    picked = y_gains[np.arange(len(pred)), pred]
    return float(np.mean(picked >= best - 1e-9))


def _metrics(logits: np.ndarray, data: Dict[str, np.ndarray], tools: Sequence[str],
             fresh: np.ndarray) -> Dict[str, Any]:
    y = np.asarray(data["Y"], dtype=int)
    gains = np.asarray(data["soft"], dtype=np.float64)
    pred = np.asarray(logits).argmax(axis=1)
    nav = tools.index("nav.goto") if "nav.goto" in tools else -1
    quest = tools.index("quest.advance") if "quest.advance" in tools else -1
    confusion = 0
    if nav >= 0 and quest >= 0:
        confusion = int(((y == nav) & (pred == quest)).sum()
                        + ((y == quest) & (pred == nav)).sum())
    stale = ~fresh
    return {
        "rows": int(len(y)),
        "accuracy": round(float((pred == y).mean()), 4),
        "macro_f1": round(_macro_f1(y, pred, len(tools)), 4),
        "optimal": round(_optimal(gains, pred), 4),
        "fresh_rows": int(fresh.sum()),
        "fresh_optimal": round(_optimal(gains[fresh], pred[fresh]), 4)
        if fresh.any() else 0.0,
        "stale_rows": int(stale.sum()),
        "stale_optimal": round(_optimal(gains[stale], pred[stale]), 4)
        if stale.any() else 0.0,
        "nav_quest_confusion": confusion,
    }


def _base_logits(model_path: Path, features: np.ndarray) -> np.ndarray:
    from agent.model_cache import onnx_session

    session = onnx_session(str(model_path))
    name = session.get_inputs()[0].name
    out = [o.name for o in session.get_outputs() if o.name == "logits"]
    output = out[0] if out else session.get_outputs()[0].name
    return np.asarray(session.run([output], {name: features})[0], dtype=np.float32)


def _steered_logits(base: np.ndarray, hidden: np.ndarray, direction: np.ndarray,
                    alpha: float, weight: np.ndarray, scale: float) -> np.ndarray:
    d = np.asarray(direction, dtype=np.float32)
    d = d / max(float(np.linalg.norm(d)), 1e-8)
    proj = np.maximum(0.0, hidden @ d)
    steered = hidden - (alpha * proj)[:, None] * d[None, :]
    delta = ((steered - hidden) @ weight.T) * scale
    return base + delta.astype(np.float32)


def _apply_steering(base: np.ndarray, projection: np.ndarray,
                    effect: np.ndarray, alpha: float,
                    mask: np.ndarray) -> np.ndarray:
    gate = np.where(mask, np.maximum(projection, 0.0), 0.0).astype(np.float32)
    return base - (alpha * gate)[:, None] * effect[None, :]


def select_steering(train_h: np.ndarray, train_base: np.ndarray,
                    train: Dict[str, np.ndarray], val_h: np.ndarray,
                    val_base: np.ndarray, val: Dict[str, np.ndarray],
                    w_dec: np.ndarray, weight: np.ndarray, scale: float,
                    tools: Sequence[str], alphas: Sequence[float]) -> Dict[str, Any]:
    """Choose one SAE latent and strength on train, then score it once on val.

    The selection objective is the acceptance gate, not reconstruction: the
    steering must cut nav.goto <-> quest.advance confusion on *train* while
    leaving the fresh stratum untouched and not giving back more than 0.5pp of
    stale optimality or 1pp of accuracy. The val split is not consulted while
    choosing, so the val number is a held-out result rather than a tuned one.
    """
    directions = np.asarray(w_dec, dtype=np.float32).T.copy()
    directions /= np.maximum(np.linalg.norm(directions, axis=1, keepdims=True),
                             1e-8)
    train_fresh = train["X"][:, OBS_STALE] < 0.5
    val_fresh = val["X"][:, OBS_STALE] < 0.5
    train_mask = ~train_fresh
    val_mask = ~val_fresh
    train_metrics = _metrics(train_base, train, tools, train_fresh)
    val_metrics = _metrics(val_base, val, tools, val_fresh)
    projections = train_h @ directions.T
    effects = (weight @ directions.T) * scale

    best: Optional[Tuple[Tuple[int, float], int, float, Dict[str, Any]]] = None
    for latent in range(directions.shape[0]):
        projection = projections[:, latent]
        effect = effects[:, latent]
        for alpha in alphas:
            if alpha == 0.0:
                continue
            candidate = _apply_steering(train_base, projection, effect, alpha,
                                        train_mask)
            metrics = _metrics(candidate, train, tools, train_fresh)
            if (metrics["fresh_optimal"] < train_metrics["fresh_optimal"] - 1e-9
                    or metrics["accuracy"] < train_metrics["accuracy"] - 0.01
                    or metrics["stale_optimal"]
                    < train_metrics["stale_optimal"] - 0.005
                    or metrics["nav_quest_confusion"]
                    >= train_metrics["nav_quest_confusion"]):
                continue
            key = (metrics["nav_quest_confusion"], -metrics["accuracy"])
            if best is None or key < best[0]:
                best = (key, latent, float(alpha), metrics)

    if best is None:
        return {"latent": -1, "alpha": 0.0,
                "train": train_metrics, "val": val_metrics,
                "direction": [0.0] * directions.shape[1]}

    _, latent, alpha, chosen = best
    val_candidate = _apply_steering(val_base, val_h @ directions[latent],
                                    (weight @ directions[latent]) * scale,
                                    alpha, val_mask)
    return {"latent": int(latent), "alpha": float(alpha),
            "train": chosen,
            "val": _metrics(val_candidate, val, tools, val_fresh),
            "direction": [float(v) for v in directions[latent].tolist()]}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path,
                        default=REPO_ROOT / "models" / "tool_policy.onnx")
    parser.add_argument("--data", type=Path,
                        default=REPO_ROOT / "data" / "tool_sft_v3")
    parser.add_argument("--out", type=Path,
                        default=REPO_ROOT / "models" / "tool_policy_sae.json")
    parser.add_argument("--hidden-model", type=Path, default=None,
                        help="write the hidden graph here (default: beside --out)")
    parser.add_argument("--dictionary", type=int, default=1024)
    parser.add_argument("--k", type=int, default=32)
    parser.add_argument("--k-aux", type=int, default=64)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch", type=int, default=512)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    tools = load_tools()
    train_path = args.data / "train.jsonl"
    val_path = args.data / "val.jsonl"
    if not train_path.exists():
        raise SystemExit("missing %s" % train_path)
    train = build(train_path, tools)
    val = build(val_path, tools)
    hidden_model = args.hidden_model or args.out.with_name("tool_policy_hidden.onnx")
    hidden_meta = export_hidden(args.model, hidden_model)
    print("hidden graph: %s -> %s (%d wide)"
          % (hidden_meta["hidden_output"], hidden_model, hidden_meta["hidden_dim"]))

    train_h = run_hidden(hidden_model, train["X"], hidden_meta["hidden_output"])
    val_h = run_hidden(hidden_model, val["X"], hidden_meta["hidden_output"])
    model = train_sae(train_h, dictionary=args.dictionary, k=args.k,
                      k_aux=args.k_aux, epochs=args.epochs, batch=args.batch,
                      lr=args.lr, seed=args.seed, quiet=args.quiet)

    import torch
    with torch.no_grad():
        w_enc = model.w_enc.detach().cpu().numpy().astype(np.float32)
        w_dec = model.w_dec.detach().cpu().numpy().astype(np.float32)
        b_pre = model.b_pre.detach().cpu().numpy().astype(np.float32)
    codes = latent_codes(model, val_h)
    target_off = ((val["X"][:, HOPS_TO_TARGET] > 0)
                  & (val["X"][:, ON_TARGET] < 0.5)).astype(np.float64)
    dialog_ready = (val["X"][:, ON_TARGET] >= 0.5).astype(np.float64)
    corr_off = np.asarray([_corr(codes[:, i], target_off)
                           for i in range(codes.shape[1])])
    corr_dialog = np.asarray([_corr(codes[:, i], dialog_ready)
                              for i in range(codes.shape[1])])
    best_off = int(np.argmax(np.abs(corr_off))) if corr_off.size else 0
    best_dialog = int(np.argmax(np.abs(corr_dialog))) if corr_dialog.size else 0
    direction = w_dec[:, best_off].astype(np.float32)
    direction /= max(float(np.linalg.norm(direction)), 1e-8)

    weight, _bias, scale = load_classifier(args.model)
    train_base = _base_logits(args.model, train["X"])
    val_base = _base_logits(args.model, val["X"])
    selected = select_steering(
        train_h, train_base, train, val_h, val_base, val, w_dec, weight, scale,
        tools, (-4.0, -3.0, -2.5, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5,
                0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0))
    nav = tools.index("nav.goto")
    quest = tools.index("quest.advance")
    val_pred = val_base.argmax(axis=1)
    val_confusion = (((val["Y"] == nav) & (val_pred == quest))
                     | ((val["Y"] == quest) & (val_pred == nav)))
    off_map = ((val["X"][:, HOPS_TO_TARGET] > 0)
               & (val["X"][:, ON_TARGET] < 0.5))
    stale = val["X"][:, OBS_STALE] >= 0.5
    print("target_off_map latent %d corr %.3f; dialog latent %d corr %.3f"
          % (best_off, corr_off[best_off], best_dialog, corr_dialog[best_dialog]))
    print("selected latent %d alpha %+.2f on obs_stale; "
          "train confusion %d, val confusion %d"
          % (selected["latent"], selected["alpha"],
             selected["train"]["nav_quest_confusion"],
             selected["val"]["nav_quest_confusion"]))
    print("target_off_map covers %d/%d val confusion rows; obs_stale covers %d/%d"
          % (int((val_confusion & off_map).sum()), int(val_confusion.sum()),
             int((val_confusion & stale).sum()), int(val_confusion.sum())))

    npz = args.out.with_suffix(".npz")
    np.savez_compressed(npz, w_enc=w_enc, b_pre=b_pre, w_dec=w_dec,
                        codes_mean=codes.mean(axis=0).astype(np.float32),
                        corr_off=corr_off.astype(np.float32),
                        corr_dialog=corr_dialog.astype(np.float32))
    payload = {
        "kind": "tool-policy-sae",
        "model": str(args.model),
        "model_sha256": hidden_meta["source_sha256"],
        "data": str(args.data),
        "tools": list(tools),
        "features": int(val["X"].shape[1]),
        "hidden_model": hidden_model.name,
        "hidden_output": hidden_meta["hidden_output"],
        "hidden_dim": int(hidden_meta["hidden_dim"]),
        "dictionary": int(args.dictionary),
        "k": int(args.k),
        "k_aux": int(args.k_aux),
        "epochs": int(args.epochs),
        "seed": int(args.seed),
        "sae_npz": npz.name,
        "predicates": {
            "target_off_map": {
                "latent": best_off,
                "correlation": round(float(corr_off[best_off]), 4),
                "definition": "(hops_to_target > 0) and (on_target == 0)",
                "val_confusion_rows_covered": int((val_confusion & off_map).sum()),
                "note": "vacuous on the measured confusion: every val confusion row "
                        "has hops_to_target == 0, so the literal gate would never fire",
            },
            "dialog_ready_on_cell": {
                "latent": best_dialog,
                "correlation": round(float(corr_dialog[best_dialog]), 4),
                "definition": "on_target == 1",
            },
        },
        "steering": {
            "latent": int(selected["latent"]),
            "alpha": float(selected["alpha"]),
            "mask": {"feature": OBS_STALE, "name": "obs_stale", "threshold": 0.5,
                     "val_confusion_rows_covered": int((val_confusion & stale).sum())},
            "formula": "logits + (-alpha * relu(h @ d) * (W_out @ d) * logit_scale) "
                       "on rows where the mask feature is >= threshold",
            "d_off_map": [round(float(v), 8) for v in selected["direction"]],
            "w_out": [[round(float(v), 8) for v in row] for row in weight.tolist()],
            "logit_scale": float(scale),
        },
        "metrics": {"train": selected["train"], "val": selected["val"],
                    "val_base": _metrics(val_base, val, tools,
                                         val["X"][:, OBS_STALE] < 0.5)},
        "val_rows": int(len(val["Y"])),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print("wrote %s (%.1f KB) and %s" % (args.out, args.out.stat().st_size / 1024.0, npz))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
