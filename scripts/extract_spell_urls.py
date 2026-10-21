import re

with open('scripts/dofus_osa_20191023131705.html', 'r', encoding='utf-8') as f:
    html = f.read()

# Find all spell URLs
spell_urls = re.findall(r'href=\"[^\"]*spells[^\"]*\"', html, re.IGNORECASE)
for url in spell_urls[:50]:
    print(url)

print(f'\nTotal spell URLs: {len(spell_urls)}')

# Also look for spell IDs in data attributes
data_spell_ids = re.findall(r'data-spell-id=\"(\d+)\"', html)
print(f'data-spell-id values: {data_spell_ids[:40]}')

# Look for spell detail URLs with IDs
spell_details = re.findall(r'spells/details\?id=(\d+)', html)
print(f'spells/details?id= values: {spell_details[:40]}')
