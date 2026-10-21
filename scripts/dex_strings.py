import re
from pathlib import Path

dex_path = Path("c:/Users/sao/Documents/dofus-emu/scripts/apk_extract/com.ankama.dofustouch.apk")
# We need to read classes.dex from the APK zip
import zipfile
z = zipfile.ZipFile(dex_path)
dex_data = z.read('classes.dex')
dex_data2 = z.read('classes2.dex')

print(f"classes.dex: {len(dex_data)} bytes")
print(f"classes2.dex: {len(dex_data2)} bytes")

# Search for Osamodas-related strings in both dex files
for name, data in [("classes.dex", dex_data), ("classes2.dex", dex_data2)]:
    print(f"\n=== Searching {name} ===")
    
    # Search for "Osamodas" (case insensitive)
    for pattern in [b'Osamodas', b'OSAMODAS', b'osamodas']:
        idx = 0
        count = 0
        while True:
            idx = data.find(pattern, idx)
            if idx == -1:
                break
            count += 1
            # Show context
            start = max(0, idx - 40)
            end = min(len(data), idx + 80)
            context = data[start:end]
            try:
                ctx_str = context.decode('utf-8', errors='replace')
            except:
                ctx_str = str(context)
            print(f"  [{count}] offset {idx}: ...{ctx_str}...")
            idx += 1
        if count > 0:
            print(f"  Total '{pattern.decode()}': {count}")
