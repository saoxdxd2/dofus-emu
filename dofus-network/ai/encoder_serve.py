"""Encoder policy adapter: makes the 30M encoder usable by the agent harness.

The gap this closes
-------------------
``ai/encoder_policy.py`` defines ``EncoderPolicy`` (an ``nn.Module``) and
``predict_rows`` (a batch helper). Neither implements ``agent.runtime.Policy``,
which is the only interface ``AgentRuntime`` and ``agent/serve.py`` know about:

    def choose(goal, observation, tools) -> Optional[ToolCall]

So the trained model could not be served at all. This adapter is the missing
piece, and it deliberately does three extra jobs that the batch path does not:

1. **schema validation** — every call goes through ``validate_call``; an invalid
   call becomes ``None`` so the caller can fall back instead of executing garbage;
2. **reply language** — ``nav.goto.lang`` defaults to ``"fr"`` in the schema, which
   made every Spanish and German player get French replies. The adapter fills it
   from the goal when the model did not choose it explicitly;
3. **fallback composition** — an optional secondary policy (the rule baseline)
   answers when the encoder refuses, so a partially trained model degrades
   gracefully instead of stalling the bot.

Both runtimes are supported: a torch checkpoint (dev) and an INT8 ONNX export
(deployment, no torch).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.runtime import ToolCall
from agent.schema import validate_call
from ai.lang_detect import reply_language

REPO_ROOT = Path(__file__).resolve().parent.parent


class EncoderToolPolicy:
    """``Policy`` implementation backed by the encoder, torch or ONNX."""

    def __init__(
        self,
        checkpoint: Optional[Path] = None,
        onnx: Optional[Path] = None,
        tokenizer: Optional[Path] = None,
        policy_json: Optional[Path] = None,
        device: str = "cpu",
        fallback: Optional[Any] = None,
        fill_reply_language: bool = True,
    ) -> None:
        if not checkpoint and not onnx:
            raise SystemExit("EncoderToolPolicy needs --checkpoint or --onnx")
        self.backend = "onnx" if onnx else "torch"
        self.device = device
        self.fallback = fallback
        self.fill_reply_language = fill_reply_language
        self.calls = 0
        self.refused = 0
        self.invalid = 0
        self.fallbacks = 0
        self.total_ms = 0.0

        if onnx:
            from ai.onnx_policy import OnnxEncoder

            self._onnx = OnnxEncoder(Path(onnx), tokenizer, policy_json)
            self._torch = None
        else:
            import torch

            from ai.encoder_policy import ENCODER_SYSTEM_PROMPT, load_checkpoint

            model, tok, meta = load_checkpoint(Path(checkpoint), device=torch.device(device))
            self._torch = (model, tok, meta.get("system_prompt") or ENCODER_SYSTEM_PROMPT)
            self._onnx = None

    # -- Policy protocol -----------------------------------------------------

    def choose(
        self,
        goal: str,
        observation: Optional[Dict[str, Any]] = None,
        tools: Optional[List[str]] = None,
    ) -> Optional[ToolCall]:
        goal = (goal or "").strip()
        if not goal:
            return None
        started = time.perf_counter()
        call = self._predict(goal, observation)
        self.total_ms += (time.perf_counter() - started) * 1000.0
        self.calls += 1

        if call is None:
            self.refused += 1
            return self._fallback(goal, observation, tools)

        tool = str(call.get("tool") or "")
        args = dict(call.get("args") or {})
        if tools and tool not in tools:
            self.invalid += 1
            return self._fallback(goal, observation, tools)

        if self.fill_reply_language and "lang" in args:
            # The schema default is "fr"; answer in the player's language instead.
            args["lang"] = reply_language(goal, default=str(args.get("lang") or "fr"))

        ok, result = validate_call(tool, args)
        if not ok:
            self.invalid += 1
            return self._fallback(goal, observation, tools)
        return ToolCall(tool, result, goal)

    # -- inference -----------------------------------------------------------

    def _predict(self, goal: str, observation: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        case = {"goal": goal, "observation": observation, "id": "live"}
        if self._onnx is not None:
            return self._onnx.predict_one(case)
        model, tokenizer, system_prompt = self._torch  # type: ignore[misc]
        from ai.encoder_policy import EncoderDataset, decode_batch

        # Live inference: the tool is predicted by the model, so the row must not
        # be rejected for omitting ``"tool"`` (see EncoderDataset.infer).
        dataset = EncoderDataset(
            [case], tokenizer, model.config.max_seq_len, system_prompt, infer=True
        )
        if not dataset.items:
            return None
        batch = next(dataset.batches(1, shuffle=False))
        return decode_batch(model, tokenizer, batch)[0]

    def _fallback(self, goal: str, observation: Optional[Dict[str, Any]],
                  tools: Optional[List[str]]) -> Optional[ToolCall]:
        if self.fallback is None:
            return None
        self.fallbacks += 1
        return self.fallback.choose(goal, observation, tools)

    # -- introspection -------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "calls": self.calls,
            "refused": self.refused,
            "invalid": self.invalid,
            "fallbacks": self.fallbacks,
            "mean_ms": round(self.total_ms / self.calls, 2) if self.calls else None,
        }
