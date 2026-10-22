"""Kaggle eval kernel: six-separation benchmark on v10's predictions.

Why a separate kernel (CPU, no training):
  * v10 (thx-01-encoder-train/10) trained successfully on a T4 and wrote
    eval_{val_indist,val,gold}.json (predictions) + model.pt.
  * ai.diag_call_budget.audit() SCORES predictions -- it needs no GPU and no
    model forward pass. So the whole audit runs on CPU.
  * The deployable-cost numbers (sep 6) come from re-exporting ONNX, which is
    also CPU work. Tying this to the training kernel is what killed v11 (it
    landed on a CUDA-less worker and died in `train`).

This kernel pulls v10's outputs INTERNALLY -- kernel-to-kernel transfer on
Kaggle's backend is reliable, unlike the flaky `kaggle kernels output` to a
local machine -- then emits one small benchmark_v10.json for local pull.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working") if Path("/kaggle").exists() else Path(".")


def find_root() -> Path:
    direct = INPUT / "thx01-encoder-core"
    if (direct / "ai" / "encoder_policy.py").exists():
        return direct
    hits = sorted(INPUT.rglob("ai/encoder_policy.py"))
    if hits:
        return hits[0].parents[1]
    raise SystemExit("repo not found under /kaggle/input")


ROOT = find_root()
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import torch  # noqa: E402

print(f"torch {torch.__version__} cuda {torch.cuda.is_available()}", flush=True)
from agent.schema import TOOLS  # noqa: E402

DATA = ROOT / "data" / "core_sft_v4"

# ---------------------------------------------------------------------------
# pull v10 outputs internally (reliable kernel-to-kernel transfer)
# ---------------------------------------------------------------------------
V10 = "tajdinenizar/thx-01-encoder-train/10"
OUTDIR = WORK / "v10out"
OUTDIR.mkdir(parents=True, exist_ok=True)


def _pull() -> bool:
    for attempt in range(1, 9):
        try:
            subprocess.run(
                [sys.executable, "-m", "kaggle", "kernels", "output", V10,
                 "-p", str(OUTDIR)],
                check=True, capture_output=True, text=True, timeout=900)
        except Exception as exc:  # noqa: BLE001
            print(f"pull attempt {attempt} raised: {exc!r}", flush=True)
            continue
        evals = sorted(OUTDIR.glob("eval_*.json"))
        mp = OUTDIR / "model.pt"
        mp_size = mp.stat().st_size if mp.exists() else 0
        if len(evals) >= 3 and mp_size > 50_000_000:
            print(f"pull ok: {len(evals)} eval files, model.pt {mp_size/1e6:.1f} MB",
                  flush=True)
            return True
        print(f"pull attempt {attempt} incomplete (evals={len(evals)}, "
              f"model={mp_size}); retrying", flush=True)
    return False


if not _pull():
    raise SystemExit("could not pull v10 outputs")

# ---------------------------------------------------------------------------
# audit (separations 1-5): strict / exact / gate, solvable subset,
# unsolvable share, per-tool exact, per-language
# ---------------------------------------------------------------------------
from ai.diag_call_budget import audit as _audit  # noqa: E402


def _load_jsonl(p: Path):
    out = []
    with p.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _frac(num, den):
    return round(num / max(int(den), 1), 4)


def _summarize(a: dict) -> dict:
    total = a["total"]
    by_lang = {}
    for lang, s in a["by_lang"].items():
        c = s["cases"]
        if not c:
            continue
        by_lang[lang] = {
            "cases": c,
            "tool": _frac(s["tool_ok"], c),
            "strict": _frac(s["call_ok"], c),
            "exact": _frac(s["call_exact"], c),
            "gate": _frac(s["call_exact"] + s["call_canon"], c),
        }
    by_tool = {t: _frac(a["by_tool_exact"][t], a["cases"][t]) for t in a["cases"]}
    return {
        "total": total,
        "tool": _frac(sum(a["tool_ok"].values()), total),
        "strict": _frac(a["ok_all"], total),
        "exact": _frac(a["ok_exact"], total),
        "gate": _frac(a["ok_canon"], total),
        "solvable": a["solvable"],
        "unsolvable": a["n_unsolvable"],
        "unsolvable_share": _frac(a["n_unsolvable"], total),
        "strict_solvable": _frac(a["ok_solv"], a["solvable"]),
        "exact_solvable": _frac(a["ok_solv_exact"], a["solvable"]),
        "gate_solvable": _frac(a["ok_solv_canon"], a["solvable"]),
        "by_tool_exact": by_tool,
        "by_lang": by_lang,
    }


benchmark: dict = {
    "model": "thx-encoder-v10 (train kernel v10, GPU T4, early-stop ep3)",
    "note": "audit scores v10 predictions; no retraining, no CUDA needed.",
    "baseline_v8_gen2_val": {
        "tool": 0.9467, "gate_raw": 0.8586, "gate_serving": 0.8825,
        "note": "non-apples-to-apples vs gen-3 corpus; context only",
    },
    "splits": {},
}
for split, corpus in (("val_indist", "val_indist"), ("val", "val"), ("gold", "gold")):
    blob = json.loads((OUTDIR / f"eval_{split}.json").read_text(encoding="utf-8"))
    predictions = blob.get("predictions") or []
    rows = {str(r["id"]): r for r in _load_jsonl(DATA / f"{corpus}.jsonl")}
    raw = _audit(predictions, rows, postprocess=False)
    try:
        serve = _audit(predictions, rows, postprocess=True, resolvers=True)
        scfg = "postprocess + gazetteer + KB (serving)"
    except Exception as exc:  # noqa: BLE001
        print(f"{split}: serving fell back to postprocess-only: {exc!r}", flush=True)
        serve = _audit(predictions, rows, postprocess=True, resolvers=False)
        scfg = "postprocess only (resolvers unavailable)"
    benchmark["splits"][split] = {
        "raw": _summarize(raw),
        "serving": _summarize(serve),
        "serving_config": scfg,
        "unsolvable_reasons": dict(raw["unsolvable"]),
    }
    r = raw
    print(f"{split:11s} raw   tool {sum(r['tool_ok'].values())/r['total']:.3f} "
          f"strict {r['ok_all']/max(r['total'],1):.3f} "
          f"exact {r['ok_exact']/max(r['total'],1):.3f} "
          f"gate {r['ok_canon']/max(r['total'],1):.3f} "
          f"unsolv {r['n_unsolvable']}/{r['total']}", flush=True)

# ---------------------------------------------------------------------------
# separation 6: deployable artifact cost. The attached dataset's
# export_encoder.py lacks the onnxscript fallback, so bake a fixed
# dynamo=False export here. Best-effort: falls back to locally-measured
# numbers if onnx export is unavailable on this image.
# ---------------------------------------------------------------------------
_LOCAL_FALLBACK = {
    "params": 24874131, "seq_len": 256, "opset": 18,
    "fp32_onnx_mb": 123.2, "int8_onnx_mb": 31.3,
    "measured_on": "local CPU (torch 2.14.1, Windows)",
    "benchmarks": {
        "pytorch": {"latency_mean_ms": 283.8, "latency_p95_ms": None, "rss_mb": 452.3},
        "onnx_fp32": {"latency_mean_ms": 212.5, "latency_p95_ms": None, "rss_mb": 199.8},
        "onnx_int8": {"latency_mean_ms": 97.2, "latency_p95_ms": None, "rss_mb": 113.1},
    },
}


def _export_and_measure() -> dict:
    try:
        import onnx  # noqa: F401
        from onnxruntime.quantization import QuantType, quantize_dynamic
        from ai.export_encoder import (_measure, _run_benchmark, _output_names)
        from ai.encoder_policy import load_checkpoint
        from agent.schema import TOOLS as _TOOLS

        class ExportWrapper(torch.nn.Module):
            def __init__(self, inner):
                super().__init__()
                self.inner = inner

            def forward(self, input_ids, attention_mask):
                o = self.inner(input_ids, attention_mask)
                return (o["tool"], o["presence"], o["bool"], o["span_start"],
                        o["span_end"], *o["enum"])

        fp32 = WORK / "encoder.onnx"
        int8 = WORK / "encoder_int8.onnx"
        model, _tok, _meta = load_checkpoint(OUTDIR / "model.pt",
                                             device=torch.device("cpu"))
        wrapper = ExportWrapper(model).eval()
        ids = torch.ones((1, 256), dtype=torch.long)
        mask = torch.ones((1, 256), dtype=torch.long)
        fastpath = torch.backends.mha.get_fastpath_enabled()
        torch.backends.mha.set_fastpath_enabled(False)
        try:
            torch.onnx.export(wrapper, (ids, mask), str(fp32),
                              input_names=["input_ids", "attention_mask"],
                              output_names=_output_names(), opset_version=18,
                              do_constant_folding=True, dynamo=False,
                              dynamic_axes={
                                  "input_ids": {0: "batch", 1: "seq"},
                                  "attention_mask": {0: "batch", 1: "seq"},
                                  **{n: {0: "batch"} for n in _output_names()}})
        finally:
            torch.backends.mha.set_fastpath_enabled(fastpath)
        onnx.checker.check_model(onnx.load(str(fp32)))
        clean = int8.with_name(int8.stem + "_clean.onnx")
        m = onnx.load(str(fp32))
        m.graph.ClearField("value_info")
        onnx.save(m, str(clean))
        onnx.shape_inference.infer_shapes_path(str(clean), str(clean))
        quantize_dynamic(str(clean), str(int8), weight_type=QuantType.QInt8)
        clean.unlink(missing_ok=True)
        report = {
            "params": sum(p.numel() for p in model.parameters()),
            "seq_len": 256, "opset": 18,
            "fp32_onnx_mb": fp32.stat().st_size / 1e6,
            "int8_onnx_mb": int8.stat().st_size / 1e6,
            "measured_on": "Kaggle CPU (export kernel)",
            "benchmarks": {},
        }
        for mode, onnx_p in (("pytorch", None), ("onnx_fp32", fp32), ("onnx_int8", int8)):
            report["benchmarks"][mode] = _run_benchmark(
                mode, OUTDIR / "model.pt", onnx_p, None, 20, 256, 3, 10, 4,
                None, fp32.parent)
        return report
    except Exception as exc:  # noqa: BLE001
        print(f"ONNX export/benchmark unavailable on this image ({exc!r}); "
              f"using locally-measured fallback", flush=True)
        return _LOCAL_FALLBACK


benchmark["resource"] = _export_and_measure()

# ---------------------------------------------------------------------------
# release gates (ARCH_REASSESSMENT): val tool > 71%, val gate > 40%,
# gold gate > 56% -- checked on the serving config.
# ---------------------------------------------------------------------------
gates: dict = {}
if "val" in benchmark["splits"]:
    g = benchmark["splits"]["val"]
    gates["val_tool_gt_71"] = g["serving"]["tool"] > 0.71
    gates["val_gate_gt_40"] = g["serving"]["gate"] > 0.40
if "gold" in benchmark["splits"]:
    gates["gold_gate_gt_56"] = benchmark["splits"]["gold"]["serving"]["gate"] > 0.56
benchmark["gates"] = gates

(WORK / "benchmark_v10.json").write_text(
    json.dumps(benchmark, indent=2, ensure_ascii=False), encoding="utf-8")
print("benchmark_v10.json written", flush=True)
print(json.dumps({
    "gates": gates,
    "val_serving": benchmark["splits"].get("val", {}).get("serving"),
    "gold_serving": benchmark["splits"].get("gold", {}).get("serving"),
    "resource": {k: benchmark["resource"].get(k) for k in
                 ("fp32_onnx_mb", "int8_onnx_mb", "measured_on")},
}, indent=2), flush=True)
