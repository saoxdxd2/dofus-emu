import subprocess, io, os, sys

repo = r'c:\Users\sao\Documents\dofus-emu'
out_path = os.path.join(repo, 'tests', '_diff_out.txt')

def show(rev):
    return subprocess.check_output(
        ['git', '--no-pager', 'show', f'{rev}:desktop/client/www/build/script.js'],
        cwd=repo)

old = show('d661b25~1')
new = show('d661b25')

pos = 5172078
lines = []
lines.append('old char at %d: %r  (U+%04X)' % (pos, old[pos:pos+1], old[pos]))
lines.append('new char at %d: %r  (U+%04X)' % (pos, new[pos:pos+1], new[pos]))
ctx = 400
lines.append('=== OLD (around diff, markers <<>>) ===')
lines.append(old[pos-ctx:pos].decode('utf-8', 'replace') + '<<<' + old[pos:pos+1].decode('utf-8', 'replace') + '>>>' + old[pos+1:pos+ctx].decode('utf-8', 'replace'))
lines.append('=== NEW (around diff, markers <<>>) ===')
lines.append(new[pos-ctx:pos].decode('utf-8', 'replace') + '<<<' + new[pos:pos+1].decode('utf-8', 'replace') + '>>>' + new[pos+1:pos+ctx].decode('utf-8', 'replace'))

with io.open(out_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('DONE ->', out_path)
