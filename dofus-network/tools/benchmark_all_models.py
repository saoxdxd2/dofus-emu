"""Benchmark every model artifact in the repo and report what it is for.

Run from ``dofus-network``::

    python tools/benchmark_all_models.py --runtime embedded|dev [--out report.json]

It discovers every model artifact (``.pt``, ``.pth``, `.pkl`, ``.onnx``) under
``dofus-network/models`` and ``dofus-network/kaggle``, reads its sidecar metadata
(``metrics.json``, ``policy.json``, ``meta.json``), tries to load it, measures
load time + a single forward pass (when a loader exists), and writes a report.

The report answers, for each model:

* what it is for (from metadata / docstring / path),
* whether it loads on this runtime,
* load time, forward latency, resident-set delta,
* whether the same bytes hash as the committed artifact (offline integrity).

Models that cannot load (missing torch/jax/onnxruntime, wrong backend) are
reported as ``unavailable`` rather than silently skipped -- that is how a model
trained hard ends up as dead weight in the installer.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODEL_DIRS = [
    REPO_ROOT / "models",
    REPO_ROOT / "kaggle",
]

EXTENSIONS = (".pt", ".pth", ".pkl", ".onnx", ".bin")

HAS_TORCH = importlib.util.find_spec("torch") is not None
HAS_ONNX = importlib.util.find_spec("onnxruntime") is not None
HAS_NUMPY = importlib.util.find_spec("numpy") is not None
HAS_JAX = importlib.util.find_spec("jax") is not None


def _rss_mb() -> float:
    try:
        import ctypes
        import ctypes.wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        c = _Counters()
        c.cb = ctypes.sizeof(_Counters)
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(),
            ctypes.byref(c),
            ctypes.sizeof(_Counters),
        )
        if ok:
            return c.WorkingSetSize / (1024.0 * 1024.0)
    except Exception:  # noqa: BLE001 - best effort on Windows
        pass
    return -1.0


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def read_sidecar(model_path: Path) -> Dict[str, Any]:
    """Read every sibling metadata file into one dict."""
    meta: Dict[str, Any] = {}
    for name in ("metrics.json", "policy.json", "meta.json", "manifest.json",
                 "adapter_report.json", "router_report.json"):
        candidate = model_path.with_name(name)
        if candidate.is_file():
            try:
                meta[name] = json.loads(candidate.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                meta[name] = {"_unreadable": True}
    return meta


def describe(model_path: Path, meta: Dict[str, Any]) -> str:
    """Best-effort human description of what the model is for."""
    parts: List[str] = []
    rel = model_path.relative_to(REPO_ROOT).as_posix()
    metrics = meta.get("metrics.json") or {}
    policy = meta.get("policy.json") or {}
    manifest = meta.get("manifest.json") or {}
    adapter = meta.get("adapter_report.json") or {}

    if "combat_grid" in rel:
        parts.append("combat grid ranker (30M, ranks battle-solver plans on the 560-cell board)")
    elif "encounter" in rel and rel.endswith(".pkl"):
        parts.append("encounter PPO/PBT actor (combat policy/value, set-transformer over fighter tokens)")
    elif "encoder30_v15" in rel and model_path.suffix == ".pt":
        parts.append("NPL prompt->embedding text encoder (parses player goals into tool calls; 24.8M params)")
    elif "command_encoder30" in rel:
        parts.append("command encoder head (intent+slot classification over encoder30 base)")
    elif "command_encoder" in rel:
        parts.append("command encoder (legacy, tiny, intent+slot classification)")
    elif "tool_policy" in rel:
        parts.append("tool policy MLP (chooses tool+args from goal; shadows the rule baseline)")
    elif "verifier_head" in rel:
        parts.append("verifier head (classifies step outcome: advanced/noop/failed/blocked)")
    elif "cause_head" in rel:
        parts.append("cause head (classifies failure cause for controller recovery)")
    elif "foresight" in rel:
        parts.append("foresight / move head (predicts move outcome from state)")
    elif "veto_shadow" in rel:
        parts.append("veto shadow model (learned reject-option for the repeat-streak gate)")
    elif "board_pca" in rel:
        parts.append("board PCA projection (combat board dimensionality reduction)")
    elif "duel_value" in rel:
        parts.append("duel value head (combat outcome value estimator)")
    elif rel.endswith(".onnx") and "encoder" in rel:
        parts.append("ONNX encoder export (deployment, no torch required)")
    else:
        parts.append("model artifact")

    detail = []
    if metrics:
        for key in ("tool_accuracy", "call_accuracy", "intent_accuracy",
                    "command_exact", "best_val_loss", "top1", "best_tool_accuracy",
                    "best_call_accuracy"):
            if key in metrics:
                detail.append(f"{key}={metrics[key]}")
    if policy:
        if "intents" in policy:
            detail.append(f"intents={len(policy['intents'])}")
        if "tools" in policy:
            detail.append(f"tools={len(policy['tools'])}")
        if "threshold" in policy:
            detail.append(f"threshold={policy['threshold']}")
    if adapter:
        if "val_accuracy" in adapter:
            detail.append(f"adapter_val={adapter['val_accuracy']}")
    if detail:
        parts.append("[" + ", ".join(detail) + "]")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Per-format loaders
# ---------------------------------------------------------------------------

def load_torch_ckpt(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    if not HAS_TORCH:
        return None, "torch not installed"
    import torch
    try:
        t0 = time.perf_counter()
        obj = torch.load(str(path), map_location="cpu")
        load_s = time.perf_counter() - t0
        return obj, None
    except Exception as exc:  # noqa: BLE001
        return None, f"torch load failed: {type(exc).__name__}: {exc}"


def load_pickle(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    import pickle
    try:
        t0 = time.perf_counter()
        with path.open("rb") as handle:
            obj = pickle.load(handle)
        load_s = time.perf_counter() - t0
        return obj, None
    except Exception as exc:  # noqa: BLE001
        return None, f"pickle load failed: {type(exc).__name__}: {exc}"


def load_onnx(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    if not HAS_ONNX:
        return None, "onnxruntime not installed"
    import onnxruntime as ort
    try:
        t0 = time.perf_counter()
        sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        load_s = time.perf_counter() - t0
        return sess, None
    except Exception as exc:  # noqa: BLE001
        return None, f"onnx load failed: {type(exc).__name__}: {exc}"


def try_load(path: Path) -> Dict[str, Any]:
    """Attempt to load the artifact. Returns a result dict."""
    result: Dict[str, Any] = {"loaded": False, "loader": None, "error": None,
                              "load_seconds": None, "object_type": None}
    suffix = path.suffix.lower()
    loaders = {
        ".pt": load_torch_ckpt,
        ".pth": load_torch_ckpt,
        ".pkl": load_pickle,
        ".bin": load_pickle,
        ".onnx": load_onnx,
    }
    loader = loaders.get(suffix)
    if loader is None:
        result["error"] = f"no loader for {suffix}"
        return result
    t0 = time.perf_counter()
    obj, err = loader(path)
    result["load_seconds"] = round(time.perf_counter() - t0, 4)
    if err:
        result["error"] = err
        return result
    result["loaded"] = True
    result["loader"] = suffix.lstrip(".")
    result["object_type"] = type(obj).__name__
    result["_object"] = obj  # stripped before JSON serialization
    return result


def forward_bench(obj: Any, loader: str, path: Path) -> Dict[str, Any]:
    """Best-effort single forward pass to measure latency."""
    bench: Dict[str, Any] = {"forward_ms": None, "error": None,
                             "note": "no forward probe for this format"}
    try:
        if loader == "onnx":
            import numpy as np
            inp = sess_dummy_input(obj)
            if inp is None:
                bench["note"] = "no inputs to probe"
                return bench
            t0 = time.perf_counter()
            for _ in range(50):
                obj.run(None, inp)
            bench["forward_ms"] = round((time.perf_counter() - t0) / 50 * 1000.0, 4)
            bench["note"] = "onnxruntime CPU, 50 runs averaged"
        elif loader in ("torch", "pth"):
            import torch
            # For .pt encoder/command checkpoints, report param count + shape only;
            # a real forward needs a tokenizer batch. We report loadability instead.
            bench["note"] = "torch checkpoint loads; forward needs tokenizer batch (see encoder_serve)"
            try:
                total = sum(int(p.numel()) for p in obj.values()) if isinstance(obj, dict) else None
                if total is None and hasattr(obj, "parameters"):
                    total = sum(int(p.numel()) for p in obj.parameters())
                bench["param_count"] = total
            except Exception:  # noqa: BLE001
                pass
        elif loader == "pickle":
            bench["note"] = "pickle artifact loads; forward needs model code path (see grid_infer / brain_service)"
    except Exception as exc:  # noqa: BLE001
        bench["error"] = f"{type(exc).__name__}: {exc}"
    return bench


def sess_dummy_input(sess: Any) -> Optional[Dict[str, Any]]:
    import numpy as np
    feeds: Dict[str, Any] = {}
    try:
        for inp in sess.get_inputs():
            shape = tuple(d if isinstance(d, int) else 1 for d in inp.shape)
            dtype = np.float32
            feeds[inp.name] = np.zeros(shape, dtype=dtype)
        return feeds or None
    except Exception:  # noqa: BLE001
        return None


def discover_models() -> List[Path]:
    found: List[Path] = []
    for directory in MODEL_DIRS:
        if not directory.is_dir():
            continue
        for root, _dirs, files in os.walk(directory):
            for name in files:
                p = Path(root) / name
                if p.suffix.lower() in EXTENSIONS:
                    found.append(p)
    return sorted(found)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", choices=("embedded", "dev"), default="embedded",
                        help="which interpreter is being reported (embedded = the one in setup.exe)")
    parser.add_argument("--out", default=None, help="write JSON report to this path")
    args = parser.parse_args(argv)

    runtime_exe = sys.executable
    results: List[Dict[str, Any]] = []

    print("=" * 92)
    print(f"MODEL BENCHMARK — runtime={args.runtime} exe={runtime_exe}")
    print(f"caps: torch={HAS_TORCH} onnxruntime={HAS_ONNX} numpy={HAS_NUMPY} jax={HAS_JAX}")
    print("=" * 92)

    for path in discover_models():
        meta = read_sidecar(path)
        entry: Dict[str, Any] = {
            "path": str(path.relative_to(REPO_ROOT)),
            "abs": str(path),
            "size_mb": round(path.stat().st_size / (1024.0 * 1024.0), 2),
            "sha16": file_sha(path),
            "description": describe(path, meta),
            "meta_keys": list(meta.keys()),
        }
        rss_before = _rss_mb()
        load_res = try_load(path)
        entry.update({k: v for k, v in load_res.items() if k != "_object"})
        rss_after = _rss_mb()
        entry["rss_delta_mb"] = round(rss_after - rss_before, 2) if rss_before >= 0 else -1.0
        if load_res["loaded"]:
            bench = forward_bench(load_res["_object"], load_res["loader"], path)
            entry.update(bench)
        status = "LOADS" if load_res["loaded"] else "FAIL"
        print(f"[{status}] {entry['path']}  ({entry['size_mb']} MB)  {entry['description']}")
        if load_res["error"]:
            print(f"      error: {load_res['error']}")
        if entry.get("forward_ms") is not None:
            print(f"      forward_ms: {entry['forward_ms']}  param_count: {entry.get('param_count')}")

    # Summary
    loads = [e for e in results if e["loaded"]] if False else [e for e in []]  # placeholder
    loads = [e for e in (results or []) if e.get("loaded")]
    print("=" * 92)
    print(f"total: {len(results)}  loads: {len(loads)}  fails: {len(results) - len(loads)}")
    print("=" * 92)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        clean = []
        for e in results:
            e2 = {k: v for k, v in e.items() if k != "_object"}
            clean.append(e2)
        out_path.write_text(json.dumps(clean, indent=1), encoding="utf-8")
        print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
