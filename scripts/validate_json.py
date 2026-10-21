import json
for p in ['scripts/output/osa_spells.json', 'scripts/output/all_spells.json']:
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
    print(p, '-> valid JSON')
    if p.endswith('osa_spells.json'):
        print(f"  class={d['class']}, class_id={d['class_id']}, total_on_source={d['total_spells_on_source']}, spells_count={len(d['spells'])}")
        for s in d['spells']:
            print(f"  id={s['id']} | {s['name_en']} / {s['name_fr']} / {s['name_es']} | type_id={s['type_id']} | summon={s.get('summon')} | levels={s['spell_levels'][:3]}...")
    else:
        print(f"  entries={len(d)}")
