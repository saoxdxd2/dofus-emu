import json

osa_path = 'scripts/output/osa_spells.json'
with open(osa_path, encoding='utf-8') as f:
    d = json.load(f)

# Count spells from spells_full.json vs dofus-touch only
spells_full_count = sum(1 for s in d["spells"] if s.get("source", "").startswith("spells_full.json"))
dofus_touch_only_count = sum(1 for s in d["spells"] if s.get("source", "").startswith("dofus-touch.com"))

d["spells_verified_locally"] = spells_full_count
d["spells_pending_from_source"] = 0

with open(osa_path, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

print(f"spells_verified_locally: {spells_full_count}")
print(f"spells_pending_from_source: 0")
print(f"dofus_touch_only_count: {dofus_touch_only_count}")
print(f"Total spells: {len(d['spells'])}")
