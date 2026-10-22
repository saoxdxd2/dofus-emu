# Pre-training gaps — what must exist before a training run means anything

> **Status moved to `PLAN.md` §4.** Two items here changed on 2026-10-22: the
> `models/dofus-thx01` path bug is moot (the THX checkpoints were deleted; the
> encoder is the only model), and W4 (sentinel on ONNX instead of torch) is still
> open — it is the remaining unlock for a ~150 MB installer.

Ordered by what actually blocks progress. Everything below was verified against
the code and the 1,611 logged records, not assumed.

---

## 0. The bar the model has to clear (measured today)

`python -m ai.release_gate` on the leakage-free `core_sft_v4` splits. All three
baselines are model-free — no encoder, no THX-01.

| split | cases | majority tool/call | **guard tool/call** | rule tool/call |
| --- | --- | --- | --- | --- |
| `val_indist` (same frames, new entities) | 800 | 16.4% / 0.0% | **69.4% / 13.5%** | 41.2% / 6.2% |
| `val` (unseen frames — the real score) | 3,290 | 5.0% / 0.0% | **71.1% / 13.6%** | 47.5% / 9.4% |
| `gold` (hand-written, 4 languages) | 71 | 5.6% / 0.0% | **73.2% / 56.3%** | 57.7% / 46.5% |

Read this carefully, because it decides what the model is *for*:

* **tool choice is already 71% solved by regexes** on corpus phrasing;
* **call accuracy is 13.6%** — the bottleneck is *argument extraction* (item
  names, quantities, prices), which regexes cannot do;
* on hand-written phrasing the guards reach 56.3% call.

So a 30M encoder that reaches 71% tool / 13% call is worthless. The gate to pass
is **> 71% tool and > 40% call on `val`**, and **> 56% call on `gold`**.

---

## 1. Harness — blocks data collection (highest priority)

These are client-side and must be done before any live learning is possible.

| # | gap | evidence | why it blocks |
| --- | --- | --- | --- |
| **H1** | **`farm.start` does not farm.** It toggles `window.__FarmBot.setEnabled(true)` and returns `note: 'zone walking / harvest loop is not implemented in the client yet'` | `mobile-disguise.js` handler | the planner's main verb has no implementation, so no farm outcome can ever be measured |
| **H2** | **`observation` is never populated** — `null` in 1,611/1,611 records | recorder schema vs `_agentSnapshot()` | the encoder's state branch never fires; state-conditioned decisions are unlearnable |
| **H3** | **no outcome counters** — zero records contain `kills`/`drops`/`minutes` | full scan of `trajectories/*.jsonl` | the learning loop is starved; `Outcome` always ends up `unverified` |
| **H4** | **market tools are stubs** — `hdv.scan`, `hdv.list`, `inv.sell` all return `ok:false, 'hdv_ui_not_mapped'` | JS handlers | the entire economy half of the planner (buy / sell / kamas-per-minute) cannot run or be measured |
| **H5** | **`inv.list` may be unavailable** — returns `'inventory is not exposed by this client build'` when `playerData.inventory` is missing | JS handler | without the bag, `inv.sell` and loot filters are guesswork |

**Order:** H2 is one line (poll `state.snapshot` on a timer into the recorder) and
unblocks the state half of the contract. H1/H3/H4 are the real work.

---

## 2. Tools — schema gaps

The model can only emit what the schema declares.

| tool | needed by | status |
| --- | --- | --- |
| `hdv.buy` | every `buy` branch | missing — `plan.missing_tools()` reports it |
| `craft.make` | every `craft` branch | missing |
| `char.stats` | level gate, profession gate, HP-based decisions | missing (`state.snapshot` partially covers) |

Adding a tool is cheap. **Adding it badly is not**: `hdv.buy` spends kamas
irreversibly, so it needs a spend cap and an item allow-list *in the schema*, not
in the prompt.

---

## 3. Deterministic ML — no neural net needed

These use no model and can be finished before any training run.

| # | item | status |
| --- | --- | --- |
| M1 | **language labelling** — corpus was 100% `"auto"`, so per-language accuracy was unmeasurable | **done**: `ai/lang_detect.py`, 94.4% on GOLD, **100% on decided cases**, abstains on language-neutral strings |
| M2 | **stratified frame split** — the hash split had put 78% of `val` in Spanish | **done**: `split_by_skeleton(stratify_by="lang")`; val is now 733 de / 756 en / 635 es / 682 fr / 484 auto |
| M3 | **release gate** — one command, all baselines, all splits, pass/fail threshold | **done**: `ai/release_gate.py` |
| M4 | **hard negatives** — `stop` / `stop farming` / `stop everything` are three different tools and the corpus barely separates them | todo: a deterministic minimal-pair generator, ~1 h |
| M5 | **time-estimate baseline** — an EWMA/persistence baseline so the ranker's gain is measurable | todo: the planner currently has no "before" to compare against |

---

## 4. Wiring — needs the model, but only glue

| # | item | status |
| --- | --- | --- |
| W1 | **encoder implements `Policy`** — it did not, so the harness could not call it at all | **done**: `ai/encoder_serve.py` (`choose(goal, observation, tools)`), with schema validation, reply-language fill and rule fallback |
| W2 | **ONNX inference without torch** — export existed, loading did not | **done**: `ai/onnx_policy.py`, decoder unit-tested (`--selftest` PASS); needs `pip install onnxruntime` in the runtime to run the graph |
| W3 | `agent/serve.py --policy encoder` | todo: one branch, uses W1 |
| W4 | sentinel loads the encoder ONNX instead of the 322M THX-01; path bug `models/dofus-thx01` → `models/finetuned/thx01-dofus` | todo |
| W5 | `/plan/*` endpoints for the schedule manager | todo |
| W6 | `nav.goto.lang` schema default `"fr"` | mitigated in W1; still wrong at the schema level |

---

## 5. Decisions that change the label space — must be settled *before* training

| # | decision | why it blocks |
| --- | --- | --- |
| **D1** | **refusal class?** 16 tools means every input maps to a tool. Real users ask "what's my DPS?" and the model will confidently emit `knowledge.search`. | adding a 17th class changes the head, the corpus and the gate. Cannot be bolted on after training. |
| **D2** | **does v1 see `observation`?** | if yes, the SFT needs state rows, which do not exist (H2). If no, say so and design the eval as goal-only. |
| **D3** | **15M vs 30M encoder** | decides the RAM budget (32 MB vs 64 MB). Measure both on `val` and pick by accuracy-per-MB. |
| **D4** | **language: per-message or session locale** | affects `nav.goto.lang`, the chat agent, and whether M1 runs per call. |

---

## 6. Two traps found while doing this audit

**The "deterministic guard" is not deterministic.** `thx01` *is* installed in the
shipped runtime, so `ThxPolicy._arguments` loads a 322M model the moment an
argument needs a number: **65 s cold, ~2.5 s per call** — a thousand times slower
than the 55 ms encoder it is supposed to short-circuit. The gate now disables it
explicitly, and any production path that claims "guards first" must do the same.

**Checkpoints carry no architecture version.** The 30M checkpoint in `%TEMP%`
predates the `lex_tool` head, so `load_state_dict` fails with a bare key list.
`load_checkpoint` now raises a readable error, but the real fix is a contract file
(version + field specs + tokenizer hash) stored beside the weights — `networkhealth`
already does this properly, `ai/` does not.

---

## 7. Suggested order

1. **H2** — poll `state.snapshot` into the recorder (hours).
2. **D1 + D2** — decide refusal class and observation scope (a conversation, not code).
3. **H1/H3** — implement or instrument the farm loop to return kills/drops/minutes.
4. **M4/M5** — hard negatives and the time baseline (a day).
5. **W3/W4** — serve the encoder; drop THX-01 from the runtime.
6. **Train** — 12 epochs on `core_sft_v4`, gate on `val` + `gold` with
   `python -m ai.release_gate --checkpoint ... --min-call-accuracy 0.40`.
7. **H4** — market tools, once the model can at least read an order correctly.
