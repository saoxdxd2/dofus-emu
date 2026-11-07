"""Export the routed command model to one ONNX graph and quantize it.

The installed app ships the base encoder's ONNX but deliberately not the FP32
PyTorch checkpoint, so a torch-only command shadow can never run there. This
exports the shared trunk, the token adapter, the shared tag head and *both*
intent/action head sets in one graph; a wrapper picks base or adapter by pooled
similarity. The FP32 graph is quantized to INT8 for the installer.

    python ai/export_command_onnx.py \
        --base models/command_encoder30/heads_base.pt \
        --adapter models/command_encoder30/heads_adapter.pt \
        --out models/command_encoder30/command_encoder.onnx
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

import numpy as np
import torch
from torch import nn

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.train_command_encoder import (  # noqa: E402
    CommandData,
    CommandHeads,
    _encode_row,
    load_base,
)
from ai.tool_policy import BOS, EOS, PAD, _special_id  # noqa: E402

FIRST_TOKEN = 222  # constant across every command row; checked by --verify-first


class RoutedGraph(nn.Module):
    def __init__(self, base_state: Dict[str, Any], adapter_state: Dict[str, Any],
                 intents: int, actions: int, tags: int, dim: int,
                 adapter: int, dropout: float, first: int = FIRST_TOKEN) -> None:
        super().__init__()
        self.first = first
        base_heads = CommandHeads(dim, intents, actions, tags, adapter=adapter,
                                  dropout=dropout)
        base_heads.load_state_dict(base_state)
        adapter_heads = CommandHeads(dim, intents, actions, tags, adapter=adapter,
                                     dropout=dropout)
        adapter_heads.load_state_dict(adapter_state)
        self.tok_emb = base_heads.tok_emb if hasattr(base_heads, "tok_emb") else None
        # The trunk lives in the checkpoint loader, not in CommandHeads; the
        # exporter supplies it after construction.
        self.token_adapter = base_heads.token_adapter
        self.pool_norm = base_heads.pool_norm
        self.tag_head = base_heads.tag_head
        self.base_intent = base_heads.intent_head
        self.base_action = base_heads.action_head
        self.adapter_intent = adapter_heads.intent_head
        self.adapter_action = adapter_heads.action_head
        self.dropout = nn.Dropout(dropout)

    def forward(self, input_ids: torch.Tensor, attention: torch.Tensor,
                last_index: torch.Tensor) -> tuple:
        positions = torch.arange(input_ids.shape[1], device=input_ids.device)
        hidden = self.tok_emb(input_ids) + self.pos_emb(positions)
        hidden = self.encoder(hidden, src_key_padding_mask=attention.eq(0))
        hidden = self.norm(hidden)
        adapted = torch.cat([torch.zeros_like(hidden[:, :self.first]),
                             self.token_adapter(hidden[:, self.first:])], dim=1)
        pooled = adapted[torch.arange(adapted.shape[0], device=adapted.device),
                         last_index.clamp_min(0)]
        pooled = self.dropout(self.pool_norm(pooled))
        tag_logits = self.tag_head(self.dropout(adapted))
        base_intent = self.base_intent(pooled)
        adapter_intent = self.adapter_intent(pooled)
        base_action = self.base_action(
            torch.cat([pooled, torch.softmax(base_intent, dim=-1)], dim=-1))
        adapter_action = self.adapter_action(
            torch.cat([pooled, torch.softmax(adapter_intent, dim=-1)], dim=-1))
        return (base_intent, base_action, adapter_intent, adapter_action,
                tag_logits, pooled)


def _sample_batch(text: str, tokenizer: Any, system_prompt: str, ids: Dict[str, int],
                  width: int) -> tuple:
    item = _encode_row({"text": text, "intent": "auto", "slots": {}}, tokenizer,
                       system_prompt, width, ids)
    if item is None:
        raise SystemExit("sample command did not encode")
    input_ids = torch.zeros((1, width), dtype=torch.long)
    attention = torch.zeros((1, width), dtype=torch.long)
    length = len(item["input_ids"])
    input_ids[0, :length] = torch.tensor(item["input_ids"])
    attention[0, :length] = 1
    return input_ids, attention, torch.tensor([length - 2])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", type=Path,
                        default=ROOT / "models" / "command_encoder30" / "heads_base.pt")
    parser.add_argument("--adapter", type=Path,
                        default=ROOT / "models" / "command_encoder30" / "heads_adapter.pt")
    parser.add_argument("--out", type=Path,
                        default=ROOT / "models" / "command_encoder30" / "command_encoder.onnx")
    parser.add_argument("--int8-out", type=Path, default=None)
    parser.add_argument("--threshold", type=float, default=0.95)
    parser.add_argument("--first", type=int, default=FIRST_TOKEN)
    args = parser.parse_args(argv)

    base_payload = torch.load(args.base, map_location="cpu", weights_only=False)
    adapter_payload = torch.load(args.adapter, map_location="cpu", weights_only=False)
    trunk, tokenizer, meta = load_base(Path(base_payload["base"]))
    system_prompt = base_payload["system_prompt"]
    intents, actions = base_payload["intents"], base_payload["actions"]
    tags = base_payload["tags"]
    width = int(trunk.pos_emb.weight.shape[0])
    ids = {"bos": _special_id(tokenizer, BOS), "eos": _special_id(tokenizer, EOS),
           "pad": _special_id(tokenizer, PAD)}
    graph = RoutedGraph(base_payload["state"], adapter_payload["state"],
                        len(intents), len(actions), len(tags),
                        int(trunk.pos_emb.weight.shape[1]),
                        int(base_payload["adapter"]), float(base_payload["dropout"]),
                        first=args.first)
    graph.tok_emb = trunk.tok_emb
    graph.pos_emb = trunk.pos_emb
    graph.encoder = trunk.encoder
    graph.norm = trunk.norm
    graph.eval()

    sample_text = "do all the quests"
    input_ids, attention, last_index = _sample_batch(
        sample_text, tokenizer, system_prompt, ids, width)
    with torch.no_grad():
        torch_outputs = graph(input_ids, attention, last_index)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        graph, (input_ids, attention, last_index), str(args.out),
        input_names=["input_ids", "attention_mask", "last_index"],
        output_names=["base_intent", "base_action", "adapter_intent",
                      "adapter_action", "tags", "pooled"],
        opset_version=17, do_constant_folding=True, dynamo=False)
    import onnx

    onnx.checker.check_model(onnx.load(str(args.out)))
    print("exported", args.out, "%.1f MB" % (args.out.stat().st_size / 1e6))

    import onnxruntime as ort

    session = ort.InferenceSession(str(args.out), providers=["CPUExecutionProvider"])

    # Parity on every base/adapter row and the real goals.
    rows: List[Dict[str, Any]] = []
    for name in ("val", "test"):
        rows.extend(json.loads(line) for line in
                    (ROOT / "data" / "commands" / (name + ".jsonl")).read_text(
                        encoding="utf-8").splitlines() if line.strip())
    rows.extend(json.loads(line) for line in
                (ROOT / "data" / "command_adapters" / "user_goals.jsonl").read_text(
                    encoding="utf-8").splitlines() if line.strip())
    sample = rows[:1]
    mismatches = {"base_intent": 0, "base_action": 0, "adapter_intent": 0,
                  "adapter_action": 0, "tags": 0}
    for row in rows:
        item = _encode_row({"text": row["text"], "intent": "auto", "slots": {}},
                           tokenizer, system_prompt, width, ids)
        if item is None:
            continue
        input_ids = torch.zeros((1, width), dtype=torch.long)
        attention = torch.zeros((1, width), dtype=torch.long)
        length = len(item["input_ids"])
        input_ids[0, :length] = torch.tensor(item["input_ids"])
        attention[0, :length] = 1
        last = torch.tensor([length - 2])
        with torch.no_grad():
            reference = graph(input_ids, attention, last)
        got = session.run(None, {"input_ids": input_ids.numpy(),
                                 "attention_mask": attention.numpy(),
                                 "last_index": last.numpy()})
        names = ["base_intent", "base_action", "adapter_intent",
                 "adapter_action", "tags", "pooled"]
        for index, name in enumerate(names[:-1]):
            expected = reference[index].argmax(-1).numpy()
            actual = got[index].argmax(-1)
            if not np.array_equal(expected, actual):
                mismatches[name] += 1
        if not np.allclose(reference[-1].numpy(), got[-1], atol=1e-4):
            mismatches["pooled"] = mismatches.get("pooled", 0) + 1
    print("parity mismatches", mismatches, "of", len(rows), "rows")

    int8_out = args.int8_out or args.out.with_name(args.out.stem + "_int8.onnx")
    try:
        from onnxruntime.quantization import QuantType, quantize_dynamic

        clean = args.out.with_name(args.out.stem + "_clean.onnx")
        import onnx as _onnx

        _onnx.save(_onnx.load(str(args.out)), str(clean))
        quantize_dynamic(str(clean), str(int8_out), weight_type=QuantType.QInt8)
        clean.unlink(missing_ok=True)
        print("quantized", int8_out, "%.1f MB" % (int8_out.stat().st_size / 1e6))
    except Exception as exc:  # noqa: BLE001 - keep the FP32 graph if quantization fails
        print("quantization skipped:", exc)
        int8_out = None

    # Ship the tokenizer and a config the torch-free wrapper reads.
    tokenizer.save(str(args.out.with_name("tokenizer.json")))
    exemplars = [row["text"] for row in
                 (json.loads(line) for line in
                  (ROOT / "data" / "command_adapters" / "user_goals.jsonl").read_text(
                      encoding="utf-8").splitlines() if line.strip())]
    config = {
        "onnx": (int8_out.name if int8_out else args.out.name),
        "tokenizer": "tokenizer.json",
        "intents": intents, "actions": actions, "tags": tags,
        "system_prompt": system_prompt,
        "first_token": args.first, "max_seq_len": width,
        "bos_id": ids["bos"], "eos_id": ids["eos"], "pad_id": ids["pad"],
        "base_intent": "base_intent", "base_action": "base_action",
        "adapter_intent": "adapter_intent", "adapter_action": "adapter_action",
        "tags_output": "tags", "pooled_output": "pooled",
        "exemplars": exemplars, "threshold": args.threshold,
        "parity": mismatches,
    }
    (args.out.with_name("command_onnx.json")).write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", args.out.with_name("command_onnx.json"))
    return 0 if not any(mismatches.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
