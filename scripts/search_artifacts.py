import json
from pathlib import Path

root = Path("c:/Users/sao/Documents/dofus-emu/dofus-network/networkhealth_artifacts")

# Search for Osamadas spell data in artifacts
search_terms = [b'osamodas', b'Osamodas', b'breed_id', b'spell_id']

for f in root.rglob("*.jsonl"):
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
                if count <= 5:
                    start = max(0, idx - 100)
                    end = min(len(data), idx + 200)
                    context = data[start:end].decode('utf-8', errors='replace')
                    print(f"  {f.name}[{count}] @{idx}: ...{context}...")
                idx += 1
            if count > 0:
                print(f"  {f.name}: {count} matches for '{term.decode()}'")
    except Exception as e:
        pass
