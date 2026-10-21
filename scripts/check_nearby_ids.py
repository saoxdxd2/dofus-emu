import json

with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    spells = json.load(f)

spells_by_id = {s["id"]: s for s in spells}

# Check spells in ID range 200-230 (near verified Osamadas spells)
print("Spells with IDs 200-230:")
for sid in range(200, 231):
    if sid in spells_by_id:
        s = spells_by_id[sid]
        print(f"  id={sid} type_id={s.get('type_id')} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}'")

print("\n" + "=" * 70)
print("Spells with IDs 201-225 specifically (Osamadas range):")
for sid in range(201, 226):
    if sid in spells_by_id:
        s = spells_by_id[sid]
        print(f"  id={sid} type_id={s.get('type_id')} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}'")
