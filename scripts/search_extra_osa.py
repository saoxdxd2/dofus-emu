import json

with open('dofus-network/data/cache/spells_extra.json', 'r', encoding='utf-8') as f:
    extra_spells = json.load(f)

print(f"Total extra spells: {len(extra_spells)}\n")

# Search for Osamadas-like patterns in extra spells
osa_patterns = [
    ("invocation de tofu", "Summoning of Tofu"),
    ("invocation de bouftou", "Summoning of Gobball"),
    ("invocation de corbac", "Crow / Corbeau"),
    ("invocation de koalak", "Summoning of Koalak"),
    ("invocation de sanglier", "Summoning of Boar"),
    ("invocation de dragonnet rouge", "Summoning of Red Wyrmling"),
    ("invocation de dopeul", "Summoning of Dopple"),
    ("béco du tofu", "Tofu Kiss"),
    ("morsure du bouftou", "Gobball Bite"),
    ("laisse spirituelle", "Spiritual Leash"),
    ("griffe cinglante", "Lashing Claw"),
    ("déplacement félin", "Feline Movement"),
    ("griffe spectrale", "Ghostly Claw"),
    ("soin bestial", "Bestial Heal"),
    ("fouet", "Whip"),
    ("substitution", "Substitution"),
    ("tumulte", "Tumult"),
    ("favoritisme", "Favouritism"),
    ("frappe du craqueleur", "Crackler Punch"),
    ("martyr", "Martyr"),
    ("bourrasque", "Gust"),
    ("communion", "Communion"),
    ("acharnement", "Determination"),
    ("symbiose", "Symbiosa"),
    ("bestial", "Bestial"),
    ("dragodinde", "Dragoturkey"),
]

print("=== Searching extra spells for Osamadas patterns ===\n")
for pattern, display in osa_patterns:
    matches = []
    for s in extra_spells:
        fr = s.get("name_fr", "").lower()
        en = s.get("name_en", "").lower()
        if pattern in fr or pattern in en:
            matches.append(s)
    
    if matches:
        print(f"--- {display} (pattern: '{pattern}') ---")
        for s in matches:
            print(f"  id={s['id']:5d} type_id={s.get('type_id'):5d} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}'")
        print()

# Also show ALL type_id=425 spells in extra data with reasonable names
print("\n=== All type_id=425 spells in extra data (IDs 3359-3500) ===")
for s in sorted(extra_spells, key=lambda x: x['id']):
    sid = s['id']
    tid = s.get('type_id')
    fr = s.get('name_fr', '')
    en = s.get('name_en', '')
    if tid == 425 and 3359 <= sid <= 3500:
        print(f"  id={sid:5d} name_fr='{fr}' name_en='{en}'")
