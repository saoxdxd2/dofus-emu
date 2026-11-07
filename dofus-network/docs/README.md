# Docs map

Twenty-three documents in `dofus-network/docs/` besides this map. The benchmark/eval JSON the
architecture docs cite live in `dofus-network/docs/benchmarks/`; those are data, not documentation.

**"How do I run it / what's actually wired?" → `CURRENT.md`.**
**"What is broken or unproven?" → `KNOWN_GAPS.md`.**
**"What is true right now, and what happened this session?" → `resume.md`** (repo root).
**"What is the AI, shipped vs target?" → `AI_ARCHITECTURE.md`.**

| document | what it answers | still current? |
|---|---|---|
| **`CURRENT.md`** | How to run it: services/ports, the 7-stage loop, think-token pacing, policy modes, model inventory, combat pipeline, the 28 tools | **yes — the running-system map** |
| **`KNOWN_GAPS.md`** | What is *not* true yet, each with evidence (forecaster dark, no vision, live fight, test-gate history) | **yes — honest limits** |
| **`AI_ARCHITECTURE.md`** | What the AI is: encoder, planner, verifier, knowledge — shipped vs target | **yes — canonical AI map (2026-10-27)** |
| `ARCH_REASSESSMENT.md` | The architectural argument: evidence, nine sections, ordered work list | reasoning; its status column is superseded by `CURRENT.md` |
| `BASELINE.md` | The recorded live baseline required by criterion (5) of Full Auto | yes — a measurement, not status |
| `BRAIN_DAEMON.md` | One Python process for all instances: `/brain/*` API, per-instance threads, locks, host integration | yes — implemented 2026-10-27 |
| `ENTITY_POINTER_ARCHITECTURE.md` | Target 80M/40M/30M entity-pointer brain, pointer actions, sub-goal stack, Kaggle training and gates | design, not shipped |
| `PLANNER_DESIGN.md` | How planning, economy and the strategy bandit fit together | yes — its `hdv.buy` example predates the 28-tool vocabulary |
| `PIPELINE.md` | The data path: corpus → training → serving → logging | yes — model widths/counts superseded by `CURRENT.md` |
| `TRAINING_PLAN.md` | One shared learned brain (≤30M total / ≤10M active); the harness as its gym | user direction, 2026-10-27 — design, not shipped |
| `docs/archive/dofus-network/PLAN.md` | The older living plan and its status tables — **moved out 2026-11-07** | **historical — status now lives in `CURRENT.md` and `resume.md`** |
| `PROJECT_LOG.md` | Why each decision was made, and what would change our mind (D1–D24) | yes — reasoning, not status |
| `PROTOCOL_NOTES.md` | Exact wire format of every action, captured from real sessions | yes — grows with each capture |
| `EXPERIENCE_LEARNING.md` | How outcomes become posteriors without a reward model | yes |
| `FAILURE_TO_SUPERVISION.md` | How kicks, refusals and regressions become gate labels, data and tests; GPU-only rule for evolution | yes — active 2026-10-24 |
| `LEARNED_FACTS.md` | Which facts belong in a constant and which must be learned; the rulebook behind the menu-window and surface-label conversion | yes — written 2026-11-07 |
| `OBJECTIVE_LEDGER.md` | Confirmation at the level of the objective: hypotheses, sweeps, stale premises | yes — ledger design |
| `LATENT_AND_ONLINE_LEARNING.md` | Judgement on latent reasoning, agent-to-agent channels, online weight updates | yes — decisions, not status |
| `CAPABILITY_REGISTRY.md` | The tools as data: names, params, descriptions | prose count predates the live 28; `data/capabilities.json` is authoritative |
| `WIRING_AUDIT.md` | What the running loop actually uses, checked at construction sites | yes — audited 2026-11-06 |
| `docs/archive/dofus-network/AGENT2_DESIGN.md` | An earlier second-agent design — **moved out 2026-11-07** | **superseded by `AGENT3_PLAN.md`** |
| `AGENT3_PLAN.md` | The current second-agent plan | design, not shipped — read `CURRENT.md` for what actually runs |
| `SECOND_AGENT.md` | Another second-agent write-up | design/history; `CURRENT.md` §2 is the live loop |

## Reading order by question

- *"How do I actually run this, and on what port?"* → `CURRENT.md` §1–4
- *"What is the AI, and which parts ship?"* → `AI_ARCHITECTURE.md`, then `CURRENT.md` §5
- *"What is broken / why did a run die?"* → `KNOWN_GAPS.md`
- *"What do I build next?"* → `CURRENT.md` §6, then `WIRING_AUDIT.md`
- *"How does this message look on the wire?"* → `PROTOCOL_NOTES.md`
- *"Why is it designed this way?"* → `ARCH_REASSESSMENT.md`, then `PROJECT_LOG.md`
- *"How does the model train and serve?"* → `PIPELINE.md` → `CURRENT.md` §5
- *"How do failures become data?"* → `FAILURE_TO_SUPERVISION.md`
- *"Should this be a constant or a learned fact?"* → `LEARNED_FACTS.md`
- *"Why did we reject X?"* → `PROJECT_LOG.md` (decisions) and
  `LATENT_AND_ONLINE_LEARNING.md` (judged proposals)

## Rules for these files

1. **One source of truth per question.** Live status lives in `CURRENT.md` and
   `resume.md`; the running topology lives only in `CURRENT.md`; open defects live in
   `KNOWN_GAPS.md`; what the AI is lives in `AI_ARCHITECTURE.md`. Other files may be
   stale on any of these and must not be trusted for them.
2. **Numbers are measured or labelled.** Anything unmeasured says so.
3. **A decision records its falsifier** — the observation that would overturn it.
4. **Capture before coding.** A protocol note is written from traffic, never from
   reading the minified bundle.

## Removed

`AI_ANALYSIS.md`, `BENCH_30M.md`, `PRETRAIN_GAPS.md` and `KAGGLE_RUNS.md` were
deleted 2026-10-26 as obsolete (the THX-01 path was killed and its benchmarks are
superseded). The one live item from `PRETRAIN_GAPS.md` — the Sentinel still
importing torch, W4 — is now `KNOWN_GAPS.md` G5. Recover any deleted file with
`git show <sha>:<path>`.

The THX-01 code itself followed on 2026-11-07: `dofus-network/pipeline/`
(fine-tune, serve, benchmark, eval, filter) was deleted, `ai/thx_policy.py`
became the model-free `ai/guard_policy.py`, and the Sentinel's THX/ModernBERT
classifier was removed. `--policy thx`, the `thx01` distribution and the
`models/dofus-thx01` path are gone. Recover with `git show <sha>:<path>`.

The two Colab notebooks for the dead training lanes — `Fine_Tune_Dofus_THX01.ipynb`
and `train_dofus_brain_t4.ipynb` — were removed on 2026-11-07 as well; the latter
imported `dofus_native_model.py`, `train_from_scratch.py` and `pipeline.py`, none of
which exist. `docs/archive/CLEANUP.md` records the quarantine path and the `git show` recovery.

## Moved to `docs/archive/`, 2026-11-07

`PLAN.md` and `AGENT2_DESIGN.md` left this directory. Both had already declared themselves
superseded, and keeping a dead plan and a dead design next to the live ones made the directory
look like four competing plans. Content is unchanged; `docs/archive/README.md` is the index.

**One trap, worth stating once.** Three documents use the word "agent" for different things, and
they are not duplicates:

| document | "agent" means | status |
|---|---|---|
| `SECOND_AGENT.md` | the **verifier** — it judges what the first agent did | live |
| `AGENT3_PLAN.md` | the **strategist** — a second brain for planning and fight decisions | design, not shipped |
| `docs/archive/dofus-network/AGENT2_DESIGN.md` | an earlier **actor** design, carried into `AGENT3_PLAN.md` | superseded |

The numbering (2, 3, second) is historical and does not describe an order. `CURRENT.md` §2 is the
only description of the loop that actually runs.
