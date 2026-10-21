import json

with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    spells = json.load(f)

pending = [
    "Feline Movement",
    "Ghostly Claw", 
    "Bestial Heal",
    "Substitution",
    "Summoning of Koalak",
    "Tumult",
    "Favouritism",
    "Summoning of Boar",
    "Determination",
    "Symbiosa",
]

print("Searching for pending spells in spells_full.json\n")

for name in pending:
    print(f"=== {name} ===")
    found_any = False
    for s in spells:
        en = s.get("name_en", "").lower()
        fr = s.get("name_fr", "").lower()
        
        match = False
        if name == "Feline Movement" and ("félin" in fr or "feline" in en):
            match = True
        elif name == "Ghostly Claw" and ("griff" in fr) and ("fantôme" in fr or "spectral" in en or "spectrale" in fr):
            match = True
        elif name == "Bestial Heal" and ("bestial" in fr or "bestial" in en) and ("soin" in fr or "heal" in en or "guérison" in fr):
            match = True
        elif name == "Substitution" and ("substitution" in fr or "substitution" in en):
            match = True
        elif name == "Summoning of Koalak" and "koalak" in fr and ("invocation" in fr or "koalak" in en):
            match = True
        elif name == "Tumult" and ("tumulte" in fr or "tumult" in en):
            match = True
        elif name == "Favouritism" and ("faveur" in fr or "favoritisme" in fr or "préférence" in fr):
            match = True
        elif name == "Summoning of Boar" and ("sanglier" in fr or "boar" in en) and ("invocation" in fr):
            match = True
        elif name == "Determination" and ("détermination" in fr):
            match = True
        elif name == "Symbiosa" and ("symbiosa" in fr or "symbiose" in fr):
            match = True
        
        if match:
            found_any = True
            print(f"  id={s['id']} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}' type_id={s.get('type_id')}")
    
    if not found_any:
        print(f"  NO MATCH")
    print()
