"""Provenance helpers: hashing, run manifests, dataset version stamps.

Every artifact written by the pipeline gets a manifest entry so that a result
can be traced back to the exact raw observations and code that produced it.
Nothing here talks to the network; it is pure bookkeeping.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "1.0"


def utc_now_iso() -> str:
    """Timezone-aware ISO timestamp; never use naive local time in artifacts."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    """Deterministic JSON so that hashing is stable across runs and machines."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def hash_object(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def hash_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Streaming file hash; safe for multi-hundred-MB JSONL files."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_git_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(Path(__file__).resolve().parent),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def environment_info() -> dict[str, Any]:
    """Capture enough about the runtime to reproduce a training run."""
    info: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "cpu_count": os.cpu_count(),
        "numpy": _module_version("numpy"),
        "torch": _module_version("torch"),
        "cuda_available": _cuda_available(),
        "git_revision": _safe_git_revision(),
    }
    if info["cuda_available"]:
        info["cuda_device"] = _cuda_device_name()
    return info


def _module_version(name: str) -> str | None:
    try:
        module = __import__(name)
    except ImportError:
        return None
    return getattr(module, "__version__", "unknown")


def _cuda_available() -> bool:
    try:
        import torch
    except ImportError:
        return False
    return bool(torch.cuda.is_available())


def _cuda_device_name() -> str | None:
    try:
        import torch
    except ImportError:
        return None
    try:
        return torch.cuda.get_device_name(0)
    except Exception:  # pragma: no cover - driver dependent
        return None


def write_manifest(path: Path, *, stage: str, inputs: Iterable[Path] = (),
                   parameters: dict[str, Any] | None = None,
                   outputs: dict[str, Path] | None = None,
                   extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write a run manifest describing what produced which artifacts.

    ``inputs`` are hashed so a result is tied to a specific dataset version;
    ``outputs`` are hashed so a downstream stage can verify it read what this
    stage wrote.
    """
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "stage": stage,
        "created_utc": utc_now_iso(),
        "parameters": parameters or {},
        "environment": environment_info(),
        "inputs": [{"path": str(p), "sha256": hash_file(p)}
                   for p in inputs if Path(p).exists()],
        "outputs": {},
    }
    for name, output_path in (outputs or {}).items():
        manifest["outputs"][name] = {
            "path": str(output_path),
            "sha256": hash_file(output_path) if Path(output_path).exists() else None,
        }
    if extra:
        manifest.update(extra)
    manifest["manifest_id"] = hash_object({
        "stage": stage,
        "parameters": manifest["parameters"],
        "inputs": manifest["inputs"],
    })
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return manifest
