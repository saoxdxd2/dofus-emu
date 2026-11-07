"""Synthetic 560-cell boards for the 30M grid encoder.

Two sources are available:

* live recorded boards with AP/MP repairs — geographically real, but many boards have no unique
  solver preference and are now excluded as tied-top rows;
* beam-vs-beam duels — synthetic combat, but each recorded turn normally has a distinct first plan.

Either mode remains solver distillation, not live outcome validation. A real fight gate still
applies before promotion.

    python -m combat.grid_synth --duel-games 120 --out data/combat_grid_duel
    python -m combat.grid_synth --out data/combat_grid_synth
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from combat import grid_dataset

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "combat_grid_synth"
VARIANTS = ((4, 2), (6, 3), (8, 4), (10, 3))


def _rows(path: Path) -> List[Dict[str, Any]]:
    out = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            board = row.get("board") if isinstance(row, dict) else None
            if isinstance(board, dict):
                out.append(board)
    return out


def _variant(payload: Dict[str, Any], ap: int, mp: int) -> Dict[str, Any]:
    board = copy.deepcopy(payload)
    board["my_ap"] = ap
    board["my_mp"] = mp
    board["is_my_turn"] = True
    board["in_preparation"] = False
    board["in_fight"] = True
    board["current_fighter_id"] = board.get("my_id")
    self_id = board.get("my_id")
    for fighter in board.get("fighters") or []:
        if not isinstance(fighter, dict):
            continue
        if fighter.get("id") == self_id or fighter.get("is_me"):
            fighter["ap"] = ap
            fighter["mp"] = mp
            fighter["max_ap"] = ap
            fighter["max_mp"] = mp
            fighter["hp"] = fighter.get("hp") or 60
            fighter["max_hp"] = fighter.get("max_hp") or 60
        else:
            fighter["ap"] = fighter.get("ap") or 6
            fighter["mp"] = fighter.get("mp") or 3
            fighter["max_ap"] = fighter.get("max_ap") or fighter["ap"]
            fighter["max_mp"] = fighter.get("max_mp") or fighter["mp"]
            fighter["hp"] = fighter.get("hp") or 100
            fighter["max_hp"] = fighter.get("max_hp") or fighter["hp"]
    return board


def _random_variant(payload: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    board = _variant(payload, rng.choice((4, 6, 8, 10, 12)),
                     rng.choice((1, 2, 3, 4, 5)))
    self_id = board.get("my_id")
    for fighter in board.get("fighters") or []:
        if not isinstance(fighter, dict):
            continue
        if fighter.get("id") == self_id or fighter.get("is_me"):
            hp = rng.randint(40, 100)
        else:
            hp = rng.randint(50, 250)
        fighter["hp"] = hp
        fighter["max_hp"] = hp
    return board


def duel_rows(games: int, seed: int, beam_width: int = 8,
              max_actions: int = 2, plan_k: int = 6) -> List[Dict[str, Any]]:
    """Record every board the beam actually plays in a duel."""
    import numpy as np
    from combat import duel as du
    from combat.battle_solver import BattleSolver

    if games <= 0:
        return []
    solver = BattleSolver(beam_width=beam_width, max_actions=max_actions)
    cells = du.load_map(du.DEFAULT_MAP)
    rows: List[Dict[str, Any]] = []
    with np.load(str(du.DEFAULT_DATA)) as data:
        count = int(data["hp"].shape[0])
        if games * 2 > count:
            raise ValueError("%d unique duels need more than %d combatants" % (games, count))
        picks = random.Random(seed).sample(range(count), games * 2)
        for index in range(games):
            ids = picks[index * 2:index * 2 + 2]
            rng = random.Random(seed * 100003 + index * 7)
            state = du.make_duel(data, *ids, cells, rng=rng)
            if state is None:
                continue

            def record(state, actor, plans, rng, model=None):
                board = du.board_for(state, actor)
                board.round = state.round
                rows.append(board.to_fight_state())
                return plans[0]

            final, _ = du.play_game(state, {1: record, 2: record}, solver, rng, k=plan_k)
            if not final.done:
                raise RuntimeError("duel %d-%d did not terminate" % tuple(ids))
    return rows


def build(path: Optional[Path], limit: int = 0, random_variants: int = 0,
          seed: int = 7, duel_games: int = 0,
          beam_width: int = 8, max_actions: int = 2, plan_k: int = 6
          ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if duel_games:
        if path is not None or random_variants or limit:
            raise ValueError("duel mode cannot combine live paths, limits, or AP/MP variants")
        synthetic = duel_rows(duel_games, seed, beam_width, max_actions, plan_k)
        source = {"kind": "duel_playthroughs", "requested_games": duel_games,
                  "source_boards": duel_games}
    else:
        if path is None:
            raise ValueError("--boards is required unless --duel-games is set")
        payloads = _rows(path)
        if limit:
            payloads = payloads[:limit]
        synthetic = []
        rng = random.Random(seed)
        for payload in payloads:
            if random_variants > 0:
                for _ in range(random_variants):
                    synthetic.append(_random_variant(payload, rng))
            else:
                for ap, mp in VARIANTS:
                    synthetic.append(_variant(payload, ap, mp))
        source = {"kind": "live_board_variants", "source": str(path),
                  "source_boards": len(payloads),
                  "variants": ("random:%d:seed=%d" % (random_variants, seed)
                               if random_variants else list(VARIANTS))}
    arrays, manifest = grid_dataset.build(synthetic)
    manifest.update(source)
    manifest.update({"beam_width": beam_width, "max_actions": max_actions,
                     "plan_k": plan_k, "live": False})
    return arrays, manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--boards", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--random-variants", type=int, default=0,
                        help="random AP/MP/HP variants per board instead of the fixed four")
    parser.add_argument("--duel-games", type=int, default=0,
                        help="generate boards from this many beam-vs-beam duels instead of live paths")
    parser.add_argument("--beam-width", type=int, default=8)
    parser.add_argument("--max-actions", type=int, default=2)
    parser.add_argument("--plan-k", type=int, default=6)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    path = args.boards
    if args.duel_games == 0:
        if path is None:
            path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm" / "fight_boards.jsonl"
        if not path.is_file():
            print("boards file not found:", path)
            return 2
    arrays, manifest = build(path, args.limit, args.random_variants, args.seed,
                             args.duel_games, args.beam_width, args.max_actions, args.plan_k)
    if not arrays:
        print("no synthetic board yielded a legal plan:", manifest)
        return 1
    manifest = grid_dataset.write_splits(arrays, manifest, args.out)
    print("synthetic rows", len(arrays["labels"]), "manifest", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
