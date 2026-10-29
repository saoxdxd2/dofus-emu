"""Is the live outcome signal learnable from the shipped features?

The pipeline has two very different questions and they were being answered with
one number:

  1. "Would this action move the client?" -- `moved`, base rate ~27% live.
  2. "Would it move a quest counter?" -- `progress`, base rate ~0.7% live.

A policy needs (2). A value head needs (1). This probe trains a plain logistic
model on `data/transitions*` (segment-split by the builder, so no step t in
train while t+1 sits in val) with the *shipped* 42-feature vector plus the tool
identity, and reports ROC-AUC, PR-AUC and calibration on val and test with a
bootstrap CI.

Why this matters: if progress cannot be predicted above chance from these
features, then no policy trained on this representation can beat the rules on
live states, and the pipeline needs new observation features before new
capacity. If it can, the next step is a reranker over the teacher's candidates,
not another end-to-end tool classifier.

    python tools/live_outcome_probe.py
    python tools/live_outcome_probe.py --data data/transitions_v2 --json

It is deliberately sklearn-only and cheap (seconds); it is a measurement, not a
training run. Every number is printed with its row count and positive count,
because a 0.9 AUC on 8 positives is not a result.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The tool vocabulary the policy may choose from. Reads are flagged separately
# instead of being dropped: "should I read" is a real decision the loop makes
# badly (44% of live rows), and the probe should be able to say so.
READ_TOOLS = {"state.snapshot", "quest.list", "inv.list", "fight.state",
              "fight.plan", "fight.estimate", "hdv.scan", "hdv.prices",
              "knowledge.search", "fight.pass"}


def load_rows(paths: Sequence[Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row.get("x"), list) and len(row["x"]) >= 8:
                    rows.append(row)
    return rows


def build_xy(rows: List[Dict[str, Any]], tools: List[str]
             ) -> Tuple[List[List[float]], List[int], List[int], List[str]]:
    index = {name: i for i, name in enumerate(tools)}
    xs: List[List[float]] = []
    moved: List[int] = []
    progress: List[int] = []
    labels: List[str] = []
    for row in rows:
        x = list(row["x"])
        onehot = [0.0] * len(tools)
        tool = str(row.get("tool") or "")
        if tool in index:
            onehot[index[tool]] = 1.0
        is_read = 1.0 if (row.get("was_read") or tool in READ_TOOLS) else 0.0
        xs.append(x + onehot + [is_read])
        moved.append(1 if row.get("moved") else 0)
        progress.append(1 if row.get("progress") else 0)
        labels.append(tool)
    return xs, moved, progress, labels


def auc(y: List[int], score: List[float]) -> Optional[float]:
    from sklearn.metrics import roc_auc_score
    if len(set(y)) < 2:
        return None
    return float(roc_auc_score(y, score))


def pr_auc(y: List[int], score: List[float]) -> Optional[float]:
    from sklearn.metrics import average_precision_score
    if len(set(y)) < 2:
        return None
    return float(average_precision_score(y, score))


def bootstrap_ci(y: List[int], score: List[float], rounds: int = 400,
                 seed: int = 0) -> Optional[Tuple[float, float]]:
    from sklearn.metrics import roc_auc_score
    positives = sum(y)
    if positives < 2 or positives == len(y):
        return None
    rng = random.Random(seed)
    idx = list(range(len(y)))
    vals: List[float] = []
    for _ in range(rounds):
        sample = [rng.choice(idx) for _ in idx]
        ys = [y[i] for i in sample]
        if len(set(ys)) < 2:
            continue
        vals.append(float(roc_auc_score(ys, [score[i] for i in sample])))
    if len(vals) < 20:
        return None
    vals.sort()
    lo = vals[int(0.025 * len(vals))]
    hi = vals[min(len(vals) - 1, int(0.975 * len(vals)))]
    return (round(lo, 3), round(hi, 3))


def precision_at(y: List[int], score: List[float], fraction: float) -> Dict[str, Any]:
    n = len(y)
    if n == 0:
        return {}
    order = sorted(range(n), key=lambda i: score[i], reverse=True)
    k = max(1, int(fraction * n))
    hits = sum(y[i] for i in order[:k])
    total = sum(y)
    return {"k": k, "hits": hits,
            "precision": round(hits / k, 3),
            "recall": round(hits / max(1, total), 3)}


def probe(data: Path, out: Dict[str, Any]) -> Dict[str, Any]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    tools = out.get("tools") or []
    splits = {}
    for name in ("train", "val", "test"):
        splits[name] = load_rows([data / ("%s.jsonl" % name)])
    if not splits["train"]:
        raise SystemExit("no training rows under %s" % data)
    if not tools:
        seen = sorted({str(r.get("tool")) for r in splits["train"] if r.get("tool")})
        tools = seen
    xs_tr, moved_tr, prog_tr, _ = build_xy(splits["train"], tools)
    result: Dict[str, Any] = {"data": str(data), "tools": tools,
                              "rows": {k: len(v) for k, v in splits.items()},
                              "labels": {}}
    for target, y_tr in (("moved", moved_tr), ("progress", prog_tr)):
        positives = sum(y_tr)
        entry: Dict[str, Any] = {"train_rows": len(y_tr), "train_positives": positives}
        if positives < 5:
            entry["note"] = ("only %d positive rows: not trainable, and any AUC "
                             "would be noise" % positives)
            result["labels"][target] = entry
            continue
        model = make_pipeline(StandardScaler(),
                              LogisticRegression(class_weight="balanced",
                                                 max_iter=3000, C=0.5))
        model.fit(xs_tr, y_tr)
        entry["coefficient_top"] = _top_coefficients(model, tools)
        for split in ("val", "test"):
            rows = splits[split]
            if not rows:
                continue
            xs, moved, prog, _ = build_xy(rows, tools)
            y = moved if target == "moved" else prog
            if sum(y) == 0:
                entry[split] = {"rows": len(y), "positives": 0, "note": "no positives"}
                continue
            score = [float(p) for p in model.predict_proba(xs)[:, 1]]
            entry[split] = {
                "rows": len(y),
                "positives": int(sum(y)),
                "base_rate": round(sum(y) / len(y), 4),
                "roc_auc": _r(auc(y, score)),
                "roc_auc_ci95": bootstrap_ci(y, score),
                "pr_auc": _r(pr_auc(y, score)),
                "brier": round(sum((p - t) ** 2 for p, t in zip(score, y)) / len(y), 4),
                "top10pct": precision_at(y, score, 0.10),
                "top25pct": precision_at(y, score, 0.25),
            }
        result["labels"][target] = entry
    return result


MECHANISM_NAMES = (
    "ready_count", "ready_multi", "ready_type_match",
    "exits", "blocked_ratio", "has_exit", "exit_on_my_cell",
    "groups", "group_monsters", "monster_match", "group_on_my_cell",
    "interactives", "interactive_on_my_cell",
    "in_dialog", "tutorial", "active_quests",
    "level", "breed",
    "args_has_map", "args_has_direction", "args_has_cell",
    "args_has_element", "args_has_quest", "args_has_monster",
    "miss_streak", "repeat_streak",
)


def mechanism_vector(row: Dict[str, Any]) -> List[float]:
    """The preconditions of the call, with no ids and no level-laden phase keys.

    The 42-vector `objective_features` describes the objective and the run's phase;
    measured 2026-10-29 it memorises days and inverts on a temporal fold. The context
    block added the same day carries exactly what decides whether a call can work -
    exits, groups, interactives, the ready list, the dialog - and nothing was reading
    it. These features are relations ("is the objective's monster here", "is there an
    exit at all"), not identities, so a new map or a new level still lands in the same
    space. Args enter only as which ones are present, never as values: a destination
    map id would be another identity to memorise.
    """
    ctx = row.get("context") or {}
    args = row.get("args") if isinstance(row.get("args"), dict) else {}
    ready = ctx.get("ready") or []
    exits = ctx.get("exits") or []
    groups = ctx.get("groups") or []
    inter = ctx.get("interactives") or []
    who = ctx.get("character") or {}
    cell = ctx.get("cell")

    def num(value: Any) -> float:
        return float(value) if isinstance(value, (int, float)) else 0.0

    def positive(value: Any) -> float:
        return 1.0 if isinstance(value, (int, float)) and value > 0 else 0.0

    ready_ids = [r.get("objective") for r in ready if isinstance(r, dict)]
    ready_types = [r.get("type") for r in ready if isinstance(r, dict)]
    blocked = [e for e in exits if isinstance(e, dict) and e.get("blocked")]
    monsters = [m for g in groups if isinstance(g, dict)
                for m in (g.get("monsters") or [])]
    param0 = None
    for item in ready:
        if isinstance(item, dict) and (item.get("params") or {}):
            param0 = (item.get("params") or {}).get("parameter0")
            break
    if param0 is None:
        param0 = (args.get("monster") if "monster" in args else None)
    monster_match = 1.0 if param0 is not None and param0 in monsters else 0.0
    exit_cells = [e.get("cell") for e in exits if isinstance(e, dict)]
    inter_cells = [e.get("cell") for e in inter if isinstance(e, dict)]
    return [
        num(len(ready)) / 8.0,
        1.0 if len(ready) > 1 else 0.0,
        1.0 if row.get("objective_type") is not None
        and any(str(t) == str(row.get("objective_type")) for t in ready_types) else 0.0,
        num(len(exits)) / 12.0,
        (len(blocked) / len(exits)) if exits else 0.0,
        1.0 if len(exits) else 0.0,
        1.0 if exit_cells and cell in exit_cells else 0.0,
        num(len(groups)) / 8.0,
        num(len(monsters)) / 24.0,
        monster_match,
        1.0 if cell in [g.get("cell") for g in groups if isinstance(g, dict)] else 0.0,
        num(len(inter)) / 12.0,
        1.0 if cell in inter_cells else 0.0,
        1.0 if ctx.get("in_dialog") else 0.0,
        1.0 if ctx.get("tutorial") else 0.0,
        num(ctx.get("active_quests")) / 5.0,
        num(who.get("level")) / 50.0,
        num(who.get("breed")) / 20.0,
        1.0 if args.get("map_id", args.get("map")) is not None else 0.0,
        1.0 if args.get("direction") else 0.0,
        1.0 if args.get("cell") is not None else 0.0,
        1.0 if args.get("element_id") is not None else 0.0,
        1.0 if args.get("quest_id") is not None else 0.0,
        1.0 if args.get("monster") is not None else 0.0,
        # Consecutive misses of this same tool, from the claim label. It is the loop's
        # own memory, not the world: measured 2026-10-29 it scored 0.805 AUC inside
        # nav.goto on a future fold, where every state feature was at chance.
        min(5.0, float(row.get("miss_streak") or 0.0)) / 5.0,
        # The same, keyed by the exact rule (tool *and* args). Measured 2026-10-29 on
        # the temporal test fold: nav.goto claim met 93% at 0, 22% after one identical
        # miss, 0/18 after two; AUC 0.851 vs 0.805 for the per-tool streak. Re-issuing
        # an identical request after a miss is the loop's dominant failure mode.
        min(5.0, float(row.get("repeat_streak") or 0.0)) / 5.0,
    ]


def _make_model() -> Any:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(StandardScaler(),
                         LogisticRegression(class_weight="balanced",
                                            max_iter=3000, C=0.5))


def _within_pairs(rows: List[Dict[str, Any]], score: List[float],
                  target: str, seed: int = 0) -> Dict[str, Any]:
    """Phase-free accuracy: only pairs of actions inside the same segment count.

    A segment is one goal run of one instance. `level`, `step_index` and `kamas`
    are constant or near-constant inside it, so a model that only knows the phase
    cannot order two of its actions; one that knows the action can. Pairs where
    the two rows carry the same tool are counted apart, because they cannot be
    won by tool identity at all.

    The CI resamples segments, not pairs: actions inside a segment are not
    independent, and the pair count (thousands) would make any interval look
    narrow while 11 segments are all the evidence there is.
    """
    by_seg: Dict[Any, List[int]] = {}
    for i, row in enumerate(rows):
        by_seg.setdefault(row.get("segment"), []).append(i)
    per_seg = []
    for seg, indices in by_seg.items():
        pos = [i for i in indices if rows[i].get(target)]
        neg = [i for i in indices if not rows[i].get(target)]
        if not pos or not neg:
            continue
        pairs = 0
        correct = 0.0
        mixed_pairs = 0
        mixed_correct = 0.0
        for i in pos:
            for j in neg:
                pairs += 1
                hit = 1.0 if score[i] > score[j] else (0.5 if score[i] == score[j] else 0.0)
                correct += hit
                if str(rows[i].get("tool")) != str(rows[j].get("tool")):
                    mixed_pairs += 1
                    mixed_correct += hit
        per_seg.append((seg, pairs, correct, mixed_pairs, mixed_correct))

    def ratio(pairs_i: int, correct_i: float) -> Optional[float]:
        return None if not pairs_i else round(correct_i / pairs_i, 3)

    pairs = sum(p[1] for p in per_seg)
    correct = sum(p[2] for p in per_seg)
    mixed_pairs = sum(p[3] for p in per_seg)
    mixed_correct = sum(p[4] for p in per_seg)
    rng = random.Random(seed)
    both_ci = None
    mixed_ci = None
    if len(per_seg) >= 3:
        boots_both, boots_mixed = [], []
        for _ in range(400):
            sample = [rng.choice(per_seg) for _ in per_seg]
            b_pairs = sum(p[1] for p in sample)
            b_mixed = sum(p[3] for p in sample)
            if b_pairs:
                boots_both.append(sum(p[2] for p in sample) / b_pairs)
            if b_mixed:
                boots_mixed.append(sum(p[4] for p in sample) / b_mixed)
        if len(boots_both) >= 40:
            boots_both.sort()
            both_ci = (round(boots_both[int(0.025 * len(boots_both))], 3),
                       round(boots_both[min(len(boots_both) - 1,
                                            int(0.975 * len(boots_both)))], 3))
        if len(boots_mixed) >= 40:
            boots_mixed.sort()
            mixed_ci = (round(boots_mixed[int(0.025 * len(boots_mixed))], 3),
                        round(boots_mixed[min(len(boots_mixed) - 1,
                                              int(0.975 * len(boots_mixed)))], 3))
    return {
        "segments_with_pos_and_neg": len(per_seg),
        "pairs": pairs,
        "pair_accuracy": ratio(pairs, correct),
        "pair_accuracy_ci95": both_ci,
        "tool_discordant_pairs": mixed_pairs,
        "tool_discordant_accuracy": ratio(mixed_pairs, mixed_correct),
        "tool_discordant_ci95": mixed_ci,
    }


def within_segment_probe(data: Path, out: Dict[str, Any]) -> Dict[str, Any]:
    """Split the signal into 'which phase of the run' and 'which action'."""
    tools = out.get("tools") or []
    splits = {}
    for name in ("train", "val", "test"):
        splits[name] = load_rows([data / ("%s.jsonl" % name)])
    if not splits["train"]:
        raise SystemExit("no training rows under %s" % data)
    if not tools:
        tools = sorted({str(r.get("tool")) for r in splits["train"] if r.get("tool")})
    # Column layout of build_xy: x features, then one tool-hot, then is_read.
    width = len(splits["train"][0]["x"])
    action_cols = list(range(width, width + len(tools) + 1))
    phase_cols = list(range(width))

    xs_tr, moved_tr, prog_tr, labels_tr = build_xy(splits["train"], tools)
    mech_width = len(MECHANISM_NAMES)
    action_width = len(tools) + 1
    mech_cols = list(range(width + action_width, width + action_width + mech_width))
    views = {
        "phase_only": phase_cols,
        "action_only": action_cols,
        "both": phase_cols + action_cols,
        "mechanism_only": mech_cols,
        "mechanism_action": mech_cols + action_cols,
        "all": phase_cols + action_cols + mech_cols,
    }

    def matrix(rows: List[Dict[str, Any]]) -> List[List[float]]:
        xs, _moved, _prog, _labels = build_xy(rows, tools)
        return [x + mechanism_vector(row) for x, row in zip(xs, rows)]

    xall_tr = matrix(splits["train"])
    result: Dict[str, Any] = {"data": str(data), "tools": tools,
                              "rows": {k: len(v) for k, v in splits.items()},
                              "labels": {}}

    def labelled(rows: List[Dict[str, Any]], target: str
                 ) -> Tuple[List[int], List[int]]:
        """Index and label of the rows that can speak for this target.

        `claim_met` is None for a read and for a call whose promise the state cannot
        show; scoring those as failures would teach the model that reading the world
        is a failure and would flatter every AUC.
        """
        if target == "claim_met":
            index = [i for i, row in enumerate(rows) if row.get("claim_met") is not None]
            return index, [1 if rows[i].get("claim_met") else 0 for i in index]
        return list(range(len(rows))), [1 if row.get(target) else 0 for row in rows]

    for target in ("moved", "progress", "claim_met"):
        fit_idx, y_tr = labelled(splits["train"], target)
        entry: Dict[str, Any] = {"train_rows": len(y_tr), "train_positives": sum(y_tr)}
        if sum(y_tr) < 5 or sum(y_tr) == len(y_tr):
            entry["note"] = "only %d positives: not trainable" % sum(y_tr)
            result["labels"][target] = entry
            continue
        xs_fit = [xall_tr[i] for i in fit_idx]
        labels_fit = [labels_tr[i] for i in fit_idx]
        models = {}
        for view, cols in views.items():
            model = _make_model()
            model.fit([[row[c] for c in cols] for row in xs_fit], y_tr)
            models[view] = (model, cols)
        # The floor a learned model has to clear: the tool's own historical rate,
        # with a Laplace prior so a tool seen four times cannot claim certainty.
        # Measured 2026-10-29 on a temporal fold, this baseline scored AUC 0.640 on
        # moved while the 42-feature model scored 0.538 - the state features were
        # memorising overlapping days, and only the tool rate survived the shift.
        base = sum(y_tr) / max(1, len(y_tr))
        seen: Dict[str, List[int]] = {}
        for label, value in zip(labels_fit, y_tr):
            cell = seen.setdefault(label, [0, 0])
            cell[0] += 1
            cell[1] += int(bool(value))
        rate = {label: (hit + base * 4.0) / (count + 4.0)
                for label, (count, hit) in seen.items()}
        for split in ("val", "test"):
            rows_all = splits[split]
            if not rows_all:
                continue
            xall = matrix(rows_all)
            index, y = labelled(rows_all, target)
            rows = [rows_all[i] for i in index]
            xs = [xall[i] for i in index]
            if sum(y) == 0:
                entry[split] = {"rows": len(y), "positives": 0}
                continue
            stat: Dict[str, Any] = {"rows": len(y), "positives": int(sum(y))}
            for view, (model, cols) in models.items():
                score = [float(p) for p in
                         model.predict_proba([[row[c] for c in cols] for row in xs])[:, 1]]
                stat[view] = {
                    "auc": _r(auc(y, score)),
                    "ci95": bootstrap_ci(y, score),
                    "within": _within_pairs(rows, score, target),
                }
            flat = [rate.get(str(row.get("tool")), base) for row in rows]
            stat["tool_rate"] = {
                "auc": _r(auc(y, flat)),
                "ci95": bootstrap_ci(y, flat),
                "within": _within_pairs(rows, flat, target),
            }
            entry[split] = stat
        result["labels"][target] = entry
    return result


def _segment_ci(rows: List[Dict[str, Any]], y: List[int], score: List[float],
                rounds: int = 400, seed: int = 0) -> Optional[Tuple[float, float]]:
    """AUC interval that resamples segments, because rows inside one are not independent."""
    by_seg: Dict[Any, List[int]] = {}
    for i, row in enumerate(rows):
        by_seg.setdefault(row.get("segment"), []).append(i)
    segs = [idx for idx in by_seg.values() if 0 < sum(y[i] for i in idx) < len(idx)]
    if len(segs) < 3:
        return None
    rng = random.Random(seed)
    vals: List[float] = []
    for _ in range(rounds):
        sample = [rng.choice(segs) for _ in segs]
        ys = [y[i] for idx in sample for i in idx]
        ss = [score[i] for idx in sample for i in idx]
        if len(set(ys)) < 2:
            continue
        vals.append(auc(ys, ss))
    if len(vals) < 20:
        return None
    vals.sort()
    return (round(vals[int(0.025 * len(vals))], 3),
            round(vals[min(len(vals) - 1, int(0.975 * len(vals)))], 3))


def _paired_delta_ci(rows: List[Dict[str, Any]], y: List[int], a: List[float],
                     b: List[float], rounds: int = 400,
                     seed: int = 0) -> Optional[Tuple[float, float]]:
    """Interval of AUC(a) - AUC(b) when the same segments are resampled for both.

    Two separate intervals that overlap say nothing about their difference: the models
    share rows, so the comparison must be paired. Measured 2026-10-29 this is what
    decides whether state context adds anything to the streak on quest.advance.
    """
    by_seg: Dict[Any, List[int]] = {}
    for i, row in enumerate(rows):
        by_seg.setdefault(row.get("segment"), []).append(i)
    segs = [idx for idx in by_seg.values() if 0 < sum(y[i] for i in idx) < len(idx)]
    if len(segs) < 3:
        return None
    rng = random.Random(seed)
    vals: List[float] = []
    for _ in range(rounds):
        idx = [i for seg in (rng.choice(segs) for _ in segs) for i in seg]
        ys = [y[i] for i in idx]
        if len(set(ys)) < 2:
            continue
        vals.append(auc(ys, [a[i] for i in idx]) - auc(ys, [b[i] for i in idx]))
    if len(vals) < 20:
        return None
    vals.sort()
    return (round(vals[int(0.025 * len(vals))], 3),
            round(vals[min(len(vals) - 1, int(0.975 * len(vals)))], 3))


def within_tool_probe(data: Path, target: str = "claim_met") -> Dict[str, Any]:
    """Fit one head per tool and score it on that tool's future rows.

    A pooled success model is dominated by the tool mix (G48): the temporal test period
    is 78% nav.goto-met and 6% quest.advance-met, so tool identity alone scores 0.877
    and the mechanism block, which excludes identity, is at chance. The only question a
    success head can answer is the one a rule cannot: *inside one tool*, does the state
    and dialog context beat the loop's own counters? `streaks_only` is that rule.
    """
    splits = {name: load_rows([data / ("%s.jsonl" % name)])
              for name in ("train", "val", "test")}
    names = list(MECHANISM_NAMES)
    streak_cols = [names.index("miss_streak"), names.index("repeat_streak")]
    mech_cols = [i for i in range(len(names)) if i not in streak_cols]
    views = (("streaks_only", streak_cols),
             ("mechanism_no_streak", mech_cols),
             ("mechanism_all", list(range(len(names)))))
    out: Dict[str, Any] = {"data": str(data), "target": target, "tools": {}}
    for tool in sorted({str(r.get("tool")) for r in splits["train"]}):
        entry: Dict[str, Any] = {}
        for split in ("train", "val", "test"):
            rows = [r for r in splits[split]
                    if str(r.get("tool")) == tool and r.get(target) is not None]
            entry[split] = {"rows": len(rows),
                            "positives": int(sum(1 for r in rows if r[target]))}
        tr = [r for r in splits["train"]
              if str(r.get("tool")) == tool and r.get(target) is not None]
        y_tr = [1 if r[target] else 0 for r in tr]
        if len(tr) < 80 or sum(y_tr) < 8 or sum(y_tr) == len(y_tr):
            entry["note"] = "too few rows or one-sided"
            out["tools"][tool] = entry
            continue
        models = {}
        for view, cols in views:
            model = _make_model()
            model.fit([[mechanism_vector(r)[c] for c in cols] for r in tr], y_tr)
            models[view] = (model, cols)
        for split in ("val", "test"):
            rows = [r for r in splits[split]
                    if str(r.get("tool")) == tool and r.get(target) is not None]
            y = [1 if r[target] else 0 for r in rows]
            if len(rows) < 30 or not 0 < sum(y) < len(y):
                continue
            stat: Dict[str, Any] = {"rows": len(rows), "positives": int(sum(y))}
            scores: Dict[str, List[float]] = {}
            for view, (model, cols) in models.items():
                score = [float(p) for p in model.predict_proba(
                    [[mechanism_vector(r)[c] for c in cols] for r in rows])[:, 1]]
                scores[view] = score
                stat[view] = {"auc": _r(auc(y, score)),
                              "ci95": _segment_ci(rows, y, score)}
            base = scores["streaks_only"]
            for view in ("mechanism_no_streak", "mechanism_all"):
                stat["delta_%s" % view] = {
                    "delta": _r(auc(y, scores[view]) - auc(y, base)),
                    "ci95": _paired_delta_ci(rows, y, scores[view], base),
                }
            entry[split] = stat
        out["tools"][tool] = entry
    return out


def _diff_ci(rows: List[Dict[str, Any]], y: List[int], flag: List[bool],
             rounds: int = 400, seed: int = 0) -> Optional[Tuple[float, float]]:
    """Cluster bootstrap of success(flag=False) - success(flag=True)."""
    by_seg: Dict[Any, List[int]] = {}
    for i, row in enumerate(rows):
        by_seg.setdefault(row.get("segment"), []).append(i)
    segs = [(idx,) for idx in by_seg.values()
            if any(flag[i] for i in idx) and any(not flag[i] for i in idx)]
    if len(segs) < 3:
        return None
    rng = random.Random(seed)
    vals: List[float] = []
    flat = [s[0] for s in segs]
    for _ in range(rounds):
        idx = [i for seg in (rng.choice(flat) for _ in flat) for i in seg]
        kept = [y[i] for i in idx if not flag[i]]
        veto = [y[i] for i in idx if flag[i]]
        if not kept or not veto:
            continue
        vals.append(sum(kept) / len(kept) - sum(veto) / len(veto))
    if len(vals) < 20:
        return None
    vals.sort()
    return (round(vals[int(0.025 * len(vals))], 3),
            round(vals[min(len(vals) - 1, int(0.975 * len(vals)))], 3))


def gate_probe(data: Path, target: str = "claim_met") -> Dict[str, Any]:
    """Price the reject option: what does refusing a call cost and save?

    A success head is only useful if vetoing its low-probability calls leaves the
    successes behind. This evaluates the rules the corpus actually supports (the
    streaks) on the test fold as *selective prediction*: vetoed calls, their success
    rate, the successes retained, and the paired segment bootstrap of the difference.
    """
    splits = {name: load_rows([data / ("%s.jsonl" % name)]) for name in ("train", "test")}
    rules = {
        "repeat_streak>=1": lambda r, k=1: float(r.get("repeat_streak") or 0) >= k,
        "repeat_streak>=2": lambda r, k=2: float(r.get("repeat_streak") or 0) >= k,
        "miss_streak>=1": lambda r, k=1: float(r.get("miss_streak") or 0) >= k,
        "miss_streak>=2": lambda r, k=2: float(r.get("miss_streak") or 0) >= k,
    }
    out: Dict[str, Any] = {"data": str(data), "target": target, "tools": {}}
    for tool in sorted({str(r.get("tool")) for r in splits["test"]}):
        rows = [r for r in splits["test"]
                if str(r.get("tool")) == tool and r.get(target) is not None]
        y = [1 if r[target] else 0 for r in rows]
        if len(rows) < 60 or not 0 < sum(y) < len(y):
            continue
        entry: Dict[str, Any] = {"rows": len(rows), "positives": sum(y),
                                 "base": round(sum(y) / len(y), 3), "rules": {}}
        for name, fn in rules.items():
            flag = [bool(fn(r)) for r in rows]
            if not any(flag):
                continue
            kept = [y[i] for i in range(len(y)) if not flag[i]]
            veto = [y[i] for i in range(len(y)) if flag[i]]
            stat = {
                "vetoed": len(veto),
                "coverage": round(len(kept) / len(y), 3),
                "vetoed_success": round(sum(veto) / len(veto), 3),
                "kept_success": round(sum(kept) / len(kept), 3) if kept else None,
                "successes_retained": round(sum(kept) / sum(y), 3) if sum(y) else None,
                "successes_lost_per_1000": round(1000.0 * sum(veto) / len(y), 1),
                "delta_ci": _diff_ci(rows, y, flag),
            }
            entry["rules"][name] = stat
        out["tools"][tool] = entry
    return out


def stopping_probe(data: Path, target: str = "claim_met", rounds: int = 400,
                   seed: int = 0) -> Dict[str, Any]:
    """When is a retry worth it? The decision variable is the state, not the count.

    A rule that failed is retried; the question is whether the world is still the same
    situation. Measured 2026-10-29 the raw counter is *not* a stopping clock: in train
    a call with repeat_streak exactly 3 met 57% of the time, because the counter persists
    across state changes and the rule is retried later in a new situation. So each retry
    of a failed rule is grouped by what happened since that failure:

      immediate     the previous step was the same rule (nothing in between)
      changed       another action ran, and cell or dialog differs from the failure
      other-action  another action ran, and the state is identical

    p(success) per group is estimated per split with a cluster bootstrap over segments,
    and the stopping rule follows: retry only while p(group) is at least the success
    rate lambda of the available alternative.
    """
    splits = {name: load_rows([data / ("%s.jsonl" % name)]) for name in ("train", "test")}
    out: Dict[str, Any] = {"data": str(data), "target": target, "splits": {}}

    def rule_of(r: Dict[str, Any]) -> str:
        return str(r.get("tool")) + "|" + json.dumps(r.get("args") or {}, sort_keys=True)

    for split, rows in splits.items():
        by_seg: Dict[Any, List[Dict[str, Any]]] = {}
        for r in rows:
            by_seg.setdefault(r.get("segment"), []).append(r)
        recs: List[Tuple[Any, str, str, int]] = []
        for seg, rs in by_seg.items():
            rs.sort(key=lambda r: r.get("t") or 0)
            last: Dict[str, int] = {}
            for i, r in enumerate(rs):
                rule = rule_of(r)
                j = last.get(rule)
                last[rule] = i
                if j is None or r.get(target) is None:
                    continue
                if rs[j].get(target) is not False:
                    continue
                a = rs[j].get("context") or {}
                b = r.get("context") or {}
                sig_a = (a.get("cell"), bool(a.get("in_dialog")))
                sig_b = (b.get("cell"), bool(b.get("in_dialog")))
                group = ("immediate" if j == i - 1
                         else ("changed" if sig_a != sig_b else "other-action"))
                recs.append((seg, str(r.get("tool")), group,
                             1 if r.get(target) else 0))
        tools: Dict[str, Any] = {}
        for tool in sorted({rec[1] for rec in recs}):
            entry: Dict[str, Any] = {}
            for group in ("immediate", "changed", "other-action"):
                cell = [rec for rec in recs if rec[1] == tool and rec[2] == group]
                if not cell:
                    continue
                n = len(cell)
                met = sum(rec[3] for rec in cell)
                segs: Dict[Any, List[int]] = {}
                for seg, _t, _g, y in cell:
                    segs.setdefault(seg, []).append(y)
                vals = list(segs.values())
                rng = random.Random(seed)
                boots: List[float] = []
                for _ in range(rounds):
                    sample = [rng.choice(vals) for _ in vals]
                    ys = [y for chunk in sample for y in chunk]
                    if ys:
                        boots.append((sum(ys) + 1.0) / (len(ys) + 2.0))
                boots.sort()
                ci = (round(boots[int(0.025 * len(boots))], 3),
                      round(boots[min(len(boots) - 1, int(0.975 * len(boots)))], 3))                     if len(boots) >= 20 else None
                entry[group] = {"n": n, "met": met,
                                "p": round((met + 1.0) / (n + 2.0), 3), "ci": ci}
            tools[tool] = entry
        out["splits"][split] = tools
    return out


def rolling_probe(data: Path, target: str = "claim_met", horizon: float = 0.15,
                  cuts: Sequence[float] = (0.40, 0.55, 0.70, 0.85),
                  seed: int = 0) -> Dict[str, Any]:
    """Is the gate a rule or one period's luck? Rolling-origin validation.

    One temporal cut gives one future. This walks the cut through the segment start
    order: train on everything before the cut, score the next `horizon` of segments,
    and repeat. A rule that only works on the 10-28/10-29 period will show it here.
    """
    rows = load_rows([data / ("%s.jsonl" % name)
                      for name in ("train", "val", "test")])
    by_seg: Dict[Any, List[Dict[str, Any]]] = {}
    for r in rows:
        by_seg.setdefault(r.get("segment"), []).append(r)
    starts = {seg: min(r["at"] for r in rs if isinstance(r.get("at"), (int, float)))
              for seg, rs in by_seg.items()
              if any(isinstance(r.get("at"), (int, float)) for r in rs)}
    order = sorted(starts, key=starts.get)
    out: Dict[str, Any] = {"data": str(data), "target": target,
                           "segments": len(order), "cuts": {}}
    for q in cuts:
        i0 = int(q * len(order))
        i1 = min(len(order), int((q + horizon) * len(order)))
        train_segs = set(order[:i0])
        test_segs = set(order[i0:i1])
        entry: Dict[str, Any] = {"test_segments": len(test_segs), "tools": {}}
        for tool in sorted({str(r.get("tool")) for r in rows}):
            tr = [r for r in rows if r.get("segment") in train_segs
                  and str(r.get("tool")) == tool and r.get(target) is not None]
            te = [r for r in rows if r.get("segment") in test_segs
                  and str(r.get("tool")) == tool and r.get(target) is not None]
            y = [1 if r[target] else 0 for r in te]
            if len(tr) < 100 or len(te) < 40 or not 0 < sum(y) < len(y):
                continue
            flag = [float(r.get("repeat_streak") or 0) >= 2 for r in te]
            kept = [y[i] for i in range(len(y)) if not flag[i]]
            veto = [y[i] for i in range(len(y)) if flag[i]]
            entry["tools"][tool] = {
                "test_rows": len(te), "base": round(sum(y) / len(y), 3),
                "vetoed": len(veto),
                "vetoed_met": round(sum(veto) / len(veto), 3) if veto else None,
                "kept_met": round(sum(kept) / len(kept), 3) if kept else None,
                "auc_failure": _r(auc(y, [-float(min(5, r.get("repeat_streak") or 0))
                                          for r in te])),
            }
        out["cuts"]["%.2f" % q] = entry
    return out


def _r(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 3)


def _top_coefficients(model: Any, tools: List[str]) -> List[Dict[str, Any]]:
    try:
        lr = model.named_steps["logisticregression"]
        coef = list(lr.coef_[0])
    except Exception:
        return []
    names = ["f%d" % i for i in range(len(coef) - len(tools) - 1)]
    names += ["tool:%s" % t for t in tools] + ["is_read"]
    ranked = sorted(zip(names, coef), key=lambda kv: -abs(kv[1]))[:10]
    return [{"name": n, "weight": round(float(w), 3)} for n, w in ranked]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=REPO_ROOT / "data" / "transitions_v2")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--within-segment", action="store_true",
                    help="compare phase-only, action-only and both inside segments")
    ap.add_argument("--within-tool", action="store_true",
                    help="fit one head per tool; compare the streaks against state context")
    ap.add_argument("--gate", action="store_true",
                    help="price vetoing low-probability calls (selective prediction)")
    ap.add_argument("--stopping", action="store_true",
                    help="fit p(success | streak) and derive the stopping threshold")
    ap.add_argument("--rolling", action="store_true",
                    help="walk the time cut through the segments to test a rule's stability")
    ap.add_argument("--target", default="claim_met",
                    help="label used by --within-tool / --gate / --stopping")
    args = ap.parse_args(argv)
    if args.rolling:
        result = rolling_probe(args.data, args.target)
        if args.json:
            print(json.dumps(result, indent=1))
            return 0
        print("data: %s  target: %s  segments %d"
              % (result["data"], result["target"], result["segments"]))
        for cut, entry in result["cuts"].items():
            print("== cut %s (test segments %d)" % (cut, entry["test_segments"]))
            for tool, stat in sorted(entry["tools"].items()):
                print("   %-13s rows %3d base %.2f | veto %3d vetoed met %s | kept met %s | AUC(fail) %s"
                      % (tool, stat["test_rows"], stat["base"], stat["vetoed"],
                         stat["vetoed_met"], stat["kept_met"], stat["auc_failure"]))
        return 0
    if args.stopping:
        result = stopping_probe(args.data, args.target)
        if args.json:
            print(json.dumps(result, indent=1))
            return 0
        print("data: %s  target: %s" % (result["data"], result["target"]))
        for split, tools in result["splits"].items():
            print("== %s" % split)
            for tool, entry in sorted(tools.items()):
                print("   %s" % tool)
                for group in ("immediate", "changed", "other-action"):
                    stat = entry.get(group)
                    if not stat:
                        continue
                    print("      %-13s n %3d met %3d  p %.3f  ci %s"
                          % (group, stat["n"], stat["met"], stat["p"],
                             str(stat["ci"])))
        return 0
    if args.gate:
        result = gate_probe(args.data, args.target)
        if args.json:
            print(json.dumps(result, indent=1))
            return 0
        print("data: %s  target: %s" % (result["data"], result["target"]))
        for tool, entry in sorted(result["tools"].items()):
            print("== %s  test rows %d pos %d base %.3f"
                  % (tool, entry["rows"], entry["positives"], entry["base"]))
            for rule, stat in entry["rules"].items():
                print("   %-17s veto %3d (cover %.0f%%)  vetoed met %.0f%%  kept met %s  "
                      "successes kept %s  lost %s/1000  diff CI %s"
                      % (rule, stat["vetoed"], 100 * stat["coverage"],
                         100 * stat["vetoed_success"],
                         stat["kept_success"], stat["successes_retained"],
                         stat["successes_lost_per_1000"], stat["delta_ci"]))
        return 0
    if args.within_tool:
        result = within_tool_probe(args.data, args.target)
        if args.json:
            print(json.dumps(result, indent=1))
            return 0
        print("data: %s  target: %s" % (result["data"], result["target"]))
        for tool, entry in sorted(result["tools"].items()):
            split_note = " ".join("%s %d/%d" % (s, entry[s]["positives"], entry[s]["rows"])
                                  for s in ("train", "val", "test"))
            print("== %s (%s)" % (tool, split_note))
            if entry.get("note"):
                print("   %s" % entry["note"])
                continue
            for split in ("val", "test"):
                stat = entry.get(split)
                if not stat:
                    continue
                print("   %-4s rows %d pos %d" % (split, stat["rows"], stat["positives"]))
                for view in ("streaks_only", "mechanism_no_streak", "mechanism_all"):
                    view_stat = stat.get(view) or {}
                    print("     %-20s AUC %-5s CI %s"
                          % (view, view_stat.get("auc"), str(view_stat.get("ci95"))))
                for view in ("mechanism_no_streak", "mechanism_all"):
                    delta = stat.get("delta_%s" % view) or {}
                    print("     delta %-14s %-5s CI %s (vs streaks_only)"
                          % (view, delta.get("delta"), str(delta.get("ci95"))))
        return 0
    result = within_segment_probe(args.data, {}) if args.within_segment else probe(args.data, {})
    if args.json:
        print(json.dumps(result, indent=1))
    elif args.within_segment:
        print("data: %s" % result["data"])
        print("rows: %s" % result["rows"])
        for target, entry in result["labels"].items():
            print("== %s (train %d rows, %d positives)"
                  % (target, entry.get("train_rows", 0), entry.get("train_positives", 0)))
            if entry.get("note"):
                print("   %s" % entry["note"])
                continue
            for split in ("val", "test"):
                stat = entry.get(split)
                if not stat:
                    continue
                print("   %s: rows %d pos %d" % (split, stat["rows"], stat["positives"]))
                for view in ("phase_only", "action_only", "both",
                             "mechanism_only", "mechanism_action", "all", "tool_rate"):
                    view_stat = stat.get(view) or {}
                    within = view_stat.get("within") or {}
                    print("     %-16s AUC %-5s CI %-16s | within-seg acc %-5s %-16s "
                          "(%d pairs, %d segs) | tool-discordant %-5s %-16s (%d pairs)"
                          % (view, view_stat.get("auc"), str(view_stat.get("ci95")),
                             within.get("pair_accuracy"),
                             str(within.get("pair_accuracy_ci95")),
                             within.get("pairs") or 0,
                             within.get("segments_with_pos_and_neg") or 0,
                             within.get("tool_discordant_accuracy"),
                             str(within.get("tool_discordant_ci95")),
                             within.get("tool_discordant_pairs") or 0))
    else:
        print("data: %s" % result["data"])
        print("rows: %s" % result["rows"])
        for target, entry in result["labels"].items():
            print("== %s (train %d rows, %d positives)"
                  % (target, entry.get("train_rows", 0), entry.get("train_positives", 0)))
            if entry.get("note"):
                print("   %s" % entry["note"])
                continue
            for split in ("val", "test"):
                stat = entry.get(split)
                if not stat:
                    continue
                if stat.get("note"):
                    print("   %-4s %s" % (split, stat["note"]))
                    continue
                print("   %-4s rows %d pos %d base %.3f  AUC %s CI %s  PR-AUC %s  "
                      "Brier %.3f  top10%% p=%.2f r=%.2f"
                      % (split, stat["rows"], stat["positives"], stat["base_rate"],
                         stat["roc_auc"], stat["roc_auc_ci95"], stat["pr_auc"],
                         stat["brier"], stat["top10pct"].get("precision", 0),
                         stat["top10pct"].get("recall", 0)))
            top = entry.get("coefficient_top") or []
            if top:
                print("   top weights: %s"
                      % ", ".join("%s %+0.2f" % (t["name"], t["weight"]) for t in top[:6]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
