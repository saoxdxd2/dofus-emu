from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from data.game_data_extractor import DamageEffect, SpellInfo

DATABASE_PATH = Path(__file__).resolve().parent / "cache" / "official_class_spells.json"
UNLIMITED_CASTS = 1_000_000


def load_official_database(path: Optional[Path] = None) -> Dict[str, Any]:
    with (path or DATABASE_PATH).open(encoding="utf-8") as handle:
        return json.load(handle)


def iter_official_spells(database: Dict[str, Any], breed_id: Optional[int] = None) -> Iterable[Dict[str, Any]]:
    for breed in database["classes"]:
        if breed_id is None or breed["breed_id"] == breed_id:
            yield from breed["spells"]


def _roll(effect: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if effect.get("damage"):
        return effect["damage"]
    if effect.get("heal"):
        return {"element": "HEAL", **effect["heal"]}
    text = effect.get("text", "")
    heal = re.search(r"(\d+)\s+to\s+(\d+)\s+HP\b", text, re.IGNORECASE)
    if heal and re.search(r"heal|restore", text, re.IGNORECASE):
        return {"element": "HEAL", "min_roll": int(heal[1]), "max_roll": int(heal[2])}
    return None


def spell_level_to_info(spell: Dict[str, Any], level: int = 6) -> SpellInfo:
    try:
        details = next(item for item in spell["levels"] if item["level"] == level)
    except StopIteration as exc:
        raise ValueError(f"Spell {spell['id']} has no level {level}") from exc

    normal = details["normal_effects"]
    critical = details["critical_effects"]
    effects = []
    for index, effect in enumerate(normal):
        roll = _roll(effect)
        if roll is None:
            continue
        critical_roll = None
        if index < len(critical):
            candidate = _roll(critical[index])
            if candidate and candidate["element"] == roll["element"]:
                critical_roll = candidate
        effects.append(DamageEffect(
            element=roll["element"],
            min_roll=roll["min_roll"],
            max_roll=roll["max_roll"],
            crit_min_roll=critical_roll["min_roll"] if critical_roll else 0,
            crit_max_roll=critical_roll["max_roll"] if critical_roll else 0,
        ))

    characteristics = details["characteristics"]
    return SpellInfo(
        id=spell["id"],
        name_fr="",
        name_en=spell["name_en"],
        name_es="",
        breed_id=spell["breed_id"],
        level=level,
        ap_cost=details["ap_cost"],
        min_range=details["min_range"],
        max_range=details["max_range"],
        cast_in_line=characteristics.get("cast_in_line", False),
        cast_in_diagonal=characteristics.get("cast_in_diagonal", False),
        cast_test_los=characteristics.get("cast_test_los", True),
        max_cast_per_turn=characteristics.get("max_cast_per_turn") or UNLIMITED_CASTS,
        max_cast_per_target=characteristics.get("max_cast_per_target") or UNLIMITED_CASTS,
        cooldown=characteristics.get("cooldown") or 0,
        crit_rate_percent=characteristics.get("critical_hit_percent", 0.0),
        effects=effects,
    )


def load_spell_infos(breed_id: Optional[int] = None, level: int = 6,
                     path: Optional[Path] = None) -> List[SpellInfo]:
    database = load_official_database(path)
    return [spell_level_to_info(spell, level) for spell in iter_official_spells(database, breed_id)]


def validate_official_database(database: Dict[str, Any]) -> Dict[str, int]:
    breeds = database["classes"]
    if len(breeds) != 15 or len({breed["breed_id"] for breed in breeds}) != 15:
        raise ValueError("Expected exactly 15 distinct classes")
    spell_count = 0
    level_count = 0
    damage_line_count = 0
    healing_line_count = 0
    for breed in breeds:
        spells = breed["spells"]
        if len(spells) != 22 or len({spell["id"] for spell in spells}) != 22:
            raise ValueError(f"Expected 22 distinct spells for {breed['name']}")
        for spell in spells:
            spell_count += 1
            levels = spell["levels"]
            if [item["level"] for item in levels] != [1, 2, 3, 4, 5, 6]:
                raise ValueError(f"Spell {spell['id']} does not have levels 1-6 in order")
            for details in levels:
                level_count += 1
                if details["ap_cost"] < 0 or details["min_range"] < 0 or details["max_range"] < details["min_range"]:
                    raise ValueError(f"Invalid AP/range for spell {spell['id']} level {details['level']}")
                for effect in details["normal_effects"] + details["critical_effects"]:
                    if not effect["text"]:
                        raise ValueError(f"Empty effect for spell {spell['id']}")
                    roll = effect.get("damage") or effect.get("heal")
                    if roll:
                        damage_line_count += bool(effect.get("damage"))
                        healing_line_count += bool(effect.get("heal"))
                        if roll["min_roll"] > roll["max_roll"]:
                            raise ValueError(f"Reversed effect roll for spell {spell['id']}")
    return {
        "classes": len(breeds),
        "spells": spell_count,
        "spell_levels": level_count,
        "damage_lines": damage_line_count,
        "healing_lines": healing_line_count,
    }
