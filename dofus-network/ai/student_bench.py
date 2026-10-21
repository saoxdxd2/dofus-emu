"""Score a tool-policy student checkpoint on the fresh v2 benchmark."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.tool_policy import _resolve_device, load_checkpoint, predict  # noqa: E402
from agent.tool_eval import evaluate  # noqa: E402


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-new-tokens", type=int, default=160)
    parser.add_argument("--out", default=None)
    parser.add_argument("--show-failures", type=int, default=5)
    args = parser.parse_args(argv)

    cases: List[Dict[str, Any]] = [
        json.loads(line) for line in Path(args.cases).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.limit:
        cases = cases[:args.limit]
    model, tokenizer, meta = load_checkpoint(
        Path(args.checkpoint), device=_resolve_device(args.device))
    predictions: Dict[str, Optional[Dict[str, Any]]] = {}
    raw: Dict[str, str] = {}
    started = time.time()
    for index, case in enumerate(cases, 1):
        call, text = predict(
            model, tokenizer, case["goal"],
            max_new_tokens=args.max_new_tokens,
            system_prompt=meta.get("system_prompt"))
        predictions[case["id"]] = call
        raw[case["id"]] = text
        if index % 50 == 0:
            print(f"  {index}/{len(cases)}", flush=True)

    report = evaluate(predictions, cases)
    report["elapsed_seconds"] = round(time.time() - started, 1)
    report["checkpoint"] = str(args.checkpoint)
    report["predictions"] = [
        {"id": case_id, "predicted": call} for case_id, call in predictions.items()]
    report["checkpoint_meta"] = {
        key: meta.get(key) for key in ("params", "step", "val_loss", "system_prompt")}
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    totals = report["totals"]
    print(json.dumps(totals, ensure_ascii=False))
    failures = report.get("failures") or []
    for failure in failures[:args.show_failures]:
        print(json.dumps(failure, ensure_ascii=False))
    print("report:", args.out or "(not saved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
