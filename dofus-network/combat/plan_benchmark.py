"""Benchmark cell-level plan selection on real map geometry.

The abstract benchmark asks whether a policy wins a coarse encounter. This one
asks a narrower question that survives into v4: given a legal board and a set
of solver plans, does a ranker pick a plan that is actually good? The solver's
own score is the reference, not the truth; the value head is allowed to prefer
a lower-damage plan when it improves the win estimate. We report both, plus
whether a lethal plan was available and chosen.

This is a planning-quality benchmark, not a win-rate benchmark. It uses
synthetic boards drawn from the v3 encounter rows on a real client map so the
cell geometry, line of sight, MP costs and ranges are exercised.
"""

from __future__ import annotations

import argparse
import json
import pickle
import random
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import jax
import jax.numpy as jnp
import numpy as np

from combat.battle_solver import BattleSolver, Board, CellMap, Fighter
from combat.duel import live_spell
from combat.encounter.build_dataset import ELEMENTS
from combat.encounter.checkpoint import config_from_payload
from combat.plan_value import rank_plans, score_plan

from combat.cli import run_main

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAP = REPO_ROOT / "data" / "maps" / "100139008.json"
DEFAULT_DATA = REPO_ROOT / "kaggle" / "tpu_selfplay" / "dataset" / "encounters.npz"
DEFAULT_CHECKPOINT = REPO_ROOT / "models" / "encounter_selfplay.pkl"
DEFAULT_OUT = REPO_ROOT / "benchmarks" / "plan_v3.json"


def load_map(path: Path) -> CellMap:
    payload = json.loads(path.read_text(encoding="utf-8"))
    flags = tuple(int(cell.get("l", 0)) for cell in payload.get("cells", []))
    return CellMap(flags)


def _resists(spec: Dict[str, np.ndarray], row: int,
             slot: int) -> Tuple[Tuple[str, int], ...]:
    return tuple(
        (name, int(round(float(spec["res"][row, slot, index]))))
        for index, name in enumerate(ELEMENTS)
    )


def make_board(data: Dict[str, np.ndarray], row: int, cells: CellMap,
               rng: random.Random) -> Optional[Board]:
    spec = data
    walkable = [cell for cell in range(len(cells.flags))
                if cells.walkable(cell) and cells.sees(cell)]
    if len(walkable) < 6:
        return None
    n_enemies = int(np.asarray(spec["alive"])[row, 1:].sum())
    if n_enemies <= 0 or n_enemies > 4:
        return None
    positions = rng.sample(walkable, 1 + n_enemies)
    player_cell = positions[0]
    enemy_cells = positions[1:]

    player_hp = float(spec["hp"][row, 0])
    fighters = [Fighter(
        id=1, name="player", cell=player_cell, team="player",
        hp=int(player_hp), max_hp=int(max(player_hp, spec["max_hp"][row, 0])),
        ap=int(spec["ap_max"][row, 0]), mp=int(spec["mp_max"][row, 0]),
        shield=0, level=int(spec["level"][row, 0]), alive=True, by_me=True,
        resist_percent=_resists(spec, row, 0), resist_fixed=(),
        critical_resistance=0,
    )]
    for index in range(n_enemies):
        slot = 1 + index
        fighters.append(Fighter(
            id=10 + index, name=f"monster{index}", cell=enemy_cells[index],
            team="enemy", hp=int(spec["hp"][row, slot]),
            max_hp=int(max(spec["max_hp"][row, slot], 1)),
            ap=int(spec["ap_max"][row, slot]), mp=int(spec["mp_max"][row, slot]),
            shield=0, level=int(spec["level"][row, slot]), alive=True,
            by_me=False, resist_percent=_resists(spec, row, slot),
            resist_fixed=(), critical_resistance=0,
        ))

    spells = []
    for slot in range(spec["spell_ap"].shape[2]):
        spells.append(live_spell(
            slot, float(spec["spell_ap"][row, 0, slot]),
            float(spec["spell_rmin"][row, 0, slot]),
            float(spec["spell_rmax"][row, 0, slot]),
            float(spec["spell_damage"][row, 0, slot]),
            float(spec["spell_heal"][row, 0, slot]),
            int(spec["spell_element"][row, 0, slot]),
            bool(spec["spell_ally"][row, 0, slot]),
        ))
    stats = {"strength": 0, "intelligence": 0, "chance": 0, "agility": 0,
             "power": 0, "damage": 0, "critical_hit": 0, "heals": 0}
    return Board(
        map_id=100139008, in_fight=True, in_preparation=False, is_my_turn=True,
        my_id=1, my_cell=player_cell, my_ap=int(spec["ap_max"][row, 0]),
        my_mp=int(spec["mp_max"][row, 0]), fighters=fighters, spells=spells,
        my_stats=stats, cells=cells,
    )


def _signature(plan) -> Tuple[Any, ...]:
    return tuple((action.kind, action.move_to, action.spell_id, action.target_id)
                 for action in plan.actions)


def _choice_metrics(plan, candidates, values: Dict[int, float]) -> Dict[str, Any]:
    best_score = max(candidate.score for candidate in candidates)
    lethal = any(candidate.kills for candidate in candidates)
    return {
        "kind": plan.actions[0].kind if plan.actions else "none",
        "damage": float(plan.expected_damage),
        "kills": len(plan.kills),
        "score": float(plan.score),
        "regret": float(best_score - plan.score),
        "value": float(values[id(plan)]),
        "lethal_available": bool(lethal),
        "lethal": bool(plan.kills),
        "pass": bool(plan.actions and plan.actions[0].kind == "pass"),
    }


def run(args: argparse.Namespace) -> int:
    started = time.time()
    payload = pickle.loads(Path(args.checkpoint).read_bytes())
    params = jax.tree_util.tree_map(jnp.asarray, payload["params"])
    cfg = config_from_payload(payload)
    data = np.load(args.data)
    cells = load_map(Path(args.map))
    rng = random.Random(args.seed)
    solver = BattleSolver(beam_width=args.beam_width, max_actions=args.max_actions)

    totals: Dict[str, Dict[str, float]] = {
        name: {"n": 0, "damage": 0.0, "kills": 0.0, "score": 0.0,
               "regret": 0.0, "value": 0.0, "lethal": 0.0, "pass": 0.0}
        for name in ("solver_best", "solver_random", "value_rank", "value_pure")
    }
    lethal_available = 0
    same_as_best = 0
    boards = 0
    rows = rng.sample(range(data["hp"].shape[0]),
                      min(args.rows, int(data["hp"].shape[0])))
    for row in rows:
        board = make_board(data, row, cells, rng)
        if board is None:
            continue
        candidates = solver.plan_candidates(board, args.candidates)
        if len(candidates) < 2:
            continue
        boards += 1
        value_map = {id(plan): score_plan(params, board, plan, cfg)
                     for plan in candidates}
        ranked = rank_plans(params, board, candidates, cfg)
        pure = max(candidates, key=lambda plan: score_plan(params, board, plan, cfg))
        choices = {
            "solver_best": candidates[0],
            "solver_random": rng.choice(candidates),
            "value_rank": ranked[0][1],
            "value_pure": pure,
        }
        if any(plan.kills for plan in candidates):
            lethal_available += 1
        if _signature(choices["value_rank"]) == _signature(candidates[0]):
            same_as_best += 1
        for name, plan in choices.items():
            row_metrics = _choice_metrics(plan, candidates, value_map)
            totals[name]["n"] += 1
            totals[name]["damage"] += row_metrics["damage"]
            totals[name]["kills"] += row_metrics["kills"]
            totals[name]["score"] += row_metrics["score"]
            totals[name]["regret"] += row_metrics["regret"]
            totals[name]["value"] += row_metrics["value"]
            totals[name]["lethal"] += float(row_metrics["lethal"])
            totals[name]["pass"] += float(row_metrics["pass"])

    summary = {}
    for name, row in totals.items():
        n = max(1, row["n"])
        summary[name] = {
            "n": row["n"],
            "mean_damage": round(row["damage"] / n, 3),
            "mean_kills": round(row["kills"] / n, 3),
            "mean_score": round(row["score"] / n, 3),
            "mean_regret": round(row["regret"] / n, 3),
            "mean_value": round(row["value"] / n, 4),
            "lethal_rate": round(row["lethal"] / n, 4),
            "pass_rate": round(row["pass"] / n, 4),
        }
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "boards": boards,
        "lethal_available": lethal_available,
        "value_rank_same_as_solver_best": same_as_best,
        "rows": len(rows),
        "seed": args.seed,
        "beam_width": args.beam_width,
        "max_actions": args.max_actions,
        "candidates": args.candidates,
        "policies": summary,
        "elapsed_s": round(time.time() - started, 1),
        "notes": [
            "The reference is the solver's own score, not the true win rate.",
            "value_rank uses the value head plus the lethal/damage tie-break; "
            "value_pure uses the value head alone.",
            "This measures plan selection on real cells, not execution or outcomes.",
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report["policies"], indent=1))
    print(f"boards={boards} lethal_available={lethal_available} "
          f"same_as_best={same_as_best} -> {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default=str(DEFAULT_DATA))
    p.add_argument("--map", default=str(DEFAULT_MAP))
    p.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    p.add_argument("--rows", type=int, default=64)
    p.add_argument("--candidates", type=int, default=8)
    p.add_argument("--beam-width", type=int, default=16)
    p.add_argument("--max-actions", type=int, default=3)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default=str(DEFAULT_OUT))
    return p


def main(argv=None):
    return run_main(build_parser, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
