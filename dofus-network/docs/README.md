# Docs map

Fifteen documents in `dofus-network/docs/`. This is what each one is for, so
nobody has to read them all to find the answer to one question.

**"How do I run it / what's actually wired?" → `CURRENT.md`.**
**"What is broken or unproven?" → `KNOWN_GAPS.md`.**
**"What is the plan and what's current?" → `PLAN.md`** (the single status source).

| document | what it answers | still current? |
|---|---|---|
| **`PLAN.md`** | What is true now, what happens next, what is blocked, the invariants | **yes — single source of truth** |
| **`CURRENT.md`** | How to run it: services/ports, the 7-stage loop, think-token pacing, policy modes, model inventory, combat pipeline, the 21 tools | **yes — the running-system map** |
| **`KNOWN_GAPS.md`** | What is *not* true yet (no keep-alive → INACTIVITY, no equip, dialog text unread, no vision, forecaster dark, live fight unverified, tests gone), each with evidence | **yes — honest limits** |
| `PROJECT_LOG.md` | Why each decision was made, and what would change our mind (D1–D15) | yes — reasoning, not status |
| `PROTOCOL_NOTES.md` | Exact wire format of every action, captured from real sessions | yes — grows with each capture |
| `ARCH_REASSESSMENT.md` | The architectural argument: evidence, nine sections, ordered work list | yes — reasoning; status column superseded by `PLAN.md` |
| `PLANNER_DESIGN.md` | How planning, economy and the strategy bandit fit together | yes — but its `hdv.buy` example predates the ban |
| `PIPELINE.md` | The data path: corpus → training → serving → logging | yes — model widths/counts superseded by `CURRENT.md` |
| `EXPERIENCE_LEARNING.md` | How outcomes become posteriors without a reward model | yes |
| `FAILURE_TO_SUPERVISION.md` | How kicks, refusals and regressions become gate labels, data and tests; GPU-only rule for evolution | yes — active 2026-10-24 |
| `CAPABILITY_REGISTRY.md` | The tools as data: names, params, descriptions | **stale — says 16 tools, the schema is 21** |
| `LATENT_AND_ONLINE_LEARNING.md` | Judgement on latent reasoning, agent-to-agent channels, online weight updates | yes — decisions, not status |
| `AGENT2_DESIGN.md` | An earlier second-agent design | **superseded by `AGENT3_PLAN.md`** |
| `AGENT3_PLAN.md` | The current second-agent plan | design, not shipped — read `CURRENT.md` for what actually runs |
| `SECOND_AGENT.md` | Another second-agent write-up | design/history; `CURRENT.md` §2 is the live loop |

## Reading order by question

- *"How do I actually run this, and on what port?"* → `CURRENT.md` §1–4
- *"What is broken / why did a run die?"* → `KNOWN_GAPS.md` (G1 keep-alive, G7 fight)
- *"What do I build next?"* → `PLAN.md` §4
- *"How does this message look on the wire?"* → `PROTOCOL_NOTES.md`
- *"Why is it designed this way?"* → `ARCH_REASSESSMENT.md`, then `PROJECT_LOG.md`
- *"How does the model train and serve?"* → `PIPELINE.md` → `CURRENT.md` §5
- *"How do failures become data?"* → `FAILURE_TO_SUPERVISION.md`
- *"Why did we reject X?"* → `PROJECT_LOG.md` (decisions) and
  `LATENT_AND_ONLINE_LEARNING.md` (judged proposals)

## Rules for these files

1. **One source of truth per question.** Status lives only in `PLAN.md`; the
   running topology lives only in `CURRENT.md`; open defects live in
   `KNOWN_GAPS.md`. Other files may be stale on any of these and must not be
   trusted for them.
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
