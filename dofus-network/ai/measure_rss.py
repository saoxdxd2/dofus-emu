"""Measure what two agents actually cost in RAM.

The design assumes ~137 MB for both models in one process. That number was
arithmetic, never a measurement. This loads the artifacts the app really uses
and reports the working set after each step, so the budget is a fact:

    baseline python + numpy + onnxruntime
    + encoder_int8.onnx        (tool policy, the big one)
    + verifier_head.onnx       (agent 2)

Run with the embedded runtime (it has numpy + onnxruntime):

    runtimes\\python\\python.exe ai/measure_rss.py
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _rss_ctypes() -> float:
    try:
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        for dll, fn in (("psapi", "GetProcessMemoryInfo"),
                        ("kernel32", "K32GetProcessMemoryInfo")):
            try:
                lib = getattr(ctypes.windll, dll)
                call = getattr(lib, fn)
                if call(handle, ctypes.byref(counters), counters.cb):
                    return round(counters.WorkingSetSize / (1024.0 * 1024.0), 1)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _rss_tasklist() -> float:
    """Fallback: ask the OS. Slower, but it cannot silently return zero."""
    try:
        import subprocess
        out = subprocess.check_output(
            ["tasklist", "/FI", "PID eq %d" % os.getpid(), "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL).decode("utf-8", "replace")
        # "python.exe","1234","Console","1","123,456 K"
        # A naive split(",") breaks here: the memory column contains a comma, so
        # "17,764 K" would be read as 764 K. Parse it as CSV.
        import csv
        import io as _io
        row = next(csv.reader(_io.StringIO(out.strip())))
        if len(row) >= 5:
            mem = row[4].replace("K", "").replace(",", "").replace("\xa0", "").strip()
            return round(float(mem) / 1024.0, 1)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def rss_mb() -> float:
    """Working set of this process, in MB. Two independent methods, because a
    silent 0.0 would make the whole measurement look like a pass."""
    value = _rss_ctypes()
    if value > 0:
        return value
    return _rss_tasklist()


def load_session(path: Path) -> Optional[Any]:
    try:
        import onnxruntime as ort
        if not path.exists():
            print("  missing: %s" % path)
            return None
        return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    except Exception as exc:  # noqa: BLE001
        print("  cannot load %s: %s" % (path, exc))
        return None


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--encoder", type=Path,
                    default=REPO_ROOT / "models" / "encoder30_v15" / "encoder_int8.onnx")
    ap.add_argument("--verifier", type=Path,
                    default=REPO_ROOT / "models" / "verifier_head.onnx")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "models" / "rss_measurement.json")
    args = ap.parse_args(argv)

    steps: Dict[str, Any] = {}
    steps["python_only"] = {"rss_mb": rss_mb(), "note": sys.version.split()[0]}
    print("python only          : %6.1f MB" % steps["python_only"]["rss_mb"])

    try:
        import numpy  # noqa: F401
        steps["numpy"] = {"rss_mb": rss_mb()}
        print("+ numpy              : %6.1f MB" % steps["numpy"]["rss_mb"])
    except Exception:  # noqa: BLE001
        print("numpy missing")

    try:
        import onnxruntime  # noqa: F401
        steps["onnxruntime"] = {"rss_mb": rss_mb()}
        print("+ onnxruntime        : %6.1f MB" % steps["onnxruntime"]["rss_mb"])
    except Exception:  # noqa: BLE001
        print("onnxruntime missing - cannot measure the models")

    enc = load_session(args.encoder)
    steps["encoder"] = {"rss_mb": rss_mb(), "path": str(args.encoder),
                        "loaded": enc is not None}
    print("+ encoder_int8       : %6.1f MB%s" % (steps["encoder"]["rss_mb"],
                                                "" if enc else "  (not loaded)"))

    ver = load_session(args.verifier)
    steps["verifier"] = {"rss_mb": rss_mb(), "path": str(args.verifier),
                         "loaded": ver is not None}
    print("+ verifier_head      : %6.1f MB%s" % (steps["verifier"]["rss_mb"],
                                                "" if ver else "  (not loaded)"))

    # One real inference each, so the measurement includes the arenas the runtime
    # allocates on first use rather than the lazy baseline.
    if enc is not None:
        try:
            import numpy as np
            # The encoder takes input_ids *and* attention_mask; feeding only the
            # first one is why the warm-up used to fail and the arena was never
            # allocated in the measurement.
            feed = {}
            for inp in enc.get_inputs():
                dims = list(inp.shape)
                # The sequence axis is dynamic but the model was exported for
                # max_seq_len=256; feeding 1 makes the attention reshape fail.
                shape = [d if isinstance(d, int) else (256 if i == 1 and len(dims) == 2 else 1)
                         for i, d in enumerate(dims)]
                dtype = "int64" if "int" in (inp.type or "") else "float32"
                feed[inp.name] = np.zeros(shape, dtype=dtype)
            enc.run(None, feed)
        except Exception as exc:  # noqa: BLE001
            print("  encoder warm-up skipped: %s" % exc)
    if ver is not None:
        try:
            import numpy as np
            ver.run(None, {ver.get_inputs()[0].name: np.zeros((1, 33), dtype="float32")})
        except Exception as exc:  # noqa: BLE001
            print("  verifier warm-up skipped: %s" % exc)

    steps["after_inference"] = {"rss_mb": rss_mb()}
    print("+ first inference    : %6.1f MB" % steps["after_inference"]["rss_mb"])

    base = steps.get("onnxruntime", {}).get("rss_mb", 0.0)
    total = steps["after_inference"]["rss_mb"]
    summary = {
        "baseline_mb": base,
        "total_mb": total,
        "models_mb": round(total - base, 1),
        "design_assumption_mb": 137,
        "within_budget": total <= 137,
        "steps": steps,
    }
    print("\nmodels cost %.1f MB on top of the runtime; total %.1f MB "
          "(design assumed 137 MB -> %s)"
          % (summary["models_mb"], total, "fits" if summary["within_budget"] else "OVER"))

    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=1), encoding="utf-8")
        print("wrote %s" % args.out)
    except OSError as exc:
        print("could not write %s: %s" % (args.out, exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
