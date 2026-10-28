"""INT8 ONNX inference for the encoder policy — the deployment path, no torch.

`ai/export_encoder.py` produces three files:

    encoder_int8.onnx   the graph (31.5 MB for the 30M model)
    tokenizer.json      the byte-level BPE used at training time
    policy.json         tool order, field specs, threshold, max_span

This module loads them and reproduces exactly the decoding that
``ai/encoder_policy.decode_batch`` does in torch, in numpy. That matters because
the shipped runtime is ONNX Runtime only: if decoding lives in torch, the model
cannot ship.

The arg decoding is factored into pure functions (``decode_outputs``) so it can be
unit-tested without onnxruntime installed — see ``--selftest``.

Usage::

    python -m ai.onnx_policy --selftest
    python -m ai.onnx_policy --onnx models/encoder30/encoder_int8.onnx \
        --goal "vends la Laine de Bouftou"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np


def _sigmoid(value: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-value))


def best_span(start_logits: np.ndarray, end_logits: np.ndarray,
              length: int, max_span: int = 32, top_k: int = 5) -> Optional[Tuple[int, int]]:
    """Same search as ``encoder_policy._best_span``, in numpy."""
    if length <= 0:
        return None
    start = start_logits[:length].copy()
    end = end_logits[:length].copy()
    start[0] = -1e4          # position 0 is [BOS]
    end[0] = -1e4
    k = min(top_k, length)
    start_idx = np.argsort(-start)[:k]
    end_idx = np.argsort(-end)[:k]
    best: Optional[Tuple[float, int, int]] = None
    for si in start_idx:
        for ei in end_idx:
            if ei < si or ei - si >= max_span:
                continue
            score = float(start[si] + end[ei])
            if best is None or score > best[0]:
                best = (score, int(si), int(ei))
    return None if best is None else (best[1], best[2])


def parse_span(text: str, field_type: str) -> Any:
    """Same coercion as ``encoder_policy._parse_span``."""
    import re

    text = (text or "").strip()
    if not text:
        return None
    if field_type == "int":
        match = re.search(r"-?\d+", text)
        return int(match.group(0)) if match else None
    if field_type == "float":
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else None
    if field_type == "str_list":
        return [part.strip() for part in re.split(r"[,;]", text) if part.strip()]
    return text


def decode_outputs(
    outputs: Sequence[np.ndarray],
    fields: Sequence[Dict[str, Any]],
    tools: Sequence[str],
    length: int,
    threshold: float = 0.5,
    max_span: int = 32,
) -> Dict[str, Any]:
    """Turn raw head outputs into ``{"tool": ..., "args": {...}}``.

    ``outputs`` order matches ``export_encoder._output_names()``:
    tool, presence, bool, span_start, span_end, enum_0..enum_n
    """
    tool_logits, presence, bool_logits, span_start, span_end = outputs[:5]
    enum_outputs = outputs[5:]

    tool_index = int(np.argmax(tool_logits))
    tool = tools[tool_index] if 0 <= tool_index < len(tools) else tools[0]
    presence_p = _sigmoid(np.asarray(presence))
    bool_p = _sigmoid(np.asarray(bool_logits))

    # Field indices belonging to the chosen tool, in schema order.
    enum_head_of: Dict[int, int] = {}
    head = 0
    for index, field in enumerate(fields):
        if field.get("enum"):
            enum_head_of[index] = head
            head += 1

    args: Dict[str, Any] = {}
    for index, field in enumerate(fields):
        if field.get("tool") != tool:
            continue
        name = field["name"]
        ftype = field.get("type")
        enum = field.get("enum") or ()

        if enum:
            head_index = enum_head_of.get(index)
            if head_index is None or head_index >= len(enum_outputs):
                continue
            logits = np.asarray(enum_outputs[head_index])
            class_index = int(np.argmax(logits))
            if 0 <= class_index < len(enum):
                args[name] = enum[class_index]
            continue

        if ftype == "bool":
            args[name] = bool(bool_p[0, index] >= 0.5)
            continue

        if float(presence_p[0, index]) < threshold:
            # Absent -- and absent means absent. See ``decode_batch`` in
            # ai/encoder_policy.py: materialising the schema default here
            # contradicts the presence head's own training target and cost
            # 49 pp of exact-match accuracy. ``validate_call`` re-applies the
            # default for the executor.
            continue

        span = best_span(np.asarray(span_start)[0, index], np.asarray(span_end)[0, index],
                         length, max_span=max_span)
        if span is None:
            continue
        args.setdefault("__span__", {})[name] = span

    return {"tool": tool, "args": args, "length": length}


class OnnxEncoder:
    """Loads the exported graph and reproduces the training-time preprocessing."""

    def __init__(
        self,
        onnx_path: Path,
        tokenizer_path: Optional[Path] = None,
        policy_path: Optional[Path] = None,
        threads: int = 4,
    ) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        onnx_path = Path(onnx_path)
        self.onnx_path = onnx_path
        self.tokenizer_path = Path(tokenizer_path) if tokenizer_path else onnx_path.with_name("tokenizer.json")
        self.policy_path = Path(policy_path) if policy_path else onnx_path.with_name("policy.json")

        self.policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        self.tools: List[str] = list(self.policy["tools"])
        self.fields: List[Dict[str, Any]] = list(self.policy["fields"])
        self.seq_len = int(self.policy.get("seq_len") or 256)
        self.threshold = float(self.policy.get("threshold") or 0.5)
        self.max_span = int(self.policy.get("max_span") or 32)
        self.system_prompt = str(self.policy.get("system_prompt") or "")

        self.tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
        self.pad_id = self._id("[PAD]")
        self.bos_id = self._id("[BOS]")
        self.eos_id = self._id("[EOS]")

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        from agent.model_cache import onnx_session

        started = time.perf_counter()
        self.session = onnx_session(str(onnx_path), sess_options=options)
        self.load_ms = (time.perf_counter() - started) * 1000.0

    def _id(self, token: str) -> int:
        value = self.tokenizer.token_to_id(token)
        if value is None:
            raise SystemExit(f"tokenizer is missing special token {token}")
        return int(value)

    # -- preprocessing -------------------------------------------------------

    def encode(self, goal: str, observation: Optional[Dict[str, Any]] = None) -> Tuple[np.ndarray, np.ndarray, List[int]]:
        from agent.tool_dataset import _observation_text

        user = f"Goal: {goal}"
        obs_text = _observation_text(observation)
        if obs_text:
            user += f"\nCurrent state: {obs_text}"
        text = self.system_prompt + "\n" + user
        ids = [self.bos_id] + list(self.tokenizer.encode(text, add_special_tokens=False).ids) + [self.eos_id]
        if len(ids) > self.seq_len:
            ids = ids[: self.seq_len - 1] + [self.eos_id]
        length = len(ids)
        input_ids = np.full((1, self.seq_len), self.pad_id, dtype=np.int64)
        attention = np.zeros((1, self.seq_len), dtype=np.int64)
        input_ids[0, :length] = np.asarray(ids, dtype=np.int64)
        attention[0, :length] = 1
        return input_ids, attention, ids

    # -- inference -----------------------------------------------------------

    def predict_one(self, case: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        input_ids, attention, ids = self.encode(
            str(case.get("goal") or ""), case.get("observation"))
        raw = self.session.run(None, {"input_ids": input_ids, "attention_mask": attention})
        decoded = decode_outputs(raw, self.fields, self.tools, len(ids),
                                 threshold=self.threshold, max_span=self.max_span)
        # Spans are token ranges; turn them back into text before coercion.
        args: Dict[str, Any] = dict(decoded["args"])
        spans = args.pop("__span__", {})
        for name, (start, end) in spans.items():
            field = next((f for f in self.fields
                          if f.get("tool") == decoded["tool"] and f["name"] == name), None)
            if field is None:
                continue
            text = self.tokenizer.decode(ids[start:end + 1], skip_special_tokens=True)
            parsed = parse_span(text, str(field.get("type")))
            if parsed is not None:
                args[name] = parsed
            # An unparseable span is an absence, not a default: the model said
            # the field is present but could not read a value out of it, and
            # inventing the schema default would claim the player stated one.
        return {"tool": decoded["tool"], "args": args}

    def predict_rows(self, rows: Sequence[Dict[str, Any]]) -> Dict[str, Optional[Dict[str, Any]]]:
        return {str(row.get("id")): self.predict_one(row) for row in rows}

    def stats(self) -> Dict[str, Any]:
        return {
            "onnx": str(self.onnx_path),
            "mb": round(self.onnx_path.stat().st_size / 1e6, 1),
            "seq_len": self.seq_len,
            "tools": len(self.tools),
            "fields": len(self.fields),
            "load_ms": round(self.load_ms, 1),
        }


def _selftest() -> int:
    """Validate the numpy decoder without onnxruntime installed."""
    tools = ["farm.start", "inv.sell", "safe.stop"]
    fields = [
        {"tool": "inv.sell", "name": "item", "type": "str", "enum": (), "default": None},
        {"tool": "inv.sell", "name": "all", "type": "bool", "enum": (), "default": False},
        {"tool": "inv.sell", "name": "price_rule", "type": "str",
         "enum": ["median_minus_1", "undercut", "fixed"], "default": "median_minus_1"},
        {"tool": "farm.start", "name": "monster", "type": "str", "enum": (), "default": None},
    ]
    length = 8
    tool_logits = np.array([[0.1, 2.5, 0.3]])
    presence = np.zeros((1, len(fields)), dtype=np.float32)
    presence[0, 0] = 3.0        # item present
    presence[0, 1] = 3.0        # all present
    presence[0, 3] = -3.0       # monster absent
    bool_logits = np.zeros((1, len(fields)), dtype=np.float32)
    bool_logits[0, 1] = -2.0    # all = False
    span_start = np.full((1, len(fields), length), -5.0, dtype=np.float32)
    span_end = np.full((1, len(fields), length), -5.0, dtype=np.float32)
    span_start[0, 0, 3] = 5.0
    span_end[0, 0, 4] = 5.0
    enum0 = np.array([[0.1, 0.2, 2.0]])   # -> fixed

    decoded = decode_outputs(
        [tool_logits, presence, bool_logits, span_start, span_end, enum0],
        fields, tools, length)
    ok = (decoded["tool"] == "inv.sell"
          and decoded["args"].get("price_rule") == "fixed"
          and decoded["args"].get("all") is False
          and decoded["args"]["__span__"]["item"] == (3, 4)
          # An absent field must be absent from the args, not materialised as
          # its schema default: the presence head's target for absence is 0,
          # and emitting a default contradicts it.
          and "monster" not in decoded["args"]
          and "monster" not in decoded["args"].get("__span__", {}))
    print("decoder selftest:", "PASS" if ok else "FAIL", json.dumps(
        {k: v for k, v in decoded.items() if k != "args"}, ensure_ascii=False))
    print("  args:", decoded["args"])
    return 0 if ok else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx", default=None)
    parser.add_argument("--tokenizer", default=None)
    parser.add_argument("--policy", default=None)
    parser.add_argument("--goal", default=None)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.onnx:
        parser.print_help()
        return 0

    encoder = OnnxEncoder(Path(args.onnx), args.tokenizer, args.policy)
    print(json.dumps(encoder.stats(), indent=2))
    if args.goal:
        started = time.perf_counter()
        call = encoder.predict_one({"goal": args.goal})
        elapsed = (time.perf_counter() - started) * 1000
        print(f"{args.goal!r} -> {json.dumps(call, ensure_ascii=False)}  ({elapsed:.1f} ms)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
