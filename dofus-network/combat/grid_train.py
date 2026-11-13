"""Supervised trainer for the 30M 560-cell combat core.

This trains the grid encoder to rank the solver's legal plans on the synthetic
solver-distillation corpus (`combat.grid_synth`). It is not live combat: the
labels are the solver's own plan scores.

The label is a distribution over plans, not an index. Where the solver has one
best plan that is a one-hot; where it is indifferent over a set the target is
uniform on that set and the loss is the KL to it. See `grid_dataset._plans` for
why the corpus no longer drops tied boards -- they are 96% of it.

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
import logging
logger = logging.getLogger(__name__)

try:
    from combat import grid_model
except ImportError:  # Kaggle standalone: grid_model.py sits beside this script
    import grid_model

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "combat_grid_synth"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "models" / "combat_grid"


#: (array key, trailing width, name of the constant that sets it)
_WIDTHS = (
    ("cells", grid_model.CELL_FEATS, "CELL_FEATS"),
    ("fighters", grid_model.FIGHTER_FEATS, "FIGHTER_FEATS"),
    ("plans", grid_model.PLAN_FEATS, "PLAN_FEATS"),
)


def _load(path: Path, limit: int = 0) -> Dict[str, jax.Array]:
    """Load one split, refusing a tensor whose last axis the model cannot eat.

    The width check exists because the failure it prevents is invisible. A
    12-wide corpus fed to a 14-input ``cell_in`` does not raise here; it raises
    deep inside a jitted matmul, after the params are built and the first batch
    is resident, with a shape message that names neither the file nor the
    encoder. Worse, if the widths happened to agree by accident (say a corpus
    built for a 12-channel encoder and a model widened to 12 for some other
    reason) it would train happily on misaligned channels and simply score
    worse -- a silent regression with no traceback at all.

    So: fail on read, name the file, name the constant, and print the command
    that regenerates the corpus. ``combat.grid_synth`` is the only thing that
    writes these tensors, and it reads the width from ``grid_dataset._cells``,
    so re-running it is always the correct repair.
    """
    data = np.load(path)
    out = {}
    for key, width, const in _WIDTHS:
        if key not in data:
            raise SystemExit("%s has no %r array; regenerate the corpus:\n"
                             "  python -m combat.grid_synth --out %s"
                             % (path, key, path.parent))
        got = data[key].shape[-1]
        if int(got) != int(width):
            raise SystemExit(
                "%s: %s is %d-wide but grid_model.%s is %d.\n"
                "The corpus predates the encoder. Regenerate it:\n"
                "  python -m combat.grid_synth --out %s"
                % (path, key, got, const, width, path.parent))
    for key in ("cells", "cell_mask", "fighters", "fighter_mask",
                "plans", "plan_mask", "scores", "labels"):
        value = data[key]
        if limit and value.shape[0] > limit:
            value = value[:limit]
        out[key] = jnp.asarray(value)
    # A corpus written before the soft label existed carries only the hard one.
    # Treat it as a one-hot target rather than refusing the file: the arithmetic
    # is identical for a unique best plan, which is all such a corpus contains.
    if "targets" in data:
        targets = data["targets"]
        if limit and targets.shape[0] > limit:
            targets = targets[:limit]
        out["targets"] = jnp.asarray(targets, dtype=jnp.float32)
    else:
        hard = np.asarray(out["labels"])
        onehot = np.zeros((hard.shape[0], grid_model.MAX_PLANS), dtype=np.float32)
        onehot[np.arange(hard.shape[0]), hard.astype(np.int32)] = 1.0
        out["targets"] = jnp.asarray(onehot)
    return out


def _batch(data: Dict[str, jax.Array], indices: np.ndarray) -> Dict[str, jax.Array]:
    return {key: value[indices] for key, value in data.items()}


def _tree_zeros(params) -> Any:
    return jax.tree_util.tree_map(jnp.zeros_like, params)


def adam_init(params) -> Any:
    return {"m": _tree_zeros(params), "v": _tree_zeros(params), "t": 0}


def adam_update(params, grads, opt, lr: float = 3e-4) -> Any:
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
    # Cross-entropy against the solver's *soft* label. When the solver has one
    # best plan this is the ordinary -log p(label); when it is indifferent over a
    # set of k plans the target is uniform on that set and the loss is the KL
    # between the model's distribution and "any of these is fine". That is the
    # whole reason the corpus stopped rejecting ties: 96% of boards are tied, and
    # a hard label there would have to invent a preference.
    ce = -jnp.mean(jnp.sum(batch["targets"] * logp, axis=-1))
    best = jnp.sum(batch["targets"] * batch["scores"], axis=-1)
    value_target = jnp.tanh(best / 1000.0)
    vloss = jnp.mean(jnp.square(out["value"] - value_target))
    rows = jnp.arange(logp.shape[0])
    picked = out["scores"].argmax(axis=-1)
    top1 = jnp.mean((batch["targets"][rows, picked] > 0.0).astype(jnp.float32))
    return ce + 0.1 * vloss, {"ce": ce, "vloss": vloss, "top1": top1}


@jax.jit
def _eval_step(params, cells, cell_mask, fighters, fighter_mask,
               plans, plan_mask, targets) -> Any:
    """One evaluation batch, compiled.

    Eager `grid_model.forward` over 30.7 M parameters costs tens of seconds per
    batch on CPU: attention over 560 cells is dispatched one op at a time. The
    training step was jitted and this was not, so validation -- which runs every
    epoch and again at the end -- dominated a run that was assumed to be
    compute-bound on the gradients. Measured: a 16-step training run with 128
    rows per split did not finish in 15 minutes, almost all of it here.
    """
    out = grid_model.forward(params, cells, cell_mask, fighters, fighter_mask,
                             plans, plan_mask)
    logp = jax.nn.log_softmax(out["scores"], axis=-1)
    ce = -jnp.sum(jnp.sum(targets * logp, axis=-1))
    picked = out["scores"].argmax(-1)
    hit = jnp.sum(targets[jnp.arange(logp.shape[0]), picked] > 0.0)
    return ce, hit


def evaluate(params, data: Dict[str, jax.Array], batch_size: int) -> Dict[str, float]:
    if not data["labels"].shape[0]:
        return {"rows": 0}
    total = 0.0
    top1 = 0.0
    rows = int(data["labels"].shape[0])
    for start in range(0, rows, batch_size):
        batch = _batch(data, np.arange(start, min(rows, start + batch_size)))
        ce, hit = _eval_step(params, batch["cells"], batch["cell_mask"],
                             batch["fighters"], batch["fighter_mask"],
                             batch["plans"], batch["plan_mask"],
                             batch["targets"])
        total += float(ce)
        top1 += float(hit)
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
    parser.add_argument("--min-top1", type=float, default=None,
                        help="override the corpus's uniform-guess floor for the verification check")
    parser.add_argument("--allow-below-floor", action="store_true",
                        help="write the checkpoint even when top-1 does not beat the floor")
    args = parser.parse_args(argv)
    train = _load(args.data / "train.npz", args.limit)
    val = _load(args.data / "val.npz", args.limit)
    test = _load(args.data / "test.npz", args.limit)
    if not train["labels"].shape[0]:
        # The recorded corpus is pass-only, so this is the expected state of a
        # fresh checkout. Point at the generator that produces actionable rows
        # instead of at the empty directory.
        logger.debug("no training rows. Build the synthetic curriculum first:\n"
              "  python -m combat.grid_synth --curriculum 200 --out %s" % args.data)
        return 2
    params = grid_model.init_params(
        jax.random.PRNGKey(args.seed), hidden=args.hidden,
        cell_layers=args.cell_layers, fighter_layers=args.fighter_layers,
        plan_layers=args.plan_layers)
    logger.debug("params", grid_model.param_count(params), "train", train["labels"].shape[0],
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
        logger.debug(json.dumps(row, sort_keys=True))
    metrics = {"history": history, "config": {
        "hidden": args.hidden, "cell_layers": args.cell_layers,
        "fighter_layers": args.fighter_layers, "plan_layers": args.plan_layers,
        "epochs": args.epochs, "batch": args.batch, "lr": args.lr,
    }}
    if test["labels"].shape[0]:
        metrics["test"] = evaluate(params, test, args.batch)
        logger.debug("test", json.dumps(metrics["test"], sort_keys=True))

    # Verification before live boards: the corpus manifest records the
    # uniform-guess floor over its own winning sets. A ranker that cannot beat
    # "pick a winner at random" has not learned the board, and shipping it is
    # worse than shipping nothing -- it replaces the solver's ranking with noise.
    floor = None
    manifest_path = args.data / "manifest.json"
    if manifest_path.is_file():
        try:
            floor = float(json.loads(manifest_path.read_text(encoding="utf-8"))
                          .get("uniform_guess_floor") or 0.0)
        except (ValueError, OSError):
            floor = None
    evaluated = metrics.get("test") or (history[-1].get("val") if history else None)
    verified = True
    if floor is not None and evaluated and evaluated.get("rows"):
        threshold = args.min_top1 if args.min_top1 is not None else floor
        passed = float(evaluated["top1"]) > float(threshold)
        metrics["verification"] = {"top1": evaluated["top1"], "floor": floor,
                                  "threshold": threshold, "passed": passed}
        logger.debug("verification", json.dumps(metrics["verification"], sort_keys=True))
        verified = passed or args.allow_below_floor
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "model.pkl").open("wb") as handle:
        pickle.dump({"params": jax.tree_util.tree_map(np.asarray, params),
                     "config": metrics["config"], "metrics": metrics}, handle)
    (args.out / "metrics.json").write_text(
        json.dumps(metrics, indent=1, sort_keys=True), encoding="utf-8")
    logger.debug("wrote", args.out)
    if not verified:
        logger.debug("checkpoint written but verification FAILED: top-1 does not beat the "
              "uniform-guess floor; pass --allow-below-floor to accept it deliberately")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
