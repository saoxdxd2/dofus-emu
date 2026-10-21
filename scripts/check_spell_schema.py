import json

with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    spells = json.load(f)

# Check all keys in the first few spells
for i, s in enumerate(spells[:3]):
    print(f'Spell {i}: keys={list(s.keys())}')
    print(f'  {s}')
    print()

# Check if any spell has breed or class info
has_breed = any('breed' in s or 'class' in s for s in spells)
print(f'Any spell has breed/class field: {has_breed}')

# Count total spells
print(f'Total spells: {len(spells)}')

# Get all keys across all spells
all_keys = set()
for s in spells:
    all_keys.update(s.keys())
print(f'All keys: {all_keys}')

# Show verified Osamadas spells
osa_like = [s for s in spells if s['id'] in [201, 202, 214, 224]]
for s in osa_like:
    print(f'  id={s["id"]} type_id={s["type_id"]} name_fr={s["name_fr"]} name_en={s["name_en"]}')
