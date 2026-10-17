import subprocess, os

repo = r'c:\Users\sao\Documents\dofus-emu'
CYR = 'GameMapMovementConfirmMessagе'  # cyrillic е
LAT = 'GameMapMovementConfirmMessage'   # latin e

# list commits touching script.js
log = subprocess.check_output(
    ['git', '--no-pager', 'log', '--format=%H %ad %s', '--date=short', '--', 'desktop/client/www/build/script.js'],
    cwd=repo).decode('utf-8', 'replace').strip().split('\n')
print('commits touching script.js:')
for l in log:
    print('  ', l)

for line in log:
    h = line.split()[0]
    try:
        blob = subprocess.check_output(
            ['git', '--no-pager', 'show', '%s:desktop/client/www/build/script.js' % h],
            cwd=repo)
    except subprocess.CalledProcessError:
        print(h[:8], 'no blob')
        continue
    text = blob.decode('utf-8', 'replace')
    print('%s len=%d cyr=%d lat=%d' % (h[:8], len(blob), text.count(CYR), text.count(LAT)))
