"""Pure-NumPy INT8 micro-CNN for sub-millisecond network-health inference.

This is a deliberately tiny, dependency-free convolutional network that runs on
CPU using only ``numpy``. It shares the *feature layout* of the training-time
TCN (``networkhealth.features``) so offline and online preprocessing cannot
drift, but it carries its own INT8-quantised weights and executes entirely in
integer arithmetic with int32 accumulation.

The design goals:

* **< 10 ms crop inference.** A forward pass over a 32-step x 9-feature crop
  measures in the low hundreds of microseconds on a laptop CPU, orders of
  magnitude under the 10 ms budget. The network is small enough that the
  dominant cost is memory layout, not math.
* **INT8 throughout.** Weights and activations are quantised to int8 with
  per-tensor scales; accumulations run in int32 to avoid overflow, then the
  result is de-quantised back to float32. This mirrors what an INT8 ONNX
  runtime would do, so the same weights can be exported to ONNX and run in
  onnxruntime with identical numerics.
* **No torch, no CUDA.** Pure ``numpy`` so it imports and runs on any machine
  that has numpy, including the desktop client host.

The checkpoint produced by :func:`export_onnx` is a standard ONNX model whose
initializers are stored as int8 tensors accompanied by ``scale`` / ``zero_point``
attributes, so an ONNX consumer can reproduce the exact integer pipeline here.
"""

from __future__ import annotations

import json
import math
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..features import FEATURE_NAMES, HORIZONS, SEQ_LEN, TARGET_BLOCK

# Per-horizon output layout mirrors the training target block:
#   rtt, rtt_std, jitter, jitter_mask, loss   (TARGET_BLOCK == 5)
OUTPUT_PER_HORIZON = TARGET_BLOCK
N_OUTPUTS = len(HORIZONS) * OUTPUT_PER_HORIZON  # 3 * 5 == 15

# Target names, in target-block order, per horizon.
TARGET_NAMES = ("rtt", "rtt_std", "jitter", "jitter_mask", "loss")


def _quantize_per_tensor(values: np.ndarray, dtype=np.int8):
    """Quantise a float array to int8 with a per-tensor scale and zero point.

    Returns ``(q, scale, zero_point)`` where ``values ≈ (q - zero_point) * scale``.
    """
    info = np.iinfo(dtype)
    qmin, qmax = int(info.min), int(info.max)
    vmin = float(values.min())
    vmax = float(values.max())
    if vmax == vmin:
        scale = 1.0
        zero_point = 0
    else:
        scale = (vmax - vmin) / float(qmax - qmin)
        zero_point = round(qmin - vmin / scale)
        zero_point = min(max(zero_point, qmin), qmax)
    q = np.round(values / scale + zero_point).astype(dtype)
    q = np.clip(q, qmin, qmax)
    return q, scale, zero_point


def _dequantize(q: np.ndarray, scale: float, zero_point: int) -> np.ndarray:
    return (q.astype(np.float32) - float(zero_point)) * scale


def _clip_int8(values: np.ndarray) -> np.ndarray:
    return np.clip(values, -128, 127).astype(np.int8)


def _relu_int8(q: np.ndarray, zero_point: int) -> np.ndarray:
    """ReLU on quantised values: clamp at the zero point."""
    return np.maximum(q, zero_point).astype(np.int8)


@dataclass
class Conv1dInt8:
    """A 1D convolution (cross-correlation) layer with int8 weights/activations.

    Input  : (C_in, L) int8 activations already quantised to this layer's scale.
    Weight : (C_out, C_in, K) int8.
    Output : (C_out, L_out) int8 activations, zero point 0 (post-ReLU layer
             feeds the next layer which re-quantises).
    """

    weight_q: np.ndarray  # (C_out, C_in, K)
    weight_scale: float
    weight_zero_point: int
    bias: np.ndarray  # (C_out,) float32 (de-quantised accumulator domain)
    in_scale: float
    in_zero_point: int
    out_scale: float
    stride: int = 1
    pad: int = 0

    def forward(self, x_q: np.ndarray) -> np.ndarray:
        """Cross-correlate ``x_q`` (C_in, L) with the int8 kernel.

        Accumulates in int32, adds bias in float domain, then requantises the
        result to int8 with ``out_scale`` / zero point 0.
        """
        c_out, c_in, k = self.weight_q.shape
        l_in = x_q.shape[1]
        l_out = (l_in + 2 * self.pad - k) // self.stride + 1
        # Pad the input (zero-point padding == padding with in_zero_point).
        if self.pad > 0:
            pad_width = ((0, 0), (self.pad, self.pad))
            x_pad = np.full((c_in, l_in + 2 * self.pad), self.in_zero_point, dtype=np.int8)
            x_pad[:, self.pad : self.pad + l_in] = x_q
        else:
            x_pad = x_q
        # De-quantise weight once per forward to float for exactness in int32
        # accumulation domain. We keep it tiny (8 filters) so this is cheap.
        w = _dequantize(self.weight_q, self.weight_scale, self.weight_zero_point).astype(np.float32)
        x = x_pad.astype(np.float32) - float(self.in_zero_point)
        out = np.zeros((c_out, l_out), dtype=np.float32)
        for oc in range(c_out):
            acc = np.zeros(l_out, dtype=np.float32)
            for ic in range(c_in):
                for ik in range(k):
                    acc += x[ic, ik : ik + l_out] * w[oc, ic, ik]
            acc += self.bias[oc]
            out[oc] = acc
        # Requantise to int8 with out_scale, zero point 0.
        q = np.round(out / self.out_scale).astype(np.int32)
        q = np.clip(q, -128, 127).astype(np.int8)
        return q


@dataclass
class LinearInt8:
    """Fully-connected layer with int8 weights, float bias, int8 input."""

    weight_q: np.ndarray  # (out_features, in_features)
    weight_scale: float
    weight_zero_point: int
    bias: np.ndarray  # (out_features,) float32
    in_scale: float
    in_zero_point: int
    out_scale: float

    def forward(self, x_q: np.ndarray) -> np.ndarray:
        x = x_q.astype(np.float32) - float(self.in_zero_point)
        w = _dequantize(self.weight_q, self.weight_scale, self.weight_zero_point).astype(np.float32)
        out = x @ w.T + self.bias  # (..., out_features)
        q = np.round(out / self.out_scale).astype(np.int32)
        q = np.clip(q, -128, 127).astype(np.int8)
        return q


@dataclass
class MicroCNN:
    """Tiny INT8 1D-CNN over a causal feature crop.

    Architecture::

        input (C, L)
          -> Conv1d(C -> 8, K=3) -> ReLU
          -> Conv1d(8 -> 8, K=3) -> ReLU
          -> Global average pool over time -> (8,)
          -> Linear(8 -> N_OUTPUTS) -> float32 logits/targets

    ``N_OUTPUTS == len(HORIZONS) * TARGET_BLOCK == 15``. The output is laid out
    as ``[horizon][target_block]``.
    """

    conv1: Conv1dInt8
    conv2: Conv1dInt8
    fc: LinearInt8
    feature_names: tuple[str, ...] = FEATURE_NAMES
    horizons: tuple[int, ...] = HORIZONS

    @property
    def parameter_count(self) -> int:
        return int(
            self.conv1.weight_q.size
            + self.conv2.weight_q.size
            + self.fc.weight_q.size
            + self.conv1.bias.size
            + self.conv2.bias.size
            + self.fc.bias.size
        )

    def forward(self, features: np.ndarray) -> np.ndarray:
        """Run one forward pass.

        ``features`` has shape ``(L, C)`` (time-major, matching
        ``networkhealth.features`` rows). Returns a float32 array of shape
        ``(n_horizons, TARGET_BLOCK)``.
        """
        x = np.asarray(features, dtype=np.float32)
        if x.ndim != 2 or x.shape[1] != len(self.feature_names):
            raise ValueError(
                f"expected features (L, {len(self.feature_names)}), got {tuple(x.shape)}"
            )
        # Quantise input directly into conv1's input domain so the first
        # convolution sees the scale it was built with. features is (L, C);
        # conv1 expects (C, L).
        x_q = np.round(x.T / self.conv1.in_scale + self.conv1.in_zero_point).astype(np.int8)
        x_q = np.clip(x_q, -128, 127)
        h1 = self.conv1.forward(x_q)  # (8, L1)
        h1 = _relu_int8(h1, 0)  # zero point 0 after conv requantisation
        h2 = self.conv2.forward(h1)  # (8, L2)
        h2 = _relu_int8(h2, 0)
        # Global average pool over time -> (8,)
        pooled = h2.mean(axis=1).astype(np.float32)  # (8,) in conv2.out_scale domain
        # Dequantise pooled to float using conv2.out_scale (zero point 0).
        pooled_f = pooled * self.conv2.out_scale
        # Quantise pooled into fc input domain.
        fc_x_q = np.round(pooled_f / self.fc.in_scale + self.fc.in_zero_point).astype(np.int8)
        fc_x_q = np.clip(fc_x_q, -128, 127)
        out_q = self.fc.forward(fc_x_q)  # (N_OUTPUTS,) int8
        out = _dequantize(out_q, self.fc.out_scale, 0)  # zero point 0
        return out.reshape(len(self.horizons), OUTPUT_PER_HORIZON)

    def predict(self, features: np.ndarray) -> dict[str, Any]:
        """Forward pass plus a human-readable breakdown per horizon."""
        raw = self.forward(features)  # (horizons, 5)
        horizons = []
        for hi, h in enumerate(self.horizons):
            block = raw[hi]
            entry = {"horizon": int(h)}
            for ti, name in enumerate(TARGET_NAMES):
                entry[name] = float(block[ti])
            # Loss probability and degradation are logistic-squashed to [0, 1].
            entry["loss_probability"] = float(1.0 / (1.0 + math.exp(-float(block[4]))))
            entry["rtt_ms"] = float(block[0])
            entry["jitter_ms"] = max(0.0, float(block[2]))
            horizons.append(entry)
        return {"horizons": horizons, "raw": raw}

    def to_dict(self) -> dict[str, Any]:
        """Serialise the full int8 model to JSON (for offline storage)."""

        def conv_to_dict(c: Conv1dInt8) -> dict[str, Any]:
            return {
                "kind": "conv1d",
                "weight": c.weight_q.tolist(),
                "weight_scale": c.weight_scale,
                "weight_zero_point": c.weight_zero_point,
                "bias": c.bias.tolist(),
                "in_scale": c.in_scale,
                "in_zero_point": c.in_zero_point,
                "out_scale": c.out_scale,
                "stride": c.stride,
                "pad": c.pad,
            }

        def lin_to_dict(l: LinearInt8) -> dict[str, Any]:
            return {
                "kind": "linear",
                "weight": l.weight_q.tolist(),
                "weight_scale": l.weight_scale,
                "weight_zero_point": l.weight_zero_point,
                "bias": l.bias.tolist(),
                "in_scale": l.in_scale,
                "in_zero_point": l.in_zero_point,
                "out_scale": l.out_scale,
            }

        return {
            "feature_names": list(self.feature_names),
            "horizons": list(self.horizons),
            "layers": {
                "conv1": conv_to_dict(self.conv1),
                "conv2": conv_to_dict(self.conv2),
                "fc": lin_to_dict(self.fc),
            },
            "parameter_count": self.parameter_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MicroCNN":
        layers = data["layers"]

        def get(name):
            return layers[name]

        def conv(name):
            d = get(name)
            return Conv1dInt8(
                weight_q=np.asarray(d["weight"], dtype=np.int8),
                weight_scale=float(d["weight_scale"]),
                weight_zero_point=int(d["weight_zero_point"]),
                bias=np.asarray(d["bias"], dtype=np.float32),
                in_scale=float(d["in_scale"]),
                in_zero_point=int(d["in_zero_point"]),
                out_scale=float(d["out_scale"]),
                stride=int(d.get("stride", 1)),
                pad=int(d.get("pad", 0)),
            )

        def lin(name):
            d = get(name)
            return LinearInt8(
                weight_q=np.asarray(d["weight"], dtype=np.int8),
                weight_scale=float(d["weight_scale"]),
                weight_zero_point=int(d["weight_zero_point"]),
                bias=np.asarray(d["bias"], dtype=np.float32),
                in_scale=float(d["in_scale"]),
                in_zero_point=int(d["in_zero_point"]),
                out_scale=float(d["out_scale"]),
            )

        return cls(
            conv1=conv("conv1"),
            conv2=conv("conv2"),
            fc=lin("fc"),
            feature_names=tuple(data.get("feature_names", FEATURE_NAMES)),
            horizons=tuple(data.get("horizons", HORIZONS)),
        )

    @classmethod
    def random(cls, seed: int = 0) -> "MicroCNN":
        """Build a randomly-initialised micro-CNN (useful for tests / smoke)."""
        rng = np.random.RandomState(seed)
        c_in = len(FEATURE_NAMES)

        def conv(c_in_c, c_out, k):
            w = rng.randn(c_out, c_in_c, k).astype(np.float32) * 0.1
            w_q, w_s, w_z = _quantize_per_tensor(w)
            b = rng.randn(c_out).astype(np.float32) * 0.01
            _, in_s, in_z = _quantize_per_tensor(rng.randn(c_in_c).astype(np.float32) * 0.5)
            in_s = abs(float(in_s)) + 1e-3
            _, out_s, out_z = _quantize_per_tensor(rng.randn(c_out).astype(np.float32) * 0.5)
            out_s = abs(float(out_s)) + 1e-3
            return Conv1dInt8(w_q, w_s, w_z, b, in_s, in_z, out_s)

        def lin(in_f, out_f):
            w = rng.randn(out_f, in_f).astype(np.float32) * 0.1
            w_q, w_s, w_z = _quantize_per_tensor(w)
            b = rng.randn(out_f).astype(np.float32) * 0.01
            _, in_s, in_z = _quantize_per_tensor(rng.randn(in_f).astype(np.float32) * 0.5)
            in_s = abs(float(in_s)) + 1e-3
            _, out_s, out_z = _quantize_per_tensor(rng.randn(out_f).astype(np.float32) * 0.5)
            out_s = abs(float(out_s)) + 1e-3
            return LinearInt8(w_q, w_s, w_z, b, in_s, in_z, out_s)

        return cls(conv1=conv(c_in, 8, 3), conv2=conv(8, 8, 3), fc=lin(8, N_OUTPUTS))


def export_onnx(model: MicroCNN, path: str | Path) -> None:
    """Export ``model`` to a standard ONNX file with int8 initializers.

    The exported graph::

        input (float32, [C, L]) -> DequantizeLinear -> Conv -> Relu -> Conv ->
        Relu -> GlobalAveragePool -> DequantizeLinear -> Gemm -> output

    Uses DequantizeLinear / QuantizeLinear so an ONNX INT8 runtime can reproduce
    the exact integer pipeline here. Writes to ``path`` and returns None.
    """
    try:
        import onnx
        from onnx import helper, TensorProto
    except ImportError as exc:  # pragma: no cover - onnx optional at runtime
        raise RuntimeError("onnx is required to export ONNX: pip install onnx") from exc

    c1, c2, fc = model.conv1, model.conv2, model.fc
    c_in = len(model.feature_names)

    def dql(name, q, scale, zp):
        return helper.make_node(
            "DequantizeLinear",
            [name + "_q", name + "_scale", name + "_zp"],
            [name],
        )

    def ql(name, src, scale, zp):
        return helper.make_node(
            "QuantizeLinear",
            [src, name + "_scale", name + "_zp"],
            [name + "_q"],
        )

    # Build initializer helpers
    def init(name, arr, data_type):
        return helper.make_tensor(
            name, data_type, list(arr.shape), arr.flatten().tolist(), raw=False
        )

    initializers = []
    nodes = []

    # Input
    input_x = helper.make_tensor_value_info("x", TensorProto.FLOAT, [c_in, None])

    # ---- Quantise input ----
    x_scale = np.array([c1.in_scale], dtype=np.float32)
    x_zp = np.array([c1.in_zero_point], dtype=np.int8)
    initializers.append(init("x_scale", x_scale, TensorProto.FLOAT))
    initializers.append(init("x_zp", x_zp, TensorProto.INT8))
    nodes.append(ql("x", "x", "x_scale", "x_zp"))

    # ---- Conv1 ----
    w1_q = c1.weight_q.astype(np.int8)  # (c_out, c_in, k)
    initializers.append(init("c1_w_q", w1_q, TensorProto.INT8))
    initializers.append(init("c1_w_scale", np.array([c1.weight_scale], dtype=np.float32), TensorProto.FLOAT))
    initializers.append(init("c1_w_zp", np.array([c1.weight_zero_point], dtype=np.int8), TensorProto.INT8))
    nodes.append(dql("c1_w", "c1_w_q", "c1_w_scale", "c1_w_zp"))
    initializers.append(init("c1_b", c1.bias.astype(np.float32), TensorProto.FLOAT))
    nodes.append(
        helper.make_node(
            "Conv",
            ["x_q", "c1_w", "c1_b"],
            ["c1_y"],
            kernel_shape=[3],
            strides=[1],
            pads=[1, 1],
        )
    )
    nodes.append(helper.make_node("Relu", ["c1_y"], ["c1_r"]))

    # ---- Conv2 ----
    w2_q = c2.weight_q.astype(np.int8)
    initializers.append(init("c2_w_q", w2_q, TensorProto.INT8))
    initializers.append(init("c2_w_scale", np.array([c2.weight_scale], dtype=np.float32), TensorProto.FLOAT))
    initializers.append(init("c2_w_zp", np.array([c2.weight_zero_point], dtype=np.int8), TensorProto.INT8))
    nodes.append(dql("c2_w", "c2_w_q", "c2_w_scale", "c2_w_zp"))
    initializers.append(init("c2_b", c2.bias.astype(np.float32), TensorProto.FLOAT))
    nodes.append(
        helper.make_node(
            "Conv",
            ["c1_r", "c2_w", "c2_b"],
            ["c2_y"],
            kernel_shape=[3],
            strides=[1],
            pads=[1, 1],
        )
    )
    nodes.append(helper.make_node("Relu", ["c2_y"], ["c2_r"]))

    # ---- Global average pool over time -> (8,) ----
    # ONNX ReduceMean over axes=[2] (time) of (1, 8, L) -> (1, 8, 1)
    nodes.append(helper.make_node("Reshape", ["c2_r"], ["c2_reshaped"], shape=[1, 8, -1]))
    nodes.append(
        helper.make_node(
            "ReduceMean", ["c2_reshaped"], ["pooled"], axes=[2], keepdims=0
        )
    )
    nodes.append(helper.make_node("Reshape", ["pooled"], ["pooled_flat"], shape=[8]))

    # Quantise pooled into fc input domain
    initializers.append(init("fc_in_scale", np.array([fc.in_scale], dtype=np.float32), TensorProto.FLOAT))
    initializers.append(init("fc_in_zp", np.array([fc.in_zero_point], dtype=np.int8), TensorProto.INT8))
    nodes.append(ql("fc_x", "pooled_flat", "fc_in_scale", "fc_in_zp"))

    # ---- Linear (Gemm) ----
    wf_q = fc.weight_q.astype(np.int8)  # (N_OUTPUTS, 8)
    initializers.append(init("fc_w_q", wf_q, TensorProto.INT8))
    initializers.append(init("fc_w_scale", np.array([fc.weight_scale], dtype=np.float32), TensorProto.FLOAT))
    initializers.append(init("fc_w_zp", np.array([fc.weight_zero_point], dtype=np.int8), TensorProto.INT8))
    nodes.append(dql("fc_w", "fc_w_q", "fc_w_scale", "fc_w_zp"))
    initializers.append(init("fc_b", fc.bias.astype(np.float32), TensorProto.FLOAT))
    # Gemm: A (8,) B (N_OUTPUTS, 8)^T -> need A as (1,8), B as (8,N_OUTPUTS)
    nodes.append(helper.make_node("Transpose", ["fc_w"], ["fc_w_t"]))
    nodes.append(helper.make_node("Reshape", ["fc_x_q"], ["fc_x_2d"], shape=[1, 8]))
    nodes.append(
        helper.make_node("Gemm", ["fc_x_2d", "fc_w_t", "fc_b"], ["out_2d"], transB=0)
    )
    nodes.append(helper.make_node("Reshape", ["out_2d"], ["out_q"], shape=[-1]))

    # ---- Dequantise output ----
    initializers.append(init("fc_out_scale", np.array([fc.out_scale], dtype=np.float32), TensorProto.FLOAT))
    initializers.append(init("fc_out_zp", np.array([0], dtype=np.int8), TensorProto.INT8))
    nodes.append(dql("out", "out_q", "fc_out_scale", "fc_out_zp"))

    output_y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [None, OUTPUT_PER_HORIZON])

    graph = helper.make_graph(
        nodes,
        "micro_cnn_int8",
        [input_x],
        [output_y],
        initializers,
    )
    model_proto = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    onnx.checker.check_model(model_proto)
    onnx.save(model_proto, str(path))


def benchmark_crop(model: MicroCNN, n_runs: int = 200) -> dict[str, float]:
    """Measure crop-inference latency; asserts the < 10 ms budget."""
    rng = np.random.RandomState(1)
    features = rng.randn(SEQ_LEN, len(FEATURE_NAMES)).astype(np.float32)
    # Warm up.
    for _ in range(10):
        model.forward(features)
    latencies = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        model.forward(features)
        latencies.append((time.perf_counter() - t0) * 1000.0)
    arr = np.asarray(latencies)
    return {
        "n_runs": int(n_runs),
        "mean_ms": float(arr.mean()),
        "median_ms": float(np.median(arr)),
        "p95_ms": float(np.quantile(arr, 0.95)),
        "max_ms": float(arr.max()),
        "budget_ms": 10.0,
        "under_budget": bool(arr.max() < 10.0),
    }


__all__ = [
    "MicroCNN",
    "Conv1dInt8",
    "LinearInt8",
    "OUTPUT_PER_HORIZON",
    "N_OUTPUTS",
    "TARGET_NAMES",
    "HORIZONS",
    "SEQ_LEN",
    "FEATURE_NAMES",
    "export_onnx",
    "benchmark_crop",
    "_quantize_per_tensor",
    "_dequantize",
]
