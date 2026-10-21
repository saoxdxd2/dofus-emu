import json

osa_path = 'scripts/output/osa_spells.json'
with open(osa_path, encoding='utf-8') as f:
    d = json.load(f)

# Fix summon flags for spells_full.json entries that are summon spells
summon_updates = {
    629: {"summon": True, "summon_name": "Crobak"},
    604: {"summon": True, "summon_name": "Cave Gobball"},
    1706: {"summon": True, "summon_name": "Boar"},
}

for s in d["spells"]:
    sid = s["id"]
    if sid in summon_updates:
        s.update(summon_updates[sid])
        print(f"Updated id={sid}: summon={s['summon']} summon_name={s['summon_name']}")

with open(osa_path, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

print(f"\nTotal spells: {len(d['spells'])}")
summon_count = sum(1 for s in d['spells'] if s.get('summon'))
print(f"Summon spells: {summon_count}")
