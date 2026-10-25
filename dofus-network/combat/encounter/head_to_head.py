"""Direct policy-vs-policy matches for encounter checkpoints.

The benchmark so far scores every policy against the scripted heuristic. That
can hide a policy that is stronger in direct play. This module puts two learned
checkpoints on opposite teams and measures the actual match, with both side
assignments and disjoint rows.

Teams are asymmetric in the encounter env (one player vs up to four monsters),
so every matchup is run twice with the checkpoints swapped. The model is
team-relative, which makes both sides playable by the same architecture.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import jax
import jax.numpy as jnp
import numpy as np

from combat.encounter import model
from combat.encounter.env import (Config, action_masks, initial_state, observe,
                                  sample_actions, step)
from combat.encounter.train import SPEC_KEYS, TABLE_KEYS

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA = REPO_ROOT / "kaggle" / "tpu_selfplay" / "dataset" / "encounters.npz"


def load_slice(path: Path, start: int, samples: int):
    data = np.load(path)
    n = int(data["hp"].shape[0])
    start = max(0, min(start, n))
    take = min(samples, n - start)
    tables = {key: jnp.asarray(data[key][start:start + take])
              for key in TABLE_KEYS}
    spec = {key: jnp.asarray(data[key][start:start + take])
            for key in SPEC_KEYS if key in data}
    return tables, spec


def load_params(path: Path):
    import pickle

    payload = pickle.loads(Path(path).read_bytes())
    return jax.tree_util.tree_map(jnp.asarray, payload["params"])


def make_rollout(cfg: Config, length: int):
    def rollout(params_a, params_b, state0, tables, key):
        def body(carry, _):
            state, key = carry
            k1, k2 = jax.random.split(key)
            obs, glob, attn = observe(state, tables, cfg)
            masks = action_masks(state, tables, cfg)
            logits_a = model.forward(params_a, obs, glob, attn)
            logits_b = model.forward(params_b, obs, glob, attn)
            acts_a = sample_actions(k1, logits_a, masks, cfg)
            acts_b = sample_actions(k2, logits_b, masks, cfg)
            use_b = state["acting_team"] == 1
            acts = {name: jnp.where(use_b[:, None], acts_b[name], acts_a[name])
                    for name in ("move", "spell", "target")}
            state = step(state, acts, tables, cfg)
            return (state, k2), None
        (state, _), _ = jax.lax.scan(body, (state0, key), None, length=length)
        return state
    return rollout


def slice_masks(spec: Dict[str, Any], cfg: Config) -> Dict[str, np.ndarray]:
    alive = np.asarray(spec["alive"])
    group = (np.asarray(spec["group"]) if "group" in spec
             else alive[:, cfg.allies:].sum(axis=1))
    boss = (np.any(np.asarray(spec["boss"]), axis=1) if "boss" in spec
            else np.zeros(alive.shape[0], bool))
    first = np.asarray(spec["first_team"]) == 0
    level = (np.asarray(spec["level"])[:, 0] if "level" in spec
             else np.ones(alive.shape[0]))
    masks = {
        "all": np.ones(alive.shape[0], bool),
        "boss": boss, "non_boss": ~boss,
        "first": first, "second": ~first,
    }
    for size in range(1, cfg.enemies + 1):
        masks[f"group_{size}"] = group == size
    for low, high, name in ((1, 20, "level_1_20"), (21, 40, "level_21_40"),
                            (41, 60, "level_41_60")):
        masks[name] = (level >= low) & (level <= high)
    return masks


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    parser.add_argument("--samples", type=int, default=1024)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--seeds", nargs="+", type=int, default=[1, 2])
    parser.add_argument("--max-rounds", type=int, default=24)
    parser.add_argument("--out", default=str(REPO_ROOT / "benchmarks" / "head_to_head.json"))
    args = parser.parse_args(argv)

    cfg = Config(max_rounds=args.max_rounds)
    tables, spec = load_slice(Path(args.data), args.start, args.samples)
    state0 = initial_state(spec, tables, cfg)
    masks = slice_masks(spec, cfg)
    rollout = jax.jit(make_rollout(cfg, 2 * cfg.max_rounds))
    paths = {
        "ppo": REPO_ROOT / "models" / "encounter_selfplay.pkl",
        "pbt": REPO_ROOT / "models" / "encounter_pbt.pkl",
        "es": REPO_ROOT / "models" / "encounter_es_long.pkl",
    }
    params = {name: load_params(path) for name, path in paths.items()}
    matchups = [
        ("pbt", "ppo"), ("ppo", "pbt"),
        ("pbt", "es"), ("es", "pbt"),
        ("ppo", "es"), ("es", "ppo"),
    ]
    results = {}
    for name_a, name_b in matchups:
        winners = []
        for seed in args.seeds:
            final = rollout(params[name_a], params[name_b], state0, tables,
                            jax.random.PRNGKey(seed))
            winners.append(np.asarray(final["winner"]))
        winner = np.concatenate(winners)
        overall = {
            "n": int(winner.size),
            "a_win": float((winner == 0).mean()),
            "b_win": float((winner == 1).mean()),
            "draw": float((winner == 2).mean()),
        }
        slices = {}
        repeated = {key: np.tile(mask, len(args.seeds)) for key, mask in masks.items()}
        for key, mask in repeated.items():
            if not mask.any():
                continue
            slices[key] = {
                "n": int(mask.sum()),
                "a_win": float((winner[mask] == 0).mean()),
                "b_win": float((winner[mask] == 1).mean()),
                "draw": float((winner[mask] == 2).mean()),
            }
        results[f"{name_a}_vs_{name_b}"] = {"overall": overall, "slices": slices}
        print(f"{name_a} vs {name_b}: {json.dumps(overall, sort_keys=True)}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
