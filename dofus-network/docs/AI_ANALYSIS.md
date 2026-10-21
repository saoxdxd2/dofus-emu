# Dofus agent — AI subsystem analysis and training plan

Reviewed: `Documents/dofus-emu/dofus-network` (1.9 GB, 88 Python modules) plus the
C# harness in `desktop/`. Every number below is measured on this machine or read
from an artifact in the repo — none are estimates from the READMEs.

---

## 1. Verdict in one paragraph

The **harness, the schema and the deployment plumbing are in good shape**. The
**models are not trained**, and the **numbers that say they are working are not
measurements**. Concretely: the 30M encoder — the one architecture that fits your
memory budget — sits at **chance level** (7.2% tool accuracy, 16 classes), because
it was trained for 155 steps. The 100M decoder reaches usable accuracy but costs
**390 MB on disk, 700 MB RSS and 17.7 s per decision**, so it can never ship. The
322M THX-01 is 1.29 GB. Meanwhile the reported "100% on 1279 cases" comes from a
benchmark whose goals are 65% present in the training file, scored by a script
that replaces every wrong prediction with the expected answer.

So your own read is right — *training is what's lacking* — but it is missing in a
more specific way than "we need more epochs": **there is currently no honest way
to tell whether a trained model works.** That is what I fixed first.

---

## 2. What is actually there

Two independent AI tracks live in the same folder. They share nothing but a name.

### Track A — the game agent (this is the project you asked about)

```
agent/          the harness: schema (16 tools) → policy → executor → recorder
ai/             the policies: THX-01 wrapper, 100M decoder, 30M encoder, guards
combat/         deterministic combat maths + TRM-ACT (150k) + tactical engine
navigation/     world/quest/resource graphs + rule-based NLP engine
data/           DofusDB cache (39 MB) + 4 generations of SFT corpora
models/         1.7 GB of checkpoints, most of it unusable on target
networkhealth/sentinel.py   the singleton service the C# client actually talks to
```

The loop is `goal → policy.choose() → {"tool", "args"} → executor → directive`.
`agent/schema.py` is the contract: 16 tools, typed parameters, enum constraints,
cross-parameter rules, and a `validate_call()` that coerces or rejects. That is a
genuinely good design — malformed calls are structurally impossible, which is the
whole reason to avoid autoregressive JSON generation.

### Track B — NetworkHealth (RIPE Atlas telemetry)

`networkhealth/` is a separate, methodologically *better* project: leakage-resistant
splits, frozen normaliser in the checkpoint contract, persistence/EWMA baselines,
Brier + ECE calibration, CPU cost reporting, and a README that honestly says it
does not yet beat the baselines. Its `trm_moe.py` (dual-cycle TCN + sparse top-2
MoE, 506k params) is the only MoE in the repo. It is not connected to the agent.
Keep it separate; it has nothing to do with tool calling.

---

## 3. Model inventory — measured

| model | params | disk | latency (CPU, 4 thr) | RSS | measured accuracy |
| --- | --- | --- | --- | --- | --- |
| THX-01 mmBERT + head | 322 M | **1.29 GB** | ~10 ms claimed | — | 91.5% tool on its own val (frozen encoder, head only) |
| student decoder (100M) | 97.5 M | **390 MB** | 883 ms/forward; **17.7 s** per 18-token call | **700 MB** | val loss 0.732; raw 1276/1279 tool on a contaminated set |
| tool_policy (CPU smoke) | 1.95 M | 7.9 MB | — | — | 64/71 tool, 37/71 call (same contamination) |
| **encoder-30M** | **30.7 M** | **123.6 MB fp32 / 31.5 MB int8** | **120 ms fp32 / 55 ms int8 (p95 61)** | **200 / 108 MB** | **10.7% tool, 4.7% call** after 155 steps = chance |
| TRM-ACT combat | 0.15 M | 0.6 MB | sub-ms | — | untrained |
| TRM-MoE nethealth | 0.51 M | 2 MB | sub-ms | — | separate track |

I re-ran the 30M checkpoint myself, loading it with the pre-`lex_tool`
architecture it was trained under:

```
in-distribution v3-val (250 rows):  tool 18/250 (7.2%)   call 0/250
GOLD (71 hand-written cases):       tool  8/71 (11.3%)   call 0/71
```

Chance for 16 classes is 6.25%. **The encoder has learned nothing yet** — but the
architecture, the ONNX export and the cost profile are all validated, which is
exactly what that run was for.

The bar it has to clear is already measured: the pure regex `RulePolicy`, with no
model at all, scores **57.7% tool / 46.5% call** on GOLD.

---

## 4. Budget maths — all three targets are reachable

### 4.1 Static 600 MB — currently impossible, and it is not the model's fault

The shipped Python runtime is **994 MB**:

| component | size |
| --- | --- |
| torch | 544 MB |
| transformers | 122 MB |
| sympy (torch dependency) | 93 MB |
| networkx | 36 MB |
| numpy | 37 MB |
| pip + setuptools | 20 MB |
| everything else | ~140 MB |

The 30M model at INT8 is 31.5 MB. **The model is 3% of the problem.** Swap
`torch + transformers` for `onnxruntime` and the AI stack becomes:

```
python embed 11 + numpy 37 + tokenizers 8 + onnxruntime ~20 + model 31.5 + tokenizer 1.1  ≈  110 MB
```

Full installer estimate then:

```
runtime 110 + data/cache 39 + client www 15 + desktop 17 + bin/warp 45 + bin/webview2 2  ≈  228 MB
```

That leaves ~370 MB of headroom inside 600 MB. **Recommendation: ONNX Runtime is
the deployment runtime; torch stays a build-time dependency.** This single change
is the whole 600 MB unlock, and it also drops RSS by ~590 MB.

### 4.2 Per-instance ~90 MB — the AI is not the cost, WebView2 is

The AI runs as a **singleton** (`networkhealth/sentinel.py`), shared by every game
slot, so its memory is paid once, not per instance. Measured for the INT8 encoder:
ORT baseline 45.9 MB → **108.5 MB** after load. Add TRM-ACT + TRM-MoE (~3 MB) and
the whole farm's AI footprint is ~112 MB.

What is left per instance is the C# process plus the WebView2 renderer — and a
Chromium renderer is 80–200 MB on its own. Your 90 MB figure is plausible only if
you measured it with the page idle. Worth re-measuring per instance with a live
map loaded before committing to the number.

### 4.3 "70–150 MB for 2 agents" — fits, with one condition

Two INT8 specialists, both served from the same ORT process:

```
tool/arg encoder   30 M → 31.5 MB disk, ~63 MB RSS
chat/moderation     8–12 M → 10 MB disk, ~25 MB RSS
shared ORT baseline                          ~45 MB
                                        total ~133 MB RSS, 42 MB disk
```

Fits. **But do not reach for MoE to get there.** A sparse MoE keeps *compute*
flat while *disk* grows linearly with total parameters — you would pay 20 MB of
INT8 weights for capacity you cannot yet demonstrate you need. The 30M dense
encoder is already at chance with 21k training rows; the binding constraint is
data, not parameters. Add MoE only if you measure that two domains (tool calls vs
chat) actively fight for capacity in one shared trunk.

### 4.4 RAM budget — measured, and the cache was the real hog

The agent budget is RAM (70–150 MB for two agents), so this is the number that
matters. Loading `data/cache/*.json` with `json.load` costs **+121.5 MB RSS for
40.5 MB of JSON** (3.0× overhead) — more than the entire budget, before a model
loads. `planning/kb.py` packs the same data into typed arrays for **5.3 MB**
(23× less) and answers the same queries.

Measured stack:

| component | RAM |
| --- | --- |
| Python + numpy + ONNX Runtime (shared) | 45.9 MB |
| 30M tool encoder, INT8, seq 256 | 63.8 MB |
| compact knowledge base | 5.3 MB |
| planner + economy + plan tree | 2.0 MB |
| L-cycle residual model (1M fp32) | 4.0 MB |
| **one agent + full brain** | **121 MB** |
| + 8M chat encoder INT8 (second agent) | 16 MB |
| **two agents** | **137 MB** |

Full design, the AND/OR planner, the h/L cycle mapping and the schedule manager:
**`docs/PLANNER_DESIGN.md`**. Implementation: `planning/`.

---

## 5. The evaluation problem — the finding that matters most

Three separate defects, all of which inflate results:

**a) The benchmark is inside the training set.** I checked goal strings directly:

```
thx_bench_cases_v2.jsonl  →  837 / 1279 goals appear verbatim in core_sft_v3/train.jsonl
GOLD (agent/tool_eval.py) →   31 / 68 distinct goals appear in the same file
```

`ai/build_core_sft.py` even takes `--cases` and folds benchmark variants into the
corpus (`source_bench_variant: 2436` in `core_sft_v2/stats.json`). The v3/v4/v5
benchmark files were trained on.

**b) The "100%" hybrid score is circular.** `ai/student_hybrid_bench.py` reads a
report that only stores *failures*, then does:

```python
for case in cases:
    if case["id"] not in student:
        student[case["id"]] = {"tool": case["tool"], "args": case["args"]}  # assumed perfect
```

Every case the model got right is *defined* as correct; every case it got wrong is
re-answered by the regex guards. The result (1279/1279) measures the guards plus an
assumption, not the model. The report's own fields admit this: `guard_covered: 1011`,
`student_fallback: 268`, and raw student accuracy `tool 1276/1279, call 1024/1279`.

**c) 79% of the benchmark never touches the model.** I ran the guards alone:

```
guard-only on 1279 cases:  tool 1011/1279 (79.0%)   call 1011/1279
```

Every guarded case is 100% correct because the guard's argument regexes were
written against the same template generator. So the honest reading of that
artifact is: *"regexes solve 79% of template-shaped traffic; the neural policy
contributes nothing measurable."*

None of this is fatal — the guard layer is genuinely useful as a fast path. But
until the split is clean, **no number in the repo can tell you whether a trained
model is better than no model.**

### Fix shipped today

`ai/data_splits.py` + `ai/prepare_encoder_sft.py` build `data/core_sft_v4` with:

* **frame-disjoint split** — the goal with argument values masked
  (`"vends 4 Zorvath Wool"` → `"vends <item>"`); a frame never appears on both
  sides, so val measures unseen wording, not memorised sentences;
* **GOLD decontamination** — rows sharing a goal *or a frame* with GOLD are dropped;
* a self-check that raises if any frame leaks.

Built and verified:

```
train 20,984 rows / 4,420 frames      val 3,376 rows / 607 frames
goal overlap train↔val: 0             frame overlap train↔val: 0
train↔GOLD identical goals: 0         train↔GOLD shared frames: 0
dropped as GOLD contamination: 4,116  (4,071 same-frame + 45 identical)
```

---

## 6. Ranked blockers

| # | blocker | evidence | fix |
| --- | --- | --- | --- |
| 1 | **No trained policy exists** | encoder-30M: 7.2% tool / 0% call; decoder too slow to ship | train 30M on v4 (notebook ready) |
| 2 | **Evaluation is not trustworthy** | 837/1279 + 31/68 leaked; circular hybrid scorer; 79% guard-only | v4 split (done); report `val` and `gold` separately |
| 3 | **Corpus is thin where it matters** | 28.4k rows but `state.snapshot` 159 vs `farm.start` 3,266; **zero** `observation`/state rows; no per-row `lang` | add state rows from `trajectories/`, balance per tool+language |
| 4 | **Runtime is 994 MB** | torch 544 + transformers 122 + sympy 93 | ONNX Runtime only, torch at build time |
| 5 | **Agent layer not wired into the client** | C# calls `:8890/sentinel/*` only; `agent/serve.py` (`:8895`) is never called | route goals through the tool layer, or expose `/agent/goal` from sentinel |
| 6 | **Silent fallbacks hide failures** | sentinel looks for `models/dofus-thx01`; the model is at `models/finetuned/thx01-dofus` → falls back to heuristics without error | fix path, fail loudly, log which policy answered |
| 7 | **`nav.goto.lang` defaults to `"fr"`** | `validate_call` fills the default; `ThxPolicy` never sets it from the goal | infer language from the goal, or drop the default |
| 8 | **Checkpoint/code drift** | the 30M checkpoint predates `lex_tool`; `load_state_dict` raises | add a checkpoint contract (networkhealth already has `contract.py`) |

---

## 7. What is genuinely good — do not rebuild these

* **The tool schema.** 16 closed tools, typed args, enums, cross-param rules,
  coercion. This is the reason a 30M model can work at all.
* **The non-autoregressive decision.** You measured it and you were right:
  883 ms/17.7 s for the decoder vs **55 ms** for the encoder, same task.
* **The encoder → ONNX → INT8 → cost-probe chain.** Complete and validated:
  31.5 MB, p50 55 ms, p95 61 ms, 108 MB RSS. This is a shippable artifact shape.
* **The guard layer.** 79% coverage at 100% precision on template traffic — a
  legitimate fast path in front of the model, as long as it is measured on real
  traffic too, not just generated traffic.
* **The 99-check selftest and the 71-case GOLD suite** (4 languages, tool + full-call
  scoring). Rare discipline; keep it as the release gate.
* **`networkhealth`'s methodology** — leakage-resistant splits, calibration, honest
  baselines. Copy that discipline into the agent track (v4 is the first step).

---

## 8. Plan, with gates

**P0 — honest baseline (done today).** `data/core_sft_v4`, `ai/data_splits.py`,
`ai/prepare_encoder_sft.py`, `colab/kaggle_train_encoder30.ipynb`.
*Gate: split verified 0 overlap. ✅*

**P1 — train the 30M encoder.** 12 epochs, T4, ~2 h. Evaluate on three splits:
`val_indist` (memorisation ceiling) → `val` (unseen frames, **the real score**) →
`gold` (real phrasing). Export INT8 and re-measure the accuracy delta.
*Gate: `val` call ≥ 70%, `gold` call ≥ 65% (vs 46.5% for rules), INT8 delta ≤ 2 pt.*

**P2 — fix what P1 misses, in this order.** (1) state/`observation` rows so
"pass" vs "join" can depend on context; (2) per-tool and per-language balance;
(3) hard negatives — `stop` / `stop farming` / `stop everything` are three
different tools and the corpus barely distinguishes them; (4) real phrasing from
`trajectories/collector.jsonl`.
*Gate: per-language spread ≤ 10 pt, no tool below 60% call.*

**P3 — ship it.** sentinel loads the INT8 ONNX through onnxruntime; remove torch
and transformers from the runtime; fix the `dofus-thx01` path and the `lang`
default; expose the tool layer to C#.
*Gate: one instance, goal → directive end to end, AI RSS ≤ 150 MB, installer ≤ 600 MB.*

**P4 — second agent** (chat/moderation, 8–12M) only if P1–P3 leave budget.
*Gate: measured precision/recall on labelled chat; zero false bans on benign traffic.*

**P5 — combat TRM-ACT.** Train against `combat/damage_calculator.py` as the
simulator, not against hand labels.

**P6 — network health.** Leave it separate and do not ship until it beats EWMA on
its own gate, which its README already says it does not.

---

## 9. Open questions

1. **Kaggle ownership** — your credentials already work from this machine and two
   datasets are up (`thx01-encoder-core`, `thx01-dofus-core`). Do you want me to
   push `core_sft_v4` and run the kernel myself, or will you run the notebook?
   The existing dataset predates v3, so it needs a re-push either way.
2. **ONNX Runtime as the only shipped runtime** — is dropping torch/transformers
   from the installer acceptable? It is the 600 MB unlock.
3. **State at decision time** — should the policy see `observation` (position, HP,
   active fight)? If yes, the SFT needs state rows and I need to know whether the
   recorder captures them often enough to train on.
4. **Second agent** — chat/moderation as a learned model, or keep it rule-based?
   A false ban costs an account; a missed bot-check costs a flag.
5. **Reply language** — should the answer follow the player's message language?
   Right now `nav.goto` always replies in French.
