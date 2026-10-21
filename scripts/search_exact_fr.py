import json

with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    spells = json.load(f)

# Search for specific French names that could match dofus-touch Osamadas spells
target_fr_names = [
    'Lien Spirituel', 'Griffe Lacérante', 'Béco du Tofu', 'Mouvement Féline',
    'Griffe Fantôme', 'Corbeau', 'Morsure du Bouftou', 'Soin Bestial',
    'Fouet', 'Substitution', 'Invocation de Koalak', 'Tumulte',
    'Faveuritisme', 'Invocation de Sanglier', 'Frappe du Craqueleur',
    'Martyr', 'Bourrasque', 'Communion', 'Détermination',
    'Symbiosa', 'Exaltation Osamodas'
]

for target in target_fr_names:
    found = False
    for s in spells:
        if s.get('name_fr', '').lower() == target.lower():
            print(f'EXACT: id={s["id"]} name_fr="{s["name_fr"]}" name_en="{s.get("name_en","")}" type_id={s.get("type_id")}')
            found = True
            break
    if not found:
        print(f'NOT FOUND: {target}')
