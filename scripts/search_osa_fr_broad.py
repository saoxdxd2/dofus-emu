import json

with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    spells = json.load(f)

# Search for spells with Osamadas-related French keywords
osa_keywords = [
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
    'Bouftou', 'bouftou',
    'Tofu', 'tofu',
    'Koalak', 'koalak',
    'Bestial', 'bestial',
    'Félin', 'félin',
    'Fantôme', 'fantôme',
    'Lacération', 'lacération',
    'Arakne', 'arakne',
    'Araignée', 'araignée',
]

print("=== Spells with Osamadas-related French keywords (type_id=425 only) ===\n")
results = []
for s in spells:
    if s.get('type_id') != 425:
        continue
    fr = s.get('name_fr', '')
    en = s.get('name_en', '')
    for kw in osa_keywords:
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

print(f"Found {len(unique)} unique spells with type_id=425 and Osamadas-related keywords\n")
for s in sorted(unique, key=lambda x: x['id']):
    print(f"id={s['id']} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}'")
