import io, os, re, unicodedata

repo = r'c:\Users\sao\Documents\dofus-emu'
path = os.path.join(repo, 'desktop', 'client', 'www', 'build', 'script.js')
text = io.open(path, 'rb').read().decode('utf-8', errors='replace')
out = []

# All quoted strings that look like Dofus protocol messages (end with 'Message' or known prefixes)
pat = re.compile(r'"([A-Za-zА-Яа-яЁё_]*(?:Message|Request|Confirm|Error)[A-Za-zА-Яа-яЁё_]*)"')
bad = {}
good_count = 0
for m in pat.finditer(text):
    s = m.group(1)
    non_ascii = [(ch, 'U+%04X' % ord(ch), unicodedata.name(ch, '?')) for ch in s if ord(ch) > 127]
    if non_ascii:
        if s not in bad:
            bad[s] = (m.start(), non_ascii)
    else:
        good_count += 1

out.append('=== Message-like literals WITH non-ASCII chars: %d unique ===' % len(bad))
for s, (pos, chars) in sorted(bad.items(), key=lambda kv: kv[1][0]):
    out.append('@%d %r -> %s' % (pos, s, chars))
out.append('')
out.append('(ascii message literals total: %d)' % good_count)

# Also catch the exact confirm string and its byte context
needle = 'GameMapMovementConfirmMessag'
i = text.find(needle)
while i >= 0:
    seg = text[i:i+40]
    out.append('CONFIRM @%d: %r' % (i, seg))
    i = text.find(needle, i+1)

with io.open(os.path.join(repo, 'tests', '_msgnames_out.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('DONE')
