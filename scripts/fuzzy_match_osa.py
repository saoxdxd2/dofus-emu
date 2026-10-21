import json

with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    spells = json.load(f)

# Dofus-touch spell names with English+French keyword pairs
osa_spells = [
    ("Spiritual Leash", ["spiritual", "leash", "link"], ["lien", "esprit", "âme"]),
    ("Lashing Claw", ["lashing", "claw", "laceration"], ["griffe", "lacération", "lacérer"]),
    ("Summoning of Tofu", ["tofu"], ["tofu", "bécu"]),
    ("Feline Movement", ["feline", "movement", "agility"], ["félin", "féline", "mouvement", "agilité"]),
    ("Ghostly Claw", ["ghostly", "claw", "spectral"], ["griffe", "fantôme", "spectral"]),
    ("Crow", ["crow", "raven"], ["corbeau", "corbac"]),
    ("Summoning of Gobball", ["gobball"], ["bouftou"]),
    ("Bestial Heal", ["bestial", "heal", "healing"], ["bestial", "soin", "guérison"]),
    ("Whip", ["whip"], ["fouet"]),
    ("Substitution", ["substitution"], ["substitution"]),
    ("Summoning of Koalak", ["koalak"], ["koalak"]),
    ("Tumult", ["tumult"], ["tumulte"]),
    ("Favouritism", ["favouritism", "favoritism", "favor"], ["faveur", "favoritisme", "préférence"]),
    ("Summoning of Boar", ["boar"], ["sanglier"]),
    ("Crackler Punch", ["crackler", "punch"], ["craqueleur"]),
    ("Martyr", ["martyr"], ["martyr"]),
    ("Gust", ["gust", "squall", "burst"], ["bourrasque"]),
    ("Communion", ["communion"], ["communion"]),
    ("Summoning of Red Wyrmling", ["wyrmling", "red dragon", "dragon"], ["dragonnet", "dragon", "wyrm"]),
    ("Determination", ["determination", "determined"], ["détermination"]),
    ("Symbiosa", ["symbiosa", "symbiosis"], ["symbiosa", "symbiose"]),
    ("Osamodas Exaltation", ["exaltation"], ["exaltation"]),
]

print("=== Fuzzy matching dofus-touch Osamadas spells to spells_full.json ===\n")

all_matches = []
for en_name, en_kw, fr_kw in osa_spells:
    print(f"\n--- {en_name} ---")
    matches = []
    for s in spells:
        en = s.get('name_en', '').lower()
        fr = s.get('name_fr', '').lower()
        
        # Check if any English keyword matches AND any French keyword matches
        en_match = any(kw in en for kw in en_kw)
        fr_match = any(kw in fr for kw in fr_kw)
        
        if en_match and fr_match:
            matches.append(s)
    
    # Deduplicate
    seen = set()
    unique = []
    for s in matches:
        if s['id'] not in seen:
            seen.add(s['id'])
            unique.append(s)
    
    if unique:
        for s in sorted(unique, key=lambda x: x['id']):
            print(f"  id={s['id']} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}' type_id={s.get('type_id')}")
            all_matches.append(s)
    else:
        print(f"  NO MATCH")

print(f"\n=== Total unique matches: {len(all_matches)} ===")
# Deduplicate all matches
seen = set()
unique_all = []
for s in all_matches:
    if s['id'] not in seen:
        seen.add(s['id'])
        unique_all.append(s)

for s in sorted(unique_all, key=lambda x: x['id']):
    print(f"  id={s['id']} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}' type_id={s.get('type_id')}")
