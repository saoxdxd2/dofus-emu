"""Turn a trained self-play policy into fight.estimate calibration rows.

For each encounter in the dataset, this runs the learned player against the
scripted opponent, then records:

* the analytic estimator's own features for the initial encounter (the exact
  contract ``fit_calibrator`` and the live guard use);
* the outcome under the learned policy.

The result is a JSONL that ``python -m combat.fight_estimate --rows ...`` can
fit. It is a silver label from the simulator, so the row carries
``source: "sim"`` and must not be presented as live evidence.
"""

from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path
from typing import Any, Dict, List

import jax
import jax.numpy as jnp
import numpy as np

from combat.encounter import model
from combat.encounter.env import Config, initial_state, observe
from combat.encounter.train import make_eval_rollout
from combat.fight_estimate import EnemyFacts, EnemyMember, PlayerFacts, estimate_from_facts

ELEMENTS = ("neutral", "earth", "fire", "water", "air")
TABLE_KEYS = ("spell_ap", "spell_rmin", "spell_rmax", "spell_damage",
              "spell_heal", "spell_element", "spell_ally")
SPEC_KEYS = ("hp", "max_hp", "ap_max", "mp_max", "power", "res", "dist",
             "initiative", "alive", "first_team", "level", "monster_id")


def _load(path: Path, samples: int):
    data = np.load(path)
    n = int(data["hp"].shape[0])
    take = min(samples, n)
    tables = {k: jnp.asarray(data[k][:take]) for k in TABLE_KEYS}
    spec = {k: jnp.asarray(data[k][:take]) for k in SPEC_KEYS}
    return data, tables, spec, take


def _player_facts(spec: Dict[str, np.ndarray], tables: Dict[str, np.ndarray],
                  b: int, ap_max: int) -> PlayerFacts:
    hp = float(spec["hp"][b, 0])
    power = float(spec["power"][b, 0])
    dpt: Dict[str, float] = {}
    heal_turn = 0.0
    for s in range(tables["spell_ap"].shape[2]):
        ap = float(tables["spell_ap"][b, 0, s])
        if ap <= 0:
            continue
        mult = (100.0 + power) / 100.0
        damage = float(tables["spell_damage"][b, 0, s]) * mult
        heal = float(tables["spell_heal"][b, 0, s]) * mult
        if bool(tables["spell_ally"][b, 0, s]) and heal > 0:
            heal_turn = max(heal_turn, heal / ap * ap_max)
        elif damage > 0:
            element = ELEMENTS[int(tables["spell_element"][b, 0, s]) % len(ELEMENTS)]
            per_turn = damage / ap * ap_max
            dpt[element] = max(dpt.get(element, 0.0), per_turn)
    res = {
        name: float(spec["res"][b, 0, i]) for i, name in enumerate(ELEMENTS)
    }
    return PlayerFacts(
        level=int(spec["level"][b, 0]),
        current_hp=hp,
        max_hp=float(spec["max_hp"][b, 0]),
        ap=ap_max,
        dpt_by_element=dpt,
        damage_lines=sum(1 for value in dpt.values() if value > 0),
        heal_per_turn=heal_turn,
        resist_percent=res,
    )


def _enemy_facts(spec: Dict[str, np.ndarray], b: int,
                 n_allies: int) -> EnemyFacts:
    members: List[EnemyMember] = []
    for slot in range(n_allies, spec["alive"].shape[1]):
        if not bool(spec["alive"][b, slot]):
            continue
        monster_id = int(spec["monster_id"][b, slot]) if "monster_id" in spec else 0
        members.append(EnemyMember(
            monster_id=monster_id,
            name=f"monster:{monster_id}",
            level=int(spec["level"][b, slot]) if "level" in spec else 1,
            hp=float(spec["hp"][b, slot]),
            ap=float(spec["ap_max"][b, slot]),
            resist_percent={
                name: float(spec["res"][b, slot, i])
                for i, name in enumerate(ELEMENTS)
            },
            fixed_res=0.0,
            is_boss=False,
        ))
    return EnemyFacts(members=members)


def export(args: argparse.Namespace) -> int:
    payload = pickle.loads(Path(args.params).read_bytes())
    saved = payload.get("config", {})
    cfg = Config(
        allies=int(saved.get("allies", 1)),
        enemies=int(saved.get("enemies", 4)),
        spells=int(saved.get("spells", 4)),
        max_rounds=int(saved.get("max_rounds", 24)),
        max_dist=int(saved.get("max_dist", 12)),
        casts_per_turn=int(saved.get("casts_per_turn", 4)),
    )
    heads = int(payload.get("heads", 4))
    params = jax.tree_util.tree_map(jnp.asarray, payload["params"])
    _raw, tables, spec, take = _load(Path(args.data), args.samples)
    state0 = initial_state(spec, tables, cfg)
    length = 2 * cfg.max_rounds
    rollout = jax.jit(make_eval_rollout(cfg, length, opponent="heuristic"))
    final = rollout(params, state0, tables, jax.random.PRNGKey(args.seed))
    winner = np.asarray(final["winner"])

    player_team = jnp.zeros((take,), dtype=jnp.int32)
    acting_team = state0["acting_team"]
    obs, glob, mask = observe(state0, tables, cfg, acting_team=acting_team)
    acting_values = np.asarray(
        model.forward(params, obs, glob, mask, heads=heads)["value"])
    # The head predicts the *acting* team's result, so an enemy-first state
    # carries the enemy's value; negate it to get the player's value.
    player_values = np.where(np.asarray(acting_team) == 0,
                             acting_values, -acting_values)

    n_allies = int(cfg.allies)
    spec_np = {k: np.asarray(v) for k, v in spec.items()}
    table_np = {k: np.asarray(v) for k, v in tables.items()}
    rows: List[Dict[str, Any]] = []
    for b in range(take):
        player = _player_facts(spec_np, table_np, b, int(spec_np["ap_max"][b, 0]))
        enemy = _enemy_facts(spec_np, b, n_allies)
        estimate = estimate_from_facts(player, enemy)
        rows.append({
            "source": "sim",
            "session": "selfplay",
            "features": estimate["features"],
            "won": bool(winner[b] == 0),
            "draw": bool(winner[b] == 2),
            "value": float(player_values[b]),
            "player_level": int(spec_np["level"][b, 0]),
            "enemy_levels": [m.level for m in enemy.members],
            "enemy_hp": round(float(enemy.total_hp), 1),
        })
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    wins = sum(1 for row in rows if row["won"])
    draws = int((winner == 2).sum())
    print(f"wrote {out}: {take} rows, wins {wins}, losses {take - wins - draws}, "
          f"draws {draws}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True, help="encounters.npz")
    p.add_argument("--params", required=True, help="encounter_selfplay.pkl")
    p.add_argument("--out", default="trajectories/sim_fight_outcomes.jsonl")
    p.add_argument("--samples", type=int, default=1024)
    p.add_argument("--seed", type=int, default=1)
    return p


def main(argv=None) -> int:
    return export(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
