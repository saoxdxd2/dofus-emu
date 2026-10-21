"""Measure the local runtime cost of a tool-policy checkpoint.

The probe separates the two costs that matter for deployment:

* a single forward pass over the prompt, which approximates the future
  encoder + decision-head runtime;
* full autoregressive generation, which is the current decoder baseline.

It reports process RSS, peak RSS, wall time, CPU time and prompt tokens per
case so hardware decisions are based on measurements instead of estimates.

Run from ``dofus-network``::

    python ai/cost_probe.py \
        --checkpoint models/finetuned/thx01-student-100m/model.pt \
        --cases "$env:TEMP/opencode/thx_bench_cases_v2.jsonl" \
        --limit 20 --warmup 2 --max-new-tokens 64 \
        --out models/finetuned/thx01-student-100m/cost_probe.json
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch  # noqa: E402

from ai.tool_policy import (  # noqa: E402
    _resolve_device,
    build_prompt_ids,
    generate_ids,
    load_checkpoint,
    parse_call,
)


def _memory_mb() -> Dict[str, Optional[float]]:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb):
            return {"rss_mb": None, "peak_rss_mb": None, "private_mb": None}
        return {
            "rss_mb": counters.WorkingSetSize / 1_000_000,
            "peak_rss_mb": counters.PeakWorkingSetSize / 1_000_000,
            "private_mb": counters.PrivateUsage / 1_000_000,
        }

    status = Path("/proc/self/status")
    if status.exists():
        values: Dict[str, float] = {}
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith(("VmRSS:", "VmHWM:", "VmSize:")):
                key, value, _unit = line.split()
                values[key.rstrip(":")] = float(value) / 1024
        return {
            "rss_mb": values.get("VmRSS"),
            "peak_rss_mb": values.get("VmHWM"),
            "private_mb": values.get("VmSize"),
        }

    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss = usage.ru_maxrss
        if sys.platform != "darwin":
            rss /= 1024
        return {"rss_mb": rss, "peak_rss_mb": rss, "private_mb": None}
    except Exception:
        return {"rss_mb": None, "peak_rss_mb": None, "private_mb": None}


def _percentile(values: Sequence[float], percent: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _summary(values: Sequence[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p95": None}
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "p50": _percentile(values, 50),
        "p95": _percentile(values, 95),
        "min": min(values),
        "max": max(values),
    }


def _load_cases(path: Optional[str], limit: int) -> List[Dict[str, Any]]:
    if path:
        cases = [
            json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        from agent.tool_eval import GOLD

        cases = list(GOLD)
    if limit:
        cases = cases[:limit]
    if not cases:
        raise SystemExit("no cases to measure")
    return cases


def _truncate_prompt(prompt: List[int], max_seq_len: int, max_new_tokens: int) -> List[int]:
    keep = max(max_seq_len - max_new_tokens, 1)
    if len(prompt) <= keep:
        return prompt
    return [prompt[0]] + prompt[-keep + 1:]


def _measure_generation(
    model: Any,
    tokenizer: Any,
    prompt: List[int],
    max_new_tokens: int,
    device: torch.device,
) -> Dict[str, Any]:
    started = time.perf_counter()
    cpu_started = time.process_time()
    generated = generate_ids(
        model, tokenizer, prompt, max_new_tokens=max_new_tokens)
    cpu_seconds = time.process_time() - cpu_started
    seconds = time.perf_counter() - started
    text = tokenizer.decode(generated, skip_special_tokens=True)
    return {
        "seconds": seconds,
        "cpu_seconds": cpu_seconds,
        "generated_tokens": len(generated),
        "parsed": parse_call(text) is not None,
    }


def _measure_forward(
    model: Any,
    prompt: List[int],
    device: torch.device,
) -> Dict[str, Any]:
    ids = torch.tensor([prompt], dtype=torch.long, device=device)
    started = time.perf_counter()
    cpu_started = time.process_time()
    with torch.inference_mode():
        model(ids)
    cpu_seconds = time.process_time() - cpu_started
    seconds = time.perf_counter() - started
    return {"seconds": seconds, "cpu_seconds": cpu_seconds}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--cases", default=None,
                        help="JSONL cases; defaults to agent.tool_eval.GOLD")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--max-new-tokens", type=int, default=160)
    parser.add_argument("--mode", choices=("both", "generate", "forward"), default="both")
    parser.add_argument("--threads", type=int, default=0)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    if args.threads:
        torch.set_num_threads(args.threads)
    cases = _load_cases(args.cases, args.limit)
    checkpoint = Path(args.checkpoint)
    device = _resolve_device(args.device)

    gc.collect()
    memory_before = _memory_mb()
    load_started = time.perf_counter()
    model, tokenizer, meta = load_checkpoint(checkpoint, device=device)
    load_seconds = time.perf_counter() - load_started
    gc.collect()
    memory_after_load = _memory_mb()

    system_prompt = meta.get("system_prompt")
    prompt_ids = [
        build_prompt_ids(tokenizer, case["goal"], case.get("state"),
                         system_prompt=system_prompt)
        for case in cases
    ]
    prompt_ids = [
        _truncate_prompt(prompt, model.config.max_seq_len, args.max_new_tokens)
        for prompt in prompt_ids
    ]

    for index in range(min(args.warmup, len(cases))):
        if args.mode in ("both", "forward"):
            _measure_forward(model, prompt_ids[index], device)
        if args.mode in ("both", "generate"):
            _measure_generation(
                model, tokenizer, prompt_ids[index],
                args.max_new_tokens, device)

    gc.collect()
    memory_after_warmup = _memory_mb()
    records: List[Dict[str, Any]] = []
    generation_seconds: List[float] = []
    generation_cpu: List[float] = []
    forward_seconds: List[float] = []
    forward_cpu: List[float] = []
    prompt_lengths: List[int] = []
    generated_tokens: List[int] = []

    for case, prompt in zip(cases, prompt_ids):
        record: Dict[str, Any] = {
            "id": case.get("id"),
            "lang": case.get("lang"),
            "prompt_tokens": len(prompt),
        }
        prompt_lengths.append(len(prompt))
        if args.mode in ("both", "forward"):
            forward = _measure_forward(model, prompt, device)
            record["forward"] = forward
            forward_seconds.append(forward["seconds"])
            forward_cpu.append(forward["cpu_seconds"])
        if args.mode in ("both", "generate"):
            generation = _measure_generation(
                model, tokenizer, prompt, args.max_new_tokens, device)
            record["generation"] = generation
            generation_seconds.append(generation["seconds"])
            generation_cpu.append(generation["cpu_seconds"])
            generated_tokens.append(generation["generated_tokens"])
        records.append(record)

    gc.collect()
    memory_after = _memory_mb()
    report: Dict[str, Any] = {
        "checkpoint": str(checkpoint),
        "checkpoint_mb": checkpoint.stat().st_size / 1_000_000,
        "device": str(device),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "torch_threads": torch.get_num_threads(),
        "mode": args.mode,
        "max_new_tokens": args.max_new_tokens,
        "cases": len(cases),
        "warmup": min(args.warmup, len(cases)),
        "model": {
            "params": sum(parameter.numel() for parameter in model.parameters()),
            "param_mb": sum(
                parameter.numel() * parameter.element_size()
                for parameter in model.parameters()) / 1_000_000,
            "config": {
                "vocab_size": model.config.vocab_size,
                "max_seq_len": model.config.max_seq_len,
                "d_model": model.config.d_model,
                "n_layers": model.config.n_layers,
                "n_heads": model.config.n_heads,
                "ffn": model.config.ffn,
            },
        },
        "load_seconds": load_seconds,
        "memory_before_mb": memory_before,
        "memory_after_load_mb": memory_after_load,
        "memory_after_warmup_mb": memory_after_warmup,
        "memory_after_mb": memory_after,
        "prompt_tokens": _summary(prompt_lengths),
        "generation_seconds": _summary(generation_seconds),
        "generation_cpu_seconds": _summary(generation_cpu),
        "generation_tokens": _summary(generated_tokens),
        "forward_seconds": _summary(forward_seconds),
        "forward_cpu_seconds": _summary(forward_cpu),
        "records": records,
    }
    if generation_seconds:
        report["generation_per_second"] = len(generation_seconds) / sum(generation_seconds)
    if forward_seconds:
        report["forward_per_second"] = len(forward_seconds) / sum(forward_seconds)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
