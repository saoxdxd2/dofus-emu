"""Retrain two combat teachers on a train-only encounter table.

The outputs are teacher candidates only; dev/test scoring happens outside Kaggle with pinned
partitions. Budgets have explicit local-smoke overrides.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import pickle
import sys
import time
from pathlib import Path

import jax


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


DATA_MATCH = sorted(glob.glob(str(Path(os.environ.get("KAGGLE_INPUT_ROOT", "/kaggle/input")) / "**/encounters/train.npz"), recursive=True))
if not DATA_MATCH:
    raise SystemExit("encounters/train.npz missing from the mounted Kaggle dataset")
DATA = Path(DATA_MATCH[0])
PACKAGE = DATA.parent.parent
sys.path.insert(0, str(PACKAGE))
from combat.encounter.train import build_parser, train  # noqa: E402

WORK = Path(os.environ.get("KAGGLE_WORKING", "/kaggle/working"))
OUT = WORK / "teachers"
OUT.mkdir(parents=True, exist_ok=True)
REQUIRE_ACCELERATOR = os.environ.get("REQUIRE_ACCELERATOR", "1") != "0"


def value(name, default):
    return int(os.environ.get(name, str(default)))


def source_manifest() -> dict:
    rows = []
    for path in sorted(PACKAGE.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".pkl", ".npz", ".json"}:
            rows.append({"path": path.relative_to(PACKAGE).as_posix(), "sha256": sha256(path)})
    return {"package": str(PACKAGE), "files": rows}


def geometry(path: Path) -> tuple[int, int]:
    payload = pickle.loads(path.read_bytes())
    return int(payload.get("hidden", 96)), int(payload.get("layers", 2))


def run(name: str, init: Path | None, hidden: int, layers: int, samples: int,
        warm: int, selfplay: int, seed: int, devices: int = 0):
    started = time.time()
    stage = {}
    for label, opponent, iterations in (("phase1", "heuristic", warm), ("phase2", "self", selfplay)):
        if iterations <= 0:
            continue
        checkpoint = OUT / ("%s_%s.pkl" % (name, label))
        argv = ["--data", str(DATA), "--out", str(checkpoint), "--samples", str(samples),
                "--iters", str(iterations), "--epochs", "2", "--max-rounds", "24",
                "--opponent", opponent, "--eval-every", "25", "--save-every", "100",
                "--hidden", str(hidden), "--layers", str(layers), "--seed", str(seed),
                "--devices", str(devices or jax.local_device_count())]
        if init is not None:
            want_hidden, want_layers = geometry(init)
            if (hidden, layers) != (want_hidden, want_layers):
                raise ValueError("resumed teacher geometry does not match its initial checkpoint")
            argv += ["--init", str(init)]
        stage[label] = train(build_parser().parse_args(argv))
        init = checkpoint
        print(json.dumps({"arm": name, "stage": label, "seconds": round(time.time() - started, 1),
                          "last": stage[label]["metrics"][-1]}, sort_keys=True), flush=True)
    final = OUT / ("%s.pkl" % name)
    if not final.exists():
        import shutil
        shutil.copyfile(init, final)
    params = pickle.loads(final.read_bytes())["params"]
    return {"arm": name, "checkpoint": str(final), "sha256": sha256(final),
            "hidden": hidden, "layers": layers,
            "params": int(jax.tree_util.tree_reduce(lambda x, y: x + int(y.size), params, 0)),
            "samples": samples, "warm_iters": warm, "self_iters": selfplay,
            "requested_devices": devices or jax.local_device_count(),
            "seconds": round(time.time() - started, 1), "stages": {k: v["metrics"][-1] for k, v in stage.items()}}


def save_partial(partial):
    (WORK / "metrics_partial.json").write_text(json.dumps(partial, indent=1, sort_keys=True), encoding="utf-8")
    print("partial metrics saved", flush=True)


if __name__ == "__main__":
    backend = jax.default_backend()
    if REQUIRE_ACCELERATOR and backend not in ("tpu", "gpu"):
        raise SystemExit("refusing to spend the training budget on " + backend)
    student = PACKAGE / "student_encounter_selfplay.pkl"
    seed_model = PACKAGE / "seed_encounter_es.pkl"
    if not student.is_file() or not seed_model.is_file():
        raise SystemExit("student and ES seed checkpoints are required")
    partial = {
        "jax_version": jax.__version__, "backend": backend,
        "devices": [device.platform for device in jax.devices()],
        "data": {"path": str(DATA), "sha256": sha256(DATA)},
        "sources": source_manifest(), "student": {"path": str(student), "sha256": sha256(student)},
        "arms": []}
    save_partial(partial)
    partial["arms"].append(run("teacher_large", None, value("TEACHER_HIDDEN", 128),
                               value("TEACHER_LAYERS", 3), value("TEACHER_SAMPLES", 512),
                               value("TEACHER_WARM_ITERS", 250), value("TEACHER_SELF_ITERS", 600),
                               value("TEACHER_SEED", 20261105), value("TEACHER_DEVICES", 1)))
    save_partial(partial)
    partial["arms"].append(run("teacher_es_seed", seed_model, 96, 2,
                               value("SEED_SAMPLES", 4096), value("SEED_WARM_ITERS", 200),
                               value("SEED_SELF_ITERS", 500), value("SEED_SEED", 20261106),
                               value("SEED_DEVICES", 2)))
    save_partial(partial)
    (WORK / "metrics.json").write_text(json.dumps(partial, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(partial, indent=1, sort_keys=True))
