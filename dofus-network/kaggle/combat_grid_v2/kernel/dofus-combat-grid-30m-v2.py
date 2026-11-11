"""Kaggle GPU run of the 30M 560-cell combat grid trainer.

The trainer itself is **not** copied here. It is imported from the mounted
dataset (`grid_train.py`, `grid_model.py`, `grid_backend.py`, and the three npz
splits), so this kernel cannot drift from the harness the way the previous
version did: it carried its own 200-line copy of `grid_train`, which silently
stopped matching the real one as soon as the label contract changed from a hard
index to a distribution over the solver's tied-best set.

What this file adds is only what Kaggle needs:

* find where the dataset is mounted -- the depth varies between
  `/kaggle/input/<slug>` and `/kaggle/input/<owner>/<slug>`;
* put that directory on `sys.path` so the standalone modules import;
* write the checkpoint to `/kaggle/working/combat_grid` so it comes back as a
  kernel output.

Locally the same training is `python -m combat.grid_train`, on CPU.
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path


def dataset_dir() -> Path:
    found = sorted(glob.glob("/kaggle/input/**/train.npz", recursive=True))
    if not found:
        raise SystemExit("no train.npz under /kaggle/input -- is the dataset attached?")
    return Path(found[0]).parent


def main() -> int:
    data = dataset_dir()
    sys.path.insert(0, str(data))
    print("dataset", data)
    manifest = data / "manifest.json"
    if manifest.is_file():
        print("manifest", json.loads(manifest.read_text(encoding="utf-8")))

    import numpy as np
    with np.load(str(data / "train.npz")) as split:
        print("train cells", split["cells"].shape,
              "targets" in split, "rows", int(split["cells"].shape[0]))

    from grid_train import main as train_main

    return train_main([
        "--data", str(data),
        "--out", "/kaggle/working/combat_grid",
        "--hidden", "768",
        "--cell-layers", "3",
        "--fighter-layers", "2",
        "--plan-layers", "1",
        "--epochs", "12",
        "--batch", "16",
        "--lr", "3e-4",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
