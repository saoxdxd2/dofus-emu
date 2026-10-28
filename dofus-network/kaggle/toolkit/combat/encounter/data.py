"""Shared encounter dataset slicing and checkpoint parameter loading."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

import jax
import jax.numpy as jnp
import numpy as np

from combat.encounter.checkpoint import load_params as _load_params
from combat.encounter.train import SPEC_KEYS, TABLE_KEYS


def load_slice(path: Path, start: int, samples: int
               ) -> Tuple[Dict[str, jax.Array], Dict[str, jax.Array]]:
    data = np.load(path)
    total = int(data["hp"].shape[0])
    start = max(0, min(int(start), total))
    take = min(int(samples), total - start)
    tables = {key: jnp.asarray(data[key][start:start + take])
              for key in TABLE_KEYS}
    spec = {key: jnp.asarray(data[key][start:start + take])
            for key in SPEC_KEYS if key in data}
    return tables, spec


def load_params(path: Path):
    return _load_params(path)
