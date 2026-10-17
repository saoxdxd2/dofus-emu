import io, os, hashlib

repo = r'c:\Users\sao\Documents\dofus-emu'
path = os.path.join(repo, 'desktop', 'client', 'www', 'build', 'script.js')
raw = io.open(path, 'rb').read()
print('bytes:', len(raw))
print('sha256:', hashlib.sha256(raw).hexdigest()[:16])
text = raw.decode('utf-8', errors='replace')
print('chars:', len(text))
print('CRLF count:', raw.count(b'\r\n'), 'LF count:', raw.count(b'\n'))
for pat in ['_requestMapChange', 'checkMapCoherence', 'launchMapTransition',
            'unlockMessages', 'ChangeMapMessage', 'gotoNeighbourMap']:
    idxs = []
    start = 0
    while True:
        i = text.find(pat, start)
        if i < 0: break
        idxs.append(i)
        start = i + 1
    print('%-24s count=%d offsets=%s' % (pat, len(idxs), idxs[:12]))
