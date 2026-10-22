# Project log — ideas, decisions, progress

One document that keeps the thinking, not just the results. Every entry says what
was decided, **why**, and what would change our mind. Numbers are measured on this
machine; anything unmeasured is marked as such.

Last updated: 2026-10-22.

---

## 0. What this project is

A Dofus Touch agent that takes a goal in natural language ("I want kamas", "do all
the quests", "beat a level-200 monster at level 50") and carries it out
unattended: it plans, moves, fights, farms, trades, and learns from what actually
happened. It runs inside a WebView2 emulator with no vision model — it reads the
game's own object model and a static map graph — and it is built to be cheap
enough to run per instance: a 25 MB INT8 encoder, a symbolic planner, and a
posterior learning loop that costs microseconds.

The constraint that shapes everything: **two agents inside ~200 MB per instance,
and no cloud dependency at runtime.**

---

## 1. Where we are (measured)

### Model

| checkpoint | val tool | val call | gold tool | gold call |
|---|---|---|---|---|
| v9 | 87.1% | 63.7% | 59.2% | 42.2% |
| v10 (stopped on the wrong metric) | 89.6% | 56.1% | — | — |
| v11 (recipe fixed) | 92.9% | 68.5% | 87.3% | 71.8% |
| **v15 (resume)** | **93.9%** | **69.8%** | **94.4%** | **77.5%** |

INT8 is bit-identical to fp32 on the serving path (gold 0.944/0.775 both), at
**62 ms** per call and **25.4 MB** on disk.

### Harness

- `observation` is now piggybacked on every directive response, age 0 — the log
  stops saying `null`.
- Farm counters (fights / wins / losses / kills / xp / kamas / drops) are derived
  by diffing client state around `GameFightEndMessage`. A fight with no xp gain
  counts as a loss, never as kills.
- `test_regressions.py`: 52/52.

### World data

2,696 maps, 1,509,760 cells (**1,150,576 walkable, 76.2%**), 336,785 static
sprites, 10,782 directed edges, 46 MB raw / 2.2 MB index with per-map walk masks.

### What is still stubbed in the client

`farm.start` toggles auto-fight but does not walk or harvest; `hdv.*` and
`quest.advance` return `*_ui_not_mapped`. These three are what stand between the
current build and real logs.

---

## 2. Decisions, with the reason and the falsifier

| # | decision | why | what would change our mind |
|---|---|---|---|
| D1 | Encoder, not decoder | 55–62 ms vs 17.7 s per decision, same task | a task needing free-form generation |
| D2 | Early stop on **call**, not tool | tool saturates at 0.93–0.95 while call still moves; v10 threw away 82% of its run | — |
| D3 | Anchors refreshed once per epoch, not every 50 steps | the head is overwritten with the encoder's own output, so a 50-step clock makes the target non-stationary (the v9/v10 sawtooth) | a run where 700-step refresh underperforms 0 |
| D4 | `price_rule=fixed` without a price is dropped, not guessed | 459/800 val calls were rejected over one optional field; dropping restores the label's canonical form | a goal that states a price must keep the rule (it does) |
| D5 | Resume, not restart | free at inference, starts from 0.685 call instead of 0.20 | — |
| D6 | Latent bus **and** text sidecar between agents | latent carries intent, text carries audit; 1 KB and 0 ms vs 15 ms of decoding | — |
| D7 | User-facing language from templates, not a decoder | the bot's statements are structured facts; a decoder can hallucinate a price, a template cannot | a user asks something templates cannot express |
| D8 | Fast weights only in the L-cycle | its output is a bounded time correction, so drift cannot cause a bad purchase | drift proven harmless at the trunk |
| D9 | No evolutionary weight search | a farm episode is minutes; one generation ≈ 33 h on moving content | evaluation becomes cheap |
| D10 | MoE deferred | +30 MB of INT8 weights for capacity 21k rows cannot feed | one trunk demonstrably underfits two domains |
| D11 | hdv through the in-game UI, not an API | the UI is the precise source; external feeds drift and break | the UI mapping proves unstable |

---

## 3. Ideas backlog, judged

### 3.1 Procedural weights ("store a seed, rebuild the model at install")

The idea: instead of shipping 25 MB of INT8 weights, ship a seed and a generator
and materialise the weights during installation.

**This is a real research line.** *SeedLM: Compressing LLM Weights through Seeds of
a Pseudo-Random Generator* (Shafipour et al., Apple, ICLR 2025) stores a PRNG seed
per weight block plus a small set of learned scales, reconstructs the weights at
load time, and reports roughly half the size of the quantised baseline with no
measurable accuracy loss on the models they tested. The mechanism works because a
trained weight matrix is *not* random: a seed-generated matrix captures the bulk
distribution and a few compensation parameters capture the rest.

**But it cannot do what the name suggests.** A hash is one-way; you cannot
recover 25 MB of learned content from 64 bits. What SeedLM buys is
*compression by structure*, not *free storage*. The floor is the entropy of the
weights, and a densely trained small model has less redundancy than a large
language model, so the realistic gain here is smaller than the paper's headline —
an honest estimate is 25 MB → 15–18 MB, and that is a guess until measured.

**Verdict: not the lever.** The installer is 1.44 GB; the model is 25 MB, i.e.
1.7% of it. Compressing the model by a third saves 8 MB. The same effort applied
to the payload saves 1.2 GB (see §3.2). Worth measuring *later*, for download
size across a fleet, not now.

### 3.2 The installer payload — where 1.4 GB actually goes

Measured sizes on disk:

| component | size |
|---|---|
| `runtimes/python` (torch 544 + transformers 122 + sympy 93 + …) | 1.1 GB |
| `dofus-network/models` (34 files, all historical checkpoints) | **2.22 GB** |
| `asset_cache` | 116 MB |
| `bin` (warp + webview2) | 46 MB |
| `data/maps` (new) | 52 MB |
| `client` | 15 MB |
| the model actually used (INT8 + tokenizer + policy) | 26 MB |

The build reports `Payload streams ready: 1375.5 MB` and produces a 1.44 GB
`setup.exe`. **Resolved:** the numbers reconcile once compression is counted —
2.22 GB of checkpoints deflate to ~1.3 GB, which is where the 1.375 GB payload
came from. The installer was shipping every historical checkpoint, all 34 files.

After the cleanup in §3.2b (models 2.22 GB → 215 MB) the same build produces
**284.4 MB**, i.e. **under the 600 MB target already**, without touching the
runtime. Dropping torch/transformers would take it to roughly 150 MB.

What is certain regardless: **the Python runtime is the dominant term.** The repo
already measured that swapping `torch + transformers` for `onnxruntime` takes the
AI stack from 994 MB to ~110 MB, and nothing at inference needs torch.
 That is
the single largest saving available, and it does not depend on the question above.

Projected once both are fixed: ~1.44 GB → **~250–300 MB**, inside the 600 MB
target with room for the map data.

**Verdict: do this before any weight-compression work.** It is a build-config
change, not a research project, and it is 100× the size of the model.

### 3.2b Checkpoint cleanup (done) and the real runtime blocker

`dofus-network/models` went from **2.22 GB to 215 MB** by moving three dead
directories to `_model_archive/` (outside the payload path, reversible):

| moved | size | why it is dead |
|---|---|---|
| `finetuned/thx01-dofus` | 1.26 GB | 322M THX-01; the sentinel looks for `models/dofus-thx01`, a path that has never existed, so it has been silently falling back to heuristics since day one |
| `finetuned/thx01-student-100m` | 372 MB | 100M decoder at 17.7 s/decision — documented as unshippable |
| `cache/thx01_markers.pt` | 40 MB | only read by the THX arg extractor above |
| `tool_policy/` | 15 MB | 1.95M CPU smoke model, superseded by the encoder |

Kept: `encoder30_v15/` (fp32 for resume, INT8 for shipping, 215 MB total) and the
empty `dofus-brain-native/`. **52/52 tests still pass after the move.**

**Why the libraries cannot simply be pip-installed yet.** `networkhealth/sentinel.py`
— the singleton the C# talks to — imports `torch` at module scope and uses it in
12 places for the THX classifier. So `requirements.txt` cannot shrink to
`onnxruntime + numpy + tokenizers` until that path is replaced by the ONNX encoder
policy, which is `PRETRAIN_GAPS` item W4. That single refactor is what unlocks the
994 MB → ~110 MB runtime, and therefore the 600 MB installer.

Order: **W4 (sentinel on ONNX, drop torch) → requirements shrink → payload
excludes `models/` and ships the INT8 graph only → ~250 MB installer.**



### 3.3 Licence check + fleet learning

The proposal: instances ship their logs to our endpoint, the models retrain
centrally, and the licence check gates access to the weights.

**Design that works:**

```
instance                          server
  trajectory.jsonl  --wss signed-->  ingest (redacted, source=live only)
  heartbeat (version, uptime)        │
                                     ├─ nightly: corpus build + gate
                                     ├─ retrain only if the gate passes
                                     └─ publish signed checkpoint vN
  <- signed model + licence token -- 
```

- The token is bound to a machine id and to the checkpoint version; the model
  file is encrypted with a key the server releases per token.
- **What this buys:** real phrasing and real outcomes from real play — the one
  input this project is starved of — and a model that improves for everyone at
  once.
- **What it does not buy:** protection against a determined user. Once the
  weights are decrypted to run, they can be dumped. Real protection means
  server-side inference (breaks offline + 200 MB) or a TEE (breaks "runs
  anywhere"). **Say this plainly: the licence check stops casual sharing, not
  extraction.**
- **Risks to design for now, not later:** poisoned outcomes (a user reporting
  fake drops teaches a false rate) — mitigate by accepting only `source=live`,
  rejecting implausible residuals, and never letting one instance move a
  posterior by more than a bounded amount per day; and privacy — the recorder
  already redacts account identifiers, so the ingest carries goals and outcomes,
  not credentials.

**Verdict: build the ingest + signed-checkpoint path after real logs exist.**
Shipping a licence check before the model is worth stealing is premature.

### 3.4 Time-limited training pass ("master it, then master it under time")

Train first for correctness, then add a latency budget to the objective: sample
per-step wall-clock and penalise the loss when a step exceeds the budget. The
turn-based game gives a natural budget (500 ms), and the encoder already runs at
62 ms, so the pass would mostly constrain *planning* depth, not the encoder.

**Verdict: later, and mostly on the planner, not the network.** A 62 ms model has
nothing to optimise; a 3,000-node plan search does.

### 3.5 CPU tuning

The real per-instance cost is WebView2, not the AI. Measured earlier: the AI is a
singleton (~112 MB for one agent + KB + planner). CPU work worth doing: batch the
encoder calls when several instances tick at once, and cap planner nodes per tick.

---

### 3.6 Play recorder (built, waiting for the first session)

`window.__DofusRecorder` in `scripts/mobile-disguise.js`, auto-started 1.5 s after
the page loads (`localStorage.recorderOff = '1'` disables it). Every event goes to
the host as a `PLAY_LOG` web message and is appended verbatim, one JSON object per
line, to `dofus-network/trajectories/play_<instance>.jsonl` by
`PlayLog.Append` in `WebView2ClientForm.cs` (UTF-8 without BOM, 64 MB rotation).

What it captures:

| kind | what |
|---|---|
| `session_start` / `session_stop` | session id, UA, screen, final counts |
| `send` | **every message the client sends** — movement, fight actions, item use, market ops, chat |
| `recv` | every server message, if the message registry can be wrapped |
| `click` | tag/id/class, screen x/y, and the **cell under the pointer** |
| `key` | key + code + whether the target was an input (never the value) |
| `state` / `state_delta` | position, map, hp, xp, level, kamas, fight flag, inventory size |
| `map_change` | from → to, with the cell — the edge the world graph is built from |
| `fight_start` / `fight_end` | with the state at that moment |
| `probe` | the full API surface at two moments (early, and 25 s in) |

Privacy is structural, not a filter: input **values** are never read, so a
password cannot reach the file even by accident.

The `probe` event is the important one — it reports which of `gui`, `dofus`,
`isoEngine`, `actorManager`, `mapData`, `connectionManager` exist and which
methods they expose, so the farm loop and the market automation get written
against the real API instead of a guess.

---

## 4. Research notes (citations we rely on)

| topic | source | what we take from it |
|---|---|---|
| latent recursion beats token CoT | *Less is More: Recursive Reasoning with Tiny Networks* (Jolicoeur-Martineau, Samsung SAIL, Oct 2025) | 7M params, no CoT tokens, beats much larger models on ARC-AGI / Sudoku-Extreme |
| continuous-thought channel | *Training LLMs to Reason in a Continuous Latent Space* (Coconut), arXiv:2412.06769 (Meta + UCSD, Dec 2024) | reasoning as a hidden-state vector, fewer tokens |
| the counter-argument | *Do Latent Tokens Think? A Causal and Adversarial Analysis of Chain-of-Continuous-Thought* (2025) | latent reasoning nothing checks is indistinguishable from none — hence the audit log |
| weight compression by seeding | *SeedLM* (Apple, ICLR 2025) | seed + compensation parameters reconstruct weights; ~50% on large models |
| classical compression | *Deep Compression* (Han et al., 2015) | pruning + quantisation + Huffman, 35–49× on LeNet/AlexNet-class |
| test-time training | NeurIPS 2025 continual-learning track; in-place TTT | fast weights updated at inference, O(1) memory |

---

## 5. Open questions

1. **Fleet ingest format** — one JSONL line per step (large, auditable) or a
   compact binary batch (small, opaque)? Default: JSONL, gzip, batched hourly.
2. **Template register** — decided: one neutral register per language, short
   sentences, explicit numbers, no idioms. Revisit only if users complain.
3. **TRM scope** — what the second agent must cover first: residual (time
   correction) → verdict → drift → critique. Its training data does not exist
   until live logs do.
4. **Model protection** — accept that a local model can be extracted, or move
   inference server-side for the paid tier? This is a business decision, not a
   technical one.
5. **Map data in the installer** — ship all 2,696 maps (52 MB) or fetch on demand
   from the CDN at first run (0 MB, needs network)?

---

## 6. Next, in order

1. Finish the three client verbs (`farm.start` walking, `hdv.*`, `quest.advance`)
   so the agent can actually earn and trade.
2. Run the pipeline live with **no TRM** and collect logs — real goals, real
   outcomes, real residuals. Everything downstream is starved without this.
3. Payload fix (§3.2): exclude `models/`, drop torch from the runtime.
4. Second agent, residual head first, trained on the residuals from (2).
5. Fleet ingest + signed checkpoints (§3.3), once there is something worth
   protecting and data worth aggregating.
