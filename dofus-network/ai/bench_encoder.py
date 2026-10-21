"""Benchmark an encoder checkpoint on the four spoken languages.

What is measured, and why each one matters:

    tool accuracy      did it pick the right tool at all
    call accuracy      right tool AND every argument correct  <- the real target
    top-3 tool         is the right tool at least in the shortlist
    per language       fr / en / es / de, because an average hides a regression
    confidence split   mean max-probability on correct vs wrong answers; a model
                       that is wrong at 95% confidence cannot be gated safely
    latency            p50/p95 per example, batch 1, CPU

Legacy checkpoints: a checkpoint saved before a head was added cannot load
strictly. ``--allow-legacy`` zeroes the missing heads, warns loudly, and records
that in the report — the numbers are still a valid measurement of *those weights*.

Usage::

    python -m ai.bench_encoder --checkpoint <model.pt> --allow-legacy
    python -m ai.bench_encoder --checkpoint <model.pt> --splits gold val
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch

from agent.tool_eval import evaluate
from ai.data_splits import load_jsonl

SFT_DIR = REPO_ROOT / "data" / "core_sft_v4"
SPLITS = {
    "gold": SFT_DIR / "gold.jsonl",
    "val": SFT_DIR / "val.jsonl",
    "val_indist": SFT_DIR / "val_indist.jsonl",
}
LANG_ORDER = ("fr", "en", "es", "de", "auto")


def load_legacy(path: Path, device: torch.device) -> Tuple[Any, Any, Dict[str, Any], List[str]]:
    """Load a checkpoint, tolerating heads that did not exist when it was trained."""
    from tokenizers import Tokenizer

    from ai.encoder_policy import ENCODER_SYSTEM_PROMPT, EncoderConfig, EncoderPolicy

    blob = torch.load(path, map_location="cpu", weights_only=False)
    config = EncoderConfig(**blob["config"])
    model = EncoderPolicy(config)
    missing, unexpected = model.load_state_dict(blob["state_dict"], strict=False)
    missing = list(missing)
    if missing:
        # A head added after training starts at its initial distribution. For the
        # lexical tool head that is `normal(0, 0.02)` around zero, which adds noise
        # to the tool logits; zeroing it reproduces the architecture the weights
        # were trained under.
        with torch.no_grad():
            for name in missing:
                if name.endswith(".weight") or name.endswith(".bias"):
                    tensor = model.get_parameter(name)
                    tensor.zero_()
    model.to(device).eval()
    tokenizer = Tokenizer.from_str(blob["tokenizer_json"])
    meta = blob.get("meta") or {}
    meta.setdefault("system_prompt", ENCODER_SYSTEM_PROMPT)
    return model, tokenizer, meta, missing + list(unexpected)


def _percentile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


@torch.no_grad()
def run_split(
    model: Any,
    tokenizer: Any,
    rows: Sequence[Dict[str, Any]],
    system_prompt: str,
    device: torch.device,
    batch_size: int = 16,
) -> Dict[str, Any]:
    from ai.encoder_policy import TOOL_NAMES, EncoderDataset, decode_batch

    dataset = EncoderDataset(rows, tokenizer, model.config.max_seq_len, system_prompt)
    if not dataset.items:
        return {"cases": 0, "skipped": len(rows)}

    predictions: Dict[str, Optional[Dict[str, Any]]] = {}
    confidences: List[Tuple[float, bool, bool]] = []   # (max_prob, tool_ok, call_ok)
    latencies: List[float] = []
    top3_hits = 0
    top3_total = 0
    offset = 0

    for batch in dataset.batches(batch_size, shuffle=False):
        input_ids = batch["input_ids"].to(device)
        attention = batch["attention_mask"].to(device)
        started = time.perf_counter()
        output = model(input_ids, attention)
        latencies.append((time.perf_counter() - started) * 1000.0 / len(batch["input_ids"]))

        probs = torch.softmax(output["tool"], dim=-1)
        decoded = decode_batch(model, tokenizer, batch, output=output)
        chunk = dataset.items[offset:offset + len(decoded)]
        for item, prediction, row_probs in zip(chunk, decoded, probs):
            case_id = str(item["id"])
            predictions[case_id] = prediction
            expected = TOOL_NAMES.index(item["tool"]) if item["tool"] in TOOL_NAMES else -1
            if expected >= 0:
                top = torch.topk(row_probs, k=min(3, row_probs.numel())).indices.tolist()
                top3_hits += int(expected in top)
                top3_total += 1
            tool_ok = prediction["tool"] == item["tool"]
            confidences.append((float(row_probs.max()), tool_ok, False))
        offset += len(decoded)

    cases = [
        {"id": str(row.get("id")), "lang": row.get("lang") or "auto",
         "goal": row.get("goal") or "", "tool": row.get("tool"),
         "args": row.get("args") or {}}
        for row in rows if str(row.get("id")) in predictions
    ]
    report = evaluate(predictions, cases)
    totals = report["totals"]
    case_count = max(totals["cases"], 1)

    conf_correct = [c[0] for c in confidences if c[1]]
    conf_wrong = [c[0] for c in confidences if not c[1]]

    per_lang: Dict[str, Dict[str, Any]] = {}
    for lang in LANG_ORDER:
        stats = report["by_lang"].get(lang)
        if not stats:
            continue
        per_lang[lang] = {
            "cases": stats["cases"],
            "tool": round(stats["tool_ok"] / max(stats["cases"], 1), 4),
            "call": round(stats["call_ok"] / max(stats["cases"], 1), 4),
        }

    per_tool: Dict[str, Dict[str, Any]] = {}
    for tool, stats in sorted(report["by_tool"].items()):
        per_tool[tool] = {
            "cases": stats["cases"],
            "tool": round(stats["tool_ok"] / max(stats["cases"], 1), 4),
            "call": round(stats["call_ok"] / max(stats["cases"], 1), 4),
        }

    return {
        "cases": totals["cases"],
        "skipped_too_long": dataset.skipped,
        "tool_accuracy": round(totals["tool_ok"] / case_count, 4),
        "call_accuracy": round(totals["call_ok"] / case_count, 4),
        "top3_tool": round(top3_hits / max(top3_total, 1), 4),
        "by_lang": per_lang,
        "by_tool": per_tool,
        "confidence": {
            "mean_correct": round(statistics.fmean(conf_correct), 4) if conf_correct else None,
            "mean_wrong": round(statistics.fmean(conf_wrong), 4) if conf_wrong else None,
            "separation": (round(statistics.fmean(conf_correct) - statistics.fmean(conf_wrong), 4)
                           if conf_correct and conf_wrong else None),
        },
        "latency_ms": {
            "p50": round(_percentile(latencies, 0.5) or 0.0, 1),
            "p95": round(_percentile(latencies, 0.95) or 0.0, 1),
            "batches": len(latencies),
        },
        "failures": [
            {"id": f["id"], "lang": f["lang"], "goal": f["goal"],
             "expected": f["expected"], "predicted": f["predicted"]}
            for f in (report.get("failures") or [])[:12]
        ],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--splits", nargs="*", default=["gold", "val", "val_indist"])
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--allow-legacy", action="store_true",
                        help="zero heads missing from an older checkpoint")
    parser.add_argument("--out", default=None, help="write the full report as JSON")
    parser.add_argument("--show-failures", type=int, default=6)
    args = parser.parse_args(argv)

    torch.set_num_threads(args.threads)
    device = torch.device(args.device)
    checkpoint = Path(args.checkpoint)

    started = time.perf_counter()
    if args.allow_legacy:
        model, tokenizer, meta, missing = load_legacy(checkpoint, device)
    else:
        from ai.encoder_policy import load_checkpoint
        model, tokenizer, meta = load_checkpoint(checkpoint, device)
        missing = []
    load_seconds = time.perf_counter() - started

    params = sum(p.numel() for p in model.parameters())
    print(f"checkpoint : {checkpoint}")
    print(f"params     : {params / 1e6:.2f}M   disk {checkpoint.stat().st_size / 1e6:.1f} MB")
    print(f"config     : d={model.config.d_model} L={model.config.n_layers} "
          f"vocab={model.config.vocab_size} seq={model.config.max_seq_len}")
    print(f"loaded in  : {load_seconds:.2f}s on {device}"
          + (f"   LEGACY: zeroed {len(missing)} missing params {missing}" if missing else ""))
    print()

    report: Dict[str, Any] = {
        "checkpoint": str(checkpoint),
        "params": params,
        "config": {
            "d_model": model.config.d_model, "n_layers": model.config.n_layers,
            "vocab_size": model.config.vocab_size, "max_seq_len": model.config.max_seq_len,
        },
        "legacy_zeroed": missing,
        "load_seconds": round(load_seconds, 2),
        "splits": {},
    }

    for name in args.splits:
        path = SPLITS.get(name)
        if path is None or not path.exists():
            print(f"  ({name}: missing at {path})")
            continue
        rows = load_jsonl(path)
        result = run_split(model, tokenizer, rows, meta["system_prompt"], device,
                           batch_size=args.batch_size)
        report["splits"][name] = result
        print(f"{name}  ({result['cases']} cases, {result.get('skipped_too_long', 0)} skipped)")
        print(f"  tool {result['tool_accuracy']:6.1%}   call {result['call_accuracy']:6.1%}   "
              f"top3 {result['top3_tool']:6.1%}   "
              f"latency p50 {result['latency_ms']['p50']} ms / p95 {result['latency_ms']['p95']} ms")
        conf = result["confidence"]
        print(f"  confidence: correct {conf['mean_correct']}   wrong {conf['mean_wrong']}   "
              f"separation {conf['separation']}")
        langs = "  ".join(f"{lang}:{stats['tool']:.0%}/{stats['call']:.0%}({stats['cases']})"
                          for lang, stats in result["by_lang"].items())
        print(f"  per language (tool/call): {langs}")
        worst = sorted(result["by_tool"].items(), key=lambda kv: kv[1]["call"])[:3]
        print("  weakest tools: " + "  ".join(
            f"{tool}:{stats['call']:.0%}({stats['cases']})" for tool, stats in worst))
        for failure in result["failures"][:args.show_failures]:
            print(f"    MISS [{failure['lang']}] {failure['goal']!r}")
            print(f"         expected {failure['expected']}")
            print(f"         got      {failure['predicted']}")
        print()

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"report -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
