"""Rank solver plans with the 30M 560-cell grid model.

This is an opt-in read-only ranker. It loads the synthetic solver-distillation
artifact (``models/combat_grid/model.pkl``), builds the same cell/fighter/plan
tensors used in training, and returns the model's score for each legal plan.
It never casts, moves or ends a turn.

    python -m combat.grid_infer --check

The model loads on the installed embedded interpreter through the numpy backend
(``combat.grid_backend``) -- no jax wheel required -- so an installed copy can
serve the learned ranker instead of the solver-only path. ``rank_plans`` returns
``None`` when the checkpoint or its data is absent, and the caller treats that
as a refusal, never a crash.
"""
from __future__ import annotations

import argparse
import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from combat.grid_backend import XP, tree_map, HAS_JAX, BACKEND

DEFAULT_MODEL = Path(__file__).resolve().parent.parent / "models" / "combat_grid" / "model.pkl"
_CACHE: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]] = {}


def _deps() -> Optional[Dict[str, Any]]:
    """Load the model modules on first use, not at import time.

    ``grid_model`` now works on the numpy backend, so importing it never raises
    ImportError for a missing jax wheel. ``grid_dataset`` is numpy-only already.
    """
    if _CACHE and False:  # placeholder; cache keyed by path in load_model
        pass
    try:
        from combat import grid_dataset, grid_model
    except ImportError:  # Kaggle standalone
        try:
            import grid_dataset
            import grid_model
        except ImportError:
            return None
    return {"grid_dataset": grid_dataset, "grid_model": grid_model}


def load_model(path: Path = DEFAULT_MODEL
               ) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Load the checkpoint's params and config, backend-agnostic.

    On jax the leaves become jax arrays; on numpy they stay numpy arrays. The
    forward pass in ``grid_model.forward`` is written against ``XP`` so it runs
    identically on either.
    """
    key = str(path)
    if key in _CACHE:
        return _CACHE[key]
    if not path.is_file():
        return None
    deps = _deps()
    if deps is None:
        return None
    grid_model = deps["grid_model"]
    with path.open("rb") as handle:
        payload = pickle.load(handle)
    params = tree_map(XP.asarray, payload["params"])
    config = dict(payload.get("config") or {})
    # A checkpoint carries the input width it was trained with, and `forward` would
    # fail deep inside a dense layer if the encoder has since grown. Refusing here
    # turns that into the answer the caller already handles -- `rank_plans` returns
    # None, `live_policy._grid_choice` falls through to the solver -- instead of an
    # exception on the first fight turn after an upgrade.
    #
    # Measured 2026-11-11: the shipped checkpoint was trained on CELL_FEATS=12 and
    # the encoder now writes 14 (enemy coverage and reachability were added), so
    # this checkpoint is stale until `combat.grid_dataset` rebuilds the tensors and
    # `combat.grid_train` refits. That is a deliberate cost: the two channels are
    # the difference between a model that can see the map and one that can see the
    # fight on it.
    cell_in = params.get("cell_in") if isinstance(params, dict) else None
    # `cell_in` is a `(weight, bias)` pair, not a bare array -- `_dense_apply` takes
    # both. Reading `.shape` off the tuple returns None, which is how the first
    # version of this guard silently passed a stale checkpoint through.
    if isinstance(cell_in, (tuple, list)) and cell_in:
        cell_in = cell_in[0]
    width = getattr(cell_in, "shape", None)
    if width is not None and int(width[0]) != int(grid_model.CELL_FEATS):
        _CACHE[key] = None
        return None
    _CACHE[key] = (params, config)
    return params, config


def _padded(board, plans: Sequence[Any]):
    deps = _deps()
    if deps is None:
        return None
    grid_model = deps["grid_model"]
    grid_dataset = deps["grid_dataset"]
    me = board.me
    cells = grid_dataset._cells(board, me)
    fighters, fighter_mask = grid_dataset._fighters(board, me)
    # `plan_rows`, not `_plans`: `_plans` is the training featuriser, it shuffles the
    # candidates so a label cannot be read off row order, and it rejects a board whose
    # best plan is tied. Either one alone makes ranking wrong here -- a shuffle attaches
    # every score to a different plan than the one it was computed for. This call used
    # to be `_plans` unpacked as five values from a seven-tuple, so it raised
    # `ValueError` on every invocation and `live_policy._grid_choice` caught it and
    # returned None: the trained grid policy never ranked a single plan.
    rows, mask, _scores, _notes = grid_dataset.plan_rows(board, plans)
    if rows.shape[0] == 0:
        return None
    pad = grid_model.MAX_PLANS - rows.shape[0]
    if pad > 0:
        rows = np.vstack([rows, np.zeros((pad, grid_model.PLAN_FEATS), np.float32)])
        mask = np.concatenate([mask, np.zeros((pad,), bool)])
    return {
        "cells": XP.asarray(cells[None, ...]),
        "cell_mask": XP.ones((1, cells.shape[0]), dtype=bool),
        "fighters": XP.asarray(fighters[None, ...]),
        "fighter_mask": XP.asarray(fighter_mask[None, ...]),
        "plans": XP.asarray(rows[None, ...]),
        "plan_mask": XP.asarray(mask[None, ...]),
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
        "backend": BACKEND,
    }


def _check() -> int:
    """Report whether the shipped checkpoint can serve the current encoder.

    This used to require `data/combat_grid_synth/val.npz` to exist and never read
    it, so it reported on a corpus the encoder had already outgrown while saying
    nothing about the only thing that can break a fight turn: whether the saved
    `cell_in` width still matches `grid_model.CELL_FEATS`.
    """
    loaded = load_model()
    if loaded is None:
        print("grid model unavailable: %s backend could not load %s"
              % (BACKEND, DEFAULT_MODEL))
        print("  (a checkpoint trained on an older encoder is refused by design)")
        return 2
    deps = _deps()
    params, config = loaded
    width = int(np.asarray(params["cell_in"][0]).shape[0])
    print("model", DEFAULT_MODEL, "params", deps["grid_model"].param_count(params),
          "backend", BACKEND)
    print("encoder cell width", width, "live CELL_FEATS",
          deps["grid_model"].CELL_FEATS, "config", config)
    if width != int(deps["grid_model"].CELL_FEATS):
        print("MISMATCH: the ranker would be refused at load time")
        return 1
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
