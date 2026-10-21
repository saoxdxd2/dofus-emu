"""Kaggle entry point for the 30M encoder policy."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path("/kaggle/input/thx01-encoder-core")
if not (ROOT / "ai" / "encoder_policy.py").exists():
    candidates = sorted(Path("/kaggle/input").rglob("ai/encoder_policy.py"))
    if candidates:
        ROOT = candidates[0].parents[1]
if not (ROOT / "ai" / "encoder_policy.py").exists():
    work = Path("/kaggle/working/thx01-encoder-core")
    work.mkdir(parents=True, exist_ok=True)
    for archive in ROOT.glob("*.zip"):
        with zipfile.ZipFile(archive) as handle:
            handle.extractall(work)
    ROOT = work
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
print(f"ROOT={ROOT} exists={ROOT.exists()}", flush=True)

from ai.encoder_policy import main  # noqa: E402

DATA = ROOT / "data" / "core_sft_v3"
OUT = Path("/kaggle/working/encoder_policy") if Path("/kaggle").exists() else ROOT / "models" / "encoder_policy"

main([
    "train",
    "--sft-dir", str(DATA),
    "--tokenizer", str(DATA / "tokenizer.json"),
    "--out", str(OUT),
    "--system", "encoder",
    "--batch-size", "32",
    "--epochs", "1",
    "--eval-every", "25",
    "--patience", "2",
    "--min-delta", "0.01",
    "--log-every", "25",
    "--lr", "3e-4",
    "--threads", "4",
])

main([
    "eval",
    "--checkpoint", str(OUT / "model.pt"),
    "--out", str(OUT / "gold_eval.json"),
    "--show-failures", "10",
])
