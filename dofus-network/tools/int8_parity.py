"""INT8 vs fp32 parity check for the v11 encoder, through the serving path.

The release gate says the quantised artifact must not lose more than 2 points of
call accuracy. This measures it the only way that counts: same corpus rows, same
`EncoderToolPolicy.choose()` (postprocess + resolvers on), two backends.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(r"C:/Users/sao/Documents/dofus-emu/dofus-network")
sys.path.insert(0, str(ROOT))
import os

os.chdir(ROOT)

from agent.tool_eval import score_one  # noqa: E402
from ai.encoder_serve import EncoderToolPolicy  # noqa: E402

MODEL = ROOT / "models" / "encoder30_v11"


def rows(split: str, limit: int = 0):
    path = ROOT / "data" / "core_sft_v4" / f"{split}.jsonl"
    out = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out[:limit] if limit else out


def evaluate(policy: EncoderToolPolicy, cases) -> dict:
    tool_ok = call_ok = 0
    lat = 0.0
    for case in cases:
        started = time.perf_counter()
        call = policy.choose(case["goal"], None, None)
        lat += (time.perf_counter() - started) * 1000.0
        prediction = None if call is None else {"tool": call.tool, "args": call.args}
        t, c = score_one(case, prediction)
        tool_ok += int(t)
        call_ok += int(c)
    n = max(len(cases), 1)
    return {"cases": len(cases), "tool": tool_ok / n, "call": call_ok / n,
            "ms_per_call": lat / n}


def main() -> int:
    tok = MODEL / "tokenizer.json"
    pol = MODEL / "policy.json"
    torch_policy = EncoderToolPolicy(checkpoint=MODEL / "model.pt", tokenizer=tok,
                                     policy_json=pol, device="cpu")
    int8_policy = EncoderToolPolicy(onnx=MODEL / "encoder_int8.onnx", tokenizer=tok,
                                    policy_json=pol)
    report = {}
    for split, limit in (("gold", 0), ("val", 0), ("val_indist", 300)):
        cases = rows(split, limit)
        fp32 = evaluate(torch_policy, cases)
        int8 = evaluate(int8_policy, cases)
        delta = {k: round(int8[k] - fp32[k], 4) for k in ("tool", "call")}
        report[split] = {"fp32": fp32, "int8": int8, "delta": delta}
        print(f"{split:11s} fp32 tool {fp32['tool']:.3f} call {fp32['call']:.3f} "
              f"({fp32['ms_per_call']:.0f} ms) | int8 tool {int8['tool']:.3f} "
              f"call {int8['call']:.3f} ({int8['ms_per_call']:.0f} ms) | "
              f"delta tool {delta['tool']:+.3f} call {delta['call']:+.3f}", flush=True)
    (MODEL / "int8_parity.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    worst = min(v["delta"]["call"] for v in report.values())
    print(f"worst call delta {worst:+.4f} -> gate (>= -0.02): {worst >= -0.02}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
