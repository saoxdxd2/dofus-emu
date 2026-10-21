import json
from pathlib import Path

# Load spells for name matching
spells_path = Path("c:/Users/sao/Documents/dofus-emu/dofus-network/data/cache/spells_full.json")
with open(spells_path, 'r', encoding='utf-8') as f:
    all_spells = json.load(f)

name_to_spell = {}
for s in all_spells:
    for lang_name in [s.get('name_fr', ''), s.get('name_en', ''), s.get('name_es', '')]:
        if lang_name:
            name_to_spell.setdefault(lang_name.lower().strip(), s)

# Check combat policy dataset for Osamadas spell usage
combat_path = Path("c:/Users/sao/Documents/dofus-emu/dofus-network/data/dofus_combat_policy_dataset.json")
with open(combat_path, 'r', encoding='utf-8') as f:
    combat = json.load(f)

print(f"Combat entries: {len(combat)}")

# Find Osamadas entries
osa_entries = [e for e in combat if e.get('character', {}).get('breed_id') == 2 or e.get('character', {}).get('breed') == 'Osamadas']
print(f"Osamadas entries: {len(osa_entries)}")

# Extract spell/action names from Osamadas entries
mentioned_actions = set()
for entry in osa_entries[:50]:  # Limit to first 50 for inspection
    text = json.dumps(entry, ensure_ascii=False)
    # Look for spell names
    for name, spell in name_to_spell.items():
        if name and len(name) > 3 and name in text.lower():
            mentioned_actions.add((spell['id'], spell['name_fr'], spell.get('name_en', '')))

print(f"\nSpell names mentioned in Osamadas combat entries: {len(mentioned_actions)}")
for sid, name_fr, name_en in sorted(mentioned_actions):
    print(f"  id={sid} name_fr='{name_fr}' name_en='{name_en}'")
