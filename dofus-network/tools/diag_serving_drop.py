"""Where does the serving path lose the model's call?

`EncoderToolPolicy.choose()` drops from 92.9% tool (raw prediction) to 77.7%
when it goes through postprocess + validate_call. This separates the two
possible causes instead of guessing:

    model call -> clean_call -> validate_call

and counts, per tool, the calls that were valid before postprocess and invalid
after (postprocess made it worse) versus invalid on both sides (the model's own
fault). Only the first class is a bug in clean_call.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from agent.schema import validate_call  # noqa: E402
from ai.encoder_serve import EncoderToolPolicy  # noqa: E402
from ai.lang_detect import reply_language  # noqa: E402
from ai.postprocess import clean_call  # noqa: E402

MODEL = ROOT / "models" / "encoder30_v11"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 800


def rows(split: str):
    out = []
    with (ROOT / "data" / "core_sft_v4" / f"{split}.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out[:LIMIT]


def main() -> int:
    policy = EncoderToolPolicy(onnx=MODEL / "encoder_int8.onnx",
                               tokenizer=MODEL / "tokenizer.json",
                               policy_json=MODEL / "policy.json")
    cases = rows("val")
    counts = Counter()
    by_tool: dict = {}
    examples = []
    for case in cases:
        call = policy._predict(case["goal"], None)
        if call is None:
            counts["no_call"] += 1
            continue
        tool = str(call.get("tool") or "")
        raw_args = dict(call.get("args") or {})
        ok_raw, _ = validate_call(tool, dict(raw_args))
        cleaned = clean_call(tool, dict(raw_args), case["goal"],
                             gazetteer=policy.gazetteer, kb=policy.kb,
                             lang=reply_language(case["goal"], default="fr"))
        ok_clean, err = validate_call(tool, dict(cleaned))
        key = (ok_raw, ok_clean)
        counts[f"raw={ok_raw} clean={ok_clean}"] += 1
        slot = by_tool.setdefault(tool, Counter())
        slot[f"raw={ok_raw} clean={ok_clean}"] += 1
        if ok_raw and not ok_clean and len(examples) < 8:
            examples.append({"tool": tool, "goal": case["goal"][:70],
                             "raw": raw_args, "clean": cleaned, "error": str(err)[:160]})
    total = max(len(cases), 1)
    print(f"cases {len(cases)}")
    for key, value in counts.most_common():
        print(f"  {key:22s} {value:5d}  {value / total:6.1%}")
    print("\nper tool (raw=ok clean=bad only):")
    for tool, slot in sorted(by_tool.items(), key=lambda kv: -kv[1]["raw=True clean=False"]):
        bad = slot["raw=True clean=False"]
        if bad:
            print(f"  {tool:18s} made-worse {bad:3d} / {sum(slot.values()):3d}")
    print("\nexamples where postprocess broke a valid call:")
    for ex in examples:
        print(json.dumps(ex, ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
