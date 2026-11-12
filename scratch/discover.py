import json, os, glob, collections, sys, io

TRAJ = r'C:\Users\sao\Documents\dofus-emu\dofus-network\trajectories'
dec = json.JSONDecoder()

files = sorted(glob.glob(os.path.join(TRAJ, '*.jsonl')))
print("FILES:", len(files))

kinds = collections.Counter()
keysets = collections.defaultdict(collections.Counter)
examples = {}
bad = collections.Counter()
rows_per_file = collections.Counter()
tsmin = {}
tsmax = {}
# hunt for verdict-ish keys anywhere shallow
verdict_keys = collections.Counter()
meta_keys = collections.Counter()
result_keys = collections.Counter()

def walk_shallow(o, prefix, depth, out):
    if depth > 2 or not isinstance(o, dict):
        return
    for k, v in o.items():
        out[prefix + '.' + k] += 1
        if isinstance(v, dict):
            walk_shallow(v, prefix + '.' + k, depth + 1, out)

for path in files:
    name = os.path.basename(path)
    n = 0
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            n += 1
            pos = 0
            objs = []
            while pos < len(line):
                try:
                    o, end = dec.raw_decode(line, pos)
                except Exception:
                    bad[name] += 1
                    break
                objs.append(o)
                pos = end
                while pos < len(line) and line[pos] in ' \t\r\n':
                    pos += 1
                if pos < len(line) and line[pos] != '{':
                    bad[name] += 1
                    break
            for o in objs:
                if not isinstance(o, dict):
                    bad[name] += 1
                    continue
                k = o.get('kind', '<none>')
                kinds[k] += 1
                rows_per_file[name] += 1
                keysets[k][tuple(sorted(o.keys()))] += 1
                t = o.get('ts')
                if isinstance(t, (int, float)):
                    if name not in tsmin or t < tsmin[name]: tsmin[name] = t
                    if name not in tsmax or t > tsmax[name]: tsmax[name] = t
                if k not in examples:
                    examples[k] = (name, o)
                if isinstance(o.get('meta'), dict):
                    for mk in o['meta']: meta_keys[mk] += 1
                if isinstance(o.get('result'), dict):
                    for rk in o['result']: result_keys[rk] += 1
    print("  scanned", name, n, "lines", rows_per_file[name], "objs", flush=True)

print("\n=== KIND COUNTS ===")
for k, c in kinds.most_common():
    print(f"{c:9d}  {k}")

print("\n=== TOP-LEVEL KEYSETS PER KIND (top 3) ===")
for k, c in kinds.most_common():
    print(f"-- {k} ({c})")
    for ks, kc in keysets[k].most_common(3):
        print(f"   {kc:8d} {list(ks)}")

print("\n=== META KEYS ===", dict(meta_keys.most_common(40)))
print("\n=== RESULT KEYS ===", dict(result_keys.most_common(40)))

print("\n=== PARSE FAILURES ===", dict(bad), "TOTAL", sum(bad.values()))

print("\n=== TS RANGE PER FILE ===")
import datetime
for name in sorted(rows_per_file):
    a = tsmin.get(name); b = tsmax.get(name)
    fa = datetime.datetime.fromtimestamp(a, datetime.timezone.utc).isoformat() if a else None
    fb = datetime.datetime.fromtimestamp(b, datetime.timezone.utc).isoformat() if b else None
    print(f"{name:50s} rows={rows_per_file[name]:7d} {fa} -> {fb}")

print("\n=== EXAMPLES (compact, no observation) ===")
for k, c in kinds.most_common(14):
    name, o = examples[k]
    slim = {kk: vv for kk, vv in o.items() if kk not in ('observation', 'result', 'snapshot')}
    s = json.dumps(slim, ensure_ascii=False)
    print(f"\n-- kind={k} from {name}\n{s[:2200]}")
    if isinstance(o.get('result'), dict):
        r = {kk: vv for kk, vv in o['result'].items() if kk != 'snapshot'}
        print("   result(no snapshot):", json.dumps(r, ensure_ascii=False)[:1500])