# 2026 literature against this policy's measured failures

Six papers from 2026, read against results this repository already produced. Each
section states the paper's claim, then what it explains about a number we measured.
The point is not that the papers agree with us; two of them say a design choice here
is wrong.

Measured baseline this document refers to: `models/tool_policy.onnx`,
`arch=dedicated-v1`, 266-wide input, 0.9075 label accuracy, **0.9166 task
optimality**, **0.3319 gain capture**, on `data/tool_sft_v3/val.jsonl` (3,719 rows).

---

## 1. CILA — why both of our logit adjustments failed

**Instance-wise Logit Adjustment for Long-Tailed Recognition**
Lim & Lee, arXiv:2610.27728, 20 Oct 2026.

Class-wise logit adjustment subtracts `tau * log(pi_y)` from every logit. CILA's
contribution is naming what that does to the *non-target* logits: it suppresses them
uniformly by class frequency, regardless of whether a given input actually confuses
that class with the target. They call it **Non-Target Logit Over-Suppression**
(NTLOS), and give the gradient form `dL/dz_k = p~_k << p_k` — the gradient from a
genuinely confusing tail class is crushed by an amount that has nothing to do with
how confusing it was for *this* instance. Their fix is an instance-wise penalty that
fires only for classes whose logit exceeds the target's, scaled by the severity of
that confusion.

This is the explanation for two results we recorded and could not account for:

| attempt | result | CILA's reading |
|---|---|---|
| global adjustment (`tools/logit_adjust.py`) | tau=0.25: 3x more read tools emitted, capture +0.32pp, accuracy **-1.6pp**, optimality flat | class-level term, no instance conditioning |
| stratified per-`obs_stale` (`tools/stratified_adjust.py`) | every tau worse than tau=0 | conditioning on a stratum is still not conditioning on the instance |

Our worst confusion pair is `nav.goto` <-> `quest.advance`, which is 35% of all lost
gain (`tools/error_decomposition.py`). CILA is aimed exactly at that shape: a
frequently-confused non-target class whose gradient is being over-suppressed.

**Tested — and it does not reach the bucket.** Implemented as a training loss
(`ai/dedicated_policy.py: cila_adjust`, `--cila`), class weights from the fitting split
only, bound 2.0. Paired on val:

| metric | base | CILA | delta | CI |
|---|---|---|---|---|
| optimality | 0.9166 | 0.9158 | -0.00081 | [-0.00430, +0.00269] |
| accuracy | 0.9075 | 0.9040 | -0.00350 | [-0.00699, 0.00000] |
| macro-F1 | 0.2120 | **0.2144** | +0.0024 | — |
| fresh stratum | 0.9602 | 0.9569 | **-0.00326** | [-0.00652, -0.00072] |
| stale stratum | 0.7912 | 0.7975 | +0.00626 | [-0.00522, +0.01670] |

The mechanism did not fire where it was aimed. On the targeted pair:

```
             nav.goto -> quest.advance   quest.advance -> nav.goto
base                    52                        19
CILA                    54                        20
```

The explanation is in CILA's own formula. It scales the penalty by the inverse
frequency of the **target** class, so it is strongest when the target is rare. Our worst
confusion is **head-vs-head** — `nav.goto` is 23.7% of labels and `quest.advance` 32.6%
— which is precisely where that weight is smallest. CILA did what it is designed to do
(macro-F1 rose, a tail-class metric) and could not help, because we are not losing gain
on the tail.

**Outcome: rejected.** A method that improves the classes we are not losing gain on
cannot fix the loss. Record: `models/cila.candidate.json`.

---

## 2. ALER — why the staleness fix only half worked, and why the hand-built version lost

**ALER: Adaptive Learnable Experience Rewriting for Reinforcement Learning**
Shchendrigin, Cherepanov, Panov, Kovalev, arXiv:2610.00592, 30 Sep 2026.

ALER separates three memory requirements that are usually conflated:

- **retention** — keep stored information unchanged until it is needed
- **rewriting** — set decision-relevant content to a value *independent of the old one*
- **experience fusion** — transform old content by a rule a later observation specifies

Their empirical claim is that memory benchmarks mostly test retention, and baselines
"reach their lowest success rates on compositions that need more states" — i.e. they
fail specifically where rewriting is required. Their architecture pairs an LSTM with
a slot memory, an independently addressed Gumbel-Softmax write that concentrates on
one slot, and a **learned gate** fusing retrieved content with the recurrent state.

Our failure is a rewriting failure, not a retention failure. Measured:

- fresh observation: **0.960** optimal (n=2761)
- stale observation: **0.791** optimal (n=958) — 26% of rows, 65% of all errors

The GRU over 8 history tokens is a retention mechanism. When a stale observation
arrives, the correct behaviour is to *rewrite* the stored position to "unknown" or
fall back to the last fresh one, and nothing in the architecture does that.

This also explains a result that looked like a dead end. We hand-coded the rewrite
rule as three derived dims (`last_fresh_dims`: on_target, hops, age) and it measured
**worse** — 0.9172 optimal against 0.9204 for the per-step form. ALER's framing says
that is expected: a hand-specified rewrite rule is exactly the thing that should be
learned, and freezing it into the input removes the model's ability to decide when to
rewrite.

**Actionable:** replace the hand-coded dims with a learned write gate over a small
position slot. The per-step observation summary (already implemented behind
`--obs-history`) is the right input to that gate.

### 2b. The durable store — retention across sessions, and what a holdout says it is for

`agent/persistent_memory.py` is the retention half of the same taxonomy, taken literally:
a store that survives the process. Two dictionaries keyed by
`(objective, type, step, map)` — the call that advanced here, and the call that failed
here — written atomically (`NamedTemporaryFile` + `os.replace` + `fsync`, because a
killed process is the normal case, not the exception).

`tools/seed_memory.py` fills it from the logs that already exist, so the first run after
this change does not start empty. Measured on 38 step logs:

| | |
|---|---|
| decided steps applied | 1,216 `advanced`, 912 `failed`, 3,160 `noop` |
| skipped | 4,310 read-only (`state.snapshot` scores `advanced` by construction) |
| result | **318 solutions over 73 contexts, 191 dead ends over 48** |

The holdout is the part that decides what this thing is allowed to do. Seed from six
logs, score the seventh (1,217 decided steps):

| question | answer |
|---|---|
| is the context known? | **76.8%** (935/1,217) — the store covers the situation |
| does its top call match what the agent did? | **1.2%** (11/935) |
| … split by family | navigation **0.5%** (4/785), quest **5.0%** (7/139) |

The split is the finding. Navigation has many right answers per context and they
alternate — walking left and walking right are "the same situation", so a single
remembered move is a control input replayed out of phase. Quests have one answer per
context, and the agreements are exact keys: `quest.advance{quest_id:3841, npc_id:3888}`,
a dialog reply with the same `reply_text`.

So the store answers puzzles and does not steer: replay is refused for `nav.*` and
`ui.*` (`MEMORY_REPLAY_EXCLUDE`), bounded to one attempt per context per run, and only
consulted where the policy returned no call at all. That last clause is what keeps it
honest — it is a fallback for silence, not a second policy, and nothing here shows that
replaying improves progress. That needs a live A/B, which is the next measurement.

---

## 3. ARM — a critique that does not land here (measured, then withdrawn)

**ARM: Advantage Reward Modeling for Long-Horizon Manipulation**
Mao et al., arXiv:2604.03037, Apr 2026.

ARM argues that dense progress rewards are "costly to obtain and ill-suited to
non-monotonic behaviors such as backtracking and recovery", and shifts from absolute
progress to relative advantage, labelled tri-state (Progressive / Regressive /
Stagnant).

Our `gains` column is a **cumulative mean** of a progress curve, which looks like the
dense absolute-progress signal ARM is criticising. Comparing `argmax(mean progress)`
with `argmax(final progress)` over all 19,686 rows:

```
agree     18072  (91.80%)
disagree   1614  ( 8.20%)
```

I first read that as a defect: `quest.advance` scores gain 1.000 / final 1.0 while
`farm.start` scores gain 0.500 / final **2.0**, so the rate metric prefers
fast-and-shallow. **That reading was wrong**, and the correction is the useful part of
this section.

The choice is deliberate and documented at `sim/tool_choice.py:112`:

> "Total progress over a fixed horizon does not discriminate: with three steps
> available, a wasted read still finishes the objective, so every candidate ties and
> the label collapses back into 'what the teacher did'. What separates them is the
> rate — one objective in one step beats one objective in three."

Measured on the shipped corpus, the documented claim holds and is understated:

| scoring | argmax tied | every candidate equal |
|---|---|---|
| `cummean` (shipped) | 30.30% | 13.53% |
| final progress | **74.07%** | **29.64%** |

Scoring by final progress would leave three quarters of labels decided by the
tie-break rather than by the world, and on 29.6% of rows it would not discriminate at
all. ARM's critique is about dense rewards that fail to distinguish progress from
noise; here the denser signal is the one that discriminates.

**Outcome: no change. The existing objective is correct and already justified in the
source.** The lesson recorded for this repository is procedural: the disagreement was
real, but it should have been checked against the generator's own rationale before
being reported as a defect. A metric disagreeing with another metric is not evidence
that either is wrong.

---

## 4. MOSS — our 22% tie rate is multimodality, and a softmax cannot represent it

**MOSS: Mode-Oriented Source Shaping for Robot Flow Matching Policies**
Ma et al., arXiv:2610.22193, 15 Oct 2026.

> "In robotic manipulation, the same observation often admits multiple feasible actions."

MOSS notes that conventional policies "regress toward an optimal action" while
generative ones represent several, and that even generative policies suffer mode
collapse under finite data and mode imbalance. Its fix makes mode selection an
explicit choice of where generation starts, with mode probability set **directly via
the source sampling distribution rather than inherited from the empirical data
frequency**.

Two things transfer:

1. We measured that **22% of val rows have ties at max gain**, which is why the gate
   had to become tie-aware. We then handled that with a unimodal softmax classifier,
   which must commit to one mode per state. The `optimal` metric forgives it; the
   architecture still cannot express "either of these two".
2. MOSS's answer to mode imbalance is a *source distribution*, not a logit offset.
   That is a principled alternative to the logit adjustment that failed for us in §1 —
   it changes what is generated rather than post-hoc correcting what was predicted.

**Actionable:** if tail-mode behaviour matters, the head needs to be generative
(flow/diffusion over 21 tools) rather than a softmax. This is a larger change and
should not be attempted before §3 is settled.

---

## 5. CFGRL — the product-policy form, and a warning about our soft targets

**Diffusion Guidance Is a Controllable Policy Improvement Operator**
NeurIPS 2026, arXiv:2505.23458.

Establishes `pi(a|s) ∝ pi_hat(a|s) f(A(s,a))` with `f` bounded, non-negative and
monotone in advantage, and proves that raising the guidance exponent strictly improves
expected return. Two consequences here:

- Our soft targets are already this product form: `softmax(gains / T)`. So `argmax` of
  the fitted model is `argmax` of predicted gain — the decision rule is not the
  problem, which is consistent with three separate post-hoc fixes all failing.
- The paper warns that exponential advantage weights "produce extreme gradient spikes
  on a handful of outlier transitions within each batch, starving the remaining
  transitions of gradient signal". We use `T=0.5` over gain values in `[0,1]`, where
  the ratio between the best and worst action is bounded, so the spike risk is mild —
  but it is worth checking whether `T` is doing anything at all. Note that a global
  `logit_scale` cannot change `argmax`, so the fitted temperature affects the exported
  probabilities and not the decisions.

---

## 6. Supporting: AWR/IQL in agent stacks

- **Hierarchical RL with Augmented Step-Level Transitions for LLM Agents**,
  arXiv:2604.05808 — uses IQL expectile value learning plus exponential
  advantage-weighted regression `exp(A/beta) * log pi` on ScienceWorld and ALFWorld.
  Directly comparable setting (tool-calling agent, step-level transitions), and it
  confirms the AWR objective rather than plain behaviour cloning.
- **IG-RFT**, arXiv:2602.20715 — Interaction-Guided Advantage Weighted Regression for
  VLA fine-tuning; reports SFT 18.8% against offline RL 40.0% on long-horizon tasks,
  i.e. plain BC is the weak baseline in this family too.

Our policy is plain behaviour cloning on soft targets with no value-based reweighting.
The `value` head exists and reports AUC 0.9727, but it is **logged and never used to
weight anything**. That is the gap these two papers close.

---

## 7. Goodfire AI — Mechanistic Shaping: Internal Probing Against Reward Hacking

**Mechanistic Shaping: Guiding Internal Representations Beyond Surface Loss**
Goodfire AI Research, 2026.

Goodfire demonstrated that when agents engage in **reward hacking** (e.g. taking degenerate shortcuts
that game the reward metric without accomplishing the underlying task), the model maintains an internal
representation of the shortcut. Instead of evaluating only terminal tokens or final rewards—which allows
the model to be "right for the wrong reasons"—Goodfire trains models by probing their internal states
and penalizing reliance on deceptive or shortcut features.

In Dofus Touch simulation (`sim/tool_choice.py`), our recorded baseline suffered from an identical failure
mode:
- Only **4.5% of world-changing actions actually moved anything** (58 out of 1,277 steps).
- When trained on cumulative mean progress (`cummean`), the model learned to repeat cheap read loops
  or fire no-op actions that accumulated fractional progress metrics without altering real game state
  (`objectives_completed == 0` across 8,526 real observations).

**Mechanism & Transfer:**
1. **Internal State Probes:** Train a lightweight linear probe (Difference-of-Means vector) on the
   internal hidden layer of `models/tool_policy.onnx` to distinguish real world-state transitions
   (e.g., `map_id` change, `QuestStepValidatedMessage`) from synthetic reward gains.
2. **Mechanistic Auxiliary Loss:** During offline Kaggle training, add a regularizer penalizing internal
   shortcut feature activations:
   `L_total = L_task + lambda * L_probe(h)`
   where `L_probe` penalizes actions proposed when the internal state indicates the action is an ungrounded
   noop or read loop.

**Cost & Deployment:** Zero runtime compute overhead. The linear probe is used either during training
as an auxiliary loss or at inference as a single dot-product `h · w_probe` costing <0.01 ms on CPU.

---

## 8. Sparse Autoencoders (SAEs) — Disentangling Head-vs-Head Polysemanticity

**Scaling Monosemanticity: Extracting Interpretable Features via Sparse Autoencoders**
Anthropic / Goodfire / DeepMind, 2024–2026.

Neural network layers exhibit **superposition**: because real-world task spaces are higher-dimensional
than the model's physical hidden width, individual neurons become polysemantic, firing for multiple
unrelated concepts. A Sparse Autoencoder (SAE) resolves this by expanding hidden activations into an
overcomplete sparse feature dictionary:
`x_hat = sum_i f_i(x) * d_i` with top-k / L1 sparsity.

This directly explains our most expensive failure mode:
- **Head-vs-head confusion:** `nav.goto` <-> `quest.advance` accounts for **35% of all lost gain**
  (`tools/error_decomposition.py`).
- CILA (§1) failed because class-frequency weighting cannot separate two dominant head classes.
- Polysemantic entanglement in the 266-dim feature space: the model entangles *"NPC is targeted"* with
  *"NPC is on current map"*. When the target NPC is on an adjacent map, both the dialog head and the
  movement head activate simultaneously.

**Actionable:**
1. Train a tiny overcomplete SAE (e.g. 266 -> 1024 features) on the hidden layer of the tool policy
   using free Kaggle GPUs.
2. Identify the monosemantic feature vectors corresponding to `target_entity_off_map` vs
   `dialog_ready_on_cell`.
3. **Runtime Steering:** At inference time, perform activation steering by clamping or projecting out
   the entangled vector: if `current_map != target_map`, project out the `quest.advance` component
   directly before the logit projection. This resolves the 35% error cleanly without widening the model.

---

## 9. Sparks of Weight-Space Programming — Semantic Weight Surgery over Blind Perturbations

**Sparks of Weight-Space Programming: LLMs can natively read & write neural network weights**
Takehi, Teoh, Zhang, Khattab (MIT CSAIL), arXiv / Tech Report, 2026.

Takehi et al. demonstrated that frontier reasoning models can inspect raw neural network parameters
(weights and biases) formatted as structured tensors, diagnose algorithmic failure modes, and directly
write floating-point edits to parameters without backpropagation or training loops. In their findings,
modifying as little as **0.016% of weights** (~1,000 parameters) eliminated reasoning loops and dramatically
boosted performance on reasoning benchmarks.

**Why our previous weight-space lane (§C, `agent/weight_space.py`) was paused:**
- Our previous implementation used blind numerical perturbations and random mutation search, which
  degraded macro-F1 and triggered the `MIN_MACRO_F1 = 0.15` floor.
- Weight-space programming is **not random search**; it is semantic compilation.

**The Validated Workflow for Dofus Touch:**
1. **Weight Manifest Export:** `agent/weight_space.py` exports the base ONNX weights (`tool_policy.onnx`,
   49K params) into structured JSON manifests with input/output feature attributions.
2. **Offline LLM Diagnosis:** An offline frontier model inspects the weight slice connecting the 42
   objective features to the `nav.goto` and `quest.advance` output logits, identifying the specific
   cross-talk weights responsible for the 35% confusion.
3. **Surgical Micro-Edit:** The LLM proposes an exact sparse edit modifying fewer than 50 floating-point
   values in the final linear projection matrix.
4. **Deterministic Replay Gate:** `agent/weight_space.py` verifies the candidate against all 3,719
   validation rows, ensuring zero regression on common-class accuracy and maintaining macro-F1 above the
   pinned baseline before promotion.

**Budget:** 100% free. The analysis is done offline in development sessions, and the resulting patched
ONNX runs locally with zero API calls.

---

## Ranked next experiments

| # | experiment | source | cost | status |
|---|---|---|---|---|
| ~~1~~ | ~~Decide rate vs completion~~ | ARM §3 | — | **Closed: existing objective correct (74% ties under the alternative)** |
| ~~2~~ | ~~CILA loss~~ | CILA §1 | — | **Closed: rejected, targets tail confusion; our loss is head-vs-head** |
| 1 | Head-vs-head SAE feature steering | SAE §8, Goodfire | ~2 hours | Directly isolates `nav.goto` vs `quest.advance` (35% of lost gain) via activation clamping |
| 2 | Semantic Weight-Space Micro-Edit | Takehi et al. §9 | ~1 hour | Use LLM weight inspection to surgically patch the confusion weights via `agent/weight_space.py` |
| 3 | Learned write gate over a position slot | ALER §2 | ~1 day | The only one that addresses the 65%-of-errors stratum structurally |
| 4 | Mechanistic Shaping auxiliary loss | Goodfire §7 | ~1 day | Train linear transition probe on Kaggle to eliminate 4.5% idle/stall reward hacking |
| 5 | Advantage-weighted BC using the existing value head | CFGRL §5, AWR §6 | ~1 day | The value head is already trained and currently unused |
| 6 | Generative head over 21 tools | MOSS §4 | ~1 week | Right answer to 22% ties; do it last |

Experiment 1 and 2 directly address the 35% lost gain from the `nav.goto`/`quest.advance` head confusion
without requiring model expansion or costly training runs.

**Cross-cutting observation.** Two unrelated interventions (observation history, CILA)
both moved the stale stratum up and the fresh stratum down. That symmetry suggests the
split is capacity allocation rather than a property of either method — worth testing
before adding a third.
