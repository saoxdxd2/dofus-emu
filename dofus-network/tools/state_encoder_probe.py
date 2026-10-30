"""Does a learned state representation beat hand-built features, out of time?

The pipeline's incumbent is a hand-built vector plus a linear head. The alternative
is the one this project has proved once already (the command parser): a frozen
encoder pretrained on abundant unlabelled data, with a tiny head on the scarce
labels. Here the unlabelled task is the world model - given the state and the call,
predict which watched keys change in the next observation - and the scarce label is
the call's own claim.

Both representations get the same probe and the same temporal cut, so the question
is about the representation, not the classifier. The counter (`repeat_streak >= 2`)
is reported as the floor to beat at the operating point (G51/G54).

    python tools/state_encoder_probe.py --epochs 120
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.state_features import (ENTITY_DIM, GLOBAL_DIM, entity_vectors,  # noqa: E402
                                  global_vector, world_delta)
from agent.observe import flatten  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TRAJ = REPO_ROOT / "trajectories"
MAX_ENT = 20
ARG_KINDS = ("map_id", "direction", "cell", "element_id", "quest_id", "monster")


def load_steps() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in sorted(TRAJ.glob("dofus-*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("tool") and isinstance(row.get("observation"), dict):
                out.append(row)
    out.sort(key=lambda r: (r.get("instance") or "", float(r.get("ts") or 0)))
    return out


def segments(steps: List[Dict[str, Any]], gap_s: float = 120.0) -> List[List[Dict[str, Any]]]:
    out: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    last_ts = None
    for r in steps:
        ts = float(r.get("ts") or 0)
        if cur and (r.get("instance") != cur[-1].get("instance") or ts - (last_ts or ts) > gap_s):
            out.append(cur)
            cur = []
        cur.append(r)
        last_ts = ts
    if cur:
        out.append(cur)
    return out


def pairs(seg: List[Dict[str, Any]]):
    for i in range(len(seg) - 1):
        yield i, seg[i], seg[i + 1]


def claim_label(tool: str, args: Dict[str, Any], before: Any, after: Any):
    try:
        from build_live_transitions import _claim_of
    except ImportError:
        from tools.build_live_transitions import _claim_of  # type: ignore
    return _claim_of(tool, args, before, after)


def build_dataset() -> Dict[str, Any]:
    steps = load_steps()
    segs = segments(steps)
    rules: Dict[str, int] = {}
    rows: List[Dict[str, Any]] = []
    tools = sorted({str(s.get("tool")) for s in steps})
    for seg in segs:
        for i, r, nxt in pairs(seg):
            tool = str(r.get("tool"))
            args = r.get("args") if isinstance(r.get("args"), dict) else {}
            claim, met = claim_label(tool, args, r.get("observation"), nxt.get("observation"))
            if met is None:
                continue
            rule = tool + "|" + json.dumps(args, sort_keys=True)
            streak = rules.get(rule, 0)
            day = _dt.datetime.fromtimestamp(
                float(r.get("ts") or 0), _dt.timezone.utc).date().isoformat()
            rows.append({
                "day": day, "ts": float(r.get("ts") or 0),
                "segment": "%s:%d" % (seg[0].get("instance"),
                                      int(float(seg[0].get("ts") or 0))),
                "tool": tool, "args": args, "claim_met": bool(met),
                "repeat_streak": streak,
                "obs": r.get("observation"), "next": nxt.get("observation"),
            })
            rules[rule] = 0 if met else streak + 1
    days = sorted({r["day"] for r in rows})
    test_days = set(days[-2:]) if len(days) > 2 else set(days[-1:])
    for r in rows:
        r["split"] = "test" if r["day"] in test_days else "train"
    return {"rows": rows, "tools": tools, "days": days, "test_days": sorted(test_days)}


def claim_kinds(rows: List[Dict[str, Any]], limit: int = 12) -> List[Tuple[Any, int]]:
    kinds: Counter = Counter()
    for r in rows:
        claim, met = claim_label(r["tool"], r["args"], r["obs"], r["next"])
        kinds[(r["tool"], claim, bool(met))] += 1
    return kinds.most_common(limit)


def split_rows(data: Dict[str, Any], mode: str):
    """Return (train, test, note).

    `days` is the honest fold across the whole log, but the claim vocabulary is
    not stationary: `nav.goto` on the early days only promised `step`, the `at:`
    form first appears on 2026-10-28, and the dialog claim means the opposite
    before and after the per-form fix. A model cannot transfer a claim kind it
    has never seen, so `days` measures vocabulary drift. `within` keeps one
    regime (the latest days) and cuts it by time, which is the fold that can
    compare representations.
    """
    rows = data["rows"]
    if mode == "days":
        return ([r for r in rows if r["split"] == "train"],
                [r for r in rows if r["split"] == "test"],
                "across days %s -> %s" % (sorted({r['day'] for r in rows if r['split'] == 'train'}),
                                          data["test_days"]))
    regime = sorted((r for r in rows if r["day"] in data["test_days"]),
                    key=lambda r: (r["day"], r["ts"]))
    if len(regime) < 300:
        return [], [], "regime too small (%d pairs)" % len(regime)
    cut = int(round(0.6 * len(regime)))
    while cut < len(regime) and regime[cut]["segment"] == regime[cut - 1]["segment"]:
        cut += 1
    return regime[:cut], regime[cut:], "within days %s, first 60%% -> last 40%%" % data["test_days"]


def state_tensors(rows: List[Dict[str, Any]]):
    g = np.zeros((len(rows), GLOBAL_DIM), dtype=np.float32)
    e = np.zeros((len(rows), MAX_ENT, ENTITY_DIM), dtype=np.float32)
    m = np.zeros((len(rows), MAX_ENT), dtype=np.float32)
    a = np.zeros((len(rows), len(tools_ref) + len(ARG_KINDS)), dtype=np.float32)
    d = np.zeros((len(rows), 10), dtype=np.float32)
    for i, r in enumerate(rows):
        obs = r["obs"]
        g[i] = global_vector(obs)
        ents = entity_vectors(obs)[:MAX_ENT]
        for j, vec in enumerate(ents):
            e[i, j] = vec
            m[i, j] = 1.0
        if r["tool"] in tools_ref:
            a[i, tools_ref.index(r["tool"])] = 1.0
        for k, key in enumerate(ARG_KINDS):
            if r["args"].get(key) is not None:
                a[i, len(tools_ref) + k] = 1.0
        d[i] = world_delta(obs, r.get("next"))
    return g, e, m, a, d


def pooled_baseline(g, e, m):
    count = m.sum(axis=1, keepdims=True)
    mean = (e * m[..., None]).sum(axis=1) / np.clip(count, 1, None)
    mx = (e + (m[..., None] - 1.0) * 1e6).max(axis=1)
    mx[m.sum(axis=1) == 0] = 0.0
    return np.concatenate([g, mean, mx, count / MAX_ENT], axis=1)


class Encoder:
    def __init__(self, hidden: int = 32, emb: int = 32) -> None:
        import torch
        from torch import nn
        self.torch = torch
        self.net = nn.Sequential(
            nn.Linear(ENTITY_DIM, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
        )
        self.head = nn.Sequential(
            nn.Linear(hidden + GLOBAL_DIM, 64), nn.ReLU(),
            nn.Linear(64, emb), nn.Tanh(),
        )
        self.pred = nn.Linear(emb + len(tools_ref) + len(ARG_KINDS), 10)

    def embed(self, g, e, m):
        t = self.torch
        ge = t.as_tensor(g)
        ee = t.as_tensor(e)
        me = t.as_tensor(m)
        h = self.net(ee) * me[..., None]
        count = me.sum(1, keepdim=True).clamp(min=1.0)
        pooled = h.sum(1) / count
        return self.head(t.cat([ge, pooled], dim=1))

    def fit_world_model(self, g, e, m, a, d, epochs: int = 120, lr: float = 1e-3,
                        weight=None):
        t = self.torch
        opt = t.optim.Adam(list(self.net.parameters()) + list(self.head.parameters())
                           + list(self.pred.parameters()), lr=lr)
        loss_fn = t.nn.functional.binary_cross_entropy_with_logits
        gt, et, mt, at, dt = (t.as_tensor(x) for x in (g, e, m, a, d))
        wt = t.as_tensor(np.ones(len(g), dtype=np.float32) if weight is None
                         else np.asarray(weight, dtype=np.float32))
        n = len(gt)
        for epoch in range(epochs):
            perm = t.randperm(n)
            total = 0.0
            for start in range(0, n, 256):
                idx = perm[start:start + 256]
                emb = self.embed(gt[idx].numpy(), et[idx].numpy(), mt[idx].numpy())
                logits = self.pred(t.cat([emb, at[idx]], dim=1))
                per = loss_fn(logits, dt[idx], reduction="none").mean(dim=1)
                w = wt[idx]
                loss = (per * w).sum() / w.sum().clamp(min=1e-6)
                opt.zero_grad()
                loss.backward()
                opt.step()
                total += float(loss.detach()) * float(w.sum())
            if epoch % 30 == 0 or epoch == epochs - 1:
                print("   epoch %3d  bce %.4f" % (epoch, total / max(1.0, float(wt.sum()))))


def auc(y, s):
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, s))


def seg_ci(rows, y, s, rounds=300, seed=0):
    by: Dict[str, List[int]] = {}
    for i, r in enumerate(rows):
        by.setdefault(r["segment"], []).append(i)
    segs = [v for v in by.values() if 0 < sum(y[i] for i in v) < len(v)]
    if len(segs) < 3:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(rounds):
        idx = [i for v in (rng.choice(segs) for _ in segs) for i in v]
        ys = [y[i] for i in idx]
        if len(set(ys)) < 2:
            continue
        vals.append(auc(ys, [s[i] for i in idx]))
    vals.sort()
    return (round(vals[int(0.025 * len(vals))], 3), round(vals[int(0.975 * len(vals)) - 1], 3))


def probe(name, Xtr, ytr, Xte, yte, te_rows):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    model = make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced",
                                                              max_iter=3000, C=0.5))
    model.fit(Xtr, ytr)
    p = model.predict_proba(Xte)[:, 1]
    k = max(1, int(round(0.15 * len(p))))
    veto = np.zeros(len(p), dtype=bool)
    veto[np.argsort(p)[:k]] = True
    print("%-22s AUC %.3f CI %-14s | veto15%%: vetoed met %5.1f%%  successes kept %5.1f%%"
          % (name, auc(yte, p), str(seg_ci(te_rows, yte, p)),
             100 * yte[veto].mean(), 100 * yte[~veto].sum() / yte.sum()))


tools_ref: List[str] = []


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--split", choices=("within", "days"), default="within",
                    help="within = one label regime cut by time; days = all days")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    import torch
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)

    data = build_dataset()
    rows = data["rows"]
    global tools_ref
    tools_ref = data["tools"]
    train, test, note = split_rows(data, args.split)
    if len(train) < 200 or len(test) < 100:
        print("not enough pairs for the experiment (%d/%d)" % (len(train), len(test)))
        return 1
    print("split %s: %s" % (args.split, note))
    print("pairs train %d / test %d | positives train %d (%.0f%%) test %d (%.0f%%)"
          % (len(train), len(test),
             sum(r["claim_met"] for r in train), 100 * np.mean([r["claim_met"] for r in train]),
             sum(r["claim_met"] for r in test), 100 * np.mean([r["claim_met"] for r in test])))
    for name, part in (("train", train), ("test", test)):
        print("  %s kinds %s" % (name, [(str(k[0][0]) + "/" + str(k[0][1])
                                         + ("+" if k[0][2] else "-"), k[1])
                                        for k in claim_kinds(part, 8)]))

    g_tr, e_tr, m_tr, a_tr, d_tr = state_tensors(train)
    g_te, e_te, m_te, a_te, d_te = state_tensors(test)
    y_tr = np.array([1 if r["claim_met"] else 0 for r in train])
    y_te = np.array([1 if r["claim_met"] else 0 for r in test])

    # Baseline: hand-built pooled state + action.
    base_tr = np.concatenate([pooled_baseline(g_tr, e_tr, m_tr), a_tr], axis=1)
    base_te = np.concatenate([pooled_baseline(g_te, e_te, m_te), a_te], axis=1)
    probe("hand-built + action", base_tr, y_tr, base_te, y_te, test)

    # World-model floor: how predictable is the next state at all?
    delta_rate = d_tr.mean(axis=0)
    print("world-model target rates", [round(float(x), 3) for x in delta_rate])

    # Self-supervision may use every recorded state, including test-day inputs: the
    # world-model target is the next state, never the claim label. But the test rows'
    # own (state, action) -> next-state pairs are the mechanism the claim label is
    # derived from, so they are masked out of the loss. Pretraining on them would let
    # the encoder memorise the answers instead of learning transferable dynamics.
    g_all, e_all, m_all, a_all, d_all = state_tensors(rows)
    held = {id(r) for r in test}
    weight = np.array([0.0 if id(r) in held else 1.0 for r in rows], dtype=np.float32)
    enc = Encoder()
    print("pretraining the world model on %d unlabelled pairs (%d held-out deltas masked)"
          % (len(rows), int((weight == 0).sum())))
    enc.fit_world_model(g_all, e_all, m_all, a_all, d_all, epochs=args.epochs, weight=weight)
    with torch.no_grad():
        emb_tr = enc.embed(g_tr, e_tr, m_tr)
        emb_te = enc.embed(g_te, e_te, m_te)
        pred_tr = torch.sigmoid(enc.pred(torch.cat([emb_tr, torch.as_tensor(a_tr)], dim=1)))
        pred_te = torch.sigmoid(enc.pred(torch.cat([emb_te, torch.as_tensor(a_te)], dim=1)))
    emb_tr, emb_te = emb_tr.numpy(), emb_te.numpy()
    pred_tr, pred_te = pred_tr.numpy(), pred_te.numpy()
    learned_tr = np.concatenate([emb_tr, a_tr], axis=1)
    learned_te = np.concatenate([emb_te, a_te], axis=1)
    probe("encoder + action", learned_tr, y_tr, learned_te, y_te, test)

    # The claim is a function of the next state, so the world model's predicted
    # deltas are the direct feature; the embedding is only one summary of them.
    probe("world-model deltas", pred_tr, y_tr, pred_te, y_te, test)
    probe("encoder + deltas + action",
          np.concatenate([learned_tr, pred_tr], axis=1), y_tr,
          np.concatenate([learned_te, pred_te], axis=1), y_te, test)

    # The counter as the floor, scored exactly like a model.
    rule = np.array([0.0 if (r["repeat_streak"] or 0) >= 2 else 1.0 for r in test])
    veto = rule < 0.5
    print("%-22s AUC %.3f CI %-14s | veto %3d (%.0f%%): vetoed met %5.1f%%  successes kept %5.1f%%"
          % ("counter rule", auc(y_te, rule), str(seg_ci(test, y_te, rule)),
             veto.sum(), 100 * veto.mean(), 100 * y_te[veto].mean(),
             100 * y_te[~veto].sum() / y_te.sum()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
