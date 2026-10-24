from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable

from data.official_spell_repository import DATABASE_PATH, validate_official_database

CLASS_SOURCES = {
    "Feca": (1, "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/1-feca"),
    "Osamodas": (2, "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/2-osamodas"),
    "Enutrof": (3, "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/3-enutrof"),
    "Sram": (4, "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/4-sram"),
    "Xelor": (5, "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/5-xelor"),
    "Ecaflip": (6, "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/6-ecaflip"),
    "Eniripsa": (7, "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/7-eniripsa"),
    "Iop": (8, "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/8-iop"),
    "Cra": (9, "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/9-cra"),
    "Sadida": (10, "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/10-sadida"),
    "Sacrier": (11, "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/11-sacrier"),
    "Pandawa": (12, "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/12-pandawa"),
    "Rogue": (13, "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/13-rogue"),
    "Masqueraider": (14, "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/14-masqueraider"),
    "Foggernaut": (15, "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/15-foggernaut"),
}


def _number(value: str):
    if value.lower() == "unlimited":
        return None
    match = re.search(r"-?\d+", value)
    return int(match[0]) if match else value


def _characteristic(label: str, value: str):
    boolean = True if value == "Yes" else False if value == "No" else None
    fields = {
        "Probability of Critical Hit": ("critical_hit_percent", _number(value)),
        "Uses per turn": ("max_cast_per_turn", _number(value)),
        "Uses per turn per player": ("max_cast_per_target", _number(value)),
        "Uses per target": ("max_cast_per_target", _number(value)),
        "Number of casts per turn per target": ("max_cast_per_target", _number(value)),
        "Cooldown": ("cooldown", _number(value)),
        "Recast interval": ("cooldown", _number(value)),
        "Global recast interval": ("global_cooldown", _number(value)),
        "Area of Effect": ("area_of_effect", value),
        "Modifiable range": ("modifiable_range", boolean),
        "Line of sight": ("cast_test_los", boolean),
        "Cast in a straight line only": ("cast_in_line", boolean),
        "Cast diagonally only": ("cast_in_diagonal", boolean),
        "Target necessary": ("target_required", boolean),
        "Free cell required": ("need_free_cell", boolean),
    }
    return fields.get(label)


def _normalise_effect(effect: Dict[str, Any]) -> Dict[str, Any]:
    text = effect["text"]
    damage = re.search(r"(\d+)(?:\s+to\s+(\d+))?\s+\((Neutral|Earth|Fire|Water|Air) damage\)", text, re.I)
    if damage:
        effect["damage"] = {
            "element": damage[3].upper(),
            "min_roll": int(damage[1]),
            "max_roll": int(damage[2] or damage[1]),
        }
    heal = re.search(r"(\d+)(?:\s+to\s+(\d+))?\s+\(HP restored\)", text, re.I)
    if heal:
        effect["heal"] = {"min_roll": int(heal[1]), "max_roll": int(heal[2] or heal[1])}
    return effect


def _normalise_spell(spell: Dict[str, Any]) -> Dict[str, Any]:
    for details in spell["levels"]:
        characteristics = {}
        for label, value in details["raw_characteristics"].items():
            mapped = _characteristic(label, value)
            if mapped:
                characteristics[mapped[0]] = mapped[1]
        details["characteristics"] = characteristics
        details["normal_effects"] = [_normalise_effect(effect) for effect in details["normal_effects"]]
        details["critical_effects"] = [_normalise_effect(effect) for effect in details["critical_effects"]]
    return spell


def _spells(payload: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(payload, list):
        yield from payload
    else:
        for spells in payload.values():
            yield from spells


def build(paths: Iterable[Path]) -> Dict[str, Any]:
    grouped = {name: [] for name in CLASS_SOURCES}
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for spell in _spells(json.load(handle)):
                grouped[spell["breed"]].append(_normalise_spell(spell))
    classes = []
    for name, spells in grouped.items():
        breed_id, source_url = CLASS_SOURCES[name]
        unique = {spell["id"]: spell for spell in spells}
        classes.append({
            "breed_id": breed_id,
            "name": name,
            "source_url": source_url,
            "spells": [unique[key] for key in sorted(unique)],
        })
    database = {
        "source": "Dofus Touch official encyclopedia",
        "damage_formula_source": "https://www.dofus-touch.com/en/mmorpg/tutorials/1664038-damage-resistances",
        "locale": "en",
        "retrieved_on": date.today().isoformat(),
        "classes": classes,
    }
    validate_official_database(database)
    return database


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=DATABASE_PATH)
    args = parser.parse_args()
    database = build(args.inputs)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(database, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(validate_official_database(database)))


if __name__ == "__main__":
    main()
