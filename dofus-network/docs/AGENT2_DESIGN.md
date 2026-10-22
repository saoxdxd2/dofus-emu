# Agent 2 — what it is, how it is trained, and why

Status: design decided, first harness built (see §9). Written 2026-10-22 after the
session in which the client was fixed, the loop ran for the first time, and the
verifier/controller was wired.

The question this answers: **can a fully automatic agent really "do all the
quests", and what does it take to train the second agent to do it?**

---

## 1. The correction that changes everything

Earlier I said "do all the quests" was out of reach for the rule policy. That was
half right and half wrong, and the wrong half matters.

The knowledge base already contains the whole quest graph:

| fact | count |
|---|---|
| quests | 1,976 (all with `steps`) |
| quests with a start position (map + NPC) | 1,765 |
| quest objectives | ~17,000, in 13 machine-readable types |
| NPCs | 6,472 |
| monsters / items | 3,700 / 24,000+ |

And the objective types are decodable from their own text:

| type | text template | what it needs |
|---|---|---|
| 1, 9 | `Talk to {npc,447}` | go to NPC, talk |
| 4 | `Find the map: Wabbit laboratory` | go to map |
| 5 | `Find the {subarea,165} area` | go to subarea |
| 6 | `Defeat 1 {monster,182} in a single fight` | go to map, fight |
| 14 | `Beat x3 {monster,3453}` | fight 3 |
| 16 | `Defeat 8 x {monster,4128} on the {map,216531204}` | go to map, fight 8 |
| 0 | `Locate and neutralise Nomarow Transplent` | find + kill |
| 3 | `Take 1 {item,1746} to {npc,119}` | have item, go to NPC |
| 2 | `Show 1 {item,1871} to {npc,6309}` | have item, go to NPC |
| 12 | `Take 1 {monster,928} soul to {npc,6693}` | fight, carry, deliver |
| 17 | `Craft 1 {item,9977}` | craft |

**So the plan is derivable, not guessable.** The reason the rule policy fails is
not that the task is too hard for a small model — it is that a regex has no way to
read a graph. The knowledge is in the KB; what is missing is a component that
*uses* it.

That reframes agent 2: it is not "a bigger model that knows Dofus". It is a
**planner that reads the KB, chooses what to do next, and repairs the plan when the
world disagrees** — which is exactly the work this session did by hand.

---

## 2. What the research actually says

Four families were considered. Summary of what each is good for, not a survey.

**Voyager** (NVIDIA, 2023) — the canonical "fully automatic agent that improves
while playing". Three parts: an automatic curriculum (what to try next), a skill
library of executable code (what worked), and iterative prompting with *environment
feedback and self-verification* (why it failed). No gradient updates during play:
it improves by **writing to a library**, not by changing weights. Its lesson is
direct: lifelong improvement came from memory + curriculum, not from a bigger
policy. [Voyager]

**TRM — Tiny Recursive Model** (Samsung SAIL, arXiv:2510.04871, Oct 2025) — a
2-layer, ~7M-parameter network that beats far larger models on ARC-AGI and Sudoku
by *recursing on a latent answer* for many steps with deep supervision. Two things
transfer to us and one does not:
* transfers: recursion with deep supervision works when the task has a **verifiable
  answer** (a plan either executes or it does not) and when you can train on many
  refinement steps;
* transfers: small + deep-in-time beats big + deep-in-layers for this class;
* does not transfer: TRM is not an agent. It has no memory, no environment, no
  curriculum. It is a reasoner you call, not a thing that plays.
The right use here is **plan repair**: `(failed plan, state) → corrected plan` is a
small, verifiable, recursively improvable task. [TRM]

**ACT — Action Chunking Transformer** (Zhao et al., 2023) — imitation learning that
predicts a chunk of future actions from observations; the standard in robot
manipulation. It buys smoothness and handles multimodal demos. It is the wrong
tool here for two concrete reasons: it needs demonstrations (we have ~0 human
ones), and chunking *deliberately* suppresses per-step reactivity — while our
entire problem is reacting when the world changes mid-plan. [ACT]

**Cache-to-Cache / LatentMAS / DroidSpeak** (2025) — agents can exchange KV-cache
or hidden states instead of text, which is richer and lower-latency than a JSON
message. Interesting for the actor↔planner link, but full C2C needs identical
architectures and careful engineering. The cheap, honest version for two small
models on one CPU is a **shared trunk with two heads**, where the planner's last
hidden state is projected into the actor's conditioning vector. That is latent
communication without the research infrastructure. [C2C] [LatentMAS]

**DDQN** — value-based RL over a discrete action set. Rejected for three reasons
specific to this project, not in general:
1. it needs a fast, resettable environment; the real game gives ~100 steps/hour;
2. the action space is *combinatorial* (16 tools × typed arguments), so `Q(s,a)`
   over a flat action index cannot express `quest.advance(npc=447, quest=1935)`;
3. reward is sparse and delayed by minutes (a quest step completes long after the
   action that mattered), which is the regime where tabular/deep Q-learning needs
   enormous sample counts.

---

## 3. Decisions

**D25 — Agent 2 is a planner over the KB, not a policy over observations.**
It emits a *plan*: an ordered list of tool calls with concrete ids
(`quest.advance(quest_id=1935, npc_id=447)`), derived from the quest graph and the
current state. The learned part is **selection, ordering and repair**; the facts are
looked up. A 7M model cannot memorise 17,000 objectives and does not need to.

**D26 — The training target is a plan, not an action.**
One example = `(goal, state summary) → plan of 1–12 steps`. This is a text-to-text
(or text-to-structured) task, which is what the encoder is already good at, and it
is directly scoreable: the plan either completes the objective in the simulator or
it does not.

**D27 — Not DDQN, not ACT. TRM only as a plan-repair component.**
Reasoning above. If plan repair proves learnable, a TRM-style recursive refiner is
the right shape for it; until then a plain transformer head is the baseline it must
beat.

**D28 — Latent link, cheap version.**
Shared encoder trunk; planner head and actor head on top; the planner's hidden
state is projected into the actor's context. No JSON round-trip between the two
agents. This is D25's "communication" and it costs one projection matrix.

**D29 — Online means collect + remember, not backpropagate.**
Gradients during play are unsafe on a CPU box with a live account: a bad update
can wreck the actor mid-run and you cannot roll it back. What runs online is
**memory**: trajectories, verifier labels, human corrections, and the controller's
strategy table. Training happens offline on the accumulated data (Kaggle), and the
artifact is swapped in between sessions. This is Voyager's lesson, and it is also
what makes "trains while playing" true without being reckless.

**D31 — Knowledge is data the harness owns; the model learns to manage it.**
The model must not memorise the KB (24,000 items, 1,976 quests, 6,472 NPCs) — it
would be huge, stale on the next patch, and impossible to correct. Instead:
* facts live in `agent/knowledge_store.py`, an append-only log with provenance;
* the model gets two tools — `knowledge.remember` and `knowledge.forget` — and
  learns *when* to use them, which is a small, transferable skill;
* the planner reads the store before planning, so a discovery changes behaviour
  **without any training**;
* the store stays small by construction: only discoveries are stored (never a copy
  of the cache), weak sources cannot overwrite strong ones, three disputes delete a
  fact, and unused facts expire.

This is the cost argument in one line: a fact is a row, not a weight.

**D32 — Training is free because it runs when nothing else does.**
The loop writes `autopilot.lock` while it runs; `ai/idle_trainer.py` refuses to
start if that lock exists or a client bridge is answering. When the loop ends, the
machine is idle and the run has just produced data — that is the only moment
training costs nothing. It writes `verifier_head.candidate.onnx`, never the live
artifact: adopting a model is a deliberate act.

**D33 — The tool choice is learned, not an if-chain.**
`plan_objective` used to be `if t in MAP_TYPES ... elif t in TALK_TYPES ...`. That is
a hardcoded rule: correct until a new objective type or a renamed parameter makes it
silently wrong, with no way to improve except editing code. It is now a small
network (`ai/train_planner_student.py`, 25 KB, 33 features → 5 tools) trained on the
table's decisions and served by `agent/planner_nn.py`. The table stays only as the
*teacher* that generates data; at runtime the model chooses.

Measured: **21 quests vs 21** — the student matches the teacher in the simulator,
and 1.00 accuracy on objective types it never saw (3, 6, 14 held out).

The lesson from getting there is worth keeping: the first student scored **0** while
the teacher scored 21, with 99% tool accuracy. Two causes, both data, not model:
a missing feature (whether the needed item is already carried) and a corpus with
only one state per objective, so the decision boundary was invisible. A model cannot
learn a distinction that is not in its inputs.

**D34 — Every model resumes, under a time budget, and is scored on calibration.**
`ai/self_improve.py` runs rounds: resume from the last checkpoint → train for N
seconds → score on held-out data → keep the best by `accuracy - 0.5 × ECE`.
The penalty exists because accuracy alone rewards a model that is right and certain
on the easy half; ECE measures that, and temperature scaling (fitted on validation)
is the cheapest correction.

Measured on the verifier head, 3 rounds × 40 epochs:

| round | accuracy | ECE | calibrated ECE | T |
|---|---|---|---|---|
| 1 (scratch) | 0.9833 | **0.3105** | 0.2086 | 0.55 |
| 2 (resume) | 1.0000 | 0.0253 | 0.0074 | 0.55 |
| 3 (resume) | 1.0000 | 0.0075 | 0.0012 | 0.55 |

Accuracy rises and overconfidence collapses across rounds — the claim in the paper,
reproduced on this task. The harness prints a warning above ECE 0.15 and refuses to
let such a model veto actions.

**D35 — Recursion is rejected on this task; chunking is kept.**
Two GPU runs on a T4, 89k behavioural rows, measured rather than argued:

| model | params | result |
|---|---|---|
| TRM (K=12 recursion, deep supervision) | 4.23M | **1.0000 at k=1**, ECE 0.000 at every step |
| plan chunk (GRU, 3 states → 3 tools) | 1.79M | exact 0.9682, per-horizon 0.9859 / 0.9820 / 0.9748 |

The TRM result is a clean negative and worth stating plainly: **recursion bought
nothing because the first pass was already perfect.** The paper's claim needs a task
where one pass is insufficient; single-tool classification is a lookup, and a 4M
model solves it in one step. Twelve recursions were 12× the compute for zero gain.

The chunk model is kept because it answers a different question and degrades the way a
real plan does: 98.6% on the next call, 97.5% three calls out. That 1.1-point decay is
the honest cost of planning ahead.

**D36 — The simulator is now the bottleneck, not the model.**
Both runs hit ~1.0, which does not mean the agents can play. It means the symbolic
world is *deterministic given the features*, so a small model can learn its rule
exactly. This is the same trap as the verifier's first 1.0: a perfect score on a task
whose answer was in the inputs.

Therefore the next work is **not** more parameters or more rows. It is making the world
hard enough to be worth learning: stochastic success, delayed observations, and
partial state — the three things that make the real game hard. A model that scores
0.97 on a noisy world is worth more than one that scores 1.00 on a deterministic one.

**D30 — The simulator is the training ground, the game is the exam.**
A symbolic simulator over the KB (maps, NPCs, monsters, inventory, quest
objectives) generates thousands of episodes per hour on CPU. The game generates
about a hundred steps per hour and can ban you. Train and evaluate at scale in the
simulator; validate on real sessions, never train on them directly.

---

## 4. Architecture

```
                 goal ("do all the quests")
                          │
                  ┌───────▼────────┐
                  │  Curriculum    │  which quest next? level, prereqs,
                  │  (planner)     │  distance, reward/effort
                  └───────┬────────┘
                          │ quest_id
                  ┌───────▼────────┐
                  │  Quest → plan  │  objectives → steps → tool calls
                  │  (KB lookup)   │  with concrete ids (deterministic)
                  └───────┬────────┘
                          │ [ToolCall, ...]
                  ┌───────▼────────┐
                  │  Controller    │  execute, verify, retry / recover /
                  │  (agent 2)     │  replan / abort   ← already built
                  └───────┬────────┘
                          │
                  ┌───────▼────────┐
                  │  Actor         │  30M encoder, picks the exact call
                  └───────┬────────┘
                          │
                    game / simulator
```

The learned pieces are the **curriculum** (which quest, in what order) and the
**repair** (what to do when a step fails). The `quest → plan` translation is
deterministic and testable — it should not be learned, because it is already
correct by construction.

---

## 5. Data format

One JSON object per line, in `data/planner_sft/{train,val}.jsonl`:

```json
{
  "id": "q1935:s2",
  "goal": "do all the quests",
  "state": {
    "level": 42, "map_id": 185862149, "kamas": 12000,
    "inventory": {"1746": 1}, "quests_done": [18, 22],
    "quests_active": [1935]
  },
  "plan": [
    {"tool": "nav.goto", "args": {"map_id": 185862149}, "why": "objective 4: find the map"},
    {"tool": "quest.advance", "args": {"quest_id": 1935, "npc_id": 447}, "why": "objective 1: talk"}
  ],
  "outcome": {"completed": true, "steps": 2},
  "source": "sim"
}
```

`source` is `sim` / `live` / `human`, with the same weighting discipline as the
verifier corpus (human 1.0, live 0.7, sim 0.4). A simulated plan that never ran in
the game must not outvote a human correction.

---

## 6. Training pipeline

```
KB (quests, npcs, maps)                 live autopilot.jsonl
        │                                       │
        ▼                                       ▼
 sim/episodes  ──►  planner_sft (sim)  ──►  merge + weight  ──►  train
        │                                       │                 │
        └──── reward: quests/hour, steps, cost ─┘                 ▼
                                                          planner.onnx (INT8)
```

Stage 1 — **SFT on simulator plans** (deterministic teacher: the KB translator).
The model learns the mapping `state + objective → steps`, including which ids to
use. Cheap, unlimited data, verifiable.

Stage 2 — **preference tuning on outcomes**. Same state, two plans (teacher vs
model, or model at two temperatures); the better one is the one that completes more
objectives per step in the simulator. DPO, not PPO: no reward model to train, no
rollout infrastructure.

Stage 3 — **plan repair (TRM candidate)**. Dataset: `(plan, failure, state) →
fixed plan`, generated by the simulator by injecting failures (wrong target,
missing item, unreachable map). This is the small verifiable task where recursion
with deep supervision plausibly wins.

Stage 4 — **live validation**. One session per week on the real client, scored by
the verifier; the sim score is never reported as the product score.

---

## 7. Harness (built, see §9)

`sim/world.py` — symbolic world over the KB:
* state: map, level, kamas, inventory, quests done/active, step progress;
* `step(tool, args)` applies a tool the same way the client would, with explicit
  preconditions (must be on the map, must have the item, must be able to fight);
* rewards: objectives completed, quests completed, minus steps and kamas spent.

`sim/run.py` — episode runner + baselines:
* random policy (lower bound),
* rule policy (the current one),
* planner (this design).

The harness exists so a change can be scored in seconds instead of a live session.

---

## 8. Metrics and falsifiers

| metric | why | target |
|---|---|---|
| quests completed / 1,000 steps | the product metric | > 3× rule policy |
| steps per objective | plan quality | ≤ 2.5 |
| replans per quest | plan robustness | < 0.5 |
| plan validity (ids exist, prereqs met) | correctness before execution | > 99% |
| sim→live transfer | honesty | live ≥ 50% of sim |

Falsifiers, written before the work:
* if the planner cannot beat the rule policy **in the simulator**, the whole
  approach is wrong and no amount of training will help;
* if plan validity is below 99%, the teacher is wrong, not the model;
* if sim→live transfer is under 50%, the simulator is lying and must be fixed
  before any model is trained on it.

---

## 9. What exists after this session

* `agent/planner.py` — quest graph → plan, with objective-type translation.
* `sim/world.py` — symbolic world + `step()` with preconditions.
* `sim/run.py` — episode runner and baselines, prints quests/1,000 steps.
* `ai/build_planner_sft.py` — simulator → planner SFT corpus.
* `python -m agent.smoke_verifier` covers the planner/sim wiring.

Everything above is offline and CPU-only.

### Measured, first run

`python sim/run.py --policy <p> --level 60 --steps 500`:

| policy | objectives | quests | quests / 1000 steps |
|---|---|---|---|
| planner (this design) | 21 | **21** | 42.0 |
| random | 1 | 0 | 0.0 |
| rule (the app today) | 0 | 0 | 0.0 |

The planner also emits only ids that exist (0 invalid in 30 sampled steps), so the
plans are executable rather than plausible.

That is the falsifier from §8 answered: **the planner beats the rule policy in the
simulator**, by a margin that is not a rounding error. It does not yet prove the
plans survive the real client — that is the next measurement, and the one that
matters.

### What this does *not* claim

* The simulator abstracts movement, combat and drops. 21 quests in 500 steps is a
  *planning* score, not a play-through score.
* Unsupported objective types (areas, crafting, escorts) are skipped rather than
  faked; the planner simply does not choose those quests.
* No model has been trained on this corpus yet. The corpus exists
  (`data/planner_sft`), the teacher is deterministic, and the next step is a
  student that reproduces the teacher's plans from a state — then improves on it
  where the teacher is wrong.

---

## References

[Voyager] Wang et al., *Voyager: An Open-Ended Embodied Agent with Large Language
Models*, NVIDIA, 2023.
[TRM] Jolicoeur-Martineau, *Less is More: Recursive Reasoning with Tiny Networks*,
Samsung SAIL Montréal, arXiv:2510.04871, 2025.
[ACT] Zhao et al., *Learning Fine-Grained Bimanual Manipulation with Low-Cost
Hardware*, RSS 2023.
[C2C] *Cache-to-Cache: Direct Semantic Communication Between Large Language
Models*, 2025.
[LatentMAS] *LatentMAS: end-to-end latent collaboration between LLM agents*, 2025.
