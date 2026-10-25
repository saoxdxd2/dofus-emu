"""Measure encounter policies before changing the training recipe.

The benchmark is deliberately boring: a fixed held-out slice, fixed seeds, the
same environment and opponent for every candidate, slice-level win rates, and
a calibration split that never sees its own test rows. The point is to make a
change falsifiable before spending another GPU hour on it.

Baselines
---------
* uniform-random player vs the scripted heuristic,
* the scripted heuristic against itself (the environment's fair-fight rate),
* every supplied checkpoint against the scripted heuristic.

Every policy is evaluated on the same rows and seeds. Calibration uses the
value head on the initial state with the actual first team, then flips the sign
when the monsters act first. A harness that forgets that flip will report a
well-ranked value head as badly calibrated, which is exactly what happened once.

Usage
-----
    python -m combat.encounter.benchmark \
        --data kaggle/tpu_selfplay/dataset/encounters.npz \
        --samples 2048 --start 0 --seeds 1 2 3 \
        --out benchmarks/encounter_v3.json
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import jax
import jax.numpy as jnp
import numpy as np

from combat.encounter import model
from combat.encounter.env import Config, action_masks, initial_state, observe
from combat.encounter.labels import _enemy_facts, _player_facts
from combat.encounter.train import (SPEC_KEYS, TABLE_KEYS, load_encounters,
                                    make_baseline_rollout, make_eval_rollout)
from combat.fight_estimate import estimate_from_facts, fit_calibrator

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA = REPO_ROOT / "kaggle" / "tpu_selfplay" / "dataset" / "encounters.npz"
DEFAULT_OUT = REPO_ROOT / "benchmarks" / "encounter_v3.json"
DEFAULT_CHECKPOINTS = (
    ("v2_base", REPO_ROOT / "models" / "encounter_selfplay_v2_base.pkl"),
    ("v3_phase1", REPO_ROOT / "models" / "encounter_selfplay_v3_phase1.pkl"),
    ("v3_phase2", REPO_ROOT / "models" / "encounter_selfplay.pkl"),
)


@dataclass
class Checkpoint:
    name: str
    path: Path
    params: Any
    feat_dim: int
    global_dim: int
    saved_config: Dict[str, Any]
    payload: Dict[str, Any]


# ---------------------------------------------------------------------------
# data and checkpoints
# ---------------------------------------------------------------------------


def load_slice(path: Path, start: int, samples: int
               ) -> Tuple[Dict[str, jax.Array], Dict[str, jax.Array]]:
    """Load a fixed row range instead of ``load_encounters``' tail slice."""
    data = np.load(path)
    total = int(data["hp"].shape[0])
    start = max(0, min(int(start), total))
    take = min(int(samples), total - start)
    tables = {key: jnp.asarray(data[key][start:start + take])
              for key in TABLE_KEYS}
    spec = {key: jnp.asarray(data[key][start:start + take])
            for key in SPEC_KEYS if key in data}
    return tables, spec


def load_checkpoint(name: str, path: Path) -> Checkpoint:
    payload = pickle.loads(Path(path).read_bytes())
    params = jax.tree_util.tree_map(jnp.asarray, payload["params"])
    saved = dict(payload.get("config") or {})
    return Checkpoint(
        name=name,
        path=Path(path),
        params=params,
        feat_dim=int(payload.get("feat_dim", 0)),
        global_dim=int(payload.get("global_dim", 0)),
        saved_config=saved,
        payload=payload,
    )


def config_from_saved(saved: Dict[str, Any], fallback: Config) -> Config:
    return Config(
        allies=int(saved.get("allies", fallback.allies)),
        enemies=int(saved.get("enemies", fallback.enemies)),
        spells=int(saved.get("spells", fallback.spells)),
        max_rounds=int(saved.get("max_rounds", fallback.max_rounds)),
        max_dist=int(saved.get("max_dist", fallback.max_dist)),
        casts_per_turn=int(saved.get("casts_per_turn", fallback.casts_per_turn)),
    )


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def _bootstrap_ci(values: np.ndarray, seed: int = 0, draws: int = 2000
                  ) -> Tuple[float, float]:
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = np.empty(draws, dtype=float)
    for i in range(draws):
        means[i] = values[rng.integers(0, values.size, values.size)].mean()
    return (float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975)))


def _hp_left(final: Dict[str, Any], spec: Dict[str, Any], cfg: Config) -> Tuple[float, float]:
    hp = np.asarray(final["hp"])
    max_hp = np.asarray(final["max_hp"])
    alive = np.asarray(spec["alive"])
    slots = np.arange(cfg.n_actors)[None, :]
    player_slots = alive & (slots < cfg.allies)
    enemy_slots = alive & (slots >= cfg.allies)
    player = float(((hp * player_slots).sum(axis=1)
                    / np.maximum((max_hp * player_slots).sum(axis=1), 1.0)).mean())
    enemy = float(((hp * enemy_slots).sum(axis=1)
                   / np.maximum((max_hp * enemy_slots).sum(axis=1), 1.0)).mean())
    return player, enemy


def _main_resist(spec: Dict[str, Any], tables: Dict[str, Any]
                 ) -> Tuple[np.ndarray, np.ndarray]:
    """Resistance of the enemies to the player's strongest spell element."""
    damage = np.asarray(tables["spell_damage"][:, 0])
    element = np.asarray(tables["spell_element"][:, 0])
    index = np.argmax(damage, axis=1)
    rows = np.arange(damage.shape[0])
    main = element[rows, index]
    resists = np.asarray(spec["res"])[:, 1:, :]
    picked = resists[rows[:, None], np.arange(resists.shape[1])[None, :],
                     main[:, None]]
    return picked.min(axis=1), picked.mean(axis=1)


def _focus_count(spec: Dict[str, Any], tables: Dict[str, Any]) -> np.ndarray:
    damage = np.asarray(tables["spell_damage"][:, 0])
    element = np.asarray(tables["spell_element"][:, 0])
    out = []
    for i in range(damage.shape[0]):
        mask = damage[i] > 0
        out.append(len(set(element[i][mask].tolist())))
    return np.asarray(out, dtype=np.int32)


def slice_masks(spec: Dict[str, Any], tables: Dict[str, Any],
                cfg: Config) -> Dict[str, np.ndarray]:
    alive = np.asarray(spec["alive"])
    group = (np.asarray(spec["group"]) if "group" in spec
             else alive[:, cfg.allies:].sum(axis=1).astype(np.int32))
    boss = (np.any(np.asarray(spec["boss"]), axis=1) if "boss" in spec
            else np.zeros(alive.shape[0], dtype=bool))
    first = np.asarray(spec["first_team"]) == 0
    level = np.asarray(spec["level"])[:, 0] if "level" in spec else np.ones(
        alive.shape[0], dtype=np.float32)
    focus = _focus_count(spec, tables)
    min_res, _mean_res = _main_resist(spec, tables)
    masks: Dict[str, np.ndarray] = {
        "all": np.ones(alive.shape[0], dtype=bool),
        "boss": boss,
        "non_boss": ~boss,
        "first": first,
        "second": ~first,
        "focus_1": focus == 1,
        "focus_2": focus == 2,
        "focus_3": focus >= 3,
        "main_resist_negative": min_res < 0,
        "main_resist_low": (min_res >= 0) & (min_res < 10),
        "main_resist_high": min_res >= 10,
    }
    for size in range(1, cfg.enemies + 1):
        masks[f"group_{size}"] = group == size
    for low, high, label in ((1, 20, "level_1_20"),
                             (21, 40, "level_21_40"),
                             (41, 60, "level_41_60"),
                             (61, 200, "level_61_plus")):
        masks[label] = (level >= low) & (level <= high)
    return masks


def _slice_report(winner: np.ndarray, masks: Dict[str, np.ndarray]
                  ) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for name, mask in masks.items():
        n = int(mask.sum())
        if n == 0:
            out[name] = {"n": 0}
            continue
        values = winner[mask]
        out[name] = {
            "n": n,
            "player_win": float((values == 0).mean()),
            "enemy_win": float((values == 1).mean()),
            "draw": float((values == 2).mean()),
        }
    return out


def policy_metrics(final: Dict[str, Any], spec: Dict[str, Any],
                   tables: Dict[str, Any], cfg: Config
                   ) -> Dict[str, Any]:
    winner = np.asarray(final["winner"]).reshape(-1)
    masks = slice_masks(spec, tables, cfg)
    player_hp, enemy_hp = _hp_left(final, spec, cfg)
    overall = _slice_report(winner, {"all": masks["all"]})["all"]
    low, high = _bootstrap_ci((winner == 0).astype(float))
    return {
        "overall": {
            **overall,
            "player_win_ci95": [low, high],
            "rounds": float(np.asarray(final["round"]).mean()),
            "player_hp_left": player_hp,
            "enemy_hp_left": enemy_hp,
        },
        "slices": _slice_report(winner, masks),
    }


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------


def _values_at_start(checkpoint: Checkpoint, cfg: Config,
                     state0: Dict[str, jax.Array], tables: Dict[str, jax.Array]
                     ) -> np.ndarray:
    obs, glob, mask = observe(state0, tables, cfg)
    values = np.asarray(model.forward(checkpoint.params, obs, glob, mask)["value"])
    acting = np.asarray(state0["acting_team"])
    return np.where(acting == 0, values, -values)


def analytic_features(spec: Dict[str, Any], tables: Dict[str, Any],
                      cfg: Config) -> List[Dict[str, float]]:
    spec_np = {key: np.asarray(value) for key, value in spec.items()}
    table_np = {key: np.asarray(value) for key, value in tables.items()}
    features: List[Dict[str, float]] = []
    for b in range(spec_np["hp"].shape[0]):
        player = _player_facts(spec_np, table_np, b, int(spec_np["ap_max"][b, 0]))
        enemy = _enemy_facts(spec_np, b, cfg.allies)
        features.append(estimate_from_facts(player, enemy)["features"])
    return features


def _auc(y: np.ndarray, p: np.ndarray) -> float:
    order = np.argsort(p)
    y = y[order]
    p = p[order]
    ranks = np.empty(len(p), dtype=float)
    i = 0
    while i < len(p):
        j = i
        while j + 1 < len(p) and p[j + 1] == p[i]:
            j += 1
        ranks[i:j + 1] = (i + j) / 2.0 + 1.0
        i = j + 1
    positives = float(y.sum())
    negatives = float(len(y) - positives)
    if positives <= 0 or negatives <= 0:
        return float("nan")
    return float((ranks[y == 1].sum() - positives * (positives + 1) / 2.0)
                 / (positives * negatives))


def _ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = 0.0
    for k in range(bins):
        upper = edges[k + 1] if k < bins - 1 else edges[k + 1] + 1e-9
        mask = (p >= edges[k]) & (p < upper)
        if mask.any():
            total += mask.mean() * abs(p[mask].mean() - y[mask].mean())
    return float(total)


def _binary_metrics(y: np.ndarray, p: np.ndarray) -> Dict[str, float]:
    p = np.clip(np.asarray(p, dtype=float), 1e-7, 1.0 - 1e-7)
    return {
        "ece": _ece(y, p),
        "brier": float(np.mean((p - y) ** 2)),
        "logloss": float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))),
        "auc": _auc(y, p),
    }


def _platt_fit(p: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
    """One-feature logistic calibration, implemented without sklearn."""
    x = np.log(np.clip(p, 1e-7, 1 - 1e-7) / (1 - np.clip(p, 1e-7, 1 - 1e-7)))
    a, b = 1.0, 0.0
    for _ in range(400):
        z = np.clip(a * x + b, -40, 40)
        pred = 1.0 / (1.0 + np.exp(-z))
        error = pred - y
        grad_a = float(np.mean(error * x)) + 1e-3 * a
        grad_b = float(np.mean(error))
        a -= 0.5 * grad_a
        b -= 0.5 * grad_b
    return (a, b)


def _platt_apply(p: np.ndarray, a: float, b: float) -> np.ndarray:
    x = np.log(np.clip(p, 1e-7, 1 - 1e-7) / (1 - np.clip(p, 1e-7, 1 - 1e-7)))
    return 1.0 / (1.0 + np.exp(-np.clip(a * x + b, -40, 40)))


def _split_indices(y: np.ndarray, seed: int, fraction: float = 0.7
                   ) -> Tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    train: List[int] = []
    test: List[int] = []
    for value in (0.0, 1.0):
        idx = np.flatnonzero(y == value)
        rng.shuffle(idx)
        cut = max(1, int(round(len(idx) * fraction)))
        train.extend(idx[:cut].tolist())
        test.extend(idx[cut:].tolist())
    rng.shuffle(train)
    rng.shuffle(test)
    return np.asarray(train, dtype=int), np.asarray(test, dtype=int)


def calibration_report(values: np.ndarray, winner: np.ndarray,
                       features: List[Dict[str, float]],
                       splits: int = 5) -> Dict[str, Any]:
    """Raw/Platt value calibration and the analytic estimator on held-out rows."""
    keep = winner != 2
    y = (winner[keep] == 0).astype(float)
    p = np.clip((values[keep] + 1.0) / 2.0, 1e-7, 1 - 1e-7)
    kept_features = [features[i] for i in np.flatnonzero(keep)]
    rows = [{"features": feature, "won": bool(label)}
            for feature, label in zip(kept_features, y)]

    def gather(metric_rows: List[Dict[str, float]]) -> Dict[str, float]:
        keys = metric_rows[0].keys()
        return {key: float(np.mean([row[key] for row in metric_rows]))
                for key in keys}

    raw: List[Dict[str, float]] = []
    platt: List[Dict[str, float]] = []
    analytic: List[Dict[str, float]] = []
    blend: List[Dict[str, float]] = []
    for split in range(max(1, splits)):
        train, test = _split_indices(y, seed=100 + split)
        raw.append(_binary_metrics(y[test], p[test]))
        a, b = _platt_fit(p[train], y[train])
        platt.append(_binary_metrics(y[test], _platt_apply(p[test], a, b)))
        train_rows = [rows[i] for i in train]
        calibrator = fit_calibrator(train_rows, l2=1.0)
        if calibrator is None:
            continue
        predicted = np.asarray([
            calibrator.predict(rows[i]["features"]) or 0.5 for i in test
        ], dtype=float)
        analytic.append(_binary_metrics(y[test], predicted))
        train_predicted = np.asarray([
            calibrator.predict(rows[i]["features"]) or 0.5 for i in train
        ], dtype=float)
        blend_train = [
            {"features": {"analytic": float(train_predicted[i]),
                          "value": float(p[train][i])},
             "won": bool(y[train][i])}
            for i in range(len(train))
        ]
        blend_model = fit_calibrator(blend_train,
                                     features=("analytic", "value"), l2=1.0)
        if blend_model is None:
            continue
        blend_test = np.asarray([
            blend_model.predict({"analytic": float(predicted[i]),
                                 "value": float(p[test][i])}) or 0.5
            for i in range(len(test))
        ], dtype=float)
        blend.append(_binary_metrics(y[test], blend_test))

    reliability = []
    for low, high in ((0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)):
        mask = (p >= low) & (p < high)
        if mask.any():
            reliability.append({
                "range": [low, high], "n": int(mask.sum()),
                "predicted": float(p[mask].mean()),
                "actual": float(y[mask].mean()),
            })
    result: Dict[str, Any] = {
        "n": int(len(y)),
        "wins": int(y.sum()),
        "losses": int((1.0 - y).sum()),
        "raw": gather(raw),
        "platt": gather(platt),
        "reliability": reliability,
    }
    if analytic:
        result["analytic"] = gather(analytic)
    if blend:
        result["blend"] = gather(blend)
    return result


# ---------------------------------------------------------------------------
# contract checks
# ---------------------------------------------------------------------------


def contract_checks(checkpoint: Checkpoint, cfg: Config,
                    state0: Dict[str, jax.Array], tables: Dict[str, jax.Array],
                    obs0: jax.Array, glob0: jax.Array) -> Dict[str, Any]:
    masks = action_masks(state0, tables, cfg)
    obs, glob, attn = observe(state0, tables, cfg)
    checks = {
        "checkpoint_loads": checkpoint.params is not None,
        "feat_dim_matches": int(obs0.shape[-1]) == checkpoint.feat_dim,
        "global_dim_matches": int(glob0.shape[-1]) == checkpoint.global_dim,
        "checkpoint_config_matches_spells":
            int(checkpoint.saved_config.get("spells", cfg.spells)) == cfg.spells,
        "checkpoint_config_matches_actors":
            int(checkpoint.saved_config.get("allies", cfg.allies)) == cfg.allies
            and int(checkpoint.saved_config.get("enemies", cfg.enemies)) == cfg.enemies,
        "tables_finite": bool(all(
            np.isfinite(np.asarray(tables[key])).all() for key in TABLE_KEYS)),
        "pass_always_legal": bool(np.array_equal(
            np.asarray(masks["spell"][..., cfg.spells]),
            np.asarray(masks["actor"]))),
        "actor_mask_matches_team": bool(
            np.array_equal(
                np.asarray(masks["actor"]),
                np.asarray(state0["alive"]
                           & (state0["team"] == state0["acting_team"][:, None])))),
        "attn_mask_is_alive": bool(
            np.array_equal(np.asarray(attn), np.asarray(state0["alive"]))),
        "observation_finite": bool(np.isfinite(np.asarray(obs)).all()),
        "value_finite": bool(np.isfinite(np.asarray(
            model.forward(checkpoint.params, obs0, glob0, attn)["value"])).all()),
    }
    return checks


# ---------------------------------------------------------------------------
# benchmark
# ---------------------------------------------------------------------------


def evaluate_policy(cfg: Config, state0: Dict[str, jax.Array],
                    spec: Dict[str, jax.Array], tables: Dict[str, jax.Array],
                    checkpoint: Optional[Checkpoint], kind: str,
                    seeds: Sequence[int]) -> Dict[str, Any]:
    length = 2 * cfg.max_rounds
    if kind == "heuristic":
        baseline = make_baseline_rollout(cfg, length)
        final = baseline(state0, tables)
        runs = [final for _seed in seeds]
    else:
        dummy = jax.tree_util.tree_map(
            jnp.zeros_like,
            model.init_params(jax.random.PRNGKey(0), 1, 1, cfg))
        if checkpoint is not None:
            dummy = checkpoint.params
        rollout = jax.jit(make_eval_rollout(
            cfg, length, opponent="heuristic",
            player="random" if kind == "random" else "learned"))
        runs = [rollout(dummy, state0, tables, jax.random.PRNGKey(int(seed)))
                for seed in seeds]

    per_seed = []
    all_winners = []
    all_rounds = []
    player_hp = []
    enemy_hp = []
    for seed, final in zip(seeds, runs):
        metrics = policy_metrics(final, spec, tables, cfg)
        per_seed.append({"seed": int(seed), **metrics["overall"]})
        all_winners.append(np.asarray(final["winner"]).reshape(-1))
        all_rounds.append(np.asarray(final["round"]).reshape(-1))
        hp_p, hp_e = _hp_left(final, spec, cfg)
        player_hp.append(hp_p)
        enemy_hp.append(hp_e)

    winner = np.concatenate(all_winners)
    overall = _slice_report(winner, {"all": np.ones(winner.shape[0], bool)})["all"]
    overall.update({
        "player_win_ci95": list(_bootstrap_ci((winner == 0).astype(float))),
        "rounds": float(np.concatenate(all_rounds).mean()),
        "player_hp_left": float(np.mean(player_hp)),
        "enemy_hp_left": float(np.mean(enemy_hp)),
        "n": int(winner.size),
        "seeds": [int(seed) for seed in seeds],
    })
    masks = slice_masks(spec, tables, cfg)
    repeated_masks = {name: np.tile(mask, len(seeds))
                      for name, mask in masks.items()}
    slices = _slice_report(winner, repeated_masks)
    return {"overall": overall, "slices": slices, "per_seed": per_seed}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    parser.add_argument("--samples", type=int, default=2048)
    parser.add_argument("--start", type=int, default=0,
                        help="first row of the held-out slice (default: 0)")
    parser.add_argument("--max-rounds", type=int, default=24)
    parser.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3])
    parser.add_argument("--calibration-splits", type=int, default=5)
    parser.add_argument("--checkpoint", action="append", default=[],
                        metavar="NAME=PATH")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.time()
    cfg = Config(max_rounds=args.max_rounds)
    tables, spec = load_slice(Path(args.data), args.start, args.samples)
    state0 = initial_state(spec, tables, cfg)
    obs0, glob0, _attn = observe(state0, tables, cfg)

    checkpoint_args = list(args.checkpoint)
    if not checkpoint_args:
        checkpoint_args = [f"{name}={path}" for name, path in DEFAULT_CHECKPOINTS
                           if Path(path).exists()]
    checkpoints: List[Checkpoint] = []
    for item in checkpoint_args:
        if "=" not in item:
            raise SystemExit(f"--checkpoint expects NAME=PATH, got {item!r}")
        name, path = item.split("=", 1)
        if not Path(path).exists():
            raise SystemExit(f"checkpoint not found: {path}")
        checkpoints.append(load_checkpoint(name, Path(path)))

    policies: Dict[str, Any] = {}
    policies["random_player"] = evaluate_policy(
        cfg, state0, spec, tables, None, "random", args.seeds)
    policies["heuristic_mirror"] = evaluate_policy(
        cfg, state0, spec, tables, None, "heuristic", args.seeds)

    contracts: Dict[str, Any] = {}
    calibration: Dict[str, Any] = {}
    features = analytic_features(spec, tables, cfg)
    for checkpoint in checkpoints:
        if checkpoint.feat_dim and checkpoint.feat_dim != int(obs0.shape[-1]):
            raise SystemExit(
                f"{checkpoint.name}: feat_dim {checkpoint.feat_dim} does not match "
                f"the environment's {int(obs0.shape[-1])}")
        if checkpoint.global_dim and checkpoint.global_dim != int(glob0.shape[-1]):
            raise SystemExit(
                f"{checkpoint.name}: global_dim {checkpoint.global_dim} does not "
                f"match the environment's {int(glob0.shape[-1])}")
        saved_cfg = config_from_saved(checkpoint.saved_config, cfg)
        if saved_cfg.max_rounds != cfg.max_rounds:
            print(f"[benchmark] note: {checkpoint.name} was trained with "
                  f"max_rounds={saved_cfg.max_rounds}, benchmarking at {cfg.max_rounds}")
        policies[checkpoint.name] = evaluate_policy(
            cfg, state0, spec, tables, checkpoint, "checkpoint", args.seeds)
        contracts[checkpoint.name] = contract_checks(
            checkpoint, cfg, state0, tables, obs0, glob0)
        values = _values_at_start(checkpoint, cfg, state0, tables)
        winner = _winner_for_calibration(
            cfg, state0, tables, checkpoint, args.seeds[0])
        calibration[checkpoint.name] = calibration_report(
            values, winner, features, splits=args.calibration_splits)

    baseline_slices = policies["heuristic_mirror"]["slices"]
    for result in policies.values():
        result["delta_vs_heuristic"] = {
            name: (round(slice_row.get("player_win", 0.0)
                         - baseline_slices.get(name, {}).get("player_win", 0.0), 4)
                   if slice_row.get("n") and baseline_slices.get(name, {}).get("n")
                   else None)
            for name, slice_row in result["slices"].items()
        }

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data": str(Path(args.data)),
        "row_slice": {"start": args.start, "samples": args.samples},
        "environment": cfg.__dict__,
        "seeds": [int(seed) for seed in args.seeds],
        "policies": policies,
        "contract": contracts,
        "calibration": calibration,
        "elapsed_s": round(time.time() - started, 1),
        "notes": [
            "The row slice defaults to the first rows, which the v3 resume run "
            "did not train on (it took the tail).",
            "Calibration uses the initial state and the actual first team; the "
            "value is negated for enemy-first rows. Draws are excluded.",
            "Simulation only. This is not live combat evidence.",
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print_summary(report)
    print(f"wrote {out}")
    return 0


def _winner_for_calibration(cfg: Config, state0: Dict[str, jax.Array],
                            tables: Dict[str, jax.Array],
                            checkpoint: Checkpoint, seed: int) -> np.ndarray:
    length = 2 * cfg.max_rounds
    rollout = jax.jit(make_eval_rollout(cfg, length, opponent="heuristic",
                                        player="learned"))
    final = rollout(checkpoint.params, state0, tables, jax.random.PRNGKey(int(seed)))
    return np.asarray(final["winner"]).reshape(-1)


def print_summary(report: Dict[str, Any]) -> None:
    print(f"data={report['data']} slice={report['row_slice']} "
          f"seeds={report['seeds']}")
    print(f"{'policy':<16} {'n':>5} {'win':>6} {'enemy':>6} {'draw':>6} "
          f"{'rounds':>7} {'pHP':>6} {'eHP':>6}")
    for name, result in report["policies"].items():
        value = result["overall"]
        print(f"{name:<16} {value['n']:>5} {value['player_win']:>6.3f} "
              f"{value['enemy_win']:>6.3f} {value['draw']:>6.3f} "
              f"{value['rounds']:>7.2f} {value['player_hp_left']:>6.3f} "
              f"{value['enemy_hp_left']:>6.3f}")
    if report["calibration"]:
        print("\ncalibration (held-out splits):")
        print(f"{'policy':<16} {'raw ECE':>8} {'platt ECE':>10} "
              f"{'analytic ECE':>13} {'blend ECE':>10} {'raw AUC':>8} "
              f"{'platt AUC':>10} {'blend AUC':>9}")
        for name, result in report["calibration"].items():
            raw = result["raw"]
            platt = result["platt"]
            analytic = result.get("analytic") or {}
            blend = result.get("blend") or {}
            print(f"{name:<16} {raw['ece']:>8.4f} {platt['ece']:>10.4f} "
                  f"{analytic.get('ece', float('nan')):>13.4f} "
                  f"{blend.get('ece', float('nan')):>10.4f} "
                  f"{raw['auc']:>8.4f} {platt['auc']:>10.4f} "
                  f"{blend.get('auc', float('nan')):>9.4f}")
    for name, checks in report["contract"].items():
        failed = [key for key, ok in checks.items() if not ok]
        print(f"contract {name}: {'OK' if not failed else 'FAILED ' + ', '.join(failed)}")


if __name__ == "__main__":
    raise SystemExit(main())
