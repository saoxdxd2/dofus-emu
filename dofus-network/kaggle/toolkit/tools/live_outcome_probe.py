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
    python tools/live_outcome_probe.py --calibration --data data/transitions_temporal

`--calibration` prices the reject option *per call form*: it fits p(met | form,
repeat-streak bucket) with a Beta prior on train only, picks a veto threshold on
train, and scores it on the frozen fold against the loop's own counter
(`repeat_streak_norm >= 2`) at that counter's veto budget. That comparison is the
number the first learned worker has to beat, so it is measured before one exists.

`--worker` then asks whether a state head has anything left to add: it fits the
mechanism block (without the streaks) per form on train, reports how well it orders
the calls the counter *keeps*, and prices a second veto layer of 5% and 10% on top of
the counter with the paired segment CI and the bar in `_extension_ok`.

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
                  seed: int = 0, models: bool = False) -> Dict[str, Any]:
    """Is the gate a rule or one period's luck? Rolling-origin validation.

    One temporal cut gives one future. This walks the cut through the segment start
    order: train on everything before the cut, score the next `horizon` of segments,
    and repeat. A rule that only works on the 10-28/10-29 period will show it here.

    `models` adds the logistic and forest arms G56 measured on one fold, scored at the
    same number of vetoes the rule takes on each cut. AUC says how well a model ranks;
    this says what it would cost at the operating point the rule actually runs at.
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
            if models:
                entry["tools"][tool]["models"] = _rolling_models(tr, te, y, len(veto))
        out["cuts"]["%.2f" % q] = entry
    return out


def _rolling_models(tr: List[Dict[str, Any]], te: List[Dict[str, Any]],
                    y: List[int], veto_n: int) -> Dict[str, Any]:
    """Each model at the rule's own veto count, plus its AUC on the same rows."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    x_tr = [mechanism_vector(r) for r in tr]
    x_te = [mechanism_vector(r) for r in te]
    y_tr = [1 if r.get("claim_met") else 0 for r in tr]
    out: Dict[str, Any] = {}
    if len(set(y_tr)) < 2:
        return out
    arms = {"logistic": _make_model(),
            "forest": HistGradientBoostingClassifier(max_depth=3, max_iter=120,
                                                     learning_rate=0.06, random_state=0)}
    for name, model in arms.items():
        try:
            model.fit(x_tr, y_tr)
            p = [float(v) for v in model.predict_proba(x_te)[:, 1]]
        except Exception as exc:  # a degenerate cut must not kill the whole sweep
            out[name] = {"error": "%s: %s" % (type(exc).__name__, exc)}
            continue
        order = sorted(range(len(p)), key=lambda i: p[i])
        veto = set(order[:veto_n])
        kept = [y[i] for i in range(len(y)) if i not in veto]
        called = [y[i] for i in veto]
        out[name] = {
            "auc": _r(auc(y, p)),
            "vetoed": len(called),
            "vetoed_met": _r(sum(called) / len(called)) if called else None,
            "kept_met": _r(sum(kept) / len(kept)) if kept else None,
        }
    return out


def shadow_probe(data: Path, target: str = "claim_met") -> Dict[str, Any]:
    """The live shadow gate: would-veto decisions, what they did, what came next.

    `agent.gate.RuleGate` records `meta.gate = {rule, streak, would_veto}` on every
    live decision without changing it. This reads those rows back: a would-veto call
    that still met its promise is the cost of the gate; one that missed is the saving.
    """
    rows = load_rows([data / ("%s.jsonl" % name)
                      for name in ("train", "val", "test")])
    tagged = [r for r in rows if isinstance(r.get("gate"), dict)]
    out: Dict[str, Any] = {"data": str(data), "decisions": len(rows),
                           "tagged": len(tagged), "tools": {}}
    by_seg: Dict[Any, List[Dict[str, Any]]] = {}
    for r in rows:
        by_seg.setdefault(r.get("segment"), []).append(r)
    for rs in by_seg.values():
        rs.sort(key=lambda r: r.get("t") or 0)
    nxt = {}
    for rs in by_seg.values():
        for i, r in enumerate(rs[:-1]):
            nxt[(r.get("segment"), r.get("t"))] = rs[i + 1]
    for tool in sorted({str(r.get("tool")) for r in tagged}):
        cell = [r for r in tagged if str(r.get("tool")) == tool]
        veto = [r for r in cell if (r.get("gate") or {}).get("would_veto")]
        entry: Dict[str, Any] = {"decisions": len(cell), "would_veto": len(veto)}
        judged = [r for r in veto if r.get(target) is not None]
        entry["vetoed_judged"] = len(judged)
        entry["vetoed_met"] = sum(1 for r in judged if r[target])
        rule_of = lambda r: (r["tool"], json.dumps(r.get("args") or {}, sort_keys=True))
        same = other = 0
        for r in veto:
            n = nxt.get((r.get("segment"), r.get("t")))
            if n is None:
                continue
            if rule_of(n) == rule_of(r):
                same += 1
            else:
                other += 1
        entry["next_same_rule"] = same
        entry["next_other_rule"] = other
        out["tools"][tool] = entry
    return out


# The pre-registered promotion bar for any gate that claims to beat the counter.
# It is fixed here, before a worker exists, so the number cannot move after a result.
PROMOTION_RETENTION = 0.95          # keep at least this share of the successes
PROMOTION_MIN_COUNTER_VETOES = 10   # a budget smaller than this is not a comparison
PROMOTION_DELTA_POINTS = 0.05       # a learned head must beat this count gate by this much

CALIBRATION_BUCKETS = ("0", "1", "2", "3+")
CALIBRATION_TAUS = tuple(round(0.02 * i, 2) for i in range(1, 50))
CALIBRATION_ALPHA = 1.0   # Laplace on the global rate
CALIBRATION_PRIOR = 8.0   # pseudo-rows shrinking a cell toward its form's rate


def call_form(row: Dict[str, Any]) -> str:
    """The call's shape, which is what its promise depends on.

    `quest.advance` has three promises with three different difficulty levels: a talk
    opens a dialog, a reply moves the node, an accept changes quest state. Pooling them
    calibrates a mixture and no per-form threshold can be read off it.
    """
    tool = str(row.get("tool") or "")
    args = row.get("args") if isinstance(row.get("args"), dict) else {}
    if tool == "nav.goto":
        if args.get("map_id", args.get("map")) is not None:
            return "nav.goto:at"
        if args.get("direction") or args.get("cell") is not None:
            return "nav.goto:step"
        return "nav.goto:other"
    if tool == "quest.advance":
        if args.get("accept"):
            return "quest.accept"
        if args.get("reply") is not None:
            return "quest.reply"
        if args.get("npc_id") is not None:
            return "quest.talk"
        return "quest.other"
    return tool


def streak_bucket(row: Dict[str, Any]) -> str:
    """The loop's own memory, normalized over volatile args, as the live gate counts it.

    `repeat_streak_norm` is the key `agent.gate.RuleGate` thresholds on; the exact
    key counts taps on one element with different skill_uids as three fresh calls.
    """
    value = row.get("repeat_streak_norm")
    if value is None:
        value = row.get("repeat_streak")
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        value = 0
    return "3+" if value >= 3 else str(max(0, value))


def fit_calibration(rows: List[Dict[str, Any]], alpha: float = CALIBRATION_ALPHA,
                    prior: float = CALIBRATION_PRIOR) -> Dict[str, Any]:
    """p(met | form, bucket) with a Beta prior; fitted on the rows it is given.

    A raw cell rate is 100% after one met call. The global rate is a Laplace estimate,
    each form is shrunk toward it with `prior` pseudo-rows, and each bucket toward its
    form the same way, so a bucket with two rows cannot claim certainty. The caller
    fits this on train only; scoring it on the same rows would be a calibration of the
    data, not of the rule.
    """
    judged = [r for r in rows if r.get("claim_met") is not None]
    n_all = len(judged)
    met_all = sum(1 for r in judged if r.get("claim_met"))
    global_rate = (met_all + alpha) / (n_all + 2.0 * alpha)
    forms: Dict[str, Any] = {}
    for form in sorted({call_form(r) for r in judged}):
        cell = [r for r in judged if call_form(r) == form]
        met = sum(1 for r in cell if r.get("claim_met"))
        rate = (met + prior * global_rate) / (len(cell) + prior)
        buckets: Dict[str, Any] = {}
        for bucket in CALIBRATION_BUCKETS:
            sub = [r for r in cell if streak_bucket(r) == bucket]
            hit = sum(1 for r in sub if r.get("claim_met"))
            buckets[bucket] = {
                "n": len(sub), "met": hit,
                "p": (hit + prior * rate) / (len(sub) + prior),
            }
        forms[form] = {"n": len(cell), "met": met, "rate": rate, "buckets": buckets}
    return {"global_rate": global_rate, "alpha": alpha, "prior": prior,
            "train_rows": n_all, "train_positives": met_all, "forms": forms}


def calibration_p(model: Dict[str, Any], row: Dict[str, Any]) -> float:
    """The fitted probability for one call; unknown forms fall back to the global rate."""
    entry = model["forms"].get(call_form(row))
    if not entry:
        return float(model["global_rate"])
    bucket = entry["buckets"].get(streak_bucket(row))
    return float(bucket["p"]) if bucket else float(entry["rate"])


def _selective(y: List[int], flag: List[bool]) -> Dict[str, Any]:
    kept = [y[i] for i in range(len(y)) if not flag[i]]
    veto = [y[i] for i in range(len(y)) if flag[i]]
    positives = sum(y)
    negatives = len(y) - positives
    return {
        "vetoed": len(veto),
        "coverage": round(len(kept) / len(y), 3) if y else None,
        "vetoed_success": round(sum(veto) / len(veto), 3) if veto else None,
        "kept_success": round(sum(kept) / len(kept), 3) if kept else None,
        "successes_retained": round(sum(kept) / positives, 3) if positives else None,
        "failures_vetoed": (round(sum(1 for v in veto if not v) / negatives, 3)
                            if negatives else None),
    }


def select_threshold(rows: List[Dict[str, Any]], p: List[float], target: str = "claim_met",
                     retention: float = PROMOTION_RETENTION) -> Optional[float]:
    """On train only: the threshold with the best kept rate under a retention floor.

    Higher tau vetoes more; the kept rate rises until a success is vetoed. Requiring a
    share of the successes to survive is what stops the answer from being "veto
    everything", which has a perfect vetoed rate and an empty coverage. Ties go to the
    lower tau, i.e. to more coverage at the same rate.
    """
    y = [1 if r.get(target) else 0 for r in rows]
    if not y or not 0 < sum(y) < len(y):
        return None
    best: Optional[Tuple[float, float]] = None
    tau_best: Optional[float] = None
    for tau in CALIBRATION_TAUS:
        stat = _selective(y, [value < tau for value in p])
        if stat["successes_retained"] is None or stat["successes_retained"] < retention:
            continue
        if stat["kept_success"] is None or not stat["vetoed"]:
            continue
        key = (stat["kept_success"], stat["coverage"] or 0.0)
        if best is None or key > best:
            best, tau_best = key, tau
    return tau_best


def _kept_delta_ci(rows: List[Dict[str, Any]], y: List[int], a: List[bool],
                   b: List[bool], rounds: int = 400,
                   seed: int = 0) -> Optional[Tuple[float, float]]:
    """Paired segment bootstrap of kept_success(a) - kept_success(b).

    The two gates select different rows, so comparing their kept rates needs the same
    resampled segments on both sides; two separate intervals say nothing about which
    gate is better on the same future.
    """
    by_seg: Dict[Any, List[int]] = {}
    for i, row in enumerate(rows):
        by_seg.setdefault(row.get("segment"), []).append(i)
    segs = list(by_seg.values())
    if len(segs) < 3:
        return None
    rng = random.Random(seed)
    vals: List[float] = []
    for _ in range(rounds):
        idx = [i for seg in (rng.choice(segs) for _ in segs) for i in seg]
        kept_a = [y[i] for i in idx if not a[i]]
        kept_b = [y[i] for i in idx if not b[i]]
        if not kept_a or not kept_b:
            continue
        vals.append(sum(kept_a) / len(kept_a) - sum(kept_b) / len(kept_b))
    if len(vals) < 20:
        return None
    vals.sort()
    return (round(vals[int(0.025 * len(vals))], 3),
            round(vals[min(len(vals) - 1, int(0.975 * len(vals)))], 3))


def _counter_flag(rows: List[Dict[str, Any]]) -> List[bool]:
    """The loop's operating rule today: veto after two identical misses."""
    return [float(r.get("repeat_streak_norm") or 0) >= 2 for r in rows]


def _evaluate(rows: List[Dict[str, Any]], p: List[float], taus: Dict[str, Optional[float]],
              target: str) -> Dict[str, Any]:
    """Score chosen taus and the counter on the same rows, at matched veto budgets."""
    y = [1 if r.get(target) else 0 for r in rows]
    out: Dict[str, Any] = {"rows": len(rows), "positives": sum(y),
                           "base": round(sum(y) / len(y), 3) if y else None}
    counter = _counter_flag(rows)
    out["counter"] = _selective(y, counter)
    out["counter"]["rule"] = "repeat_streak_norm>=2"
    budget = out["counter"]["vetoed"]
    comparable = budget >= PROMOTION_MIN_COUNTER_VETOES and 0 < sum(y) < len(y)
    for name, tau in taus.items():
        if tau is None:
            continue
        flag = [value < tau for value in p]
        stat = _selective(y, flag)
        stat["tau"] = tau
        stat["delta_vs_counter"] = _kept_delta_ci(rows, y, flag, counter)
        out[name] = stat
        if comparable:
            # The counter's own budget, so the comparison is "which calls to drop",
            # not "how many"; a gate that vetoes more looks better on any rate.
            order = sorted(range(len(p)), key=lambda i: p[i])
            head = set(order[:budget])
            equal = [i in head for i in range(len(p))]
            matched = _selective(y, equal)
            matched["delta_vs_counter"] = _kept_delta_ci(rows, y, equal, counter)
            out["%s_at_counter_budget" % name] = matched
    if comparable:
        counter_kept = out["counter"].get("kept_success")
        out["promotion"] = {
            "retention_floor": PROMOTION_RETENTION,
            "min_delta_points": PROMOTION_DELTA_POINTS,
            "counter_kept_success": counter_kept,
            "counter_vetoes": budget,
            "safe_beats_counter": _promotion_ok(out.get("safe_at_counter_budget"),
                                                counter_kept),
            "balanced_beats_counter": _promotion_ok(out.get("balanced_at_counter_budget"),
                                                    counter_kept),
        }
    return out


def _promotion_ok(stat: Optional[Dict[str, Any]],
                  counter_kept: Optional[float]) -> bool:
    """The pre-registered bar, all three parts of it.

    `PROMOTION_DELTA_POINTS` was declared long before it was enforced: a bootstrap
    interval that merely clears zero can clear it by half a point, which is not the
    five points the plan asked for. So the comparison carries the point gain as well
    as the interval, and a gate that ties or edges the counter is not promoted.
    """
    if not stat or counter_kept is None:
        return False
    ci = stat.get("delta_vs_counter")
    points = (stat.get("kept_success") or 0.0) - counter_kept
    return (bool(ci) and ci[0] > 0
            and points >= PROMOTION_DELTA_POINTS
            and (stat.get("successes_retained") or 0.0) >= PROMOTION_RETENTION)


def calibration_probe(data: Path, target: str = "claim_met") -> Dict[str, Any]:
    """Fit the per-form calibration on train; score it on the frozen test fold.

    The output is both halves of the plan's calibration item: the curve (predicted vs
    observed per bucket, with the weighted calibration error) and the threshold sweep's
    chosen operating points, each compared with the counter at that counter's veto
    budget so the comparison cannot be won by vetoing more calls.
    """
    splits = {name: load_rows([data / ("%s.jsonl" % name)]) for name in ("train", "test")}
    train = [r for r in splits["train"] if r.get(target) is not None]
    test = [r for r in splits["test"] if r.get(target) is not None]
    if not train:
        raise SystemExit("no judged training rows under %s" % data)
    model = fit_calibration(train)
    out: Dict[str, Any] = {"data": str(data), "target": target,
                           "train_rows": len(train), "test_rows": len(test),
                           "alpha": model["alpha"], "prior": model["prior"],
                           "forms": {}}
    p_train = [calibration_p(model, r) for r in train]
    p_test = [calibration_p(model, r) for r in test]

    def block(tr: List[Dict[str, Any]], te: List[Dict[str, Any]],
              ptr: List[float], pte: List[float]) -> Dict[str, Any]:
        taus = {"safe": select_threshold(tr, ptr, target, PROMOTION_RETENTION),
                "balanced": select_threshold(tr, ptr, target, 0.90)}
        entry: Dict[str, Any] = {"train_rows": len(tr),
                                 "train_positives": sum(1 for r in tr if r[target]),
                                 "test_rows": len(te),
                                 "test_positives": sum(1 for r in te if r[target])}
        if not te or not 0 < entry["test_positives"] < len(te):
            entry["note"] = "no usable test rows"
            return entry
        y = [1 if r.get(target) else 0 for r in te]
        entry.update(_evaluate(te, pte, taus, target))
        return entry

    out["pooled"] = block(train, test, p_train, p_test)
    for form in sorted({call_form(r) for r in train}):
        tr = [r for r in train if call_form(r) == form]
        te = [r for r in test if call_form(r) == form]
        entry = block(tr, te, [calibration_p(model, r) for r in tr],
                      [calibration_p(model, r) for r in te])
        curve = []
        ece = 0.0
        for bucket in CALIBRATION_BUCKETS:
            sub = [r for r in te if streak_bucket(r) == bucket]
            fitted = model["forms"][form]["buckets"][bucket]
            observed = (round(sum(1 for r in sub if r[target]) / len(sub), 3)
                        if sub else None)
            curve.append({"bucket": bucket, "train_n": fitted["n"],
                          "train_met": fitted["met"], "train_p": round(fitted["p"], 3),
                          "test_n": len(sub), "test_observed": observed})
            if sub and observed is not None and te:
                ece += (len(sub) / len(te)) * abs(fitted["p"] - observed)
        entry["curve"] = curve
        entry["ece"] = round(ece, 3)
        out["forms"][form] = entry
    return out


# The second layer's bar, also fixed before a worker exists. The counter already
# spends the veto budget; anything added on top must remove calls that are almost all
# failures, and the state score must show headroom among the calls the counter keeps.
WORKER_MIN_TRAIN = 100
WORKER_MIN_POSITIVES = 8
WORKER_MIN_TEST = 40
WORKER_EXTENSIONS = (0.05, 0.10)
WORKER_EXTENSION_MAX_REMOVED_MET = 0.10
WORKER_EXTENSION_MIN_REMOVED = 5
WORKER_HEADROOM_MIN_AUC = 0.55


def _state_columns() -> List[int]:
    """The mechanism block without the two streak counters the rule already uses."""
    streaks = {MECHANISM_NAMES.index("miss_streak"),
               MECHANISM_NAMES.index("repeat_streak")}
    return [i for i in range(len(MECHANISM_NAMES)) if i not in streaks]


def _extension_ok(stat: Dict[str, Any], auc_kept: Optional[float]) -> bool:
    if stat.get("removed_n", 0) < WORKER_EXTENSION_MIN_REMOVED:
        return False
    removed = stat.get("removed_met")
    if removed is None or removed > WORKER_EXTENSION_MAX_REMOVED_MET:
        return False
    if auc_kept is None or auc_kept < WORKER_HEADROOM_MIN_AUC:
        return False
    ci = stat.get("delta_vs_counter")
    return bool(ci) and ci[0] > 0


def worker_probe(data: Path, target: str = "claim_met") -> Dict[str, Any]:
    """Can state add a second veto layer on the calls the counter keeps?

    The count calibration lost to the counter at equal budget (G66), so the only place
    a learned head can earn its keep is *inside* the counter's kept set: either re-rank
    the same budget or remove a few more calls. A worker that cannot order the kept
    calls (AUC ~0.5) has no headroom no matter how it is trained, so that is printed
    first. Each extension is priced as the success rate of the calls it removes plus a
    paired segment bootstrap of the kept-success difference, against the same bar in
    `_extension_ok` - fitted on train, scored on the frozen fold.
    """
    splits = {name: load_rows([data / ("%s.jsonl" % name)]) for name in ("train", "test")}
    train = [r for r in splits["train"] if r.get(target) is not None]
    test = [r for r in splits["test"] if r.get(target) is not None]
    cols = _state_columns()
    out: Dict[str, Any] = {"data": str(data), "target": target,
                           "extensions": list(WORKER_EXTENSIONS), "forms": {}}
    for form in sorted({call_form(r) for r in train}):
        tr = [r for r in train if call_form(r) == form]
        te = [r for r in test if call_form(r) == form]
        entry: Dict[str, Any] = {
            "train_rows": len(tr),
            "train_positives": sum(1 for r in tr if r[target]),
            "test_rows": len(te),
            "test_positives": sum(1 for r in te if r[target]),
        }
        out["forms"][form] = entry
        if (len(tr) < WORKER_MIN_TRAIN
                or entry["train_positives"] < WORKER_MIN_POSITIVES):
            entry["note"] = ("train has %d rows / %d positives; a worker needs %d / %d"
                             % (len(tr), entry["train_positives"],
                                WORKER_MIN_TRAIN, WORKER_MIN_POSITIVES))
            continue
        if len(te) < WORKER_MIN_TEST or not 0 < entry["test_positives"] < len(te):
            entry["note"] = "test has %d rows / %d positives" % (
                len(te), entry["test_positives"])
            continue
        model = _make_model()
        model.fit([[mechanism_vector(r)[c] for c in cols] for r in tr],
                  [1 if r[target] else 0 for r in tr])
        p = [float(v) for v in model.predict_proba(
            [[mechanism_vector(r)[c] for c in cols] for r in te])[:, 1]]
        y = [1 if r[target] else 0 for r in te]
        counter = _counter_flag(te)
        kept = [i for i in range(len(te)) if not counter[i]]
        entry["headroom"] = {
            "auc_all": _r(auc(y, p)),
            "auc_kept": _r(auc([y[i] for i in kept], [p[i] for i in kept])),
            "kept_rows": len(kept),
            "kept_positives": sum(y[i] for i in kept),
        }
        entry["counter"] = _selective(y, counter)
        budget = sum(counter)
        if budget >= WORKER_EXTENSION_MIN_REMOVED:
            order = sorted(range(len(p)), key=lambda i: p[i])
            head = set(order[:budget])
            stat = _selective(y, [i in head for i in range(len(p))])
            stat["delta_vs_counter"] = _kept_delta_ci(
                te, y, [i in head for i in range(len(p))], counter)
            entry["at_counter_budget"] = stat
        # The extension names: the calls removed on top of the counter's vetoes.
        order_kept = sorted(kept, key=lambda i: p[i])
        for share in WORKER_EXTENSIONS:
            k = max(1, int(share * len(kept)))
            removed = set(order_kept[:k])
            flag = [counter[i] or i in removed for i in range(len(te))]
            stat = _selective(y, flag)
            stat["removed_n"] = k
            stat["removed_met"] = round(sum(y[i] for i in removed) / k, 3)
            stat["delta_vs_counter"] = _kept_delta_ci(te, y, flag, counter)
            stat["promotion"] = _extension_ok(stat, entry["headroom"]["auc_kept"])
            entry["extension_%dpct" % int(round(share * 100))] = stat
        entry["promotion"] = any(
            (entry.get("extension_%dpct" % int(round(share * 100))) or {}).get("promotion")
            for share in WORKER_EXTENSIONS)
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


def _segment_bootstrap(pairs: Dict[str, List[Any]], draws: int = 2000,
                       seed: int = 7) -> Optional[Tuple[float, float]]:
    """Paired 95% CI over segments, so one long session cannot carry a result alone."""
    import random

    segments = [s for s in pairs if pairs[s]]
    if not segments:
        return None

    def contrast(draw: List[str]) -> Optional[float]:
        same: List[bool] = []
        diff: List[bool] = []
        for seg in draw:
            for cls, met in pairs[seg]:
                (same if cls == "same" else diff).append(met)
        if not same or not diff:
            return None
        return sum(diff) / len(diff) - sum(same) / len(same)

    rng = random.Random(seed)
    values = [v for v in (contrast([rng.choice(segments) for _ in segments])
                          for _ in range(draws)) if v is not None]
    if len(values) < 20:
        return None
    values.sort()
    return values[int(0.025 * len(values))], values[int(0.975 * len(values))]


def alternative_probe(data: Path, target: str = "claim_met",
                      veto_at: int = 2) -> Dict[str, Any]:
    """Price what the loop does *instead* of a call the streak gate would veto.

    G51 made `repeat_streak_norm >= 2` the incumbent veto, but every plan said the
    alternative was unpriced (G39/G46/G52/G55), so the veto could be measured but not
    wired. G55 priced it on labels that G60 (call identity) and G65 (a refusal is a miss)
    later invalidated, so this recomputes it on the current corpus: for each acting call
    at the veto threshold, what was the next acting call in the same segment, was it the
    same promise again or a different one, and did it meet.

    Reads are excluded from the comparison because they make no promise. The advantage is
    observational: the loop chose the alternative, it was not assigned. That is stated in
    the verdict rather than hidden, because the causal version needs the randomized arm.
    """
    from agent.gate import RuleGate

    out: Dict[str, Any] = {"data": str(data), "veto_at": veto_at, "folds": {}}
    for fold in ("train", "val", "test"):
        path = data / ("%s.jsonl" % fold)
        rows = load_rows([path])
        acting = [r for r in rows if r.get(target) is not None
                  and not r.get("was_read") and r.get("claim")]
        if not acting:
            continue
        by_segment: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            by_segment.setdefault(row.get("segment") or "", []).append(row)
        for group in by_segment.values():
            group.sort(key=lambda r: r.get("t") or 0)
        position = {(r.get("segment"), r.get("t")): i
                    for s, group in by_segment.items()
                    for i, r in enumerate(group)}

        vetoed = [r for r in acting if (r.get("repeat_streak_norm") or 0) >= veto_at]
        met = [r for r in acting if r.get(target)]
        met_vetoed = [r for r in vetoed if r.get(target)]
        classes: Dict[str, List[bool]] = {"same": [], "different": []}
        pairs: Dict[str, List[Any]] = {}
        per_tool: Dict[str, Dict[str, int]] = {}
        for row in vetoed:
            index = position.get((row.get("segment"), row.get("t")))
            group = by_segment.get(row.get("segment") or "", [])
            tool = per_tool.setdefault(row.get("tool") or "?", {"vetoed": 0, "met": 0})
            tool["vetoed"] += 1
            tool["met"] += 1 if row.get(target) else 0
            if index is None or index + 1 >= len(group):
                continue
            nxt = next((later for later in group[index + 1:]
                        if not later.get("was_read") and later.get("claim")), None)
            if nxt is None:
                continue
            cls = "same" if (RuleGate.key(nxt.get("tool"), nxt.get("args"))
                             == RuleGate.key(row.get("tool"), row.get("args"))) \
                  else "different"
            met_next = bool(nxt.get(target))
            classes[cls].append(met_next)
            pairs.setdefault(row.get("segment") or "", []).append((cls, met_next))
        out["folds"][fold] = {
            "acting": len(acting), "met": len(met),
            "vetoed": len(vetoed),
            "vetoed_share": round(len(vetoed) / len(acting), 4),
            "vetoed_refused": sum(1 for r in vetoed if r.get("refused")),
            "vetoed_met": len(met_vetoed),
            "vetoed_met_rate": round(len(met_vetoed) / len(vetoed), 4) if vetoed else None,
            "successes_removed_by_veto": (round(len(met_vetoed) / len(met), 4)
                                          if met else None),
            "retention": (round(1 - len(met_vetoed) / len(met), 4) if met else None),
            "next_after_veto": {
                cls: {"n": len(vals), "met": sum(vals),
                      "met_rate": round(sum(vals) / len(vals), 4) if vals else None}
                for cls, vals in classes.items()},
            "advantage_different_minus_same":
                (round(sum(classes["different"]) / len(classes["different"])
                       - sum(classes["same"]) / len(classes["same"]), 4)
                 if classes["same"] and classes["different"] else None),
            "paired_ci_95": _segment_bootstrap(pairs),
            "per_tool": per_tool,
        }
    temporal = out["folds"].get("test") or {}
    ci = temporal.get("paired_ci_95")
    out["verdict"] = {
        "alternative_priced": bool(temporal.get("next_after_veto", {}).get("different", {})
                                   .get("n")),
        "ci_excludes_zero": bool(ci and (ci[0] > 0.0 or ci[1] < 0.0)),
        "reads_the_veto_with_this_note": (
            "observational: the loop chose the alternative; only the randomized arm "
            "(G39) makes it causal"),
    }
    return out


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
    ap.add_argument("--rolling-models", action="store_true",
                    help="--rolling plus logistic/forest arms at the rule's veto count")
    ap.add_argument("--shadow", action="store_true",
                    help="read the live gate's would-veto records (agent/gate.py)")
    ap.add_argument("--calibration", action="store_true",
                    help="per-form calibration curve and veto threshold on the frozen fold")
    ap.add_argument("--worker", action="store_true",
                    help="state headroom and second veto layer on the counter's kept calls")
    ap.add_argument("--alternative", action="store_true",
                    help="price what the loop does instead of a call the streak gate would veto")
    ap.add_argument("--target", default="claim_met",
                    help="label used by --within-tool / --gate / --stopping")
    args = ap.parse_args(argv)
    if args.alternative:
        result = alternative_probe(args.data, args.target)
        if args.json:
            print(json.dumps(result, indent=1))
            return 0
        print("data: %s  veto threshold repeat_streak_norm >= %d"
              % (result["data"], result["veto_at"]))
        for fold, entry in sorted(result["folds"].items()):
            print("== %s  %d acting calls, %d met  (%d at the veto threshold, %.1f%%)"
                  % (fold, entry["acting"], entry["met"], entry["vetoed"],
                     100.0 * entry["vetoed_share"]))
            print("   the vetoed calls that met: %d/%d (%s)   refused by rule: %d"
                  % (entry["vetoed_met"], entry["vetoed"],
                     entry["vetoed_met_rate"], entry["vetoed_refused"]))
            print("   successes a wired veto would remove: %s  -> retention %s"
                  % (entry["successes_removed_by_veto"], entry["retention"]))
            for cls, stat in sorted(entry["next_after_veto"].items()):
                print("   next acting call was %-9s n=%-4d met=%-3d (%s)"
                      % (cls, stat["n"], stat["met"], stat["met_rate"]))
            print("   different minus same: %s  95%% CI %s (segment bootstrap)"
                  % (entry["advantage_different_minus_same"], entry["paired_ci_95"]))
            for tool, stat in sorted(entry["per_tool"].items(),
                                     key=lambda kv: -kv[1]["vetoed"])[:5]:
                print("     %-16s vetoed %-4d met %d" % (tool, stat["vetoed"], stat["met"]))
        print("verdict: %s" % result["verdict"])
        return 0
    if args.worker:
        result = worker_probe(args.data, args.target)
        if args.json:
            print(json.dumps(result, indent=1))
            return 0
        print("data: %s  target: %s  extensions %s"
              % (result["data"], result["target"], result["extensions"]))
        for form, entry in sorted(result["forms"].items()):
            print("== %s  train %d/%d met  test %d/%d met"
                  % (form, entry["train_positives"], entry["train_rows"],
                     entry["test_positives"], entry["test_rows"]))
            if entry.get("note"):
                print("   %s" % entry["note"])
                continue
            head = entry["headroom"]
            print("   headroom   AUC all %s  kept %s  (%d kept rows, %d met)"
                  % (head["auc_all"], head["auc_kept"], head["kept_rows"],
                     head["kept_positives"]))
            stat = entry["counter"]
            print("   counter     veto %3d  kept met %-5s retained %-5s"
                  % (stat["vetoed"], stat["kept_success"], stat["successes_retained"]))
            stat = entry.get("at_counter_budget")
            if stat:
                print("   same budget veto %3d  kept met %-5s retained %-5s  CI %s"
                      % (stat["vetoed"], stat["kept_success"],
                         stat["successes_retained"], stat["delta_vs_counter"]))
            for share in result["extensions"]:
                stat = entry.get("extension_%dpct" % int(round(share * 100)))
                if not stat:
                    continue
                print("   extension %2d%% +%d removed (met %-5s) kept met %-5s retained %-5s "
                      "CI %s  promotion %s"
                      % (int(round(share * 100)), stat["removed_n"], stat["removed_met"],
                         stat["kept_success"], stat["successes_retained"],
                         stat["delta_vs_counter"], stat["promotion"]))
            print("   promotion: %s" % entry["promotion"])
        return 0
    if args.calibration:
        result = calibration_probe(args.data, args.target)
        if args.json:
            print(json.dumps(result, indent=1))
            return 0
        print("data: %s  target: %s  train %d judged rows  test %d  alpha %.1f prior %.0f"
              % (result["data"], result["target"], result["train_rows"],
                 result["test_rows"], result["alpha"], result["prior"]))
        blocks = [("pooled", result["pooled"])] + sorted(result["forms"].items())
        for name, entry in blocks:
            print("== %s" % name)
            if entry.get("note"):
                print("   %s" % entry["note"])
                continue
            print("   train %d/%d met  test %d/%d met  base %s  ece %s"
                  % (entry["train_positives"], entry["train_rows"],
                     entry["test_positives"], entry["test_rows"],
                     entry.get("base"), entry.get("ece")))
            for row in entry.get("curve") or []:
                print("   streak %-2s train %3d/%3dm p %-5s | test %3d obs %s"
                      % (row["bucket"], row["train_met"], row["train_n"], row["train_p"],
                         row["test_n"], row["test_observed"]))
            for arm in ("counter", "safe", "balanced",
                        "safe_at_counter_budget", "balanced_at_counter_budget"):
                stat = entry.get(arm)
                if not stat:
                    continue
                print("   %-26s veto %3d (cover %s) vetoed met %-5s kept met %-5s "
                      "succ kept %-5s fail veto %-5s CI %s"
                      % (arm, stat["vetoed"], stat["coverage"], stat["vetoed_success"],
                         stat["kept_success"], stat["successes_retained"],
                         stat["failures_vetoed"], stat.get("delta_vs_counter")))
            promo = entry.get("promotion") or {}
            if promo:
                print("   promotion (counter vetoes %d): safe %s balanced %s"
                      % (promo["counter_vetoes"], promo["safe_beats_counter"],
                         promo["balanced_beats_counter"]))
        return 0
    if args.shadow:
        result = shadow_probe(args.data, args.target)
        if args.json:
            print(json.dumps(result, indent=1))
            return 0
        print("data: %s  decisions %d  tagged %d"
              % (result["data"], result["decisions"], result["tagged"]))
        for tool, stat in sorted(result["tools"].items()):
            print("   %-13s decisions %4d  would-veto %4d  vetoed met %s/%s  next: same %d other %d"
                  % (tool, stat["decisions"], stat["would_veto"],
                     stat["vetoed_met"], stat["vetoed_judged"],
                     stat["next_same_rule"], stat["next_other_rule"]))
        return 0
    if args.rolling or args.rolling_models:
        result = rolling_probe(args.data, args.target, models=args.rolling_models)
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
                for name, arm in sorted((stat.get("models") or {}).items()):
                    if "error" in arm:
                        print("      %-8s %s" % (name, arm["error"]))
                        continue
                    print("      %-8s AUC %.3f | veto %3d vetoed met %s | kept met %s"
                          % (name, arm["auc"], arm["vetoed"], arm["vetoed_met"],
                             arm["kept_met"]))
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
