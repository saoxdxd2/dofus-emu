# The strategist plan — two brains, many heads

**This is the remade plan for the "third agent" and for language, knowledge and
fight decisions.** It does not replace the verified client/network state in
`PLAN.md`; it replaces the part of that plan that said "add a third model" as if
the actor, the planner and the advisor were three chat agents.

Last rewritten: 2026-10-24.

---

## 0. The three questions, answered up front

**1. Should the three agents become one model?**
Not now. Merge the **planner (agent 2) and the advisor (agent 3)** into one
**strategist** with several heads, and keep the **actor (agent 1)** separate. The
strategist is one interface, one shared text encoder and several small task
heads — so it is already "one model" in the sense that matters, without becoming
one generative model that hallucinates tool calls and cannot be safety-audited.

**2. What does the planner need to know about NPCs and monsters?**
Every `state.snapshot` now carries the NPCs, monster groups, exits and
interactives of the current map. The planner filters them against the active
objective cheaply; only when the actor is about to fight does it run a full
`fight.estimate` on that group. The actor never needs the whole map.

**3. What about understanding French dialogs and quests?**
The client already resolves dialog text: `gui.npcDialogUi.currentMsg` and
`.replies` hold the formatted French question and answers, and
`gui.npcDialogHandler.replyIds` holds the ids the server expects. Choosing a
reply is a **ranking** problem, not a generation problem. It needs a multilingual
encoder trained on outcomes, not a generative LLM in the loop. Papycha is a
reference source for mechanics and guides; its content signal forbids AI
training, so it is RAG-only with attribution.

**TPU** was never dropped; it was never started. It is not useful for the 48.6K
actor. It is useful for combat self-play and hard-world search, and it is now
split into two entries: combat self-play as soon as the encounter environment is
vectorised, and quest/tool evolution only after the Touch corpus exists.

---

## 1. Why two brains and not one

The obvious idea — one big model that plans, talks and fights — fails on four
measured constraints:

| constraint | what happens with one model |
|---|---|
| latency | the actor runs at 62 ms/call INT8. A generative model that also reasons about the world is 10-100× slower, and the game does not wait. |
| labels | there is no dialog-outcome or fight-outcome dataset yet. A single end-to-end model cannot be trained without one; the modular shell generates the labels while it runs. |
| safety | the claim guard, no-buy rule and fail-closed egress are auditable code. A model may propose; the shell disposes. A monolithic model moves safety inside weights. |
| debugging | when a step fails today, the verdict says `noop`/`failed`/`blocked` and names the precondition. One model gives a token stream. |

The research points the same way, with a caveat:

* **MAST** (Why Do Multi-Agent LLM Systems Fail?) finds most multi-agent failures
  are coordination and verification failures, not raw model capability. That
  argues against chatty agents; it does **not** argue against a typed
  planner/executor interface, which is what this is.
* **Cognition, "Don't Build Multi-Agents"**: agents that cannot share full
  context make conflicting assumptions. Here the interface is an explicit
  observation/action contract, not a conversation, so context is shared by
  construction.
* **Anthropic's multi-agent research**: multi-agent systems use roughly 15× the
  tokens of a single agent and help mainly with parallel read-heavy work. The
  strategist is not parallel read-heavy work; it is one slow decision per map or
  fight.
* **TRM** (7M params, strong on ARC-AGI-1, weak on ARC-AGI-2) supports the useful
  half of the intuition: tiny specialised recursive nets can be very strong in a
  narrow domain. It does not support replacing the shell with one small model.
* **HiPER** (hierarchical plan/execute) is the closest published shape: a slow
  planner over abstract actions, a fast executor over tokens. That is exactly the
  two-brain split.
* **MoE / task-specific parameters** are the right answer *if* the heads
  conflict during training. They have not been trained yet, so adding MoE now is
  a guess. Keep the heads separate first; merge the trunks when the gradients
  say so.

The decision:

```
                     ┌──────────────────────────────────────────┐
  natural-language   │  STRATEGIST (slow, Python, one interface) │
  goal ─────────────►│  deterministic shell                      │
                     │    quest graph · schedule · guard · budget│
                     │  heads                                    │
                     │    dialog ranker   (multilingual encoder) │
                     │    fight value     (sim + calibration)    │
                     │    loadout value   (sim + advisor)        │
                     │    verdict/residual/drift (small MLP)     │
                     └───────────────┬──────────────────────────┘
                                     │ typed directive
                                     ▼
                     ┌──────────────────────────────────────────┐
                     │  ACTOR (fast, ONNX, frozen)               │
                     │  observation + goal → tool + args         │
                     └───────────────┬──────────────────────────┘
                                     │ bridge :8893
                                     ▼
                              Dofus Touch client
                                     │
                     observation v1 ─┴─► outcome v1 ─► labels
```

**The guard is not a head.** `FORBIDDEN_TOOLS`, the claim gate
(`_questClaimCheck`), the no-buy rule and the egress fail-closed check stay
outside every model and run before any directive reaches the actor.

---

## 2. What "one model" would actually mean here

The strategist can become a single model later without ever becoming a monolith:

* **Shared text trunk**: one multilingual encoder embeds the active objective,
  the dialog question, each reply, the quest step and retrieved guide text. The
  dialog ranker, objective resolver and guide retriever are heads over that
  trunk.
* **Shared numeric trunk**: one small MLP over the 12 schedule features
  (`ScheduleManager` already computes them), the character sheet and the fight
  estimate features. Verdict, residual and drift are heads over that trunk.
* **Actor stays separate**: it is already trained, deployed and fast. Distilling
  it into the strategist is a separate experiment with its own gate.

This is "two brains, many heads". A single generative model is explicitly **not**
the target; the target is one deployable artifact with several small heads, which
can be quantised and kept under the memory budget.

---

## 3. Knowledge spine: build it once

The language layer is only as good as the entities behind the words. Today the
data is split across four sources with different ids and different coverage:

| source | what it has | what it lacks | authority |
|---|---|---|---|
| live client DB (`gui.databases`) | Touch-specific quests, objectives, achievements, items, monsters | needs a read-only harvest; tables are not documented | **highest** for Touch |
| `data/cache/quests_full.json` | 1,976 quests, 5 languages, steps, objectives, `{monster,182}` placeholders | misses Touch-only quests (e.g. tutorial 3841) | second |
| `data/cache/achievements_full.json` | 2,781 achievements, names/descriptions, `objective_ids` | **no objective details** | second |
| papycha.fr | boss mechanics, seasonal events, quest walkthroughs, French | robots/content-signal: `ai-train=no`; auth-walled REST | reference only |

### 3.1 Canonical store

Build `knowledge/dofus_knowledge.sqlite` plus a JSONL export, with every row
carrying provenance:

```sql
entities(kind, id, name_fr, name_en, name_es, name_de, name_pt,
         source, source_version, verified_live, fetched_at)
quests(quest_id, name_json, level_min, level_max, category_id, is_event,
       is_dungeon, is_party, start_json, source)
quest_steps(quest_id, step_index, step_id, name_json, description_json,
            optimal_level, source)
quest_objectives(objective_id, quest_id, step_index, type_id, type_name,
                 text_json, map_id, coords_json, params_json, source)
achievements(achievement_id, name_json, description_json, points, level,
             category_id, source)
achievement_objectives(achievement_id, objective_id, details_json, source)
monsters(monster_id, name_json, is_boss, grades_json, source)
npc_placements(npc_id, map_id, cell, source)
guide_refs(id, source, url, title, lang, text, license, fetched_at)
```

Rules:

1. **One entity id per thing.** `objective_id` is the global id the client uses;
   `quest_id` + `step_index` order it; `npc_id` is the template id, not the actor
   id (the actor id changes every map load — that confusion already cost a live
   session).
2. **Precedence on conflict**: live client DB > live capture > DofusDB cache >
   guide text. A lower source never overwrites a higher one; it is stored as a
   separate row with its provenance.
3. **Placeholder resolution is a first-class operation.** `{monster,182}`,
   `{npc,119}`, `{item,1746}` become typed references with localized names. The
   resolver is generic (kind + id), not per quest.
4. **`verified_live` is earned**, not assumed. A row becomes live-verified when a
   captured session shows the entity behaving as the row says.
5. **No raw payloads in the store.** Store the fields the heads need, not the
   server message.

### 3.2 Achievement enrichment

`achievements_full.json` has `objective_ids` but no objectives. The client DB has
an achievement-objective table; harvest it in the same read-only pass as task
#27. Until then, the achievement layer is a name + description index, and the
planner must not pretend it knows an unenriched objective.

### 3.3 Papycha as a reference, not a corpus

The site's `robots.txt` declares:

```
Content-signal: search=yes, ai-input=yes, ai-train=no, use=reference
```

The French line says indexation and answers **with reference** are allowed;
training is refused. Therefore:

* build `data/cache/guide_refs/` from the public sitemap
  (`https://papycha.fr/wp-sitemap.xml`), one cached page per URL, with title,
  language, URL, fetch date and attribution;
* use it at inference time through retrieval, and cite the page in the
  strategist's explanation;
* **never** copy its text into a training corpus, and never bypass the REST 401;
* rate-limit the harvester and respect retries/backoff; a reference cache is not
  a scraping race.

This is also the right product behaviour: a guide is evidence, not a label. The
labels come from what the game did.

---

## 4. Observation contract v1

The contract is the thing that must be right from the start; every head reads it
and every label is joined to it.

| field | contents | state |
|---|---|---|
| `map` | `map_id`, cell, x/y, `neighbours`, `exits` (direction, cells, tutorial `blocked`), `npcs` (template id, actor id, cell), `interactives` (elem id, cell, skill uid) | live |
| `map.groups` | per group: actor id, cell, `monster_ids`, per-member `{monster_id, level, is_boss, is_mini_boss}`, `member_count`, `levels_known`, `total_level` (null unless complete), `max_level`, `scale_level` | **added 2026-10-24, offline-tested** |
| `dialog` | `open`, `npc_id`, `name_id`, question `message`, `replies[{index,text,reply_id}]`, `reply_count`, `ids_ready`, `text_ready` | **added 2026-10-24, offline-tested** |
| `character` | level, HP/AP/MP, stats, resistances, known spells and grades, equipped gear | partial (level/HP/AP/MP); sheet next |
| `quests` | active quests, current objective, status, counters, client DB objective type | live |
| `fight` | board snapshot when in a fight (`fight.state`) | live |
| `tutorial` | allowed crossings, cells, NPC | live |

Design constraints:

* **Unknown stays unknown.** `total_level` is null unless every member level is
  known; absent levels are not zero. The same rule applies to prices and
  estimates.
* **Caps are explicit.** 20 groups, 12 replies, 20 NPCs, active quests only.
  A snapshot is an observation, not a transcript.
* **`groups` and `npcs` are always present**, so the planner scans every map
  without a special "scan" action. The actor does not need them to execute; the
  planner uses them to decide whether a fight is worth starting.
* **Text is already resolved.** The page never re-implements text-id lookup; it
  reads the strings the client UI already formatted. That is why the dialog
  contract is cheap and robust across bundle updates.

### 4.1 The live validation checklist for M0

1. Restart the app fully (the agent is injected at WebView2 document creation;
   `debug.reload` loads the old captured script).
2. `debug.version` returns `2026-10-24c`.
3. On a map with monsters, `state.snapshot.map.groups` is non-empty and
   `debug.groups` lists the same ids.
4. Talk to an NPC with more than one reply: `snap.dialog.message` is French text,
   `replies` are non-empty and each has a `reply_id`.
5. Send `quest.advance` with `reply_text` copied from the snapshot. It must
   resolve to the same index the client would send; `debug.talk_state.last` must
   include `question`, `reply_text` and `reply_id`.
6. Keep one `autopilot.jsonl` row containing a non-null dialog observation.

---

## 5. Action and outcome contract v1

Actions are still the existing tool calls. Three additions matter:

| action | addition | why |
|---|---|---|
| `quest.advance` | `reply_text` (exact text from the observation) | text is stable, index is positional; the client resolves text back to the id |
| `fight.join` / `farm.start` | group id is the actor id | already fixed; the map key fallback is gone |
| `fight.estimate` | read-only estimate, never sends | the planner needs a number before it commits |

The outcome row is the label. One row per step, written by `agent/autopilot.py`
and the recorder:

```json
{
  "session": "...",
  "step": 412,
  "observation": {"map_id": 147064834, "cell": 255, "groups": [...],
                  "dialog": {"message": "...", "replies": [...]},
                  "quest": {"id": 3841, "objective_id": 13922}},
  "action": {"tool": "quest.advance", "args": {"reply_text": "Oui"}},
  "guard": {"ok": true, "reason": null},
  "result": {"ok": true, "reply_id": 41},
  "outcome": {"objective_before": 13922, "objective_after": 13924,
              "quest_step_advanced": true, "source": "live"},
  "verdict": {"verdict": "advanced", "residual": 1.0, "source": "rule"},
  "language": "fr",
  "provenance": {"game_build": "1.74.9", "agent_build": "2026-10-24c"}
}
```

Free labels, in order of availability:

1. **dialog**: objective id/status before vs after, quest step change, dialog
   closed, elapsed. A reply that advances is positive; one that closes the
   dialog without progress is negative; an unchanged state is `noop`, not
   negative.
2. **fight**: result, turns, HP loss, deaths, drops, XP. The estimate is a
   falsifiable claim; the residual is observed/predicted.
3. **map**: from/to map id, transition success, time.
4. **listing**: `state: open`, never kamas earned.

`source` is `live` or `sim`; training only counts `live` for gold labels, and
simulation labels are explicitly marked. Papycha is not a source value at all.

---

## 6. The dialog/quest language policy

### 6.1 It is a ranker, not a chatbot

Input:

```
active quest title + step description + objective text (French)
current dialog question (French)
candidate replies (French, already formatted)
world context: NPC name, map, level, inventory flags, party
```

Output: one of the candidate reply ids, or **abstain**. Abstaining is a real
action: it leaves the dialog open, logs the case, and the planner can retry after
a map change or ask for a human/guide lookup.

Why ranking:

* the candidate set is small (1-6 replies), so a cross-encoder is enough;
* labels are free from objective deltas;
* a wrong reply can close a dialog but cannot invent a quest state; the risk is
  bounded;
* a generative model adds latency, hallucination and an unbounded action space
  for no gain.

### 6.2 Three stages

**Stage 0 (now, deterministic).** Use the observation:
* one reply → take it (no choice exists);
* multiple replies → score each reply by normalized token overlap with the
  active objective text and step description, in French, using the client's own
  text; if the top two tie, abstain;
* if the dialog has no text yet, wait (the client fills `replies` a moment after
  `isInDialog` flips — the existing handler already refuses in that gap).

This is generic, not per-quest. It exists to collect labels, not to be the final
policy.

**Stage 1 (multilingual cross-encoder).** Fine-tune a small multilingual encoder
(`paraphrase-multilingual-MiniLM-L12-v2`, `multilingual-e5-small`, or similar)
as a cross-encoder over `(quest context, question, reply)`. Train on live
outcomes and a small synthetic set from simulator quest steps. INT8 target
≤50 MB, ≤30 ms for a dialog with 6 replies. Benchmark the candidates on French
retrieval before choosing; do not assume an English-only ModernBERT checkpoint
transfers.

**Stage 2 (retrieval + optional small LLM).** When the ranker abstains and the
quest is a known guide case, retrieve the relevant papycha passage and use it as
extra context for the ranker. A small multilingual instruct model may summarise
the passage offline, but it does not drive the client directly; its output is
cited text. This is the only place a generative model is allowed, and it is
outside the control loop.

### 6.3 Quest/achievement understanding

The planner needs more than dialog: it needs to know what an objective means.

* Resolve `{kind,id}` placeholders against the knowledge spine.
* Translate `type_id` to the client's objective type (`QuestObjectiveGoToNpc`,
  `FightMonster`, `DiscoverMap`, ...), not to a guessed English label.
* Keep quest logic generic: the planner receives the objective's details and the
  available actions. No per-quest branch in code. Hard quests are for the model
  and the guide retrieval to solve.
* Achievements are a second objective graph; enrich them before treating them as
  goals.

### 6.4 Evaluation gates

* **D0**: 100 live dialogs with complete observation and `source=live` labels.
* **D1**: top-1 reply accuracy on held-out live dialogs beats the deterministic
  baseline by ≥10 points, with ≤10% abstain.
* **D2**: zero dialogs closed by an unsafe/ambiguous choice in the eval set; every
  abstain names its reason.
* **D3**: on a replay of 20 quests, the fraction of objectives advanced per
  dialog increases without a rise in `noop` steps.

---

## 7. Fight feasibility and loadout

### 7.1 What the planner does on every map

1. Read `map.groups` and filter by the active objective (a fight objective names
   a monster id; a "defeat X" objective may accept any group containing it).
2. If a candidate is relevant, call `fight.estimate(group_id)`.
3. Compare `p_win` and expected HP loss with the policy thresholds.
4. Only then hand a directive to the actor. The actor is not told about groups it
   does not need.

This satisfies the request: the planner constantly watches NPCs and monsters, but
does not burden the actor with the whole map.

### 7.2 `fight.estimate` v0 — analytic, calibrated

The existing `combat/world_model.py` models a spell sequence against a target; it
is not a full encounter simulator. The first estimator should be honest about
that:

* inputs: character level/HP/AP/MP/resistances, known spells and damage, group
  members and levels, monster DB grades (HP, AP/MP, resists);
* features: effective HP, damage per turn by element, turns-to-kill, incoming
  damage per turn, initiative, group size, level gap, boss flag;
* output: `p_win`, `expected_hp_loss`, `expected_turns`, `confidence`,
  `model_version`, `missing` (which inputs were absent);
* calibration: fit a logistic/GBM calibrator on live fight outcomes; a raw
  heuristic is not a probability.

If the DB lacks a monster or the character sheet is incomplete, return
`confidence: low` and the missing fields. The planner treats that as "do not
start a fight unattended".

### 7.3 `fight.estimate` v1 — Monte Carlo

Once the encounter model exists, sample N=200 encounters: initial positions,
initiative order, enemy spell choices, player policy from `combat/` (solver or
tactical engine). Return the distribution, not just the mean. Cache by
`hash(character_sheet, spells, group_composition, encounter_model_version)` and
invalidate on any gear/level/spell change. This is the same cache discipline as
the planner's reasoning cache: structure cached, estimates recomputed.

### 7.4 Loadout and stats

The advisor already computes element choice, spell budgets, gear scores and
market reports (`advisor/`). The strategist adds the missing link:

* compute `Δp_win` for each candidate loadout under a kama/time budget;
* rank by `Δp_win` per kama, not by item score alone;
* produce a **plan** (quest reward, craft, market listing to watch), not a
  purchase;
* buying stays human: `hdv.buy` remains forbidden. A recommendation is not a
  transaction.

This is where agent 2 and agent 3 actually merge: the planner asks "what would
make this fight winnable?", the advisor answers with numbers, and the same
strategist schedules the quest that obtains the item.

### 7.5 Autonomy rule

* Fight only if `p_win ≥ threshold` (start at 0.90, tune on live data) **and**
  expected HP loss is below the configured fraction **and** no unknown monster
  data.
* A boss or wanted monster is opt-in, never the default.
* A failed estimate is not permission to try. It is a `blocked` verdict with the
  missing input named.
* Every fight writes its estimate before the action and its outcome after; those
  two rows are the calibration dataset.

---

## 8. Training factory and the TPU answer

### 8.1 Sources and allowed use

| source | use | training? |
|---|---|---|
| live sessions (`source=live`) | gold labels for dialog, fight, map, market state | **yes** |
| simulator / self-play (`source=sim`) | silver labels, pre-training, hard-case generation | **yes**, tagged and validated |
| evolution/search on remote GPU/TPU | plan structure and hard quests | **yes**, as search output, never as live policy |
| papycha / guides | inference-time retrieval, cited | **no** |
| raw server payloads | never; extract fields at the edge | **no** |

The rule from `FAILURE_TO_SUPERVISION.md` still holds: evolution/search runs
remotely, live serves a frozen ONNX. Nothing is trained against the real server
by brute force.

### 8.2 Models and deployment

| model | params / size | deployment |
|---|---|---|
| actor (existing) | 48.6K, 196 KB ONNX, INT8 25.4 MB with encoder | frozen, host |
| dialog cross-encoder | 30-50 MB INT8 target | host, per instance |
| fight/loadout value | <1M params, <5 MB | host, per instance |
| verdict/residual/drift | <1M params, <5 MB | host, per instance |
| optional guide summariser | small multilingual instruct, offline only | not in the control loop |

The 200 MB per-instance budget is the constraint. The actor + a small
multilingual encoder + three small heads fits; a large generative model does not,
which is another reason not to put one in the loop.

### 8.3 TPU split (this is the answer to "did we drop it?")

TPU was never dropped and never started. There is no TPU kernel in the repo; all
three Kaggle kernels (`kaggle/trm`, `kaggle/hard`, `kaggle/seq`) have
`enable_gpu: true`.

The correct split:

1. **Combat self-play on TPU first.** Vectorise the encounter environment
   (JAX/PyTorch-XLA): batch thousands of fights, train a value/policy head, use
   it to generate `fight.estimate` labels. This is the workload TPUs are for:
   many independent episodes, small per-step compute. It can start once the
   encounter model of §7.3 exists; it does **not** need the Touch quest corpus.
2. **Quest/tool evolution on TPU later.** Parallel search over plan structures
   and tool sequences, gated on the Touch-specific DB rows (task #27) so the
   simulator matches the live client.
3. **The 48.6K actor never needs TPU.** Its capacity curve is already flat; the
   bottleneck is labels, not FLOPs. Spending TPU on it would be waste.

GPU now: dialog cross-encoder fine-tuning, fight calibration, simulator dataset
generation, and the existing kernel work. TPU is a second phase with a named
entry point, not a vague promise.

---

## 9. Milestones and gates

| # | milestone | deliverable | gate |
|---|---|---|---|
| **M0** | observation v1 | `map.groups`, `snap.dialog`, `reply_text` in `quest.advance`; offline tests | live checklist §4.1 on a real session; `debug.version` = 2026-10-24c |
| **M1** | knowledge spine | client DB harvest, achievement objectives, canonical SQLite, placeholder resolver, papycha reference cache | every active quest objective resolves to typed entities; 0 unresolved placeholders on 50 live objectives |
| **M2** | dialog policy v0 | deterministic ranker + logging; labels from objective deltas | D0-D2; 100 live dialogs |
| **M3** | fight estimate v0 | analytic estimator + calibration + planner thresholds; `fight.estimate` tool | 50 live fights; calibration error <0.1; zero avoidable deaths |
| **M4** | strategist merge | one Python package/interface for planner + advisor; heads behind one `observe/decide` call | the four questions (reply, win, loadout, continue?) answered from one observation; no duplicated logic |
| **M5** | training factory | sim/self-play labels; dialog encoder; fight value head; TPU self-play pilot | dialog D1; fight calibration on sim vs live transfer; TPU run reproducible |
| **M6** | single-model distillation (optional) | distill actor + strategist heads into one artifact | matches shell on held-out live sessions; ≤200 MB; guard still outside |

M0 is deliberately first because every later gate depends on the observation and
the labels. It is also the smallest change that makes the current live loop
useful: even with no new model, `map.groups` + `snap.dialog` turn the autopilot's
verdicts into training rows.

---

## 10. What we deliberately do not do

1. **No generative LLM driving the client.** It has no labels, no audit trail and
   no latency budget.
2. **No per-quest hard-coding.** Quest logic receives details and generic
   actions; hard quests are solved, not enumerated.
3. **No unattended buying.** `hdv.buy` stays forbidden; listings are supply, not
   revenue.
4. **No training on papycha.** Reference only, with attribution.
5. **No merging the actor into the strategist before labels exist.**
6. **No TPU for the tiny actor.**
7. **No raw payload logs.** Extract at the edge; store the contract.
8. **No "unknown = 0".** Missing level, price or monster is `null` and causes an
   abstain.

---

## 11. Decisions needed

1. **Accounts** — unchanged: real accounts in `accounts.txt`, one per slot. The
   update wiped guests; automated signup is rejected for CAPTCHA/CloudFront and
   ban risk.
2. **Multilingual encoder choice** — benchmark `paraphrase-multilingual-MiniLM-L12-v2`
   against a multilingual E5-small on French dialog ranking before committing.
   The metric is top-1 reply choice on held-out live dialogs, not English MTEB.
3. **Papycha attribution/rate** — how the UI should cite a guide page, and the
   crawl rate. The content signal allows reference, not training.
4. **Fight threshold** — start at 0.90 and tune on live outcomes; confirm whether
   a 5% expected death chance is acceptable for quest bosses (default: no).
5. **Patch 4** — `launchMapTransition` still does not match in 1.74.9. Port it or
   accept the unwatched transition.

---

## 12. Invariants added by this plan

1. **Text is an observation, not a program.** The model proposes `reply_text`;
   the client resolves it to a reply id; the handler sends it. Never parse text
   into a fabricated protocol message.
2. **Unknown is not zero.** A missing level, price or monster input is null and
   forces an abstain, not a default.
3. **One contract, one source.** `map.groups`, `snap.dialog` and `reply_text`
   have exactly one reader/resolver each; a second copy is a bug.
4. **Reference is not training.** A guide can inform a decision; it cannot
   become a label.
5. **The planner may estimate; only the server decides.** A win probability is a
   claim, and the outcome row is the answer.
6. **Every head abstains when its inputs are missing.** An abstain is a valid,
   logged decision; a guess is not.

---

## Appendix A — evidence used

* Client bundle 1.74.9: `gui.npcDialogUi.currentMsg` / `.replies` receive the
  formatted dialog; `gui.npcDialogHandler.replyIds` holds the ids;
  `_uiReplyHandler(index)` sends `NpcDialogReplyMessage{replyId}`.
* Client bundle: `GameRolePlayGroupMonsterInformations` carries
  `staticInfos.mainCreatureLightInfos` plus `underlings`, each with
  `creatureGenericId` and `staticInfos.level/isBoss/isMiniBoss`. The tooltip
  reads exactly those fields.
* `data/cache/quests_full.json`: 1,976 quests with steps and objectives,
  5 languages, placeholder text.
* `data/cache/achievements_full.json`: 2,781 achievements, `objective_ids`
  without objective details.
* `data/cache/monsters_full.json`: 2,000 monsters with per-grade HP/AP/MP and
  elemental resistances.
* `data/cache/papycha_pages/` is empty; `navigation/quest_graph.py` fetches live
  articles but is not a harvester.
* No usable language checkpoint exists: `thx-01-encoder-train/model.pt` is 0
  bytes, `models/dofus-brain-native` is empty, no domain vocab file exists, and
  the THX notebook uses English-centric ModernBERT. The dialog encoder is a new
  training job, not a fine-tune of a working French model.
* All Kaggle kernels are GPU; no TPU code exists.
