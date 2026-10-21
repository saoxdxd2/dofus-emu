import json
from pathlib import Path

OUT = Path(r"C:\Users\sao\Documents\dofus-emu\dofus-network\colab\kaggle_train_encoder30.ipynb")


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}


def code(text):
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
            "source": text.strip("\n").splitlines(keepends=True)}


cells = []

cells.append(md(r"""
# THX encoder-30M — honest training run (Kaggle T4)

Trains the 30M non-autoregressive tool policy (`ai/encoder_policy.py`) on a
**leakage-free** split, then exports the deployable INT8 ONNX artifact.

## Why this notebook exists

The previous run trained 155 steps and reached **10.7% tool / 4.7% call**
accuracy — chance level for 16 tools. The corpus was also leaking: 837 of the
1279 benchmark goals and 31 of the 71 GOLD goals appear verbatim in
`core_sft_v3`. So neither "it failed" nor "it scored 100%" was a real
measurement.

This notebook fixes both: `ai/prepare_encoder_sft.py` builds `core_sft_v4`
with a **frame-disjoint** split and GOLD decontamination, and the model is
trained for enough steps to actually converge.

## Three numbers to read, in this order

| split | what it proves |
| --- | --- |
| `val_indist` | memorisation ceiling — same frames, new entities |
| `val` | **generalisation** — unseen sentence frames, this is the real score |
| `gold` | real hand-written orders in EN/FR/ES/DE |

Bar to beat: the pure regex `RulePolicy` scores **57.7% tool / 46.5% call** on
GOLD with no model at all. A neural policy that does not clear that is not
worth shipping.

## Expected cost

30.7M params, `d=512 L=7`, seq 256, batch 32 on a T4: ~0.65 s/step,
~872 steps/epoch → ~10 min/epoch. 12 epochs ≈ 2 h, well inside the 12 h quota.
"""))

cells.append(md("## 1. Environment"))

cells.append(code(r"""
import json, os, shutil, subprocess, sys, time
from pathlib import Path

import torch

print("torch", torch.__version__, "| cuda", torch.cuda.is_available())
if torch.cuda.is_available():
    print(torch.cuda.get_device_name(0))

# ONNX export is optional: training and evaluation work without it.
try:
    import onnx, onnxruntime
    print("onnx", onnx.__version__, "| onnxruntime", onnxruntime.__version__)
except ImportError:
    print(subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "onnx", "onnxruntime", "onnxscript"],
        capture_output=True, text=True).stdout[-1500:])
"""))

cells.append(md(r"""
## 2. Locate the repo and the corpus

The Kaggle dataset is a repo dump. `ROOT` must contain `ai/encoder_policy.py`
and `data/`.
"""))

cells.append(code(r"""
ROOT = None
for candidate in [Path("/kaggle/input/thx01-encoder-core"),
                  Path("/kaggle/input/thx01-dofus-core")]:
    if (candidate / "ai" / "encoder_policy.py").exists():
        ROOT = candidate
        break
if ROOT is None:
    hits = sorted(Path("/kaggle/input").rglob("ai/encoder_policy.py"))
    if hits:
        ROOT = hits[0].parents[1]
if ROOT is None:
    raise SystemExit("repo not found under /kaggle/input — attach the dataset first")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
print("ROOT =", ROOT)

DATA = ROOT / "data"
for name in ("core_sft_v3", "core_sft_v2", "core_sft"):
    d = DATA / name
    if d.exists():
        print(f"{name:14s}", {f.name: f.stat().st_size // 1024 for f in d.glob('*.jsonl')})
"""))

cells.append(md(r"""
## 3. Build the leakage-free split

`core_sft_v4` = v3 rows, de-duplicated, GOLD-decontaminated, then split by
**sentence frame** so no template is in both train and val.

The same script runs locally, so the artifact you train here is reproducible:

```bash
python -m ai.prepare_encoder_sft \
    --input data/core_sft_v3/train.jsonl data/core_sft_v3/val.jsonl \
    --out data/core_sft_v4
```
"""))

cells.append(code(r"""
from ai.prepare_encoder_sft import build

corpus = [DATA / "core_sft_v3" / "train.jsonl", DATA / "core_sft_v3" / "val.jsonl"]
corpus = [p for p in corpus if p.exists()]
if not corpus:
    corpus = [DATA / "core_sft_v2" / "train.jsonl", DATA / "core_sft_v2" / "val.jsonl"]

SFT = Path("/kaggle/working/core_sft_v4")
report = build(corpus, SFT, val_fraction=0.12, seed="dofus-encoder-v4",
               drop_same_frame=True, indist_rows=800)

print(json.dumps({
    "written": report["written"],
    "split": report["split"],
    "gold_decontamination": report["gold_decontamination"],
}, indent=2))

# Sanity gate: a leak here invalidates every number below.
from ai.data_splits import load_jsonl, normalize, skeleton
tr = load_jsonl(SFT / "train.jsonl")
va = load_jsonl(SFT / "val.jsonl")
assert not ({normalize(r["goal"]) for r in tr} & {normalize(r["goal"]) for r in va}), "goal leak"
assert not ({skeleton(r["goal"], r["args"]) for r in tr}
            & {skeleton(r["goal"], r["args"]) for r in va}), "frame leak"
print("split verified: 0 goal overlap, 0 frame overlap")
"""))

cells.append(md(r"""
## 4. Train

`EncoderConfig` is fixed at the deployable size — 30.7M params is what fits the
600 MB static budget at INT8 (31.5 MB on disk, ~63 MB RSS). Do not grow
`--dim`/`--layers` here; if accuracy is short, the answer is better data, not a
bigger model.

Early stopping watches frame-held-out `val` loss, not the in-distribution one.
"""))

cells.append(code(r"""
from ai.encoder_policy import main as encoder_main

OUT = Path("/kaggle/working/encoder30")
OUT.mkdir(parents=True, exist_ok=True)

t0 = time.time()
encoder_main([
    "train",
    "--sft-dir", str(SFT),
    "--tokenizer", str(DATA / "core_sft_v3" / "tokenizer.json")
        if (DATA / "core_sft_v3" / "tokenizer.json").exists()
        else str(DATA / "core_sft_v2" / "tokenizer.json"),
    "--out", str(OUT),
    "--system", "encoder",
    "--max-seq-len", "256",
    "--dim", "512", "--layers", "7", "--heads", "8", "--ffn", "2048",
    "--dropout", "0.1",
    "--batch-size", "32",
    "--epochs", "12",
    "--lr", "3e-4",
    "--weight-decay", "0.01",
    "--eval-every", "150",
    "--patience", "5",
    "--min-delta", "0.005",
    "--log-every", "50",
    "--device", "cuda",
])
print(f"trained in {(time.time() - t0) / 60:.1f} min")
"""))

cells.append(md(r"""
## 5. Evaluate on the three splits

`ai/encoder_policy.py eval` reports tool accuracy (right tool) and call
accuracy (right tool **and** every argument). Read `val` before `val_indist`.
"""))

cells.append(code(r"""
from ai.encoder_policy import evaluate
from types import SimpleNamespace

results = {}
for name, path in [("val_indist", SFT / "val_indist.jsonl"),
                   ("val", SFT / "val.jsonl"),
                   ("gold", SFT / "gold.jsonl")]:
    report = evaluate(SimpleNamespace(
        checkpoint=str(OUT / "model.pt"), cases=str(path), limit=0,
        batch_size=16, device="cuda", out=str(OUT / f"eval_{name}.json"),
        show_failures=8))
    results[name] = report["totals"]

print()
print(f"{'split':12s} {'cases':>6s} {'tool':>8s} {'call':>8s}")
for name, t in results.items():
    cases = max(t["cases"], 1)
    print(f"{name:12s} {t['cases']:6d} "
          f"{t['tool_ok'] / cases:8.1%} {t['call_ok'] / cases:8.1%}")
print("\nRule policy baseline on GOLD: tool 57.7%  call 46.5%")
"""))

cells.append(md(r"""
## 6. Export the deployable artifact

FP32 ONNX → dynamic INT8 → measured latency and RSS. The numbers we are
targeting, already measured on this machine for a 30M encoder at seq 256:

| variant | disk | p50 latency | p95 | process RSS |
| --- | --- | --- | --- | --- |
| fp32 ONNX | 123.6 MB | 120 ms | 139 ms | 200 MB |
| INT8 ONNX | 31.5 MB | 55 ms | 61 ms | 108 MB |
"""))

cells.append(code(r"""
try:
    from ai.export_encoder import main as export_main

    export_main([
        "--checkpoint", str(OUT / "model.pt"),
        "--out", str(OUT / "encoder.onnx"),
        "--int8-out", str(OUT / "encoder_int8.onnx"),
        "--seq-len", "256",
        "--threads", "4",
        "--limit", "20",
        "--runs", "10",
        "--report", str(OUT / "encoder.report.json"),
    ])
except Exception as exc:  # noqa: BLE001 - export is optional, training is not
    print("ONNX export failed:", type(exc).__name__, exc)
    print("The checkpoint is still valid; export locally with ai/export_encoder.py")
"""))

cells.append(md("## 7. Package for download"))

cells.append(code(r"""
archive = shutil.make_archive("/kaggle/working/encoder30_artifacts", "zip",
                              root_dir=str(OUT))
print(archive, Path(archive).stat().st_size / 1e6, "MB")
for f in sorted(OUT.iterdir()):
    print(f"  {f.name:28s} {f.stat().st_size / 1e6:8.2f} MB")
"""))

nb = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.11"},
        "kaggle": {
            "accelerator": "nvidiaTeslaT4",
            "dataSources": [{"sourceType": "datasetVersion",
                             "sourceId": "tajdinenizar/thx01-encoder-core"}],
            "isGpuEnabled": True,
            "isInternetEnabled": True,
        },
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print("wrote", OUT, OUT.stat().st_size, "bytes,", len(cells), "cells")
