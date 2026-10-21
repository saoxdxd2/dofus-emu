import json
import re
from pathlib import Path

# Load spells_full.json for name matching
spells_path = Path("c:/Users/sao/Documents/dofus-emu/dofus-network/data/cache/spells_full.json")
with open(spells_path, 'r', encoding='utf-8') as f:
    all_spells = json.load(f)

spell_names_fr = {s['name_fr'].lower().strip() for s in all_spells}
spell_names_en = {s['name_en'].lower().strip() for s in all_spells if s.get('name_en')}
spell_names_es = {s['name_es'].lower().strip() for s in all_spells if s.get('name_es')}

# Map name -> spell id
name_to_id = {}
for s in all_spells:
    for lang_name in [s.get('name_fr', ''), s.get('name_en', ''), s.get('name_es', '')]:
        if lang_name:
            name_to_id[lang_name.lower().strip()] = s['id']

# Search corpus for spell names used by Osamodas
corpus_path = Path("c:/Users/sao/Documents/dofus-emu/dofus-network/data/dofus_deep_training_corpus.json")
with open(corpus_path, 'r', encoding='utf-8') as f:
    corpus = json.load(f)

print(f"Corpus entries: {len(corpus)}")

# Find entries mentioning Osamadas
osa_entries = [e for e in corpus if 'osamodas' in json.dumps(e, ensure_ascii=False).lower()]
print(f"Entries mentioning Osamadas: {len(osa_entries)}")

# Extract spell names mentioned near "Osamadas"
mentioned_spells = set()
for entry in osa_entries:
    text = json.dumps(entry, ensure_ascii=False)
    # Look for spell-like patterns
    for spell_name in spell_names_fr:
        if spell_name and len(spell_name) > 3 and spell_name in text.lower():
            mentioned_spells.add(spell_name)

print(f"\nPotentially mentioned spell names (FR): {len(mentioned_spells)}")
for name in sorted(mentioned_spells):
    sid = name_to_id.get(name, '?')
    s = next((x for x in all_spells if x['name_fr'].lower().strip() == name), None)
    if s:
        print(f"  id={s['id']} name_fr='{s['name_fr']}' name_en='{s.get('name_en')}'")

# Also check episodic_skills for Osamadas
epi_path = Path("c:/Users/sao/Documents/dofus-emu/dofus-network/data/cache/episodic_skills.json")
if epi_path.exists():
    with open(epi_path, 'r', encoding='utf-8') as f:
        epi = json.load(f)
    print(f"\n=== Episodic skills ===")
    for key, val in epi.items():
        if isinstance(val, dict) and val.get('breed_id') == 2:
            print(f"  {key}: breed_id=2, {val.get('target_pattern', '')}, {val.get('stance', '')}")
        elif isinstance(val, dict) and 'osamodas' in json.dumps(val).lower():
            print(f"  {key}: mentions Osamadas")
