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

> **Measured 2026-11-02, this table's first row is not a baseline.** All 800 rows of `val_indist.jsonl` are
> byte-identical to rows of `train.jsonl` (`ai/prepare_encoder_sft.py:13` states the intent: a sample of train
> frames used as a memorisation check). 96.1 / 69.2 there measures recall. Re-run on the honest split with the
> repo's own scorer, the deployment encoder (`models/encoder30_v15/encoder_int8.onnx`) is **val 94.1 / 69.7
> (n=3,073, 0% leak)** and **gold 94.4 / 77.5 (n=71)**, with the connected rule policy at val 42.9 / 29.3 and
> gold 63.4 / 50.7. Also stale below: `data/core_sft_v4/val.jsonl` exists (3,073 rows), so "the val row no
> longer exists" is no longer true -- and `docs/benchmarks/eval_v8_v4val_full.json` covers only 304 of those ids, so the
> 94.7 / 85.9 figure cannot be re-derived on today's corpus at all.

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

## K. The first unseen-language holdout — 95 hand-labelled novel frames, three arms

Every number in §0–§J is measured on sentences the corpus generator produced. §I.4 says so, and the
capture instrument (`ai/utterance_capture.py`) confirmed the supply side is empty: 250k sampled
trajectory lines yield **zero** player messages, so there is still no real-player eval set. What now
exists is the honest stand-in: `data/utterances/novel_frames.jsonl` — 95 rows authored by hand, labelled
by construction, deliberately written in the register the alias tables were never built for
(`jai quoi a lhdv`, `was steht bei mir im ah`, `pon 5 lana a 2k`, `Verkaf meine bouftollwolle`,
`koi comme quete`), 34 en / 31 fr / 20 es / 20 de, all 16 registered tools. **Authored, not player
data** — it measures robustness to frames the generator never made, not to how people actually type.

10 of the 105 drafted rows were already verbatim in the SFT corpus (`pass`, `zaap astrub`,
`cherche bouftou`, …) and were dropped, and 1 more was a **void label**: `chat.say` with
`channel: "group"`, which is not in that parameter's enum. Five `chat.say` rows were then re-labelled to
carry the message text their sentence actually says. `ingest` now enforces all of it — unregistered tool,
arg outside the schema, value outside an enum, wrong type — because a void label depresses every arm
equally and the *ranking* still looks clean while the absolute numbers are junk. 12 rows are legitimately
under-specified (a required parameter the player never stated: `quest.advance` with no quest id,
`hdv.list` with no item); they are kept, counted, and they cap achievable accuracy for any system that
refuses to invent.

`python -X utf8 ai/utterance_capture.py benchmark` — one row set, one scorer (`agent.tool_eval.evaluate`),
three arms, rules scored through the live router (`rule_parse` delegates to `RulePolicy.choose`, args
included rather than thrown away):

| arm | size | tool | call | misses: abstain / wrong-tool / missing-arg / wrong-arg | per-language tool (de/en/es/fr) |
|---|---|---|---|---|---|
| connected rules | 40 KB of aliases | **28.4** | **21.1** | 64 / 4 / 7 / 0 | 15.8 / 29.0 / 29.4 / 35.7 |
| learned lexical policy | 15 MB | **63.2** | **46.3** | 23 / 12 / 16 / 0 | 63.2 / 74.2 / 58.8 / 53.6 |
| 30M ONNX encoder | 25 MB | **49.5** | **34.7** | 0 / 48 / 8 / 6 | 36.8 / 58.1 / 52.9 / 46.4 |

Marginal accuracies on 95 rows carry a ~20 pt single-arm floor, so the report pairs them: McNemar exact on
the discordant rows (validated against `scipy.stats.binomtest`, and it can fail — 5/5 discordance returns
1.0). rules vs policy **1 / 25, p=8.0e-07**; encoder vs rules 7 / 20, p=0.019; policy vs encoder **19 / 8,
p=0.052**. So the policy's lead over the incumbent is certified; its lead over the encoder is a 13.7 pt
direction that n=95 still cannot certify, and needs roughly 250 rows to do so.

The policy row is measured *after* one fix this holdout caused: its argument misses were numbers stated
outside any span (`100x`, `4,-19`, `2k`), shapes the corpus generator never emits, so `read_stated` now
fills them — fill-only, never overwriting a claim. Before it: 45.3 call / 17 missing-arg; after: 46.3 / 16.
`val` and `gold` did not move at all (**93.3/66.6** and **97.2/73.2**, byte-identical), which is the point:
the fix targets exactly what the generated corpus cannot test, and the holdout is where it shows.

What this decides, in the shape the NPL question was asked:

- the failure modes are **different diseases**, and the taxonomy is the finding. The incumbent goes silent
  (64 of its 67 misses are no-tool-at-all); the trained encoder never goes silent and is wrong a third of
  the time (48 wrong-tool misses) and is the **only arm that invents values** (6 wrong-arg). The lexical
  policy invents nothing on unseen language either — `wrong_arg: 0`, the same property the regression suite
  pins on the training distribution.
- 16 of the policy's misses are missing args, and **none** of them are the under-specified rows -- every one
  is a value the sentence does state. `geh zu 4,-19` chose `nav.goto` and lost both coordinates, and
  `farm bouftou 100x` lost its kill count: both are digits stated outside any span, which the corpus
  generator never emits, so `read_stated` now recovers them (fill-only, never overwriting a claim). What is
  left is a single class -- **entity names the gazetteer does not know** (`laine de bouftou`,
  `bouftollwolle`, `Bouftou`) -- which is coverage in a lookup, not capacity in a model, and the same rule
  the prompt layer already follows: resolution stays a lookup and a per-language coverage report comes
  first. The 12 under-specified rows go 5/12, and 7 of those failures never route at all: the abbreviated
  register (`jai quoi a lhdv`, `was steht bei mir im ah`) is a *routing* problem before it is an argument
  problem.
- The remaining routing gap is 12 wrong-tool rows: `quete` ↔ `quest.advance`/`quest.list`, `arrete le farm`
  → `farm.start`, `mes items en vente` → `inv.list`. Those are alias-table and training-frame gaps, again
  fixable inside the existing design.
- **nothing here argues for putting a 0.8B language model in this seat.** The encoder's advantage on the
  generated corpus (§0) does not survive contact with unseen frames; the cheapest arm wins, and the axis
  the LLM was supposed to buy — multilingual robustness — is exactly the axis where it lost.

### K.1 Why the lookup cannot say the item's name — coverage measured, not assumed

The holdout leaves 32 unrecovered *string* arguments. Asked of the two corpus-derived lookups (the policy's
own retrieval domains and `ai/gazetteer.py`), they split **24 with no entry, 2 resolving to a different
spelling, 7 where the expected value IS in the policy's retrieval domain and was still missed**. The last
class is an extractor bug and is worth fixing; the first is not a bug at all, it is a vocabulary mismatch:
`farm.start monster=Bouftou`, `resource=wood/bois/madera/holz`, `inv.sell item=laine de bouftou` are
*ordinary early-game Dofus words*, and the generated corpus never used them — `bouftou` does appear inside
22 gazetteer item surfaces and `wool` inside 501, but no entry for the compound a player actually types.
Worse, the two lookups do not share a field vocabulary: the gazetteer carries
`{item, monster, npc, query, reason, target, text, zaap, zone}` plus `map_id`, while the schema's entity
fields include `resource`, `area` and `kind` — so `lookup_name("resource", ...)` is structurally always
None, and no amount of seam-side cleverness changes that.

Consequence, stated as the rule it already is: entity resolution stays a **lookup**, and the lookup has to
be bound to the official catalogue with a **per-language coverage report** before anyone tunes a threshold
or adds a head. That report is the next measurement (`#52`), and the holdout is what asked for it — on
generated frames every lookup looks perfect (97.9 tool on `val`) because the lookup and the benchmark
share one vocabulary.

## L. What the installer actually shipped, and what it ships now (2026-11-03)

Nobody had read the artifact. `tools/payload_audit.py` now parses the central directory of the zip
embedded in `setup.exe` and reports the top-level byte ledger, so every claim about the installer
comes from the binary instead of from the staging source. `tools/payload_audit.py --gate` is the
enforcement half, and `agent/regression.py::test_installed_payload_and_dependencies` runs it, the
builder-source assertions and `tools/shipped_deps.py` as ten gate checks.

| measure | 2026-10-30 build | this build |
|---|---|---|
| `setup.exe` | 224,413,696 B (214.0 MiB) | **104,947,200<!--f:setup_bytes--> B** |
| payload files | 773 | **511<!--f:payload_files-->** |
| unpacked payload | 439.4 MB | **254,432,218<!--f:payload_unpacked--> B** |
| empty folder markers | 16 | **0** |
| credential-shaped entries | 4 files | **0** |
| Python distributions declared | 4 | **10** |

**The largest single item in the installer was bytes the product could not read.**
`models/combat_grid/model.pkl` is 108.8 MB compressed of JAX weights, and the interpreter that
ships in the payload (`runtimes/python`) has no jax wheel at all: measured, the development
interpreter imports jax 0.12.0.1 while the embedded one raises ModuleNotFoundError. The builder now
skips the artifact **conditionally on the shipped runtime** (`RuntimeHasJax()`), so if jax ever lands
in the embedded tree the ranker ships and works instead of silently staying cut.

**Two of the four real defects were stability, not size.**

* `agent/brain_daemon.py:289` passed `explore_objectives=` into `Autopilot.__init__`, whose parameter
  is `explore` -- so every instance start through the shipped daemon raised TypeError. The gate was
  green because every test in `agent/test_brain_daemon.py` replaces `_make_pilot` with a stub; nothing
  in the repo had ever constructed a real pilot. Pinned by a new test that does.
* `combat/grid_infer.py` imported jax at module level, and `agent/executor.py:1130` imports that
  module on the opt-in grid ranker path -- so an installed copy raised ImportError where the code
  intends a `grid_refused` refusal. Imports are lazy now and the refusal is returned; verified on
  both interpreters.
* `requirements.txt` is the installed app's only source of Python packages (site-packages never
  crosses the installer), and it listed four distributions while the import closure of the real
  entry points needs seven more. An installed copy therefore could not load the INT8 encoder
  (`onnxruntime`, `tokenizers`) or the lexical command policy (`scikit-learn`, `joblib`), and
  `requests` was missing for the collector paths. `tools/shipped_deps.py` computes the closure
  (174 modules) and keeps the guarded-but-optional imports out of the list, so a probe that has a
  ctypes fallback does not drag psutil into every install.
* the builder excluded `bin/warp/wgcf.exe` while the shipped `start-network.bat` tells the operator
  to run exactly that file to enrol a tunnel. It ships again (+5.7 MiB) because an instruction that
  cannot be followed is not a saving.

**The installer was carrying this machine's credentials.** Measured out of the built binary:
`accounts.txt` held **21 `login:password` pairs**, and `bin/warp/wireproxy.conf`,
`bin/warp/wireproxy.runtime.conf` and `wgcf-profile.conf` each held a **44-character WireGuard
PrivateKey**, with `wgcf-account.toml` carrying a WARP `access_token`, `license_key`, `private_key`
and `device_id`. `builder/Build.cs` now stages a comment-only `accounts.txt` and a
`wireproxy.conf.template` with every secret field replaced by a placeholder, and keeps the four
key-bearing files out of the payload; `payload_audit --gate` fails an installer whose entry contents
match credential shapes, so this cannot come back quietly. **The repo's own copies were not touched**
-- they are this machine's working data -- but any installer built before today distributed those
values, so the WARP identity and the listed account passwords should be treated as exposed and
rotated. The repo files remain the operator's to delete or rotate; nothing here was removed.

Remaining byte levers, in order, with the measured cost: `runtimes/python_embed.zip` is a 10.7 MB
nested copy of the interpreter the payload already ships unpacked (it is `installer.cs:1043`'s
repair path, so removing it means giving up that recovery); `data/cache/items_full.json` is 23.5 MB
raw / 6.2 MB shipped and the runtime does read it (`agent/features.py:208`, `agent/planner.py:145`),
so it needs a compact form rather than deletion; the two INT8 graphs are 41.9 MB of the 50.1 MB
models line and are the point of the payload. And `docs/CURRENT.md`/`README.md` no longer claim a
"213.8 MB standalone WebView2 runtime": `bin/webview2` is a 1.7 MB bootstrapper, and the bytes that
claim was hiding were the JAX weights above.

### L.1 The tree is edited under a newer Python than the one it ships

`runtimes/python` is **Python 3.11.9**; the interpreter that runs the tests here is 3.14. Two
consequences, both found by tools that did not exist until now (`tools/ship_compile.py`, run by the
embedded interpreter, and `tools/embed_imports.py`, which imports the whole shipped closure under
it -- both wired into `test_installed_payload_and_dependencies`):

* `ai/diag_call_budget.py:352` was a **SyntaxError on the installed path**: a string literal
  continued across a line break inside an f-string expression, which PEP 701 allows from 3.12 and
  3.11 rejects. It parsed fine in every check the repo had ever run, because every check ran under
  3.14. Fixed by hoisting the expression into a local; the sweep now compiles **214 shipped files
  under 3.11.9 with zero failures**.
* **155 of the 167 closure modules import cleanly under the shipped interpreter.** The eight that do
  not are all `combat.*` JAX models (`board_pca, grid_dataset, grid_model, grid_synth, grid_train,
  live_policy, plan_benchmark, plan_value`), recorded in `JAX_ONLY` rather than hidden: the embedded
  runtime has no jax wheel, and jaxlib is not something to slip into `requirements.txt` quietly --
  the gate's job is to keep that list from growing unnoticed.

What that list still costs the product, and what now happens instead of a crash:
`agent/executor.py` probes the `combat.plan_value` import before ranking and reports
`value_runtime_missing` (the same refusal channel as `checkpoint_missing`), `combat/grid_infer.py`
loads jax lazily and returns `None` so the grid ranker reports `grid_checkpoint_missing`, and
`combat/combat_ai_server.py` still imports `combat.live_policy` at module level -- which is the
documented reason the host cannot start the learned fight brain on port 8891 from the embedded
interpreter (docs/CURRENT.md §0) and the page's own heuristic answers instead. Closing that last one
means a decision the measurements cannot make: ship jax, or rewrite the two fight models to run on
the ONNX Runtime the payload already carries. Both are open; neither is silent any more.

### L.2 The learned fight brain was unstartable on the installed copy, and now it is

`combat/combat_ai_server.py` imported `combat.live_policy` at module level. Because the payload
carries no jax wheel, that meant the process could not even come up on an installed copy -- and every
handler in it already treats `POLICY is None` as "solver only" (`/health` answers `status: loading`),
so the only thing missing was a lazy import. Fixed: the server now starts under
`runtimes/python/python.exe` and answers `/health`, and its `backend` field described what had
actually loaded (`solver-only (jax absent)`) instead of claiming `jax` while nothing of the sort was
loaded. §L.7 supersedes the last part: the policy now loads on numpy, so the honest answer on an
installed copy is `backend=numpy`, and the string for a copy with *no* array library at all is
`solver-only (no array backend installed)`.

This is pinned by two checks in `test_installed_payload_and_dependencies`: the brain is launched on
**the interpreter that ships**, on a spare port, and must answer; and `tools/embed_imports.py` sweeps
the whole closure under it (now 156 modules, 8 recorded `JAX_ONLY` gaps). Both failed before the fix
for the right reason -- the first because the process died, the second because `combat.combat_ai_server`
raised ModuleNotFoundError.

What is still open is a product decision, not a bug: the PPO/PBT actor and the win-value ranker
(`combat/live_policy.py`, `combat/plan_value.py`) are JAX models, so installed copies answer fights with
the deterministic solver until either jax ships or those two are exported to the ONNX Runtime the payload
already carries. `docs/CURRENT.md` §0 already recorded the cause; the difference is that the service now
starts, refuses honestly, and is checked by the gate.

### L.3 What `data/cache` costs the installed brain, measured on the shipped interpreter

`tools/payload_audit.py` says the cache ships 8.8 MB compressed / 42.4 MB raw, so the question worth
asking is not only what it costs the installer but what it costs the process. Measured under
`runtimes/python/python.exe` (3.11.9) with the same `GetProcessMemoryInfo` counters the encoder probe
uses: the brain daemon idles at **21.6 MB** and the first `knowledge.search` for items takes it to
**108.7 MB (+87.1 MB, 0.54 s, 21,554 rows)**; monsters +0.1, quests +11.4, spells +1.9, npcs +7.9,
ending at **129.9 MB resident / 123.0 MB committed** for the shared process.

Field-by-field for the item kind (a probe held each piece alive to price it): parsing the JSON is
63.9 MB, normalized names plus the joined haystack 15.1 MB, the `_index_record` projections 20.7 MB,
the description tuples 22.7 MB. The existing design already spends the 20.7 MB of projections
*deliberately*, so the raw rows can be released (`agent/executor.py:138`), and it defers description
normalisation until a full-text query needs it and then strips them from the rows
(`_haystacks_for`). That is why the remaining cut is not "delete the cache" but #58's item 2: a
compact store per kind -- one UTF-8 blob plus int32 offsets, the shape `planning/kb.py` already uses
for items/monsters/quests at ~4 MB resident -- serving `knowledge.search` too, instead of two
representations of the same world.

Fixed while measuring: a kind with no entry in `KNOWLEDGE_FILES` resolved to `cache_dir` itself, and
because a directory satisfies `path.exists()`, the first such query logged `Unreadable cache file
...\data\cache: Permission denied` as though a data file were corrupt. It returns empty now, and
`test_installed_payload_and_dependencies` pins both that and the fact that a real item query answers.
Verified: identical search ids for seven queries across the development 3.14 and the shipped 3.11.9
interpreters.

Re-measured with the peak/commit pair and a forced `EmptyWorkingSet`, because a single resident number
conflates live objects with pages the heap has not returned (embedded 3.11.9, item kind, 21,554 rows):

| stage | resident | peak | commit |
|---|---|---|---|
| before the first query | 21.9 MB | 21.9 | 15.1 |
| after `entries()` | 109.0 MB | 132.1 | 102.5 |
| after the haystack is built | 128.3 MB | 132.1 | 121.7 |
| after `EmptyWorkingSet` | **0.7 MB** | 132.1 | 121.7 |
| after the index is released | 0.7 MB | — | **18.2 MB** |

Three things follow. The residency is genuinely the process's own (trimming the working set reclaims
127.6 MB, so it is not an accounting artifact), and releasing the index returns the commit to 18.2 MB,
so it is live rather than leaked. The searchable text at that point is `hay_chars = 16,162,650`
characters -- 16.2 MB -- against 121.7 MB committed, so **the cost is object overhead and the parse
transient, not the corpus**: a compact store cannot win back less than roughly 6x, and it takes the
132.1 MB build peak down to the ~25 MB the blob needs. And the same shape repeats per kind
(achievements +26.0 MB commit for 1.08 MB of text, npcs +29.6 for 0.47), which is why #58's item 2 is
worth doing once for every kind rather than tuning the item path alone. Dev 3.14 measures the same
story at +~3 MB of interpreter tax (110.9 → 132.5 resident, peak 160.5).

**Compacted the same day.** `entries()` now streams each cache array with
`agent/jsonio.iter_array` and folds every record's normalized text straight into **one UTF-8 byte
blob per kind** plus a row-offset array; `search()` matches with `blob.find(needle, start, end)`, so
there is no per-row haystack string and no description tuple left behind. On the shipped
interpreter the item kind went **122.3 → 74.5 MB committed** and **128.8 → 80.4 MB resident**; all
seven kinds together **162.5 → 135.4 MB committed**. The first step did not fix the *peak*, though:
with the file read as one 24 MB string the isolated item build peaked at **138.4 MB**, above the
**132.2 MB** the original `loads()` build cost. `iter_array` now reads in 1 MiB blocks and reassembles
records across the boundary, taking that build to **95.6 MB** and the full seven-kind index to
**175.9 → 141.6 MB** peak, **133.7 MB** committed. The block size is the point: a brain daemon that
indexes all seven kinds multiplies this by its instance count on a host that kills long processes
when free RAM drops to ~2.6-3.7 GB of 7.8 GB. Answers stayed identical -- all 16 baseline arms, on
the shipped interpreter -- and `test_the_array_reader_streams_without_losing_records` now reassembles
records and multi-byte characters at `chunk_bytes=1` (every record straddles the window), keeps the
whole rows of a torn tail, and refuses a non-array top level: the boundary is the only failure mode a
whole-file reader does not have. Answers were verified identical for
all 16 baseline arms on 3.11.9 and on 3.14.8 (`tools/knowledge_index_probe.py compare`), and a gate
check now diffs `knowledge.search` against the checked-in `data/knowledge_index_baseline.json`.
The trade to record: description normalisation is no longer deferred, so the first *name-only* item
query costs 1.53 s instead of 0.54 s; a full-text query costs what it always cost (0.54 + ~0.85 s),
because that work merely moved earlier.

**Found while pinning it: `quest.list` with an area argument could not match anything.** The filter
tested `area_norm not in haystack` against the entries' *description* slot, and the quest cache
carries zero description characters across all 1,976 rows -- so the test was a membership check
against an empty tuple and every quest was dropped whenever the area did not resolve to known
subareas. The tool's own fallback note ("showing quests whose name contains the text") described a
path that had never run. It reads the names field now, and two checks hold the premise (quests have
no descriptions; names are populated) and the shape.

### L.4 A killed rebuild used to truncate the corpus and keep the old manifest

`test_cross_file_counts_agree` failed on the run after these changes: `data/transitions_temporal`
held **4,766 lines against a manifest claiming 9,687**. The builder opened the three folds in `"w"`
mode -- which truncates them -- and only then serialised the rows, so any interruption after that
point left a half-written dataset beside the previous build's manifest. On this host the
interruption is not hypothetical: the OS kills long-running processes when free RAM drops to
~2.6-3.7 GB of 7.8 GB (resume.md, measured 2026-10-31 and again 2026-11-01), and the same gate that
now reports the lie had been producing it.

Fixed in `tools/build_live_transitions.py`: every fold is serialised in memory first, `stats["rows"]`
becomes the number of lines actually written and `rows_built` the number read, and the files are only
touched once the text exists. `tools/refresh_live_dataset.py` forwards both fields. Re-running the
builder restored `data/transitions_temporal` and `data/transitions` to 9,687 lines with matching
manifests.

The L.4 rewrite moved the line the mutation catalogue anchors
(`transition: the split is by row instead of by segment`), and the gate's anchor check failed loudly
rather than letting the mutation quietly stop existing. The anchor was re-pointed at the new
expression -- `folds[plan[row["segment"]]].append(` and its `split_of(row["id"])` mutant -- so the
segment-vs-row leakage mutation is still applied and still has to be caught, and the catalogue now
describes the code that exists.

**The same defect came back from the reader's side.** Serialising before writing protects a killed
build; it does not protect a *concurrent read*, because `write_text` still empties the target before
the new bytes arrive. Measured 2026-11-03 during this session's own gate run:
`test_cross_file_counts_agree` reported `(9687, 9295)` -- `train` plus `test`, with `val` caught
mid-write. The corpus was never actually wrong (it finished at 9,687 and still reads 9,687), but no
reader can be trusted while a rebuild is running, and the readers are not only the gate: the daemon's
ingest loop rebuilds this directory, and `--json` callers read it.

So the folds now land as `<name>.jsonl.tmp` siblings and swap in through
`os.replace`-equivalent renames, with `manifest.json` published last. That choice is not free on this
OS: Windows refuses a rename over a file another process has open, so the first version of the fix
raised `PermissionError` on `train.jsonl.tmp` **and left four `.tmp` files in the corpus directory**
-- a torn read traded for a crash. `_publish()` retries the swap eight times at 50 ms (a reader holds
one of these files for milliseconds) and the publish block unlinks every staged temp if it fails.

Proven with a two-arm probe rather than asserted: a poller read the three folds continuously while a
real rebuild ran (**367 reads, every one exactly 9,687 rows**), then the same poller watched a scratch
copy written the old in-place way (**19 of 19 reads torn**, totals 6,000 / 10,000 / 20,741 / 24,000).
The control arm is what makes the first arm's clean result mean anything. Two gate checks now hold
the line: the builder's publish path is rename-based (`def _publish(`, `tmp.replace(target)`, the
`.tmp` sibling), and the corpus directory contains no `*.tmp`.

### L.5 The last 10.7 MB, and the price of the jax option

`runtimes/python_embed.zip` is out of the payload: it is the same interpreter the archive already
carries unpacked (`runtimes/python`, 12.2 MB), i.e. a compressed zip stored inside a compressed zip --
**9.4% of setup.exe bought for a repair branch that could only fire if the unpacked copy failed**.
`desktop/installer.cs` now resolves that zip from *beside* `setup.exe` (still a repair path for an
operator who keeps one), and when `runtimes\python\python.exe` is missing after extraction it says so
loudly instead of leaving the installed AI silently dead: every Python service the app runs --
brain daemon, sentinel, combat brain, agent HTTP -- needs that one file. `payload_audit.FORBIDDEN`
now lists `python_embed.zip`, so it cannot creep back.

Measured artifact progression, all from the binary itself:
**224,413,696 B (214.0 MiB, 2026-10-30) → 118,907,904 B (113.4 MiB) → 107,699,712 B (102.7 MiB) →
104,937,984 B (100.1 MiB)**, 509 files, budget tightened from 130 MiB to 115 MiB and then to 105 MiB,
so a single large regression still trips the gate.

The jax decision from §L.1 turned out to be settled by PyPI rather than by taste, so the numbers are
recorded here: `jaxlib 0.12.0.1` publishes **no cp311 wheel at all** (win_amd64 exists only for
cp312/313/314/315), and the newest release with a `cp311 win_amd64` wheel is `0.9.2` at **59.9 MB**
(plus `jax` 3.3 MB and its dependencies) -- so "just add jax to requirements.txt" would grow the
102.7 MiB installer by more than half and pin the fight models to an eighteen-month-old jax. The
option that costs no bytes is exporting `combat/live_policy.py` and `combat/plan_value.py` to the
ONNX Runtime already in the payload, the way the tool encoder was; until then installed copies answer
fights with the deterministic solver and say `backend=solver-only (jax absent)`. **§L.7 closed this
without either option**: the models turned out to need nothing jax-specific, so they now run on the
numpy that is already a declared dependency -- no ONNX graph, no extra byte in the installer.

*(Re-opened and actually closed on 2026-11-03 in §L.24. "Nothing jax-specific" was true of the three
modules that were migrated and false of the one they import: `combat/encounter/env.py` still did
`import jax` at module scope, and jax's functional indexed update `x.at[idx].set/.add` was still used
in ten places including `action_masks`. So the claim held for the files that were looked at and an
installed copy still had no policy to load -- and no check could see it, because the shipped-runtime
sweep compiles files rather than importing them.)*

### L.6 The zero-reference tier, and the grep that finds it

`payload_audit` answers *how big* each entry is; it did not answer *whether anything reads it*. For
this pass every entry under `models/` and `data/` was scored by `grep -rl <stem> --include=*.py
dofus-network`, with the mirror and the writer itself excluded. Four files came back with **no reader
anywhere**: `models/cause_head.onnx` (2,095,330 B in-zip), `models/cause_head_int8.onnx` (564,754 B),
`models/verifier_head_int8.onnx` (14,418 B) and `models/veto_shadow/` (27 KB) -- the cause head's
runtime form is `models/cause_head.json` plus its sibling prototypes, both of which keep shipping, and
`agent/regression.py` already asserts the veto shadow is unwired (`"veto_shadow" not in autopilot`).
Alongside them went this box's dev residue: 90 `.ruff_cache` entries, 4 `.pytest_cache`, 
`.vscode/settings.json`, `dofus-network/.gitignore`, three zero-byte `client/www/staging-*` copies and
six one-off probe logs under `data/`. **2,761,728 B and 110 files off `setup.exe`, budget 115 → 105 MiB.**

Two things this pass caught that a size-only review would have shipped:

* The first rebuild still failed its own gate: `forbidden={'/staging-': 3}`. `SkipDevPayload` was wired
  into the `dofus-network` staging loops only, while the client bundle is staged by a separate loop that
  skipped nothing but `.bak`. Both loops now consult the same predicate -- the exclusion list is worth
  nothing if the builder has two copies of the truth.
* `test_cause_head_artifact` checked the ensemble's retrieval half with
  `metrics["prototypes_path"]`, which is whatever the training run recorded -- on this box
  `C:\Users\sao\Documents\dofus-emu\dofus-network\models\...`. An installed machine has that file beside
  the json, so the check failed on every install *while the payload carried the file*. It now resolves
  the recorded sibling name first and falls back to the recorded absolute path.

Left in on purpose, with the numbers: the two INT8 command graphs are 43,890,288 B in-zip -- 42% of the
installer -- and `command_encoder30/command_encoder_int8.onnx` has zero references by name anywhere in
the tree, so the grep alone indicts it. It stays: `agent/command_shadow.py` reaches it through
`command_onnx.json`, `tools/live_ready.py` probes that config, `payload_audit.REQUIRED` lists both
graphs, and `requirements.txt` declares `onnxruntime` + `tokenizers`, so unlike the 108.8 MB of JAX
weights in §L they load on the installed box. The same reasoning keeps ~123 KB of `ai/train_*` and
`tools/build_*` scripts: they are dev-side, but the installed self-test invokes tool paths and 0.1% of
the artifact is not worth a break.

### L.7 The learned fight policy now runs on an installed copy -- on numpy

§L.5 priced two ways to get the fight policy onto installed copies (ship jax, export to ONNX) and
neither was needed. Reading `combat/encounter/model.py` showed what the model actually asks of an
array library: dense matmul, layernorm, relu, masked softmax, `einsum`, `argmax`, `where`, `stack`,
`tanh`. numpy has every one of them, and `models/encounter_selfplay.pkl` -- already in the payload --
unpickles into **45 numpy float32 leaves, 811,520 bytes, with no jax import at all** (measured by
blocking `sys.modules["jax"]` and loading it). The only jax-specific thing in the whole path was
`jax.jit`, an optimisation, and `jax.tree_util.tree_map(jnp.asarray, ...)`, which converted arrays
that were already numpy arrays.

So `combat/encounter/backend.py` picks the library: jax when it is installed, numpy when it is not,
with `relu`/`softmax`/`log_softmax`/`rsqrt`/`jit`/`tree_map`/`random` behind it, and 121 jax-flavoured
call sites in `model.py`, `plan_value.py`, `live_policy.py` and `checkpoint.py` were re-pointed at
that one module. `DOFUS_FIGHT_BACKEND=numpy` forces the fallback even where jax exists, which is what
makes the claim testable on this box rather than asserted about another one.

Measured, same checkpoint, same fixture boards, `python -m combat.plan_benchmark`:

| backend | boards | lethal_available | report fields differing | elapsed |
|---|---|---|---|---|
| jax | 25 | 4 | — | 2.5 s |
| numpy | 25 | 4 | **0 of 45** (apart from `generated_at`, `elapsed_s`) | 6.0 s |

The honest costs: numpy has no compiled path, so a benchmark pass is **2.4x slower** (6.0 s vs 2.5 s),
and the repo's copy of `runtimes/python` still cannot import these modules -- not because of jax but
because site-packages never crosses the installer and that interpreter has no numpy until
`pip install -r requirements.txt` has run. That is why the gate checks the *behaviour*, not the
interpreter: `test_installed_payload_and_dependencies` now scores the fight benchmark once per backend
and fails if any reported field moves, asserts none of the four modules imports jax at module level
again (one stray line puts the whole policy back behind a wheel PyPI does not publish for cp311), and
the `/health` assertion was corrected from "ok means jax" to "ok means the backend it names".

### L.8 The install paid 617.7 MB for one optional service

> **Update 2026-11-07.** This section is the measurement that justified the tier
> split and is kept as history. Two of its premises have since changed: the
> Sentinel no longer imports torch (the THX-01 classifier was removed), and the
> `thx01` distribution was removed with the rest of that lane. The AI tier is now
> 617.4 MB and no shipped entry point needs it at import time.

`requirements.txt` carried ten distributions. Measured as installed footprint on this host:

| package | installed | who needs it |
|---|---|---|
| torch | 508.8 MB | `networkhealth/sentinel.py` only, at import time |
| transformers | 107.8 MB | training/export (`ai/train_*`, `ai/export_*`) |
| onnxruntime | 45.8 MB | the int8 graphs that ship in `models/` |
| scikit-learn | 40.9 MB | the lexical command policy at the `parse_command` seam |
| numpy | 31.4 MB | everything |
| tokenizers | 7.8 MB | those graphs' `tokenizer.json` |
| joblib | 1.9 MB | loads `models/lexical_policy/policy.joblib` |
| requests | 0.5 MB | `data/sync_full_database.py`, the brain's ingest job |
| safetensors | 0.8 MB | the sentinel's `model.safetensors` path |
| thx01 | 0.3 MB | `ai/thx_policy.py` (removed with the THX lane, 2026-11-07) |

Blocking `sys.modules['torch']` and importing `agent.commands`, `agent.executor`,
`agent.brain_daemon`, `agent.autopilot`, `agent.serve`, `combat.combat_ai_server`,
`ai.onnx_policy`, `ai.lexical_policy`, `run_brain`, `run_autopilot` succeeded -- **all ten**. Only
`networkhealth.sentinel` failed, and it fails because it builds `TinyRecursiveReasoner(nn.Module)`
and loads a checkpoint inside a class body, so it genuinely has no degraded mode. That is the whole
case for the split: `setup.exe` installs the six rows above (~128 MB) and the two it truly needs
nothing without are in `requirements-ai.txt` (617.7 MB), applied by `install-python-ai.bat`;
`start-sentinel.bat` preflights for torch and prints that instruction instead of a traceback. The
installer's pip step gets 90 s (`WaitForExit(90000)`), which was never enough to fetch half a
gigabyte on a shared line.

Finding the split needed a fix in `tools/shipped_deps.py` itself, and the bug was the instructive
part. It walked imports with `ast.walk`, which cannot tell `import torch` at module scope from one
inside a function, and it expanded a package import to **every** file in that package -- so
`import combat` was treated as importing all 30-odd combat modules, and torch looked unavoidable.
It now separates three tiers (eager / deferred / guarded) and resolves dotted targets to the file
Python would actually load, so `CORE_ROOTS` (everything but the sentinel) yields `numpy` eagerly and
nothing else, with the rest classified by the `RUNTIME_TIER_EXTRA` reasons table -- the successor to
the old "union, never a substitution" rule, same judgement, now written down per package. The
builder also stopped *retyping* `start-sentinel.bat` and `install-python-ai.bat` from inline strings,
which is why repo edits to those files had been silently failing to ship.

Six gate checks in `test_installed_payload_and_dependencies` hold it: no AI-tier name in
`requirements.txt`, the AI file names exactly the four, the torch-blocked import of all ten core
entry points as a subprocess, the sentinel's preflight text, the builder staging rather than writing
the launchers, and `tools/shipped_deps.py` reporting PASS on the tiers. 1808 checks, 0 failed.

### L.9 The 56% pass rate is a committed-turn artifact, not a policy failure (and a fix that was measured, wrong, and reverted)

Weight-space programming round two ended by pointing away from the weights. The side-neutral paired
duel (`tools/wsp_fight.py`, 40 matchups = 80 games, `DOFUS_FIGHT_BACKEND=numpy`) has the trained
action head at **0.1875 win rate against the deterministic solver -- 0 wins, 13 draws, 27 losses,
and 1 win in the 16 games it decided end to end without falling back** -- while reporting
`mean_probability 0.936`. The motivated edit (spell-head bias `-4,0,0,+4,0`) moved the metric
+0.0063 and the random sign-flip control moved it +0.0187, so at that sample size the harness cannot
credit an edit with anything; and `plan_benchmark` cannot see action-head edits at all, because
`rank_plans` reads only `forward()["value"]`. That is a verdict on the eval, not on the technique.
The behaviour was read from the brain's own telemetry instead.

`trajectories/live_policy_decisions.jsonl` holds **794 real logged turns** from the shipped 8891
server (56 fights, ~4 days; the 15 rows whose targets are the regression fixture fighters are
excluded). Action mix `pass` 447 / `cast` 254 / `move` 37 / nothing 72; reasons `policy_pass` 443,
`policy_direct` 219, `solver_fallback` 35, `policy_move` 30, `empty_spell_slot` 29, `solver_move` 7,
`no_legal_plan` 3; mean claimed probability 0.977, median 1.0; server latency p50 12.8 ms, p95
42.4 ms. So the passes are not the head being shy: **235 of 794 turns (29.6%) reach the policy with
four empty slots**, and "no spell" is then the only honest answer. The slots are emptied by one
field -- `_bind_slots` drops every spell whose `castable` flag is false, and the page fills that
flag with `myId !== null && !!fm.canCastThisSpell(myId, spellId)` inside a `try/except`
(`mobile-disguise.js:7868`), so "false" also means "asked at a moment the client will not cast".

The hypothesis was that the flag is wrong when it vetoes the *whole* spellbook: 175 of 794 turns do
that, 135 of them still had AP (111 at AP 3, 16 at AP 6) and no disabling state, and 109 of those
carry an in-range spell with computed damage in the same payload. A `Board` predicate for exactly
that contradiction, consulted by `battle_solver._templates`/`cast_ranges`/`fighter_spells` and
`live_policy._bind_slots`, was built and measured on the recorded corpus
(`tools/policy_over_record_boards.py --all-turns`, the same 294 boards, same fill hypothesis):
casts **30 -> 57**, passes **36 -> 16**, p50 latency unchanged at 2.1 ms. Binder-only was a relabel
(`policy_pass` 35 -> 15 into `no_legal_plan` 1 -> 21, action mix identical), which is why the
relaxation went as deep as legality.

**Then it was refuted, and reverted.** Splitting the same turns by whether the character had already
cast this turn (`spell_cast_counts` in the raw boards, `casts_this_turn` per spell in the
observations): of the 115 blanket-vetoed live turns with AP and no state, **113 had already cast**,
and only 2 were a fresh turn -- so the "contradiction" is the app consulting the brain *after its
own action is committed*, at which point Dofus genuinely stops offering spells. The recorded corpus
says the same thing: of 96 blanket boards, **none** is a fresh turn with AP left (44 are fresh at
AP 0 or unknown, 52 have a cast spent), and their `turn_started_ms_ago` is p50 1,911 ms against
6,451 ms on boards with at least one castable spell -- they are captured seconds into our own turn.
The relaxation therefore proposes a second cast on turns that have none left, and the client's
pre-click re-check (`mobile-disguise.js:2467`) turns each one into a local-planner fallback -- the
retry path whose cost is already documented at `mobile-disguise.js:2472`. Reverted in full: no
`castable_veto_untrusted`, no `blanket_veto`, `combat/` back to 92 passed, replay back to 30 casts /
36 passes. The two facts the experiment produced survive as the finding:

1. **~19% of live `/v1/combat/decide` requests are asked after the turn is settled** -- 113 with a
   cast already spent plus 40 at AP 0, of 794. Each costs a solve plus a policy forward (p50 12.8 ms
   in-server) and writes a `policy_pass` at probability ~1.0 into the log. Not asking is free, and
   it also stops the telemetry reading as "the brain declines half of every fight".
2. **The head is still the weaker player** (0.1875 vs the solver, 1 win in 16 fully-decided games),
   and that number now has to be re-measured on boards where it is asked *before* it has acted,
   because every measurement so far mixes in turns it could only pass.

`docs/CURRENT.md` says the same in one paragraph; the ask-side gate is task #66.

### L.10 The artifact shipped the file that measured itself, so its size could not be quoted

`tools/doc_facts.py` exists so no document retypes a number: the docs hold `{{fact}}` and the tool
materialises `value<!--f:name-->`. Two of those facts are about the installer itself
(`payload_files`, `payload_unpacked`), and `data/doc_facts_history.json` -- the history the tool
appends a record to on every `--render`/`--check` -- was inside the payload. So the thing being
quoted grew by the act of quoting it: render, rebuild, re-check moved `payload_unpacked`
254,462,587 -> 675 -> 689 -> 703 -> 717, +14 B per pass, and `--check` could not converge. The
render step itself is idempotent (a second `--render` with no build writes nothing), which is what
localized it to the file rather than to the writer.

`data/doc_facts_history.json` is now in `SkipDevPayload` in `builder/Build.cs` and in `FORBIDDEN` in
`tools/payload_audit.py`, so it cannot be smuggled back by a rename. `--check` reports "every tagged
number matches its measurement", the gate passes at **512 files / 104,956,928 B** (was 513 /
104,958,976), and 1808 checks, 0 failed. The forbidden pattern is proven to bite, not just to be
declared: `gate()` on a synthetic row list containing the name returns
`forbidden={'/data/doc_facts_history.json': 1}, ok=False`.

### L.11 47.8 MB of learned weights left the installer, and it took a wrong measurement to find two defects

`payload_audit --readers` reported one 22 MB entry with "no reader anywhere". It does have a reader
-- `agent/command_shadow.py` reaches `command_encoder_int8.onnx` through `command_onnx.json`, so the
stem grep cannot see it, and the file is in `REQUIRED` for that reason. Chasing why a required graph
looked unreadable produced the actual finding: the *tiers* were inconsistent with each other.

Measured before the change: `models/command_encoder30/command_encoder_int8.onnx` 21,999,494 B
in-zip, `models/encoder30_v15/encoder_int8.onnx` 21,890,794 B, `models/lexical_policy/policy.joblib`
3,662,354 B, `models/tool_policy.onnx` 183,599 B, `models/verifier_head.onnx` 48,480 B -- **47.8 MB
in-zip, 45.5% of a 100.1 MiB `setup.exe`**. Their consumers are all off by default: the live policy
stack runs `--policy rule`, `Autopilot(command_shadow="off")`, and the lexical command policy needs
`DOFUS_LEARNED_COMMAND_MODEL`. The owner's call was to drop them from the installer and move the
wheels that read them to the opt-in tier, so `requirements.txt` went from six distributions to two
(numpy 25 MB + numpy.libs 21 MB + requests et al = 47 MiB of wheels as measured in this repo's
embedded runtime, down from 128.3 MB) and `requirements-ai.txt` from four to eight (adding
onnxruntime 43 MB, scikit-learn 29 MB, tokenizers 7.9 MB, joblib 1.3 MB). `SkipDevPayload` drops the
five weights plus the small configs' big siblings, and `FORBIDDEN` names them so a rename cannot
smuggle them back; `command_onnx.json`, `policy.json` and `tokenizer.json` still ship, so an
operator who copies a weight in still resolves it. **`setup.exe` is now 57,171,968 B / 506 files
(54.5 MiB), and the budget has been cut from 105 MiB to 60 MiB** so the win is enforced rather than
noticed.

Two defects fell out of doing it with the instrument instead of by hand:

* `tools/shipped_deps.py` compared *distribution* names against *import* names. `AI_TIER` is written
  in import names, so `"sklearn"` never matched the distribution `scikit-learn`, and the first
  `--write` silently **dropped scikit-learn and joblib from the tier that exists to install them**
  while still printing PASS. Every set in `main()` is now mapped through `DIST` first
  (`ai_names`, `reached`, and the `smuggled` comparison). The regression check that hardcoded
  `("torch", "transformers", "safetensors", "thx01")` had the same disease one level up: a typed
  tuple asserts the old answer. It now asks `shipped_deps --json` for the tier (`ai_tier_names()`).
* `_parse` does see `joblib` and `sklearn` in `ai/lexical_policy.py`'s *every-import* set (lines
  273-290, both inside methods), yet neither name reached the closure's `deferred` or `optional`
  buckets -- while `onnxruntime`/`tokenizers`, imported the same way in `agent/command_onnx.py`, did.
  The cause was in pass 2 of `closure()`: a file the *eager* graph reached recorded only its
  import-time names, so anything it imported inside a function body belonged to no bucket at all,
  and the `deferred |= here` line ran only for lazily reached files. Fixed there (`deferred |=
  here - hard[key]`), which also reclassified `safetensors`/`transformers` from optional to
  deferred, and the hand-declared `AI_TIER_EXTRA` table was deleted: regenerated from a
  package-list-less file, `shipped_deps --write` now derives all eight AI-tier distributions from
  the graph alone (`joblib, onnxruntime, safetensors, scikit-learn, thx01, tokenizers, torch,
  transformers`, check PASS).

Also fixed here: the payload carried **two contradictory `requirements.txt` files**. The root one is
what `setup.exe` and `install-python-ai.bat` install; `dofus-network/requirements.txt` shipped
numpy+requests only, under a stale "this machine runs Python 3.15, use Colab" header, so anyone who
pip-installed from it got an env that cannot load what the product expects. The second file no
longer crosses. `install-python-ai.bat` stopped enumerating its tier's packages for the same reason
-- it now points at the file that is the list.

### L.12 The projection dicts left the index too: 134.1 -> 101.5 MB committed on the shipped interpreter

§L.3 ended with the projection dicts as the piece still being spent "deliberately": `_index_record`
kept six fields per item row so a result could be formatted without touching the cache file again,
and that cost **20.7 MB of live objects for 21,554 rows**. Reading the consumers settled it --
`_compact` is called for at most `limit` winners per query, and the only per-row field reader left is
`quest.list`, which filters quests on `is_dungeon`/`level_min`/`start_position`. Quests have no
`_INDEX_FIELDS` entry, so they keep their materialized records; every projected kind now stores its
projection as **one JSON blob per kind with an int32 row-offset array** -- the same shape §L.3
adopted for the haystack -- and `entries()` keeps only the row number in that slot. `_stored()`
decodes a row when, and only when, a query is about to return it.

Measured with `tools/knowledge_index_probe.py snapshot` before and after, same 16 query arms, both
interpreters (`compare` reports *answers identical: True* on each):

| | python 3.11.9 (ships) | python 3.14.8 (dev) |
| --- | --- | --- |
| commit after all kinds | 134.1 -> **101.5 MB** | 130.7 -> **101.1 MB** |
| peak | 141.9 -> **114.9 MB** | 140.4 -> **117.3 MB** |
| resident | 140.1 -> **107.4 MB** | 138.6 -> 109.2 |
| item build time | 1.689 -> **1.578 s** | 1.392 -> 1.453 s |

So the installed brain gives back 32.6 MB of commit charge and 27.0 MB of peak -- on a 7.8 GB host
that is the difference between two more instances -- and the index builds *faster*, because writing
a six-field `json.dumps` into a bytearray beats allocating 21,554 dicts and their keys. The
committed `data/knowledge_index_baseline.json` is re-blessed from the shipped interpreter
(101.1 / 114.8 / 106.9).

Pinned in `test_knowledge_index` next to the haystack pin: a projected row's slot is an `int` and
its fields come from the kind-wide blob, and a quest row's slot is still a `dict`. The control is
the shape itself -- empty `_INDEX_FIELDS` and the store disappears, so the check fails rather than
quietly passing on the old representation.

### L.13 The mutation sweep could not see the knowledge index, so four honest mutations read as MISSED

§L.12 shipped with two shape pins inside `test_installed_payload_and_dependencies` and I claimed the
store was pinned. Writing four mutations against it proved the claim wrong: `python -u
tools/mutate_cause_gate.py` with `MUT_ONLY="proj-store:"` reported **MISSED, 0 failing checks** for
all four. The reason is not a weak check but an unseen one -- the sweep runs a fixed snippet of
named test functions (88 of them), and nothing in that list touches
`agent/executor.py::_KnowledgeIndex`. Guessing at names made it look solved twice:
`test_knowledge_index` is about `tools/knowledge_index.py` scanning observation logs, and
`test_index_route_travel` is about route travel; the checks that would have caught the mutations sit
in `test_installed_payload_and_dependencies`, which reads the built `setup.exe` and spawns
interpreters, so it cannot run once per mutation.

Fixed by splitting the fast half out: `test_knowledge_index_storage` (spell + quest, ~0.2 s -- the
mechanism is kind-independent, so the big item build is not needed to prove it) is now in the
snippet, and all four mutations are **CAUGHT**, two failing checks each, with the restore arm at
0 failing checks / exit 0. The list is 542 -> 546 mutations and the snippet 88 -> 89 tests, both
quoted in the docs as measured tags, so `--render` moved them with the tree. `1812 checks, 0
failed`, gate PASS at 54.5 MiB / 506 files.

The lesson is the same one §L.11 recorded twice already, now about this tool rather than that one:
a check that is not *run* is not a check, and a coverage claim needs the mutation to prove it. When
a sweep says "MISSED", read it as "no witness was wired", and go find whether the witness exists.

### L.14 The per-row name lists went into the blob too, and the 162 answers that moved were all splices

§L.13's instrument made the next cut measurable instead of guessed at. Sized directly
(`sys.getsizeof` over every row): the name objects were **14.28 MB of `list[str]` plus 4.92 MB of
joined name strings** across the seven kinds (8.88 + 3.19 MB of that on the item kind alone), held
for a scan that asks two questions -- "is this needle inside this row's names" for every row, and
"does one name start with / equal it" only for the rows that answered yes.

Each row's text in the haystack blob is now `name\x00name\x00…\x01description description…` with
one int32 per row marking where its names end, and `_names(kind, index)` splits that region for the
candidate rows only. The joined-name slot and the list slot are both `()` -- the same shared empty
tuple -- so a row costs its tuple shell, its id, its index and nothing else.

`tools/knowledge_index_probe.py` on the shipped 3.11.9 interpreter, after all kinds and the probes:
**commit 101.5 -> 78.0 MB, peak 114.9 -> 98.6, resident 107.4 -> 84.3**, and the 16 probe arms are
identical on both interpreters. Across today's three cuts the index went from 134.1 MB committed
and 141.9 MB peak to 78.0 / 98.6 -- a 42% cut in committed and 30% in peak -- on a host with 7.8 GB.

The separator is the part that changes behaviour, so it was measured, not asserted. A
340-query/1,380-arm harness (260 real names drawn from items/quests/spells, 80 constructed
`name_a + " " + name_b` splices, each run against `any`/`item`/`quest`/`spell`) compared the old
representation with the new one before anything was merged: **162 arms drift, all 162 are
constructed splices, zero real queries moved.** Under a space join, "coup de poing punch" -- the
French name and the English name of one spell, side by side -- is "present in the row" and takes the
whole-query tier (2.0 + 1.5 + 0.5 per token); with the separator it is not one name and can only
reach the token tier (1.5 + 0.5 per token). The old match was a bug that handed a name bonus to a
row for a name nobody has. `test_knowledge_index_storage` pins that arithmetic, and the fifth
`proj-store:` mutation restores the space join to prove the pin bites: five mutations, five
CAUGHT, restore arm 0 failing checks / exit 0.

Two honest caveats. The pins that used to read `row[4]` for the quest area fallback were rewritten
to go through the index rather than grep `executor.py`, because a source-string assertion is a
second thing to update for no extra truth. And three of the checks I wrote for this failed on the
first run -- a token-count I hardcoded at 2 instead of computing, a substring test I wrote as list
membership, and a clause that searched for the separator inside a region whose whole purpose is to
contain it. Those were check bugs, not index bugs; each is fixed above and the failures they
produced are the reason the arithmetic is now spelled out in the check rather than in my head.

### L.15 The mutation sweep died on the first traceback it could not decode

Running the 18 mutations over the files changed today, the sweep aborted at mutation 3: a mutated
route check made the child process write `0xe9`, `subprocess.run(text=True)` raised
`UnicodeDecodeError` inside its own reader thread, `proc.stdout` therefore came back `None`, and
`None + str` raised `TypeError` in `run_sections()`. The tree was safe -- the journal restored the
file and no `.mutate-journal.json` was left behind -- but 15 verdicts were lost, which is the exact
failure mode the tool's own docstring warns about after the 2026-11-01 incident.

The instrument was fragile at the worst moment: it reads output produced *by deliberately broken
code*, so undecodable bytes are a normal outcome, not an exceptional one. It now captures bytes,
decodes with `errors="replace"`, treats a `None` stream as empty, and asks the child for
`PYTHONIOENCODING=utf-8:replace` / `PYTHONUTF8=1` so the common path is UTF-8 anyway. Re-running the
same slice: **18 of 18 CAUGHT, exit 0, restore arm 0 failing checks**, no exception. That is the
fix's own proof -- the same mutations that killed it now complete.

### L.16 The row loop was the cost, not the searching: 65 -> 48 ms worst query, and a fixture that proves the guard

The latency arm added in §L.14's follow-up made the next defect visible in one number. A two-word
query (`dofus pourpre`, kind=item) cost 65 ms, and decomposing it said where: one `find` of the
whole needle over the kind's 15.4 MB blob costs **0.04 ms**, while asking the same question once
per row -- 21,554 bounded searches -- costs **48 ms**. The searching was never the problem; the
Python loop around it was. So the scan asks the blob once and maps each occurrence back to the row
it starts in, keeping the rule the bounded search had: an occurrence counts for a row only if it
both starts and ends inside that row's region, because records are concatenated with nothing
between them and `lenda.epee d` is a real string that crosses two item rows and must belong to
neither.

The name tiers moved onto the same bytes: a name *starts* with the query when an occurrence sits at
the region start or right after a `\x00`, and *equals* it when it also ends on such a boundary.
That matters because the first version of this decoded each candidate row's names instead, and on a
common word (`epee`) that cost 7.4 -> 15.6 ms -- the decode-per-candidate replaced the scan it was
meant to avoid, so the whole-blob trick now runs only where it wins (the row loop) and the names
pass stays a per-row bounded search over a few hundred kilobytes.

Measured, dev 3.14.8, cache emptied before each of 5 repeats over 19 arms: worst mean
**65.5 -> 47.9 ms**, `glouton|any` 37.0 -> 24.2 ms, `dofus pourpre|item` 65.5 -> 47.9, and the
common single-word arms where the names pass short-circuits stayed where they were (`epee|item`
7.45 -> 7.05 ms, `bague|item` 7.75 -> 7.46 ms); two arms lost a little (`pelle|any` 11.8 -> 13.1,
`Astrub|npc` 1.7 -> 2.3) because they enumerate occurrences in the description text too. On the
shipped 3.11.9: worst 49.5 ms, and commit after all kinds **134.1 -> 78.8 MB** with the 16 probe
arms identical. Parity was checked far wider than the probes first: 2,142 arms (340 queries
including 12 real record-crossing needles, six kinds each) -- **0 drifted**.

The containment rule took three attempts to get a witness that could actually fail it, and that is
the honest part of this entry. The first check compared `_rows_with` against the bounded oracle on
quest data, where every row ends with its `\x01` separator, so *no separator-free crossing can
exist* and the comparison was vacuous -- the mutation stayed MISSED. The second built crossing
needles from the tail of one record and the head of the next but sliced `tail[:4]` instead of
`tail[-4:]`, which leaves a gap and puts the needle wholly inside the previous row: still MISSED.
The third is a three-record item fixture whose needles are enumerated from real blob bytes, counts
a crossing only when the needle occurs and fits no row, and catches it: **7 of 7 `proj-*` mutations
CAUGHT, restore arm 0 failing checks / exit 0**.

### L.17 What the index is actually made of, and a saving that measured worse

The compactions so far were driven by "this object should not exist". To find out whether anything
is left, the index was added up against the OS instead: `python tools/knowledge_index_probe.py
breakdown`, which prints each component and separates what the process *maps* from what Windows
*charges* it. Shipped 3.11.9, all seven kinds, 36,970 rows:

| | MB |
| --- | --- |
| haystack blobs (names + descriptions, one buffer per kind) | 17.33 |
| projection blobs (the six fields a result exposes, as JSON) | 7.71 |
| row tuple shells | 2.82 |
| row int objects (id and value-row index) | 1.87 |
| row-offset and name-end arrays | 0.28 |
| **accounted** | **30.01** |
| commit before -> after the build | 15.5 -> 78.1 |
| resident before -> after the build | 22.4 -> 84.1 |
| peak during the build | 98.8 |

So ~30 MB is the index and ~32 MB more is charged than accounted. The obvious reading -- "the
`bytes(blob)` copies duplicate 25 MB of grow-in-place buffers; keep the bytearrays" -- was tried
and **measured worse**: commit 77.6 -> 80.3 MB with residency unchanged, because an over-grown
bytearray keeps its spare capacity (19.3 MB held for 17.3 MB of content, 8.4 for 7.7). The exact
copy wins, and that is now the reason written at the copy site rather than a preference
[agent/executor.py:588](../agent/executor.py). The remaining gap is memory the build passed
through and neither CPython's arenas nor the OS returns, which is what the daemon's
`EmptyWorkingSet` cadence exists to hand back; it is not a per-row cost, and the first version of
this arm claimed it was (899 B/row) before the arithmetic was checked against `accounted`.

Two witnesses came out of this and one came out of the previous entry: `latency` and `breakdown`
are now permanent arms of the probe, and `test_knowledge_index_storage` pins the *blessed*
snapshot's residency under 100 MB resident / 110 MB commit -- loose on purpose, since the state
before this month's compaction sat at 168 / 162 and a re-materialization of per-row objects would
land there again. Reading a recorded file rather than re-measuring keeps the check free of the
2 s build it would otherwise have to repeat once per mutation.

### L.18 The head's plan *preference* is the losing part, and the fix I nearly shipped is not a fix

`tools/wsp_fight.py` now carries its own uncertainty: `summary()` bootstraps the mean over
matchups (the unit of replication, since each is played with the network on each side) and gives an
exact one-sided sign probability against parity. At 40 matchups that mattered -- a random sign flip
moved the metric further than a motivated edit did. At **200 matchups / 400 games** (numpy backend,
`--matchup-seed 11`) the live policy's answer against the deterministic solver is:

| | |
| --- | --- |
| win rate | **0.1512** (bootstrap CI95 **0.121 -- 0.184**) |
| wins / draws / losses | 2 / 53 / 145 |
| sign test, one-sided vs parity | p = 0.0 |
| games the head decided with no solver fallback | 73, winning **0.0205** |
| fallback rate / mean claimed probability | 0.2271 / 0.9134 |

So the indictment is specific and it is not "the network plays illegal moves": the arm being scored
realizes the head's (spell, target) *onto the solver's own candidate plans* and takes the solver's
top plan when nothing matches. What loses is the head's **preference among plans the solver already
accepted as legal** -- and the fewer solver turns mixed in, the worse it gets (0.151 overall, 0.02
where the head alone decided).

That reading makes exactly one change look obvious: stop letting the head's own cast happen when the
solver did not propose it -- gate `_realize`'s `policy_direct` path on candidate membership
[combat/live_policy.py:475](../combat/live_policy.py). Measured on the 294 recorded live boards
through the production `decide()`: **all 11 `policy_direct` decisions named a (spell, target) that
*was* among `plan_candidates`, and 0 were not.** `policy_direct` is not the head overriding the
planner; it is the planner's own cast executed from the current cell instead of walking there first,
which is better tempo. The change would have cost tempo to fix a problem that does not exist, and
the only reason to believe that is that the question was asked of the real path rather than of the
harness. (The same 294 boards also re-confirm §L.9: 246 of them yield no reason at all, because the
app asks the brain on turns that are already settled.)

Open, and now precisely stated: the head's *preference* is what loses, so the candidate change is in
the matching branch (the `_value_choice` / `_move_score` pick among `matching` plans), not in
`policy_direct`. The harness is an approximation of `_realize`, so it is not yet sufficient evidence
to change the live path -- what is needed is the production rule measured game-by-game, which means
`LivePolicy.decide()` driving `combat.duel.play_game` as the network side. Also recorded here: 200
matchups is affordable (a few minutes), so the "is this edit better than noise" question no longer
has to be asked at n=40.

### L.19 An offline duel cannot score the shipped decision path yet, and the number that said otherwise was wrong

`Board.to_fight_state()` (§L.18's follow-up) made it possible to ask the client's own function --
`LivePolicy.decide()` -- from inside `combat.duel.play_game`, so the arm that the §L.18 table
describes as an adapter could be replaced by the real thing. It is wired (`tools/wsp_fight.py
--policy production`) and it does not yet produce a usable number. Here is what the run says:

| | |
| --- | --- |
| win rate of the "production" arm | 0.4813 (CI95 0.45-0.50, 0 wins / 38 draws / 2 losses) |
| turns whose emitted action matched a candidate plan | 87 of 209 (**41.6%**) |
| turns that fell through to the solver's own top plan | 122 (58.4%) |
| production reasons behind those | `policy_direct` 148, `policy_move` 31, `policy_approach` 28, `policy_retreat` 2 |

A 0.48 win rate against the solver, with 58% of turns literally played by the solver, is the
solver scored against itself -- it is not a measurement of the network, and reporting 0.4813 as one
would be the same mistake §L.18 just avoided in the other direction. The mismatch has a specific
shape: `decide()` emits a *walk toward a cast cell* (`policy_move`, `policy_approach`) on 59 of
those turns, and every candidate the duel engine holds starts with the cast that the walk enables
(`from_cell` elsewhere, `move_to` folded in), so no candidate begins with the pure move the client
was told to make -- and of the 148 direct casts, 61 are for a (spell, target) the beam did not
carry at all. The two tiers fail for opposite reasons, and a single "fallback" counter would have
hidden that; `meta.action_tiers` is in the harness output now for exactly this.

So the honest state of the shipped-path question: the serialization is in and round-trip tested
(`BoardSerializationTests`: a board rebuilt from its own payload equals the original, and a
deliberately flipped `requires_los` comes back different, so the test is not vacuous); the arm
exists; and what is missing is an engine that accepts an action the way the client does -- a
walk-only plan, plus the 61 casts outside the beam. That is task #73, and until it is done, §L.18's
adapter number stands as the only head-vs-solver measurement with power (0.1512, CI 0.121-0.184),
with its limitation stated where it is quoted: the adapter is not `_realize`.

*(Closed the same day, in §L.21: the engine does accept them now, and the adapter number was the
one to distrust.)*

Found on the way, and fixed before any of it was trusted: the first version of the arm read
`action["spell_id"]` while the payload's keys are camelCase (`spellId`, `targetId`), which made
*every* turn unmatched and produced 0.4813 with a 0% mapping rate. The counters caught it because
they were printed next to the win rate.

### L.20 The mutation the sweep could not catch turned out to be a hole in the tests, not in the sweep

`Board.to_fight_state()` (§L.19) needed a witness the sweep could reach, so `test_board_payload_round_trip`
was added to the snippet -- it runs the serialization class in-process (~0.2 s) and asserts the
round trip against `battle_solver` directly. Then a mutation withered: dropping `targetId` from
`_action_for_plan` -- the last translation before the page clicks, the field that says *whom* to
aim at -- read **MISSED with 0 failing checks**, and it stayed MISSED after the witness was wired.

It was not the wiring this time. The realization tests that do assert a `targetId` reach it through
`_direct_cast`, which builds its own dict; no test in the tree ever called `_action_for_plan` on a
plan whose first action is a cast. So `combat/test_live_policy.py` gained
`test_a_planned_cast_reaches_the_page_with_its_spell_and_target` (spell id, target id, target cell),
and the class is now run by the snippet witness alongside the serialization one. Both payload
mutations are CAUGHT, the restore arm is 0 failing checks / exit 0, and `combat/` sits at 96 passed.

Worth keeping the shape of this in mind, because it recurred four times in one day, in four
different tools: a check that is never executed, a check whose input cannot fail, a comparison whose
needles were all filtered out, and here a function with no test at all that every green signal
implied was covered. `MISSED` is the only one of those that announces itself.

### L.21 The shipped rule scores 0.406, not 0.151: the adapter was measuring a policy nobody runs

`BattleSolver.plan_for_action()` is the missing inverse. `_action_for_plan` hands the page one
action and the page asks again from the new cell, so a turn of the shipped rule is a *sequence* of
single actions; the beam could not express that, because it folds the walk into the cast and prunes
every (spell, target) pair that is not top-scoring. The new method takes the emitted dict back and
prices it with the search's own `_templates` arithmetic -- the same `dealt`, `KILL_BONUS`,
`HEAL_WEIGHT`, `damage_int` and AP/MP terms the beam expansion uses at
[combat/battle_solver.py:1312](../combat/battle_solver.py). `combat/test_battle_solver.py::ActionRealizationTests`
(6 tests) pins it against a depth-1 beam on the same board, so the synthesis and the search cannot
drift apart quietly: a single-cast turn must equal the beam's in AP, MP, `final_hp`, expected damage
and score, and every illegal action -- out of range, unknown spell, unaffordable, already dead,
walking onto an occupied cell, walking nowhere -- has to return `None` rather than invent a legal one.
Five `action:` mutations, each one clause of the copied arithmetic or one of those gates, are CAUGHT.

With that, `tools/wsp_fight.py --policy production` plays the real rule: ask, apply, ask again. Two
rules make the two sides comparable rather than nominally so. A walk costs MP but **no cast slot**,
because the beam's walk is free inside its folded plan -- charging one would have handed the network
a handicap invented by the harness. And the casts per turn are capped at the beam's own
`max_actions` (2), which the network hit unrestrained 536 times of 1281 turns, so the cap is a real
and disclosed cost, not a simplification.

200 matchups / 400 games, `--matchup-seed 11 --seed 7`, numpy backend, 124 s:

| | |
| --- | --- |
| win rate of the shipped rule | **0.4062** (bootstrap CI95 **0.379 -- 0.433**) |
| wins / draws / losses | 1 / 157 / 42 |
| sign test, one-sided vs parity | p = 0.0 |
| turns the solver had to stand in for | **0** (fallback rate 0.0, errors 0) |
| actions played / per turn | 2985 / 2.33 (1945 casts, 1040 walks, 443 declared passes) |
| casts the beam never offered | 951 of 1945 (**48.9%**) |
| `decide()` calls the solver's own beam decided | 126 of 3964 (3.2%) |
| mean claimed probability | 0.9237 |

The paired comparison against §L.18's adapter arm is **delta +0.2550** (CI 0.211 -- 0.299), 121
matchups improved, 19 worsened, 60 unchanged. So the answer to the question §L.19 raised is that the
0.1512 was the artifact: it scored the head's preference *among plans the beam already kept*, and
then handed the turn to the solver whenever the head wanted something else -- which is most of the
time, because half the casts the shipped rule makes are pairs the beam pruned. Those 951 casts are
not illegal and not noise; they are the part of the network that the previous measurement could not
see at all, and it is the part that plays better. What remains true and is the actual finding:
**0.406 is below parity, and the shape of it is 1 outright win against 42 outright losses.** The
network loses duels it could have finished; 157 of 200 matchups end in the 12-round HP tie-break.
§L.18's open question -- the `_value_choice` / `_move_score` pick among `matching` plans -- is now
measurable at this resolution, which is where the next edit goes.

Found on the way, and worse than a typo: `compare()`'s `sign_test_p_two_sided` summed the binomial
tail from `min(ups, downs)` **up to n**, which is P(X >= 19) ~ 1 for any data at all. It printed
1.0 for the 121-19 split above and would have printed 1.0 for a 140-0 one, so every paired verdict
this tool ever labelled was labelled inconclusive by an instrument that cannot be otherwise. No doc
quoted the field (checked), so no published claim needed retracting -- but the class is the one this
month keeps hitting (§L.20 lists four instances), and it is now pinned from both ends:
`test_duel_harness_statistics` requires a 39-1 split to read p < 1e-6 **and** a 20-20 split to read
p > 0.5, so a formula collapsed at either end fails. The reverted `sign:` mutation reproduces the
original bug exactly (p = 1.0 on the 39-1 arm). `summary()`'s one-sided test was already summing the
correct way, which is why §L.18's p = 0.0 stands.

Verification: `combat/` 102 passed, the two new witness functions are in the sweep snippet,
`MUT_ONLY="action:,sign:"` 6/6 CAUGHT with `restored: 0 failing checks, exit 0` and
"mutations that no check noticed: none".

### L.22 The critic never picks the realization: four arms, two datasets, and the branch that does

§L.18 left one candidate open -- "the head's *preference among plans the solver already kept* is
what loses", which is `_value_choice` (the trained critic) falling back to `_move_score` (distance).
The production arm made that measurable, so it was counted instead of argued. `_value_choice` now
increments itself and the count is readable as `LivePolicy.value_choice_stats`, printed by
`python tools/policy_over_record_boards.py` and reported in `wsp_fight`'s meta.

Both datasets answer the same way: **asked = 0, fired = 0.** 3964 `decide()` calls across the 400
duel games (the win rate reproduced 0.4062 to the digit, which is the control that says the counter
changed no decision), and 135 own-turn recorded boards. Four arms on those boards, because one
number could mean two different deaths -- `_value_choice` is gated on `_value_ready` (only 8 of the
135 boards are value-ready, the rest have no enemy spellbook) *and* on `len(matching) >= 2`:

| arm | asked |
| --- | --- |
| shipped | 0 |
| `_cast_cell` forced to None | 0 |
| that, plus `_value_ready` forced True | 0 |
| `_value_ready` forced True only | 0 |

So it is not the readiness gate. The `matching` list is empty every time the code reaches it, and
there is a structural reason it should be: `_cast_cell` scans `cells.reachable(my_cell, my_mp)`
intersected with "this pair is legal from here", and every cast origin the beam can plan is in
exactly that set -- the beam's walks are paid from the same MP. So whenever the actor's pair has a
reachable cast cell, `_cast_cell` returns first and the beam branch is never asked; and when it does
not, the beam has no matching plan either. §L.18's candidate is not a live mechanism.

What *is* live is the thing the critic was never wired to. `_cast_cell` answers with the **first**
reachable cell in BFS order that makes the cast legal -- the walk destination is chosen by iteration
order, not by distance to the target, not by the critic (it is by cost, since BFS order is cheapest
first). That branch fired 611 times in
the duel (15% of `decide()` calls) and once on the recorded boards: it is where the shipped rule
actually decides where to stand, and it decides it arbitrarily. That is the next change to measure
(#75), and the production arm can now score it in two minutes at 400 games with the paired delta,
CI and sign test attached.

*(Measured the same day, §L.23: the arbitrariness costs nothing. The cheapest cell and the closest
cell are the same cell in 610 of the 611 calls, so this was a hypothesis about the code rather than
a measured defect, and the paragraph above is the claim a reading of the source invites.)*

The witness, and a tripwire that earned its keep: `SlotRealizationTests` gained
`test_the_value_head_branch_reports_whether_it_was_reached` -- not value-ready is not asked, one
plan is not asked, two plans with a stub that cannot rank is asked-but-not-fired (the arm that
makes `asked=0` a finding rather than a tautology). Two `critic:` mutations are CAUGHT. And the
sweep's restore arm came back `restored: 1 failing checks` on the clean tree, because the tool's new
print ran against `test_board_replay`'s `_Policy` stub, which had no such attribute: the breakage I
had just written was reported by the same mechanism, within one sweep, before any number was
published. The stub now carries the attribute and the check asserts the line is printed.

### L.23 Two ablations that came back empty, and the counter that made them provable anyway

§L.22 left one candidate: the walk destination chosen by `_cast_cell`. `tools/wsp_fight.py
--origin first|closest` now computes *both* answers on every call and returns whichever arm is asked
for, so the run reports the disagreement rate as well as the win rate. At 200 matchups / 400 games:

| arm | win rate | CI95 | W/D/L | `_cast_cell` calls | disagree |
| --- | --- | --- | --- | --- | --- |
| `first` (shipped, cheapest in BFS order) | 0.4062 | 0.379-0.433 | 1/157/42 | 611 | 1 |
| `closest` (nearest the target) | 0.4062 | 0.379-0.433 | 1/157/42 | 611 | 1 |

Paired delta **0.0000** (CI 0.000-0.000), 0 matchups improved, 0 worsened, 200 unchanged. The rules
agree because getting a cast to land means walking until the target enters the spell's range ring,
and the first cell that does is also the nearest one; distance to the target falls monotonically along
the BFS path until the ring is crossed. So the shipped rule does not under-advance, and the one-cell
disagreement was not enough to flip a single game. `first` also reproduced the unwrapped baseline to
the digit, which is the control that says the wrapper is faithful rather than a second policy.

The other empty result is the `--ranker distance` arm this section began with. It was written before
§L.22's counters existed, and once those showed `asked = 0` on both datasets the knob toggled a
branch that never runs -- a switch that cannot move the metric. It is deleted rather than kept as
apparent rigour; `value_choice_stats` stays, because it is the instrument that says so.

Both arms are worth recording for what they rule out: the remaining gap to parity (0.406 against
0.5, with 1 win for every 42 losses) is not the walk destination and not the plan preference. It is
the head's own spell/target choice and its failure to finish fights, which is a training question,
not another seam in the realization rule.

### L.24 The trained policy could not load on an installed copy at all, and two blind checks said otherwise

Certifying §L.21's code on the interpreter that actually ships (`runtimes/python/python.exe`,
3.11.9) returned `ModuleNotFoundError: No module named 'jax'` for `combat.encounter.env` -- and
through it `combat.encounter.model`, `combat.plan_value` and `combat.live_policy`. The whole learned
path was unloadable on an installed machine, which is the exact state
`combat/encounter/backend.py` documents itself as existing to end ("every installed fight fell back
to the deterministic solver and `/health` said `backend=solver-only (jax absent)` -- a decided
feature that was not in the product"). #57 was closed as done: the backend shim exists, `model.py`
and `plan_value.py` were migrated to it, but `env.py` still did `import jax` at module scope, and
`from combat.encounter.env import Config` sits on the import chain of everything above it.

Two checks could not see it, and they are worth naming because they are different mistakes:

* `tools/ship_compile.py` compiles every shipped `.py` with the embedded interpreter. That proves
  syntax. A file that parses perfectly and imports a package the runtime does not have is invisible
  to it, and the check's own label -- "every shipped file parses under the interpreter that ships" --
  is honest about what it tests. Nobody read it as an import guarantee, but that is what it was doing.
* The parity check ran `combat.plan_benchmark` twice on the *development* interpreter, once with
  `DOFUS_FIGHT_BACKEND=numpy`. That forces the numpy arrays, but jax is still importable there, so
  both arms had the jax-only API available. The benchmark also does not go through
  `LivePolicy._forward_bundle`, so the jax-specific indexed updates in `env.action_masks` and
  `env.step` never executed in either arm.

Fixing the import exposed the second blocker immediately and with a count: on the shipped
interpreter `decide()` answered `policy_error: AttributeError: 'numpy.ndarray' object has no
attribute 'at'` on **120 of 135 recorded boards**. `x.at[idx].set(v)` / `.add(v)` is jax's immutable
functional update; numpy has no equivalent, and eight sites in `env.py` plus two in
`combat/live_policy.py:_forward_bundle` used it. `backend.at(x)[idx].set(v)` is now the shim: the
jax branch returns `x.at` so the training box is unchanged to the bit, the numpy branch copies and
assigns, and `.add` uses unbuffered `numpy.add.at` because several rows landing on one cell must
accumulate -- `out[idx] += v` would let one of them win silently and would be a wrong env, not a
slower one.

What the installed copy can now do, measured rather than asserted:

| | |
| --- | --- |
| census (`--imports --decide`) on 3.11.9 | PASS, and it answers `cast (policy_direct)` on a real duel board |
| 135 recorded live boards, 3.11.9 + numpy vs 3.14.8 + jax | **identical decision by decision** on all seven recorded fields (type, spellId, targetId, cell, reason, probability and value to 9 dp) |
| reason distribution, both interpreters | direct 29, approach 42, pass 35, retreat 11, not-actionable 15, no-legal-plan 1, move 1, solver-fallback 1 |
| action distribution, both interpreters | cast 30, move 54, pass 36, none 15 |
| `plan_benchmark`, jax vs forced numpy | all fields equal (7 boards) |
| latency p50 on the 135 boards, quiet box | 3.3 ms dev+jax, 3.8 ms shipped+numpy |

The latency row is a correction of a measurement taken earlier in this same session: the first dev
run read p50 18.8 ms while two 200-game duel arms were still running on a 7.8 GB box. Re-measured
with the machine idle, the numpy path is ~15% slower, not five times faster. Shipping without jax
costs essentially nothing; it did not buy speed.

New instruments, each with an arm that can fail: `ship_compile.py --imports [names] [--decide]`
(the `--imports jax` arm is asserted to exit 1, so a census that always passes is caught), two
regression checks wired to it, a direct contract test on `at()` for both branches, and three
mutations -- `env:` accumulate-vs-overwrite, `env:` every-slot-castable, `backend: at() returns the
array` -- all CAUGHT. The rebuilt `setup.exe` was read back and the four changed modules carry the
fix in the artifact's own bytes (env.py 18,756 B with the backend import, backend.py 6,329 B with
`class _IndexAt`, live_policy.py 33,618 B with the counters, battle_solver.py 83,798 B with
`plan_for_action`), not just in the tree the build read from.

What is deliberately **not** changed: `combat/brain_service.py` still picks an interpreter with
`import jax, numpy`, so installed fights keep the deterministic solver. That is no longer a
capability limit, it is a strength decision, and the measurement says the trained policy is the
weaker of the two (0.406 against the solver at 400 games, §L.21). Flipping the probe would make
every installed fight play the policy; it should happen when the policy passes parity, not because
the runtime now allows it. Recorded as #77 for the user to call.

### L.25 Weight-space edits at 400 games: two nulls, one control, and what the control actually bought

§L.21 made the shipped rule measurable, so the weight-space question #63 left open can be answered
with an instrument instead of an argument. All arms are `--policy production`, 200 matchups / 400
games, `--matchup-seed 11 --seed 7`, paired against the unmodified checkpoint (0.4062).

| arm | edit | win rate | CI95 | paired delta | up/down/unchanged | actions moved |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | none | 0.4062 | 0.379-0.433 | -- | -- | -- |
| move +8 approach | `move` bias index 0 | 0.4062 | 0.379-0.433 | **0.0000** (CI 0.000-0.000) | 0/0/200 | 25 of 2,985 |
| move -8 retreat | `move` bias index 2 | 0.4062 | 0.379-0.433 | **0.0000** (CI 0.000-0.000) | 0/0/200 | 26 of 2,985 |
| spell +8 pass slot | `spell` bias index 4 | **0.4475** | 0.425-0.468 | **+0.0413** (CI 0.024-0.061), p 6.6e-5 | 21/2/177 | 425 of 2,985 |

The move-head edits are nulls, and they are *validated* nulls rather than silent ones: each changed
the mix of actions the policy plays (walks 1040 -> 1014 and 1013, casts 1945 -> 1946) while changing
no game result. In this duel model a walk that does not enable a cast is free but worth nothing --
position enters only through range legality -- so 26 wasted tempo actions across 400 games cannot
move an HP tie-break. That is the honest version of "the move head is inert": it is not inert, its
choices are simply not scored by this outcome.

The control is the more interesting result, because it moved the metric in the *wrong* direction of
interpretation. Biasing the pass slot raised the win rate by +0.0413 with p = 6.6e-5 -- and it did
that by making `decide()` abstain: the solver-decided share of `decide()` calls went from **126 of
3,964 (3.2%)** to **396 of 3,567 (11.1%)**, `solver_fallback` 99 -> 351, casts 1945 -> 2002 and walks
1040 -> 692. The arm is better because the deterministic beam played more of it. That is the §L.19
artifact in a new costume, and it is the reason the win rate must never be quoted without the
solver-decided share next to it: near the top of that curve sits 100% solver, which by construction
scores 0.5 on side-swapped matchups. So the only thing a bias edit can buy at this scale is *abstention*,
which is not a weight-space improvement of the policy -- it is a vote to run the solver.

Read together with §L.21 (the head's own spell/target choices, 48.9% of them outside the beam, score
0.406) and §L.22 (the critic is never consulted), the verdict on weight-space editing here is
specific and negative: at 1.0 MB of parameters, 3-5 logits per head, with a numpy-only shipped
runtime, there is no bias or low-rank direction in this checkpoint that turns the fight policy from
below parity to above it. The gap is in what the heads were trained to prefer, not in how they are
offset. Anything that wants the network to win duels has to change the training, not the weights'
neighborhood.

The instrument learned the lesson too: `fallback_rate` stayed 0.0 throughout the control arm, so it
was no protection against this. `meta.solver_decided_rate` is now printed next to the win rate (and
carried into `summary()`), which is the number that says how much of a score the beam decided.

Found and fixed while building the control: the `random` arm flipped exactly one coordinate no
matter what `--edits` said, so the old "a random sign flip moved the metric further than a motivated
edit did" reading at n=40 compared one no-op against another. It now flips `--edits` distinct
coordinates and reports how many were non-zero and therefore actually moved. The module docstring's
move-head order was also backwards (it said retreat/stay/approach; `_move_dest` and `env.step` both
make index 0 approach and index 2 retreat), and its "97 of 105 boards chose retreat" premise is
stale -- the 135 recorded boards now answer approach 42, retreat 11.

### L.26 How far a hybrid can climb: the head's own turns are the whole deficit, at 200 matchups

§L.25 showed a weight edit could raise the win rate only by making the head abstain, so the honest
question is how much of the deficit that buys and whether anything beats the solver. `--abstain p`
plays the head's action when its claimed probability is at least `p` and hands the turn to the beam's
top plan below it -- counted as a harness fallback, so `fallback_rate` says what it is. Same 200
matchups / 400 games, `--matchup-seed 11 --seed 7`, paired against the unmodified production arm:

| arm | solver-played turns | win rate | CI95 | W/D/L | paired delta vs baseline |
| --- | --- | --- | --- | --- | --- |
| shipped rule (never abstain) | 0.0% | 0.4062 | 0.379-0.433 | 1/157/42 | -- |
| abstain below 0.95 | 49.7% | 0.4738 | 0.456-0.490 | 3/182/15 | +0.0675 (CI 0.044-0.093), p 5.8e-8 |
| abstain below 0.99 | 64.0% | 0.4800 | 0.464-0.494 | 2/186/12 | +0.0737 (CI 0.051-0.099), p 7.9e-9 |

Two things fall out. The gain is real and large -- the head's confident-but-wrong turns are the whole
deficit, and taking half of them away recovers most of it. And the curve does not cross parity: even
with the solver playing 64% of its turns, this arm is still measurably below 0.5, its CI excluding
0.5 above. So there is no mixture of the two policies that beats the deterministic solver on this
instrument; the best a network-assisted fighter can do here is approach the solver from underneath.
The interpretation is not that the beam is magic -- the solver side of every matchup is the same
solver, so a 0.5 would mean "as good as the solver, where it is not the solver". Reaching that would
mean the head never plays a turn the solver would not have played, which is not a policy, it is a
copy.

Read against §L.21 (the head plays 48.9% of its casts on pairs the beam never offered, scoring below
parity) this is the answer to the fight-policy question this thread started with. It also bears on
the live machine, and the direction is uncomfortable: on the dev box the brain *does* run (an
interpreter with jax exists), so fights there are played by the weaker of the two policies today,
while an installed copy falls back to the stronger one. Two options are on the table for #77, in
order of what is measured: prefer the solver on turns the head is not confident about (`--abstain
0.95` is the best-measured variant, +0.074), or keep the head out of fights entirely. The caveat that
belongs with both: the duel scores direct damage only -- no statuses, shields, push, traps or AP
removal -- so it cannot see a policy that wins by mechanics it does not model. It is nevertheless the
only outcome instrument in the project, and every claim in this section is measured on it rather than
argued around it.

`--abstain` is a harness knob, not shipped behavior: nothing in the live path changes until #77 is
answered.

### L.27 The page now stops asking for a board the brain can only refuse

The last half of #66 that needed no live session. Of the 135 own-turn recorded boards, 15 (11%)
arrive at the brain with a contract error `no_live_enemies`: the ask costs a round trip and a beam
search and can only return a refusal. The page holds the same `fighters` list the brain parses, so a
guard there is decidable offline -- and decidable *exactly*: the predicate "some fighter has
`team == 'enemy'`, `alive !== false` and `hp > 0`" reproduced the brain's refusal on 15 of 15 boards
and fired on 0 of the other 120 (allies use `team: 'player'`, which is also the brain's own enemy
definition).

`scripts/mobile-disguise.js:_askBrain` now returns false in that case, which is the exit the
already-fought-over branch takes: the caller falls through to the local tick, so no turn can stall
on it. Pinned headlessly in `tools/test_fight_state_fields.js`, which extracts the real block from
the page source and runs it against four shapes (dead-only enemy, standing enemy, allies-only party,
enemy with `alive` never set, and a board with no `fighters` key at all) -- 55 checks, 0 failures.
One mutation, `guard: allies are counted as live enemies`, is CAUGHT with the restore arm at 0
failing checks.

What is deliberately still open in #66: 28 of 135 own-turn boards are asked with `ap == 0`. Those are
not pure waste -- with no AP the shipped rule still walks toward range, and position is worth
something -- so suppressing them is a walk-policy decision, not a free byte. And 64 of the 135 show
`max_ap: None`: those are older recordings, from before the maxima fix that
`test_fight_state_fields.js` documents, not a live defect.

### L.28 What an AP-zero walk is worth, and where the duel stops being able to say

The second half of #66 was phrased as a decision -- 28 of 135 recorded live asks (21%) arrive with no
AP left, where the only thing the brain can return is a walk. That is priceable, so it was priced:
`tools/wsp_fight.py base --matchups 200 --matchup-seed 11 --seed 7 --policy production --ap0-walks
drop` ends the turn instead of playing the walk, against the same `play` arm (0.4062).

| arm | win rate | CI95 | W/D/L | walks dropped | paired delta |
| --- | --- | --- | --- | --- | --- |
| `play` (shipped) | 0.4062 | 0.379-0.433 | 1/157/42 | 0 | -- |
| `drop` | 0.4037 | 0.376-0.431 | 1/156/43 | 60 | **-0.0025** (CI -0.0075-0.000), 1 worsened / 199 unchanged |

So the walk is not worthless, but it is worth about 0.00004 win-rate points per turn -- 60 of them cost
a quarter of a point, and the interval touches zero. The `play` arm reproduced 0.4062 exactly, which is
the control that says the added condition changed nothing by itself.

Where the measurement stops: the duel only produced 60 such turns in 1,281 (4.7%), while the live
boards are 21%. That is a property of the duel's AP economy -- two spells of 3-6 AP rarely empty the
bar -- not of the knob, so the 21% population cannot be reproduced on this instrument and the
per-walk price must not be multiplied up to it in the dark. Two honest readings survive: suppressing
those asks buys roughly a fifth of the brain's calls and CPU for something like two hundredths of a
win rate, and in a model that has no zones, no push and no statuses, position may matter *more* than
this prices it, not less. So #66's remaining half stays a decision with a number attached rather than
a change made. The part that was strictly waste -- the 11% of asks with no living enemy -- shipped in
§L.27, because there the predicate and the brain's refusal agree exactly and there is nothing to lose.

### L.29 The head's damage is concentrated: 8% of the turns cost nearly half the deficit

Five arms of §L.21/§L.25/§L.26 already measured the same thing from different directions, so they
can be read as one curve: win rate against the share of the network's turns that its own
*beam* -- the deterministic planner inside `decide()` -- decided rather than the heads. The share is
`harness fallback + (1 - harness fallback) * (solver_fallback + solver_move) / decide() calls`,
which is what `meta.solver_decided_rate` alone understates: it counts only the substitutions inside
`decide()`, not the ones the harness made.

| arm | turns the beam decided | win rate |
| --- | --- | --- |
| shipped rule | 0.032 | 0.4062 |
| `move` +8 approach (a null edit) | 0.034 | 0.4062 |
| `spell` +8 pass slot | 0.111 | 0.4475 |
| abstain below 0.95 | 0.511 | 0.4738 |
| abstain below 0.99 | 0.650 | 0.4800 |

A straight line through all five is `win = 0.112 x share + 0.413` with a maximum residual of 0.022 --
and the line is the wrong model, which is the finding. The slope is not constant: from 0.032 to 0.111
of beam share the curve rises **0.52 per unit**, from 0.111 to 0.511 it rises 0.066, and from 0.511 to
0.650 only 0.045. The deficit is therefore *front-loaded*: handing the beam just 8 percentage points
of the turns recovers +0.041 of the 0.094 gap to parity -- nearly half of it -- while the next 40
points buy only +0.026.

Two consequences, and both matter more than the headline 0.4062 did.

First, `--abstain 0.95` is the wrong shape of gate. Confidence is a blunt proxy: it substitutes half
the turns to buy two-thirds of the available recovery. The cheap rule implied by the curvature is to
find *which* turns do the damage -- a small, identifiable class, since 8% of them account for almost
half the deficit -- and abstain on exactly those. The candidate classes are already named by the
tiers in the same runs: `cast_outside_beam` (951 of 1,945 casts), the two `policy_move` walk
destinations, and `no_legal_plan`. That is a measurement task, not a training task, and it is #80.

Second, the linear fit makes a prediction the mirror control can kill: extrapolated to a beam share of
1.0 it reads 0.525, while a solver playing the solver on both sides must score 0.5 by construction --
every matchup is played with the network on each side, and the two sides would then be identical
policies. So the curve cannot stay linear; it bends down toward 0.50, exactly as its own flattening
already suggests. `--abstain 1.01` measures that endpoint directly (#79): if it lands near 0.50 the
concave reading stands and 0.5 becomes a hard ceiling for any head-plus-beam mixture; if it lands
above 0.5, some seat asymmetry is surviving the swap and every number in §L.21 through §L.29 has to be
re-read against that bias instead of against 0.5.

### L.30 The head loses fights it enters favored, and the gate rescues losses rather than buying wins

The five arms can be joined back to the dataset rows, which says something the aggregate win rate
could not. Characterizing the shipped arm's 200 matchups by the two builds in them (from
`kaggle/tpu_selfplay/dataset/encounters.npz`, the network side first):

| outcome | n | our HP | their HP | our best spell | theirs | our spells | initiative diff |
| --- | --- | --- | --- | --- | --- | --- | --- |
| outright loss | 42 | **467** | 415 | 70 | 70 | 4.0 | +4 |
| draw | 157 | 431 | 446 | 73 | 72 | 4.0 | -1 |
| outright win | 1 | 360 | 360 | 90 | 47 | 4.0 | +3 |

So the fights the head throws away are not harder fights. In the matchups it lost outright it was the
*larger* build on average -- 467 HP against 415, with the same spell count, the same top-damage spell
and a small initiative edge -- and it still lost. Matchup selection explains about none of the
deficit, which is what the side-swapped design was supposed to ensure and is now checked rather than
assumed. The one matchup it won outright is the shape you would expect from a policy that finishes
fights: 90 damage against 47.

The second result is about the gate in §L.29. The `abstain below 0.99` arm improved 32 matchups, and
**31 of the 32 were matchups the head had lost outright** -- only one was a draw, and none was a win it
gave back or extended. Handing turns to the beam does not make this side win more; it stops it losing.
Read together with the front-loaded curvature, the picture is a policy that in roughly one matchup in
five gets into a sequence it cannot finish, and a gate that mostly prevents that sequence rather than
improving the ones that were already survivable.

What this rules in and out for #80: a *matchup-level* rule -- abstain against big builds, or against
high-HP pairs -- has nothing to bite on, because the losing matchups are not distinguishable by build
strength. The gate has to be a property of the decision the head is about to make, measured at the
moment of choosing. That narrows the candidate classes to the ones already in the tiers
(`cast_outside_beam`, the `policy_move` walk destination, `no_legal_plan`) and predicts the useful
signal is per-turn, not per-fight.

### L.31 The ceiling is real, the backends are bit-identical, and the cost is in parsing the same payload twice

Three measurements that were queued while the mutation sweep held the tree, all now closed.

**The 0.5 baseline is what it was assumed to be.** `--abstain 1.01` makes the head abstain on every
turn, so both sides of every matchup play the same deterministic solver: **0.4938** (bootstrap CI
0.4838-0.5038, which covers 0.5), 2 wins / 192 draws / 6 losses over 400 games, `fallback_rate` 1.000.
Seat asymmetry is therefore ~0.006 and not significant, so reading the head's score against 0.5 is
legitimate -- and §L.29's linear extrapolation to 0.525 is refuted as it had to be. The curve is
concave, which is the substantive claim: the first few turns taken away from the heads are worth far
more than the later ones.

The same arm gives the number #77 should be decided on. Paired against the shipped head:
**+0.0875** for the pure solver (CI 0.0612-0.1150), 40 matchups improved, 3 worsened, sign test
p = 3.0e-9. That is the cost of the heads' decisions against the fallback, bias-corrected by the
mirror that produced it: 0.4062 against a ceiling of 0.4938, not against an assumed 0.5000.

**The two array backends agree bit for bit.** §L.24's cross-interpreter parity was re-run at full
precision instead of rounded to nine decimal places, because an exact match at nine dp is itself
evidence worth checking rather than accepting. Over the 135 recorded live boards, Python 3.14.8 with
jax against 3.11.9 with numpy: **0 differing decision fields, max |delta probability| 0.0, max |delta
value| 0.0**. Not "equal within tolerance" -- identical, so the numpy path is not an approximation of
the trained one and the claim does not need a caveat.

**Where the 3.3 ms actually goes** (`cProfile` over 135 boards on the shipped interpreter, so the
absolute seconds carry profiling overhead and only the shares should be read):

| share of `decide()` | frame |
| --- | --- |
| 37% | `combat/observation.py::build_observation` |
| 23% | `Board.from_fight_state` |
| 20% | `LivePolicy._forward_bundle` (of which the model `forward` itself is 12%) |
| 15% | `_fighter_dict` / `_spell` / `_effects` -- the per-spell parse, 3,694 and 7,388 calls for 135 boards |

**[Corrected by §L.33: this table is a cProfile artifact and the shares are wrong.]** cProfile
instruments every Python-level call, so a frame that is cheap but numerous is priced as if each call
were free of everything around it. Timed as wall clock in one interleaved process, the same 135 boards
give `build_observation` **20.1%** of `decide()` (not 37%) and `Board.from_fight_state` **20.2%** (not
23%), leaving ~55% for the forward, the slot/target binding and the realization rule. The table above
is kept because it is what was measured and because the error is the interesting part: it is what made
#82 look worth 37% of a request when it was worth 2.6% of 20%.

The headline is that the network forward is 12% of the request and the surrounding data plumbing is
~60%. **[§L.33: refuted -- plumbing is 40%, and the forward's share was understated because cProfile
charges Python frames and not C ones.]** One correction to say how that plumbing costs, because the obvious story is wrong: the payload
is **not** parsed twice. `build_observation` already takes a parsed `Board`
(`combat/observation.py:252`), so the two `_spell` frames in the profile are different functions --
`battle_solver.py:713` building `LiveSpell`s from the payload, and `observation.py:55` serializing
already-parsed spells into observation dicts. What the 37% actually does is re-walk every fighter's
whole spellbook on every request: `everything = self_spells + each fighter's spells`, then
`for spell in everything: for effect in spell.effects + spell.critical_effects` to collect effect
kinds and unmodeled ids, plus a dict per spell (3,538 `_spell` calls for 135 boards of 23 spells).
That is derived data over immutable `LiveSpell` tuples -- exactly the thing this codebase already
caches twice elsewhere (`Duelist._feature_cache`, `Fighter._target_stats`) -- so it is a memoization
target rather than a redesign (#82).
The verification is already built -- 135-board per-field parity against the current code, which today
shows exact equality, so any such change must keep that property exactly and can be checked in seconds.

### L.32 The out-of-beam casts are not where the damage is: +0.033 for 29% of the turns

#80's prime suspect was `cast_outside_beam` -- the 951 of 1,945 casts (§L.21) whose (spell, target)
pair the deterministic beam never offered, i.e. the head plainly overriding the planner. The knob is
`tools/wsp_fight.py --abstain-class outside_beam`, which hands such a turn to the beam's top plan and
counts it as a fallback. Same 200 matchups / 400 games, `none` verified first: it reproduces the
unmodified arm's toy win rate exactly, so the shared `beam_pairs` refactor of the tier counter changed
no behavior.

| arm | turns given to the beam | win rate | CI95 | vs shipped head | vs the 0.4938 ceiling |
| --- | --- | --- | --- | --- | --- |
| shipped head | 0% | 0.4062 | 0.379-0.433 | -- | -0.0875 |
| gate out-of-beam casts | 29.1% (566) | 0.4387 | 0.416-0.460 | **+0.0325** (CI 0.010-0.058), p 0.015 | -0.055 (CI -0.08--0.03), p 1.1e-4 |
| gate every walk (`any_walk`) | 53.9% | 0.4062-ish at n=8 | -- | -- | -- |

The pre-registered bar in #80 was "recovers >= +0.03 at <= 15% beam share", because §L.29's curvature
says a real class should be cheap to remove. This class buys +0.0325 for 29.1% of the turns -- about
one point of recovery per ten points of substitution, which is what proportional looks like. So the
suspect is cleared: removing the head's most distinctive behavior, the half of its casts the planner
would never have chosen, recovers barely a third of the gap and leaves 0.055 below the solver on the
ceiling that §L.31 measured.

Combined with §L.30 (the losses are not against tougher builds) the picture is consistent and
unhelpful: the deficit is not concentrated in a shippable predicate over decisions either. It is
spread across the head's policy generally, which is where the three independent lines of evidence now
point the same way -- the only thing that fixes 0.406 is training, and the cheap knobs (§L.25 weight
edits, §L.29 confidence gates, §L.32 class gates) all converge on "less head, better score" without
ever crossing the solver.

`any_walk` was left as the one loose end at n=8, where 30 of 56 turns were substituted and the win
rate did not move. Running it at 200 disproves that reading: **0.4412** (CI 0.418-0.464),
**+0.0350** over the shipped head (CI 0.009-0.060, p 0.011) for 28.4% of turns substituted, and
**-0.0525** below the 0.4938 ceiling (p 9.5e-7). So the toy was noise, exactly the mistake §L.18 said
n=40 was for -- a smaller sample agreeing with a hypothesis is not evidence.

The two class gates are then indistinguishable per unit substituted: 0.0325 / 0.291 and
0.0350 / 0.284, both ~0.12 of win rate per unit of beam share, both leaving ~0.055 on the table
against the ceiling. Which is the concentrated-curve signature failing to appear for either candidate
class, and the fourth independent confirmation of the same conclusion: the deficit belongs to the
head's policy as a whole. What remains genuinely unmeasured is the live-path question -- §L.18 found
every `policy_direct` pair among the beam's candidates on 294 recorded boards, so if the ~49% out-of-beam
share is an artifact of the duel's small builds, neither gate has anything to act on in real fights.

### L.33 #82 refuted: the per-spell walk is worth 2.6% of 20% of a request, and the profiler said 37%

#82 was pre-registered on the strength of §L.31's table: memoize the per-spell walk, because
`build_observation` looked like 37% of `decide()` and `_spell` looked like a third of that. The
memoization itself is easy and the design was sound -- `LiveSpell` and `LiveEffect` are both
`@dataclass(frozen=True)`, so a parsed spell is a provably complete memo key (every field `_spell`
reads is a field of the key), and the hit rates over the 135 recorded boards are as good as anyone
could want:

| memo | calls | distinct keys | hit rate |
| --- | --- | --- | --- |
| `_spell`, keyed on the whole `LiveSpell` | 3,538 | 88 | **97.5%** |
| `LiveEffect.kind`/`element`, keyed on `effect_id` | 12,617 | 58 | **99.5%** |

Both were built, and both were bit-exact: the full observation plus contract for all 135 boards,
dumped as canonical JSON, is **5,079,902 bytes with md5 `205cd6e316328123659679ed00744384` on both
arms**, and `build_observation` is idempotent when called twice on one board. So correctness was never
the question. The question was whether it was worth anything, and the answer arrived in four steps
that disagree with each other until the last one:

1. **Isolated component loop**: `_spell` alone goes 7.152 -> 0.499 us per call, **14.7x**, i.e.
   0.193 -> 0.013 ms per board. Against a `build_observation` of 0.613 ms that predicts a 29% cut.
2. **Cross-process paired A/B**, 8 interleaved rounds, one process per arm: **+2.3%**, faster in
   **4/8** rounds, sign p = 1.0 -- and the *same* arm's round-to-round spread was 15.8% before and
   20.9% after. The effect is inside the harness noise.
3. **In-process forced miss** (the memo's `get` patched to always return None, same boards, same
   objects): miss is +0.345 ms per board, **10/10 blocks**. Looks decisive, but this arm is *not* the
   old code -- with the mechanics walk now routed through `_spell`, a miss rebuilds the entry twice per
   board instead of once, so it prices the memo at roughly double its value.
4. **The decisive one**: load the pre-change `observation.py` as a second module object bound to the
   same `combat.battle_solver`, so both implementations run in one process on identical `Board`
   objects, alternating, 48 blocks. Answers verified identical on all 135 boards first. Result:
   **before 0.5718 ms/board, after 0.5569, mean delta +0.0149 ms = 2.6%, sd 0.0706, paired t = 1.46
   (p ~ 0.15), after faster in 28/48 blocks.** At n=12 the same harness said 3.24% with t = 1.74;
   quadrupling the sample moved the point estimate *down* and the spread *up*.

Step 1 is the artifact, and it is worth naming precisely because it will recur: a tight loop over the
same 27 objects prices a function in isolation, with warm sub-caches and nothing competing for the
allocator, and that price does not transfer. `_spell` measured 31% of `build_observation` alone and
2.6% of it in place. Component timings on this workload are not additive, so only the end-to-end
paired number is admissible. This arm is also conservative -- the before module inherits the memoized
`LiveEffect.kind`, so it is priced slightly cheaper than the true pre-change tree.

**Both memos were reverted.** `combat/observation.py` is byte-identical to HEAD again and
`combat/battle_solver.py` differs from HEAD only by the two methods #73/#74 added. The reason to
revert rather than keep a favorable-looking point estimate: 2.6% of the 20.1% that `build_observation`
actually is comes to **~0.5% of a request**, which cannot be distinguished from zero here, and the
price is 40 lines, a module-level cache of up to 512 serialized spellbooks resident on a 7.8 GB box
where residency is a tracked cost, and a shared-dict hazard -- the memo hands out a dict that one call
site has to remember to copy before writing `geometry` into it. Code that cannot be shown to move the
metric is not cheaper.

**What the wall clock says instead** (9 interleaved blocks, best of 3, dev interpreter with jax, 135
boards, per board):

| stage | ms | share of `decide()` |
| --- | --- | --- |
| `decide()` total | 3.193 | 100% |
| `Board.from_fight_state` | 0.646 | 20.2% |
| `build_observation` | 0.641 | 20.1% |
| `validate_observation` | 0.020 | 0.6% |
| `_bind_slots` + `_bind_targets` | 0.129 | 4.0% |
| remainder: forward, plan, realization | ~1.758 | ~55% |

**The real finding, and it is #83, not #82.** Inside `decide()` the observation is read for exactly
two things: `contract = validate_observation(observation)` supplies the `actionable` early-return gate
and `value_ready` (which gates the critic that §L.30 showed is never asked), and `base["observation"]`
carries it out in the response. The decision itself -- `_bind_slots`, `_bind_targets`,
`board_encounter`, `_bundle`, `_choose`, `_realize` -- reads `board`, never `observation`. So 20.1% of
a live request builds a structure the decision does not consume. The only offline consumer found is
`combat/grid_probe.py --decisions`, which reads `decision.observation.board` -- the four-key sub-dict
(`my_cell`, `cell_count`, `map_id`, `occupied_cells`), not the 26 serialized spellbooks or the
`derived` damage estimates. A cheap `validate_board(board)` producing the same two booleans plus that
small `board` block is worth ~20% of `decide()`, roughly **8x** what the memo was worth, and it is
priced rather than guessed. It is not done here because it changes what `live_policy_decisions.jsonl`
and the brain's HTTP response carry, which is other tools' evidence, so the consumer audit is the
first step of the item and not something to settle mid-revert.

Two instrument lessons to carry, both cheap and both repeatable:

* **cProfile shares may not be used to size a saving.** They sized #82 at 37% of a request; the wall
  clock says 20%, and the memoizable part of that 20% is 2.6%. Use the profiler to nominate
  candidates and nothing else.
* **Cross-process A/B on this box has 16-21% round-to-round spread**, which is larger than most
  optimizations being considered. Load both implementations into one process and interleave them, or
  the measurement is noise with a sign attached. §L.24's 3.3 ms vs 3.8 ms figures were cross-process
  and are quoted as rough for that reason; anything finer needs the in-process harness used here.

### L.34 #83 audited and priced: 92% of the observation path is unread, and the replacement is exact

§L.33 filed #83 with one consumer known and the rest assumed. The audit is now complete, and it
corrects §L.33 in one respect that matters: the observation is built in **three** live places, not
one, so the waste is not confined to the brain's request.

**Every producer of `build_observation`'s dict, and what each consumer actually reads.**

| producer | what it does with the dict | who reads that |
| --- | --- | --- |
| `combat/live_policy.py:603` | `contract = validate_observation(obs)`, then `base["observation"] = obs` in the HTTP reply and the decision log | the reply: `scripts/mobile-disguise.js:2446` reads `reply.ok`, `reply.action`, `reply.policy`, `reply.reason`/`reply.error` -- never `reply.observation`. The log: `combat/grid_probe.py:51` reads `decision.observation.board` only |
| `agent/executor.py:1117` (`_attach_observation`, called at `:1506` for every `fight.state` dispatch) | `data["observation"] = obs` and `result.data["observation"] = obs` | because `fight_state()` returns that same mutated `data` object, `Autopilot._record_fight_board` (`agent/autopilot.py:1282`) writes it to `fight_boards.jsonl`. No tool reads it back: `tools/policy_over_record_boards.py` reads `board` (`:126`)
and `board.contract.actionable` (`:137`), and `tools/brain_readiness.py` rebuilds its own contract |
| `agent/executor.py:1244` (`fight.plan`) | `data["observation"] = obs`, `data["observation_version"]`, `data["contract"]` | `contract["value_ready"]` at `:1258`. The observation reaches the model only through `agent/tool_dataset.py:182 _observation_text`, which truncates to 1,200 characters |

Checked and clean -- none of these reads any part of a fight observation: `combat/combat_ai_server.py`
(zero occurrences; it serializes `decision` verbatim at `:140` and returns it at `:180`),
`combat/brain_service.py` (the word appears only in its docstring), `combat/board_pca.py`,
`combat/grid_dataset.py`, `tools/damage_calibration.py:101-134` (reads `decision.slots` and
`decision.targets`; its `observation` mention at `:249` is a comment about `fight_boards.jsonl`),
`tools/capture_fight_outcome.py:84` (writes its own one-key marker), `tools/test_fight_state_fields.js`
(zero occurrences), and `combat/test_live_policy.py` (zero occurrences). The many `row.get("observation")`
hits in `tools/build_live_transitions.py`, `tools/dialog_labels.py`, `tools/group_scan.py` and friends
are the **agent step observation**, a different structure; so are `combat/fight_estimate.py:1082`
(`map`/`groups`) and `combat/encounter/benchmark.py:409` (its own sim array). `agent/regression.py`'s
1,830 checks never touch the fight observation, which is why the gate cannot catch a change to it.

**What that costs on disk.** Compact-serialized (the shipped writer is orjson, so this is the same
byte count the files actually carry, not `json.dumps`' spaced default):

| file | size | rows | observation bytes | share | what is read back |
| --- | --- | --- | --- | --- | --- |
| `trajectories/live_policy_decisions.jsonl` | 27,232,409 B | 810 | 25,772,507 B (mean 31,817 B/row) | **94.6%** | `observation.board` = 64,990 B = **0.24%** |
| `%LOCALAPPDATA%\DofusFarm\fight_boards.jsonl` | 23,694,872 B | 302 | 9,038,440 B over 294 rows (mean 30,742 B) | **38.1%** | nothing |

Inside the observation the unread part is concentrated: `self` 77.9%, `derived` 12.4%, `enemies` 7.4%
-- the serialized spellbooks, the per-(spell, enemy) damage estimates and the geometry -- against
`mechanics` 0.8%, `fight` 0.5%, `allies` 0.3% and the `board` block that is actually read. So
**34.8 MB across the two logs is written and never read by anything**, and §L.18's "294 recorded
boards" is that second column: the corpus the whole §L analysis runs on comes from
`fight_boards.jsonl`'s raw `fight.state` payload, not from the observation nested inside it, which is
why dropping the observation does not touch the evidence base.

**The replacement is built and it is exact, not approximate.** A prototype `contract_from_board(board)`
-- 90 lines, reading only `Fighter` scalars, `LiveSpell.id`/`ap_cost`/`is_weapon`/`effects` truthiness
and `LiveEffect.kind` -- plus `board_block(board)` for the four keys, run against the shipped
`validate_observation(build_observation(board))` on all 135 recorded own-turn boards:

* **contract mismatches: 0 / 135** (the whole dict: `version`, `ok`, `actionable`, `value_ready`, and
  the `errors`/`warnings` lists exactly, including the `unmodeled_effects:` join and the per-spell
  `self_spell_effects_missing:{id}` warnings); 120 of 135 boards actionable, as shipped.
* **board-block mismatches: 0 / 135**.
* **price, shipped interpreter (`runtimes/python` 3.11.9, numpy backend), 9 interleaved rounds x 6
  passes x 135 boards, order rotated per round, best of 9:** `build_observation` + `validate_observation`
  **0.55685 ms/board** (rounds 0.557-0.595) against the prototype **0.04578 ms/board** (rounds
  0.046-0.049). Delta **0.51107 ms = 91.8% of the observation path**, which is **16.0% of `decide()`**
  at §L.33's 3.193 ms. The two distributions do not overlap at any round, so unlike #82's 2.6% this is
  not an estimate that a larger sample could move -- it is a factor of 12.

**Landed, and measured through the production function, paired.** The three producers now
call one entry point, `combat/observation.py:live_view(board)`, which returns
`({"schema_version", "source", "board"}, contract)` by default and the whole observation when
`DOFUS_FIGHT_OBSERVATION=full`. That flag is read per call, so the same loaded policy, the same 135
boards and the same code path give both arms of one paired A/B -- and the arm that is being compared
is `combat_ai_server.decide()`, the actual production entry point, with `_log_decision` stubbed so
the measurement does not write a row into the evidence file §L.34 quotes.

12 paired rounds x 3 passes x 135 boards, order rotated within each round, shipped interpreter:

| arm | median ms/turn | round range |
| --- | --- | --- |
| full observation | 3.313 | 3.153-3.486 |
| slim observation | **2.660** | 2.573-2.781 |
| paired delta | **0.668 ms** (mean 0.660, sd 0.129, min 0.490, max 0.852) | slim faster **12/12**, sign test p = 0.00049 |

**20.2% of the request.** The reply payload goes 32,041 -> **1,861 B (-94.2%)**, which matches the
94.6% log measurement because the log row is the reply.

One measurement lesson, because the first number here was wrong and it is the mistake §L.33 just
recorded: taken as best-of-8 *rounds per arm* the same change read 0.794 ms / 21.7%, and a single
pass read 0.447 ms / 14.2%. Comparing two minima rewards whichever arm drew the luckier round on a
box with 16-21% round spread; the paired per-round delta is the only one of the three with a sign
test, and it is the number to quote. The absolute saving does land where the isolated component
measurement predicted it (0.483-0.511 ms), which is the useful cross-check: the prediction was 16.0%
only because it was divided by the dev interpreter's 3.193 ms, and the shipped request is smaller
than that here.

**The non-regression proof, which is the claim that matters:** running both arms on all 135 boards
through `combat_ai_server.decide()`, `action`, `contract` and `reason` are **identical on 135/135**.
The observation was never read by the decision; removing it changed nothing about what the page is
told to do. Everything else is the same equality the prototype had --
`tools/observation_contract_probe.py` is the landed instrument and reports 0 mismatches on contract,
board block, slim `live_view` and full `live_view` over the corpus.

`agent/regression.py` now runs that parity as `test_slim_live_observation` (7 checks), because the
gate had **no** check on the fight observation before and so could not have caught the change or any
later drift in it. It pins both ends, per the house rule that a probe must be able to fail: the
synthetic board must be `actionable` and *not* `value_ready` with an `enemy_spells_missing:` warning
(a contract that is trivially empty would make the equality meaningless), the slim reply must be more
than an order of magnitude smaller, `DOFUS_FIGHT_OBSERVATION=full` must restore the exact full dict,
and killing the only enemy must add `no_live_enemies` and clear `actionable` **identically in both
arms**. Gate total: **1,837 checks, 0 failed** (was 1,830; +7 here), `combat/` 103 passed, JS 55
passed, toolkit mirror 208 sync / 8 pinned / 0 drift.

The shipped artifact was rebuilt and checked, not assumed: `compile.bat` ->
**setup.exe 57,199,616 B, 507 payload files** (506 before; the new probe tool is the extra),
`tools/payload_audit.py setup.exe --gate` **PASS** at 54.5 MiB under the 60.0 MiB budget with no
forbidden entry and no secret-shaped value, and every changed file read back *out of the built
binary* is byte-identical to the tree -- `combat/observation.py` 25,365 B, `combat/live_policy.py`
33,517 B, `agent/executor.py` 89,707 B, `agent/regression.py` 816,587 B,
`tools/observation_contract_probe.py` 6,818 B, all `exact=True`. An installer that does not carry the
fix would make every number above a claim about the tree rather than about what ships.

What is deliberately unchanged: `build_observation` and `validate_observation` stay in place and
unmodified, because `tools/brain_readiness.py:64` rebuilds a contract from a recorded row that has
no `board.contract` (294 of 302), and the sim/training path uses `observation_from_board`. The full
observation is one env var away, so the diagnostic capability §L.33 worried about losing is retained
rather than deleted.

**The numbers are references now, which is the first piece of #81.** `observation_contract_probe.py`
writes `data/fight_observation_baseline.json` -- the same convention as `data/last_gate_run.json` --
and `tools/doc_facts.py` exposes five facts from it: `fight_obs_boards`, `fight_obs_mismatches` (the
four parity counters summed), `fight_obs_full_ms`, `fight_obs_slim_ms`, `fight_obs_removed_pct`.
`CURRENT.md` quotes them as `{{fact}}` references, so `--render` rewrites them and `--check` fails if
the code moves under them. This already earned its keep twice in one session: `gate_checks` and
`setup_bytes` each went stale the moment the check count and the installer changed, and both were
corrected by re-rendering rather than by someone retyping a number. What #81 still owes is the
*quality* side -- head win rate 0.4062, the 0.4938 abstain ceiling, the §L.29-§L.32 deltas -- because
those need `tools/wsp_fight.py` to write its own baseline, and a 200-matchup run owns the box for long
enough that it cannot be sandwiched between latency measurements (§L.33's other lesson).

Two things the audit settled that are worth not re-asking:
`Board.from_fight_state` is **not** a second copy of this win. Its spell parse is load-bearing for the
decision -- `plan_value._enemy_rows` (`:136`, `:232`, `:365`) feeds each enemy's spellbook into the
actor's own input tables, so skipping it would change what the head does, not just what it costs. The
fields that #83 did orphan (`last_actions`, `states`, `summoner_id`, `is_companion`,
`creature_generic_id`) are each a single attribute read and are still required by
`Board.to_fight_state()` (`:801-818`), which is the round-trip seam §L.19 measures the live policy
through -- deleting them would cost the offline tooling and save nothing.

### L.35 #81's quality side landed, and the assumption that blocked it was wrong

#81 had been parked on the belief that pinning the head's win rate needed a box-owning job: a
200-matchup duel run was assumed to be hours, so it was queued behind the mutation sweep instead of
done. `wsp_fight.py` records its own `elapsed_s`, and reading that number instead of remembering the
task took the premise out: the 200-matchup production arm takes **57.1 s** and the abstain arm
**33.9 s**. The whole job was about ninety seconds, and it had been deferred for a day on an estimate
nobody checked. That is the same failure as §L.18's n=40 and §L.32's n=8 -- acting on a remembered
figure rather than the one the instrument prints.

Re-run on the post-§L.34 tree, into tracked evidence rather than temp:

| quantity | published (§L.31) | re-measured now | source |
| --- | --- | --- | --- |
| head win rate, production policy | 0.4062 (CI 0.379-0.433) | **0.4062** (CI 0.3787-0.4325), 1 W / 157 D / 42 L | `data/fight_head_production.json` |
| solver ceiling, `--abstain 1.01` | 0.4938 (CI 0.4838-0.5038) | **0.4938**, 2 W / 192 D / 6 L | `data/fight_head_ceiling.json` |
| paired delta, 200 matchups | +0.0875 for the solver | **-0.0875** for the head; 40 worse, 3 better, 157 unchanged, sign test p = 3.02e-09 | `data/fight_head_vs_solver.json` |

All three reproduce to the last digit, which is a stronger confirmation of §L.34's parity claim than
the per-turn check alone: the slim view did not change what the head does, at the level of *who wins
400 games*, not just of which action a board emits. The three documents are now what
`tools/doc_facts.py` reads, through eight new facts (`fight_head_win_rate`, `_ci_low`, `_ci_high`,
`_wins`, `_draws`, `_losses`, `_cost_vs_solver`, `fight_ceiling_win_rate`), and `CURRENT.md` quotes
them as references.

One instrument note, because it cost two rounds: a **negative fact does not survive the round trip**.
Rendered, `-0.0875` came out as `--0.0875` next to markdown emphasis and `--check` then read the
literal as `0.0875`, so it reported a mismatch against a number that was on the page correctly. The
fix is not a parser patch -- it is that `fight_head_cost_vs_solver` exposes the magnitude and the
sentence carries the direction ("the head's own decisions cost 0.0875 ... the head is *below* the
ceiling"). A reference should be a positive quantity with its sign in prose.

Non-vacuity, since a pinned number nobody has seen fail is decoration: `CURRENT.md`'s tagged win rate
was tampered from 0.4062 to 0.4100 and `--check` named the line, the fact and both values
(`says 0.4100, measured 0.4062`); `--render` then restored the file byte-for-byte. So the check
bites and the recovery is the same command that produced the number.

What #81 still owes is smaller than the part now closed: §L.33's wall-clock decomposition table and
the §L.29-§L.32 gate/arm figures are still prose. Those need their own writer, not a re-run --
`tools/wsp_fight.py` already emits a document per arm, so an abstain-class or confidence-gate arm can
be pinned the same way whenever one is next measured.

### L.36 The brain's memory footprint, finally measured -- and #83 saved about 1 MB, not a lot

The cost ledger had one blank row: the fight brain's own residency. An earlier probe returned null
because it passed a real process handle to `GetProcessMemoryInfo`; the repo already had a correct
reader in `tools/knowledge_index_probe.residency()` (INVALID_HANDLE_VALUE, `argtypes` declared --
without them the call silently returns 0), so the gap was a bug in a caller and not a missing
capability. `tools/brain_ram_probe.py` now imports that reader and writes
`data/brain_ram_baseline.json`, measured on `runtimes/python` 3.11.9 with the numpy backend,
675 streamed decides.

| stage | resident MB | commit MB | what the jump is |
| --- | --- | --- | --- |
| interpreter | 14.7 | 9.7 | the embedded Python itself |
| + `import numpy` | 24.2 | **243.0** | +9.5 resident but **+233 commit** -- numpy's arena reservation |
| + combat imported | 30.5 | 248.1 | battle_solver + observation + damage calculator |
| + checkpoint loaded and warm | 32.9 | 282.1 | **the trained head is 2.4 MB resident** |
| after 675 decides | 33.7 | 283.3 | steady state |

**Peak working set 146.8 MB, resident 33.7 MB, commit
283.3 MB.** Two things worth more than the totals:

* **The head is cheap; numpy is not.** Loading and warming the policy costs 2.4 MB resident and
  34 MB commit, while `import numpy` alone reserves 233 MB of commit. So any future "shrink the
  brain" idea aimed at the weights is aiming at 2.4 MB of a 283 MB commit. Commit is not residency
  on this box (the standing host-quirk finding), which is why the 233 MB is a budget note and not a
  233 MB hole in a 7.8 GB machine.
* **#83 bought CPU and bytes, essentially no memory.** slim 33.7 / 33.9 resident and 146.8 / 147.0
  peak against full 34.3 / 34.8 resident and 147.4 / 147.8 peak, two runs each: about **1 MB** of
  resident and peak, consistently in the right direction but ~3% of the process. The 35 KB of dicts
  per request were transient and the allocator reused the arena. The honest claim for §L.34 is
  therefore "faster and 94% smaller on the wire and on disk", not "smaller footprint" -- and it is
  worth saying plainly that a change this size did *not* move RAM, because "cheaper" invites
  assuming every saving is a memory saving.

`decide()` here measures **2.946 ms** on the shipped interpreter versus 3.71 ms with
the full observation, i.e. **20.4%**, which lands on §L.34's paired 20.2% from a completely different
harness -- two instruments, same delta. That agreement is the reason to trust the 20% rather than the
14.2% single-pass reading.

Two measurement faults to keep, both caught before anything was published:
`own_turn_payloads` reads and fills a board inside the timing loop, so the loop's wall clock is
7.9 ms per iteration while `decide()` itself is 2.9 ms. The tool's first draft called the loop figure
`ms_per_decide`, which would have gone into a document as a latency. It now times `decide()` alone and
publishes `ms_per_loop_iteration` separately.

**#84, the peak attribution, and the answer was not the question.** Three arms in separate processes,
twice each, because peak is cumulative and cannot be reset in place: `floor` = load the head, warm up,
decide **one** board; `parse` = fill and parse all 135 boards and never call the model; `loop` = the
5 x 135 sustained run.

| arm | resident MB | peak MB | commit MB |
| --- | --- | --- | --- |
| floor, 1 decide | 32.7 / 33.3 | 146.0 / 146.1 | 281.1 / 282.2 |
| parse, 135 boards, no model call | 33.8 / 33.7 | 146.2 / 146.2 | 284.3 / 284.3 |
| loop, 675 decides | 33.9 / 34.1 | 146.9 / 147.3 | 283.1 / 283.0 |

The premise of the item was that the peak belonged to either the forward or the parse and a third arm
would separate them. It does not: **both reach ~146 MB independently**, one board's decide as surely as
675 of them. So the correct reading is that ~146 MB is a transient high-water mark the process touches
as soon as it does any real work at all -- resident stays at 33-34 MB in every arm -- and asking which
stage "owns" it is asking the wrong question, because it is not a property of the workload.

What actually matters for planning, and is the useful output of the run: **the peak does not grow with
load and residency does not drift.** 675 sustained decides end at 33.9-34.1 MB resident against 32.7-33.3
after one, with the peak unmoved. On the standing host-quirk finding that commit is not residency, the
number to plan instances against is ~34 MB resident, not 147 MB peak, and the brain can fight a whole
encounter without growing. The tempting story -- "the forward needs 113 MB" -- is exactly the kind
§L.33 got caught asserting, and the data refuses it.

This section keeps its numbers written out, because it is a log of particular runs: `docs/ARCH_REASSESSMENT.md` is deliberately not in `tools/doc_facts.py`'s DOCS list, and an auto-render would rewrite history the first time the tree moved. The live claim -- what the brain costs today -- is tagged as references in `docs/CURRENT.md` instead.

### L.37 §L.34's own file had no witness: four mutations added, four caught, and one needed the arm written for it

#83 replaced the live path's contract source, passed 1,837 gate checks, and was rebuilt into the
installer. None of that proves the checks can *see* the new code. `tools/mutate_cause_gate.py` had a
target for `combat/live_policy.py` (`LPOL`) and one for `combat/battle_solver.py`, but **no target at
all for `combat/observation.py`** -- the file where `contract_from_board`, `board_block` and
`live_view` now live -- and `test_slim_live_observation` was not in the sweep's `SNIPPET` test list
either. So the honest answer to "would a break in the new code be caught" was not "yes, the gate is
green" but "never tested" -- the same hole §L's knowledge-index item found, where mutations written
against an unwitnessed file report MISSED with zero failing checks, and the fault is in the sweep's
reach rather than in the gate's strength.

Added an `OBS` target and four mutations, each anchored to text **unique to the slim function**. That
matters more here than usual: several of `contract_from_board`'s lines are character-for-character the
same as `validate_observation`'s, and the sweep applies the first match. An ambiguous anchor would have
mutated the full path, moved both arms together, kept the parity check green, and reported MISSED --
indistinguishable from a genuinely weak check. Every anchor therefore carries a neighbouring line only
one version has (`f"...{ident}:{spell.id}"` against the full path's `{spell.get('id')}`, `enemy.alive`
against `enemy.get("alive")`, `me is not None` against `fight.get("in_fight")`).

`MUT_FILES=observation.py` -- **4 of 4 CAUGHT**, and `restored: 0 failing checks, exit 0`:

| mutation | the check that failed |
| --- | --- |
| a dead enemy still counts as live advice | the degraded board moves both contracts identically |
| the enemy-stats warning stops being published | slim contract equals full; `live_view` shape |
| actionable inverts its self check | slim equals full; non-vacuity (`actionable` must be True) |
| occupied cells inverts its filter | board block unchanged; `live_view` shape |

The first row is the point of the exercise. Dropping the `enemy.alive` filter is **invisible on every
healthy board** -- nothing is dead, so the two contracts agree with or without it. It is caught only by
the arm that kills the sole enemy and asserts the contract still matches *and still changes*. Had
`test_slim_live_observation` pinned only the equal-arms property, as its first draft very nearly did,
that mutation would have been MISSED and the file would still have looked covered. Pinning both ends is
not ceremony: here it is exactly one quarter of the coverage.

Plan rises 563 -> **567** mutations and the snippet to **93** tests; both are `doc_facts` facts
(`mutations`, `snippet_tests`), so the prose moved by re-rendering rather than retyping, and the gate
stays at 1,837 checks / 0 failed. #18's re-sweep now has four more rows to run, and it remains the item
that turns "an anchor exists" into "the sweep ran it on this tree".

### L.38 The audit generalized: 14 changed files with no anchor, and the worst hole was the checker

One unwitnessed file is a find; the question is whether it is a pattern. Diffing the session's 49
changed files against the sweep's 61 mutation targets: **14 changed `.py` files have no anchor at
all**, including `combat/plan_value.py` (the module that builds the actor's own input tables, which
§L.34 just showed is load-bearing), `combat/combat_ai_server.py` (the production entry point and the
decision-log writer), `combat/encounter/model.py`, `combat/encounter/checkpoint.py`,
`combat/grid_infer.py`, `agent/brain_daemon.py`, `networkhealth/sentinel.py`, `tools/doc_facts.py`,
`tools/mutate_cause_gate.py` itself, and three test/analysis files where an anchor would be odd.
That list is the honest scope of what "the gate is green" does *not* cover, and it is worth more than
another mutation to a file already witnessed.

The one that mattered most was `tools/doc_facts.py`, because of what §L.35 had just demonstrated:
doc_facts is now the thing that catches a stale win rate, a moved installer size and a drifted gate
count. **A checker cannot be caught by the checks it runs** -- mutate `check_docs` so it returns no
problems and the gate's own "every tagged number equals its measurement" check goes green, because it
is asking that mutated tool. So it needed an arm that does not trust the verdict:
`test_doc_facts_self_witness` points `DOCS` at a throwaway file and pins four behaviours, three of
which must FAIL -- a matching tagged number is not a problem, the same number off by one is named
with both values, an unrendered `{{fact}}` is reported rather than silently shipped, and a tag naming
no fact is a problem. It leans on a fact that has a measurement (`gate_checks`) and stops rather than
passing if that measurement is ever absent.

Two mutations against `doc_facts.py` then proved the hole was not theoretical:

| mutation | failing checks |
| --- | --- |
| a stale tagged number stops being a problem | **1** -- the off-by-one arm |
| an unrendered reference stops being reported | **1** -- the never-rendered arm |

Each was caught by exactly one check, and that check is the new fixture. Before it, both reported
MISSED with the gate otherwise green: the sweep had no way to notice that the tool had stopped
noticing. `MUT_FILES=doc_facts.py,observation.py` now runs **6 of 6 CAUGHT**, `restored: 0 failing
checks, exit 0`.

Gate total **1,842 checks / 0 failed** (was 1,837; +5 here), mutation plan **569**, snippet **94**
tests -- and the two counts again arrived by re-rendering, not by typing, including the off-by-one-run
flap where the gate's own count in the docs lags one run behind because `last_gate_run.json` is
written after the check that reads it. Two render/gate cycles close it; the second cycle reporting
"rendered 0 doc(s)" is the fixed point, and it is what this section was written against.

What this does not do: it does not witness `plan_value.py`, `combat_ai_server.py`, `model.py` or
`checkpoint.py`. Those need checks that do not exist yet -- a fixture pinning the shape of the
encounter tables, and one pinning the decision log's writer -- and inventing mutations for them would
produce honest MISSED rows rather than coverage. The right order is check first, then the mutation
that proves the check bites, which is what §L.37 and this section each did.

### L.39 The encounter tables are now witnessed, and the boss arm is what proved the rule

Following §L.38's own instruction -- check first, mutation second -- `test_encounter_input_tables`
pins `plan_value.board_encounter`, the function that builds the arrays the trained head reads. It
builds the synthetic board three ways (clean, `is_boss` set, enemy given a real damage spell) through
`BattleSolver.plan_turn` and asserts the player and enemy rows, the initiative and power fields, the
empty-spellbook case and the loaded-spellbook case. Then four mutations against a new `PVAL` target:

| mutation | caught by |
| --- | --- |
| enemy stops carrying its level as initiative | the actor-1 initiative assertion |
| **the boss power branch collapses (1.5 -> 1.0)** | **only the boss arm** |
| enemy spell rows never reach the tables | the loaded-spellbook assertion |
| the player's max HP is dropped from the spec | the actor-0 state assertion |

**4 of 4 CAUGHT**, each by exactly one check, `restored: 0 failing checks, exit 0`. The second row is
the one to keep: replacing the boss multiplier with the ordinary one is *identical* on every non-boss
board, so an assertion written only against the fixture as shipped -- hp 100, one normal enemy --
would have passed it, and the file would have looked covered while a boss fight silently changed what
the head reads. That is the third time in two days the same shape has bitten (#82's cache hit-rate arm,
§L.37's dead-enemy arm, here), which is why both ends of every branch is the rule and not decoration.

The third row closes the loop with §L.34: that section's conclusion was that `from_fight_state` could
not be trimmed because `_enemy_rows` feeds enemy spellbooks into these very tables. Until now that was
an argument from reading the code. It is now a check that fails when the feed is cut.

Gate **1,849 checks / 0 failed** (was 1,842; +7 here), mutation plan **573**, snippet **95** tests --
all three read off `doc_facts` rather than counted by hand, including this paragraph's first draft,
which said 96 and was wrong. Two render/gate cycles to the fixed point. Still unwitnessed:
`combat_ai_server.py`, `combat/encounter/model.py`, `checkpoint.py` (#85).

### L.40 The decision log gets its own fixture, and two of its four catches were crash-only at first

#85's second half. `combat/combat_ai_server.py` is the production entry point and the writer of the
row every offline read-out is built from, and it had neither an anchor nor a test: #13's glued-record
fix was pinned through `agent/jsonio.py`, which *is* a mutation target, so the server's own rotation
threshold, latency stamp and "an unwritable log must never stop a fight" promise were all outside the
sweep's reach. `test_combat_decision_log` now points the writer at a temp path with a stubbed policy
and asserts four things: one JSON record per line carrying the ask and the answer (including the
`challenges` a fight started with, which is what lets a later reward be attributed), the server's own
latency stamp, that the log rotates rather than growing without bound, and that what was rotated away
still parses. Then the failure path: with `LOG_PATH` set to a directory so every write raises,
`decide()` must still return a usable decision -- the contract that a diagnostic log can never cost a
fight.

Four `CSRV` mutations, run twice. The first run is the interesting one:

| mutation | first run | after fixing the fixture |
| --- | --- | --- |
| records get glued onto one line | CAUGHT, **by a crash** | CAUGHT by the readability assertion |
| the starting challenges stop being recorded | CAUGHT, **by a crash** | CAUGHT by the row-shape assertion |
| the rotation threshold never fires | CAUGHT by the rotation assertion | CAUGHT by two assertions |
| the server stops stamping its latency | CAUGHT by the latency assertion | unchanged |

Two of four were caught only because the fixture died -- `json.loads` on a glued line raises, and
`rows[0]["challenges"][0]` raises on the now-empty list. The sweep reports CAUGHT either way, so a
green sweep count hides the difference, and this project already treats a crash-only catch as weaker
evidence than a named one (#2 was spent re-verifying exactly that). So the parse became a helper that
collects bad lines instead of raising, the key reads became `.get()`, and the run was repeated until
**4 of 4 CAUGHT with no `ran to completion` entry** -- every defect now named by the check that saw it.

One more trap the same fixture would have walked into: `LOG_MAX_BYTES` was first set to 8 KiB, which
after §L.34 is large -- a slim row is ~200 B, so all 25 records fit in one file, nothing rotated, and
the "rotation" assertion would have compared an empty archive list and still passed. Dropped to 1 KiB
so the behaviour it claims to test actually happens.

Gate **1,855 checks / 0 failed** (+6), mutation plan **577**, snippet **96** tests -- all three read
from `doc_facts`, not counted by hand. Payload gate PASS 513 files / 54.6 MiB with every changed file
byte-identical out of `setup.exe`, mirror 0 drift, render at its fixed point. #85 now owes only
`combat/encounter/model.py` and `checkpoint.py`.

### L.41 The head's own forward pass gets pinned -- and two candidate mutations were refused, not run

#85's last two files. `encounter/backend.py` was already a mutation target, so the array shim was
covered while the layer logic sitting on top of it was not: `model.py` produces every logits and value
the live head acts on, and `checkpoint.py` decides how wide those inputs are. `test_encounter_model_and_checkpoint`
loads the real `models/encounter_selfplay.pkl`, runs `forward` on a ramped input, and pins: the value
inside its tanh range, the same weights and input giving identical logits twice, an illegal move
becoming ~1e9 nats worse **while no other actor is touched by someone else's mask**, a pass whose
target index cannot change its log-prob **while a real spell's can**, and the config loader reading its
nested `config` block, applying its own defaults, coercing a JSON string, and deriving `n_spells =
spells + 1`.

Two things the writing caught before the run, both worth keeping:

* **An assertion correct in intent and wrong in scope.** The first masking check was
  `XP.all(illegal - legal < -1.0)` and it FAILED on unmutated code, because only actor 0 was masked --
  every other actor's delta is legitimately ~0. A check that fails the correct code is worse than no
  check, because the temptation is to weaken it until it passes. The fix was to sharpen it: name the
  blocked actor, and assert the others are *unaffected*, which is a stronger property than the one I
  wrote by accident and is what makes the "masking deleted" mutation die.
* **A no-op mutation nearly shipped.** The first draft of the pass-target mutation was
  `True and XP.where(...)`, which is exactly `XP.where(...)` -- it would have been reported CAUGHT or
  MISSED for reasons having nothing to do with the code, and a green sweep would have enshrined a test
  that changes nothing. Rewritten to force the guard's condition true (`actions["spell"] >= 0`), so the
  target term is actually added for a pass.

Also refused: a dropped `tanh` and a misaligned `tokens[:, 1:]` slice. Neither is reachable from any
assertion in the fixture, and adding them would have manufactured two honest MISSED rows -- coverage
debt dressed as coverage. They belong to a future fixture with a golden output, not to this list.

**5 of 5 CAUGHT**, each named by an assertion rather than by a crash: illegal-move masking, the
pass/target guard, the nested config read, the loader's `max_rounds` default, and the `int()` coercion.

Gate **1,866 checks / 0 failed** (+11), mutation plan **582**, snippet **97** tests, installer rebuilt
-- payload gate PASS 513 files / 54.6 MiB, `model.py`, `checkpoint.py`, `combat_ai_server.py` and
`plan_value.py` all byte-identical out of `setup.exe`, mirror 0 drift, render at its fixed point.
That closes #85: the four files the head depends on and the sweep could not see are now four files it
can kill. #18 remains the item that runs all 582 against this tree.

### L.42 The two refused mutations became killable, and one only a fingerprint can see

§L.41 left two bug classes unpinnable -- dropping `tanh`, and misaligning `tokens[:, 1:]` -- because
no property in the fixture could reach them. A golden fingerprint can, so one was added: one forward
pass through the shipped head, recorded as the value head's output, the per-actor argmaxes of the move
and spell heads, and the three logit sums, with `DOFUS_GOLDEN=1` printing current values for whoever
retrains on purpose.

**The tolerance is the finding, not a detail.** The first draft stored the sums at four decimals and
compared exactly. Captured on both interpreters, the same input gives `sum(target)` = **1305.2408**
under jax/3.14 and **1305.2396** under numpy/3.11. §L.31's cross-backend claim is that the *decision
fields* -- probabilities and values -- are bit-identical; it was never a claim about a float32
accumulation over 45 logits, where summation order is allowed to matter in the last bits. Written as
exact equality the pin would have been a green check on one interpreter and a red one on the other,
which is the worst kind of test: it disagrees with itself about the code it is watching. So argmaxes
and the value stay exact (integers and a 5-dp float agree across backends) and the sums carry 0.02.

Both new mutations then ran, and the outcome splits cleanly:

| mutation | caught by |
| --- | --- |
| the value head stops being squashed into [-1, 1] | the tanh-range check **and** the fingerprint |
| the actor slice is misaligned by one token | **only the fingerprint** |

The second row is the argument for the whole exercise: `tokens[:, :-1]` is shape-valid, monotone, and
leaves every argmax where the property checks expect it. Nothing in §L.41's fixture -- not masking,
not the pass/target guard, not the config loader -- can see it. Only a pin on the numbers themselves
does. And the first row is why a fingerprint alone is not enough: dropping a monotone `tanh` leaves
every ranking intact, so the *range* property is what names it and the fingerprint merely agrees.
One catch each way is the case for keeping both kinds of check.

Gate **1,867 checks / 0 failed**, mutation plan **584**, snippet 97 tests. All **7**
`model.py`/`checkpoint.py` mutations now CAUGHT, `restored: 0 failing checks, exit 0`, so #85 is
fourteen-for-fourteen across its three files. Installer rebuilt and byte-verified, mirror 0 drift,
render at its fixed point. One drafting error worth naming: an edit meant to be cosmetic collapsed the
`def` line into its docstring and broke the module with an `IndentationError`; it was caught by running
the file rather than by trusting the edit, which is the only reason it is a footnote and not a shipped
broken tree.

### L.43 Half the runtime has no mutation anchor, so a clean sweep cannot prove coverage

#18 was progressing shard by shard while this measurement ran, and it exposed a limit on what those
shards can ever say. Counting every `.py` file under the runtime directories (agent, combat, ai,
networkhealth, sim; the Kaggle mirror and caches excluded) against the 66 targets the sweep actually
mutates:

| bucket | files | lines | no anchor | lines with no anchor | share |
| --- | --- | --- | --- | --- | --- |
| runtime | 146 | 71,408 | 114 | 35,614 | **49.9%** |
| offline tools | 54 | 19,617 | 32 | 9,559 | 48.7% |
| tests | 12 | 2,462 | 11 | 2,225 | 90.4% |

So the correct reading of today's **40 of 40 CAUGHT, 0 MISSED** is narrower than it sounds: it says the
*anchored* surface is sensitive. It cannot say anything about the other half, because a defect confined
to an unanchored file is invisible to the sweep by construction -- the sweep perturbs a file, runs the
snippet, and never touches that one. A full 584-mutation clean run would therefore be consistent with
half the runtime being unwatched, and reporting "0 MISSED" as a coverage result would be a category
error. This is §L.38's finding about `observation.py` measured at whole-repo scale rather than found by
accident a fifth time.

One caveat stated so the number is not overread: no anchor is not the same as untested. `combat/grid_probe.py`
and `combat/duel.py` are executed by checks and by the duel harness without ever being mutated; a defect
in them can still surface as a crash in an anchored file's snippet. The claim is precisely that the
sweep can never *directly* perturb these files, which is the weaker statement and the one the table
supports.

The part worth acting on is not the percentage but which files. Ranked by decision-bearing, unanchored,
largest first: `combat/online_learn.py` (552 lines -- the calibrator promotion gate, AUC/Brier/ECE and a
500-draw permutation test; a defect there ships a miscalibrated `p_win` and nothing else watches the
thresholds), `combat/duel.py` (595 -- the simulator every win rate in §L is measured in, so a silent
change moves all of them at once), `combat/fight_estimate.py` (1,593 -- group selection and the autopilot's
fight-join choices), `ai/lexical_policy.py` (647 -- the grounding seam #50 shipped, and #67 already found
one defect hiding in it that a static walk missed), `ai/tool_policy.py` (678), `ai/encoder_policy.py`
(1,181), `networkhealth/sentinel.py` (854).

That reverses the order of the two remaining jobs. Running all 584 mutations proves the existing 66
targets are alive; adding a handful of targets plus witnesses for the files above is what would make a
future clean sweep mean something. The right sequence is anchors first, then the full run -- otherwise
the sweep finishes green over a surface that structurally cannot fail, and the hours buy a number that
does not mean what people will think it means.

Measured with a throwaway script under the running sweep, deliberately: this section is prose in a file
the sweep's snippet never reads, so it cannot perturb the batch in flight. Writing to any mirrored or
anchored `.py` right now would make the mirror-drift check inside the sweep's own snippet fire, which
would report every remaining mutation as CAUGHT for a reason unrelated to the code -- the batch would
manufacture its own false positives, which is exactly why §L.38 excludes that test from the snippet.

## M. Why the head loses to its own fallback, and what will be done about it

§L.32 concluded "only training fixes 0.406" and stopped there. Four measurements say the deficit is not
a vague policy-quality problem but a specific, fixable wiring gap.

**M.1 The head is rarely choosing at all, and when it chooses it is right.** Running the 135 recorded
own-turn boards through the real `LivePolicy._bundle` masks: on the 32 boards where spell slot 0 is
legal the head picks slot 0 **32/32 (100%)**; on 72 boards with AP > 0 slot 0 is **masked** and the head
emits `pass`; on 31 boards AP is 0. Slot 4 *is* the pass index (`n_spells = spells + 1`, `env.py:56`), so
the shipped head abstains on **76% of live boards**, and mean legal spell slots per board is **1.56** --
pass plus at most one real spell. Over the same boards the beam solver produces a cast on **135/135**, and
its first cast is slot 0 in **128/135 (95%)**. The head's move head picks index 0 on 106 and index 2 on
29 while the solver moves on 3. So the turn is not "the head picks bad spells": **when it has a real
choice it agrees with the planner every time; when it does not, it passes instead of walking into range**
and the planner keeps casting. That re-reads §L.32 -- gating out-of-beam casts bought +0.03 and +0.035
because it substituted a branch that is rarely reachable, and no predicate over the head's decisions
could close the gap because most of those decisions were already forced by the mask.

**M.2 The head has never been trained against, or supervised by, the thing it is graded on.**
`encounter/train.py:473` accepts `--opponent heuristic | random | self`, and `encounter/pbt.py:96`
alternates `self` and `heuristic` across the population. `heuristic.py` is deliberately simple -- close,
cast the best affordable spell at the weakest valid target -- while acceptance scores against
`BattleSolver` at beam width 8, depth 2. The gradient path optimizes a different objective from the one
that decides promotion, and `es.py:1` records the surprise honestly: "a hand-built solver is a hard
baseline." It is hard because nobody ever taught the policy from it.

**M.3 The model is the right size for weight-space programming, which is why the first attempt failed for
the wrong reason.** 202,882 parameters; the deciding slices are tiny -- `move` 288, `spell` 480,
`t_bias` 25, the two output biases 8 numbers. An LLM can read all of it and hold the architecture in
mind. But measured biases are all |b| < 0.06 while the `actors @ W` term dominates the argmax, so
hand-writing decision slices cannot flip a choice the mask already forced -- which is what §L.31's WSP
round diagnosed (move collapse, overconfidence, inert `t_bias`, frozen input layer). WSP is right for the
structural slices and wrong for the 190K trunk; the trunk needs gradients, and the duel makes each
evaluation 57 s.

**M.4 The plan, with the gate fixed before any result exists.**
1. **Distil the beam into the head.** Harvest `(board -> beam's first action)` pairs from duel
   playthroughs, map them into the head's own `(move, spell slot, target slot)` index space as
   `_bind_slots`/`_bind_targets` define it, and train all 202,882 parameters by cross-entropy with jax
   gradients. This is the only step that can prove a *floor*: matching the teacher is the definition of
   the ceiling, not a hope.
2. **Train against the teacher.** Add `beam` to `train.py:473`'s `--opponent` choices -- the missing
   branch, not a new system -- and fine-tune the distilled checkpoint against `BattleSolver`, so the
   objective matches the promotion benchmark for the first time.
3. **Keep WSP where it is right** -- `t_bias` (25 numbers), the 8 output biases, and the mask semantics
   M.1 exposes -- read by the LLM, edited by hand, then confirmed or refuted by the harness.

**Pre-registered acceptance gate, so "solved" cannot be moved later:** `python tools/wsp_fight.py base
--matchups 200 --policy production` on a distilled or fine-tuned checkpoint must exceed the measured
solver ceiling **0.4938** (CI 0.4838-0.5038), with a paired sign test against the `--abstain 1.01` arm
excluding zero, on matchup seeds **held out from distillation** -- the disjoint-slice discipline
`online_learn` applies to the calibrator. Distillation alone landing at ~0.49 is *parity with the
teacher*: it closes the 0.0875 deficit but is not a win, and the objective the user set ("it must be
solving it") is beating 0.4938. Anything short will be reported as partial.

## N. Weight-space programming, done at the scale the logits actually live at

The user directed this round: weight-space programming, not distillation. It produced the first real
gain the head has ever shown, and it did not meet the §M.4 gate. Both facts are recorded.

**N.1 The harness could not score an arm, and then scored the wrong one silently.** `wsp_fight.py`
read the module constant `CHECKPOINT` and had no `--checkpoint`, so every arm that is a whole file
rather than an edit was unscoreable. The flag was added, and the first run with it printed
`"arm": "unmodified checkpoint"` with statistics identical to the incumbent: the flag had not been
passed through at the call site. That is the same defect class as §L.25 -- an arm that changes
nothing reads as a measured null -- and the witness that caught it is now permanent: `base` names the
file whenever it is not the incumbent, and `config.checkpoint` records the path in every result.

**N.2 Localise before editing.** `tools/wsp_program.py cache` runs 768 boards (45 s) through the real
`_bundle` and the beam, and classifies each board by what would have to change:

| class | boards | share | reachable by weights |
| --- | --- | --- | --- |
| agree | 378 | 49.2% | -- |
| head passes, teacher's cast is masked | 139 | 18.1% | no |
| wrong spell, teacher's spell is masked | 81 | 10.5% | no |
| head casts, teacher's best plan does not cast | 121 | 15.8% | yes |
| wrong target, same spell | 33 | 4.3% | yes |
| wrong spell, teacher's spell legal | 15 | 2.0% | yes |
| head passes though a legal teacher cast exists | 1 | 0.1% | yes |

220/768 = **28.6% of boards are settled by the mask against the teacher**. No weight edit can reach
them; that is a property of `env.action_masks`' range gate, i.e. code. The largest weight-fixable
class is not "wrong spell" (15 boards) but "casts when the teacher would not" (121), which is what
the duel tiers had been saying with 951 `cast_outside_beam`.

**N.3 Why the earlier WSP rounds were inert.** Measured on the incumbent: spell logits run 8-15,
target logits +/-40, `t_bias` spans 0.18 and the head biases are ~0.01. An edit written at bias scale
cannot move an argmax that `actors @ W` dominates. This round wrote a rank-1 edit at the scale of the
activations it competes with: for each disagreeing board, `W[:, teacher] += eta * h` and
`W[:, head] -= eta * h` with `h` the actor-0 trunk representation, averaged over boards.

**N.4 Head edits admit an exact counterfactual, so bad arms cost no duels.** The heads are linear
reads of the trunk, so editing one leaves the representation unchanged. Caching `(h, masks, teacher)`
once makes every candidate edit exactly evaluable -- same argmax, same masks -- on train and held-out
boards. Agreement is counted only over the 548 boards where the teacher's own choice is legal, or the
mask-forced boards cap every arm at the same number. Selection is by train agreement; an edit whose
held-out agreement falls is not written.

| edit | eta | train agreement | held-out agreement |
| --- | --- | --- | --- |
| incumbent | -- | 0.7417 | 0.7778 |
| pass direction only | 0.005 | 0.9502 | 0.9524 |
| pass direction only | 0.01 | 0.9573 | 0.9206 |
| pass direction only | 0.02 | 0.9265 | 0.8889 |
| both directions | 0.05 | 0.8626 | 0.8333 |
| `t_bias` target prior | 0.5-32 | no held-out gain | not duel-scored |

**N.5 Duel results, 200 matchups, production path, paired against the recorded arms.**

| arm | win rate | delta vs shipped | CI95 | improved / worsened | sign p | solver-decided |
| --- | --- | --- | --- | --- | --- | --- |
| shipped | 0.4062 | -- | -- | -- | -- | 3.2% |
| pass eta 0.02 | **0.4675** | +0.0612 | [0.0375, 0.0862] | 30 / 3 | 1.4e-06 | 15.4% |
| both eta 0.05 | 0.4650 | +0.0587 | [0.0325, 0.0862] | 32 / 6 | 2.4e-05 | 39.7% |
| pass eta 0.01 | 0.4625 | +0.0563 | [0.0350, 0.0800] | 27 / 2 | 1.6e-06 | 7.8% |
| solver ceiling | 0.4938 | pass-0.01 arm: -0.0312 | [-0.0512, -0.0125] | 4 / 14 | 0.031 | 100% |

`cast_outside_beam` fell from 951 to 670 (pass 0.02), 815 (pass 0.01) and 380 (both 0.05).

**N.6 The gain is the weights, not the fallback.** §L.25's trap applies to every number above: a
pass-ward edit also reaches `_realize`'s solver branch, because a pass whose walk is impossible asks
the solver for a plan. So the three arms were read as a dose-response -- the paired delta is +0.0563,
+0.0612, +0.0587 while the solver-decided share runs 7.8%, 15.4%, 39.7%. A five-fold change in solver
substitution moves the gain by less than its own CI width, so substitution is not the explanation.

**N.7 Verdict against the pre-registered gate: not met, and not nothing.** WSP closed 0.056-0.061 of
the 0.0875 deficit (about two thirds) with one matrix write and no training run, and the best arm is
still 0.026-0.031 below the teacher (significant, p = 0.031). The remainder is not addressable by
more eta: the counterfactual already reaches 0.95 teacher agreement while the duel stalls near 0.47,
and 28.6% of boards are decided by the mask rather than by any weight. The next increment is either
the target head done per-actor (33 boards, and the flat `t_bias` prior already failed held-out) or the
mask's range semantics, which is a code change and explicitly not weight-space programming.

Artifacts, none of them shipped -- production still runs `models/encounter_selfplay.pkl`, and landing
an arm is a separate decision that needs an installer rebuild:
`models/encounter_wsp_pass01.pkl` (sha256 a8004274336c8699), `encounter_wsp_pass02.pkl`
(c9c0139fa7cd0111), `encounter_wsp_all.pkl` (cf4f4dd14bf001ab); results in
`data/fight_head_wsp_{pass,pass2,spell}.json`. The runs used temp paths that were copied to these
files byte-for-byte, so `config.checkpoint` inside the JSONs names the temp original.

    python tools/wsp_program.py cache --boards 768 --out %TEMP%/wsp_cache.npz
    python tools/wsp_program.py edit --cache %TEMP%/wsp_cache.npz --head spell \
        --teacher-class pass --etas 0.002,0.005,0.01,0.02,0.04 --out arm.pkl
    python tools/wsp_fight.py base --matchups 200 --policy production --checkpoint arm.pkl --out arm.json
    python tools/wsp_fight.py compare dofus-network/data/fight_head_production.json arm.json

Postscript on the abandoned route: the first distillation attempt never trained. Its loss was a
function of logits cached before the loop and did not depend on `params`, so every gradient was
exactly zero and the loss could not fall. `models/encounter_distilled.pkl` was therefore the
incumbent's parameters under another name and has been removed rather than left to be scored as an
arm. §M.4 stage 1 is superseded by this section; stage 2 (`--opponent beam`) remains untried.

## N.8 Replication on fresh populations, and the corrected ceiling

**N.8.1 The dose ordering survives, the absolute numbers do not carry over.** §N.5/§N.7 quoted one
matchup population. Re-running shipped, ceiling and three WSP arms at matchup seeds 23/37/53 -- 200
paired duels each, `wsp_fight compare` refusing mismatched data hashes or search settings -- gives
`dofus-network/data/wsp_scale/seedrep_report.json`:

| arm | s23 | s37 | s53 | pooled delta | improved / worsened | pooled sign p |
| --- | --- | --- | --- | --- | --- | --- |
| both directions eta 0.05 | +0.0775 | +0.0338 | +0.0638 | **+0.0584** | 101 / 23 | 7.5e-13 |
| pass eta 0.01 | +0.0425 | +0.0213 | +0.0563 | +0.0400 | 67 / 11 | 6.1e-11 |
| pass eta 0.02 | +0.0575 | +0.0312 | +0.0663 | +0.0517 | 83 / 13 | 1.2e-13 |
| solver ceiling | +0.0925 | +0.0725 | +0.1075 | +0.0908 | 122 / 5 | 3.1e-30 |

Two published numbers are corrected by this. The ceiling is 0.5025 / 0.5062 / 0.5088 win rate, not
§N.5's single-population 0.4938. And §N.7's "0.026-0.031 below the teacher at p = 0.031" belongs to
the pass-0.01 arm; the `all` arm's residual against the solver is -0.0150 / -0.0387 / -0.0437,
significant on every seed (mean -0.0325).

**N.8.2 Evolutionary search and PBT are not better than the write.** ES paired +0.0300 / +0.0275 /
+0.0563 against the shipped student on the same seeds (mean +0.038). Head-to-head against the WSP
arm: the write is ahead by +0.0475 on seed 23 (p = 3.2e-4) and ties on 37 (-0.0063, p = 0.46) and 53
(-0.0075, p = 0.44). PBT sits below the write on 23 (-0.0500, p = 2.7e-5) and 53 (-0.0437,
p = 9.1e-4) and ties on 37. No arm here is wired in; production still loads
`models/encounter_selfplay.pkl`. Seed 11's ES and PBT runs have no paired `prod` reference on that
population, so they are left out of the paired claims rather than compared across seeds.

## N.9 Three silent-corruption defects, each now refused by construction

**N.9.1 A cache and its checkpoint could disagree.** `wsp_program edit` resolved its base weights
from a CLI default while consuming activations cached from *another* checkpoint, so an ES+WSP
artifact was really "ES trunk with the production spell head" -- all 202,882 leaves differed from
the checkpoint it claimed to derive from. The cache now carries `checkpoint_path`/`checkpoint_sha256`
and `data_path`/`data_sha256`, re-hashes both files after the walk, and `edit` refuses on any
mismatch, on a cache without identity, and on an output path equal to its source. Three mutations
(hash check removed, CLI default restored, overwrite guard removed) are each caught by
`tools/test_wsp_program.py`. The "97.6% agreement" reading of that broken artifact is retracted; the
rebuilt arms differ only in `spell`.

**N.9.2 Importing a measurement tool reconfigured the process that imported it.** `wsp_fight.py` and
`wsp_program.py` called `os.environ.setdefault("DOFUS_FIGHT_BACKEND", "numpy")` at *import* time, so
a driver that imports `compare` from them forced the inference-only backend on its own training
children -- which then died on "numpy is inference-only" while jax was installed and importable. The
default now applies only when the tool is run as a script, `backend.py` records the underlying
`ImportError` and the training refusal prints it, and the pipeline refuses up front instead of an
hour in. Witnesses: import purity for both tools, CLI still defaulting to numpy, the broken-jax and
forced-numpy refusals naming their own cause; replacing the `__main__` guard with `if True:` fails
the purity test.

**N.9.3 A "resumable" driver that could not resume, and a verifier that could pass vacuously.**
`run_distill_controls` skipped existing artifacts but refused its own output directory, so the
multi-hour job could not continue after any failure. `check_resume` now reads the identities the
artifacts carry -- cache checkpoint SHA and table, corpus source table plus seed and budget, every
duel's data SHA and search settings -- and continues only the job that owns them (9 witnesses, one
per corruption). The Kaggle fetcher mapped `/kaggle/working/...` onto a POSIX root that does not
exist on Windows, reported `verified_package_files: []` and called that a pass while both checkpoints
were in fact downloaded and byte-correct; it now resolves the download tree, re-hashes the 16 staged
input files, the train table and both checkpoints locally, refuses path traversal and foreign roots,
and hard-fails when fewer than five source files verify (11 witnesses).

## N.10 The Kaggle teacher, and what the full chain actually bought

**N.10.1 Artifacts.** Corpus `combat-teacher-corpus-2026-11-05`: 8192 unique rows, zero duplicates,
split 4096 train / 1024 edit / 1024 dev / 2048 test by row fingerprint, tables
`2e1207ff` / `100f9125` / `bf58271d` / `87a30bf3`; the test partition was never read. Both arms
trained only on the train table and returned with hashes re-verified on this machine:
`teacher_large` (128 hidden, 3 blocks, 488,994 params, sha `342dcf87`, 776.1 s) and
`teacher_es_seed` (96/2, 202,882 params, sha `25ea235c`, 1554.7 s).

**N.10.2 The two teachers are the same strength.** On 200 paired dev matchups both beat the shipped
student: +0.0362 (CI [0.0150, 0.0575], 19↑/4↓, p = 0.0026) and +0.0350 (CI [0.0163, 0.0550], 16↑/2↓,
p = 0.0013). Against each other: -0.0013, CI [-0.0163, 0.0150], 5↑/6↓, p = 1.0. The selection step
"chose" `teacher_large` by 0.0012, which is noise, and `teacher_selection.json` records the
head-to-head so the choice is not read as a finding.

**N.10.3 Attribution, 17 stages, every arm paired on the same table.**

| comparison | delta | CI95 | improved / worsened | sign p |
| --- | --- | --- | --- | --- |
| WSP on the neural teacher vs the raw teacher | **+0.0312** | [0.0163, 0.0475] | 17 / 1 | 1.45e-4 |
| raw-teacher soft KL vs beam labels | -0.0088 | [-0.0250, 0.0075] | 4 / 8 | 0.39 |
| WSP-teacher soft KL vs beam labels | -0.0025 | [-0.0175, 0.0125] | 4 / 5 | 1.0 |
| WSP before distillation vs after | +0.0063 | [-0.0013, 0.0163] | 3 / 1 | 0.63 |
| student WSP after raw-teacher distillation | +0.0037 | [-0.0075, 0.0150] | 4 / 2 | 0.69 |
| student WSP after WSP-teacher distillation | +0.0050 | [-0.0050, 0.0150] | 3 / 1 | 0.63 |

Win rates: `teacher_programmed` 0.4825 (+0.0675 vs shipped, p = 4.1e-9),
`distill_progteacher_programmed` 0.4800 (+0.0650, p = 3.7e-8), `distill_beam` 0.4775 (+0.0625),
`distill_progteacher` 0.4750, `distill_rawteacher_programmed` 0.4725, `distill_rawteacher` 0.4688,
`raw_teacher` 0.4512. The beam arm's student-side edit **refused**: no eta improved held-out
agreement over a 0.9524 baseline, because distillation had already absorbed the solver's spell
choices -- WSP after hard distillation is frequently inert, which §N.7's chain implicitly assumed it
would not be.

**N.10.4 The verdict against the cheap incumbent.** Re-duelling §N's own recipe
(`models/encounter_wsp_all.pkl`) on this same dev table gives 0.4675, +0.0525 over shipped
(CI [0.0262, 0.0775], p = 2.4e-5). The best deployable arm leads it by **+0.0125, CI
[-0.0050, +0.0325], 10↑/5↓, p = 0.30** -- unresolved at this power. So: retraining a teacher on
Kaggle, WSP-ing it, soft-distilling it into the student geometry and WSP-ing the student again does
not measurably beat one rank-1 matrix write on the shipped weights, and the teacher's own +0.0312
does not survive transfer into a student that is supervised on the same solver labels. Sizes:
shipped 1,025,321 B, the deployable arm 1,105,819 B (+7.8%, same 96/2 geometry, same runtime cost),
`teacher_programmed` 2,168,724 B at 128/3. Nothing is wired in; promoting any arm is the user's call
and needs an installer payload rebuild.

**N.10.5 The mask, priced on the same cache (#88).** Of 768 edit-partition boards: agree 498,
**mask-forced 195 (25.4%)**, fixable-in-weight-space 69 (9.0%), and *all 195* range-blocked casts
were reachable after a walk (`unreachable_after_walk = 0`). The block is the cast-before-move
ordering rule, not an illegal target, and it is 2.8x the entire weight-space headroom while capping
every arm above -- teacher, WSP'd teacher, distilled students and the solver reference alike. Fixing
it is a code change, and `action_masks` and `step`'s cast-before-move semantics have to move
together; §V.18's abandoned castable-veto is not the same thing.

**N.10.6 Instrument scope (corrected 2026-11-05).** `wsp_fight.play` assigns the network
one seat and `solver_policy` the other, then reverses their assignments with a different RNG seed.
It is not identical-policy self-play and its score is not mathematically constrained to 0.5.
The earlier claim that a 0.9 score was inexpressible was wrong. The duel builds two player-row
combatants, not the corpus's boss rosters; `--harness pvm` instead evaluates raw encounter tensors
against our scripted opponent. Neither harness proves live Touch boss victories.

    python tools/run_distill_controls.py --partitions <corpus> --teacher <teacher.pkl> --out <job> \
        --matchups 200 --matchup-seed 71          # 17 stages, refuses a forced-numpy environment
    python C:/Users/sao/Documents/dofus-evidence/compare_distill_controls.py <job>   # adjacent pairs

## N.11 Real boss boards: the bench, the missing opponent arm, and what weight-space bought

The corpus draws kit/stat data from the Touch API, but the simulator retains scalar geometry,
scripted opposition, invented player builds and incomplete mechanics. These experiments are not
faithful boss-fight validation. Corrections below supersede the initial interpretation of this run.

**N.11.1 The bench.** `encounters_boss_s{4,6}_2048.npz` and `encounters_boss_eval_s4_2048.npz` (seed
8, separately generated; content disjointness not certified) under `C:/Users/sao/Documents/dofus-evidence/touch-data/` -- kept
outside the repo on purpose, because `dofus-network/data/**` is walked into `setup.exe`.
`train.py --spells` makes the 6-slot corpus trainable; the head is `spells+1` because the last choice
is pass. A mismatch is now refused instead of silently reshaping the game: with a 4-wide head on a
6-wide corpus, `step`'s `pass_index` reads the *fifth real spell's* AP and damage while treating the
cast as a pass, and the sixth slot can never be named. Measured refusal:
`carries 6 spell columns but --spells is 4; build the corpus with --slots 4 or train with --spells 6`.

**N.11.2 The difficulty census, not an opinion.** 2,048 boards each, greedy-heuristic player against
the scripted monster, both corpora the same boards with kits truncated at 4 or kept to 6:

| slice | s6 win | boards |
|---|---:|---:|
| all | 0.1753 | 2048 |
| 1 enemy | 0.4454 | 449 |
| 2 enemies | 0.1276 | 1168 |
| 3 enemies | 0.0354 | 254 |
| 4 enemies | 0.0056 | 177 |
| boss present | 0.1717 | 1002 |
| no boss | 0.1788 | 1046 |
| acting first | 0.3340 | 521 |
| acting second | 0.1212 | 1527 |

Mean simulated fight length was 2.9 rounds. These marginal rates decline with enemy count,
but do not establish that bosses and ordinary groups are equivalent: no size-adjusted comparison
was run, and key boss mechanics are absent. They describe this generated corpus and simulator,
not the live client's difficulty. The s4/s6 aggregate difference was about 0.0024; its cause and
transfer to a trained six-slot model remain unmeasured.

**N.11.3 Historical localiser output (#128).** On 192 projected boards from a mixed boss/non-boss
corpus: agree 95, mask-forced 54 (28.1%), reported weight-addressable 14 (7.3%), median spell margin
0.0. The 14 count excludes 29 legal teacher-pass disagreements and includes 8 target disagreements,
so it is not a spell-head headroom total. The continuous-range check passed all 54 blocked casts,
but does not prove reachability by the env's three discrete full-MP moves or by the grid pathfinder.
A zero median logit margin alone does not prove half the boards have exactly one legal action.

Historical movement labels disagreed on 129/192 boards: head approach/hold/retreat 185/3/4,
teacher 62/129/1, median move margin 9.26. These labels are not an executable equivalent of the
beam plan: they reduce its entire final position to a direction, and no-cast plans mistakenly use
the player's own cell as their movement reference. Do not treat 67% as established useful headroom.

**N.11.4 The instrument §N.10.6 said was missing.** `wsp_fight --harness pvm` plays every board of a
corpus to a terminal result, the monster side on `heuristic_actions`, the arm's own heads choosing
directly through the env -- no solver, no `decide()` realisation, no fallback share to subtract,
because the weights are the thing under test. Its parity is not 0.5, so the statistic is the paired
per-board delta, which `compare` computes unchanged (and it now refuses to pair two harnesses).
The initial baseline **0.2185 [0.2009, 0.2356]** was a score giving draws half-credit,
not a win rate: 408 wins / 79 draws / 1,561 losses. The repeatedly inspected seed-8 corpus
is now development data, not an untouched final test.

| historical arm | score (draw = 0.5) | paired score delta | up/down | sign p |
|---|---:|---:|---|---|
| move head → beam's walk | 0.1475 | **-0.0710** [-0.082, -0.060] | 5 / 172 | 1.5e-44 |
| spell head → beam ("all") | 0.0823 | **-0.1362** | 1 / 337 | 1.2e-99 |
| spell head → beam (casts only) | -- | refused by the train gate | -- | -- |
| uniform move bias +10 (hold) | 0.1790 | -0.0396 | 10 / 99 | 1.5e-19 |
| uniform move bias -10 (no approach) | 0.1726 | -0.0459 | 9 / 121 | 3.5e-26 |
| random 2-coordinate bias flip | 0.2185 | 0.0000 | 0 / 0 | -- |

**N.11.5 Corrected interpretation (2026-11-05).** These edits degraded outcomes in the abstract
evaluator; they did not measure the cost of movement ordering. The cache's `make_board` drops
enemy spellbooks and boss identity, randomizes geometry, and its projection substitutes stats and
rebinds player slots. Direction labels also lose movement amount and action ordering. A rank-1
matrix edit and a two-coordinate bias flip are not size- or norm-matched controls. Two harmful
biases do not establish a local optimum, nor do these experiments rule out useful WSP.

The next teacher must use unchanged raw encounter tensors and evaluate candidate actions through
the same environment used for scoring. Movement semantics need a separate controlled intervention,
not attribution from an imitation loss. Summons, states, glyphs, challenge rules and live opponent
behaviour remain fidelity gaps; complex boss victories are not verified by these results.

**N.11.6 Terminal-win recheck.** The repaired harness preserves terminal winner and original corpus
row IDs, separates boss/non-boss buckets, refuses nonterminal outcomes and dimension mismatches,
and binds paired runs to metric, config, backend and source digests. It no longer clears an explicit
numpy backend selection. Re-running the original weights and existing move edit on the same 2,048
development rows gave baseline **408/2048 = 0.1992 [0.1821, 0.2163]**, move edit
**249/2048 = 0.1216 [0.1079, 0.1353]**. Paired terminal-win delta **-0.0776**
(CI95 [-0.0894, -0.0659], 5 improved / 164 worsened, p=2.98e-42).
Boss subset: baseline 192/1041 = 0.1844, edit 118/1041 = 0.1134.
Evidence: `C:/Users/sao/Documents/dofus-evidence/pvm_boss_baseline_verified_v2.json`
and `pvm_boss_move_verified_v2.json`. The 26 root WSP tests pass; this is simulator evidence only.

Prerequisite recorded for #130: `kaggle/tpu_selfplay/dataset/combat/encounter/` is a hand-maintained
copy and is **stale** -- its `train.py` has no `--spells`, so a boss-corpus kernel would train a
4-wide head against a 6-wide table and hit the new refusal. Sync it the way `tools/sync_kaggle_toolkit.py`
syncs `kaggle/toolkit/` before any spend. The corpus-side knob for the readiness question a pre-fight
plan has to answer (#131) is `build_dataset --enemy-level-low`, which lets the player out-level the
pack instead of meeting it at equal grade.

**N.11.7 Outcome-keyed WSP (the recipe that finally helps, a little).** The teacher no longer
imitates the client's beam. `wsp_program cache-pvm` walks the *raw corpus tensors* themselves -- no
`make_board`, no `plan_value` projection, no `LivePolicy` slot rebinding -- caches the checkpoint's
own trunk activations and masks, and for every legal action of the head under edit applies it through
`encounter.env.step` and plays the fight to a terminal state under a fixed greedy-continuation policy.
A decision becomes an edit key only when a different legal action *turns that loss into a win*.
`edit --method covariance` then writes the minimum-context-disturbance matrix that satisfies those
key constraints (ridge 0.01, eta grid scored exactly on the cached logits, held-out outcome gate
unchanged), so it is still one direct weight write, not training.

Cache seeds 7/11/23 on the 2,048-row training corpus each found **45 strict improvements in 4,334
player decisions** (edit keys 36/33/27 after the row-level train split). Paired on the repeatedly
inspected seed-8 development corpus, greedy play, terminal wins only:

| cache seed | arm delta | arm up/down | arm p | same-support, same-norm random control |
|---|---:|---|---|---|
| 7 | +0.0029 | 16 / 10 | 0.327 | -0.0054, -0.0112, -0.0166 (three seeds) |
| 11 | +0.0073 | 20 / 5 | **0.0041** | -0.0371 (7 / 83, p=1.3e-17) |
| 23 | +0.0029 | 15 / 9 | 0.307 | +0.0010 (20 / 18) |

One-shot test on a corpus never inspected before (seed 99, base **0.2163**): arms
**+0.0000 / +0.0024 / +0.0015**, controls **-0.0054 / -0.0420 / -0.0044**. So on untouched data the
motivated writes are flat-to-slightly-positive and every matched control is negative; pooled across
the three seeds the arm wins 41 boards and loses 33, which is **not** significant.

What this establishes, stated at its real size: an outcome-keyed write no longer damages the policy
the way the beam-imitation edits of §N.11.5 did (-0.0776 and -0.1362 terminal-win), it beats
equal-norm equal-support random edits of the same head on both corpora, and it is safe to keep. What
it does not establish: a meaningful boss win rate. Only ~1% of labelled decisions can be turned from
loss to win by changing one head at one state, so the head is not where the 79% loss rate lives. The
binding constraints measured here are the fight arithmetic of the corpus (solo versus groups,
§N.11.2) and the unmodelled mechanics (summons, states, glyphs, the client's cast-from-new-position
rule, #88), not the weights.

Artifacts: `wsp_raw_spell_{s11,s23}.npz`, `wsp_raw_spell_{cov2048,arm_s11,arm_s23}.pkl`,
matched controls `wsp_raw_spell_{control0,ctrl_s11,ctrl_s23}.pkl`, arms
`pvm_raw_dev_greedy_cov2048.json`, `pvm_s{11,23}_{arm,ctrl}.json`, one-shot
`pvm_hold_*.json` under `C:/Users/sao/Documents/dofus-evidence/`.

**N.11.8 Why the spell head was pushing toward pass -- and the census error that corrected it.**
The first version of this paragraph claimed 1,651 of 2,048 opening boards (80.6%) had *no castable
real spell*, and used that to explain the `median_margin = 0.0` of §N.11.3. That was an artifact of
where the census stood: `initial_state` gives AP only to the team that acts first (`env.py:78-80`),
and the monsters open on 1,527 of the 2,048 boards, so those rows report nothing legal because it is
not the player's turn -- a statement about turn order, not about the range gate. The instrument is
now `C:/Users/sao/Documents/dofus-evidence/census_castable.py`; it measures each board on the state
where the player actually acts, and it asserts its own re-derivation of the range rule against
`action_masks` before printing anything (the two agree on every slice below).

| slice | rows | castable slots, pre-fix gate | post-fix gate | zero-castable pre | post |
|---|---:|---:|---:|---:|---:|
| player opens | 521 | 1.89 | 3.72 | 23.8% | **1.0%** |
| monsters open, player survives their turn | 917 | 1.44 | 3.55 | 38.1% | **1.5%** |
| every row at the opening state (the old, wrong slice) | 2,048 | 0.48 | 0.95 | 80.6% | 74.8% |

Two conclusions, one of them bigger than anything about the weights. The range gate *was* binding:
on a state where the player holds AP, admitting MP-reachable spells roughly doubles its options and
collapses the "nothing to choose" class from a quarter of boards to about one percent -- that is what
§N.11.3's zero median margin was really measuring. And the error hid the larger fact: **after a
single monster opening turn the player is already dead on 610 of 1,527 boards (39.9%)**, so on three
boards in ten the fight contains no player decision at all. No action head, edited or trained, can
affect those boards; refusing to enter them is a planning problem (#131), and it is what the corpus's
solo-versus-group arithmetic actually implies.

**N.11.9 Scaling the keys did not scale the win rate -- the recipe is at its ceiling.** The three
caches were merged under one row-level split (rows < 1536 train, >= 1536 hold out; no row crosses,
checked) into 13,002 labelled decisions with **96 train-side strict improvements** -- three times the
edit keys of any single seed. Its internal held-out terminal-win score rose honestly,
0.2654 → 0.2700, and train 0.3140 → 0.3223, so the write was allowed by the same gate. Priced on
whole fights on the development corpus, greedy, terminal wins: **+0.0029 (16 up / 10 down,
p = 0.327)** -- and this time the support-and-norm-matched random control was also positive,
**+0.0020 (19 / 15, p = 0.608)**. The single-seed arms had beaten their controls by a wide margin
(seed 11: +0.0073 against -0.0371); with three times the keys the advantage disappeared.

Read plainly: three times the programmed states bought the same ~+0.3 points, and the control
resampled into the same band. The limit is not the number of edit keys. The labels are one-decision
counterfactuals -- "this cast, played greedily from here, wins" -- and they do improve (that is what
the held-out 0.2654 → 0.2700 measures), but a terminal outcome is decided by everything the fight
does *after* that one decision, under a head the edit does not touch. The single-decision win
probability moves a few points; the fight-level probability moves ~0.3.

So on this bench, WSP of the action heads is worth **~+0.003 terminal-win at best**, is unsafe when
the teacher is the client's beam (-0.0776, -0.1362) and is not improvable by writing more of the same
keys. The two things that could change that are structural and already identified: the turn semantics
(§N.11.8 -- on a state where the player holds AP the range gate alone decided a quarter of its boards,
which the fix takes to about one percent), and the corpus's lethality arithmetic (§N.11.2 -- solo
against groups; §N.11.8 -- 39.9% of monster-opening boards kill the player before it ever acts).
Neither is a weight edit, and neither is fixed by retraining on the same game.
`wsp_raw_spell_merged{.npz,_arm.pkl,_ctrl.pkl}`, `pvm_merged_{arm,ctrl}.json`.

**N.11.10 The turn order was fixed, and it bought more than any weight edit (#88 landed).**
`action_masks` now admits a spell when any **MP-reachable** distance lies in its range, and `step`
spends MP **before** resolving casts, walking along the chosen direction and stopping at the first
distance the spell is legal from. Three properties were pinned by new `combat/test_env_math.py`
arms, green on both backends: a spell only reachable by approaching is admitted *and* lands; an
actor that holds does **not** get that cast (no teleport, so the move head keeps its meaning); and a
walk that would overshoot out of a legal range snaps back to the nearest legal distance instead of
forfeiting a cast the old order guaranteed. A fourth arm refuses a spell outside every reachable
distance and flips the verdict by changing only MP, so the refusal is the budget talking.

**The first measurement was wrong, and the reason is worth keeping.** Before touching the
opponent, the unmodified checkpoint appeared to jump from 0.2012 to **0.5508** on the dev corpus.
That was self-inflicted: `heuristic_actions` holds position whenever *anything* is castable, so an
optimistic mask made the scripted monsters refuse their own walks and waste turns. Giving the
opponent the matching rule -- walk the gap the *chosen* spell needs -- moved the number to
**0.2266** (464 wins / 54 draws / 1,530 losses). The real effect of the fix on the same weights is
**+0.025**, not +0.35. Lesson: a reachability gate is a change to *both* sides' action sets; if the
reference policy does not walk, the benchmark measures the handicap you gave your opponent.

Numbers under the corrected game, same corpora (2,048 rows each, terminal wins only):

| measure | pre-fix | post-fix |
|---|---:|---:|
| castable slots, player-led boards (521) | 1.89 | 3.72 |
| castable slots, monster-led boards the player survives (917) | 1.44 | 3.55 |
| those boards with no legal spell at all | 23.8% / 38.1% | **1.0% / 1.5%** |
| greedy-vs-greedy player win | 0.1753 | 0.2378 |
| ... against 1 enemy / 4 enemies | 0.445 / 0.0056 | 0.526 / 0.062 |
| mean fight length (greedy-vs-greedy) | 2.90 rounds | 1.90 rounds |
| unmodified checkpoint on dev corpus | 0.2012 | 0.2266 |

(The castable-slot rows come from `census_castable.py`, which measures only states where the player
holds AP; the earlier 0.481/0.946 pair mixed in the 1,527 boards where it is the monsters' turn.)

**WSP did not get more headroom from the fix -- it got less.** The teacher relabelled the raw
tensors: 3,216 decisions, **47 strict improvements (1.5% of decisions, against 1.0% pre-fix)**, and
its decision-level baseline win rose 0.3140 → 0.4595 because the greedy continuation can now cast
after walking. But every eta in the grid *reduced* training outcome (best 0.4583 against 0.4595
baseline), so `edit --method covariance` refused to write: "no training outcome gain; edit not
written". The gate did its job and the honest reading is that once the environment stops forbidding
the cast, one linear write on the spell head has nothing left to take -- the ordering fix was worth
+0.025 terminal win where the best programmed edit was worth +0.003 and is now refused.

What is left open, now attributed rather than assumed: the "74.8% of boards offer nothing castable"
figure was this script's own error (it counted rows where the player holds no AP because it is not
its turn), and the real attrition is that **39.9% of monster-opening boards kill the player before
its first decision**. So the largest remaining lever on boss outcomes is not the mask and not the
weights but the decision to enter those fights at all (#131), plus the fidelity gaps this bench
still has -- no party, no summons, no states, no glyphs.

Artifacts: `wsp_fixed_spell_s7.npz` (refused edit, no arm written), `pvm_fixed_dev_base2.json`,
`/tmp/baseline_h2.pkl` run rows, `duel_production_postfix.json` (the shipped `decide()` realization
on 120 games: 0 errors, 0 fallbacks, 3.2% solver-decided, 0.4667), `census_castable.py`.
The corrected semantics invalidate every §N.11 number before this section for comparison purposes:
`wsp_fight.compare` refuses to pair runs across the change because it binds the source digests of
`env.py` / `heuristic.py` / `model.py` / `train.py`.

## N.12 Is the deficit in the weights or in the layers? Measured, because the claim is testable

The user's reading was: "the problem is lack of training while the weights are nearly perfect but
there are problems in the layers". That is falsifiable with the artifacts that already exist, so it
was tested rather than agreed with. `tools/wsp_layer_ceiling.py` reads the raw outcome cache (trunk
activation + the terminal win indicator of *every* legal action at that decision) and computes a
ladder: the shipped head's score, the best possible **linear** readout of the same vector (closed
form ridge, fitted only on train rows, scored on rows never fitted), a **random-forest** readout of
the same vector, and the **oracle** (`max` over legal actions = perfect one-decision play given this
representation).

On the post-#88 cache (`wsp_fixed_spell_s7.npz`, 3,216 decisions: 2,433 train / 783 held out):

| readout of the same trunk vector | held-out decision score |
|---|---:|
| shipped head (incumbent) | 0.4368 |
| best linear fit, any ridge strength | **0.4100** (worse than the incumbent) |
| random-forest fit | 0.4406 (train 0.4714 -- it over-fits) |
| oracle: perfect one-decision play | 0.4508 |

**"The weights are nearly perfect" is quantitatively true.** The whole one-decision headroom in this
representation is **1.4 points** (0.4508 − 0.4368), the shipped head sits 0.4 points below the best
nonlinear readout, and *no* linear readout beats it at all -- which is why the outcome-keyed
covariance write of §N.11.9 was correctly refused: there was nothing there to take. It also retires
the "more complex weight precision" idea for this head, at this state: the ceiling is not a
resolution problem, it is 1.4 points of room total.

**"There are problems in the layers" is true, but not where it sounds.** The forest beats every
linear readout by 3.1 points held-out on the *same* vector, so the trunk does carry action value the
single linear head cannot express -- that is an architecture/capacity fact, and it is the one place a
representation change (or a genuinely nonlinear readout) could pay. The bigger fact, though, is that
the ceiling itself is low: a perfect decision-maker at these states gains 1.4 points per decision,
because the outcome is largely settled *before* the decision -- the ambush class of §N.11.8 (39.9% of
monster-opening boards end with the player dead before acting) and the group arithmetic of §N.11.2.
So the productive targets are the fight's structure and the entry decision, not the head.

## N.13 The economy objective the user asked for is not groundable yet, and what is instead

Proposed shape: punish gear cost, reward verified victory and drop collection, optimise kamas per
hour, over a simulated mob population with its real drop table, with WSP repairing the weights. The
data verdict, checked before writing any reward function:

* **Drop tables are real and rich**: 11,771 `MonsterDrop` rows over 1,461 monsters, 3,408 distinct
  dropped objects, per-grade percentages (`percentDropForGrade1..5`) and a `criteria` string on
  9,164 rows (`PL>19&PL<61&Sc!5814` = a player-level window plus a special condition), so drop value
  genuinely depends on grade, prospection and level range -- the "population simulation" has ground.
* **Kamas values do not exist in the tables we hold.** `items_fr.json` carries `price` on all 13,686
  rows, but the field takes nine distinct values across the whole table -- {0, 1, 10, 11, 5000,
  50000, 200000, 400000, 500000} -- and is 1 for essentially every item, including weapons. It is a
  placeholder, not a market price. `isBidhouseKamasOnly` marks auction eligibility without giving a
  value.
* **Craft cost is real**: 4,337 recipes with `ingredientIds` and `quantities`, so "punish build cost"
  can be priced in *materials*, and item/recipe `level` gives a progression axis consistent with the
  user's requirement that level gates stay unmodified so the numbers mean something.

So a kamas/hour reward would be a fiction until actual market values are fetched; the honest form
today is **rarity-and-craft-cost-weighted drop value per simulated hour, with credit only on verified
collection** (victory already verified by `--harness pvm`; collection needs the drop-roll and pickup
step added to the harness), and the kamas scale is a separate fetch task rather than an assumption
inside the reward.

## O. The network mess had one root: nobody could tell its processes from this run's

The user's guess -- "the auto startup of the model doing bugs" -- was right, and it was checkable
in three columns of `wmic`: PID, PPID, CreationDate.

**O.1 What was actually running.** The combat brain answering `:8891` was pid **7696**, started
**05:51** with `combat_ai_server.py 8891` on the **dev** interpreter
(`AppData\...\Python314\python.exe`), parented by pid 4980 -- an earlier application session. The
current DofusFarm is pid **7932**, started **06:39**. So fights were being advised by a process 48
minutes and three restarts older than the app that believed it owned them, on the wrong interpreter
(the two-interpreter rule), with whatever checkpoint it had loaded at 05:51. The same shape hit the
relay first: `proxy.log` shows `[FATAL] ... bind 127.0.0.1:8880` at 06:39:33 because the *previous*
run's `dofus-net-proxy` (pid 540) still held the port; the app's own relay only got it at 06:45:05
(pid 10940). Meanwhile the page reported `map fetch HTTP 0` for a map that `curl` through the
tunnel returned **200 / 9,574 B** for -- the broken hop was local, not upstream.

**O.2 Why nothing could see it.** Every component was asked a question it could answer "yes" to
while being wrong. `brain_service.brain_is_up` asks whether `/health` answers; the orphan answered,
with `status: ok`, a model name and `backend: jax`. `HTTPServer` sets `SO_REUSEADDR`, which on
Windows permits a *second* listener on the same port, so traffic can split between two processes
each holding its own instance table -- `agent/serve.py:65` and `aipanel.cs:1501` had already
recorded that on `:8895`. Health proves a socket. It never proved ownership.

**O.3 The fix is one fact carried end to end.** `run_brain.py` mints a per-session token
(`data/run_token.json`, older than 6 h is treated as a dead session, not a long one) and every child
inherits `DOFUS_RUN_TOKEN`; `combat_ai_server` stamps it into `/health` as `identity` with pid,
interpreter, script path, start time and checkpoint hash; `ExclusiveHTTPServer` sets
`allow_reuse_address = False`, so a second brain **fails the bind loudly** instead of quietly
sharing the port. `brain_service.ensure_brain` now returns `reused` only on a token match, and
`reused_foreign` / `reused_unlabelled` otherwise.

**O.4 `net/orchestrator.py` is the pipeline.** Four declared components -- egress (WARP :40000),
relay (:8880), brain (:8891), agent (:8895) -- each with a probe, an owner test and an action,
walked in dependency order, reported as one table: `state` (down / bound-not-answering / answering)
plus `run` (this-run / foreign / foreign-unlabelled), the owning PID and its image name from
`netstat`/`tasklist`. `status` exits non-zero when something is wrong; `plan` is advisory and
touches nothing; `ensure` acts **only on components it owns**. The refusal rule is the point:
reaping needs proof -- the health identity names our script, or the OS command line for the owning
PID does -- because an orchestrator that kills "whatever holds the port" eventually kills something
unrelated. `DOFUS_NO_REAP=1` leaves the decision to the operator.

**O.5 Measured.** `net/test_orchestrator.py` runs **24 arms, all PASS**, including the negative
ones: an unrelated command line is refused, an unlabelled server is *not* `this-run`, a fresh token
is reused while a stale one is replaced. Live, the pipeline caught a bug in itself: an owned server
answering with **no identity at all** was classified reusable -- which is precisely today's orphan.
`classify` now calls that `foreign-unlabelled`, and the live plan reads
`brain: reap-then-start, answers /health but is not this run (owner pid 7696)`. `py_compile` did not
catch the missing `_http_json` I spliced out; running the module did -- compile checks are not tests.

**O.6 Not done, and what is owed.** `ensure` has not been run against the live machine, so **pid
7696 is still answering :8891** and the kill decision stays with the user. The orphan cannot report
an identity until it is replaced, so the first reap necessarily goes through the command-line
proof. `compile.bat` has not been run, so the C# changes in this session (the fail-open adoption
gate, `SavePermanentAccount`'s cached set) are not in the exe. `StopAuto` still writes only the
shared `AUTOPILOT_STOP` plus files for `_procs` children, while `autopilot.py:765` reads
`AUTOPILOT_STOP.<instance>` -- fixing that needs the daemon's instance list, and I would not edit it
without reading that code. The pink screen's remaining cause is separate and client-side: the page
still requests retired `3.3.10` map and `1.10.7` UI paths, which come back 403/ERR and sit in the
relay's 10-minute negative cache.

    python -m net.orchestrator status --json     # who owns each port, and is it this run
    python -m net.orchestrator plan              # what it would do, touches nothing
    python -m net.orchestrator ensure --json     # act on owned components, then re-verify
    python -m net.test_orchestrator              # 24 arms, pass and fail cases

## P. The black screen's actual root, and why only a browser-shaped probe can see it

**P.1 It was CORS, and it had happened before.** The relay answered every request with
`Access-Control-Allow-Origin: *` and `Access-Control-Allow-Headers: *`. A wildcard is not legal once
the request carries credentials, and the client's map XHR does (`withCredentials` in the bundle). So
Chromium **discarded a response the relay had served with 200 OK**: the page saw `HTTP 0` for
`maps/145490432.json` eighteen times, `onreadystatechange` then threw
`parameter 1 is not of type 'ArrayBuffer'` on the empty body, and no map or background ever loaded.
The protocol path was healthy throughout -- the log even says
`[autopilot] the character is in the world; starting` -- which is why it read as a rendering or
version problem rather than a discarded response.

**P.2 Why every existing check said fine.** The blocked GET never reaches the relay, so
`requests.log` shows only the prefetch's own `WARM` lines for that map; the C# interceptor reported
`94 requests, 0 failed`; the relay's health probe returned 204; and a `curl` for the same URL
returned **200 / 9,574 B** because curl does not enforce CORS. Every server-side instrument was
right and the client was still blind. The version ladder in §P.4 was worth building for rollout
skew, but it was not this bug: escalating a request the browser throws away changes nothing.

**P.3 The fix is a header policy, and it is now asserted.** `setCORSHeaders(h, r, methods)` in
`scripts/dofus-net-proxy.go` echoes the request's `Origin`, sets
`Access-Control-Allow-Credentials: true` and `Vary: Origin`, and echoes the preflight's requested
headers; wildcards are kept only for traffic that sends no `Origin`. All eight call sites plus the
two inline wildcard sets were converted; `go vet` and the build are clean; the old binary is kept as
`dofus-net-proxy.exe.wildcard-cors` (8,152,064 B, `b0250ee5b2c0`) beside the new one
(11,788,288 B, `08ff08696de4`). Proven against the **installed** binary on a throwaway port:

    ok: true   status: 204   allow_origin: https://dofus.local   allow_credentials: true   vary: Origin

and the same call against the old binary returned `allow_origin: *`, no credentials -- which is the
arm that makes the check meaningful rather than decorative.

**P.4 What is now automatic per update, so "the same problem as last update" cannot recur quietly.**
Each of these was built in this session and each can fail loudly:
- `net/orchestrator.py::cors_contract` runs a credentialed-shaped OPTIONS probe and reports
  `cors-contract-violation` for the relay; `status` exits non-zero on it, and
  `net/test_orchestrator.py` asserts it on every run (the skip is named, never silent).
- Run identity end to end: a per-session token from `run_brain.py` → `DOFUS_RUN_TOKEN` → `/health`
  `identity`, with `allow_reuse_address = False` so a second brain fails the bind instead of
  sharing a port with a leftover. `ensure` reaps only what it can prove is ours, by health identity
  or by the OS command line.
- The asset version is **learned, never written down**: `window.__DOFUS_CDN_STATS.assetVersions` from
  live traffic plus a stylesheet seed, a numeric compare (so `3.3.9` cannot outrank `3.3.10`), one
  escalation retry per request for `403/404/HTTP 0`, per-kind isolation, and voiding the ten-minute
  map negative cache when the version it was judged under is superseded.
- The page-side compat layer (§N/§O) binds by name with `bound`/`fired` counters and fails open to
  the pristine bundle, and `clientbundle.cs` now refuses to adopt a half-patched bundle.

**P.5 Still owed, in order.** Relaunch the client so the app spawns the fixed relay (it starts
per-launch: it did so at 06:39 and 06:45 today), then confirm `relay: answering` in
`python -m net.orchestrator status` and that `map fetch HTTP 0` stops appearing. Then `compile.bat`
for the C# changes (adoption gate, `SavePermanentAccount`) and delete `no-client-update.flag` once
the patch set is proven against 3.3.11 again -- updates are deliberately off right now, with a dated
note in the flag file. Also still open: the brain on `:8891` is down and will be started by
`ensure_brain` at the next fight with this session's token.

## Q. The instruments that could not be wrong, and the ones that replaced them (2026-11-04)

Two claims in this session turned out to be produced by instruments that cannot report failure: a
build line that printed a patch count nobody measured, and a bundle self-test whose "IDENTICAL"
compares text the rules produced against text the rules produced. Both were green while the client
was black. §Q.1-Q.2 are the repairs, §Q.3 is the instrument that replaced them, §Q.4 the GUI
counter, §Q.5 what is still owed.

**Q.1 `[OK] Client bundle patched successfully (11 patches active)` printed over zero patches.**
The string was a literal in `builder/Build.cs`, printed whenever `patch-engine.js` exited 0 -- and
`Run()` only echoes a child's stdout when the child *fails*, so the tool's own
`[SKIP] client patches quarantined` line never reached the log either. Measured on this tree: a
build ran, printed the success line, and `desktop/client/www/build/script.js` came out
byte-identical to before (`sha256 96fdf75cefe006e9cd07934e64ed8c7b98f71b1e60daf7b34e85974753594b1a`
before and after; the resilience marker `_seriesDone` occurs 0 times in it). The line is gone; the
verdict is now computed from the served file against the state the tree declares:

    [OK] patches quarantined: the served bundle is the vendor's (5397933 bytes, 0 applied).

`bytes` there is `FileInfo.Length`, not `string.Length` -- the first version of that line printed
5,362,738, which is the UTF-16 code-unit count of the same file and 35,195 smaller than the file
on disk. The same slip was sitting in `compat-shim.js`'s auto-resolved table as `bundleBytes:
5362738` for a 5,397,933-byte file; `tools/compat_probe.js` now writes both counts
(`bundleChars` and a real `bundleBytes`) and the gate checks the recorded bytes
against the file's own size (§R.6). A patched adoption is refused outright when it contradicts the flag: `quarantined &&
patched` exits 1 rather than packaging the black screen.

**Q.2 The quarantine is asserted, not excused.** `client-patches.quarantine` inverts one invariant
-- the bundle the client serves must be the vendor's -- and three tools had to learn which world
they are in, from one reader (`tools/quarantine.js`) so they cannot disagree. All four states were
proved in a scratch tree that mirrors the layout, without touching the live bundle:

| flag | served bundle | `apply-map-resilience --check` | watchdog bundle arms |
|---|---|---|---|
| set | pristine (the state now) | `QUARANTINED ... applied 0/14`, exit 0 | PASS (neither rule present) |
| set | patched | `CHECK FAILED: 14 rule(s) are in the bundle while the patch set is quarantined`, exit 1 | FAIL |
| unset | patched | `CHECK OK: bundle already carries every resilience rule` | PASS |
| unset | pristine | `CHECK FAILED: 2 rule(s) need re-deriving` | FAIL (2 arms) |

The middle row is the one that pays: it is what a stray re-patch looks like, and it now stops. The
watchdog's thirteen behavioural arms run the generator text directly and pass in all four states,
which is why the bundle arms could be inverted without losing the behaviour proof. Against the
pristine 3.3.11 bundle 12 rules are APPLICABLE and 2 are genuinely STALE (the
`dom.touchstart`/`dom.touchend` handler text moved in this build).

**Q.3 The boot answer is now an input to adoption.** `--bundle-selftest` on the current tree prints
`rules: 23`, `matched: 0,1,2,...,31` (23/23), `patched 5365990 chars, live 5362738 chars`,
`RESULT: DIFFERENT` -- and before this session it printed `RESULT: IDENTICAL` for the very bundle
that black-screened on `Callback was already called`. Nothing in the chain asked the running game,
so the patch set was trusted on evidence that could not have detected the break. Now:

- `desktop/client/www/js/compat-shim.js` sends `{"type":"BOOT_CANARY","ok":...,"stamp":...}`. `true`
  requires the facade *and* a map fetch that returned 2xx-3xx (the facade is assigned long before
  anything draws, so a black screen has one); `false` is a self-inflicted `ASSET_MISSING`/
  `LOADING_MAP_ERROR` teardown that outlived the veto budget -- the 3.3.11 signature. No evidence
  means no message: silence leaves the build unjudged instead of inventing a verdict.
- `desktop/WebView2ClientForm.cs` injects `window.__DOFUS_BOOT_STAMP` from
  `ClientBundle.BundleStamp()` -- the manifest token for `build/script.js`, read from
  `client_version.json`, so the arming side and the answering side name the build the same way. An
  answer without a stamp or without `ok` is dropped rather than guessed.
- `desktop/BootState.cs` is the decision, pure and executed by the gate: patch only if the build is
  not on the never-patch list and something actually matched; on a first failure **revert** (from
  `script.js.bak`, the bytes Ankama served -- no network needed) and *watch* the build; on a second
  failure for the same build, blacklist it. Two strikes because `ASSET_MISSING` is the vendor's own
  reason as well as our patch's signature: one bad boot cannot tell a doubled series callback from
  ten minutes of CDN 403s, and a wrong blacklist would leave the client on unpatched bytes -- the
  login break these patches exist to prevent.
- The updater's partial-patch refusal now fires only when it decided to patch, so a deliberate
  vendor adoption is not blocked by the rules it chose not to apply.

**Q.4 The pipeline has a counter the operator sees.** `net/orchestrator.py` reports one shape
whatever wrote it (an `ensure` report nests its live view under `after`, so `components` and
`checked_at` are lifted on read -- otherwise a reader cannot tell staleness and paints hours-old
green as live), `agent/serve.py` answers `GET /net/status`, and `desktop/NetPill.cs` turns it into
the nav pill: `net ok` / `net! relay,brain` / `net ok 10m` / `net ? 2h`. `NetPill.cs` and
`BootState.cs` are compiled with a probe `Main` and *run* by the gate (`_cs_logic_probe`), not
grepped, and `tools/test_boot_canary.js` drives the shipped shim in a `vm` sandbox; two of them were
mutation-checked against the pre-fix bodies, which reproduced the exact
lies they exist to prevent: `net! relay 277h` from a report read first-match instead of last, and
`applied 14/14 while the patch set is quarantined`.

**Q.5 Owed, in order.** Re-derive the 23 rules against 3.3.11 (2 are stale on text, the rest wrong
in effect) and prove them in-game; then delete `client-patches.quarantine` and
`no-client-update.flag`, which are both deliberate stops, not destinations. Closed the same day (§R.6): the probe writes
`bundleChars` beside a real `bundleBytes`, and the recorded row is checked against the bundle on disk. Launch the app once with the rebuilt binaries to see the pill live and to let
the app spawn its per-instance relay, and confirm the canary round-trip in the log
(`armed for a boot answer` then `boot attested for <stamp>`) -- until that pair is seen on a real
launch the mechanism is proven offline only.


## R. The AI plays the live client, and the daemon now says whether it is current (2026-11-04)


**R.1 Three defects, not one.** After the client rendered (section Q), "the ai doesnt" was still
true, and it was three separate things stacked on each other. `agent/serve.py` defaulted
`--brain-policy` to `rule` and `BrainDaemon.default_policy` to `"rule"`, so `/brain/start` with the
default goal "do all the quests" reached a policy that has no quest planner at all and answered
`None` with no reason attached -- the same lesson `_base_policy` had already learned for the CLI path
on 2026-10-29, kept quietly broken on the daemon path. Second, the snapshot reached the policy
without flat `map_id`/`cell` when the client nested them under `position`, so navigation questions
were unanswerable from the row (`knowledge_context._lift_position` now fills them only when absent,
before the early return that skips enrichment when no store is configured). Third, and the one that
made the first two look unfixed: the daemon the host had attached to started at 11:03, the fixes
landed 11:15-11:21, and `/brain/health` answered `status: ok` for the whole time. Both defaults are
now `planner`, `RulePolicy.last_no_call` records why a goal was declined, and the autopilot's
no-call row carries that reason next to what it had in hand.


**R.2 The live verdict, from the daemon's own log.** One bounded run (40 steps / 4 minutes) on
instance `dofus-01`, goal "do all the quests": 11 decisions, of which 8 `quest.advance` and 3
`nav.goto`, verdicts 11 advanced / 2 noop / 1 failed. It walked quest 4262 end to end -- talked to
npc 3898, 3888 and 3950, read the replies the server sent ("Make your report", "Hurry after the
raccoon", "Run after the blueprints"), crossed to map 145490432 and then `direction: right`. The one
`failed` row is the interesting part: `npc 3950 is not on map 145489920 (npcs here: 3962,3896,3888)`
-- the planner was refused, chose one of the NPCs the refusal named, and the next step advanced.
A refusal that changes the next action is the behaviour the retirement bounds were built to protect.


**R.3 Currency, not liveness.** `/brain/health` now returns `code_stale`: the repo modules whose
file is newer than `PROCESS_START`, the moment this process imported them
(`agent/brain_daemon.stale_modules`). `desktop/aipanel.cs` reads it before attaching -- a stale
daemon is restarted, and if it cannot be stopped the panel refuses to run against pre-fix code
rather than reporting green. Both arms were proven against the live daemon, not a fixture: fresh
process answers `"code_stale":[]`, touching `agent/planner.py` one minute later answers
`["agent.planner"]` with the matching warning line naming the pid and the age. The first version of
this probe crashed inside `health()` on an undefined `logger` and the endpoint returned an empty
reply -- a currency probe that dies is indistinguishable from a dead daemon, so the gate now pins
that the inspection does not raise, that it uses the module's own logging convention, and that a
path it cannot stat is skipped instead of propagated. The host change is compiled-clean against the
real source set (a Roslyn `/target:library` over all 24 desktop files, exit 0) but not yet run: it
takes effect the next time `compile.bat` produces `DofusFarm.exe`.


**R.4 Two gain numbers for one head, reconciled.** The weight-space plan quoted expected gain 1.2553
against an oracle 1.3141 and a gain capture of 0.9553; the 2026-11-04 replay of the same shipped
head gives 0.7977 against 0.8209 and capture 0.0799. The formula is identical
(`(model - teacher) / (oracle - teacher)` over per-row gains) and so is the path it cites,
`data/tool_sft_v3/val.jsonl` -- the corpus behind that path is not. The report scored 8,453 val rows
from the v2+v2b shards; the file at that path today has 3,719. A path is not a dataset, so
`agent/weight_space.split_provenance()` now records the row count and sha256 of the split actually
read next to the report's own numbers, and publishes `comparable: false` with a note saying which
figure to use. `models/weight_edits_replay.json` carries it, and the replay prints under
`PYTHONIOENCODING=cp1252` (exit 0) because it also gained the shared `utf8_console()` guard. What
this changes for Phase 3: the headroom is not "already 95% captured". On the split that exists, the
teacher sits 0.0252 below the oracle and the head takes 0.0020 of it -- 2.9% of absolute gain in
play, of which the head captures 8%. That is a real target and a small one, and the 0.9553 that made
the approach look finished is not reproducible.


**R.5 Still owed here, in order.** Relaunch the app once with the rebuilt binaries so the restart-on-stale
path runs in anger, not only in the probe. The "49 retired" line in a planner's decline is
explained and is not a bug -- `QuestPlanner.choose` walks the whole curriculum candidate list inside
one call and retires each candidate it cannot start, and with no client-reported quests every
candidate is retired, so the count is the pool size, not leaked state; the note now says so with the
observation it had. Re-deriving the 23 patch rules against 3.3.11 (section Q.5) is still the thing
standing between this and a patched client.



**R.6 Two measurement defects closed, and the instrument that caught the third.** The compat
probe wrote `bundleBytes: text.length` into the shim's per-build table -- a record, so the wrong
unit is what the next reader inherits. `tools/compat_probe.js` now emits `bundleChars: text.length`
beside `bundleBytes: Buffer.byteLength(text, "utf8")`, and `agent/regression.py::
test_measured_sizes_say_their_unit` reads the recorded row back against the bundle file's own size
(5,362,738 code units, 5,397,933 bytes -- 35,195 apart, all of it non-ASCII in the bundle) rather
than trusting the field name; the mutation that puts `text.length` back is CAUGHT by two checks.
Rewriting that table changed the shim's bytes, and the page-assets suite failed on the spot: the
`?v=` stamp in `standalone.html` no longer matched the file it names. That is the cache-buster
defect from §Q.3 doing exactly what it was built to do, caught in the repo rather than on the
client. The third item is about the sweep itself: the four currency mutations first reported MISSED
with every arm green, because the mutation tool runs a fixed list of gate tests and nothing in that
list opened `BootState.cs`. That is the same failure the knowledge index and `combat/observation.py`
documented, so it is now a ratchet -- `test_mutation_targets_have_witnesses` derives the target file
of every mutation from the tool's own source and requires a test the sweep runs to name it, pinning
the 24 targets (175 mutations) that are covered only behaviourally and may only ever shrink.

**R.7 Why 23/23 matched and the client still black-screened.** The rule audit the
section Q owed turned into something sharper. `tools/apply-patch-rules.py --check`
classifies each rule as `applies`, `already-applied`, `ambiguous` or `moved`, and on
build 3.3.11's vendor bundle nothing has moved: 23/23 anchor. But applying the same
rules to their own output applies 4 of them again -- rules 1, 2 and 3 are boundary
rules whose replacement still contains their own start marker, and rule 0's
replacement contains its needle. A second adoption therefore wraps those callbacks
twice, and a doubled wrapper is exactly what "Callback was already called" looks
like from the client. That is the mechanism behind the recorded "pristine alone
didn't fix it, the updater re-patches". Both the reference patcher and the shipped
`ClientBundle.ApplyPatches` now skip a rule only when it is self-embedding and its
effect is already present: pass one still takes 23/23 with the same 3,252-character
growth, pass two applies nothing, and the patched bundle is a fixed point. The blunt
version of that guard ("skip when the effect text is present") was written first and
quietly dropped rule 10 from the first pass, because the vendor bundle already
contains that snippet -- which is why `test_patch_rules_are_idempotent` builds a
bundle out of each rule's own anchor and tests all 23 individually, and why the blunt
guard is itself now a mutation (CAUGHT). What is still owed at the root:
`tools/make-patch-rules.py` generates the rules from `patch-engine.js`, and the
generated replacements still embed their anchors, so the rules file is non-idempotent
by construction and the guard is a safety net, not the fix (#94).

## S. The session that bounced on one border, and what bounding it took (2026-11-04)

**S.1 The ceiling was throughput, not intelligence.** Session 13:03 on the live client:
34 interventions, 30.7 ms per step, 18 minutes, and it ended on
`quest 4262 stayed chosen after 3 retirements`. Nine of the middle steps were
`nav.goto {map_id: 145490945}` sent from 145490944 -- the target is one crossing away, the
client walked the character from cell 299 to cell 537, could not cross, and walked back.
Every bounce was verdicted `advanced` because the cell really did change, so the loop's own
progress accounting credited it: `verdicts {advanced: 12, noop: 3, failed: 2}`. About 84 s
of the 18 minutes went into that one border, and the retirement guard, not the planner, is
what finally stopped the run.

**S.2 The first fix keyed on the wrong distance, and the live client refuted it.**

`ROUTE_STEP_LIMIT = 12` was written for a route: twelve crossings that never come nearer is a wrong plan. The obvious reading of the bounce was "a target one border away needs a smaller budget", so the limit was scaled by `route_distance(here, target)`. The next live session bounced anyway -- eight asks in a row (session `76b38fdf`). The reason is in the shipped index: that objective's map was **four** crossings away and the loop was asking for its *first hop* -- `route_first_hop(145490944, 145490435)` returns `(145490945, 'bottom', 4)` -- so the distance to the final target stayed 4 and the generic budget of twelve still applied. What needed bounding was the **border**, not the destination.

`_travel_call` is therefore now a thin wrapper around the branch logic: whatever crossing the step decided to ask for, by `map_id` or by `direction` (the client walks both identically), is counted per `(objective, here, destination)`. A border asked `CROSSING_ASK_LIMIT = 2` times from the same map without landing is **spent**: `_open_exits()` drops spent borders before the "all exits lead back" fallback, so the search cannot re-list one either. When every border out of the map is spent the step is a read that names them (`the bottom/right edges -> 145490945/145490946`). Bounded replay of the live shape asks twice for the hop, twice for the other border, then reads -- four `nav.goto` instead of twelve-plus.

The distance-scaled limit became unobservable once the border count existed: its mutation read `MISSED, 0 failing checks` while the other eight arms were green. It was deleted rather than kept and papered over with a check that could not tell it apart, and `ADJACENT_ROUTE_LIMIT` was renamed `CROSSING_ASK_LIMIT` because it no longer describes an adjacency.

**S.3 Two guards, one bounce.** The tightened budget pre-empted an existing check that
asserted the A->B->A return guard (`RETURN_LIMIT = 3`) gets the last word. It was not wrong,
just no longer first: the fixture holds the character on the same map, which is exactly the
"crossing refused" shape the travel guard was written for. Both rows are now pinned as the
tighter of the two budgets, and separately as *which* guard spoke, because "bounded" alone
would still pass if either guard were deleted. `test_ready_objective_scan` had the same
over-specification -- it asserted the *fourth* step of a fixed-scene replay was still a
crossing; the objective is now travelled to twice and the fourth row is the bounded read,
still claimed by the same objective, which is what lets a later visit try the border again.

**S.4 An arrival settles the question.** `steps` counts crossings that failed to land, and the planner is not consulted while it stands on the target -- so a budget spent on an earlier visit would refuse a legitimate return for the rest of the run. `_note_situation()`, the function every real decision runs before it plans, clears both memories whose destination is the map underfoot: the route budget and the per-border counts. This was written *after* a mutation proved it was needed: the first witness called the leaf `_note_arrival()` directly, so deleting the call from `_note_situation()` read `MISSED, 0 failing checks`. Moving the witness onto the live path made it CAUGHT -- which is the third recorded instance of "a check that tests the leaf, not the wiring, witnesses nothing".

**S.5 The sweep manufactured its own verdicts.** All five new mutations first read
`CAUGHT ... 0 failing check(s) (section crashed)` -- not because the code is well covered but
because the driver had been started on the embedded runtime, whose `._pth` ignores the
working directory, so the snippet died on `import agent` before any mutation was applied and
a crash counts as a red gate. The driver now runs the snippet once against the **clean**
tree and refuses to start on a red (`REFUSED: the clean tree already fails the snippet`), one
extra ~37 s pass per batch, after the `MUT_PLAN` short-circuit so planning stays free. That
preflight immediately caught a real failure my subset run had missed
(`test_ready_objective_scan`, S.3), which is the second documented recurrence of "the suites
I chose to run are not the suites that bite".

**S.6 Weight-space round four, done the way the paper did it.** The corrected operation: a
label-free direction traced on TRAIN rows only (mean h2 where the head disagrees with the
rule engine minus mean h2 where it agrees), subtracted from the 64 W2 columns that project
hardest onto it, dose swept 0.05..2.0, with random-direction, shuffled-pair and
uniform-compression controls. At eta=0.35 accuracy 0.9010 against the incumbent's 0.9043 and
mean gain 0.7997 against 0.7977 -- and capture-vs-oracle 0.1597 against 0.0799, which is the
number that would have made this look like a breakthrough in an aggregate-only report. The
paired per-row test is 31 better / 29 worse, p=0.897: a coin flip. The one dose that reached
significance (eta=0.45, 80/55, p=0.0385) buys rare `farm.start` rows by destroying that
class -- recall 0.884 -> 0.690, accuracy -1.0 pt -- which is a protected-skill regression, not
a trade. Small doses (eta<=0.15) move fewer than 10 rows at p=1.0: nothing happens at the
doses that do not damage. Weight-space editing stays closed on the tool policy; the consult
gate (0.9043 -> 0.9099, 26 rescued / 5 broken, p=0.0002) remains the route that pays.
Artifact: `models/weight_edits_round4_trunk.json`.

**S.7 Certified state, and what the live sessions say.** Gate **1957 checks, 0 failed** (exit 0, twice, with `doc_facts --render` reporting "rendered 0 doc(s)" between the runs); Kaggle mirror 0 drift; the mutation list stood at **603** at that point and all eight `adjacent-border` arms are CAUGHT with 2-8 real failing checks each, after `clean tree runs the snippet: 0 failing checks, exit 0` and ending `restored: 0 failing checks, exit 0`. (S.8 supersedes both counts: 1970 checks, 609 mutations.)

Live, from `autopilot.dofus-01.jsonl` (17.5 MB, 1,960 step rows, 32 sessions) -- repeated asks for one destination, per session:

| session | when | code | steps | worst border | verdicts |
|---|---|---|---|---|---|
| `dee5e40b` | 13:03 | before any fix | 34 | **9 asks** for 145490945 | advanced 12 / noop 3 / failed 2 |
| `76b38fdf` | 14:47 | distance-scaled budget | 16 | **8 asks** for 145490945 | advanced 7 / noop 1 |
| `663eaa47` | 15:03 (your run) | distance-scaled budget | 28 | 2 asks (two borders) | advanced 10 / noop 0 / failed 0 |
| `42cb6dcf` | 15:53 | crossing guard | 8 | **1 ask** | advanced 5 / blocked 3, 18.8 s |

`42cb6dcf` did real work instead of bouncing: a tapped interactive element, three `quest.advance` calls on quest 4262 (dialogue and close), one border crossing. Be precise about what this does and does not prove: the broken-border shape did not recur in that session, so the live claim is **no repeated border was observed**, while the bound itself is proven by the replay of the recorded route plus the eight mutations. The distance-scaled fix's own refutation is the row count in `76b38fdf`, and `663eaa47` shows a session where the target was adjacent (so the older code applied) also staying at two asks.

Two process facts earned the same instruments their keep: `/brain/health` reported `code_stale: ["agent.planner"]` after I edited the file under the running daemon -- the currency probe catching my own edit -- and the restart cleared it (`code_stale: []`, pid 11308). And `combat_ai_server.py` on port 8897 was an orphan whose parent was a `bash.exe` from an earlier session's shell snapshot, running 07:40 code; reaping it and its child left the fresh daemon to bind cleanly.

Still owed: the rebuilt host against a stale brain in anger (#93), and the payload rebuild so `setup.exe` stops shipping the pre-fix shim (#90).

**S.8 Priced the verdict complaint, and answered it with a shadow instead of a change.**
#96 says a same-map cell bounce is credited as `advanced`. Before touching the verdict
rule -- whose four words feed the corpus labels and the online learner -- I counted what a
promise-aware verdict would have reclassified, over the whole live log (980 verdict-bearing
rows, 32 sessions): 192 `advanced`, of which the map actually changed in **15**; a
`nav.goto` that named a map and did not arrive accounted for **23 rows = 12.0% of
`advanced`**, and 16 of those 23 were the two bounce sessions. Cell-only movement on other
tools (110 rows: `quest.advance`, taps, moves) is real progress and was never in question.

The rule was not changed. What shipped is `verifier.promise_state(tool, args, before, after)`
-> `met` / `unmet` / `unknown` / `na`, stamped on the step row as `promise` and summed in the
session summary as `promises`. It changes no verdict and no label; `VERDICTS` is pinned to the
same four words by a check. `unknown` is a real answer and must not collapse into `met` -- the
`blocked` rows have no post-observation at all, and a shadow that called those successes would
be worse than no shadow. One arm looked unreachable and was not: `farm.start` carries a
`map_id` in the schema, so a non-crossing tool must still read `na`; the mutation that dropped
the tool guard was MISSED until that row was added, then CAUGHT.

**And it caught the shape live, on the current build, in the very next session** (pid 6280,
`code_stale: []`, 8 steps): `verdicts {noop 1, advanced 4, blocked 3}` beside
`promises {unmet 2, na 3}` -- two `nav.goto {direction: left}` from 145490435 that never left
145490435, one of them verdicted `advanced` purely because the cell moved 462 -> 484. The
crossing guard spent the edge after those two asks and the loop went on to talk to the NPC. So
the disagreement is not a historical curiosity: `advanced` still overstates progress today, at
roughly one in five acting rows in this sample, and now every session says so in its summary.

Decided against, for now: making `noop`/`advanced` promise-aware. It would relabel 12% of the
existing corpus's positive rows, and `online_learn`'s newest-slice holdout is keyed on those
labels -- the shadow gives the count without spending the labels. Numbers: gate **1970 checks,
0 failed** (twice, render converged), mirror 0 drift, mutation list **609**, the six `promise:`
arms CAUGHT (3/1/1/1/1/1 real failing checks) after one was MISSED and repaired by adding the
`farm.start` row, `restored: 0 failing checks, exit 0`.

## T. The promotion gate that nothing ever opened (2026-11-04)

**T.1 §L.43 said half the runtime has no mutation anchor, and it was right about this file.**
`combat/online_learn.py` decides whether a learned fight calibrator replaces the analytic
estimate -- the most consequential promotion the loop makes -- and the sweep had never written a
byte into it. The witness is now `evaluate_gate` on synthetic slices of 60 interleaved fights
(train 42 / eval 18), and four anchors in it are CAUGHT: issuing a verdict below the fight
floor, clearing the AUC margin, accepting any permutation result, and dropping the permutation
comparison (1/1/2/1 real failing checks, `restored: 0 failing checks, exit 0`).

**T.2 The arms, in the numbers the gate itself prints.** A calibrator that merely re-ranks the
analytic score gets `delta_auc 0.000` with both AUCs at 1.000 and `perm_p 1.000`, refused with
*both* reasons ("does not beat the analytic" and "permutation p ... is above"). The same slice
with the analytic score ranking the outcome backwards gives `delta_auc 1.000`, `perm_p 0.002`,
`reasons []` and passes -- so the gate discriminates rather than refuses everything. Ten fights
read `collecting: 10 of 40 resolved fight(s) with features`; a one-class slice is refused through
the singular `reason`. The two verdict shapes are different objects and asserting `reasons` on an
early return is a KeyError at gate time -- designed around, now pinned.

**T.3 A property the fixture exposed rather than assumed.** A third arm made the analytic score
constant (every fight `raw_score 0.5`): the baseline AUC is then **0.500** -- an uninformative
comparator -- and the gate **passes** the candidate. The promotion margin is relative to whatever
the analytic score happened to rank, with no requirement that the comparator itself beat chance,
so an 18-row eval slice and a flat baseline is enough to promote. Pinned as a check rather than
quietly inherited. Whether to demand a baseline floor is a decision for the same review that owns
#77 (whether installed fights should use the trained policy at all).

**T.4 One mis-guess, caught by measuring first.** The draft asserted `eval_n == MIN_EVAL`; the
real slice is `max(MIN_EVAL, round(n*0.3))` = 18 against a floor of 12. Running the probe before
writing the assertions turned that into a fixed check instead of a red gate. #86 is one-third
landed: `online_learn.py` has anchors, `duel.py` and `lexical_policy.py` still have none.
Certified with this section: gate **1981 checks, 0 failed**, mirror 0 drift, mutation list **613**.

**T.5 `duel.py` is anchored too, and two arms had to be rebuilt rather than deleted.**
`test_duel_simulator_keeps_its_own_contract` runs in 0.22 s (hand-built duelists on the tracked
`data/maps/100139008.json` -- no dataset, no solver, so the sweep can afford it per mutation) and
pins 21 things the simulator owes its own numbers: the actor alternates on turn parity; a turn
starts with the actor refilled and the other side at zero; the round cap releases the duel on the
sixth half-turn of `max_rounds=3`, not the fourth; overtime is scored on HP **fraction** (A
500/1000 beats B 400/500 -- raw HP would pick A), and an equal fraction is a draw, not a win; the
board hands the actor the `player` team and `by_me`, its own cell/AP/spellbook, and the fight
payload the policy reads carries `my_cell`, `current_fighter_id` and `last_actions`; an ally heal
outranks the same spell's damage *only* for an ally target; a fractional AP cost is never free;
ranges are clamped below and rounded; `EFFECT_BY_ELEMENT` maps five elements and wraps outside
them; an engaged pair is walkable, inside 3..8 and has line of sight; and `lookahead_features`
puts back the AP it borrowed.

The first sweep on it read **6 CAUGHT, 2 MISSED**: the round-cap mutation could not be seen
because my fixture only checked a state already past the cap, and the line-of-sight mutation could
not be seen because seeds 1/2/3/7 happen to draw pairs that have LOS anyway. Both were fixed by
measuring the discriminating case rather than by weakening the claim -- the full six-step trace
for the cap, and seeds 9/15/16/21, whose first band-legal pair lacks LOS (seed 9 would return
`325,424` without the requirement instead of `271,200`). Re-swept: **8 of 8 CAUGHT**, one real
failing check each, `clean tree runs the snippet: 0 failing checks, exit 0`, `restored: 0 failing
checks, exit 0`. That leaves only `ai/lexical_policy.py` unanchored from #86's list.

**T.6 `ai/lexical_policy.py` is anchored, which finishes #86's list.** The seam that ships the
93.3 command number is `recount` + `read_stated` + `extract_value` + `_fold`, and all four are pure
-- no sklearn, no joblib, no corpus -- so `test_lexical_seam_fills_only_what_the_sentence_states`
runs 24 checks in 0.21 s and seven anchors came back **7 of 7 CAUGHT** (2/1/1/1/1/1/1 failing
checks). The contract is *fill only*, and the arms are that sentence made executable: a stated
value never overwrites what a span already grounded (`kill_count 2` stays 2 against "100x";
`nav.goto` with `x` half-grounded keeps `x=1` rather than taking the stated pair), a `2k` price is
2000 kamas not 2, a whole digit run moves out of a span and not just its first character
(`12 potions` → quantity 12), the longest stated value wins (`laine de bouftou` over `bouftou`),
a value is never grounded on a fragment of a word (`xaine` does not match `aine`), and folding
separates on punctuation instead of welding tokens together.

Two of those are observations rather than endorsements, and are pinned as such. `quest.list` has
no `quantity` in its schema, so `recount("3 kolk")` puts the 3 in **`level_max`** -- a plausible
reading, but a number landing in a field the player never named. And `read_stated("farm bouftou
3x5x")` fills nothing, because the `\b(\d+)\s*[x×]\b` boundary fails on an ambiguous digit run:
right outcome, worth knowing it is decided by a word boundary.

Certified with #86 closed: gate **2026 checks, 0 failed**, mirror 0 drift, mutation list **628**
(12 arms added across `online_learn.py`, `duel.py`, `ai/lexical_policy.py`, plus 6 for the promise
shadow and 8 for the crossing guard). The full #18 re-sweep is now worth running against a tree
where the fight-calibration path is no longer invisible to it.

## U. What the log said back when it was asked honestly (2026-11-04)

**U.1 The overstatement is bigger than §S.8 made it look, once crossings are the denominator.**
Backfilling `verifier.promise_state` over the shipped log (`autopilot.dofus-01.jsonl`, 18.5 MB,
11,024 records, 1,007 steps that carry a verdict): **791 `nav.goto` calls, 15 of which landed on a
different map** -- 98.1% unmet, Wilson 95% CI 96.9-98.8%. Most of those 776 were correctly verdicted
`noop`/`failed`/`blocked`, so the defect is the credited subset: of the **85 `nav.goto` rows
verdicted `advanced`, 70 (82.4%) never accomplished the map change the call asked for**. §S.8's
"12.0% of all advanced rows" is true but easy to under-weight: among crossings specifically, the
accounting is wrong four times out of five. Four sessions since the field shipped repeat it live
(4/4 `nav.goto` unmet, 3 credited `advanced`).

Honest caveat before anyone relabels anything: a `nav.goto {map_id}` naming the map the character
already stands on -- a house door on the same map -- reads `met` when nothing happens, so `met` is
an upper bound on crossings that worked. `cell`, `npc` and `zaap` asks read `na` and are excluded,
and a row with no post-observation reads `unknown` rather than being counted either way.

**U.2 The log's own shape, measured rather than assumed.** 8,839 of the 11,024 records (80.2%) are
`wait` rows -- idle passes that acted on nothing -- against 1,007 acting steps, which is the
backdrop any "cheaper per decision" claim (#91, #66) has to be quoted against. 1,007 records have
`kind: decision` with a tool and no verdict: a *distinct* record type, not a duplicate -- so a
reader that counts rows containing `"tool"` reports 2,014 steps where there are 1,007, which is
how the first pass of today's analysis mis-stated a session as 8 steps when it had 5. And the
writer glued 15 record boundaries (`}{` with no newline, 0 CRLF in the file), which is #13's class
recurring at low rate; filed as #98 with these counts, because a `splitlines()` reader silently
loses the record after each glue point.

**U.3 Run sizing is honoured, once the right key is used.** `steps_per_goal` reaches the pilot
(`_make_pilot` reads it and `_DaemonAutopilot` takes it): a session asked for `steps_per_goal: 2`
stopped at exactly 2 steps. A session that ends early is therefore reporting a real stop condition,
not a lost parameter -- the current run-control surface is trustworthy for sizing.

## V. What the loop does with its time, measured from the record (2026-11-04)

Taken off `autopilot.dofus-01.jsonl` (10,987 timestamped records; 1,007 acting steps, 8,839 `wait`
rows, 32 sessions) while a mutation sweep held the working tree, so **no timing measurement is made
here that depends on a quiet machine** -- every number below is read out of records taken earlier.

**V.1 94.7% of wall-clock is waiting, and it waits for one reason.** In the five largest sessions
the split is e.g. 2,903 waits against 20 steps over 1,141 s (1,081 s attributable to waits), 2,021
against 36, 1,411 against 33; the most active session in the file (db682fc5) is the exception --
294 steps, 189 waits. The wait rows carry their own cause: **8,342 of 8,839 (94.4%) read "the
policy had no action to take"**; the rest are recovery (`incomplete` 434, `stale_state` 22) or an
explicit group wait (9). So the dominant cost is not fighting, travelling, or the model -- it is the
loop re-asking a policy that has already said it has nothing to do.

**V.2 The stall is long, uniform, and almost never escalated.** The wait record stores its run
length, and at the recorded poll gap (wait→wait: median **0.35 s**, p90 0.35 s, max 3.48 s) that
gives a **median stall of 198 polls = 69.3 s**, p90 437 = 152.9 s, max 509 = 178.1 s; **94.9% of
wait rows sit inside a run of 100 or more**. `escalated` is set on **5 rows out of 8,839**. So the
bound that exists (~512 polls) is reached regularly, and the path meant to surface a stuck run fires
about once per 1,770 waits. Median step latency for comparison is 22.45 ms.

**V.3 What this prices, and what it does not decide.** Two independent levers, both needing a call
rather than more measurement: (i) cadence -- an idle pass at 0.35 s is ~2.9 polls/s of work whose
answer is already known; back-off while `consecutive` grows, or gate on the client's own event
stream, converts a 69 s median stall from ~198 passes into a handful. (ii) #66 -- the no-action
answer is 94.4% of waits, so the cheap version of "stop asking the combat brain when nothing can be
advised" is to stop re-asking *anything* until the observation changes, and the 434 + 22 recovery
rows are exactly the cases that must keep polling. This section does not pick the numbers; it gives
#91 and #66 a denominator so the choice is made against measured cost instead of a guess. Deliberately
no live re-measurement here: a sweep was mutating the tree, and this host's timings are 5.7x off
under background load (Windows host quirks, §L).

**V.4 The 70 credited-but-unmet crossings are not one class, and that decides the shape of #96.**
Splitting them by *which attempt on that border from that map in that session* it was:

| attempt on the same border | rows |
|---|---|
| first | 30 |
| second | 4 |
| third | 2 |
| **fourth or later** | **34** |

and by what the call asked for: **47 by `direction`, 23 by `map_id`** -- so most of them are the
costume the crossing guard was built to bound. For context the full nav.goto population is
`(noop, unmet) 694`, `(advanced, unmet) 70`, `(advanced, met) 15`, `(failed, unmet) 12`.

The reading: a **fourth-or-later** ask for a border that has already failed to deliver is not
progress under any definition, and those are half the credited rows (34 of 70). A **first** ask that
has not landed yet is often just in flight -- the crossing is being walked and the settle window may
have expired before the server moved the character -- and the loop already says "nothing observable
changed" separately, as 694 `noop` rows. So if `advanced` is ever made promise-aware, the rule that
survives contact with the data is *a repeated ask for an already-refused border is not progress*,
not *a crossing that has not landed yet is a failure*. The first form relabels the half that matters
and leaves the honest in-flight case alone; the second would move 30 rows that are not defects, and
would do it inside the labels `online_learn` trains on.

**V.5 A correction to §S.2, found by auditing my own claim.** §S said the bounded read row
"names the border that refused", quoting the text the planner builds. That is true of the object the
planner returns, and the fixtures assert it -- but it is **not in the live log**: counting the shipped
rows, **0 of 791 `nav.goto` records carry a `why` at all**. So the sentence is observable offline and
invisible on the client, which is exactly why I could not attribute the bounce to a branch from the
log earlier today and had to reconstruct it from the index. What the log *does* show for the same
state is a `wait` row with `reason` (8,839 of them carry one; 8,342 read "the policy had no action to
take") and, when the loop is forced to emit something, a `blocked` step with
`error="policy returned no call"` (27 rows). The claim in §S is therefore narrowed to: the planner
names the refused border in its reason; a live row does not yet, and that is an instrument gap of its
own (#99), not a property of the guard.

**V.6 What that means for reading the crossing guard's effect.** Because the reason never lands, the
live evidence that the bound fired is *behavioural*: repeated asks for one border stop at two, and the
following rows are work elsewhere. That is what §S.7's table measures (9 -> 8 -> 2 -> 1 worst-case
asks per destination). Any future claim of the form "the row said why" has to be checked against the
log, not the returned call -- which is the same lesson as §S.5's subset blindness, one level down:
an assertion about an artifact's *contents* is only worth what its recorded form shows.

**V.7 §S.7 understated the worst case, because it looked at the wrong session.** Re-deriving the
repeat count per `(observed map, asked border)` across the whole log, the sessions in §S.7's table are
nowhere near the record. From map **147063810** there are **731** nav rows, of which **721 are the same
`nav.goto {direction: left}`** and **every single one leaves the character on 147063810** -- 688 verdicted
`noop` (so the accounting did catch most of it) and **43 credited `advanced`**. The worst single session
repeats that one border **284 times**; the next are 193, 60, 56, 31, 25, 22, 19.

What those asks belong to is the part that matters: during all 731 rows the current objective was
`QuestObjectiveFightMonster` ("Defeat 1 Aggressive Merkaptan...") **with `map_id: None`** -- a hunt with
no destination map. That is the fight-**search** path: it reaches a direction ask through
`_explore_with_memory` on the search branches, which never passes through `_travel_call`, so **the
crossing guard added today does not count it, and cannot spend it**. The search's own memory records
maps *stood on*, and when a map offers one usable edge the "all exits lead back; walking back beats
stalling" fallback re-lists it indefinitely. So the honest scope of §S is: the *travel* bounce is
bounded at two asks per border; the historically largest bounce -- a single refused edge asked 721 times
while the character hunts -- is still open, filed as #100 with the fix design (count at the point the
search chooses, not only at the travel wrapper).

One instrument caveat on the same rows, because it will mislead the next reader: `obs_before.map.exits`
is **empty for all 731 rows**, which does *not* prove the planner saw no exits -- `obs_before` is the
compacted projection the row keeps (#83's slim observation), not the bytes the planner read. A fixture
that infers "no exits, so explore returns None" from a logged row would be testing the compactor.

**V.8 The guard that exists for exactly this shape never fired.** Repeating the audit over *every*
tool rather than just `nav.goto`, the longest run of an identical ask and the longest run of
nothing-changed actions are the same event: session `db682fc5` asked
`nav.goto {direction: left}` from 147063810 **276 times in a row**, of which **275 consecutive
verdicts were `noop`/`failed`** (2b0723fc: 189 and 188; then 56/55 and 48/47). No other tool in the
whole log repeats like that. Yet the loop's own stall detector -- the dead-window bound that ends a run
when it is only reading -- did not trigger, because a `noop` `nav.goto` **is an action, so it resets
the counter**. Four of 36 sessions show a run of 20 or more nothing-changed actions; the worst is 275.
So for a loop that keeps asking a border that never opens, there is currently *no* backstop at all:
the verdict calls it progress 43 times, the dead-window counter calls it progress every time, and the
travel guard added today does not see the path. That is the full justification for #100 being a
planner-side bound rather than "wait for the dead-window to catch it".

**V.9 #96 and #100 are mostly different rows, and that picks the key for #96.** Testing the overlap
directly: of the **70 advanced-but-unmet crossings, only 4 (6%) sit inside one of the ≥20-identical-ask
runs** from §V.8 -- the runs (276, 189, 56, 48 rows, all `nav.goto {direction: left}` from 147063810)
are almost entirely `noop`-verdicted, so the pathological spam is *correctly* uncredited while the
miscrediting lives in short, interleaved sequences. None of the 15 `met` rows is inside such a run.

The consequence for the design: a rule keyed on **consecutive** repeats -- the natural reading of "a
stuck loop" -- would relabel 4 of 70 and leave the actual defect in place. The rule that covers it is
the **cumulative** count of asks for one `(objective, here, border)` since the last arrival on that
border, which is §V.4's "fourth or later" and catches 34 of 70. Those two numbers are consistent, not
contradictory: 276-in-a-row is one border hammered by a search that never advances, while the 70
credited rows are mostly borders asked 4-19 times *across* a session, between other actions. Both
counts come from the same structure, which is the argument for #100's single shared counter
(`_crossings`) serving three purposes: bound the travel path (done), mark the search's exhausted visit
(#100), and decide #96's verdict question -- instead of three rules with three thresholds drifting.

Also measurable in the same pass, and the honest live status of §S's fix: the three sessions run on
builds containing the crossing guard contain **zero** rows inside any ≥20 identical-ask run, against
4 sessions historically that do. That is an absence of the pathological shape, not an observation of
the bound firing, which stays #97.

**V.10 Auditing today's own additions, and the arithmetic that looked wrong.** Three checks, because
"the gate is green" and "the gate ran my work" are different claims:

* `data/last_gate_run.json` records **2026 checks, 0 failed**, and `sum(per_test) == 2026` -- the
  accounting rule holds, not just the total. Today's five suites are each in it with their own counts:
  `test_adjacent_crossing_is_bounded_tighter` 5, `test_promise_shadow_names_a_bounced_crossing` 13,
  `test_online_learn_gate_needs_transfer_it_did_not_copy` 11,
  `test_duel_simulator_keeps_its_own_contract` 21,
  `test_lexical_seam_fills_only_what_the_sentence_states` 24 (74 checks), plus the re-fitted
  `test_objective_travel_is_bounded` at 8.
* All **33 of today's arms are banked by label** in the focused logs, so the running full pass is not
  re-running them and has not dropped them either -- `still to run in this pass: 0`.
* The full pass printed "595 of 628 selected (34 already caught)". 628 − 34 = 594, not 595: the extra
  banked label is the deleted distance-scaled-budget arm (§S.5, §T.1), which no longer exists in
  `MUTATIONS` and so removes nothing. Checked rather than reasoned about, because "one fewer row was
  removed than expected" is exactly what a *silently dropped* arm looks like, and that would have meant
  an unverified claim in §T.

### V.11 Which objective owns the stall, and the border asked 528 times (measured 2026-11-04, off-tree)

The payoff of #100 and #66 was sized from the live log -- `trajectories/dofus-01.jsonl`,
2,194 records, 31 sessions, 2026-10-27T14:22 to 2026-11-04T18:41 -- by giving every
intra-session gap to the `current_objective` carried by the record it follows. Two
instrument facts came out of the parse itself: `ts` is in **seconds**, not the
milliseconds the browser log uses, and records are joined so loosely that a
line-split loses rows while a brace-skipping scanner finds none -- only a streaming
`raw_decode` over the whole text recovers all 2,194.

- Intra-session stall: 13,143.5 s over 2,163 gaps. Median gap 3.02 s, p90 6.0 s,
  max 294.3 s. Unattributed 933.5 s (7.1%) -- records whose observation carries no
  `current_objective` at all, reported as its own bucket rather than hidden in a total.
- `QuestObjectiveFightMonster(6)` owns **9,231.1 s = 70.2%** of the stall across 1,669
  gaps. `QuestObjectiveFreeForm(0)` 2,808.5 s = 21.4% over 252 gaps, `DiscoverMap(4)`
  86.5 s, `GoToNpc(1)` 83.9 s.
- The fight objective's cost is **volume, not depth**: 1,669 waits of ~5.5 s mean, not a
  handful of long ones. So #66 pays in round trips removed, and no timeout change would
  buy it back.
- Worst single border: 147063810 -> 147063298 asked **528 times** inside the
  2026-10-27T16:47 session (build 2026-10-27l) and landing **0** times; the same border
  carries 74, 61 and 60 asks in three more sessions -- 723 asks, 0 landings in total.
  Every one of them sits on `FightMonster(6)`, which is the search path #100 covers and
  the crossing guard does not. (The "asked 721 times" in #100 was the cumulative count
  mistaken for a single run; the session-scoped figure is 528.)
- Today's sessions repeat the shape one order smaller: 145490944 -> 145490945 asked 9
  times at 13:03 and 8 times at 14:47, both landing 0, on `FreeForm(0)`.
- Caveat on attributing any of it to the planner fix: every record from today still
  stamps `agent_build` 2026-10-31a, so a live row cannot say whether the process carried
  the per-border counter. A fix is only observable live once the build label moves with
  the code -- the same defect class as #89, and a precondition for #97.

### V.12 The fight stall is one refused border, and #66 does not own it (measured 2026-11-04, off-tree)

§V.11 named the objective that owns the wall clock but not the mechanism, so the same
31 sessions were re-walked at bucket level: inside each session every border
`(here, dest)` asked by `nav.goto` is counted, a border asked 8+ times is marked
**refused**, and each intra-session gap is given to the bucket of the record it follows
(a `wait poll` behind a refused ask inherits that ask). Attribution is now complete --
13,143.5 s of 13,143.5 s, 100%, with no fallback bucket.

`QuestObjectiveFightMonster(6)`, 9,231.1 s:

| bucket | seconds | share of the fight stall | gaps |
| --- | --- | --- | --- |
| `nav.goto` refused-edge ask | 4,825.6 | 52.3% | 723 |
| wait poll behind a refused edge | 3,260.9 | 35.3% | 725 |
| `safe.stop` | 591.6 | 6.4% | 3 |
| `quest.advance` | 313.0 | 3.4% | 105 |
| `wait poll` (elsewhere) | 97.2 | 1.1% | 95 |
| `state.snapshot` | 74.9 | 0.8% | 5 |
| `nav.goto` that was not refused | 60.6 | 0.7% | 10 |
| `fight.join` | 7.3 | 0.1% | 3 |

- **87.6% of the fight stall -- 8,086.5 s, which is 61.5% of every intra-session second
  in the log -- sits behind the refused-edge loop that #100 exists to bound.** The
  mechanism is not a slow server: the asks themselves are cheap and the polls are 3.02 s
  at the median; the hours come from *how many times* the same dead border is asked.
- #66 gets refuted as a wall-clock win by this instrument: `fight.join` is **7.3 s** of
  9,231 s. "Stop asking the combat brain when nothing can be advised" was priced in
  decision counts (11% shipped, 21% pending), and on session time it buys almost nothing.
  Caveat, not a retraction: fight decisions also land in `fight_outcomes.jsonl` and the
  brain's own log, so absence here is not absence everywhere -- but for throughput per
  hour of session, #100 is first by two orders of magnitude.
- Priority is therefore #100, then #99/#101 so the fix is visible live; #66 stays open as
  a request-count and CPU item, not a stall item. Script: `decompose.py` in
  `C:/Users/sao/Documents/dofus-evidence/live-stall-attribution-2026-11-04/`.

### V.13 The four missed arms: one dead branch, one unstepped module, and a crash the new suite found (measured 2026-11-05)

The re-sweep verdict, read off `trajectories/mutation-sweep/full-628-pass.log`: 595 of 628
selected, **CAUGHT 591 / MISSED 4 / SKIP 0**, preflight `clean tree runs the snippet: 0
failing checks, exit 0`, restore `0 failing checks, exit 0`. Each of the four arms was
diagnosed before anything was asserted, and the causes are different in kind:

1. `critic: a pick is reported that the ranker never made` (`live_policy.py:454`,
   `+ int(picked is not None)`). The suite already had three counter cases, and every one
   of them reached the **raise** path: the stub policy carries no `params`, so
   `rank_plans(self.params, board, plans, self.cfg)` threw before the pick line ran. A
   counter that adds one per consultation is then indistinguishable from one that adds a
   pick. Repaired by stubbing `combat.plan_value.rank_plans` and giving the stub the
   attributes the call reads: `[(1.0, None)]` -> `asked 2, fired 0`; `[]` -> `asked 3,
   fired 0`; a real pick -> `asked 4, fired 1`. The raise case is kept as its own finding
   ("reached and gave up" and "never reached" are different facts, which is why the
   counter is taken before the ranking).
2. + 3. `env: a cast overwrites the target's hp instead of accumulating` and
   `env: every spell slot is declared castable`. Nothing in the tree **steps** the
   encounter env: the trained path is scored from cached activations, the duel simulator
   and the battle solver carry their own arithmetic, and `plan_benchmark` reads masks it
   never contradicts. So no snippet check could see either line, whatever it did. Added
   `combat/test_env_math.py` (five cases on a one-ally / two-monster board: 12 AP with a
   5-AP spell repeats exactly twice, 60 hp - 2x10 = 40; the 20-AP slot is masked False
   while the 5-AP slot and pass are True; mask and `step` agree that an unaffordable slot
   lands nothing; a kill floors at 0 and the untouched monster stays at 60; the turn
   hands AP to the team that acts next), and
   `agent.regression.test_env_scatter_and_mask_are_witnessed` so the mutation snippet
   reaches it in-process.
4. `no-call: the wait row never reads the reason`. The code it mutated was **dead**. The
   no-call branch copied `last_no_call` into `_forced_reason`, but that branch emits a
   blocked step, `_emit()` clears `_forced_reason` in the same pass (autopilot.py:698), and
   the goal loop clears it again per goal -- so no row could ever carry the copy. The
   witness that made the arm look covered was `inspect.getsource(...)["last_no_call"]`,
   which a different line of the same file also satisfies. The copy is deleted, the arm now
   mutates the live read (the blocked row's `note`, autopilot.py:1715), and the check
   asserts the planner's own sentence plus the `obs_keys=/map=/src=/age=` context on the
   record. Under the mutation two checks fail.

Re-run of the repaired arms: `MUT_ONLY="env:,no-call:"` selected 5 of 628, ran the
clean-tree preflight (0 failing checks, exit 0), and returned **5 CAUGHT / 0 MISSED** with
the tree restored byte-identical. The pass is therefore 595/595 killable.

**What the new suite found on its first gate run is the more useful result.** `env.step()`
guarded itself with `isinstance(act, jax.Array)`, and the backend shim's numpy branch sets
`jax = None` (backend.py:74) -- so stepping the env raised AttributeError on exactly the
interpreter `combat/encounter/backend.py` exists to serve, which is the installed copy
#76 claimed as fixed. The gate runs delegated suites as subprocesses and that is the branch
the suite landed in; the crash was invisible until something actually stepped the env. The
guard now asks `jnp.ndarray`, which exists on both branches, and the same five cases pass
on both backends: jax 3.401 s OK, `DOFUS_FIGHT_BACKEND=numpy` 0.007 s OK.

Two of my own standing claims needed correcting from the same measurements:

- **#99 is narrower than filed.** The reason does reach a record on current code -- the
  blocked row carries it in `note`. What no row carries is the reason for a call the policy
  *does* make (the plan's `why` has no field on `Step`), and the live log shows nothing at
  all because the attached brain runs a pre-fix build, which is #101, not #99.
- **#100 is still open and now measured on current code.** With every exit already walked,
  `_explore_with_memory` proposes the same edge **20 out of 20** times and never touches
  `_crossings` -- the crossing bound covers travel asks only. The fix must rotate the
  fallback through the spent set, not delete it: the walk-back rule is what keeps a dead
  search from stalling the run (§V.5's 467-pass lesson).
- **The line-ending rule I applied to myself was wrong.** The repo is
  `* text=auto` with `core.autocrlf=true`, and `combat/encounter/env.py` and
  `backend.py` sit on disk as pure LF (434/163 lines, 0 CRLF). "The file must be CRLF" is
  not the invariant; the invariant is that a mutation cycle leaves the file byte-identical
  and in the shape it found -- which is now what the arm-proof script asserts.

### V.14 #100 landed: the search now spends the border it keeps asking (measured 2026-11-05)

The bound the travel path got on 2026-11-04 never reached the search. Driving the current
planner with the live shape (a fight objective, no group on the map, a border that does not
deliver) gave, before the change: **20 asks of the same edge and zero entries in
`_crossings`** -- both for the never-landing case and for the every-exit-walked case. That
is the 528-ask border of §V.11, still reproducible on today's code, and §V.12 priced it at
8,086 s of session time.

The fix is one shared budget, not a new one. `_explore_with_memory` now:

- derives the spent set for this objective from `_crossings` (border key `(objective, here,
  destination)`, the same key the travel path uses) and merges it with the caller's `spent`,
  so a border asked `CROSSING_ASK_LIMIT` times is dropped by `_open_exits` before any pick;
- counts **every** ask it returns, including the one that leads to an unwalked map -- which
  is where the repeat actually lived, because `seen` only records maps the character has
  stood on, so a border that never delivers never looks walked;
- answers with nothing and records `(quest, objective, here)` in `_exhausted_at` when the
  exits exist but are all spent -- distinguished from a scene that reports no exits at all,
  which is not knowledge and must not retire the objective;
- keeps the fallback that answers, because a search that cannot answer stalls the run
  (the 467-pass lesson, §V.5); only its bound is new.

The scripted shape -- `child_for_objective` returning `{"kind": "explore", "args":
{"direction": "left"}}` for a fight objective with no readable group -- needed its own
guard: it asked its named direction with no counter at all. It now checks the count, refuses
without incrementing it again (a refusal is not another ask), and marks the map spent.

After, same fixtures: `left, left, top, top, [] x6` with `crossings = {->200: 2, ->201: 2}`
and `_exhausted_at = {(5, 7, 946)}` -- 12 asks fewer out of a possible 20, and a terminal
answer instead of a ring. `agent.regression.test_the_search_borders_are_bounded` pins all
of it (9 checks), and the mutation plan grew six `search:` arms.

Two bookkeeping findings from the sweep itself:

- One arm was MISSED the first time (`the rotation is a fixed edge`). It was not a witness
  gap: preferring the *least asked* edge and taking `exits[0]` produce the same sequence of
  asks once the spent set is applied, so no check could ever tell them apart. The
  preference is deleted -- an unobservable branch is debt, the same rule that removed the
  distance-scaled budget in §V.4 -- and the arm was re-pointed at the thing that does
  matter (`_open_exits(obs, spend)` -> `_open_exits(obs)`).
- Rewriting the method silently invalidated five *older* `fresh-search:` arms whose anchors
  lived in its body; the sweep reported them as `anchor not found`, and the gate's own
  anchor section is what would have made that red. All five were re-anchored to the new
  text with their intent kept, one obsolete arm removed, and the entry style bit back: the
  driver's tuples need a comma between the anchor literal and the mutation literal, or
  Python concatenates them into a 3-element tuple. `MUT_ONLY="fresh-search:,search:"`
  after the repair: **11 of 11 CAUGHT, 0 MISSED, 0 SKIP**, clean-tree preflight 0/0,
  restore 0/0.

### V.15 #101 landed: the brain stamps the code it is running on every row (measured 2026-11-05)

§V.11 found that a live row could not say whether a fix was loaded, and named it as the
precondition for #97 and #99 rather than a curiosity. The cause is specific: `agent_build`
is the *page's* stamp -- `scripts/mobile-disguise.js:31`, a hand-typed string, with its own
gate check (`the repo page carries the 2026-10-31a build`). The planner, the verifier and
the fight policy can be rewritten all day and that label will not move. Two stamps that
each say one true thing beat one stamp asked to say two.

`agent/autopilot.py` now computes its own identity:

- `BRAIN_MODULES` names the ten modules that decide a step (`agent.planner`,
  `agent.plan_stack`, `agent.autopilot`, `agent.thinking`, `agent.controller`,
  `agent.runtime`, `agent.verifier`, `agent.executor`, `combat.live_policy`,
  `ai.lexical_policy`);
- `brain_inputs()` takes the source of each: `inspect.getsource` when the process already
  holds the module (that is literally the running code), and the file `find_spec` points at
  when it does not. It does **not** import a module merely to hash it -- the first version
  did, and §V.16 has the cost;
- a module that yields neither is recorded as `<unloaded:ErrorName>` in the digest rather
  than dropped: a stamp that quietly loses a module would read as a code change, and that
  ambiguity is what this field exists to remove;
- `brain_build()` returns `brain-<12 hex>`, memoized per process;
- `_stamp_brain_build()` writes it into the row, called at the **one seam every row already
  passes** (`_append_log_line`), so there is no call site to forget -- thirteen writers, one
  stamp. An object with no keys is left alone rather than made invalid.

`agent.regression.test_the_brain_stamps_its_own_code_identity` keeps six checks, including
the ones that make it hard to fool: all ten modules contribute, the value is stable and
shaped, a stamp computed from different code differs, a stamped row still parses as JSON,
`{}` is untouched, and a real `ap._run()` puts the stamp on **every row kind** it writes.
Three sweep arms cover it (`MUT_ONLY="brain stamp:"` => **3 of 3 CAUGHT**, preflight 0/0,
restore 0/0): the digest replaced by a constant, the seam call removed, and the source
replaced by the module name.

What this does *not* yet do: the daemon health readout does not expose the digest, so the
host's `code_stale` decision still compares the page stamp only. Until a brain is restarted
on current code, the live log will keep showing an unattributable build -- the writer now
knows, the reader does not yet look.

### V.16 #99 landed: the plan reason is on the row, and #101 costs 0.2 s not 3 s (measured 2026-11-05)

**#99.** `ToolCall.why` already existed (`runtime.py:41`, filled by the planner at
`planner.py:3507`) and `record_meta()` carried it into the trajectory corpus -- but the
dispatch path built `_meta` at `autopilot.py:1922`, handed it to the executor, and never
put it on the run record: `Step` had no field for it. So the corpus knew why a call was
made and the log did not, which is precisely the asymmetry behind "0 of 1,044 step rows
carry a reason". One field (`Step.why`, capped at 240 chars, sourced from the same `_meta`
the recorder gets) closes it; it rides the record and never the directive, which is the
invariant `ToolCall`'s own comment states.

`test_the_plan_reason_reaches_the_action_row` (4 checks) drives a real `ap._run()` with a
policy that returns a call carrying a reason, and asserts the row carries it, that `args`
is still exactly the directive, and that a call with no reason is **not** given an invented
one. Two arms: dropping the fill (27 checks fail -- the field turned out to be load-bearing
well beyond its own suite) and reading `args["why"]` instead (1 check fails).

**A cost I introduced in #101 and the gate caught.** `brain_inputs()` originally hashed
each module by importing it and calling `inspect.getsource`. Importing
`combat.live_policy` to ask what it contains drags jax into every process that writes a
row: measured 3.4 s on this box, and the gate's own timing budget failed twice --
`test_gate_shadow_reaches_the_trajectory` went from under its 3 s ceiling to 5.2 s. The
mechanism is now: take the loaded source when the module is already imported, otherwise
read the file `find_spec` points at -- never import merely to hash. First-call cost is
**0.227 s**, `jax` is no longer in `sys.modules` after computing the stamp, all ten modules
still contribute, and the three `brain stamp:` arms still bite (3/3 CAUGHT) after being
re-anchored to the new lines.

Two rules this round keeps teaching: a witness that depends on which modules a process
happens to have imported is a witness that can pass for the wrong reason (the drift check
was rewritten against the digest's inputs for that reason), and a refactor silently
invalidates the mutation anchors in the code it moved -- the gate's anchor section is the
thing that makes that visible rather than a sweep full of `SKIP anchor not found`.

Final state of the verification round: the 595-arm re-sweep landed 591/4, all four missed
arms repaired and re-run to CAUGHT, the search bound (#100) landed with 11/11 arms and a
20/20 -> bounded proof, the brain stamps its own code identity (#101) and the row now
carries the plan's sentence (#99), one shipped-path defect found on the way
(`env.step` under the numpy backend), and the gate at **2049+ checks, 0 failed**.

### V.17 A coverage claim that did not hold: the replay corpus counts labels, not captures (measured 2026-11-05)

While looking for evidence that #100's bound does not disturb recorded history, the frozen
replay corpus was read directly (`data/replay/planner_obs.jsonl`, 420 rows). The gate check
about it says *"the corpus covers several recorded sessions, not one lucky run"* -- and the
measurement says the check is counting the wrong thing:

- **17 session labels, 12 distinct contents.** One identical 30-row window is carried under
  **six** different session ids, so the label count overstates the number of captures by
  half;
- 417 of the 420 rows are one objective type (`QuestObjectiveFreeForm`), 9 distinct maps,
  and 376 rows are a consecutive same-objective / same-map repeat;
- there is **no fight-search shape in the corpus at all**.

The consequence is a correction to this session's own reasoning, not to the code: the fact
that `test_planner_replay` passed after the crossing bound is *not* evidence that the bound
leaves recorded behaviour intact. The corpus cannot see the path that was changed, because
it never contains it -- the same "the snippet cannot reach the module" failure mode that
made two `env:` arms MISSED in §V.13, now found in a fixture instead of a selection list.
What the replay does still prove is that the plans it does contain are reproduced from the
frozen observations.

The first fix is landed with this section: `test_planner_replay` now also asserts
"the sessions are distinct captures, not one window under new names", hashing each
session's rows and requiring at least 10 distinct bodies. Measured at 17 labels / 12
distinct bodies (largest duplicate family: 6 labels for one 30-row window), so the check
passes at 10 and would fail at 13 -- it constrains rather than decorates. The second half
stays open in #103: re-collect so at least one `QuestObjectiveFightMonster` search and one
refused-border sequence are frozen, which is what would make a future bound on that path
measurable against history instead of only against synthetic fixtures.

The general rule this is an instance of: a green suite is evidence only about the shapes it
contains, and a diversity claim needs the *content* counted, not the identifier.

### V.18 The buildings complaint, read from the run's own rows: the claim in the first draft of this section was wrong (measured 2026-11-05)

The report was "he still can't enter and quit buildings freely, and it turned off by
itself", with a run attached. The first draft of this section said the complaint was a
`map_id` blindness in `planner.py` -- that replaying the session's observation produced
`nav.goto {map_id: <the map the character is standing on>}` four times out of four, and
that the bot was therefore "a bot that cannot move". **That is refuted by the rows of
the run it describes**, and the refutation is simple: 0 of the 57 `nav.goto` rows in
that session asked for the map its own pre-action observation named.

The run's shape, from `trajectories/dofus-01.jsonl` (2,208 rows, 1,052 steps, 161 of
them on 2026-11-04/05: `nav.goto` 57, `quest.advance` 52, `safe.stop` 27, `fight.join`
10, `tap.interactive` 8, `state.snapshot` 5, `ui.tap` 2 -- **every one of the 161 with
`ok: true`**):

* **Crossings work, in both directions, on the last cycle.** 04:33:55 asked
  `nav.goto {direction: left}` from 145490435 and the next observation put the character
  on 145489923 -- the building was left. 04:33:59 and 04:34:06 asked
  `nav.goto {map_id: 145490435}` *from* 145489923 -- a return trip, not a self-travel --
  and by 04:34:52 the character was back on 145490435. Over the two days, 37 of the 47
  asks that named a map id landed on it, and 7 of the 10 that named a direction crossed.
* **Replaying those eight scenes through today's `QuestPlanner` finds no self-travel
  either.** Five return `tap.interactive {element_id: 513196, skill_uid: 136257451}` --
  the interaction the objective needed -- one returns a read, and the two that return
  `nav.goto {map_id: 145490435}` are the ones taken from 145489923.

**What the blindness actually was, and what the accessor is worth.** The 16
`obs.get("map_id")` reads in `planner.py` did go blind when the slim live view landed
(#83), and `map_id_of` + the flat/slim parity checks close that class. But the run
already carried the cure: `knowledge_context._lift_position` fills the flat `map_id`
from `position.map_id` (and `map.map_id`) inside `enrich_observation`, which every
observation passes through before the policy sees it -- and the logged row at 04:33:47
does have a top-level `map_id`. Measured, not argued: over 419 objective-bearing
captures in `data/replay/planner_obs.jsonl`, replacing the accessor with the old
top-level read changes **nothing at all** through `choose()` -- no-call 0 vs 0,
travel-to-own-map 0 vs 0, and an identical tool distribution (270 `quest.advance`,
109 `tap.interactive`, 19 `state.snapshot`, 18 `shop.buy`, 2 `nav.goto`, 1 `ui.tap`).
The defect is real on the `plan_objective` path (withhold `position.map_id` and the
objective *"Give a report to Great Jarl Ordyn."* plans a trip to the map it is already
on, 4/4 -- the suite now carries that witness check), but `choose()` reaches the
ready-objective path first, so on the shipped pipeline it was not the cause of this
session. That is the honest scope, and it is narrower than what the first draft said.

**The second finding stands, and so does the fix.** House map 145490432 publishes no
exits and no neighbours live while `data/maps/_index.json` lists four (top 145490689,
bottom 145490433, left 145489920, right 145490944), and the planner answered a spent
border with a `state.snapshot` read that the game serves forever. `_indexed_crossing_ask`
now asks each crossing the index knows -- within the same border budget, and only while
the client is silent about its edges, so a map that lists its exits keeps authority. The
live rows show why the budget needed bounding too: **145489920 was asked 19 times and
145490945 18 times in two days.** That rule came with five `house map:` arms (one
failing 55 checks) and two older arms re-anchored after the rewrite invalidated them, and
the gate then stood at **2064 checks, 0 failed** -- the guard is what makes the
difference, and it was red in the first draft: asking the index unconditionally
pre-empted `route_first_hop`, the simulator teacher completed 0 quests in 400 steps, and
three suites said so before it shipped.

**"It turned off by itself" was answerable, but only from a volatile endpoint.** The
brain daemon (pid 6280, `loaded_s` 46,180 s = 12.8 h) still held that session's summary
in `/brain/status`: `steps 11, waits 2, verdicts {advanced 7, blocked 4}, promises
{na 4, met 2, unmet 1}, reason "goals exhausted"`, and the blocked row's note --
*"quest mode: nothing plannable -- 1 ready objective(s), 1 active quest(s), **90
retired**, tutorial=raw, map=145490435"*. So the run ended because the planner retired
the one active quest and had nothing left to plan, eleven steps in. Nothing in the log
says that: 27 `safe.stop` rows across two days, 7 of them taken on an observation with
no position at all (`in_world` false, `hp 0/0`), and not one reason. That gap is #105,
and it is now closed -- see V.19. Reading the daemon's memory was the only way to get
it, and that state dies with the process.

Still open, and now better specified: the retirement itself (why did `_plan_quest` give
up on quest 4262 with a ready objective on the current map, and what are the 90?), the
`npcs[].name: null` id-space mismatch (`parameter0: 727568` against `npc_id` 3920/3934,
so "is the quest NPC here?" cannot be answered by name), and a first-time door with no
index edge at all. All three are in #104 / #107 with the measurements.

### V.19 Two rules written by hand, both witnessed: an in-flight crossing, and an end that says why (landed 2026-11-05)

The user's proposal was "add a layer for thinking -- for intelligence we can write the
weights directly". These are hand-written rules, no training, each one derived from a
row in the run rather than from a guess.

**1. A crossing is a journey, and re-asking it cancels it.** The settle gave `nav.goto`
a 6 s window; when the border had not changed by then the loop decided again, from an
observation that still showed the old map, and decided to ask the same border. Each new
ask re-paths the client's walk from wherever the character stands. The evidence is in
the spacing: border 145489920 was asked five times at ~6.5 s intervals and the crossing
landed 30 s after the first ask -- during the fifth; border 145490945 was asked ten
times in 54 s while the character paced cell 299 -> 537 (the exit) -> 299 and never
crossed. From outside the window that is exactly "cannot quit the building".

`agent/autopilot.py::settle_deadline(tool, started, last_change, window, cap_s)` is the
rule, kept as pure arithmetic on monotonic numbers so it is checkable without burning
wall clock: while the world keeps changing, a crossing's deadline slides forward
(`last_change + window`), capped at `started + CROSSING_IN_FLIGHT_S` (30 s, the measured
landing). A border that really refuses now costs two bounded waits and the crossing
bound retires it, instead of ten asks that interrupt each other. Two loop arms on the
*same* walk settle it: with a 0.4 s cap the promise is returned unfulfilled (5 polls,
still map 100); with a generous cap the walk is waited out and the landed border is
returned (map 200, later than the base window). Caught while writing it, and now pinned
by its own check: binding the cap as a default argument froze the constant, so patching
`CROSSING_IN_FLIGHT_S` did nothing -- that mutation fails 3 checks.

**2. A run that ends says why, on the log.** `_finish(reason)` is the seam the five
`_run` exits and the CLI interrupt path return through; it reports, then writes exactly
one `kind: "session_end"` row per pilot -- `reason`, `steps`, `waits`, `verdicts`,
`promises`, `retired`, `recoveries`, `dead_talks`, `declined_crossings`, `elapsed_s`,
`session`, `instance` -- and inherits the `brain_build` stamp through `_append_log_line`.
The next run will say in the file what today only the daemon's memory knew. `summary()`
was the obvious seam and had to be given back its purity: it is also called as a plain
report on partial objects (`_Shim`, `types.SimpleNamespace`) by two suites, and writing
from inside it broke both -- a 650-arm sweep preflight refused to mutate anything until
that was fixed. A needle check now holds the shape: `return self.summary(` must appear
zero times in `autopilot.py`.

Both together: **8 arms, 8 CAUGHT** (3-6 failing checks each), two new suites,
16 new checks. #105 closed, #106 closed. What remains blocked on the machine, not on
the code: the daemon that drove the reported run is still `code_stale` on 20 modules, so
none of #99/#100/#101/#105/#106 is in any live row yet -- `verdict`, `why`, `promise` and
`brain_build` are absent from all 161 rows of the run, which is how the run is dated
against the code. Restarting it is the user's call, because a client instance is
attached and a failed restart leaves the window brain-less.

### V.20 The buildings complaint, finally: my own bounce guard barred the objective's own map (measured and fixed 2026-11-05)

V.18 corrected what the run's rows did *not* show. The replay that found what it *did*
show was one line different from every replay before it: **the eight final scenes were
fed through one carried `QuestPlanner`**, the way the loop actually uses it, instead of a
fresh planner per scene. A fresh planner hid the defect completely.

The chain, scene by scene from `trajectories/dofus-01.jsonl`:

1. On 145490435, objective 13958 *"Give a report to Great Jarl Ordyn."* -- the character
   is already on the objective's map. The loop taps the interaction, talks to `npc_id`
   3920 and 3934, closes the dialog. The client still reports the same objective, so
   every option is now marked spent: both NPCs asked, the interactive tried.
2. Standing on the map the objective names, with nothing left to try there, the planner
   records the visit: `planner.py:2961` adds `(quest, objective, here)` to
   `_exhausted_at` -- "this visit found no call", the knowledge written 2026-10-29 to stop
   quests 4262/4264 walking the 145489920 <-> 145489921 edge for twenty steps. Traced, not
   inferred: instrumenting the set during the replay shows that exact line firing at
   04:33:55 and again at 04:34:52 with the triple `(4262, 13958, 145490435)`.
3. One crossing away (145489923), the same objective is still ready and still names
   145490435. The ready scan asks its question -- *has this objective already visited
   `need` and found nothing?* -- and the record says yes. **The trip back is barred by
   the last visit to the very map the objective happens on.**
4. `plan_objective` returns nothing, `_plan_quest` returns None, and the retire site adds
   quest 4262 to `_skipped`. The next scene says "nothing plannable -- 1 ready
   objective(s), 1 active quest(s), **90 retired**", `goals exhausted`, `safe.stop`.

So the bounce guard and the dead end were the same line of code, and for a one-map
objective the guard *is* the dead end: `_exhausted_at` is keyed on a map that means
"where I stood" when written by the travel paths and "where the objective must happen"
when read by the ready scan. The user's sentence -- "he still can't enter and quit
buildings freely" -- was literally true in one direction: **leaving worked, and once the
visit had been recorded, re-entering was barred for the rest of the session.** And "it
turned off by itself" was the retirement at step 4.

The rule written in its place (`agent/planner.py::_visit_is_exhausted`) separates the two
halves of the guard instead of choosing one of them: the **first** return to a spent
visited map is granted -- the mark is spent, not obeyed -- and if that visit finds
nothing either, the re-marked map is barred for good. The subtlety that decided the
implementation: **an ask is not a visit.** The first draft recorded the grant without
touching the mark, so the very next decision -- which arrives before the crossing has
landed, because a crossing is a 30 s walk (#106) -- read the still-present mark, refused,
and retired the quest one step later than before. Spending the mark models what actually
happened: the return is on its way, and only an arrival that finds nothing again re-marks
it. So a bounce can cost one extra lap per map and no more, while an objective with a
single possible map is never permanently unreachable. Grants are cleared wherever the
marks are cleared, so a spent grant cannot outlive the gate that spent it.

Measured on the same eight scenes with the same carried planner: retired quests **90 ->
0**, and every scene returns a call -- `nav.goto {map_id: 145490435}` for the two taken
from 145489923 (the return V.18's first draft had wrongly called a self-travel), the
interaction on the target map, `nav.goto {direction: right}` once it is back and the talk
is exhausted. Carried replay of all 17 corpus sessions (419 objective-bearing steps) gives
**0 no-calls and 0 retirements**, so the rule does not over-grant either; the caveat is
that those rows were collected one scene at a time, so carrying state across them is a
synthetic sequence -- it shows the rule does not over-bar, not a verdict on those sessions.
`test_a_return_to_the_objective_map_is_granted_once` holds it with 13 checks: the unmarked
case, the granted first return, the grant spending the mark, the second dead visit
barring, the non-integer key staying silent, both scene resets clearing grants, the
bounded-bounce arithmetic (12 alternations between two spent maps grant exactly **2**
laps, never 20), and the *same rule through its second consumer* -- the active-quest
funnel inside `plan_objective`, which no other suite reaches.

Three arms came back MISSED on the first pass and each had a different cause, all three
instrumental rather than incidental: the funnel call site had no witness at all until the
two `plan_objective` checks above were added; the new-scripted-step reset branch was not
the branch the first reset check exercised (it took the scenario-over branch); and
`test_fork_carries_the_planner_state` -- which is what fails when a fork forgets the new
set -- **was not in the sweep's snippet list**, so nothing that reads `fork()` ran. That
is the fourth time the snippet's own coverage has been the weak link, and it is now in
the list. Final tally for this rule: **9 arms, 9 CAUGHT**, and the whole gate at
**2,095 checks, 0 failed** (the sweep table itself is now 660 mutations).

The house mechanism, assembled from the four hand-written rules now in the tree: a
learned door cell is stepped on rather than re-asked (`planner.py:1595`, existing), a
map that hides its edges is left through the crossings the index knows (#100/#104), a
crossing already being walked is waited out instead of re-asked (#106), and a visited
map that was found spent grants exactly one return before it is barred (#108).

**The daemon restart, and the first row the new code wrote on real wiring.** The stale
daemon (pid 6280, `loaded_s` 46,180 s = 12.8 h, `code_stale` naming 20 modules) was killed
and relaunched with the host's own arguments (`python -X utf8 run_brain.py --port 8895
--brain --brain-log-dir ... --recorder-dir .../trajectories`, working directory
`dofus-network`) -- which is what `desktop/aipanel.cs` does itself when `BootState`
returns BrainRestart, so the restart is the supported path, not a hand-rolled one. The new
daemon reports `code_stale: []`, and the first run started against it wrote:

```
{"kind": "session_end", "session": "2d7ab0e1", "instance": "dofus-01",
 "reason": "time budget reached", "steps": 0, "waits": 0, "verdicts": {},
 "promises": {}, "retired": 0, "recoveries": 0, "dead_talks": 0,
 "declined_crossings": 0, "elapsed_s": 0.0, "brain_build": "brain-d64b0dea9138"}
```

That is #105 proven on live wiring: an end that names its reason, stamped with the code
that wrote it, in the file rather than on a console. `steps: 0` is the honest half -- the
run had no client streaming observations (the game window was not attached), so it waited
out its budget without dispatching. **The crossing rules (#106, #108) are therefore still
demonstrated only offline**: by the replay of the user's own scenes and by the corpus, not
by a live walk through a door. The next session with the window open closes #97 and the
live half of #101.

A second observation from the same restart, resolved rather than left open: `/brain/start`
answered with a `last_step` belonging to the *previous* session (index 7, `blocked`,
"policy returned no call"). It is not inherited state -- `BrainInstance.status()` reads
that field by tailing the log (`brain_daemon.py::_last_step_line`), and the only thing
that persists across processes is the controller's own `stats()` plus its strategy table.
So the retirement set is process-local: a run cannot inherit "90 retired" from the past,
and the number was earned inside those eleven steps.

The lesson, because it is the second time this session: **a replay that rebuilds the
component per scene cannot see state-dependent defects at all.** Every earlier
counterfactual in V.17 and V.18 -- including the one that "proved" the accessor changed
nothing -- ran a fresh planner, and the bug lived in the memory between scenes. Fixing
the class means the replay harness carries state the way the loop does, which is the
shape #103's re-collection should take.

### V.21 The retirement that ended the session: one unplannable pass was a verdict (landed 2026-11-05)

Same replay, one step further. With V.20's bar lifted the run no longer retires
anything, but the trace of the pre-fix behaviour was worth keeping: tracing every
`_skipped.add(quest_id)` with the old always-bar semantics restored showed quest 4262
being executed at `agent/planner.py:3932` -- the line under *"the objective's type
translates to nothing we can do"* -- not at the `_plan_quest` site the first draft of
V.20 blamed. The two sites that retire the **active** quest retired on the *first* empty
plan, and one empty plan is a routine occurrence: the crossing is still being walked
(#106), the dialog page has not arrived, or the granted return was just spent.

The rule, again written by hand rather than learned: `_plan_none_reaches_limit(quest_id,
situation)` counts consecutive unplannable passes keyed on the **objective id and the
map**, and only at `ACTIVE_PLAN_NONE_LIMIT` (3) does it call the retirement a verdict --
which then clears the count, so a quest reopened by `_reopen` starts clean. A pass that
does plan calls `_refund_plan_none`, because the count measures a *streak*, not a
lifetime. `fork()` and `_reopen()` both carry and clear it, so a counterfactual cannot
invent a retirement and a new scripted step cannot inherit an old debt.

Witnessed on the live-shaped scene (standing on the objective's map with both NPCs asked
and the interactive tried, which is exactly the state the run reached): the quest is
still alive after one empty pass, still alive after two, and retired when the same
situation empties for the third time -- and while it is alive the plan is allowed to go
looking instead (`nav.goto {direction: left}`), which is the behaviour the user should
see: an agent that tries something else rather than quitting. Nine checks, five arms.

This does not end the retirement machinery -- a quest whose step types this build cannot
express is still given up on, correctly, by the `_plannable` scan (that is where the
other 89 came from). It ends the *one-step* execution of a quest that was being worked,
which was the difference between a stalled session and an ended one.

**And the next run reports whether the crossing rules did anything at all.** A log where
nothing went wrong looks the same as a log where the walk was waited out six times, so
`summary()` and the `session_end` row now carry `crossings_waited_out` (how many times
#106's rule kept the loop patient) and `returns_granted` (how many maps have spent their
one granted return under #108). Counting the extension rather than the ask matters: a
crossing that is waited out produces *fewer* rows, and fewer rows is otherwise
indistinguishable from a session that never had to cross anything.

Still open: *why* the server never advanced objective 13958 after the two talks and the
close (the promise half of #96), and the id-space mismatch that makes "is the quest NPC
actually here?" unanswerable -- `npcs[].name` arrives `null` while the objective names
`parameter0: 727568` and the map carries `npc_id` 3920/3934. Both are in #104; the
return grant is what stops them from ending the session.

## W. The action weights are not the ceiling; the value read is (measured 2026-11-05)

> **Audit correction — supersedes the conclusions below.** W.10–W.11 do not establish a
> pre-fight ceiling or safe live gate. `strike.py` multiplies the player's attack across all enemy
> slots, including padding, and omits power/cast limits; the stepped benchmark broadcasts row-zero
> HP and does not activate each side independently. The 0.8071 score correlation may be real, but
> the physical interpretation and 0.7479 comparator are invalid. Final `round` is not elapsed
> fight time and excludes terminal increments: 92.71/111.48 are counter ratios, not throughput.
> Keeping all wins is not a throughput upper bound; the “80% information absent” inference is
> withdrawn. Closed-form ridge is supervised fitting, not direct LLM weight programming. The
> limited AUCs do not certify pre-fight refusal, plan ranking or live positioning.
>
> `preflight.play` applied the policies to opposite sides on monster-opening boards. It now routes
> by `acting_team`; estimates independently initialize both teams' turns; readiness prioritizes the
> worst verified outcome and cannot label a verified loss/draw favourable. New regression fixtures
> failed before these fixes. Historical preflight outcomes must be regenerated. `env.dist` changes
> only for the moving actor and is not pairwise geometry, so the distance correlation cannot license
> “stand far” advice in the client. #139 is reopened; the old artifacts are not promoted.

The user's diagnosis was "the weights are nearly perfect but there are problems in the layers", and
their stated view of WSP was not "train a smaller model to write weights" but "read a trained
network, find the coordinates that do nothing, tell meaningless noise from a defect, and fix it by
writing directly". Both halves were built and run: `tools/wsp_noise_audit.py` (dead-weight census)
and `tools/wsp_lose_predictor.py` (is losing predictable at all) and `tools/wsp_program_value.py`
(write the head that is not at its ceiling). The answer is that the diagnosis was right, the
locus was not a layer, and this is the first edit in the whole line that clears its own gate by
more than the noise floor.

**W.1 There is no meaningless weight on the action head, on 13,002 real boss decisions.**
`wsp_noise_audit --cache wsp_raw_spell_merged.npz --head spell`: 0 inert columns, 0
out-of-contention columns, 0 columns never chosen, and **0 of 8** 64-coordinate blocks with no
effect (block flips 759-1,644 decisions each). Every legal action column is chosen somewhere, and
zeroing any 64 coordinates moves hundreds of decisions. The noise-removal use of WSP has nothing
to remove here. The move head (975 decisions) is different in kind: all three columns are load
bearing, but column 2 (*hold*) is chosen on 4 of 975 boards while its **sign flip changes 929** --
a strongly signed suppressor, which is #63's move collapse seen from the weights. That is the case
the audit's `reading` field names: writing to it is a scale change with a large blast radius, not
a repair. `tools/test_wsp_noise_audit.py` pins the distinction with a diagonal fixture -- an
inert-but-contiguous column must show `weights_inert` and *not* `out_of_contention`, a clamped one
must show both -- because conflating noise with clamp is how an audit like this misleads.

**W.2 The representation is narrow, but it is narrow because the corpus is narrow.** The cached
trunk vector (96 coordinates) has participation ratio **6.24** and 78% of its variance in 10
directions, and the *inputs* have 5.09. So the trunk is not collapsing structure that was wide; it
inherits the width from the observation. The truncation curve (`tools/wsp_rank_curve.py`, split by
corpus row so no fight is on both sides) says the small-variance directions are not junk either:
a closed-form readout climbs monotonically with rank, 0.2039 at k=4 to 0.2259 at k=96. Held out,
the incumbent head scores **0.2654** against the best linear readout of its own vector at
**0.2259** and the per-decision oracle at **0.2773** (random-legal floor -0.1234). Read together:
the head is a better reader of that vector than anything we can solve on it, and the entire
one-decision budget is +0.0119.

**W.3 Losing *is* predictable -- better than the shipped value head knows.** Same cache, same
labels, no training: fit a readout and score AUC against the terminal outcome of the fight played
from that decision. A plain read of the material (`mean ally hp_ratio - mean enemy hp_ratio`, both
from the cached observation tokens) gives **0.7986**. The network's own `value` head -- the thing
`combat/plan_value.rank_plans` already trusts -- gives **0.7816**, i.e. worse than subtracting
health bars. A closed-form linear readout of the actor vector in the same forward pass gives
**0.9009**. The information that a fight is lost is in the trunk; the head does not report it.
The tool also refuses to run unless `model.forward` on the cached observation reproduces the cache
own head logits (max |delta| 2.2e-05), so the value arm cannot quietly be a different network.

**W.4 The write: solve `value2`, and the discrimination moves +0.117.** `value2` is a linear read
of one activation, so it is exactly the kind of head WSP programs without training. Fitted on 9,735
train decisions against +/-1 terminal outcomes and scored on 3,267 held out: **AUC 0.7816 ->
0.8986** (+0.1170), and Brier on the probability scale **0.2293 -> 0.1188** against the constant
base-rate predictor's 0.1950 -- the trained head was *worse than predicting the base rate every
time*, the solved one is clearly better than it. `tools/wsp_program_value.py` refuses to write if
held-out AUC does not improve (so the gate that stopped every action-head edit is the same gate),
and after writing it re-reads the artifact and asserts two things: the artifact's value matches the
solved scores (8e-08) and the parameters that differ from the source are **exactly `value2`**. The
artifact is `dofus-evidence/encounter_value_wsp.pkl`, sha256 `4de9d8f8...4eca2e`.

**W.5 To get there, the model's trunk had to stop being copied.** `wsp_program/trunk_actors()` had
carried a hand copy of `model.forward`'s trunk with the comment "replicated rather than refactored
so the shipped model file stays untouched". That is the wrong trade: a copy has no witness, and the
day `forward` changes, the caches describe a network that no longer exists. `model.forward` is now
`trunk()` + the heads + `value_readout()`, one implementation each, and `value_readout` returns the
activation `value2` reads so an edit can be solved against the tensor the shipped path actually
sees. The refactor is behaviour-preserving where it matters: re-running the published baseline
(`wsp_fight base --harness pvm --data encounters_boss_eval_s4_2048.npz --action-selection greedy`)
gives **464 wins / 54 draws / 1,530 losses = 0.2266**, identical to the number in section N.11.10.

**W.6 The other half: what a simulator is worth, and what it costs.** `tools/wsp_search_value.py`
plays the same boards twice with the same weights -- greedy argmax head vs enumerating every legal
spell, stepping it, rolling it to a terminal result under the fixed greedy continuation and keeping
the candidate that wins. On 512 boss-eval boards: greedy **0.2188** [0.1895, 0.2578], search
**0.2441** [0.2109, 0.2852], paired delta **+0.0254** with CI **[+0.0117, +0.0391]**, and the flips
are 13 loss->win against **0** win->loss. So a 1-ply lookahead over the *same* network is worth
about as much as the turn-order fix (#88, +0.025) and roughly **8x** the best weight edit ever
measured on the action head. The decomposition matters more than the headline: the third arm, a
uniform draw over the legal columns, scores **0.1758**, i.e. **-0.0430** below greedy. Leaving the
head is harmful; the search's gain is the values (+0.0684 over random-legal), not the variety.
Cost, batch-amortised over 512 boards: 1.12 ms/decision for greedy, 11.57 ms for search (10.3x).
That is a *throughput* figure, not the latency of one live decision -- at 8 boards the same arm
reads 183 ms/decision, and that is the number a live call would pay.

**W.7 A one-step value backup loses -- and roughly half of the loss was my query, not the head.**
The cheap version of lookahead (skip the rollouts, read the trained `value` on the successor state)
was measured on the same 512 boards and lost heavily: `value1ply` **0.1172** (delta **-0.1016**, CI
[-0.1309, -0.0723], 60 wins turned into losses for 8 gained), and the arm backed by the *solved*
head from W.4 did worse still, **0.0957** (-0.1230). Mean rounds rises from 2.51 to 3.34/3.67: the
value-argmax prefers positions it scores high and the fight survives longer in. That is what a
bootstrapped self-play value does when asked to choose instead of to report, and it is not a cost
story -- the arm costs 2.9-3.6 ms/decision against the rollout search's 10.3.

Before accepting the sentence above I tested the one confound that could make it my fault rather
than the network's: the head is only ever trained on states where the side whose value it reports
is the side to act, and after a cast it is the monsters' turn, so the bare successor read is
off-distribution by construction. `--arms value1ply_after_enemy` plays the monsters' reply and then
reads. It recovers most of the gap: **-0.0586** [-0.084, -0.0293] for the trained head and
**-0.0664** [-0.0898, -0.0449] for the solved one, i.e. 42% and 46% of the earlier damage was the
query. Both arms remain clearly below greedy, so the surviving conclusion is narrower than the
first draft of this paragraph: **the value head is not a one-step action backup**, and the
difference between the trained and the solved head as backups (0.0215 at the bare read) shrinks to
0.0079 once the query is right, which at n=512 is not a difference -- so "making it a better
evaluator makes it a worse selector" was mostly the off-distribution read talking, and I retract it
as a claim about the edit.

**W.8 The trap that produced the first version of that number, and what it cost to see it.** The
first five-arm run returned **exactly** the same 0.0957, the same 65 flips and the same mean rounds
for `value1ply` and `value1ply_edited`, which is not a null result: `one_ply_value` read one params
dict for both arms, so the "comparison" was one arm computed twice -- the same class as the
un-threaded `--checkpoint` in N.11. The fix is a wiring witness rather than a carefuler read: every
arm now returns a hash of the *sequence of columns it played*, the tool refuses to print a report if
two arms share a signature, and `--arms` plus a `value_source(arm)` function make the two heads
separate call sites. Comparing the choice sequence rather than the outcome was itself a second
finding: an earlier version asserted on differing terminal winners and false-alarmed at 8 boards,
where two genuinely different policies can win and lose the same small set of fights.

**W.9 What this settles for the plan.** The lookahead that pays is *rolled out in the simulator*
(+0.025, 13 losses turned, none lost); no value-argmax arm, read any way I could query it, beat the
plain head (best: -0.0586). The value head therefore belongs on the *assessment* side of the
architecture -- pre-fight readiness, "am I losing", refusing a board -- where the solved readout now
clears both the trained head (0.8986 against 0.7816) and the health-bar floor (0.7986). Making it a
usable action backup needs successor-state labels, which this corpus does not contain: every cached
outcome is the result of playing *one* decision and then the greedy continuation, never the value of
a chosen next state under a chosen policy. `plan_value.rank_plans` already reads `value`, so it is
served better by the written head than by the trained one; nothing in the live loop should start
argmaxing over it.

Open in this line: #138 (what the head is for, decided above but not yet wired), #131 (use the
assessment head to refuse before the fight), #135 (pay only for drops actually collected).

**W.10 Before the fight the network is blind, and one line of arithmetic is not.**
`tools/wsp_refuse_rule.py` scores a signal computed at the *opening* board against the terminal
result of the same board, rolling the fight out with the shipped `make_eval_rollout` (greedy
selection, scripted opponent) -- the same instrument that publishes 0.2266, which this run
reproduced exactly on 2,048 boards. Arms are ranked by tie-corrected Mann-Whitney AUC and each one
also gets a refusal curve: accept the top q by score, then report the win rate among accepted, the
share of losses avoided and the share of wins thrown away.

    shipped_rule / strike_economy  AUC 0.8071  [0.7881, 0.8320]
    stepped_estimate (preflight)   AUC 0.7479  [0.7222, 0.7744]
    hp_material                    AUC 0.7211  [0.6945, 0.7443]
    value_solved  (W.4's artifact) AUC 0.6822  [0.6487, 0.7071]
    first_to_act  (initiative)     AUC 0.5993  [0.5751, 0.6200]
    value_trained  (shipped head)  AUC 0.5439  [0.5128, 0.5743]
    random                         AUC 0.4780  [0.4475, 0.5099]

The trained value head is **indistinguishable from noise before the first cast** (0.5439 against a
random arm at 0.4780), and even the WSP-solved head, which reads terminal outcomes at 0.8986
mid-fight, only reaches 0.6822 here -- correctly, because an opening board has no damage on it to
read. What does work is the arithmetic a player does in their head: `floor(AP / cost) * damage` per
spell, best slot per actor, through the target's resistance to the element actually cast, compared
as two clocks -- rounds to kill against rounds to die. It beats the health difference, the
initiative floor and both value heads, and the tool refuses to publish anything unless the shipped
implementation and its own copy agree to 0.0 (`shipped_vs_tool_score_max_abs_delta`).

Why it also beats the version measured by *stepping the environment* (0.7479) is the part worth
keeping: the stepped quantity scores the spell the current head happens to choose, so it forecasts
the policy's present competence, while the tables measure what the fight contains. For a decision
taken before the fight, where the policy cannot be retroactively improved, the ceiling is the right
quantity and the floor is the wrong one.

**The rule is shipped with no fitted constant.** `combat/encounter/strike.py` implements the race,
and `pre_fight()` declines when the arithmetic says you die first -- `rounds_to_die <
rounds_to_kill`, no threshold tuned on this corpus, which is what `hp_over_opening_damage < 3.0`
never had. On the 2,048 boards it accepts **779** and refuses **1,269**, and the separation is
large: it wins **0.4339** of what it accepts and **0.0993** of what it refuses, avoiding 1,143
losses at a cost of 126 of the 464 wins the unrestricted policy would have taken. The throughput
check is the one that decides whether to believe any of it, because a higher win rate bought by
refusing short hopeless fights is nothing: accepted fights are *longer* (mean 3.89 rounds against
2.44 overall) and still pay more, **92.71 wins per 1,000 rounds unrestricted against 111.48 under
the rule, +20.2%**. Rounds are the simulator's clock, so that is a proxy for time-on-task, not a
measured kamas/hour -- the corpus has no kamas values (§N.13).

`combat/test_strike_economy.py` pins ten arms so no quantity in it can be silently zeroed: one
spell's damage must flip the verdict, 50% resistance on the element actually cast must halve my
output *and* change the verdict, a dead enemy must stop contributing its damage, an unaffordable
spell must count as no damage at all and produce `unknown` rather than a winner, and partial AP
must buy whole casts only. Three more arms pin the wiring in `preflight._verdict`: losing the race
is `not_ready`, winning it is `favourable`, an unmeasurable race is **not** favourable, and the
ambush branch still returns `refuse` however good the race looks.

Two consequences, both uncomfortable and both stated rather than smoothed. (1) The pre-fight gate
is arithmetic, not the network, so the WSP-solved head's real job is mid-fight assessment and
plan ranking -- it is not the thing that stops the bot walking into a loss, and #138 is left open
on that reading. (2) `preflight.readiness()` layers the ambush check on top of the race, and on
this corpus 73.97% of boards open on the monsters; run at the dungeon's own optimal level it still
returns `refuse` with "verified outcome: loss in 0 rounds". A gate that refuses everything is not a
planner, so the acceptance rate of the *combined* verdict has to be measured on the dungeon join
before it is wired into the live loop -- the race alone is measured (38% accepted), the composition
is not.

**W.11 How much of a pre-fight forecast is reachable: about a fifth, and the ceiling says so.**
W.10's rule was the first gate; this pass added the two things needed to judge it. First, a
*ceiling*: `perfect_foresight` refuses exactly the boards that end in a loss, so no pre-fight rule
can beat it, and every other row is read as a fraction of its headroom. Second, a *solved* gate: a
ridge readout on 14 features of the opening board -- the two clocks, the damage totals, group size,
boss count, both levels, initiative, distance, resistance -- fitted on the first half of the corpus
rows and scored on the second, with the untuned arithmetic reported on the same held-out half so
the comparison is not tilted by the fit's own data.

Throughput on the held-out half, wins per 1,000 rounds of fight time, at the arithmetic rule's own
acceptance share (0.3682):

    accept everything       96.30    win rate 0.2363
    untuned arithmetic     112.60    win rate 0.4668
    solved readout         120.54    win rate 0.5464   (held-out AUC 0.8956, in-sample 0.8810)
    perfect foresight      218.02    win rate 1.0000   (accepts 0.2363 of boards)

So the untuned rule captures 13.4% of the reachable improvement and the fitted one 20.0%, against a
ceiling that would accept only a quarter of all fights and win every one. The honest summary of the
whole pre-fight direction is that number, not the AUCs: **roughly four fifths of the gain available
from refusing fights is not present in the opening board at all.** The forecast is close to done;
the remaining headroom is capability and structure -- the +0.025 the rollout search buys (W.6), the
turn-semantics class #88 fixed, and what the env does not model yet (party, summons, boss states).

**The ambush layer is free and buys nothing, provably.** `preflight.readiness()` also refuses any
board whose opening monster turn kills before the character acts. On the 2,048 boards that newly
refuses 32 of them and gives up **zero** wins, because all 32 were already losses. Its effect on
throughput is exactly nil by construction, not by measurement: a refused board that ends in 0
rounds removes nothing from the round denominator and nothing from the win numerator, so
`wins_per_1000_rounds` is 92.71 with the ambush filter and 92.71 without it, while the win *rate*
rises from 0.2266 to 0.3165 -- the ratio of a rate to a mean rounds, invariant to deleting zeroes.
Keep it (it prevents pointless deaths at no cost) but do not claim it plans better. Acceptance
falls from 0.3804 (race alone) to 0.3647 (race and ambush), so the "will this gate refuse
everything?" worry from W.10 was unfounded: 36.5% of boards are still played, and the composed rule
wins 0.4525 of them.

**Only three features carry the forecast, and one of them is a variable the corpus drew at random.**
Refitting without each feature in turn and re-measuring held-out AUC: `rounds_to_die` -0.0533,
`distance` -0.0459, `incoming_damage` -0.0348, and every other term within +/-0.005 of nothing --
including `group_size` (-0.0001) and `boss_count` (+0.0003). Group size is not unimportant; it is
*redundant*, because the pooled damage clocks already contain it. That re-lands the section N.11
finding in a new place: what kills the solo player is the group's damage output and the opening it
gets, not the number of bodies.

`distance` deserves the sharper look, because it enters the fitted gate and not the arithmetic one,
and its standalone AUC is 0.6466 in the direction **further away wins more often**. That is the #88
reachability rule from the other side: a ranged character at distance 8 casts while the group spends
MP approaching. It is also a variable this corpus *sampled uniformly* at build time, so here it is
an independent cause of outcome variation rather than a consequence of play -- which means a live
planner can exploit it directly (stand far), and it also means the fitted gate's edge is partly an
edge the simulator hands to whoever positions well. The measurement is therefore about the game's
arithmetic, and only secondarily about the sampler.

`strike_score` is the one coefficient worth naming, because it inverts the naive reading: the
fitted weight is negative (-0.088) and dropping the feature *raises* held-out AUC by 0.0019. It is
`rounds_to_die - rounds_to_kill`, both of which are already in the model, so its weight is a
residual between features that contain it. Coefficients are not importances; the ablation table is,
and it was added to the tool for exactly that reason (`--ablations`).

**Artifacts.** `dofus-evidence/opening_gate_model.json` holds the 14 weights, the exact
standardisation they were solved against, the acceptance threshold, the split, and the digests of
the corpus and checkpoint used; the tool re-loads it, re-scores it and refuses to finish unless the
replayed model reproduces the held-out AUC it printed (0.8956 both ways). Nothing hardcodes today's
fit.

---

## X. Mechanics reach the network, and the two numbers that bound what weights can buy (measured 2026-11-05/06)

Section W ended by withdrawing claims. This one replaces them with measurements, and the headline is
not a gain: it is the size of the lever that action-head edits have at all.

### X.1 What the board was throwing away, counted from the client's own tables

`touch_source.slot()` returns `None` unless a spell level has a positive damage or heal line, so a
boss spell that only summons, dispels, pushes or buffs never reached the model. Counted over the
real kits (`dofus-evidence/mechanic_loss.py`):

| quantity | measured |
|---|---|
| boss/miniboss species | 519 |
| boss spell levels referenced | 1,775 |
| dropped by the damage-or-nothing filter | **949 (53.5%)** |
| of those, carrying a timed (over-N-rounds) effect | 617 |
| species losing at least one spell | **456 of 519** |
| species that put a state on the board | 108 |
| species that summon | 138 |

Mechanic kinds across boss species: invoke 137, push 126, state_add 106, ap_loss 65, pull 61,
ap_gain 57, mp_gain 56, swap 49, kill 46, state_remove 31, teleport 24, glyph 12, ap_steal 11,
cleanse 9.

[combat/encounter/mechanics.py](../combat/encounter/mechanics.py) types every one of the 700
published effect definitions from the client's own fields -- `category`, `bonusType`,
`characteristic` -- falling back to the description template only where those are silent, and
encodes a spell level into a 74-number record (kind multi-hot, which characteristic a modifier
moves, duration, delay, cast caps, cooldown, line-of-sight, line/diagonal, cells required, crit and
failure chance, and the raw target mask kept as an opaque bucket because the mask alphabet is the
client's private notation and I have no published decode for it). Re-measured after that change:
1,503 of 1,775 boss spell levels now carry at least one effect the environment simulates, against
826 before, and the remaining 272 are counted and labelled `unmodelled` rather than silently dropped.

```
python -m combat.encounter.mechanics                     # coverage over the real boss kits
python -m combat.encounter.mechanics --json
```

Typing on `category` alone was wrong the first time: it left 641 boss effect instances as `other`
because most characteristic modifiers carry `category == 0`. Using `characteristic` cut that to 286.

### X.2 The head could not pair a spell with a target -- measured, not argued

`model.forward` scores targets as `att[actors, actors] + t_bias[spell, actor]`, so across two spells
the target *ordering* differs only by a learned constant per actor slot. On the 2,048-board boss
evaluation corpus (`dofus-network/tools/wsp_expressivity.py structure`):

| | shipped head | mechanic-aware head |
|---|---|---|
| cross-spell gap equals `t_bias[s1] - t_bias[s2]` | **true, exactly** | no |
| standard deviation of that gap across boards | **0.000** | 0.477 |

This is a representability result, not a performance one: no weight vector and no amount of training
makes that head route a spell by what its target carries, because the quantity does not exist inside
it. It is pinned by `combat/test_mech_net.py` (6 tests), which also pins that a *solved* relational
matrix installs exactly -- `t_q = sqrt(hidden) M`, `t_k = I` reproduces `s^T M a` to float32. That
test failed first and was right to: `forward` divides by `sqrt(hidden)`, so the naive install wrote
a different function than the one solved. Same ordering, wrong magnitudes, unreproducible artifact.

### X.3 The vulnerability channel is read, and worth zero wins

The proposal on the table was a tool that hands the agent mob/boss resistances and mechanics. The
part of that the fight net can use is already wired in, so the question is usage, and usage was
measured by zeroing the five resistance columns of the observation while leaving the physics alone
(`dofus-network/tools/wsp_expressivity.py ablation`, shipped `models/encounter_selfplay.pkl`,
2,048 boards, 3 seeds):

| measure | value |
|---|---|
| win rate with resistances | 0.2266 |
| win rate with them zeroed | 0.2329 |
| paired delta per board | **-0.0063, CI [-0.0078, +0.0078]** |
| opening target choice that moves | 5.0% |
| opening spell choice that moves | 2.2% |
| opening move choice that moves | 1.4% |

So the channel is not inert -- decisions change when it is removed -- and using it buys nothing
measurable. Two consequences. A lookup that feeds *this* head the same facts cannot raise in-fight
wins, and the win-rate part of that proposal is a null until the decision-maker changes. The cost
comparison still favours data: the same facts as a derived table are **149 KB gzipped** for all
2,540 monsters (36 KB for the 519 bosses) against **33.7 MB resident** for the network that does not
profit from them.

### X.4 The ceiling on action-head edits, and the axis outside it

`tools/wsp_program_target.py` labels every legal candidate at every player decision on the scripted
reference line by applying it and playing the rest of the fight out in the environment, so the label
is a terminal outcome rather than a score. 256 real boss boards give 956 decision states and 3,213
labelled pairs; the shares below are over the **194 states held out from the solve**, since a
metric computed on the states the matrix was fitted to would not be a held-out number.

| share of the 194 held-out states | meaning |
|---|---|
| 77.3% | won whichever legal pair was picked |
| **3.1%** (6 states) | the pair chosen changes the terminal outcome |

At 64 boards the mixed share was **zero** (70 states, 62 winnable, none decided by the pair), which
is why the metric had to be restricted to mixed states at all -- pooling over pairs printed
`solved 1.000 vs chance 1.000` for a scorer that had ranked nothing.

That number is measured against a scripted continuation, which is a suspect choice: a weak player
after the candidate can lose anyway and make two candidates look equivalent. So the ruler now takes
`--continuation checkpoint` and re-labels with the trained shipped policy playing the rest of the
fight, with the reference line and the "the heuristic's own pair" reference moving with it. On an
8-board directional run the share of states that win *regardless of pair* fell from 0.773 to 0.467,
while the share where the pair decides stayed at 0.022 -- so the weak continuation was inflating
"already won", not hiding leverage, and the ceiling is not an artifact of it. The 128-board pair and
move ceilings under both continuations are what that paragraph should eventually quote; until they
land the 3.1% stands as measured under the scripted continuation, flagged as such.

That is the bound on this whole family of work: an action-head edit that picks the perfect pair
still cannot win fights the pair does not decide. It also explains the ledger without any new
mechanism -- the best action-head covariance write ever measured bought about +0.003, while
landing cast-from-new-position (#88) bought +0.025, because the walk changes *which pairs are legal
at all* rather than ordering a set that mostly does not matter. The move axis is therefore solved
and priced separately (`--moves all`, `decision_leverage.move_*`), with the pair axis held fixed for
comparison.

### X.5 The write was priced, refused, and the gate that first accepted it was wrong

`tools/wsp_program_target.py` was run on 256 development boards with the 2,048-board holdout corpus
untouched, twice: solved against a frozen random trunk (`--tokens random`) and against identity
feature tokens with the blocks zeroed (`--tokens identity`), so the second arm leaves the solved
matrix as the only thing the head can be doing.

| arm, held-out win rate | random tokens | identity tokens |
|---|---|---|
| shipped trained checkpoint | 0.2490 | 0.2441 |
| uniform-legal pair choice | 0.0723 | 0.0767 |
| same net, write removed | 0.0068 | 0.0615 |
| closed-form relational write | 0.0293 | 0.0156 |
| lowest-index target control | 0.0303 | 0.0142 |

**Neither write is shipped.** The first run reported `gate_write_accepted: true`, and it was wrong:
the rule compared the written net against "the same net with the write removed", which wins 0.68%,
and ignored uniform-legal choice at 7.2% and the trained checkpoint at 24.9%. Beating a straw man
is not an improvement, so acceptance now requires all three of: a paired gain over uniform-legal
choice on held-out wins, at least `--min-mixed` states where the choice decides the fight, and a
ranking that beats chance on those states. With that rule the identity run refuses and names itself:

```
"written network does not beat uniform-legal pair choice on held-out wins (-0.0610, ci [-0.0119, +0.0119])"
"only 6 states where the choice decides the fight, below the 30 needed to call a ranking difference real"
"pair ranking: solved 0.000 against chance 0.264 where the choice decides"
```

Three things follow, and they are the reason this section is a negative result rather than a gain.
A closed-form relational write does not rescue an untrained network: it lands an order of magnitude
below the trained checkpoint, which is a statement about the trunk, not about the matrix. The
mixed-state count at 256 boards is 6 of 956, so *any* claim about pair-ranking quality is
underpowered by construction at this label volume -- the ceiling in X.4 is what limits it, not the
solver. And the arms above share a confound: a freshly initialised `mech_net` also has a random move
head whose argmax is effectively a constant, so those win rates partly measure the walk and not the
write. `written_hmove` (the written pair ranking with the scripted walk) and `scripted` (no learned
policy at all) are now reported alongside it for exactly that reason.

The move axis is where the write paid. At 128 development boards, identity feature tokens, both
axes enumerated, scored on the untouched 2,048-board holdout:

| arm, held-out win rate | value |
|---|---|
| move-head write alone | **0.2661** |
| scripted policy, no learned policy at all | 0.2637 |
| trained shipped checkpoint | 0.2441 |
| uniform-legal pair choice | 0.0767 |
| write removed | 0.0615 |
| move write **and** pair write together | 0.0684 |
| pair-head write alone | 0.0112 |
| scripted walk + written pair ranking | 0.0635 |
| lowest-index target control | 0.0107 |

Paired against uniform-legal choice the move write is **+0.1894, CI [+0.1735, +0.2053]**. Leverage
on the two axes is comparable -- 14 of 413 held-out states decided by the pair, 15 of 413 by the
walk -- and only the walk produced a policy worth having.

**That reading needed the missing cell, and the cell needed the labels.** The arms above differ in
two factors at once -- pair head and walk -- so the honest design is a 2x2: pair head {random,
solved} x walk {random, solved, scripted rule}. Scoring it on labels from only 32 boards made the
solved walk look *worse* than the rule (0.1519 against 0.2500), which I recorded as a refutation. On
labels from 512 boards the same comparison flips sign: the solved walk beats the scripted walk with
the pair head held random (**+0.0151, CI [−0.0068, +0.0068]**), and beats the trained checkpoint
(**+0.0210, CI [−0.0102, +0.0102]**) -- the bar that matters, because `combat/live_policy.py:38`
binds exactly that artifact, `models/encounter_selfplay.pkl`, as the live actor. The 32-board reading
was an artifact of a solve fitted on ~1.3k rows at `lam=10`; the correction was to the estimator, not
to the fight.

| arm, 512 dev boards / 2,048 holdout | rate | paired comparison | delta, CI |
|---|---|---|---|
| solved walk, random pair head | 0.2651 | uniform-legal choice | +0.1885 [±0.0174] |
| | | trained checkpoint | **+0.0210 [±0.0102]** |
| | | scripted walk, same pair head | **+0.0151 [±0.0068]** |
| scripted walk, solved pair head | 0.1099 | scripted walk, random pair head | **−0.1401 [±0.0159]** |
| solved walk, solved pair head | 0.1260 | solved walk, scripted pair head | +0.0161 [±0.0069] |
| full scripted policy | 0.2637 | | |
| write removed entirely | 0.0615 | | |

The pair write is not merely useless, it is actively harmful: with the walk held at the scripted
rule, installing the solved pair matrix costs 14 points of win rate (0.2500 down to 0.1099). That is
myopia rather than a bug. The matrix is fitted to "this pair now, then the scripted player finishes
the fight", but the deployed policy consults the same matrix every turn, so the rule it plays is not
the rule that was solved. The same mismatch was seen from the other side when a 1-ply value backup
lost to greedy; arriving at it from the write side is the more interesting confirmation, and it says
a one-step outcome-fit is not a Q function.

The gate still refuses the write, for a reason worth keeping rather than rounding away: 23
move-decided held-out states is below the 30 required before a ranking difference separates from
chance -- even though the ordering on those 23 was perfect (solved 1.000 against chance 0.551) and
the terminal-win evidence is already far from zero. The bar goes up by labelling more boards, not by
lowering the number.

### X.6 The write that cleared the gate, and what it is worth

1,024 development boards: 42,057 candidate pairs labelled by playing each one out to a terminal
result, 4,059 decision states, 449 s of labelling. Leverage measured on the same data: the walk
decides 3.08% of states, the pair 2.76%, and 77.4% are won whichever candidate is taken.

| held-out win rate, 2,048 untouched boards | |
|---|---|
| solved move head | **0.2656** |
| scripted policy | 0.2637 |
| trained shipped checkpoint | 0.2441 |
| scripted walk, random pair head | 0.2500 |
| uniform-legal pair choice | 0.0767 |
| write removed | 0.0615 |
| solved pair head | 0.0044 |

| paired comparison, per board | delta | CI |
|---|---|---|
| solved walk − trained checkpoint | **+0.0215** | ±0.0102 |
| solved walk − scripted walk, pair head held random | **+0.0156** | ±0.0067 |
| solved walk − uniform-legal choice | +0.1890 | ±0.0174 |
| solved pair head − random pair head, walk held scripted | **−0.1802** | ±0.0172 |

The move ranking on the 43 held-out states where the walk decides the fight is solved **0.977**
against chance **0.605**, which is the mechanism claim the smaller sample could not support. Every
condition of the gate is now met independently: gain over a uniform baseline, gain over the live
checkpoint, enough decided states, and ranking better than chance on them.

**What was published.** `dofus-evidence/encounter_target_wsp.pkl`, 32,626 bytes, sha256 prefix
`e698529deb67e72d`, arm `move_written`: `W` is `(16, 3)` solved by ridge from the labelled pairs and
installed as the move head. The file carries its own proof -- it was read back from disk after
writing, and the record says `changed_leaf_groups: ["move"]`, exactly the one head this arm authors,
with `move_max_abs_error` **3.3e-07** against the solved matrix. Source checkpoint, development
corpus and holdout digests are inside the artifact, so the numbers above can be attributed to the
files that produced them.

**What was not done.** This is an evidence artifact outside the repository, not a promotion: it is
not in `dofus-network/models/`, not wired into `combat/live_policy.py`, and no fight has been played
with it on the live client. The improvement is measured in the simulator against the scripted
opponent, on boards generated from real Touch kits. And the pair-axis result stands as a warning
about the method: the same solver, on the axis where the labels say the choice matters about as much
(2.76% against 3.08%), produces a policy 18 points *worse* than leaving that head alone.


A 512-board rerun is pending because the power condition is real: 19 pair-decided and 15
move-decided held-out states cannot separate a ranking from chance, which is why the gate refuses
even though the win-rate evidence against a live baseline is already far from zero. The gate itself
was rewritten for this run: it compares the best arm with the checkpoint (or with the scripted
policy for the walk-substituted arm), names the arm it is quoting, applies the power check to the
axis that arm writes rather than to the pair axis always, and computes move-axis ranking quality the
same way as pair-axis quality. Labelled candidates are cached to npz keyed by seed, width, token
mode, move set, board count, continuation and corpus digest, so a re-analysis costs 50 seconds
instead of re-rolling 155 seconds of counterfactual fights -- and a cache from a mismatched run is
refused rather than reused.


`tools/test_wsp_program_target.py` (7 tests) pins the ruler that produced every number above: mixed
states are keyed by (board, step) rather than by step, an all-winnable state contributes no ranking
evidence, `chance` inside a mixed state is the winning share of its pairs and not one-over-pairs,
paired intervals shrink with boards rather than with seeds, and a width that does not divide by the
head count is refused instead of crashing inside an attention reshape.

### X.7 Both sides of a mechanic are now input, and what is still missing is not input

`dofus-evidence/probe_state_tables.py` asked the same proxied `/data/map` endpoint that produced the
2,540-monster corpus for 24 table names in both languages -- `States`, `StateTypes`, `Status`,
`Statuses`, `Buffs`, `MonsterTeams`, `MonsterGroups`, `MonsterAI`, `AI`, `Scripts`, `Characteristics`,
`Templates` and others. Every one answered **HTTP 400**. Scoped correctly: this endpoint, under these
names, publishes no state semantics and no monster team. That is not the claim that they are
unobtainable -- the client's asset bundle and the community databases are untried routes, and I have
made that overreach before in this project.

The player's side got typed anyway, because the encyclopedia records carry the same facts in prose
plus a characteristics block: all **330** class spells now encode into the same 79-number row as a
monster's kit, with damage 146, state 67, AP loss 43, summon 33, push 30, teleport 16, heal 15 and
**cleanse 6**. Six cleanses is the point -- the answer to a boss state is now visible to the network
on the player's side of the relation.

Two defects surfaced while doing it, and both were silently producing wrong data instead of failing:

  * `FEATURE_WIDTH` said 74 while `feature_row` emitted 79. The constant was hand-counted; it is now
    built from the same component counts the encoder uses, and both encoders' output length is
    checked against it.
  * `encode_official` read the top level of the spell record, where none of the fields live, so every
    one of the 330 spells encoded to zeros -- a feature tensor that would have looked real and said
    nothing. It now descends into `levels` and raises when a record yields no cost, no range and no
    recognised effect.

That changes the ordering conclusion this section started with. Expressiveness is no longer the
missing piece: what a boss casts and what a player can answer with are both encodable. What is
missing is that nothing in the environment applies or removes a state yet, so a routing decision
that would be correct against a real boss has no consequence to be correct about. The next build is
the smallest physics that creates that consequence, not another feature column.

### X.8 The consequence was built, and the three places it was being silently thrown away

`combat/encounter/env.py` now applies four things instead of describing them: an instant kill drops
HP to 0 whatever the bar says, a pushback changes the distance the next legality check reads, and a
timed AP/MP reduction is stored on the victim and bites at the victim's own turn-start refill, with
a round counter that expires it. Subtracting the AP at the cast would have been a no-op -- the
refill erases it a moment later -- which is why the arithmetic went into `ap_mod` and the refill
became `max(ap_max + ap_mod, 0)`. `combat/test_env_mechanics.py` (11 tests, both backends) pins each
rule against the opposite outcome with its column cleared, because a fixture that cannot fail proves
nothing.

Four defects surfaced while putting that data in front of a network. All four produced plausible
numbers rather than errors, which is the only kind worth writing down:

  * **the prose encoder described two casts at once.** `encode_official` looped over
    `normal_effects` *and* `critical_effects` and summed them. All 1,122 levels that carry a
    critical line word it differently -- "13 to 15" against "10 to 12", "Steals 20 Intelligence"
    against 10 -- so a 1-cell pushback was typed as 2 and every crit spell's damage doubled. The
    corpus generator reads normal-only, so two encoders of the same spell disagreed by a factor of
    two on the same client table.
  * **a multi-element spell was priced as the sum of its elements.** 24 Sram and Ecaflip levels
    publish equal rolls in Air, Earth and Fire; a cast hits with one of them, chosen by the build.
    Summing made a 4-7 spell cost 21 damage. Taking the strongest element -- what the generator
    already did -- brought the two encoders to **exactly the same damage on 1,980 of 1,980 spell
    levels**, which is the witness that they describe one cast and not two opinions.
  * **"steals" was typed as one mechanic.** The client writes "Steals 2 AP" and "Steals 10
    Intelligence" with the same verb. The old pattern keyed off the verb, so every characteristic
    steal became an AP drain -- a Feca spell that moves 40 Intelligence would have taken 1 AP from
    the boss's next turn. It now keys off the noun, and the amount comes from the digits before the
    "(N turns)" tail, so pushback values land in the plausible 1-9 cell range instead of 30 and 80.
  * **the generator could not run.** `build()` assigned the per-board player stats to the name
    `stats`, shadowing the caller's counters dict: the first truncated kit raised `KeyError`, and
    every counter read with `.get()` was written into the player-stats dict and lost. So
    `python -m combat.encounter.build_dataset` has been broken since `player_stats()` was
    extracted, and the `source_stats` block in every corpus meta predates that refactor.

The two loaders had the same shape of bug one layer up: `train.py` and `encounter/data.py` iterate
a hardcoded seven-name table list, so a corpus carrying the mechanic columns trains a policy that
can neither see a pushback nor be hurt by one. They now derive the table set from the file and
raise if a base column is missing -- 12 keys on the mechanic corpus, 7 on the old one, verified.

With those fixed, the boss corpora were regenerated from the client tables at the same seeds and
the same knobs as the published ones (2,048 boards, `--slots 4`, levels 20-60, `--boss-prob 0.5`).
The boards are the same fights: HP, distances, monster ids, initiative, damage and heal lines are
bit-identical to the previous corpora, and 57 of 40,960 spell slots changed -- the mechanic-only
spells the old `damage or heal` filter dropped. What the columns say:

| side | push slots | AP drain | MP drain | instant kill |
|---|---|---|---|---|
| player (seed 7 dev) | 1,574 | 0 | 263 | 0 |
| enemies (seed 7 dev) | 3 | 0 | 132 | 162 |

No class spell in the encyclopedia drains AP, so `spell_ap_delta` is empty on the player's side
because the client's prose does not say otherwise, not because the column was skipped.

The observation is the last contract. `observe` appends the five numbers to each spell token --
9 columns to 14, an actor vector of 49 to 69 -- and does so **only when the corpus carries them**,
so the shipped checkpoint and the live path keep reading the 49-number board they were trained on.
A 69-wide corpus cannot be scored with the 49-wide checkpoint, and the pricing tool now says so and
switches its live comparator to the scripted walk, recording which one it used, instead of
measuring against nothing. `table_keys`/`spell_features` are derived from the data, never passed in
by a caller who might be wrong.

### X.9 The kill was a damage number, and the boss still barely uses its mechanics

`dofus-evidence/mechanic_ablation_env.py` is the probe that can fail: the same 1,024 real holdout
boards, both sides scripted with the shipped `train.make_baseline_rollout`, with the five mechanic
columns (a) present, (b) zeroed on the player, (c) zeroed on the enemies. If zeroing a column changes
neither the winner nor the final HP, that column reaches the observation and nothing else.

| ablated | boards, final HP changed | outcomes flipped | win rate ablated | with mechanics |
|---|---|---|---|---|
| player side | 50 of 1,024 | 24 (18 wins→losses, 5 losses→wins) | 0.2676 | **0.2803** |
| enemy side | **0** of 1,024 | 0 | 0.2803 | 0.2803 |

*Scope of the enemy row, added when the instrument was corrected:* these arms sliced actor 0 as "the
player" and actors 1.. as "the enemy", which is right for 1v4 and wrong for a party -- on a 4v4 board
it put three allies inside the enemy arm. Re-measured by **team** on 512 party boards, zeroing the
monsters' mechanics moves **5 boards' final HP and 1 outcome** (win rate 0.9082 with them, 0.9063
without) while the players' side moves 49 and 4. So the honest statement is not "a boss expresses
nothing", it is "a boss expresses almost nothing *that changes a 1v4 outcome*", and the party boards
where it does change something are already won (0.908), which is what hides it.
| both | 50 | 24 | 0.2676 | 0.2803 |

So the player's mechanics are real -- `+0.0127` of terminal win rate for a policy that cannot even
see them -- and the boss's are not. The reason was in the data, not the head:

    kill rows in the client's monster kits: damage [0.0]  rmax [1]  ap [3, 4, 16]

`touch_source.slot()` had written effect 141 ("Tue la cible") into the **damage** line as a `9999`
sentinel, from before the environment could express removal. Two consequences, both measured: the
fight's kill branch was unreachable because the 9,999 damage killed anyway -- which is exactly why
the first ablation run reported 0 changed boards on the enemy side and looked like a dead instrument.
Counted in the corpus that carried it: **162 slots**, all enemy, against a largest genuine damage
line of **2,542** -- 3.9x. The feature the network actually reads is the log-scaled one, so
`log1p(9999)/log(301) = 1.614` versus `1.374` for the hardest real hit: 17% apart in the input, 3.9x
apart in what the spell does to the board. Compression hid the sentinel from the head while the
fight treated it as an execution, which is the gap between a typed mechanic and a simulated one.

Removing it did put the mechanic into the opponent's hands: `combat/encounter/heuristic.py` ranked
spells by damage alone, so a 0-damage kill was never chosen, and it now prices a removal above any
damage the same cast could do. Traced step by step over the same 48 turns, that changes the
monster's chosen spell or target in 23 states. The outcomes still do not move, for a reason that is
about the model of the fight rather than the weights:

  * the kill is legal at **range 1 to 1** -- every one of the 140 kill slots in the holdout corpus
    (counted, not assumed) -- and the 1v4 boards place monsters at distance 1-12 with no map
    geometry, so adjacency is rare;
  * 24 of those 140 slots (17.1%) cost 16 AP against a monster AP bar capped at 12, so they are
    uncastable at any distance; the remaining 115 cost 3;
  * the enemy side carries 140 kill slots and 130 MP-drain slots, 1 push slot and **0 AP-drain**
    slots, because the boss kits in this table set express their threat through summons, glyphs and
    states -- 108 species add a state, 138 summon -- none of which the environment simulates yet.

That is the numeric answer to "make the model understand boss mechanics". The understanding channel
is built and measured live on the player's side; on the boss's side it is capped by how seldom a
monster gets to use a mechanic in this simulator, and the cap is a fidelity gap in the *monster
model* (no summons, no glyphs, no states, no adjacency), not a property of the action head. Building
the mechanic routing on top of it would fit a signal that is not there.

### X.10 The closed-form write re-priced on mechanic boards: same verdict, and the ceiling barely moved

`tools/wsp_program_target.py --dev encounters_boss_mech_s4_2048 --hold
encounters_boss_hold_mech_s4_2048 --boards 192 --holdout 512 --tokens identity --moves all` -- the
same knobs as the published mechanic-free run, on boards that carry the 14-number spell token.
7,998 counterfactually labelled candidates over 837 decision states, scored on 512 untouched boards,
3 seeds, paired per board. The live comparator is the scripted walk, because the shipped checkpoint
cannot read a 69-wide observation; the report records that.

| quantity | mechanic-free corpora | mechanic corpora |
|---|---|---|
| states where the pair decides the fight | 3.08% | **3.82%** (32 of 837) |
| states where the walk decides the fight | 2.76% | 2.39% (20 of 837) |
| pair ranking held out, solved vs chance | 0.579 vs 0.408 (19 states) | 0.579 vs 0.458 (19 states) |
| move ranking held out, solved vs chance | 0.977 vs 0.605 (43 states) | 1.000 vs 0.571 (**7 states**) |
| scripted win rate, holdout | 0.2637 (512 boards) | 0.2715 (512 boards) |
| solved move head | 0.2656, **+0.0156 ±0.0067** vs scripted -- measured on 2,048 boards, a wider holdout than this run's | 0.2852, **+0.0137 ±0.0245** (512 boards) |
| solved pair head | −0.1802 ±0.0172 with the walk fixed (2,048) | **−0.2500** vs scripted (512) |
| gate | move write accepted | **refused** |

The two columns are not a paired comparison: the left-hand figures come from the published run's
2,048-board holdout and 43 decided states, the right-hand from 512 boards and 7. Read them as the
same experiment re-run on different data, not as a difference of 0.0019 in the move write.

Three things follow, and none of them is what the mechanics thesis predicted.

  1. Putting a real pushback, a real MP steal and a real execution into the data and into the
     observation raised the pair-decided share from 3.08% to 3.82%. That is the whole of the effect
     on the ceiling: the axis where a spell-target choice matters is still a few percent of states,
     because the fights are decided by damage arithmetic that no routing choice changes.
  2. The pair write is *still* actively harmful, and now measurably so against a competent
     comparator: 0.0215 terminal wins against scripted 0.2715, `−0.2500` paired. A one-step outcome
     fit is not a Q function, and mechanics in the token do not make it one.
  3. The move write kept its sign (+0.0137) and lost its significance (±0.0245), because at this
     label budget only 7 held-out states are decided by the walk, against 43 in the powered
     mechanic-free run. The gate refused it for exactly that reason and said so -- `7 move-decided
     states held out, below the 60 needed`. That is a loss of power, not a reversal: 192 boards of
     labels is 5.7x the states of the accepted run's 32-board pilot but the held-out decided-states
     count is what binds, and it is 6x smaller.

So the honest status of "keep the network and optimise it with WSP" on the mechanic data is: the
accepted artifact stays the one written on the mechanic-free corpora (`move` head, sha256 prefix
`e698529deb67e72d`), re-pricing it on mechanic boards needs ~1,024 labelled boards to reach the same
number of decided states, and the pair head remains refused on both datasets. Nothing about the
mechanics layer changed that verdict, which is the point of measuring it rather than asserting it.

## XI. The party stops being an abstraction, and the ruler that was hiding it (measured 2026-11-06)

Everything in section X was measured on a board with one player actor. A real group is up to four,
and the project's own abstraction has always been `allies=1, enemies=4`. This section builds the
party and then measures the one quantity that decides whether coordination is worth building at all.

### XI.1 `allies` was a parameter nothing used

`build(...)` accepted `allies` and computed `n = allies + enemies`, but the generator filled **actor 0
only**: at `--allies 4` the corpus would have been four dead allies and a wrong team split, scored as
if it were a party. No corpus, test or tool in the tree ever set it above 1.

Fixed, with three decisions that matter more than the loop:

  * the extra allies are drawn **after** the monster block, not interleaved. Ally 0's draw sequence
    is therefore untouched, and the parity gate is real: rebuilding seed 7 at `--allies 1` produces
    a file whose every array is **bit-identical** to `encounters_boss_mech_s4_2048.npz`, with no key
    added and none dropped. An `allies=1` corpus is the same 2,048 fights, not a re-roll.
  * the group-size draw now renormalises over `min(max_enemies, enemies)` instead of `1..max_enemies`,
    so a party board cannot be handed 1-4 monsters while 4 enemy slots stay padded dead. For the
    published 1v4 and 1v6 shapes the draw set is identical, so this is not a re-pricing.
  * `first_team` compares the fastest monster against the **best roller in the party**, not against
    ally 0. Giving the other allies a constant initiative would have made every coordination gain an
    artifact of who acts first -- the exact mistake the strike-formula correction in section W made.

Party corpora: 2,048 boards at seeds 7 and 99, `--allies 4 --enemies 4 --slots 4`, levels 20-60,
`--boss-prob 0.5` → 8 actor slots, 12,940 player spell slots carrying a forced effect (against 1,837
solo), 275 enemy slots. `allies` and `enemies` are recorded in the corpus **meta**, not in the npz: a
0-d array breaks the loaders' `[:boards]` slicing, and a party file scored with the wrong
`Config(allies=..)` is silently meaningless, so the number lives where the knobs do and the probe
refuses when the actor width disagrees.

### XI.2 The published 3.8% was measured with a one-actor ruler, and it survives being re-derived

`tools/wsp_program_target.py` hardcodes actor 0 -- masks, action writes, and the tokens it solves on.
That is the right ruler for a solo player and the wrong one for a party, and the quantity that
bounds *every* per-actor method (a trained head, a closed-form write, a lookup, one ally reading a
wire) is instead:

    the share of party decision states where changing one living ally's action, everyone else held
    at the reference plan, flips the fight.

`tools/party_leverage.py` measures that per ally and as a union, on the shipped instruments
(`heuristic_actions` for the reference line, `train.make_baseline_rollout` for the continuation, a
terminal win as the label). It has a built-in control -- a candidate equal to the reference action
must reproduce the reference outcome -- and it reports the base win rate beside the share, because
four allies deal roughly four times the damage and a saturated board has nothing to decide.

At 64 boards, control mismatches **0**:

| shape | decision states | states one ally can decide | share | 95% interval |
|---|---|---|---|---|
| 1v4, mechanic corpus | 124 | 4 | 3.23% | [1.3%, 8.0%] |
| 4v4 party corpus | 179 | 9 | **5.03%** | [2.7%, 9.3%] |

The 1v4 row is the instrument agreeing with the instrument: 3.23% on 124 states against the 3.82% on
837 states published in X.10, same ruler, same data, different board sample.

Per ally the shares are 2.79 / 2.23 / 2.23 / 3.91%, which sum to 11.1% against a **5.0% union**. The
overlap is the finding: the same handful of knife-edge fights are decidable by several allies at
once, so a party does not multiply the number of fights that hang on a decision -- it multiplies the
number of ways to decide the ones that already hang.

### XI.4 The ceiling was diluted by 2.2x -- and the correction does not resurrect the pair write

`dofus-evidence/check_published_ceiling_denominator.py` reads the published label cache and asks, for
every (board, step) group, whether the fight was already over at that step. It rolls the reference
line once per board, records the first step whose *post*-state is terminal, and compares -- with the
off-by-one made explicit, because a cache row at step s is the state *before* step s.

On the 192 mechanic dev boards behind the published 3.82%:

| quantity | as counted | live states only |
|---|---|---|
| decision states in the denominator | 837 | 385 |
| states sitting on an already-finished board | **452 (54%)** | -- |
| pair-axis deciding states | 32 | **32** |
| pair-axis decided share | 3.82% | **8.31%** |
| every axis (moves included) | 6.81% | 14.81% |

Two conclusions, and they pull in opposite directions.

  * **The headline ceiling was understated by 2.2x.** Every board in that sample ended before the
    48-step walk ran out -- mostly on the round cap, where both teams are still alive and so every
    remaining step still offers legal candidates. `env.step` freezes `acting_team` on a done board,
    and `collect_labels` selected on `acting == 0` alone, so a finished fight contributed up to
    dozens of "decisions" whose winner was already fixed. A denominator that grows while the
    numerator cannot is a diluted ceiling, and 3.1%-3.8% had been quoted in this doc, in task
    descriptions and in memory as if it were the property of the game rather than of the query.
  * **No refusal changes.** All 32 deciding states were live, so the *count* that gates bind on
    (`--min-mixed 60`) was never inflated by the error. The pair write was refused because 32
    deciding states is too few to rank against chance, and it measured **−0.25** against the
    scripted comparator on the mechanic boards; a bigger share of a smaller denominator does not
    make a harmful write good. The `move` write's acceptance likewise rested on 43 decided states
    over 2,048 holdout boards, not on the share.

The corrected solo figure and the independent instrument agree, which is the point of measuring it
twice: `tools/party_leverage.py`, which filters `done` by construction, reports solo pair leverage
**9.31%** [6.6%, 12.2%] over 512 boards against the corrected 8.31% from the published cache. Two
rulers, one answer.

### XI.5 The bid channel, priced: it does not pay, and the reason is that there was nothing to say

`tools/bid_channel.py` -- allies aim alone, publish their intended target, aim again reading that
count, weighted in multiples of the score gap it has to overturn (measured 9.4-16.3 units; the first
version weighted it at 1.0 against damage scores in the hundreds, which is inert by arithmetic and
told me nothing about the mechanism). Terminal win rate, 256 boards, 3 seeds, paired against weight
zero:

| bid | hard party boards (base 0.8125) | party boards (base 0.9141) |
|---|---|---|
| concentrate, weight 1 | 0.7969 (−0.0156) | 0.9102 (−0.0039) |
| concentrate, weight 4 | 0.8047 (−0.0078) | 0.9062 (−0.0078) |
| spread, weight 1 | 0.8281 (+0.0156) | 0.9023 (−0.0117) |
| spread, weight 4 | 0.7656 (−0.0469) | 0.8711 (−0.0430) |

Nothing helps, and the diagnosis is in the mechanism column rather than the win column: in the
*independent* arm, 2.23 of the 4 allies already aim at the busiest target on the opening turn. A
damage-maximising rule points everyone at the same softest monster by itself, so "focus fire" is not
information a channel can add -- it is what the no-wire policy already does. Pushing the count hard
over-concentrates into overkill and costs wins, which is the only effect that clears noise here.

Read with XI.2's corrected denominators, the party is also not the headroom it first looked like:
solo pair leverage is 8.3-9.3%, party 10.9% [5.3%, 19.1%] -- overlapping intervals. My own 64-board
party estimate of 5.0% was wrong in the other direction, because states cluster inside fights and a
Wilson interval over states pretends they do not; resampling boards is the interval that survives
contact with more data.

So the communication layer as designed -- shared intention about *whom to hit* -- is not the win. If
a wire is going to earn its cost it has to carry something the actors cannot infer from their own
damage calculation, and the candidates are known: who is being disabled rather than damaged, who is
covering the ally that is out of position, and which summon must die before the next round. All
three require the environment to model states, glyphs and summons (#145), which it still does not.



### XI.3 What I predicted before measuring, kept because being wrong is the useful part

*Superseded on two points by XI.4 and XI.5, which is why it stays rather than being edited into
agreement with the data: the leverage did not stay "around 4%" (it was ~8% once finished boards came
out of the denominator), and the party did not separate from solo on the corrected basis.*

The pre-registered test was: if party leverage stays around 4%, per-actor edits have no new room and
the wire is a side show; at 15-20% it becomes the mainline. Measured: **5.0%, with an interval that
still overlaps the solo one.** So on current evidence a wire buys no headroom that a weight edit
could not reach, and I am not going to build a coordination layer on an interval.

The honest caveat is that this ruler cannot see what the wire actually does. It deviates **one** ally
and holds the rest at the reference plan. A shared bid channel changes **all four simultaneously**,
and a joint deviation is not bounded by the single-ally share at all -- that is precisely the
mechanism by which focus fire kills a monster before it ever acts, which no single-ally edit can
express. The next measurement is therefore an arm comparison, not another weight edit: the same party
played with independent actors against the same party played through a bid, paired per board on
terminal wins, on boards made hard enough that the base win rate sits near 0.5.

Scope to hold onto: the party is synthetic. Real composition is still unobserved (#127), classes are
drawn independently so a corpus can contain the same class twice, and no ally targets another ally's
states yet because the environment still does not put any there (#145).






## XII. The boss can finally summon, and summoning is worth less than an attack (measured 2026-11-06)

§X.9 left the enemy side with a mechanic it could not express: zeroing every enemy mechanic column
changed **0 of 512 boards' final HP**, because an instant kill was being read as a damage number, a
push moved nothing that was not already in range, and 34% of boss spell levels -- the summons -- had
no representation at all. This section closes the third of those and prices it.

**What was built.** Three columns (`spell_summon_bodies`, `spell_summon_hp`,
`spell_summon_damage`) resolved by the generator from the client's own tables: a summon writes its
species id into `diceNum` and one entry per body, and the body's HP is that species' grade nearest
the *summoner's* level, its damage the strongest line of its own kit. In the environment the swarm is
one mean-field pool per team, not new actor slots: incoming damage to a team is intercepted by that
team's pool before it reaches a fighter, the pool strikes for its own damage at the end of its
master's turn spread evenly over the living opponents, and its output decays with its surviving HP
(`striking * (standing - absorbed) / standing`). The pool is capped at the team's own total max HP.
Corpus, physics and observation key off one list (`SUMMON_TABLES`), so a corpus cannot half-upgrade,
and a board without the columns plays bit-for-bit like the old one -- `has_summons` gates the branch
rather than a zero running through new arithmetic.

**Two typing decisions that had to be made, and why.** A summon fills a cell beside the caster, and
the mask has only two target grammars -- "an enemy in range" or "a friendly actor" -- so summon rows
are typed friendly, which is the grammar that ignores range. Typed hostile, a 0-0 range summon is
uncastable: typed, priced, never applied. And `slot()` used to drop a spell level with no damage and
no heal line, which is exactly a pure summon; a summon-only row now survives, which is why these
corpora are *not* bit-identical to the `*_mech_*` ones -- the kits changed on purpose.

**The physics bites, arm by arm.** `dofus-evidence/mutate_summon_physics.py` ignores one input of
the pool arithmetic at a time and re-runs `combat/test_env_mechanics.py::SummonTests`:

| mutation | caught by |
|---|---|
| `absorb` ignored | `test_the_bodies_take_the_hit_that_was_meant_for_the_boss` (2 arms) |
| `swing` ignored | `test_the_swarm_strikes_back_for_its_own_damage_line` (2 arms) |
| decay ignored | `test_destroying_half_the_swarm_halves_what_it_deals` |
| cap ignored | `test_the_pool_never_outbulksthe_team_that_summoned_it` (2 arms) |
| growth ignored | `test_the_bodies_take_the_hit_that_was_meant_for_the_boss` (7 arms) |

The cap arm exists because the first version of it was wrong in a way only a batch could show: the
per-row gather `pool_cap[rows, own]` met the scatter-add's `[B, 2]` inside `jnp.minimum`, which
broadcasts happily at B=1 and B=2 -- every fixture in the suite -- and raises at B=64. It reached the
corpus runner, not the tests. A batch-4 arm now pins the shape (`test_the_pool_cap_is_a_batch_shape_and
_not_a_lucky_one`). 174 combat tests pass.

**Reach, on the three new corpora at 1,024 boards** (`dofus-evidence/swarm_merit.py`, scripted
reference line, `Config(allies=1|4, enemies=4, spells=4, max_rounds=24)`):

| corpus | enemy slots with bodies | boards with a body-bearing enemy | openings offering a summon | boards where the reference actually summoned |
|---|---|---|---|---|
| `encounters_boss_swarm_s4` (seed 7) | 452 | 331 | 266 (26.0%) | 61 (6.0%) |
| `encounters_boss_swarm_hold_s4` (seed 99) | 422 | 318 | 234 (22.9%) | 77 (7.5%) |
| `encounters_party_swarm_s4` (4 allies) | 413 | 316 | 179 (17.5%) | 74 (7.2%) |

**Merit, on the same boards, paired.** Two interventions, both compared board by board:

| corpus | player win rate: reference | enemy summons cleared | enemy *forced* to summon whenever legal |
|---|---|---|---|
| boss swarm (dev) | 0.2275 | 0.2383 (+0.0107) | 0.3242 (+0.0967) |
| boss swarm (hold) | 0.2432 | 0.2627 (+0.0195) | 0.3281 (+0.0850) |
| party swarm (4v4) | 0.8730 | 0.8984 (+0.0254) | 0.8936 (+0.0205) |

and `mechanic_ablation_env.py --columns summon` on the hold corpus: **68 of 1,024 boards end with a
different HP, 20 with a different winner, max HP difference 1,776**; the party corpus gives 67 and 26.
The same instrument's `player` arm gives 0 and 0 -- correctly, because the generator writes no player
summons (the class-spell database describes those effects in prose and that encoder is not wired in),
and the script refuses to call that a wash by printing the verdict line.

**What the numbers say.** Clearing the boss's bodies helps the player (+0.011 to +0.025), so the
mechanic is worth something to the boss -- the first enemy-side mechanic on this project that moves a
terminal state at all. Forcing the boss to take the summon every turn helps the player *far* more
(+0.085 to +0.097 on the solo corpora, 99 monster-wins turned into player-wins against 0 the other
way): a summoned wave is worth roughly a *pass*, not an attack, and a boss that spends its AP on
bodies instead of its damage line loses the fight faster. The reference line sits on the good side of
that curve by construction -- the greedy script only reaches for the summon when nothing hostile is
legal, which is precisely when bodies beat doing nothing.

**Both interpreters, and the old corpus, checked rather than assumed.** On the shipped numpy branch
(`DOFUS_FIGHT_BACKEND=numpy`, the interpreter that has no jax at all) a swarm board observes as 81
numbers per actor with 13 globals and carries `pool_hp`/`pool_dmg`, and six scripted steps run clean;
the same script on `encounters_boss_mech_s4_2048.npz` still observes 69 per actor with 9 globals and
reports no pool keys in the state. Presence-gating is therefore a property of both backends, not of
the jax one only.

**Consequences for the queue.** (1) "Teach the boss/party to summon more" is closed with a sign, not
left unmeasured: the direction is negative, so no head edit or reward shaping should push it. (2) The
observation is 17 numbers per spell token (81 per actor) and 13 globals on these boards, so the
mechanic-aware checkpoint of #142 has to be trained at that width; nothing existing reads it, so
nothing here is promotable. (3) Decision leverage: 20 of 1,024 boards change winner on the summon channel under the
reference line, which is inside the ~8% ceiling that bounds per-actor head edits (§X.4, XI.2); the
same ruler re-run on the swarm corpora is what decides whether the ceiling itself moved, and that
number is recorded below, not predicted here.
(4) The mean-field pool is a model with named limits -- bodies have no initiative slot, cannot be
targeted or displaced, the swarm's damage ignores resistances and `power`, and glyph, state-field and
over-time effects are still unmodelled. Every one of those is a reason the +0.02 is a floor on what a
real summon does, not a ceiling.

### XII.1 The filter that fixes the ceiling was credited without proof, and now it is proved (2026-11-06)

§XI.4 restated the pair ceiling from 3.82% to 8.31% and attributed the correction to one line in
`tools/wsp_program_target.py`: `rows = np.where((acting == 0) & ~state["done"])`. When the test class
for that line was mutated, the counts did not move, and the honest reading was that either the test or
the attribution was wrong. Both turned out to be instructive, and the audit
(`dofus-evidence/check_frozen_candidate_filter.py`, 256 boards per corpus, counting per step) settles
it in the direction that keeps the published number:

| corpus | player-turn rows | of those, on a decided board | still carrying a legal (spell, target) | rows the filter keeps |
|---|---|---|---|---|
| `encounters_boss_mech_s4` (1v4) | 2,975 | 2,479 | **527** | 496 |
| `encounters_boss_swarm_hold_s4` (1v4) | 2,879 | 2,293 | **535** | 586 |
| `encounters_party_swarm_s4` (4v4) | 10,817 | 10,173 | **1,151** | 644 |

The static reason the test was inert is now written into the fixture's comment, and it is a good one:
`step` repeats a cast against the *same* target while AP remains, so a four-monster wipe takes four
player turns and the fight terminates on the monsters' turn -- where `acting == 0` had already excluded
the rows and `~done` had nothing to remove. The only way to freeze a decided board *with* the player
holding the turn is a board that ends on the player's own turn, and the only candidates that survive
such a board are friendly-target casts (a heal, or now a summon), because every hostile pair needs a
living monster. So the repaired fixture has one 1 HP monster and a spell that does 200, and slot 3 is
an ally heal.

Two arms now pin it. `test_a_decided_board_in_this_fixture_still_offers_a_friendly_cast` is the
non-vacuity witness -- it asserts the decided board is frozen with `acting == 0` *and* still has a legal
pair, and it is what fails if someone strips the ally spell and makes the other arm decorative again.
`test_a_frozen_board_contributes_one_state_not_one_per_remaining_step` then removes the filter: with
`rows = np.where(acting == 0)` the board reports **4 decision states instead of 1** and the arm fails.
10/10 tests pass on the real tool. Same suite, mutation applied and reverted by
`dofus-evidence/mutate_summon_physics.py`'s sibling procedure: this time the mutation *moves a number*.

The same audit corrects one unit confusion in how these two instruments get read together.
`party_leverage.py`'s `base_win_rate` is a mean over (board, step) *decision rows*, while
`swarm_merit.py`'s player win rate is a mean over *boards*; on the 4v4 swarm corpus they print 0.7238
and 0.8730 for what looks like the same quantity. Neither is wrong and they must not be compared --
losing boards live longer and so contribute more rows, which is exactly the skew that made row means
the wrong ruler for win rate in the first place.

**The ceiling with summons live.** `party_leverage.py --corpus encounters_party_swarm_s4_2048.npz
--boards 512`: 1,296 decision states, 139 decided by one ally's deviation, leverage **10.73% CI
[6.76, 15.23]**, per-ally shares 0.053 / 0.054 / 0.036 / 0.035, control mismatches 0. Against the
pre-summon reading of 10.9% on the old party corpus, the summon physics did not move the ceiling: a
new mechanic that changes 26 of 1,024 winners still lands inside the same per-actor headroom band,
which is the point of quoting the band before editing weights rather than after.

### XII.2 Two corpora destroyed by a CLI whose argument changes meaning by subcommand (2026-11-06)

While checking whether today's observation widening broke the shipped path, `python
tools/wsp_gate.py score <corpus>.npz` was run twice. `score` reads its positional argument as the
**output** report path -- `write_text(json.dumps(report))` -- so it overwrote
`encounters_boss_mech_s4_2048.npz` and `encounters_boss_swarm_s4_2048.npz` with a 4.7 KB JSON. The
docstring's `score <out.json>` was the only signal, and the numbers came back identical for both
"inputs", which is what should have been the tell: that mode does not read a corpus at all, it
generates its own boards.

State of the evidence directory after the sweep (`np.load` on all 15 corpora, all now OK):

* `encounters_boss_swarm_s4_2048.npz` was **rebuilt** from the knobs its own meta records -- seed 7,
  2,048 samples, level 20-60, boss_prob 0.5, slots 4, max_enemies 4, enemy-level -1..+4, touch tables
  -- and reproduces every counter byte-for-byte-comparable: 7,703 real kit slots, 264 enemy slots with
  a forced effect, 892 with a summon, 1,360 bodies, 184 summoned slots that cannot attack.
* `encounters_boss_mech_s4_2048.npz` is **gone and not reproducible**: the generator that made it has
  since changed (summon-only spell levels now survive `slot()`, summon rows are typed friendly, three
  new columns are written). Its meta `.json` survives, so its knobs are documented; its arrays do not.
  Anything that needs those exact boards must be re-derived on `encounters_boss_swarm_hold_s4_2048.npz`
  (survived) or the dev swarm corpus (rebuilt), and a number that was only ever computed on the lost
  file is now a citation, not a re-runnable measurement.
* Everything else survived: the hold/eval/party corpora, `wsp_mech_labels.npz`, the tunnel-key files
  were untouched.

No published figure in this document changed as a result: §X.10, §XI.2 and §XI.4 were each quoted
from the hold-side runs and the label cache, and the two ceiling instruments re-run today agree with
them (below). What did change is the rule -- `wsp_gate.py` now refuses a `score` target that is not a
`.json`, and refuses one that exists and does not start with `{` or `[`, *before* `collect()` runs,
and `tools/test_wsp_gate_report_path.py` pins all four arms (refuse-a-corpus with the bytes asserted
unchanged, refuse-a-binary-json, refusal-before-work with an unloadable checkpoint so a late guard
would surface as a different error, and the control that a fresh `.json` is still accepted). 4/4 pass.

**Ceiling re-measured with the summon physics live** (`tools/party_leverage.py`, 512 boards,
board-clustered bootstrap, `control_mismatches: 0` on both):

| corpus | decision states | decided by one ally's deviation | leverage | CI |
|---|---|---|---|---|
| `encounters_boss_swarm_hold_s4` (1v4) | 1,080 | 96 | **8.89%** | [6.33, 12.13] |
| `encounters_party_swarm_s4` (4v4) | 1,296 | 139 | **10.73%** | [6.76, 15.23] |

Against the pre-summon readings of 9.31% (solo) and 10.9% (party), the bands overlap almost exactly:
adding a mechanic that moves 26 of 1,024 winners did **not** add per-actor headroom. That is the
strongest form of the bound this project has now measured three times -- it is not a property of the
label cache or of the scripted policy, it survives the world getting richer, and it is the number any
future action-head edit has to beat before it is worth training.

One unit trap surfaced by the same pair of instruments and now recorded so nobody compares them again:
`party_leverage.py`'s `base_win_rate` is a mean over (board, step) decision *rows*, while
`swarm_merit.py`'s win rate is a mean over *boards*. On the 4v4 swarm corpus they print 0.7238 and
0.8730 for what looks like the same quantity, because boards the player loses survive longer and
contribute more rows.

### XII.3 Actual 81-wide weights: the ablation is exact, and seeing mechanics costs wins at this budget (2026-11-06)

The visible/blind pair now exists as real parameter trees, not as a plan:
`dofus-evidence/models/mech_swarm_{visible,blind}_s1.pkl`. Both were trained on the same 256 swarm
development boards for 20 iterations, two epochs, seed 1, hidden 96, two layers, against the
heuristic opponent. Each carries **206,338 parameters in 45 leaves**, an 81-number actor token and
13 globals; the visible file is 835,813 bytes and the blind file 835,844. The blind arm records
`blind=["mechanics", "summons", "pool"]` in its own payload, and the evaluator refuses to score it
with any other observation.

`tools/read_weights.py` reads every array on disk. Its `--init-seed 1` arm rebuilds the *actual*
`model.init_params` reference rather than comparing norms and guessing. In the blind checkpoint the
aggregate drift of all 20 mechanic rows, 12 summon rows and four pool-global rows from that reference
is **exactly 0.0**; the actor-base and shipped-spell rows did train (encoder L2 drift 0.143330). In
the visible checkpoint those inputs are not dead: mechanic-row drift by slot is 0.042824 / 0.048654 /
0.043878 / 0.038723, summon-row drift 0 / 0.046875 / 0.034985 / 0.035999, pool drift 0.030281, and
whole-encoder drift 0.176109. The two encoders differ by L2 0.197816. Three inspector tests pass,
including a mutation that writes one blinded mechanic row and one pool row and must be caught.

The terminal price is `tools/mech_input_price.py` on **1,024 untouched hold boards**, three greedy
evaluation seeds, each arm reading its own recorded blindness:

| arm | player win rate |
|---|---:|
| visible mechanics + summons + pool | **0.036133** |
| blind to those inputs | **0.092773** |
| scripted reference | **0.243164** |

Visible minus blind is **−0.056641 ± 0.016104** (95% interval **[−0.072744, −0.040537]**), with 66
boards in the blind-wins/visible-loses cell against 8 in the reverse cell. Blind trails the reference
by 0.150391 ± 0.022237 and visible trails it by 0.207031 ± 0.024977. The verdict is therefore
**mechanic input costs wins at this training budget**, not that the inputs are disconnected and not
that mechanics can never pay. Neither checkpoint is promotable.

One instrument defect was caught before this verdict was written: the shared `paired()` returns a
±margin, while the first version of this tool tested that margin as though it were an absolute
interval and printed "no measurable effect" beside the 5.66-point loss. `input_verdict` now adds the
delta before testing exclusion of zero, and five pricing tests pin positive, negative and null cases
plus all checkpoint-refusal arms. Reports: `mech_swarm_input_price.json`,
`mech_swarm_weight_audit.json`, and `mech_swarm_blind_init_audit.json` beside the checkpoints.

### XII.4 Direct numerical WSP: one traced weight, no fitting (2026-11-06)

The user clarified the requested technique: read the existing weights, trace a wrong computation,
and directly author a numerical correction, not regress outcomes into a replacement head. The
covariance path was stopped. `dofus-evidence/direct_weight_trace.py` uses the unchanged `model.trunk`,
`model.forward`, `env.step` and `make_eval_rollout` to examine the stronger blind checkpoint.

On development row 5, the player stands at distance 5, has 4 MP, selects a range-4 spell, but holds
still and loses. Changing only that opening movement to approach wins. The move logits are
[-4.282377, -0.805686, -3.016546] for approach/hold/retreat. Neuron 89 activates at 9.888225, and its
weights are −0.3382473 into approach versus +0.1245009 into hold: their difference contributes
**+4.575758** to a hold-over-approach gap of only 3.476691. The manually authored edit is exactly
`params['move'][0][89, 0]: -0.33824729919433594 -> 0.12450093030929565`, equalizing those two weights.
No labels were fitted, no gradient calculated, and no magnitude search performed.

The saved artifact `dofus-evidence/models/mech_swarm_blind_direct_move89.pkl` was reloaded and every
parameter compared: **one coordinate changed**, all other coordinates identical. Its approach-logit
change equals `activation[89] * weight_delta` within 1e-5; hold/retreat, spell, target and value
outputs on the same states are unchanged. Row 5's approach logit becomes +0.293382 and the full
edited policy wins that fight. Across the 64 diagnostic development boards, wins rise 6 -> 11,
with five gained and none lost.

The literal edit was then frozen and scored on the previously unused **last 1,024** rows of the
swarm hold corpus (rows 1024..2047; not the first half used in XII.3), with identical scripted enemies:

| arm | wins / 1,024 |
|---|---:|
| original blind network | 95 |
| one authored weight | **138** |
| same coordinate, opposite-direction change | 44 |
| bias-only control matching row 5's +4.575758 approach-logit change | 137 |
| scripted player reference | 230 |

The authored edit gains 44 wins and loses one: **+0.041992**, paired 95% interval
**[+0.029406, +0.054578]**. This is evidence of a useful direct weight correction to this checkpoint,
not evidence of a decoded range detector: changing row 5's distance from 1 to 5 to 9 gives neuron-89
activations 9.8223 / 9.8882 / 10.4823, and the bias control almost matches the gain. Weight versus
bias is only +0.000977, interval [−0.002340, +0.004293]. The supported interpretation is an excessive
approach penalty in this weak network, not a recovered sophisticated strategy.

Artifact SHA-256: `3b6a5ec66c9eb7dc2ce8c7d33157a458a9c2ab06d61bcc4073a0495baf212dea`.
The original remains unchanged (`7e667af3cdaf8203ef537cb1fe8893e3adc7e1075ea429851d5876a0e1f8a2c1`).
Trace and paired per-row results are `models/direct_move89_trace.json` and `models/direct_move89_hold.json`
in the evidence directory. This is a solo abstract-simulator result, not a 4v4 or live-client result;
the candidate still trails the scripted reference and is not installed or promoted.

### XII.5 A second direct weight fixes one decision and breaks two later turns: rejected (2026-11-06)

With the movement edit fixed, the first 64 development boards contain three opening losses that a
single spell change can rescue. Row 13 selects slot 2 (logit 6.900372) over slot 0 (3.016503), although
slot 0 wins the fight. Input interventions distinguish two strong contributions to that gap: neuron
91 rises when slot 0 loses its damage, so its negative slot-0 coefficient can be useful; neuron 85
rises from 3.738021 to 4.564860 when slot-0 damage is restored, yet its slot-0 coefficient is
−0.212843135. A literal change of that one coefficient to +0.75 makes slot 0 win the ranking
(7.411747), while the zero-damage control still selects slot 2. No gradient, fit or sweep was used.
Reloading `mech_swarm_direct_move89_spell85.pkl` verifies exactly one additional coordinate changed;
all other output heads on identical inputs are unchanged.

But the full policy remains **11 wins / 64**: rows 13 and 59 are gained, rows 4 and 20 are lost.
`direct_weight_trace.py --spell-regressions` walks the unchanged parent trajectory to each first
changed decision and executes three alternatives through `env.step`. Both losses happen at distance
1 with slot 0's minimum range 2, while the movement head chooses approach. On row 4 the old spell
kills the 272-HP target, the new spell leaves it at 272, and the new spell with retreat kills it;
on row 20 the same three outcomes are 0 / 26 / 0 HP. This directly verifies a movement/spell
mismatch, not a damage or target-selection failure.

The candidate is **rejected on development evidence**, with no further holdout consumed and no
change to the retained movement-only candidate. The lesson is scoped: correcting one damage-related
readout is insufficient when the independent movement decision can invalidate its selected spell.
Next diagnosis must examine that interaction rather than keep increasing slot-0 preference. The
artifact is retained only as a rejected experiment (SHA-256
`068f3fc77e4f7c9744fb9a0e859e4b6c7adbafa1e963540698afa55c1565d6bd`); reports are
`dofus-evidence/models/direct_spell85_development.json` and `direct_spell85_regressions.json`.

### XII.6 Local range repair fails to generalize; two direct weights replicate (2026-11-06)

The movement/spell interaction was traced by varying distance (1/3/7) and slot-0 minimum range
(1/2), with the other inputs held fixed, at the two regression states. The existing representation
responds to both inputs. Exact inequalities on the movement readout identify one retreat connection
that can make both too-close states retreat while all six control states do not: neuron 28, whose
coefficient increment must lie strictly in (1.667112, 1.764827). The literal edit
`move[0][28,2]: 0.1153397 -> 1.82` was authored inside that interval, without regression fitting or a
parameter sweep. Replaying the actual forward pass passes all eight controls. Re-reading the saved
artifact proves one additional coordinate changed; unchanged logits and the expected activation-times-
weight change are checked. Development wins rise only 11 -> 12/64, losing a different board.

All candidates were then frozen. Two fresh corpora were generated offline from the cached Touch
tables, using the same distribution knobs as the swarm hold corpus and new seeds **117** and **231**,
1,024 boards each. The second was evaluated without any intervening weight change. The arms isolate
the spell and range corrections while holding the first movement correction fixed:

| arm | seed 117 wins | seed 231 wins |
|---|---:|---:|
| original 20-iteration blind network | 107 | 95 |
| first movement weight only | 159 | 145 |
| movement + spell weight | **181** | **161** |
| movement + range weight (no spell edit) | 140 | 136 |
| all three weights | 161 | 153 |
| scripted reference | 236 | 229 |
| random-legal player, one RNG seed | 105 | 99 |

This revises XII.5's small-development-sample rejection of the spell edit: it improves the
movement-only policy on both fresh corpora (+0.021484, CI [0.006960, 0.036009]; +0.015625,
CI [0.001849, 0.029401]). The two-coordinate artifact is now the stronger experimental candidate.
Across the two fresh sets it wins **342/2,048 versus the original's 202/2,048**, paired
**+0.068359**, CI **[0.055501, 0.081218]**, 165 wins gained and 25 lost. These are simulator-board
intervals for fixed weights, not training-seed uncertainty or live-client success rates.

The range correction is rejected: all three weights win 314/2,048, **28 fewer** than the two-weight
candidate (16 gained, 44 lost; delta −0.013672, CI [−0.021063, −0.006281]). Satisfying eight local
range controls did not make neuron 28 a generally reliable range predicate. The two-weight candidate
still loses to the scripted reference, 342 versus 465, delta −0.060059, CI [−0.072744, −0.047373].
No installer or live policy changed. The original NN is untouched.

The retained candidate remains `mech_swarm_direct_move89_spell85.pkl`, SHA-256
`068f3fc77e4f7c9744fb9a0e859e4b6c7adbafa1e963540698afa55c1565d6bd`: exactly the two authored numbers
in XII.4–XII.5, with all parameters finite and every other coordinate unchanged. Evidence beside it:
`direct_range_constraints.json`, `direct_range28_development.json`, `direct_wsp_fresh117.json`,
`direct_wsp_fresh231.json`. The fresh corpora are
`touch-data/encounters_direct_wsp_fresh_s{117,231}_1024.npz` under `dofus-evidence`.

### XII.7 The two-weight candidate’s remaining losses are a sequential-policy gap (2026-11-06)

`dofus-evidence/direct_weight_trace.py --classify-dev` took the first 512 swarm development boards,
played the scripted monster opening, then evaluated every legal value of one action head at a time
while the other two heads and the continuation remained fixed. Of the candidate’s **106** active
player-turn losses, **97 cannot be turned into a win by changing any move, spell, or target choice**.
Nine can, producing ten axis attributions—move 4, spell 6, target 2, with overlaps. Thus exactly
**8.5%** of these losses are one-decision errors; **91.5%** require a sequence, not another single
action correction.

The winning choices do not form a repairable class. They scatter across range-1 and range-4 spells,
approach/hold/retreat, both live enemy slots, and even pass; board-level health totals differ too.
There is no repeated numerical signature that justifies editing another coordinate. This closes the
first-decision direct-WSP line on the retained candidate rather than showing that the candidate is
optimal. The candidate’s measured win count remains 342/2,048 fresh boards, versus 465 scripted, so
the remaining 123 wins are attributed to the policy’s later decisions and continuation behavior.
The relevant tool, candidate, and evidence tests pass: 47 tests and 24 subtests. Reports:
`direct_candidate_classification.json`, `direct_candidate_classification512.json`.

### XII.8 A third direct repair removes a hard-coded slot assumption (2026-11-06)

The first attempt to score the frozen solo candidate on fresh 4v4 boards raised
`TypeError: ... (1024, 8, 1, 8), (1, 1, 5, 5)`. `t_bias` is `[spell, actor]`, so its five trained
actor slots cannot address an eight-slot party board—despite the model docstring claiming actor-token
symmetry. The repair was also numerical rather than fitted: set the entire trained target prior to
zero. The saved 5-slot checkpoint has `t_bias` norm exactly 0.0. On fresh solo seed 501, base and
two-coordinate action candidate produce exactly the same 1,024 terminal winners before and after
zeroing: 96 and 171 respectively, paired delta 0.0. The prior was behaviorally inert at terminal
resolution; removing its hard-coded slot association costs no solo wins.

Eight-slot artifacts with `t_bias` zeros then evaluated two fresh 1,024-board party corpora, seeds
317 and 433. These corpora were generated before any party tuning and were not used to choose the
solo edits:

| arm, target prior zeroed | seed 317 | seed 433 | pooled / 2,048 |
|---|---:|---:|---:|
| original blind network | 490 | 491 | 981 (47.9%) |
| movement coordinate only | 744 | 738 | 1,482 (72.3%) |
| movement + spell coordinates | **793** | **779** | **1,572 (76.8%)** |
| scripted reference | 900 | 890 | 1,790 (87.4%) |

Pooled paired effects against the preceding arm:
- movement versus original: **+0.244629**, CI **[0.225569, 0.263689]**; 510 gained, 9 lost.
- spell added to movement: **+0.043945**, CI **[0.033198, 0.054693]**; 110 gained, 20 lost.
- both action edits versus original: **+0.288574**, CI **[0.268667, 0.308481]**; 597 gained, 6 lost.
- candidate versus scripted: **−0.106445**, CI **[−0.121845, −0.091046]**; 32 gained, 250 lost.

The first two results are a surprisingly strong transfer of solo-authored numbers to four-actor
party boards, while the last keeps the gap honest: scripted play still wins 218 more of these
encounters. These intervals condition on one fixed checkpoint and one rollout RNG seed; they are not
training-seed or live-client uncertainty.

Saved as experimental artifacts outside the installer payload:
- solo (`n5_s501_v5`) SHA-256 for movement+spell zero prior:
  `c90b4b60b2466b50267a22cbecdbb73a84aa4424a2fc99b3ec213e83120edaf1`.
- party (`n8_s317/s433_v5`) movement+spell zero prior:
  `92d024d340194c1127b204ffbd433b1c626603b4a8062d5b0478bb0bd505bb4a`.
`tools/wsp_fight.py --harness pvm` reproduced the same 793 wins once `play_pvm` was corrected to pass
the checkpoint’s recorded `blind` list to `make_eval_rollout`. Before that repair it reported 777
(16 wins lost) by giving the blind network mechanic, summon and pool inputs it never trained on—
the same width-masking defect §XII.3 had guarded against at the pricing-tool level, not at harness
construction. A regression now wraps `make_eval_rollout`, proves the harness passes
`blind=("mechanics",)`, and records it in the result metadata. Full direct-weight suite:
48 tests plus 24 subtests pass. Reports `direct_party_harness317.json` (defective comparison) and
`direct_party_harness317_blindfix.json` (77.44%, bootstrap CI 74.90–79.98). The paired fresh-party
per-board evidence is `direct_bias_transfer_rows.json` plus `direct_party_fresh{317,433}.json`. The
party checkpoint is shape-compatible but not promoted: it remains an evidence artifact, and its party
performance was not used to select its two action weights. No installed policy changed.

### XII.9 A repeated spell contribution is real, but one-number equalization does not replicate (2026-11-06)

The party checkpoint retains 218 of 2,048 fresh 4v4 wins over the scripted reference. A 128-board
first-divergence trace on seed 317 classified 125 scripted-win/candidate-loss boards: the first
divergent head was spell in 86, target in 20, and move in 11. Replacing only that first ally action
recovered 26 wins; replacing all four allies' spell and target choices for the whole turn recovered
59; replacing only target choices recovered 33 and only moves recovered 10. Thus the party gap is
team-turn coordination, with the spell head largest but not sole.

Aggregating hidden activations on the original candidate before this trace produced a repeated
signature: coordinate 91 appeared in 82 slot-0-versus-slot-2 wrong-preference terms. Its actual
column weights are slot0 −0.306483686 and slot2 +0.262106776. The literal repair equaled slot2 to
slot0, changing exactly one coordinate in the 8-actor zero-prior artifact. It is a direct numerical
edit based on traced weights and activations, not a fit. The new trace redirected the dominant wrong
term to coordinate 71 and slot1, rather than solving the ranking.

Terminal results, 1,024 boards per corpus:

| party seeds | original candidate | one-coordinate arm | paired change |
|---|---:|---:|---|
| 317 and 433, used for diagnosis | 1,572 | 1,589 | +0.008301, CI [−0.000091, +0.016693] |
| 701 and 901, never used for selection | 1,549 | 1,555 | **+0.002930, CI [−0.005304, +0.011163]** |
| all four pooled | 3,121 | 3,144 | **+0.005615, CI [−0.000263, +0.011493]**; 87 gained, 64 lost |

The replication fails: the new corpora do not exclude zero, and all four pooled still do. The
one-coordinate arm is therefore **rejected**; the original two-action-weight zero-prior candidate is
retained unchanged. Artifacts: accepted candidate SHA-256
`92d024d340194c1127b204ffbd433b1c626603b4a8062d5b0478bb0bd505bb4a`; rejected arm SHA-256
`3e4c06dcfb0db930b40b57609aa0f88f7d1308a9681ecb714a22c238dcea8999`. Trace and reports:
`party_first_divergence317_pairs128.json`, `party_trace_spell91slot2_sample128.json`,
`party_spell91_slot2_terminal.json`, and `party_spell91_slot2_replication701901.json`.

The conclusion is narrower than “spell ranking is the fix”: a repeated local weight contribution can
be real without being a terminal policy cause. The remaining party gap requires coordinated multi-step
choices, not another isolated coefficient adjustment. No installed policy changed.

### XII.10 The biggest traced number is a correlate, and the party gap lives in the opening (2026-11-06)

`dofus-evidence/party_first_divergence.py --depth` adds a takeover ladder: scripted play is handed the
whole team turn at the first divergent turn, then at the next, and so on, over the 125 traced
scripted-win/candidate-loss boards on party seed 317. Recovered boards by number of scripted team
turns: **0 → 0, 1 → 71, 2 → 109, 3 → 119, 4 → 123, 6 → 123, all → 125**. Two controls bind: the
zero-takeover arm must reproduce the sampled losses (it returned exactly 0) and full scripted control
from the divergence state must win every board (it returned 125/125).

Logging every divergence of the full-control continuation gives 1,908 later events, and they are
overwhelmingly movement: move 1,538, spell 202, target 168. One coordinate dominates — hidden unit 33
of the move head prefers approach where the script holds in **1,527 of the 1,538** move events. Its
shipped weights are approach +0.23302744, hold −0.34863120, retreat −0.36485359, and the traced term
averages about 6 logits, so the number is large enough to be the cause of the direction choice.
`party_weight_write.py` authors it — one head, one coordinate, refusing to write unless exactly one
float in the whole parameter tree moved — and reproduces the XII.9 arm's weights from the same base.

Both directions of that one number are terminal losses. Equalizing approach to hold
(`move[0][33,0] := move[0][33,1]`) wins **545/1,024** and **522/1,024** where the base candidate wins
793 and 779: paired **−0.24219** CI [−0.26886, −0.21552] and **−0.25098** CI [−0.27823, −0.22373],
3 gained / 251 lost and 5 / 262. Equalizing hold to approach instead (`move[0][33,1] :=
move[0][33,0]`) wins 552 and 535: **−0.23535** and **−0.23828**, same sign and magnitude. The largest
available single-coordinate intervention on the most repeated traced contribution therefore destroys
about a quarter of the candidate's wins whichever way it is moved. Artifacts
`…n8_move33hold_v7.pkl` (SHA-256 `183f1906…`) and `…n8_move33approach_v8.pkl` are both rejected and
recorded in `dofus-evidence/models/ARTIFACT_LEDGER.json`.

`dofus-evidence/party_chain_window.py` then asks *where in the chain* control has to be, restricting
takeovers to a step window and pricing on 2 × 1,024 party boards (250 gap rows). Recovered rows:
**opening steps 0–5 → 236/250 (94.4%)**, middle 6–17 → 61/250 (24.4%), closing ≥18 → 9/250 (3.6%),
opening+middle → 250/250, every turn → 250/250. The first divergence itself lands on team turn 1 or 2
in the saved records. So the party gap is not spread over the sequence: it is set in the opening three
rounds, and the later divergences — including the 1,527 events attributed to coordinate 33 — are the
downstream consequence of a chain that was already wrong. Editing a symptom site cannot fix it, and
that is exactly what the two rejected arms show.

The lever that exists for a chain-conditioned fix is in the observation already: `observe()` puts
`state["round"] / cfg.max_rounds` into the global vector, so the round position of the fight reaches
every head through `glob[0]`. The next numerical step is therefore not another head column but the
round-conditioned path, priced against the opening window it is meant to repair. No installed policy
changed; the accepted candidate remains `mech_swarm_direct_move_spell_bias0_n8_s317_v5.pkl`.

### XII.11 The one chain feature the net can read is trained and almost worthless (2026-11-06)

XII.10 says the party gap is chain conditioning in the opening, so the cheapest candidate carrier was
measured before writing into it. `dofus-evidence/round_channel_price.py` finds the round column
empirically — shifting `round` by half of `max_rounds` moves exactly one global feature, column 2, by
0.5 — and zeroes that row of `glob[0]`. The witness is exact: 96 coordinates changed, all in
`glob[0][2]`, which carried 96 nonzero weights of L2 3.5613 and maximum 0.8707. So the channel is not
dead code; it is fully trained.

Terminal price of deleting it, 1,024 party boards per corpus: decisions differ on 25 and 21 rows, and
wins fall 793 → 788 and 779 → 768 against scripted 900 and 890 — paired **−0.00488**
CI [−0.01445, +0.00469] and **−0.01074** CI [−0.01949, −0.00199]. The whole round channel is worth
about one point, while the opening window that XII.10 recovers under scripted control is worth
236/250 boards.

That closes the cheap version of the chain hypothesis. What the net is told about the chain is one
normalized round counter: it cannot see what its team already committed, which spells it already spent,
or which positions it walked to. No weight can condition on information the input does not carry, so a
chain-conditioned opening needs a history feature in `observe()` first, and only then numbers to write
into it. Evidence: `dofus-evidence/models/party_round_channel_price.json`, ablated arm
`party_round_channel_zeroarmed.pkl` (SHA-256 `a3edae82…`). No installed policy changed.

### XII.12 Historical history/readout probe; active-row audit required (2026-11-06)

XII.10 and XII.11 left one question before writing any new input: does fight history actually carry
the scripted choice, or does the snapshot already contain it? `dofus-evidence/history_leverage.py`
walks the candidate's own opening (first eight team-turn steps of each party board) and labels every
living ally with what scripted play does in that state. Seven chain features are used: casts spent by
this ally and by the team, its previous spell and move, damage it dealt and the team took on the
previous step, round, and step fraction. The probe is fit on seed 317 (19,356 ally-turns) and scored on
seed 433 (18,790 ally-turns) it has never seen, always on the same rows and labels -- only the feature
set changes.

| arm | spell | move |
|---|---:|---:|
| snapshot only (what the net is already told) | 0.9402 | 0.9518 |
| snapshot + chain history | **0.9438 (+0.36 pt)** | **0.9558 (+0.40 pt)** |
| snapshot + shuffled chain history (control) | 0.9401 (−0.01) | 0.9511 (−0.07) |
| chain history alone | 0.8663 | 0.8507 |
| the net's own hidden state alone | 0.8863 | 0.8998 |
| floors: most-frequent label / uniform over legal | 0.4515 / 0.5717 | 0.8341 / 0.3333 |
| net's own argmax agrees with scripted play | 0.6829 | **0.1763** |

**These decision-level statistics are historical, pending an active-row audit.** The collector did
not exclude terminal boards. The table therefore does not establish that history is unnecessary,
that the trunk loses useful information, or that a readout defect causes the terminal-win gap.
Teacher-label predictability and terminal policy quality must be measured separately; the majority
floor must also be evaluated on the held-out rows, not assumed from the fit set.

The subsequent fitted move-head experiment is recorded in XII.13 and lost terminal wins. It is not
the requested direct numerical WSP method. Its failure does not explain why coordinate 33 was
harmful, nor does it eliminate other numerical edits. The current next step is the active-state
spell-target score trace in XII.16c. No installed policy changed; the accepted candidate remains
`mech_swarm_direct_move_spell_bias0_n8_s317_v5.pkl`.

### XII.13 Imitating the scripted move rule at the head level also costs wins (2026-11-06)

`dofus-evidence/party_move_readout_write.py` ran that route. The trunk is untouched, so every hidden
vector is the one the shipped net already computes; only `move[0]` (96×3) and `move[1]` (3) are
authored, 291 coordinates in total, from a multinomial fit of the scripted opening move on 19,356
ally-turns of the candidate's own opening. On those rows the new head agrees with scripted play on
**0.9065** where the shipped head agreed on **0.1667**, and 84.2% of opening movement decisions change.
Arm SHA-256 `0c75b1d21601bed9ca30c71ff63237004217bbc7a9b476322f8df0e2169a42b5`.

Terminal price, four party corpora of 1,024 boards each, paired per board:

| party seed | base | move-readout | paired delta | gained / lost |
|---|---:|---:|---|---|
| 317 | 793 | 742 | −0.04980 [−0.06565, −0.03396] | 10 / 61 |
| 433 | 779 | 737 | −0.04102 [−0.05730, −0.02474] | 16 / 58 |
| 701 | 770 | 738 | −0.03125 [−0.04545, −0.01705] | 12 / 44 |
| 901 | 779 | 749 | −0.02930 [−0.04499, −0.01361] | 19 / 49 |
| pooled 4,096 | 3,121 | 2,966 | **−0.0378**, CI ≈ [−0.0471, −0.0286] | 57 / 212 |

All four per-corpus intervals exclude zero on the wrong side, so this fitted move readout is rejected.
That result does not establish that all per-decision readouts fail or that communication is required.
The takeover arm changes more than coordination; its 236/250 recovery is not an isolated test of
cross-ally reservation. XII.16c supplies that control and finds nearly all diagnosis gain survives
independent raw teacher pair scoring. The decision-level fit/agreement counts above also require an
active-row audit before reuse; terminal outcomes and decision agreement are different measurements.

Consequence for the plan: retain the failed arm as evidence, not a representability impossibility
proof. Trace actual network arrays before choosing a direct numerical edit and gate on paired
terminal outcomes. `party_move_readout_write.py`, `models/party_move_readout_write.json` and
`models/party_move_readout_terminal.json` hold the historical evidence; v9 remains rejected in
`ARTIFACT_LEDGER.json`. No installed policy changed.

### XII.14 Opening head controls and an unproven static prior (2026-11-06)

`dofus-evidence/party_head_window.py` restricts scripted takeover to the opening (steps 0-5) and writes
only a chosen *subset* of the three heads on those turns, over the 250 gap boards of party seeds 317
and 433. Boards recovered out of 250:

| heads scripted in the opening | recovered | share |
|---|---:|---:|
| none (control: candidate alone) | 0 | 0.0% |
| move | 39 | 15.6% |
| spell | 145 | 58.0% |
| target | 88 | 35.2% |
| move+spell | 163 | 65.2% |
| move+target | 105 | 42.0% |
| **spell+target** | **220** | **88.0%** |
| move+spell+target | 236 | 94.4% |

The control binds at exactly 0, and the full triple reproduces XII.10's 236. Pairwise excess over the
better single head is spell+target **+75 boards**, move+spell +18, move+target +17. These counts show
that replacing both spell and target helps more than either alone; they do not establish
super-additivity or isolate cross-ally coordination. Scripted movement adds 16 recoveries after the
pair is replaced. These are opening-only, script-only-loss-subset results, not all-fight win rates.

`party_target_prior_write.py` then wrote the one channel in the architecture that literally expresses
"when casting slot k, aim at actor j" -- `t_bias`, `[n_spells, n_actors]`, all zeros since XII.8 zeroed
it. The table comes from the scripted opening itself: counts of (slot, target) over 1,024 boards,
deviated from uniform-over-enemies and scaled to 1 median target-logit unit (the shipped median logit
magnitude measured 10.6956, so the write is priced in the score's own units). 25 coordinates authored,
witness reports only the `t_bias` leaf changed, arm SHA-256 `a76ab702…`.

Terminal price: **801 vs 793** wins on seed 317 (paired +0.0078, CI [−0.0007, +0.0164], 14 gained / 6
lost) and **780 vs 779** on seed 433 (+0.0010, CI [−0.0059, +0.0079], 7 / 6). Pooled over 2,048 boards:
**+0.0044, CI ≈ [−0.0023, +0.0111]**, 21 gained / 12 lost.

This tested prior has a positive estimate but an interval crossing zero, so it is not promoted.
A static table cannot reproduce arbitrary state-dependent score differences, but this single failed
arm does not exhaust every useful static edit. Selecting a new head requires a representability
trace, not inference from the sign of one experiment. The 220/250 recovery remains an opening-only
reference, not a replacement for all-board terminal-win comparisons. Evidence:
`models/party_head_window.json`, `models/party_target_prior_write.json`, and
`models/party_target_prior_terminal.json`. No installed policy changed; the accepted candidate remains
`mech_swarm_direct_move_spell_bias0_n8_s317_v5.pkl`.

### XII.15 Historical score proxy; causal interpretation withdrawn (2026-11-06)

XII.14 localized the gap to the spell+target pair but a ceiling in boards does not name the leak.
`dofus-evidence/party_pairing_price.py` measures the opening in the scripted rule's own units by
gathering from `heuristic.action_values` (the [caster, spell, target] score tensor, resistance and kill
bonus included) instead of reimplementing damage math. Over both party corpora, 27,846 living-ally
turns inside opening steps 0-5 of the candidate's own trajectory:

| quantity, in the scripted score's units | scripted | candidate |
|---|---:|---:|
| value per ally-turn (real casts only) | 68.66 | 47.32 (68.9% of scripted) |
| enemy slots hit by more than one ally | 4,952 | 4,789 |
| overkill damage beyond the target's HP | 195,510 | 134,404 |
| total value | 1,911,946 | 1,317,663 |
| same spells, targets re-allocated greedily (HP-capped, kill-aware) | -- | 1,300,778 (98.7% of its own total) |

**Interpretation withdrawn after the active-row audit.** This collector included finished boards,
and the reallocation row compares an HP-capped total with an uncapped original. The 98.7% ratio is
therefore neither a target-optimality bound nor a measured no-change interval. Duplicate/overkill
counts on these trajectories do not eliminate either mechanism as a terminal-outcome cause.
The associated history/decodability statistics in XII.12 need the same active-row audit.

The table remains a record of `dofus-evidence/models/party_pairing_price.json`, not a current gate.
Use XII.16c's matched terminal-outcome controls to choose the next experiment: independent raw
spell-target scoring retains nearly all the diagnosis gain, so trace that scoring through the actual
network before assuming a cross-ally communication requirement. No installed policy changed.

### XII.16 All-fight HP-capped spell-target overlay (2026-11-06)

**Interpretation corrected by XII.16c:** the wins below are all-fight outcomes, not a matched test
of the opening-only ceiling. Independent raw teacher scoring retains almost all the diagnosis gain.
The pooled spread interval below is across corpus estimates, not a paired-board confidence interval.
Seeds 701/901 were fresh at the original measurement, not for future selection. HP capping can tie a
kill bonus with ordinary damage, so it does not guarantee that kill slots always outrank damage.

`dofus-evidence/party_joint_alloc_arm.py` keeps the net's movement and selects each ally's legal
(spell, target) pair in slot order. Enemy-target teacher scores are capped by remaining HP credit;
friendly-target scores are uncapped. This is score accounting, not an exact simulation of damage
or healing, and the cap can erase the ordering advantage of a kill bonus. The overlay uses
`heuristic.action_values` and `env.step` rather than reimplementing the teacher or environment.

Four unique party corpora, 1,024 boards each, per-board paired against the accepted candidate:

| party seed | candidate | joint allocation | scripted | paired delta | gained / lost | gap boards recovered |
|---|---:|---:|---:|---|---|---|
| 317 (diagnosis) | 793 | 893 | 900 | +0.09766 [0.07547, 0.11984] | 122 / 22 | 114 of 125 |
| 433 (diagnosis) | 779 | 881 | 890 | +0.09961 [0.07813, 0.12109] | 119 / 17 | 114 of 125 |
| 701 (fresh) | 770 | 864 | 880 | +0.09180 [0.07053, 0.11307] | 113 / 19 | 105 of 124 |
| 901 (fresh) | 779 | 883 | 894 | +0.10156 [0.07978, 0.12335] | 122 / 18 | 114 of 130 |
| pooled 4,096 | 3,121 | **3,521** | 3,564 | **+0.0977**, spread CI [0.0934, 0.1019] | 476 / 76 | 447 of 504 |

Each per-corpus paired interval excludes zero. The overlay trails scripted play by **1.05 percentage
points** (43/4,096), and recovers 447 of the 504 script-only wins. These all-fight counts cannot certify
the opening-only intervention ceiling in XII.14; the action window and evaluated board sets differ.
The two originally held-out corpora have since been reused and must not select future edits.

This is a *policy overlay*, not a weight write or proof that the network lacks coordination.
The 3,521/4,096 result remains an offline reference for a weight arm evaluated on those same boards.
XII.16c isolates the coordination contribution on the diagnosis sets; nearly all gain survives without
cross-ally reservation. Nothing is installed or promoted, and the accepted checkpoint is unchanged.

Evidence: `dofus-evidence/models/party_joint_alloc_arm_all4.json` (first pass double-counted two seeds
because `--corpus` was passed on top of the default list; the stored `corpora` array is the four unique
seeds and the pooled block was recomputed from it).

#### XII.16a Open caveat on the allocation arm, recorded before it is trusted (2026-11-06)

The label extractor (`dofus-evidence/allocation_labels.py`) refuses to record a pair that
`action_masks` forbids, and on the first board with no affordable cast it fired: the allocator's
argmax over an all-sentinel row returns index zero, i.e. spell 0 at actor 0, which the mask forbids
and `env.step` does not re-check. So the XII.16 arm could emit illegal pairs.

Fix applied in `party_joint_alloc_arm.py`: an illegal or non-positive argmax falls back to the net's
own masked action, and the arm now counts those fallbacks (~115k-119k per 1,024-board corpus). Re-priced
on all four party seeds the win counts are **unchanged to the board** (893 / 881 / 864 / 883, paired
+0.09766 / +0.09961 / +0.09180 / +0.10156), so the reported gain does not depend on illegal actions.

What is still open, and why the arm is not promoted: the fallback counter is almost certainly
over-counting -- it treats a turn whose only legal option is the pass slot as an illegal pick, because
`action_values` fills the pass column with the -1e9 sentinel even when passing is legal. Until that
counter is made exact, the arm is a measured *upper reference* on what joint allocation is worth, not
a certified mask-clean policy, and the label file it was supposed to emit has not been produced yet.
Next action in order: (1) make the legality test compare against the pass column so the counter is
trustworthy, (2) re-extract `party_allocation_labels_*.npz` with the legality witness passing,
(3) only then distill or hand-author toward the 3,521 / 4,096 gate. Evidence:
`dofus-evidence/models/party_joint_alloc_arm_legal.json` (its `pooled` block repeats the two default
corpora; read the four per-corpus entries).

#### XII.16b Allocation label export (2026-11-06; superseded by active-row audit)

**Correction:** the original legality and replay witnesses did not exclude finished fights.
The exporter retained surviving allies on `done` rows until the slowest board finished, yielding
298,318 rows of which **279,999 (93.86%) were post-terminal**. These are not decisions. The old
artifact is preserved by hash but must not be used for weight work. Its `credit` column was the
uncapped, nonnegative teacher score, not the HP-capped allocation credit.

The corrected `party_allocation_labels_active_s317_433_v2.npz` contains **18,319 active decisions**:
9,110 on seed 317 and 9,209 on seed 433. Every retained key and value matches its old row exactly
(`credit` is now accurately named `raw_score`). Old = retained + terminal-excluded reconciles on each
corpus; the old NPZ remains byte-intact. Replays still win 893 and 881 boards. Full-versus-200-board
slice checks cover every label column and terminal winners. Three toy tests pass, including a mutation
that removes the terminal filter and is caught. New NPZ SHA-256:
`02ac1ea6023ccdb0add641867f610679ab391bd043a25a084135fe4912baa999`.

The earlier opening-row lower bound used a different policy trajectory and was not a valid cross-check.
The export contains labels, not network input tensors; inputs must be reconstructed by deterministic
replay. The four-corpus reference is **3,521/4,096**, not 3,521/2,048; on seeds 701/901 alone it is
1,747/2,048. No weights or runtime policy changed in this audit.

The original export account below is retained as superseded evidence, not a current certification.

Two things closed the caveat. First the legality test was made exact: the argmax now runs only over
real casts that `masks["target"]` allows, so "no legal cast this turn" and "picked an illegal pair"
are separate facts, and re-pricing seed 317 under that rule leaves the win count at
893 of 1024 -- the gain does not depend on how the
pass sentinel is treated. Second, the winning behaviour is exported as supervision:
`dofus-evidence/allocation_labels.py` writes 298318 labels over
2 corpora to `party_allocation_labels_s317_433.npz`, recording for each living ally-turn the
allocation's spell and target, the HP-capped credit it earned, and the accepted candidate's own pair
and the scripted pair for contrast. Three witnesses are enforced inside the extractor and none are
assumed: every recorded pair is legal under `action_masks` (it raised on the first illegal one before
the fix), extraction is reproducible on a 200-board frozen slice,
and every rollout reaches terminal states.

Per corpus: seed 317 -> 149514 labels, mean credit 6.98, allocation disagrees with the net's spell on 4.0% of ally-turns, seed 433 -> 148804 labels, mean credit 6.98, allocation disagrees with the net's spell on 4.1% of ally-turns. That last column is the size of the learning problem: the joint allocator
is not re-deriving the network's spells, it is overruling them on most turns.

The gate for the weight route is fixed at 2048 boards -> 3,521 wins, the measured
allocation reference; a distilled or hand-authored channel that does not beat it is not a fix. Nothing
is installed or promoted, and no weights have reproduced this yet.

#### XII.16c Pair scoring explains nearly all the diagnosis gain (2026-11-06)

Same 2,048 boards (seeds 317/433), all fight turns, identical NN movement and heuristic opponent.
Only player spell-target selection changes: joint HP-capped scores reserve credit in ally order;
independent capped scores do not reserve; independent raw scores neither reserve nor cap.

| arm | seed 317 | seed 433 | pooled wins / 2,048 |
|---|---:|---:|---:|
| accepted NN candidate | 793 | 779 | 1,572 |
| independent raw teacher scores | 890 | 880 | 1,770 |
| independent HP-capped scores | 886 | 879 | 1,765 |
| joint HP-capped allocation | 893 | 881 | 1,774 |
| fully scripted | 900 | 890 | 1,790 |

Paired differences use winner == 0 and mean +/- 1.96 sample-standard-deviation / sqrt(2,048),
not corpus-to-corpus spread. Units below are absolute percentage points.

| comparison | delta | paired 95% CI | gained / lost |
|---|---:|---|---:|
| joint - independent capped | +0.4395 | [+0.0948, +0.7841] | 11 / 2 |
| independent capped - raw | -0.2441 | [-0.7770, +0.2887] | 13 / 18 |
| independent raw - NN | +9.6680 | [+8.1278, +11.2081] | 238 / 40 |
| joint - independent raw | +0.1953 | [-0.3947, +0.7853] | 21 / 17 |

Independent raw scoring retains **198/202 = 98.02%** of the joint overlay's net win gain here.
Reservation helps relative to the capped control, but a joint advantage over raw scoring is not
established. This localizes the next weight trace to dynamic spell-target scoring; it does not prove
coordination never matters on other encounters. Joint spell-and-target choice within one actor is
not the same intervention as reserving target HP across allies.

All **18,319 active golden labels** and exact terminal winners reproduce after the allocator switches.
Active no-legal-cast decisions total **72** (37/35), not the historical post-terminal-inflated counts.
Every active pair is mask-checked; independent raw pairs match the heuristic's spell/target choices.
Four toy tests pass, including a terminal-filter mutant that is caught and a two-ally fixture where
reservation changes the second target. Labels contain no network inputs; replay remains necessary.
The label header records an older allocator hash; the controls record the changed source hash and
prove default active-action/winner parity rather than claiming the source stayed byte-identical.

Corrections to earlier interpretations: the all-fight overlay is not an opening-only ceiling test;
exceeding the best single head by 75 boards is not proof of super-additivity; XII.15 compared capped
and uncapped totals and included terminal rows, so its 98.7% proxy does not establish target optimality.
The failed fitted readout does not refute direct numerical WSP, and history-probe decision statistics
need their own active-row audit. No weights, installed policy or live outcomes changed in these controls.

Evidence: `C:/Users/sao/Documents/dofus-evidence/allocation_controls.py`,
`test_allocation_labels.py`, and `models/allocation_controls_s317_433_v1.json` (per-board winners).
Report SHA-256: `f7c6a38f6aa3a5ab95aad38fe08fa6ff99a3714ffec845096dcfe2c6562e0f3b`.
Checkpoint SHA-256: `92d024d340194c1127b204ffbd433b1c626603b4a8062d5b0478bb0bd505bb4a`.
Active-label SHA-256: `02ac1ea6023ccdb0add641867f610679ab391bd043a25a084135fe4912baa999`.
Task #152 is complete; #150 and the direct-weight objective remain open. Future selected weight
edits need genuinely unused boards and a recursive unchanged-leaf witness before promotion.
#### XII.16d The disagreement traced through the arrays: it is spell choice, and no bias edit reaches it (2026-11-06)

XII.16c left the next move as "trace the active spell/target disagreements through the actual network
arrays before authoring numerical edits", with regression fitting and distillation excluded. This does
the tracing and reports what those arrays can and cannot express. Nothing was promoted, installed or
retrained.

**What was replayed.** Both label corpora (seeds 317 and 433, 2,048 boards, 18,319 active decisions)
were re-run with the accepted checkpoint, and at every active decision the trace recorded the exact
tensors the two heads read: the spell logits, the target attention `q_i.k_t/sqrt(H)` for the
allocator's spell, and both legality masks. The replay is the label export's own trajectory and not a
similar one -- `board`, `step`, `ally`, `spell` and `target` are identical row for row.

The heads fix what an edit can even look like:

| head | form | what a coordinate edit can express |
|---|---|---|
| spell | `actors @ W_spell + b_spell` | a 5-number bias; no state dependence |
| target | `q_i.k_t/sqrt(H) + t_bias[spell, target]` | a 5x8 table; target order independent of spell |

**The disagreement is spell selection.** Against the allocator's pair the network agrees on 29.36%,
the scripted opening on 91.33%. Splitting it: spell differs 60.41%, target 25.50%, and 10.22% is the
same spell with a different target.

| spell | allocator | network | script |
|---|---:|---:|---:|
| 0 | 77.56% | 37.85% | 77.35% |
| 1 | 13.19% | 28.16% | 14.03% |
| 2 | 4.81% | 26.81% | 5.24% |
| 3 | 4.04% | 6.77% | 2.98% |
| pass | 0.39% | 0.41% | 0.39% |

The network is not substituting a wrong spell for the right one; it is **spreading the basic cast
across spells 1 and 2**. Of the 14,208 rows whose allocator spell is 0, it keeps 0 on 5,812 (40.9%)
and moves 3,737 to spell 1 and 3,807 to spell 2.

**No bias edit of the existing shape reproduces it.** Max-margin feasibility over the 5-number spell
bias is infeasible at margin **-9.18** logits: there is no constant offset that makes the wanted spell
the argmax on every row. The best accuracy that shape can reach, fitted on even boards and scored on
odd ones:

| axis | edit | network | constant (majority label) |
|---|---:|---:|---:|
| spell (5 numbers) | 80.17% | 38.53% | 76.48% |
| target (40 numbers) | 74.85% | 73.35% | 56.55% |
| pair | 60.34% | 27.91% | -- |

The spell column is the finding: the bias lifts the head 41.6 points over the network and only **3.7
points over always answering "spell 0"**. Its fitted value is `[0, -6.25, -8, -8, -8]`, which is not a
decision rule but a marginal. The target table is worth +1.50 points, and its worst group is spell 3
(62.48% over 741 rows). Scripted play sits at 90.99% on the same rows, so an edit of this shape closes
roughly half the distance (27.91% to 60.34%) and none of the last thirty points.

**Two consequences, both about where the work is not.** XII.16c localised the next trace to "dynamic
spell-target scoring"; the target axis is already 73-75% right and a 40-number table buys 1.5 points,
so the mass is spell selection, which no writable coordinate of either head carries. The information
the allocator uses is HP-capped credit -- whether a cast's damage can actually be removed, and whether
the slot is a kill -- and neither head reads it. That is an input or an interaction term (#142), not a
bias write, and the direct numerical WSP objective does not become reachable by searching harder over
the arrays that already exist.

**The label is also ambiguous, measured.** Grouping rows by integer-rounded spell logits and legality:
1,238 groups covering 5,244 rows (**28.63%**) demand more than one spell from the same input. At two
decimals the test has no power -- 18,319 groups, one row each -- so the coarse number is the one that
means anything, and it is the same disease as the 23.8% exact ties in the tool-choice corpus.

Not established here: that a fitted head (supervised ridge on the trunk) could not close the gap --
that was excluded by instruction, and it is what the `wsp_program_*` tools now label themselves as;
nor anything about optimality, since the allocator agrees with the scripted opening 91.33% of the
time, so this measures the distance from the network to the script, not to a best policy.

Evidence: `C:/Users/sao/Documents/dofus-evidence/party_allocation_trace.py` (replay and analysis),
`party_allocation_trace.replay_v1.py` (the byte-identical code that produced the arrays),
`test_party_allocation_trace.py` (14 checks, including a synthetic feasible case and a contradictory
one, so "no edit exists" is a measurable claim rather than an assertion), and
`models/party_allocation_trace_s317_433_v1.json` with `.arrays.npz` (18,319 recorded rows).
Report SHA-256: `500d3dc15a696bb3c0dcf2aa24aeb1506b4144d99874e1c2899af250a5cbe087`.
Arrays SHA-256: `2edf1267fba17a89b4afe9e134712546d21d25c102420de6c53780f335d03b10`.
Checkpoint SHA-256: `92d024d340194c1127b204ffbd433b1c626603b4a8062d5b0478bb0bd505bb4a`.
Reproduce: `python party_allocation_trace.py --out models/party_allocation_trace_s317_433_v1.json`
(replay 3m39s on CPU, analysis 7s). Task #142 stays open and now has a measured target; #123's
"pipeline versus cheap weight-edit gap" is the gap this trace quantifies from the cheap side.
#### XII.16e Reading the weights: three exact zeros, and the experiment nobody ran (2026-11-06)

XII.16d traced the decision and found no bias edit reproduces it. This reads the checkpoint's
numbers directly -- `tools/read_weights.py`, then the arrays themselves -- and the cause is not a
coordinate that is wrong. It is coordinates that are absent.

**What the accepted checkpoint contains.** `mech_swarm_direct_move_spell_bias0_n8_s317_v5.pkl`
(SHA-256 `92d024d3...`), `blind = ("mechanics", "summons", "pool")`:

| coordinate | measured value | what it means |
|---|---|---|
| `t_bias` (5x8 = 40 numbers) | **every value exactly 0.0** | target logits are identical for all five spell slots |
| `spell/1` (5 numbers) | max abs **1.451e-3** | the spell head has no bias at all |
| `spell/0` columns | norms 1.607 / 1.330 / 1.562 / 1.487 / 1.480, cosines 0.004-0.195 | not a collapsed or duplicated head |
| `enc1` blinded rows | 32 of 81 actor inputs, 4 of 13 globals | fed constant zeros |
| drift from init, `--init-seed 1` | **0.0** on all nine blinded blocks | never trained |
| drift from init, live blocks | 0.053-0.078 per slot, actor_base 0.078 | trained normally |

The target head's spell-invariance is not inferred from the zero table. Forward pass, 64 boards:
`max |target[s] - target[0]|` over all five slots is **0.0**. With `t_bias` at zero the head ranks
targets once per caster and reuses that ranking for every spell, which is #142 stated exactly.

The encoder's blinded rows are at initialization because `observe` zeroes their input: a weight with
a zero input receives a zero gradient, so it never leaves its random start. `read_weights.py` proves
it rather than inferring it (`blinded_input_l2_from_init: 0.0`).

**What that does to the decision.** Same weights, same states, `blind` mask removed -- a paired test
over all 18,319 active decisions, trajectory held fixed:

| arm | spell agrees with allocator | target agrees | pair agrees |
|---|---:|---:|---:|
| accepted weights, input as trained | 39.59% | 74.50% | 29.36% |
| same weights, full input | 36.31% | 73.60% | 27.10% |

Unmasking makes it **worse**: 3,714 spell choices change, 915 for the better and 1,516 for the
worse. That is the drift witness's prediction arriving on schedule -- real values multiplied by
frozen random weights are noise, not dormant knowledge. So the blindness cannot be undone by an
observation flag any more than by a bias.

**The head is confidently wrong, not undecided.** On the 8,396 rows that want spell 0, the slot the
network picks leads by a median **1.952** logits (73.5% of rows above 1.0, 49.0% above 2.0). The
best bias needs 6.25-8.00 to overturn them, three to four times the head's own separation. Across
all multi-legal rows the top-1 minus top-2 gap is 1.377 median, with only 4.5% within 0.1 of a tie.

**The experiment that was never run.** All 27 party checkpoints on disk are blind to
mechanics/summons/pool. The only arm trained with the full observation is `mech_swarm_visible_s1.pkl`,
and it is solo (`allies = 1`), so it cannot be evaluated on party states -- its observation is 5
actors wide against the party's 8. No party policy has ever been trained on the numbers the
allocator decides with, which is why the "party arm" comparison in XII.16 was a comparison of
blind variants.

So the fix is not a weight write: a bias edit does not exist (XII.16d), unmasking injects noise, and
`t_bias` is worth +1.50 points because the target axis is already 73-75% right. The one arm that
could settle it is a party run with `--blind ""`, which is one command and has never been executed:

```
python -m combat.encounter.train --data touch-data/encounters_boss_swarm_s4_2048.npz \
    --out models/mech_swarm_party_visible_s1.pkl --blind "" --iters 20 --seed 1 \
    --samples 256
```

`--samples` is not optional on this machine. At the full 2,048-board batch the run dies in
iteration 2 with `RESOURCE_EXHAUSTED: Out of memory allocating 11,913,068,544 bytes`; the box
has 8.4 GB of RAM and 3.9 GB free, so the batch has to come down to fit. A 256-board arm is a
weaker sample than the blind checkpoint was trained on, and any result from it is reported
with that caveat rather than as a like-for-like arm.

Evidence: `party_allocation_trace.py` (replay, unblind pass, analysis),
`models/party_allocation_trace_s317_433_v2.arrays.npz` (adds `spell_logit_full`, `net_*_full`),
`tools/read_weights.py --init-seed 1` output recorded below. Arrays SHA-256:
`4dcd724909bdf7f78907d0d8f448edbca452a83b3b0935be934c54508e50f9b5`.
`--init-seed` drift, verbatim: `blinded_input_l2_from_init: 0.0`, `encoder_l2_from_init: 0.143329591`.
#### XII.16f The arm nobody had trained: +33.4 points of spell agreement from seeing the mechanics (2026-11-06)

XII.16e found the accepted party checkpoint blind to the columns the allocator decides with, with
those encoder rows at initialization. This runs the missing experiment and measures it against the
same 18,319 states.

**The trainer could not build a party arm.** `train.Config` was constructed as
`Config(max_rounds=..., spells=...)`, so `allies` was always 1 -- every checkpoint on disk is a solo
arm, and the "party" ones are that arm relabelled at evaluation time. That is exactly how `t_bias`
acquired its 40 zeros: the shape is `[spells, n_actors]`, so re-instantiating at 8 actors created it
fresh. `--allies`/`--enemies` are now arguments, with a guard that refuses a corpus whose actor slots
disagree rather than silently training a solo policy on a party board.

**The run.** Kaggle, 2xT4, 60 iterations, **170.3 s**, corpus `encounters_direct_wsp_party_s317_1024`
(8 actors, all eight mechanic/summon columns), `--blind ""`. Checkpoint
`models/mech_swarm_party_visible_s1.pkl`, SHA-256 `358e4e69...`; kernel
`tajdinenizar/dofus-party-visible-train`, dataset `tajdinenizar/dofus-party-visible-kit`.

**Decision agreement on identical states** (paired; the accepted checkpoint drives the trajectory, so
board/step/ally/spell/target still match the label export row for row):

| arm | spell agrees | target agrees | pair agrees |
|---|---:|---:|---:|
| accepted, input as trained | 39.59% | 74.50% | 29.36% |
| accepted weights, full input | 36.31% | 73.60% | 27.10% |
| **trained party arm, mechanics visible** | **73.03%** | 74.55% | **55.12%** |

**+33.44 points on the spell axis** from training alone -- the blindness was the cause, and no weight
write could have recovered it (XII.16d: infeasible at margin -9.18; XII.16e: unmasking on frozen rows
costs 3.28 points).

**The target axis did not move: +0.05.** Not a training shortfall -- a scale problem. The trained
table is nonzero but tiny (20 of 40 values, max 0.0095, l2 0.0208), while the attention term it is
added to spans a measured 116 logits (std 16.84, p1 -33.1, p99 41.4 over legal targets). A table four
orders of magnitude below the term it offsets cannot express a spell-dependent ranking, so
`t_bias` is dead by construction at this scale and #142 needs the interaction term (or a table at
attention scale), not more iterations.

Honest limits: 60 iterations is a small budget, and this arm **wins less** than the accepted one --
52.9% player win against the heuristic in its own rollout, where the accepted arm takes 793/1024
(77.4%) on the same corpus. It is a diagnostic that shows where the decision comes from, not a
promotion candidate. The corpus is also a WSP-generated party set, so the allocator it agrees with is
the same rule that generated it.

Evidence: `models/party_allocation_trace_s317_433_v4.json` and `.arrays.npz` (adds
`net_spell_vis`/`net_target_vis`), `models/party_visible_s1.json` (the kernel's provenance sidecar),
`kaggle/party_visible/` and `kaggle/party_visible_kernel/` in the evidence directory. Arrays SHA-256
below; `--shadow` on `party_allocation_trace.py` reproduces the third arm.
#### XII.16g How good is it: the arm decides better and wins less, and the loss is the trunk (2026-11-06)

XII.16f measured decision agreement. This measures games won, because that is the yardstick that
decides whether anything ships: 1,024 boards per corpus, greedy player, scripted opponent, one
function (`party_eval_wins.py`), paired per board.

| arm | wins s317 | wins s433 | spell agrees | pair agrees |
|---|---:|---:|---:|---:|
| scripted (the reference ceiling) | 87.9% | 86.9% | 91.3% | 91.3% |
| accepted: blind weights + direct edits | 77.4% | 76.1% | 39.6% | 29.4% |
| trained tonight: mechanics visible | 52.8% | 53.5% | 73.0% | 55.1% |

The accepted arm is **10.45 / 10.84 points behind the script** (paired CI [-12.65, -8.25] and
[-13.00, -8.68]). Nothing here beats the rule it would replace, and the honest headline for the
party head is "second best of three, ten points short".

**The new arm's 23-point win loss is not its spell policy.** Swapping one head at a time
(`party_head_swap.py`, same 1,024 boards):

| swap | s317 | s433 |
|---|---:|---:|
| trained arm + accepted `move` | 52.9% (+0.10, CI [-0.09, +0.29]) | 53.7% (+0.20, CI [-0.08, +0.47]) |
| accepted + trained spell/target head | 75.4% (-2.05, CI [-3.59, -0.51]) | 75.0% (-1.07, CI [-2.36, +0.21]) |

So the move array is not the cause, and the spell/target head costs **one to two points** while
buying 33 points of decision agreement. The 23 points live in the **trunk** — 60 iterations from
scratch is not a trained encoder, and no head swap recovers it.

That makes the trained spell head nearly free rather than a trade: transplanted onto the accepted
trunk it keeps 75-75.4% of the wins and decides 33 points more like the allocator. Whether that
translation survives a *trained* trunk is the open question, and it needs a longer run, not another
swap.

**What is actually in the loop is a different checkpoint.** `dofus-network/models/encounter_selfplay.pkl`
is the actor `combat_ai_server` loads: **solo** (`allies=1`), `blind=None`, 4,096 boards, last eval
**72.4%** against the heuristic. It has still never been executed against the game — 0 `fight.join`
in 697 recorded steps, per `docs/archive/now.md`. So "the model" for live fights is a solo 72% arm that has not
met the client, and the party work above is an offline harness result.

Limits: one seed, 60 iterations for the new arm; both corpora are WSP-generated, so the allocator
these arms agree with is the generator's own rule, and agreeing with it is not the same as playing
well — this section is the evidence for that distinction.

Evidence: `models/party_eval_wins_full_v1.json` (per-board winners, paired deltas),
`models/party_head_swap_v1.json`, `party_eval_wins.py`, `party_head_swap.py` in the evidence
directory.
