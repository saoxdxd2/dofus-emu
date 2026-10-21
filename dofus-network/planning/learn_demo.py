"""Closed-loop learning demo: does experience actually fix the estimates?

A hidden "world" has different physics from the cost model's priors. Each episode
the planner plans with its current beliefs, the world produces an outcome, and the
outcome goes straight back into the posteriors. There is no training run, no
batch, no gradient — the experience *is* the training.

Two scenarios, because they test different parameters:

``discover_cheap_craft``
    The model thinks crafting is 9x worse than buying, but ingredients are already
    in the bag and crafting is really 12x *cheaper*. Watch the correction drop and
    the plan flip to craft. This is strategy learning.

``learn_drop_rate``
    The model assumes a 6.25% drop rate; the truth is 2%. The farm estimate is
    silently 3x too optimistic until the posterior moves. This is parameter
    learning, and it changes the plan's *honesty*, not just its choice.

Run from ``dofus-network``::

    python -m planning.learn_demo
    python -m planning.learn_demo --scenario discover_cheap_craft --episodes 200
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planning.bandit import ARMS, StrategyRanker, band_of
from planning.economy import CostModel, Economy, PlayerState
from planning.experience import ExperienceStore
from planning.kb import KnowledgeBase
from planning.planner import Goal, Planner

NOISE_SIGMA = 0.25   # multiplicative noise on every observed duration

SCENARIOS: Dict[str, Dict[str, Any]] = {
    "discover_cheap_craft": {
        "item": 31671,          # Langue d'Amphibouc: buy 369m vs craft 1098m predicted
        "level": 1,
        "episodes": 200,
        "exploration_ratio": 6.0,
        "truth_arm": {"buy": 1.0, "craft": 0.25, "farm": 2.2, "quest": 3.0},
        "truth_drop": 0.02,
        "story": "model says craft is 3x worse; ingredients are already owned, so it is 4x cheaper",
    },
    "learn_drop_rate": {
        "item": 384,            # Laine de Bouftou, level 19, dropped by Bouftou (lvl 22)
        "level": 5,
        "episodes": 160,
        "exploration_ratio": 10.0,
        "truth_arm": {"buy": 1.0, "farm": 2.2, "craft": 0.9, "quest": 3.0},
        "truth_drop": 0.02,
        "story": "model assumes 6.25% drop; truth is 2%",
    },
}


def _true_minutes(node: Any, truth: Dict[str, float]) -> float:
    return node.minutes * truth.get(node.kind, 1.0)


def run(
    scenario: str = "discover_cheap_craft",
    episodes: Optional[int] = None,
    seed: int = 7,
    quiet: bool = False,
) -> Dict[str, Any]:
    cfg = SCENARIOS[scenario]
    episodes = episodes or int(cfg["episodes"])
    truth = cfg["truth_arm"]
    truth_drop = float(cfg["truth_drop"])
    rng = random.Random(seed)

    kb = KnowledgeBase()
    with tempfile.TemporaryDirectory() as tmp:
        store = ExperienceStore(
            log_path=Path(tmp) / "experience.jsonl",
            snapshot_path=Path(tmp) / "posteriors.json",
            autosave_every=10_000,
        )
        cost = CostModel(kb, store)
        economy = Economy(cost, store)
        ranker = StrategyRanker(store, exploration_ratio=cfg["exploration_ratio"], seed=seed)
        planner = Planner(kb, cost, economy, ranker)
        player = PlayerState(level=int(cfg["level"]), kamas=0)
        goal = Goal(item_id=int(cfg["item"]))

        first_plan = planner.plan(goal, player)
        initial = {
            "chosen": first_plan.root.kind,
            "minutes": None if math.isinf(first_plan.minutes) else round(first_plan.minutes, 1),
            "alternatives": {node.kind: round(node.minutes, 1)
                             for node in first_plan.alternatives},
            "farm_kills": first_plan.root.meta.get("kills") or next(
                (node.meta.get("kills") for node in first_plan.alternatives
                 if node.kind == "farm"), None),
        }

        history: List[Dict[str, Any]] = []
        drop_estimates: List[float] = []
        farm_minutes_seen: List[float] = []

        for episode in range(1, episodes + 1):
            plan = planner.plan(goal, player)
            chosen = plan.root
            if math.isinf(chosen.minutes):
                break

            feasible = [chosen] + [alt for alt in plan.alternatives if not math.isinf(alt.minutes)]
            oracle = min(feasible, key=lambda node: _true_minutes(node, truth))

            observed = chosen.minutes * truth.get(chosen.kind, 1.0)
            observed *= math.exp(rng.gauss(0.0, NOISE_SIGMA))
            observed = max(observed, 0.01)

            # --- learning: one observation, three possible posteriors ----------
            ranker.observe(chosen.kind, player.level, chosen.minutes, observed,
                           meta={"episode": episode, "scenario": scenario})

            if chosen.kind == "farm":
                kills = int(chosen.meta.get("kills") or 0)
                monster_id = int(chosen.meta.get("monster_id") or 0)
                if kills and monster_id:
                    drops = sum(1 for _ in range(kills) if rng.random() < truth_drop)
                    economy.record_drop_run(int(cfg["item"]), drops, kills)
                    cost.record_farm(monster_id, player.level, kills, observed)
                    drop_estimates.append(economy.drop_rate(int(cfg["item"])))
                    farm_minutes_seen.append(chosen.minutes)

            correction = float(chosen.meta.get("correction") or 1.0)
            history.append({
                "episode": episode,
                "chosen": chosen.kind,
                "predicted": chosen.minutes,
                "predicted_calibrated": chosen.minutes * correction,
                "correction": correction,
                "observed": observed,
                "oracle": oracle.kind,
                "hit": chosen.kind == oracle.kind,
                "regret": (_true_minutes(chosen, truth) - _true_minutes(oracle, truth)),
            })

        final_plan = planner.plan(goal, player)
        report = {
            "scenario": scenario,
            "story": cfg["story"],
            "episodes": len(history),
            "initial": initial,
            "final": {
                "chosen": final_plan.root.kind,
                "minutes": None if math.isinf(final_plan.minutes) else round(final_plan.minutes, 1),
                "corrected_minutes": final_plan.root.meta.get("corrected_minutes"),
                "alternatives": {node.kind: round(node.minutes, 1)
                                 for node in final_plan.alternatives},
            },
            "corrections": {arm: round(ranker.correction(band_of(player.level), arm), 3)
                            for arm in ARMS},
            "truth": truth,
            "drop_rate": round(economy.drop_rate(int(cfg["item"])), 4),
            "drop_rate_truth": truth_drop,
            "drop_samples": len(drop_estimates),
            "farm_minutes": ([round(farm_minutes_seen[0], 1), round(farm_minutes_seen[-1], 1)]
                             if len(farm_minutes_seen) > 1 else []),
            "flip_episode": _flip_episode(history),
            "oracle_kind": _oracle_kind(history),
            "params_learned": len(store),
            "events": store.events_seen,
            "history": history,
        }
        if not quiet:
            _print_report(report, drop_estimates)
        return report


def _oracle_kind(history: List[Dict[str, Any]]) -> Optional[str]:
    """The strategy that was truly best most often — what learning should find."""
    if not history:
        return None
    counts: Dict[str, int] = {}
    for row in history:
        counts[row["oracle"]] = counts.get(row["oracle"], 0) + 1
    return max(counts, key=lambda key: counts[key])


def _flip_episode(history: List[Dict[str, Any]]) -> Optional[int]:
    """First episode after which the chosen strategy equals the oracle for good."""
    oracle = _oracle_kind(history)
    if oracle is None:
        return None
    last_bad = 0
    for row in history:
        if row["chosen"] != oracle:
            last_bad = row["episode"]
    return None if last_bad == 0 else last_bad + 1


def _print_report(report: Dict[str, Any], drop_estimates: List[float]) -> None:
    history = report["history"]
    print(f"scenario: {report['scenario']}  ({report['story']})")
    if not history:
        print("  no feasible plan; nothing to learn\n")
        return

    initial, final = report["initial"], report["final"]
    print(f"  before : {initial['chosen']:5s} {initial['minutes']}m raw   "
          f"alts {initial['alternatives']}")
    print(f"  after  : {final['chosen']:5s} {final['minutes']}m raw, "
          f"{final.get('corrected_minutes')}m calibrated   "
          f"alts {final['alternatives']}")
    if initial["chosen"] != final["chosen"]:
        print(f"  -> strategy flipped from {initial['chosen']} to {final['chosen']}")
    else:
        print(f"  -> strategy stayed {final['chosen']} (now with corrected numbers)")
    if report.get("flip_episode"):
        print(f"  -> settled on the truly-best arm ({report['oracle_kind']}) "
              f"from episode {report['flip_episode']} onward")
    print()

    print(f"  {'window':>10s} {'hit':>6s} {'regret':>9s} {'err(corr)':>10s} {'err(raw)':>9s}")
    for label, chunk in (("first 20", history[:20]), ("last 20", history[-20:])):
        if not chunk:
            continue
        hits = sum(1 for row in chunk if row["hit"]) / len(chunk)
        regret = statistics.fmean(row["regret"] for row in chunk)
        err_cal = statistics.fmean(
            row["observed"] / max(row["predicted_calibrated"], 1e-6) for row in chunk)
        err_raw = statistics.fmean(
            row["observed"] / max(row["predicted"], 1e-6) for row in chunk)
        print(f"  {label:>10s} {hits:6.0%} {regret:9.1f} {err_cal:10.2f} {err_raw:9.2f}")

    print()
    print("  err(corr) = observed / calibrated estimate. 1.00 means the estimate is now")
    print("  accurate; err(raw) is what the model would have said without experience.")
    print()
    print("  corrections learned vs truth (1.00 = the model was already right):")
    for arm, learned in report["corrections"].items():
        truth = report["truth"].get(arm, 1.0)
        good = abs(learned - truth) < 0.3 * max(truth, 0.1)
        print(f"    {arm:6s} learned x{learned:5.2f}   truth x{truth:4.2f}   "
              f"{'converged' if good else 'still off'}")

    if drop_estimates:
        first = statistics.fmean(drop_estimates[:3])
        last = statistics.fmean(drop_estimates[-3:])
        print()
        print(f"  drop rate  0.0625 (prior) -> {first:.4f} -> {last:.4f} "
              f"(truth {report['drop_rate_truth']:.4f})")
        if report["farm_minutes"] and len(report["farm_minutes"]) >= 2:
            print(f"  farm estimate {report['farm_minutes'][0]}m -> {report['farm_minutes'][-1]}m "
                  f"as the drop rate corrected")

    print()
    print(f"  {report['params_learned']} parameters, {report['events']} observations, "
          f"O(1) per update")
    print()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS) + ["all"], default="all")
    parser.add_argument("--episodes", type=int, default=None)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    names = sorted(SCENARIOS) if args.scenario == "all" else [args.scenario]
    reports = [run(name, args.episodes, args.seed, quiet=args.json) for name in names]
    if args.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "history"} for r in reports],
                         indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
