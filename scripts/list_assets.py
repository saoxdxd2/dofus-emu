import zipfile
z = zipfile.ZipFile('scripts/apk_extract/com.ankama.dofustouch.apk')
for info in z.infolist():
    if info.filename.startswith('assets/'):
        print(info.filename, info.file_size)
