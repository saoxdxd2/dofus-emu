import io, os, re, sys

repo = r'c:\Users\sao\Documents\dofus-emu'
path = os.path.join(repo, 'desktop', 'client', 'www', 'build', 'script.js')
out_path = os.path.join(repo, 'tests', '_grep_out.txt')

data = io.open(path, 'r', encoding='utf-8', errors='replace').read()

patterns = sys.argv[1:] if len(sys.argv) > 1 else []
lines = []
for pat in patterns:
    lines.append('##### PATTERN: %s' % pat)
    cnt = 0
    for m in re.finditer(pat, data):
        cnt += 1
        s = max(0, m.start() - 350)
        e = min(len(data), m.end() + 350)
        snippet = data[s:e].replace('\n', ' ')
        lines.append('--- match %d @%d ---' % (cnt, m.start()))
        lines.append(snippet)
        if cnt >= 8:
            break
    lines.append('total shown: %d' % cnt)
    lines.append('')

with io.open(out_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('DONE', out_path)
