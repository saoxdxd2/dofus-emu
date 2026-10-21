import json
from pathlib import Path

spells_path = Path("c:/Users/sao/Documents/dofus-emu/dofus-network/data/cache/spells_full.json")
with open(spells_path, 'r', encoding='utf-8') as f:
    spells = json.load(f)

# Sort by ID and show ranges
spells_sorted = sorted(spells, key=lambda s: s['id'])
print(f"ID range: {spells_sorted[0]['id']} - {spells_sorted[-1]['id']}")
print(f"Total spells: {len(spells_sorted)}")

# Show spells in ID range 200-260 (where known Osamadas spells are)
print(f"\n=== Spells with IDs 200-260 ===")
for s in spells_sorted:
    if 200 <= s['id'] <= 260:
        print(f"  id={s['id']} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}' type_id={s.get('type_id')}")
