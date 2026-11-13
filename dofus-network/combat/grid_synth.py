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
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from combat import grid_dataset

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "combat_grid_synth"
VARIANTS = ((4, 2), (6, 3), (8, 4), (10, 3))


def _point_cells() -> Dict[Tuple[int, int], int]:
    """(x, y) -> cell id, the inverse of ``battle_solver.cell_point``."""
    from combat.battle_solver import NB_CELLS, cell_point

    return {cell_point(cell): cell for cell in range(NB_CELLS)}


def _push_spell() -> Any:
    """A 2-AP push, so the solver can produce displacement plans.

    ``duel.live_spell`` only synthesises damage and heal effects -- the duel says
    push is not applied by its game loop -- so every kit the curriculum sees is
    push-free and the encoder never gets a displacement example. The *solver*
    does support push (effect 5), and a plan is what the dataset labels; adding
    the spell to the board's spell list, without playing the duel with it, is
    enough to record those plans.
    """
    from combat.battle_solver import LiveEffect, LiveSpell

    return LiveSpell(
        id=9001, name="curriculum.push", ap_cost=1, min_range=1, max_range=6,
        cast_in_line=False, cast_in_diagonal=False, requires_los=True,
        need_free_cell=False, need_taken_cell=True, max_cast_per_turn=None,
        max_cast_per_target=None, min_cast_interval=0, casts_this_turn=0,
        castable=True, critical_hit_probability=0.0,
        effects=(LiveEffect(5, 2, 2),), critical_effects=(), is_weapon=False,
        zone_size=None, zone_shape=None)


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


def classify_plans(plans: List[Any]) -> Tuple[bool, bool]:
    """(combo, displacement) for the plans the solver considers equally best.

    The curriculum has to exercise the three things the 560-cell encoder exists
    for and a live pass-only corpus never shows: several spells in one turn, a
    LOS break, and a push. This reads the solver's own winners rather than
    guessing from board flags.
    """
    if not plans:
        return False, False
    best = max(plan.score for plan in plans)
    winners = [plan for plan in plans if plan.score == best]
    combo = False
    for plan in winners:
        casts = [action for action in plan.actions if action.kind == "cast"]
        if len(casts) >= 2 and len({action.spell_id for action in casts}) >= 2:
            combo = True
            break
    # A plan that pushes is a displacement example even when it also deals
    # damage: the bucket would call that "damage", but the action the encoder
    # has to learn to rank is the forced move.
    displacement = any(plan.bucket == "displacement"
                       or any(action.moved_target is not None for action in plan.actions)
                       for plan in winners)
    return combo, displacement


def los_blocked_variant(board: Any, rng: random.Random) -> Optional[Any]:
    """A copy of ``board`` with one wall on the caster's line to an enemy.

    The recorded maps are open, so the encoder rarely sees a ranged plan fail
    for lack of line of sight. Closing one interior cell of an existing line --
    not adding geometry at random -- produces a board that is one cell away from
    the original and illegal for the plan that just won, which is exactly the
    contrast the ranker needs. Returns None when no interior cell can be closed
    (the line has no free interior, or another route still sees through).
    """
    from combat.battle_solver import CellMap, cell_point, line_points

    me = board.me
    if me is None or me.cell is None:
        return None
    occupied = frozenset(f.cell for f in board.fighters if f.cell is not None)
    enemies = [f for f in board.fighters
               if f.team != me.team and f.alive and f.cell is not None]
    rng.shuffle(enemies)
    points = _point_cells()
    for enemy in enemies:
        if not board.cells.has_los(me.cell, enemy.cell, occupied):
            continue
        ax, ay = cell_point(me.cell)
        bx, by = cell_point(enemy.cell)
        interior = [points[p] for p in line_points(ax, ay, bx, by)[:-1]
                    if p in points]
        for cell in reversed(interior):
            if cell in occupied or cell == me.cell:
                continue
            flags = list(board.cells.flags)
            if not (flags[cell] & 2):
                continue
            flags[cell] &= ~3  # a wall: neither walkable nor see-through
            variant = replace(board, cells=CellMap(tuple(flags)))
            if not variant.cells.has_los(me.cell, enemy.cell, occupied):
                return variant
    return None


def curriculum_rows(games: int, seed: int, beam_width: int = 8,
                    max_actions: int = 3, plan_k: int = 8
                    ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Duel playthroughs plus the LOS-blocked twins of every recorded board.

    Multi-spell turns come from ``max_actions=3`` and a wider beam: the solver
    only composes two casts when it is allowed to and when a second cast scores.
    Pushback comes from the displacement bucket in the class kits. LOS blocks
    are synthesised from the board itself (``los_blocked_variant``) because the
    duel map has no cover worth the name.
    """
    import numpy as np
    from combat import duel as du
    from combat.battle_solver import BattleSolver

    if games <= 0:
        return [], {}
    solver = BattleSolver(beam_width=beam_width, max_actions=max_actions)
    rows: List[Dict[str, Any]] = []
    stats: Counter = Counter()
    PUSH_SPELL = _push_spell()
    with np.load(str(du.DEFAULT_DATA)) as data:
        count = int(data["hp"].shape[0])
        if games * 2 > count:
            raise ValueError("%d curriculum duels need more than %d combatants"
                             % (games, count))
        picks = random.Random(seed).sample(range(count), games * 2)
        for index in range(games):
            ids = picks[index * 2:index * 2 + 2]
            rng = random.Random(seed * 100003 + index * 7)
            state = du.make_duel(data, *ids, cells=du.load_map(du.DEFAULT_MAP), rng=rng)
            if state is None:
                continue

            def record(state, actor, plans, rng, model=None):
                board = du.board_for(state, actor)
                board.round = state.round
                combo, displacement = classify_plans(plans)
                rows.append(board.to_fight_state())
                stats["boards"] += 1
                if combo:
                    stats["combo_boards"] += 1
                if displacement:
                    stats["displacement_boards"] += 1
                variant = los_blocked_variant(board, rng)
                if variant is not None:
                    rows.append(variant.to_fight_state())
                    stats["los_blocked_boards"] += 1
                # The same board with a push in the spellbook. Recorded only when
                # the solver actually prefers a displacement plan on it, so the
                # corpus gains pushback examples instead of near-duplicates.
                push_board = replace(board, spells=tuple(board.spells) + (PUSH_SPELL,))
                push_plans = solver.plan_candidates(push_board, 16)
                _combo, pushes = classify_plans(push_plans)
                if pushes:
                    rows.append(push_board.to_fight_state())
                    stats["pushback_boards"] += 1
                return plans[0]

            final, _ = du.play_game(state, {1: record, 2: record}, solver, rng, k=plan_k)
            if not final.done:
                raise RuntimeError("curriculum duel %d-%d did not terminate" % tuple(ids))
    return rows, dict(stats)


def build(path: Optional[Path], limit: int = 0, random_variants: int = 0,
          seed: int = 7, duel_games: int = 0,
          beam_width: int = 8, max_actions: int = 2, plan_k: int = 6,
          curriculum: int = 0
          ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if curriculum:
        if path is not None or random_variants or limit or duel_games:
            raise ValueError("curriculum mode cannot combine live paths, limits, "
                             "AP/MP variants, or --duel-games")
        synthetic, stats = curriculum_rows(curriculum, seed, beam_width,
                                           max_actions, plan_k)
        source = {"kind": "synthetic_curriculum", "requested_games": curriculum,
                  "curriculum": stats}
    elif duel_games:
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
    parser.add_argument("--curriculum", type=int, default=0,
                        help="duel boards plus LOS-blocked twins; multi-spell turns, "
                             "pushback, and cover (default max-actions 3)")
    parser.add_argument("--beam-width", type=int, default=8)
    parser.add_argument("--max-actions", type=int, default=2)
    parser.add_argument("--plan-k", type=int, default=6)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    if args.curriculum and args.max_actions == 2:
        # A combo needs three actions: move, cast, cast. 2 is the duel default,
        # and leaving it there silently produced no multi-spell boards at all.
        args.max_actions = 3
    path = args.boards
    if args.duel_games == 0 and args.curriculum == 0:
        if path is None:
            path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DofusFarm" / "fight_boards.jsonl"
        if not path.is_file():
            print("boards file not found:", path)
            return 2
    arrays, manifest = build(path, args.limit, args.random_variants, args.seed,
                             args.duel_games, args.beam_width, args.max_actions,
                             args.plan_k, args.curriculum)
    if not arrays:
        print("no synthetic board yielded a legal plan:", manifest)
        return 1
    manifest = grid_dataset.write_splits(arrays, manifest, args.out)
    print("synthetic rows", len(arrays["labels"]), "manifest", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
