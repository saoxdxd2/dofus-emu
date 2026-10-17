import zipfile, os, io, sys

repo = r'c:\Users\sao\Documents\dofus-emu'
xapk = os.path.join(repo, 'DOFUS+Touch_+A+WAKFU+Prequel_3.2.7_APKPure.xapk')
out_path = os.path.join(repo, 'tests', '_apk_out.txt')
lines = []

with zipfile.ZipFile(xapk) as z:
    names = z.namelist()
    lines.append('=== XAPK entries ===')
    for n in names:
        info = z.getinfo(n)
        lines.append('%10d  %s' % (info.file_size, n))
    # find the base apk (largest .apk)
    apks = [n for n in names if n.lower().endswith('.apk')]
    lines.append('APKs inside: %r' % (apks,))
    # pick biggest apk
    if apks:
        biggest = max(apks, key=lambda n: z.getinfo(n).file_size)
        lines.append('Inspecting inner APK: ' + biggest)
        data = z.read(biggest)
        with zipfile.ZipFile(io.BytesIO(data)) as az:
            interesting = [n for n in az.namelist() if 'script.js' in n or ('assets/www' in n and n.endswith('.js'))]
            lines.append('=== inner APK js entries under assets/www ===')
            for n in interesting[:50]:
                lines.append('%10d  %s' % (az.getinfo(n).file_size, n))
            target = 'assets/www/build/script.js'
            if target in az.namelist():
                payload = az.read(target)
                with open(os.path.join(repo, 'tests', '_apk_script.js'), 'wb') as f:
                    f.write(payload)
                lines.append('EXTRACTED %s -> tests/_apk_script.js (%d bytes)' % (target, len(payload)))
            else:
                lines.append('NOT FOUND: ' + target)

with io.open(out_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('DONE')
