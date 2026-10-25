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

Deliberately conservative: only direct single-target damage, steal, and heals
are scored. Buffs, traps, glyphs, push/pull, AP/MP removal, and area zones are
not planned yet; a spell that only has those effects is reported in ``notes``
instead of being treated as a no-op damage spell. The executor must still
validate every chosen action against the client and re-plan on disagreement.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from combat.damage_calculator import (
    CharacterStats,
    DofusDamageCalculator,
    SpellDamageResult,
    TargetStats,
)
from data.game_data_extractor import DamageEffect, SpellInfo

NB_CELLS = 560

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
_EPS = 1e-9


def cell_point(cell_id: int) -> Tuple[int, int]:
    """Cell id to map point, exactly as the client's getMapPointFromCellId."""
    t = cell_id % 14 - cell_id // 28
    return t + 19, t + cell_id // 14


_POINTS: Dict[Tuple[int, int], int] = {cell_point(c): c for c in range(NB_CELLS)}


def _js_round(value: float) -> float:
    """Math.round semantics (half toward +infinity), not Python's banker's rounding."""
    return math.floor(value + 0.5)


def line_points(x1: int, y1: int, x2: int, y2: int) -> List[Tuple[int, int]]:
    """Port of the client's getLine: origin-exclusive, target-inclusive cells."""
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


@dataclass(frozen=True)
class CellMap:
    """Cell flags for one map, in the client's own bit encoding."""

    flags: Tuple[int, ...]

    @classmethod
    def from_board(cls, payload: Dict[str, Any]) -> "CellMap":
        raw = payload.get("cells") or []
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
        x, y = cell_point(cell)
        out: List[int] = []
        for dx, dy in ((0, 1), (-1, 0), (0, -1), (1, 0)):
            out.append(_POINTS.get((x + dx, y + dy), -1))
        if diagonal:
            for dx, dy in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
                out.append(_POINTS.get((x + dx, y + dy), -1))
        return [c for c in out if self.valid(c)]

    def has_los(self, a: int, b: int, occupied: FrozenSet[int] = frozenset()) -> bool:
        """The client's line check: actors and non-seeing cells block the line."""
        if a == b:
            return True
        ax, ay = cell_point(a)
        bx, by = cell_point(b)
        line = line_points(ax, ay, bx, by)
        last = len(line) - 1
        for index, point in enumerate(line):
            cell = _POINTS.get(point)
            if cell is None or not self.valid(cell):
                return False
            if index < last and cell in occupied:
                return False
            if not self.sees(cell):
                return False
        return True

    def reachable(self, start: int, mp: float,
                  occupied: FrozenSet[int] = frozenset()) -> Dict[int, float]:
        """Cells reachable with ``mp`` points; occupied cells are not enterable.

        Cardinal steps only. The client's fight reachable-zone walker uses
        ``getNeighbourCells(cell, false)`` and charges exactly one MP per cell.
        """
        if not self.walkable(start):
            return {}
        best: Dict[int, float] = {start: 0.0}
        queue: List[Tuple[float, int]] = [(0.0, start)]
        while queue:
            cost, cell = heapq.heappop(queue)
            if cost > best.get(cell, math.inf) + _EPS:
                continue
            x, y = cell_point(cell)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = _POINTS.get((x + dx, y + dy))
                if nxt is None or not self.walkable(nxt) or nxt in occupied:
                    continue
                new_cost = cost + 1.0
                if new_cost > mp + _EPS:
                    continue
                if new_cost + _EPS < best.get(nxt, math.inf):
                    best[nxt] = new_cost
                    heapq.heappush(queue, (new_cost, nxt))
        return best

    def range_cells(self, origin: int, min_range: int, max_range: int,
                    cast_in_line: bool = False,
                    cast_in_diagonal: bool = False) -> List[int]:
        """Port of getSpellRange: a ring, not a filled area."""
        if max_range < 0 or min_range > max_range:
            return []
        x, y = cell_point(origin)
        points: List[Tuple[int, int]] = []
        if min_range == 0:
            points.append((x, y))

        def diamond(radius: int) -> Iterable[Tuple[int, int]]:
            for r in range(radius):
                s = radius - r
                yield x + r, y - s
                yield x + s, y + r
                yield x - r, y + s
                yield x - s, y - r

        def cross(radius: int) -> Iterable[Tuple[int, int]]:
            yield x - radius, y
            yield x + radius, y
            yield x, y - radius
            yield x, y + radius

        def diagonal(radius: int) -> Iterable[Tuple[int, int]]:
            yield x - radius, y - radius
            yield x - radius, y + radius
            yield x + radius, y - radius
            yield x + radius, y + radius

        for radius in range(max(1, min_range), max_range + 1):
            if cast_in_line and cast_in_diagonal:
                points.extend(cross(radius))
                points.extend(diagonal(radius))
            elif cast_in_line:
                points.extend(cross(radius))
            elif cast_in_diagonal:
                points.extend(diagonal(radius))
            else:
                points.extend(diamond(radius))

        out: List[int] = []
        seen = set()
        for point in points:
            cell = _POINTS.get(point)
            if cell is None or not self.valid(cell) or cell in seen:
                continue
            seen.add(cell)
            out.append(cell)
        return out


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

    @property
    def kind(self) -> str:
        if self.effect_id in HEAL_EFFECT_IDS:
            return "heal"
        entry = ELEMENT_BY_EFFECT.get(self.effect_id)
        return entry[0] if entry else "other"

    @property
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

    @property
    def damage_effects(self) -> List[LiveEffect]:
        return [e for e in self.effects if e.kind in ("damage", "steal")]

    @property
    def heal_effects(self) -> List[LiveEffect]:
        return [e for e in self.effects if e.kind == "heal"]

    @property
    def has_damage(self) -> bool:
        return bool(self.damage_effects)

    @property
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

    def _percent(self, element: str) -> int:
        return dict(self.resist_percent).get(element.lower(), 0)

    def _fixed(self, element: str) -> int:
        return dict(self.resist_fixed).get(element.lower(), 0)

    def target_stats(self) -> TargetStats:
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

    @classmethod
    def from_fight_state(cls, payload: Dict[str, Any]) -> "Board":
        fighters = [f for f in (cls._fighter(raw) for raw in payload.get("fighters") or [])
                    if f is not None]
        my_id = _int(payload.get("my_id"))
        me = next((f for f in fighters if f.id == my_id), None) if my_id is not None else None
        if me is None:
            me = next((f for f in fighters if f.by_me), None)
        my_cell = me.cell if me is not None else _int(payload.get("my_cell"), 0)
        return cls(
            map_id=_int(payload.get("map_id")),
            in_fight=bool(payload.get("in_fight")),
            in_preparation=bool(payload.get("in_preparation")),
            is_my_turn=bool(payload.get("is_my_turn")),
            my_id=my_id if my_id is not None else (me.id if me else -1),
            my_cell=my_cell,
            my_ap=me.ap if me is not None else _int(payload.get("my_ap"), 0),
            my_mp=me.mp if me is not None else _int(payload.get("my_mp"), 0),
            fighters=fighters,
            spells=[cls._spell(raw) for raw in payload.get("spells") or []],
            my_stats=dict(payload.get("my_stats") or {}),
            cells=CellMap.from_board(payload),
            round=_int(payload.get("round")),
            fight_id=_int(payload.get("fight_id")),
            current_fighter_id=_int(payload.get("current_fighter_id")),
            last_actions=tuple(action for action in payload.get("last_actions") or []
                               if isinstance(action, dict)),
        )

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
            castable=bool(raw.get("castable", True)),
            critical_hit_probability=_float(raw.get("critical_hit_probability")),
            effects=_effects(raw.get("effects")),
            critical_effects=_effects(raw.get("critical_effects")),
            is_weapon=bool(raw.get("is_weapon")),
            zone_size=_int(raw.get("zone_size")) if raw.get("zone_size") is not None else None,
            zone_shape=raw.get("zone_shape") or None,
        )

    @property
    def me(self) -> Optional[Fighter]:
        return next((f for f in self.fighters if f.id == self.my_id), None)

    @property
    def enemies(self) -> List[Fighter]:
        return [f for f in self.fighters if f.alive and f.id != self.my_id
                and f.team != "player"]

    @property
    def allies(self) -> List[Fighter]:
        return [f for f in self.fighters if f.alive and f.team == "player"]

    def character_stats(self) -> CharacterStats:
        stats = self.my_stats or {}
        me = self.me

        def value(key: str) -> int:
            return _int(stats.get(key), 0)

        return CharacterStats(
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


@dataclass(frozen=True)
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
        }


@dataclass(frozen=True)
class _CastTemplate:
    spell: LiveSpell
    target_id: int
    target_cell: int
    damage: float
    healing: float
    steal_heal: float
    target_max_hp: int


@dataclass
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


KILL_BONUS = 0.35
HEAL_WEIGHT = 0.8


class BattleSolver:
    """One-turn beam search over legal moves and casts."""

    def __init__(self, beam_width: int = 16, max_actions: int = 4,
                 max_nodes: int = 20000) -> None:
        self.beam_width = max(1, beam_width)
        self.max_actions = max(0, max_actions)
        self.max_nodes = max(1, max_nodes)
        self.last_candidates: List[TurnPlan] = []

    def plan_candidates(self, board: Board, k: int = 5) -> List[TurnPlan]:
        """The top-k distinct turn plans from the last search, pass included.

        ``plan_turn`` still returns the single best plan; this exposes the
        alternatives next to it so a learned ranker can choose between them.
        The pass plan is always kept as an option, even when the search found
        more action plans than ``k``.
        """
        self.plan_turn(board)
        plans = self.last_candidates
        pass_plan = next(
            (plan for plan in plans
             if plan.actions and plan.actions[0].kind == "pass"),
            self._pass_plan(board, []))
        action_plans = [plan for plan in plans
                        if plan.actions and plan.actions[0].kind != "pass"]
        limit = max(1, int(k))
        return (action_plans[:max(0, limit - 1)] + [pass_plan])[:limit]

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
                          if not s.has_damage and not s.has_heal})
        if skipped:
            notes.append("not modelled yet (buff/utility/area only): " + ", ".join(skipped))
        blocked = sorted({s.name or str(s.id) for s in board.spells if not s.castable})
        if blocked:
            notes.append("client marks as not castable: " + ", ".join(blocked))

        occupied = frozenset(f.cell for f in board.fighters if f.alive and f.id != me.id)
        template_cache: Dict[int, List[_CastTemplate]] = {}
        reach_cache: Dict[Tuple[int, int], Dict[int, float]] = {}
        character = board.character_stats()

        def templates(cell: int) -> List[_CastTemplate]:
            if cell in template_cache:
                return template_cache[cell]
            out = self._templates(board, cell, character, occupied)
            template_cache[cell] = out
            return out

        def reach(cell: int, mp: float) -> Dict[int, float]:
            key = (cell, round(mp, 3))
            if key not in reach_cache:
                reach_cache[key] = board.cells.reachable(cell, mp, occupied)
            return reach_cache[key]

        hp = {f.id: f.hp for f in board.fighters if f.alive}
        start = _Node(cell=me.cell, ap=board.my_ap, mp=float(board.my_mp), hp=hp,
                      spell_uses={}, target_uses={}, healed_by_target={},
                      actions=[], damage=0.0, healing=0.0, kills=[], score=0.0)
        best = start
        frontier = [start]
        seen: Dict[Tuple[Any, ...], float] = {}
        expanded = 0

        for _depth in range(self.max_actions):
            candidates: List[_Node] = []
            for node in frontier:
                if expanded >= self.max_nodes:
                    notes.append("search stopped at the node budget")
                    break
                expanded += 1
                for cell, mp_cost in reach(node.cell, node.mp).items():
                    for template in templates(cell):
                        if template.target_id not in node.hp or node.hp[template.target_id] <= 0:
                            continue
                        spell = template.spell
                        if spell.ap_cost > node.ap or not spell.castable:
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
                        key_uses = (spell.id, template.target_id)
                        if spell.max_cast_per_target is not None and (
                                node.target_uses.get(key_uses, 0)
                                >= spell.max_cast_per_target):
                            continue
                        target_hp = node.hp[template.target_id]
                        dealt = min(template.damage, target_hp)
                        kill = template.damage >= target_hp
                        direct_gain = min(
                            template.healing,
                            max(0, template.target_max_hp - target_hp
                                - node.healed_by_target.get(template.target_id, 0)),
                        )
                        steal_gain = 0.0
                        if template.steal_heal > 0:
                            steal_gain = min(
                                template.steal_heal,
                                max(0, me.max_hp - me.hp
                                    - node.healed_by_target.get(me.id, 0)),
                            )
                        gained = direct_gain + steal_gain
                        step_score = dealt + (KILL_BONUS * template.target_max_hp if kill else 0.0)
                        step_score += HEAL_WEIGHT * gained
                        if step_score <= 0:
                            continue
                        new_hp = dict(node.hp)
                        new_hp[template.target_id] = max(0, target_hp - int(round(template.damage)))
                        healed_by_target = dict(node.healed_by_target)
                        if direct_gain > 0:
                            healed_by_target[template.target_id] = (
                                healed_by_target.get(template.target_id, 0) + direct_gain)
                        if steal_gain > 0:
                            healed_by_target[me.id] = (
                                healed_by_target.get(me.id, 0) + steal_gain)
                        uses = dict(node.spell_uses)
                        uses[spell.id] = uses.get(spell.id, 0) + 1
                        target_uses = dict(node.target_uses)
                        target_uses[key_uses] = target_uses.get(key_uses, 0) + 1
                        kills = node.kills + ([template.target_id] if kill else [])
                        action = PlannedAction(
                            kind="cast", spell_id=spell.id, spell_name=spell.name,
                            from_cell=cell, move_to=cell if cell != node.cell else None,
                            target_id=template.target_id, target_cell=template.target_cell,
                            mp_cost=mp_cost, ap_cost=spell.ap_cost,
                            damage=template.damage, healing=gained, kills=kill,
                        )
                        candidate = _Node(
                            cell=cell, ap=node.ap - spell.ap_cost,
                            mp=max(0.0, node.mp - mp_cost), hp=new_hp,
                            spell_uses=uses, target_uses=target_uses,
                            healed_by_target=healed_by_target,
                            actions=node.actions + [action],
                            damage=node.damage + dealt,
                            healing=node.healing + gained,
                            kills=kills, score=node.score + step_score,
                        )
                        signature = (
                            candidate.cell, candidate.ap, round(candidate.mp, 3),
                            tuple(sorted(candidate.hp.items())),
                            tuple(sorted(candidate.spell_uses.items())),
                            tuple(sorted(candidate.target_uses.items())),
                            tuple(sorted(candidate.healed_by_target.items())),
                        )
                        if signature in seen and seen[signature] >= candidate.score - _EPS:
                            continue
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
            plans.append(self._to_plan(node, notes))
        plans.append(self._pass_plan(board, notes))
        self.last_candidates = plans
        return plan

    def _templates(self, board: Board, cell: int, character: CharacterStats,
                   occupied: FrozenSet[int]) -> List[_CastTemplate]:
        me = board.me
        assert me is not None
        out: List[_CastTemplate] = []
        enemy_ids = {f.id for f in board.enemies}
        if not board.cells.walkable(cell):
            return out
        for spell in board.spells:
            if spell.ap_cost <= 0 or not spell.castable:
                continue
            if not spell.has_damage and not spell.has_heal:
                continue
            if spell.need_free_cell:
                continue
            in_range = set(board.cells.range_cells(
                cell, spell.min_range, spell.max_range,
                spell.cast_in_line, spell.cast_in_diagonal))
            if not in_range:
                continue
            targets = [f for f in board.fighters if f.alive]
            for target in targets:
                if target.cell not in in_range:
                    continue
                if spell.requires_los and not board.cells.has_los(
                        cell, target.cell, occupied):
                    continue
                is_enemy = target.id in enemy_ids
                damage = 0.0
                healing = 0.0
                steal_heal = 0.0
                if is_enemy and spell.has_damage:
                    result = self._estimate(board, spell, character, target, "damage")
                    damage = result.expected_value if result else 0.0
                    if any(e.kind == "steal" for e in spell.damage_effects):
                        steal_heal = damage
                elif not is_enemy and spell.has_heal:
                    result = self._estimate(board, spell, character, target, "heal")
                    healing = result.expected_value if result else 0.0
                elif not is_enemy and target.id == me.id and spell.has_damage:
                    continue
                if damage <= 0 and healing <= 0:
                    continue
                if spell.need_taken_cell and target.cell not in occupied:
                    continue
                out.append(_CastTemplate(
                    spell=spell, target_id=target.id, target_cell=target.cell,
                    damage=damage, healing=healing, steal_heal=steal_heal,
                    target_max_hp=max(1, target.max_hp),
                ))
        out.sort(key=lambda t: -max(t.damage, t.healing))
        return out

    @staticmethod
    def _estimate(board: Board, spell: LiveSpell, character: CharacterStats,
                  target: Fighter, mode: str) -> Optional[SpellDamageResult]:
        wanted = ("damage", "steal") if mode == "damage" else ("heal",)
        normal = [e for e in spell.effects if e.kind in wanted]
        if not normal:
            return None
        critical: Dict[str, List[LiveEffect]] = {}
        for effect in spell.critical_effects:
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
        info = SpellInfo(
            id=spell.id,
            name_fr=spell.name,
            name_en=spell.name,
            name_es="",
            breed_id=0,
            level=1,
            ap_cost=max(1, spell.ap_cost),
            min_range=spell.min_range,
            max_range=spell.max_range,
            cast_in_line=spell.cast_in_line,
            cast_in_diagonal=spell.cast_in_diagonal,
            cast_test_los=spell.requires_los,
            effects=damage_effects,
            crit_rate_percent=spell.critical_hit_probability or 0.0,
        )
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
