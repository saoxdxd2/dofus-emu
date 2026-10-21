import json

with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    spells = json.load(f)

# Check max ID in spells_full.json
max_id = max(s['id'] for s in spells)
min_id = min(s['id'] for s in spells)
print(f"spells_full.json: {len(spells)} spells, ID range: {min_id} - {max_id}")

# Check if dofus-touch IDs exist in spells_full.json
dofus_touch_ids = [420, 4714, 4716, 4718, 4720, 4722, 4724, 4726, 4728, 4730, 4732, 4734, 4736, 4738, 4740, 4742, 4744]
print(f"\nChecking dofus-touch IDs in spells_full.json:")
for tid in dofus_touch_ids:
    found = [s for s in spells if s['id'] == tid]
    if found:
        s = found[0]
        print(f"  id={tid} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}' type_id={s.get('type_id')}")
    else:
        print(f"  id={tid} NOT FOUND")

# Check ID range distribution
print(f"\nSpells with ID > 1000: {sum(1 for s in spells if s['id'] > 1000)}")
print(f"Spells with ID > 4000: {sum(1 for s in spells if s['id'] > 4000)}")

# List all spells with ID > 4000
high_ids = [s for s in spells if s['id'] > 4000]
print(f"\nSpells with ID > 4000 ({len(high_ids)}):")
for s in sorted(high_ids, key=lambda x: x['id'])[:30]:
    print(f"  id={s['id']} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}' type_id={s.get('type_id')}")
