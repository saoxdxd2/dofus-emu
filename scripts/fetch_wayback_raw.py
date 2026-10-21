import urllib.request
import re

url = 'https://web.archive.org/web/20230315180203/https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/2-osamodas'

try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode('utf-8', errors='replace')
    
    # Save to file
    with open('scripts/wayback_osa.html', 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f'Saved {len(html)} bytes')
    
    # Search for spell IDs in the HTML
    # Look for spell URLs like /spells/201-...
    spell_links = re.findall(r'/spells/(\d+)', html)
    print(f'Spell links (/spells/ID): {spell_links}')
    
    # Look for spell URLs like /encyclopedia/spells/201-...
    spell_links2 = re.findall(r'/encyclopedia/spells/(\d+)', html)
    print(f'Spell links (/encyclopedia/spells/ID): {spell_links2}')
    
    # Look for spell_id patterns
    spell_id_patterns = re.findall(r'spell[_-]?id[\"\']?\s*[:=]\s*[\"\']?(\d+)', html, re.IGNORECASE)
    print(f'spell_id patterns: {spell_id_patterns}')
    
    # Look for data-spell-id
    data_ids = re.findall(r'data[_-]?spell[_-]?id[=\"\']\s*(\d+)', html, re.IGNORECASE)
    print(f'data-spell-id: {data_ids}')
    
    # Look for any URLs with spell IDs
    url_ids = re.findall(r'spell[=/](\d+)', html, re.IGNORECASE)
    print(f'spell=/ID or spell=ID: {url_ids}')
    
    # Look for dofus-touch spell URLs
    dt_spell = re.findall(r'dofus-touch\.com[^\"\']*spell[^\"\']*(\d+)', html, re.IGNORECASE)
    print(f'dofus-touch spell URLs: {dt_spell}')
    
    # Search for any 3-4 digit numbers that could be spell IDs
    all_numbers = re.findall(r'\b(\d{3,4})\b', html)
    print(f'All 3-4 digit numbers (first 50): {all_numbers[:50]}')
    
except Exception as e:
    print(f'Error: {e}')
