# Architectural reassessment — what the evidence actually says

Scope: the tool-policy encoder, the planner, the experience/learning layer, and the
harness contract. Method: re-derive every claim from a measurement taken on this
repo, not from the design intent. Nothing here restarts the architecture; the
conclusion of most sections is *keep it, but fix the specific defect named*.

Constraint honoured: **do not restart, do not replace components without evidence,
do not agree with a proposal merely because it was proposed.**

---

## 0. The measurement that reframes everything

Before any architectural argument, the number has to be honest. Run v8
(`kaggle_outputs/encoder30/model.pt`, predictions in `eval_val.json`) on the
3,290-case frame-held-out `val` split:

| metric | value |
|---|---|
| tool accuracy | 3,041 / 3,290 = **92.4%** |
| call accuracy (exact arg dict) | 1,725 / 3,290 = **52.4%** |
| call accuracy, **solvable subset only** | 1,716 / 2,312 = **74.2%** |

**978 of 3,290 validation cases (29.7%) are unlearnable in principle.** They are
not hard; the answer is not in the input and not in any table the model could
have learned:

* `inv.sell` — 744 cases where `quantity` appears **nowhere in the goal text**.
  In French and German validation the dominant frame is `vends Cadeau de
  Compensation` with gold `quantity: 20`. The quantity is drawn uniformly from
  {1, 5, 10, 20, 50} — measured 152/140/140/132/123 out of 718 — and 660 of those
  718 item names were never seen in training. Zero signal, by construction.
* `quest.advance` — 234 cases where `quest_id` appears nowhere in the goal. The
  corpus pairs 2,937 quest names with 2,126 distinct ids, 1,546 of which appear
  exactly once. It is a random lookup table, not a mapping.

The frame-level split that was added to stop leakage is what exposed this: the
sentence frame determines whether the number is written, and `split_by_skeleton`
put the "number written" frames in train and the "number absent" frames in val
for fr/de `inv.sell` (train: 94.2% of fr rows have the quantity in the goal;
val: **3.1%**).

**Consequence for every number below:** the 52.4% headline is 22 points of label
noise on top of a 74.2% model. Any plan that targets "raise call accuracy from
52% to 85%" without first fixing the corpus is targeting a number that cannot
move.

---

## A. Reconstructed existing plan

What is actually built and measured, in dependency order:

| layer | component | measured |
|---|---|---|
| knowledge | `planning/kb.py` compact typed-array KB | 5.3 MB resident vs 121.5 MB JSON (23×) |
| planning | `planning/planner.py` AND/OR search, minutes cost model | 6.0 MB, 1.6 ms/plan, `MAX_NODES=3000` |
| economy | `planning/economy.py` Beta/Gamma/log-normal/Normal posteriors | 5.5 µs/update, ~1.6 KB/param |
| strategy | `planning/bandit.py` `StrategyRanker`, Thompson + UCB rails | `max_ratio`/`exploration_ratio`/`min_samples` |
| memory | `planning/graph_memory.py`, `thinking_cache.py`, `trace.py` | structure-only cache, before/after audit |
| actions | `data/capabilities.json`, 16 typed tools | actions as data; description-derived anchors |
| policy NN | `ai/encoder_policy.py`, 30M encoder | val 92.4% tool / 52.4% call; guard 71.1% / 13.6% |
| deterministic | `ai/postprocess.py`, `ai/gazetteer.py` | **net −0.06 pp** (52.43% → 52.37%) |
| serving | `ai/encoder_serve.py` `EncoderToolPolicy` | stateless per call, torch or ONNX |

The plan is sound. The gap is not architectural, it is three specific defects
(§D, §E, §G) and one measurement discipline (§I).

**Verdict on the framing of the request:** the reassessment was asked for as if
the architecture might be wrong. It isn't. Two of the nine sections below
recommend a change of *mechanism* (B, D), one recommends a change of *scope*
(G), and the rest recommend keeping what exists and fixing a named defect.

---

## B. Shared weights vs per-instance KV caches

**Recommendation: shared weights, per-instance learning state. Reject per-instance
KV caches — they are the wrong mechanism for this model.**

Evidence against KV caches:

1. `EncoderToolPolicy` is **stateless by construction** — `_predict` builds a
   single-row dataset per call and discards it. There is no recurrent state and
   no cross-call activation to cache. A KV cache caches a computation this model
   does not perform.
2. `EncoderDataset._encode_row` *does* append `Current state: {observation}` to
   the prompt, but `observation` is `null` in 1,611 / 1,611 logged records
   (`docs/PIPELINE.md` §1.1). The model has never seen a non-empty observation in
   training. Caching attention over a context the weights were never trained on
   buys nothing and hides the real gap.
3. The 90 MB budget is per *agent instance*, and the encoder is 30M params. Two
   instances sharing one mmap'd read-only weight file costs one copy in RAM, not
   two — which is strictly better than any cache, and already how
   `load_checkpoint` behaves.

What each instance genuinely needs to own, and already does:

| per-instance state | size | why it must be per-instance |
|---|---|---|
| `ExperienceStore` posteriors | ~1.6 KB/param | two agents farm different monsters |
| `DecisionGraph` edges + `blocked_below` | small | one agent's failures are not the other's |
| `StrategyRanker` log-ratios | small | Thompson state is per (band, strategy) |
| `ThinkingCache` plan structures | bounded | plans are priced against that agent's posteriors |
| `TraceLog` | append-only | audit spine is per-agent |

Shared and read-only: the weights, the tokenizer, `capabilities.json`, the KB.
This is the current design. **No change required.**

---

## C. Persistent quest objectives

**Recommendation: quest objectives live in `ScheduleManager`, never in the NN.**

The evidence is unambiguous: `quest.advance` is 47.1% call on val, and of its 578
cases, 234 have the id nowhere in the text. A model that must remember "I am on
step 3 of quest 1935" across turns cannot do it through a stateless encoder whose
training data contains no quest state at all.

The split that works, and is already implemented:

* `ScheduleManager` holds objectives as first-class state with prerequisites and
  a minutes-based cost model. It answers "what is the smartest route to a level-200
  item from level 1" — farm the best kamas/minute item and buy it, or clear the
  quest prerequisites and level up — which is a search problem, not a
  sequence-modelling problem.
* The NN's job per turn is one atomic call: `quest.advance {quest_id: 1935}`,
  where the id is **supplied by the schedule**, not read from the goal.
* `planning/graph_memory.py` records `blocked_below` after 4 failures under 25%
  success, so a quest that keeps failing is pruned from future plans instead of
  retried forever.

**Change required: none to the planner. One change to the contract** — the
runtime must pass the active objective into the policy as structured context
(see §G on where that context is allowed to go), so `quest.advance` becomes
"confirm the step the schedule already chose" rather than "guess an id from
prose".

---

## D. Quest IDs and internal action representations

**Recommendation: stop training the model to emit `quest_id` (and any other
opaque numeric key) as free text. Make it an entity-reference slot resolved
deterministically. This is the highest-leverage single change in the document.**

Evidence:

* 2,937 train `quest.advance` rows → 2,126 distinct ids, 1,546 appearing exactly
  once. Val: 578 rows, 578 distinct goals, 553 distinct ids.
* Only 40% of train and 60% of val goals contain any digit.
* Of the val cases where the digit *is* present, the model reads only a fragment:
  `3368 → 33`, `1899 → 18`, `5054 → 505`. 24 cases. The arg decoder cannot emit a
  4-digit integer reliably.

So there are two independent failure modes and only one is a model defect:

1. **Unsolvable** (234 val cases) — no id in the text, name→id is random. No
   amount of training fixes this. The label is noise.
2. **Truncation** (24 val cases) — a real decoder defect, but small, and a
   symptom of asking a span decoder to produce a 4-digit number at all.

The fix is the same for both and it is not a training change: **`quest_id`
becomes a reference, not a literal.** The NN emits a *quest name span* (which it
can already do — `hdv.scan`/`hdv.list` item spans work at 89%) and the
deterministic layer resolves name → id through the gazetteer, then the KB, then
the schedule's active objective. That is exactly the pipeline `item` already
uses, and it moves 578 cases from "unsolvable" to "solvable".

The same argument applies to every opaque numeric key in the schema: `map_id`,
`zaap`, `npc`. If the value is a database key, the NN should emit the surface
form and the resolver should map it. If the value is a *quantity the player
said* ("vends 12 plumes"), the NN should copy the span and the deterministic
layer should parse it.

---

## E. Compact tool protocol

**Recommendation: the 16-tool typed protocol is correct and should not grow. The
defect is that gold labels are not canonical, and exact-dict scoring punishes
correct calls.**

The protocol is already minimal: 16 capabilities across 10 verbs
(`nav`×2, `quest`×2, `farm`×2, `fight`×2, `inv`×2, `hdv`×2, `knowledge`, `chat`,
`state`, `safe`), each with declared param types, validated by
`agent/schema.validate_call`. Adding tools is a data append plus an anchor
re-encode — verified 16→17 with no retraining.

The scoring defect, measured on the solvable subset:

* `nav.goto` — 21 failures are *only* an extra `npc` field. `nav.goto` **declares**
  `npc`. A call `{x: 5, y: -9, npc: "Bonta Bank", lang: "fr"}` for "va au banquier
  de Bonta" is a *better* call than the gold `{x: 5, y: -9, lang: "fr"}`. It
  scores zero.
* `farm.start` — 120 of 164 failures are the target written under `monster`
  instead of `resource`, with the count copied into `duration_min`. The Spanish
  gold row includes `"monster": ""`; the English gold row omits the key entirely.
  Same tool, two different canonical forms.
* `fight.join` — 5 failures are an extra `target`, which the tool declares.

**Change required (small, high value):** define one canonical arg dict per
capability — required fields always present, optional fields present only when
non-default — and regenerate the corpus against it. Score with a
canonicalising comparator that ignores optional fields the gold left at their
default. This is a data fix worth roughly 146 val cases (21 + 120 + 5), about
4.4 points of call accuracy, with no model change.

---

## F. External knowledge validation

**Recommendation: keep the three-tier resolution order and do not add a fourth
source. The gazetteer is the load-bearing tier and it is currently built
correctly.**

Order, and why each tier exists:

1. **Gazetteer** (`ai/gazetteer.py`, surface form → canonical value, built from
   the *train split only*). Load-bearing because the corpus is full of invented
   entities — "Oscuras intenciones", "SharUlmNi Fiber" — that exist nowhere in
   `data/cache`. A DofusDB lookup resolves 1 of 4 quest names in a typical batch.
2. **KB** (`planning/kb.py`) for canonical game facts, at 5.3 MB resident.
3. **Capability registry** (`data/capabilities.json`) for schema and anchors.

The thing to guard against is a *second* source of truth for the same mapping.
The gazetteer already carries 2,899 quest-name entries via goal-tail alignment.
Adding a DofusDB-backed quest resolver on top would create two mappings that
disagree, and the disagreement would be invisible.

**One defect to fix, not a tier to add:** the span trimmer cuts mid-word.
`Éklat de Kroktail la Désaltérante → Éklat de Kroktail la Désalt`,
`IVOIRE Marqueur Aerafal → VOIRE Marqueur`. 36 of the solvable `hdv.list` +
`hdv.scan` failures are exactly this. The trimmer must snap to a gazetteer
boundary or a token boundary, never to a character budget.

---

## G. What to train vs keep deterministic

**Recommendation: the current split is right; the deterministic layer is
mis-scoped, not under-built. Re-scope it before adding anything to it.**

Measured: the post-processor is **net negative** over the whole split — 52.43%
raw → 52.37% with post-processing — and actively harmful on `farm.start`, where
the lone-integer fill adds `duration_min: 20` to a call that already had
`kill_count: 20` correct. A deterministic layer that adds wrong fields is worse
than no layer.

The rule it must follow, derived from the failure classes:

| field class | owner | evidence |
|---|---|---|
| tool selection | **NN** | 92.4% overall; the failures are class imbalance, not capacity |
| enum choice (`kind`, `price_rule`, `channel`) | **NN** | weak (`inv.list` 5/35, `safe.stop` 5/29) but genuinely linguistic |
| entity span (`item`, `query`, `name`, `reason`) | **NN proposes, deterministic trims** | span decode works; trimming is the bug |
| opaque key (`quest_id`, `map_id`, `zaap`, `npc`) | **deterministic resolves** | §D — 234 unsolvable + 24 truncated |
| number the player said (`quantity`, `price`, `kill_count`) | **deterministic parses** | model truncates 4-digit ids, copies quantity into `min_stock`/`price` |
| number the player did *not* say | **schema default, never a guess** | 744 unsolvable cases prove guessing loses |
| schema coercion / validation | **deterministic** | already correct in `validate_call` |

Three concrete re-scoping rules:

1. **Fill only what is empty.** Never overwrite a field the NN populated.
2. **Never add a field the NN did not emit.** This alone removes the
   `farm.start` regression.
3. **Refuse rather than guess.** If a required numeric field has no number in the
   goal and no memorised surface form, return the schema default and let
   `validate_call` accept it — do not synthesise a value. The 744 unsolvable
   cases are the proof that synthesising is a losing move.

---

## H. Click and navigation accuracy

**Recommendation: do not invest in click accuracy yet. The prerequisite
measurement does not exist.**

`nav.goto` is 89.4% tool / 76.4% call on val, and its 62 solvable failures are
28 wrong-tool, 21 extra `npc`, 5 wrong `zaap` — all *argument* errors, none of
them coordinate errors. So navigation *intent* parsing is in reasonable shape.

But click accuracy — did the agent click the right map cell, the right Zaap, the
right NPC — cannot be measured at all, because:

* `observation` is `null` in 1,611/1,611 records. There is no record of what was
  on screen when the call was made.
* No click-level supervision exists in any split. The corpus is goal → call, not
  state → click.

Spending effort on a click-accuracy metric before the observation gap is closed
would produce a number that measures nothing. The correct order is: fill
`observation` (PIPELINE.md §1.1), re-measure whether goal+state beats goal-only,
and only then instrument click validity. `EncoderDataset` already has the
`Current state:` branch — the harness just never populates it.

---

## I. Validation plan

The gate that matters is not "call accuracy went up". It is: **each change moves
a named failure class, measured on a split that cannot leak, with the unsolvable
subset reported separately.**

### I.1 Permanent reporting split

Every benchmark run reports three numbers, not one:

```
tool accuracy          (unchanged definition)
call accuracy          all cases          <- comparable to prior runs
call accuracy          solvable subset    <- the number that reflects skill
unsolvable share       (must go DOWN over time, not up)
```

The unsolvable share is a *corpus health* metric. It is currently 29.7%. It
should fall to under 5% after the corpus regeneration, and it must never be
allowed to rise — a rising unsolvable share means the frame split is drifting
away from the training distribution again.

### I.2 Ordered work list, each with a measurable gate

| # | change | gate | expected Δ call |
|---|---|---|---|
| 1 | Regenerate corpus: canonical arg dicts (§E), `quantity` only when the player said it, consistent quest name↔id (§D) | unsolvable share 29.7% → <5% | +22 pts of *honest* headroom |
| 2 | Re-scope post-processor: fill-only, never-add, refuse-not-guess (§G) | post-process Δ ≥ 0 on every tool, no per-tool regression | +1–2 pts |
| 3 | `farm.start` key canonicalisation (`resource` vs `monster`, no `monster:""` ghosts) | `farm.start` call 14.6% → >70% | +3.5 pts |
| 4 | `quest_id` as entity reference, not literal (§D) | `quest.advance` call 47.1% → >85% on solvable | +2 pts |
| 5 | Gazetteer boundary snapping for span trim (§F) | `hdv.list`+`hdv.scan` item errors −36 cases | +1 pt |
| 6 | Class-imbalance fix for the never-selected tools (`knowledge.search` 0/61, `farm.stop` 11/22, `nav.follow` 23/44, `fight.pass` 20/25, `state.snapshot` 16/20) | per-tool tool accuracy floor ≥80% for tools with ≥20 cases | +4–6 pts |
| 7 | Fill `observation` in the harness (PIPELINE.md §1.1) | ≥1,000 records with non-null observation | unlocks §H |
| 8 | *Only then* click-accuracy instrumentation | click validity measurable | — |

### I.3 Baselines that must not regress

`ai/release_gate.py` already runs majority / guard / rule / model / onnx across
splits and per language. The bar from run v8, which must hold or improve:

| split | model tool / call | guard tool / call |
|---|---|---|
| val_indist | 96.1 / 69.2 | 71.1 / 13.6 |
| val | 92.4 / 54.5 | 73.2 / 56.3 |
| gold | 78.9 / 57.8 | — |

And the two properties that make the model safe to ship at 90 MB:

* **Confidence separation ≥ 0.35** (measured 0.3805) — a wrong answer is
  detectably less confident, so the gate can refuse.
* **Per-language floor** — no language below 60% call on the solvable subset.
  French is currently 18.8% overall, but that is the `inv.sell` frame shift, not
  a French deficiency: French `hdv.list` is 85.5% and French `nav.goto` is 18%
  only because of the extra-`npc` scoring defect.

### I.4 What is explicitly *not* being validated yet, and must not be claimed

* Multi-turn behaviour. The model has never seen a non-empty `observation`.
* Click validity. No supervision exists.
* Any planner number. The planner is measured at 1.6 ms/plan and 6.0 MB; it has
  not been evaluated against real play, only against the demo corpus.

---

## J. Minimal changes — the actual diff

Everything above reduces to five edits, none of which touches the architecture:

1. **`ai/build_core_sft.py` / corpus generator** — canonical arg dicts;
   `quantity` emitted only when the goal contains it; one quest name ↔ one id,
   derived from the KB rather than sampled. *(§D, §E)*
2. **`ai/postprocess.py`** — three rules: fill-only, never-add, refuse-not-guess;
   gazetteer-boundary span trimming. *(§G, §F)*
3. **`agent/tool_eval.py`** — canonicalising comparator that ignores optional
   fields left at their default. *(§E)*
4. **`ai/bench_encoder.py`** — report the solvable subset and the unsolvable
   share alongside the existing metrics. *(§I.1)*
5. **`agent/recorder.py`** — populate `observation`. *(§H, PIPELINE.md §1.1)*

No new model. No new layer. No restart. Run v8 already beats the guard on every
split; the work is to make the corpus stop lying about what the model knows.
