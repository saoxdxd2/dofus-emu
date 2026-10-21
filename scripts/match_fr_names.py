import json

with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    spells = json.load(f)

# French spell names from dofus-touch page
french_spells = [
    ("Laisse Spirituelle", "Spiritual Leash", 420),
    ("Griffe Cinglante", "Lashing Claw", 4714),
    ("Invocation de Tofu", "Summoning of Tofu", 4716),
    ("Déplacement Félin", "Feline Movement", 4718),
    ("Griffe Spectrale", "Ghostly Claw", 4720),
    ("Corbeau", "Crow", 4722),
    ("Invocation de Bouftou", "Summoning of Gobball", 4724),
    ("Soin Bestial", "Bestial Heal", 4726),
    ("Fouet", "Whip", 4728),
    ("Substitution", "Substitution", 4730),
    ("Invocation de Koalak", "Summoning of Koalak", 4732),
    ("Tumulte", "Tumult", 4734),
    ("Favoritisme", "Favouritism", 4736),
    ("Invocation de Sanglier", "Summoning of Boar", 4738),
    ("Frappe du Craqueleur", "Crackler Punch", 4740),
    ("Martyr", "Martyr", 4742),
    ("Bourrasque", "Gust", 4744),
    ("Communion", "Communion", 4746),
    ("Invocation de Dragonnet Rouge", "Summoning of Red Wyrmling", 4748),
    ("Acharnement", "Determination", 4750),
    ("Symbiose", "Symbiosa", 4752),
    ("Invocation de Dopeul Osamodas", "Summoning of Osamodas Dopple", 6341),
]

KNOWN = {201, 202, 214, 224}

print("Matching French dofus-touch spell names to spells_full.json\n")

confirmed = {}
pending = []

for fr_name, en_name, touch_id in french_spells:
    print(f"=== {fr_name} ({en_name}, touch_id={touch_id}) ===")
    
    found = None
    
    # Try exact French name match
    for s in spells:
        if s.get("name_fr", "").lower() == fr_name.lower():
            found = s
            break
    
    if not found:
        # Try French name containing key words
        fr_kw = fr_name.lower()
        # Remove "Invocation de " prefix
        if fr_kw.startswith("invocation de "):
            fr_short = fr_kw.replace("invocation de ", "")
        else:
            fr_short = fr_kw
        
        for s in spells:
            fr = s.get("name_fr", "").lower()
            en = s.get("name_en", "").lower()
            
            if fr_short in fr or fr_short in en:
                found = s
                break
    
    if not found:
        # Try broader keyword matching
        for s in spells:
            fr = s.get("name_fr", "").lower()
            en = s.get("name_en", "").lower()
            
            if fr_name == "Laisse Spirituelle" and ("lien" in fr or "spirituelle" in fr or "spiritual" in en):
                found = s; break
            if fr_name == "Griffe Cinglante" and ("cinglant" in fr or "cinglante" in fr or "lashing" in en):
                found = s; break
            if fr_name == "Invocation de Tofu" and "tofu" in fr and "invocation" in fr:
                found = s; break
            if fr_name == "Déplacement Félin" and ("félin" in fr or "feline" in en):
                found = s; break
            if fr_name == "Griffe Spectrale" and ("griff" in fr) and ("spectral" in fr or "spectrale" in fr or "ghostly" in en):
                found = s; break
            if fr_name == "Corbeau" and ("corbeau" in fr or "corbac" in fr):
                found = s; break
            if fr_name == "Invocation de Bouftou" and ("bouftou" in fr) and ("invocation" in fr):
                found = s; break
            if fr_name == "Soin Bestial" and ("bestial" in fr) and ("soin" in fr):
                found = s; break
            if fr_name == "Fouet" and ("fouet" in fr or "whip" in en):
                found = s; break
            if fr_name == "Substitution" and ("substitution" in fr):
                found = s; break
            if fr_name == "Invocation de Koalak" and "koalak" in fr and ("invocation" in fr or "koalak" in en):
                found = s; break
            if fr_name == "Tumulte" and ("tumulte" in fr):
                found = s; break
            if fr_name == "Favoritisme" and ("favorit" in fr or "faveur" in fr):
                found = s; break
            if fr_name == "Invocation de Sanglier" and ("sanglier" in fr) and ("invocation" in fr):
                found = s; break
            if fr_name == "Frappe du Craqueleur" and ("craqueleur" in fr) and ("frappe" in fr):
                found = s; break
            if fr_name == "Martyr" and ("martyr" in fr):
                found = s; break
            if fr_name == "Bourrasque" and ("bourrasque" in fr):
                found = s; break
            if fr_name == "Communion" and ("communion" in fr):
                found = s; break
            if fr_name == "Invocation de Dragonnet Rouge" and ("dragonnet" in fr) and ("rouge" in fr):
                found = s; break
            if fr_name == "Acharnement" and ("acharnement" in fr):
                found = s; break
            if fr_name == "Symbiose" and ("symbiose" in fr or "symbiosa" in fr):
                found = s; break
            if fr_name == "Invocation de Dopeul Osamodas" and ("dopeul" in fr):
                found = s; break

    if found:
        sid = found["id"]
        confirmed[sid] = (fr_name, en_name, touch_id, found)
        status = "✓ ALREADY VERIFIED" if sid in KNOWN else "✓ FOUND"
        print(f"  {status}: id={sid} name_fr='{found.get('name_fr')}' name_en='{found.get('name_en')}' type_id={found.get('type_id')}")
    else:
        pending.append((fr_name, en_name, touch_id))
        print(f"  ✗ NO MATCH FOUND")

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Confirmed: {len(confirmed)}")
for sid in sorted(confirmed.keys()):
    fr_name, en_name, touch_id, found = confirmed[sid]
    print(f"  id={sid} name_en='{found['name_en']}' name_fr='{found['name_fr']}' type_id={found['type_id']}")

print(f"\nPending: {len(pending)}")
for fr_name, en_name, touch_id in pending:
    print(f"  {fr_name} ({en_name}, touch_id={touch_id})")

all_ids = sorted(set(list(KNOWN) + list(confirmed.keys())))
print(f"\nAll confirmed IDs: {all_ids}")
print(f"Count: {len(all_ids)} / 22")
