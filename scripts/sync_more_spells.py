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
print(f"Existing spells: {len(existing_spells)}, IDs: {min(existing_ids)}-{max(existing_ids)}")

# Fetch more spells from the API, starting after the existing max ID
# DofusDB uses pagination with $limit and $skip
all_new_spells = []
skip = 0
limit = 100

while skip < 5000:  # Fetch up to 5000 more spells
    url = f"{DOFUSDB_BASE}/spells?$limit={limit}&$skip={skip}"
    try:
        res = fetch_json(url)
        data = res.get("data", [])
        if not data:
            break
        
        new_spells = []
        for s in data:
            sid = s.get("id")
            if sid not in existing_ids:
                # Parse spell in the same format as spells_full.json
                name_obj = s.get("name", {})
                new_spells.append({
                    "id": sid,
                    "name_fr": name_obj.get("fr", ""),
                    "name_en": name_obj.get("en", ""),
                    "name_es": name_obj.get("es", ""),
                    "type_id": s.get("typeId"),
                    "spell_levels": s.get("spellLevels", []),
                })
                existing_ids.add(sid)
        
        all_new_spells.extend(new_spells)
        print(f"  Fetched batch at skip={skip}: {len(data)} spells, {len(new_spells)} new")
        
        if len(data) < limit:
            break
        
        skip += limit
        time.sleep(0.5)  # Be polite to the API
        
    except Exception as e:
        print(f"  Error at skip={skip}: {e}")
        break

print(f"\nTotal new spells found: {len(all_new_spells)}")

# Show spells that match Osamadas patterns
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

print(f"\nOsamadas-like spells found in new data: {len(osa_matches)}")
for s in osa_matches:
    print(f"  id={s['id']:5d} type_id={s.get('type_id'):5d} name_fr='{s.get('name_fr')}' name_en='{s.get('name_en')}'")

# Save new spells
if all_new_spells:
    combined = existing_spells + all_new_spells
    combined.sort(key=lambda x: x['id'])
    with open('dofus-network/data/cache/spells_full.json', 'w', encoding='utf-8') as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)
    print(f"\nSaved {len(combined)} total spells to spells_full.json")
