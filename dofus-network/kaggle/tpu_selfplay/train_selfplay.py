"""Kaggle accelerator kernel: Dofus combat self-play.

Prerequisites, both supplied as a private Kaggle dataset named
``dofus-encounter-tables`` (see ``prepare_dataset.py``):

    encounters.npz
    combat/encounter/{env,model,heuristic,train}.py
    combat/__init__.py

The kernel runs two phases:

1. the player policy learns against the scripted opponent, which finishes
   fights and gives the value head decisive targets;
2. the same weights continue with both sides learned (self-play), which is the
   distribution the value head will actually be asked about.

Set ``PHASE1_ITERS`` / ``PHASE2_ITERS`` / ``TPU_SAMPLES`` in the kernel
environment to change the budget. No internet or credentials are used here.
"""

from __future__ import annotations

import glob
import json
import os
import sys
import zipfile
from pathlib import Path

import jax

INPUTS = sorted(str(path) for path in Path("/kaggle/input").glob("*")) \
    if Path("/kaggle/input").exists() else []
print("dataset mount:", INPUTS)
CANDIDATES = sorted(glob.glob("/kaggle/input/**/encounters.npz", recursive=True))
if not CANDIDATES:
    raise SystemExit(
        "encounters.npz not found under /kaggle/input (saw %s). Add the "
        "private 'dofus-encounter-tables' dataset to this kernel first."
        % (INPUTS or "nothing"))
DATA = CANDIDATES[0]
ROOT = Path(DATA).parent


def _add_package(root: Path) -> Path:
    """Make ``combat.encounter`` importable from a dataset folder or zip.

    The Kaggle CLI archives nested folders as ``combat.zip``; a dataset that
    was uploaded from the prepared folder has a plain directory instead. Both
    layouts must work, because the kernel cannot afford a silent fallback to
    "module not found" after the TPU is allocated.
    """
    work = Path("/kaggle/working/encounter_pkg")
    for archive in sorted(root.glob("*.zip")):
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            if any(name.startswith("combat/") for name in names):
                zf.extractall(work)
            else:
                target = work / "combat"
                target.mkdir(parents=True, exist_ok=True)
                zf.extractall(target)
    for candidate in (work, root):
        if (candidate / "combat" / "__init__.py").exists():
            sys.path.insert(0, str(candidate))
            return candidate
    raise SystemExit(f"combat package not found under {root} or its zips")


PACKAGE_ROOT = _add_package(ROOT)
print("package root:", PACKAGE_ROOT)

from combat.encounter.train import build_parser, train  # noqa: E402

WORK = Path("/kaggle/working")
# 4096 fights per update is still small for a 0.2M-parameter head, but the
# harder curriculum has more groups of 3-4 and bosses, so the extra rows are
# worth the launch. The rollout is a 48-step scan; iteration cost after the
# first compile is the number to watch in the log.
SAMPLES = int(os.environ.get("TPU_SAMPLES", "4096"))
PHASE1 = int(os.environ.get("PHASE1_ITERS", "250"))
PHASE2 = int(os.environ.get("PHASE2_ITERS", "600"))
REQUIRE_ACCELERATOR = os.environ.get("REQUIRE_ACCELERATOR", "1") != "0"
BASE_CANDIDATES = sorted(
    glob.glob("/kaggle/input/**/base_encounter_selfplay.pkl", recursive=True))
BASE = Path(BASE_CANDIDATES[0]) if BASE_CANDIDATES else None


def run(iters: int, opponent: str, out: Path, init: Path = None):
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
    args = build_parser().parse_args(argv)
    return train(args)


def main() -> int:
    devices = jax.devices()
    platform = jax.default_backend()
    print("jax", jax.__version__, "backend", platform, "devices", devices)
    print("local_device_count", jax.local_device_count())
    if platform not in ("tpu", "gpu"):
        message = (f"this kernel is running on {platform}, not an accelerator. "
                   "Refusing to spend hours on CPU; check the accelerator and retry.")
        if REQUIRE_ACCELERATOR:
            raise SystemExit(message)
        print("WARNING: " + message)
    phase1 = WORK / "phase1.pkl"
    final = WORK / "encounter_selfplay.pkl"
    print("resume base:", BASE or "none (fresh phase 1)")
    first = run(PHASE1, "heuristic", phase1, init=BASE)
    print("phase1 metrics:", json.dumps(first["metrics"][-1], sort_keys=True))
    if PHASE2 > 0:
        second = run(PHASE2, "self", final, init=phase1)
        print("phase2 metrics:", json.dumps(second["metrics"][-1], sort_keys=True))
    else:
        final = phase1
    last = second if PHASE2 > 0 else first
    summary = {
        "device": platform,
        "device_count": len(devices),
        "samples": SAMPLES,
        "phase1_iters": PHASE1,
        "phase2_iters": PHASE2,
        "out": str(final),
        "baseline": first.get("baseline"),
        "last": last["metrics"][-1],
    }
    (WORK / "metrics.json").write_text(json.dumps(summary, indent=1),
                                       encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
