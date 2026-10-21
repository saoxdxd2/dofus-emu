import json
from pathlib import Path

spells_path = Path("c:/Users/sao/Documents/dofus-emu/dofus-network/data/cache/spells_full.json")
with open(spells_path, 'r', encoding='utf-8') as f:
    spells = json.load(f)

print(f"Total spells: {len(spells)}")

# Known Osamodas spells
known_osa_ids = {201, 202, 214, 224}

# Find spells with type_id 425 (shared by 3 of 4 known Osamodas spells)
type_425 = [s for s in spells if s.get('type_id') == 425]
print(f"\n=== Spells with type_id=425 ({len(type_425)}) ===")
for s in type_425:
    marker = " <<< KNOWN OSA" if s['id'] in known_osa_ids else ""
    print(f"  id={s['id']} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}' name_es='{s.get('name_es')}'{marker}")

# Find spells with name patterns that could be Osamodas
print(f"\n=== Spells with 'Invocation' in name_fr ===")
for s in spells:
    name_fr = s.get('name_fr', '')
    if 'nvocation' in name_fr.lower() or 'nvocation' in s.get('name_en', '').lower():
        marker = " <<< KNOWN OSA" if s['id'] in known_osa_ids else ""
        print(f"  id={s['id']} name_fr='{name_fr}' name_en='{s.get('name_en')}' name_es='{s.get('name_es')}' type_id={s.get('type_id')}{marker}")

print(f"\n=== Spells with 'Béco' or 'Beco' in name ===")
for s in spells:
    name_fr = s.get('name_fr', '')
    name_en = s.get('name_en', '')
    if 'béco' in name_fr.lower() or 'beco' in name_fr.lower() or 'kiss' in name_en.lower():
        marker = " <<< KNOWN OSA" if s['id'] in known_osa_ids else ""
        print(f"  id={s['id']} name_fr='{name_fr}' name_en='{name_en}' name_es='{s.get('name_es')}' type_id={s.get('type_id')}{marker}")

print(f"\n=== Spells with 'Morsure' or 'Bite' in name ===")
for s in spells:
    name_fr = s.get('name_fr', '')
    name_en = s.get('name_en', '')
    if 'morsure' in name_fr.lower() or 'bite' in name_en.lower():
        marker = " <<< KNOWN OSA" if s['id'] in known_osa_ids else ""
        print(f"  id={s['id']} name_fr='{name_fr}' name_en='{name_en}' name_es='{s.get('name_es')}' type_id={s.get('type_id')}{marker}")
