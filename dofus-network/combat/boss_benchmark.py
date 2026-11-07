"""Measure whether the greedy solver can beat a boss, and what a threat-aware plan adds.

`normal_build_duel.py` pits two level-200 player builds against each other, where a
greedy maximum-damage turn is close to optimal because both sides trade the same way.
A boss breaks that: one melee bruiser that hits for more than a normal build's health
in two turns cannot be out-traded, only kited, and two builds with the same solver will
differ entirely in whether they end the turn inside its reach.

The player is a real normal build (`dana_ouro`, 12/6/6). The boss is a synthetic
archetype -- a melee bruiser and a ranged artillery -- built from the same `Duelist`
the duels use, with one spell each. Two policies run on the player's side:

    solver    plans[0], the greedy maximum-score turn the live path starts from
    two_ply   apply the plan, let the boss take its greedy reply, rank by the HP
              position that results (`duel.make_two_ply_policy`)

The boss always plays `solver` (greedy), which is the assumption: an opponent that
plays the obvious best turn. Win rates are per scenario over seeded games; games that
hit the round cap count as losses for the player because a boss that cannot be killed
inside the cap is a failed fight.

    python -m combat.boss_benchmark --games 20
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from combat.battle_solver import BattleSolver
from combat.cli import run_main
from combat.duel import (DuelState, Duelist, engaged_cells, live_spell,
                         load_map, make_two_ply_policy, play_game,
                         solver_policy, value_policy)
from combat.encounter.build_dataset import load_class_loadouts
from combat.normal_build_duel import build_duelist

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAP = REPO_ROOT / "data" / "maps" / "100139008.json"
DEFAULT_OUT = REPO_ROOT / "benchmarks" / "boss_archetypes.json"

BOSSES: Dict[str, Dict[str, Any]] = {
    # Hits for ~1200 on 4 AP at range 1: a normal build at ~4800 HP dies in two
    # turns inside reach, so the fight is only winnable without giving it a turn
    # in range. This is the archetype the "it just fights forward" report breaks on.
    "melee_bruiser": {"hp": 4200, "ap": 10, "mp": 4, "initiative": 2200,
                      "spell": {"ap": 4, "rmin": 1, "rmax": 1, "damage": 1600}},
    # Deals ~700 at range 8: closing in is correct here, and a policy that always
    # retreats would lose. Kept so a kiting rule cannot pass the benchmark by
    # learning one direction.
    "ranged_artillery": {"hp": 4200, "ap": 10, "mp": 4, "initiative": 2200,
                         "spell": {"ap": 4, "rmin": 4, "rmax": 8, "damage": 650}},
}


def make_boss(name: str, cell: int, ident: int = 2) -> Optional[Duelist]:
    spec = BOSSES.get(name)
    if spec is None:
        return None
    spell = spec["spell"]
    return Duelist(
        id=ident, row=0, class_name="Iop", level=200,
        hp=float(spec["hp"]), max_hp=float(spec["hp"]),
        ap=float(spec["ap"]), max_ap=float(spec["ap"]),
        mp=float(spec["mp"]), max_mp=float(spec["mp"]),
        initiative=float(spec["initiative"]), cell=cell,
        resist=(), resist_fixed=(),
        spells=(live_spell(0, spell["ap"], spell["rmin"], spell["rmax"],
                           spell["damage"], 0, 1, False),),
        stats={},
    )


def make_state(cells, rng: random.Random, boss: str,
               max_rounds: int = 16) -> Optional[DuelState]:
    pair = engaged_cells(cells, rng)
    if pair is None:
        return None
    payload = load_class_loadouts(200)
    player = build_duelist("dana_ouro", pair[0], 1, payload)
    enemy = make_boss(boss, pair[1])
    if player is None or enemy is None:
        return None
    order = ((player.id, enemy.id) if player.initiative >= enemy.initiative
             else (enemy.id, player.id))
    return DuelState(fighters=(player, enemy), cells=cells, order=order,
                     max_rounds=max_rounds)


def run_matchup(cells, solver: BattleSolver, boss: str, policy_name: str,
                games: int, seed: int, model=None) -> Dict[str, Any]:
    two_ply = make_two_ply_policy(solver, opponent_k=3)
    if policy_name == "two_ply":
        player_policy = two_ply
    elif policy_name == "value":
        if model is None:
            return {"boss": boss, "policy": policy_name, "games": 0, "wins": 0,
                    "win_rate": None, "mean_turns": None, "mean_hp_left": None,
                    "error": "no value model"}
        def player_policy(state, actor, plans, rng, _model=None):
            return value_policy(state, actor, plans, rng, model)
    else:
        player_policy = solver_policy
    wins = 0
    rounds: List[int] = []
    hp_left: List[float] = []
    played = 0
    for index in range(games):
        rng = random.Random(seed + index * 7919)
        state = make_state(cells, rng, boss)
        if state is None:
            continue
        played += 1
        policies = {1: player_policy, 2: solver_policy}
        state, _records = play_game(state, policies, solver, rng, k=6)
        player = state.by_id(1)
        won = state.winner == 1 and player.alive
        wins += 1 if won else 0
        rounds.append(state.turns)
        hp_left.append(max(0.0, player.hp) / max(1.0, player.max_hp))
    return {
        "boss": boss,
        "policy": policy_name,
        "games": played,
        "wins": wins,
        "win_rate": round(wins / played, 3) if played else None,
        "mean_turns": round(sum(rounds) / len(rounds), 2) if rounds else None,
        "mean_hp_left": round(sum(hp_left) / len(hp_left), 3) if hp_left else None,
    }


def run(args: argparse.Namespace) -> int:
    started = time.time()
    cells = load_map(Path(args.map))
    solver = BattleSolver(beam_width=args.beam_width,
                          max_actions=args.max_actions)
    model = None
    if not args.no_value and Path(args.value).exists():
        import pickle
        try:
            model = pickle.loads(Path(args.value).read_bytes())["model"]
        except Exception as exc:  # noqa: BLE001
            print("value model could not be loaded: %s" % exc)
    rows: List[Dict[str, Any]] = []
    policy_names = ["solver", "two_ply"] + (["value"] if model is not None else [])
    for boss in ([args.boss] if args.boss else list(BOSSES)):
        for policy in policy_names:
            row = run_matchup(cells, solver, boss, policy, args.games, args.seed,
                              model=model)
            rows.append(row)
            print("%-18s %-8s wins %2d/%-2d (%s) turns %s hp_left %s"
                  % (boss, policy, row["wins"], row["games"], row["win_rate"],
                     row["mean_turns"], row["mean_hp_left"]))
    verdict: Dict[str, Any] = {"matchups": rows,
                               "seconds": round(time.time() - started, 1)}
    by_key = {(row["boss"], row["policy"]): row for row in rows}
    for boss in {row["boss"] for row in rows}:
        greedy = by_key.get((boss, "solver"), {}).get("win_rate")
        threat = by_key.get((boss, "two_ply"), {}).get("win_rate")
        value = by_key.get((boss, "value"), {}).get("win_rate")
        verdict["delta_" + boss] = (None if greedy is None or threat is None
                                    else round(threat - greedy, 3))
        verdict["delta_value_" + boss] = (None if greedy is None or value is None
                                          else round(value - greedy, 3))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    print("wrote " + str(out))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261028)
    parser.add_argument("--boss", default=None, choices=sorted(BOSSES))
    parser.add_argument("--map", default=str(DEFAULT_MAP))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--value", default=str(REPO_ROOT / "models" / "duel_value_v4_0.pkl"))
    parser.add_argument("--no-value", dest="no_value", action="store_true")
    parser.add_argument("--beam-width", dest="beam_width", type=int, default=12)
    parser.add_argument("--max-actions", dest="max_actions", type=int, default=3)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    return run_main(build_parser, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
