import json

with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    spells = json.load(f)

type425 = [s for s in spells if s.get('type_id') == 425]
type425.sort(key=lambda x: x['id'])

print(f"Total spells with type_id=425: {len(type425)}\n")

# Osamadas-related French keywords
osa_fr_keywords = ['tofu', 'bouftou', 'corbac', 'koalak', 'craqueleur', 'dragonnet', 'sanglier', 
                   'invocation', 'béco', 'bécot', 'symbiose', 'symbiosa', 'exaltation',
                   'fouet', 'griff', 'lacération', 'bourrasque', 'tumulte', 'communion',
                   'substitution', 'martyr', 'détermination', 'félin', 'bestial', 'faveur',
                   'lien', 'fantôme', 'spectral', 'osamodas', 'dopeul']

for s in type425:
    fr = s.get('name_fr', '').lower()
    en = s.get('name_en', '').lower()
    
    # Check if any Osamadas keyword appears
    matched_kw = [kw for kw in osa_fr_keywords if kw in fr or kw in en]
    
    if matched_kw:
        print(f"id={s['id']:5d} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}' matched_kw={matched_kw}")
