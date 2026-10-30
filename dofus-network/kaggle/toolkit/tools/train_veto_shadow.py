"""Train a frozen veto model on the past and score it on the newest day.

G58 measured the forest by retraining it inside each rolling cut. A live shadow cannot
do that: it serves one frozen file, trained once, and every later session is out of
time. This script is that deployment rehearsal - train on every day before the cut,
score the cut day only, and compare with the counter at the counter's own veto count.

The model's inputs are the 26 mechanism values, which include `repeat_streak`, so the
model can express the rule itself; any gain is what the state context adds on top.

    python tools/train_veto_shadow.py                 # cut = newest day in the corpus
    python tools/train_veto_shadow.py --cut 2026-10-29 --out models/veto_shadow
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import live_outcome_probe as probe  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data" / "transitions_v2"
OUT = REPO_ROOT / "models" / "veto_shadow"


def _day(row: Dict[str, Any]) -> Optional[str]:
    stamp = row.get("at")
    if not isinstance(stamp, (int, float)):
        return None
    return _dt.datetime.fromtimestamp(stamp, _dt.timezone.utc).date().isoformat()


def _judged(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in rows if r.get("claim_met") is not None and r.get("tool")]


def _pair_ci(values: Sequence[float], rounds: int = 2000,
             seed: int = 0) -> Optional[Tuple[float, float]]:
    """Cluster bootstrap over sessions: rows inside a session are not independent."""
    import random
    if len(values) < 3:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(rounds):
        sample = [rng.choice(values) for _ in values]
        means.append(sum(sample) / len(sample))
    means.sort()
    return round(means[int(0.025 * len(means))], 3), round(means[int(0.975 * len(means)) - 1], 3)


def _matched(y: Sequence[int], p: Sequence[float], rule: Sequence[bool]) -> Dict[str, Any]:
    k = sum(1 for flag in rule if flag)
    order = sorted(range(len(p)), key=lambda i: p[i])
    veto = set(order[:k])
    kept = [y[i] for i in range(len(y)) if i not in veto]
    called = [y[i] for i in veto]
    rule_kept = [y[i] for i in range(len(y)) if not rule[i]]
    rule_called = [y[i] for i in range(len(y)) if rule[i]]
    return {
        "vetoed": k,
        "model": {"vetoed_met": probe._r(sum(called) / len(called)) if called else None,
                  "kept_met": probe._r(sum(kept) / len(kept)) if kept else None},
        "rule": {"vetoed_met": probe._r(sum(rule_called) / len(rule_called)) if rule_called else None,
                 "kept_met": probe._r(sum(rule_kept) / len(rule_kept)) if rule_kept else None},
        "_kept": kept, "_called": called, "_rule_kept": rule_kept, "_rule_called": rule_called,
        "_veto": veto, "_rule": rule,
    }


def _report(rows: List[Dict[str, Any]], y: List[int], p: List[float],
            rule: List[bool], label: str) -> Dict[str, Any]:
    base = _matched(y, p, rule)
    out = {"rows": len(rows), "positives": sum(y), "base": probe._r(sum(y) / len(y)),
           "rule": base["rule"], "model": base["model"], "vetoed": base["vetoed"]}
    per_seg: Dict[str, List[float]] = defaultdict(lambda: [0, 0])
    for i, r in enumerate(rows):
        met = y[i]
        d = per_seg[str(r.get("segment"))]
        d[0] += 1 if (met and not base["_rule"][i]) else 0
        d[1] += 1 if (met and i not in base["_veto"]) else 0
    deltas = [v[1] - v[0] for v in per_seg.values()]
    out["segments"] = len(deltas)
    out["segments_with_gain"] = sum(1 for d in deltas if d > 0)
    out["segments_with_loss"] = sum(1 for d in deltas if d < 0)
    out["kept_met_delta_per_segment"] = {
        "mean": round(sum(deltas) / len(deltas), 3) if deltas else None,
        "ci95": _pair_ci(deltas),
    }
    print("%-18s rows %4d pos %4d base %.2f veto %4d | rule kept %s vetoed-met %s | model kept %s vetoed-met %s | delta/seg %s CI %s"
          % (label, out["rows"], out["positives"], out["base"], out["vetoed"],
             out["rule"]["kept_met"], out["rule"]["vetoed_met"],
             out["model"]["kept_met"], out["model"]["vetoed_met"],
             out["kept_met_delta_per_segment"]["mean"],
             out["kept_met_delta_per_segment"]["ci95"]))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=DATA)
    ap.add_argument("--cut", default=None, help="first day scored (default: newest day)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rows = probe.load_rows([args.data / n for n in ("train.jsonl", "val.jsonl", "test.jsonl")])
    dated = [(r, _day(r)) for r in _judged(rows)]
    days = sorted({d for _r, d in dated if d})
    if len(days) < 2:
        print("need at least two days, found %s" % days)
        return 1
    cut = args.cut or days[-1]
    train = [r for r, d in dated if d and d < cut]
    test = [r for r, d in dated if d and d >= cut]
    if len(train) < 300 or len(test) < 100:
        print("not enough judged rows (train %d / test %d)" % (len(train), len(test)))
        return 1
    y_tr = [1 if r["claim_met"] else 0 for r in train]
    y_te = [1 if r["claim_met"] else 0 for r in test]
    if len(set(y_tr)) < 2 or len(set(y_te)) < 2:
        print("degenerate labels (train %d / test %d positives)" % (sum(y_tr), sum(y_te)))
        return 1

    from sklearn.ensemble import HistGradientBoostingClassifier
    params = {"max_depth": 3, "max_iter": 120, "learning_rate": 0.06, "random_state": 0}
    model = HistGradientBoostingClassifier(**params)
    x_tr = [probe.mechanism_vector(r) for r in train]
    x_te = [probe.mechanism_vector(r) for r in test]
    model.fit(x_tr, y_tr)
    p_te = [float(v) for v in model.predict_proba(x_te)[:, 1]]
    p_tr = [float(v) for v in model.predict_proba(x_tr)[:, 1]]

    rule_te = [float(r.get("repeat_streak") or 0) >= 2 for r in test]
    print("cut %s | train %d rows (%d pos, days < %s) | test %d rows (%d pos, days >= %s)"
          % (cut, len(train), sum(y_tr), cut, len(test), sum(y_te), cut))
    print("train AUC %.3f  test AUC %.3f  rule test AUC %.3f"
          % (probe.auc(y_tr, p_tr), probe.auc(y_te, p_te),
             probe.auc(y_te, [-float(min(5, r.get("repeat_streak") or 0)) for r in test])))

    result: Dict[str, Any] = {"cut": cut, "train_rows": len(train),
                              "train_positives": sum(y_tr), "test_rows": len(test),
                              "test_positives": sum(y_te),
                              "train_auc": probe._r(probe.auc(y_tr, p_tr)),
                              "test_auc": probe._r(probe.auc(y_te, p_te)),
                              "tools": {}}
    pooled = _report(test, y_te, p_te, rule_te, "all tools")
    pooled.pop("_kept", None); pooled.pop("_called", None)
    result["pooled"] = pooled
    for tool in sorted({str(r.get("tool")) for r in test}):
        idx = [i for i, r in enumerate(test) if str(r.get("tool")) == tool]
        yy = [y_te[i] for i in idx]
        if len(idx) < 40 or not 0 < sum(yy) < len(yy):
            continue
        sub = [test[i] for i in idx]
        pp = [p_te[i] for i in idx]
        rr = [rule_te[i] for i in idx]
        got = _report(sub, yy, pp, rr, tool)
        for key in ("_kept", "_called", "_rule_kept", "_rule_called", "_veto", "_rule"):
            got.pop(key, None)
        result["tools"][tool] = got

    args.out.mkdir(parents=True, exist_ok=True)
    import joblib
    model_path = args.out / "model.joblib"
    joblib.dump(model, model_path)
    sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
    manifest = {
        "created": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "script": "tools/train_veto_shadow.py",
        "model": "HistGradientBoostingClassifier", "params": params,
        "features": list(probe.MECHANISM_NAMES), "feature_count": len(probe.MECHANISM_NAMES),
        "data": str(args.data.relative_to(REPO_ROOT)),
        "cut": cut, "train_rows": len(train), "train_positives": sum(y_tr),
        "test_rows": len(test), "test_positives": sum(y_te),
        "train_auc": result["train_auc"], "test_auc": result["test_auc"],
        "test_pooled": result["pooled"], "test_tools": result["tools"],
        "sha256": sha,
        "note": ("veto shadow only: `repeat_streak` is an input, so this generalises "
                 "the counter rather than replacing it; the score must be recorded "
                 "beside gate.would_veto, never acted on, until it wins out of time"),
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print("saved %s (sha %s) + manifest" % (model_path, sha[:12]))
    if args.json:
        print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
