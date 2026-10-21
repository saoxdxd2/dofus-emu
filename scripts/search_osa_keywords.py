import json

with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    spells = json.load(f)

# Search for spells with Osamadas-related French keywords
keywords = [
    'Invocation', 'invocation', 'INVOCATION',
    'Fouet', 'fouet',
    'Tumulte', 'tumulte',
    'Détermination', 'détermination',
    'Symbiosa', 'symbiosa',
    'Exaltation', 'exaltation',
    'Lien', 'lien',
    'Griffe', 'griffe',
    'Corbeau', 'corbeau',
    'Substitution', 'substitution',
    'Faveur', 'faveur',
    'Sanglier', 'sanglier',
    'Craqueleur', 'craqueleur',
    'Martyr', 'martyr',
    'Bourrasque', 'bourrasque',
    'Communion', 'communion',
    'Dragon', 'dragon',
    'Wyrm', 'wyrm',
]

print("=== Spells with Osamadas-related French keywords ===\n")
results = []
for s in spells:
    fr = s.get('name_fr', '')
    en = s.get('name_en', '')
    for kw in keywords:
        if kw.lower() in fr.lower() or kw.lower() in en.lower():
            results.append(s)
            break

# Deduplicate
seen = set()
unique = []
for s in results:
    if s['id'] not in seen:
        seen.add(s['id'])
        unique.append(s)

print(f"Found {len(unique)} unique spells with Osamadas-related keywords\n")
for s in sorted(unique, key=lambda x: x['id']):
    print(f"id={s['id']} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}' type_id={s.get('type_id')}")
