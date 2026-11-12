import json, os, glob, collections, datetime, re

TRAJ = r'C:\Users\sao\Documents\dofus-emu\dofus-network\trajectories'
ROOT = r'C:\Users\sao\Documents\dofus-emu\trajectories'
CUTOFF = 1794360732.410 - 7 * 86400   # anchored to global max ts found earlier

def ts_of(o):
    t = o.get('ts') or o.get('at') or o.get('time')
    if isinstance(t, (int, float)):
        return t / 1000.0 if t > 1e12 else t
    return None

def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat() if t else None

# ---------- A: verify gate_veto on state.snapshot ----------
print("=== A: gate_veto / memory_veto sample rows ===")
shown = 0
for p in sorted(glob.glob(os.path.join(TRAJ, 'dofus-0*.jsonl'))) + [os.path.join(TRAJ, 'default.jsonl')]:
    with open(p, 'r', encoding='utf-8', errors='replace') as f:
        for l in f:
            try: d = json.loads(l)
            except Exception: continue
            if d.get('kind') != 'step': continue
            m = d.get('meta') or {}
            if m.get('gate_veto') or m.get('memory_veto'):
                r = (d.get('result') or {})
                resp = r.get('response') or {}
                inner = resp.get('result') or {}
                print(' ', os.path.basename(p), d.get('tool'), 'ok=', d.get('ok'),
                      'inner_ok=', inner.get('ok'), 'err=', str(d.get('error'))[:80],
                      'meta=', json.dumps(m, ensure_ascii=False)[:300])
                shown += 1
                if shown >= 6: break
    if shown >= 6: break

# ---------- B: deep dive on longest streaks ----------
print("\n=== B: longest state.snapshot streak in default.jsonl ===")
p = os.path.join(TRAJ, 'default.jsonl')
run = []
best = []
with open(p, 'r', encoding='utf-8', errors='replace') as f:
    for l in f:
        try: d = json.loads(l)
        except Exception: continue
        if d.get('kind') != 'step': continue
        if d.get('tool') == 'state.snapshot':
            run.append(d)
        else:
            if len(run) > len(best): best = run
            run = []
if len(run) > len(best): best = run
print('streak len', len(best))
if best:
    t0, t1 = ts_of(best[0]), ts_of(best[-1])
    print('window', iso(t0), '->', iso(t1), 'duration_s=', round(t1 - t0, 1))
    print('goals', dict(collections.Counter(d.get('goal') for d in best)))
    print('inner_ok', dict(collections.Counter(((d.get('result') or {}).get('response') or {}).get('result', {}).get('ok') for d in best)))
    print('observation_source', dict(collections.Counter(d.get('observation_source') for d in best)))
    ages = [d.get('observation_age_ms') for d in best if isinstance(d.get('observation_age_ms'), (int, float))]
    if ages: print('obs_age_ms min/med/max', min(ages), sorted(ages)[len(ages)//2], max(ages))
    # did the world move at all during the streak?
    def s(o):
        po = (o or {}).get('position') or {}
        return (po.get('map_id'), po.get('cell'))
    print('distinct pre-obs (map,cell) during streak:', len(set(s(d.get('observation')) for d in best)))
    print('sample:', list(set(s(d.get('observation')) for d in best))[:6])
    print('first row meta:', json.dumps(best[0].get('meta'), ensure_ascii=False)[:200])
    print('inner errors:', dict(collections.Counter(str((((d.get('result') or {}).get('response') or {}).get('result') or {}).get('error'))[:100] for d in best).most_common(5)))

# ---------- C: objectives_completed / quests_finished per file trajectory ----------
print("\n=== C: objective counter trajectories (from step observations) ===")
for p in sorted(glob.glob(os.path.join(TRAJ, '*.jsonl'))):
    n = os.path.basename(p)
    if n.startswith('play_'): continue
    seq = []
    with open(p, 'r', encoding='utf-8', errors='replace') as f:
        for l in f:
            try: d = json.loads(l)
            except Exception: continue
            if d.get('kind') not in ('step', 'observation'): continue
            o = d.get('observation') or {}
            oc, qf = o.get('objectives_completed'), o.get('quests_finished')
            so = o.get('step_objectives')
            dn = sum(1 for x in so if isinstance(x, dict) and x.get('done')) if isinstance(so, list) else None
            tot = len(so) if isinstance(so, list) else None
            if oc is not None or qf is not None:
                seq.append((ts_of(d), oc, qf, dn, tot))
    if not seq: continue
    ocs = [x[1] for x in seq if isinstance(x[1], int)]
    qfs = [x[2] for x in seq if isinstance(x[2], int)]
    dns = [x[3] for x in seq if isinstance(x[3], int)]
    print(f"{n:46s} n={len(seq):5d} obj_completed first={ocs[0] if ocs else None} last={ocs[-1] if ocs else None} max={max(ocs) if ocs else None} | quests_finished first={qfs[0] if qfs else None} last={qfs[-1] if qfs else None} max={max(qfs) if qfs else None} | step_obj_done max={max(dns) if dns else None}")

# ---------- D: play_* fights + console errors ----------
print("\n=== D: play_* client recorder census ===")
fs = collections.Counter(); fe = collections.Counter()
fs_recent = 0; fe_recent = 0
console_lvl = collections.Counter()
console_txt = collections.Counter()
err_like = collections.Counter()
per_file_fight = collections.defaultdict(lambda: [0, 0])
xp_gain = []
lvl = collections.Counter()
pat = re.compile(r'(cannot|can\'t|not allowed|impossible|guest|level|too low|refus|forbidden|invalid|error|failed|impossible|interdit|impossible|vous ne pouvez|niveau)', re.I)
rows_total = 0
for p in sorted(glob.glob(os.path.join(TRAJ, 'play_*.jsonl'))):
    n = os.path.basename(p)
    with open(p, 'r', encoding='utf-8', errors='replace') as f:
        for l in f:
            l = l.strip()
            if not l: continue
            try: d = json.loads(l)
            except Exception: continue
            rows_total += 1
            e = d.get('event') or {}
            k = e.get('kind')
            t = ts_of(d)
            rec = t is not None and t >= CUTOFF
            if k == 'fight_start':
                fs[n] += 1; per_file_fight[n][0] += 1
                if rec: fs_recent += 1
            elif k == 'fight_end':
                fe[n] += 1; per_file_fight[n][1] += 1
                if rec: fe_recent += 1
                st = e.get('state') or {}
                if st.get('level') is not None: lvl[st['level']] += 1
            elif k == 'console':
                console_lvl[e.get('level')] += 1
                tx = (e.get('text') or '')
                if e.get('level') in ('error', 'warn'):
                    console_txt[tx[:160]] += 1
                if pat.search(tx) and len(tx) < 400:
                    err_like[tx[:160]] += 1
print('play rows total', rows_total)
print('fight_start total', sum(fs.values()), 'recent', fs_recent)
print('fight_end   total', sum(fe.values()), 'recent', fe_recent)
print('per file [start,end]:', {k: v for k, v in sorted(per_file_fight.items())})
print('console levels:', dict(console_lvl))
print('level at fight_end hist:', dict(sorted(lvl.items())[:25]))
print('\nTOP console error/warn texts:')
for t, c in console_txt.most_common(25): print(f'  {c:6d}  {t}')
print('\nTOP refusal/error-like console texts:')
for t, c in err_like.most_common(35): print(f'  {c:6d}  {t}')

# ---------- E: in-fight policy decisions ----------
print("\n=== E: live_policy_decisions.jsonl (in-fight AI) ===")
p = os.path.join(ROOT, 'live_policy_decisions.jsonl')
acts = collections.Counter(); same = collections.Counter(); oks = collections.Counter()
n = 0; tmin = None; tmax = None
errs = collections.Counter()
with open(p, 'r', encoding='utf-8', errors='replace') as f:
    for l in f:
        try: d = json.loads(l)
        except Exception: continue
        n += 1
        t = ts_of(d)
        if t:
            tmin = t if tmin is None or t < tmin else tmin
            tmax = t if tmax is None or t > tmax else tmax
        dec = d.get('decision') or {}
        oks[dec.get('ok')] += 1
        a = (dec.get('action') or {})
        acts[a.get('type')] += 1
        same[dec.get('same')] += 1
        if dec.get('error'): errs[str(dec['error'])[:140]] += 1
print('rows', n, 'window', iso(tmin), '->', iso(tmax))
print('ok:', dict(oks))
print('action types:', dict(acts.most_common(20)))
print('same-counter hist (repeat identical decision):', dict(sorted(same.items(), key=lambda x: (x[0] is None, x[0]))[:25]))
print('errors:', dict(errs.most_common(15)))