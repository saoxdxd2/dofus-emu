"""Fine-tune a per-instance adapter on the user's real goals, guarded by the base set.

The shared command heads stay the source; this script fits a small adapter to the
recorded prompts of one user (``data/command_adapters/user_goals.jsonl``) and
accepts it only if the 26 held-out base phrasings lose no intent and no exact
command. The base is backed up before promotion, so the update is rollback-able.

    python ai/finetune_command_adapter.py --parts action
    python ai/finetune_command_adapter.py --parts heads --promote

The 16 adapter rows are per-instance data, not a benchmark: the user prompts are
seen after this. The held-out 26 remain the generalization gate.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import torch
from torch.nn import functional as F

ROOT = Path(__file__).resolve().parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.command_shadow import adapter_accepts, canary_accepts  # noqa: E402
from agent.commands import intent_tool  # noqa: E402
from ai.train_command_encoder import (  # noqa: E402
    CommandData,
    CommandHeads,
    cache_hidden,
    evaluate,
    load_base,
)

DEFAULT_HEADS = ROOT / "models" / "command_encoder30" / "heads.pt"
DEFAULT_ROWS = ROOT / "data" / "command_adapters" / "user_goals.jsonl"
DEFAULT_OUT = ROOT / "models" / "command_encoder30" / "heads_adapter.pt"
DEFAULT_REPORT = ROOT / "models" / "command_encoder30" / "adapter_report.json"
CANARY = ["list my quests", "liste les quêtes", "fais les quêtes à Bonta",
          "quêtes de niveau 40", "advance quest 1935", "fais la quête 1935",
          "stop everything now", "vends tout", "zaap Astrub", "follow me"]


def read_rows(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def base_counts(*metrics: Dict[str, Any]) -> Dict[str, int]:
    counts = {"intent": 0, "exact": 0, "rows": 0}
    for metric in metrics:
        base = metric["base_rows"]
        rows = int(base["rows"])
        counts["rows"] += rows
        counts["intent"] += round(base["intent_accuracy"] * rows)
        counts["exact"] += round(base["command_exact"] * rows)
    return counts


def adapter_metrics(heads: CommandHeads, data: CommandData,
                    intents: List[str], actions: List[str]) -> Dict[str, Any]:
    heads.eval()
    hits = {"intent": 0, "action": 0, "tool": 0, "rows": 0}
    rows: List[Dict[str, Any]] = []
    offset = 0
    with torch.no_grad():
        for batch in data.hidden_batches(list(range(len(data.items))), 8):
            output = heads(batch["hidden"], batch["lengths"], batch["user_first"])
            predicted_intent = output["intent"].argmax(-1).tolist()
            predicted_action = output["action"].argmax(-1).tolist()
            chunk = data.items[offset:offset + len(predicted_intent)]
            for position, item in enumerate(chunk):
                intent = intents[predicted_intent[position]]
                action = actions[predicted_action[position]]
                tool = intent_tool(intent, action)
                want_tool = intent_tool(item["intent"],
                                        (item["row"].get("slots") or {}).get("action"))
                hits["rows"] += 1
                hits["intent"] += intent == item["intent"]
                hits["action"] += action == actions[item["action_id"]]
                hits["tool"] += tool == want_tool and tool is not None
                rows.append({"text": item["command"], "intent": intent,
                             "action": action, "tool": tool,
                             "gold_intent": item["intent"],
                             "gold_action": actions[item["action_id"]],
                             "gold_tool": want_tool})
            offset += len(predicted_intent)
    return {"hits": hits, "rows": rows}


def canary_predictions(heads: CommandHeads, data: CommandData,
                       intents: List[str], actions: List[str]) -> Dict[str, Any]:
    heads.eval()
    out: Dict[str, Any] = {}
    rows = 0
    with torch.no_grad():
        for batch in data.hidden_batches(list(range(len(data.items))), 8):
            output = heads(batch["hidden"], batch["lengths"], batch["user_first"])
            pi = output["intent"].argmax(-1).tolist()
            pa = output["action"].argmax(-1).tolist()
            for position, item in enumerate(data.items[rows:rows + len(pi)]):
                out[item["command"]] = (intents[pi[position]],
                                        actions[pa[position]])
            rows += len(pi)
    return out


def train(args: argparse.Namespace) -> Dict[str, Any]:
    torch.set_num_threads(8)
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    payload = torch.load(Path(args.heads), map_location="cpu", weights_only=False)
    trunk, tokenizer, _meta = load_base(Path(payload["base"]))
    system_prompt = payload["system_prompt"]
    intents = payload["intents"]
    actions = payload["actions"]
    tags = payload["tags"]
    heads = CommandHeads(trunk.pos_emb.weight.shape[1], len(intents), len(actions),
                         len(tags), adapter=payload["adapter"],
                         dropout=payload["dropout"])
    heads.load_state_dict(payload["state"])
    heads.eval()
    _set = lambda enabled: [p.requires_grad_(enabled) for p in heads.parameters()]
    _set(False)
    if args.parts == "intent_only":
        modules = [heads.intent_head]
    else:
        modules = [heads.action_head]
        if args.parts in ("intent", "heads"):
            modules.append(heads.intent_head)
        if args.parts == "heads":
            modules.extend((heads.token_adapter, heads.pool_norm))
    for module in modules:
        for parameter in module.parameters():
            parameter.requires_grad_(True)
    trainable = [p for p in heads.parameters() if p.requires_grad]

    adapter_data = CommandData(read_rows(Path(args.rows)), tokenizer, system_prompt,
                               trunk.pos_emb.weight.shape[0], intents, actions)
    rehearsal_data = None
    if args.rehearsal > 0:
        train_rows = read_rows(ROOT / "data" / "commands" / "train.jsonl")
        rng = random.Random(args.seed)
        rehearsal_rows = train_rows if args.rehearsal >= len(train_rows) else rng.sample(
            train_rows, args.rehearsal)
        rehearsal_data = CommandData(rehearsal_rows, tokenizer, system_prompt,
                                     trunk.pos_emb.weight.shape[0], intents, actions)
    val_data = CommandData(read_rows(ROOT / "data" / "commands" / "val.jsonl"),
                           tokenizer, system_prompt,
                           trunk.pos_emb.weight.shape[0], intents, actions)
    test_data = CommandData(read_rows(ROOT / "data" / "commands" / "test.jsonl"),
                            tokenizer, system_prompt,
                            trunk.pos_emb.weight.shape[0], intents, actions)
    canary_data = CommandData(
        [{"text": text, "intent": "unknown", "slots": {}} for text in CANARY],
        tokenizer, system_prompt, trunk.pos_emb.weight.shape[0], intents, actions)
    for name, data in (("adapter", adapter_data), ("val", val_data),
                       ("test", test_data), ("canary", canary_data),
                       ("rehearsal", rehearsal_data) if rehearsal_data else
                       ("rehearsal", adapter_data)):
        cache_hidden(trunk, data)
        print(f"cached {name}: {len(data.items)} rows")

    before_base = base_counts(evaluate(heads, val_data), evaluate(heads, test_data))
    before_adapter = adapter_metrics(heads, adapter_data, intents, actions)
    before_canary = canary_predictions(heads, canary_data, intents, actions)
    print("before base", before_base, "adapter", before_adapter["hits"],
          "canary", before_canary)

    optimizer = torch.optim.AdamW(trainable, lr=args.lr, weight_decay=args.weight_decay)
    indices = list(range(len(adapter_data.items)))
    # A candidate is kept only from an epoch that already passes the base guard.
    # Checking once at the end is how an overfit action head gets selected and then
    # rejected after it has already overwritten the only good checkpoint.
    best_state = None
    best_score = (-1, -1)
    best_epoch = 0
    rehearsal_indices = list(range(len(rehearsal_data.items)))         if rehearsal_data is not None else []
    for epoch in range(1, args.epochs + 1):
        heads.train()
        random.shuffle(indices)
        random.shuffle(rehearsal_indices)
        cursor = 0
        for start in range(0, len(indices), args.batch):
            batch = next(adapter_data.hidden_batches(indices[start:start + args.batch],
                                                    args.batch))
            output = heads(batch["hidden"], batch["lengths"], batch["user_first"])
            loss = F.cross_entropy(output["action"], batch["action"])
            if heads.intent_head.weight.requires_grad:
                loss = loss + args.intent_weight * F.cross_entropy(
                    output["intent"], batch["intent"])
            if rehearsal_indices:
                for _ in range(2):
                    if cursor >= len(rehearsal_indices):
                        cursor = 0
                        random.shuffle(rehearsal_indices)
                    rehearse = rehearsal_indices[cursor:cursor + args.batch]
                    cursor += args.batch
                    rbatch = next(rehearsal_data.hidden_batches(rehearse, args.batch))
                    routput = heads(rbatch["hidden"], rbatch["lengths"],
                                    rbatch["user_first"])
                    loss = loss + args.rehearsal_weight * F.cross_entropy(
                        routput["action"], rbatch["action"])
                    if heads.intent_head.weight.requires_grad:
                        loss = loss + args.rehearsal_weight * args.intent_weight *                             F.cross_entropy(routput["intent"], rbatch["intent"])
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(trainable, 1.0)
            optimizer.step()
        if epoch % args.eval_every and epoch != args.epochs:
            continue
        heads.eval()
        with torch.no_grad():
            adapter_now = adapter_metrics(heads, adapter_data, intents, actions)["hits"]
            if adapter_now["tool"] <= before_adapter["hits"]["tool"]:
                continue
            base_now = base_counts(evaluate(heads, val_data), evaluate(heads, test_data))
            if not adapter_accepts(before_base, base_now):
                continue
            canary_now = canary_predictions(heads, canary_data, intents, actions)
            if not canary_accepts(before_canary, canary_now):
                continue
            score = (adapter_now["tool"], base_now["exact"])
            if score > best_score:
                best_score = score
                best_epoch = epoch
                best_state = {key: value.detach().clone()
                              for key, value in heads.state_dict().items()}
    if best_state is not None:
        heads.load_state_dict(best_state)
    heads.eval()
    after_base = base_counts(evaluate(heads, val_data), evaluate(heads, test_data))
    after_adapter = adapter_metrics(heads, adapter_data, intents, actions)
    after_canary = canary_predictions(heads, canary_data, intents, actions)
    canary_ok = canary_accepts(before_canary, after_canary)
    accepted = (adapter_accepts(before_base, after_base)
                and after_adapter["hits"]["tool"] > before_adapter["hits"]["tool"]
                and canary_ok)
    report = {
        "heads": str(args.heads), "rows": str(args.rows), "parts": args.parts,
        "epochs": args.epochs, "lr": args.lr, "batch": args.batch,
        "best_epoch": best_epoch, "rehearsal": args.rehearsal,
        "rehearsal_weight": args.rehearsal_weight,
        "intent_weight": args.intent_weight,
        "before_base": before_base, "after_base": after_base,
        "before_adapter": before_adapter["hits"],
        "after_adapter": after_adapter["hits"],
        "canary_ok": canary_ok,
        "before_canary": {key: list(value) for key, value in before_canary.items()},
        "after_canary": {key: list(value) for key, value in after_canary.items()},
        "accepted": accepted, "promoted": False, "at": time.time(),
        "adapter_rows": after_adapter["rows"],
    }
    candidate = dict(payload)
    candidate["state"] = heads.state_dict()
    torch.save(candidate, args.out)
    if accepted and args.promote:
        base_backup = Path(args.heads).with_name("heads_base.pt")
        if not base_backup.exists():
            torch.save(payload, base_backup)
        torch.save(candidate, args.heads)
        report["promoted"] = True
        report["base_backup"] = str(base_backup)
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("before_base", "after_base", "before_adapter", "after_adapter",
                       "accepted", "promoted")}, indent=2))
    for row in after_adapter["rows"]:
        print("  %-36r %-8s %-8s -> %s" % (row["text"], row["intent"],
                                           row["action"], row["tool"]))
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--heads", type=Path, default=DEFAULT_HEADS)
    parser.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--parts", choices=("action", "intent_only", "intent", "heads"),
                        default="action",
                        help="action: action head; intent_only: intent head; "
                             "intent: both; heads: + token adapter and pool norm")
    parser.add_argument("--intent-weight", type=float, default=1.0)
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--lr", type=float, default=2e-3)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--rehearsal", type=int, default=0,
                        help="base train rows replayed with the adapter update")
    parser.add_argument("--rehearsal-weight", type=float, default=0.5)
    parser.add_argument("--eval-every", type=int, default=5)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--promote", action="store_true",
                        help="replace heads.pt only when the guard accepts")
    args = parser.parse_args(argv)
    train(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
