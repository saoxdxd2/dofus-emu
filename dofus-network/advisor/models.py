"""Typed snapshots of the read-only client reports the advisor consumes.

Everything here is parsed from `debug.character` and `market.capture` payloads.
The advisor never equips, spends spell points or buys; these models only make
the server's own numbers easier to reason about, and keep the raw payload
around so a report can point at what it was given.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

ELEMENTS: Tuple[str, ...] = ("earth", "fire", "water", "air")
PRIMARY_STAT: Dict[str, str] = {
    "earth": "strength",
    "fire": "intelligence",
    "water": "chance",
    "air": "agility",
}
STAT_ELEMENT: Dict[str, str] = {stat: elem for elem, stat in PRIMARY_STAT.items()}
ELEMENT_LABEL: Dict[str, str] = {
    "earth": "Earth / Strength",
    "fire": "Fire / Intelligence",
    "water": "Water / Chance",
    "air": "Air / Agility",
}


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _int(value: Any) -> Optional[int]:
    number = _num(value)
    return int(round(number)) if number is not None else None


def stat_total(value: Any) -> float:
    """Read one characteristic, whether the client sent a number or a stat row.

    `_agentReadStat` normally returns `{total, base, equipment, bonus, additional}`.
    Older snapshots and hand-written fixtures may just carry a number.
    """
    if isinstance(value, dict):
        for key in ("total", "_total", "value", "base", "_base"):
            number = _num(value.get(key))
            if number is not None:
                return number
        return 0.0
    return _num(value) or 0.0


def stat_detail(value: Any) -> Dict[str, float]:
    if not isinstance(value, dict):
        number = _num(value)
        return {"total": number} if number is not None else {}
    out: Dict[str, float] = {}
    for key, raw in value.items():
        number = _num(raw)
        if number is not None:
            out[str(key)] = number
    return out


def effect_number(row: Dict[str, Any]) -> float:
    """Average value of one item/set effect row.

    The client uses `diceNum`/`diceSide` as the min/max roll for variable
    effects (10 to 20 is num=10, side=20), not as a dice pool. Fixed effects
    carry `value`; sometimes both are present and `value` is the roll.
    """
    value = _num(row.get("value"))
    low = _num(row.get("dice_num"))
    high = _num(row.get("dice_side"))
    if low is not None and high is not None and high >= low:
        rolled = (low + high) / 2.0
        if value is None or value == 0:
            return rolled
        return value
    if value is not None:
        return value
    return low if low is not None else 0.0


def _unwrap(payload: Any) -> Dict[str, Any]:
    """Accept `{ok, data}` or a raw data object from a saved capture."""
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict) and ("character" in data or "stats" in data
                                  or "spells" in data or "prices" in data
                                  or "average_count" in data):
        return data
    return payload


@dataclass
class SpellGrade:
    grade: int
    min_player_level: Optional[int] = None
    ap_cost: Optional[int] = None
    min_range: Optional[int] = None
    max_range: Optional[int] = None
    max_cast_per_turn: Optional[int] = None
    max_cast_per_target: Optional[int] = None
    max_cast_per_fight: Optional[int] = None
    cooldown: Optional[int] = None
    critical_hit_probability: Optional[int] = None
    effects: List[Dict[str, Any]] = field(default_factory=list)
    critical_effects: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_bridge(cls, row: Dict[str, Any]) -> "SpellGrade":
        return cls(
            grade=_int(row.get("grade")) or 0,
            min_player_level=_int(row.get("min_player_level")),
            ap_cost=_int(row.get("ap_cost")),
            min_range=_int(row.get("min_range")),
            max_range=_int(row.get("max_range")),
            max_cast_per_turn=_int(row.get("max_cast_per_turn")),
            max_cast_per_target=_int(row.get("max_cast_per_target")),
            max_cast_per_fight=_int(row.get("max_cast_per_fight")),
            cooldown=_int(row.get("cooldown")),
            critical_hit_probability=_int(row.get("critical_hit_probability")),
            effects=list(row.get("effects") or []),
            critical_effects=list(row.get("critical_effects") or []),
        )


@dataclass
class SpellState:
    spell_id: int
    level: int
    max_grade: int
    max_grade_for_level: int
    name_id: Optional[int] = None
    breed_id: Optional[int] = None
    is_item: bool = False
    grades: List[SpellGrade] = field(default_factory=list)

    @property
    def unlocked(self) -> bool:
        return self.level > 0

    def grade(self, number: int) -> Optional[SpellGrade]:
        for row in self.grades:
            if row.grade == number:
                return row
        return None

    @classmethod
    def from_bridge(cls, row: Dict[str, Any]) -> "SpellState":
        level = _int(row.get("level")) or 0
        max_grade = _int(row.get("max_grade")) or 6
        cap = _int(row.get("max_grade_for_level"))
        if cap is None:
            cap = level
        return cls(
            spell_id=_int(row.get("spell_id")) or 0,
            level=level,
            max_grade=max_grade,
            max_grade_for_level=max(0, min(cap, max_grade)),
            name_id=_int(row.get("name_id")),
            breed_id=_int(row.get("breed_id")),
            is_item=bool(row.get("is_item")),
            grades=[SpellGrade.from_bridge(g) for g in (row.get("grades") or [])
                    if isinstance(g, dict)],
        )


@dataclass
class EquipmentItem:
    gid: int
    uid: Optional[int] = None
    position: Optional[int] = None
    position_name: Optional[str] = None
    quantity: int = 1
    name_id: Optional[int] = None
    level: Optional[int] = None
    type_id: Optional[int] = None
    super_type_id: Optional[int] = None
    set_ids: Tuple[int, ...] = ()
    effects: List[Dict[str, Any]] = field(default_factory=list)
    raw_template: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_bridge(cls, row: Dict[str, Any]) -> "EquipmentItem":
        template = row.get("template") if isinstance(row.get("template"), dict) else {}
        return cls(
            gid=_int(row.get("gid")) or 0,
            uid=_int(row.get("uid")),
            position=_int(row.get("position")),
            position_name=row.get("position_name"),
            quantity=_int(row.get("quantity")) or 1,
            name_id=_int(template.get("name_id")),
            level=_int(template.get("level")),
            type_id=_int(template.get("type_id")),
            super_type_id=_int(template.get("super_type_id")),
            set_ids=tuple(int(x) for x in (template.get("set_ids") or [])
                          if _int(x) is not None),
            effects=list(row.get("effects") or []),
            raw_template=template,
        )


@dataclass
class ActiveSet:
    set_id: int
    equipped_count: int
    piece_ids: Tuple[int, ...] = ()
    missing_ids: Tuple[int, ...] = ()
    owned_piece_count: Optional[int] = None
    owned_quantity: Optional[int] = None
    active_effects: List[Dict[str, Any]] = field(default_factory=list)
    bonus_is_secret: bool = False

    @classmethod
    def from_bridge(cls, row: Dict[str, Any]) -> "ActiveSet":
        return cls(
            set_id=_int(row.get("set_id")) or 0,
            equipped_count=_int(row.get("equipped_count")) or 0,
            piece_ids=tuple(int(x) for x in (row.get("piece_ids") or [])
                            if _int(x) is not None),
            missing_ids=tuple(int(x) for x in (row.get("missing_ids") or [])
                              if _int(x) is not None),
            owned_piece_count=_int(row.get("owned_piece_count")),
            owned_quantity=_int(row.get("owned_quantity")),
            active_effects=list(row.get("active_effects") or []),
            bonus_is_secret=bool(row.get("bonus_is_secret")),
        )


@dataclass
class MarketListing:
    count: int
    min: int
    median: int
    max: int
    age_ms: Optional[int] = None


@dataclass
class MarketSnapshot:
    captured_at: Optional[int] = None
    average_at: Optional[int] = None
    average_age_ms: Optional[int] = None
    prices: Dict[int, float] = field(default_factory=dict)
    listings: Dict[int, MarketListing] = field(default_factory=dict)
    average_count: int = 0
    listing_count: int = 0
    requested: bool = False
    request_error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    def average(self, gid: int) -> Optional[float]:
        price = self.prices.get(int(gid))
        return price if price and price > 0 else None

    def listing(self, gid: int) -> Optional[MarketListing]:
        return self.listings.get(int(gid))

    def best_listing(self, gid: int) -> Optional[int]:
        row = self.listing(gid)
        return row.min if row and row.min > 0 else None

    @classmethod
    def from_bridge(cls, payload: Any) -> "MarketSnapshot":
        data = _unwrap(payload)
        prices: Dict[int, float] = {}
        for pair in data.get("prices") or []:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                gid = _int(pair[0])
                price = _num(pair[1])
                if gid is not None and gid > 0 and price is not None and price > 0:
                    prices[gid] = price
        for key, raw in (data.get("averages") or {}).items():
            gid = _int(key)
            price = _num(raw)
            if gid is not None and price is not None and price > 0:
                prices[gid] = price
        listings: Dict[int, MarketListing] = {}
        for key, raw in (data.get("listings") or {}).items():
            gid = _int(key)
            if gid is None or not isinstance(raw, dict):
                continue
            count = _int(raw.get("count")) or 0
            minimum = _int(raw.get("min")) or 0
            if count <= 0 or minimum <= 0:
                continue
            listings[gid] = MarketListing(
                count=count,
                min=minimum,
                median=_int(raw.get("median")) or minimum,
                max=_int(raw.get("max")) or minimum,
                age_ms=_int(raw.get("age_ms")),
            )
        count = _int(data.get("average_count"))
        return cls(
            captured_at=_int(data.get("captured_at")),
            average_at=_int(data.get("average_at")),
            average_age_ms=_int(data.get("average_age_ms")),
            prices=prices,
            listings=listings,
            average_count=count if count is not None else len(prices),
            listing_count=_int(data.get("listing_count")) or len(listings),
            requested=bool(data.get("requested")),
            request_error=data.get("request_error"),
            raw=data,
        )


@dataclass
class CharacterSnapshot:
    captured_at: Optional[int] = None
    character_id: Optional[int] = None
    name: Optional[str] = None
    level: int = 0
    breed_id: int = 0
    sex: Optional[str] = None
    position: Dict[str, Any] = field(default_factory=dict)
    stats: Dict[str, float] = field(default_factory=dict)
    stat_details: Dict[str, Dict[str, float]] = field(default_factory=dict)
    points: Dict[str, Any] = field(default_factory=dict)
    spells: List[SpellState] = field(default_factory=list)
    equipment: List[EquipmentItem] = field(default_factory=list)
    sets: List[ActiveSet] = field(default_factory=list)
    inventory: Dict[str, Any] = field(default_factory=dict)
    db: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    # -- convenience reads -------------------------------------------------
    def stat(self, *names: str) -> Optional[float]:
        for name in names:
            if name in self.stats:
                return self.stats[name]
        return None

    @property
    def ap_budget(self) -> int:
        ap = self.stat("actionPoints", "action_points")
        if ap is None or ap < 1:
            return 6
        return max(1, int(round(ap)))

    @property
    def max_hp(self) -> float:
        for name in ("effectiveMaxLifePoints", "maxLifePoints", "lifePoints"):
            value = self.stat(name)
            if value:
                return value
        return 0.0

    @property
    def primary_totals(self) -> Dict[str, float]:
        return {element: self.stat(stat) or 0.0
                for element, stat in PRIMARY_STAT.items()}

    @property
    def dominant_element(self) -> Optional[str]:
        totals = self.primary_totals
        element = max(totals, key=lambda key: totals[key])
        return element if totals[element] > 0 else None

    @property
    def equipped_gids(self) -> Dict[int, int]:
        out: Dict[int, int] = {}
        for item in self.equipment:
            if item.gid > 0:
                out[item.gid] = out.get(item.gid, 0) + max(1, item.quantity)
        return out

    @property
    def owned_gids(self) -> Dict[int, int]:
        out = dict(self.equipped_gids)
        raw = self.inventory.get("gids") if isinstance(self.inventory, dict) else None
        if isinstance(raw, dict):
            for key, value in raw.items():
                gid = _int(key)
                qty = _int(value) or 1
                if gid is not None and gid > 0:
                    out[gid] = out.get(gid, 0) + qty
        return out

    @property
    def spell_by_id(self) -> Dict[int, SpellState]:
        return {spell.spell_id: spell for spell in self.spells}

    def to_character_stats(self):
        """Build the combat calculator's CharacterStats from this snapshot."""
        from combat.damage_calculator import CharacterStats

        return CharacterStats(
            name=self.name or "Player",
            breed_id=self.breed_id or 0,
            level=self.level or 1,
            action_points=self.ap_budget,
            movement_points=int(round(self.stat("movementPoints", "movement_points") or 4)),
            strength=int(round(self.stat("strength") or 0)),
            intelligence=int(round(self.stat("intelligence") or 0)),
            chance=int(round(self.stat("chance") or 0)),
            agility=int(round(self.stat("agility") or 0)),
            power=int(round(self.stat("damagesBonusPercent", "power") or 0)),
            damage=int(round(self.stat("damageBonus", "damage") or 0)),
            damage_neutral=int(round(self.stat("neutralDamageBonus") or 0)),
            damage_earth=int(round(self.stat("earthDamageBonus") or 0)),
            damage_fire=int(round(self.stat("fireDamageBonus") or 0)),
            damage_water=int(round(self.stat("waterDamageBonus") or 0)),
            damage_air=int(round(self.stat("airDamageBonus") or 0)),
            critical_hit=int(round(self.stat("criticalHit") or 0)),
            damage_critical=int(round(self.stat("criticalDamageBonus") or 0)),
            heals=int(round(self.stat("healBonus") or 0)),
        )

    @classmethod
    def from_bridge(cls, payload: Any) -> "CharacterSnapshot":
        data = _unwrap(payload)
        character = data.get("character") if isinstance(data.get("character"), dict) else {}
        stats: Dict[str, float] = {}
        details: Dict[str, Dict[str, float]] = {}
        for key, raw in (data.get("stats") or {}).items():
            stats[str(key)] = stat_total(raw)
            detail = stat_detail(raw)
            if detail:
                details[str(key)] = detail
        points = data.get("points") if isinstance(data.get("points"), dict) else {}
        equipment = [EquipmentItem.from_bridge(row) for row in data.get("equipment") or []
                     if isinstance(row, dict)]
        spells = [SpellState.from_bridge(row) for row in data.get("spells") or []
                  if isinstance(row, dict)]
        breed = _int(character.get("breed"))
        if breed is None:
            breed = next((s.breed_id for s in spells if s.breed_id), 0)
        return cls(
            captured_at=_int(data.get("at")),
            character_id=_int(character.get("id")),
            name=character.get("name"),
            level=_int(character.get("level")) or 0,
            breed_id=breed or 0,
            sex=character.get("sex"),
            position=data.get("position") or {},
            stats=stats,
            stat_details=details,
            points=points,
            spells=spells,
            equipment=equipment,
            sets=[ActiveSet.from_bridge(row) for row in data.get("sets") or []
                  if isinstance(row, dict)],
            inventory=data.get("inventory") if isinstance(data.get("inventory"), dict) else {},
            db=data.get("db") if isinstance(data.get("db"), dict) else {},
            raw=data,
        )


def merge_market(*snapshots: MarketSnapshot) -> MarketSnapshot:
    """Combine captures without letting an older price overwrite a newer one."""
    prices: Dict[int, float] = {}
    listings: Dict[int, MarketListing] = {}
    newest = MarketSnapshot()
    for snapshot in snapshots:
        if snapshot is None:
            continue
        prices.update(snapshot.prices)
        listings.update(snapshot.listings)
        if (snapshot.captured_at or 0) >= (newest.captured_at or 0):
            newest = snapshot
    merged = MarketSnapshot(
        captured_at=newest.captured_at,
        average_at=newest.average_at,
        average_age_ms=newest.average_age_ms,
        prices=prices,
        listings=listings,
        average_count=len(prices),
        listing_count=len(listings),
        requested=newest.requested,
        request_error=newest.request_error,
        raw=newest.raw,
    )
    return merged
