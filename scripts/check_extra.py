import json, os

path = 'dofus-network/data/cache/spells_extra.json'
if os.path.exists(path):
    d = json.load(open(path, encoding='utf-8'))
    print(f'{len(d)} extra spells found')
    for s in d:
        print(f'  id={s["id"]} type_id={s.get("type_id")} name_fr={s.get("name_fr")} name_en={s.get("name_en")}')
else:
    print('File does not exist')
