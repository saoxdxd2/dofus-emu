import urllib.request
import re

# Try fetching raw HTML from Wayback Machine
timestamps = ['20230315180203', '20220928053818', '20210512091843', '20200808060929']

for ts in timestamps:
    url = f'https://web.archive.org/web/{ts}id_/https://www.dofus-touch.com/en/mmorpg/encyclopedia/classes/2-osamodas'
    print(f"\n=== Trying {ts} ===")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode('utf-8', errors='replace')
        
        print(f"Got {len(html)} bytes")
        
        # Save to file
        fname = f'scripts/wayback_osa_{ts}.html'
        with open(fname, 'w', encoding='utf-8') as f:
            f.write(html)
        
        # Search for spell IDs in the HTML
        spell_links = re.findall(r'/spells/(\d+)', html)
        print(f'Spell links (/spells/ID): {spell_links}')
        
        spell_links2 = re.findall(r'/encyclopedia/spells/(\d+)', html)
        print(f'Spell links (/encyclopedia/spells/ID): {spell_links2}')
        
        # Look for any dofus-touch spell URLs
        dt_urls = re.findall(r'dofus-touch\.com[^\"\'<>]*spell[^\"\'<>]*(\d+)', html, re.IGNORECASE)
        print(f'dofus-touch spell URLs: {dt_urls}')
        
        # Look for spell IDs in various patterns
        patterns = [
            r'spell[_-]?id[\"\']?\s*[:=]\s*[\"\']?(\d+)',
            r'data[_-]?spell[_-]?id[=\"\']\s*(\d+)',
            r'spell[=/](\d+)',
            r'"id"\s*:\s*(\d+)',
        ]
        for pat in patterns:
            matches = re.findall(pat, html, re.IGNORECASE)
            if matches:
                print(f'Pattern {pat}: {matches[:20]}')
        
        # If we found spell links, we're done
        if spell_links or spell_links2:
            print(f"*** FOUND SPELL IDS in {ts} ***")
            break
            
    except Exception as e:
        print(f'Error: {e}')
