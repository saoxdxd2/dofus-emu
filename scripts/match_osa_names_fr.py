import json
from pathlib import Path

# Load spells_full.json
spells_path = Path("c:/Users/sao/Documents/dofus-emu/dofus-network/data/cache/spells_full.json")
with open(spells_path, 'r', encoding='utf-8') as f:
    spells = json.load(f)

# French keywords to search for each Osamadas spell
osa_search_terms = {
    "Spiritual Leash": ["lien spirituel", "lien", "esprit"],
    "Lashing Claw": ["griffe lacérante", "griffe", "lacération"],
    "Summoning of Tofu": ["bécu du tofu", "tofu"],
    "Feline Movement": ["mouvement félin", "félin", "mouvement"],
    "Ghostly Claw": ["griffe fantôme", "fantôme"],
    "Crow": ["corbeau"],
    "Summoning of Gobball": ["morsure du bouftou", "bouftou"],
    "Bestial Heal": ["soin bestial", "bestial"],
    "Whip": ["fouet"],
    "Substitution": ["substitution"],
    "Summoning of Koalak": ["invocation de koalak", "koalak"],
    "Tumult": ["tumulte"],
    "Favouritism": ["faveur", "favoritisme"],
    "Summoning of Boar": ["invocation de sanglier", "sanglier"],
    "Crackler Punch": ["frappe du craqueleur", "craqueleur"],
    "Martyr": ["martyr"],
    "Gust": ["bourrasque"],
    "Communion": ["communion"],
    "Summoning of Red Wyrmling": ["petit dragon rouge", "wyrm", "draconien"],
    "Determination": ["détermination"],
    "Symbiosa": ["symbiosa", "symbiose"],
    "Osamodas Exaltation": ["exaltation osamodas", "exaltation"],
}

print("=== Searching for Osamadas spells by French name keywords ===\n")

found_spells = {}
for english_name, keywords in osa_search_terms.items():
    print(f"\n--- {english_name} ---")
    matches = []
    for s in spells:
        fr = s.get('name_fr', '').lower()
        for kw in keywords:
            if kw in fr:
                matches.append(s)
                break
    
    # Deduplicate by id
    seen_ids = set()
    unique_matches = []
    for m in matches:
        if m['id'] not in seen_ids:
            seen_ids.add(m['id'])
            unique_matches.append(m)
    
    if unique_matches:
        for m in unique_matches:
            print(f"  id={m['id']} name_fr='{m['name_fr']}' name_en='{m.get('name_en')}' type_id={m.get('type_id')}")
            if m['id'] not in found_spells:
                found_spells[m['id']] = {'english_name': english_name, 'spell': m}
    else:
        print(f"  NO MATCH")

print(f"\n=== Total unique spells found: {len(found_spells)} ===")
for sid, info in sorted(found_spells.items()):
    s = info['spell']
    print(f"  id={sid} matched_as='{info['english_name']}' name_fr='{s['name_fr']}' name_en='{s.get('name_en')}' type_id={s.get('type_id')}")
