import re
import json

html = open('scripts/wayback_osa_20220928053818.html', encoding='utf-8').read()

# Find all spell-id attributes
spell_ids = re.findall(r'spell-id="(\d+)"', html)

# Find all tooltip text (spell names)
tooltips = re.findall(r'<span class="ak-tooltip hidden">\s*([^<]+)\s*</span>', html)

# Find all alt text
alts = re.findall(r'alt="([^"]+)"', html)

print("Spell IDs:", spell_ids)
print("Count:", len(spell_ids))
print()
print("Tooltips:", tooltips)
print("Count:", len(tooltips))
print()
print("Alts:", alts)
print("Count:", len(alts))

# Try to pair them: each spell block has spell-id and a tooltip
# Let's look at the structure more carefully
# Pattern: <div class="ak-list-block ak-spell ..."> ... spell-id="N" ... <img ... alt="NAME"> ... <span class="ak-tooltip hidden">NAME</span>
blocks = re.findall(
    r'<div class="ak-list-block ak-spell[^"]*">.*?spell-id="(\d+)".*?<img[^>]*alt="([^"]+)".*?<span class="ak-tooltip hidden">\s*([^<]+)\s*</span>',
    html, re.DOTALL
)
print()
print("Paired blocks:", blocks)
print("Count:", len(blocks))
