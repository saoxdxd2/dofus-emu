"""Hidden-state extraction for the shipped tool policy.

The SAE and the transition probe both need the vector the policy computes just
before its output projection, not the logits.  The shipped graph does not expose
that tensor, so this module adds one output to a copy of the graph and runs the
copy through ONNX Runtime.  It is an offline/training helper: the runtime reads
the exported hidden graph, never ``onnx`` itself.

For ``dedicated-v1`` the hidden tensor is the input of the ``tool_fc`` Gemm.
A graph without that name falls back to the last Gemm that produces the logits,
which covers the legacy dense heads as well.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hidden_tensor_name(model: Any, head_tensor: str = "tool_fc.weight") -> str:
    """Name of the pre-logit activation the head consumes."""
    graph = model.graph
    for node in graph.node:
        if node.op_type == "Gemm" and head_tensor in node.input:
            return node.input[0]
    # Fallback: the Gemm whose output is the graph's logits output.  This is the
    # legacy dense head, where the last Gemm is the classifier itself.
    logits_names = {out.name for out in graph.output}
    for node in reversed(list(graph.node)):
        if node.op_type == "Gemm" and any(out in logits_names for out in node.output):
            return node.input[0]
    raise ValueError("could not locate a pre-logit Gemm in %s" % head_tensor)


def hidden_width(model: Any, hidden_name: str,
                 head_tensor: str = "tool_fc.weight") -> int:
    """Infer the hidden width from the classifier weight, then from value info."""
    for init in model.graph.initializer:
        if init.name == head_tensor and len(init.dims) >= 2:
            return int(init.dims[1])
    for value in list(model.graph.value_info) + list(model.graph.output):
        if value.name == hidden_name:
            dims = [d.dim_value for d in value.type.tensor_type.shape.dim]
            if len(dims) >= 2 and dims[1] > 0:
                return int(dims[1])
    raise ValueError("could not infer hidden width for %s" % hidden_name)


def export_hidden(src: Path, dst: Path, *, head_tensor: str = "tool_fc.weight",
                  hidden_name: Optional[str] = None) -> Dict[str, Any]:
    """Write a copy of ``src`` with the pre-logit tensor as an extra output."""
    import onnx

    src, dst = Path(src), Path(dst)
    model = onnx.load(str(src))
    hidden = hidden_name or hidden_tensor_name(model, head_tensor)
    width = hidden_width(model, hidden, head_tensor)
    existing = {out.name for out in model.graph.output}
    if hidden not in existing:
        model.graph.output.append(onnx.helper.make_tensor_value_info(
            hidden, onnx.TensorProto.FLOAT, [None, width]))
    dst.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(dst))
    return {
        "source": str(src),
        "source_sha256": _sha256(src),
        "hidden_model": str(dst),
        "hidden_model_sha256": _sha256(dst),
        "hidden_output": hidden,
        "hidden_dim": width,
    }


def run_hidden(model_path: Path, features: Any,
               output: Optional[str] = None) -> np.ndarray:
    """Run the hidden graph and return ``[batch, hidden]`` float32."""
    from agent.model_cache import onnx_session

    session = onnx_session(str(model_path))
    input_name = session.get_inputs()[0].name
    outputs = [o.name for o in session.get_outputs()]
    name = output or outputs[0]
    if name not in outputs:
        raise ValueError("hidden graph %s has no output %r (has %s)"
                         % (model_path, name, outputs))
    x = np.asarray(features, dtype=np.float32)
    result = session.run([name], {input_name: x})[0]
    return np.asarray(result, dtype=np.float32)


def load_classifier(model_path: Path, *,
                    head_tensor: str = "tool_fc.weight") -> Tuple[np.ndarray, np.ndarray, float]:
    """Return ``(weight, bias, logit_scale)`` for the output projection.

    The SAE steers the hidden vector, but the logits also pass through a learned
    temperature.  Applying the hidden delta without that scale would move the
    decision at the wrong strength, so the caller gets both numbers from the same
    artifact the runtime uses.
    """
    import onnx
    from onnx import numpy_helper

    model = onnx.load(str(model_path))
    weight = bias = None
    scale = 1.0
    for init in model.graph.initializer:
        if init.name == head_tensor:
            weight = numpy_helper.to_array(init).astype(np.float32)
        elif init.name == head_tensor.replace(".weight", ".bias"):
            bias = numpy_helper.to_array(init).astype(np.float32)
        elif init.name == "logit_scale":
            scale = float(numpy_helper.to_array(init).reshape(-1)[0])
    if weight is None:
        raise ValueError("no %s in %s" % (head_tensor, model_path))
    if bias is None:
        bias = np.zeros(weight.shape[0], dtype=np.float32)
    return weight, bias, scale
