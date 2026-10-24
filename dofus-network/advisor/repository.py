"""Read-only data access for the advisor.

The advisor joins three sources that already exist in this project:

* the official class-spell database (15 classes x 22 spells x 6 grades),
* the normalized Dofus Touch encyclopedia dump (items, recipes, sets) and the
  inferred effect-id map under `advisor/data/`,
* live snapshots produced by the client diagnostics.

Nothing here guesses a drop table or a price. A missing effect id is unknown,
not zero; a missing recipe is missing, not empty.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
ADVISOR_DATA = HERE / "data"

try:  # normal use: `python -m advisor` from dofus-network/
    from data.official_spell_repository import (
        load_official_database,
        spell_level_to_info,
    )
except ModuleNotFoundError:  # running a report from another working directory
    sys.path.insert(0, str(PROJECT))
    from data.official_spell_repository import (  # type: ignore
        load_official_database,
        spell_level_to_info,
    )

from .models import (
    ELEMENTS,
    EquipmentItem,
    MarketSnapshot,
    effect_number,
)

DEFAULT_EFFECT_MAP = ADVISOR_DATA / "effect_map.json"
DEFAULT_ITEMS = ADVISOR_DATA / "touch_items.json"
DEFAULT_SETS = ADVISOR_DATA / "touch_sets.json"

SLOT_NAMES: Dict[int, str] = {
    0: "amulet", 1: "weapon", 2: "ring_left", 3: "belt", 4: "ring_right",
    5: "boots", 6: "hat", 7: "cape", 8: "pet",
    9: "dofus_1", 10: "dofus_2", 11: "dofus_3", 12: "dofus_4",
    13: "dofus_5", 14: "dofus_6", 15: "shield", 16: "mount",
}

# Trophies use the Dofus slots (Dofus Touch Fandom: "They use Dofus slots").
# Backpacks share the cape slot. The scraped category labels contain U+FFFD
# where accents were lost, so match on an ASCII-normalized prefix.
SLOT_ORDER = (0, 2, 4, 3, 5, 6, 7, 15, 9, 10, 11, 12, 13, 14)


def pretty_label(text: Optional[str]) -> str:
    """Repair the common accent losses in the scraped French labels."""
    if not text:
        return ""
    out = text.replace(" \ufffd ", " à ")
    out = re.sub(r"(?<=[A-Za-z])\ufffd(?=[A-Za-z]|$)", "é", out)
    return out.replace("\ufffd", "e")


def _label_key(text: str) -> str:
    repaired = pretty_label(text)
    normalized = unicodedata.normalize("NFKD", repaired)
    return "".join(ch for ch in normalized if ch.isalpha()).lower()


@dataclass(frozen=True)
class StatRange:
    minimum: float
    maximum: float

    @property
    def average(self) -> float:
        return (self.minimum + self.maximum) / 2.0

    @classmethod
    def from_raw(cls, raw: Any) -> Optional["StatRange"]:
        if isinstance(raw, dict):
            low = raw.get("min", raw.get("minimum"))
            high = raw.get("max", raw.get("maximum", low))
            try:
                low_f = float(low)
                high_f = float(high)
            except (TypeError, ValueError):
                return None
            if high_f < low_f:
                low_f, high_f = high_f, low_f
            return cls(low_f, high_f)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return cls(float(raw), float(raw))
        return None


@dataclass
class ItemTemplate:
    id: int
    name: str
    type: str
    level: int
    type_id: Optional[int]
    set_ids: Tuple[int, ...]
    stats: Dict[str, StatRange]
    condition: List[str] = field(default_factory=list)
    ingredients: List[Tuple[int, int]] = field(default_factory=list)
    special: List[str] = field(default_factory=list)
    unknown: List[str] = field(default_factory=list)

    def stat_average(self, key: str) -> float:
        row = self.stats.get(key)
        return row.average if row else 0.0

    @classmethod
    def from_raw(cls, raw: Dict[str, Any]) -> "ItemTemplate":
        stats: Dict[str, StatRange] = {}
        for key, value in (raw.get("stats") or {}).items():
            row = StatRange.from_raw(value)
            if row is not None:
                stats[str(key)] = row
        ingredients: List[Tuple[int, int]] = []
        for row in raw.get("ingredients") or []:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                try:
                    ingredients.append((int(row[0]), int(row[1])))
                except (TypeError, ValueError):
                    continue
            elif isinstance(row, dict) and row.get("id") is not None:
                try:
                    ingredients.append((int(row["id"]), int(row.get("quantity", 1))))
                except (TypeError, ValueError):
                    continue
        set_ids: Tuple[int, ...] = ()
        if raw.get("set_ids"):
            set_ids = tuple(int(x) for x in raw["set_ids"] if str(x).isdigit())
        elif raw.get("set_id"):
            set_ids = (int(raw["set_id"]),)
        return cls(
            id=int(raw.get("id") or 0),
            name=pretty_label(raw.get("name")),
            type=pretty_label(raw.get("type")),
            level=int(raw.get("level") or 0),
            type_id=int(raw["type_id"]) if raw.get("type_id") is not None else None,
            set_ids=set_ids,
            stats=stats,
            condition=[str(x) for x in raw.get("condition") or []],
            ingredients=ingredients,
            special=[str(x) for x in raw.get("special") or []],
            unknown=[str(x) for x in raw.get("unknown") or []],
        )


@dataclass
class SetTemplate:
    id: int
    name: str
    level: int
    piece_ids: Tuple[int, ...]
    bonus_by_count: Dict[int, Dict[str, StatRange]]

    def bonus(self, count: int) -> Dict[str, StatRange]:
        if count <= 0:
            return {}
        return self.bonus_by_count.get(int(count), {})

    @classmethod
    def from_raw(cls, raw: Dict[str, Any]) -> "SetTemplate":
        bonuses: Dict[int, Dict[str, StatRange]] = {}
        for count, stats in (raw.get("bonus_by_count") or {}).items():
            try:
                number = int(count)
            except (TypeError, ValueError):
                continue
            parsed: Dict[str, StatRange] = {}
            for key, value in (stats or {}).items():
                row = StatRange.from_raw(value)
                if row is not None:
                    parsed[str(key)] = row
            bonuses[number] = parsed
        return cls(
            id=int(raw.get("id") or 0),
            name=pretty_label(raw.get("name")),
            level=int(raw.get("level") or 0),
            piece_ids=tuple(int(x) for x in raw.get("piece_ids") or []),
            bonus_by_count=bonuses,
        )


def _int_field(value: Any, default: int = 0) -> int:
    """Parse a scraped numeric field.

    The encyclopedia writes unbounded cooldowns as the string "inf."; treat
    that as a very large cooldown so a spell is still limited to one cast,
    never as zero (which would read as "no cooldown").
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value if value is not None else "").strip().lower()
    if text.startswith("inf"):
        return 1_000_000
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default


@dataclass
class OfficialGrade:
    grade: int
    required_level: int
    ap_cost: int
    min_range: int
    max_range: int
    max_cast_per_turn: int
    max_cast_per_target: int
    max_cast_per_fight: int
    cooldown: int
    crit_percent: float
    cast_in_line: bool
    cast_in_diagonal: bool
    cast_test_los: bool
    effects: List[Dict[str, Any]]
    critical_effects: List[Dict[str, Any]]
    description: str = ""

    @classmethod
    def from_raw(cls, raw: Dict[str, Any]) -> "OfficialGrade":
        characteristics = raw.get("characteristics") or {}
        return cls(
            grade=_int_field(raw.get("level")),
            required_level=_int_field(raw.get("required_level")),
            ap_cost=_int_field(raw.get("ap_cost")),
            min_range=_int_field(raw.get("min_range")),
            max_range=_int_field(raw.get("max_range")),
            max_cast_per_turn=_int_field(
                characteristics.get("max_cast_per_turn"), 1_000_000),
            max_cast_per_target=_int_field(
                characteristics.get("max_cast_per_target"), 1_000_000),
            max_cast_per_fight=_int_field(
                characteristics.get("max_cast_per_fight"), 1_000_000),
            cooldown=_int_field(characteristics.get("cooldown")),
            crit_percent=float(characteristics.get("critical_hit_percent") or 0.0),
            cast_in_line=bool(characteristics.get("cast_in_line")),
            cast_in_diagonal=bool(characteristics.get("cast_in_diagonal")),
            cast_test_los=bool(characteristics.get("cast_test_los", True)),
            effects=list(raw.get("normal_effects") or []),
            critical_effects=list(raw.get("critical_effects") or []),
            description=str(raw.get("description") or ""),
        )


@dataclass
class OfficialSpell:
    id: int
    name: str
    breed_id: int
    grades: Dict[int, OfficialGrade]
    raw: Dict[str, Any] = field(repr=False, default_factory=dict)

    def grade(self, number: int) -> Optional[OfficialGrade]:
        return self.grades.get(int(number))

    def info(self, grade: int):
        return spell_level_to_info(self.raw, int(grade))

    @classmethod
    def from_raw(cls, raw: Dict[str, Any]) -> "OfficialSpell":
        grades = {
            int(row.get("level") or 0): OfficialGrade.from_raw(row)
            for row in raw.get("levels") or []
            if isinstance(row, dict)
        }
        return cls(
            id=int(raw.get("id") or 0),
            name=pretty_label(raw.get("name_en") or raw.get("name_fr")),
            breed_id=int(raw.get("breed_id") or 0),
            grades=grades,
            raw=raw,
        )


class Repository:
    def __init__(
        self,
        effect_map_path: Optional[Path] = None,
        items_path: Optional[Path] = None,
        sets_path: Optional[Path] = None,
        spells_path: Optional[Path] = None,
    ) -> None:
        self.effect_map_path = Path(effect_map_path or DEFAULT_EFFECT_MAP)
        self.items_path = Path(items_path or DEFAULT_ITEMS)
        self.sets_path = Path(sets_path or DEFAULT_SETS)
        self.spells_path = Path(spells_path) if spells_path else None

        effect_doc = self._read_json(self.effect_map_path)
        self.effect_to_key: Dict[int, str] = {
            int(key): str(value)
            for key, value in (effect_doc.get("effect_to_key") or {}).items()
        }
        self.effect_confidence: Dict[int, float] = {}
        for key, value in (effect_doc.get("confidence") or {}).items():
            # The generated map stores {key, votes, share} per effect id,
            # not a bare number.
            share = value.get("share") if isinstance(value, dict) else value
            try:
                self.effect_confidence[int(key)] = float(share)
            except (TypeError, ValueError):
                continue
        self.non_stat_effect_ids = {
            int(x) for x in effect_doc.get("non_stat_effect_ids") or []
        }

        item_doc = self._read_json(self.items_path)
        raw_items = item_doc.get("items") or {}
        self.items: Dict[int, ItemTemplate] = {}
        if isinstance(raw_items, dict):
            iterable = raw_items.values()
        else:
            iterable = raw_items
        for raw in iterable:
            if not isinstance(raw, dict):
                continue
            item = ItemTemplate.from_raw(raw)
            if item.id > 0:
                self.items[item.id] = item

        set_doc = self._read_json(self.sets_path)
        raw_sets = set_doc.get("sets") or []
        self.sets: Dict[int, SetTemplate] = {}
        for raw in raw_sets:
            if not isinstance(raw, dict):
                continue
            entry = SetTemplate.from_raw(raw)
            if entry.id > 0:
                self.sets[entry.id] = entry

        spell_doc = load_official_database(self.spells_path)
        self.spells: Dict[int, OfficialSpell] = {}
        self.by_breed: Dict[int, List[OfficialSpell]] = {}
        self.breed_names: Dict[int, str] = {}
        for breed in spell_doc.get("classes") or []:
            breed_id = int(breed.get("breed_id") or 0)
            if breed.get("name"):
                self.breed_names[breed_id] = str(breed["name"])
            for raw in breed.get("spells") or []:
                spell = OfficialSpell.from_raw(raw)
                if spell.id <= 0:
                    continue
                self.spells[spell.id] = spell
                self.by_breed.setdefault(breed_id, []).append(spell)
        for rows in self.by_breed.values():
            rows.sort(key=lambda spell: spell.id)

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    # -- items / sets ------------------------------------------------------
    def item(self, gid: int) -> Optional[ItemTemplate]:
        return self.items.get(int(gid))

    def set(self, set_id: int) -> Optional[SetTemplate]:
        return self.sets.get(int(set_id))

    def item_name(self, gid: int, fallback: Optional[str] = None) -> str:
        item = self.item(gid)
        return item.name if item else (fallback or "#%d" % gid)

    def set_name(self, set_id: int, fallback: Optional[str] = None) -> str:
        entry = self.set(set_id)
        return entry.name if entry else (fallback or "#%d" % set_id)

    def item_positions(self, item: ItemTemplate) -> Tuple[int, ...]:
        label = _label_key(item.type)
        if label.startswith("amulet"):
            return (0,)
        if label.startswith("anneau") or label.startswith("ring"):
            return (2, 4)
        if label.startswith("ceinture") or label.startswith("belt"):
            return (3,)
        if label.startswith("botte") or label.startswith("boot"):
            return (5,)
        if label.startswith("chapeau") or label.startswith("hat"):
            return (6,)
        if label.startswith("cape") or label.startswith("sac"):
            return (7,)
        if label.startswith("bouclier") or label.startswith("shield"):
            return (15,)
        if label.startswith("troph"):
            # Trophies use Dofus slots in Dofus Touch.
            return (9, 10, 11, 12, 13, 14)
        if label.startswith("dofus"):
            return (9, 10, 11, 12, 13, 14)
        type_positions = {
            1: (0,), 9: (2, 4), 10: (3,), 11: (5,), 16: (6,), 17: (7,),
            23: (9, 10, 11, 12, 13, 14), 82: (15,), 151: (9, 10, 11, 12, 13, 14),
            248: (15,),
        }
        return type_positions.get(item.type_id or -1, ())

    def item_stats(self, item: ItemTemplate) -> Dict[str, float]:
        return {key: row.average for key, row in item.stats.items()}

    def effect_stats(self, rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                effect_id = int(row.get("effect_id", row.get("effectId", -1)))
            except (TypeError, ValueError):
                continue
            key = self.effect_to_key.get(effect_id)
            if not key:
                continue
            out[key] = out.get(key, 0.0) + effect_number(row)
        return out

    def equipment_stats(self, item: EquipmentItem) -> Dict[str, float]:
        template = self.item(item.gid)
        if template is not None and template.stats:
            return self.item_stats(template)
        return self.effect_stats(item.effects)

    # -- spells ------------------------------------------------------------
    def class_spells(self, breed_id: int) -> List[OfficialSpell]:
        return list(self.by_breed.get(int(breed_id), []))

    def spell(self, spell_id: int) -> Optional[OfficialSpell]:
        return self.spells.get(int(spell_id))

    def spell_for(self, spell_id: int, breed_id: Optional[int] = None) -> Optional[OfficialSpell]:
        if breed_id is not None:
            for spell in self.by_breed.get(int(breed_id), []):
                if spell.id == int(spell_id):
                    return spell
        return self.spells.get(int(spell_id))

    def grade_cap(self, spell: OfficialSpell, live, level: int) -> int:
        """Highest grade the character may actually reach.

        The live server wins when it is present: `max_grade_for_level` is the
        client's own `setLevelByPlayerLevel` result. With no live state, the
        encyclopedia's per-grade required_level is the fallback.
        """
        official_max = max(spell.grades) if spell.grades else 0
        if live is not None:
            cap = int(live.max_grade_for_level or 0)
            if live.level <= 0:
                return 0
            return max(0, min(cap, official_max))
        cap = 0
        for grade, row in spell.grades.items():
            if row.required_level <= int(level):
                cap = max(cap, grade)
        return cap

    def grade_costs(self, current: int, target: int) -> int:
        total = lambda n: n * (n - 1) // 2 if n > 0 else 0
        return max(0, total(int(target)) - total(int(current)))


def effect_element(effect: Dict[str, Any]) -> Optional[str]:
    damage = effect.get("damage") if isinstance(effect, dict) else None
    if isinstance(damage, dict) and damage.get("element"):
        element = str(damage["element"]).lower()
        return element if element in ELEMENTS else None
    text = str(effect.get("text") or "") if isinstance(effect, dict) else ""
    match = re.search(r"\((Earth|Fire|Water|Air|Neutral) (?:damage|steal)\)", text, re.I)
    if not match:
        return None
    element = match.group(1).lower()
    if element == "neutral":
        return "earth"  # neutral damage scales with Strength in the calculator
    return element if element in ELEMENTS else None


def effect_roll(effect: Dict[str, Any]) -> Optional[Tuple[str, int, int]]:
    """Element and min/max roll of a damage effect, including text-only lines."""
    if not isinstance(effect, dict):
        return None
    damage = effect.get("damage")
    if isinstance(damage, dict):
        element = str(damage.get("element") or "").lower()
        if element == "neutral":
            element = "earth"  # neutral damage scales with Strength
        if element in ELEMENTS:
            return element, int(damage.get("min_roll") or 0), int(damage.get("max_roll") or 0)
        return None
    text = str(effect.get("text") or "")
    match = re.search(r"(\d+)\s+to\s+(\d+)\s+\((Earth|Fire|Water|Air|Neutral) (?:damage|steal)\)",
                      text, re.I)
    if not match:
        return None
    element = match.group(3).lower()
    if element == "neutral":
        element = "earth"
    return element, int(match.group(1)), int(match.group(2))
