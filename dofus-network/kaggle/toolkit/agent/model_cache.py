"""Process-wide model and knowledge caches for the multi-instance daemon.

The daemon runs one Autopilot per instance in one process. Before this module,
each planner opened its own ONNX Runtime session and its own SQLite handle. ONNX
weights are the expensive part: a 25 MB INT8 graph loaded N times is N copies of
the same read-only data. ONNX Runtime sessions are safe for concurrent ``run``
calls, so one session per (path, options) is the correct sharing unit.

The knowledge handle is read-only. It is opened with ``check_same_thread=False``
because the daemon constructs the planner on the control thread and runs the loop
on a worker thread; SQLite's serialized mode makes concurrent reads safe.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

_ONNX: Dict[Tuple[Any, ...], Any] = {}
_ONNX_LOCK = threading.Lock()


def _options_key(options: Any) -> Any:
    if options is None:
        return None
    return (
        getattr(options, "intra_op_num_threads", None),
        getattr(options, "inter_op_num_threads", None),
        int(getattr(options, "graph_optimization_level", -1)),
        bool(getattr(options, "enable_mem_pattern", True)),
    )


def onnx_session(path: str | Path,
                 providers: Optional[Sequence[str]] = None,
                 sess_options: Any = None) -> Any:
    """One InferenceSession per (path, providers, options) in this process."""
    import onnxruntime as ort

    chosen = tuple(providers or ("CPUExecutionProvider",))
    key = (str(Path(path)), chosen, _options_key(sess_options))
    with _ONNX_LOCK:
        existing = _ONNX.get(key)
    if existing is not None:
        return existing
    session = ort.InferenceSession(str(path), sess_options=sess_options,
                                   providers=list(chosen))
    with _ONNX_LOCK:
        return _ONNX.setdefault(key, session)


def clear() -> None:
    with _ONNX_LOCK:
        _ONNX.clear()
