"""Measure what the encoder policy costs to *run* — RAM, latency, throughput.

``ai/bench_encoder.py`` answers "is it accurate". This answers the other half of
the shipping question: does it fit the 90 MB per-agent-instance budget, and how
many milliseconds does a turn cost. Both are measured here rather than estimated,
because the RAM budget is the constraint the whole architecture was chosen against
and a 2x error in it invalidates the shared-weights argument in
``docs/ARCH_REASSESSMENT.md`` §B.

What it reports
---------------
* **resident set** at three points — interpreter only, after the model and
  tokenizer load, and after the gazetteer and KB attach — so the cost of the
  deterministic layer is visible separately from the cost of the model;
* **per-call latency** mean / p50 / p95 / p99 and the split between the forward
  pass and ``clean_call``;
* **throughput** in calls per second, single-threaded, which is the number that
  decides whether one agent can keep up with a fight;
* **refusals, invalid calls and repairs**, because a fast policy that refuses is
  not fast.

Run from ``dofus-network``::

    python ai/encoder_cost_probe.py --checkpoint models/encoder30/model.pt \\
        --cases data/core_sft_v4/val.jsonl --limit 300 --warmup 20 \\
        --out models/encoder30/cost_probe.json

The ONNX path is probed the same way when ``--onnx`` is given; pass both to get
the comparison in one report.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# resident set, without adding a dependency
# ---------------------------------------------------------------------------

def _win_counters() -> Optional[Tuple[int, int]]:
    """``(working_set, peak_working_set)`` in bytes via ``GetProcessMemoryInfo``.

    ``argtypes`` must be declared: without it ctypes passes the structure pointer
    as a 32-bit int, the call fails, and every RSS reading comes back ``None`` --
    which is how the first version of this probe shipped.
    """
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
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        get_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_info.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.POINTER(_Counters),
            ctypes.wintypes.DWORD,
        ]
        get_info.restype = ctypes.wintypes.BOOL
        if get_info(ctypes.windll.kernel32.GetCurrentProcess(),
                    ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)
    except Exception:
        pass
    return None


def _rss_bytes() -> Optional[int]:
    """Current working set in bytes, or ``None`` if the platform hides it."""
    try:
        import psutil  # type: ignore

        return int(psutil.Process().memory_info().rss)
    except Exception:
        pass
    counters = _win_counters()
    return None if counters is None else counters[0]


def _peak_rss_bytes() -> Optional[int]:
    """Peak working set, or ``None``. Only meaningful as a high-water mark."""
    try:
        import psutil  # type: ignore

        return int(psutil.Process().memory_info().peak_wss)
    except Exception:
        pass
    counters = _win_counters()
    return None if counters is None else counters[1]


def _mb(value: Optional[int]) -> Optional[float]:
    return None if value is None else round(value / (1024 * 1024), 1)


def _torch_threads() -> Optional[int]:
    try:
        import torch

        return int(torch.get_num_threads())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------

def _load_goals(path: Optional[Path], limit: int) -> List[str]:
    """Goals to run through the policy. The corpus is the realistic input."""
    goals: List[str] = []
    if path and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            goal = row.get("goal")
            if isinstance(goal, str) and goal.strip():
                goals.append(goal)
            if limit and len(goals) >= limit:
                break
    if not goals:
        goals = [
            "va à [12, -8]", "avance la quête 1935", "vends 12 Laines de Bouftou",
            "scanne le prix de la Laine de Bouftou", "farm des Kanigs",
            "arrête tout", "suis-moi", "où suis-je",
        ]
    return goals


def probe(
    checkpoint: Optional[Path],
    onnx: Optional[Path],
    tokenizer: Optional[Path],
    policy_json: Optional[Path],
    cases: Optional[Path],
    limit: int,
    warmup: int,
    postprocess: bool,
    device: str,
    threads: int = 0,
) -> Dict[str, Any]:
    from ai.encoder_serve import EncoderToolPolicy

    baseline = _rss_bytes()
    started = time.perf_counter()
    policy = EncoderToolPolicy(
        checkpoint=checkpoint,
        onnx=onnx,
        tokenizer=tokenizer,
        policy_json=policy_json,
        device=device,
        postprocess=postprocess,
    )
    load_seconds = round(time.perf_counter() - started, 2)
    after_model = _rss_bytes()

    goals = _load_goals(cases, limit)
    if not goals:
        raise SystemExit("no goals to probe")

    # Warm up: the first call pays for lazy imports, allocator growth and the
    # gazetteer/KB index build. Reporting it would measure Python, not the model.
    for goal in goals[: max(warmup, 1)]:
        policy.choose(goal, tools=None)

    latencies: List[float] = []
    post_ms: List[float] = []
    forward_ms: List[float] = []
    refused = invalid = fallbacks = 0
    # ``total_ms`` and ``postprocess_ms`` are cumulative counters, so the
    # per-call cost is the delta against the previous iteration.
    prev_total = policy.total_ms
    prev_post = policy.postprocess_ms
    for goal in goals:
        policy.choose(goal, tools=None)
        step = policy.total_ms - prev_total
        post = policy.postprocess_ms - prev_post
        prev_total, prev_post = policy.total_ms, policy.postprocess_ms
        latencies.append(step)
        post_ms.append(post)
        forward_ms.append(max(step - post, 0.0))
        if policy.calls > 0 and policy.refused + policy.invalid > refused + invalid:
            refused = policy.refused
        invalid = policy.invalid
        fallbacks = policy.fallbacks

    latencies.sort()
    post_ms.sort()
    forward_ms.sort()

    def pct(values: Sequence[float], q: float) -> Optional[float]:
        if not values:
            return None
        index = min(int(round(q * (len(values) - 1))), len(values) - 1)
        return round(values[index], 3)

    total_seconds = sum(latencies) / 1000.0
    after_run = _rss_bytes()
    stats = policy.stats()
    refused = stats["refused"]

    return {
        "backend": stats["backend"],
        "device": device,
        "threads": threads or None,
        "torch_threads": _torch_threads(),
        "postprocess": postprocess,
        "cases": len(goals),
        "load_seconds": load_seconds,
        "rss_mb": {
            "interpreter": _mb(baseline),
            "after_model_load": _mb(after_model),
            "after_run": _mb(after_run),
            "peak": _mb(_peak_rss_bytes()),
            "resolvers": stats["postprocess"].get("resolver_mb"),
        },
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 3) if latencies else None,
            "p50": pct(latencies, 0.50),
            "p95": pct(latencies, 0.95),
            "p99": pct(latencies, 0.99),
            "max": round(latencies[-1], 3) if latencies else None,
            "forward_mean": round(statistics.fmean(forward_ms), 3) if forward_ms else None,
            "postprocess_mean": round(statistics.fmean(post_ms), 3) if post_ms else None,
        },
        "throughput_calls_per_s": (
            round(len(goals) / total_seconds, 1) if total_seconds > 0 else None
        ),
        "calls": stats["calls"],
        "refused_none": refused,
        "invalid": invalid,
        "fallbacks": fallbacks,
        "repairs": stats["postprocess"].get("repaired"),
    }


def _render(report: Dict[str, Any]) -> str:
    rss = report["rss_mb"]
    lat = report["latency_ms"]
    out = [
        f"backend {report['backend']}  device {report['device']}  "
        f"threads {report['torch_threads']}  postprocess {report['postprocess']}",
        f"cases {report['cases']}   load {report['load_seconds']}s",
        "resident set (MB):",
        f"  interpreter        {rss['interpreter']}",
        f"  + model & tokenizer {rss['after_model_load']}",
        f"  + resolvers         {rss['resolvers']}",
        f"  after run           {rss['after_run']}",
        f"  peak                {rss['peak']}",
        "latency (ms):",
        f"  mean {lat['mean']}   p50 {lat['p50']}   p95 {lat['p95']}   "
        f"p99 {lat['p99']}   max {lat['max']}",
        f"  forward {lat['forward_mean']}   postprocess {lat['postprocess_mean']}",
        f"throughput {report['throughput_calls_per_s']} calls/s",
        f"calls {report['calls']}  refused(None) {report['refused_none']}  "
        f"invalid {report['invalid']}  fallbacks {report['fallbacks']}  "
        f"repairs {report['repairs']}",
    ]
    return "\n".join(out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--onnx", default=None)
    parser.add_argument("--tokenizer", default=None)
    parser.add_argument("--policy-json", default=None)
    parser.add_argument("--cases", default=None,
                        help="jsonl of corpus rows; goals are read from 'goal'")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--no-postprocess", action="store_true")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--threads", type=int, default=0,
                        help="torch CPU threads; 0 leaves the runtime default. "
                             "Record it -- latency is not comparable across "
                             "thread counts and the default varies by machine.")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    if not args.checkpoint and not args.onnx:
        parser.error("one of --checkpoint / --onnx is required")

    if args.threads:
        import torch

        torch.set_num_threads(args.threads)

    report = probe(
        checkpoint=Path(args.checkpoint) if args.checkpoint else None,
        onnx=Path(args.onnx) if args.onnx else None,
        tokenizer=Path(args.tokenizer) if args.tokenizer else None,
        policy_json=Path(args.policy_json) if args.policy_json else None,
        cases=Path(args.cases) if args.cases else None,
        limit=args.limit,
        warmup=args.warmup,
        postprocess=not args.no_postprocess,
        device=args.device,
        threads=args.threads,
    )
    print(_render(report))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
