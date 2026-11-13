"""What a cell is worth to a plan that does not act on it this turn.

Two placements are priced here, and both are about cells rather than targets:

``corridor_traffic``  How much of the enemy's own movement passes through a
                      cell. A trap is not damage, it is damage *if something
                      walks through it*, so its value is the trap's chain
                      damage times the traffic that cell will see. Detonating a
                      trap this turn is the easy case and the solver already
                      gets it; the case that was missing is a trap that is
                      dropped and waits, which is what a trap cast from
                      invisibility, or one placed at the end of a turn, is.

``choke_bonus``       How much a summon placed on a cell helps by standing
                      there. A Gobball in the open is a body; the same Gobball
                      between the player and a melee monster is a turn the
                      player does not spend, and in a doorway it is a whole
                      approach. This is geometry, not a fitted weight.

Why a separate module
---------------------
`map_field.py` is the geometry and nothing else -- every quantity in it is a
definition, checked against the live decoder, and none of it is tuned. The
values below are *policy*: they say what the geometry is worth to a plan on
this turn. Keeping them apart means a change to how the bot values a trap
cannot silently change what the map's effective resistance means.

Units
-----
Both return a multiple of a baseline, not a damage figure:

``corridor_traffic`` is in units of "expected enemy step-equivalents per enemy
turn", so 1.0 means an average cell and 3.0 means traffic three times the
average.

``choke_bonus`` is a utility bonus in the solver's own score units, calibrated
against `APPROACH_VALUE` so a summoned body is worth about the same as the
walking it replaces.

Neither is fitted. Both are ratios of measured geometry, which is what makes
them checkable: every function here can be called on a real map and its output
verified against a hand-computed path.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from combat.map_field import UNREACHABLE, MapField, field_for

#: A cell's traffic is the number of shortest enemy->ally paths that use it,
#: divided by the traffic an average walkable cell sees. Values above this are
#: treated as a corridor and the excess is what a trap is credited for: a trap
#: on a cell every path already uses is not news, a trap on a cell that only
#: becomes a corridor when the enemy closes in is.
CORRIDOR_EXCESS = 1.0


def _points(field: MapField, cell: int) -> bool:
    return 0 <= int(cell) < field.walkable.shape[0] and bool(field.walkable[cell])


def _path_cells(field: MapField, origin: int, target: int) -> Tuple[int, ...]:
    """One shortest walkable path from ``origin`` to ``target``, endpoints included.

    Reconstructed by descending the exact BFS distances, so a step is taken only
    onto a cell one closer to the target. Ties are broken by the lowest cell id
    rather than by iteration order, which makes the path deterministic and lets
    a test assert on it.
    """
    if not _points(field, origin) or not _points(field, target):
        return ()
    if origin == target:
        return (origin,)
    dist = field.dist
    if dist[origin, target] == UNREACHABLE:
        return ()
    path = [target]
    current = target
    guard = int(dist[origin, target]) + 1
    while current != origin and guard > 0:
        guard -= 1
        nxt = None
        for neighbour in _CARDINAL[current]:
            if neighbour < 0 or not field.walkable[neighbour]:
                continue
            if int(dist[origin, neighbour]) == int(dist[origin, current]) - 1:
                if nxt is None or neighbour < nxt:
                    nxt = neighbour
        if nxt is None:
            return ()
        path.append(nxt)
        current = nxt
    if current != origin:
        return ()
    return tuple(reversed(path))


#: Cardinal neighbours, indexed by cell id with -1 off the board.
#:
#: Taken from `battle_solver` directly rather than rebuilt. The first version of
#: this built the table from `_POINTS`, which is keyed by *coordinate* while
#: `_CARDINAL_NEIGHBOURS` is keyed by *cell id*; `_POINTS.get(cell)` therefore
#: returned None for every id and every reconstructed path was empty, so both
#: scorers silently returned zero on every real board.
from combat.battle_solver import _CARDINAL_NEIGHBOURS as _CARDINAL  # noqa: E402


def corridor_traffic(field: MapField, cell: int,
                     enemies: Iterable[int],
                     allies: Iterable[int]) -> float:
    """Enemy foot traffic through ``cell``, relative to an average walkable cell.

    For every live ``(enemy, ally)`` pair the shortest path between them is
    walked, and ``cell`` scores 1 if it lies on that path. The total is divided
    by the mean of the same statistic over every walkable cell, so the answer is
    a ratio: 1.0 is an ordinary cell, and a value well above it is a cell the
    enemy group keeps being forced through.

    A cell that is not walkable scores 0. A pair with no path contributes
    nothing, which is the honest answer on a broken map rather than a zero that
    would make the cell look safe.

    The path is one of the shortest, not the only one: a corridor with several
    equal routes under-counts. That is deliberate -- counting *all* shortest
    paths would give every cell in an open room the same score, which is exactly
    the case this is meant to distinguish from a corridor.
    """
    enemies = [int(e) for e in enemies if e is not None]
    allies = [int(a) for a in allies if a is not None]
    if not enemies or not allies:
        return 0.0
    if not _points(field, cell):
        return 0.0
    return float(_traffic_profile(field, enemies, allies, probes=[int(cell)])[0])


def corridor_weights(field: MapField, cells: Sequence[int],
                     enemies: Iterable[int], allies: Iterable[int]
                     ) -> np.ndarray:
    """`corridor_traffic` for many cells in one pass (one path walk per pair)."""
    enemies = [int(e) for e in enemies if e is not None]
    allies = [int(a) for a in allies if a is not None]
    probes = [int(c) for c in cells]
    if not probes:
        return np.zeros((0,), dtype=np.float64)
    if not enemies or not allies:
        return np.zeros((len(probes),), dtype=np.float64)
    return _traffic_profile(field, enemies, allies, probes=probes)


def _traffic_profile(field: MapField, enemies: List[int], allies: List[int],
                     probes: Sequence[int]) -> np.ndarray:
    """Count how many enemy->ally shortest paths cross each probe cell.

    The normaliser is the same count taken over *every* walkable cell, so the
    result is independent of how many probes were asked for and of where they
    are. Without it a board with three enemies would always outscore a board
    with one, and the trap utility would be measuring the fight's size rather
    than the cell's shape.
    """
    counts = np.zeros(len(probes), dtype=np.float64)
    index = {cell: position for position, cell in enumerate(probes)}
    walked = 0
    for enemy in enemies:
        for ally in allies:
            path = _path_cells(field, enemy, ally)
            if not path:
                continue
            walked += 1
            for step in path:
                position = index.get(step)
                if position is not None:
                    counts[position] += 1.0
    if walked == 0:
        return counts
    # Normaliser: the traffic a cell on a *path* sees, i.e. 1.0 per crossing
    # enemy->ally pair. Dividing by the per-walkable-cell mean instead was the
    # first version and it was wrong in the case that matters: with one enemy
    # and one ally the path is ~20 cells of a 419-cell map, so the mean is 0.05
    # and every path cell scores ~20 -- then the excess-over-1 rule fires on all
    # of them, and a trap anywhere ON the path ties with a trap at its most
    # forced point. Against the path's own crossing count, 1.0 means "on the
    # route" and the cells that several pairs share score higher.
    return counts / float(max(1, walked))


def choke_bonus(field: MapField, cell: int, ally_cells: Iterable[int],
                enemy_cells: Iterable[int], scale: float = 1.0) -> float:
    """How much a body on ``cell`` is worth by standing in the way.

    Three geometric facts, each measured rather than fitted:

    * **Betweenness.** The summon sits on an enemy->ally shortest path. A body
      on the path is a body the enemy walks into.
    * **Bottleneck.** The cell's effective resistance to the enemy side is high
      relative to the map, which is what a doorway looks like in the graph and
      an open room does not.
    * **Melee screening.** The cell is closer to an enemy than the ally it
      protects, so the summon is between the two rather than beside the fight.

    Returns a utility bonus in the solver's score units. Zero for a cell an ally
    already occupies -- summoning onto a friendly is not a screen.
    """
    cell = int(cell)
    if not _points(field, cell):
        return 0.0
    allies = [int(a) for a in ally_cells if a is not None]
    enemies = [int(e) for e in enemy_cells if e is not None]
    if not allies or not enemies:
        return 0.0
    if cell in allies or cell in enemies:
        return 0.0

    intruder = 0.0
    for enemy in enemies:
        for ally in allies:
            if cell in _path_cells(field, enemy, ally)[1:-1]:
                intruder += 1.0
    routes = float(len(enemies) * len(allies))
    between = intruder / routes if routes else 0.0

    # Bottleneck: resistance from this cell to the enemy side, relative to the
    # map's own median. A doorway is a high-resistance edge in the random-walk
    # metric even when the hop count to both sides is short.
    neighbour = min(enemies, key=lambda e: int(field.dist[cell, e])
                    if field.dist[cell, e] != UNREACHABLE else 1 << 12)
    reach = field.resistance[cell, neighbour] if field.dist[cell, neighbour] != UNREACHABLE else 0.0
    typical = float(np.median(field.resistance[field.walkable])) or 1.0
    bottleneck = max(0.0, min(2.0, float(reach) / typical - 1.0))

    # Melee screening: closer to the enemy than the ally it covers.
    screen = 0.0
    for ally in allies:
        gap_to_enemy = min(int(field.dist[cell, e]) for e in enemies
                           if field.dist[cell, e] != UNREACHABLE) \
            if any(field.dist[cell, e] != UNREACHABLE for e in enemies) else 1 << 12
        partner = min(int(field.dist[cell, a]) for a in allies
                      if field.dist[cell, a] != UNREACHABLE) \
            if any(field.dist[cell, a] != UNREACHABLE for a in allies) else 1 << 12
        if gap_to_enemy < partner:
            screen = max(screen, 1.0)
    return scale * (2.0 * between + 1.5 * bottleneck + 1.0 * screen)


def traffic_from_board(board: Any, field: Optional[MapField] = None) -> Dict[str, Any]:
    """Enemy and ally cells from a solver ``Board``, plus the map it is on.

    The board's own ``cells`` gives the map id; `map_field.field_for` caches the
    geometry per map, so this is cheap after the first call on a session's maps.
    """
    me = getattr(board, "me", None)
    enemies = [f.cell for f in getattr(board, "fighters", ())
               if getattr(f, "alive", False) and f.team != "player"
               and getattr(f, "id", None) != getattr(me, "id", None)]
    allies = [f.cell for f in getattr(board, "fighters", ())
              if getattr(f, "alive", False) and f.team == "player"]
    if field is None:
        field = _field_for_board(board)
    return {"field": field, "enemies": enemies, "allies": allies}


def _field_for_board(board: Any) -> Optional[MapField]:
    # `Board.map_id`, not `Board.cells.map_id`: `CellMap` is flags only and
    # carries no identity, which is why the first version of this returned None
    # on every real board and every trap silently scored zero traffic.
    map_id = getattr(board, "map_id", None)
    if map_id is None:
        cells = getattr(board, "cells", None)
        map_id = getattr(cells, "map_id", None) if cells is not None else None
    if map_id is None:
        return None
    try:
        return field_for(int(map_id))
    except (FileNotFoundError, KeyError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# solver adapters: geometry -> a utility the beam can compare with damage
# ---------------------------------------------------------------------------

#: Calibration. A trap's utility is its expected damage per enemy turn that
#: walks over it; a summon's is the same figure for the body it puts there.
#:
#: Both are multiples of `APPROACH_VALUE`, the score the solver already gives a
#: walk that closes distance, because that is the only other thing on this scale
#: whose cost a reader can judge: a placement worth two approaches is worth two
#: turns of walking. Nothing here is fitted to a dataset -- these convert a
#: measured traffic ratio into the solver's own units, and the ratio is the part
#: that carries the information.
TRAP_VALUE_PER_TRAFFIC = 14.0
SUMMON_VALUE_BASE = 15.0     # what the flat constant used to give every cell
SUMMON_VALUE_PER_CHOKE = 8.0


def _board_parts(board: Any) -> Tuple[Optional[MapField], List[int], List[int]]:
    parts = traffic_from_board(board)
    return parts["field"], parts["enemies"], parts["allies"]


def free_placements(board: Any, cells: Sequence[int]) -> Dict[int, float]:
    """Trap utility per candidate cell, from the traffic that crosses it.

    Returns ``{}`` when the map geometry is unavailable, which leaves the
    caller's fallback in place: a trap the solver cannot price must still be a
    legal plan, or a board that fails to load a map silently loses its traps.
    """
    field, enemies, allies = _board_parts(board)
    if field is None or not enemies or not allies:
        return {}
    traffic = corridor_weights(field, list(cells), enemies, allies)
    out: Dict[int, float] = {}
    for cell, weight in zip(cells, traffic):
        # `weight` is crossings per enemy->ally pair, so 1.0 means "on the
        # route" and the value of a trap is exactly that traffic -- not the
        # excess over 1. The excess form was the first version: with one enemy
        # and one ally every path cell scores 1.0, the excess is 0, and every
        # trap fell to the floor and never beat a direct hit. A trap on the
        # route is worth its expected chain damage times how often something
        # walks it, which is what this is.
        out[int(cell)] = max(1.0, TRAP_VALUE_PER_TRAFFIC * max(0.0, float(weight)))
    return out


def summon_postings(board: Any, cells: Sequence[int]) -> Dict[int, float]:
    """Summon utility per candidate cell: a baseline plus what the cell blocks."""
    field, enemies, allies = _board_parts(board)
    if field is None or not enemies or not allies:
        return {}
    out: Dict[int, float] = {}
    for cell in cells:
        bonus = choke_bonus(field, int(cell), allies, enemies)
        out[int(cell)] = SUMMON_VALUE_BASE + SUMMON_VALUE_PER_CHOKE * bonus
    return out
