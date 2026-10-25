# Dofus combat self-play. Paste this cell into a Kaggle notebook.
# Before running:
#   1. Add Data -> tajdinenizar/dofus-encounter-tables (private dataset)
#   2. Settings -> Accelerator -> TPU v5e-8
#   3. Run this cell
import glob
import json
import sys
import zipfile
from pathlib import Path

import jax

print("jax", jax.__version__, "backend", jax.default_backend())
print("devices", jax.devices())
if jax.default_backend() != "tpu":
    raise SystemExit(
        "TPU is not attached. Set Settings -> Accelerator -> TPU v5e-8, "
        "restart the notebook, then run this cell again."
    )

data_candidates = sorted(glob.glob("/kaggle/input/**/encounters.npz", recursive=True))
if not data_candidates:
    raise SystemExit(
        "encounters.npz not found. Add the tajdinenizar/dofus-encounter-tables "
        "dataset with Add Data, then run this cell again."
    )
DATA = data_candidates[0]
ROOT = Path(DATA).parent

package_root = None
for path in [ROOT, *ROOT.rglob("combat")]:
    if (path / "combat" / "__init__.py").exists():
        package_root = path
        break
    if path.name == "combat" and (path / "__init__.py").exists():
        package_root = path.parent
        break
if package_root is None:
    for archive in sorted(ROOT.glob("*.zip")):
        with zipfile.ZipFile(archive) as zf:
            if any(name.startswith("combat/") for name in zf.namelist()):
                target = Path("/kaggle/working/_pkg")
                target.mkdir(parents=True, exist_ok=True)
                zf.extractall(target)
                package_root = target
                break
if package_root is None:
    raise SystemExit("combat package not found in the dataset")
sys.path.insert(0, str(package_root))
print("package root:", package_root)
print("data:", DATA)

from combat.encounter.train import build_parser, train

WORK = Path("/kaggle/working")
# Quick smoke check: 256 / 20 / 5. Full run: 2048 / 900 / 400.
SAMPLES = 2048
PHASE1_ITERS = 900
PHASE2_ITERS = 400


def run(iters, opponent, out, init=None):
    argv = [
        "--data", DATA,
        "--out", str(out),
        "--samples", str(SAMPLES),
        "--iters", str(iters),
        "--epochs", "2",
        "--max-rounds", "24",
        "--opponent", opponent,
        "--eval-every", "20",
        "--save-every", "50",
    ]
    if init is not None:
        argv += ["--init", str(init)]
    return train(build_parser().parse_args(argv))


phase1 = run(PHASE1_ITERS, "heuristic", WORK / "phase1.pkl")
print("phase1 last:", json.dumps(phase1["metrics"][-1], sort_keys=True))
if PHASE2_ITERS > 0:
    final = run(
        PHASE2_ITERS,
        "self",
        WORK / "encounter_selfplay.pkl",
        init=WORK / "phase1.pkl",
    )
else:
    final = phase1
summary = {
    "backend": jax.default_backend(),
    "device_count": len(jax.devices()),
    "samples": SAMPLES,
    "phase1_iters": PHASE1_ITERS,
    "phase2_iters": PHASE2_ITERS,
    "baseline": phase1.get("baseline"),
    "last": final["metrics"][-1],
}
(WORK / "metrics.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
print(json.dumps(summary, indent=1))
