import json
from pathlib import Path

root = Path("c:/Users/sao/Documents/dofus-emu/dofus-network")

# Search for "osamodas" or "breed_id": 2 in JSON files
search_terms = [b'osamodas', b'Osamodas', b'breed_id', b'"breed": 2', b'class_id": 2']

for f in root.rglob("*.json"):
    try:
        data = f.read_bytes()
        for term in search_terms:
            idx = 0
            count = 0
            while True:
                idx = data.find(term, idx)
                if idx == -1:
                    break
                count += 1
                if count <= 3:
                    start = max(0, idx - 60)
                    end = min(len(data), idx + 120)
                    context = data[start:end].decode('utf-8', errors='replace')
                    print(f"  {f.name}[{count}] @{idx}: ...{context}...")
                idx += 1
            if count > 0:
                print(f"  {f.name}: {count} matches for '{term.decode()}'")
    except Exception as e:
        pass
