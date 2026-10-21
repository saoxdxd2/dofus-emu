import json

with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    spells = json.load(f)

touch_spells = [
    ("Spiritual Leash", 420), ("Lashing Claw", 4714), ("Summoning of Tofu", 4716),
    ("Feline Movement", 4718), ("Ghostly Claw", 4720), ("Crow", 4722),
    ("Summoning of Gobball", 4724), ("Bestial Heal", 4726), ("Whip", 4728),
    ("Substitution", 4730), ("Summoning of Koalak", 4732), ("Tumult", 4734),
    ("Favouritism", 4736), ("Summoning of Boar", 4738), ("Crackler Punch", 4740),
    ("Martyr", 4742), ("Gust", 4744), ("Communion", 4746),
    ("Summoning of Red Wyrmling", 4748), ("Determination", 4750),
    ("Symbiosa", 4752), ("Summoning of Osamodas Dopple", 6341),
]

KNOWN = {201, 202, 214, 224}

print("=" * 70)
print("Matching dofus-touch Osamadas spell names to spells_full.json")
print("=" * 70)

confirmed = {}
pending = []

for en_name, touch_id in touch_spells:
    print(f"\n--- {en_name} (touch_id={touch_id}) ---")
    found = None
    
    # Exact English name match
    for s in spells:
        if s.get("name_en", "").lower() == en_name.lower():
            found = s
            break
    
    if not found:
        # Keyword matching
        for s in spells:
            en = s.get("name_en", "").lower()
            fr = s.get("name_fr", "").lower()
            
            if en_name == "Spiritual Leash" and ("lien" in fr):
                found = s; break
            if en_name == "Lashing Claw" and ("griff" in fr or "laceration" in en):
                found = s; break
            if en_name == "Summoning of Tofu" and "tofu" in fr and "tofu" in en and "invocation" in fr:
                found = s; break
            if en_name == "Feline Movement" and ("félin" in fr):
                found = s; break
            if en_name == "Ghostly Claw" and ("griff" in fr) and ("spectral" in en or "spectrale" in fr or "fantôme" in fr):
                found = s; break
            if en_name == "Crow" and ("corbeau" in fr or "corbac" in fr):
                found = s; break
            if en_name == "Summoning of Gobball" and ("bouftou" in fr or "gobball" in en) and "invocation" in fr:
                found = s; break
            if en_name == "Bestial Heal" and ("bestial" in fr or "bestial" in en) and ("soin" in fr or "heal" in en):
                found = s; break
            if en_name == "Whip" and ("fouet" in fr or "whip" in en):
                found = s; break
            if en_name == "Substitution" and ("substitution" in fr or "substitution" in en):
                found = s; break
            if en_name == "Summoning of Koalak" and "koalak" in fr and "invocation" in fr:
                found = s; break
            if en_name == "Tumult" and ("tumulte" in fr):
                found = s; break
            if en_name == "Favouritism" and ("faveur" in fr or "favoritisme" in fr):
                found = s; break
            if en_name == "Summoning of Boar" and ("sanglier" in fr or "boar" in en) and "invocation" in fr:
                found = s; break
            if en_name == "Crackler Punch" and ("craqueleur" in fr or "crackler" in en):
                found = s; break
            if en_name == "Martyr" and ("martyr" in fr or "martyr" in en):
                found = s; break
            if en_name == "Gust" and ("bourrasque" in fr):
                found = s; break
            if en_name == "Communion" and ("communion" in fr or "communion" in en):
                found = s; break
            if en_name == "Summoning of Red Wyrmling" and ("dragonnet" in fr or "dragonnet" in en) and ("rouge" in fr or "red" in en):
                found = s; break
            if en_name == "Determination" and ("détermination" in fr):
                found = s; break
            if en_name == "Symbiosa" and ("symbiosa" in fr or "symbiose" in fr):
                found = s; break
            if en_name == "Summoning of Osamodas Dopple" and ("dopeul" in fr):
                found = s; break

    if found:
        sid = found["id"]
        confirmed[sid] = (en_name, touch_id, found)
        status = "✓ ALREADY VERIFIED" if sid in KNOWN else "✓ FOUND"
        print(f"  {status}: id={sid} name_fr='{found.get('name_fr')}' name_en='{found.get('name_en')}' type_id={found.get('type_id')}")
    else:
        pending.append((en_name, touch_id))
        print(f"  ✗ NO MATCH FOUND")

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Confirmed: {len(confirmed)}")
for sid in sorted(confirmed.keys()):
    en_name, touch_id, found = confirmed[sid]
    print(f"  id={sid} name_en='{found['name_en']}' name_fr='{found['name_fr']}' type_id={found['type_id']}")

print(f"\nPending: {len(pending)}")
for en_name, touch_id in pending:
    print(f"  {en_name} (touch_id={touch_id})")

all_ids = sorted(set(list(KNOWN) + list(confirmed.keys())))
print(f"\nAll confirmed IDs: {all_ids}")
print(f"Count: {len(all_ids)}")
