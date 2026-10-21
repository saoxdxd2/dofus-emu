import os
from pathlib import Path

root = Path("c:/Users/sao/Documents/dofus-emu")

print("=== XAPK file ===")
for f in root.glob("*.xapk"):
    print(f, f.stat().st_size, "bytes")

print("\n=== assets dir (top level) ===")
assets = root / "assets"
if assets.exists():
    for item in assets.iterdir():
        print(" ", item.name, "(dir)" if item.is_dir() else f"file {item.stat().st_size}b")

print("\n=== asset_cache dir ===")
ac = root / "asset_cache"
if ac.exists():
    for item in ac.iterdir():
        print(" ", item.name, "(dir)" if item.is_dir() else f"file {item.stat().st_size}b")

print("\n=== Search for spell-related files anywhere ===")
for f in root.rglob("*"):
    if f.is_file() and "spell" in f.name.lower():
        print(" ", f)

print("\n=== Search for .json files in assets/asset_cache ===")
for d in [assets, ac]:
    if d.exists():
        for f in d.rglob("*.json"):
            print(" ", f, f.stat().st_size, "bytes")
