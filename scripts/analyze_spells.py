import json
from collections import Counter

with open('dofus-network/data/cache/spells_full.json', encoding='utf-8') as f:
    spells = json.load(f)

print('Total spells:', len(spells))
print('Spell keys:', list(spells[0].keys()))

c = Counter(s['type_id'] for s in spells)
print('Unique type_ids:', len(c))
print('Most common type_ids:', c.most_common(20))

print('\n--- Spells with type_id 425 ---')
for s in spells:
    if s['type_id'] == 425:
        print(s['id'], '|', s['name_en'], '|', s['spell_levels'][:3])
