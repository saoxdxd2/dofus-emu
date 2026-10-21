import json

with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    spells = json.load(f)

# Count spells by type_id
type_counts = {}
for s in spells:
    tid = s.get('type_id')
    type_counts[tid] = type_counts.get(tid, 0) + 1

# Show top type_ids
sorted_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
print("Top 20 type_ids by count:")
for tid, count in sorted_types[:20]:
    print(f"  type_id={tid}: {count} spells")

print(f"\nTotal spells: {len(spells)}")
print(f"Total unique type_ids: {len(type_counts)}")

# List all spells with type_id=425
type425_spells = [s for s in spells if s.get('type_id') == 425]
print(f"\nSpells with type_id=425: {len(type425_spells)}")
for s in sorted(type425_spells, key=lambda x: x['id']):
    print(f"  id={s['id']} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}'")
