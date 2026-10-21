import json

with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    spells = json.load(f)

# Osamadas spell French patterns to search for
osa_patterns = [
    # Summons (Invocation de X)
    ("Invocation de Tofu", "invocation de tofu"),
    ("Invocation de Bouftou", "invocation de bouftou"),
    ("Invocation de Corbac", "invocation de corbac"),
    ("Invocation de Koalak", "invocation de koalak"),
    ("Invocation de Sanglier", "invocation de sanglier"),
    ("Invocation de Dragonnet Rouge", "invocation de dragonnet rouge"),
    ("Invocation de Arakne", "invocation de arakne"),
    ("Invocation de Dopeul", "invocation de dopeul"),
    # Kisses and Bites
    ("Béco du Tofu", "béco du tofu"),
    ("Morsure du Bouftou", "morsure du bouftou"),
    # Other Osamadas-themed
    ("Laisse Spirituelle", "laisse spirituelle"),
    ("Griffe Cinglante", "griffe cinglante"),
    ("Déplacement Félin", "déplacement félin"),
    ("Griffe Spectrale", "griffe spectrale"),
    ("Soin Bestial", "soin bestial"),
    ("Fouet", "fouet"),
    ("Substitution", "substitution"),
    ("Tumulte", "tumulte"),
    ("Favoritisme", "favoritisme"),
    ("Frappe du Craqueleur", "frappe du craqueleur"),
    ("Martyr", "martyr"),
    ("Bourrasque", "bourrasque"),
    ("Communion", "communion"),
    ("Acharnement", "acharnement"),
    ("Symbiose", "symbiose"),
]

print("Searching for Osamadas spells in spells_full.json\n")

found_spells = {}
for display_name, pattern in osa_patterns:
    matches = []
    for s in spells:
        fr = s.get("name_fr", "").lower()
        en = s.get("name_en", "").lower()
        if pattern in fr or pattern in en:
            matches.append(s)
    
    if matches:
        found_spells[display_name] = matches
        print(f"=== {display_name} (pattern: '{pattern}') ===")
        for s in matches:
            print(f"  id={s['id']:5d} type_id={s.get('type_id'):5d} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}'")
        print()

print("\n" + "=" * 70)
print("SUMMARY: All unique spells found")
print("=" * 70)
all_found = {}
for display_name, matches in found_spells.items():
    for s in matches:
        sid = s['id']
        if sid not in all_found:
            all_found[sid] = s
            print(f"  id={sid:5d} type_id={s.get('type_id'):5d} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}'")

print(f"\nTotal unique spells found: {len(all_found)}")
