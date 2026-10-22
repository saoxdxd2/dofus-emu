# Second agent — now linked, and how to test it

The second agent is the **verifier**: it looks at what the first agent did and
says whether it worked, how late it was, and whether the observation now
contradicts what the step assumed.

It is no longer a design. `agent/verifier.py` exists, the loop uses it, and it
runs **without a model** — rules today, model when there is one, human labels on
top. The old framing ("we need data first") was backwards: the loop has to run
and fail first, and the labels fall out of it.

---

## 1. What is wired

| piece | where | state |
| --- | --- | --- |
| Verdict schema | `agent/verifier.py::Verdict` | verdict + confidence + residual + drift + source |
| Rule verifier | `agent/verifier.py::_rule` | always available, no deps |
| Baseline model | `models/verifier_baseline.json` | 33-feature multinomial, pure Python, loads and predicts |
| Neural model | `Verifier._load_model` | ONNX hook, used when onnxruntime is present |
| Human labels | `HumanLabels` → `verdicts_human.jsonl` | append-only, wins over rule/model |
| Loop integration | `agent/autopilot.py::_verdict` | returns the full `Verdict`, not a string |
| Step record | `Step.session/.source/.residual_s/.drift/.obs_before/.obs_after` | written to `autopilot.jsonl` |
| Drift → replan | two consecutive drifts set `replan_requested` | consumed by the host/scheduler |
| Dataset builder | `ai/build_verifier_sft.py` | steps → train/val, session split, class balance |
| Baseline trainer | `ai/train_verifier_baseline.py` | trains + evaluates + writes the artifact |
| Smoke test | `python -m agent.smoke_verifier` | 30 checks, offline, no torch, no numpy |

Every step row is now a supervised example: `(goal, tool, args, before, after) →
(verdict, residual, drift)`, tagged with `session` so splits cannot leak.

## 2. Run it

```bash
cd dofus-network

# link check, no game needed
python -m agent.smoke_verifier

# real run (produces the labels)
python run_autopilot.py --goals-file goals.txt --steps 0 --policy rule

# with a verifier head, once one exists
python run_autopilot.py --goals-file goals.txt --verifier-model models/verifier_v1.onnx \
                        --veto-threshold 0.7
```

## 3. Human in the loop

```bash
python -m agent.verifier --stats                       # how much is labelled
python -m agent.verifier --sessions                    # every run, with verdict counts
python -m agent.verifier --show 98106503 30            # the last 30 steps of one run
python -m agent.verifier --label 98106503 4 failed "the listing never appeared"
```

`--label` prints the next step that still carries only a rule label, so a review
session is a queue rather than a search.

Writes `%LOCALAPPDATA%\DofusFarm\verdicts_human.jsonl`. The loop re-reads it on
the next step, so a reviewer can correct labels while the bot is running, and
those corrections outrank both the rule and the model. That file is the gold set
— it is the only label in the system that is not a heuristic.

## 3b. The pipeline, end to end, without the game

```bash
# 1. labels (synthetic sessions, real verifier)
python -m ai.build_verifier_sft --selfcheck --out data/verifier_sft

# 2. train the baseline + measure it
python ai/train_verifier_baseline.py \
    --train data/verifier_sft/train.jsonl \
    --val   data/verifier_sft/val.jsonl \
    --out   models/verifier_baseline.json

# 3. prove it loads and predicts
python -m agent.smoke_verifier
```

Measured on the shipped artifact: **val accuracy 1.0 on 60 examples**. Read that
number carefully — the self-check labels are produced by the rule verifier from
exactly the features the model sees, so the model is learning the rule function,
not the game. It proves the wiring (features → weights → Verdict → step row) and
nothing about generalisation. A real number needs live or human labels.

The features (`agent.verifier.features`) are shared by trainer and runtime on
purpose: two implementations of a feature set is how train/serve skew starts.

## 4. What is still missing

1. **A real session.** Nothing here needs to be designed; it needs `autopilot.jsonl`
   to exist. That is a run, not a feature.
2. **The neural head.** The dataset builder and the baseline exist; what is
   missing is `ai/train_verifier.py` (head on the encoder trunk, INT8 ONNX export
   with the same bit-identical check as the policy). `Verifier._load_model`
   already takes an `.onnx` artifact — the interface is done, the model is not.
3. **A measured veto threshold.** `--veto-threshold` exists but defaults to 0
   (never veto). It should be set from ECE on a held-out session, not guessed.
4. **Drift → real replan.** The loop prints `REPLAN`; wiring it to
   `ScheduleManager.replan("drop_rate_drift")` is the next line of code, and it
   needs a live plan to be meaningful.
5. **Memory measurement.** 16 MB (8M INT8) + 4 MB assumed; never measured with
   two sessions in one process.

## 5. Falsifiers

- If the rule verdict and the model disagree on >30% of steps and the human gold
  set sides with the rules, the verifier is not worth 16 MB.
- If `advanced` is >85% of steps, a 4-way head is the wrong shape — predict a
  scalar "did the world change" and threshold it.
- If two consecutive drifts fire constantly, the drift rules are wrong, not the
  client: they must be explainable to a human, one line each.
