# Latent reasoning, agent-to-agent channels, and online weight updates

Decision document. Every proposal below is judged on four axes: **what it buys**,
**what it costs in RAM/CPU**, **what can go wrong silently**, and **whether the
repo can measure the gain**. A proposal that cannot be measured here is rejected,
because this project's whole discipline is that an unmeasured claim is a guess.

Context that constrains every answer: two agents inside 200 MB/instance, one
30M encoder at 25 MB INT8 and 60 ms/call, a symbolic planner at 1.6 ms/plan, and
a conjugate-posterior learning loop at 5.5 µs/observation. Anything that costs
more than those numbers has to buy something they cannot.

---

## 1. The claim under test: thinking without writing tokens

Two independent lines of evidence say the tokens are not where the reasoning has
to live.

**TRM — "Less is More: Recursive Reasoning with Tiny Networks"**
(Jolicoeur-Martineau, Samsung SAIL Montréal, Oct 2025). A 2-layer, 7M-parameter
network that recurses on a latent state, with no chain-of-thought tokens and no
RLHF, beats models three orders of magnitude larger on Sudoku-Extreme, Maze-Hard
and ARC-AGI. It is the successor to HRM (Hierarchical Reasoning Model), which
used two networks recursing at different frequencies — the h/L cycle this repo
already mirrors in `combat/recursive_reasoner.py` and the planned L-cycle model.

**Coconut — "Training Large Language Models to Reason in a Continuous Latent
Space"** (Hao et al., Meta + UCSD, arXiv:2412.06769, Dec 2024). Instead of
emitting reasoning tokens, the model feeds its last hidden state back as the next
input embedding, so the "thought" is a continuous vector. Reported result: equal
or better reasoning with fewer tokens, because the breadth of a continuous state
replaces the serial depth of a token chain.

The counter-evidence is real and should be read before committing:
**"Do Latent Tokens Think? A Causal and Adversarial Analysis of
Chain-of-Continuous-Thought"** (2025) finds that latent tokens can carry
information the output head never learned to use — the model can appear to reason
in latent space while the answer is still decided by the last few tokens. That is
the failure mode to guard against: *latent reasoning that nothing checks is
indistinguishable from no reasoning at all.*

**What this means for us.** The encoder is text→action. It has no decoder and
needs none: the "thought" is the pooled 512-d vector, and the action head is a
cosine against 16 capability anchors. That is already a latent decision — we are
not adding latent reasoning, we are already doing it. The open question is
therefore narrower and more useful: **what else can that vector carry, and who
should read it?**

---

## 2. The agent-to-agent channel

### Proposal: a shared latent bus, not text

Two agents in one process do not need to talk in tokens. The first agent already
produces, per step: a 512-d pooled vector, the chosen capability id, the validated
args, the plan node it came from, and the observation. The second agent's job
(§5) is to judge that step. Handing it the pooled vector plus a 12-dim progress
vector costs nothing — no tokenizer, no decoding, no latency.

| channel | cost per message | lossiness | debuggable |
|---|---|---|---|
| text (today's logs) | ~20 tokens ≈ 15 ms decode | entity names survive, *intent* does not | yes |
| latent vector (512-d fp16) | ~1 KB, 0 ms | intent survives, names do not | no, unless logged |
| both (latent + text sidecar) | 1 KB + the log line we already write | neither | yes |

**Decision: both.** The latent bus is the control channel; the JSONL log stays the
audit channel. This is the same split the repo already uses for plans —
`plan.render()` is for humans, the plan tree is for the machine — and it costs
one `tolist()` per step.

**What it buys:** the second agent can be conditioned on *why* the first chose a
call, not just what it chose. A critic reading `"hdv.scan {item: X}"` cannot tell
whether the model was confident or guessing; a critic reading the pooled vector
and the anchor margin can.

**What it costs:** the two agents must share an encoder version. If they diverge,
the vector means something different to each and the failure is silent. Mitigation:
stamp every latent message with the checkpoint hash, and have the second agent
refuse on mismatch — the same discipline `networkhealth/contract.py` already
applies to its normaliser.

**Rejected variant: a learned communication channel** (each agent learns its own
encoder/decoder pair, as in emergent-communication work). It cannot be debugged,
it cannot be trained without a reward that is itself unmeasured, and the two
agents already share a trunk. Not worth a megabyte.

---

## 3. The user-facing response — the hard half

The user's constraint is correct and it is the constraint that kills the naive
answer: **a bot that answers in broken language is worse than one that says
nothing.** "No tokens" works for agent→agent; it does not work for agent→human.

Three options, judged:

| option | latency | RAM | language quality | can hallucinate a price? |
|---|---|---|---|---|
| **A. template renderer** from the plan tree | <1 ms | 0 | fixed but correct, 4 languages | no — it prints numbers it has |
| **B. small decoder (8–12M)** | ~200 ms | ~25 MB | good for seen phrasings, degrades on new ones | yes |
| **C. external LLM (DeepSeek/Qwen)** | 400 ms–2 s | 0 local | best | yes, and it costs money per turn |

**Decision: A by default, B only for free-form chat, C never in the loop.**

The reasoning is not "A is cheaper". It is that **the bot's user-facing statements
are almost always structured facts**: "I am at 12,400 kamas/hour, 3 hours from
your target", "this fight is 62% winnable, I would need 4 more levels", "I cannot
buy that: the cap is 50k and it costs 120k". A template over the plan tree renders
all three, in four languages, with the numbers coming from the posteriors — so it
cannot claim a number it does not have. A decoder can, and a decoder that
hallucinates a price in a market bot is a financial bug, not a style bug.

The template layer is also where the **keyword feedback** the user asked for
lives: `IMPOSSIBLE`, `POSSIBLE <pct>`, `BLOCKED <reason>`, `DONE <n>`. Those are
four tokens the *scheduler* emits, not the model — the model never has to learn to
say them, which is exactly why they will always be right.

B is worth building only when a real user asks something the templates cannot
express. Until then it is 25 MB of unmeasured risk.

---

## 4. Online weight updates — four proposals, one accepted

The user asked for a judgement on MoE, first-pass weight optimisation,
evolutionary weight search, and real-time weight improvement. In order of how
much I would trust each:

### 4.1 Test-time training / fast weights — **accept, narrowly**

NeurIPS 2025 was dominated by this: update a small set of "fast" weights at
inference so learning and inference happen together, with memory O(1) in context
length instead of growing a KV cache.

The catch is catastrophic forgetting plus **unverifiable drift**: if the weights
move on every episode, no two runs are comparable and a bad batch teaches a
lesson you cannot roll back. For a bot that spends kamas, that is unacceptable at
the trunk.

Where it *is* acceptable: the **L-cycle residual model** (~1M params, 4 MB),
whose entire output is a bounded multiplicative correction on a time estimate. If
it drifts, the worst case is "the bot thinks this farm takes 20% longer" — it
cannot make the bot buy the wrong item. Fast weights there are a real win because
the label (predicted vs actual minutes) arrives for free on every step.

**Verdict: fast weights in the L-cycle only, clamped to [0.5, 2.0], with the
previous weights kept and rolled back if a held-out window degrades.** Not in the
encoder.

### 4.2 Evolutionary / population weight search — **reject**

Evolution needs many full evaluations per generation. One farm episode is minutes;
a population of 20 over 100 episodes is ~33 hours of wall-clock for one
generation, on content that changes as the player levels. The gradient signal here
(residuals from the planner's own predictions) is free and immediate. Evolution is
the right tool when the objective is non-differentiable and evaluation is cheap —
neither holds.

### 4.3 MoE — **defer, with a named trigger**

The repo already measured this: a sparse top-2 MoE on the 30M trunk adds ~30 MB of
INT8 weights for capacity that 21k training rows cannot feed. `PLANNER_DESIGN.md`
§7 says don't; the evidence has not changed.

The trigger to revisit is specific: **one dense trunk demonstrably underfits two
domains at once** — i.e. adding the chat/critic task makes tool accuracy drop
while both tasks' losses fall. Then MoE is the right answer, because the two
experts want different features. Not before.

### 4.4 First-pass weight optimisation (better init) — **accept, cheaply**

This is the one with the best ratio: no runtime cost at all, only a better starting
point. Concretely, initialise the 30M encoder from the *previous* checkpoint
(`--resume`, now implemented) and spend the saved steps on the epochs that were
being cut short. v11 stopped at 2,400 of 8,244 planned steps; a continuation at
lr 3e-5 is strictly cheaper than a scratch run and starts from 0.685 call instead
of 0.20.

**Verdict: this is what the current run does. It is the only "online" change that
is free at inference.**

---

## 5. The second agent — scope

The user's framing: it organises the schedule, tracks what the first agent does,
and covers moderation, drift detection and self-critique — the things the first
cannot afford.

That is one model with one input and four outputs, and the input is already
computed:

```
input   (12-dim, exists today in ScheduleManager)
        elapsed/expected, kamas_rate obs/exp, drops/kills vs prior, level_gap,
        steps_done/total, replans, deaths, hp_avg, inventory_pressure,
        price_drift, age_minutes, strategy_id
        + 512-d pooled vector from the first agent (the latent bus, §2)

outputs
        verdict     continue | replan | block | escalate   (4-way)
        residual    multiplicative correction on minutes    (scalar, clamped)
        drift       is the world different from the model's belief?  (score)
        critique    which argument of the last call was least supported (16+1 way)
```

Size: ~1M params, <5 MB fp32, sub-millisecond. It is the L-cycle the design
already specified, plus a verdict head.

**Why it can learn without labels:** every output has a free label.
`residual` is predicted-vs-actual minutes. `verdict` is checkable after the fact —
did the step succeed within 1.5× its estimate? `drift` is the same residual
distribution shift. `critique` is only trainable once the outcome says which
argument was wrong, which is why it comes last.

**Order of building:** residual → verdict → drift → critique. Each is useful
alone, and each one's labels are generated by the previous one.

---

## 6. What I would build, in order

1. **Latent bus** (1 day): stamp the pooled vector on every step record; second
   agent reads it. Zero new model.
2. **Template renderer** (§3A): the four keyword verdicts + the plan-tree
   sentences, in en/fr/es/de. This is what makes the bot speakable.
3. **L-cycle with fast weights** (§4.1): 1M params, residual head first.
4. **Second agent verdict head** on top of (3).
5. **MoE only if** the trigger in §4.3 fires.

Everything else — evolutionary search, a learned inter-agent code, a decoder in
the control loop — is rejected above with the reason.

---

## 7. Open questions for the user

1. **Language for the template layer**: do the four languages need different
   *tone* (formal/informal), or is one register per language enough?
2. **Keyword vocabulary**: `IMPOSSIBLE` / `POSSIBLE <pct>` / `BLOCKED <reason>` /
   `DONE <n>` — is that the whole set, or does the scheduler need more?
3. **Fast-weight rollback window**: how many episodes of held-out evidence before
   a drift is trusted enough to roll back? (Default I would pick: 50.)
