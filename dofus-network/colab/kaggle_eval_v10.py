"""Kaggle kernel: score encoder v10 across all six benchmark separations.

Run AFTER ``tajdinenizar/thx-01-encoder-train`` (v10) has completed and written
its artifacts. It does NOT retrain -- everything here is scoring and
measurement, not training:

  * v10 predictions already exist in ``eval_{val_indist,val,gold}.json``
    (three comparators, solvable subset, unsolvable share, per-tool and
    per-language) via ``ai.diag_call_budget.audit`` -- pure scoring, no GPU.
  * resource use and latency via ``ai.encoder_cost_probe`` on the v10 model
    (CPU -- that is the deployment target) and a reading of the ONNX artifact
    sizes.

The v10 training kernel attaches its own outputs as a kernel source, so the
checkpoint and eval reports are local. We still pull a second time to be sure
the artifacts are present (Kaggle's output fetch is idempotent), with a retry
loop because the API occasionally truncates a large transfer.

Output: ``/kaggle/working/benchmark_v10.json`` (a few KB) plus a printed summary.
The downstream report step downloads only this file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/kaggle/input/thx01-encoder-core")
if not (ROOT / "ai" / "encoder_policy.py").exists():
    hits = sorted(Path("/kaggle/input").rglob("ai/encoder_policy.py"))
    if hits:
        ROOT = hits[0].parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
WORK = Path("/kaggle/working") if Path("/kaggle").exists() else Path(".")

V10_SRC = "tajdinenizar/thx-01-encoder-train"
SPLITS = ("val_indist", "val", "gold")


def _pull_v10() -> Path:
    """Locate v10's artifacts. When this kernel is run with the v10 training
    kernel attached as a *kernel source*, the artifacts are already mounted
    under ``/kaggle/input/<slug>`` -- use that first. Otherwise (or if the mount
    is incomplete) fetch them through the API, retrying the occasional
    truncated transfer. Returns the directory they landed in."""
    # Common kernel-source mount locations for the v10 training kernel output.
    candidates = [
        WORK / "v10src",
        Path("/kaggle/input/thx-01-encoder-train/encoder30"),
        Path("/kaggle/input/thx-01-encoder-train"),
        Path("/kaggle/input/model/encoder30"),
    ]
    for cand in candidates:
        model = cand / "model.pt"
        if (cand / "eval_val.json").exists() and model.exists() \
                and model.stat().st_size > 100_000_000:
            print(f"v10 artifacts found at {cand}", flush=True)
            return cand

    out = WORK / "v10src"
    for attempt in range(1, 9):
        subprocess.run(
            ["kaggle", "kernels", "output", V10_SRC, "-p", str(out)],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        model = out / "model.pt"
        eval_ok = (out / "eval_val.json").exists()
        model_ok = model.exists() and model.stat().st_size > 100_000_000
        # eval_val.json is small; model.pt is ~120 MB and the API occasionally
        # truncates a large transfer, leaving it far smaller.
        if model_ok and eval_ok:
            print(f"pulled v10 artifacts (attempt {attempt})", flush=True)
            return out
        print(f"pull attempt {attempt} incomplete "
              f"(model_ok={model_ok}, eval_ok={eval_ok}); retrying", flush=True)
        time.sleep(15)
    raise SystemExit("could not pull v10 artifacts after retries")


def _split_block(report_path: Path, corpus_dir: Path, split: str) -> dict:
    """Three comparators + solvable subset + unsolvable share + per-tool/lang."""
    from ai.diag_call_budget import audit
    from ai.data_splits import load_jsonl

    report = json.loads(report_path.read_text(encoding="utf-8"))
    predictions = report.get("predictions") or []
    rows = {str(r["id"]): r
            for r in load_jsonl(corpus_dir / f"{split}.jsonl")}
    totals = report.get("totals", {})
    cases = max(totals.get("cases", len(predictions)), 1)

    raw = audit(predictions, rows, postprocess=False, resolvers=False)
    serving = audit(predictions, rows, postprocess=True, resolvers=True)

    def _frac(n, d):
        return round(n / max(d, 1), 4)

    def _table(audit_r):
        # strict = ok_all (raw exact-dict); exact = ok_exact (canonicalised
        # exact-dict); gate = ok_canon (score_one: canonical + containment).
        # ``call_ok`` is a *per-tool* Counter, not the gate aggregate.
        return {
            "strict": _frac(audit_r["ok_all"], audit_r["total"]),
            "exact": _frac(audit_r["ok_exact"], audit_r["total"]),
            "gate": _frac(audit_r["ok_canon"], audit_r["total"]),
            "tool": _frac(totals.get("tool_ok", 0), cases),
            "solvable_subset_gate": _frac(audit_r["ok_solv_canon"], audit_r["solvable"]),
            "solvable_subset_exact": _frac(audit_r["ok_solv_exact"], audit_r["solvable"]),
            "unsolvable_share": _frac(audit_r["n_unsolvable"], audit_r["total"]),
            "unsolvable_reasons": dict(audit_r["unsolvable"]),
            # per-tool EXACT-correct (the regression-prone view: an aggregate
            # gain can hide a capability that fell off a cliff).
            "by_tool_exact": {
                tool: {
                    "exact_ok": ok_e,
                    "exact_frac": _frac(ok_e, audit_r["total"]),
                }
                for tool, ok_e in audit_r["by_tool_exact"].items()
            },
            "by_lang": {
                lang: {
                    "cases": d["cases"],
                    "tool": _frac(d["tool_ok"], d["cases"]),
                    "gate": _frac(d["call_ok"], d["cases"]),
                    "exact": _frac(d["call_exact"], d["cases"]),
                }
                for lang, d in audit_r["by_lang"].items()
            },
        }

    return {
        "cases": cases,
        "tool_ok": totals.get("tool_ok", 0),
        "raw": _table(raw),
        "serving": _table(serving),
    }


def main() -> None:
    src = _pull_v10()
    data = ROOT / "data" / "core_sft_v4"

    benchmark: dict = {"model": "encoder v10 (LR warmup)", "splits": {}}
    for split in SPLITS:
        report_path = src / f"eval_{split}.json"
        if not report_path.exists():
            print(f"skip {split}: no eval report", flush=True)
            continue
        print(f"scoring {split} ...", flush=True)
        benchmark["splits"][split] = _split_block(report_path, data, split)

    # Resource use / latency: CPU is the deployment target.
    cp = src / "model.pt"
    tok = src / "tokenizer.json"
    policy = src / "policy.json"
    onnx_fp32 = src / "encoder.onnx"
    onnx_int8 = src / "encoder_int8.onnx"
    probe_out = WORK / "cost_v10.json"
    if cp.exists():
        subprocess.run([
            sys.executable, "-m", "ai.encoder_cost_probe",
            "--checkpoint", str(cp),
            "--tokenizer", str(tok),
            "--policy-json", str(policy) if policy.exists() else "",
            "--cases", str(data / "val.jsonl"),
            "--limit", "300", "--warmup", "20",
            "--device", "cpu", "--threads", "4",
            "--out", str(probe_out),
        ], check=False)
    if probe_out.exists():
        benchmark["resource"] = json.loads(probe_out.read_text(encoding="utf-8"))

    # ONNX artifact sizes (architecture-bound; reported for the gate).
    for name, p in (("fp32_onnx_mb", onnx_fp32), ("int8_onnx_mb", onnx_int8)):
        benchmark[name] = round(p.stat().st_size / 1e6, 2) if p.exists() else None

    # Prior-art baseline for the regression view (different corpus: v8 was scored
    # on gen-2 val; v10 on gen-3 val). Flagged as directional, not apples-to-apples.
    benchmark["v8_baseline_gen2_val"] = {
        "note": "v8 scored on gen-2 val (3,056 rows); v10 scored on gen-3 val "
                "(3,073 rows). Corpora differ, so this is a directional check "
                "on the warmup-fix effect, not a matched comparison.",
        "tool": 0.9467, "gate_raw": 0.8586, "gate_serving": 0.8825,
    }

    out_path = WORK / "benchmark_v10.json"
    out_path.write_text(json.dumps(benchmark, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print("\n===== benchmark_v10.json =====")
    print(json.dumps(benchmark, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
