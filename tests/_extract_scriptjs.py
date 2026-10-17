import io, os, sys

repo = r'c:\Users\sao\Documents\dofus-emu'
path = os.path.join(repo, 'desktop', 'client', 'www', 'build', 'script.js')
data = io.open(path, 'r', encoding='utf-8', errors='replace').read()

# args: start end outfile
start = int(sys.argv[1]); end = int(sys.argv[2]); out = sys.argv[3]
seg = data[start:end]
# split into 1500-char chunks so the read tool doesn't truncate
lines = []
for i in range(0, len(seg), 1500):
    lines.append('@[%d] %s' % (start + i, seg[i:i+1500]))
with io.open(out, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('WROTE', out, len(seg))
