# Architectural reassessment — what the evidence actually says

Scope: the tool-policy encoder, the planner, the experience/learning layer, and the  
harness contract. Method: re-derive every claim from a measurement taken on this  
repo, not from the design intent. Nothing here restarts the architecture; the  
conclusion of most sections is *keep it, but fix the specific defect named*.

Constraint honoured: **do not restart, do not replace components without evidence,  
do not agree with a proposal merely because it was proposed.**

> **Revision 2.** Sections §0, §A, §C, §D, §E, §F, §G, §I and §J have been updated  
> against measurements taken after the corpus regeneration. Where a first-draft  
> number is withdrawn it is said so in place rather than deleted, because the  
> reason it was wrong is the load-bearing lesson: **the 52.4% headline was a  
> strict raw-dict comparator reading a gold-label convention, not a model  
> capability** (§0.1–0.2), and the post-processor described as "net negative" is  
> net **+2.4 pp** once re-scoped (§G). Every number below now names the corpus  
> generation and the comparator it was measured under.

---

## 0. The measurement that reframes everything — and what has changed since

Before any architectural argument, the number has to be honest. **A number quoted  
without its corpus generation and its comparator is not a number.** The first draft  
of this section quoted one of each; both have since been decomposed, and the  
decomposition changes the conclusion. This section records the original  
measurement, the three corrections, and what survives.

### 0.1 The original measurement, kept for the record

Run v8 (`kaggle_outputs/encoder30/model.pt`, predictions in `eval_val.json`) on the  
3,290-case `val` split of the **first** v4 corpus — preserved at  
`data/core_sft_v4.bak`, because it no longer exists in `data/core_sft_v4`:

| metric                                  | value                     |
| --------------------------------------- | ------------------------- |
| tool accuracy                           | 3,041 / 3,290 = **92.4%** |
| call accuracy, **strict raw dict**      | 1,725 / 3,290 = **52.4%** |
| call accuracy, exact on canonical forms | 1,861 / 3,290 = 56.6%     |
| call accuracy, release gate             | 1,986 / 3,290 = 60.4%     |
| unsolvable by construction              | 621 / 3,290 = **18.9%**   |

Two corrections to the first draft, both reproducible with  
`python -m ai.diag_call_budget --eval <report> --split val --corpus <dir>`:

- the headline was **52.4% under a strict raw-dict comparator**, not under the  
  release gate. Quoted next to §I.3's 54.5% (the same predictions, a different  
  comparator) the two looked like a disagreement; they are two readings of one run.
- the unsolvable count is **947 (28.8%)** under the original two-field audit — 713  
  `inv.sell` rows whose `quantity` is absent from the goal plus 234 `quest.advance`  
  rows whose `quest_id` is — and **621 (18.9%)** under the current audit, which  
  canonicalises both sides and lets a resolver map a name. The 978/29.7% figure in  
  the first draft is not reproducible from the corpus it cites and is withdrawn.

What survives unchanged, because it is a property of the *generator* and not of any  
scoring choice: **the frame-level split put the "number written" frames in train and  
the "number absent" frames in val for fr/de `inv.sell`** (train: 94.2% of fr rows  
carry the quantity in the goal; val: 3.1%). The split is doing its job. The  
generator was not.

### 0.2 The same predictions, three comparators — the 49-point gap is not leniency

`ai/diag_call_budget.py` scores one prediction set three ways, because they answer  
different questions and reporting one of them misleads. On gen-2's 3,056-row val  
(`data/core_sft_v4.v8meas`), the *same* v8 predictions:

| comparator               | raw                       | + gazetteer + KB (serving) |
| ------------------------ | ------------------------- | -------------------------- |
| strict raw dict          | 1,045 / 3,056 = **34.2%** | 1,118 / 3,056 = **36.6%**  |
| exact on canonical forms | 2,551 / 3,056 = **83.5%** | 2,624 / 3,056 = **85.9%**  |
| release gate             | 2,624 / 3,056 = **85.9%** | 2,697 / 3,056 = **88.3%**  |

- **artefact (exact − strict) = +49.3 pp, and it is not leniency.** gen-1  
  materialised every optional field's default into the gold label and trained the  
  model to reproduce it; gen-2 writes the canonical form, which is what  
  `validate_call` re-derives at execution time. A raw-dict comparator therefore  
  punishes the model for correctly answering "absent". The artefact is +4.1 pp on  
  gen-1 and +49.3 pp on gen-2 — *the model did not change, the label-writing  
  convention did.*
- **leniency (gate − exact) = +2.4 pp.** That is the containment-either-way rule  
  for text arguments, and it is what lets a truncated span  
  ("Eklat de Kroktail la Desalt") score as correct. It is the only part of the gap  
  that is genuinely the comparator being generous.

### 0.3 What the corpus regeneration actually bought — and what it did not

Same checkpoint, same predictions, two corpora, release gate:

|                            | gen-1 val (3,290) | gen-2 val (3,056) |
| -------------------------- | ----------------- | ----------------- |
| gate, raw                  | 60.4%             | 85.9%             |
| gate, serving              | 69.3%             | 88.3%             |
| gate, **solvable subset**  | 85.5%             | 88.3%             |
| unsolvable by construction | 621 (18.9%)       | **0 (0%)**        |

**+25.5 points, from a corpus change and a post-processor, with no retraining.**  
Two things follow, and the second is the uncomfortable one:

1. The gain is real but it is **not skill**. On the solvable subset v8 was already  
   at 85.5%; regenerating the corpus moved the *all-cases* number towards it by  
   deleting the rows that could not be learned. §I.1's target of "<5% unsolvable"  
   is met by **dropping unlearnable labels in `sanitize_labels` rule 2**, not by  
   making them learnable. That is the right call — keeping them teaches the model  
   to invent values — but a 0% unsolvable share is a property of the filter, and  
   reporting it as model quality would be the same mistake in a new coat.
2. **The distribution moved with it.** `inv.sell` went from 953 val rows (29% of  
   the split) to 20 (0.65%); `quest.advance` went from 578 to 734. v8's gen-1  
   numbers and v9's gen-3 numbers are therefore *not* comparable as a headline,  
   which is why §I.3 below reports both.

### 0.4 Consequence for every number below

The 52.4% headline is 49 points of label-writing convention on top of an 83.5%  
model. Any plan that targets "raise call accuracy from 52% to 85%" is targeting a  
number that moves when the corpus is rewritten correctly and does not move when the  
model improves. **The number to move is the solvable-subset gate score, and the  
number to watch is the unsolvable share.**

---

## A. Reconstructed existing plan

What is actually built and measured, in dependency order:

| layer         | component                                                     | measured                                                               |
| ------------- | ------------------------------------------------------------- | ---------------------------------------------------------------------- |
| knowledge     | `planning/kb.py` compact typed-array KB                       | 5.3 MB resident vs 121.5 MB JSON (23×)                                 |
| planning      | `planning/planner.py` AND/OR search, minutes cost model       | 6.0 MB, 1.6 ms/plan, `MAX_NODES=3000`                                  |
| economy       | `planning/economy.py` Beta/Gamma/log-normal/Normal posteriors | 5.5 µs/update, ~1.6 KB/param                                           |
| strategy      | `planning/bandit.py` `StrategyRanker`, Thompson + UCB rails   | `max_ratio`/`exploration_ratio`/`min_samples`                          |
| memory        | `planning/graph_memory.py`, `thinking_cache.py`, `trace.py`   | structure-only cache, before/after audit                               |
| actions       | `data/capabilities.json`, 21 typed tools                      | actions as data; description-derived anchors                           |
| policy NN     | `ai/encoder_policy.py`, 30M encoder                           | gen-2 val 94.7% tool / 88.3% gate call                                 |
| deterministic | `ai/postprocess.py`, `ai/gazetteer.py`, `planning/kb.py`      | **net +2.4 pp** on gen-2, **+9.0 pp** on gen-1; no per-tool regression |
| serving       | `ai/encoder_serve.py` `EncoderToolPolicy`                     | stateless per call, torch or ONNX                                      |

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

| per-instance state                      | size          | why it must be per-instance                      |
| --------------------------------------- | ------------- | ------------------------------------------------ |
| `ExperienceStore` posteriors            | ~1.6 KB/param | two agents farm different monsters               |
| `DecisionGraph` edges + `blocked_below` | small         | one agent's failures are not the other's         |
| `StrategyRanker` log-ratios             | small         | Thompson state is per (band, strategy)           |
| `ThinkingCache` plan structures         | bounded       | plans are priced against that agent's posteriors |
| `TraceLog`                              | append-only   | audit spine is per-agent                         |

Shared and read-only: the weights, the tokenizer, `capabilities.json`, the KB.  
This is the current design. **No change required.**

---

## C. Persistent quest objectives

**Recommendation: quest objectives live in `ScheduleManager`, never in the NN.**

The evidence is unambiguous: on gen-1 `quest.advance` was 47.1% call, and of its  
578 cases, 234 had the id nowhere in the text. A model that must remember "I am on  
step 3 of quest 1935" across turns cannot do it through a stateless encoder whose  
training data contains no quest state at all.

The split that works, and is already implemented:


- `ScheduleManager` holds objectives as first-class state with prerequisites and  
  a minutes-based cost model. It answers "what is the smartest route to a level-200  
  item from level 1" — farm the best kamas/minute item and buy it, or clear the  
  quest prerequisites and level up — which is a search problem, not a  
  sequence-modelling problem.
- The NN's job per turn is one atomic call: `quest.advance {quest_id: 1935}`,  
  where the id is **supplied by the schedule**, not read from the goal.
- `planning/graph_memory.py` records `blocked_below` after 4 failures under 25%  
  success, so a quest that keeps failing is pruned from future plans instead of  
  retried forever.

**Change required: none to the planner. One change to the contract** — the  
runtime must pass the active objective into the policy as structured context  
(see §G on where that context is allowed to go), so `quest.advance` becomes  
"confirm the step the schedule already chose" rather than "guess an id from  
prose". **Still open**, and it is the one item on this list that no amount of  
corpus work substitutes for: §D's resolver reads an id the *player named*, and  
that is a different problem from remembering an id the *agent chose* three turns  
ago.

---

## D. Quest IDs and internal action representations

**Recommendation: stop training the model to emit `quest_id` (and any other  
opaque numeric key) as free text. Make it an entity-reference slot resolved  
deterministically. This is the highest-leverage single change in the document.**

**Status: implemented.** `planning/kb.py::quests_named_in` /  
`resolve_quest_reference` and `ai/postprocess.py::resolve_quest_id` now do exactly  
this, and the same function serves the corpus builder and the serving path — see  
§0.2 and §G for what it moved.

Evidence:

- 2,937 train `quest.advance` rows → 2,126 distinct ids, 1,546 appearing exactly  
  once. Val: 578 rows, 578 distinct goals, 553 distinct ids.
- Only 40% of train and 60% of val goals contain any digit.
- Of the val cases where the digit *is* present, the model reads only a fragment:  
  `3368 → 33`, `1899 → 18`, `5054 → 505`. 24 cases. The arg decoder cannot emit a  
  4-digit integer reliably.

So there are two independent failure modes and only one is a model defect:

1. **Unsolvable** (234 val cases on gen-1) — no id in the text, name→id is random. No  
   amount of training fixes this. The label is noise.
2. **Truncation** (21 of the 344 id-bearing val cases on gen-2) — a real decoder  
   defect: `807 → 80`, `5340 → 53`, `3183 → 318`. Small, and a symptom of asking a  
   span decoder to produce a 4-digit number at all.

The fix is the same for both and it is not a training change: **`quest_id`  
becomes a reference, not a literal.** The NN emits a *quest name span* (which it  
can already do — `hdv.scan`/`hdv.list` item spans work at 89%) and the  
deterministic layer resolves name → id through the gazetteer, then the KB, then  
the schedule's active objective. That is exactly the pipeline `item` already  
uses, and it moves the unsolvable family from "noise" to "learnable".

The direction matters, and the first implementation had it backwards. Stripping  
the lead verb and the quest noun off the goal tail — `quest_tail()` — recovers  
**263 of 695** name-bearing supplement rows, because the quest noun sits  
mid-sentence in half the templates (`mach mit Quest X weiter`, `fais l'étape
suivante de la quête X`) and the names themselves contain commas. Scanning the  
goal for *known KB names* instead — the reverse containment — recovers **657**,  
because it never has to find the boundary first. Longest match wins and ties are  
refused: when the goal says "Rose à lys", the quest literally named "Rose à lys"  
is the one it means, and a goal naming a quest the KB lists 34 times  
("Merry Kwismas") resolves to nothing rather than to a guess.

Truncation gets the same treatment as a truncated span, for the same reason: the  
digits are *in the goal*, so restoring them is not a guess. Measured on gen-2, the  
repair (`clean_call` step 5b) takes `quest.advance` from 88.4% to 94.5% and leaves  
every other tool unchanged. It carries three guards, and the first measurement  
without them is the cautionary tale: rewriting `min_stock: 1 → 1698` from an  
unrelated quest id in the goal cost 115 `inv.sell` rows, because **a value sitting  
at its schema default is the canonical "not stated"** and the comparator forgives  
exactly that. A value the goal actually states ("niveau 5 à 55" → 5) is likewise  
never touched, and two candidate extensions is a refusal.

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

**Status: implemented.** `agent/schema.canonicalize_args` drops optional fields  
sitting at their default, and `agent/tool_eval.score_one` canonicalises *both*  
sides before comparing. This is worth far more than the first draft claimed and for  
a different reason: measured on gen-2 it is worth **+49.3 pp** (§0.2), not the  
"roughly 146 val cases, about 4.4 points" estimated from the solvable subset  
alone. The first draft was reading a default-representation artefact as a scoring  
defect and pricing it as though it were small.

The protocol is already minimal: 16 capabilities across 10 verbs  
(`nav`×2, `quest`×2, `farm`×2, `fight`×2, `inv`×2, `hdv`×2, `knowledge`, `chat`,  
`state`, `safe`), each with declared param types, validated by  
`agent/schema.validate_call`. Adding tools is a data append plus an anchor  
re-encode — verified 16→17 with no retraining.

The residual scoring leniency is small and should stay visible rather than be  
engineered away. The release gate accepts containment either way for text  
arguments, which is what lets a truncated span score correct; measured on gen-2 it  
is worth **+2.4 pp**. That is the honest price of not requiring the model to emit  
a character-perfect 40-character item name, and it is reported separately in  
`ai/diag_call_budget.py` as `leniency (gate - exact)` precisely so it cannot be  
quietly widened.

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

**One defect to fix, not a tier to add: the span trimmer cuts mid-word.**  
`Éklat de Kroktail la Désaltérante → Éklat de Kroktail la Désalt`,  
`IVOIRE Marqueur Aerafal → VOIRE Marqueur`. 36 of the solvable `hdv.list` +  
`hdv.scan` failures are exactly this. The trimmer must snap to a gazetteer  
boundary or a token boundary, never to a character budget.

**Status: implemented.** The gazetteer's lookups now match through  
`_bounded_match`, which checks the characters either side of a candidate by hand  
(`\b` is unreliable across apostrophes, hyphens and accents) and takes the  
**longest** bounded surface form rather than the first — so "Parchemin de Vaelor"  
resolves on the full name rather than on "Parchemin", and the item surface `tin`  
no longer hijacks "glimmerroot potion". The truncation repair in `clean_call` is  
restricted to a *strict prefix* of a grounded form, because the corpus contains  
prefix-colliding synthetic names ("SkiNi Shard" and "SkiNi Shard 89" are two  
different items) and a gazetteer hit shorter than the model's span is a different  
entity, not a correction — overwriting there turned 34 correct calls into wrong  
ones across `hdv.list`/`inv.sell`/`nav.goto`.

---

## G. What to train vs keep deterministic

**Recommendation: the current split is right; the deterministic layer is  
mis-scoped, not under-built. Re-scope it before adding anything to it.**

**Status: re-scoped and re-measured. The post-processor is now net positive.**

Measured on the same predictions, gen-2 val, release gate: **85.9% raw → 88.3%  
with the gazetteer and the KB attached (+2.4 pp)**, and on gen-1 val **60.4% →  
69.3% (+9.0 pp)**. Per tool on gen-2 the deltas are `quest.advance` +6.1 pp,  
`safe.stop` +27.6 pp, and **every other tool ≥ 0** — the earlier "net −0.06 pp,  
actively harmful on `farm.start`" reading is withdrawn. What changed is the  
scoping below, not the ambition.

It looks *less* valuable on the new corpus than on the old one, and that is the  
corpus's doing, not the layer's: gen-2 deleted the rows where the resolver did  
most of its work (§0.3). At serving time the agent still meets goals with  
quantities and quest names in them, so the +9.0 pp on gen-1 is the closer estimate  
of production value.

The rule it must follow, derived from the failure classes:

| field class                                                | owner                                | evidence                                                              |
| ---------------------------------------------------------- | ------------------------------------ | --------------------------------------------------------------------- |
| tool selection                                             | **NN**                               | 92.4% overall; the failures are class imbalance, not capacity         |
| enum choice (`kind`, `price_rule`, `channel`)              | **NN**                               | weak (`inv.list` 5/35, `safe.stop` 5/29) but genuinely linguistic     |
| entity span (`item`, `query`, `name`, `reason`)            | **NN proposes, deterministic trims** | span decode works; trimming is the bug                                |
| opaque key (`quest_id`, `map_id`, `zaap`, `npc`)           | **deterministic resolves**           | §D — 234 unsolvable + 24 truncated                                    |
| number the player said (`quantity`, `price`, `kill_count`) | **deterministic parses**             | model truncates 4-digit ids, copies quantity into `min_stock`/`price` |
| number the player did *not* say                            | **schema default, never a guess**    | 744 unsolvable cases prove guessing loses                             |
| schema coercion / validation                               | **deterministic**                    | already correct in `validate_call`                                    |


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

- `observation` is `null` in 1,611/1,611 records. There is no record of what was  
  on screen when the call was made.
- No click-level supervision exists in any split. The corpus is goal → call, not  
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

Every benchmark run reports these numbers, not one — and every one of them carries  
the corpus generation and the comparator it was measured under, because §0 showed  
that omitting either makes two readings of one run look like a disagreement:

```
tool accuracy                 (unchanged definition)
call accuracy   all cases     strict raw dict   <- the harshest reading
call accuracy   all cases     exact on canonical forms
call accuracy   all cases     release gate      <- the comparable headline
call accuracy   solvable subset, same three comparators
unsolvable share              (must go DOWN over time, not up)
comparator leniency           (gate - exact)    <- must stay small and visible
comparator artefact           (exact - strict)  <- a gold-label property, not skill
```

`ai/diag_call_budget.py` emits exactly this table. On the current corpus the  
solvable subset and all cases are the *same set* — the builder drops unlearnable  
rows — so that separation is vacuous for gen-2/gen-3 and remains meaningful only  
for gen-1 and for the gold split. Saying so is part of the report.

The unsolvable share is a *corpus health* metric. It is **0% on gen-2 and gen-3  
val, and 18.9% on gen-1**, and it must never be allowed to rise — a rising  
unsolvable share means the frame split is drifting away from the training  
distribution again. But 0% is achieved by dropping unlearnable labels, not by  
making them learnable (§0.3), so it certifies the *filter*, not the model.

### I.2 Ordered work list, each with a measurable gate

| # | change                                                                                                                                                        | gate                                                       | status                                                                                                         |
| - | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| 1 | Regenerate corpus: canonical arg dicts (§E), `quantity` only when the player said it, consistent quest name↔id (§D)                                           | unsolvable share 18.9% → 0%                                | **done** — gen-2/gen-3, 0/3,056 and 0/3,073                                                                    |
| 2 | Re-scope post-processor: fill-only, never-add, refuse-not-guess (§G)                                                                                          | post-process Δ ≥ 0 on every tool, no per-tool regression   | **done** — +2.4 pp gen-2, +9.0 pp gen-1, no per-tool regression                                                |
| 3 | `farm.start` key canonicalisation (`resource` vs `monster`, no `monster:""` ghosts)                                                                           | `farm.start` call 14.6% → >70%                             | **not done** — still 14.6% gate on gen-1, 46.7% on gen-2. Needs the label regenerated, not the model retrained |
| 4 | `quest_id` as entity reference, not literal (§D)                                                                                                              | `quest.advance` call → >85% on solvable                    | **done** — 88.4% → 94.5% on gen-2 with the resolver and the truncation repair; 97.2% on gen-1                  |
| 5 | Gazetteer boundary snapping for span trim (§F)                                                                                                                | `hdv.list`+`hdv.scan` item errors −36 cases                | **done** — `_bounded_match` with longest-match and prefix-only repair                                          |
| 6 | Class-imbalance fix for the never-selected tools (`knowledge.search` 0/60, `farm.stop` 11/22, `nav.follow` 23/44, `fight.pass` 20/25, `state.snapshot` 16/20) | per-tool tool accuracy floor ≥80% for tools with ≥20 cases | **open** — the highest-value remaining training-side item                                                      |
| 7 | Fill `observation` in the harness (PIPELINE.md §1.1)                                                                                                          | ≥1,000 records with non-null observation                   | **open** — null in 1,611/1,611 records                                                                         |
| 8 | *Only then* click-accuracy instrumentation                                                                                                                    | click validity measurable                                  | blocked on 7                                                                                                   |

### I.3 Baselines that must not regress

`ai/release_gate.py` already runs majority / guard / rule / model / onnx across  
splits and per language. The bar from run v8, which must hold or improve:

| split                   | model tool / call | guard tool / call |
| ----------------------- | ----------------- | ----------------- |
| val_indist              | 96.1 / 69.2       | 71.1 / 13.6       |
| val (gen-1, 3,290 rows) | 92.4 / 54.5       | 73.2 / 56.3       |
| gold                    | 78.9 / 57.8       | —                 |

**These are gen-1 numbers and the val row no longer exists.** v8's predictions,  
re-scored on gen-2's 3,056-row val with the same release gate, give **94.7% tool /  
85.9% call raw and 88.3% with the resolvers attached**. That is the honest v8  
baseline for the regenerated corpus, and it is the number v9 has to beat — not  
54.5%, which is a different corpus read through a different comparator. Reporting  
the improvement as "54.5% → 88%" would be claiming a corpus rewrite as a model  
gain; §0.3 exists to make that impossible to do by accident.

And the two properties that make the model safe to ship at 90 MB:

- **Confidence separation ≥ 0.35** (measured 0.3805) — a wrong answer is  
  detectably less confident, so the gate can refuse.
- **Per-language floor** — no language below 60% call on the solvable subset.  
  On gen-2 with the resolvers attached, per-language gate call is  
  de 91.8% / es 97.0% / fr 89.3% / en 74.2% / auto 87.2% — English is the floor,  
  and it is low because of `knowledge.search` (1/60 tool) and `farm.start`, not  
  because of English.

### I.4 What is explicitly *not* being validated yet, and must not be claimed

- Multi-turn behaviour. The model has never seen a non-empty `observation`.
- Click validity. No supervision exists.
- Any planner number. The planner is measured at 1.6 ms/plan and 6.0 MB; it has  
  not been evaluated against real play, only against the demo corpus.

---

## J. Minimal changes — the actual diff

Everything above reduces to five edits, none of which touches the architecture.  
Four of the five are done; the fifth is the one that is still open.

| # | edit                                                                                                                                              | status                                                                    |
| - | ------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| 1 | corpus generator — canonical arg dicts; `quantity` only when the player said it; one quest name ↔ one id, derived from the KB rather than sampled | **done** — `ai/prepare_encoder_sft.py`, gen-2/gen-3                       |
| 2 | `ai/postprocess.py` — fill-only, never-add, refuse-not-guess; gazetteer-boundary span trimming; truncated-number repair                           | **done** — net +2.4 pp on gen-2, no per-tool regression                   |
| 3 | `agent/tool_eval.py` — canonicalising comparator that ignores optional fields left at their default                                               | **done** — `score_one` canonicalises both sides                           |
| 4 | `ai/bench_encoder.py` — report the solvable subset and the unsolvable share alongside the existing metrics                                        | **done** — `ai/diag_call_budget.py` emits the full three-comparator table |
| 5 | `agent/recorder.py` — populate `observation`                                                                                                      | **open** — null in 1,611/1,611 records; gates §H                          |

No new model. No new layer. No restart. The work is to make the corpus stop lying  
about what the model knows — and, now that it has stopped, to stop the *report*  
from lying in the other direction by quoting a corpus-generation gain as a model  
gain.
