# Pipeline contract — one job per stage, two learning curves

The rule this document enforces:

> Every stage has exactly one job. Nothing is learned from a payload, nothing is
> trained on an epoch, and no expensive reasoning is thrown away.

---

## 1. Audit — what the AI receives and what the logs actually hold

I read all 1,611 logged records (`trajectories/*.jsonl`) and the code that
produces and consumes them. Four defects, in order of impact:

### 1.1 `observation` is `null` in 100% of records

The recorder schema has a slot for state, `EncoderDataset` has a
`"Current state: ..."` branch, and `RulePolicy.choose(goal, observation, tools)`
accepts it. Nothing ever fills it:

```
trajectories/default.jsonl   10 steps,   observation: null  (10/10)
trajectories/collector.jsonl 440 steps,  observation: null  (440/440)
trajectories/synthetic.jsonl 1161 steps, observation: null  (1161/1161)
```

So the policy is **goal-only**. Any decision that should depend on context —
"pass" when it is my turn, "join" when a fight is open, "stop" when HP is low —
cannot be learned, because the input never contained the context.

### 1.2 `ok` conflates two different questions

`ok` currently means "the tool executed without an exception". It is `true` for
every one of the 1,611 records, including `quest.list` calls that returned 1,976
quests the agent did nothing with. The question the learner needs is "did the
action achieve its intent", which is a different field.

`planning/outcome.py` now makes that distinction explicit and returns
`verified` / `unverified` / `failed`. **Five of the nine actions in the demo are
`unverified` and correctly produce zero updates.**

### 1.3 `result` is raw payload, and the outcome fields do not exist

A single `quest.list` result carries up to 1,976 quest records with five
localisations and full step lists — tens of KB. Meanwhile the fields the learner
needs (`kills`, `drops`, `minutes`, `kamas`, `price`) appear in **zero** records,
because no farm action has ever been executed end to end.

So the log is simultaneously too big (raw payload) and too empty (no outcome).

### 1.4 The tool contract is defined three times

| where | length | used by |
| --- | --- | --- |
| `tool_dataset.SYSTEM_PROMPT` | 3,005 chars | decoder SFT (`--system full`) |
| `tool_dataset.SYSTEM_PROMPT_COMPACT` | 1,276 chars | decoder SFT (`--system compact`), the shipped 100M model |
| `encoder_policy.ENCODER_SYSTEM_PROMPT` | generated from `TOOLS` | the 30M encoder |

Three copies of the same contract is how a model ends up trained against a schema
the executor no longer has. One of them is generated from `agent/schema.py`
(good); the other two are hand-maintained (fragile).

### 1.5 A bug this audit found

`Economy.__init__` did `experience or ExperienceStore()`. Because
`ExperienceStore` defines `__len__`, an **empty** store is falsy — so the economy
silently created a second store and learning split across two instances: the
ranker wrote to one, the drop/kpm/price posteriors to the other. Fixed to
`if experience is not None`, with a comment so it does not come back.

That is precisely the "don't mix things up" failure mode: same type, same name,
two objects.

---

## 2. Stage contract

| # | stage | input | output | its one job | must NOT do |
| --- | --- | --- | --- | --- | --- |
| 0 | **harness** (`agent/`) | goal, observation | `ToolResult` | execute one validated call | learn anything |
| 1 | **recorder** (`agent/recorder.py`) | every event | `trajectories/*.jsonl` | raw, redacted, replayable log | interpret outcomes |
| 2 | **outcome** (`planning/outcome.py`) | `ToolResult` | `Outcome` | payload → lesson, or refuse | invent a signal |
| 3 | **experience** (`planning/experience.py`) | `Outcome` | posteriors | instant curve: one observation → one parameter | decide strategy |
| 4 | **graph memory** (`planning/graph_memory.py`) | `Trace` | edges, constraints | long-term curve: structure of the map | override feasibility |
| 5 | **trace** (`planning/trace.py`) | stages 2–4 | `traces.jsonl` | audit: what moved, by how much | be the training corpus |
| 6 | **ranker** (`planning/bandit.py`) | residuals | corrections | re-order feasible branches | add branches |
| 7 | **planner** (`planning/planner.py`) | goal, posteriors | `Plan` tree | search the AND/OR graph | estimate from raw data |
| 8 | **cache** (`planning/thinking_cache.py`) | plan structure | re-priced plan | reuse reasoning, never the estimate | cache a judgement |
| 9 | **scheduler** (`planning/schedule.py`) | plan + results | next action | sequence, detect drift, replan | own learning logic |
| 10 | **encoder** (`ai/encoder_policy.py`) | text | tool + args | language → contract | be trained online |

The rule that keeps this honest: **a stage may only read the output of the stage
before it.** The planner reads posteriors, never a raw `ToolResult`. The encoder
never sees a trace.

---

## 3. The two curves

```
action ──► ToolResult ──► Outcome ──┬──► posteriors        (instant: seconds)
                                     ├──► graph edges       (long-term: hours)
                                     └──► trace             (audit: forever)
```

### Instant — one action, one parameter

From the demo, a single farm session:

```
16 kills, no drop, 96 min   -> verified   residual 1.06
   drop:384          —  ->  0.0417   (n 0 -> 16,  drops / kills)
   kpm:101:1-20      —  ->  0.1753   (n 0 -> 96,  kills / minutes)
   rank:1-20:farm    —  ->  x1.02    (n 0 -> 1,   observed / predicted)
```

Cost: 5.5 µs. No batch, no epoch, no GPU. The `residual` (observed/predicted) is
the label, and the system generates it itself — this is the "logical self
feedback": the plan made a falsifiable claim, the world answered.

### Long-term — the map changes shape

The same actions accumulate into edges, and edges become structure:

```
attempt 1..4: failed
attempt 5:    blocked_below=38

constraints: [{"edge": "monster:47", "action": "farm", "blocked_below": 38,
               "success_rate": 0.222, "attempts": 5}]
planner now prunes it: True
```

A route that keeps failing stops being merely expensive and becomes *pruned below
level 38*. That is the dynamic graph the plan asked for: it can start complex,
collapse to something simple, or diverge from the static cache when a drop is
observed that the cache did not know about (`discover_drop`).

---

## 4. Expensive reasoning is never wasted

The cache stores the **structure** of a conclusion and re-prices it on every use:

```
attempt 1: buy 11.1m  nodes=  4  cache_hit=False   65.8 ms
attempt 2: buy 11.1m  nodes=  0  cache_hit=True     0.2 ms
attempt 3: buy 11.1m  nodes=  0  cache_hit=True     0.1 ms
cache: {"hit_rate": 0.75, "reasoning_ms_saved": 197.2}
```

Three safeguards, because a stale cache is worse than a slow one:

1. **only the structure is stored** — every estimate is recomputed from current
   posteriors on each hit, so a price change cannot be missed;
2. **the conclusion is re-validated** — if a re-priced alternative is now >5%
   cheaper, the entry is invalidated and a real search runs;
3. **the knowledge base is versioned** — if `data/kb/dofus_kb.npz` is rebuilt, the
   structure itself may be wrong and the whole cache is treated as stale.

The worst case of a bad recipe closure is 3,000 nodes; that is the loop worth
caching, and it is cached as a tree, not as a number.

---

## 5. "Training by logical self feedback", precisely

| | RLHF / NSTF | this |
| --- | --- | --- |
| label source | a human preference | the plan's own prediction vs the outcome |
| update unit | a batch of comparisons | one observation |
| cost per update | a training run | 5.5 µs |
| what it can fix | anything | estimation and structure, not language |
| what it cannot fix | — | unseen phrasing (that needs the slow loop) |

No epoch, no offline phase, no replay buffer for the fast loop. The slow loop
still exists for the one thing experience cannot teach: language. See
`docs/EXPERIENCE_LEARNING.md` §5 for its trigger and budget.

---

## 6. What production must pass

`ScheduleManager._ingest` is the single learning path. For it to work, each tool
result must carry the outcome fields — the harness side of this contract:

| field | where | needed by |
| --- | --- | --- |
| `ok` | `ToolResult.ok` | replan trigger |
| `source` | `ToolResult.source` | accept/reject learning (`live` only) |
| `data.kills`, `data.drops` | farm directive response | drop rate, throughput |
| `data.minutes` | farm directive response | throughput, residual |
| `data.kamas` | farm/sell response | kamas/minute |
| `data.price`, `item_id` | `hdv.scan` response | price posterior |
| `observation` | **currently never set** | state-conditioned decisions |

Two changes are worth doing before any of this matters in production:

1. **populate `observation`** in the recorder (position, HP, active fight, bag
   pressure) — without it, half the contract is a no-op;
2. **return counters from `farm.start`** — kills, drops, elapsed minutes. Today
   the directive ack carries no outcome, so a live farm teaches nothing.

Gates:

1. **G1** — 20 live farm sessions with `signal=verified` and non-zero `updates`.
2. **G2** — `store.rejected_events` stays 0 while simulated runs happen
   (proves the `source` filter holds).
3. **G3** — cache hit rate > 0.5 after a day, with zero invalidations caused by a
   wrong conclusion (only by KB rebuilds or TTL).
4. **G4** — at least one constraint learned from real failures, and the planner
   demonstrably prunes it.
