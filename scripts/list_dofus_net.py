from pathlib import Path
dofus_net = Path('c:/Users/sao/Documents/dofus-emu/dofus-network')
for f in sorted(dofus_net.rglob('*')):
    if f.is_file():
        print(f, f.stat().st_size)
