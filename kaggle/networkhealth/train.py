"""Retrain the network-health model on Kaggle, where the GPU is.

Why this exists: the checkpoint in `networkhealth_artifacts/model.pt` was trained before the
architecture changed (BatchNorm -> GroupNorm) and before SEQ_LEN went 128 -> 32, so it no
longer loads against the code. Retraining it locally took 42 minutes of CPU for 11 minutes of
wall time and had not finished. A T4 does it in a couple of minutes.

The kernel is deliberately thin: it imports the same `networkhealth.training.train_model` the
CLI uses, so there is no second implementation to drift. What it adds is a GPU and a writable
output directory — the package directory is read-only on Kaggle.

Input:  a private dataset containing `networkhealth/` (the package) and `features.npz`
        (built by `python -m networkhealth prepare --seq-len 32`)
Output: /kaggle/working/model.pt, evaluation.json, report.md
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working")


def find_package_root() -> Path:
    """Where is the `networkhealth` package?

    Kaggle mounts each uploaded directory as a zip, so the package can arrive as
    `<slug>/networkhealth/__init__.py` or inside `<slug>/networkhealth.zip`. Search rather
    than assume, because guessing wrong here costs a full kernel run.
    """
    for candidate in INPUT.rglob("networkhealth/__init__.py"):
        return candidate.parent.parent
    for archive in INPUT.rglob("*.zip"):
        try:
            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
                if any(n.endswith("networkhealth/__init__.py") for n in names):
                    dest = WORK / "_unpacked"
                    dest.mkdir(parents=True, exist_ok=True)
                    zf.extractall(dest)
                    for candidate in dest.rglob("networkhealth/__init__.py"):
                        return candidate.parent.parent
        except zipfile.BadZipFile:
            continue
    raise SystemExit("could not find the networkhealth package under /kaggle/input")


def find_features() -> Path:
    for candidate in INPUT.rglob("features.npz"):
        return candidate
    raise SystemExit("could not find features.npz under /kaggle/input")


def main() -> int:
    root = find_package_root()
    features = find_features()
    sys.path.insert(0, str(root))
    print("package root:", root)
    print("features:    ", features, "%.1f MB" % (features.stat().st_size / 1e6))

    import numpy as np
    import torch

    from networkhealth.cli import _load_examples
    from networkhealth.features import SEQ_LEN
    from networkhealth.splits import SplitConfig
    from networkhealth.training import TrainConfig, train_model

    print("torch", torch.__version__, "cuda", torch.cuda.is_available(),
          torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")

    examples, payload = _load_examples(features)
    x = payload["x"]
    print("examples: %d, window %d x %d features" % (x.shape[0], x.shape[1], x.shape[2]))
    if x.shape[1] != SEQ_LEN:
        raise SystemExit(
            "features are built for a %d-step window but the code expects %d; rebuild them "
            "with: python -m networkhealth prepare --seq-len %d" % (x.shape[1], SEQ_LEN, SEQ_LEN))

    # Same minimal series map cmd_train builds when clean.jsonl is absent. Baselines only
    # need the raw rows; the model itself trains from the windows.
    #
    # The RTT has to come from the npz's own `last_rtt` column: `_load_examples` does not
    # copy it into the example dicts, and reading it from there would silently make every
    # baseline row a loss.
    last_rtt = payload["last_rtt"]
    series_map: dict = {}
    for i, example in enumerate(examples):
        key = (example["source"], example["measurement_id"], example["probe_id"])
        payload_row = series_map.setdefault(key, {
            "series_id": example["series_id"], "rows": [], "features": None})
        rtt = float(last_rtt[i])
        payload_row["rows"].append({
            "timestamp": example["timestamp"],
            "rtt_ms": None if rtt < 0 else rtt,
        })
    for payload_row in series_map.values():
        payload_row["rows"].sort(key=lambda r: r["timestamp"])

    config = TrainConfig(
        epochs=int(os.environ.get("EPOCHS", "80")),
        batch_size=256,
        learning_rate=2e-3,
        patience=10,
        hidden=64,
        levels=4,
        dropout=0.1,
        seed=42,
        use_identity_mask=True,
        split=SplitConfig(mode="time_and_series", valid_fraction=0.2,
                          purge_seconds=3600, gap_seconds=1800, seed=42),
    )

    out_dir = WORK / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = train_model(examples, series_map, config, out_dir)
    print("artifacts:", json.dumps({k: str(v) for k, v in artifacts.items()}, indent=1)[:800])

    # Put the two files a caller needs at the top level of /kaggle/working, because the
    # output download is what this whole run exists for.
    checkpoint = Path(artifacts["checkpoint"])
    if checkpoint.exists():
        shutil.copy2(checkpoint, WORK / "model.pt")
    evaluation = out_dir / "evaluation.json"
    if evaluation.exists():
        shutil.copy2(evaluation, WORK / "evaluation.json")
        metrics = json.loads(evaluation.read_text(encoding="utf-8"))
        (WORK / "report.md").write_text(
            "# network health retrain\n\n"
            "window: %d steps x %d features, %d examples\n\n"
            "```json\n%s\n```\n" % (x.shape[1], x.shape[2], x.shape[0],
                                     json.dumps(metrics, indent=1)[:4000]),
            encoding="utf-8")

    for path in sorted(WORK.glob("*")):
        if path.is_file():
            print("output:", path.name, "%.2f MB" % (path.stat().st_size / 1e6))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
