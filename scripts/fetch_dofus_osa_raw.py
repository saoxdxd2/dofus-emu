import urllib.request
import re

# Try fetching raw HTML from Wayback Machine for dofus.com Osamadas page
timestamps = ['20191023131705', '20180121223804', '20171215215824']

for ts in timestamps:
    url = f'https://web.archive.org/web/{ts}id_/https://www.dofus.com/en/mmorpg/encyclopedia/classes/2-osamodas'
    print(f"\n=== Trying {ts} ===")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode('utf-8', errors='replace')
        
        print(f"Got {len(html)} bytes")
        
        # Save to file
        fname = f'scripts/dofus_osa_{ts}.html'
        with open(fname, 'w', encoding='utf-8') as f:
            f.write(html)
        
        # Search for spell IDs in the HTML
        # Look for spell URLs like /spells/201-beco-du-tofu
        spell_links = re.findall(r'/spells/(\d+)', html)
        print(f'Spell links (/spells/ID): {spell_links[:30]}')
        
        # Look for spell detail URLs
        spell_details = re.findall(r'spells/details[^\"\']*id=(\d+)', html)
        print(f'spells/details?id=ID: {spell_details[:30]}')
        
        # Look for spell IDs in data attributes
        data_ids = re.findall(r'data[_-]?spell[_-]?id[=\"\']\s*(\d+)', html, re.IGNORECASE)
        print(f'data-spell-id: {data_ids[:30]}')
        
        # If we found spell links, we're done
        if spell_links or spell_details:
            print(f"*** FOUND SPELL IDS in {ts} ***")
            break
            
    except Exception as e:
        print(f'Error: {e}')
