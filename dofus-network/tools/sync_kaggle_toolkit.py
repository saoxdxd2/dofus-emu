"""Regenerate the Kaggle toolkit from the harness, with an explicit copy/pin matrix (G10).

`kaggle/toolkit/` is a hand-assembled copy that backs a published Kaggle dataset. It has
drifted, and the rule for "which file is a copy and which is dataset-specific" was never
written down. This tool writes it down:

- code under `agent/`, `sim/` and `ai/` is copied from the harness when the source exists;
- a toolkit-only module (removed from the harness) is **pinned**, not deleted;
- `data/capabilities.json` and the quest/subarea caches are copied;
- `data/maps/_index.json` is **pinned** because the toolkit ships a trimmed map index;
- `dataset-metadata.json` is pinned (Kaggle metadata).

`--check` reports drift without writing (exit 1 on drift); `--write` copies and records
`kaggle/toolkit/TOOLKIT_MANIFEST.json` (path, source, sha256, status, generated_at). It
never deletes a file. Re-upload the dataset after a write if a kernel should consume it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
TOOLKIT = ROOT / "kaggle" / "toolkit"
MANIFEST = TOOLKIT / "TOOLKIT_MANIFEST.json"

SYNC_DIRS = ("agent", "sim", "ai")
SYNC_FILES = (
    "data/cache/quests_full.json",
    "data/cache/subareas_full.json",
    "data/capabilities.json",
)
PINNED = (
    "dataset-metadata.json",
    "data/maps/_index.json",
)
SKIP_SUFFIXES = (".pyc",)
SKIP_PARTS = ("__pycache__",)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_dir(toolkit: Path, name: str) -> List[Path]:
    base = toolkit / name
    if not base.is_dir():
        return []
    out = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(toolkit).as_posix()
        if any(part in SKIP_PARTS for part in path.parts) or path.name.endswith(SKIP_SUFFIXES):
            continue
        out.append(Path(rel))
    return out


def plan(root: Path = ROOT, toolkit: Path = TOOLKIT) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    seen = set()
    for name in SYNC_DIRS:
        for rel in _iter_dir(toolkit, name):
            source = root / rel
            status = "sync" if source.is_file() else "pinned"
            entries.append(_entry(rel, source, toolkit / rel, status))
            seen.add(rel.as_posix())
    for rel in SYNC_FILES:
        path = Path(rel)
        if (toolkit / path).is_file():
            source = root / path
            if not source.is_file():
                raise FileNotFoundError("toolkit file has no source: %s" % rel)
            entries.append(_entry(path, source, toolkit / path, "sync"))
            seen.add(rel)
    for rel in PINNED:
        path = Path(rel)
        if (toolkit / path).is_file():
            entries.append(_entry(path, None, toolkit / path, "pinned"))
            seen.add(rel)
    return sorted(entries, key=lambda item: item["path"])


def _entry(rel: Path, source: Path | None, target: Path, status: str) -> Dict[str, Any]:
    source_sha = sha256(source) if source is not None and source.is_file() else None
    target_sha = sha256(target) if target.is_file() else None
    return {
        "path": rel.as_posix(),
        # The toolkit-relative path is the harness-relative path for a sync entry;
        # a pinned entry has no source by definition.
        "source": rel.as_posix() if status == "sync" else None,
        "status": status,
        "source_sha256": source_sha,
        "toolkit_sha256": target_sha,
        "drift": bool(status == "sync" and source_sha and target_sha
                      and source_sha != target_sha),
        "missing": bool(status == "sync" and (source_sha is None or target_sha is None)),
    }


def drift(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [item for item in entries if item["drift"] or item["missing"]]


def write(entries: List[Dict[str, Any]], root: Path = ROOT,
          toolkit: Path = TOOLKIT) -> int:
    copied = 0
    for item in entries:
        if item["status"] != "sync":
            continue
        source = root / item["source"]
        target = toolkit / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".syncing")
        shutil.copyfile(source, tmp)
        os.replace(tmp, target)
        copied += 1
    # The manifest describes the tree *after* the copy: recording the pre-copy
    # entries would ship a manifest that still says the files drift.
    fresh = plan(root, toolkit)
    payload = {
        "generator": "tools/sync_kaggle_toolkit.py",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": ("pinned files are dataset-specific or legacy; a write never deletes "
                 "or overwrites them"),
        "files": fresh,
    }
    MANIFEST.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    return copied


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report drift, write nothing")
    mode.add_argument("--write", action="store_true", help="sync copies and write the manifest")
    args = parser.parse_args(argv)
    entries = plan()
    synced = sum(1 for item in entries if item["status"] == "sync")
    pinned = sum(1 for item in entries if item["status"] == "pinned")
    bad = drift(entries)
    print("toolkit: %d sync, %d pinned, %d drift/missing" % (synced, pinned, len(bad)))
    for item in bad[:20]:
        print("  drift: %s" % item["path"])
    if args.write:
        copied = write(entries)
        print("copied %d file(s); manifest %s" % (copied, MANIFEST))
        return 0
    if not MANIFEST.is_file():
        print("manifest missing; run --write once")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
