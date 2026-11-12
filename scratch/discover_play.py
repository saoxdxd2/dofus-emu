import json, os, glob, collections

TRAJ = r'C:\Users\sao\Documents\dofus-emu\dofus-network\trajectories'
dec = json.JSONDecoder()
files = sorted(glob.glob(os.path.join(TRAJ, 'play_*.jsonl')))

ek = collections.Counter()
ex = {}
sess = collections.Counter()
atmin, atmax = None, None
per_file = collections.Counter()
name_keys = collections.Counter()
for path in files:
    n = os.path.basename(path)
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            per_file[n] += 1
            s = d.get('session')
            if s: sess[s] += 1
            a = d.get('at')
            if isinstance(a, (int, float)):
                if atmin is None or a < atmin: atmin = a
                if atmax is None or a > atmax: atmax = a
            e = d.get('event')
            if isinstance(e, dict):
                k = e.get('kind', '<nokind>')
                ek[k] += 1
                if k not in ex: ex[k] = (n, e)
                if k not in ('recv', 'send') and isinstance(e.get('name'), str):
                    name_keys[(k, e['name'])] += 1
            else:
                ek['<noevent:' + str(type(e).__name__) + '>'] += 1

print("=== event.kind counts (play_*) ===")
for k, c in ek.most_common(60): print(f"{c:9d}  {k}")
print("\n=== non-recv/send (kind,name) top 60 ===")
for (k, nm), c in name_keys.most_common(60): print(f"{c:9d}  {k} / {nm}")
print("\n=== sessions ===", len(sess))
for s, c in sess.most_common(30): print(f"{c:9d}  {s}")
import datetime
print("\nAT range:", datetime.datetime.fromtimestamp(atmin/1000, datetime.timezone.utc).isoformat(),
      "->", datetime.datetime.fromtimestamp(atmax/1000, datetime.timezone.utc).isoformat())
print("\n=== per file ===")
for n, c in per_file.most_common(): print(f"{c:9d}  {n}")
print("\n=== EXAMPLES per event.kind (non recv/send) ===")
for k, c in ek.most_common():
    if k in ('recv', 'send'): continue
    n, e = ex[k]
    print(f"\n-- {k} ({c}) from {n}\n{json.dumps(e, ensure_ascii=False)[:1800]}")
print("\n=== recv/send example ===")
for k in ('recv', 'send'):
    if k in ex:
        n, e = ex[k]
        print(f"\n-- {k} from {n}\n{json.dumps(e, ensure_ascii=False)[:700]}")