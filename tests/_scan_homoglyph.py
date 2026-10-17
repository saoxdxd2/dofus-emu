import io, os, re, unicodedata

repo = r'c:\Users\sao\Documents\dofus-emu'
path = os.path.join(repo, 'desktop', 'client', 'www', 'build', 'script.js')
raw = io.open(path, 'rb').read()
text = raw.decode('utf-8', errors='replace')
out = []

# 1) Find all string literals containing non-ASCII chars (focus on those that look like identifiers/message names)
pat = re.compile(r'"([^"\\]{2,80})"')
sus = {}
for m in pat.finditer(text):
    s = m.group(1)
    if any(ord(ch) > 127 for ch in s):
        # likely identifier-like strings (camelcase / Message names) or containing cyrillic
        has_cyr = any('CYRILLIC' in unicodedata.name(ch, '') for ch in s)
        key = s
        if key not in sus:
            sus[key] = [m.start(), has_cyr]

out.append('=== string literals with non-ASCII (%d unique) ===' % len(sus))
shown = 0
for s, (pos, cyr) in sus.items():
    flag = 'CYRILLIC' if cyr else 'non-ascii'
    # only show identifier-ish or cyrillic ones
    if cyr or re.match(r'^[A-Za-zА-Яа-яЁё][\w]*$', s, re.UNICODE):
        out.append('@%d [%s] %r' % (pos, flag, s))
        shown += 1
        if shown > 120:
            out.append('... more ...')
            break

# 2) Specifically Cyrillic anywhere in code (outside obvious text): report context
out.append('')
out.append('=== ALL cyrillic char occurrences (context) ===')
count = 0
for m in re.finditer(r'[^\x00-\x7F]', text):
    ch = m.group(0)
    name = unicodedata.name(ch, '')
    if 'CYRILLIC' in name:
        count += 1
        s = max(0, m.start()-100); e = min(len(text), m.start()+100)
        out.append('@%d U+%04X %s ... %s' % (m.start(), ord(ch), name, text[s:e].replace('\n',' ')))
        if count > 40:
            out.append('... more ...')
            break
out.append('total cyrillic (capped): %d' % count)

with io.open(os.path.join(repo, 'tests', '_homoglyph_out.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('DONE')
