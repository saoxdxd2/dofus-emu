# Live Testing Workflow for Model Weights

After you build a new model artifact (ONNX/checkpoint), run the post-build live
test harness to validate it before promoting. Two modes:

## Option 1: One-off test

```bash
# Test a single candidate against the baseline (offline, no client needed)
python tools/live_test_after_build.py --model models/tool_policy.onnx

# Compare a candidate folder against baseline
python tools/live_test_after_build.py --model models/candidates/foo.onnx \
    --baseline models/tool_policy.onnx --report out/report.json

# Full suite including live client (requires Dofus window open)
python tools/live_test_after_build.py --model models/tool_policy.onnx --live
```

## Option 2: Auto-watch while you iterate

Run this in one terminal while you build weights in another:

```bash
python tools/watch_build_and_test.py --baseline models/tool_policy.onnx
```

It polls `models/` and `models/candidates/` for new `.onnx` files. When one
appears, it auto-runs the offline test suite and writes a JSON report to
`data/live_test_results/`.

## What gets tested

| Stage | What | Pass criteria |
|-------|------|---------------|
| 1. Offline model scoring | Candidate ONNX vs baseline on `data/tool_sft_v3/val.jsonl` via `agent.weight_space.candidate_verdict` | Gate decision not "REFUSED"; no tool set mismatch |
| 2. World-state exactness | `tools/test_world_state.py` (terrain encoding) | 27/27 OK |
| 3. Dialog policy | `tools/test_dialog_policy.py` | 7/7 OK |
| 4. Battle solver | `combat.test_battle_solver` | No new failures vs baseline (2 pre-existing OK) |
| 5. Planner benchmark | `tools/bench_quest_policies.py --policies rule` | Behavior identical to baseline (quests 190.00, per1000 126.6) |
| 6. Regression gate | `python -m agent.regression` | All checks pass |
| 7. Live evidence (--live only) | `tools/live_evidence.py --report-only` | Requires client window |

## Report

Each run writes a JSON report to `data/live_test_results/live_test_<ts>.json`
with per-stage decisions and an overall `PASS`/`FAIL`/`PARTIAL` verdict.

Promote a model only when `overall == "PASS"` (or `PARTIAL` with only skipped
live stages).