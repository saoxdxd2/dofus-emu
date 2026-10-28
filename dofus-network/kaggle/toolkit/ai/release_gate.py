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

Splits, in increasing order of honesty:

    val_indist   same sentence frames, new entities   -> memorisation ceiling
    val          unseen sentence frames               -> the real score
    gold         hand-written, 4 languages            -> real phrasing

Note on the guard baseline: ``ThxPolicy._arguments`` falls back to the THX-01
model (322M) whenever an argument needs a number or a price. Measured on this
machine that is a **65 s cold load** and ~2.5 s per numeric call, so the "fast
path" is slower than the encoder it is supposed to replace. The gate therefore
disables the model explicitly and scores the guards as they really are when no
model is available: regexes only, missing args allowed to fail.

Usage (from ``dofus-network``)::

    python -m ai.release_gate                       # baselines on all three splits
    python -m ai.release_gate --checkpoint models/encoder30/model.pt
    python -m ai.release_gate --onnx models/encoder30/encoder_int8.onnx \
        --min-call-accuracy 0.65
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
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


def guard_policy(model_free: bool = True) -> Policy:
    """Regex guards only, with the THX-01 fallback explicitly disabled.

    Without this, any goal whose arguments need a number silently loads a 322M
    model — the opposite of a fast path.
    """
    from ai.thx_policy import ThxPolicy, _guard_tool

    policy = ThxPolicy()
    if model_free:
        def _disabled(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
            raise RuntimeError("model disabled: model-free guard baseline")

        policy._decide = _disabled  # type: ignore[assignment]

    def predict(case: Dict[str, Any]) -> Prediction:
        goal = str(case.get("goal") or "")
        tool = _guard_tool(goal)
        if not tool:
            return None
        try:
            args, _meta = policy._arguments(tool, goal)
        except Exception:  # noqa: BLE001 - thx01 missing or extraction failed
            args = {}
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


def score(policy: Policy, cases: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    predictions: Dict[str, Prediction] = {}
    for case in cases:
        try:
            predictions[str(case.get("id"))] = policy(case)
        except Exception as exc:  # noqa: BLE001 - a baseline must never abort the gate
            predictions[str(case.get("id"))] = None
            if exc.__class__.__name__ not in ("RuntimeError", "KeyError"):
                raise
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
        print(f"\n{split_name}  ({path.name}, {len(rows)} cases)")
        print(f"  {'policy':12s} {'cases':>5s}  {'tool':>7s}  {'call':>7s}")
        results[split_name] = {}
        for name, policy in baselines:
            result = score(policy, rows)
            results[split_name][name] = result
            print(_format(name, result))
            if name != "majority":
                print(_lang_line(result))

    if args.json:
        print(json.dumps(results, indent=2))

    if args.min_call_accuracy is not None:
        reference = results.get("val") or results.get("custom") or {}
        candidates = {k: v for k, v in reference.items() if k in ("model", "onnx")}
        if not candidates:
            print("\nno model scored; cannot gate on call accuracy")
            return 0
        best_name, best = max(candidates.items(), key=lambda kv: kv[1]["call"])
        passed = best["call"] >= args.min_call_accuracy
        print(f"\ngate: {best_name} call {best['call']:.1%} "
              f"{'≥' if passed else '<'} {args.min_call_accuracy:.1%} -> "
              f"{'PASS' if passed else 'FAIL'}")
        return 0 if passed else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
