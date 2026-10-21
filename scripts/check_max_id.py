import json

with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    spells = json.load(f)

max_id = max(s['id'] for s in spells)
min_id = min(s['id'] for s in spells)
print(f"Total spells: {len(spells)}")
print(f"ID range: {min_id} - {max_id}")

# Check which IDs are missing in the range
all_ids = set(s['id'] for s in spells)
missing = [i for i in range(min_id, max_id + 1) if i not in all_ids]
print(f"Missing IDs in range: {len(missing)}")
if missing:
    print(f"First 50 missing: {missing[:50]}")

# Check if dofus-touch IDs exist
dofus_touch_ids = [420, 4714, 4716, 4718, 4720, 4722, 4724, 4726, 4728, 4730, 4732, 4734, 4736, 4738, 4740, 4742, 4744, 4746, 4748, 4750, 4752, 6341]
print(f"\nChecking dofus-touch IDs in spells_full.json:")
for tid in dofus_touch_ids:
    found = [s for s in spells if s['id'] == tid]
    if found:
        s = found[0]
        print(f"  id={tid} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}'")
    else:
        print(f"  id={tid} NOT FOUND")
