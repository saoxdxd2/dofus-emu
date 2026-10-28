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

and one more that the batch path has but the serving path was missing:

4. **deterministic post-processing** — ``clean_call`` runs between the model and
   ``validate_call``, with the gazetteer and the KB attached. Without it the
   measured gains (truncated-span repair, grounded fills, quest-id resolution)
   existed only in the evaluation harness and were never realised in production.
   The gazetteer is corpus-derived and the KB is game data; neither is a model,
   so this does not move the parameter count or the RAM budget by much (see
   ``_resolver_mb``).

Both runtimes are supported: a torch checkpoint (dev) and an INT8 ONNX export
(deployment, no torch).
"""

from __future__ import annotations

import json
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
        postprocess: bool = True,
        gazetteer: Optional[Any] = None,
        kb: Optional[Any] = None,
        load_resolvers: bool = True,
    ) -> None:
        if not checkpoint and not onnx:
            raise SystemExit("EncoderToolPolicy needs --checkpoint or --onnx")
        self.backend = "onnx" if onnx else "torch"
        self.device = device
        self.fallback = fallback
        self.fill_reply_language = fill_reply_language
        # Post-processing is on by default: it is the difference between a model
        # that emits "Eklat de Kroktail la Desalt" and one whose call the
        # executor can actually run. ``postprocess=False`` is the ablation switch.
        self.postprocess = postprocess
        self.calls = 0
        self.refused = 0
        self.invalid = 0
        self.fallbacks = 0
        self.total_ms = 0.0
        self.postprocess_ms = 0.0
        self.repaired = 0

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

        # The resolvers are looked up lazily and defensively: a deployment that
        # ships only the ONNX graph still has to serve, it just serves without
        # post-processing. Gazetteer -> KB is the resolution order (corpus
        # evidence before game data), matching ``clean_call`` step 6.
        self.gazetteer = gazetteer
        self.kb = kb
        if self.postprocess and load_resolvers:
            if self.gazetteer is None:
                self.gazetteer = self._load_gazetteer()
            if self.kb is None:
                self.kb = self._load_kb()

    # -- resolvers -----------------------------------------------------------

    @staticmethod
    def _load_gazetteer() -> Optional[Any]:
        try:
            from ai.gazetteer import DEFAULT_GAZETTEER, Gazetteer

            if not DEFAULT_GAZETTEER.exists():
                return None
            return Gazetteer()
        except Exception:  # noqa: BLE001 - a missing resolver must not kill serving
            return None

    @staticmethod
    def _load_kb() -> Optional[Any]:
        try:
            from planning.kb import DEFAULT_KB, DEFAULT_NAMES, KnowledgeBase

            if not (DEFAULT_KB.exists() and DEFAULT_NAMES.exists()):
                return None
            return KnowledgeBase()
        except Exception:  # noqa: BLE001 - numpy-less deployments fall back
            return None

    def _resolver_mb(self) -> float:
        total = 0.0
        if self.gazetteer is not None:
            payload = getattr(self.gazetteer, "payload", None) or {}
            try:
                total += len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) / 1e6
            except (TypeError, ValueError):
                pass
        if self.kb is not None:
            stats = self.kb.stats()
            total += float(stats.get("arrays_mb", 0.0)) + float(stats.get("names_mb", 0.0))
        return round(total, 2)

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

        if self.postprocess:
            started_pp = time.perf_counter()
            from ai.postprocess import clean_call

            args = clean_call(
                tool,
                args,
                goal,
                gazetteer=self.gazetteer,
                kb=self.kb,
                lang=reply_language(goal, default="fr"),
            )
            self.postprocess_ms += (time.perf_counter() - started_pp) * 1000.0
            if args != dict(call.get("args") or {}):
                self.repaired += 1

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
            "postprocess": {
                "enabled": self.postprocess,
                "repaired": self.repaired,
                "mean_ms": round(self.postprocess_ms / self.calls, 3) if self.calls else None,
                "gazetteer": self.gazetteer is not None,
                "kb": self.kb is not None,
                "resolver_mb": self._resolver_mb(),
            },
        }
