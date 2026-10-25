"""GPU-native evolution strategy for the JAX combat policy.

The v4.0 duel showed a hand-built solver is a hard baseline. This harness
tests the alternative the user asked for: let the neural policy be the actor
and search its weights with a population, using vmap over individuals and
pmap over GPUs/TPUs.

It evolves the full policy/value network against the scripted opponent. The
fitness is win rate plus a small surviving-HP term, with a draw penalty. A
held-out slice is evaluated separately; the benchmark gate still decides
whether a generation is useful.

Usage (single GPU/CPU smoke):
    python -m combat.encounter.es --data <npz> --samples 64 \
        --population 8 --generations 3 --out /tmp/es.pkl

Kaggle: --samples 512 --population 64 --generations 200.
"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from pathlib import Path
from typing import Any, Dict, Tuple

import jax
import jax.numpy as jnp
from jax import flatten_util
import numpy as np

from combat.encounter.env import Config, initial_state
from combat.encounter.train import (TABLE_KEYS, load_encounters,
                                    make_eval_rollout)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = REPO_ROOT / "models" / "encounter_es.pkl"


def _player_slots(cfg: Config):
    slots = np.arange(cfg.n_actors)[None, :]
    return slots < cfg.allies


def _fitness(final: Dict[str, jax.Array], cfg: Config) -> jax.Array:
    winner = final["winner"]
    win = (winner == 0).astype(jnp.float32)
    draw = (winner == 2).astype(jnp.float32)
    hp = final["hp"]
    max_hp = jnp.maximum(final["max_hp"], 1.0)
    player = jnp.asarray(_player_slots(cfg))
    hp_ratio = jnp.sum(hp * player, axis=1) / jnp.maximum(
        jnp.sum(max_hp * player, axis=1), 1.0)
    return jnp.mean(win + 0.1 * hp_ratio - 0.05 * draw)


def _load_checkpoint(path: Path) -> Tuple[Dict[str, Any], Config]:
    payload = pickle.loads(Path(path).read_bytes())
    saved = payload.get("config") or {}
    cfg = Config(
        allies=int(saved.get("allies", 1)),
        enemies=int(saved.get("enemies", 4)),
        spells=int(saved.get("spells", 4)),
        max_rounds=int(saved.get("max_rounds", 24)),
        max_dist=int(saved.get("max_dist", 12)),
        casts_per_turn=int(saved.get("casts_per_turn", 4)),
    )
    params = jax.tree_util.tree_map(jnp.asarray, payload["params"])
    return params, cfg


def _eval_slice(data_path: Path, start: int, samples: int):
    data = np.load(data_path)
    n = int(data["hp"].shape[0])
    start = max(0, min(start, n))
    take = min(samples, n - start)
    tables = {key: jnp.asarray(data[key][start:start + take])
              for key in TABLE_KEYS}
    spec_keys = ("hp", "max_hp", "ap_max", "mp_max", "power", "res",
                 "dist", "initiative", "alive", "first_team")
    spec = {key: jnp.asarray(data[key][start:start + take])
            for key in spec_keys if key in data}
    return tables, spec


def run(args: argparse.Namespace) -> int:
    started = time.time()
    if args.init:
        params, cfg = _load_checkpoint(Path(args.init))
    else:
        raise SystemExit("--init is required; ES starts from the v3 checkpoint")
    train_tables, train_spec = load_encounters(
        Path(args.data), samples=args.samples)
    train_state = initial_state(train_spec, train_tables, cfg)
    eval_tables, eval_spec = _eval_slice(
        Path(args.data), args.eval_start, args.eval_samples)
    eval_state = initial_state(eval_spec, eval_tables, cfg)
    length = 2 * cfg.max_rounds
    rollout = make_eval_rollout(cfg, length, opponent="heuristic")
    eval_rollout = jax.jit(rollout)

    flat, unravel = flatten_util.ravel_pytree(params)
    theta = jnp.asarray(flat, dtype=jnp.float32)
    dimension = int(theta.shape[0])
    population = int(args.population)
    if population % 2:
        population += 1
    n_dev = len(jax.local_devices())
    if population % n_dev:
        population += n_dev - (population % n_dev)
    pairs = population // 2
    print(f"dimension {dimension}, population {population}, "
          f"devices {n_dev}, samples {args.samples}, "
          f"eval samples {args.eval_samples}")

    def single(theta_one, key):
        params_one = unravel(theta_one)
        final = rollout(params_one, train_state, train_tables, key)
        return _fitness(final, cfg)

    def eval_pop(theta_pop, keys):
        return jax.vmap(single)(theta_pop, keys)

    if n_dev > 1:
        per_device = population // n_dev
        eval_sharded = jax.pmap(
            lambda t, k: eval_pop(t, k), in_axes=(0, 0))

        def evaluate(theta_pop, keys):
            return eval_sharded(
                theta_pop.reshape(n_dev, per_device, dimension),
                keys.reshape(n_dev, per_device, 2)).reshape(-1)
    else:
        evaluate = jax.jit(eval_pop)

    rng = np.random.default_rng(args.seed)
    metrics = []
    best_fitness = float("-inf")
    best_theta = theta
    for generation in range(1, args.generations + 1):
        eps = rng.normal(size=(pairs, dimension)).astype(np.float32)
        theta_pop = jnp.concatenate([
            theta[None, :] + args.sigma * jnp.asarray(eps),
            theta[None, :] - args.sigma * jnp.asarray(eps),
        ], axis=0)
        # Common random numbers: every individual sees the same action-noise
        # draw, so the ranking reflects the policy difference, not luck.
        key = jax.random.PRNGKey(args.seed + generation)
        keys = jnp.tile(key[None, :], (population, 1))
        fitness = np.asarray(evaluate(theta_pop, keys))
        ranked = np.argsort(np.argsort(fitness)).astype(np.float32)
        ranked = ranked / max(1, population - 1) - 0.5
        plus, minus = ranked[:pairs], ranked[pairs:]
        grad = np.mean((plus - minus)[:, None] * eps, axis=0) / args.sigma
        theta = theta + args.lr * jnp.asarray(grad) - args.wd * theta
        mean_fitness = float(fitness.mean())
        best_index = int(fitness.argmax())
        if fitness[best_index] > best_fitness:
            best_fitness = float(fitness[best_index])
            best_theta = theta_pop[best_index]
        row = {
            "generation": generation,
            "fitness_mean": mean_fitness,
            "fitness_max": float(fitness.max()),
            "best_fitness": best_fitness,
            "theta_norm": float(jnp.linalg.norm(theta)),
            "elapsed_s": round(time.time() - started, 1),
        }
        if generation % args.eval_every == 0 or generation == args.generations:
            eval_params = unravel(best_theta if best_fitness > float("-inf")
                                  else theta)
            final = eval_rollout(eval_params, eval_state, eval_tables,
                                 jax.random.PRNGKey(args.seed + 7919))
            win = np.asarray(final["winner"])
            row["eval_player_win"] = float((win == 0).mean())
            row["eval_enemy_win"] = float((win == 1).mean())
            row["eval_draw"] = float((win == 2).mean())
            row["eval_rounds"] = float(np.asarray(final["round"]).mean())
        metrics.append(row)
        print(json.dumps(row, sort_keys=True))
        if args.save_every and generation % args.save_every == 0:
            _save(Path(args.out), unravel(theta), cfg, metrics, args)
    final_theta = best_theta if best_fitness > float("-inf") else theta
    _save(Path(args.out), unravel(final_theta), cfg, metrics, args)
    print(f"saved {args.out}")
    return 0


def _save(path: Path, params, cfg: Config, metrics, args) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "params": jax.tree_util.tree_map(np.asarray, params),
        "config": cfg.__dict__,
        "hidden": 96,
        "heads": 4,
        "feat_dim": int(params["enc1"][0].shape[0]),
        "global_dim": int(params["glob"][0].shape[0]),
        "metrics": metrics,
        "method": "openai-es",
        "sigma": args.sigma,
        "population": int(args.population),
        "samples": int(args.samples),
        "best_fitness": max((row["best_fitness"] for row in metrics),
                            default=None),
        "data": str(args.data),
    }
    with path.open("wb") as handle:
        pickle.dump(payload, handle)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True)
    p.add_argument("--init", default=str(REPO_ROOT / "models" / "encounter_selfplay.pkl"))
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--samples", type=int, default=512)
    p.add_argument("--eval-start", type=int, default=0)
    p.add_argument("--eval-samples", type=int, default=512)
    p.add_argument("--population", type=int, default=64)
    p.add_argument("--generations", type=int, default=100)
    p.add_argument("--sigma", type=float, default=0.02)
    p.add_argument("--lr", type=float, default=0.005)
    p.add_argument("--wd", type=float, default=1e-4)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--eval-every", type=int, default=10)
    p.add_argument("--save-every", type=int, default=25)
    return p


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
