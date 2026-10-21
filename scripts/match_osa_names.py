import json
from pathlib import Path

# Load spells_full.json
spells_path = Path("c:/Users/sao/Documents/dofus-emu/dofus-network/data/cache/spells_full.json")
with open(spells_path, 'r', encoding='utf-8') as f:
    spells = json.load(f)

# Spell names from the archived dofus-touch.com Osamadas class page
osa_spell_names = [
    "Spiritual Leash",
    "Lashing Claw",
    "Summoning of Tofu",
    "Feline Movement",
    "Ghostly Claw",
    "Crow",
    "Summoning of Gobball",
    "Bestial Heal",
    "Whip",
    "Substitution",
    "Summoning of Koalak",
    "Tumult",
    "Favouritism",
    "Summoning of Boar",
    "Crackler Punch",
    "Martyr",
    "Gust",
    "Communion",
    "Summoning of Red Wyrmling",
    "Determination",
    "Symbiosa",
    "Osamodas Exaltation",
]

print(f"Total spell names: {len(osa_spell_names)}")

# Try exact match on name_en first
matches = []
unmatched = []

for name in osa_spell_names:
    found = None
    for s in spells:
        if s.get('name_en', '').lower().strip() == name.lower().strip():
            found = s
            break
    if found:
        matches.append(found)
        print(f"  MATCH: id={found['id']} name_en='{found['name_en']}' name_fr='{found['name_fr']}' type_id={found.get('type_id')}")
    else:
        unmatched.append(name)
        print(f"  NO MATCH: {name}")

print(f"\n=== Matched: {len(matches)}, Unmatched: {len(unmatched)} ===")
print(f"\nUnmatched names: {unmatched}")

# Try fuzzy match for unmatched names
print(f"\n=== Fuzzy matching unmatched names ===")
for name in unmatched:
    print(f"\nSearching for: '{name}'")
    for s in spells:
        en = s.get('name_en', '').lower()
        fr = s.get('name_fr', '').lower()
        es = s.get('name_es', '').lower()
        name_l = name.lower()
        # Check if key words match
        if name_l in en or name_l in fr or name_l in es:
            print(f"  POSSIBLE: id={s['id']} name_en='{s.get('name_en')}' name_fr='{s.get('name_fr')}' type_id={s.get('type_id')}")
