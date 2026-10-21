import zipfile
from pathlib import Path
import json

xapk = Path("c:/Users/sao/Documents/dofus-emu/DOFUS+Touch_+A+WAKFU+Prequel_3.2.7_APKPure.xapk")

print("=== XAPK contents ===")
with zipfile.ZipFile(xapk, 'r') as z:
    for info in z.infolist():
        print(f"  {info.filename}  ({info.file_size} bytes)")

print("\n=== Check asset_cache .meta files for content hints ===")
ac = Path("c:/Users/sao/Documents/dofus-emu/asset_cache")
meta_files = list(ac.glob("*.meta"))[:5]
for mf in meta_files:
    print(f"\n--- {mf.name} ---")
    print(mf.read_text(encoding='utf-8', errors='replace'))

print("\n=== Search asset_cache for JSON/spell content ===")
for f in ac.glob("*"):
    if f.suffix == ".meta":
        continue
    try:
        data = f.read_bytes()
        # Check if it's JSON
        if data[:1] in (b'[', b'{'):
            text = data.decode('utf-8', errors='replace')[:200]
            print(f"\n=== JSON-like: {f.name} ({len(data)} bytes) ===")
            print(text[:300])
    except Exception:
        pass
