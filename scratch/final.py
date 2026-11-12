import json, os, glob, collections, datetime

TRAJ = r'C:\Users\sao\Documents\dofus-emu\dofus-network\trajectories'
dec = json.JSONDecoder()
GMAX = 1794360732.410
CUTOFF = GMAX - 7 * 86400
VOLATILE = {'cycle', 'cycle_ms', 'ts', 'at', '_t', 'nonce', 'request_id', 'id', 'timeout_ms', 'wait_ms'}
READONLY = {'state.snapshot', 'quest.list', 'inv.list', 'safe.stop'}

def ts_of(o):
    t = o.get('ts') or o.get('at') or o.get('time')
    if isinstance(t, (int, float)):
        return t / 1000.0 if t > 1e12 else t
    return None

def norm_args(a):
    if not isinstance(a, dict): return json.dumps(a, sort_keys=True)
    return json.dumps({k: v for k, v in sorted(a.items()) if k not in VOLATILE}, sort_keys=True)

def sig(o):
    if not isinstance(o, dict): return None
    pos = o.get('position') or {}; hp = o.get('hp') or {}
    co = o.get('current_objective') or {}
    so = o.get('step_objectives')
    done = sum(1 for x in so if isinstance(x, dict) and x.get('done')) if isinstance(so, list) else None
    qa = o.get('quests_active')
    qas = tuple(sorted((str(k), v) for k, v in qa.items())) if isinstance(qa, dict) else None
    ro = o.get('ready_objectives')
    return (pos.get('map_id'), pos.get('cell'), o.get('xp'), o.get('kamas'), hp.get('life'),
            o.get('in_fight'), o.get('quests_finished'), o.get('objectives_completed'), qas,
            co.get('objective_id') if isinstance(co, dict) else None, done,
            len(ro) if isinstance(ro, list) else None)

# malformed-line accounting
LINE_STATS = collections.Counter()
def rows(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            LINE_STATS['lines'] += 1
            pos = 0; got = 0
            while pos < len(line):
                try: o, end = dec.raw_decode(line, pos)
                except Exception:
                    LINE_STATS['unrecoverable_lines'] += 1; break
                if isinstance(o, dict): got += 1; yield o
                else: LINE_STATS['nondict'] += 1
                pos = end
                while pos < len(line) and line[pos] in ' \t\r\n': pos += 1
                if pos < len(line) and line[pos] != '{':
                    LINE_STATS['unrecoverable_lines'] += 1; break
            if got > 1: LINE_STATS['concatenated_lines'] += 1; LINE_STATS['extra_objs_recovered'] += got - 1
            if got == 0: LINE_STATS['empty_or_bad'] += 1

class A:
    def __init__(s):
        s.n=0; s.v=collections.Counter(); s.tools=collections.Counter()
        s.ro=0; s.act=0; s.act_v=collections.Counter(); s.ro_v=collections.Counter()
        s.rep=0; s.rebad=0; s.streaks=[]; s.errs=collections.Counter(); s.oerr=collections.Counter()
        s.objup=0; s.qfup=0; s.mapchg=0; s.nosnap=0; s.shadowveto=0
        s.oc=collections.Counter(); s.qf=collections.Counter()

W = {'recent': collections.defaultdict(A), 'all': collections.defaultdict(A)}
T = {'recent': A(), 'all': A()}

for p in sorted(glob.glob(os.path.join(TRAJ, '*.jsonl'))):
    fn = os.path.basename(p)
    if fn.startswith('play_'): continue
    prev=None; prevbad=False; sk=None; sl=0
    for o in rows(p):
        if o.get('kind') != 'step': continue
        t = ts_of(o)
        wins = ['all'] + (['recent'] if t and t >= CUTOFF else [])
        tool=o.get('tool'); key=(tool, norm_args(o.get('args')))
        res=o.get('result') if isinstance(o.get('result'),dict) else {}
        resp=res.get('response') if isinstance(res.get('response'),dict) else {}
        inner=resp.get('result') if isinstance(resp.get('result'),dict) else {}
        iok=inner.get('ok'); ierr=inner.get('error')
        if ierr is None and isinstance(inner.get('data'),dict): ierr=inner['data'].get('error')
        pre=o.get('observation'); snap=res.get('snapshot')
        sp,sq=sig(pre),sig(snap)
        chg = (sp!=sq) if (sp is not None and sq is not None) else None
        meta=o.get('meta') if isinstance(o.get('meta'),dict) else {}
        # CORRECTED: gate_veto/memory_veto are shadow annotations; row still dispatched.
        if tool in (None,'-',''): v='blocked'
        elif o.get('ok') is False or iok is False: v='failed'
        elif iok is True:
            v = 'advanced' if chg is True else ('noop' if chg is False else 'ok_nosnapshot')
        else: v='ok_noinner' if o.get('ok') else 'unknown'
        ro = tool in READONLY
        for w in wins:
            for a in (W[w][fn], T[w]):
                a.n+=1; a.v[v]+=1; a.tools[tool]+=1
                if ro: a.ro+=1; a.ro_v[v]+=1
                else: a.act+=1; a.act_v[v]+=1
                if ierr: a.errs[str(ierr)[:180]]+=1
                if o.get('error'): a.oerr[str(o['error'])[:180]]+=1
                if meta.get('gate_veto') or meta.get('memory_veto'): a.shadowveto+=1
                if sp is not None and sq is not None:
                    if isinstance(sp[7],int) and isinstance(sq[7],int) and sq[7]>sp[7]: a.objup+=1
                    if isinstance(sp[6],int) and isinstance(sq[6],int) and sq[6]>sp[6]: a.qfup+=1
                    if sp[0]!=sq[0] and sq[0] is not None: a.mapchg+=1
                else: a.nosnap+=1
                if isinstance(pre,dict):
                    if isinstance(pre.get('objectives_completed'),int): a.oc[pre['objectives_completed']]+=1
                    if isinstance(pre.get('quests_finished'),int): a.qf[pre['quests_finished']]+=1
                if key==prev:
                    a.rep+=1
                    if prevbad: a.rebad+=1
        if key==sk: sl+=1
        else:
            if sl>=3 and sk is not None:
                T['all'].streaks.append((sl,sk[0],sk[1][:120],fn))
                if t and t>=CUTOFF: T['recent'].streaks.append((sl,sk[0],sk[1][:120],fn))
            sk=key; sl=1
        prev=key; prevbad = v in ('noop','failed','blocked')
    if sl>=3 and sk is not None: T['all'].streaks.append((sl,sk[0],sk[1][:120],fn))

def pc(n,d): return f"{100.0*n/d:.1f}%" if d else "n/a"
for w in ('recent','all'):
    print("="*80); print("WINDOW", w)
    for label,a in [('OVERALL',T[w])]+[(f,W[w][f]) for f in sorted(W[w])]:
        if not a.n: continue
        print(f"\n-- {label} [{w}] steps={a.n}")
        print("   verdict:",dict(a.v.most_common()))
        print(f"   advanced/all={pc(a.v['advanced'],a.n)}  ACTION-only n={a.act} advanced={a.act_v['advanced']} ({pc(a.act_v['advanced'],a.act)}) noop={a.act_v['noop']} failed={a.act_v['failed']}")
        print(f"   READONLY n={a.ro} ({pc(a.ro,a.n)}) {dict(a.ro_v.most_common())}")
        print(f"   repeat={a.rep} ({pc(a.rep,a.n)}) reissue_after_bad={a.rebad} ({pc(a.rebad,a.n)}) shadow_veto={a.shadowveto} nosnap={a.nosnap} mapchg={a.mapchg} objup={a.objup} qfup={a.qfup}")
        print("   tools:",dict(a.tools.most_common(12)))
        if label=='OVERALL':
            print("   top inner errors:",dict(a.errs.most_common(12)))
    print("\n  streaks>=3:",len(T[w].streaks),"steps inside:",sum(x[0] for x in T[w].streaks))
    for L,tl,ar,fn in sorted(T[w].streaks,reverse=True)[:12]:
        print(f"    {L:5d}  {tl} {ar}  [{fn}]")

print("\n=== LINE / PARSE ACCOUNTING (non-play logs) ===", dict(LINE_STATS))

# play_* line accounting + fight win/loss
PS=collections.Counter(); hp0=0; hppos=0; deltas=[]
recent_f=[]
for p in sorted(glob.glob(os.path.join(TRAJ,'play_*.jsonl'))):
    fn=os.path.basename(p)
    with open(p,'r',encoding='utf-8',errors='replace') as f:
        for line in f:
            line=line.strip()
            if not line: continue
            PS['lines']+=1
            pos=0; got=0
            while pos<len(line):
                try: o,end=dec.raw_decode(line,pos)
                except Exception: PS['unrecoverable_lines']+=1; break
                got+=1
                e=o.get('event') or {}
                if e.get('kind')=='fight_end':
                    st=e.get('state') or {}
                    h=st.get('hp')
                    if isinstance(h,(int,float)):
                        if h<=0: hp0+=1
                        else: hppos+=1
                    t=ts_of(o)
                    if t and t>=CUTOFF: recent_f.append((fn,st.get('level'),h,st.get('xp')))
                pos=end
                while pos<len(line) and line[pos] in ' \t\r\n': pos+=1
                if pos<len(line) and line[pos]!='{': PS['unrecoverable_lines']+=1; break
            if got>1: PS['concatenated_lines']+=1; PS['extra_objs_recovered']+=got-1
print("=== play_* LINE ACCOUNTING ===",dict(PS))
print("fight_end hp<=0 (died):",hp0," hp>0 (survived):",hppos)
print("recent fight_end sample:",recent_f[:15])

# live/fights.jsonl + summary
for rel in ('live/fights.jsonl','live/summary.json'):
    p=os.path.join(TRAJ,rel)
    if not os.path.exists(p): continue
    print("\n===",rel)
    if rel.endswith('summary.json'):
        print(open(p,encoding='utf-8',errors='replace').read()[:900]); continue
    c=collections.Counter(); n=0; keys=collections.Counter()
    for o in rows(p):
        n+=1
        for k in o: keys[k]+=1
        c[o.get('kind') or o.get('type')]+=1
        if 'won' in o: c['won='+str(o['won'])]+=1
        if 'outcome' in o: c['outcome='+str(o['outcome'])]+=1
    print('rows',n,'kinds',dict(c.most_common(12)),'keys',dict(keys.most_common(20)))