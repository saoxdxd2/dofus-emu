# now.md — where this project stands, and what happens next

Written 2026-10-22, second session. **Supersedes the earlier version: four of its
numbers were produced by bugs.** The correction comes first because it changes the
plan. Everything below is either measured (a number from a run) or marked unknown.

---

## 0. What the previous version got wrong

| it said | what was actually happening |
|---|---|
| "planner completes **21 quests / 21 objectives** in 500 steps" | It completed 21, then spent 429 steps re-accepting quest 458, which it had already finished. `sim/world.py` truncated `quests_done` to the last 20 entries and the planner treats that list as the set of finished quests, so the 21st became invisible. After the fix: **119 quests / 148 objectives in 400 steps**. |
| "the corpus covers **2 of 18 tools** — a generator bug" | The generator could only emit what the teacher emitted, and the teacher emitted two tools because **every tool could complete every objective**: `quest.advance` completed a kill, a delivery, anything. The tool choice was not a decision, so there was nothing to learn. |
| "**0.8830** accuracy, real learning over a 62% majority" | Measured on a corpus where objective type 0 — 5,669 open-world kills, the most common objective in the KB — was encoded as *unknown*, because `int(obj.get("type_id") or -1)` turns 0 into −1. That line existed in eight files. |
| "the simulator is deterministic given the features" | True, and it also silently accepted impossible calls and had no way to notice a loop. `score()` now reports `max_repeat` and `stalled`. |

None of that was a modelling problem. It was the world disagreeing with itself, and
the model faithfully learning the disagreement.

---

## 1. In one paragraph

The client works, the agent loop runs, and the simulator now charges for travel — which
is the first time it has disagreed with its own teacher. That change was worth making
because it invalidated the previous conclusion: the learned tool policy that won 2.8%
in a free-travel world is **19% worse** once movement costs steps, because its errors
concentrate on the one action whose price just went up. Its earlier win was an artefact
of cheap mistakes, so it is not wired in. What survives is a great deal of measurement
infrastructure and four separate negative results (capacity 138×, data 2.8×, recursion
K=12, and now the tool policy itself), all pointing the same way: **the teacher's exact
precondition checks are worth more than a learned approximation of them.** A model can
only add value where the teacher has *no rule at all* — after a failure — and that
needs labels from a live session, which still does not exist.

---

## 2. What works, verified

| thing | evidence |
|---|---|
| Client launches, no black screen | STA fix + bundle patches; `START: UI thread apartment: STA` in the log |
| Bundle patcher reproducible | `--bundle-selftest` → IDENTICAL, 13 rules |
| Agent loop end-to-end | `inv.list ok advanced 141ms -> 23 items` against the live bridge |
| Planner completes quests | sim, deterministic, L60, 400 steps: **119 quests / 148 objectives**; random 0, rule 0 |
| Planner does not loop | `max_repeat: 1`, `stalled: false` across L20/L60/L100 |
| Tool policy beats the teacher | 7 seeds, `--noise`, 400 steps: **99.6 vs 96.9 quests**, 109.4 vs 104.4 objectives |
| Tool policy beats copying the teacher | val acc **0.8986** vs teacher-agreement **0.8235** (+7.5 pts), 41→192→192→18, 48,594 params |
| The decision, not the label, is what improved | gain per decision **0.6965** vs teacher 0.6659, oracle 0.7149 → **97.4% of oracle** |
| Where the label is meaningful, it is near-perfect | margin > 0.1 (60.6% of val): accuracy **0.9691**; exact ties (22.7%): 0.7611 |
| Rare tools are learned, not ignored | macro-F1 **0.5321** after down-weighting tie rows (was 0.28) |
| Verifier is calibrated | val acc 1.0000, ECE **0.0075** after resume rounds |
| Tool policy is calibrated | ECE **0.0242** |
| Test suite | **79 checks, 0 failed** (`python agent/smoke_verifier.py`) |

**18 tools** in `data/capabilities.json`; `agent/features.py` reads that file, so the
action head is 18-wide by construction. The stale 16-tool encoder head is now only a
historical artifact.

---

## 3. What is proven dead (do not rebuild these)

**Recursion (TRM, K=12, deep supervision).** Two runs, two worlds:

| world | data | result |
|---|---|---|
| deterministic | 89,152 rows | 1.0000 at **k=1**, ECE 0.000 |
| noisy (15% refusal, stale/partial obs) | 105,522 rows | 0.8830 at **k=1**, flat to k=12, **+0.0000 gain** |

Twelve passes of a GRU cell produced the same answer as one. Tool selection is a
lookup; the paper's claim needs a task where one pass is insufficient.

**The learned value model.** 1,500 counterfactual states, 11 features: regret 0.062
for the model, 0.058 for the best constant ("always continue"), 0.133 for the shipped
"always retry". It did not earn its place and `models/decision_value.onnx` is not
wired in.

**The "first failure → retry" rule.** 2.3× worse than continuing in the simulator; in
the real client 0 of 9 retries worked, because they were retrying an absent client.

**Soft targets from the fork's gain distribution.** The corpus stores the full gain
vector, so training on the distribution instead of the argmax looked obviously right.
Measured: decision gain 0.7031 vs 0.7028 for hard targets (**+0.0003**), and ECE
0.1762 vs 0.0122. Rejected by its own result; `--targets soft` is kept only so the
comparison is reproducible.

**A smoother fork score (`--score cummean`).** The rate score can only take the values
1, 1/2, 1/3, 1/4, 1/5, so candidates tie constantly and the tie-break decides the
label. Scoring the *mean cumulative* gain instead was supposed to fix that. Measured
on the same seeds: ambiguity 34.5% vs 27.7%, simulator 98.6 vs 99.6 quests. It made
both worse. Default stays `rate`.

**The learned tool policy as a replacement for the teacher.** This one reverses an
earlier claim, and it is the most important entry here. In the free-travel world the
policy won 99.6 vs 96.9 quests. In the priced world it loses badly:

| policy | quests / 400 steps, 7 seeds, `--noise --travel-cost` |
|---|---|
| teacher (nearest ordering) | **82.6** |
| learned (nearest ordering) | 66.7 (−19%) |
| hybrid: teacher travels, model acts | 82.9 (+0.4%, i.e. nothing) |

The mechanism is exact. 357 of its errors are spurious `nav.goto` (recall 0.95,
precision 0.73). When travel is free, over-travelling is cheap insurance and being on
the right map is a precondition, so the habit is nearly harmless. When travel costs up
to three steps, that same habit is the most expensive mistake available. Suppressing
travel at decode time makes it far worse (66.7 → 12.0), because most of its travel
calls are necessary — so this is not a threshold that can be tuned.

The lesson generalises past this model: **it won only because its mistakes were
cheap.** The teacher's precondition checks are exact, and tool choice is mostly
preconditions, so a model at 81% cannot replace it. `models/tool_policy.onnx` is
therefore **not wired in**, on the same rule that retired `decision_value.onnx`.

**More data.** 32k → 90k rows (2.8×): validation accuracy +0.002, simulator score
**−0.9 quests**. Data volume is not the lever.

**Capacity.** Kaggle T4 sweep, 189k rows, five architectures, same corpus and seed:

| params | shape | val acc | macro-F1 | ECE |
|---|---|---|---|---|
| 8,018 | h64 d2 | 0.8961 | 0.2859 | 0.0076 |
| 48,594 | h192 d2 | 0.9005 | 0.2867 | 0.0074 |
| 293,394 | h512 d2 | 0.9007 | 0.2867 | 0.0066 |
| 1,111,058 | h1024 d2 | 0.9008 | 0.2871 | 0.0080 |
| 818,706 | h512 d4 | 0.9006 | 0.2864 | 0.0113 |

**138× the parameters buys +0.0045 accuracy.** The 8k model is within half a point of
the million-parameter one. This is the same answer recursion got, from the other
direction, and it is the third independent measurement that the task is short of
labels rather than of capacity.

*Caveat, because it matters for the macro-F1 column:* that corpus was generated before
the margin field existed, so no tie-weighting was applied and the rare classes were
never learned. The shipped local model gets macro-F1 **0.53** on 28k rows *because* it
down-weights ties — the column above is not a fair fight, and accuracy is.

---

## 4. What is blocked, and by what

0. **The world is built from the teacher's own rules, so it can only teach noise.**
   This is the real blocker, and it subsumes the rest. Every number this simulator
   produces is "how well does X imitate the teacher, plus handle the injected noise" —
   which is why the learned policy's entire advantage is +2.8% (99.6 vs 96.9 quests).
   The docstring admits the cause: *movement cost is abstracted away*. Travel is one
   free step from anywhere to anywhere, which makes "one objective at a time, re-plan
   every step" provably optimal.

   `World(travel_cost=True)` now charges hops (same map 0, same subarea 1, same area 2,
   otherwise 3, from the KB's 15,400 maps in 567 subareas across 67 areas). Measured at
   L60/400 steps: the teacher drops **119 → 97 quests (−18%)**. That 18% was being
   given away for free, and it is the size of the gap between "the world agrees with
   the teacher" and "the world doesn't".

   The remaining planning headroom is smaller than it looked: reordering a step's
   objectives nearest-first saves **22% of travel steps** offline, but in the actual
   loop it is worth **+2%** (61.0 vs 59.8 quests per 600 steps, 5 seeds), and
   nearest-neighbour is *exactly optimal* on this data (7.3 vs 7.3 steps per step). So
   ordering is a ten-line heuristic, not a research problem.

   **And the shipping curriculum never faces it.** With "fewest objectives first",
   only **11 of 400 steps** have more than one open objective — the shortest quests are
   the ones with one objective per step. With the corpus generator's diverse
   curriculum it is 1,500 of 3,000. So "fewest objectives first" does not merely
   prefer short quests; it systematically selects the quests with no ordering decision
   in them. That is open question #4, answered: it is convenient, and it hides the
   planning problem.

1. **The label is ambiguous, and it is now measured rather than suspected.** 40.1% of
   validation states whose exact 41-feature vector also appears in training carry more
   than one label. **23.8% of the corpus is an exact tie**: the top two candidates
   scored the same, so the label is my tie-break, not the world. Splitting the model's
   accuracy by that margin is the whole story:

   | margin between top two | share of val | accuracy |
   |---|---|---|
   | exact tie (0.0) | 22.7% | 0.7611 |
   | small (≤ 0.1) | 16.7% | 0.8538 |
   | clear (> 0.1) | 60.6% | **0.9691** |

   Where the fork has an opinion, the model is at 96.9%. The ten-point gap to the
   headline is entirely rows where the world had no preference. No capacity, data, or
   recursion removes that; the fix is either a better tie-break or fewer ties.
2. **The world only needs four verbs.** Label mix: `farm.start` 36%, `quest.advance`
   32%, `nav.goto` 24%, `fight.join` 4.6% — 96.9% of all decisions. The old P0
   criterion ("≥5 tools at ≥5% of rows") was a guess made before anyone had measured
   what the world requires; the measured answer is four. Forcing a fifth would mean
   inventing value for tools that have none.
3. **Six of the eighteen tools are placeholders in the world.** `hdv.scan`,
   `hdv.list`, `chat.say`, `knowledge.search`, `knowledge.remember`,
   `knowledge.forget` have no state effect, so no counterfactual can ever prefer them.
   The model correctly never emits them, and therefore never learns *when* to.
4. **No live session.** 41 real steps, 15 failures, **14 of 15 structural** ("no client
   instance"), so they cannot rank recovery strategies. `ai/reality_check.py` refuses
   to conclude below 30 genuine failures, and correctly.
5. **The fork cannot see a stale-belief mistake.** A read (`inv.list`) wins only when
   the stale observation would have changed the *choice*. In the fork the action and
   its arguments are fixed by the caller, so acting on a stale observation usually
   still works. Reads are therefore 1.2% of labels — possibly understated by the
   method, not by the world.

---

## 5. The plan, in order

### P0 — the corpus — DONE (with one criterion revised)
`sim/tool_choice.py` forks the world once per candidate tool, runs the teacher five
steps, and labels the tool with the best **objectives per step**. 18-wide label space,
41 shared features, 5% trivial rows.
**Result:** 4 tools ≥5% (not 5), 96.9% of mass, teacher agreement 81%.
**Falsifier as written:** "if the planner still only emits two tools, the planner is
the bottleneck". It emitted four once the world stopped letting one tool do everything.

### P0.5 — the label ceiling (next, no GPU needed)
23.8% of rows are exact ties and another 16.7% are within noise. Two ways to attack:
(a) **a better tie-break** — right now ties go to the teacher, which is defensible but
unmeasured against "prefer the cheaper tool" or "prefer the one that worked last time";
(b) **fewer ties** — the rate score quantises to 1/k, so most ties are an artefact of
dividing by a small integer, not of the world being indifferent.
**Done when:** exact ties below 10% with the simulator score unchanged or better.
**Falsifier:** if neither moves the tie rate, the world really is indifferent in a
quarter of its states and the corpus should simply drop them.

### P1 — one real session (needs the client running)
20–30 steps of `farm` / `go to` / `open inventory` with the loop attached, then
`python ai/reality_check.py`.
**Done when:** ≥30 genuine (non-structural) failures. **Falsifier:** if the real
ranking contradicts the simulator, the simulator's noise model is wrong and gets fixed
before any further training.

### P2 — the decision model, second attempt (this is now the *only* live model target)
Not the tool choice: the teacher already does that exactly. The only place a model can
add value is where the teacher has no rule — what to do **after a failure**.
**Done when:** it beats both constants on real failures. **Falsifier:** if it loses,
the decision is not learnable from these 11 features and the next move is richer
features, not a bigger net. Blocked on P1 for labels.

### P3 — plan chunks in the runtime
`plan_chunk.onnx` (1.79M, exact 0.9682) predicts the next 3 tools. Wire it as a hint
the controller can veto, and measure replans per quest. **Falsifier:** if replans don't
drop, the chunk is decoration.

### P4 — the ladder (value → search), only if P2 succeeds
Do not start before P2 has a positive result.

---

## 6. Code map

```
dofus-network/
  agent/
    features.py          THE featuriser: 41 dims, 18 tools from capabilities.json,
                         ObsTracker (stale/partial inference), objective_type()
    autopilot.py         the loop: plan → execute → verify → decide
    planner.py           KB → plan. Deterministic teacher + fork() + note_result()
    planner_nn.py        same interface, tool from models/tool_policy.onnx
    controller.py        continue/retry/recover/replan + error taxonomy
    verifier.py          did the step work? rules + optional ONNX head
    knowledge_store.py   discovered facts, not weights
  sim/
    world.py             symbolic world; noise=True; map preconditions; reads repair
                         stale observations; stall detection in score()
    tool_choice.py       COUNTERFACTUAL tool corpus: fork per candidate, label by
                         objectives-per-step (this replaced rollout.py for training)
    rollout.py           the old imitation corpus — superseded, kept for comparison
    decisions.py         counterfactual repair corpus (continue/retry/recover/replan)
    run.py               planner | nn | random | rule → quests per N steps, --noise
  ai/
    train_tool_policy.py 18-way trainer + capacity sweep; self-contained for Kaggle
    train_planner_student.py  the old 5-tool student (superseded)
    self_improve.py      resume → train N seconds → score acc & ECE → keep best
    reality_check.py     real failures → can they rank recovery strategies?
  kaggle/
    toolkit/             the simulator + KB, shipped as a private dataset
    tool/                the kernel that generates a corpus then trains on it
  models/tool_policy.onnx  48,594 params, 18 tools, 41 features
```

---

## 7. How to run it

```bash
# tests
cd dofus-network && python agent/smoke_verifier.py

# simulator scoreboard (always quote --noise)
python sim/run.py --policy planner --level 60 --steps 400
python sim/run.py --policy nn      --level 60 --steps 400 --noise

# the counterfactual corpus (6 workers, ~4 min → ~32k rows)
python sim/tool_choice.py --episodes 400 --steps 80 --noise --parallel 6 --minutes 4 --out data/tool_sft

# training (local, CPU, ~40 s)
python ai/train_tool_policy.py --data data/tool_sft --out models/tool_policy.onnx --epochs 60

# capacity sweep (the GPU question)
python ai/train_tool_policy.py --data data/tool_sft --sweep --device cuda

# kaggle: generate + train + sweep in one kernel (~28 min: 26 generating, 2 training)
kaggle kernels push -p kaggle/tool

# the output is ~4.5 MB (model + reports). It used to be 134 MB because the corpus
# was written to /kaggle/working; that is fixed, and the corpus now goes to /tmp.
kaggle kernels output tajdinenizar/thx-02-tool-policy -p /tmp/kout
```

Kaggle: account `tajdinenizar`, credential at `~/.kaggle/kaggle.json` (outside the
repo). Private datasets: `thx-02-sim-toolkit` (code + KB), `thx-02-tool-corpus`.
Pushing a new version of a *queued* kernel returns 409; the kernel `id` must equal its
real slug. Datasets mount at `/kaggle/input/datasets/<owner>/<slug>/`, and the CLI
uploads each directory as a **zip**, so the kernel unpacks whatever it finds.

**Keep the output small.** The training kernel first wrote its 189k-row corpus into
`/kaggle/working`, which made the output 134 MB and cost two things that had nothing
to do with the experiment: `kaggle kernels output` broke twice on the 100 MB+ file
(`IncompleteRead`), and a dependent kernel that only wanted the 4 MB model had to
mount all 134 MB and sat in RUNNING for ten minutes. The corpus now goes to `/tmp`;
the artifact is the model. A script kernel still takes 1–2 minutes to provision and
convert to HTML, so "it's just a file copy" is never just a file copy.

---

## 8. Open questions nobody has answered yet

1. Does the real game rank recovery decisions the way the simulator does? **Unknown.**
   Needs P1.
2. Of the 40% ambiguous states, how much is "the plan's next step is not a feature"
   and how much is the noise draw? The first is fixable, the second should be smoothed.
   **Unknown, and it is the next measurement.**
3. Are reads genuinely worth 1.2% of decisions, or does the fork understate them
   because it fixes the action before the stale observation can mislead the choice?
4. The shipping curriculum (fewest objectives first) is now known to bias the state
   distribution — that is why the corpus needed objective-type steering. Nobody has
   compared the shipping curriculum against an alternative.
5. Six tools have no effect in the world. When should an agent sell, scan the market,
   or record a discovered fact? Nothing in this project can answer that yet.
