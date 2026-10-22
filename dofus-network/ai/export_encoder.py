"""Export the encoder policy to ONNX, quantize it, and measure runtime cost.

The exported graph has a fixed batch of one and a fixed padded sequence
length, because the local tool router always processes one player goal at a
time.  Dynamic INT8 quantization keeps the deployable artifact small and is
the format the C# runtime is expected to load.

Run from ``dofus-network``::

    python ai/export_encoder.py \
        --checkpoint models/finetuned/encoder30/model.pt \
        --out models/finetuned/encoder30/encoder.onnx \
        --limit 20 --runs 10 --threads 4

The benchmark runs in isolated child processes so RSS numbers are not polluted
by loading PyTorch and ONNX Runtime into the same process.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


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


def _output_names() -> List[str]:
    from ai.encoder_policy import ENUM_FIELDS

    return ["tool", "presence", "bool", "span_start", "span_end"] + [
        f"enum_{index}" for index in range(len(ENUM_FIELDS))]


def export_graph(checkpoint: Path, out: Path, seq_len: int, opset: int) -> int:
    try:
        import onnx
        import torch
    except ImportError as exc:
        raise SystemExit(
            "ONNX export needs torch, onnx and onnxscript; install them into the "
            "runtime environment before exporting") from exc

    from dataclasses import asdict

    from agent.schema import TOOLS
    from ai.encoder_policy import ENCODER_SYSTEM_PROMPT, FIELDS, load_checkpoint

    class ExportWrapper(torch.nn.Module):
        def __init__(self, inner: Any) -> None:
            super().__init__()
            self.inner = inner

        def forward(self, input_ids: torch.Tensor,
                    attention_mask: torch.Tensor) -> Tuple[torch.Tensor, ...]:
            output = self.inner(input_ids, attention_mask)
            return (output["tool"], output["presence"], output["bool"],
                    output["span_start"], output["span_end"], *output["enum"])

    model, tokenizer, _meta = load_checkpoint(checkpoint, device=torch.device("cpu"))
    if seq_len > model.config.max_seq_len:
        raise SystemExit(
            f"--seq-len {seq_len} exceeds checkpoint max_seq_len {model.config.max_seq_len}")
    out.parent.mkdir(parents=True, exist_ok=True)
    tokenizer.save(str(out.with_name("tokenizer.json")))
    policy = {
        "format": "thx-encoder-policy-v1",
        "seq_len": seq_len,
        "system_prompt": ENCODER_SYSTEM_PROMPT,
        "tools": [spec.name for spec in TOOLS],
        "fields": [asdict(field) for field in FIELDS],
        "threshold": 0.5,
        "max_span": 32,
    }
    out.with_name("policy.json").write_text(
        json.dumps(policy, indent=2), encoding="utf-8")
    wrapper = ExportWrapper(model).eval()
    ids = torch.ones((1, seq_len), dtype=torch.long)
    mask = torch.ones((1, seq_len), dtype=torch.long)
    started = time.perf_counter()
    with torch.inference_mode():
        try:
            # Preferred path: the dynamo exporter traces shapes symbolically and
            # needs onnxscript, which is a *separate* package from onnx.
            torch.onnx.export(
                wrapper,
                (ids, mask),
                str(out),
                input_names=["input_ids", "attention_mask"],
                output_names=_output_names(),
                opset_version=opset,
                do_constant_folding=True,
                dynamo=True,
                external_data=False,
            )
            backend = "dynamo"
        except (ImportError, ModuleNotFoundError) as exc:
            # Kaggle's image ships torch + onnx but not onnxscript, so the
            # dynamo path dies at import time and the whole export is lost --
            # including the INT8 artifact the deployment measurement needs.
            # The TorchScript exporter needs neither.
            print(f"dynamo exporter unavailable ({exc}); "
                  f"falling back to the TorchScript exporter", flush=True)
            # The fused ``aten::_transformer_encoder_layer_fwd`` has no ONNX
            # translation, so the legacy exporter aborts the whole graph with
            # ``UnsupportedOperatorError``. ``nn.TransformerEncoderLayer`` picks
            # that op whenever ``torch.backends.mha.get_fastpath_enabled()`` is
            # True, which it is by default. Turning it off makes the layer run
            # its decomposed path, which traces to ordinary ONNX ops. It is a
            # process-global switch, so restore it as soon as the trace is done.
            fastpath = torch.backends.mha.get_fastpath_enabled()
            torch.backends.mha.set_fastpath_enabled(False)
            try:
                torch.onnx.export(
                    wrapper,
                    (ids, mask),
                    str(out),
                    input_names=["input_ids", "attention_mask"],
                    output_names=_output_names(),
                    opset_version=opset,
                    do_constant_folding=True,
                    # Must be explicit: ``dynamo`` defaults to True from torch
                    # 2.9 on, so omitting it silently re-enters the path that
                    # just failed.
                    dynamo=False,
                    dynamic_axes={
                        "input_ids": {0: "batch", 1: "seq"},
                        "attention_mask": {0: "batch", 1: "seq"},
                        **{name: {0: "batch"} for name in _output_names()},
                    },
                )
                backend = "torchscript"
            finally:
                torch.backends.mha.set_fastpath_enabled(fastpath)
    onnx_model = onnx.load(str(out))
    onnx.checker.check_model(onnx_model)
    graph_batch = (onnx_model.graph.input[0].type.tensor_type.shape.dim[0].dim_param
                   or "fixed")
    print(
        f"exported {out} in {time.perf_counter() - started:.1f}s "
        f"({out.stat().st_size / 1e6:.1f} MB) via {backend}, "
        f"batch dim {graph_batch}",
        flush=True,
    )
    return sum(parameter.numel() for parameter in model.parameters())


def quantize_graph(out: Path, int8_out: Path) -> None:
    try:
        import onnx
        from onnxruntime.quantization import QuantType, quantize_dynamic
    except ImportError as exc:
        raise SystemExit(
            "INT8 quantization needs onnx and onnxruntime; install them into the "
            "runtime environment before quantizing") from exc

    model = onnx.load(str(out))
    model.graph.ClearField("value_info")
    clean = int8_out.with_name(int8_out.stem + "_clean.onnx")
    onnx.save(model, str(clean))
    onnx.shape_inference.infer_shapes_path(str(clean), str(clean))
    started = time.perf_counter()
    quantize_dynamic(str(clean), str(int8_out), weight_type=QuantType.QInt8)
    clean.unlink(missing_ok=True)
    print(
        f"quantized {int8_out} in {time.perf_counter() - started:.1f}s "
        f"({int8_out.stat().st_size / 1e6:.1f} MB)",
        flush=True,
    )


def _load_cases(path: Optional[str], limit: int) -> List[Dict[str, Any]]:
    if path:
        cases = [
            json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        from agent.tool_eval import GOLD

        cases = list(GOLD)
    return cases[:limit] if limit else cases


def _prepare_inputs(
    checkpoint: Path,
    cases_path: Optional[str],
    limit: int,
    seq_len: int,
) -> List[Tuple[Any, Any, int]]:
    import torch

    from ai.encoder_policy import (
        ENCODER_SYSTEM_PROMPT,
        EncoderDataset,
        load_checkpoint,
    )

    _model, tokenizer, _meta = load_checkpoint(checkpoint, device=torch.device("cpu"))
    rows = _load_cases(cases_path, limit)
    dataset = EncoderDataset(rows, tokenizer, seq_len, ENCODER_SYSTEM_PROMPT)
    if not dataset.items:
        raise SystemExit("no cases fit inside --seq-len")
    inputs: List[Tuple[Any, Any, int]] = []
    for item in dataset.items[:limit]:
        length = len(item["input_ids"])
        ids = torch.full((1, seq_len), dataset.pad_id, dtype=torch.long)
        mask = torch.zeros((1, seq_len), dtype=torch.long)
        ids[0, :length] = torch.tensor(item["input_ids"], dtype=torch.long)
        mask[0, :length] = 1
        inputs.append((ids, mask, length))
    return inputs


def _write_inputs(inputs: Sequence[Tuple[Any, Any, int]], path: Path) -> None:
    import numpy as np

    ids = np.concatenate([item[0].numpy() for item in inputs], axis=0)
    masks = np.concatenate([item[1].numpy() for item in inputs], axis=0)
    np.savez(path, input_ids=ids, attention_mask=masks)


def _measure(
    mode: str,
    checkpoint: Path,
    onnx_path: Optional[Path],
    inputs_path: Optional[Path],
    cases_path: Optional[str],
    limit: int,
    seq_len: int,
    warmup: int,
    runs: int,
    threads: int,
) -> Dict[str, Any]:
    if mode == "pytorch":
        import torch

        from ai.encoder_policy import load_checkpoint

        torch.set_num_threads(threads)
        inputs = _prepare_inputs(checkpoint, cases_path, limit, seq_len)
        memory_before = _memory_mb()
        model, _tokenizer, _meta = load_checkpoint(
            checkpoint, device=torch.device("cpu"))
        session = None
        load_seconds = 0.0

        def invoke(index: int) -> None:
            ids, mask, _length = inputs[index]
            with torch.inference_mode():
                model(ids, mask)

        examples = len(inputs)
    elif mode.startswith("onnx"):
        if onnx_path is None:
            raise SystemExit("--onnx is required for ONNX benchmark modes")
        if inputs_path is None:
            raise SystemExit("--inputs is required for ONNX benchmark modes")
        try:
            import numpy as np
            import onnxruntime as ort
        except ImportError as exc:
            raise SystemExit("ONNX benchmark needs numpy and onnxruntime") from exc
        data = np.load(inputs_path)
        input_ids = data["input_ids"]
        attention_mask = data["attention_mask"]
        examples = len(input_ids)
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.enable_mem_pattern = False
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        memory_before = _memory_mb()
        started = time.perf_counter()
        session = ort.InferenceSession(
            str(onnx_path), sess_options=options, providers=["CPUExecutionProvider"])
        load_seconds = time.perf_counter() - started
        model = None

        def invoke(index: int) -> None:
            session.run(None, {
                "input_ids": input_ids[index:index + 1],
                "attention_mask": attention_mask[index:index + 1],
            })
    else:
        raise SystemExit(f"unknown benchmark mode {mode}")

    for index in range(min(warmup, examples)):
        invoke(index)
    memory_after_warmup = _memory_mb()
    times: List[float] = []
    cpu_times: List[float] = []
    for _ in range(runs):
        for index in range(examples):
            wall_started = time.perf_counter()
            cpu_started = time.process_time()
            invoke(index)
            times.append(time.perf_counter() - wall_started)
            cpu_times.append(time.process_time() - cpu_started)
    memory_after = _memory_mb()
    return {
        "mode": mode,
        "examples": examples,
        "seq_len": seq_len,
        "load_seconds": load_seconds,
        "latency": _summary(times),
        "cpu": _summary(cpu_times),
        "memory": {
            "before": memory_before,
            "after_warmup": memory_after_warmup,
            "after_runs": memory_after,
        },
        "threads": threads,
    }


def _run_benchmark(
    mode: str,
    checkpoint: Path,
    onnx_path: Optional[Path],
    cases_path: Optional[str],
    limit: int,
    seq_len: int,
    warmup: int,
    runs: int,
    threads: int,
    extra_libs: Optional[str],
    work_dir: Path,
) -> Dict[str, Any]:
    command = [
        sys.executable, str(Path(__file__).resolve()),
        "--bench-one", mode,
        "--checkpoint", str(checkpoint),
        "--seq-len", str(seq_len),
        "--limit", str(limit),
        "--warmup", str(warmup),
        "--runs", str(runs),
        "--threads", str(threads),
    ]
    inputs_path: Optional[Path] = None
    if mode.startswith("onnx"):
        inputs_path = work_dir / f".bench_{mode}.npz"
        _write_inputs(
            _prepare_inputs(checkpoint, cases_path, limit, seq_len), inputs_path)
        command.extend(["--inputs", str(inputs_path)])
    if extra_libs:
        command.extend(["--extra-libs", extra_libs])
    if onnx_path is not None:
        command.extend(["--onnx", str(onnx_path)])
    if cases_path:
        command.extend(["--cases", cases_path])
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    completed = subprocess.run(
        command, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True)
    if inputs_path is not None:
        inputs_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise SystemExit(
            f"benchmark {mode} failed\nstdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise SystemExit(f"benchmark {mode} produced no output")
    return json.loads(lines[-1])


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--int8-out", default=None)
    parser.add_argument("--onnx", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--inputs", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--bench-one", choices=("pytorch", "onnx_fp32", "onnx_int8"),
                        default=None, help=argparse.SUPPRESS)
    parser.add_argument("--cases", default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--seq-len", type=int, default=256)
    parser.add_argument("--opset", type=int, default=18)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--extra-libs", default=None,
                        help="optional directory containing onnx/onnxruntime")
    parser.add_argument("--skip-quantize", action="store_true")
    parser.add_argument("--skip-bench", action="store_true")
    parser.add_argument("--report", default=None)
    args = parser.parse_args(argv)
    if args.extra_libs and args.extra_libs not in sys.path:
        sys.path.insert(0, args.extra_libs)

    checkpoint = Path(args.checkpoint)
    if args.bench_one:
        onnx_path = Path(args.onnx) if args.onnx else None
        inputs_path = Path(args.inputs) if args.inputs else None
        result = _measure(
            args.bench_one, checkpoint, onnx_path, inputs_path, args.cases,
            args.limit, args.seq_len, args.warmup, args.runs, args.threads)
        print(json.dumps(result))
        return 0

    if not args.out:
        raise SystemExit("--out is required")
    out = Path(args.out)
    int8_out = Path(args.int8_out) if args.int8_out else out.with_name(
        out.stem + "_int8.onnx")
    report_path = Path(args.report) if args.report else out.with_suffix(".report.json")
    params = export_graph(checkpoint, out, args.seq_len, args.opset)
    if not args.skip_quantize:
        quantize_graph(out, int8_out)
    import torch

    report: Dict[str, Any] = {
        "checkpoint": str(checkpoint),
        "params": params,
        "seq_len": args.seq_len,
        "opset": args.opset,
        "fp32_onnx": str(out),
        "fp32_mb": out.stat().st_size / 1e6,
        "tokenizer": str(out.with_name("tokenizer.json")),
        "policy": str(out.with_name("policy.json")),
        "int8_onnx": None if args.skip_quantize else str(int8_out),
        "int8_mb": None if args.skip_quantize else int8_out.stat().st_size / 1e6,
        "torch_version": torch.__version__,
        "platform": platform.platform(),
        "benchmarks": {},
    }
    if not args.skip_bench:
        report["benchmarks"]["pytorch"] = _run_benchmark(
            "pytorch", checkpoint, None, args.cases, args.limit, args.seq_len,
            args.warmup, args.runs, args.threads, args.extra_libs, out.parent)
        report["benchmarks"]["onnx_fp32"] = _run_benchmark(
            "onnx_fp32", checkpoint, out, args.cases, args.limit, args.seq_len,
            args.warmup, args.runs, args.threads, args.extra_libs, out.parent)
        if not args.skip_quantize:
            report["benchmarks"]["onnx_int8"] = _run_benchmark(
                "onnx_int8", checkpoint, int8_out, args.cases, args.limit,
                args.seq_len, args.warmup, args.runs, args.threads,
                args.extra_libs, out.parent)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"report {report_path}", flush=True)
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
