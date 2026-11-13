"""Exact geometry of a 560-cell map, and the threat field built on it.

Why this exists
---------------
`agent/world_context.py` describes a map with twelve numbers: how many exits it
has, whether a door leads somewhere, whether an NPC is standing here.
`combat/grid_dataset._cells` describes a *cell* with twelve numbers, and every
one of them is relative to where the player stands at this instant -- is it
walkable, can I see it, how many steps away is it, is somebody on it.

Neither is a model of a map. Two cells with the same twelve numbers -- six
steps away, walkable, visible -- can have completely different futures: one
opens onto the room, the other is a cul-de-sac that a monster can seal. A fight
is lost in the second cell and the encoder cannot represent the difference.
That is what "superficial understanding" means here, concretely, and it is why
the policy walks into a monster's range instead of around it: nothing in its
input says where the monster's range ends.

This module computes the map instead of describing it.

    distance       exact BFS on the walkable graph, cardinal steps, one point
                   per cell. All 560x560 pairs take 40 ms.
    line of sight  the client's own ``getLine``, which is *origin-exclusive*:
                   ``has_los(a, b)`` checks ``b`` and the cells between, never
                   ``a``. The consequence is measurable, and was measured: on
                   map 100139008, 2,618 of 44,800 sampled pairs disagree with
                   their reverse -- 5.8%. A symmetric LOS matrix would be
                   wrong in exactly the case that matters, a monster standing
                   on a cell you cannot see through. The matrix is directed.
    spectrum       the normalised graph Laplacian. Its low eigenvectors are the
                   shape of the map -- corridors separate from rooms -- with no
                   corridor detector written by hand.
    resistance     effective resistance from the Laplacian pseudo-inverse, the
                   random-walk distance. Large across a wall, small across an
                   open room, which hop count cannot express.
    threat         for a concrete board, the cells each enemy can damage next
                   turn: its reachable set dilated by spell range under the
                   map's own line rule, weighted by expected damage.

Every quantity here is a definition rather than a fitted weight, so the tests
check definitions against the live decoder (`CellMap`) instead of against
stored numbers. Nothing in this module is tuned.

What it costs
-------------
``dist`` is 40 ms per map. ``los`` is the expensive one -- about 15 s for a
full 560x560 directed matrix, because it is 272,000 line walks in Python -- so
it is built lazily on first use and can be cached to ``data/map_los/``. A fight
only ever needs one map at a time, and a session fights on a handful.

    python -m combat.map_field --report
    python -m combat.map_field --warm 100139008 100139009
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field as dc_field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from scipy.linalg import eigh
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, shortest_path

from combat.battle_solver import (NB_CELLS, _CARDINAL_NEIGHBOURS, _POINTS,
                                  cell_point, line_points)
import logging
logger = logging.getLogger(__name__)

MAPS_DIR = Path(__file__).resolve().parent.parent / "data" / "maps"
LOS_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "map_los"

# Distance sentinel. Large enough that `d <= max_range` is false for any real
# spell, small enough to stay inside int16.
UNREACHABLE = 999

# Bumped whenever the meaning of the cached matrix changes. Version 1 omitted
# the diagonal, so every cell that is not see-through was recorded as unable to
# see itself; a cache written by that build is silently wrong and is rejected.
LOS_VERSION = 2

# Spectral channels kept per cell. Eight is the point where the eigenvalue gap
# of a 560-cell Dofus map stops being informative; more coordinates fit noise.
SPECTRAL_DIM = 8


# ---------------------------------------------------------------------------
# The two predicates. Copied from `CellMap` on purpose: the live decoder is the
# only one that has been checked against a real fight, so it is the reference.
# ---------------------------------------------------------------------------

def is_walkable(flags: int) -> bool:
    """`CellMap.walkable`: bit 0 set and bit 2 clear."""
    return (flags & 5) == 1


def sees_through(flags: int) -> bool:
    """`CellMap.sees`: bit 1 set."""
    return (flags & 2) == 2


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def _adjacency(walkable: np.ndarray) -> csr_matrix:
    """Cardinal adjacency over walkable cells only, symmetric."""
    rows: List[int] = []
    cols: List[int] = []
    for cell in range(NB_CELLS):
        if not walkable[cell]:
            continue
        for nxt in _CARDINAL_NEIGHBOURS[cell]:
            if walkable[nxt]:
                rows.append(cell)
                cols.append(nxt)
    count = len(rows)
    data = np.ones(count, dtype=np.int8)
    return csr_matrix((data, (rows, cols)), shape=(NB_CELLS, NB_CELLS))


def _distance_matrix(adj: csr_matrix) -> np.ndarray:
    """All-pairs unweighted distance, int16, `UNREACHABLE` off-component."""
    raw = shortest_path(adj, unweighted=True, directed=False)
    finite = np.isfinite(raw)
    out = np.full((NB_CELLS, NB_CELLS), UNREACHABLE, dtype=np.int16)
    out[finite] = raw[finite].astype(np.int16)
    return out


def _los_matrix(sees: np.ndarray) -> np.ndarray:
    """Directed line of sight, exactly as ``CellMap.has_los`` defines it.

    The origin is never checked -- ``getLine`` is origin-exclusive -- so a cell
    you can stand on but not see through still sees outward. The target *is*
    checked, which is why only see-through columns can ever be true.
    """
    out = np.zeros((NB_CELLS, NB_CELLS), dtype=bool)
    points = [cell_point(cell) for cell in range(NB_CELLS)]
    targets = [cell for cell in range(NB_CELLS) if sees[cell]]
    lookup = _POINTS
    for origin in range(NB_CELLS):
        ox, oy = points[origin]
        row = out[origin]
        for target in targets:
            if origin == target:
                row[target] = True
                continue
            tx, ty = points[target]
            visible = True
            for px, py in line_points(ox, oy, tx, ty):
                cell = lookup.get((px, py))
                if cell is None or not sees[cell]:
                    visible = False
                    break
            row[target] = visible
    # A cell always sees itself, whatever its own `sees` bit says -- `has_los`
    # returns True for `a == b` before it looks at anything. The diagonal is
    # therefore forced rather than computed, and a random sample found the one
    # case where leaving it out disagreed with the live decoder.
    np.fill_diagonal(out, True)
    return out


def _spectral(adj: csr_matrix, dim: int) -> Tuple[np.ndarray, np.ndarray]:
    """Normalised Laplacian spectrum, skipping the trivial constant eigenvector."""
    dense = adj.toarray().astype(np.float64)
    degree = dense.sum(axis=1)
    inv = np.zeros_like(degree)
    nonzero = degree > 0
    inv[nonzero] = 1.0 / np.sqrt(degree[nonzero])
    laplacian = np.eye(NB_CELLS) - (inv[:, None] * dense * inv[None, :])
    values, vectors = eigh(laplacian)
    # `eigh` is ascending; index 0 is the constant vector on a connected graph.
    return values[1:dim + 1], vectors[:, 1:dim + 1]


def _effective_resistance(adj: csr_matrix) -> np.ndarray:
    """Full effective-resistance matrix from the combinatorial Laplacian.

    ``R[i, j] = L+[i,i] + L+[j,j] - 2 L+[i,j]``. Within a component this is the
    expected commute time divided by edge count, i.e. the random-walk distance:
    a corridor between two rooms is "far" even when it is three hops.
    """
    dense = adj.toarray().astype(np.float64)
    degree = dense.sum(axis=1)
    laplacian = np.diag(degree) - dense
    pinv = np.linalg.pinv(laplacian, hermitian=True)
    diagonal = np.diag(pinv)
    return diagonal[:, None] + diagonal[None, :] - 2.0 * pinv


# ---------------------------------------------------------------------------
# The field
# ---------------------------------------------------------------------------

@dataclass
class EnemyThreat:
    """One enemy, reduced to the three things that decide whether it can hit."""

    cell: int
    mp: int
    spells: Tuple[Tuple[int, int, float], ...]  # (min_range, max_range, weight)

    @classmethod
    def from_fighter(cls, fighter: Any) -> Optional["EnemyThreat"]:
        """Build from a live ``Fighter``, or ``None`` when it cannot be built.

        A live board sends ``mp: null`` and ``spell_ids: []`` for every monster,
        so this path is normally closed; ``monster_mech.threat_for`` is what
        opens it, by looking the monster up by id. The ``None`` matters: reading
        a missing MP as zero here would mark every cell safe against a monster
        that reaches eight, which is the one error that loses a fight silently.
        """
        mp = getattr(fighter, "mp", None)
        if mp is None:
            return None
        spells: List[Tuple[int, int, float]] = []
        for spell in getattr(fighter, "spells", ()) or ():
            weight = 0.0
            for effect in getattr(spell, "damage_effects", ()) or ():
                weight += 0.5 * (effect.min_roll + effect.max_roll)
            if weight <= 0:
                continue
            spells.append((int(spell.min_range), int(spell.max_range), float(weight)))
        if not spells:
            return None
        return cls(cell=int(fighter.cell), mp=int(mp), spells=tuple(spells))


@dataclass
class MapField:
    """One map, as geometry."""

    map_id: Optional[int]
    flags: np.ndarray                 # int32[560], the client's own bits
    walkable: np.ndarray              # bool[560]
    sees: np.ndarray                  # bool[560]
    adjacency: csr_matrix
    dist: np.ndarray                  # int16[560,560], UNREACHABLE off-graph
    components: np.ndarray            # int32[560]
    ecc: np.ndarray                   # float32[560]
    closeness: np.ndarray             # float32[560]
    openness: np.ndarray              # float32[560,4] within 2,4,6,8 steps
    resistance: np.ndarray            # float32[560,560]
    spectral: np.ndarray              # float32[560,SPECTRAL_DIM]
    spectral_values: np.ndarray       # float32[SPECTRAL_DIM], ascending
    _los: Optional[np.ndarray] = dc_field(default=None, repr=False)

    # -- construction -------------------------------------------------------

    @classmethod
    def from_flags(cls, flags: Sequence[int], map_id: Optional[int] = None) -> "MapField":
        raw = np.zeros(NB_CELLS, dtype=np.int32)
        count = min(NB_CELLS, len(flags))
        raw[:count] = np.asarray(flags[:count], dtype=np.int32)
        walkable = (raw & 5) == 1
        sees = (raw & 2) == 2
        adjacency = _adjacency(walkable)
        dist = _distance_matrix(adjacency)
        _count, labels = connected_components(adjacency, directed=False)
        components = labels.astype(np.int32)

        reachable = dist != UNREACHABLE
        count_reachable = reachable.sum(axis=1).astype(np.float32)
        with np.errstate(invalid="ignore", divide="ignore"):
            ecc = np.where(reachable, dist, 0).max(axis=1).astype(np.float32)
            total = np.where(reachable, dist, 0).sum(axis=1).astype(np.float32)
            closeness = np.where(total > 0, (count_reachable - 1) / np.maximum(total, 1e-6), 0.0)
        openness = np.stack([(dist <= k).sum(axis=1) for k in (2, 4, 6, 8)],
                            axis=1).astype(np.float32)

        _values, spectral = _spectral(adjacency, SPECTRAL_DIM)
        resistance = _effective_resistance(adjacency).astype(np.float32)

        return cls(map_id=map_id, flags=raw, walkable=walkable, sees=sees,
                   adjacency=adjacency, dist=dist, components=components,
                   ecc=ecc, closeness=closeness.astype(np.float32),
                   openness=openness, resistance=resistance,
                   spectral=spectral.astype(np.float32),
                   spectral_values=_values.astype(np.float32))

    @classmethod
    def from_map_id(cls, map_id: int) -> "MapField":
        path = MAPS_DIR / ("%d.json" % int(map_id))
        if not path.exists():
            raise FileNotFoundError("no map data for %d (%s)" % (map_id, path))
        payload = json.loads(path.read_text(encoding="utf-8"))
        cells = payload.get("cells") or []
        flags = [int(cell.get("l", 0)) if isinstance(cell, dict) else 0 for cell in cells]
        return cls.from_flags(flags, map_id=int(map_id))

    # -- line of sight ------------------------------------------------------

    @property
    def los(self) -> np.ndarray:
        """Directed visibility, built on first use (about 15 s for 560x560)."""
        if self._los is None:
            cached = _load_los(self.map_id)
            self._los = cached if cached is not None else _los_matrix(self.sees)
            if cached is None:
                _store_los(self.map_id, self._los)
        return self._los

    def has_los(self, origin: int, target: int,
                occupied: FrozenSet[int] = frozenset()) -> bool:
        """``CellMap.has_los`` with actors, checked against the precomputed walls.

        An actor blocks only if it sits strictly between the endpoints, which is
        the same rule the live decoder uses (``index < last``).
        """
        if origin == target:
            return True
        if not self.los[origin, target]:
            return False
        if not occupied:
            return True
        ox, oy = cell_point(origin)
        tx, ty = cell_point(target)
        line = line_points(ox, oy, tx, ty)
        last = len(line) - 1
        for index, point in enumerate(line):
            if index >= last:
                break
            cell = _POINTS.get(point)
            if cell is not None and cell in occupied:
                return False
        return True

    # -- movement -----------------------------------------------------------

    def reachable_mask(self, origin: int, mp: float,
                       occupied: FrozenSet[int] = frozenset()) -> np.ndarray:
        """Cells reachable with ``mp`` points, actors not enterable."""
        out = np.zeros(NB_CELLS, dtype=bool)
        if not (0 <= origin < NB_CELLS) or not self.walkable[origin]:
            return out
        out[origin] = True
        frontier = [origin]
        cost = 0.0
        while frontier and cost + 1.0 <= mp + 1e-9:
            cost += 1.0
            nxt_frontier: List[int] = []
            for cell in frontier:
                for nxt in _CARDINAL_NEIGHBOURS[cell]:
                    if out[nxt] or not self.walkable[nxt] or nxt in occupied:
                        continue
                    out[nxt] = True
                    nxt_frontier.append(nxt)
            frontier = nxt_frontier
        return out

    # -- threat -------------------------------------------------------------

    def coverage(self, origins: Iterable[int], min_range: int,
                 max_range: int) -> np.ndarray:
        """Cells any of ``origins`` can damage with a spell of this range.

        Walls only: actors are not treated as blockers here, because this is the
        field a *learning* target needs and it must not depend on where anybody
        happens to stand. ``danger_at`` is the exact per-cell question.
        """
        out = np.zeros(NB_CELLS, dtype=bool)
        cells = [int(c) for c in origins if 0 <= int(c) < NB_CELLS]
        if not cells:
            return out
        rows = self.dist[np.asarray(cells, dtype=np.int32)]
        band = (rows >= int(min_range)) & (rows <= int(max_range)) & (rows != UNREACHABLE)
        visible = self.los[np.asarray(cells, dtype=np.int32)]
        return (band & visible).any(axis=0)

    def threat_field(self, enemies: Sequence[EnemyThreat],
                     occupied: FrozenSet[int] = frozenset()) -> np.ndarray:
        """Expected damage per cell next turn, summed over enemies and spells.

        This is the quantity the reward was trying to approximate by hand: a
        cell is bad in proportion to how much damage can land on it, and the
        field is zero wherever no enemy can reach. Standing in a zero cell while
        still being able to cast is the whole of "avoid instead of walking in".
        """
        field = np.zeros(NB_CELLS, dtype=np.float32)
        for enemy in enemies:
            reach = self.reachable_mask(enemy.cell, enemy.mp, occupied)
            origins = np.flatnonzero(reach)
            if not origins.size:
                continue
            rows = self.dist[origins]
            visible = self.los[origins]
            for min_range, max_range, weight in enemy.spells:
                band = ((rows >= min_range) & (rows <= max_range)
                        & (rows != UNREACHABLE) & visible)
                field += weight * band.any(axis=0).astype(np.float32)
        return field

    def danger_at(self, cell: int, enemies: Sequence[EnemyThreat],
                  occupied: FrozenSet[int] = frozenset()) -> float:
        """Expected damage landing on ``cell`` next turn, actors included.

        ``threat_field`` ignores actors for speed and for stability as a target;
        this is the exact version for a single cell, which is what a live
        decision asks. It re-checks line of sight per candidate origin so a
        monster standing in the way counts.
        """
        if not (0 <= cell < NB_CELLS):
            return 0.0
        total = 0.0
        for enemy in enemies:
            reach = self.reachable_mask(enemy.cell, enemy.mp, occupied)
            origins = np.flatnonzero(reach)
            if not origins.size:
                continue
            distance = self.dist[origins, cell]
            for min_range, max_range, weight in enemy.spells:
                candidates = origins[(distance >= min_range) & (distance <= max_range)
                                     & (distance != UNREACHABLE)]
                if any(self.has_los(int(origin), cell, occupied) for origin in candidates):
                    total += weight
        return total

    def safe_cells(self, enemies: Sequence[EnemyThreat], mp: float,
                   origin: int, occupied: FrozenSet[int] = frozenset()) -> np.ndarray:
        """Reachable cells with zero incoming damage, best first.

        Sorted by ``closeness`` so the answer is a cell that is both safe and
        still connected to the rest of the map -- a corner nobody can reach is
        safe and useless.
        """
        reach = self.reachable_mask(origin, mp, occupied)
        if not reach.any():
            return np.zeros(0, dtype=np.int32)
        field = self.threat_field(enemies, occupied)
        cells = np.flatnonzero(reach & (field <= 0.0))
        if not cells.size:
            return cells
        return cells[np.argsort(-self.closeness[cells])]

    # -- tactical intent & resistance gradient -------------------------------

    def resistance_gradient(self, origin: int, dest: int, target: int) -> float:
        """Difference in effective resistance to target between dest and origin."""
        if not (0 <= origin < NB_CELLS and 0 <= dest < NB_CELLS and 0 <= target < NB_CELLS):
            return 0.0
        return float(self.resistance[dest, target] - self.resistance[origin, target])

    def tactical_intent(self, origin: int, final: int, target: int,
                        enemy_cells: Sequence[int] = ()) -> int:
        """Classify a move into the 5-class tactical cell-intent basis.

        0: close_to_optimal_range (closing distance to target)
        1: step_to_break_los (stepping behind obstacle breaking enemy directed LOS)
        2: lateral_flank_los (flanking to gain LOS or lateral step along resistance gradient)
        3: hold (stationary on cell)
        4: kite_max_mp (retreating / kiting)
        """
        origin = int(origin)
        final = int(final)
        target = int(target)
        if final == origin:
            return 3  # hold

        # 1. Step to break enemy directed line of sight
        threats = [int(c) for c in enemy_cells if 0 <= int(c) < NB_CELLS]
        if not threats and 0 <= target < NB_CELLS and target != origin:
            threats = [target]
        if threats:
            had_los = [c for c in threats if self.has_los(c, origin)]
            have_los = [c for c in threats if self.has_los(c, final)]
            if target in threats and self.has_los(target, origin) and not self.has_los(target, final):
                return 1  # step_to_break_los
            if had_los and not have_los:
                return 1  # step_to_break_los

        # 2. Check if move gained line of sight to target
        if 0 <= target < NB_CELLS:
            if not self.has_los(origin, target) and self.has_los(final, target):
                return 2  # lateral_flank_los

        # 3. Distance and resistance gradient check
        d_before = int(self.dist[origin, target]) if (0 <= origin < NB_CELLS and 0 <= target < NB_CELLS) else UNREACHABLE
        d_after = int(self.dist[final, target]) if (0 <= final < NB_CELLS and 0 <= target < NB_CELLS) else UNREACHABLE
        if d_before == UNREACHABLE or d_after == UNREACHABLE:
            ox, oy = cell_point(origin)
            fx, fy = cell_point(final)
            tx, ty = cell_point(target)
            d_before = max(abs(ox - tx), abs(oy - ty))
            d_after = max(abs(fx - tx), abs(fy - ty))

        if d_after == d_before:
            return 2  # lateral_flank_los
        if d_after < d_before:
            return 0  # close_to_optimal_range
        return 4  # kite_max_mp

    # -- encoding -----------------------------------------------------------

    def encoding(self) -> np.ndarray:
        """``[560, 18]`` static geometry channels, for a cell encoder.

        This is the map's own description, independent of where anybody stands.
        The twelve channels in ``grid_dataset._cells`` answer "what does this
        cell look like from here"; these answer "what is this cell", and the two
        together are what the model was missing.
        """
        live = self.components[self.walkable]
        main = int(np.bincount(live).argmax()) if live.size else 0
        in_main = (self.components == main).astype(np.float32)
        # Resistance is unbounded; scale by the 95th percentile so the channel
        # is comparable across maps without a per-map constant the model has to
        # learn separately.
        resistance = self.resistance.max(axis=1)
        scale = float(np.percentile(resistance, 95)) or 1.0
        return np.stack([
            self.walkable.astype(np.float32),
            self.sees.astype(np.float32),
            in_main,
            self.ecc / max(1.0, float(self.ecc.max())),
            self.closeness,
            self.openness[:, 0] / 20.0,
            self.openness[:, 1] / 40.0,
            self.openness[:, 2] / 60.0,
            self.openness[:, 3] / 80.0,
            np.minimum(1.0, resistance / scale),
        ] + [self.spectral[:, i] for i in range(SPECTRAL_DIM)], axis=1).astype(np.float32)

    # -- diagnostics --------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        walkable = int(self.walkable.sum())
        # Components are counted over walkable cells only. Every non-walkable
        # cell is a singleton by construction -- the graph has no edges to it --
        # so counting all 560 reported "142 components" for a map whose walkable
        # floor is one piece, which is a number that says nothing.
        live = self.components[self.walkable]
        sizes = np.bincount(live) if live.size else np.zeros(1, dtype=np.int64)
        sizes = sizes[sizes > 0]
        main = int(sizes.max()) if sizes.size else 0
        degree = np.asarray(self.adjacency.sum(axis=1)).ravel()
        return {
            "map_id": self.map_id,
            "walkable": walkable,
            "see_through": int(self.sees.sum()),
            "components": int(sizes.size),
            "main_component": main,
            "off_main": walkable - main,
            "mean_degree": float(degree.sum() / max(1, walkable)),
            "diameter": int(self.ecc.max()) if walkable else 0,
            "mean_openness_4": float(self.openness[:, 1].mean()),
            "dead_ends": int(((degree == 2) & self.walkable).sum()),
        }

    def danger_profile(self, enemy_cell: int, mp: int, min_range: int,
                       max_range: int) -> List[Dict[str, Any]]:
        """Danger as a function of the one spatial channel the model has.

        ``grid_dataset._cells`` gives the encoder a cell's *distance* from the
        player (channel 6) and nothing else that describes space -- the other
        channels are flags, occupancy and raw coordinates. Distance is therefore
        the only thing a policy can use to reason about where an enemy's reach
        ends. This tabulates, at every distance from one enemy, how many cells
        that enemy can actually damage next turn.

        If both numbers are non-zero at the same distance, then distance does
        not determine danger, and no amount of training on that channel can
        recover the difference: the information is not in the input.
        """
        enemy = EnemyThreat(cell=int(enemy_cell), mp=int(mp),
                            spells=((int(min_range), int(max_range), 1.0),))
        threat = self.threat_field([enemy])
        distances = self.dist[int(enemy_cell)]
        rows: List[Dict[str, Any]] = []
        for distance in range(1, 13):
            band = self.walkable & (distances == distance)
            count = int(band.sum())
            if not count:
                continue
            values = threat[band]
            rows.append({
                "distance": distance,
                "cells": count,
                "threatened": int((values > 0).sum()),
                "safe": int((values <= 0).sum()),
                "mean": float(values.mean()),
            })
        return rows


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

@lru_cache(maxsize=8)
def field_for(map_id: int) -> MapField:
    """Per-process cache. Eight maps is more than one session fights on."""
    return MapField.from_map_id(map_id)


def _cache_path(map_id: Optional[int]) -> Optional[Path]:
    if map_id is None:
        return None
    return LOS_CACHE_DIR / ("%d.npz" % int(map_id))


def _load_los(map_id: Optional[int]) -> Optional[np.ndarray]:
    path = _cache_path(map_id)
    if path is None or not path.exists():
        return None
    try:
        with np.load(path) as data:
            if int(data["version"]) != LOS_VERSION:
                return None
            out = data["los"]
        return out if out.shape == (NB_CELLS, NB_CELLS) else None
    except Exception:
        return None


def _store_los(map_id: Optional[int], los: np.ndarray) -> None:
    path = _cache_path(map_id)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, los=los, version=np.int32(LOS_VERSION))
    except Exception:
        # A read-only tree is not a reason to fail a decision.
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _report(limit: int) -> int:
    files = sorted(MAPS_DIR.glob("*.json"))
    if not files:
        logger.debug("no maps in %s" % MAPS_DIR)
        return 1
    rows = []
    for path in files[:limit]:
        try:
            field = MapField.from_map_id(int(path.stem))
        except Exception as exc:  # pragma: no cover - a bad file is reported
            logger.debug("%-14s unreadable: %s" % (path.stem, exc))
            continue
        rows.append(field.summary())
    if not rows:
        return 1
    logger.debug("%-14s %8s %8s %6s %7s %8s %9s %8s" % (
        "map", "walk", "see", "comp", "deg", "diam", "open@4", "deadend"))
    for row in rows[:20]:
        logger.debug("%-14s %8d %8d %6d %7.2f %8d %9.2f %8d" % (
            row["map_id"], row["walkable"], row["see_through"], row["components"],
            row["mean_degree"], row["diameter"], row["mean_openness_4"],
            row["dead_ends"]))
    logger.debug("\n%d maps, mean walkable %.0f, mean diameter %.1f" % (
        len(rows), float(np.mean([r["walkable"] for r in rows])),
        float(np.mean([r["diameter"] for r in rows]))))

    # The argument for the whole module, as a number: distance is the only
    # spatial channel the model gets, and danger is not a function of it.
    probe = field_for(int(rows[0]["map_id"]))
    walkable = np.flatnonzero(probe.walkable)
    if walkable.size:
        me = int(walkable[np.argmax(probe.closeness[walkable])])
        profile = probe.danger_profile(me, mp=5, min_range=1, max_range=4)
        logger.debug("\nmap %s: one enemy standing on %d, 5 MP, a 1-4 range spell"
              % (probe.map_id, me))
        logger.debug("  %-9s %6s %11s %6s %7s" % ("distance", "cells", "threatened", "safe", "mean"))
        for row in profile:
            logger.debug("  %-9d %6d %11d %6d %7.2f" % (
                row["distance"], row["cells"], row["threatened"], row["safe"], row["mean"]))
        mixed = [row for row in profile if row["threatened"] and row["safe"]]
        logger.debug("  %d of %d distances hold both safe and threatened cells --"
              % (len(mixed), len(profile)))
        logger.debug("  distance alone cannot decide whether standing there is safe.")
    return 0


def _warm(map_ids: Sequence[int]) -> int:
    for map_id in map_ids:
        field = field_for(int(map_id))
        _ = field.los
        logger.debug("%d: los %s" % (map_id, "cached" if _cache_path(map_id).exists() else "not written"))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="summarise the map corpus")
    parser.add_argument("--limit", type=int, default=200, help="maps to summarise")
    parser.add_argument("--warm", nargs="*", type=int, default=None,
                        help="build and cache the line-of-sight matrix for these maps")
    args = parser.parse_args(argv)
    if args.warm:
        return _warm(args.warm)
    return _report(args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
