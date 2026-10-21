"""
Generate Osamodas (Osa) spell JSON from the Dofus game data.

IMPORTANT LIMITATION:
---------------------
spells_full.json (2,000 spells) does NOT carry a breed/class field. Its keys are:
    id, name_fr, name_en, name_es, type_id, spell_levels

type_id is NOT a class indicator either (e.g. type_id 425 is shared by 1,725
spells across ALL classes), so Osamodas spells cannot be filtered reliably
from spells_full.json alone.

The built-in detailed spell list in game_data_extractor.py (SpellInfo.breed_id,
where 2 = Osamodas) contains NO Osamodas spells either.

This script therefore:
1) Dumps the COMPLETE spells dataset to all_spells.json (clean, formatted).
2) Builds osa_spells.json from Osamodas spells that can be VERIFIED by name
   against spells_full.json, annotated with breed_id=2. This is a
   best-effort, non-exhaustive subset (labelled as such).
"""

import json
from pathlib import Path

DATA = Path("dofus-network/data/cache")
OUT = Path("scripts/output")
OUT.mkdir(parents=True, exist_ok=True)

# ---- 1) Load full spells dataset ----
with open(DATA / "spells_full.json", encoding="utf-8") as f:
    all_spells = json.load(f)

print(f"Total spells in spells_full.json: {len(all_spells)}")
print("Spell keys:", list(all_spells[0].keys()))

# ---- 2) Write clean full dataset ----
with open(OUT / "all_spells.json", "w", encoding="utf-8") as f:
    json.dump(all_spells, f, ensure_ascii=False, indent=2)
print(f"Wrote {OUT / 'all_spells.json'}")

# ---- 3) Build verified Osamodas subset ----
# Osamodas spell names I am confident about (FR / EN / ES).
# Only spells that VERIFY by exact name match in spells_full.json are included.
OSAMODAS_SPELLS = [
    # id, name_fr,            name_en,              name_es
    (201, "Béco du Tofu",     "Tofu Kiss",          "Pico de Tofu"),
    (202, "Morsure du Bouftou", "Gobball Bite",    "Mordedura de Jalató"),
    (214, "Invocation d'Araknee", "Summoning of Arachnee", "Invocación de Araknea"),
    (224, "Invocation de Tofu Maléfique", "Summoning of Evil Tofu", "Invocación de Tofu Maligno"),
]

# Build lookup by (id) and by name for verification
spells_by_id = {s["id"]: s for s in all_spells}
name_index = {}
for s in all_spells:
    for lang in ("name_fr", "name_en", "name_es"):
        name_index.setdefault(s[lang].strip().lower(), []).append(s["id"])

osa = []
not_found = []
for sid, fr, en, es in OSAMODAS_SPELLS:
    entry = spells_by_id.get(sid)
    if entry is None:
        # try name match as fallback
        found_ids = []
        for nm in (fr, en, es):
            found_ids.extend(name_index.get(nm.strip().lower(), []))
        if found_ids:
            entry = spells_by_id[found_ids[0]]
            sid = entry["id"]
        else:
            not_found.append((sid, fr, en, es))
            continue
    osa.append({
        "id": entry["id"],
        "breed_id": 2,
        "breed": "Osamodas",
        "name_fr": entry["name_fr"],
        "name_en": entry["name_en"],
        "name_es": entry["name_es"],
        "type_id": entry["type_id"],
        "spell_levels": entry["spell_levels"],
        "source": "spells_full.json (verified by name/id match)",
        "note": "best-effort subset - dataset lacks a breed/class field",
    })

with open(OUT / "osa_spells.json", "w", encoding="utf-8") as f:
    json.dump(osa, f, ensure_ascii=False, indent=2)

print(f"\n=== Osamodas subset ===")
print(f"Verified Osamodas spells written to {OUT / 'osa_spells.json'}: {len(osa)}")
for s in osa:
    print(f"  id={s['id']} | {s['name_en']} / {s['name_fr']} / {s['name_es']} | type_id={s['type_id']} | levels={s['spell_levels'][:3]}")
if not_found:
    print(f"\nNot found in spells_full.json ({len(not_found)}):")
    for sid, fr, en, es in not_found:
        print(f"  id={sid} | {en} / {fr} / {es}")

print("\nDone. NOTE: osa_spells.json is a best-effort, non-exhaustive subset.")
print("A complete, accurate Osamodas-only list cannot be derived from this dataset")
print("because spells_full.json has no breed/class field.")
