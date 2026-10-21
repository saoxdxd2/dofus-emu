import json
from pathlib import Path

OUT = Path("scripts/output")

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

osa_path = OUT / "osa_spells.json"
with open(osa_path, encoding="utf-8") as f:
    osa = json.load(f)

existing_touch_ids = set()
for s in osa.get("spells", []):
    # Check if any existing spell has a dofus_touch_id field
    if "dofus_touch_id" in s:
        existing_touch_ids.add(s["dofus_touch_id"])

added = []
for spell in DOFUS_TOUCH_ONLY:
    tid = spell["touch_id"]
    if tid in existing_touch_ids:
        continue
    
    entry = {
        "id": tid,  # Use dofus-touch ID as the spell id
        "breed_id": 2,
        "breed": "Osamodas",
        "name_fr": spell["name_fr"],
        "name_en": spell["name_en"],
        "name_es": spell["name_fr"],  # No ES translation available from dofus-touch
        "type_id": None,  # Not available from dofus-touch
        "spell_levels": [],  # Not available from dofus-touch
        "source": "dofus-touch.com (not found in spells_full.json)",
        "summon": spell["summon"],
    }
    if spell.get("summon_name"):
        entry["summon_name"] = spell["summon_name"]
    
    osa["spells"].append(entry)
    added.append(tid)
    existing_touch_ids.add(tid)

osa["spells"].sort(key=lambda s: s["id"] if s["id"] is not None else 999999)
osa["spells_verified_locally"] = len(osa["spells"])
osa["spells_pending_from_source"] = max(0, 22 - len(osa["spells"]))

with open(osa_path, "w", encoding="utf-8") as f:
    json.dump(osa, f, ensure_ascii=False, indent=2)

print(f"Added {len(added)} dofus-touch-only spells: {added}")
print(f"Total spells in osa_spells.json: {len(osa['spells'])}")
print(f"Written to {osa_path}")
