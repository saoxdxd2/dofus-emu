import json, os, glob, collections, datetime, sys

TRAJ = r'C:\Users\sao\Documents\dofus-emu\dofus-network\trajectories'
ROOT_TRAJ = r'C:\Users\sao\Documents\dofus-emu\trajectories'
dec = json.JSONDecoder()

VOLATILE = {'cycle', 'cycle_ms', 'ts', 'at', '_t', 'nonce', 'request_id', 'id', 'timeout_ms', 'wait_ms'}

def rows(path):
    """Stream JSON objects, recovering concatenated records. Returns (obj, ok_flag)."""
    nbad = 0
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            pos = 0
            got = 0
            while pos < len(line):
                try:
                    o, end = dec.raw_decode(line, pos)
                except Exception:
                    nbad += 1
                    break
                if isinstance(o, dict):
                    got += 1
                    yield o
                else:
                    nbad += 1
                pos = end
                while pos < len(line) and line[pos] in ' \t\r\n':
                    pos += 1
                if pos < len(line) and line[pos] != '{':
                    nbad += 1
                    break
            if got == 0:
                pass
    BAD[os.path.basename(path)] += nbad

BAD = collections.Counter()

def norm_args(a):
    if not isinstance(a, dict):
        return json.dumps(a, sort_keys=True, ensure_ascii=False)
    return json.dumps({k: v for k, v in sorted(a.items()) if k not in VOLATILE},
                      sort_keys=True, ensure_ascii=False)

def sig(o):
    """World signature from an observation/snapshot dict."""
    if not isinstance(o, dict):
        return None
    pos = o.get('position') or {}
    hp = o.get('hp') or {}
    co = o.get('current_objective') or {}
    so = o.get('step_objectives')
    done = None
    if isinstance(so, list):
        done = sum(1 for x in so if isinstance(x, dict) and x.get('done'))
    qa = o.get('quests_active')
    qas = tuple(sorted((str(k), v) for k, v in qa.items())) if isinstance(qa, dict) else None
    ro = o.get('ready_objectives')
    return (
        pos.get('map_id'), pos.get('cell'),
        o.get('xp'), o.get('kamas'), hp.get('life'),
        o.get('in_fight'), o.get('quests_finished'),
        o.get('objectives_completed'), qas,
        co.get('objective_id') if isinstance(co, dict) else None,
        done, len(ro) if isinstance(ro, list) else None,
    )

def dialog_sig(o):
    if not isinstance(o, dict):
        return None
    d = o.get('dialog') or {}
    return (o.get('in_dialog'), d.get('open'), d.get('npc_id'), (d.get('message') or '')[:80])

READONLY = {'state.snapshot', 'quest.list', 'inv.list', 'quest.state', 'map.info',
            'fight.estimate', 'inv.inspect', 'shop.list', 'quest.catalog', 'state.get',
            'observe', 'noop', '-', None, 'safe.stop'}

# ---------------- accumulators ----------------
class Acc:
    def __init__(self):
        self.steps = 0
        self.obs = 0
        self.verdict = collections.Counter()
        self.tools = collections.Counter()
        self.tool_outcome = collections.defaultdict(collections.Counter)
        self.outer_ok = collections.Counter()
        self.inner_ok = collections.Counter()
        self.errors = collections.Counter()
        self.inner_errors = collections.Counter()
        self.readonly = 0
        self.action = 0
        self.repeat_steps = 0          # step identical to immediately previous step
        self.streaks = []              # (len, tool, args)
        self.reissue_after_bad = 0     # identical re-issue right after noop/failed
        self.reissue_after_bad_tools = collections.Counter()
        self.gate_veto = 0
        self.memory_veto = 0
        self.gate_streak_hist = collections.Counter()
        self.would_veto = 0
        self.obj_completed_vals = collections.Counter()
        self.quests_finished_vals = collections.Counter()
        self.step_obj_done_total = 0
        self.obj_advanced_steps = 0    # steps where objectives_completed increased
        self.quest_finished_steps = 0
        self.map_change_steps = 0
        self.dialog_only_steps = 0
        self.no_snapshot = 0
        self.first_ts = None
        self.last_ts = None
        self.goals = collections.Counter()
        self.latency = []

ALL = {'recent': collections.defaultdict(Acc), 'all': collections.defaultdict(Acc)}
TOT = {'recent': Acc(), 'all': Acc()}

def get_acc(win, fname):
    return ALL[win][fname]

# session_end / fights / verdict rows (global, not per-window-split by file)
SESSION_ENDS = []
VERDICT_ROWS = []
FIGHT_STARTS = []
FIGHT_OUTCOMES = []
DIALOG_ROWS = []
WAIT_ROWS = []
DECISION_ROWS = []
LIVE_TURNS = []

INSTANCE_FILES = [p for p in sorted(glob.glob(os.path.join(TRAJ, '*.jsonl')))
                  if not os.path.basename(p).startswith('play_')]

# ---- pass 1: find global max ts to anchor the 7-day window ----
GMAX = 0.0
for p in INSTANCE_FILES + sorted(glob.glob(os.path.join(TRAJ, 'play_*.jsonl'))):
    with open(p, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get('ts') or o.get('at') or o.get('time')
            if isinstance(t, (int, float)):
                if t > 1e12: t = t / 1000.0
                if t > GMAX: GMAX = t
CUTOFF = GMAX - 7 * 86400
print('GLOBAL MAX TS:', datetime.datetime.fromtimestamp(GMAX, datetime.timezone.utc).isoformat())
print('7-DAY CUTOFF :', datetime.datetime.fromtimestamp(CUTOFF, datetime.timezone.utc).isoformat())
print()

def ts_of(o):
    t = o.get('ts') or o.get('at') or o.get('time') or o.get('when')
    if isinstance(t, (int, float)):
        return t / 1000.0 if t > 1e12 else t
    return None

# ---- pass 2: instance logs ----
for p in INSTANCE_FILES:
    fname = os.path.basename(p)
    prev_key = None
    prev_bad = False
    streak_len = 0
    streak_key = None
    for o in rows(p):
        t = ts_of(o)
        k = o.get('kind')
        if k == 'session_end':
            SESSION_ENDS.append((fname, o)); continue
        if 'verdict' in o and 'tool' in o:
            VERDICT_ROWS.append((fname, o)); continue
        if k == 'fight_start':
            FIGHT_STARTS.append((fname, o)); continue
        if k == 'fight_outcome':
            FIGHT_OUTCOMES.append((fname, o)); continue
        if k == 'dialog':
            DIALOG_ROWS.append((fname, o)); continue
        if k == 'wait':
            WAIT_ROWS.append((fname, o)); continue
        if k == 'decision':
            DECISION_ROWS.append((fname, o)); continue
        if k == 'live_turn':
            LIVE_TURNS.append((fname, o)); continue
        if k == 'observation':
            for w in (('recent',) if (t and t >= CUTOFF) else ()) + ('all',):
                get_acc(w, fname).obs += 1
                TOT[w].obs += 1
            continue
        if k != 'step':
            continue

        wins = ['all']
        if t and t >= CUTOFF: wins.append('recent')
        tool = o.get('tool')
        args = o.get('args')
        key = (tool, norm_args(args))
        outer_ok = o.get('ok')
        res = o.get('result') if isinstance(o.get('result'), dict) else {}
        resp = res.get('response') if isinstance(res.get('response'), dict) else {}
        inner = resp.get('result') if isinstance(resp.get('result'), dict) else {}
        inner_ok = inner.get('ok') if isinstance(inner, dict) else None
        inner_err = inner.get('error') if isinstance(inner, dict) else None
        if inner_err is None and isinstance(inner.get('data'), dict):
            inner_err = inner['data'].get('error')
        snap = res.get('snapshot')
        pre = o.get('observation')
        meta = o.get('meta') if isinstance(o.get('meta'), dict) else {}

        s_pre, s_post = sig(pre), sig(snap)
        changed = None
        dchg = None
        if s_pre is not None and s_post is not None:
            changed = s_pre != s_post
            dchg = dialog_sig(pre) != dialog_sig(snap)
        else:
            changed = None

        # derived verdict
        if meta.get('gate_veto') or meta.get('memory_veto') or tool in (None, '-', ''):
            v = 'blocked'
        elif outer_ok is False or inner_ok is False:
            v = 'failed'
        elif inner_ok is True:
            if changed is True:
                v = 'advanced'
            elif changed is False:
                v = 'noop'
            else:
                v = 'ok_nosnapshot'
        elif inner_ok is None:
            v = 'ok_noinner' if outer_ok else 'unknown'
        else:
            v = 'unknown'

        ro = tool in READONLY
        for w in wins:
            for a in (get_acc(w, fname), TOT[w]):
                a.steps += 1
                a.verdict[v] += 1
                a.tools[tool] += 1
                a.tool_outcome[tool][v] += 1
                a.outer_ok[str(outer_ok)] += 1
                a.inner_ok[str(inner_ok)] += 1
                if ro: a.readonly += 1
                else: a.action += 1
                if o.get('error'): a.errors[str(o['error'])[:200]] += 1
                if inner_err: a.inner_errors[str(inner_err)[:200]] += 1
                if meta.get('gate_veto'): a.gate_veto += 1
                if meta.get('memory_veto'): a.memory_veto += 1
                g = meta.get('gate') or {}
                if isinstance(g, dict):
                    st = g.get('streak')
                    if isinstance(st, int): a.gate_streak_hist[min(st, 20)] += 1
                    if g.get('would_veto'): a.would_veto += 1
                if isinstance(pre, dict):
                    oc = pre.get('objectives_completed')
                    if isinstance(oc, int): a.obj_completed_vals[oc] += 1
                    qf = pre.get('quests_finished')
                    if isinstance(qf, int): a.quests_finished_vals[qf] += 1
                if s_pre is not None and s_post is not None:
                    # indices: 7=objectives_completed, 6=quests_finished, 0=map_id
                    if isinstance(s_pre[7], int) and isinstance(s_post[7], int) and s_post[7] > s_pre[7]:
                        a.obj_advanced_steps += 1
                    if isinstance(s_pre[6], int) and isinstance(s_post[6], int) and s_post[6] > s_pre[6]:
                        a.quest_finished_steps += 1
                    if s_pre[0] != s_post[0] and s_post[0] is not None:
                        a.map_change_steps += 1
                    if not changed and dchg:
                        a.dialog_only_steps += 1
                else:
                    a.no_snapshot += 1
                if o.get('goal'): a.goals[str(o['goal'])[:80]] += 1
                lm = o.get('latency_ms')
                if isinstance(lm, (int, float)): a.latency.append(lm)
                if t:
                    if a.first_ts is None or t < a.first_ts: a.first_ts = t
                    if a.last_ts is None or t > a.last_ts: a.last_ts = t
                # repeat pathology
                if key == prev_key:
                    a.repeat_steps += 1
                    if prev_bad:
                        a.reissue_after_bad += 1
                        a.reissue_after_bad_tools[tool] += 1
        # streak tracking (all-time stream, per file)
        if key == streak_key:
            streak_len += 1
        else:
            if streak_len >= 3 and streak_key is not None:
                TOT['all'].streaks.append((streak_len, streak_key[0], streak_key[1][:160], fname))
                if t and t >= CUTOFF:
                    TOT['recent'].streaks.append((streak_len, streak_key[0], streak_key[1][:160], fname))
            streak_key = key; streak_len = 1
        prev_key = key
        prev_bad = v in ('noop', 'failed', 'blocked')
    if streak_len >= 3 and streak_key is not None:
        TOT['all'].streaks.append((streak_len, streak_key[0], streak_key[1][:160], fname))

# ---------------- report ----------------
def pct(n, d):
    return f"{100.0*n/d:.1f}%" if d else "n/a"

def dump_acc(label, a):
    print(f"\n### {label}: steps={a.steps} observations={a.obs}")
    if not a.steps:
        print("  (no steps)"); return
    print("  derived verdict:", dict(a.verdict.most_common()))
    print("  advanced frac of all steps:", pct(a.verdict['advanced'], a.steps))
    ex = a.steps - a.verdict['blocked']
    print("  advanced frac of non-blocked:", pct(a.verdict['advanced'], ex))
    print("  outer ok:", dict(a.outer_ok), " inner ok:", dict(a.inner_ok))
    print(f"  readonly={a.readonly} ({pct(a.readonly,a.steps)})  action={a.action} ({pct(a.action,a.steps)})")
    print(f"  repeat-identical steps={a.repeat_steps} ({pct(a.repeat_steps,a.steps)})  reissue-after-bad={a.reissue_after_bad} ({pct(a.reissue_after_bad,a.steps)})")
    print("  reissue-after-bad by tool:", dict(a.reissue_after_bad_tools.most_common(10)))
    print(f"  gate_veto={a.gate_veto} memory_veto={a.memory_veto} would_veto={a.would_veto}")
    print("  gate streak hist (capped20):", dict(sorted(a.gate_streak_hist.items())))
    print(f"  no comparable snapshot={a.no_snapshot}  map_change_steps={a.map_change_steps}  dialog_only_steps={a.dialog_only_steps}")
    print(f"  steps where objectives_completed increased={a.obj_advanced_steps}  quests_finished increased={a.quest_finished_steps}")
    print("  objectives_completed value hist:", dict(sorted(a.obj_completed_vals.items())[:20]))
    print("  quests_finished value hist:", dict(sorted(a.quests_finished_vals.items())[:25]))
    print("  goals:", dict(a.goals.most_common(6)))
    if a.latency:
        ls = sorted(a.latency)
        print(f"  latency_ms n={len(ls)} p50={ls[len(ls)//2]:.1f} p95={ls[int(len(ls)*0.95)]:.1f} max={ls[-1]:.1f}")
    print("  TOP TOOLS:")
    for tl, c in a.tools.most_common(25):
        oc = a.tool_outcome[tl]
        print(f"    {c:7d} {pct(c,a.steps):>6s}  {str(tl):22s} {'RO' if tl in READONLY else 'ACT'}  {dict(oc.most_common())}")
    print("  TOP INNER (client) ERRORS:")
    for e, c in a.inner_errors.most_common(20):
        print(f"    {c:6d}  {e}")
    print("  TOP OUTER ERRORS:")
    for e, c in a.errors.most_common(12):
        print(f"    {c:6d}  {e}")

for w in ('recent', 'all'):
    print("\n" + "=" * 90)
    print("WINDOW:", w)
    print("=" * 90)
    dump_acc('OVERALL ' + w, TOT[w])
    for fname in sorted(ALL[w]):
        dump_acc(fname + ' [' + w + ']', ALL[w][fname])
    print("\n  LONGEST IDENTICAL-CALL STREAKS (>=3):")
    for L, tl, ar, fn in sorted(TOT[w].streaks, reverse=True)[:20]:
        print(f"    len={L:4d} {tl} {ar}  [{fn}]")
    print("  total streaks>=3:", len(TOT[w].streaks),
          " steps inside them:", sum(x[0] for x in TOT[w].streaks))

print("\n" + "=" * 90)
print("PARSE FAILURES (unrecoverable):", dict(BAD), "TOTAL", sum(BAD.values()))

print("\n=== SESSION_END ROWS ===", len(SESSION_ENDS))
rc = collections.Counter()
agg_steps = 0
agg_verd = collections.Counter()
for fn, o in SESSION_ENDS:
    rc[o.get('reason')] += 1
    agg_steps += o.get('steps') or 0
    for k, v in (o.get('verdicts') or {}).items(): agg_verd[k] += v
print("reasons:", dict(rc.most_common()))
print("sum steps:", agg_steps, "sum verdicts:", dict(agg_verd.most_common()))
print("instances:", dict(collections.Counter(o.get('instance') for _, o in SESSION_ENDS)))
print("elapsed_s:", sorted(o.get('elapsed_s') for _, o in SESSION_ENDS))
print("recent session_ends:", sum(1 for _, o in SESSION_ENDS if (ts_of(o) or 0) >= CUTOFF))
for fn, o in SESSION_ENDS:
    print("  ", fn, datetime.datetime.fromtimestamp(ts_of(o), datetime.timezone.utc).isoformat(),
          o.get('instance'), o.get('session'), o.get('reason'), 'steps=', o.get('steps'),
          'verdicts=', o.get('verdicts'), 'waits=', o.get('waits'), 'elapsed=', o.get('elapsed_s'))

print("\n=== EXPLICIT VERDICT ROWS ===", len(VERDICT_ROWS))
print("verdict:", dict(collections.Counter(o.get('verdict') for _, o in VERDICT_ROWS).most_common()))
print("recent:", dict(collections.Counter(o.get('verdict') for _, o in VERDICT_ROWS if (ts_of(o) or 0) >= CUTOFF)))
print("by tool:", dict(collections.Counter(o.get('tool') for _, o in VERDICT_ROWS).most_common(15)))
print("error texts:", dict(collections.Counter(str(o.get('error'))[:120] for _, o in VERDICT_ROWS if o.get('error')).most_common(15)))
print("notes:", dict(collections.Counter(str(o.get('note'))[:120] for _, o in VERDICT_ROWS if o.get('note')).most_common(15)))

print("\n=== FIGHT_START / FIGHT_OUTCOME (agent logs) ===")
print("fight_start:", len(FIGHT_STARTS), " fight_outcome:", len(FIGHT_OUTCOMES))
print("won:", dict(collections.Counter(o.get('won') for _, o in FIGHT_OUTCOMES)))
print("status:", dict(collections.Counter(o.get('status') for _, o in FIGHT_OUTCOMES)))
print("recent starts:", sum(1 for _, o in FIGHT_STARTS if (ts_of(o) or 0) >= CUTOFF),
      "recent outcomes:", sum(1 for _, o in FIGHT_OUTCOMES if (ts_of(o) or 0) >= CUTOFF))
for fn, o in FIGHT_OUTCOMES:
    print("  ", fn, datetime.datetime.fromtimestamp(ts_of(o), datetime.timezone.utc).isoformat() if ts_of(o) else None,
          'won=', o.get('won'), 'turns=', o.get('turns'), 'hp_loss=', o.get('hp_loss'),
          'started_by=', o.get('started_by'), 'group=', o.get('group_id'))

print("\n=== WAIT ROWS ===", len(WAIT_ROWS))
print("reasons:", dict(collections.Counter(str(o.get('reason'))[:120] for _, o in WAIT_ROWS).most_common(15)))
print("consecutive hist:", dict(sorted(collections.Counter(o.get('consecutive') for _, o in WAIT_ROWS).items(), key=lambda x: (x[0] is None, x[0]))))
print("\n=== DECISION ROWS ===", len(DECISION_ROWS))
print("action:", dict(collections.Counter(o.get('action') for _, o in DECISION_ROWS).most_common(15)))
print("cause:", dict(collections.Counter(o.get('cause') for _, o in DECISION_ROWS).most_common(15)))
print("\n=== DIALOG ROWS ===", len(DIALOG_ROWS))
print("outcome:", dict(collections.Counter(o.get('outcome') for _, o in DIALOG_ROWS).most_common()))
print("reason:", dict(collections.Counter(str(o.get('reason'))[:120] for _, o in DIALOG_ROWS).most_common(10)))
print("\n=== LIVE_TURNS ===", len(LIVE_TURNS))