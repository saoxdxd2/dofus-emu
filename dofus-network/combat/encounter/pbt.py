"""Generational PPO / population-based training for the combat policy.

PPO is the inner learner; the outer loop keeps a population of short PPO
generations, evaluates each on a disjoint held-out slice, and promotes the
best checkpoint. This keeps gradients (and the value head) while adding the
exploration/diversity the pure-ES run lacked.

Each candidate is a normal PPO segment starting from the current best
checkpoint, with a different seed and opponent mix. The best held-out win
promotes; otherwise the incumbent stays. No candidate is deleted.
"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from pathlib import Path
from typing import Any, Dict

import jax
import numpy as np

from combat.encounter import train as trainer
from combat.encounter.env import Config, initial_state
from combat.encounter.data import load_params, load_slice
from combat.encounter.train import make_eval_rollout

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA = REPO_ROOT / "kaggle" / "tpu_selfplay" / "dataset" / "encounters.npz"
DEFAULT_INIT = REPO_ROOT / "models" / "encounter_selfplay.pkl"


def eval_checkpoint(path: Path, data: Path, samples: int, max_rounds: int,
                    seed: int = 1) -> Dict[str, float]:
    cfg = Config(max_rounds=max_rounds)
    tables, spec = load_slice(Path(data), 0, samples)
    state0 = initial_state(spec, tables, cfg)
    params = load_params(Path(path))
    rollout = jax.jit(make_eval_rollout(cfg, 2 * cfg.max_rounds,
                                       opponent="heuristic"))
    final = rollout(params, state0, tables,
                    jax.random.PRNGKey(seed))
    winner = np.asarray(final["winner"])
    return {
        "player_win": float((winner == 0).mean()),
        "enemy_win": float((winner == 1).mean()),
        "draw": float((winner == 2).mean()),
        "rounds": float(np.asarray(final["round"]).mean()),
        "samples": int(len(winner)),
    }


def train_candidate(args: argparse.Namespace, init: Path, out: Path,
                    opponent: str, seed: int) -> Dict[str, Any]:
    argv = [
        "--data", str(args.data),
        "--out", str(out),
        "--init", str(init),
        "--samples", str(args.samples),
        "--iters", str(args.iters),
        "--epochs", "2",
        "--max-rounds", str(args.max_rounds),
        "--opponent", opponent,
        "--eval-every", "40",
        "--save-every", "0",
        "--seed", str(seed),
    ]
    parsed = trainer.build_parser().parse_args(argv)
    payload = trainer.train(parsed)
    return payload


def run(args: argparse.Namespace) -> int:
    started = time.time()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics: Dict[str, Any] = {
        "method": "generational-ppo-pbt",
        "init": str(args.init),
        "rounds": [],
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    best_path = Path(args.init)
    best_eval = eval_checkpoint(best_path, Path(args.data), args.eval_samples,
                                args.max_rounds, seed=args.seed)
    print("incumbent", json.dumps(best_eval, sort_keys=True))
    best_win = best_eval["player_win"]

    for round_index in range(1, args.rounds + 1):
        candidates = []
        for candidate in range(args.candidates):
            # Alternate heuristic and self-play across the population; the
            # candidate still starts from the incumbent, not from scratch.
            opponent = "self" if (round_index + candidate) % 2 == 0 else "heuristic"
            path = out_dir / f"round{round_index}_cand{candidate}.pkl"
            seed = args.seed + round_index * 17 + candidate * 101
            print(f"[pbt] round {round_index} candidate {candidate} "
                  f"opponent={opponent} seed={seed}")
            payload = train_candidate(args, best_path, path, opponent, seed)
            result = eval_checkpoint(path, Path(args.data), args.eval_samples,
                                     args.max_rounds, seed=args.seed)
            last = (payload.get("metrics") or [{}])[-1]
            candidates.append({
                "candidate": candidate,
                "path": str(path),
                "opponent": opponent,
                "seed": seed,
                "eval": result,
                "last_train": last,
            })
            print(f"[pbt] candidate {candidate}: "
                  f"held-out {result['player_win']:.3f}")
        winner = max(candidates, key=lambda row: row["eval"]["player_win"])
        promoted = winner["eval"]["player_win"] > best_win + 1e-4
        if promoted:
            best_path = Path(winner["path"])
            best_win = winner["eval"]["player_win"]
        metrics["rounds"].append({
            "round": round_index,
            "candidates": candidates,
            "promoted": promoted,
            "best_path": str(best_path),
            "best_win": best_win,
        })
        (out_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=1), encoding="utf-8")
        print(f"[pbt] round {round_index}: promoted={promoted} "
              f"best={best_win:.3f} path={best_path}")

    final_path = Path(args.out)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    with Path(best_path).open("rb") as src:
        payload = pickle.loads(src.read())
    with final_path.open("wb") as dst:
        pickle.dump(payload, dst)
    metrics["final"] = {"path": str(final_path), "held_out": best_win,
                        "elapsed_s": round(time.time() - started, 1)}
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1),
                                          encoding="utf-8")
    print(json.dumps(metrics["final"], indent=1))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default=str(DEFAULT_DATA))
    p.add_argument("--init", default=str(DEFAULT_INIT))
    p.add_argument("--out-dir", default=str(REPO_ROOT / "models" / "pbt"))
    p.add_argument("--out", default=str(REPO_ROOT / "models" / "encounter_pbt.pkl"))
    p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--candidates", type=int, default=2)
    p.add_argument("--iters", type=int, default=120)
    p.add_argument("--samples", type=int, default=4096)
    p.add_argument("--eval-samples", type=int, default=1024)
    p.add_argument("--max-rounds", type=int, default=24)
    p.add_argument("--seed", type=int, default=7)
    return p


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
