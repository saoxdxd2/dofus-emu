"""Kaggle kernel: train the 30M capability encoder (anchor mode).

Corrected recipe versus the run that produced the degenerate checkpoints:

    steps          12 epochs of core_sft_v4 (~7,900) instead of 1 epoch (155)
    early stop     on val *call* accuracy (tool saturates ~0.90; call is what
                   the release gate scores). v10 stopped at step 1,500 on a
                   tool-accuracy delta and shipped worse call than v9.
    head           capability anchors derived from descriptions, not a class list;
                   refreshed once per epoch, not every 50 steps (a 50-step
                   refresh overwrites the head with the encoder's own output,
                   i.e. a moving target -- the sawtooth in v9/v10)
    weighting      off (the sqrt inverse-frequency weighting biased the head
                   toward rare tools; opt-in now, default off)
    warmup         5% linear ramp to peak lr, then cosine. v9 skipped it and
                   never left the thrash phase, so this is now a real argument
                   (--warmup-frac) rather than a description of the intent.

Outputs (in /kaggle/working): encoder30/{model.pt, tokenizer.json, metrics.json},
eval_{val_indist,val,gold}.json, encoder_int8.onnx if onnx is available.
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
    """Locate the repo inside the attached dataset.

    Kaggle may attach the dataset either as plain folders or, when it was pushed
    with ``--dir-mode zip``, as one zip per top-level folder (agent.zip, ai.zip,
    data.zip). Extract every archive into one directory so the repo layout is
    reconstructed before searching.
    """
    direct = INPUT / "thx01-encoder-core"
    if (direct / "ai" / "encoder_policy.py").exists():
        return direct

    hits = sorted(INPUT.rglob("ai/encoder_policy.py"))
    if hits:
        return hits[0].parents[1]

    archives = sorted(INPUT.rglob("*.zip"))
    if archives:
        target = WORK / "repo"
        target.mkdir(parents=True, exist_ok=True)
        for archive in archives:
            try:
                with zipfile.ZipFile(archive) as handle:
                    handle.extractall(target)
                print(f"extracted {archive.name} -> {target}", flush=True)
            except zipfile.BadZipFile:
                print(f"skipped non-zip {archive}", flush=True)
        if (target / "ai" / "encoder_policy.py").exists():
            return target
        nested = sorted(target.rglob("ai/encoder_policy.py"))
        if nested:
            return nested[0].parents[1]

    listing = [str(p.relative_to(INPUT)) for p in list(INPUT.iterdir())[:12]]
    raise SystemExit(f"repo not found under {INPUT}: {listing}")


ROOT = find_root()
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
print(f"ROOT = {ROOT}", flush=True)

import torch  # noqa: E402

print(f"torch {torch.__version__} cuda {torch.cuda.is_available()}", flush=True)
if torch.cuda.is_available():
    print(torch.cuda.get_device_name(0), flush=True)

# Device lottery guard. A GPU-enabled Kaggle kernel is occasionally scheduled on
# a CPU-only worker; the old code then trained the full recipe on CPU, which
# takes 8-12 h instead of 20 min and burns the whole 12 h kernel limit for one
# unusable number. Failing here instead marks the run ERROR in seconds, and the
# re-push lands on a GPU worker. Set ALLOW_CPU_TRAIN=1 to opt into a CPU run.
if not torch.cuda.is_available() and os.environ.get("ALLOW_CPU_TRAIN", "") != "1":
    raise SystemExit(
        "this worker has no CUDA device; refusing to train the 30M recipe on CPU "
        "(set ALLOW_CPU_TRAIN=1 to override). Re-push to reschedule onto a GPU.")

from agent.schema import CAPABILITY_SOURCE, TOOLS  # noqa: E402
from ai.encoder_policy import main as encoder_main  # noqa: E402

DATA = ROOT / "data" / "core_sft_v4"
TOKENIZER = DATA / "tokenizer.json"
# The builder writes its own tokenizer now, so a missing one means the dataset
# is stale rather than merely old. Falling back to the v3 tokenizer trained
# against a corpus that cannot see the new vocabulary, which is the failure this
# used to hide -- fail loudly instead.
if not TOKENIZER.exists():
    raise SystemExit(f"{TOKENIZER} missing: re-run ai.prepare_encoder_sft and "
                     f"push a fresh dataset version")
OUT = WORK / "encoder30"
OUT.mkdir(parents=True, exist_ok=True)

print(f"capabilities: {len(TOOLS)} (source: {CAPABILITY_SOURCE})", flush=True)
print(f"sft dir     : {DATA}", flush=True)
for name in ("train.jsonl", "val.jsonl", "val_indist.jsonl", "gold.jsonl"):
    path = DATA / name
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            print(f"  {name:18s} {sum(1 for _ in handle):6d} rows", flush=True)

# The corpus regeneration that added name-bearing quest.advance rows is the
# point of this run, so verify it actually landed before spending GPU quota on
# a corpus that still has the gap.
def _name_bearing(path: Path) -> int:
    import re

    count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("tool") != "quest.advance":
                continue
            quest_id = (row.get("args") or {}).get("quest_id")
            if quest_id is None:
                continue
            if not re.search(r"(?<!\d)" + str(quest_id) + r"(?!\d)",
                             str(row.get("goal") or "")):
                count += 1
    return count


for split in ("train.jsonl", "val.jsonl"):
    named = _name_bearing(DATA / split)
    print(f"  {split:18s}   name-bearing quest.advance: {named}", flush=True)
    if named == 0:
        raise SystemExit(
            f"{split} has no name-bearing quest.advance rows: the pushed "
            f"dataset predates the corpus regeneration")

started = time.time()
# A checkpoint in the dataset turns this run into a continuation. The dataset is
# the only reliable channel between runs (kernel output downloads fail), so its
# presence is the switch -- no separate kernel, no flag to forget.
PREV = ROOT / "data" / "checkpoints" / "encoder30_prev.pt"
train_args = [
    "train",
    "--sft-dir", str(DATA),
    "--tokenizer", str(TOKENIZER),
    "--out", str(OUT),
    "--system", "encoder",
    "--max-seq-len", "256",
    "--dim", "512", "--layers", "7", "--heads", "8", "--ffn", "2048",
    "--dropout", "0.1",
    "--batch-size", "32",
    "--warmup-frac", "0.05",
    "--weight-decay", "0.01",
    "--eval-every", "150",
    "--patience", "6",
    "--min-delta", "0.01",
    "--log-every", "50",
    # Early stop on `call`, not `tool`. Evidence from v9 and v10: the tool head
    # saturates near 0.87-0.90 while call accuracy -- tool AND args, which is
    # what the release gate scores -- is still moving. v10 stopped at step 1,500
    # of 8,244 on a 0.005 tool-accuracy delta, and it shipped a *worse* call
    # accuracy (0.561) than v9's 0.637 at step 1,950. Watching the saturated
    # metric threw away the run.
    "--early-stop", "call",
    "--tool-weights", "off",
    "--anchor-mode",
    # Anchor refresh period. v9/v10 refreshed every 50 steps, and because the
    # head is *overwritten* with the encoder's own pooled embeddings, the tool
    # target moved every 50 steps -- which is the sawtooth in both histories
    # (v9 tool 0.870 -> 0.625 -> 0.794 between evals). Refreshing once per epoch
    # (~687 steps) keeps the head tracking the trunk without making the loss
    # non-stationary on a 50-step clock. Compare against 0 (frozen at step 0)
    # and 50 (the v9/v10 behaviour) on the same seed before settling.
    "--anchor-every", "700",
    "--device", "cuda",
]
if PREV.exists():
    # Continuation: 4 more epochs at a tenth of the peak lr. The counters carry
    # over, so early stopping still compares against the checkpoint's own
    # 0.685 call -- a resumed run that makes things worse stops instead of
    # shipping.
    train_args += ["--resume", str(PREV), "--epochs", "4", "--lr", "3e-5"]
    print(f"resume    : {PREV} -> 4 epochs at lr 3e-5", flush=True)
else:
    train_args += ["--epochs", "12", "--lr", "1e-4"]
    print("scratch   : no checkpoint in the dataset, training 12 epochs at lr 1e-4",
          flush=True)
encoder_main(train_args)
print(f"trained in {(time.time() - started) / 60:.1f} min", flush=True)

# ---- evaluate on the three splits, per language ---------------------------
from types import SimpleNamespace  # noqa: E402

from ai.encoder_policy import evaluate  # noqa: E402

summary = {}
for split, path in (("val_indist", DATA / "val_indist.jsonl"),
                    ("val", DATA / "val.jsonl"),
                    ("gold", DATA / "gold.jsonl")):
    if not path.exists():
        continue
    report = evaluate(SimpleNamespace(
        checkpoint=str(OUT / "model.pt"), cases=str(path), limit=0, batch_size=32,
        device="cuda", out=str(WORK / f"eval_{split}.json"), show_failures=8))
    totals = report["totals"]
    cases = max(totals["cases"], 1)
    summary[split] = {
        "cases": totals["cases"],
        "tool": round(totals["tool_ok"] / cases, 4),
        "call": round(totals["call_ok"] / cases, 4),
        "by_lang": {lang: {"cases": stats["cases"],
                           "tool": round(stats["tool_ok"] / max(stats["cases"], 1), 4),
                           "call": round(stats["call_ok"] / max(stats["cases"], 1), 4)}
                    for lang, stats in report["by_lang"].items()},
    }
    print(f"{split:11s} tool {summary[split]['tool']:.1%}  "
          f"call {summary[split]['call']:.1%}", flush=True)

(WORK / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

# ---- six-separation audit ------------------------------------------------
# summary.json is the *raw* comparator. The release gate is scored on the
# serving config (postprocess + gazetteer + KB resolvers), so report both: the
# gap between them is a comparator artefact, not model skill
# (ARCH_REASSESSMENT §0.2). Never let this block kill a training run.
try:
    from ai.diag_call_budget import audit as _audit

    def _rows(path: Path) -> dict:
        rows = {}
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    rows[str(row["id"])] = row
        return rows

    bench: dict = {}
    for split in ("val_indist", "val", "gold"):
        ev = WORK / f"eval_{split}.json"
        corpus = DATA / f"{split}.jsonl"
        if not ev.exists() or not corpus.exists():
            continue
        blob = json.loads(ev.read_text(encoding="utf-8")) or {}
        predictions = blob.get("predictions") or []
        rows = _rows(corpus)
        entry: dict = {}
        for label, kwargs in (("raw", dict(postprocess=False)),
                              ("serving", dict(postprocess=True, resolvers=True))):
            try:
                a = _audit(predictions, rows, **kwargs)
            except Exception as exc:  # noqa: BLE001
                print(f"{split}/{label}: audit failed: {type(exc).__name__}: {exc}",
                      flush=True)
                continue
            total = max(int(a["total"]), 1)
            entry[label] = {
                "cases": int(a["total"]),
                "tool": round(sum(a["tool_ok"].values()) / total, 4),
                "strict": round(a["ok_all"] / total, 4),
                "exact": round(a["ok_exact"] / total, 4),
                "gate": round(a["ok_canon"] / total, 4),
                "unsolvable": int(a["n_unsolvable"]),
            }
        bench[split] = entry
        if "raw" in entry and "serving" in entry:
            print(f"{split:11s} raw gate {entry['raw']['gate']:.3f} -> "
                  f"serving gate {entry['serving']['gate']:.3f}  "
                  f"(exact {entry['serving']['exact']:.3f}, "
                  f"unsolvable {entry['serving']['unsolvable']})", flush=True)
    bench["gates"] = {
        "val_tool_gt_71": bool(bench.get("val", {}).get("serving", {}).get("tool", 0) > 0.71),
        "val_gate_gt_40": bool(bench.get("val", {}).get("serving", {}).get("gate", 0) > 0.40),
        "gold_gate_gt_56": bool(bench.get("gold", {}).get("serving", {}).get("gate", 0) > 0.56),
    }
    (WORK / "benchmark_v11.json").write_text(
        json.dumps(bench, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"gates: {bench['gates']}", flush=True)
except Exception as exc:  # noqa: BLE001 - the audit must never kill a run
    print(f"six-separation audit skipped: {type(exc).__name__}: {exc}", flush=True)

# ---- deployment artifact -------------------------------------------------
try:
    from ai.export_encoder import main as export_main

    export_main([
        "--checkpoint", str(OUT / "model.pt"),
        "--out", str(OUT / "encoder.onnx"),
        "--int8-out", str(OUT / "encoder_int8.onnx"),
        "--seq-len", "256", "--threads", "4", "--limit", "20", "--runs", "10",
        "--report", str(OUT / "encoder.report.json"),
    ])
except Exception as exc:  # noqa: BLE001 - export is optional
    print(f"ONNX export skipped: {type(exc).__name__}: {exc}", flush=True)

archive = shutil.make_archive(str(WORK / "encoder30_artifacts"), "zip", root_dir=str(OUT))
print(f"archive {archive} {Path(archive).stat().st_size / 1e6:.1f} MB", flush=True)
for path in sorted(OUT.iterdir()):
    print(f"  {path.name:26s} {path.stat().st_size / 1e6:8.2f} MB", flush=True)

# ---------------------------------------------------------------------------
# six-separation benchmark (no extra model work: the per-split predictions and
# the export report already exist in /kaggle/working). Appended here so the
# scoring runs in the same kernel that produced the artifacts -- there is no
# flaky cross-kernel model.pt transfer.
# ---------------------------------------------------------------------------
def _load_jsonl_local(p: Path):
    out = []
    with p.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _frac(num, den):
    return round(num / max(int(den), 1), 4)


def _summarize(audit_out: dict) -> dict:
    total = audit_out["total"]
    by_lang = {}
    for lang, s in audit_out["by_lang"].items():
        cases = s["cases"]
        if not cases:
            continue
        # gate = exact + the containment-either-way leeway (call_canon counts
        # cases that are gate-correct but not exact).
        by_lang[lang] = {
            "cases": cases,
            "tool": _frac(s["tool_ok"], cases),
            "strict": _frac(s["call_ok"], cases),
            "exact": _frac(s["call_exact"], cases),
            "gate": _frac(s["call_exact"] + s["call_canon"], cases),
        }
    by_tool_exact = {
        t: _frac(audit_out["by_tool_exact"][t], audit_out["cases"][t])
        for t in audit_out["cases"]
    }
    return {
        "total": total,
        "tool": _frac(sum(audit_out["tool_ok"].values()), total),
        "strict": _frac(audit_out["ok_all"], total),
        "exact": _frac(audit_out["ok_exact"], total),
        "gate": _frac(audit_out["ok_canon"], total),
        "solvable": audit_out["solvable"],
        "unsolvable": audit_out["n_unsolvable"],
        "unsolvable_share": _frac(audit_out["n_unsolvable"], total),
        "strict_solvable": _frac(audit_out["ok_solv"], audit_out["solvable"]),
        "exact_solvable": _frac(audit_out["ok_solv_exact"], audit_out["solvable"]),
        "gate_solvable": _frac(audit_out["ok_solv_canon"], audit_out["solvable"]),
        "by_tool_exact": by_tool_exact,
        "by_lang": by_lang,
    }


try:
    from ai.diag_call_budget import audit as _audit

    benchmark: dict = {
        "model": "thx-encoder-v10-recipe (unified train+eval kernel)",
        "note": "warmup 5% cosine schedule; 12 epochs core_sft_v4; anchor head; "
                "no inverse-freq weighting.",
        "baseline_v8_gen2_val": {
            "tool": 0.9467, "gate_raw": 0.8586, "gate_serving": 0.8825,
            "note": "non-apples-to-apples vs gen-3 corpus; recorded for context only",
        },
        "splits": {},
    }
    for split, corpus_name in (("val_indist", "val_indist"),
                               ("val", "val"),
                               ("gold", "gold")):
        eval_path = WORK / f"eval_{split}.json"
        if not eval_path.exists():
            print(f"skip {split}: {eval_path} missing", flush=True)
            continue
        blob = json.loads(eval_path.read_text(encoding="utf-8"))
        predictions = blob.get("predictions") or []
        rows = {str(r["id"]): r for r in _load_jsonl_local(DATA / f"{corpus_name}.jsonl")}
        raw = _audit(predictions, rows, postprocess=False)
        try:
            serve = _audit(predictions, rows, postprocess=True, resolvers=True)
            serving_cfg = "postprocess + gazetteer + KB (serving)"
        except Exception as exc:  # noqa: BLE001
            print(f"{split}: serving audit fell back to postprocess-only: {exc}", flush=True)
            serve = _audit(predictions, rows, postprocess=True, resolvers=False)
            serving_cfg = "postprocess only (resolvers unavailable)"
        benchmark["splits"][split] = {
            "raw": _summarize(raw),
            "serving": _summarize(serve),
            "serving_config": serving_cfg,
            "unsolvable_reasons": dict(raw["unsolvable"]),
        }
        r = raw
        print(f"{split:11s} raw   tool {r['tool_ok'] and sum(r['tool_ok'].values())/r['total']:.3f} "
              f"strict {r['ok_all']/max(r['total'],1):.3f} "
              f"exact {r['ok_exact']/max(r['total'],1):.3f} "
              f"gate {r['ok_canon']/max(r['total'],1):.3f} "
              f"unsolv {r['n_unsolvable']}/{r['total']}", flush=True)

    # separation #6: deployable artifact cost (already measured by export)
    report_path = OUT / "encoder.report.json"
    if report_path.exists():
        rep = json.loads(report_path.read_text(encoding="utf-8"))
        benchmark["resource"] = {
            "params": rep.get("params"),
            "seq_len": rep.get("seq_len"),
            "opset": rep.get("opset"),
            "fp32_onnx_mb": rep.get("fp32_mb"),
            "int8_onnx_mb": rep.get("int8_mb"),
            "benchmarks": {
                k: {
                    "latency_mean_ms": (v.get("latency") or {}).get("mean"),
                    "latency_p95_ms": (v.get("latency") or {}).get("p95"),
                    "rss_mb": ((v.get("memory") or {}).get("after_runs") or {}).get("rss_mb"),
                }
                for k, v in (rep.get("benchmarks") or {}).items()
            },
        }

    # configured release gates (ARCH_REASSESSMENT): val tool > 71%, val gate > 40%,
    # gold gate > 56%. Checked on the serving config; raw shown alongside.
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
    print(json.dumps({"gates": gates,
                      "val_serving": benchmark["splits"].get("val", {}).get("serving"),
                      "gold_serving": benchmark["splits"].get("gold", {}).get("serving")},
                     indent=2), flush=True)
except Exception as exc:  # noqa: BLE001 - scoring must never fail the kernel
    print(f"benchmark scoring skipped: {type(exc).__name__}: {exc}", flush=True)
    import traceback
    traceback.print_exc()
