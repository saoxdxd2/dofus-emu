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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from combat.battle_solver import BattleSolver
from combat.duel import (DuelState, board_for, load_map,
                         make_residual_policy, make_two_ply_policy,
                         play_game, random_policy, solver_policy,
                         value_policy)
from combat.normal_build_duel import DEFAULT_MAP, make_normal_duel
import logging
logger = logging.getLogger(__name__)

SEAT_DOMINANCE = 0.8
ALPHA = 0.05

Policy = Callable[..., Any]


def _load_model(spec: str) -> Any:
    path = Path(spec.split(":", 1)[1])
    return pickle.loads(path.read_bytes())["model"]


def value_head_spread(model: Any, solver: BattleSolver, cells: Any,
                      mirror_key: str, boards: int = 6) -> Optional[float]:
    """Median spread of the value head's predictions across candidate plans.

    A learned ranker that returns the same probability for every candidate is not
    a weak ranker, it is an inert one: `duel.plan_scores` adds the solver's score
    as a 1e-4 tiebreak, so a constant head reproduces `solver_policy` bit for bit
    and any "value vs solver" comparison then measures the solver against itself.
    Measured on `models/duel_value_v4_0.pkl` over real level-200 builds: spread
    exactly 0.0 on 12 of 12 boards, because that head was trained on the v3
    synthetic encounters and every real row lands in the same leaf. None when the
    head cannot be evaluated at all.
    """
    from combat.duel import apply_plan, lookahead_features, plans_for

    spreads: List[float] = []
    for index in range(boards):
        state = make_normal_duel(cells, random.Random(index), mirror_key=mirror_key)
        if state is None:
            continue
        actor = state.actor
        rows = []
        for plan in plans_for(state, solver, 6):
            clone = state.clone()
            apply_plan(clone, plan)
            if not clone.done:
                rows.append(lookahead_features(clone, actor))
        if len(rows) < 2:
            continue
        try:
            predicted = [float(row[1]) for row in model.predict_proba(rows)]
        except Exception:  # noqa: BLE001 - an inert head is the finding, not a crash
            return None
        spreads.append(max(predicted) - min(predicted))
    if not spreads:
        return None
    return round(float(sorted(spreads)[len(spreads) // 2]), 6)


def make_hp_policy() -> Policy:
    """The one-ply HP-margin re-rank: `LivePolicy._hp_choice` before module 1.

    This is the incumbent the analytic reply model has to beat, on the same
    candidate plans, so the comparison isolates the reply term and nothing
    else (same solver, same beam, same boards, same seeds).
    """
    stats = {"decisions": 0, "changed_vs_solver": 0}

    def policy(state: DuelState, actor: Any, plans: Sequence[Any],
               rng: random.Random, model: Any = None) -> Any:
        stats["decisions"] += 1
        if len(plans) < 2:
            return plans[0]

        def margin(plan: Any) -> float:
            mine = theirs = 0.0
            for fighter in state.fighters:
                hp = float(plan.final_hp.get(fighter.id, fighter.hp))
                share = max(0.0, min(1.0, hp / max(1.0, fighter.max_hp)))
                if fighter.id == actor.id:
                    mine += share
                else:
                    theirs += share
            return mine - theirs

        picked = max(plans, key=margin)
        if picked is not plans[0]:
            stats["changed_vs_solver"] += 1
        return picked
    policy.stats = stats  # type: ignore[attr-defined]
    return policy


def make_hp2_policy(solver: BattleSolver) -> Policy:
    """The analytic worst-case-reply re-rank (`evaluate_two_ply`).

    Carries a `timing` dict so the harness can report the mean milliseconds
    the ranking itself costs per decision -- the p95 decide budget is a gate,
    and a ranker that wins games but blows the budget is not a promotion.
    """
    timing = {"seconds": 0.0, "calls": 0}
    stats = {"decisions": 0, "changed_vs_solver": 0}

    def policy(state: DuelState, actor: Any, plans: Sequence[Any],
               rng: random.Random, model: Any = None) -> Any:
        stats["decisions"] += 1
        if len(plans) < 2:
            return plans[0]
        started = time.perf_counter()
        ranked = solver.evaluate_two_ply(board_for(state, actor), plans,
                                         top_k=6)
        timing["seconds"] += time.perf_counter() - started
        timing["calls"] += 1
        picked = ranked[0][0] if ranked else plans[0]
        if picked is not plans[0]:
            stats["changed_vs_solver"] += 1
        return picked

    policy.timing = timing  # type: ignore[attr-defined]
    policy.stats = stats  # type: ignore[attr-defined]
    return policy


def make_live_policy(checkpoint: Optional[Path] = None, beam_width: int = 8,
                     max_actions: int = 4) -> Policy:
    """The shipped `LivePolicy.decide` chain on the duel's own board.

    `Board.to_fight_state()` was written for this seam: the duel hands `decide()` the
    payload the page would send instead of a hand-rolled selector. The chain still
    answers with one action while a duel turn is a whole plan, so the action is matched
    to the candidate plan whose first action it is; an action no candidate expresses
    falls back to the solver's plan and is counted. The branch census comes from the
    chain's own `reason` field, so a run where `_value_choice` never fires is visible
    rather than assumed.
    """
    from combat.live_policy import LivePolicy

    live = LivePolicy(checkpoint=checkpoint, beam_width=beam_width,
                      max_actions=max_actions)
    stats = {"decisions": 0, "cast": 0, "move": 0, "pass": 0, "unmatched": 0,
             "changed_vs_solver": 0, "errors": 0, "seconds": 0.0, "reasons": {}}

    def policy(state: DuelState, actor: Any, plans: Sequence[Any],
               rng: random.Random, model: Any = None) -> Any:
        started = time.perf_counter()
        try:
            decision = live.decide(board_for(state, actor).to_fight_state())
        except Exception:  # noqa: BLE001 - an arm that crashes is still a datum
            stats["errors"] += 1
            stats["seconds"] += time.perf_counter() - started
            return plans[0]
        stats["seconds"] += time.perf_counter() - started
        stats["decisions"] += 1
        reason = str(decision.get("reason"))
        stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
        action = decision.get("action") or {}
        kind = action.get("type")
        stats[kind if kind in ("cast", "move", "pass") else "pass"] += 1
        picked = _plan_for_action(plans, action)
        if picked is None:
            stats["unmatched"] += 1
            picked = plans[0]
        elif picked is not plans[0]:
            stats["changed_vs_solver"] += 1
        return picked

    policy.stats = stats  # type: ignore[attr-defined]
    policy.live = live  # type: ignore[attr-defined]
    return policy


def _plan_for_action(plans: Sequence[Any], action: Dict[str, Any]) -> Optional[Any]:
    """The candidate whose first action is the live chain's answer, or None."""
    kind = action.get("type")
    spell_id = action.get("spellId")
    target_id = action.get("targetId")
    cell = action.get("cell")
    walk_first = None
    for plan in plans:
        if not plan.actions:
            continue
        first = plan.actions[0]
        if kind == "cast" and first.kind == "cast":
            if spell_id is not None and int(first.spell_id) != int(spell_id):
                continue
            if target_id is not None and first.target_id is not None \
                    and int(first.target_id) == int(target_id):
                return plan
            if cell is not None and first.target_cell is not None \
                    and int(first.target_cell) == int(cell):
                return plan
        elif kind == "move" and cell is not None:
            # `_emit` walks first only when the plan's cast starts elsewhere, and it
            # emits the pre-cast cell -- not the plan's final cell.
            if first.kind == "cast" and int(first.from_cell) == int(cell) \
                    and walk_first is None:
                walk_first = plan
            elif first.kind == "move" and int(plan.final_cell) == int(cell):
                return plan
        elif kind == "pass" and first.kind == "pass":
            return plan
    return walk_first


def resolve(spec: str, solver: BattleSolver) -> Policy:
    """A policy bound to its own model, so two specs can differ inside one game."""
    if spec == "solver":
        return solver_policy
    if spec == "random":
        return random_policy
    if spec == "two-ply":
        return make_two_ply_policy(solver, opponent_k=3)
    if spec == "hp":
        return make_hp_policy()
    if spec == "hp2":
        return make_hp2_policy(solver)
    if spec == "live" or spec.startswith("live:"):
        checkpoint = Path(spec.split(":", 1)[1]) if ":" in spec else None
        return make_live_policy(checkpoint)
    if spec == "live-wide":
        # Diagnostic only: the shipped chain's selectors on the harness's own
        # candidate width, so a difference against `hp2` cannot be the beam.
        return make_live_policy(None, beam_width=16, max_actions=5)
    if spec.startswith("value:"):
        model = _load_model(spec)
        return lambda state, actor, plans, rng, _m=None: value_policy(
            state, actor, plans, rng, model)
    if spec.startswith("residual:"):
        return make_residual_policy(_load_model(spec))
    raise SystemExit("unknown policy %r: use solver, random, two-ply, hp, hp2, live, "
                     "value:<path> or residual:<path>" % spec)


def binomial_two_sided(wins: int, losses: int) -> float:
    """Exact two-sided sign test, capped at 1."""
    n = wins + losses
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(0, min(wins, losses) + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def damage_split(final: Any, a_seat: int) -> Tuple[float, float]:
    """(damage dealt by a, damage dealt by b) in a finished duel.

    Damage dealt is the opponent's HP loss, read off the two fighters rather than
    accumulated per action, so it stays correct when a plan overkills: HP is
    clamped at zero and the excess is not counted, which is what "damage that
    mattered" means for efficiency.
    """
    by_seat = {int(fighter.id): fighter for fighter in final.fighters}
    b_seat = 3 - a_seat
    a_target = by_seat.get(b_seat)
    b_target = by_seat.get(a_seat)
    if a_target is None or b_target is None:
        return 0.0, 0.0
    damage_a = max(0.0, float(a_target.max_hp) - float(a_target.hp))
    damage_b = max(0.0, float(b_target.max_hp) - float(b_target.hp))
    return damage_a, damage_b


def efficiency(games: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Damage per turn per policy, paired by board.

    Win/loss saturates on these level-200 builds: seat 1 wins essentially every
    decided game regardless of policy (measured, `seat_dominated` at 1.0 for
    solver-vs-solver and two-ply-vs-solver), so the win column cannot rank two
    good policies. Damage per turn still separates them, because the seat that
    loses the game still deals damage before it dies, and the pairing means each
    policy is measured once from each seat on the same board.
    """
    rows = [game for game in games
            if game.get("turns") and game.get("damage_a") is not None]
    if not rows:
        return {"boards": 0, "a_damage_per_turn": None,
                "b_damage_per_turn": None, "diff": None, "ci95": None,
                "p_value": None,
                "boards_favouring_a": 0, "boards_favouring_b": 0}
    per_board: Dict[int, Dict[str, float]] = {}
    for game in rows:
        entry = per_board.setdefault(game["seed"], {"a": 0.0, "b": 0.0, "n": 0})
        turns = float(game["turns"])
        entry["a"] += float(game["damage_a"]) / turns
        entry["b"] += float(game["damage_b"]) / turns
        entry["n"] += 1
    diffs = []
    for entry in per_board.values():
        halves = max(1, entry["n"])
        diffs.append(entry["a"] / halves - entry["b"] / halves)
    favour_a = sum(1 for diff in diffs if diff > 0)
    favour_b = sum(1 for diff in diffs if diff < 0)
    mean_a = sum(entry["a"] / max(1, entry["n"]) for entry in per_board.values()) / len(per_board)
    mean_b = sum(entry["b"] / max(1, entry["n"]) for entry in per_board.values()) / len(per_board)
    # Paired percentile bootstrap over boards: the promotion gate is a CI on
    # the mean paired difference that excludes zero, not a point estimate.
    rng = random.Random(0)
    n = len(diffs)
    boots = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n
                   for _ in range(2000))
    return {
        "boards": len(per_board),
        "a_damage_per_turn": round(mean_a, 2),
        "b_damage_per_turn": round(mean_b, 2),
        "diff": round(mean_a - mean_b, 2),
        "ci95": [round(boots[49], 3), round(boots[1949], 3)],
        "p_value": round(binomial_two_sided(favour_a, favour_b), 4),
        "boards_favouring_a": favour_a,
        "boards_favouring_b": favour_b,
    }


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
    # A `value:`/`residual:` arm is only a different policy if its head can tell
    # two candidate plans apart. Probe it once, so a constant head is reported as
    # inert instead of as a draw with the solver.
    heads: Dict[str, Any] = {}
    for label, spec in (("a", spec_a), ("b", spec_b)):
        if spec.startswith(("value:", "residual:")):
            heads[label] = value_head_spread(_load_model(spec), solver, cells,
                                             mirror_key)
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
            damage_a, damage_b = damage_split(final, a_seat)
            played.append({"seed": board_seed, "half": half, "a_seat": a_seat,
                           "winner": winner, "winner_seat": winner_seat,
                           "turns": final.turns,
                           "damage_a": round(damage_a, 2),
                           "damage_b": round(damage_b, 2)})
    score = score_games(played)
    latency: Dict[str, Any] = {}
    arm_stats: Dict[str, Any] = {}
    for label, policy in (("a", policy_a), ("b", policy_b)):
        stats = getattr(policy, "stats", None)
        if stats:
            arm_stats[label] = dict(stats)
            if stats.get("seconds") is not None:
                arm_stats[label]["mean_ms"] = round(
                    1000.0 * stats["seconds"] / max(1, stats["decisions"]), 3)
            live = getattr(policy, "live", None)
            if live is not None:
                # Which selectors the chain actually reached, so "the HP ranker" is a
                # measurement rather than an assumption about the default path.
                arm_stats[label]["hp_choice"] = live.hp_choice_stats
                arm_stats[label]["value_choice"] = live.value_choice_stats
        timing = getattr(policy, "timing", None)
        if timing and timing["calls"]:
            latency[label] = {
                "mean_ms": round(1000.0 * timing["seconds"] / timing["calls"], 3),
                "calls": timing["calls"],
            }
    return {"a": spec_a, "b": spec_b, "mirror": mirror_key, "seed": seed,
            "rounds": rounds, "score": score,
            "efficiency": efficiency(played), "value_head_spread": heads,
            "decide_latency": latency, "arm_stats": arm_stats, "games": played}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", default="solver")
    parser.add_argument("--b", default="random")
    parser.add_argument("--games", "--boards", type=int, default=20,
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
        logger.debug(json.dumps({k: v for k, v in report.items() if k != "games"},
                         indent=1, sort_keys=True))
    else:
        score = report["score"]
        logger.debug("%s vs %s on %s, seats swapped (%d boards -> %d games, %.1fs)"
              % (args.a, args.b, args.mirror, args.games, score["games"],
                 report["elapsed_s"]))
        logger.debug("  a as seat 1: %s   a as seat 2: %s   seat 1 wins: %s"
              % (score["a_seat1_win"], score["a_seat2_win"], score["seat1_win"]))
        logger.debug("  a %d - b %d, two-sided p %s -> %s"
              % (score["wins_a"], score["wins_b"], score["p_value"],
                 score["verdict"]))
        if score["verdict"] == "seat_dominated":
            logger.debug("  this build pair cannot judge a decision: one seat wins "
                  "%.0f%% of the decided games regardless of policy"
                  % (100.0 * max(score["seat1_win"], 1.0 - score["seat1_win"])))
        eff = report["efficiency"]
        if eff["boards"]:
            logger.debug("  damage/turn  a %s  b %s  diff %+.2f  "
                  "(boards a %d - b %d, p %s)"
                  % (eff["a_damage_per_turn"], eff["b_damage_per_turn"],
                     eff["diff"], eff["boards_favouring_a"],
                     eff["boards_favouring_b"], eff["p_value"]))
            if eff.get("ci95"):
                logger.debug("  paired bootstrap 95%% CI on diff: [%+.3f, %+.3f]"
                      % (eff["ci95"][0], eff["ci95"][1]))
        for label, entry in report.get("decide_latency", {}).items():
            logger.debug("  decide latency %s: %.3f ms mean over %d calls"
                  % (label, entry["mean_ms"], entry["calls"]))
        for label, entry in report.get("arm_stats", {}).items():
            logger.debug("  arm %s: %d decisions, %.3f ms mean, cast %d move %d pass %d, "
                  "unmatched %d, changed vs solver %d, reasons %s"
                  % (label, entry["decisions"], entry["mean_ms"], entry["cast"],
                     entry["move"], entry["pass"], entry["unmatched"],
                     entry["changed_vs_solver"], entry["reasons"]))
            if "hp_choice" in entry:
                logger.debug("    selectors %s: hp %s, value %s"
                      % (label, entry["hp_choice"], entry["value_choice"]))
        for label, spread in report["value_head_spread"].items():
            if spread is None:
                logger.debug("  value head %s: could not be evaluated; the arm is the "
                      "solver" % label)
            elif spread <= 0.0:
                logger.debug("  value head %s: INERT (prediction spread %.6f) -- this "
                      "arm is the solver, not a learned policy" % (label, spread))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
