from pathlib import Path
d = Path('c:/Users/sao/Documents/dofus-emu/dofus-network/data')
for f in sorted(d.rglob('*')):
    if f.is_file():
        print(f, f.stat().st_size)
