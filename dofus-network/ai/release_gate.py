"""Release gate: the baseline table you must beat before training is meaningful.

Why this exists
---------------
"Did training help?" is unanswerable without numbers that were produced the same
way, on the same splits, by the same scorer. This script produces that table:

    majority     always the most frequent tool in train       (the floor)
    guard        regex guards only, no model                  (the fast path)
    rule         RulePolicy, the trilingual baseline          (the current product)
    model        an encoder checkpoint, if given              (torch)
    onnx         an INT8 ONNX export, if given                (deployment)

Splits, in order of honesty:

    val          unseen sentence frames               -> the real score (3,073 rows, 0% in train)
    gold         hand-written, 4 languages            -> real phrasing (71 rows, 0% in train)
    val_indist   NOT A HELD-OUT SPLIT                 -> do not headline it

``val_indist.jsonl`` is measured to be 800/800 byte-identical to rows of ``train.jsonl`` (2026-11-02), and
``ai/prepare_encoder_sft.py:13`` says so by design -- "small random sample of train frames, for the
memorisation" check. It is a recall probe. Any number taken from it (the encoder scores 98.8 / 82.1 there
against 94.1 / 69.7 on ``val``) says how well the model memorised, not how well it understands, so it is
listed last and labelled here rather than presented as the most honest column.

Note on the guard baseline: ``ai.guard_policy.GuardPolicy`` is model-free. It
picks a tool from fixed regex guards and copies arguments from the goal by regex
or the entity index; anything it cannot read is left missing and the schema
decides. There is no model to load, so the "fast path" is actually fast, and
the baseline is reproducible on a machine with no model cache.

Usage (from ``dofus-network``)::

    python -m ai.release_gate                       # baselines on all three splits
    python -m ai.release_gate --checkpoint models/encoder30/model.pt
    python -m ai.release_gate --onnx models/encoder30/encoder_int8.onnx \
        --min-call-accuracy 0.65
    python -m ai.release_gate --onnx models/encoder30/encoder_int8.onnx --glicko

Archetype-stratified gate (``--glicko``)
---------------------------------------
A single accuracy number lets a model gain 2 points on ``hdv`` (1,088 val rows) while
getting much worse on ``fight`` (37 rows). With ``--glicko`` every case becomes a
match between the challenger and the fixed ``rule`` reference: 1.0 when only the
challenger's call is correct, 0.0 when only the reference's is, 0.5 otherwise. One
rating period is one tool family, so each archetype gets its own Glicko-2 rating and
rating deviation. The gate passes only when **every** family with at least
``--min-archetype-support`` cases has ``r - k*RD >= 1500 + margin`` -- the lower
confidence bound, not the point estimate. A family with 3 cases can win 3-0 and
still fail (r = 1763, RD = 176, bound = 1412), which is the point: small families
must not be released on thin evidence.

The reference rating is pinned; it is a measuring stick, not a trained competitor.
Scores of 0.5 are kept, so the many cases where both policies are right (or both
wrong) still shrink the deviation instead of being silently discarded.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.tool_eval import evaluate
from ai.data_splits import load_jsonl

SFT_DIR = REPO_ROOT / "data" / "core_sft_v4"
SPLITS = {
    "val_indist": SFT_DIR / "val_indist.jsonl",
    "val": SFT_DIR / "val.jsonl",
    "gold": SFT_DIR / "gold.jsonl",
}

Prediction = Optional[Dict[str, Any]]
Policy = Callable[[Dict[str, Any]], Prediction]


# ---------------------------------------------------------------------------
# baselines
# ---------------------------------------------------------------------------


def majority_policy(rows: Sequence[Dict[str, Any]]) -> Policy:
    """Always answer the most frequent tool, with default args. The floor."""
    counts = Counter(str(row.get("tool")) for row in rows)
    if not counts:
        return lambda case: None
    tool = counts.most_common(1)[0][0]
    return lambda case: {"tool": tool, "args": {}}


def guard_policy() -> Policy:
    """Regex guards only. No model, no fallback, no cache."""
    from ai.guard_policy import GuardPolicy, _guard_tool

    policy = GuardPolicy()

    def predict(case: Dict[str, Any]) -> Prediction:
        goal = str(case.get("goal") or "")
        tool = _guard_tool(goal)
        if not tool:
            return None
        args, _meta = policy._arguments(tool, goal)
        return {"tool": tool, "args": args or {}}

    return predict


def rule_policy() -> Policy:
    """The trilingual rule baseline that ships today."""
    from agent.runtime import RulePolicy

    policy = RulePolicy()

    def predict(case: Dict[str, Any]) -> Prediction:
        call = policy.choose(str(case.get("goal") or ""))
        if call is None:
            return None
        return {"tool": call.tool, "args": call.args}

    return predict


def torch_policy(checkpoint: Path, device: str = "cpu") -> Policy:
    """A trained encoder checkpoint (torch)."""
    import torch

    from ai.encoder_policy import (
        ENCODER_SYSTEM_PROMPT, EncoderDataset, decode_batch, load_checkpoint,
    )

    model, tokenizer, meta = load_checkpoint(checkpoint, device=torch.device(device))
    system_prompt = meta.get("system_prompt") or ENCODER_SYSTEM_PROMPT

    def predict(case: Dict[str, Any]) -> Prediction:
        dataset = EncoderDataset([case], tokenizer, model.config.max_seq_len, system_prompt)
        if not dataset.items:
            return None
        batch = next(dataset.batches(1, shuffle=False))
        return decode_batch(model, tokenizer, batch)[0]

    return predict


def onnx_policy(onnx_path: Path) -> Policy:
    """An INT8 ONNX export through onnxruntime — the deployment path."""
    from ai.onnx_policy import OnnxEncoder

    encoder = OnnxEncoder(onnx_path)

    def predict(case: Dict[str, Any]) -> Prediction:
        return encoder.predict_one(case)

    return predict


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def predict_all(policy: Policy, cases: Sequence[Dict[str, Any]]) -> Dict[str, Prediction]:
    """Raw per-case predictions, shared by the accuracy table and the rating matches."""
    predictions: Dict[str, Prediction] = {}
    for case in cases:
        try:
            predictions[str(case.get("id"))] = policy(case)
        except Exception as exc:  # noqa: BLE001 - a baseline must never abort the gate
            predictions[str(case.get("id"))] = None
            if exc.__class__.__name__ not in ("RuntimeError", "KeyError"):
                raise
    return predictions


def score(policy: Policy, cases: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    predictions = predict_all(policy, cases)
    report = evaluate(predictions, cases)
    totals = report["totals"]
    cases_n = max(totals.get("cases", 0), 1)
    return {
        "cases": totals.get("cases", 0),
        "tool": totals.get("tool_ok", 0) / cases_n,
        "call": totals.get("call_ok", 0) / cases_n,
        "by_lang": {
            lang: {
                "cases": stats["cases"],
                "tool": stats["tool_ok"] / max(stats["cases"], 1),
                "call": stats["call_ok"] / max(stats["cases"], 1),
            }
            for lang, stats in report["by_lang"].items()
        },
        "failures": len(report.get("failures") or []),
    }


def _format(name: str, result: Dict[str, Any]) -> str:
    return (f"  {name:12s} {result['cases']:5d}  {result['tool']:7.1%}  {result['call']:7.1%}")


def _lang_line(result: Dict[str, Any]) -> str:
    parts = []
    for lang, stats in sorted(result["by_lang"].items()):
        parts.append(f"{lang}:{stats['call']:.0%}({stats['cases']})")
    return "      per-language call: " + "  ".join(parts)


# ---------------------------------------------------------------------------
# archetype-stratified Glicko-2 gate
# ---------------------------------------------------------------------------

# The reference is pinned: it is a measuring stick, not a player being updated.
GLICKO_REFERENCE_R = 1500.0
GLICKO_REFERENCE_RD = 50.0


def archetype(case: Dict[str, Any]) -> str:
    """Tool family. `hdv.scan` and `hdv.price` share a family; `lang` stays in the
    accuracy table instead, so a family is not split into unmeasurable cells."""
    return str(case.get("tool") or "").split(".", 1)[0] or "unknown"


def glicko_archetypes(
    candidate: Dict[str, Prediction],
    reference: Dict[str, Prediction],
    cases: Sequence[Dict[str, Any]],
    min_support: int = 20,
    k: float = 2.0,
    margin: float = 0.0,
) -> Dict[str, Any]:
    """Rate a challenger against the fixed `rule` reference, one period per family.

    A case is a win only when the challenger's call is correct *and* the reference's
    is not; otherwise it is a loss or a half-point. The half-points are what keep the
    deviation honest: discarding every case where both policies agree would make
    1,000 easy rows look like 1,000 pieces of evidence that the challenger is better.
    """
    from agent.tool_eval import score_one
    from ai.glicko2 import Rating, rate, win_rate

    groups: Dict[str, List[Any]] = defaultdict(list)
    for case in cases:
        key = str(case.get("id"))
        _, challenger_ok = score_one(case, candidate.get(key))
        _, reference_ok = score_one(case, reference.get(key))
        if challenger_ok and not reference_ok:
            outcome = 1.0
        elif reference_ok and not challenger_ok:
            outcome = 0.0
        else:
            outcome = 0.5
        groups[archetype(case)].append(
            (GLICKO_REFERENCE_R, GLICKO_REFERENCE_RD, outcome))

    floor = GLICKO_REFERENCE_R + margin
    rows = []
    for name, matches in groups.items():
        rated = rate(Rating(), matches)
        supported = len(matches) >= min_support
        lower = rated.lower(k)
        rows.append({
            "archetype": name,
            "matches": len(matches),
            "score_rate": round(win_rate(matches), 4),
            "r": round(rated.r, 2),
            "rd": round(rated.rd, 2),
            "lower": round(lower, 2),
            "supported": supported,
            "pass": supported and lower >= floor,
        })
    rows.sort(key=lambda d: -d["matches"])
    supported = [r for r in rows if r["supported"]]
    return {
        "rows": rows,
        "floor": floor,
        "k": k,
        "min_support": min_support,
        "supported_families": len(supported),
        "passed": bool(supported) and all(r["pass"] for r in supported),
    }


def _print_glicko(name: str, report: Dict[str, Any]) -> None:
    print("\n  glicko-2 [%s]  floor %.0f  k %.1f  min support %d" % (
        name, report["floor"], report["k"], report["min_support"]))
    print("  %-14s %7s %9s %9s %8s %9s %6s" % (
        "family", "matches", "score", "rating", "RD", "r-k*RD", "gate"))
    for r in report["rows"]:
        print("  %-14s %7d %8.1f%% %9.2f %8.2f %9.2f %6s" % (
            r["archetype"], r["matches"], 100 * r["score_rate"], r["r"], r["rd"],
            r["lower"], "PASS" if r["pass"] else ("skip" if not r["supported"] else "FAIL")))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", default=None, help="encoder .pt to score")
    parser.add_argument("--onnx", default=None, help="encoder ONNX to score")
    parser.add_argument("--splits", nargs="*", default=list(SPLITS),
                        help="which splits to run (default: all three)")
    parser.add_argument("--cases", default=None,
                        help="score a custom jsonl instead of the built-in splits")
    parser.add_argument("--min-call-accuracy", type=float, default=None,
                        help="exit 1 when the best model is below this on `val`")
    parser.add_argument("--limit", type=int, default=0,
                        help="score only the first N cases per split (quick check)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--glicko", action="store_true",
                        help="archetype-stratified Glicko-2 gate against the rule baseline")
    parser.add_argument("--glicko-split", default="val",
                        help="split the rating gate runs on (default: val)")
    parser.add_argument("--min-archetype-support", type=int, default=20,
                        help="families with fewer matches are reported but not gated")
    parser.add_argument("--glicko-k", type=float, default=2.0,
                        help="gate on r - k*RD (default: 2.0)")
    parser.add_argument("--glicko-margin", type=float, default=0.0,
                        help="require r - k*RD >= 1500 + margin")
    args = parser.parse_args(argv)

    train_rows = load_jsonl(SFT_DIR / "train.jsonl")
    baselines: List[Tuple[str, Policy]] = [
        ("majority", majority_policy(train_rows)),
        ("guard", guard_policy()),
        ("rule", rule_policy()),
    ]
    if args.checkpoint:
        baselines.append(("model", torch_policy(Path(args.checkpoint))))
    if args.onnx:
        try:
            baselines.append(("onnx", onnx_policy(Path(args.onnx))))
        except ImportError as exc:
            print(f"  (skipping onnx baseline: {exc})")

    targets = [(name, SPLITS[name]) for name in args.splits if name in SPLITS]
    if args.cases:
        targets = [("custom", Path(args.cases))]

    results: Dict[str, Dict[str, Dict[str, Any]]] = {}
    split_rows: Dict[str, List[Dict[str, Any]]] = {}
    for split_name, path in targets:
        rows = load_jsonl(path)
        if args.limit and len(rows) > args.limit:
            # Evenly spaced sample: taking the head of a sorted split is not a
            # sample, it is one language and one tool.
            stride = len(rows) / float(args.limit)
            rows = [rows[int(i * stride)] for i in range(args.limit)]
        if not rows:
            print(f"  ({split_name}: no rows at {path})")
            continue
        split_rows[split_name] = rows
        print(f"\n{split_name}  ({path.name}, {len(rows)} cases)")
        print(f"  {'policy':12s} {'cases':>5s}  {'tool':>7s}  {'call':>7s}")
        results[split_name] = {}
        for name, policy in baselines:
            result = score(policy, rows)
            results[split_name][name] = result
            print(_format(name, result))
            if name != "majority":
                print(_lang_line(result))

    glicko: Dict[str, Any] = {}
    glicko_passed = True
    if args.glicko:
        rating_split = args.glicko_split if args.glicko_split in split_rows else (
            "custom" if "custom" in split_rows else None)
        challengers = [(n, p) for n, p in baselines if n in ("model", "onnx")]
        reference_policy = next((p for n, p in baselines if n == "rule"), None)
        if rating_split is None:
            print("\nglicko: split %r was not scored; gate inactive" % args.glicko_split)
        elif not challengers or reference_policy is None:
            print("\nglicko: no model/onnx challenger scored; gate inactive")
        else:
            cases = split_rows[rating_split]
            ref_preds = predict_all(reference_policy, cases)
            for name, policy in challengers:
                rep = glicko_archetypes(
                    predict_all(policy, cases), ref_preds, cases,
                    min_support=args.min_archetype_support,
                    k=args.glicko_k, margin=args.glicko_margin)
                glicko[name] = rep
                _print_glicko(name, rep)
                ok = sum(1 for r in rep["rows"] if r["supported"] and r["pass"])
                print("  -> %s: %d/%d supported families pass -> %s" % (
                    name, ok, rep["supported_families"], "PASS" if rep["passed"] else "FAIL"))
                glicko_passed = glicko_passed and rep["passed"]

    if args.json:
        payload: Dict[str, Any] = dict(results)
        if glicko:
            payload["glicko"] = {"split": rating_split, "candidates": glicko}
        print(json.dumps(payload, indent=2))

    failed = False
    if args.min_call_accuracy is not None:
        reference = results.get("val") or results.get("custom") or {}
        candidates = {k: v for k, v in reference.items() if k in ("model", "onnx")}
        if not candidates:
            print("\nno model scored; cannot gate on call accuracy")
        else:
            best_name, best = max(candidates.items(), key=lambda kv: kv[1]["call"])
            passed = best["call"] >= args.min_call_accuracy
            print(f"\ngate: {best_name} call {best['call']:.1%} "
                  f"{'≥' if passed else '<'} {args.min_call_accuracy:.1%} -> "
                  f"{'PASS' if passed else 'FAIL'}")
            failed = failed or not passed
    if args.glicko and glicko:
        print("glicko archetype gate: %s" % ("PASS" if glicko_passed else "FAIL"))
        failed = failed or not glicko_passed
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
