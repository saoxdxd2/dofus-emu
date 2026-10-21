import re
from collections import Counter

with open('dofus-network/data/game_data_extractor.py', encoding='utf-8') as f:
    content = f.read()

print("=== Breed section comments ===")
for m in re.finditer(r'# --- (.+) ---', content):
    print(m.group(1))

print("\n=== All breed_id values with counts ===")
c = Counter(int(m.group(1)) for m in re.finditer(r'breed_id=(\d+)', content))
for breed, count in sorted(c.items()):
    print(f"breed_id={breed}: {count} spells")

print("\n=== Osamodas (breed_id=2) spell lines ===")
osa_section = False
for line in content.splitlines():
    if 'Osamodas' in line:
        osa_section = True
        print(line)
        continue
    if osa_section:
        if line.strip().startswith('# ---') and 'Osamodas' not in line:
            osa_section = False
        if osa_section and 'breed_id=2' in line:
            print(line)
