"""Tool-policy student: byte-level BPE tokenizer + causal LM over tool calls.

This is the training skeleton for the action head of the 100M brain. It learns
``(system tool contract, goal, optional state) -> {"tool", "args"}`` from the
SFT corpus produced by ``agent/tool_dataset.py`` and is scored on the held-out
``agent/tool_eval.py`` benchmark.

The same code scales from a CPU smoke model to the Kaggle student: raise
``--dim/--layers/--vocab-size`` and the tokenizer/checkpoint stay compatible.
The tokenizer is byte-level BPE so JSON, accents and all four languages are
representable — unlike the legacy word-level ``DofusDomainTokenizer``.

Usage (from ``dofus-network``)::

    python ai/tool_policy.py train --smoke
    python ai/tool_policy.py eval --checkpoint models/tool_policy/model.pt
    python ai/tool_policy.py generate --checkpoint models/tool_policy/model.pt \
        --goal "farm 100 kanigs in Astrub"

Kaggle/final presets::

    python ai/tool_policy.py train --vocab-size 32768 --dim 768 --layers 12 \
        --heads 12 --ffn 3072 --batch-size 32 --epochs 3
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch
from torch import nn

from agent import tool_dataset, tool_eval

PAD, BOS, EOS, UNK, ASSISTANT = "[PAD]", "[BOS]", "[EOS]", "[UNK]", "[ASSISTANT]"
SPECIAL_TOKENS = [PAD, BOS, EOS, UNK, ASSISTANT]

DEFAULT_SFT_DIR = REPO_ROOT / "data" / "agent_sft"
DEFAULT_OUT_DIR = REPO_ROOT / "models" / "tool_policy"


# ---------------------------------------------------------------------------
# tokenizer
# ---------------------------------------------------------------------------


def train_tokenizer(
    rows: Sequence[Dict[str, Any]],
    out_path: Path,
    vocab_size: int = 4096,
    system_prompt: Optional[str] = None,
) -> Any:
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers

    texts: List[str] = []
    if system_prompt:
        texts.append(system_prompt)
    for row in rows:
        for message in row.get("messages") or []:
            content = message.get("content")
            if content:
                texts.append(str(content))
    for case in tool_eval.GOLD:
        texts.append(f"Goal: {case['goal']}")
    if not texts:
        raise SystemExit("no training rows found — run agent/tool_dataset.py first")

    tokenizer = Tokenizer(models.BPE(unk_token=UNK))
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(
        vocab_size=int(vocab_size),
        special_tokens=SPECIAL_TOKENS,
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
        show_progress=False,
    )
    tokenizer.train_from_iterator(texts, trainer=trainer)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tokenizer.save(str(out_path))
    return tokenizer


def load_tokenizer(path: Path) -> Any:
    from tokenizers import Tokenizer

    return Tokenizer.from_file(str(path))


def _encode(tokenizer: Any, text: str) -> List[int]:
    return tokenizer.encode(text, add_special_tokens=False).ids


def _special_id(tokenizer: Any, token: str) -> int:
    token_id = tokenizer.token_to_id(token)
    if token_id is None:
        raise SystemExit(f"tokenizer is missing special token {token}")
    return int(token_id)


def build_prompt_ids(
    tokenizer: Any,
    goal: str,
    observation: Optional[Dict[str, Any]] = None,
    system_prompt: Optional[str] = None,
) -> List[int]:
    user = f"Goal: {goal}"
    observation_text = tool_dataset._observation_text(observation)
    if observation_text:
        user += f"\nCurrent state: {observation_text}"
    return (
        [_special_id(tokenizer, BOS)]
        + _encode(tokenizer, system_prompt or tool_dataset.SYSTEM_PROMPT)
        + _encode(tokenizer, user)
        + [_special_id(tokenizer, ASSISTANT)]
    )


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------


@dataclass
class PolicyConfig:
    vocab_size: int = 4096
    max_seq_len: int = 1024
    d_model: int = 192
    n_layers: int = 3
    n_heads: int = 6
    ffn: int = 512
    dropout: float = 0.0


class ToolPolicyLM(nn.Module):
    def __init__(self, config: PolicyConfig) -> None:
        super().__init__()
        self.config = config
        self.tok_emb = nn.Embedding(config.vocab_size, config.d_model)
        self.pos_emb = nn.Embedding(config.max_seq_len, config.d_model)
        layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.n_heads,
            dim_feedforward=config.ffn,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(
            layer, num_layers=config.n_layers, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(config.d_model)
        self.head = nn.Linear(config.d_model, config.vocab_size, bias=False)
        self.head.weight = self.tok_emb.weight
        self.apply(self._init)

    @staticmethod
    def _init(module: nn.Module) -> None:
        if isinstance(module, nn.Linear):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        _batch, length = input_ids.shape
        if length > self.config.max_seq_len:
            raise ValueError(
                f"sequence of {length} exceeds max_seq_len {self.config.max_seq_len}")
        positions = torch.arange(length, device=input_ids.device)
        hidden = self.tok_emb(input_ids) + self.pos_emb(positions)
        causal = nn.Transformer.generate_square_subsequent_mask(
            length, device=input_ids.device, dtype=hidden.dtype)
        padding = None
        if attention_mask is not None:
            padding = attention_mask.eq(0)
        hidden = self.encoder(
            hidden, mask=causal, src_key_padding_mask=padding)
        return self.head(self.norm(hidden))


# ---------------------------------------------------------------------------
# dataset
# ---------------------------------------------------------------------------


class ToolSFTDataset:
    def __init__(
        self,
        rows: Sequence[Dict[str, Any]],
        tokenizer: Any,
        max_seq_len: int,
        system_ids: Optional[List[int]] = None,
        system_prompt: Optional[str] = None,
    ) -> None:
        self.tokenizer = tokenizer
        self.max_seq_len = max_seq_len
        self.pad_id = _special_id(tokenizer, PAD)
        self.eos_id = _special_id(tokenizer, EOS)
        self.assistant_id = _special_id(tokenizer, ASSISTANT)
        self.bos_id = _special_id(tokenizer, BOS)
        self.system_ids = system_ids or _encode(
            tokenizer, system_prompt or tool_dataset.SYSTEM_PROMPT)
        self.items: List[Tuple[List[int], List[int]]] = []
        self.skipped = 0
        self.lengths: List[int] = []
        for row in rows:
            item = self._encode_row(row)
            if item is None:
                self.skipped += 1
                continue
            self.items.append(item)
            self.lengths.append(len(item[0]))

    def _encode_row(self, row: Dict[str, Any]) -> Optional[Tuple[List[int], List[int]]]:
        messages = row.get("messages") or []
        user = next((m.get("content") for m in messages if m.get("role") == "user"), None)
        assistant = next(
            (m.get("content") for m in messages if m.get("role") == "assistant"), None)
        if not user or not assistant:
            return None
        prompt = (
            [self.bos_id] + self.system_ids + _encode(self.tokenizer, str(user))
            + [self.assistant_id])
        completion = _encode(self.tokenizer, str(assistant)) + [self.eos_id]
        ids = prompt + completion
        if len(ids) > self.max_seq_len:
            return None
        labels = [-100] * len(prompt) + completion
        return ids, labels

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> Tuple[List[int], List[int]]:
        return self.items[index]

    def stats(self) -> Dict[str, Any]:
        if not self.lengths:
            return {"examples": 0, "skipped": self.skipped}
        ordered = sorted(self.lengths)
        return {
            "examples": len(self.lengths),
            "skipped_too_long": self.skipped,
            "tokens": sum(self.lengths),
            "min": ordered[0],
            "p50": ordered[len(ordered) // 2],
            "p95": ordered[int(len(ordered) * 0.95)],
            "max": ordered[-1],
        }

    def batches(
        self,
        batch_size: int,
        shuffle: bool = True,
        rng: Optional[random.Random] = None,
    ) -> Iterable[Tuple[torch.Tensor, torch.Tensor, torch.Tensor]]:
        order = list(range(len(self.items)))
        if shuffle:
            (rng or random).shuffle(order)
        for start in range(0, len(order), batch_size):
            chunk = order[start:start + batch_size]
            width = max(len(self.items[i][0]) for i in chunk)
            input_ids = torch.full((len(chunk), width), self.pad_id, dtype=torch.long)
            labels = torch.full((len(chunk), width), -100, dtype=torch.long)
            attention = torch.zeros((len(chunk), width), dtype=torch.long)
            for row, index in enumerate(chunk):
                ids, row_labels = self.items[index]
                input_ids[row, :len(ids)] = torch.tensor(ids, dtype=torch.long)
                labels[row, :len(row_labels)] = torch.tensor(row_labels, dtype=torch.long)
                attention[row, :len(ids)] = 1
            yield input_ids, labels, attention


# ---------------------------------------------------------------------------
# training / evaluation
# ---------------------------------------------------------------------------


def _read_rows(path: Path) -> List[Dict[str, Any]]:
    rows = tool_dataset.read_jsonl(path)
    if not rows:
        raise SystemExit(f"no rows in {path} — run agent/tool_dataset.py first")
    return rows


def _loss(logits: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
    return nn.functional.cross_entropy(
        logits[:, :-1, :].reshape(-1, logits.shape[-1]),
        labels[:, 1:].reshape(-1),
        ignore_index=-100,
    )


def _resolve_device(value: str) -> torch.device:
    if value == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(value)


def _evaluate_loss(
    model: ToolPolicyLM,
    dataset: ToolSFTDataset,
    batch_size: int,
    device: torch.device,
) -> float:
    model.eval()
    total = 0.0
    batches = 0
    with torch.no_grad():
        for input_ids, labels, attention in dataset.batches(
                batch_size, shuffle=False):
            total += float(_loss(
                model(input_ids.to(device), attention.to(device)),
                labels.to(device)).item())
            batches += 1
    model.train()
    return total / max(batches, 1)


def train(args: argparse.Namespace) -> Dict[str, Any]:
    seed = args.seed
    random.seed(seed)
    torch.manual_seed(seed)
    if args.threads:
        torch.set_num_threads(args.threads)

    train_rows = _read_rows(Path(args.sft_dir) / "train.jsonl")
    val_rows = _read_rows(Path(args.sft_dir) / "val.jsonl")
    system_prompt = tool_dataset.SYSTEM_PROMPTS[args.system]
    tokenizer_path = Path(args.out) / "tokenizer.json"
    if args.tokenizer:
        tokenizer = load_tokenizer(Path(args.tokenizer))
    elif tokenizer_path.exists():
        tokenizer = load_tokenizer(tokenizer_path)
    else:
        print(f"training byte-level BPE ({args.vocab_size} tokens) ...")
        tokenizer = train_tokenizer(
            train_rows + val_rows, tokenizer_path, vocab_size=args.vocab_size,
            system_prompt=system_prompt)

    train_data = ToolSFTDataset(
        train_rows, tokenizer, args.max_seq_len, system_prompt=system_prompt)
    val_data = ToolSFTDataset(
        val_rows, tokenizer, args.max_seq_len, system_ids=train_data.system_ids)
    if not train_data.items:
        raise SystemExit("every training sequence was too long; raise --max-seq-len")
    print(f"train     : {json.dumps(train_data.stats())}")
    print(f"val       : {json.dumps(val_data.stats())}")
    print(f"vocab     : {tokenizer.get_vocab_size()}")

    config = PolicyConfig(
        vocab_size=tokenizer.get_vocab_size(),
        max_seq_len=args.max_seq_len,
        d_model=args.dim,
        n_layers=args.layers,
        n_heads=args.heads,
        ffn=args.ffn,
        dropout=args.dropout,
    )
    model = ToolPolicyLM(config)
    params = sum(p.numel() for p in model.parameters())
    print(f"model     : {params / 1e6:.2f}M parameters  "
          f"({params} raw), d={config.d_model} L={config.n_layers}")
    device = _resolve_device(args.device)
    model.to(device)
    print(f"device    : {device}")

    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    total_steps = args.max_steps
    if total_steps <= 0:
        total_steps = math.ceil(len(train_data) / args.batch_size) * args.epochs
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=max(total_steps, 1))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    best_path = out_dir / "model.pt"
    best_loss = float("inf")
    history: List[Dict[str, Any]] = []
    step = 0
    started = time.time()
    model.train()

    while step < total_steps:
        epoch = step * args.batch_size // max(len(train_data), 1)
        for input_ids, labels, attention in train_data.batches(
                args.batch_size, shuffle=True, rng=random.Random(seed + step)):
            if step >= total_steps:
                break
            logits = model(input_ids.to(device), attention.to(device))
            loss = _loss(logits, labels.to(device))
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            step += 1
            if step % args.log_every == 0 or step == 1:
                elapsed = max(time.time() - started, 1e-6)
                tokens = step * args.batch_size * train_data.stats()["p50"]
                print(f"  step {step:5d}/{total_steps}  loss {loss.item():.4f}  "
                      f"lr {scheduler.get_last_lr()[0]:.2e}  "
                      f"~{tokens / elapsed:,.0f} tok/s")
            if args.eval_every and step % args.eval_every == 0:
                val_loss = _evaluate_loss(model, val_data, args.batch_size, device)
                history.append({"step": step, "epoch": epoch,
                                "train_loss": float(loss.item()), "val_loss": val_loss})
                print(f"  val loss {val_loss:.4f}")
                if val_loss < best_loss:
                    best_loss = val_loss
                    _save(best_path, model, tokenizer, config, {
                        "step": step, "val_loss": val_loss, "train_rows": len(train_rows),
                        "val_rows": len(val_rows), "params": params,
                        "system_prompt": system_prompt,
                    })

    final_val = _evaluate_loss(model, val_data, args.batch_size, device)
    if final_val < best_loss:
        best_loss = final_val
        _save(best_path, model, tokenizer, config, {
            "step": step, "val_loss": final_val, "train_rows": len(train_rows),
            "val_rows": len(val_rows), "params": params,
            "system_prompt": system_prompt,
        })
    _save(out_dir / "model_final.pt", model, tokenizer, config, {
        "step": step, "val_loss": final_val, "train_rows": len(train_rows),
        "val_rows": len(val_rows), "params": params,
        "system_prompt": system_prompt,
    })
    tokenizer.save(str(out_dir / "tokenizer.json"))

    metrics = {
        "steps": step,
        "params": params,
        "best_val_loss": best_loss,
        "final_val_loss": final_val,
        "train_stats": train_data.stats(),
        "val_stats": val_data.stats(),
        "system": args.system,
        "system_prompt_chars": len(system_prompt),
        "config": asdict(config),
        "history": history,
        "elapsed_seconds": round(time.time() - started, 1),
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved     : {best_path}  (best val {best_loss:.4f})")
    print(f"metrics   : {out_dir / 'metrics.json'}")
    return metrics


def _save(
    path: Path,
    model: ToolPolicyLM,
    tokenizer: Any,
    config: PolicyConfig,
    meta: Dict[str, Any],
) -> None:
    torch.save({
        "config": asdict(config),
        "state_dict": model.state_dict(),
        "tokenizer_json": tokenizer.to_str(),
        "meta": meta,
    }, path)


def load_checkpoint(
    path: Path,
    device: Optional[torch.device] = None,
) -> Tuple[ToolPolicyLM, Any, Dict[str, Any]]:
    blob = torch.load(path, map_location="cpu", weights_only=False)
    config = PolicyConfig(**blob["config"])
    model = ToolPolicyLM(config)
    model.load_state_dict(blob["state_dict"])
    if device is not None:
        model.to(device)
    model.eval()
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_str(blob["tokenizer_json"])
    return model, tokenizer, blob.get("meta") or {}


# ---------------------------------------------------------------------------
# inference / benchmark
# ---------------------------------------------------------------------------


@torch.no_grad()
def generate_ids(
    model: ToolPolicyLM,
    tokenizer: Any,
    prompt_ids: List[int],
    max_new_tokens: int = 128,
) -> List[int]:
    eos = _special_id(tokenizer, EOS)
    device = next(model.parameters()).device
    max_prompt = model.config.max_seq_len - max_new_tokens
    if len(prompt_ids) > max_prompt:
        prompt_ids = [prompt_ids[0]] + prompt_ids[-max_prompt + 1:]
    ids = torch.tensor([prompt_ids], dtype=torch.long, device=device)
    generated: List[int] = []
    for _ in range(max_new_tokens):
        logits = model(ids)
        next_id = int(torch.argmax(logits[0, -1]).item())
        if next_id == eos:
            break
        generated.append(next_id)
        ids = torch.cat(
            [ids, torch.tensor([[next_id]], dtype=torch.long, device=device)], dim=1)
        if ids.shape[1] >= model.config.max_seq_len:
            break
    return generated


def parse_call(text: str) -> Optional[Dict[str, Any]]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        tool = payload.get("tool")
        args = payload.get("args") or {}
        if isinstance(tool, str) and isinstance(args, dict):
            return {"tool": tool, "args": args}
    return None


def predict(
    model: ToolPolicyLM,
    tokenizer: Any,
    goal: str,
    observation: Optional[Dict[str, Any]] = None,
    max_new_tokens: int = 128,
    system_prompt: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    prompt = build_prompt_ids(
        tokenizer, goal, observation, system_prompt=system_prompt)
    ids = generate_ids(model, tokenizer, prompt, max_new_tokens=max_new_tokens)
    text = tokenizer.decode(ids, skip_special_tokens=True)
    return parse_call(text), text


def evaluate(args: argparse.Namespace) -> Dict[str, Any]:
    model, tokenizer, meta = load_checkpoint(
        Path(args.checkpoint), device=_resolve_device(args.device))
    cases = tool_eval.GOLD[:args.limit] if args.limit else tool_eval.GOLD
    predictions: Dict[str, Optional[Dict[str, Any]]] = {}
    raw: Dict[str, str] = {}
    started = time.time()
    for index, case in enumerate(cases, 1):
        call, text = predict(
            model, tokenizer, case["goal"], max_new_tokens=args.max_new_tokens,
            system_prompt=meta.get("system_prompt"))
        predictions[case["id"]] = call
        raw[case["id"]] = text
        if args.verbose or index % 10 == 0:
            print(f"  {index}/{len(cases)} {case['id']} -> "
                  f"{(call or {}).get('tool', 'UNPARSED')}")
    report = tool_eval.evaluate(predictions, cases=cases)
    report["elapsed_seconds"] = round(time.time() - started, 1)
    report["checkpoint"] = str(args.checkpoint)
    report["checkpoint_meta"] = meta
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    totals = report["totals"]
    print(f"\ntool accuracy: {totals['tool_ok']}/{totals['cases']} "
          f"({100 * totals['tool_ok'] / max(totals['cases'], 1):.1f}%)")
    print(f"call accuracy: {totals['call_ok']}/{totals['cases']} "
          f"({100 * totals['call_ok'] / max(totals['cases'], 1):.1f}%)")
    if args.show_failures:
        for failure in report["failures"]:
            print(f"  FAIL {failure['id']} [{failure['lang']}] {failure['goal']!r}")
            print(f"       expected {failure['expected']}")
            print(f"       got      {failure['prediction']}  raw={raw.get(failure['id'], '')!r}")
    return report


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------


def _smoke_defaults(args: argparse.Namespace) -> None:
    args.vocab_size = 4096
    args.dim = 192
    args.layers = 3
    args.heads = 6
    args.ffn = 512
    args.batch_size = 8
    args.epochs = 3
    args.max_steps = 90
    args.log_every = 10
    args.eval_every = 45
    args.lr = 5e-4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    train_parser = sub.add_parser("train", help="train the tool-policy LM")
    train_parser.add_argument("--sft-dir", default=str(DEFAULT_SFT_DIR))
    train_parser.add_argument("--out", default=str(DEFAULT_OUT_DIR))
    train_parser.add_argument("--tokenizer", default=None,
                              help="reuse an existing tokenizer.json")
    train_parser.add_argument("--system", choices=("compact", "full"), default="compact",
                              help="system contract used for training and stored in the checkpoint")
    train_parser.add_argument("--vocab-size", type=int, default=4096)
    train_parser.add_argument("--max-seq-len", type=int, default=1024)
    train_parser.add_argument("--dim", type=int, default=192)
    train_parser.add_argument("--layers", type=int, default=3)
    train_parser.add_argument("--heads", type=int, default=6)
    train_parser.add_argument("--ffn", type=int, default=512)
    train_parser.add_argument("--dropout", type=float, default=0.0)
    train_parser.add_argument("--batch-size", type=int, default=8)
    train_parser.add_argument("--epochs", type=int, default=3)
    train_parser.add_argument("--max-steps", type=int, default=0,
                              help="stop after N optimizer steps (0 = epochs)")
    train_parser.add_argument("--eval-every", type=int, default=0,
                              help="validate every N steps when max-steps is set")
    train_parser.add_argument("--lr", type=float, default=3e-4)
    train_parser.add_argument("--weight-decay", type=float, default=0.1)
    train_parser.add_argument("--seed", type=int, default=7)
    train_parser.add_argument("--threads", type=int, default=0)
    train_parser.add_argument("--device", default="auto")
    train_parser.add_argument("--log-every", type=int, default=20)
    train_parser.add_argument("--smoke", action="store_true",
                              help="tiny CPU preset (90 steps)")

    eval_parser = sub.add_parser("eval", help="score a checkpoint on tool_eval")
    eval_parser.add_argument("--checkpoint", default=str(DEFAULT_OUT_DIR / "model.pt"))
    eval_parser.add_argument("--limit", type=int, default=0)
    eval_parser.add_argument("--device", default="auto")
    eval_parser.add_argument("--max-new-tokens", type=int, default=128)
    eval_parser.add_argument("--out", default=None)
    eval_parser.add_argument("--show-failures", action="store_true")
    eval_parser.add_argument("--verbose", action="store_true")

    gen_parser = sub.add_parser("generate", help="run one goal through a checkpoint")
    gen_parser.add_argument("--checkpoint", default=str(DEFAULT_OUT_DIR / "model.pt"))
    gen_parser.add_argument("--goal", required=True)
    gen_parser.add_argument("--device", default="auto")
    gen_parser.add_argument("--state", default=None,
                            help="optional JSON observation")
    gen_parser.add_argument("--max-new-tokens", type=int, default=128)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "train":
        if args.smoke:
            _smoke_defaults(args)
        train(args)
    elif args.command == "eval":
        evaluate(args)
    elif args.command == "generate":
        model, tokenizer, _meta = load_checkpoint(
            Path(args.checkpoint), device=_resolve_device(args.device))
        state = json.loads(args.state) if args.state else None
        call, text = predict(
            model, tokenizer, args.goal, state,
            max_new_tokens=args.max_new_tokens,
            system_prompt=_meta.get("system_prompt"))
        print(text)
        print(json.dumps(call, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
