"""Rank solver plans with the 30M 560-cell grid model.

This is an opt-in read-only ranker. It loads the synthetic solver-distillation
artifact (`models/combat_grid/model.pkl`), builds the same cell/fighter/plan
tensors used in training, and returns the model's score for each legal plan.
It never casts, moves or ends a turn.

    python -m combat.grid_infer --check
"""

from __future__ import annotations

import argparse
import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

DEFAULT_MODEL = Path(__file__).resolve().parent.parent / "models" / "combat_grid" / "model.pkl"
_CACHE: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]] = {}
_LAZY: Dict[str, Any] = {}


def _deps() -> Optional[Dict[str, Any]]:
    """Load jax and the model modules on first use, not at import time.

    The interpreter that ships inside setup.exe is the embedded one and it has no jax
    wheel, while the development interpreter does. A module-level ``import jax`` therefore
    made ``from combat.grid_infer import rank_plans`` raise ImportError on an installed
    copy, which took down the decision path instead of returning the ``None`` the executor
    already treats as a refusal -- and it was invisible to every check run from the repo.
    """
    if _LAZY:
        return _LAZY
    try:
        import jax
        import jax.numpy as jnp
    except ImportError:
        return None
    try:
        from combat import grid_dataset, grid_model
    except ImportError:  # Kaggle standalone
        try:
            import grid_dataset
            import grid_model
        except ImportError:
            return None
    _LAZY.update(jax=jax, jnp=jnp, grid_dataset=grid_dataset, grid_model=grid_model)
    return _LAZY


def load_model(path: Path = DEFAULT_MODEL) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    key = str(path)
    if key in _CACHE:
        return _CACHE[key]
    if not path.is_file():
        return None
    deps = _deps()
    if deps is None:
        return None
    with path.open("rb") as handle:
        payload = pickle.load(handle)
    params = deps["jax"].tree_util.tree_map(deps["jnp"].asarray, payload["params"])
    config = dict(payload.get("config") or {})
    _CACHE[key] = (params, config)
    return params, config


def _padded(board, plans: Sequence[Any]):
    deps = _deps()
    if deps is None:
        return None
    jnp = deps["jnp"]
    grid_dataset = deps["grid_dataset"]
    grid_model = deps["grid_model"]
    me = board.me
    cells = grid_dataset._cells(board, me)
    fighters, fighter_mask = grid_dataset._fighters(board, me)
    rows, mask, scores, label, notes = grid_dataset._plans(board, plans=plans)
    if rows.shape[0] == 0:
        return None
    pad = grid_model.MAX_PLANS - rows.shape[0]
    if pad > 0:
        rows = np.vstack([rows, np.zeros((pad, grid_model.PLAN_FEATS), np.float32)])
        mask = np.concatenate([mask, np.zeros((pad,), bool)])
    return {
        "cells": jnp.asarray(cells[None, ...]),
        "cell_mask": jnp.ones((1, cells.shape[0]), dtype=bool),
        "fighters": jnp.asarray(fighters[None, ...]),
        "fighter_mask": jnp.asarray(fighter_mask[None, ...]),
        "plans": jnp.asarray(rows[None, ...]),
        "plan_mask": jnp.asarray(mask[None, ...]),
    }


def rank_plans(board, plans: Sequence[Any],
               model_path: Path = DEFAULT_MODEL
               ) -> Optional[Tuple[List[Tuple[float, Any]], Dict[str, Any]]]:
    deps = _deps()
    if deps is None:
        return None
    grid_model = deps["grid_model"]
    loaded = load_model(model_path)
    if loaded is None:
        return None
    params, config = loaded
    batch = _padded(board, plans)
    if batch is None:
        return None
    out = grid_model.forward(
        params, batch["cells"], batch["cell_mask"], batch["fighters"],
        batch["fighter_mask"], batch["plans"], batch["plan_mask"])
    scores = np.asarray(out["scores"])[0]
    ranked = sorted(zip((float(score) for score in scores[:len(plans)]), plans),
                    key=lambda item: item[0], reverse=True)
    return ranked, {
        "checkpoint": str(model_path),
        "params": grid_model.param_count(params),
        "kind": "solver_distillation",
        "live_calibrated": False,
        "config": config,
    }


def _check() -> int:
    data = Path(__file__).resolve().parent.parent / "data" / "combat_grid_synth" / "val.npz"
    if not data.is_file() or not DEFAULT_MODEL.is_file():
        print("need data/combat_grid_synth/val.npz and models/combat_grid/model.pkl")
        return 2
    loaded = load_model()
    if loaded is None:
        print("grid model unavailable: needs jax in the runtime that loads this")
        return 2
    deps = _deps()
    print("model", DEFAULT_MODEL, "params", deps["grid_model"].param_count(loaded[0]),
          "config", loaded[1])
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        return _check()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
