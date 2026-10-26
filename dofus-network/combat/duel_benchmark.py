"""Train and benchmark the v4.0 1v1 duel value lookahead.

The solver supplies legal plans. A gradient-boosted state value learns from
mixed-policy games whether the acting fighter eventually wins. At inference,
the value policy applies each candidate plan to a copy of the duel and picks
the resulting state with the highest predicted win probability.

This is a measurement harness first: train/eval rows are disjoint, games are
seeded, and the value policy is compared with the solver and random baselines.
"""

from __future__ import annotations

import argparse
import json
import pickle
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from combat.cli import run_main
from combat.battle_solver import BattleSolver
from combat.metrics import auc as _auc, ece as _ece
from combat.duel import (FEATURE_NAMES, load_map,
                         make_duel, make_two_ply_policy, play_game,
                         random_policy, solver_policy, value_policy)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = REPO_ROOT / "kaggle" / "tpu_selfplay" / "dataset" / "encounters.npz"
DEFAULT_MODEL = REPO_ROOT / "models" / "duel_value_v4_0.pkl"
DEFAULT_OUT = REPO_ROOT / "benchmarks" / "duel_v4_0.json"


def load_data(path: Path):
    data = np.load(path, allow_pickle=True)
    levels = np.asarray(data["level"])[:, 0]
    return data, levels


def sample_pair(levels: np.ndarray, rows: Sequence[int],
                rng: random.Random, max_level_gap: int = 5
                ) -> Tuple[int, int]:
    for _ in range(50):
        a = int(rng.choice(rows))
        b = int(rng.choice(rows))
        if a == b:
            continue
        if abs(float(levels[a]) - float(levels[b])) <= max_level_gap:
            return a, b
    return a, b


def _brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def fit_value(records: List[Dict[str, Any]], seed: int = 7):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import train_test_split

    labelled = [row for row in records if row.get("label") is not None]
    X = np.asarray([row["post_features"] for row in labelled], dtype=float)
    y = np.asarray([row["label"] for row in labelled], dtype=float)
    games = np.asarray([row["game"] for row in labelled], dtype=int)
    if len(y) < 50 or len(set(y.tolist())) < 2:
        raise SystemExit(f"not enough labelled states for a value model: {len(y)}")
    unique_games = np.unique(games)
    train_games, val_games = train_test_split(
        unique_games, test_size=0.2, random_state=seed)
    train_idx = np.flatnonzero(np.isin(games, train_games))
    val_idx = np.flatnonzero(np.isin(games, val_games))
    model = HistGradientBoostingClassifier(
        max_iter=250, learning_rate=0.06, max_leaf_nodes=31,
        min_samples_leaf=20, l2_regularization=1.0, random_state=seed)
    model.fit(X[train_idx], y[train_idx])
    val_p = model.predict_proba(X[val_idx])[:, 1]
    metrics = {
        "n_states": int(len(y)),
        "train_states": int(len(train_idx)),
        "val_states": int(len(val_idx)),
        "train_games": int(len(train_games)),
        "val_games": int(len(val_games)),
        "val_auc": _auc(y[val_idx], val_p),
        "val_brier": _brier(y[val_idx], val_p),
        "val_accuracy": float(((val_p >= 0.5).astype(float)
                               == (y[val_idx] >= 0.5).astype(float)).mean()),
    }
    model.fit(X, y)
    return model, metrics


def generate_games(data, levels, cells, rows: Sequence[int], games: int,
                   solver: BattleSolver, rng: random.Random,
                   solver_prob: float = 0.65, max_rounds: int = 12
                   ) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    games_done = 0
    attempts = 0
    while games_done < games and attempts < games * 5:
        attempts += 1
        row_a, row_b = sample_pair(levels, rows, rng)
        state = make_duel(data, row_a, row_b, cells, rng, max_rounds=max_rounds)
        if state is None:
            continue
        policies = {}
        for ident in (1, 2):
            policies[ident] = (solver_policy if rng.random() < solver_prob
                               else random_policy)
        state, turns = play_game(state, policies, solver, rng, capture=True)
        winner = state.winner
        for turn in turns:
            actor = int(turn["actor"])
            label = None if winner is None else float(winner == actor)
            records.append({
                **turn,
                "winner": winner,
                "label": label,
                "game": games_done,
                "row_a": row_a,
                "row_b": row_b,
            })
        games_done += 1
    return records


def evaluate_matchup(name: str, data, levels, cells, rows, games: int,
                     solver: BattleSolver, rng: random.Random,
                     model, policy_a, policy_b, capture: bool = False
                     ) -> Dict[str, Any]:
    outcomes = []
    turns_count = []
    calibration = []
    games_done = 0
    attempts = 0
    while games_done < games and attempts < games * 5:
        attempts += 1
        row_a, row_b = sample_pair(levels, rows, rng)
        state = make_duel(data, row_a, row_b, cells, rng)
        if state is None:
            continue
        policies = {1: policy_a, 2: policy_b}
        state, turns = play_game(state, policies, solver, rng, model=model,
                                 capture=capture)
        winner = state.winner
        outcomes.append(0 if winner == 1 else (1 if winner == 2 else 2))
        turns_count.append(state.turns)
        if capture and model is not None:
            labelled = [
                (float(winner == turn["actor"]), turn["post_features"])
                for turn in turns if winner is not None
            ]
            if labelled:
                probabilities = model.predict_proba(
                    [row for _label, row in labelled])[:, 1]
                calibration.extend(
                    (label, float(probability))
                    for (label, _row), probability
                    in zip(labelled, probabilities))
        games_done += 1
    wins = outcomes.count(0)
    losses = outcomes.count(1)
    draws = outcomes.count(2)
    result = {
        "games": len(outcomes),
        "side1_win": wins / max(1, len(outcomes)),
        "side2_win": losses / max(1, len(outcomes)),
        "draw": draws / max(1, len(outcomes)),
        "mean_turns": float(np.mean(turns_count)) if turns_count else 0.0,
    }
    if calibration:
        y = np.asarray([item[0] for item in calibration], dtype=float)
        p = np.asarray([item[1] for item in calibration], dtype=float)
        result["value_calibration"] = {
            "n": int(len(y)),
            "auc": _auc(y, p),
            "brier": _brier(y, p),
            "ece": _ece(y, p),
        }
    return result


def run(args: argparse.Namespace) -> int:
    started = time.time()
    data, levels = load_data(Path(args.data))
    cells = load_map(Path(args.map))
    solver = BattleSolver(beam_width=args.beam_width,
                          max_actions=args.max_actions)
    rng = random.Random(args.seed)
    n = int(data["hp"].shape[0])
    train_rows = list(range(args.train_start,
                            min(n, args.train_start + args.train_rows)))
    eval_rows = list(range(args.eval_start,
                           min(n, args.eval_start + args.eval_rows)))
    if not eval_rows:
        eval_rows = train_rows

    print(f"generating {args.train_games} training games...")
    records = generate_games(data, levels, cells, train_rows,
                             args.train_games, solver, rng,
                             solver_prob=args.solver_prob,
                             max_rounds=args.max_rounds)
    labelled = [row for row in records if row.get("label") is not None]
    print(f"states {len(records)}, labelled {len(labelled)}")
    model, train_metrics = fit_value(labelled, seed=args.seed)
    print("value model:", json.dumps(train_metrics, sort_keys=True))

    two_ply = make_two_ply_policy(solver, opponent_k=args.max_actions)
    matchups = {}
    for name, pol_a, pol_b, capture in [
        ("value_vs_solver", value_policy, solver_policy, True),
        ("solver_vs_value", solver_policy, value_policy, True),
        ("two_ply_vs_solver", two_ply, solver_policy, False),
        ("solver_vs_two_ply", solver_policy, two_ply, False),
        ("two_ply_vs_random", two_ply, random_policy, False),
        ("value_vs_random", value_policy, random_policy, False),
        ("solver_vs_random", solver_policy, random_policy, False),
        ("solver_vs_solver", solver_policy, solver_policy, False),
        ("random_vs_random", random_policy, random_policy, False),
    ]:
        matchups[name] = evaluate_matchup(
            name, data, levels, cells, eval_rows, args.eval_games, solver,
            random.Random(args.seed + hash(name) % 100000), model,
            pol_a, pol_b, capture=capture)
        print(name, json.dumps(matchups[name], sort_keys=True))

    model_path = Path(args.model_out)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    with model_path.open("wb") as handle:
        pickle.dump({"model": model, "features": FEATURE_NAMES,
                     "train": train_metrics}, handle)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data": str(args.data),
        "rows": {"train": [args.train_start,
                           args.train_start + len(train_rows)],
                 "eval": [args.eval_start,
                          args.eval_start + len(eval_rows)]},
        "games": {"train": args.train_games, "eval_per_matchup": args.eval_games},
        "value": train_metrics,
        "matchups": matchups,
        "elapsed_s": round(time.time() - started, 1),
        "notes": [
            "1v1 initiative-ordered duel on real cells; direct damage/steal/heal only.",
            "Value = gradient-boosted state win probability; lookahead over solver plans.",
            "Train and eval rows are disjoint.",
            "This is v4.0, not real-player PvP evidence yet.",
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"wrote {out} and {model_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default=str(DEFAULT_DATA))
    p.add_argument("--map", default=str(REPO_ROOT / "data" / "maps" / "100139008.json"))
    p.add_argument("--train-start", type=int, default=0)
    p.add_argument("--train-rows", type=int, default=4096)
    p.add_argument("--eval-start", type=int, default=4096)
    p.add_argument("--eval-rows", type=int, default=2048)
    p.add_argument("--train-games", type=int, default=600)
    p.add_argument("--eval-games", type=int, default=80)
    p.add_argument("--solver-prob", type=float, default=0.65)
    p.add_argument("--beam-width", type=int, default=8)
    p.add_argument("--max-actions", type=int, default=2)
    p.add_argument("--max-rounds", type=int, default=12)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--model-out", default=str(DEFAULT_MODEL))
    p.add_argument("--out", default=str(DEFAULT_OUT))
    return p


def main(argv=None):
    return run_main(build_parser, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
