"""Symmetric 1v1 duel between normal (non-Temporix) level-200 builds.

Normal Dofus Touch caps are 12 AP / 6 MP / 6 range. The two builds below are
public Dofusbook builds inside those caps:

* dana ouro (Ecaflip, water, 12/6/6, 4825 HP)
* Stuff Terre-PP THL (Cra, earth, 12/5/5, 7500 HP)

The page-computed totals are used for HP/AP/MP/range/initiative/crit/heals,
the elemental stats, damage bonuses and both resistance layers. Class spells
come from the official Touch spell cache at level 200; the solver computes
damage with the build's stats against the target's resists.

Limitation: this is still the direct damage/steal/heal solver. Spell-per-turn
limits, critical rolls, push/AP/MP denial, shields, statuses, traps and summons
are the next v4.1 mechanics increment. Temporix/event builds are deliberately
not in this file.
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

from combat.battle_solver import BattleSolver
from combat.encounter.build_dataset import load_class_loadouts
from combat.duel import (CLASS_NAMES, DuelState, Duelist, live_spell,
                         engaged_cells, load_map, make_two_ply_policy,
                         play_game, solver_policy,
                         value_policy)

from combat.cli import run_main

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAP = REPO_ROOT / "data" / "maps" / "100139008.json"
DEFAULT_VALUE = REPO_ROOT / "models" / "duel_value_v4_0.pkl"
DEFAULT_OUT = REPO_ROOT / "benchmarks" / "normal_build_duel_v4_1.json"
NORMAL_CAPS = {"ap": 12, "mp": 6, "po": 6}

BUILDS: Dict[str, Dict[str, Any]] = {
    "dana_ouro": {
        "name": "dana ouro",
        "build_id": 2128696,
        "class": "Ecaflip",
        "level": 200,
        "element": "water",
        "hp": 4825,
        "ap": 12,
        "mp": 6,
        "po": 6,
        "initiative": 3575,
        "crit": 47,
        "crit_damage": 40,
        "heals": 0,
        "summons": 3,
        "stats": {
            "strength": 380, "intelligence": 160, "chance": 1115,
            "agility": 220, "power": 100, "damage": 0,
            "damage_neutral": 57, "damage_earth": 57, "damage_fire": 20,
            "damage_water": 143, "damage_air": 32,
            "critical_hit": 47, "damage_critical": 40, "heals": 0,
        },
        "resist_percent": {"neutral": 21, "earth": 17, "fire": 24,
                           "water": 17, "air": 7},
        "resist_fixed": {"neutral": 15, "earth": 0, "fire": 20,
                         "water": 0, "air": 40},
    },
    "stuff_terre_pp": {
        "name": "Stuff Terre-PP THL",
        "build_id": 3449404,
        "class": "Cra",
        "level": 200,
        "element": "earth",
        "hp": 7500,
        "ap": 12,
        "mp": 5,
        "po": 5,
        "initiative": 1917,
        "crit": 44,
        "crit_damage": 0,
        "heals": 10,
        "summons": 3,
        "stats": {
            "strength": 1018, "intelligence": 180, "chance": 230,
            "agility": 190, "power": 1050, "damage": 25,
            "damage_neutral": 140, "damage_earth": 140,
            "damage_fire": 60, "damage_water": 70, "damage_air": 50,
            "critical_hit": 44, "damage_critical": 0, "heals": 10,
        },
        "resist_percent": {"neutral": 0, "earth": 18, "fire": 20,
                           "water": 20, "air": -4},
        "resist_fixed": {"neutral": 30, "earth": 10, "fire": 10,
                         "water": 12, "air": 12},
    },
}


def _resists(values: Dict[str, int]) -> Tuple[Tuple[str, int], ...]:
    return tuple((name, int(values.get(name, 0))) for name in
                 ("neutral", "earth", "fire", "water", "air"))


def _check_caps(spec: Dict[str, Any]) -> None:
    if spec["ap"] > NORMAL_CAPS["ap"] or spec["mp"] > NORMAL_CAPS["mp"] \
            or spec["po"] > NORMAL_CAPS["po"]:
        raise SystemExit(
            f"refusing non-normal build {spec['name']}: "
            f"{spec['ap']} AP / {spec['mp']} MP / {spec['po']} range "
            f"(normal caps {NORMAL_CAPS})")


def _pick_spells(rows: List[Dict[str, Any]], element: str,
                 slots: int = 4) -> List[Dict[str, Any]]:
    element_index = {"neutral": 0, "earth": 1, "fire": 2,
                     "water": 3, "air": 4}.get(element, 0)
    damage = [row for row in rows if not row["ally"] and row["damage"] > 0]
    heals = [row for row in rows if row["ally"] and row["heal"] > 0]
    damage.sort(key=lambda row: row["damage"] / max(row["ap"], 1.0),
                reverse=True)
    focused = [row for row in damage if int(row["element"]) == element_index]
    picked = focused[:slots - (1 if heals else 0)]
    for row in damage:
        if len(picked) >= slots - (1 if heals else 0):
            break
        if row not in picked:
            picked.append(row)
    heals.sort(key=lambda row: row["heal"] / max(row["ap"], 1.0), reverse=True)
    if heals:
        picked = picked[:slots - 1] + [heals[0]]
    return picked[:slots]


def build_duelist(key: str, cell: int, ident: int,
                  payload: List[Tuple[str, List[Dict[str, Any]]]]
                  ) -> Optional[Duelist]:
    spec = BUILDS[key]
    _check_caps(spec)
    rows = next((rows for name, rows in payload
                 if str(name).lower() == spec["class"].lower()), [])
    if not rows:
        return None
    spells = []
    for slot, row in enumerate(_pick_spells(rows, spec["element"])):
        spells.append(live_spell(
            slot, float(row["ap"]), float(row["rmin"]),
            float(row["rmax"]) + spec["po"], float(row["damage"]),
            float(row["heal"]), int(row["element"]), bool(row["ally"])))
    class_name = next((name for name in CLASS_NAMES
                       if name.lower() == spec["class"].lower()), "Iop")
    return Duelist(
        id=ident, row=spec["build_id"], class_name=class_name,
        level=spec["level"], hp=float(spec["hp"]), max_hp=float(spec["hp"]),
        ap=float(spec["ap"]), max_ap=float(spec["ap"]),
        mp=float(spec["mp"]), max_mp=float(spec["mp"]),
        initiative=float(spec["initiative"]), cell=cell,
        resist=_resists(spec["resist_percent"]),
        spells=tuple(spells), stats=dict(spec["stats"]),
        resist_fixed=_resists(spec["resist_fixed"]),
    )


def make_normal_duel(cells, rng: random.Random,
                     max_rounds: int = 16) -> Optional[DuelState]:
    pair = engaged_cells(cells, rng)
    if pair is None:
        return None
    payload = load_class_loadouts(200)
    a = build_duelist("dana_ouro", pair[0], 1, payload)
    b = build_duelist("stuff_terre_pp", pair[1], 2, payload)
    if a is None or b is None:
        return None
    order = (a.id, b.id) if a.initiative >= b.initiative else (b.id, a.id)
    return DuelState(fighters=(a, b), cells=cells, order=order,
                     max_rounds=max_rounds)


def run(args: argparse.Namespace) -> int:
    started = time.time()
    cells = load_map(Path(args.map))
    solver = BattleSolver(beam_width=args.beam_width,
                          max_actions=args.max_actions)
    rng = random.Random(args.seed)
    two_ply = make_two_ply_policy(solver, opponent_k=3)
    model = None
    if Path(args.value).exists():
        model = pickle.loads(Path(args.value).read_bytes())["model"]

    matchups = {
        "solver_vs_solver": (solver_policy, solver_policy),
        "two_ply_vs_solver": (two_ply, solver_policy),
        "solver_vs_two_ply": (solver_policy, two_ply),
        "value_vs_solver": (value_policy, solver_policy),
        "solver_vs_value": (solver_policy, value_policy),
    }
    report: Dict[str, Any] = {
        "builds": BUILDS,
        "caps": NORMAL_CAPS,
        "matchups": {},
        "notes": [
            "Normal public builds only; Temporix/event builds are excluded.",
            "Page-computed totals from the public build pages; class kits from the official Touch spell cache.",
            "Direct damage/steal/heal only; crit rolls, spell-per-turn limits, push, AP/MP denial, shields, statuses, traps and summons are not modelled yet.",
            "The learned value model was trained on v3 synthetic builds; treat it as out-of-distribution here.",
        ],
    }
    for name, (policy_a, policy_b) in matchups.items():
        wins = losses = draws = 0
        turns = []
        games = 0
        for index in range(args.games):
            state = make_normal_duel(cells, random.Random(args.seed + index))
            if state is None:
                continue
            state, _ = play_game(state, {1: policy_a, 2: policy_b}, solver,
                                 rng, model=model, capture=False)
            games += 1
            if state.winner == 1:
                wins += 1
            elif state.winner == 2:
                losses += 1
            else:
                draws += 1
            turns.append(state.turns)
        total = max(1, games)
        report["matchups"][name] = {
            "games": games,
            "side1_win": wins / total,
            "side2_win": losses / total,
            "draw": draws / total,
            "mean_turns": float(np.mean(turns)) if turns else 0.0,
        }
        print(name, json.dumps(report["matchups"][name], sort_keys=True))
    report["elapsed_s"] = round(time.time() - started, 1)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--map", default=str(DEFAULT_MAP))
    p.add_argument("--value", default=str(DEFAULT_VALUE))
    p.add_argument("--games", type=int, default=20)
    p.add_argument("--beam-width", type=int, default=16)
    p.add_argument("--max-actions", type=int, default=5)
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--out", default=str(DEFAULT_OUT))
    return p


def main(argv=None):
    return run_main(build_parser, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
