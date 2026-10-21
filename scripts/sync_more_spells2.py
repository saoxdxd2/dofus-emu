import json
import urllib.request
import time

DOFUSDB_BASE = "https://api.dofusdb.fr"

def fetch_json(url, timeout=15):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DofusFarmSync/2.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

# Load existing spells_full.json
with open('dofus-network/data/cache/spells_full.json', 'r', encoding='utf-8') as f:
    existing_spells = json.load(f)
existing_ids = {s['id'] for s in existing_spells}
print(f"Existing spells: {len(existing_spells)}, max ID: {max(existing_ids)}")

# Skip past existing spells to find higher-ID spells
# Try skip values to find spells with ID > 3358
all_new_spells = []

# Try several skip ranges to find high-ID spells
for skip_start in [2000, 5000, 8000, 10000, 12000, 14000, 16000]:
    skip = skip_start
    limit = 100
    found_in_range = 0
    
    while skip < skip_start + 500:  # Check a window of 500 entries
        url = f"{DOFUSDB_BASE}/spells?$limit={limit}&$skip={skip}"
        try:
            res = fetch_json(url)
            data = res.get("data", [])
            if not data:
                break
            
            for s in data:
                sid = s.get("id")
                if sid > 3358 and sid not in existing_ids:
                    name_obj = s.get("name", {})
                    new_spell = {
                        "id": sid,
                        "name_fr": name_obj.get("fr", ""),
                        "name_en": name_obj.get("en", ""),
                        "name_es": name_obj.get("es", ""),
                        "type_id": s.get("typeId"),
                        "spell_levels": s.get("spellLevels", []),
                    }
                    all_new_spells.append(new_spell)
                    existing_ids.add(sid)
                    found_in_range += 1
            
            if len(data) < limit:
                break
            skip += limit
            time.sleep(0.3)
        except Exception as e:
            print(f"  Error at skip={skip}: {e}")
            break
    
    print(f"  Range skip={skip_start}: found {found_in_range} spells with ID > 3358")

print(f"\nTotal new spells with ID > 3358: {len(all_new_spells)}")

# Show Osamadas-like spells
osa_patterns = [
    "invocation de tofu", "invocation de bouftou", "invocation de corbac",
    "invocation de koalak", "invocation de sanglier", "invocation de dragonnet rouge",
    "invocation de arakne", "invocation de dopeul", "béco du tofu", "morsure du bouftou",
    "laisse spirituelle", "griffe cinglante", "déplacement félin", "griffe spectrale",
    "soin bestial", "fouet", "substitution", "tumulte", "favoritisme", "frappe du craqueleur",
    "martyr", "bourrasque", "communion", "acharnement", "symbiose",
]

osa_matches = []
for s in all_new_spells:
    fr = s.get("name_fr", "").lower()
    en = s.get("name_en", "").lower()
    for pattern in osa_patterns:
        if pattern in fr or pattern in en:
            osa_matches.append(s)
            break

print(f"\nOsamadas-like spells found: {len(osa_matches)}")
for s in osa_matches:
    print(f"  id={s['id']:5d} type_id={s.get('type_id'):5d} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}'")

# Save new spells to a separate file
if all_new_spells:
    with open('dofus-network/data/cache/spells_extra.json', 'w', encoding='utf-8') as f:
        json.dump(all_new_spells, f, ensure_ascii=False, indent=2)
    print(f"\nSaved {len(all_new_spells)} extra spells to spells_extra.json")
