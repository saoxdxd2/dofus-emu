"""
Complete osa_spells.json by adding spell IDs from dofus-touch.com.

Updated: Matched 13 spells from spells_full.json by French/English name matching
against the 22 dofus-touch Osamadas spell names. 12 spells are dofus-touch only
(not found in spells_full.json) and are handled separately in DOFUS_TOUCH_ONLY.

Usage:
    1. Run: python scripts/complete_osa_spells.py
    2. Manually add dofus-touch-only spells using scripts/add_dofus_touch_only.py
    3. Run: python scripts/validate_json.py
"""

import json
from pathlib import Path

DATA = Path("dofus-network/data/cache")
OUT = Path("scripts/output")

# ---------------------------------------------------------------------------
# STEP 1: Osamadas spell IDs from spells_full.json (matched by name to dofus-touch)
# ---------------------------------------------------------------------------
# 4 verified by exact match: 201, 202, 214, 224
# 9 matched by French/English name + type_id=425: 628, 629, 604, 779, 1706, 603, 2369, 1535, 524
OSA_SPELL_IDS = [
    201, 202, 214, 224,
    628, 629, 604, 779, 1706, 603, 2369, 1535, 524,
]

# Dofus-touch only spells (IDs not in spells_full.json)
# These need to be added manually to osa_spells.json
DOFUS_TOUCH_ONLY = [
    {"touch_id": 4714, "name_en": "Lashing Claw", "name_fr": "Griffe Cinglante", "summon": False},
    {"touch_id": 4718, "name_en": "Feline Movement", "name_fr": "Déplacement Féline", "summon": False},
    {"touch_id": 4720, "name_en": "Ghostly Claw", "name_fr": "Griffe Spectrale", "summon": False},
    {"touch_id": 4726, "name_en": "Bestial Heal", "name_fr": "Soin Bestial", "summon": False},
    {"touch_id": 4728, "name_en": "Whip", "name_fr": "Fouet", "summon": False},
    {"touch_id": 4730, "name_en": "Substitution", "name_fr": "Substitution", "summon": False},
    {"touch_id": 4734, "name_en": "Tumult", "name_fr": "Tumulte", "summon": False},
    {"touch_id": 4736, "name_en": "Favouritism", "name_fr": "Favoritisme", "summon": False},
    {"touch_id": 4748, "name_en": "Summoning of Red Wyrmling", "name_fr": "Invocation de Dragonnet Rouge", "summon": True, "summon_name": "Red Wyrmling"},
    {"touch_id": 4750, "name_en": "Determination", "name_fr": "Acharnement", "summon": False},
    {"touch_id": 4752, "name_en": "Symbiosa", "name_fr": "Symbiose", "summon": False},
    {"touch_id": 6341, "name_en": "Summoning of Osamodas Dopple", "name_fr": "Invocation de Dopeul Osamadas", "summon": True, "summon_name": "Osamodas Dopple"},
]


def main():
    # Load full spells dataset
    with open(DATA / "spells_full.json", encoding="utf-8") as f:
        all_spells = json.load(f)
    spells_by_id = {s["id"]: s for s in all_spells}

    # Load existing osa_spells.json (if any)
    osa_path = OUT / "osa_spells.json"
    if osa_path.exists():
        with open(osa_path, encoding="utf-8") as f:
            osa = json.load(f)
    else:
        osa = {
            "class": "Osamodas",
            "class_id": 2,
            "source_url": "https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/2-osamodas",
            "total_spells_on_source": 22,
            "spells": [],
        }

    existing_ids = {s["id"] for s in osa.get("spells", [])}

    added = []
    not_found = []
    for sid in OSA_SPELL_IDS:
        if sid in existing_ids:
            continue
        entry = spells_by_id.get(sid)
        if entry:
            osa["spells"].append({
                "id": entry["id"],
                "breed_id": 2,
                "breed": "Osamodas",
                "name_fr": entry["name_fr"],
                "name_en": entry["name_en"],
                "name_es": entry["name_es"],
                "type_id": entry["type_id"],
                "spell_levels": entry["spell_levels"],
                "source": "spells_full.json (added via completion script)",
                "summon": False,  # set manually if it's a summon spell
            })
            added.append(sid)
        else:
            not_found.append(sid)

    # Sort spells by id for cleanliness
    osa["spells"].sort(key=lambda s: s["id"])
    osa["spells_verified_locally"] = len(osa["spells"])
    osa["spells_pending_from_source"] = max(0, 22 - len(osa["spells"]))

    with open(osa_path, "w", encoding="utf-8") as f:
        json.dump(osa, f, ensure_ascii=False, indent=2)

    print(f"Added {len(added)} new spells: {added}")
    print(f"Total spells in osa_spells.json: {len(osa['spells'])}")
    if not_found:
        print(f"NOT FOUND in spells_full.json ({len(not_found)}): {not_found}")
    print(f"Written to {osa_path}")


if __name__ == "__main__":
    main()
