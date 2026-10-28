"""Load encounter actor params and their saved config with one default contract."""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any, Dict, Tuple

import jax
import jax.numpy as jnp

from combat.encounter.env import Config


def config_from_payload(payload: Dict[str, Any]) -> Config:
    saved = payload.get("config") or {}
    return Config(
        allies=int(saved.get("allies", 1)),
        enemies=int(saved.get("enemies", 4)),
        spells=int(saved.get("spells", 4)),
        max_rounds=int(saved.get("max_rounds", 24)),
        max_dist=int(saved.get("max_dist", 12)),
        casts_per_turn=int(saved.get("casts_per_turn", 4)),
    )


def load_params_config(path: Path) -> Tuple[Dict[str, Any], Config]:
    payload = pickle.loads(Path(path).read_bytes())
    return (jax.tree_util.tree_map(jnp.asarray, payload["params"]),
            config_from_payload(payload))


def load_params(path: Path) -> Dict[str, Any]:
    return load_params_config(path)[0]
