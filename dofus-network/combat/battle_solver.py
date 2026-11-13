"""Turn-level combat solving on top of the live ``fight.state`` board.

Geometry is ported from the 1.74.9 client bundle rather than guessed:

* cell id -> map point: ``t = id % 14 - id // 28``, ``(t + 19, t + id // 14)``;
* fight walkability: ``(cell.l & 5) == 1``;
* line of sight: the client's Bresenham variant, with actors blocking every line
  cell except the target and bit 1 of the cell flags required on every line cell;
* spell ranges: diamond, cross, or diagonals, radius ``min_range..max_range``;
* fight movement is cardinal only, one MP per step: the client's own fight
  reachable-zone walker calls ``getNeighbourCells(cell, false)``. The sqrt(2)
  diagonals in the bundle belong to the overworld pathfinder, not fights.

Damage uses ``DofusDamageCalculator`` (the validated formula), fed with live
effect ids and live stats. Effect ids come from the client's action table:
96-100 damage water/earth/air/fire/neutral, 91-95 the same as steal, 108/143
heal. Effect rolls follow the client's ``getMinMaxDamageFromRawEffect``:
``diceNum`` is the minimum and ``diceSide`` the maximum, with a zero
``diceSide`` meaning a fixed roll.

Positional mechanics use the same live effect ids: teleport (4) is planned as
an alternate cast origin, push (5) and pull (6) move the target along the line
from the caster, AP/MP loss (101/133/168, 169) is scored as denied enemy
economy and AP/MP gain (111/120, 128) extends this turn's budget. Traps,
glyphs, swap, carry, states and area zones are still not planned: a spell that
only has those effects is reported in ``notes`` instead of being treated as a
no-op. The executor must still validate every chosen action against the client
and re-plan on disagreement.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field, replace
from functools import cached_property, lru_cache
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from combat.damage_calculator import (
    CharacterStats,
    DofusDamageCalculator,
    SpellDamageResult,
    TargetStats,
)
from data.game_data_extractor import DamageEffect, SpellInfo
from combat import threat


def _trade_module():
    """`combat.cell_effects`, imported on first use.

    It imports `_CARDINAL_NEIGHBOURS` from this module, so a top-level import in
    either direction would fail depending on which module Python entered first.
    The functions it provides are called only while planning a trap or summon,
    which is never at import time.
    """
    from combat import cell_effects

    return cell_effects

# Client action table, 1.74.9. Damage/steal ids carry their element by id; the
# generic 95/100 ids are the element-less variants, treated as neutral.
ELEMENT_BY_EFFECT: Dict[int, Tuple[str, str]] = {
    91: ("steal", "WATER"),
    92: ("steal", "EARTH"),
    93: ("steal", "AIR"),
    94: ("steal", "FIRE"),
    95: ("steal", "NEUTRAL"),
    96: ("damage", "WATER"),
    97: ("damage", "EARTH"),
    98: ("damage", "AIR"),
    99: ("damage", "FIRE"),
    100: ("damage", "NEUTRAL"),
    108: ("heal", "HEAL"),
    143: ("heal", "HEAL"),
}
HEAL_EFFECT_IDS = frozenset({81, 108, 143})
# Verified against the client's ACTION_* table (2026-10-25): a spell effect's
# effectId is that table's action id, and the live payload carries ids, not
# text. These are the tactical mechanics the one-turn damage planner does not
# execute yet; classifying them keeps them out of "unknown effects" and gives
# the solver and duel a generic vocabulary instead of per-spell handling.
UTILITY_EFFECT_KINDS = {
    4: "teleport",
    5: "push",
    6: "pull",
    8: "swap",
    50: "carry",
    84: "ap_steal",
    101: "ap_loss",
    102: "ap_use",
    104: "tackled",
    111: "ap_gain",
    120: "ap_gain",
    128: "mp_gain",
    132: "cleanse",
    133: "ap_loss",
    168: "ap_loss",
    169: "mp_loss",
    180: "summon",
    181: "summon",
    400: "trap",
    401: "glyph",
    402: "glyph",
    1008: "summon",
    1011: "summon",
    1041: "pushed",
    1042: "pulled",
}
# Effects the one-turn planner can price with real board state. Teleport is a
# cast origin, push/pull displace the target, AP/MP changes alter the economy,
# traps/glyphs trigger tactical chains, and summons extend the tactical board;
# the remaining utility ids stay reported as unmodelled.
PLANNED_MECHANIC_KINDS = frozenset({
    "teleport", "push", "pull", "ap_loss", "ap_steal",
    "mp_loss", "ap_gain", "mp_gain", "trap", "glyph", "summon",
})

#: The strategic buckets a candidate list is diversified over, in the order the
#: solver guarantees a slot for each. `trap_setup`, `summon_control`,
#: `buff_setup` and `kite_defend` are not separate buckets here: a trap and a
#: summon are both `setup` (see `_classify_plan`), and displacement covers the
#: kiting case. Splitting them would mean classifying by *which* mechanic fired,
#: which is one line in `_classify_plan` when a caller needs it.
#:
#: The order is a priority, not a quality ranking: with `k=6` the budget is five
#: action slots and there are four action buckets, so the first round fills four
#: and the second round adds one more before the leftover fallback. `damage` is
#: first because on a board where nothing else applies it is the only bucket.
BUCKET_ORDER = ("damage", "setup", "displacement", "boss")
_EPS = 1e-9


@dataclass
class CombatConfig:
    """All tunable combat constants. Derived from live board data where
    possible; the values below are the 1.74.9 bundle defaults and may be
    overridden via environment variables or a passed config before an upgrade.

    Environment variables (all optional):
      DOFUS_COMBAT_NB_CELLS          tactical grid cell count
      DOFUS_COMBAT_TELEPORT_POSITIONS cast positions to keep per teleport
      DOFUS_COMBAT_CAST_POSITIONS    cast positions to keep per spell/target
      DOFUS_COMBAT_AP_DENIAL_WEIGHT  weight of one denied AP
      DOFUS_COMBAT_MP_DENIAL_WEIGHT  weight of one denied MP
      DOFUS_COMBAT_AP_GAIN_WEIGHT    weight of one gained AP
      DOFUS_COMBAT_MP_GAIN_WEIGHT    weight of one gained MP
      DOFUS_COMBAT_PUSH_WEIGHT       weight of one forced cell
      DOFUS_COMBAT_HEAL_WEIGHT       weight of one healed HP
      DOFUS_COMBAT_KILL_BONUS        bonus per kill
      DOFUS_COMBAT_LRU_CACHE_LINE    lru_cache maxsize for line_points
      DOFUS_COMBAT_LRU_CACHE_PATH    lru_cache maxsize for path finding
    """

    nb_cells: int = 560
    teleport_positions: int = 8
    cast_positions: int = 3
    ap_denial_weight: float = 6.0
    mp_denial_weight: float = 3.0
    ap_gain_weight: float = 5.0
    mp_gain_weight: float = 1.5
    push_weight: float = 0.75
    heal_weight: float = 0.8
    kill_bonus: float = 0.35
    lru_cache_line: int = 16384
    lru_cache_path: int = 4096

    @classmethod
    def from_env(cls) -> "CombatConfig":
        import os as _os

        def _env_int(name: str, default: int) -> int:
            try:
                return int(_os.environ[name])
            except (KeyError, ValueError):
                return default

        def _env_float(name: str, default: float) -> float:
            try:
                return float(_os.environ[name])
            except (KeyError, ValueError):
                return default

        return cls(
            nb_cells=_env_int("DOFUS_COMBAT_NB_CELLS", 560),
            teleport_positions=_env_int("DOFUS_COMBAT_TELEPORT_POSITIONS", 8),
            cast_positions=_env_int("DOFUS_COMBAT_CAST_POSITIONS", 3),
            ap_denial_weight=_env_float("DOFUS_COMBAT_AP_DENIAL_WEIGHT", 6.0),
            mp_denial_weight=_env_float("DOFUS_COMBAT_MP_DENIAL_WEIGHT", 3.0),
            ap_gain_weight=_env_float("DOFUS_COMBAT_AP_GAIN_WEIGHT", 5.0),
            mp_gain_weight=_env_float("DOFUS_COMBAT_MP_GAIN_WEIGHT", 1.5),
            push_weight=_env_float("DOFUS_COMBAT_PUSH_WEIGHT", 0.75),
            heal_weight=_env_float("DOFUS_COMBAT_HEAL_WEIGHT", 0.8),
            kill_bonus=_env_float("DOFUS_COMBAT_KILL_BONUS", 0.35),
            lru_cache_line=_env_int("DOFUS_COMBAT_LRU_CACHE_LINE", 16384),
            lru_cache_path=_env_int("DOFUS_COMBAT_LRU_CACHE_PATH", 4096),
        )


# Single shared config instance; override with CombatConfig.from_env() or
# construct a custom instance before an upgrade.
COMBAT_CONFIG = CombatConfig.from_env()

NB_CELLS = COMBAT_CONFIG.nb_cells
TELEPORT_POSITIONS = COMBAT_CONFIG.teleport_positions
CAST_POSITIONS = COMBAT_CONFIG.cast_positions
AP_DENIAL_WEIGHT = COMBAT_CONFIG.ap_denial_weight
MP_DENIAL_WEIGHT = COMBAT_CONFIG.mp_denial_weight
AP_GAIN_WEIGHT = COMBAT_CONFIG.ap_gain_weight
MP_GAIN_WEIGHT = COMBAT_CONFIG.mp_gain_weight
PUSH_WEIGHT = COMBAT_CONFIG.push_weight
HEAL_WEIGHT = COMBAT_CONFIG.heal_weight
KILL_BONUS = COMBAT_CONFIG.kill_bonus
# What one cell of closing distance is worth when nothing can be cast this turn.
#
# The beam folds a walk into a cast, so a turn with no reachable cast produced no plan
# at all -- measured on the captured corpus: 114 of 152 turns on our own side came back
# as a single `pass`, and the live policy then hands the turn to the actor, which is the
# half measured 0.0875 worse. A level-2 character spends most of its turns closing
# distance, so "nothing to do" is the common case, not the edge.
#
# Kept below one point of damage on purpose: a walk must never outrank a cast that
# actually lands, it only has to beat standing still.
APPROACH_VALUE = 0.5
LRU_CACHE_LINE = COMBAT_CONFIG.lru_cache_line
LRU_CACHE_PATH = COMBAT_CONFIG.lru_cache_path


_RAW_POINTS: Tuple[Tuple[int, int], ...] = tuple(
    (c % 14 - c // 28 + 19, c % 14 - c // 28 + c // 14)
    for c in range(NB_CELLS)
)


def cell_point(cell_id: int) -> Tuple[int, int]:
    """Cell id to map point, exactly as the client's getMapPointFromCellId."""
    if 0 <= cell_id < NB_CELLS:
        return _RAW_POINTS[cell_id]
    t = cell_id % 14 - cell_id // 28
    return t + 19, t + cell_id // 14


_POINTS: Dict[Tuple[int, int], int] = {cell_point(c): c for c in range(NB_CELLS)}

_CARDINAL_DIRS = ((0, 1), (-1, 0), (0, -1), (1, 0))
_DIAGONAL_DIRS = ((1, 1), (-1, 1), (-1, -1), (1, -1))


class _NeighbourTable(tuple):
    """Contiguous tuple of cell neighbours; O(1) direct indexing + .get() fallback."""

    def get(self, cell: int, default: Tuple[int, ...] = ()) -> Tuple[int, ...]:
        if 0 <= cell < len(self):
            return self[cell]
        return default


def _neighbour_table(diagonal: bool) -> _NeighbourTable:
    """Precomputed cell neighbours; zero coordinate math in BFS hot loops."""
    directions = _CARDINAL_DIRS + (_DIAGONAL_DIRS if diagonal else ())
    rows: List[Tuple[int, ...]] = []
    for cell in range(NB_CELLS):
        x, y = cell_point(cell)
        rows.append(tuple(
            neighbour for dx, dy in directions
            if (neighbour := _POINTS.get((x + dx, y + dy))) is not None
        ))
    return _NeighbourTable(rows)


_CARDINAL_NEIGHBOURS = _neighbour_table(False)
_DIAGONAL_NEIGHBOURS = _neighbour_table(True)


def _js_round(value: float) -> float:
    """Math.round semantics (half toward +infinity), not Python's banker's rounding."""
    return math.floor(value + 0.5)


@lru_cache(maxsize=LRU_CACHE_LINE)
def line_points(x1: int, y1: int, x2: int, y2: int) -> List[Tuple[int, int]]:
    """Port of the client's getLine: origin-exclusive, target-inclusive cells.

    Pure in its four coordinates; the solver asks for the same pair on many
    turns, and the cached list is only read by ``has_los``.
    """
    out: List[Tuple[int, int]] = []
    sx, sy = x1 + 0.5, y1 + 0.5
    cx, cy = x2 + 0.5, y2 + 0.5
    if abs(sx - cx) == abs(sy - cy):
        steps = int(abs(sx - cx))
        dx = 1 if cx > sx else -1
        dy = 1 if cy > sy else -1
        for _ in range(steps):
            out.append((math.floor(sx + dx), math.floor(sy + dy)))
            sx += dx
            sy += dy
        return out

    along, across = "y", "x"
    if abs(sx - cx) > abs(sy - cy):
        along, across = "x", "y"
    pos = {"x": sx, "y": sy}
    end = {"x": cx, "y": cy}
    steps = int(abs(pos[along] - end[along]))
    step = {"x": 0.0, "y": 0.0}
    step[along] = 1.0 if end[along] >= pos[along] else -1.0
    if steps == 0:
        return out
    step[across] = (abs(pos[across] - end[across]) / steps
                    if end[across] > pos[across]
                    else -abs(pos[across] - end[across]) / steps)
    for _ in range(steps):
        low = _js_round(1e4 * pos[across] + 5e3 * step[across]) / 1e4
        high = _js_round(1e4 * pos[across] + 1.5e4 * step[across]) / 1e4
        if math.floor(low) == math.floor(high):
            cells = [math.floor(pos[across] + step[across])]
            if low == cells[0] and high < cells[0]:
                cells = [math.ceil(pos[across] + step[across])]
            if high == cells[0] and low < cells[0]:
                cells = [math.ceil(pos[across] + step[across])]
        elif math.ceil(low) == math.ceil(high):
            cells = [math.ceil(pos[across] + step[across])]
            if low == cells[0] and high < cells[0]:
                cells = [math.floor(pos[across] + step[across])]
            if high == cells[0] and low < cells[0]:
                cells = [math.floor(pos[across] + step[across])]
        else:
            cells = [math.floor(low), math.floor(high)]
        for value in cells:
            if along == "x":
                out.append((math.floor(pos["x"] + step["x"]), value))
            else:
                out.append((value, math.floor(pos["y"] + step["y"])))
        pos["x"] += step["x"]
        pos["y"] += step["y"]
    return out


@lru_cache(maxsize=LRU_CACHE_PATH)
def _range_ring(limit: int, origin: int, min_range: int,
                max_range: int, cast_in_line: bool,
                cast_in_diagonal: bool) -> FrozenSet[int]:
    """Cached spell range ring for one geometry.

    A ring is pure geometry: the cell flags decide walkability and line of
    sight, never which cells lie at a Manhattan radius. Only the map length
    bounds it, so keying by that length shares rings across every 560-cell map
    instead of caching a private copy per map and thrashing this cache.
    """
    if max_range < 0 or min_range > max_range:
        return frozenset()
    x, y = cell_point(origin)
    limit = min(limit, NB_CELLS)
    out: List[int] = []
    append = out.append

    if min_range == 0:
        cell = _POINTS.get((x, y))
        if cell is not None and cell < limit:
            append(cell)
    for radius in range(max(1, min_range), max_range + 1):
        if cast_in_line and cast_in_diagonal:
            points = (
                (x - radius, y), (x + radius, y),
                (x, y - radius), (x, y + radius),
                (x - radius, y - radius), (x - radius, y + radius),
                (x + radius, y - radius), (x + radius, y + radius),
            )
        elif cast_in_line:
            points = ((x - radius, y), (x + radius, y),
                      (x, y - radius), (x, y + radius))
        elif cast_in_diagonal:
            points = ((x - radius, y - radius), (x - radius, y + radius),
                      (x + radius, y - radius), (x + radius, y + radius))
        else:
            # Diamond perimeter: |dx| + |dy| == radius. Inlined because the
            # solver asks for a ring for every reachable cell.
            for dx in range(-radius, radius + 1):
                dy = radius - abs(dx)
                cell = _POINTS.get((x + dx, y - dy))
                if cell is not None and cell < limit:
                    append(cell)
                if dy:
                    cell = _POINTS.get((x + dx, y + dy))
                    if cell is not None and cell < limit:
                        append(cell)
            continue
        for px, py in points:
            cell = _POINTS.get((px, py))
            if cell is not None and cell < limit:
                append(cell)
    return frozenset(out)


@dataclass(frozen=True)
class CellMap:
    """Cell flags for one map, in the client's own bit encoding."""

    flags: Tuple[int, ...]

    @classmethod
    def from_board(cls, payload: Dict[str, Any]) -> "CellMap":
        raw = payload.get("cells") or []
        # The page sends 560 integers; copying them straight into the tuple
        # avoids 560 int() calls per decision. Anything else keeps the old
        # coercion (non-numeric entries become 0).
        if all(type(v) is int for v in raw):
            return cls(tuple(raw))
        return cls(tuple(int(v) if isinstance(v, (int, float)) else 0 for v in raw))

    def valid(self, cell: int) -> bool:
        return 0 <= cell < len(self.flags) and cell < NB_CELLS

    def walkable(self, cell: int) -> bool:
        return self.valid(cell) and (self.flags[cell] & 5) == 1

    def sees(self, cell: int) -> bool:
        return self.valid(cell) and (self.flags[cell] & 2) == 2

    def distance(self, a: int, b: int) -> int:
        ax, ay = cell_point(a)
        bx, by = cell_point(b)
        return abs(ax - bx) + abs(ay - by)

    def neighbours(self, cell: int, diagonal: bool = False) -> List[int]:
        if 0 <= cell < NB_CELLS:
            table = _DIAGONAL_NEIGHBOURS if diagonal else _CARDINAL_NEIGHBOURS
            return [c for c in table[cell] if self.valid(c)]
        return []

    def has_los(self, a: int, b: int, occupied: FrozenSet[int] = frozenset()) -> bool:
        """The client's line check: actors and non-seeing cells block the line."""
        if a == b:
            return True
        ax, ay = cell_point(a)
        bx, by = cell_point(b)
        line = line_points(ax, ay, bx, by)
        last = len(line) - 1
        flags = self.flags
        limit = min(len(flags), NB_CELLS)
        for index, point in enumerate(line):
            cell = _POINTS.get(point)
            if cell is None or cell >= limit:
                return False
            if index < last and cell in occupied:
                return False
            if (flags[cell] & 2) != 2:
                return False
        return True

    def slide(self, origin: int, target: int, distance: int,
              blocked: FrozenSet[int] = frozenset(),
              toward: bool = False) -> int:
        """Where a push (away) or pull (toward) leaves ``target``.

        The client moves the target one point at a time along the sign of the
        line from the caster; the first wall, free-cell violation or the
        caster's own cell stops it. A pull never enters the caster's cell.
        """
        if distance <= 0 or origin == target:
            return target
        ox, oy = cell_point(origin)
        tx, ty = cell_point(target)
        dx = 0 if tx == ox else (1 if tx > ox else -1)
        dy = 0 if ty == oy else (1 if ty > oy else -1)
        if toward:
            dx, dy = -dx, -dy
        if dx == 0 and dy == 0:
            return target
        cell = target
        for _ in range(int(distance)):
            x, y = cell_point(cell)
            nxt = _POINTS.get((x + dx, y + dy))
            if nxt is None or not self.walkable(nxt):
                break
            if nxt == origin or nxt in blocked:
                break
            cell = nxt
        return cell

    def reachable(self, start: int, mp: float,
                  occupied: FrozenSet[int] = frozenset()) -> Dict[int, float]:
        """Cells reachable with ``mp`` points; occupied cells are not enterable.

        Cardinal steps only. The client's fight reachable-zone walker uses
        ``getNeighbourCells(cell, false)`` and charges exactly one MP per cell.
        """
        if not self.walkable(start):
            return {}
        # Every step costs exactly one MP, so breadth-first order is already the
        # cheapest order. A heap here paid O(V log V) for nothing.
        best: Dict[int, float] = {start: 0.0}
        queue: deque = deque([start])
        while queue:
            cell = queue.popleft()
            cost = best[cell]
            if cost + 1.0 > mp + _EPS:
                continue
            for nxt in _CARDINAL_NEIGHBOURS[cell]:
                if not self.walkable(nxt) or nxt in occupied:
                    continue
                if nxt in best:
                    continue
                best[nxt] = cost + 1.0
                queue.append(nxt)
        return best

    def range_cells(self, origin: int, min_range: int, max_range: int,
                    cast_in_line: bool = False,
                    cast_in_diagonal: bool = False) -> FrozenSet[int]:
        """Port of getSpellRange: a ring, not a filled area.

        Rings depend only on the geometry and the map length, so all maps of
        the same size share them.
        """
        return _range_ring(len(self.flags), origin, min_range, max_range,
                           cast_in_line, cast_in_diagonal)


# ---------------------------------------------------------------------------
# board parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveEffect:
    effect_id: int
    min_roll: int
    max_roll: int
    target_id: Optional[int] = None
    duration: Optional[int] = None

    @cached_property
    def kind(self) -> str:
        if self.effect_id in HEAL_EFFECT_IDS:
            return "heal"
        entry = ELEMENT_BY_EFFECT.get(self.effect_id)
        if entry:
            return entry[0]
        return UTILITY_EFFECT_KINDS.get(self.effect_id, "other")

    @cached_property
    def element(self) -> Optional[str]:
        if self.effect_id in HEAL_EFFECT_IDS:
            return "HEAL"
        entry = ELEMENT_BY_EFFECT.get(self.effect_id)
        return entry[1] if entry else None


@dataclass(frozen=True)
class LiveSpell:
    id: int
    name: str
    ap_cost: int
    min_range: int
    max_range: int
    cast_in_line: bool
    cast_in_diagonal: bool
    requires_los: bool
    need_free_cell: bool
    need_taken_cell: bool
    max_cast_per_turn: Optional[int]
    max_cast_per_target: Optional[int]
    min_cast_interval: int
    casts_this_turn: int
    castable: bool
    critical_hit_probability: Optional[float]
    effects: Tuple[LiveEffect, ...]
    critical_effects: Tuple[LiveEffect, ...]
    is_weapon: bool = False
    zone_size: Optional[int] = None
    zone_shape: Optional[str] = None
    # Why the client could not answer "can this be cast". The page writes it only
    # when `canCastThisSpell` threw, and `castable` is then `None` in the payload --
    # which `_castable` reads as available, because the executor re-checks before
    # sending. Without this field the *reason* was dropped at parse time, so a live
    # session that hit a broken client API looked exactly like a healthy board.
    castable_error: Optional[str] = None
    is_summon: bool = False
    summon_type: Optional[int] = None

    # The solver asks these thousands of times per plan; recomputing the filter
    # from the effect tuple each time was measurable. The spell is immutable, so
    # the first answer is the only answer.
    @cached_property
    def is_trap(self) -> bool:
        name_l = (self.name or "").lower()
        return (
            "trap" in name_l
            or "piege" in name_l
            or "piège" in name_l
            or any(e.effect_id == 400 or e.kind == "trap" for e in self.effects)
        )

    @cached_property
    def is_glyph(self) -> bool:
        name_l = (self.name or "").lower()
        return (
            "glyph" in name_l
            or "glyphe" in name_l
            or any(e.effect_id in (401, 402) or e.kind == "glyph" for e in self.effects)
        )
    @cached_property
    def damage_effects(self) -> Tuple[LiveEffect, ...]:
        return tuple(self._effect_lines(("damage", "steal")))

    @cached_property
    def heal_effects(self) -> Tuple[LiveEffect, ...]:
        return tuple(self._effect_lines(("heal",)))

    @cached_property
    def mechanic_effects(self) -> Tuple[LiveEffect, ...]:
        return tuple(e for e in self.effects
                     if e.kind in PLANNED_MECHANIC_KINDS)

    @cached_property
    def has_mechanic(self) -> bool:
        return bool(self.mechanic_effects)

    @cached_property
    def is_teleport(self) -> bool:
        return any(e.kind == "teleport" for e in self.effects)

    @cached_property
    def mechanic_rolls(self) -> Tuple[int, int, int, int, int, int]:
        """Expected rolls: AP gain, MP gain, AP loss, MP loss, push, pull."""
        ap_gain = mp_gain = ap_loss = mp_loss = push = pull = 0
        for effect in self.effects:
            kind = effect.kind
            low = max(0, effect.min_roll)
            high = max(low, effect.max_roll)
            value = int(round((low + high) / 2.0))
            if kind == "ap_steal":
                ap_gain += value
                ap_loss += value
            elif kind == "ap_gain":
                ap_gain += value
            elif kind == "ap_loss":
                ap_loss += value
            elif kind == "mp_gain":
                mp_gain += value
            elif kind == "mp_loss":
                mp_loss += value
            elif kind == "push":
                push += value
            elif kind == "pull":
                pull += value
        return ap_gain, mp_gain, ap_loss, mp_loss, push, pull

    def _effect_lines(self, wanted: Tuple[str, ...]) -> List[LiveEffect]:
        """One effect per damage/heal line, not one per data row.

        A spell level lists the same effect id several times -- once per grade -- and
        the client applies exactly one of them. Measured 2026-11-10 on spell 61: three
        `effect_id 97` rows (18-20, 21-23, 24-26) were summed into 66 base damage, the
        caster's stats folded that to 117.65, and the live cast dealt 39. Every plan
        was therefore ranked against a number three times too large: the solver
        believed one cast finished a 120-hp monster, and fight 1559 ended with three
        enemies still standing after nine casts and 186 damage.

        Identical `(kind, effect_id)` rows are the same line at different grades, so
        the highest one is kept. A different effect id stays separate, so a genuine
        multi-line spell keeps every line. No zero-id guard is needed: `kind` is
        derived from the effect id itself, so anything that reaches this loop has a
        real one.
        """
        kept: Dict[Tuple[str, int], LiveEffect] = {}
        order: List[Tuple[str, int]] = []
        for effect in self.effects:
            if effect.kind not in wanted:
                continue
            key = (effect.kind, int(effect.effect_id))
            previous = kept.get(key)
            if previous is None:
                kept[key] = effect
                order.append(key)
            elif (effect.min_roll + effect.max_roll) > (previous.min_roll
                                                       + previous.max_roll):
                kept[key] = effect
        return [kept[key] for key in order]

    def _damage_info(self, wanted: Tuple[str, ...]) -> Optional[SpellInfo]:
        normal = self._effect_lines(wanted)
        if not normal:
            return None
        critical: Dict[str, List[LiveEffect]] = {}
        for effect in self.critical_effects:
            if effect.kind in wanted:
                critical.setdefault(effect.element or "HEAL", []).append(effect)
        damage_effects: List[DamageEffect] = []
        for effect in normal:
            pool = critical.get(effect.element or "HEAL") or []
            crit = pool.pop(0) if pool else None
            damage_effects.append(DamageEffect(
                element=effect.element or "NEUTRAL",
                min_roll=effect.min_roll,
                max_roll=effect.max_roll,
                crit_min_roll=crit.min_roll if crit else 0,
                crit_max_roll=crit.max_roll if crit else 0,
            ))
        return SpellInfo(
            id=self.id,
            name_fr=self.name,
            name_en=self.name,
            name_es="",
            breed_id=0,
            level=1,
            ap_cost=max(1, self.ap_cost),
            min_range=self.min_range,
            max_range=self.max_range,
            cast_in_line=self.cast_in_line,
            cast_in_diagonal=self.cast_in_diagonal,
            cast_test_los=self.requires_los,
            effects=damage_effects,
            crit_rate_percent=self.critical_hit_probability or 0.0,
        )

    # The damage/hit conversion depends only on the spell. Building the effect
    # list, critical pairing and SpellInfo on every (cell, target) estimate was
    # repeated work in the solver, the observation builder and the live policy.
    @cached_property
    def damage_info(self) -> Optional[SpellInfo]:
        return self._damage_info(("damage", "steal"))

    @cached_property
    def heal_info(self) -> Optional[SpellInfo]:
        return self._damage_info(("heal",))

    @cached_property
    def has_damage(self) -> bool:
        return bool(self.damage_effects)

    @cached_property
    def has_heal(self) -> bool:
        return bool(self.heal_effects)


@dataclass(frozen=True)
class Fighter:
    id: int
    name: str
    cell: int
    team: str
    hp: int
    max_hp: int
    ap: int
    mp: int
    shield: int
    level: int
    alive: bool
    by_me: bool
    resist_percent: Tuple[Tuple[str, int], ...] = ()
    resist_fixed: Tuple[Tuple[str, int], ...] = ()
    critical_resistance: int = 0
    initiative: int = 0
    max_ap: int = 0
    max_mp: int = 0
    states: Tuple[Any, ...] = ()
    is_boss: bool = False
    is_summon: bool = False
    is_companion: bool = False
    summoner_id: Optional[int] = None
    creature_generic_id: Optional[int] = None
    spells: Tuple[LiveSpell, ...] = ()

    @cached_property
    def _percent_map(self) -> Dict[str, int]:
        return dict(self.resist_percent)

    @cached_property
    def _fixed_map(self) -> Dict[str, int]:
        return dict(self.resist_fixed)

    def _percent(self, element: str) -> int:
        return self._percent_map.get(element.lower(), 0)

    def _fixed(self, element: str) -> int:
        return self._fixed_map.get(element.lower(), 0)

    def target_stats(self) -> TargetStats:
        return self._target_stats

    @cached_property
    def _target_stats(self) -> TargetStats:
        return TargetStats(
            name=self.name or str(self.id),
            max_hp=self.max_hp,
            current_hp=self.hp,
            level=self.level,
            res_percent_neutral=self._percent("neutral"),
            res_percent_earth=self._percent("earth"),
            res_percent_fire=self._percent("fire"),
            res_percent_water=self._percent("water"),
            res_percent_air=self._percent("air"),
            res_fixed_neutral=self._fixed("neutral"),
            res_fixed_earth=self._fixed("earth"),
            res_fixed_fire=self._fixed("fire"),
            res_fixed_water=self._fixed("water"),
            res_fixed_air=self._fixed("air"),
            armor_shield=self.shield,
            res_fixed_critical=self.critical_resistance,
            is_player=self.team == "player",
        )


def enemy_character(fighter: Fighter) -> CharacterStats:
    """Damage stats for an enemy: base spell rolls, HP/AP/MP from the board."""
    return CharacterStats(
        name=fighter.name or str(fighter.id),
        level=max(1, int(fighter.level)),
        action_points=max(0, int(fighter.ap)),
        movement_points=max(0, int(fighter.mp)),
    )


@dataclass(frozen=True)
class BoardMark:
    mark_id: int
    mark_type: str  # "trap" or "glyph"
    cell: int
    radius: int
    owner_id: int
    team: str
    spell_id: int = 0
    effects: Tuple[LiveEffect, ...] = ()
    push_distance: int = 0
    duration: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.mark_id,
            "mark_id": self.mark_id,
            "mark_type": self.mark_type,
            "cell": self.cell,
            "radius": self.radius,
            "owner_id": self.owner_id,
            "team": self.team,
            "spell_id": self.spell_id,
            "push_distance": self.push_distance,
            "duration": self.duration,
            "effects": [
                {"effect_id": e.effect_id, "dice_num": e.min_roll, "dice_side": e.max_roll,
                 "target_id": e.target_id, "duration": e.duration}
                for e in self.effects
            ],
        }


@dataclass(frozen=True)
class BoardMarks:
    marks: Tuple[BoardMark, ...] = ()

    @property
    def traps(self) -> Tuple[BoardMark, ...]:
        return tuple(m for m in self.marks if m.mark_type == "trap")

    @property
    def glyphs(self) -> Tuple[BoardMark, ...]:
        return tuple(m for m in self.marks if m.mark_type == "glyph")

    def traps_at(self, cell: int, cells_map: Optional[Any] = None) -> List[BoardMark]:
        """Traps whose trigger zone covers `cell`."""
        out = []
        for mark in self.marks:
            if mark.mark_type != "trap":
                continue
            if mark.radius <= 0:
                if mark.cell == cell:
                    out.append(mark)
            else:
                if cells_map is not None:
                    dist = cells_map.distance(mark.cell, cell)
                else:
                    mx, my = cell_point(mark.cell)
                    cx, cy = cell_point(cell)
                    dist = abs(mx - cx) + abs(my - cy)
                if dist <= mark.radius:
                    out.append(mark)
        return out

    def with_mark(self, mark: BoardMark) -> "BoardMarks":
        return BoardMarks(self.marks + (mark,))

    def without_mark(self, mark_id: int) -> "BoardMarks":
        return BoardMarks(tuple(m for m in self.marks if m.mark_id != mark_id))

    def without_marks(self, mark_ids: FrozenSet[int]) -> "BoardMarks":
        return BoardMarks(tuple(m for m in self.marks if m.mark_id not in mark_ids))


@dataclass
class Board:
    map_id: Optional[int]
    in_fight: bool
    in_preparation: bool
    is_my_turn: bool
    my_id: int
    my_cell: int
    my_ap: int
    my_mp: int
    fighters: List[Fighter]
    spells: List[LiveSpell]
    my_stats: Dict[str, Any]
    cells: CellMap
    round: Optional[int] = None
    fight_id: Optional[int] = None
    current_fighter_id: Optional[int] = None
    last_actions: Tuple[Dict[str, Any], ...] = ()
    marks: BoardMarks = field(default_factory=BoardMarks)
    _character_stats_cache: Optional[CharacterStats] = field(
        default=None, init=False, repr=False, compare=False)
    # The record this board was parsed from. `to_fight_state` is a useful
    # reconstruction, not a faithful inverse: it publishes `my_ap`/`my_mp` that
    # the page omits and normalises nulls to 0. A model trained on the page's
    # own payloads has to be fed the page's own payloads -- measured on the 333
    # live boards, the reconstruction moved `my_ap` on 98 and `my_mp` on 64, and
    # the enemy-turn price collapsed to 0.0 on the boards where it mattered.
    source_payload: Optional[Dict[str, Any]] = field(
        default=None, init=False, repr=False, compare=False)

    @property
    def max_summons(self) -> int:
        boost = _int(self.my_stats.get("summonable_creatures_boost"), 0) or 0
        stat = _int(self.my_stats.get("summons"), 0) or _int(self.my_stats.get("max_summons"), 0) or 0
        if stat > 0:
            return max(1, stat)
        return max(1, 1 + boost)

    @property
    def active_my_summons(self) -> int:
        return sum(1 for f in self.fighters
                   if f.alive and (f.is_summon or f.summoner_id == self.my_id)
                   and f.summoner_id == self.my_id)

    @property
    def active_summon_types(self) -> FrozenSet[int]:
        types = set()
        for f in self.fighters:
            if f.alive and f.summoner_id == self.my_id:
                if f.creature_generic_id is not None:
                    types.add(f.creature_generic_id)
                elif f.name:
                    types.add(hash(f.name))
        return frozenset(types)

    @property
    def available_summon_slots(self) -> int:
        return max(0, self.max_summons - self.active_my_summons)

    @classmethod
    def from_fight_state(cls, payload: Dict[str, Any]) -> "Board":
        fighters = [f for f in (cls._fighter(raw) for raw in payload.get("fighters") or [])
                    if f is not None]
        my_id = _int(payload.get("my_id"))
        me = next((f for f in fighters if f.id == my_id), None) if my_id is not None else None
        if me is None:
            me = next((f for f in fighters if f.by_me), None)
        my_cell = me.cell if me is not None else _int(payload.get("my_cell"), 0)
        spells = [cls._spell(raw) for raw in payload.get("spells") or []]
        stats = dict(payload.get("my_stats") or {})
        # Whether the client actually published a value, which is a different
        # question from what the value is. `mp: 0` is a character that has spent its
        # movement; `mp: null` is a client that does not publish it. Collapsing the
        # two is what made the solver chase cells it could not walk to: measured on
        # the live boards of 2026-11-10, a fighter with `mp: 0` was re-priced at the
        # full `my_stats.movement_points` budget, so every plan after the first move
        # of a turn was built on movement the character no longer had.
        raw_me = None
        for raw in payload.get("fighters") or []:
            if not isinstance(raw, dict):
                continue
            if _int(raw.get("id")) == my_id or raw.get("is_me"):
                raw_me = raw
                break
        mp_published = isinstance(raw_me, dict) and raw_me.get("mp") is not None
        if me is not None and (me.ap <= 0 or me.mp <= 0):
            # Our own fighter's AP/MP arrive null: the client publishes them on the
            # *active* fighter, and fills our row in only for the fields it has.
            # Measured 2026-11-10: 106 of 160 boards captured on our own turn read
            # `ap: 0`. Re-measured 2026-11-12 over the 333 boards in
            # `%LOCALAPPDATA%/DofusFarm/fight_boards.jsonl`: the same 106 of 160, and
            # this derivation recovers **152** of them -- only 8 boards still read no
            # positive AP, which is what `data/combat_grid/manifest.json` records as
            # `no_positive_ap: 8` and `actionable: 7`. Those 8 are not a gap: every one
            # is fight 3977 with `action_points: 6` and `spell_cast_counts {3: 2}` at a
            # 3-AP spell, so the character really had spent its turn.
            #
            # The older sentence here said the builder dropped 198 of 333 boards and
            # that the corpus "yielded three actionable rows". Both numbers were true
            # before this fallback existed and neither survived it: a comment that
            # tells the next reader the live corpus is dead is how a working dataset
            # goes unbuilt. If this is touched again, re-run the measurement rather
            # than copying the figure.
            #
            # AP is derived, not assumed: the stat is the turn's budget and
            # `spell_cast_counts` resets each round (verified across five rounds of
            # fight 3977), so spent AP comes off the budget. MP spending is not
            # published in `spell_cast_counts` either, so the budget is used only
            # when the fighter's own `mp` is missing -- never to overwrite a 0.
            budget_ap = _int(stats.get("action_points"), 0) or 0
            budget_mp = _int(stats.get("movement_points"), 0) or 0
            ap, mp = me.ap, me.mp
            if ap <= 0 and budget_ap > 0:
                cost = {spell.id: spell.ap_cost for spell in spells}
                spent = 0
                for spell_id, count in (payload.get("spell_cast_counts") or {}).items():
                    spent += cost.get(_int(spell_id, -1), 0) * (_int(count, 0) or 0)
                ap = max(0, budget_ap - spent)
            if mp <= 0 and budget_mp > 0 and not mp_published:
                mp = budget_mp
            if ap != me.ap or mp != me.mp:
                me = replace(me, ap=ap, mp=mp,
                             max_ap=me.max_ap or budget_ap,
                             max_mp=me.max_mp or budget_mp)
                fighters = [me if fighter.id == me.id else fighter for fighter in fighters]
        parsed_marks: List[BoardMark] = []
        spells_by_id = {s.id: s for s in spells}
        fighters_by_id = {f.id: f for f in fighters}

        raw_marks: List[Dict[str, Any]] = []
        if isinstance(payload.get("marks"), list):
            raw_marks.extend(raw for raw in payload["marks"] if isinstance(raw, dict))
        if isinstance(payload.get("traps"), list):
            for raw in payload["traps"]:
                if isinstance(raw, dict):
                    raw_copy = dict(raw)
                    raw_copy.setdefault("mark_type", "trap")
                    raw_marks.append(raw_copy)
        if isinstance(payload.get("glyphs"), list):
            for raw in payload["glyphs"]:
                if isinstance(raw, dict):
                    raw_copy = dict(raw)
                    raw_copy.setdefault("mark_type", "glyph")
                    raw_marks.append(raw_copy)

        for action in payload.get("last_actions") or []:
            if not isinstance(action, dict):
                continue
            action_id = _int(action.get("action_id"))
            kind = str(action.get("kind") or action.get("type") or "").lower()
            if action_id in (310, 1099) or kind in ("trap", "glyph", "mark"):
                raw_marks.append(action)

        seen_mark_keys = set()
        for index, raw in enumerate(raw_marks):
            cell = _int(raw.get("cell") or raw.get("target_cell") or raw.get("cell_id"))
            if cell is None:
                continue

            m_type_raw = raw.get("mark_type")
            if m_type_raw == 1 or str(m_type_raw).lower() in ("trap", "piege", "piège"):
                mark_type = "trap"
            elif m_type_raw == 2 or str(m_type_raw).lower() in ("glyph", "glyphe"):
                mark_type = "glyph"
            elif "glyph" in str(raw.get("kind") or raw.get("name") or "").lower():
                mark_type = "glyph"
            else:
                mark_type = "trap"

            mark_id = _int(raw.get("mark_id") or raw.get("id"), index + 1)
            radius = _int(raw.get("radius") or raw.get("zone_size") or raw.get("size"), 0) or 0
            owner_id = _int(raw.get("owner_id") or raw.get("caster_id") or raw.get("source_id"), my_id or -1)
            owner_f = fighters_by_id.get(owner_id)
            team = str(raw.get("team") or (owner_f.team if owner_f else "player"))
            spell_id = _int(raw.get("spell_id"), 0) or 0

            raw_effects = raw.get("effects")
            if raw_effects:
                effects = _effects(raw_effects)
            elif spell_id in spells_by_id:
                effects = spells_by_id[spell_id].effects
            else:
                effects = ()

            push_dist = _int(raw.get("push_distance"), 0) or 0
            if push_dist == 0 and effects:
                for e in effects:
                    if e.kind in ("push", "pushed"):
                        push_dist += int(round((e.min_roll + e.max_roll) / 2.0))
                    elif e.kind in ("pull", "pulled"):
                        push_dist -= int(round((e.min_roll + e.max_roll) / 2.0))

            duration = _int(raw.get("duration"), 0) or 0
            mark_key = (mark_type, cell, spell_id, owner_id)
            if mark_key in seen_mark_keys:
                continue
            seen_mark_keys.add(mark_key)

            parsed_marks.append(BoardMark(
                mark_id=mark_id,
                mark_type=mark_type,
                cell=cell,
                radius=radius,
                owner_id=owner_id,
                team=team,
                spell_id=spell_id,
                effects=effects,
                push_distance=push_dist,
                duration=duration,
            ))
        board_marks = BoardMarks(tuple(parsed_marks))

        board = cls(
            map_id=_int(payload.get("map_id")),
            in_fight=bool(payload.get("in_fight")),
            in_preparation=bool(payload.get("in_preparation")),
            is_my_turn=bool(payload.get("is_my_turn")),
            my_id=my_id if my_id is not None else (me.id if me else -1),
            my_cell=my_cell,
            my_ap=me.ap if me is not None else _int(payload.get("my_ap"), 0),
            my_mp=me.mp if me is not None else _int(payload.get("my_mp"), 0),
            fighters=fighters,
            spells=spells,
            my_stats=stats,
            cells=CellMap.from_board(payload),
            round=_int(payload.get("round")),
            fight_id=_int(payload.get("fight_id")),
            current_fighter_id=_int(payload.get("current_fighter_id")),
            last_actions=tuple(action for action in payload.get("last_actions") or []
                               if isinstance(action, dict)),
            marks=board_marks,
        )
        board.source_payload = payload
        return board

    @staticmethod
    def _fighter(raw: Dict[str, Any]) -> Optional[Fighter]:
        cell = _int(raw.get("cell"))
        if cell is None:
            return None
        team = str(raw.get("team") or "enemy")
        return Fighter(
            id=_int(raw.get("id"), -1),
            name=str(raw.get("name") or ""),
            cell=cell,
            team=team,
            hp=_int(raw.get("hp"), 0),
            max_hp=_int(raw.get("max_hp"), 0),
            ap=_int(raw.get("ap"), 0),
            mp=_int(raw.get("mp"), 0),
            shield=_int(raw.get("shield"), 0),
            level=_int(raw.get("level"), 1),
            alive=bool(raw.get("alive", True)),
            by_me=bool(raw.get("is_me")),
            resist_percent=tuple(sorted((str(k), _int(v))
                                        for k, v in (raw.get("resist_percent") or {}).items())),
            resist_fixed=tuple(sorted((str(k), _int(v))
                                      for k, v in (raw.get("resist_fixed") or {}).items())),
            critical_resistance=_int(raw.get("critical_resistance"), 0),
            initiative=_int(raw.get("initiative"), 0) or 0,
            max_ap=_int(raw.get("max_ap"), 0) or 0,
            max_mp=_int(raw.get("max_mp"), 0) or 0,
            states=tuple(raw.get("states") or ()),
            is_boss=bool(raw.get("is_boss")),
            is_summon=bool(raw.get("is_summon")),
            is_companion=bool(raw.get("is_companion")),
            summoner_id=_int(raw.get("summoner_id")),
            creature_generic_id=_int(raw.get("creature_generic_id")),
            spells=tuple(Board._spell(spell) for spell in raw.get("spells") or []
                         if isinstance(spell, dict)),
        )

    @staticmethod
    def _spell(raw: Dict[str, Any]) -> LiveSpell:
        effects = _effects(raw.get("effects"))
        raw_is_summon = bool(raw.get("is_summon")) or any(
            e.effect_id in (180, 181, 1008, 1011, 405) or e.kind == "summon"
            for e in effects
        )
        summon_type = _int(raw.get("summon_type") or raw.get("creature_id") or raw.get("creature_generic_id"))
        if summon_type is None and raw_is_summon:
            for e in effects:
                if e.effect_id in (180, 181, 1008, 1011, 405) or e.kind == "summon":
                    summon_type = e.min_roll or e.max_roll or _int(raw.get("id"), -1)
                    break
        return LiveSpell(
            id=_int(raw.get("id"), -1),
            name=str(raw.get("name") or ""),
            ap_cost=_int(raw.get("ap_cost"), 0),
            min_range=_int(raw.get("min_range"), 0),
            max_range=_int(raw.get("range"), 0),
            cast_in_line=bool(raw.get("cast_in_line")),
            cast_in_diagonal=bool(raw.get("cast_in_diagonal")),
            requires_los=bool(raw.get("requires_los", True)),
            need_free_cell=bool(raw.get("need_free_cell")),
            need_taken_cell=bool(raw.get("need_taken_cell")),
            max_cast_per_turn=_limit(raw.get("max_cast_per_turn")),
            max_cast_per_target=_limit(raw.get("max_cast_per_target")),
            min_cast_interval=_int(raw.get("min_cast_interval"), 0),
            casts_this_turn=_int(raw.get("casts_this_turn"), 0),
            castable=_castable(raw.get("castable")),
            critical_hit_probability=_float(raw.get("critical_hit_probability")),
            effects=effects,
            critical_effects=_effects(raw.get("critical_effects")),
            is_weapon=bool(raw.get("is_weapon")),
            zone_size=_int(raw.get("zone_size")) if raw.get("zone_size") is not None else None,
            zone_shape=raw.get("zone_shape") or None,
            castable_error=(str(raw.get("castable_error"))[:120]
                            if raw.get("castable_error") else None),
            is_summon=raw_is_summon,
            summon_type=summon_type,
        )

    @property
    def me(self) -> Optional[Fighter]:
        return next((f for f in self.fighters if f.id == self.my_id), None)

    def to_fight_state(self) -> Dict[str, Any]:
        """This board as the page's own `fight.state` payload.

        The inverse of `from_fight_state`, written because every offline measurement of the live
        fight policy has gone through an approximation of it. `combat.duel` hands the solver a
        `Board`, but `LivePolicy.decide()` -- the function the client actually calls -- takes the
        raw payload, so a duel could only score a hand-rolled adapter that matched the actor's
        (spell, target) onto a candidate plan and took the solver's best when nothing matched.
        That adapter is not `_realize`: it never walks first, never consults the value ranker,
        and does not share the direct-cast branch whose behaviour §L.18 had to measure against
        the real corpus. With this serialization the duel can run `decide()` itself.

        Every field the parser defaults is emitted explicitly, because `requires_los` defaults to
        True when absent: dropping it would silently turn a permissive spell into a line-of-sight
        one on the way back. An effect whose roll never parsed (min and max both 0) comes back the
        same way, which is the parser's own behaviour rather than a loss.
        """
        def spell_row(spell: "LiveSpell") -> Dict[str, Any]:
            def effect_row(effect: "LiveEffect") -> Dict[str, Any]:
                row: Dict[str, Any] = {"effect_id": effect.effect_id,
                                       "target_id": effect.target_id,
                                       "duration": effect.duration}
                if effect.min_roll > 0:
                    row["dice_num"] = effect.min_roll
                    row["dice_side"] = effect.max_roll or effect.min_roll
                return row

            return {
                "id": spell.id, "name": spell.name, "ap_cost": spell.ap_cost,
                "min_range": spell.min_range, "range": spell.max_range,
                "cast_in_line": spell.cast_in_line,
                "cast_in_diagonal": spell.cast_in_diagonal,
                "requires_los": spell.requires_los,
                "need_free_cell": spell.need_free_cell,
                "need_taken_cell": spell.need_taken_cell,
                "max_cast_per_turn": spell.max_cast_per_turn,
                "max_cast_per_target": spell.max_cast_per_target,
                "min_cast_interval": spell.min_cast_interval,
                "casts_this_turn": spell.casts_this_turn,
                "castable": spell.castable,
                "critical_hit_probability": spell.critical_hit_probability,
                "effects": [effect_row(e) for e in spell.effects],
                "critical_effects": [effect_row(e) for e in spell.critical_effects],
                "is_weapon": spell.is_weapon,
                "zone_size": spell.zone_size, "zone_shape": spell.zone_shape,
                # Round-trip the reason as well as the answer: a board rebuilt from
                # this payload is what every offline measurement reads, and dropping
                # it here made an unchecked spell indistinguishable from a checked
                # one on the way back.
                "castable_error": spell.castable_error,
                "is_summon": spell.is_summon,
                "summon_type": spell.summon_type,
            }

        def fighter_row(fighter: "Fighter") -> Dict[str, Any]:
            return {
                "id": fighter.id, "name": fighter.name, "cell": fighter.cell,
                "team": fighter.team, "hp": fighter.hp, "max_hp": fighter.max_hp,
                "ap": fighter.ap, "mp": fighter.mp, "shield": fighter.shield,
                "level": fighter.level, "alive": fighter.alive,
                "is_me": fighter.by_me,
                "resist_percent": dict(fighter.resist_percent),
                "resist_fixed": dict(fighter.resist_fixed),
                "critical_resistance": fighter.critical_resistance,
                "initiative": fighter.initiative, "max_ap": fighter.max_ap,
                "max_mp": fighter.max_mp, "states": list(fighter.states),
                "is_boss": fighter.is_boss, "is_summon": fighter.is_summon,
                "is_companion": fighter.is_companion,
                "summoner_id": fighter.summoner_id,
                "creature_generic_id": fighter.creature_generic_id,
                "spells": [spell_row(s) for s in fighter.spells],
            }

        return {
            "in_fight": self.in_fight, "in_preparation": self.in_preparation,
            "is_my_turn": self.is_my_turn, "my_id": self.my_id,
            "my_cell": self.my_cell, "my_ap": self.my_ap, "my_mp": self.my_mp,
            "round": self.round, "fight_id": self.fight_id, "map_id": self.map_id,
            "current_fighter_id": self.current_fighter_id,
            "cells": list(self.cells.flags), "my_stats": dict(self.my_stats),
            "spells": [spell_row(s) for s in self.spells],
            "fighters": [fighter_row(f) for f in self.fighters],
            "last_actions": [dict(a) for a in self.last_actions],
            "marks": [m.as_dict() for m in self.marks.marks],
        }

    @property
    def enemies(self) -> List[Fighter]:
        return [f for f in self.fighters if f.alive and f.id != self.my_id
                and f.team != "player"]

    @property
    def allies(self) -> List[Fighter]:
        return [f for f in self.fighters if f.alive and f.team == "player"]

    def character_stats(self) -> CharacterStats:
        cached = self._character_stats_cache
        if cached is not None:
            return cached
        stats = self.my_stats or {}
        me = self.me

        def value(key: str) -> int:
            return _int(stats.get(key), 0)

        result = CharacterStats(
            name=me.name if me else "Player",
            level=me.level if me else 1,
            action_points=self.my_ap,
            movement_points=self.my_mp,
            strength=value("strength"),
            intelligence=value("intelligence"),
            chance=value("chance"),
            agility=value("agility"),
            power=value("power"),
            damage=value("damage"),
            damage_neutral=value("damage_neutral"),
            damage_earth=value("damage_earth"),
            damage_fire=value("damage_fire"),
            damage_water=value("damage_water"),
            damage_air=value("damage_air"),
            critical_hit=value("critical_hit"),
            damage_critical=value("damage_critical"),
            heals=value("heals"),
        )
        self._character_stats_cache = result
        return result


def _int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _limit(value: Any) -> Optional[int]:
    parsed = _int(value)
    return None if parsed is None or parsed <= 0 else parsed


def _castable(value: Any) -> bool:
    """Unknown is not refused.

    The page writes ``false`` only when the client answered, and ``null`` when
    ``canCastThisSpell`` threw. Reading both as a veto drops the spell from every
    candidate set, so the AI never considers it -- and the executor re-checks with
    its own gate before sending, so an optimistic read cannot spend the turn on a
    refused cast. Absent stays available: all 1,341 spell rows in the recorded
    corpus predate the field.
    """
    return True if value is None else bool(value)


def _roll(raw: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """Damage roll, mirroring the client's getMinMaxDamageFromRawEffect.

    ``diceNum`` is the minimum, ``diceSide`` the maximum; a falsy ``diceSide``
    means the effect is fixed at ``diceNum``. Effects without dice fall back to
    an explicit ``value``.
    """
    low = _int(raw.get("dice_num"))
    high = _int(raw.get("dice_side"))
    if low is not None and low > 0:
        return low, high if high else low
    value = _int(raw.get("value"))
    if value is not None and value > 0:
        return value, value
    return None


def _effects(raw_list: Any) -> Tuple[LiveEffect, ...]:
    out: List[LiveEffect] = []
    for raw in raw_list or []:
        effect_id = _int(raw.get("effect_id"))
        if effect_id is None:
            continue
        roll = _roll(raw)
        # Utility effects (push, AP/MP loss, shields, states, traps) have no
        # damage roll. Keep their id so the observation can name the mechanic
        # and the contract can say the solver does not plan it yet.
        out.append(LiveEffect(
            effect_id=effect_id,
            min_roll=roll[0] if roll else 0,
            max_roll=roll[1] if roll else 0,
            target_id=_int(raw.get("target_id")),
            duration=_int(raw.get("duration")),
        ))
    return tuple(out)


# ---------------------------------------------------------------------------
# action generation and search
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlannedAction:
    kind: str
    spell_id: int = 0
    spell_name: str = ""
    from_cell: int = 0
    move_to: Optional[int] = None
    target_id: Optional[int] = None
    target_cell: Optional[int] = None
    mp_cost: float = 0.0
    ap_cost: int = 0
    damage: float = 0.0
    healing: float = 0.0
    kills: bool = False
    moved_target: Optional[int] = None
    moved: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "spell_id": self.spell_id,
            "spell_name": self.spell_name,
            "from_cell": self.from_cell,
            "move_to": self.move_to,
            "target_id": self.target_id,
            "target_cell": self.target_cell,
            "mp_cost": round(self.mp_cost, 3),
            "ap_cost": self.ap_cost,
            "damage": round(self.damage, 2),
            "healing": round(self.healing, 2),
            "kills": self.kills,
            "moved_target": self.moved_target,
            "moved": self.moved,
        }


@dataclass
class TurnPlan:
    actions: List[PlannedAction]
    final_cell: int
    remaining_ap: int
    remaining_mp: float
    expected_damage: float
    expected_healing: float
    kills: List[int]
    score: float
    notes: List[str] = field(default_factory=list)
    final_hp: Dict[int, int] = field(default_factory=dict)
    bucket: str = "damage"
    # Expected damage on `final_cell` from the living enemies before the
    # character acts again. Recorded rather than only scored: a plan's position
    # is otherwise invisible in the decision log, and "he walked into the open"
    # has to be a measurement, not an impression.
    exposure: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "actions": [a.as_dict() for a in self.actions],
            "final_cell": self.final_cell,
            "remaining_ap": self.remaining_ap,
            "remaining_mp": round(self.remaining_mp, 3),
            "expected_damage": round(self.expected_damage, 2),
            "expected_healing": round(self.expected_healing, 2),
            "kills": self.kills,
            "score": round(self.score, 2),
            "notes": self.notes,
            "final_hp": {str(k): int(v) for k, v in self.final_hp.items()},
            "bucket": self.bucket,
            "exposure": round(self.exposure, 2),
        }


@dataclass(frozen=True, slots=True)
class _CastTemplate:
    spell: LiveSpell
    target_id: int
    target_cell: int
    damage: float
    healing: float
    steal_heal: float
    target_max_hp: int
    damage_int: int
    ap_gain: int = 0
    mp_gain: int = 0
    ap_loss: int = 0
    mp_loss: int = 0
    utility: float = 0.0
    move_target: Optional[int] = None
    moved: int = 0
    is_trap_placement: bool = False
    is_summon: bool = False


@dataclass(slots=True)
class _Node:
    cell: int
    ap: int
    mp: float
    hp: Dict[int, int]
    spell_uses: Dict[int, int]
    target_uses: Dict[Tuple[int, int], int]
    healed_by_target: Dict[int, float]
    actions: List[PlannedAction]
    damage: float
    healing: float
    kills: List[int]
    score: float
    # Fighters a push/pull already moved this plan. The templates were
    # generated from the board's start cells, so a later cast at the old cell
    # would be illegal; a displaced target is terminal for this plan.
    moved_targets: FrozenSet[int] = frozenset()
    marks: BoardMarks = field(default_factory=BoardMarks)
    summons_count: int = 0
    summon_types: FrozenSet[int] = frozenset()


_EMPTY_FROZENSET: FrozenSet[Any] = frozenset()


class BattleSolver:
    """One-turn beam search over legal moves and casts."""

    def __init__(self, beam_width: int = 16, max_actions: int = 4,
                 max_nodes: int = 20000,
                 memory: Optional[Any] = None,
                 enemy_turn_price: Optional[Any] = None,
                 damage_calibration: Optional[Any] = None) -> None:
        self.beam_width = max(1, beam_width)
        self.max_actions = max(0, max_actions)
        self.max_nodes = max(1, max_nodes)
        # A callable returning the learned per-monster damage table, or None.
        #
        # None is the default and means "the level prior only", which is what keeps
        # the solver a pure function of its board: a benchmark, a duel or a test must
        # not change its answer because a file in `models/` was filled in by a live
        # fight. The live entry points (`LivePolicy`, `agent.executor`) pass
        # `threat.default_memory`, which re-reads the file when it changes.
        self.memory = memory
        # Two more live-only readings, on the same contract as `memory`: a callable
        # or None, and None means the solver's own prior.
        #
        # `enemy_turn_price(board_payload) -> float | None` is what `live_learn`
        # measured this group costing us in one enemy turn. It reaches the search
        # through `threat.build(turn_price=...)`, which rescales the per-cell prices
        # -- a whole-turn scalar subtracted from a score would cancel out of every
        # comparison and change nothing.
        #
        # `damage_calibration() -> dict | None` is the shadow differential between
        # the solver's damage price and what the server actually paid.
        self.enemy_turn_price = enemy_turn_price
        self.damage_calibration = damage_calibration
        self.last_candidates: List[TurnPlan] = []
        self.last_threat: Optional[Any] = None
        # Per-bucket counts from the last stratified search. A learner can see
        # whether the beam actually explored setup/displacement/boss plans or
        # collapsed onto direct damage.
        self.last_buckets: Dict[str, int] = {}

    def _table(self) -> Optional[Any]:
        """The learned table for this turn, or None for the level prior."""
        if self.memory is None:
            return None
        try:
            return self.memory() if callable(self.memory) else self.memory
        except Exception:  # noqa: BLE001 - an unreadable table is the prior
            return None

    def _turn_price(self, board: Board) -> Optional[float]:
        """The measured cost of one enemy turn here, or None to keep the prior.

        The learner is fitted on the page's own payloads, so it gets the record
        the board was parsed from. `to_fight_state` is only the fallback for a
        board built in code: it publishes `my_ap`/`my_mp` the page omits, which
        is out of distribution for the model and drove its answer to 0.
        """
        if self.enemy_turn_price is None:
            return None
        try:
            hook = self.enemy_turn_price
            payload = board.source_payload
            if payload is None:
                payload = board.to_fight_state()
            price = hook(payload) if callable(hook) else hook
            if price is None:
                return None
            value = float(price)
        except Exception:  # noqa: BLE001 - an unreadable price is the prior
            return None
        return value if math.isfinite(value) and value >= 0.0 else None

    def _calibration(self) -> Optional[Dict[str, Any]]:
        """The shadow damage calibration for this turn, or None."""
        if self.damage_calibration is None:
            return None
        try:
            hook = self.damage_calibration
            value = hook() if callable(hook) else hook
        except Exception:  # noqa: BLE001 - an unreadable calibration is no correction
            return None
        return value if isinstance(value, dict) else None

    def plan_candidates(self, board: Board, k: int = 5) -> List[TurnPlan]:
        """The top-k distinct turn plans from the last search, pass included.

        ``plan_turn`` still returns the single best plan; this exposes the
        alternatives next to it so a learned ranker can choose between them.
        The pass plan is always kept as an option, even when the search found
        more action plans than ``k``.

        When the search found nothing to cast, approach plans are offered in place of
        an empty list. Without them a turn spent walking has no candidate at all, and
        the caller's only options are "pass" or whatever fallback it keeps -- which is
        how 114 of 152 captured turns left the solver's hands entirely.
        """
        self.plan_turn(board)
        plans = self.last_candidates
        pass_plan = next(
            (plan for plan in plans
             if plan.actions and plan.actions[0].kind == "pass"),
            self._pass_plan(board, []))
        action_plans = [plan for plan in plans
                        if plan.actions and plan.actions[0].kind != "pass"]
        if not action_plans:
            action_plans = self._approach_plans(board, max(0, int(k) - 1))
            limit = max(1, int(k))
            return (action_plans[:max(0, limit - 1)] + [pass_plan])[:limit]
        limit = max(1, int(k))
        budget = max(0, limit - 1)
        selected = self._select_by_bucket(action_plans, board, budget)
        return (selected + [pass_plan])[:limit]

    def _select_by_bucket(self, plans: List[TurnPlan], board: Board,
                          budget: int) -> List[TurnPlan]:
        """The ``budget`` best plans, one or two per strategic bucket.

        A flat ``plans[:k]`` returns the k highest-scoring plans, and on a board
        where a direct hit exists the score is almost always a direct hit: the
        solver spends its whole output budget describing several ways to hit
        the same monster. Measured on 40 generated boards at ``k=6``, **39
        returned only ``damage`` + ``pass``**.

        **What this does not do.** It cannot make a trap or summon plan appear.
        Those spells are not templated at all (`_templates` drops a spell with
        no damage, no heal and no *planned* mechanic, and `PLANNED_MECHANIC_KINDS`
        has no trap or summon kind), and on the encounter table this repository
        ships, the only effect kinds present are `damage` and `heal`. So with
        this data the selection is correct but inert: the `setup` bucket is
        reachable in principle and simply never populated. The machinery is here
        because the collapse is real for the buckets the solver *can* produce --
        a movement plan loses to a hit on score and used to be sliced off the
        end of the list just the same.

        The budget is therefore spent per bucket: each declared bucket is
        guaranteed a slot, up to two, and any remaining slots go to the best of
        everything not yet chosen. The ordering *within* the result is still by
        score, so a caller that takes ``plans[0]`` still gets the best-scoring
        plan -- this changes the menu, not the default.

        Buckets are classified here rather than trusting ``_stratify`` because
        the approach plans in this list never went through the beam.

        ``BUCKET_ORDER`` is the module constant, not an instance field, so a
        subclass or a test can rely on the same list the solver used.
        """
        if budget <= 0 or not plans:
            return []
        for plan in plans:
            plan.bucket = self._classify_plan(plan, board)
        ranked = sorted(plans, key=lambda plan: plan.score, reverse=True)
        chosen: List[TurnPlan] = []
        seen = set()

        def signature(plan: TurnPlan):
            return tuple((action.kind, action.move_to, action.spell_id,
                          action.target_id) for action in plan.actions)

        def add(plan: TurnPlan) -> bool:
            key = signature(plan)
            if key in seen or len(chosen) >= budget:
                return False
            seen.add(key)
            chosen.append(plan)
            return True

        # One guaranteed slot per declared bucket, in the order the game plan
        # values them, then a second round for the same buckets. Two rounds
        # rather than "up to two for damage first": with k=6 and five buckets a
        # single round already fills the budget, and taking two `damage` slots
        # before any `trap_setup` would reproduce the collapse this replaces.
        order = BUCKET_ORDER
        for _round in (0, 1):
            for bucket in order:
                if len(chosen) >= budget:
                    break
                for plan in ranked:
                    if plan.bucket != bucket:
                        continue
                    if add(plan):
                        break
            if len(chosen) >= budget:
                break
        for plan in ranked:
            if len(chosen) >= budget:
                break
            add(plan)
        chosen.sort(key=lambda plan: plan.score, reverse=True)
        self.last_buckets = {}
        for plan in chosen:
            self.last_buckets[plan.bucket] = self.last_buckets.get(plan.bucket, 0) + 1
        return chosen

    def _approach_plans(self, board: Board, limit: int) -> List[TurnPlan]:
        """Walk plans, one per reachable position that closes distance to an enemy.

        The destination is the reachable cell nearest to each living enemy, so the
        list is a real choice when several enemies are on the board: approach the
        nearest one, or the one that is nearly dead. Each is scored by the distance it
        closes (``APPROACH_VALUE``), which keeps it below any cast that lands while
        still beating a pass.
        """
        if limit <= 0:
            return []
        me = board.me
        if me is None or not board.cells.valid(me.cell):
            return []
        enemies = [f for f in board.fighters
                   if f.alive and f.team != "player" and f.id != me.id]
        if not enemies or board.my_mp <= 0:
            return []
        occupied = frozenset(f.cell for f in board.fighters
                             if f.alive and f.id != me.id)
        try:
            reach = board.cells.reachable(me.cell, float(board.my_mp), occupied)
        except Exception:  # noqa: BLE001 - a path search must not cost the turn
            return []
        if not reach:
            return []
        hp = {f.id: f.hp for f in board.fighters if f.alive}
        nearest_now = min(board.cells.distance(me.cell, enemy.cell) for enemy in enemies)
        danger = threat.build(board, memory=self._table(), occupied=occupied,
                              turn_price=self._turn_price(board))
        plans: List[TurnPlan] = []
        taken: set = set()
        # Nearest enemies first: the closest one is the approach a player would take,
        # and the second is the alternative worth showing when it differs.
        for enemy in sorted(enemies, key=lambda f: board.cells.distance(me.cell, f.cell)):
            if len(plans) >= limit:
                break
            best_cell, best_gap = None, None
            for cell, cost in reach.items():
                if cell == me.cell or cell in occupied:
                    continue
                gap = board.cells.distance(cell, enemy.cell)
                # Same closing distance, quieter cell. The gap is an integer and
                # many cells tie on it, so this picks the covered side of the
                # approach instead of the first cell the walk happened to reach --
                # which is what walked the character into the open on every
                # "nothing castable" turn.
                key = (gap, round(danger.at(cell), 3), cost)
                if best_gap is None or key < best_gap:
                    best_cell, best_gap = cell, key
            if best_cell is None or best_cell in taken:
                continue
            gap, _exposure, cost = best_gap
            if gap >= nearest_now:
                # Nothing gained: this enemy is already as close as any reachable
                # cell gets, so walking there is not progress.
                continue
            taken.add(best_cell)
            plans.append(TurnPlan(
                actions=[PlannedAction(kind="move", from_cell=me.cell,
                                       move_to=best_cell, mp_cost=float(cost))],
                final_cell=best_cell, remaining_ap=board.my_ap,
                remaining_mp=max(0.0, float(board.my_mp) - float(cost)),
                expected_damage=0.0, expected_healing=0.0, kills=[],
                score=APPROACH_VALUE * float(nearest_now - gap),
                notes=["close distance to fighter %s (%d -> %d cells)"
                       % (enemy.id, nearest_now, gap)],
                final_hp=dict(hp), bucket="approach",
                exposure=danger.at(best_cell)))
        return plans

    def plan_for_action(self, board: Board,
                        action: Dict[str, Any]) -> Optional[TurnPlan]:
        """The turn a single client action implies, or None when it is illegal here.

        ``LivePolicy._action_for_plan`` emits one action and the page asks again
        from the new cell, so the shipped rule spends a turn as a *sequence* of
        these. The beam cannot supply them: it folds the walk into the cast and
        keeps only the highest-scoring (spell, target) pairs, so a walk toward a
        target and a direct cast the beam pruned have no candidate plan at all.
        In the first production-arm run that left 122 of 209 turns standing in
        with the solver's own best -- a measurement of the solver, not the
        network. Damage and healing come from the same ``_templates`` the search
        prices with, so an action synthesized here scores the way a beam action
        does and the two sides stay comparable.
        """
        me = board.me
        if me is None or not board.cells.valid(me.cell):
            return None
        hp = {fighter.id: fighter.hp for fighter in board.fighters if fighter.alive}
        occupied = frozenset(f.cell for f in board.fighters
                             if f.alive and f.id != me.id)
        kind = action.get("type")

        if kind == "pass":
            return self._pass_plan(board, ["live action: pass"])

        if kind == "move":
            dest = _int(action.get("cell"))
            if dest is None or dest == me.cell:
                return None
            cost = board.cells.reachable(me.cell, float(board.my_mp),
                                         occupied).get(dest)
            if cost is None:
                return None
            return TurnPlan(
                actions=[PlannedAction(kind="move", from_cell=me.cell,
                                       move_to=dest, mp_cost=float(cost))],
                final_cell=dest, remaining_ap=board.my_ap,
                remaining_mp=max(0.0, float(board.my_mp) - float(cost)),
                expected_damage=0.0, expected_healing=0.0, kills=[], score=0.0,
                notes=["live action: walk"], final_hp=dict(hp))

        if kind != "cast":
            return None
        spell_id = _int(action.get("spellId"))
        target_id = _int(action.get("targetId"))
        target_hp = hp.get(target_id) if target_id is not None else None
        if spell_id is None or target_hp is None or target_hp <= 0:
            return None
        template = next((item for item in self._templates(
            board, me.cell, board.character_stats(), occupied,
            melee=any(s.has_damage and s.max_range <= 1 for s in board.spells),
            me=me, targets=[f for f in board.fighters if f.alive])
            if item.spell.id == spell_id and item.target_id == target_id), None)
        if template is None:
            return None
        # The same arithmetic the beam expansion applies to one cast; a lone
        # action has no prior heals, so the per-node heal ledger is empty.
        dealt = min(template.damage, target_hp)
        killed = template.damage >= target_hp
        direct_gain = max(0, template.target_max_hp - target_hp)
        if template.healing != 0.0:
            direct_gain = min(template.healing, direct_gain)
        else:
            direct_gain = 0.0
        steal_gain = 0.0
        if template.steal_heal > 0:
            steal_gain = min(template.steal_heal, max(0, me.max_hp - me.hp))
        gained = direct_gain + steal_gain
        score = dealt + (KILL_BONUS * template.target_max_hp if killed else 0.0)
        score += HEAL_WEIGHT * gained + template.utility
        final_hp = dict(hp)
        if template.damage_int > 0:
            final_hp[target_id] = max(0, target_hp - template.damage_int)
        spell = template.spell
        remaining_ap = board.my_ap - spell.ap_cost + template.ap_gain
        if me.max_ap > 0:
            remaining_ap = min(remaining_ap, me.max_ap)
        remaining_mp = float(board.my_mp) + template.mp_gain
        if me.max_mp > 0:
            remaining_mp = min(remaining_mp, float(me.max_mp))
        return TurnPlan(
            actions=[PlannedAction(
                kind="cast", spell_id=spell.id, spell_name=spell.name,
                from_cell=me.cell, target_id=target_id,
                target_cell=template.target_cell, ap_cost=spell.ap_cost,
                damage=template.damage, healing=gained, kills=killed,
                moved_target=template.move_target, moved=template.moved)],
            final_cell=me.cell, remaining_ap=max(0, remaining_ap),
            remaining_mp=max(0.0, remaining_mp), expected_damage=dealt,
            expected_healing=gained, kills=[target_id] if killed else [],
            score=score, notes=["live action: cast"], final_hp=final_hp)

    def _pass(self, board: Board, notes: List[str]) -> TurnPlan:
        plan = self._pass_plan(board, notes)
        self.last_candidates = [plan]
        return plan

    def plan_turn(self, board: Board) -> TurnPlan:
        self.last_candidates = []
        notes: List[str] = []
        if not board.in_fight or board.in_preparation or not board.is_my_turn:
            return self._pass(board, notes + ["not the controlled character's turn"])
        me = board.me
        if me is None:
            return self._pass(board, notes + ["controlled fighter is missing from the board"])
        if not board.cells.valid(me.cell):
            return self._pass(board, notes + ["no cell flags for this map"])
        if board.my_ap <= 0:
            return self._pass(board, notes + ["no AP left"])

        skipped = sorted({s.name or str(s.id) for s in board.spells
                          if not s.has_damage and not s.has_heal
                          and not s.has_mechanic})
        if skipped:
            notes.append("not modelled yet (buff/utility/area only): " + ", ".join(skipped))
        blocked = sorted({s.name or str(s.id) for s in board.spells if not s.castable})
        if blocked:
            notes.append("client marks as not castable: " + ", ".join(blocked))
        # A spell the client could not check is treated as available (`_castable`), and
        # the executor re-checks before sending. That is the right call, but it made the
        # reason invisible: the page records `castable_error` when `canCastThisSpell`
        # throws, and nothing said so in the plan. Now the decision row carries it, so a
        # live session that hits a broken client API reads as that instead of as a turn
        # the solver chose to spend.
        unchecked = sorted({"%s: %s" % (s.name or s.id, s.castable_error)
                            for s in board.spells if s.castable_error})
        if unchecked:
            notes.append("client could not check these spells: " + "; ".join(unchecked))

        occupied = frozenset(f.cell for f in board.fighters if f.alive and f.id != me.id)
        template_cache: Dict[int, List[_CastTemplate]] = {}
        reach_cache: Dict[Tuple[int, int], Dict[int, float]] = {}
        los_cache: Dict[Tuple[int, int], bool] = {}
        estimate_cache: Dict[Tuple[int, int, str], Any] = {}
        slide_cache: Dict[Tuple[int, int, int], Tuple[Optional[int], int]] = {}
        character = board.character_stats()
        targets = [f for f in board.fighters if f.alive]
        melee = any(spell.has_damage and spell.max_range <= 1
                    for spell in board.spells)
        # What standing on a cell costs. Built once per turn and priced lazily,
        # because every candidate cell is asked about from several nodes.
        danger = threat.build(board, memory=self._table(), occupied=occupied,
                              turn_price=self._turn_price(board))
        self.last_threat = danger

        def templates(cell: int) -> List[_CastTemplate]:
            if cell in template_cache:
                return template_cache[cell]
            out = self._templates(board, cell, character, occupied, los_cache,
                                  estimate_cache, slide_cache, melee,
                                  me=me, targets=targets)
            template_cache[cell] = out
            return out

        def reach(cell: int, mp: float) -> Dict[int, float]:
            key = (cell, round(mp, 3))
            if key not in reach_cache:
                reach_cache[key] = board.cells.reachable(cell, mp, occupied)
            return reach_cache[key]

        # Teleport is a second way to reach a cast origin. Only destinations
        # that put some fighter back inside a castable range are interesting,
        # and the list is capped so a large ring cannot explode the beam.
        teleport_spells = [spell for spell in board.spells
                           if spell.is_teleport and spell.castable
                           and spell.ap_cost > 0 and not spell.need_taken_cell]
        cast_ranges = [(spell.min_range, spell.max_range)
                       for spell in board.spells
                       if (spell.has_damage or spell.has_heal) and spell.castable]
        fighter_cells = [f.cell for f in board.fighters if f.alive]
        fighter_spells = {spell.id for spell in board.spells
                          if spell.castable and not spell.is_teleport
                          and not spell.need_free_cell
                          and (spell.has_damage or spell.has_heal
                               or spell.has_mechanic)}
        enemy_ids = {f.id for f in board.fighters
                     if f.alive and f.team != "player" and f.id != me.id}
        teleport_cache: Dict[int, List[Tuple[int, LiveSpell]]] = {}

        def teleports(cell: int) -> List[Tuple[int, LiveSpell]]:
            cached = teleport_cache.get(cell)
            if cached is not None:
                return cached
            options: Dict[int, LiveSpell] = {}
            for spell in teleport_spells:
                for dest in board.cells.range_cells(
                        cell, spell.min_range, spell.max_range,
                        spell.cast_in_line, spell.cast_in_diagonal):
                    if dest == cell or dest in occupied \
                            or not board.cells.walkable(dest):
                        continue
                    if not any(low <= board.cells.distance(dest, target_cell) <= high
                               for low, high in cast_ranges
                               for target_cell in fighter_cells):
                        continue
                    if spell.requires_los and not board.cells.has_los(
                            cell, dest, occupied):
                        continue
                    current = options.get(dest)
                    if current is None or spell.ap_cost < current.ap_cost:
                        options[dest] = spell
            if len(options) > TELEPORT_POSITIONS:
                nearest = sorted(options, key=lambda dest: min(
                    board.cells.distance(dest, target_cell)
                    for target_cell in fighter_cells))
                options = {dest: options[dest]
                           for dest in nearest[:TELEPORT_POSITIONS]}
            result = list(options.items())
            teleport_cache[cell] = result
            return result

        def teleport_ready(spell: LiveSpell, node: _Node) -> bool:
            uses = spell.casts_this_turn + node.spell_uses.get(spell.id, 0)
            if spell.max_cast_per_turn is not None \
                    and uses >= spell.max_cast_per_turn:
                return False
            if spell.min_cast_interval > 0 and uses > 0:
                return False
            return True

        # The cheapest cast positions per spell/target depend only on the
        # cell and MP a node expands from, not on its AP, uses or HP. Casting
        # from the current cell is the common case, so the beam's eight nodes
        # often share one (cell, MP) at a depth; build their buckets once.
        bucket_cache: Dict[Tuple[int, float],
                           List[Tuple[float, int, int, _CastTemplate,
                                      Optional[LiveSpell]]]] = {}
        hp = {f.id: f.hp for f in board.fighters if f.alive}
        start = _Node(cell=me.cell, ap=board.my_ap, mp=float(board.my_mp), hp=hp,
                      spell_uses={}, target_uses={}, healed_by_target={},
                      actions=[], damage=0.0, healing=0.0, kills=[], score=0.0,
                      moved_targets=frozenset(),
                      marks=board.marks,
                      summons_count=board.active_my_summons,
                      summon_types=board.active_summon_types)
        best = start
        frontier = [start]
        seen: Dict[Tuple[Any, ...], float] = {}
        expanded = 0

        # Damage, AP cost and targets do not depend on the cell a spell is cast
        # from; only the walk cost and the final position do. Expanding every
        # reachable cell multiplied each frontier node by the whole zone, while
        # the beam only ever keeps a handful of plans. Keep the cheapest few
        # cells per spell/target, then restore reach order so equal-score ties
        # resolve as before. The live executor casts from the current cell,
        # so the discarded far cast positions were never emitted anyway.
        # A wider beam keeps more alternatives, so it needs more positions per
        # cast to stay faithful; the live beam (8) only needs the cheapest three.
        reps_per_cast = max(1, min(
            CAST_POSITIONS + max(0, self.beam_width - 8) // 2,
            CAST_POSITIONS + 3,
        ))
        for _depth in range(self.max_actions):
            candidates: List[_Node] = []
            for node in frontier:
                if expanded >= self.max_nodes:
                    notes.append("search stopped at the node budget")
                    break
                expanded += 1
                walked = reach(node.cell, node.mp)
                walk_key = (node.cell, round(node.mp, 3))
                representatives = bucket_cache.get(walk_key)
                if representatives is None:
                    by_cast: Dict[Tuple[int, int],
                                  List[Tuple[float, int, int, _CastTemplate]]] = {}
                    for index, (cell, mp_cost) in enumerate(walked.items()):
                        for template in templates(cell):
                            key = (template.spell.id,
                                   template.target_cell if (template.is_trap_placement or template.is_summon)
                                   else template.target_id)
                            bucket = by_cast.get(key)
                            if bucket is None:
                                by_cast[key] = [(mp_cost, index, cell, template)]
                            elif len(bucket) < reps_per_cast:
                                # ``reachable`` inserts cells in BFS order, so
                                # MP cost is non-decreasing and the first
                                # entries are already the cheapest few. Stop
                                # storing once the bucket is full instead of
                                # building a huge list and slicing it later.
                                bucket.append((mp_cost, index, cell, template))
                    representatives = []
                    for bucket in by_cast.values():
                        representatives.extend(
                            (mp_cost, index, cell, template, None)
                            for mp_cost, index, cell, template in bucket)
                    representatives.sort(key=lambda item: item[1])
                    bucket_cache[walk_key] = representatives
                # A cast pair the walk already covers gains nothing from a
                # teleport: the same spell would simply cost more AP. Only
                # uncovered pairs are tried from teleport destinations, and
                # each such pair uses the first (closest) destination. The
                # live executor replans after each action, so a teleport is
                # only searched as the turn opener; expanding it from every
                # beam node at every depth multiplied the template work.
                if teleport_spells and node.ap > 1 and _depth == 0:
                    walked_targets = set()
                    walked_spells = set()
                    for _mp, _idx, _cell, template, _tele in representatives:
                        walked_targets.add(template.target_id)
                        walked_spells.add(template.spell.id)
                    # If every enemy is already hit and every fighter spell
                    # already has a legal cast position, a teleport can only
                    # add AP cost: skip the destination search entirely.
                    if not (enemy_ids <= walked_targets
                            and fighter_spells <= walked_spells):
                        covered = {(template.spell.id,
                                    template.target_cell if (template.is_trap_placement or template.is_summon)
                                    else template.target_id)
                                   for _mp, _idx, _cell, template, _tele
                                   in representatives}
                        extra = []
                        for dest, tele in teleports(node.cell):
                            if dest in walked:
                                continue
                            if tele.ap_cost >= node.ap or not teleport_ready(tele, node):
                                continue
                            for template in templates(dest):
                                key = (template.spell.id,
                                       template.target_cell if (template.is_trap_placement or template.is_summon)
                                       else template.target_id)
                                if key in covered:
                                    continue
                                covered.add(key)
                                extra.append(
                                    (0.0, 1 << 30, dest, template, tele))
                        if extra:
                            representatives = representatives + extra
                for mp_cost, _index, cell, template, tele in representatives:
                    if template.target_id != 0 and template.target_id in node.moved_targets:
                        continue
                    if template.target_id:
                        target_hp = node.hp[template.target_id]
                        if target_hp <= 0:
                            continue
                    else:
                        target_hp = 1
                    spell = template.spell
                    ap_cost = spell.ap_cost + (tele.ap_cost if tele is not None else 0)
                    if ap_cost > node.ap:
                        continue
                    if spell.max_cast_per_turn is not None and (
                            spell.casts_this_turn
                            + node.spell_uses.get(spell.id, 0)
                            >= spell.max_cast_per_turn):
                        continue
                    if (spell.min_cast_interval > 0 and (
                            spell.casts_this_turn
                            + node.spell_uses.get(spell.id, 0)) > 0):
                        continue
                    key_uses = (spell.id, template.target_cell if (template.is_trap_placement or template.is_summon) else template.target_id)
                    if spell.max_cast_per_target is not None and (
                            node.target_uses.get(key_uses, 0)
                            >= spell.max_cast_per_target):
                        continue
                    if template.is_summon or spell.is_summon:
                        if node.summons_count >= board.max_summons:
                            continue
                        s_type = spell.summon_type if spell.summon_type is not None else spell.id
                        if s_type in node.summon_types:
                            continue

                    node_marks = node.marks
                    cast_cell = cell
                    walk_damage = 0.0
                    walk_ap_loss = 0
                    walk_mp_loss = 0
                    if cell != node.cell and tele is None:
                        # Check if caster walked onto an active trap
                        if node_marks.traps_at(cell, board.cells):
                            c_dest, w_dmg, w_ap, w_mp, next_w_marks, _ = self._simulate_trap_chain(
                                board=board,
                                fighter=me,
                                target_cell=cell,
                                entry_from_cell=node.cell,
                                marks=node_marks,
                                character=character,
                                blocked=occupied,
                                max_depth=8,
                            )
                            cast_cell = c_dest
                            node_marks = next_w_marks
                            walk_damage = w_dmg
                            walk_ap_loss = w_ap
                            walk_mp_loss = w_mp

                    if template.is_trap_placement:
                        target_cell = template.target_cell
                        push_r = 0
                        for e in spell.effects:
                            if e.kind in ("push", "pushed"):
                                push_r += int(round((e.min_roll + e.max_roll) / 2.0))
                            elif e.kind in ("pull", "pulled"):
                                push_r -= int(round((e.min_roll + e.max_roll) / 2.0))
                        placed_mark = BoardMark(
                            mark_id=-(len(node.actions) + 1),
                            mark_type="trap",
                            cell=target_cell,
                            radius=spell.zone_size or 0,
                            owner_id=me.id,
                            team="player",
                            spell_id=spell.id,
                            effects=spell.effects,
                            push_distance=push_r,
                            duration=1,
                        )
                        triggered_enemy = next((
                            f for f in board.fighters
                            if f.alive and f.team != "player" and board.cells.distance(target_cell, f.cell) <= placed_mark.radius
                        ), None)
                        if triggered_enemy is not None:
                            dest_c, chain_dmg, chain_ap_l, chain_mp_l, next_marks, _ = self._simulate_trap_chain(
                                board=board,
                                fighter=triggered_enemy,
                                target_cell=triggered_enemy.cell,
                                entry_from_cell=triggered_enemy.cell,
                                marks=node_marks.with_mark(placed_mark),
                                character=character,
                                blocked=occupied,
                                max_depth=8,
                            )
                            t_enemy_hp = node.hp.get(triggered_enemy.id, 0)
                            dealt = min(chain_dmg, t_enemy_hp)
                            kill = chain_dmg >= t_enemy_hp
                            base_score = dealt + (KILL_BONUS * triggered_enemy.max_hp if kill else 0.0) + template.utility
                            new_hp = dict(node.hp)
                            new_hp[triggered_enemy.id] = max(0, t_enemy_hp - int(round(chain_dmg)))
                            kills = (node.kills + [triggered_enemy.id]) if kill else node.kills
                            action = PlannedAction(
                                kind="cast", spell_id=spell.id, spell_name=spell.name,
                                from_cell=cast_cell,
                                move_to=(cast_cell if cast_cell != node.cell and tele is None else None),
                                target_id=triggered_enemy.id, target_cell=target_cell,
                                mp_cost=mp_cost + walk_mp_loss, ap_cost=spell.ap_cost + walk_ap_loss,
                                damage=dealt, healing=0.0, kills=kill,
                                moved_target=(dest_c if dest_c != triggered_enemy.cell else None),
                            )
                        else:
                            next_marks = node_marks.with_mark(placed_mark)
                            dealt = 0.0
                            kill = False
                            kills = node.kills
                            new_hp = node.hp
                            base_score = template.utility
                            action = PlannedAction(
                                kind="cast", spell_id=spell.id, spell_name=spell.name,
                                from_cell=cast_cell,
                                move_to=(cast_cell if cast_cell != node.cell and tele is None else None),
                                target_id=0, target_cell=target_cell,
                                mp_cost=mp_cost + walk_mp_loss, ap_cost=spell.ap_cost + walk_ap_loss,
                                damage=0.0, healing=0.0, kills=False,
                            )
                        direct_gain = 0.0
                        steal_gain = 0.0
                        gained = 0.0
                        next_summons_count = node.summons_count
                        next_summon_types = node.summon_types
                    elif template.is_summon or spell.is_summon:
                        s_type = spell.summon_type if spell.summon_type is not None else spell.id
                        next_summons_count = node.summons_count + 1
                        next_summon_types = node.summon_types | {s_type}
                        next_marks = node_marks
                        dealt = 0.0
                        kill = False
                        kills = node.kills
                        new_hp = node.hp
                        base_score = template.utility
                        direct_gain = 0.0
                        steal_gain = 0.0
                        gained = 0.0
                        action = PlannedAction(
                            kind="cast", spell_id=spell.id, spell_name=spell.name,
                            from_cell=cast_cell,
                            move_to=(cast_cell if cast_cell != node.cell and tele is None else None),
                            target_id=0, target_cell=template.target_cell,
                            mp_cost=mp_cost + walk_mp_loss, ap_cost=spell.ap_cost + walk_ap_loss,
                            damage=0.0, healing=0.0, kills=False,
                        )
                    else:
                        next_summons_count = node.summons_count
                        next_summon_types = node.summon_types
                        chain_damage = 0.0
                        chain_dest = template.move_target
                        next_marks = node_marks
                        chain_ap_l = 0
                        chain_mp_l = 0
                        if (template.moved > 0 or template.move_target is not None) and template.target_id in node.hp:
                            dest = template.move_target or template.target_cell
                            if dest is not None and node_marks.traps_at(dest, board.cells):
                                target_f = next((f for f in board.fighters if f.id == template.target_id), None)
                                if target_f is not None:
                                    chain_dest, chain_damage, chain_ap_l, chain_mp_l, next_marks, _ = \
                                        self._simulate_trap_chain(
                                            board=board,
                                            fighter=target_f,
                                            target_cell=dest,
                                            entry_from_cell=target_f.cell,
                                            marks=node_marks,
                                            character=character,
                                            blocked=occupied,
                                            max_depth=8,
                                        )
                        total_cast_damage = template.damage + chain_damage
                        dealt = min(total_cast_damage, target_hp)
                        kill = total_cast_damage >= target_hp
                        direct_gain = 0.0
                        if template.healing != 0.0:
                            direct_gain = min(
                                template.healing,
                                max(0, template.target_max_hp - target_hp
                                    - node.healed_by_target.get(
                                        template.target_id, 0)),
                            )
                        steal_gain = 0.0
                        if template.steal_heal > 0:
                            steal_gain = min(
                                template.steal_heal,
                                max(0, me.max_hp - me.hp
                                    - node.healed_by_target.get(me.id, 0)),
                            )
                        gained = direct_gain + steal_gain
                        base_score = dealt + (KILL_BONUS * template.target_max_hp if kill else 0.0)
                        base_score += HEAL_WEIGHT * gained + template.utility
                        base_score += AP_DENIAL_WEIGHT * chain_ap_l + MP_DENIAL_WEIGHT * chain_mp_l
                        if total_cast_damage > 0:
                            new_hp = dict(node.hp)
                            new_hp[template.target_id] = max(0, target_hp - int(round(total_cast_damage)))
                        else:
                            new_hp = node.hp
                        kills = (node.kills if not kill
                                 else node.kills + [template.target_id])
                        action = PlannedAction(
                            kind="cast", spell_id=spell.id, spell_name=spell.name,
                            from_cell=cast_cell,
                            move_to=(cast_cell if cast_cell != node.cell and tele is None
                                     else None),
                            target_id=template.target_id, target_cell=template.target_cell,
                            mp_cost=mp_cost + walk_mp_loss, ap_cost=spell.ap_cost + walk_ap_loss,
                            damage=dealt, healing=gained, kills=kill,
                            moved_target=chain_dest, moved=template.moved,
                        )

                    if base_score <= 0:
                        continue
                    # The half of the turn the search used to ignore: a cast
                    # from the open and the same cast from behind a wall scored
                    # the same, so nothing kept the character out of reach. The
                    # price rises as health runs out, so a hurt fighter trades
                    # damage for cover while a healthy one keeps attacking.
                    step_score = base_score - threat.weight_for(me) * danger.swing(
                        node.cell, cast_cell)
                    if direct_gain > 0 or steal_gain > 0:
                        healed_by_target = dict(node.healed_by_target)
                        if direct_gain > 0:
                            healed_by_target[template.target_id] = (
                                healed_by_target.get(template.target_id, 0)
                                + direct_gain)
                        if steal_gain > 0:
                            healed_by_target[me.id] = (
                                healed_by_target.get(me.id, 0) + steal_gain)
                    else:
                        healed_by_target = node.healed_by_target
                    uses = dict(node.spell_uses)
                    uses[spell.id] = uses.get(spell.id, 0) + 1
                    if tele is not None:
                        uses[tele.id] = uses.get(tele.id, 0) + 1
                    target_uses = dict(node.target_uses)
                    target_uses[key_uses] = target_uses.get(key_uses, 0) + 1
                    actions = node.actions
                    if tele is not None:
                        actions = actions + [PlannedAction(
                            kind="cast", spell_id=tele.id, spell_name=tele.name,
                            from_cell=node.cell, target_id=me.id,
                            target_cell=cell, ap_cost=tele.ap_cost)]
                    next_ap = max(0, node.ap - ap_cost - walk_ap_loss + template.ap_gain)
                    if me.max_ap > 0:
                        next_ap = min(next_ap, me.max_ap)
                    next_mp = max(0.0, node.mp - mp_cost - walk_mp_loss + template.mp_gain)
                    if me.max_mp > 0:
                        next_mp = min(next_mp, float(me.max_mp))
                    candidate = _Node(
                        cell=cast_cell, ap=next_ap, mp=next_mp, hp=new_hp,
                        spell_uses=uses, target_uses=target_uses,
                        healed_by_target=healed_by_target,
                        actions=actions + [action],
                        damage=node.damage + dealt,
                        healing=node.healing + gained,
                        kills=kills, score=node.score + step_score,
                        moved_targets=(node.moved_targets | {template.target_id}
                                       if (template.target_id != 0 and (template.moved > 0 or template.move_target is not None))
                                       else node.moved_targets),
                        marks=next_marks,
                        summons_count=next_summons_count,
                        summon_types=next_summon_types,
                    )
                    # The first expansion has one parent and every cast spends
                    # AP, so no later state can equal a depth-0 candidate and
                    # the one-parent level has nothing to deduplicate. Build
                    # its state signature only from the second level on.
                    if _depth:
                        signature = (
                            candidate.cell, candidate.ap, candidate.mp,
                            # Fighter ids never enter or leave this dict; the
                            # insertion order from the initial hp map is
                            # stable, so sorting it on every candidate was
                            # wasted work.
                            tuple(candidate.hp.values()),
                            # These maps are only used for equality. A
                            # frozenset avoids sorting every pair to hash it.
                            frozenset(candidate.spell_uses.items()),
                            frozenset(candidate.target_uses.items()),
                            (frozenset(candidate.healed_by_target.items())
                             if candidate.healed_by_target else _EMPTY_FROZENSET),
                            (candidate.moved_targets
                             if candidate.moved_targets else _EMPTY_FROZENSET),
                            tuple(candidate.marks.marks),
                            candidate.summons_count,
                            candidate.summon_types,
                        )
                        if signature in seen and seen[signature] >= candidate.score - _EPS:
                            continue
                        seen[signature] = candidate.score
                        seen[signature] = candidate.score
                    candidates.append(candidate)
                    if candidate.score > best.score + _EPS:
                        best = candidate
            if not candidates:
                break
            candidates.sort(key=lambda n: (-n.score, n.ap, -n.mp, len(n.actions)))
            frontier = candidates[:self.beam_width]

        if not best.actions:
            return self._pass(board, notes)
        plan = self._to_plan(best, notes)
        plan.bucket = self._classify_plan(plan, board)
        plan.exposure = danger.at(plan.final_cell)
        self._apply_post_cast_walk(board, plan, me, danger)
        nodes = [best] + [node for node in frontier if node.actions]
        plans: List[TurnPlan] = []
        seen_plans = set()
        for node in sorted(nodes, key=lambda item: (-item.score, item.ap, -item.mp)):
            signature = tuple(
                (action.kind, action.move_to, action.spell_id, action.target_id)
                for action in node.actions)
            if signature in seen_plans:
                continue
            seen_plans.add(signature)
            candidate = self._to_plan(node, notes)
            candidate.exposure = danger.at(node.cell)
            self._apply_post_cast_walk(board, candidate, me, danger)
            plans.append(candidate)
        plans.append(self._pass_plan(board, notes))
        plans[-1].exposure = danger.at(plans[-1].final_cell)
        self.last_candidates = self._stratify(plans, board, self.beam_width)
        return plan

    def _classify_plan(self, plan: TurnPlan, board: Board) -> str:
        """The strategic bucket a legal plan belongs to.

        The buckets are a budget, not new mechanics: direct damage, displacement/
        kiting, setup (traps/glyphs/buffs/AP-MP/push-pull), and boss state-lock
        breakers. A plan with a mechanic on a boss is boss work; a plan that moves
        a target is displacement; a plan with a non-damaging mechanic is setup;
        otherwise it is damage. Unsupported mechanics stay in ``notes`` and are
        not priced as damage.
        """
        actions = plan.actions or []
        if not actions or actions[0].kind == "pass":
            return "pass"
        spells = {spell.id: spell for spell in board.spells}
        boss_ids = {fighter.id for fighter in board.fighters
                    if getattr(fighter, "is_boss", False)}
        moved = any(action.moved or action.moved_target is not None
                    for action in actions)
        mechanics = []
        for action in actions:
            spell = spells.get(action.spell_id)
            if spell is not None and spell.has_mechanic:
                mechanics.append((action, spell))
        if any(action.target_id in boss_ids for action, _spell in mechanics):
            return "boss"
        if moved:
            return "displacement"
        if mechanics:
            return "setup"
        if plan.expected_damage > 0 or plan.expected_healing > 0:
            return "damage"
        return "setup"

    def _stratify(self, plans: List[TurnPlan], board: Board,
                  width: int) -> List[TurnPlan]:
        """Keep up to four plans per strategic bucket inside the beam width.

        The best plan stays first. The pass plan is always retained so a caller
        that wants alternatives can still see the do-nothing option.
        """
        for plan in plans:
            plan.bucket = self._classify_plan(plan, board)
        selected: List[TurnPlan] = []
        seen = set()

        def signature(plan: TurnPlan):
            return tuple((action.kind, action.move_to, action.spell_id,
                          action.target_id) for action in plan.actions)

        def add(plan: TurnPlan) -> bool:
            signature_key = signature(plan)
            if signature_key in seen:
                return False
            seen.add(signature_key)
            selected.append(plan)
            return True

        if plans:
            add(plans[0])
        for bucket in ("damage", "displacement", "setup", "boss"):
            if len(selected) >= width:
                break
            taken = 0
            for plan in plans:
                if plan.bucket != bucket:
                    continue
                if len(selected) >= width:
                    break
                if add(plan):
                    taken += 1
                if taken >= 4:
                    break
        pass_plan = next((plan for plan in plans
                          if plan.bucket == "pass"), None)
        for plan in plans:
            if len(selected) >= width:
                break
            add(plan)
        if pass_plan is not None:
            add(pass_plan)
        self.last_buckets = {}
        for plan in selected:
            self.last_buckets[plan.bucket] = self.last_buckets.get(plan.bucket, 0) + 1
        return selected

    def _safest_reachable_cell(
        self, board: Board, start_cell: int, mp: float, danger: Any
    ) -> Optional[Tuple[int, float, float]]:
        """Return ``(cell, threat, mp_cost)`` of the strictly safest reachable cell.

        Only considers cells where ``danger.at(cell) < danger.at(start_cell)``.
        Returns ``None`` when no improvement is possible or ``mp`` is exhausted.
        The walk uses the client's fight walker: cardinal steps, one MP per cell,
        occupied cells not enterable.
        """
        if mp <= 0:
            return None
        occupied = frozenset(
            f.cell
            for f in board.fighters
            if f.alive and f.id != board.my_id
        )
        try:
            reach = board.cells.reachable(start_cell, mp, occupied)
        except Exception:  # noqa: BLE001 - a path search must not cost the turn
            return None
        current_threat = danger.at(start_cell)
        best_cell: Optional[int] = None
        best_threat = current_threat
        best_cost = 0.0
        for cell, cost in reach.items():
            if cell == start_cell or cell in occupied:
                continue
            cell_threat = danger.at(cell)
            if cell_threat < best_threat:
                best_cell = cell
                best_threat = cell_threat
                best_cost = cost
        if best_cell is None:
            return None
        return (best_cell, best_threat, best_cost)

    def _apply_post_cast_walk(
        self, board: Board, plan: TurnPlan, me: Any, danger: Any
    ) -> None:
        """Spend leftover MP stepping behind cover or out of enemy reach.

        Mutates ``plan`` in place: appends a move action, updates ``final_cell``,
        ``remaining_mp``, ``score``, ``notes`` and ``exposure``. Called after the
        beam search has chosen a cast so the character does not stop inside enemy
        reach when MP remains.

        The actions list is copied before appending so the source node's list is
        never aliased -- the same ``_Node.actions`` may feed both the returned
        plan and a candidate in ``plans``.
        """
        if plan.remaining_mp <= 0:
            return
        walk = self._safest_reachable_cell(
            board, plan.final_cell, plan.remaining_mp, danger
        )
        if walk is None:
            return
        walk_cell, walk_threat, walk_cost = walk
        current_threat = danger.at(plan.final_cell)
        if walk_threat >= current_threat:
            return
        new_actions = list(plan.actions)
        new_actions.append(
            PlannedAction(
                kind="move",
                from_cell=plan.final_cell,
                move_to=walk_cell,
                mp_cost=float(walk_cost),
            )
        )
        plan.actions = new_actions
        plan.final_cell = walk_cell
        plan.remaining_mp = max(0.0, plan.remaining_mp - walk_cost)
        plan.score -= threat.weight_for(me) * danger.swing(
            plan.final_cell, walk_cell
        )
        plan.notes.append(
            "kite to safer cell %d (threat %.1f -> %.1f)"
            % (walk_cell, current_threat, walk_threat)
        )
        plan.exposure = walk_threat

    def _calculate_trap_damage(
        self, board: Board, trap: BoardMark, character: CharacterStats,
        fighter: Fighter,
    ) -> float:
        spell = next((s for s in board.spells if s.id == trap.spell_id), None)
        if spell is not None and spell.has_damage:
            res = self._damage_result(board, spell, character, fighter)
            if res is not None:
                return float(res.expected_value)
        if trap.effects:
            damage_effects = [e for e in trap.effects
                              if e.kind in ("damage", "steal") or e.effect_id in ELEMENT_BY_EFFECT]
            if damage_effects:
                spell_info_effects = []
                for e in damage_effects:
                    elem = e.element or ELEMENT_BY_EFFECT.get(e.effect_id, ("damage", "NEUTRAL"))[1]
                    spell_info_effects.append(DamageEffect(
                        element=elem,
                        min_roll=max(1, e.min_roll),
                        max_roll=max(max(1, e.min_roll), e.max_roll),
                        crit_min_roll=0,
                        crit_max_roll=0,
                    ))
                info = SpellInfo(
                    id=trap.spell_id or trap.mark_id,
                    name_fr="Trap",
                    name_en="Trap",
                    name_es="",
                    breed_id=0,
                    level=1,
                    ap_cost=1,
                    min_range=0,
                    max_range=0,
                    cast_in_line=False,
                    cast_in_diagonal=False,
                    cast_test_los=False,
                    effects=spell_info_effects,
                    crit_rate_percent=0.0,
                )
                res = DofusDamageCalculator.calculate_spell_damage(
                    info, character, fighter.target_stats())
                if res is not None:
                    return float(res.expected_value)
        return 0.0

    def _calculate_trap_denial(self, trap: BoardMark) -> Tuple[int, int]:
        ap_loss = 0
        mp_loss = 0
        for e in trap.effects:
            if e.kind in ("ap_loss", "ap_steal"):
                ap_loss += int(round((e.min_roll + e.max_roll) / 2.0))
            elif e.kind in ("mp_loss",):
                mp_loss += int(round((e.min_roll + e.max_roll) / 2.0))
        return ap_loss, mp_loss

    def _simulate_trap_chain(
        self,
        board: Board,
        fighter: Fighter,
        target_cell: int,
        entry_from_cell: int,
        marks: BoardMarks,
        character: CharacterStats,
        blocked: FrozenSet[int],
        max_depth: int = 8,
    ) -> Tuple[int, float, int, int, BoardMarks, List[str]]:
        curr_cell = target_cell
        prev_cell = entry_from_cell
        curr_marks = marks
        total_damage = 0.0
        total_ap_loss = 0
        total_mp_loss = 0
        chain_depth = 0
        notes: List[str] = []
        cells = board.cells

        while chain_depth < max_depth:
            traps = curr_marks.traps_at(curr_cell, cells)
            if not traps:
                break

            pushed = False
            for trap in traps:
                if chain_depth >= max_depth:
                    notes.append("trap chain reached max depth %d" % max_depth)
                    break
                chain_depth += 1
                curr_marks = curr_marks.without_mark(trap.mark_id)

                dmg = self._calculate_trap_damage(board, trap, character, fighter)
                total_damage += dmg

                ap_l, mp_l = self._calculate_trap_denial(trap)
                total_ap_loss += ap_l
                total_mp_loss += mp_l

                push_dist = trap.push_distance
                if push_dist != 0:
                    if curr_cell != trap.cell:
                        origin = trap.cell
                    elif prev_cell != curr_cell:
                        origin = prev_cell
                    else:
                        origin = board.my_cell

                    next_cell = cells.slide(
                        origin, curr_cell, abs(push_dist), blocked,
                        toward=(push_dist < 0)
                    )
                    if next_cell != curr_cell:
                        prev_cell = curr_cell
                        curr_cell = next_cell
                        pushed = True
                        break
            if not pushed:
                break

        return curr_cell, total_damage, total_ap_loss, total_mp_loss, curr_marks, notes

    def _templates(self, board: Board, cell: int, character: CharacterStats,
                   occupied: FrozenSet[int],
                   los_cache: Optional[Dict[Tuple[int, int], bool]] = None,
                   estimate_cache: Optional[Dict[Tuple[int, int, str], Any]] = None,
                   slide_cache: Optional[Dict[Tuple[int, int, int],
                                                  Tuple[Optional[int], int]]] = None,
                   melee: bool = False,
                   me: Optional[Fighter] = None,
                   targets: Optional[List[Fighter]] = None
                   ) -> List[_CastTemplate]:
        me = me if me is not None else board.me
        assert me is not None
        targets = targets if targets is not None else [
            f for f in board.fighters if f.alive]
        out: List[_CastTemplate] = []
        cells = board.cells
        if not cells.walkable(cell):
            return out
        distances = [cells.distance(cell, target.cell) for target in targets]
        my_id = board.my_id
        for spell in board.spells:
            if spell.ap_cost <= 0 or spell.ap_cost > board.my_ap:
                continue
            if not spell.castable:
                continue
            if (spell.max_cast_per_turn is not None
                    and spell.casts_this_turn >= spell.max_cast_per_turn):
                continue
            if spell.min_cast_interval > 0 and spell.casts_this_turn > 0:
                continue
            has_damage = spell.has_damage
            has_heal = spell.has_heal
            # `PLANNED_MECHANIC_KINDS` already contains "trap", "glyph" and
            # "summon", so this gate admits both. (It is stated with the trap and
            # summon flags too, so a future edit to that set cannot silently
            # re-close the path below -- the branches it guards were unreachable
            # once already for this reason.)
            if (not has_damage and not has_heal and not spell.has_mechanic
                    and not spell.is_trap and not spell.is_summon):
                continue
            # Teleports are handled as alternate cast origins; a spell that
            # only needs a taken cell cannot be planned with this board.
            if spell.is_teleport:
                continue
            if spell.need_free_cell and not (spell.is_trap or spell.is_summon):
                continue
            if spell.is_trap:
                in_range = cells.range_cells(
                    cell, spell.min_range, spell.max_range,
                    spell.cast_in_line, spell.cast_in_diagonal)
                if in_range:
                    candidate_cells = set()
                    for enemy in targets:
                        if enemy.team == "player" or not enemy.alive:
                            continue
                        ex, ey = cell_point(enemy.cell)
                        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                            for step in range(1, 7):
                                p = _POINTS.get((ex + dx * step, ey + dy * step))
                                if p is not None and p in in_range:
                                    candidate_cells.add(p)
                    for mark in board.marks.traps:
                        mx, my = cell_point(mark.cell)
                        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                            for step in (1, 2, 3):
                                p = _POINTS.get((mx + dx * step, my + dy * step))
                                if p is not None and p in in_range:
                                    candidate_cells.add(p)
                    if not candidate_cells:
                        candidate_cells = set(c for c in in_range if c not in occupied and cells.walkable(c))

                    valid_trap_cells = []
                    for c in candidate_cells:
                        if c in occupied or not cells.walkable(c):
                            continue
                        if spell.requires_los:
                            pair = (cell, c)
                            visible = los_cache.get(pair) if los_cache is not None else None
                            if visible is None:
                                visible = cells.has_los(cell, c, occupied)
                                if los_cache is not None:
                                    los_cache[pair] = visible
                            if not visible:
                                continue
                        valid_trap_cells.append(c)

                    enemies_alive = [t for t in targets if t.team != "player" and t.alive]
                    if enemies_alive:
                        valid_trap_cells.sort(key=lambda c: min(cells.distance(c, e.cell) for e in enemies_alive))

                    # A trap scores by the traffic its cell will see, not by a
                    # constant. Dropped at 1.0 against hits in the hundreds it
                    # lost the beam sort every time and never became a plan, so
                    # the `setup` bucket was never populated on any board.
                    placement = _trade_module().free_placements(board, valid_trap_cells)
                    for c in valid_trap_cells[:8]:
                        out.append(_CastTemplate(
                            spell=spell, target_id=0, target_cell=c,
                            damage=0.0, healing=0.0, steal_heal=0.0,
                            target_max_hp=1, damage_int=0,
                            utility=placement.get(c, 1.0), move_target=None,
                            moved=0, is_trap_placement=True,
                        ))
                continue
            if spell.is_summon:
                in_range = cells.range_cells(
                    cell, spell.min_range, spell.max_range,
                    spell.cast_in_line, spell.cast_in_diagonal)
                if in_range:
                    valid_summon_cells = []
                    for c in in_range:
                        if c in occupied or not cells.walkable(c):
                            continue
                        if spell.requires_los:
                            pair = (cell, c)
                            visible = los_cache.get(pair) if los_cache is not None else None
                            if visible is None:
                                visible = cells.has_los(cell, c, occupied)
                                if los_cache is not None:
                                    los_cache[pair] = visible
                            if not visible:
                                continue
                        valid_summon_cells.append(c)
                    enemies_alive = [t for t in targets if t.team != "player" and t.alive]
                    if enemies_alive:
                        valid_summon_cells.sort(key=lambda c: min(cells.distance(c, e.cell) for e in enemies_alive))
                    # A summon is a body, and a body is worth what it blocks.
                    # The flat 15.0 this replaces valued a summon the same in a
                    # doorway and in the middle of an empty room.
                    posting = _trade_module().summon_postings(board, valid_summon_cells)
                    for c in valid_summon_cells[:4]:
                        out.append(_CastTemplate(
                            spell=spell, target_id=0, target_cell=c,
                            damage=0.0, healing=0.0, steal_heal=0.0,
                            target_max_hp=1, damage_int=0,
                            utility=posting.get(c, 15.0), move_target=None,
                            moved=0, is_summon=True,
                        ))
                continue
            ap_gain, mp_gain, ap_loss, mp_loss, push_roll, pull_roll = \
                spell.mechanic_rolls
            # Self economy buffs have no fighter target: the caster is the
            # target and the effect adds to this turn's budget.
            if (ap_gain or mp_gain) and not has_damage \
                    and not has_heal and spell.min_range == 0 \
                    and spell.max_range == 0:
                out.append(_CastTemplate(
                    spell=spell, target_id=me.id, target_cell=cell,
                    damage=0.0, healing=0.0, steal_heal=0.0,
                    target_max_hp=max(1, me.max_hp), damage_int=0,
                    ap_gain=ap_gain, mp_gain=mp_gain,
                    utility=(AP_GAIN_WEIGHT * ap_gain
                             + MP_GAIN_WEIGHT * mp_gain),
                ))
                continue
            enemy_utility = bool(ap_loss or mp_loss or push_roll or pull_roll)
            is_steal = any(e.kind == "steal" for e in spell.damage_effects)
            needs_taken = spell.need_taken_cell
            # A ring costs ~30 us; a Manhattan pre-check costs a few
            # comparisons and rejects the common case where every target is
            # outside the spell's reach from this cell.
            if not any(distance <= spell.max_range for distance in distances):
                continue
            in_range = cells.range_cells(
                cell, spell.min_range, spell.max_range,
                spell.cast_in_line, spell.cast_in_diagonal)
            if not in_range:
                continue
            for target, distance_to_target in zip(targets, distances):
                if target.cell not in in_range:
                    continue
                if spell.requires_los:
                    pair = (cell, target.cell)
                    visible = los_cache.get(pair) if los_cache is not None else None
                    if visible is None:
                        visible = cells.has_los(cell, target.cell, occupied)
                        if los_cache is not None:
                            los_cache[pair] = visible
                    if not visible:
                        continue
                is_enemy = target.team != "player" and target.id != my_id
                if is_enemy:
                    if not has_damage and not enemy_utility:
                        continue
                elif not has_heal:
                    continue
                damage = 0.0
                healing = 0.0
                steal_heal = 0.0
                if is_enemy and has_damage:
                    key_damage = (spell.id, target.id, "damage")
                    if estimate_cache is not None and key_damage in estimate_cache:
                        result = estimate_cache[key_damage]
                    else:
                        result = self._damage_result(board, spell, character, target)
                        if estimate_cache is not None:
                            estimate_cache[key_damage] = result
                    damage = result.expected_value if result else 0.0
                    if is_steal:
                        steal_heal = damage
                elif not is_enemy and has_heal:
                    key_heal = (spell.id, target.id, "heal")
                    if estimate_cache is not None and key_heal in estimate_cache:
                        result = estimate_cache[key_heal]
                    else:
                        result = self._estimate(board, spell, character, target, "heal")
                        if estimate_cache is not None:
                            estimate_cache[key_heal] = result
                    healing = result.expected_value if result else 0.0
                elif not is_enemy and target.id == me.id and has_damage:
                    continue
                # Denial is capped by what the target actually has: removing
                # AP a fighter already spent denies nothing.
                if is_enemy:
                    cap_ap = target.ap if target.ap > 0 else max(0, target.max_ap)
                    cap_mp = target.mp if target.mp > 0 else max(0, target.max_mp)
                    target_ap_loss = min(ap_loss, cap_ap)
                    target_mp_loss = min(mp_loss, cap_mp)
                    target_ap_gain = 0
                    target_mp_gain = 0
                else:
                    target_ap_loss = 0
                    target_mp_loss = 0
                    target_ap_gain = ap_gain
                    target_mp_gain = mp_gain
                utility = (AP_DENIAL_WEIGHT * target_ap_loss
                           + MP_DENIAL_WEIGHT * target_mp_loss
                           + AP_GAIN_WEIGHT * target_ap_gain
                           + MP_GAIN_WEIGHT * target_mp_gain)
                move_target: Optional[int] = None
                moved = 0
                if is_enemy and (push_roll or pull_roll):
                    key_slide = (spell.id, cell, target.id)
                    cached_slide = (slide_cache.get(key_slide)
                                    if slide_cache is not None else None)
                    if cached_slide is not None:
                        move_target, moved = cached_slide
                    else:
                        blocked = frozenset(
                            f.cell for f in board.fighters
                            if f.alive and f.id not in (me.id, target.id))
                        before = distance_to_target
                        destination = cells.slide(
                            cell, target.cell, push_roll or pull_roll,
                            blocked, toward=bool(pull_roll))
                        after = cells.distance(cell, destination)
                        move_target = destination if destination != target.cell else None
                        moved = max(0, (after - before) if push_roll
                                    else (before - after))
                        if slide_cache is not None:
                            slide_cache[key_slide] = (move_target, moved)
                    if moved:
                        if move_target is not None and board.marks.traps_at(move_target, cells):
                            c_dest, c_dmg, c_ap_l, c_mp_l, _, _ = self._simulate_trap_chain(
                                board=board, fighter=target, target_cell=move_target,
                                entry_from_cell=target.cell, marks=board.marks,
                                character=character, blocked=blocked, max_depth=8)
                            damage += c_dmg
                            move_target = c_dest if c_dest != target.cell else None
                            utility += AP_DENIAL_WEIGHT * c_ap_l + MP_DENIAL_WEIGHT * c_mp_l
                        if push_roll:
                            # Forced movement is only fully denied when the
                            # target lacks the MP to walk back.
                            unrecovered = max(0, moved - max(0, target.mp))
                            utility += PUSH_WEIGHT * (moved + 0.5 * unrecovered)
                        elif melee:
                            utility += PUSH_WEIGHT * moved
                if damage <= 0 and healing <= 0 and utility <= 0:
                    continue
                if needs_taken and target.cell not in occupied:
                    continue
                out.append(_CastTemplate(
                    spell=spell, target_id=target.id, target_cell=target.cell,
                    damage=damage, healing=healing, steal_heal=steal_heal,
                    target_max_hp=max(1, target.max_hp),
                    damage_int=int(round(damage)),
                    ap_gain=target_ap_gain, mp_gain=target_mp_gain,
                    ap_loss=target_ap_loss, mp_loss=target_mp_loss,
                    utility=utility, move_target=move_target, moved=moved,
                ))
        return out

    def _damage_result(self, board: Board, spell: LiveSpell,
                       character: CharacterStats,
                       target: Fighter) -> Optional[SpellDamageResult]:
        """The damage price the search ranks on, corrected by live residuals.

        This is where a measured over- or under-price becomes a different plan:
        `expected_value` feeds `dealt`, the kill test and `damage_int`, so a
        spell the server pays 0.4x for stops being read as a finishing blow and
        stops winning the AP it was priced to deserve.

        `_estimate` itself stays pure and uncalibrated, and that is load-bearing.
        `observation.py` publishes its number as `derived`, which is the
        reference `live_learn.shadow_residuals` measures the server against.
        Correcting that too would make the auditor compare reality to an
        already-corrected price, and the scale would chase its own tail to 1.0.

        Only the two fields the search ranks on carry the correction; `lines`
        and the totals remain the analytic formula they were computed from.
        Heals are left alone: every bucket is built from damage effects.
        """
        result = self._estimate(board, spell, character, target, "damage")
        if result is None:
            return None
        calibration = self._calibration()
        if calibration is None:
            return result
        from combat.live_learn import apply_damage_calibration

        corrected = apply_damage_calibration(
            result.expected_value, spell.id,
            [effect.effect_id for effect in spell.damage_effects],
            calibration)
        if corrected == result.expected_value:
            return result
        return replace(
            result, expected_value=round(corrected, 2),
            ap_efficiency=round(corrected / max(1, result.ap_cost), 2))

    @staticmethod
    def _estimate(board: Board, spell: LiveSpell, character: CharacterStats,
                  target: Fighter, mode: str) -> Optional[SpellDamageResult]:
        info = spell.damage_info if mode == "damage" else spell.heal_info
        if info is None:
            return None
        return DofusDamageCalculator.calculate_spell_damage(
            info, character, target.target_stats())

    @staticmethod
    def _to_plan(node: _Node, notes: List[str]) -> TurnPlan:
        return TurnPlan(
            actions=node.actions,
            final_cell=node.cell,
            remaining_ap=node.ap,
            remaining_mp=node.mp,
            expected_damage=node.damage,
            expected_healing=node.healing,
            kills=node.kills,
            score=node.score,
            notes=notes,
            final_hp=dict(node.hp),
        )

    @staticmethod
    def _pass_plan(board: Board, notes: List[str]) -> TurnPlan:
        me = board.me
        return TurnPlan(
            actions=[PlannedAction(kind="pass", from_cell=me.cell if me else board.my_cell)],
            final_cell=me.cell if me else board.my_cell,
            remaining_ap=board.my_ap,
            remaining_mp=float(board.my_mp),
            expected_damage=0.0,
            expected_healing=0.0,
            kills=[],
            score=0.0,
            notes=notes,
            final_hp={f.id: f.hp for f in board.fighters if f.alive},
        )


def self_check() -> None:
    """Fail loudly if the ported tables and geometry stop agreeing."""
    if len(_POINTS) != NB_CELLS:
        raise RuntimeError("cell point map has %d entries, expected %d"
                           % (len(_POINTS), NB_CELLS))
    if cell_point(0) != (19, 0) or cell_point(14) != (19, 1):
        raise RuntimeError("cell point formula changed")
    open_map = CellMap(tuple([3] * NB_CELLS))  # bit 0 walkable | bit 1 sight
    if len(open_map.range_cells(300, 1, 3)) != 24:
        raise RuntimeError("diamond range ring is not 4*radius cells")
    if len(open_map.range_cells(300, 1, 3, cast_in_line=True)) != 12:
        raise RuntimeError("cross range ring is not 4 cells per radius")
    if len(open_map.range_cells(300, 1, 3, cast_in_diagonal=True)) != 12:
        raise RuntimeError("diagonal range ring is not 4 cells per radius")
    if len(open_map.reachable(300, 1.0)) != 5:
        raise RuntimeError("fight movement must be cardinal, one MP per step")
    if not ELEMENT_BY_EFFECT or 100 not in ELEMENT_BY_EFFECT:
        raise RuntimeError("damage effect table is incomplete")
