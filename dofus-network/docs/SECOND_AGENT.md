# Second agent — now linked, and how to test it

The second agent is the **verifier**: it looks at what the first agent did and
says whether it worked, how late it was, and whether the observation now
contradicts what the step assumed.

> **Status note (2026-10-26).** The `agent/smoke_verifier` gate referenced
> throughout this doc was **removed 2026-10-25** along with the rest of the test
> suite; the only surviving in-repo check is `advisor/selftest.py`
> (`KNOWN_GAPS.md` G8). The design (verifier as second agent, step rows as
> supervised examples) still describes intent; the runnable commands do not. The
> learned verifier head is also **orphaned** in the shipped loop
> (`--verifier-model` defaults to `None` — `KNOWN_GAPS.md` G6).

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
# (was `python -m agent.smoke_verifier` — removed 2026-10-25, see KNOWN_GAPS.md G8)
python -m advisor selftest

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
# was `python -m agent.smoke_verifier` — removed 2026-10-25 (KNOWN_GAPS.md G8).
# The Verifier module itself still loads; to exercise it offline:
python -m agent.verifier --stats
```

Measured on the shipped artifact: **val accuracy 1.0 on 60 examples**. Read that
number carefully — the self-check labels are produced by the rule verifier from
exactly the features the model sees, so the model is learning the rule function,
not the game. It proves the wiring (features → weights → Verdict → step row) and
nothing about generalisation. A real number needs live or human labels.

The features (`agent.verifier.features`) are shared by trainer and runtime on
purpose: two implementations of a feature set is how train/serve skew starts.

## 4. The loop is now closed (what changed)

The question that exposed the gap: *when the actor does something wrong, does
agent 2 change what it does next, or does it only record a verdict and print
REPLAN?* Until this session: only record and print. Now:

```
verdict  ->  Controller.on_step(...)  ->  Decision
                                         continue | retry | recover | replan | abort
                        |
                        +--> _forced_call  (the actor's next call is replaced)
                        +--> on_replan     (the host/scheduler is asked to replan)
```

`agent/controller.py` owns what the actor must not carry:

| state | purpose |
| --- | --- |
| objective + progress | how far along the current goal is |
| strategy table | which recovery works, per failure cause (Wilson lower bound) |
| failure memory | per-tool consecutive failures |
| freshness gate | whether an observation is evidence at all |

**Failure causes** are separated because they need different responses:
`wrong_target` / `stale_state` / `incomplete` / `invalid_assumption` / `plan_wrong`.

**Rules of authority** (each one tested):

* first failure → `retry` the same call, once;
* second → `recover` with a concrete different call (`state.snapshot`);
* `not_mapped` → `replan` immediately: the tool cannot express the goal;
* three consecutive failures of one tool → `replan` (`plan_wrong`);
* two **fresh** drifts → `replan`; drift on a stale/identical observation is not
  evidence and does not count;
* drift outranks "advanced": a step can move the character and still contradict
  the plan;
* `abort` when there is no recovery.

`Controller.observe_outcome` credits the previous decision with the next step's
result, so the strategy table learns which recovery works instead of being
written once and never revised. State persists to
`%LOCALAPPDATA%\DofusFarm\controller\<session>.json`.

## 5. Labels: weight, do not just count

Rule verdicts are heuristics, not ground truth. `ai/build_verifier_sft.py` now
carries a per-row `label_weight` — human 1.0, model 0.6, rule 0.3 — and both
trainers multiply the loss by it. A thousand confident rule labels no longer
drown ten human corrections. `--min-confidence` drops uncertain rows entirely.

## 6. Veto threshold: a trade, not a number from ECE

Both trainers now print a sweep: for each threshold, **% of real failures
prevented** and **% of good actions blocked**. Choosing the threshold is a
decision about that trade, and the table makes the cost visible instead of
hiding it behind calibration alone.

## 7. Measured memory (replaces the 137 MB assumption)

`ai/measure_rss.py`, embedded runtime, both artifacts loaded in one process:

| step | RSS |
| --- | --- |
| python only | 17.5 MB |
| + numpy | 27.3 MB |
| + onnxruntime | 43.9 MB |
| + encoder_int8.onnx (25 MB file) | 83.9 MB |
| + verifier_head.onnx | 84.0 MB |
| after first inference | **95.3 MB** |

The second agent costs **~0.1 MB** as built (33 features → 128 → 64 → 4, 52 KB),
not the 16 MB a transformer head would have needed. The 137 MB design budget
holds with ~42 MB spare.

## 8. What is still missing

1. **A live session.** Everything above is exercised offline. One real run with
   a character is still the gate for numbers that mean anything.
2. **Labels from the game.** The head trains, exports and loads, but on
   rule-labelled synthetic data: it learns the rule function. Point the builder
   at a live `autopilot.jsonl` and the same command produces a model whose score
   is a measurement.
3. **The planner's persistent objective.** The controller tracks progress for a
   session; `ScheduleManager` still does not own quest/objective state across
   restarts, so a replan currently means "ask the host", not "rebuild the plan".
4. **Experience affecting strategy choice beyond recovery.** The strategy table
   covers recovery per cause; it does not yet rank *plans*.

## 9. Falsifiers

- If the rule verdict and the model disagree on >30% of steps and the human gold
  set sides with the rules, the verifier is not worth 16 MB.
- If `advanced` is >85% of steps, a 4-way head is the wrong shape — predict a
  scalar "did the world change" and threshold it.
- If two consecutive drifts fire constantly, the drift rules are wrong, not the
  client: they must be explainable to a human, one line each.
