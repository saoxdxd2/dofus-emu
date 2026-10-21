import json

osa_path = 'scripts/output/osa_spells.json'
with open(osa_path, encoding='utf-8') as f:
    d = json.load(f)

# Count spells from spells_full.json vs dofus-touch only
spells_full_count = sum(1 for s in d["spells"] if "spells_full.json" in s.get("source", ""))
dofus_touch_only_count = sum(1 for s in d["spells"] if "dofus-touch.com" in s.get("source", ""))

# Update metadata
d["spells_verified_locally"] = spells_full_count
d["spells_pending_from_source"] = 0  # All 22 dofus-touch spells are covered
d["note"] = f"""This dataset contains {len(d['spells'])} Osamadas spells covering all 22 spells from dofus-touch.com.
- {spells_full_count} spells from spells_full.json (verified by exact ID match or French/English name matching)
- {dofus_touch_only_count} spells from dofus-touch.com only (not found in spells_full.json)
- {sum(1 for s in d['spells'] if s.get('summon'))} summon spells identified

Note: spells_full.json has no breed/class field, so Osamadas spells cannot be filtered automatically.
Matches were made by French/English name matching against dofus-touch.com spell names.
Some dofus-touch spell names may map to multiple spells_full.json entries (e.g., "Invocation de Tofu" maps to both id=201 and id=224).
"""

with open(osa_path, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

print(f"Updated metadata:")
print(f"  spells_verified_locally: {spells_full_count}")
print(f"  spells_pending_from_source: 0")
print(f"  Total spells: {len(d['spells'])}")
