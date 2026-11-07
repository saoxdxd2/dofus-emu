"""Kaggle entry point: generational PPO / PBT on the JAX combat env."""

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
    raise SystemExit("PBT needs an accelerator")

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

from combat.encounter.pbt import build_parser, run  # noqa: E402

BASE = ROOT / "base_encounter_selfplay.pkl"
if not BASE.exists():
    raise SystemExit("base_encounter_selfplay.pkl missing")
OUT_DIR = WORK / "pbt"
OUT = WORK / "encounter_pbt.pkl"
argv = [
    "--data", DATA,
    "--init", str(BASE),
    "--out-dir", str(OUT_DIR),
    "--out", str(OUT),
    "--rounds", "5",
    "--candidates", "2",
    "--iters", "120",
    "--samples", "4096",
    "--eval-samples", "1024",
    "--max-rounds", "24",
    "--seed", "7",
]
run(build_parser().parse_args(argv))
summary = {
    "backend": jax.default_backend(),
    "device_count": len(jax.devices()),
    "method": "generational-ppo-pbt",
    "checkpoint": str(OUT),
    "metrics": str(OUT_DIR / "metrics.json"),
}
(WORK / "metrics.json").write_text(json.dumps(summary, indent=1),
                                    encoding="utf-8")
print(json.dumps(summary, indent=1))
