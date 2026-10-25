"""Self-play PPO for the encounter environment.

Run a short CPU smoke first::

    python -m combat.encounter.train --data <encounters.npz> --samples 64 \
        --iters 20 --out models/encounter_selfplay.pkl

The same script is the Kaggle entry point. It shards the batch across every
local JAX device with ``pmap`` when more than one is visible (two T4s, or eight
TPU chips), and falls back to one device for CPU smoke tests. ``--samples`` is
the only knob that should change between a laptop smoke and a full run.

Why PPO and not model-free search: the environment is cheap, the episode is
short, and the quantity we need is a calibrated win probability, not a
leaderboard score. PPO's value head is trained on the same rollouts that
produce the policy, so the value is evaluated on the distribution the policy
actually creates.
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

from combat.encounter import model
from combat.encounter.env import (Config, action_masks, initial_state, observe,
                                  sample_actions, sample_random_actions, step,
                                  terminal_reward)
from combat.encounter.heuristic import heuristic_actions

TABLE_KEYS = ("spell_ap", "spell_rmin", "spell_rmax", "spell_damage",
              "spell_heal", "spell_element", "spell_ally")
SPEC_KEYS = ("hp", "max_hp", "ap_max", "mp_max", "power", "res", "dist",
             "initiative", "alive", "first_team", "level", "monster_id",
             "boss", "group")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = REPO_ROOT / "models" / "encounter_selfplay.pkl"


def load_encounters(path: Path, samples: Optional[int] = None
                    ) -> Tuple[Dict[str, jax.Array], Dict[str, jax.Array]]:
    data = np.load(path)
    n = int(data["hp"].shape[0])
    take = n if samples is None else min(int(samples), n)
    start = 0 if take == n else max(0, n - take)
    tables = {k: jnp.asarray(data[k][start:start + take]) for k in TABLE_KEYS}
    spec = {k: jnp.asarray(data[k][start:start + take])
            for k in SPEC_KEYS if k in data}
    return tables, spec


def _win_rate(mask: np.ndarray, winner: np.ndarray) -> Optional[float]:
    return float((winner[mask] == 0).mean()) if bool(mask.any()) else None


def _shard_inputs(tree, n_dev: int):
    """Split the leading batch axis into one chunk per pmap device."""
    return jax.tree_util.tree_map(
        lambda x: x.reshape((n_dev, x.shape[0] // n_dev) + x.shape[1:]), tree)


def _merge_time_device(value: jax.Array) -> jax.Array:
    """[D,T,B,...] pmap trajectory -> [D,T*B,...] for one device's PPO batch."""
    return value.reshape(
        (value.shape[0], value.shape[1] * value.shape[2]) + value.shape[3:])


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


def _opponent_actions(state, tables, cfg, opponent: str,
                      key: jax.Array) -> Tuple[Dict[str, jax.Array], jax.Array]:
    """Actions for the monster side, and a mask of steps the policy owns."""
    if opponent == "self":
        return None, jnp.ones_like(state["done"])
    if opponent == "heuristic":
        return heuristic_actions(state, tables, cfg), state["acting_team"] == 0
    return sample_random_actions(key, action_masks(state, tables, cfg), cfg), \
        state["acting_team"] == 0


def make_rollout(cfg: Config, length: int, opponent: str = "self"):
    def rollout(params, state0, tables, key):
        def body(carry, _):
            state, key = carry
            k1, k2 = jax.random.split(key)
            obs, glob, amask = observe(state, tables, cfg)
            masks = action_masks(state, tables, cfg)
            logits = model.forward(params, obs, glob, amask)
            acts = sample_actions(k1, logits, masks, cfg)
            policy_logp = acts["logp"]
            own = jnp.ones_like(state["done"])
            if opponent != "self":
                other, _ = _opponent_actions(state, tables, cfg, opponent, k2)
                use_other = state["acting_team"] == 1
                acts = {k: jnp.where(use_other[:, None], other[k], acts[k])
                        for k in ("move", "spell", "target")}
                own = ~use_other
            out = {
                "obs": obs, "glob": glob, "amask": amask,
                "move": acts["move"], "spell": acts["spell"],
                "target": acts["target"], "logp": policy_logp,
                "value": logits["value"], "team": state["acting_team"],
                "done_before": state["done"], "learned": own,
                "move_mask": masks["move"], "spell_mask": masks["spell"],
                "target_mask": masks["target"], "actor_mask": masks["actor"],
            }
            state = step(state, acts, tables, cfg)
            return (state, k2), out
        (state, _), out = jax.lax.scan(body, (state0, key), None, length=length)
        out["winner"] = state["winner"]
        out["final_hp"] = state["hp"]
        out["final_max_hp"] = state["max_hp"]
        out["final_round"] = state["round"]
        return out
    return rollout


def make_eval_rollout(cfg: Config, length: int, opponent: str = "random",
                      player: str = "learned"):
    """A player policy (learned or uniform-random) against a fixed opponent."""
    def rollout(params, state0, tables, key):
        def body(carry, _):
            state, key = carry
            k1, k2 = jax.random.split(key)
            obs, glob, amask = observe(state, tables, cfg)
            masks = action_masks(state, tables, cfg)
            if player == "random":
                chosen = sample_random_actions(k1, masks, cfg)
            else:
                logits = model.forward(params, obs, glob, amask)
                chosen = sample_actions(k1, logits, masks, cfg)
            if opponent == "random":
                other = sample_random_actions(k2, masks, cfg)
            elif opponent == "heuristic":
                other = heuristic_actions(state, tables, cfg)
            else:
                other = None
            if other is not None:
                use_other = state["acting_team"] == 1
                acts = {k: jnp.where(use_other[:, None], other[k], chosen[k])
                        for k in ("move", "spell", "target")}
            else:
                acts = chosen
            state = step(state, acts, tables, cfg)
            return (state, k2), None
        (state, _), _ = jax.lax.scan(body, (state0, key), None, length=length)
        return state
    return rollout


def make_baseline_rollout(cfg: Config, length: int):
    """Both sides on the scripted opponent: the environment's fair-fight rate."""
    def rollout(state0, tables):
        def body(state, _):
            return step(state, heuristic_actions(state, tables, cfg),
                        tables, cfg), None
        state, _ = jax.lax.scan(body, state0, None, length=length)
        return state
    return jax.jit(rollout)


def prepare_batch(traj: Dict[str, jax.Array], cfg: Config) -> Dict[str, jax.Array]:
    if traj["team"].ndim == 3:
        # pmap trajectory: [D,T,B,...]. Each device owns one D slice and its own
        # T*B transitions, so the loss and gradient run per device and are
        # averaged with pmean.
        D, T, B = traj["team"].shape
        flat = {}
        for key, value in traj.items():
            if key.startswith("final_") or key in (
                    "winner", "team", "done_before", "learned"):
                continue
            flat[key] = _merge_time_device(value)
        winner = jnp.broadcast_to(traj["winner"][:, None, :], (D, T, B))
        team = traj["team"]
        ret = terminal_reward(winner, team).reshape(D, T * B)
        learned = _merge_time_device(traj["learned"])
        valid = (~_merge_time_device(traj["done_before"])) & learned
        flat["ret"] = ret
        flat["valid"] = valid
        flat["actor_mask"] = _merge_time_device(traj["actor_mask"])
        return flat

    T, B = traj["team"].shape
    flat = {}
    for key, value in traj.items():
        if key.startswith("final_"):
            continue
        if key in ("obs", "glob", "amask"):
            flat[key] = value.reshape((T * B,) + value.shape[2:])
        elif key.endswith("_mask"):
            flat[key] = value.reshape((T * B,) + value.shape[2:])
        elif value.ndim == 2:
            flat[key] = value.reshape(T * B)
        elif value.ndim == 3:
            flat[key] = value.reshape((T * B,) + value.shape[2:])
    winner = jnp.broadcast_to(traj["winner"][None, :], (T, B))
    team = traj["team"]
    ret = terminal_reward(winner, team).reshape(T * B)
    learned = traj["learned"].reshape(T * B)
    valid = (~traj["done_before"]).reshape(T * B) & learned
    flat["ret"] = ret
    flat["valid"] = valid
    flat["actor_mask"] = traj["actor_mask"].reshape((T * B, cfg.n_actors))
    return flat


def make_loss(cfg: Config):
    def loss_fn(params, batch):
        logits = model.forward(params, batch["obs"], batch["glob"],
                               batch["amask"])
        actions = {"move": batch["move"], "spell": batch["spell"],
                   "target": batch["target"]}
        masks = {"move": batch["move_mask"], "spell": batch["spell_mask"],
                 "target": batch["target_mask"]}
        logp, ent = model.factor_logp(logits, masks, actions, cfg)
        valid = batch["valid"].astype(jnp.float32)
        adv = batch["ret"] - batch["value"]
        count = jnp.maximum(jnp.sum(valid), 1.0)
        mean = jnp.sum(jnp.where(valid > 0, adv, 0.0)) / count
        var = jnp.sum(jnp.where(valid > 0, (adv - mean) ** 2, 0.0)) / count
        adv = (adv - mean) / jnp.sqrt(var + 1e-6)
        actor = batch["actor_mask"] & (batch["valid"][:, None])
        actor_count = jnp.maximum(jnp.sum(actor.astype(jnp.float32)), 1.0)
        ratio = jnp.exp(logp - batch["logp"])
        clipped = jnp.clip(ratio, 0.8, 1.2)
        pg = -jnp.sum(jnp.where(
            actor, jnp.minimum(ratio * adv[:, None], clipped * adv[:, None]), 0.0
        )) / actor_count
        vf = jnp.sum(jnp.where(
            batch["valid"], (logits["value"] - batch["ret"]) ** 2, 0.0)) / count
        entropy = jnp.sum(jnp.where(actor, ent, 0.0)) / actor_count
        return pg + 0.5 * vf - 0.01 * entropy, {
            "pg": pg, "vf": vf, "entropy": entropy,
            "adv": jnp.mean(jnp.where(batch["valid"], adv, 0.0)),
        }
    return loss_fn


def make_grad_step(loss_fn):
    """Per-device loss/gradient, averaged across the pmap axis."""
    def step(params, batch):
        (loss, aux), grads = jax.value_and_grad(loss_fn, has_aux=True)(
            params, batch)
        loss = jax.lax.pmean(loss, "data")
        aux = jax.tree_util.tree_map(
            lambda value: jax.lax.pmean(value, "data"), aux)
        grads = jax.tree_util.tree_map(
            lambda value: jax.lax.pmean(value, "data"), grads)
        return (loss, aux), grads
    return step


def _cast_rate(traj: Dict[str, jax.Array], cfg: Config) -> float:
    spell = np.asarray(traj["spell"]).reshape(-1)
    actor = np.asarray(traj["actor_mask"]).reshape(-1)
    if actor.sum() == 0:
        return 0.0
    return float(((spell < cfg.spells) & actor).sum() / actor.sum())


def train(args: argparse.Namespace) -> Dict[str, Any]:
    cfg = Config(max_rounds=args.max_rounds)
    length = 2 * cfg.max_rounds
    tables, spec = load_encounters(Path(args.data), args.samples)
    state0 = initial_state(spec, tables, cfg)
    obs0, glob0, _ = observe(state0, tables, cfg)
    params = model.init_params(jax.random.PRNGKey(args.seed), obs0.shape[-1],
                               glob0.shape[-1], cfg)
    if args.init:
        with Path(args.init).open("rb") as fh:
            params = jax.tree_util.tree_map(jnp.asarray,
                                            pickle.load(fh)["params"])
        print(f"initialised from {args.init}")
    opt = adam_init(params)

    available = len(jax.local_devices())
    n_dev = available if args.devices <= 0 else min(args.devices, available)
    batch_size = int(state0["hp"].shape[0])
    while n_dev > 1 and batch_size % n_dev:
        n_dev -= 1
    print(json.dumps({"devices": n_dev, "available": available,
                      "batch": batch_size}, sort_keys=True))

    loss_fn = make_loss(cfg)
    rollout_fn = make_rollout(cfg, length, opponent=args.opponent)
    eval_random = make_eval_rollout(cfg, length, opponent="random")
    eval_heur = make_eval_rollout(cfg, length, opponent="heuristic")
    eval_random_jit = jax.jit(eval_random)
    eval_heur_jit = jax.jit(eval_heur)

    if n_dev > 1:
        dev_state0 = _shard_inputs(state0, n_dev)
        dev_tables = _shard_inputs(tables, n_dev)
        rollout = jax.pmap(rollout_fn, axis_name="data",
                           in_axes=(None, 0, 0, 0))
        grad = jax.pmap(make_grad_step(loss_fn), axis_name="data",
                        in_axes=(None, 0))
    else:
        rollout = jax.jit(rollout_fn)
        grad = jax.jit(jax.value_and_grad(loss_fn, has_aux=True))

    baseline = make_baseline_rollout(cfg, length)(state0, tables)
    bw = np.asarray(baseline["winner"])
    baseline_row = {
        "baseline_heuristic_player_win": float((bw == 0).mean()),
        "baseline_heuristic_enemy_win": float((bw == 1).mean()),
        "baseline_heuristic_draw": float((bw == 2).mean()),
        "baseline_rounds": float(np.asarray(baseline["round"]).mean()),
    }
    print(json.dumps(baseline_row, sort_keys=True))
    initial_alive = np.asarray(spec["alive"])
    group_np = (np.asarray(spec["group"]) if "group" in spec
                else initial_alive[:, cfg.allies:].sum(axis=1).astype(np.int32))
    boss_np = (np.any(np.asarray(spec["boss"]), axis=1) if "boss" in spec
               else np.zeros(initial_alive.shape[0], dtype=bool))
    first_np = np.asarray(state0["acting_team"])
    for group_size in range(1, cfg.enemies + 1):
        baseline_row[f"baseline_heuristic_player_win_group{group_size}"] = \
            _win_rate(group_np == group_size, bw)
    baseline_row["baseline_heuristic_player_win_boss"] = _win_rate(boss_np, bw)
    baseline_row["baseline_heuristic_player_win_non_boss"] = _win_rate(~boss_np, bw)
    baseline_row["baseline_heuristic_player_win_first"] = _win_rate(first_np == 0, bw)
    baseline_row["baseline_heuristic_player_win_second"] = _win_rate(first_np == 1, bw)
    slots = np.arange(cfg.n_actors)[None, :]
    player_slots = initial_alive & (slots < cfg.allies)
    enemy_slots = initial_alive & (slots >= cfg.allies)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    def checkpoint() -> Dict[str, Any]:
        return {
            "params": jax.tree_util.tree_map(np.asarray, params),
            "config": cfg.__dict__,
            "hidden": 96, "heads": 4,
            "feat_dim": int(obs0.shape[-1]),
            "global_dim": int(glob0.shape[-1]),
            "samples": int(state0["hp"].shape[0]),
            "metrics": metrics,
            "baseline": baseline_row,
            "data": str(args.data),
        }

    key = jax.random.PRNGKey(args.seed)
    metrics = []
    for it in range(1, args.iters + 1):
        key, k_roll = jax.random.split(key)
        if n_dev > 1:
            traj = rollout(params, dev_state0, dev_tables,
                           jax.random.split(k_roll, n_dev))
        else:
            traj = rollout(params, state0, tables, k_roll)
        batch = prepare_batch(traj, cfg)
        losses = []
        for _ in range(args.epochs):
            if n_dev > 1:
                (loss, aux), grads = grad(params, batch)
                loss_value = float(loss[0])
                aux_values = {k: float(v[0]) for k, v in aux.items()}
                grads = jax.tree_util.tree_map(lambda value: value[0], grads)
            else:
                (loss, aux), grads = grad(params, batch)
                loss_value = float(loss)
                aux_values = {k: float(v) for k, v in aux.items()}
            params, opt = adam_update(params, grads, opt, lr=args.lr)
            losses.append((loss_value, aux_values))
        win = np.asarray(traj["winner"]).reshape(-1)
        final_hp = np.asarray(traj["final_hp"]).reshape(-1, cfg.n_actors)
        final_max = np.asarray(traj["final_max_hp"]).reshape(-1, cfg.n_actors)
        final_round = np.asarray(traj["final_round"]).reshape(-1)
        row = {
            "iter": it,
            "loss": losses[-1][0],
            "pg": losses[-1][1]["pg"],
            "vf": losses[-1][1]["vf"],
            "entropy": losses[-1][1]["entropy"],
            "player_win": float((win == 0).mean()),
            "enemy_win": float((win == 1).mean()),
            "draw": float((win == 2).mean()),
            "cast_rate": _cast_rate(traj, cfg),
            "rounds": float(final_round.mean()),
            "player_hp_left": float((
                (final_hp * player_slots).sum(axis=1)
                / np.maximum((final_max * player_slots).sum(axis=1), 1.0)
            ).mean()),
            "enemy_hp_left": float((
                (final_hp * enemy_slots).sum(axis=1)
                / np.maximum((final_max * enemy_slots).sum(axis=1), 1.0)
            ).mean()),
        }
        for group_size in range(1, cfg.enemies + 1):
            row[f"player_win_group{group_size}"] = _win_rate(
                group_np == group_size, win)
        row["player_win_boss"] = _win_rate(boss_np, win)
        row["player_win_non_boss"] = _win_rate(~boss_np, win)
        row["player_win_first"] = _win_rate(first_np == 0, win)
        row["player_win_second"] = _win_rate(first_np == 1, win)
        if it % args.eval_every == 0 or it == args.iters:
            key, k_eval, k_heur = jax.random.split(key, 3)
            final = eval_random_jit(params, state0, tables, k_eval)
            w = np.asarray(final["winner"])
            row["eval_random_player_win"] = float((w == 0).mean())
            row["eval_random_draw"] = float((w == 2).mean())
            final = eval_heur_jit(params, state0, tables, k_heur)
            w = np.asarray(final["winner"])
            row["eval_heuristic_player_win"] = float((w == 0).mean())
            row["eval_heuristic_draw"] = float((w == 2).mean())
            row["eval_heuristic_player_win_boss"] = _win_rate(boss_np, w)
            row["eval_heuristic_player_win_first"] = _win_rate(first_np == 0, w)
            row["eval_heuristic_player_win_second"] = _win_rate(first_np == 1, w)
            for group_size in range(1, cfg.enemies + 1):
                row[f"eval_heuristic_player_win_group{group_size}"] = _win_rate(
                    group_np == group_size, w)
        metrics.append(row)
        print(json.dumps(row, sort_keys=True))
        if args.save_every and it % args.save_every == 0:
            with out.open("wb") as fh:
                pickle.dump(checkpoint(), fh)
            print(f"checkpoint {out} at {it}")
    payload = checkpoint()
    with out.open("wb") as fh:
        pickle.dump(payload, fh)
    print(f"saved {out}")
    return payload


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True, help="encounters.npz from build_dataset")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--samples", type=int, default=None,
                   help="use at most this many encounters (CPU smoke uses 64)")
    p.add_argument("--iters", type=int, default=100)
    p.add_argument("--epochs", type=int, default=2)
    p.add_argument("--max-rounds", type=int, default=24)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--devices", type=int, default=0,
                   help="local devices to pmap over (0 = all visible)")
    p.add_argument("--eval-every", type=int, default=5)
    p.add_argument("--save-every", type=int, default=100,
                   help="write a checkpoint every N iterations (0 disables)")
    p.add_argument("--opponent", choices=("heuristic", "random", "self"),
                   default="heuristic",
                   help="monster side during training; self = both sides learned")
    p.add_argument("--init", default=None, help="params pickle to resume from")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    train(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
