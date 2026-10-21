import json

with open('scripts/output/osa_spells.json', encoding='utf-8') as f:
    d = json.load(f)

for s in d['spells']:
    if s.get('summon'):
        print(f"id={s['id']} name_en={s['name_en']} summon={s.get('summon')} summon_name={s.get('summon_name')}")

print(f"\nTotal spells: {len(d['spells'])}")
summon_spells = [s for s in d['spells'] if s.get('summon')]
print(f"Summon spells: {len(summon_spells)}")
for s in summon_spells:
    print(f"  id={s['id']} {s['name_en']} -> {s.get('summon_name', 'NO SUMMON NAME')}")
