"""Side-swapped cross-play: judge a policy from both seats on the same board.

Two asymmetries in a duel harness have nothing to do with decisions: the build and the
seat. `normal_build_duel` measured that one build pair decided every game (100% side 2,
identical 6.0 mean turns, random or solver alike), and that even a mirror left a seat
effect (the solver won 20/20 from seat 1 and 0/20 from seat 2). A promotion judged on
one assignment can therefore reward a seat, not a policy.

This module plays every board twice with the two policies swapped between seats. Both
halves rebuild the board from the same seed and start their decision rng from the same
seed, so the only difference is which policy sits where. The verdict:

* `seat_dominated` -- one seat wins at least `SEAT_DOMINANCE` of the decided games
  regardless of the policy; this harness cannot judge a decision on these builds;
* `a_better` / `b_better` -- a two-sided exact binomial test on the paired games is
  below `ALPHA` and the winner leads from *both* seats;
* `seat_dependent_edge` -- the test favours one policy but it leads from one seat only;
* `no_measurable_difference` otherwise.

This verifies acting policies (solver, random, two-ply, value/residual with a model),
not the p_win calibrator: that head is verified by calibration on out-of-time fights.

    python -m combat.side_swap --a solver --b random --games 10 --mirror dana_ouro
    python -m combat.side_swap --a solver --b solver --games 10     # symmetry check
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import random
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from combat.battle_solver import BattleSolver
from combat.duel import (DuelState, load_map, make_residual_policy,
                         make_two_ply_policy, play_game, random_policy,
                         solver_policy, value_policy)
from combat.normal_build_duel import DEFAULT_MAP, make_normal_duel

SEAT_DOMINANCE = 0.8
ALPHA = 0.05

Policy = Callable[..., Any]


def _load_model(spec: str) -> Any:
    path = Path(spec.split(":", 1)[1])
    return pickle.loads(path.read_bytes())["model"]


def resolve(spec: str, solver: BattleSolver) -> Policy:
    """A policy bound to its own model, so two specs can differ inside one game."""
    if spec == "solver":
        return solver_policy
    if spec == "random":
        return random_policy
    if spec == "two-ply":
        return make_two_ply_policy(solver, opponent_k=3)
    if spec.startswith("value:"):
        model = _load_model(spec)
        return lambda state, actor, plans, rng, _m=None: value_policy(
            state, actor, plans, rng, model)
    if spec.startswith("residual:"):
        return make_residual_policy(_load_model(spec))
    raise SystemExit("unknown policy %r: use solver, random, two-ply, value:<path> "
                     "or residual:<path>" % spec)


def binomial_two_sided(wins: int, losses: int) -> float:
    """Exact two-sided sign test, capped at 1."""
    n = wins + losses
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(0, min(wins, losses) + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def score_games(games: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    decided = [game for game in games if game.get("winner") in ("a", "b")]
    n = len(decided)
    if n == 0:
        return {"games": len(games), "decided": 0, "seat1_win": None,
                "a_seat1_win": None, "a_seat2_win": None,
                "wins_a": 0, "wins_b": 0, "p_value": None,
                "verdict": "no_decided_game"}
    seat1 = sum(1 for game in decided if game["winner_seat"] == 1) / n

    def rate(seat: int) -> Optional[float]:
        rows = [game for game in decided if game["a_seat"] == seat]
        if not rows:
            return None
        return sum(1 for game in rows if game["winner"] == "a") / len(rows)

    a_seat1, a_seat2 = rate(1), rate(2)
    wins_a = sum(1 for game in decided if game["winner"] == "a")
    wins_b = n - wins_a
    p_value = binomial_two_sided(wins_a, wins_b)
    verdict = "no_measurable_difference"
    if max(seat1, 1.0 - seat1) >= SEAT_DOMINANCE:
        verdict = "seat_dominated"
    elif p_value < ALPHA and a_seat1 is not None and a_seat2 is not None:
        if wins_a > wins_b and a_seat1 > 0.5 and a_seat2 > 0.5:
            verdict = "a_better"
        elif wins_b > wins_a and (1.0 - a_seat1) > 0.5 and (1.0 - a_seat2) > 0.5:
            verdict = "b_better"
        else:
            verdict = "seat_dependent_edge"
    return {"games": len(games), "decided": n,
            "seat1_win": round(seat1, 4),
            "a_seat1_win": None if a_seat1 is None else round(a_seat1, 4),
            "a_seat2_win": None if a_seat2 is None else round(a_seat2, 4),
            "wins_a": wins_a, "wins_b": wins_b,
            "p_value": round(p_value, 4), "verdict": verdict}


def play_side_swapped(spec_a: str, spec_b: str, games: int = 20, seed: int = 0,
                      rounds: int = 16, map_path: Optional[Path] = None,
                      mirror_key: str = "dana_ouro",
                      solver: Optional[BattleSolver] = None) -> Dict[str, Any]:
    cells = load_map(Path(map_path or DEFAULT_MAP))
    solver = solver or BattleSolver(beam_width=16, max_actions=5)
    policy_a = resolve(spec_a, solver)
    policy_b = resolve(spec_b, solver)
    played: List[Dict[str, Any]] = []
    for index in range(games):
        board_seed = seed + index
        for half in (0, 1):
            state = make_normal_duel(cells, random.Random(board_seed),
                                     max_rounds=rounds, mirror_key=mirror_key)
            if state is None:
                continue
            a_seat = 1 if half == 0 else 2
            seats: Dict[int, Policy] = ({1: policy_a, 2: policy_b} if half == 0
                                        else {1: policy_b, 2: policy_a})
            final, _records = play_game(state, seats, solver,
                                        random.Random(board_seed), capture=False)
            winner_seat = final.winner
            winner = ("a" if winner_seat == a_seat else "b"
                      if winner_seat in (1, 2) else "draw")
            played.append({"seed": board_seed, "half": half, "a_seat": a_seat,
                           "winner": winner, "winner_seat": winner_seat,
                           "turns": final.turns})
    score = score_games(played)
    return {"a": spec_a, "b": spec_b, "mirror": mirror_key, "seed": seed,
            "rounds": rounds, "score": score, "games": played}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", default="solver")
    parser.add_argument("--b", default="random")
    parser.add_argument("--games", type=int, default=20,
                        help="boards played; each is played twice, seats swapped")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--rounds", type=int, default=16)
    parser.add_argument("--mirror", default="dana_ouro",
                        help="dana_ouro or stuff_terre_pp (the same build both sides)")
    parser.add_argument("--map", default=str(DEFAULT_MAP))
    parser.add_argument("--out", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def run(argv: Optional[Sequence[str]] = None) -> int:
    import sys
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    started = time.time()
    report = play_side_swapped(args.a, args.b, games=args.games, seed=args.seed,
                               rounds=args.rounds, map_path=Path(args.map),
                               mirror_key=args.mirror)
    report["elapsed_s"] = round(time.time() - started, 1)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps({k: v for k, v in report.items() if k != "games"},
                         indent=1, sort_keys=True))
    else:
        score = report["score"]
        print("%s vs %s on %s, seats swapped (%d boards -> %d games, %.1fs)"
              % (args.a, args.b, args.mirror, args.games, score["games"],
                 report["elapsed_s"]))
        print("  a as seat 1: %s   a as seat 2: %s   seat 1 wins: %s"
              % (score["a_seat1_win"], score["a_seat2_win"], score["seat1_win"]))
        print("  a %d - b %d, two-sided p %s -> %s"
              % (score["wins_a"], score["wins_b"], score["p_value"],
                 score["verdict"]))
        if score["verdict"] == "seat_dominated":
            print("  this build pair cannot judge a decision: one seat wins "
                  "%.0f%% of the decided games regardless of policy"
                  % (100.0 * max(score["seat1_win"], 1.0 - score["seat1_win"])))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
