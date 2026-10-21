import zipfile
import os
from pathlib import Path

xapk = Path("c:/Users/sao/Documents/dofus-emu/DOFUS+Touch_+A+WAKFU+Prequel_3.2.7_APKPure.xapk")
outdir = Path("c:/Users/sao/Documents/dofus-emu/scripts/apk_extract")
outdir.mkdir(exist_ok=True)

with zipfile.ZipFile(xapk, 'r') as z:
    # Extract main APK
    apk_data = z.read('com.ankama.dofustouch.apk')
    apk_path = outdir / 'com.ankama.dofustouch.apk'
    apk_path.write_bytes(apk_data)
    print(f"Extracted main APK: {apk_path} ({len(apk_data)} bytes)")

    # Extract manifest
    manifest = z.read('manifest.json')
    (outdir / 'manifest.json').write_bytes(manifest)
    print("Extracted manifest.json")

# Now inspect the APK (it's also a ZIP)
apk_zip = zipfile.ZipFile(apk_path)
print("\n=== APK contents (top level) ===")
for info in apk_zip.infolist():
    parts = info.filename.split('/')
    if len(parts) <= 2:
        print(f"  {info.filename}  ({info.file_size} bytes)")

print("\n=== Looking for assets folder ===")
asset_files = [i for i in apk_zip.infolist() if i.filename.startswith('assets/')]
print(f"  {len(asset_files)} files under assets/")
for info in asset_files[:30]:
    print(f"  {info.filename}  ({info.file_size} bytes)")
