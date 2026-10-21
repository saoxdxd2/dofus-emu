import json

with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    spells = json.load(f)

pending = [
    ("Feline Movement", ["félin", "feline", "agilité", "agility", "mouvement", "movement"]),
    ("Ghostly Claw", ["griff", "claw", "fantôme", "spectral", "spectrale", "ghost"]),
    ("Bestial Heal", ["bestial", "soin", "heal", "guérison", "guérisseur"]),
    ("Substitution", ["substitution", "substituer", "échange", "swap"]),
    ("Summoning of Koalak", ["koalak", "invocation de koalak"]),
    ("Tumult", ["tumulte", "tumult", "tumultueux"]),
    ("Favouritism", ["faveur", "favoritisme", "préférence", "préféré"]),
    ("Summoning of Boar", ["sanglier", "boar", "invocation de sanglier"]),
    ("Determination", ["détermination", "determination", "déterminé"]),
    ("Symbiosa", ["symbiosa", "symbiose", "symbiosis"]),
]

for name, keywords in pending:
    print(f"\n=== {name} ===")
    found = False
    for s in spells:
        en = s.get("name_en", "").lower()
        fr = s.get("name_fr", "").lower()
        
        # Match if ANY keyword appears in either name
        if any(kw in en or kw in fr for kw in keywords):
            found = True
            print(f"  id={s['id']:5d} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}' type_id={s.get('type_id')}")
    
    if not found:
        print("  NO MATCH")
