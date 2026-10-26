# Experience learning — every parameter moves from real outcomes

The rule this document implements:

> Nothing in the agent is a constant someone guessed. Every quantity the game can
> measure is a **posterior** that moves from real outcomes, and updating it costs
> nothing — so the experience *is* the training.

That is not a slogan here. Measured on this machine: **5.5 µs per observation**,
**~1.6 KB of state per parameter**, no GPU, no replay buffer, no batch step.

---

## 1. Why conjugates instead of gradient descent

An update is a handful of float adds against sufficient statistics. There is no
training run to pay for, and no way to "forget" by training on a bad batch — the
posterior is a summary of everything observed, with exponential forgetting
expressed in **days**, not steps.

| quantity | posterior | state | updated by |
| --- | --- | --- | --- |
| drop rate | Beta(α, β) | 2 floats + n | every farm: drops / kills |
| kills per minute | Gamma(count, minutes) | 2 floats + n | every farm: kills / session length |
| any duration | log-normal (n, Σlog x, Σlog²x) | 3 floats + n | travel, craft, buy |
| market price | log-normal + level prior | 3 floats + n | every `hdv.scan` |
| strategy correction | Normal over log-ratio | 3 floats + n | every completed step |

Recency is handled by scaling the sufficient statistics by
`0.5 ** (Δt / half_life)` before each update, so week-old experience fades out of
a world where prices move and level bands shift. That is the only learning rate
in the system, and it is expressed in days:

| parameter | half-life | why |
| --- | --- | --- |
| market price | 3 days | listings churn constantly |
| kills/minute | 45 days | gear and level change slowly |
| drop rate | 180 days | game constants, effectively fixed |
| strategy correction | 21 days | meta and routes shift with patches |

Cold start is handled by shrinkage: the estimate is
`(n · observed + k · prior) / (n + k)`, so with no data you get the prior and
with lots of data you get the measurement. Nothing is ever undefined, and nothing
claims more confidence than it has — `confidence()` is reported next to every
number the planner prints.

---

## 2. What must never be learned online

Three things stay hard-coded, because an online update on them means an
irreversible action can teach the wrong lesson:

1. **Feasibility** — a level-1 character cannot fight a level-200 monster. This is
   a gate, not a cost penalty.
2. **Safety** — `safe.stop`, spend limits, session caps. Never a learned policy.
3. **The tool schema** — 21 tools (see `CURRENT.md` §7), typed args. The model does
   not get to invent one, however much evidence it thinks it has.

Everything else is fair game.

---

## 3. Time estimation is a learned correction, not a formula

The planner's deterministic estimate is systematically wrong in ways the cache
cannot express: farming takes longer than the model thinks (walking, respawns,
deaths), crafting is faster when the ingredients are already in the bag. So the
ranker (`planning/bandit.py`) learns, per (level band, strategy):

```
ratio             = log(observed_minutes / predicted_minutes)
correction        = exp(posterior mean of ratio)
corrected_minutes = predicted * correction
```

One number per arm does **calibration**. Thompson sampling over its *uncertainty*
does **exploration** — an arm nobody has measured has a wide posterior, so it
occasionally wins the draw and gets measured. Both behaviours come from the same
three floats, and `choose()` is microseconds over ≤4 branches.

Three rails keep exploration from being expensive:

| rail | default | what it prevents |
| --- | --- | --- |
| `max_ratio` | 3.0 | an arm whose *corrected* estimate is 3x worse is never sampled |
| `exploration_ratio` | 4.0 | a never-tried arm may be sampled only if its raw estimate is within 4x of the best — the price of finding out, paid once |
| `min_samples` | 5 | an arm is not trusted below 5 observations, so it keeps getting trials; without this, a systematically-biased branch can never collect the evidence to correct itself |

The third rail is the one that matters most, and it was found by testing: with
only `max_ratio`, a branch the model was 3x wrong about was filtered away forever
and never learned.

---

## 4. Proof: two closed loops

`python -m planning.learn_demo` runs a hidden world whose physics differ from the
priors, feeds every outcome back, and reports what the agent learned.

### A. Strategy learning — discover the cheap craft

The model thinks crafting is 3x worse than buying. In truth the ingredients are
already owned, so crafting is 4x cheaper.

```
before : buy   368.6m raw   alts {'craft': 1098.3}
after  : craft 1098.3m raw, 279.7m calibrated   alts {'buy': 368.6}
-> strategy flipped from buy to craft
-> settled on the truly-best arm (craft) from episode 52 onward

    window    hit    regret  err(corr)  err(raw)
  first 20    10%      84.6       0.93      0.91
   last 20   100%       0.0       1.03      0.26

corrections learned vs truth:
  craft  learned x 0.26   truth x 0.25   converged
```

`err(raw)` is what the model would still be saying without experience: off by
4x. `err(corr)` is the calibrated estimate: off by 3%.

### B. Parameter learning — the drop rate was a lie

The model assumes a 6.25% drop rate; the truth is 2%. Every farm estimate is
silently 3x too optimistic until the posterior moves.

```
before : farm  90.7m raw   alts {'buy': 11.1}
after  : buy   27.8m raw, 27.7m calibrated   alts {'farm': 702.3}
-> strategy flipped from farm to buy
-> settled on the truly-best arm (buy) from episode 5 onward

    window    hit    regret  err(corr)  err(raw)
  first 20    80%      74.8       1.21      1.41
   last 20   100%       0.0       1.02      1.02

drop rate  0.0625 (prior) -> 0.0286 -> 0.0230 (truth 0.0200)
farm estimate 90.7m -> 298.7m as the drop rate corrected
```

Note what actually changed: not just the choice, but the **honesty of the
number**. The farm estimate moved 90.7 → 298.7 minutes because the drop rate
prior was wrong by 3.1x, and the planner stopped recommending a farm that was
never going to pay off.

---

## 5. Two speeds, and why most experience costs no training

This is the answer to "smart enough to not cost us the training":

```
fast loop   per observation, online, O(1), 5.5 us
            posteriors + ranker corrections
            fixes ESTIMATION error  -> free, forever, no GPU

slow loop   per N episodes, offline, batched, ~2 h on a T4
            encoder fine-tune on real trajectories
            fixes LANGUAGE error    -> only when the language actually changed
```

Most errors a bot makes are estimation errors: the plan was right, the number was
wrong. Those are fixed by the fast loop at zero cost. The slow loop is only for
**language** errors — a phrasing the encoder has never seen, a new tool, a new
language pattern — and those accumulate slowly.

Trigger the slow loop on evidence, not on a schedule:

| trigger | condition | why |
| --- | --- | --- |
| volume | ≥500 verified new episodes since the last checkpoint | enough new phrasing to matter |
| drift | `gold` call accuracy drops ≥3 points on a fresh sample | the world changed |
| capability | a new tool or language was added | the encoder cannot know it |

Guardrails for the slow loop, because retraining is the one place you can lose
ground:

* **replay ratio** — mix the `core_sft_v4` corpus in at ≥50%; never train on only
  recent data (catastrophic forgetting is the default outcome).
* **gate before shipping** — the new checkpoint must beat the old one on `val`
  (frame-held-out) *and* `gold`, and must not lose more than 2 points after INT8.
* **versioned checkpoints** — keep the previous one; rollback is one file copy.

The cost accounting: the fast loop runs on every observation for free, so the
slow loop only has to run when the *language* genuinely changed — which is rare.
That is why "the experience is the training" is not a compromise here; it is the
cheaper path.

---

## 6. Data hygiene — simulated outcomes must never teach the model

An early version of this system let a `--simulate` run write fake farm results
(200 kills, 0 drops) into the production experience log. That would have taught a
false drop rate for the rest of the bot's life.

Every event now carries a `source`, and the store accepts only `{"live", "manual"}`
by default:

```python
Event(kind="beta", key="drop:31631", value=0.02, weight=900, source="live")
```

* simulated and test events are counted in `store.rejected_events`, not applied;
* the append-only log (`data/kb/experience.jsonl`) is the **source of truth**;
  `store.replay()` rebuilds every posterior from it, so a corrupted snapshot is
  recoverable and every estimate is auditable down to the observation that moved it;
* `store.summary()` prints every parameter with mean, p05/p95 and sample count —
  "why does the bot think this farm is 300 minutes" has an answer.

---

## 7. What the executor must report

The learning loop is only as good as the outcome data. Each tool result should
carry these fields (the ones the planner can already use):

| field | used for | example |
| --- | --- | --- |
| `ok` | replan trigger | `true` |
| `source` | accept/reject the observation | `"live"` |
| `data.kills` | drop rate + throughput | `47` |
| `data.drops` | drop rate | `2` |
| `data.minutes` | throughput, correction | `18.4` |
| `data.kamas` | realised kamas/minute | `2150` |
| `data.price` + `item_id` | market price posterior | `41200` |

`ScheduleManager.report()` is the single place that turns a result into
posteriors — there is exactly one learning path, so there is exactly one place to
audit.

---

## 8. Measured costs

| | value |
| --- | --- |
| update cost | **5.5 µs** per observation |
| state per parameter | ~1.6 KB (a few floats + object overhead) |
| a realistic economy (≈600 params) | **~1 MB** |
| planning stack incl. store | 6.0 MB |
| plan latency (warm) | 1.6 ms p50 |

Against the 150 MB agent budget this is noise. The interesting comparison is with
the alternative: retraining the encoder on every session costs ~2 h of T4 and
risks forgetting, to fix errors that are almost always estimation errors — which
the fast loop fixes in microseconds.

---

## 9. Gates

1. **G1** — 100 live farm sessions; drop-rate posteriors move off the prior and
   `kills_per_minute` posterior confidence exceeds 0.5 for the three main farms.
2. **G2** — the ranker's corrections on `val` episodes reduce mean absolute
   log-error of the time estimate by ≥30% versus the raw model.
3. **G3** — `store.rejected_events` stays at 0 in production (proof that no
   simulated data leaked in).
4. **G4** — the slow loop runs at most once per 500 episodes, and the new
   checkpoint beats the old one on both `val` and `gold` before shipping.
