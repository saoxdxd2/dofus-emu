"""Kaggle kernel: train the 30M capability encoder (anchor mode).

Corrected recipe versus the run that produced the degenerate checkpoints:

    steps          12 epochs of core_sft_v4 (~7,900) instead of 1 epoch (155)
    early stop     on val tool accuracy, not val loss
    head           capability anchors derived from descriptions, not a class list
    weighting      off (the sqrt inverse-frequency weighting biased the head
                   toward rare tools; opt-in now, default off)
    warmup         cosine with a short warmup so the head does not move first

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

from agent.schema import CAPABILITY_SOURCE, TOOLS  # noqa: E402
from ai.encoder_policy import main as encoder_main  # noqa: E402

DATA = ROOT / "data" / "core_sft_v4"
TOKENIZER = DATA / "tokenizer.json"
if not TOKENIZER.exists():
    TOKENIZER = ROOT / "data" / "core_sft_v3" / "tokenizer.json"
OUT = WORK / "encoder30"
OUT.mkdir(parents=True, exist_ok=True)

print(f"capabilities: {len(TOOLS)} (source: {CAPABILITY_SOURCE})", flush=True)
print(f"sft dir     : {DATA}", flush=True)
for name in ("train.jsonl", "val.jsonl", "val_indist.jsonl", "gold.jsonl"):
    path = DATA / name
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            print(f"  {name:18s} {sum(1 for _ in handle):6d} rows", flush=True)

started = time.time()
encoder_main([
    "train",
    "--sft-dir", str(DATA),
    "--tokenizer", str(TOKENIZER),
    "--out", str(OUT),
    "--system", "encoder",
    "--max-seq-len", "256",
    "--dim", "512", "--layers", "7", "--heads", "8", "--ffn", "2048",
    "--dropout", "0.1",
    "--batch-size", "32",
    "--epochs", "12",
    "--lr", "1e-4",
    "--weight-decay", "0.01",
    "--eval-every", "150",
    "--patience", "5",
    "--min-delta", "0.005",
    "--log-every", "50",
    "--early-stop", "tool",
    "--tool-weights", "off",
    "--anchor-mode",
    "--anchor-every", "50",
    "--device", "cuda",
])
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
