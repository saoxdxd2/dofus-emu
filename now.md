# now.md — where this project stands, and what happens next

Written 2026-10-22, end of a long session. Everything below is either **measured**
(a number from a run) or **explicitly marked as unknown**. If a claim has no number,
it is a hypothesis and says so.

---

## 1. In one paragraph

The client works, the agent loop runs, and the planner completes quests in a symbolic
world (21 vs 0 for the rule policy). Three ideas were tested on GPU today and two were
rejected by their own results: recursion buys nothing (twice, on two worlds), and a
learned value model loses to the constant "always continue". What survived is smaller
and more useful: a filtered fact — **retry is the wrong default after a failure** — and
a controller that now distinguishes three kinds of failure. The binding constraint is
no longer modelling. It is **data coverage** (the corpus contains 2 of 18 tools) and
**the absence of a live session** where actions actually fail.

---

## 2. What works, verified

| thing | evidence |
|---|---|
| Client launches, no black screen | STA fix + bundle patches; `START: UI thread apartment: STA` in the log |
| Bundle patcher reproducible | `--bundle-selftest` → IDENTICAL, 13 rules |
| Agent loop end-to-end | `inv.list ok advanced 141ms -> 23 items` against the live bridge |
| Planner completes quests | sim: **21 quests / 21 objectives** in 500 steps; random 0, rule 0 |
| Learned planner matches the teacher | `NNPlanner` 21 vs teacher 21; val 1.00 on held-out objective types |
| Knowledge is editable, not memorised | store + `knowledge.remember/forget`; a learned `npc_map` changes the plan with no retraining |
| Verifier is calibrated | val acc 1.0000, ECE **0.0075** after resume rounds (was 0.3105 at scratch) |
| Failure taxonomy | 3 classes, tested against the real error strings |
| Test suite | **73 checks, 0 failed** (`python -m agent.smoke_verifier`) |

**18 tools** registered in `data/capabilities.json` (single source; the encoder's action
head is derived from it, so any 16-tool model is now shape-incompatible).

---

## 3. What is proven dead (do not rebuild these)

**Recursion (TRM, K=12, deep supervision).** Two runs:

| world | data | result |
|---|---|---|
| deterministic | 89,152 rows | 1.0000 at **k=1**, ECE 0.000 |
| noisy (15% refusal, stale/partial obs) | 105,522 rows | 0.8830 at **k=1**, flat to k=12, **+0.0000 gain** |

Twelve passes of a GRU cell produced the same answer as one. The paper's claim needs a
task where one pass is insufficient; tool selection is a lookup.

**The learned value model.** 1,500 counterfactual states, 11 features:

| policy | regret (gain left on the table) |
|---|---|
| always continue | **0.058** |
| always retry (what shipped) | 0.133 |
| learned model | 0.062 — *does not beat the best constant* |

`models/decision_value.onnx` exists but **is not wired in**. It did not earn its place.

**The "first failure → retry" rule.** 2.3× worse than continuing, in the simulator; and
in the real client, 0 of 9 retries worked (because they were retrying an absent client).

---

## 4. What is blocked, and by what

1. **The corpus covers 2 of 18 tools.** The noisy corpus is 62.1% `farm.start`, 37.9%
   `quest.advance` — no `nav.goto`, no reads. A model cannot choose an action that never
   appears in its data. This is a generator bug in `sim/rollout.py`, not a model problem.
2. **No live session.** 41 real steps exist, 15 failures, and **14 of those 15 are
   structural** ("no client instance"), so they cannot rank recovery strategies. The
   reality check refuses to draw a conclusion below 30 genuine failures, and correctly.
3. **The simulator is deterministic given the features** (until `--noise`), so scores
   there measure the world, not the agent. Every number must now be quoted at `--noise`.

---

## 5. The plan, in order

### P0 — fix the corpus (no GPU needed, highest value)
Make `sim/rollout.py` exercise the whole action space: emit `nav.goto` when the
objective names a map, record read-only calls, and let the planner pick `replan` when
the current quest is unreachable. Regenerate, check the class histogram.
**Done when:** ≥5 tools each ≥5% of rows. **Falsifier:** if the planner still only ever
emits two tools, the planner is the bottleneck, not the generator.

### P1 — one real session (needs the client running)
20–30 steps of `farm` / `go to` / `open inventory` with the loop attached, then
`python ai/reality_check.py`.
**Done when:** ≥30 genuine (non-structural) failures. **Falsifier:** if the real ranking
contradicts the simulator, the simulator's noise model is wrong and gets fixed before
any further training.

### P2 — the decision model, second attempt
Only after P0 gives label variance. Same harness, same metric (regret), same rule:
it ships only if it beats **both** constants.
**Falsifier:** if it still loses, the decision is not learnable from these 11 features
and the next move is richer features (tool id, error class, history), not a bigger net.

### P3 — plan chunks in the runtime
`plan_chunk.onnx` (1.79M, exact 0.9682, per-horizon 0.9859/0.9820/0.9748) predicts the
next 3 tools. Wire it as a *hint* the controller can veto, and measure whether it
reduces replans per quest. **Falsifier:** if replans don't drop, the chunk is decoration.

### P4 — the ladder (value → search), only if P2 succeeds
Counterfactual forks already exist. The next rungs are a value function trained on
forks, then 1-ply search at inference. Do not start this before P2 has a positive result.

---

## 6. Code map

```
dofus-network/
  agent/
    autopilot.py        the loop: plan → execute → verify → decide (owns autopilot.lock)
    planner.py          KB → plan. Deterministic translation (the *teacher*)
    planner_nn.py       same interface, tool choice from models/planner_student.onnx
    controller.py       agent 2: continue/retry/recover/replan + error taxonomy
    verifier.py         did the step work? rules + optional ONNX head
    knowledge_store.py  discovered facts (provenance, disputes, expiry) — not weights
    executor.py         local / sentinel / directive backends
    schema.py           18 tools, validated (registry = data/capabilities.json)
  sim/
    world.py            symbolic world; noise=True adds refusals + stale/partial obs
    run.py              planner | nn | random | rule → quests per 1000 steps
    rollout.py          behavioural corpus (features → tool → did it advance?)
    decisions.py        counterfactual corpus: fork the world, label the best decision
  ai/
    train_planner_student.py   teacher → student (26 KB)
    train_value.py             value model + regret vs constants
    self_improve.py            resume → train N seconds → score acc & ECE → keep best
    idle_trainer.py            trains only when no loop holds the lock; candidate only
    kaggle_report.py           status of every kernel; --fetch pulls results
    reality_check.py           real failures → can they rank recovery strategies?
  models/                planner_student, verifier_head, plan_chunk, decision_value
  docs/AGENT2_DESIGN.md  38 decisions (D25–D38) with the numbers behind them
  docs/KAGGLE_RUNS.md    generated: every kernel, status, headline result
```

---

## 7. How to run it

```bash
# tests
cd dofus-network && python -m agent.smoke_verifier

# simulator scoreboard
python sim/run.py --policy planner --level 60 --steps 500
python sim/run.py --policy rule    --level 60 --steps 500

# corpora
python sim/rollout.py --parallel 6 --minutes 5 --noise --out data/hard_sft
python sim/decisions.py --states 1500 --out data/decision_sft

# training (local, CPU, seconds)
python ai/train_planner_student.py
python ai/train_value.py --epochs 300
python ai/self_improve.py --task verifier --rounds 3 --round-seconds 40

# kaggle
python ai/kaggle_report.py --fetch --md

# reality
python ai/reality_check.py
```

Kaggle: account `tajdinenizar`, credential at `~/.kaggle/kaggle.json` (outside the repo).
Private datasets: `thx-02-planner-corpus`, `thx-02-seq-corpus`, `thx-02-hard-corpus`.
Pushing a new version of a *queued* kernel returns 409; the kernel `id` must equal its
real slug. Datasets mount at `/kaggle/input/datasets/<owner>/<slug>/`.

---

## 8. Open questions nobody has answered yet

1. Does the real game rank recovery decisions the way the simulator does? **Unknown.**
   Needs P1.
2. Is the planner's curriculum (fewest objectives first) good, or just convenient?
   Never compared against any alternative.
3. The encoder has never been retrained for 18 tools. Its action head is now stale.
4. The `knowledge_store` has never been exercised by a live session — no discoveries yet.
5. `--noise` parameters (0.15 / 0.20 / 0.10) were chosen by hand, not fitted to the
   client's observed failure rate.
