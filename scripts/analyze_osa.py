import json
from pathlib import Path

spells_path = Path("c:/Users/sao/Documents/dofus-emu/dofus-network/data/cache/spells_full.json")
with open(spells_path, 'r', encoding='utf-8') as f:
    spells = json.load(f)

known_osa_ids = {201, 202, 214, 224}

# All spells with type_id=425
type_425 = sorted([s for s in spells if s.get('type_id') == 425], key=lambda s: s['id'])
print(f"=== ALL spells with type_id=425 ({len(type_425)}) ===")
for s in type_425:
    marker = " <<< KNOWN OSA" if s['id'] in known_osa_ids else ""
    print(f"  id={s['id']} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}' name_es='{s.get('name_es')}'{marker}")

# Also check: are there other type_ids associated with known Osamodas spells?
print(f"\n=== Type IDs of known Osamodas spells ===")
for sid in sorted(known_osa_ids):
    s = next((x for x in spells if x['id'] == sid), None)
    if s:
        print(f"  id={sid} name_fr='{s.get('name_fr')}' type_id={s.get('type_id')}")

# Look at ID ranges - are Osamodas spells clustered?
print(f"\n=== All spells with IDs 200-300 ===")
for s in sorted(spells, key=lambda s: s['id']):
    if 200 <= s['id'] <= 300:
        marker = " <<< KNOWN OSA" if s['id'] in known_osa_ids else ""
        print(f"  id={s['id']} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}' type_id={s.get('type_id')}{marker}")
