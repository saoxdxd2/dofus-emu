import re
from pathlib import Path
import zipfile

z = zipfile.ZipFile('c:/Users/sao/Documents/dofus-emu/scripts/apk_extract/com.ankama.dofustouch.apk')
dex_data = z.read('classes.dex')
dex_data2 = z.read('classes2.dex')

print(f"classes.dex: {len(dex_data)} bytes")
print(f"classes2.dex: {len(dex_data2)} bytes")

# Search for Osamodas-related spell names
patterns = [b'Tofu', b'Arakne', b'Bouftou', b'Invocation', b'invocation', b'osamodas', b'Osamodas', b'OSAMODAS']

for name, data in [("classes.dex", dex_data), ("classes2.dex", dex_data2)]:
    print(f"\n=== Searching {name} ===")
    for pattern in patterns:
        idx = 0
        count = 0
        while True:
            idx = data.find(pattern, idx)
            if idx == -1:
                break
            count += 1
            if count <= 5:  # Show first 5 matches
                start = max(0, idx - 30)
                end = min(len(data), idx + 60)
                context = data[start:end]
                try:
                    ctx_str = context.decode('utf-8', errors='replace')
                except:
                    ctx_str = str(context)
                print(f"  [{count}] offset {idx}: ...{ctx_str}...")
            idx += 1
        if count > 5:
            print(f"  ... ({count} total matches for '{pattern.decode()}')")
        elif count > 0:
            print(f"  Total '{pattern.decode()}': {count}")
