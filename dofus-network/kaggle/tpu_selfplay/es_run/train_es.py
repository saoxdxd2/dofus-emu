"""Kaggle GPU entry point for the population-based combat ES."""

from __future__ import annotations

import glob
import json
import sys
import zipfile
from pathlib import Path

import jax

print("jax", jax.__version__, "backend", jax.default_backend(),
      "devices", jax.devices())
if jax.default_backend() not in ("gpu", "tpu"):
    raise SystemExit("ES needs an accelerator; this kernel is on CPU")

DATA = sorted(glob.glob("/kaggle/input/**/encounters.npz", recursive=True))
if not DATA:
    raise SystemExit("encounters.npz not found")
DATA = DATA[0]
ROOT = Path(DATA).parent
WORK = Path("/kaggle/working")
PKG = WORK / "encounter_pkg"
for archive in sorted(ROOT.glob("*.zip")):
    with zipfile.ZipFile(archive) as zf:
        if any(name.startswith("combat/") for name in zf.namelist()):
            zf.extractall(PKG)
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT))
print("package", PKG, "data", DATA)

from combat.encounter.es import build_parser, run  # noqa: E402

BASE = ROOT / "base_encounter_selfplay.pkl"
if not BASE.exists():
    raise SystemExit("base_encounter_selfplay.pkl missing from dataset")
OUT = WORK / "encounter_es.pkl"
argv = [
    "--data", DATA,
    "--init", str(BASE),
    "--out", str(OUT),
    "--samples", "1024",
    "--population", "128",
    "--generations", "600",
    "--sigma", "0.02",
    "--lr", "0.003",
    "--wd", "0.0001",
    "--eval-start", "0",
    "--eval-samples", "1024",
    "--eval-every", "20",
    "--save-every", "50",
    "--seed", "7",
]
run(build_parser().parse_args(argv))
import pickle  # noqa: E402
payload = pickle.loads(OUT.read_bytes())
summary = {
    "method": "openai-es",
    "backend": jax.default_backend(),
    "device_count": len(jax.devices()),
    "population": 128,
    "samples": 256,
    "generations": 120,
    "best_fitness": payload.get("best_fitness"),
    "last": payload.get("metrics", [{}])[-1] if payload.get("metrics") else None,
    "out": str(OUT),
}
(WORK / "metrics.json").write_text(json.dumps(summary, indent=1),
                                    encoding="utf-8")
print(json.dumps(summary, indent=1))
