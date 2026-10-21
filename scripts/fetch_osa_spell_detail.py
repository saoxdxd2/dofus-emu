import urllib.request
import re

# Try fetching dofus-touch spell detail pages through Wayback Machine
# These might contain French names or main Dofus spell IDs

spell_ids = [420, 4714, 4716, 4718, 4720, 4722, 4724, 4726, 4728, 4730, 4732, 4734, 4736, 4738, 4740, 4742, 4744]

for sid in spell_ids[:5]:  # Try first 5
    url = f'https://web.archive.org/web/2022/https://www.dofus-touch.com/en/mmorpg/encyclopedia/spells/details?id={sid}&level=1&selector=1&characterlevel=1&showlevel=0&startingspell=0'
    print(f"\n=== Trying spell id={sid} ===")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='replace')
        
        print(f"Got {len(html)} bytes")
        
        # Save to file
        fname = f'scripts/dt_spell_{sid}.html'
        with open(fname, 'w', encoding='utf-8') as f:
            f.write(html)
        
        # Look for French name or spell ID
        # Look for spell name in title or h1
        title_match = re.search(r'<title>([^<]*)</title>', html)
        if title_match:
            print(f"Title: {title_match.group(1)}")
        
        # Look for French spell name patterns
        fr_patterns = [
            r'<h1[^>]*>([^<]*)</h1>',
            r'spell[_-]?name[\"\']?\s*[:=]\s*[\"\']?([^\"\']+)',
            r'name[\"\']?\s*[:=]\s*[\"\']?([^\"\']+)',
        ]
        for pat in fr_patterns:
            matches = re.findall(pat, html, re.IGNORECASE)
            if matches:
                print(f"Pattern {pat}: {matches[:5]}")
        
        # Look for main dofus spell ID
        dofus_ids = re.findall(r'id[\"\']?\s*[:=]\s*[\"\']?(\d{2,4})', html)
        if dofus_ids:
            print(f"Possible spell IDs: {dofus_ids[:10]}")
            
    except Exception as e:
        print(f'Error: {e}')
