"""Build a whitelist-only Kaggle dataset from the current combat trainer.

Only encounter-training modules, explicit checkpoints, and the four partitioned encounter tables
are copied. Machine credentials, maps, live logs, and every file outside the manifest cannot enter
the staging tree because this script creates that tree file by file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


REQUIRED = {
    "combat/__init__.py": "local",
    "combat/encounter/__init__.py": "local",
    "combat/encounter/backend.py": "local",
    "combat/encounter/env.py": "local",
    "combat/encounter/model.py": "local",
    "combat/encounter/heuristic.py": "local",
    "combat/encounter/train.py": "local",
}
CORPUS = ("train.npz", "edit.npz", "dev.npz", "test.npz")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(root: Path, corpus: Path, student: Path, seed: Path, out: Path) -> dict:
    root = Path(root).resolve(); out = Path(out).resolve()
    if out.exists():
        raise FileExistsError("use a fresh staging directory")
    for name in REQUIRED:
        source = root / "dofus-network" / name
        if name == "combat/__init__.py":
            source = root / "dofus-network/kaggle/tpu_selfplay/dataset/combat/__init__.py"
        if not source.is_file():
            raise FileNotFoundError(source)
    for path in (student, seed):
        if not Path(path).is_file():
            raise FileNotFoundError(path)
    if not (corpus / "manifest.json").is_file():
        raise FileNotFoundError("corpus manifest is required")

    staging = out
    staging.mkdir(parents=True)
    files = []
    (staging / "dataset-metadata.json").write_text(json.dumps({
        "title": "Dofus combat teacher corpus and source bundle v1",
        "id": "tajdinenizar/dofus-combat-teacher-v1", "licenses": [{"name": "CC0-1.0"}]}, indent=1),
        encoding="utf-8")
    files.append({"role": "dataset-metadata", "target": "dataset-metadata.json",
                  "bytes": (staging / "dataset-metadata.json").stat().st_size,
                  "sha256": sha256(staging / "dataset-metadata.json")})

    def copy(source: Path, target: Path, role: str):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        files.append({"role": role, "target": target.relative_to(out).as_posix(),
                      "bytes": target.stat().st_size, "sha256": sha256(target)})

    for name in REQUIRED:
        source = root / "dofus-network" / name
        if name == "combat/__init__.py":
            source = root / "dofus-network/kaggle/tpu_selfplay/dataset/combat/__init__.py"
        copy(source, staging / name, "trainer-source")
    for name in ("encounters.json",):
        old = root / "dofus-network/kaggle/tpu_selfplay/dataset" / name
        if old.is_file():
            copy(old, staging / name, "source-provenance")
    copy(student, staging / "student_encounter_selfplay.pkl", "student-init")
    copy(seed, staging / "seed_encounter_es.pkl", "teacher-seed")
    for name in CORPUS:
        copy(corpus / name, staging / "encounters" / name, "encounter-table")
    copy(corpus / "manifest.json", staging / "encounters/manifest.json", "corpus-manifest")
    manifest = {"schema": 1, "root": str(out.relative_to(root) if out.is_relative_to(root) else out),
                "staged_bytes": sum(row["bytes"] for row in files), "files": files,
                "excluded_by_design": ["machine credentials", "tunnel configs", "accounts",
                                       "live logs", "maps", "desktop source", "untracked files"]}
    (out / "BUNDLE_MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--student", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.root, args.corpus, args.student, args.seed, args.out)
    print(json.dumps({"staged_files": len(result["files"]), "staged_bytes": result["staged_bytes"]}))
