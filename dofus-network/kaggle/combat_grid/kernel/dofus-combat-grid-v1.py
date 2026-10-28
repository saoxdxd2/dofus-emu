"""Supervised trainer for the 30M 560-cell combat core.

This trains the grid encoder to rank the solver's legal plans on the synthetic
solver-distillation corpus (`combat.grid_synth`). It is not live combat: the
labels are the solver's own plan scores. The live gate is still one actionable
fight and a side-swapped win.

    python -m combat.grid_train --hidden 64 --cell-layers 1 \
        --fighter-layers 1 --plan-layers 1 --epochs 1 --batch 8
    python -m combat.grid_train --hidden 768 --epochs 4 --batch 8
"""

from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import jax
import jax.numpy as jnp
import numpy as np

# Kaggle mounts the dataset at a depth that varies (thx-06-combat-grid or
# datasets/<owner>/<slug>). Put its directory on sys.path before importing the
# standalone grid_model.py that ships beside train.npz.
import glob as _glob
import sys as _sys
for _train in sorted(_glob.glob("/kaggle/input/**/train.npz", recursive=True)):
    _sys.path.insert(0, str(Path(_train).parent))
    break

try:
    from combat import grid_model
except ImportError:  # Kaggle standalone: grid_model.py sits beside this script
    import grid_model

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "combat_grid_synth"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "models" / "combat_grid"


def _load(path: Path, limit: int = 0) -> Dict[str, jax.Array]:
    data = np.load(path)
    out = {}
    for key in ("cells", "cell_mask", "fighters", "fighter_mask",
                "plans", "plan_mask", "scores", "labels"):
        value = data[key]
        if limit and value.shape[0] > limit:
            value = value[:limit]
        out[key] = jnp.asarray(value)
    return out


def _batch(data: Dict[str, jax.Array], indices: np.ndarray) -> Dict[str, jax.Array]:
    return {key: value[indices] for key, value in data.items()}


def _tree_zeros(params):
    return jax.tree_util.tree_map(jnp.zeros_like, params)


def adam_init(params):
    return {"m": _tree_zeros(params), "v": _tree_zeros(params), "t": 0}


def adam_update(params, grads, opt, lr: float = 3e-4):
    b1, b2, eps = 0.9, 0.999, 1e-8
    t = opt["t"] + 1
    norm = jnp.sqrt(sum(jnp.sum(g * g) for g in jax.tree_util.tree_leaves(grads)))
    scale = jnp.minimum(1.0, 1.0 / (norm + 1e-6))
    grads = jax.tree_util.tree_map(lambda g: g * scale, grads)
    m = jax.tree_util.tree_map(lambda m, g: b1 * m + (1 - b1) * g,
                               opt["m"], grads)
    v = jax.tree_util.tree_map(lambda v, g: b2 * v + (1 - b2) * g * g,
                               opt["v"], grads)
    mh = jax.tree_util.tree_map(lambda x: x / (1 - b1 ** t), m)
    vh = jax.tree_util.tree_map(lambda x: x / (1 - b2 ** t), v)
    params = jax.tree_util.tree_map(
        lambda p, mh, vh: p - lr * mh / (jnp.sqrt(vh) + eps), params, mh, vh)
    return params, {"m": m, "v": v, "t": t}


def _loss(params, batch) -> Tuple[jax.Array, Dict[str, jax.Array]]:
    out = grid_model.forward(
        params, batch["cells"], batch["cell_mask"],
        batch["fighters"], batch["fighter_mask"],
        batch["plans"], batch["plan_mask"])
    logp = jax.nn.log_softmax(out["scores"], axis=-1)
    rows = jnp.arange(logp.shape[0])
    ce = -jnp.mean(logp[rows, batch["labels"]])
    best = jnp.take_along_axis(batch["scores"], batch["labels"][:, None], axis=1)[:, 0]
    value_target = jnp.tanh(best / 1000.0)
    vloss = jnp.mean(jnp.square(out["value"] - value_target))
    top1 = jnp.mean((out["scores"].argmax(axis=-1) == batch["labels"]).astype(jnp.float32))
    return ce + 0.1 * vloss, {"ce": ce, "vloss": vloss, "top1": top1}


def evaluate(params, data: Dict[str, jax.Array], batch_size: int) -> Dict[str, float]:
    if not data["labels"].shape[0]:
        return {"rows": 0}
    total = 0.0
    top1 = 0.0
    rows = int(data["labels"].shape[0])
    for start in range(0, rows, batch_size):
        batch = _batch(data, np.arange(start, min(rows, start + batch_size)))
        out = grid_model.forward(
            params, batch["cells"], batch["cell_mask"],
            batch["fighters"], batch["fighter_mask"],
            batch["plans"], batch["plan_mask"])
        logp = jax.nn.log_softmax(out["scores"], axis=-1)
        indices = jnp.arange(logp.shape[0])
        total += float(-jnp.sum(logp[indices, batch["labels"]]))
        top1 += float(jnp.sum(out["scores"].argmax(-1) == batch["labels"]))
    return {"rows": rows, "ce": total / rows, "top1": top1 / rows}


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--hidden", type=int, default=grid_model.DEFAULT_HIDDEN)
    parser.add_argument("--cell-layers", type=int, default=grid_model.DEFAULT_CELL_LAYERS)
    parser.add_argument("--fighter-layers", type=int, default=grid_model.DEFAULT_FIGHTER_LAYERS)
    parser.add_argument("--plan-layers", type=int, default=grid_model.DEFAULT_PLAN_LAYERS)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    train = _load(args.data / "train.npz", args.limit)
    val = _load(args.data / "val.npz", args.limit)
    test = _load(args.data / "test.npz", args.limit)
    if not train["labels"].shape[0]:
        print("no training rows; run combat.grid_synth first")
        return 2
    params = grid_model.init_params(
        jax.random.PRNGKey(args.seed), hidden=args.hidden,
        cell_layers=args.cell_layers, fighter_layers=args.fighter_layers,
        plan_layers=args.plan_layers)
    print("params", grid_model.param_count(params), "train", train["labels"].shape[0],
          "val", val["labels"].shape[0], "test", test["labels"].shape[0])
    opt = adam_init(params)
    grad = jax.jit(jax.value_and_grad(_loss, has_aux=True))
    history = []
    rng = np.random.default_rng(args.seed)
    indices = np.arange(int(train["labels"].shape[0]))
    for epoch in range(1, args.epochs + 1):
        rng.shuffle(indices)
        total = 0.0
        count = 0
        for start in range(0, len(indices), args.batch):
            batch = _batch(train, indices[start:start + args.batch])
            (loss, aux), grads = grad(params, batch)
            params, opt = adam_update(params, grads, opt, args.lr)
            total += float(loss)
            count += 1
        row = {"epoch": epoch, "loss": total / max(1, count)}
        if val["labels"].shape[0]:
            row["val"] = evaluate(params, val, args.batch)
        history.append(row)
        print(json.dumps(row, sort_keys=True))
    metrics = {"history": history, "config": {
        "hidden": args.hidden, "cell_layers": args.cell_layers,
        "fighter_layers": args.fighter_layers, "plan_layers": args.plan_layers,
        "epochs": args.epochs, "batch": args.batch, "lr": args.lr,
    }}
    if test["labels"].shape[0]:
        metrics["test"] = evaluate(params, test, args.batch)
        print("test", json.dumps(metrics["test"], sort_keys=True))
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "model.pkl").open("wb") as handle:
        pickle.dump({"params": jax.tree_util.tree_map(np.asarray, params),
                     "config": metrics["config"], "metrics": metrics}, handle)
    (args.out / "metrics.json").write_text(
        json.dumps(metrics, indent=1, sort_keys=True), encoding="utf-8")
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    import glob
    mounted = sorted(glob.glob("/kaggle/input/**/train.npz", recursive=True))
    if mounted:
        raise SystemExit(main([
            "--data", str(Path(mounted[0]).parent),
            "--out", "/kaggle/working/combat_grid",
            "--hidden", "768",
            "--cell-layers", "3",
            "--fighter-layers", "2",
            "--plan-layers", "1",
            "--epochs", "4",
            "--batch", "8",
        ]))
    raise SystemExit(main())
