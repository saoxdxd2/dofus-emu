"""Generate disjoint encounter tables for training, editing, selection and final evaluation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dofus-network"))


def row_fingerprints(arrays):
    names = sorted(arrays)
    size = len(arrays[names[0]])
    if any(len(arrays[name]) != size for name in names):
        raise ValueError("encounter arrays have different row counts")
    result = []
    for row in range(size):
        digest = hashlib.sha256()
        for name in names:
            value = np.asarray(arrays[name][row])
            digest.update(name.encode())
            digest.update(str(value.dtype).encode())
            digest.update(str(value.shape).encode())
            digest.update(value.tobytes())
        result.append(digest.hexdigest())
    return result


def partition(arrays, seed):
    fingerprints = row_fingerprints(arrays)
    unique = {}
    for index, digest in enumerate(fingerprints):
        unique.setdefault(digest, index)
    indices = np.asarray(list(unique.values()))
    np.random.default_rng(seed).shuffle(indices)
    if len(indices) < 32:
        raise ValueError("need at least 32 distinct encounters")
    n = len(indices)
    parts = np.split(indices, [n // 2, 5 * n // 8, 3 * n // 4])
    splits = dict(zip(("train", "edit", "dev", "test"), parts))
    manifest = {"generated_rows": len(fingerprints), "unique_rows": n,
                "removed_duplicates": len(fingerprints) - n, "seed": seed,
                "splits": {name: {"rows": len(rows), "fingerprints": [fingerprints[i] for i in rows]}
                           for name, rows in splits.items()}}
    return {name: {key: value[rows] for key, value in arrays.items()}
            for name, rows in splits.items()}, manifest


def prepare(out, samples, seed):
    from combat.encounter import build_dataset

    if out.exists():
        raise ValueError("output already exists; use a fresh corpus directory")
    arrays = build_dataset.build(samples, seed)
    splits, manifest = partition(arrays, seed)
    manifest["kind"] = "synthetic_encounters"
    manifest["limitations"] = ["synthetic enemy spell approximations", "not live outcome validation"]
    manifest["generator_sha256"] = hashlib.sha256(Path(build_dataset.__file__).read_bytes()).hexdigest()
    manifest["sources"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in (build_dataset.SPELLS_PATH, build_dataset.MONSTERS_PATH)}
    out.mkdir(parents=True)
    for name, data in splits.items():
        path = out / (name + ".npz")
        np.savez_compressed(path, **data)
        manifest["splits"][name]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps({"rows": manifest["unique_rows"], "removed_duplicates": manifest["removed_duplicates"],
                      "splits": {name: item["rows"] for name, item in manifest["splits"].items()}}))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=8192)
    parser.add_argument("--seed", type=int, default=20261105)
    args = parser.parse_args()
    prepare(args.out, args.samples, args.seed)
