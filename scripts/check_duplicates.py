import json
from pathlib import Path

spells_path = Path("c:/Users/sao/Documents/dofus-emu/dofus-network/data/cache/spells_full.json")
with open(spells_path, 'r', encoding='utf-8') as f:
    spells = json.load(f)

# Find spells with duplicate names
from collections import defaultdict
name_groups = defaultdict(list)
for s in spells:
    name_groups[s.get('name_fr', '')].append(s)

print("=== Duplicate spell names (same name_fr, different IDs) ===")
dupes = {name: entries for name, entries in name_groups.items() if len(entries) > 1}
for name, entries in sorted(dupes.items()):
    print(f"\n'{name}':")
    for e in entries:
        print(f"  id={e['id']} type_id={e.get('type_id')} name_en='{e.get('name_en')}' name_es='{e.get('name_es')}'")
