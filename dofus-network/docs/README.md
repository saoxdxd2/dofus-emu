# Docs map

Eleven documents accumulated over the project. This is what each one is for, so
nobody has to read them all to find the answer to one question.

**Start here → `PLAN.md`.** It is the only place that claims to be current.

| document | what it answers | still current? |
|---|---|---|
| **`PLAN.md`** | What is true now, what happens next, what is blocked, and the invariants | **yes — single source of truth** |
| `PROJECT_LOG.md` | Why each decision was made, and what would change our mind (D1–D15) | yes — reasoning, not status |
| `PROTOCOL_NOTES.md` | Exact wire format of every action, captured from real sessions | yes — grows with each capture |
| `ARCH_REASSESSMENT.md` | The architectural argument: what the evidence says, nine sections, the ordered work list | yes — reasoning; its status column is superseded by `PLAN.md` |
| `PLANNER_DESIGN.md` | How planning, economy and the strategy bandit fit together | yes |
| `PIPELINE.md` | The data path: corpus → training → serving → logging | yes |
| `EXPERIENCE_LEARNING.md` | How outcomes become posteriors without a reward model | yes |
| `CAPABILITY_REGISTRY.md` | The 16 tools as data: names, params, descriptions | yes |
| `AI_ANALYSIS.md` | Why the encoder beat the decoder, with the measurements | yes — historical reasoning |
| `BENCH_30M.md` | The 30M encoder benchmark that killed the 322M THX path | yes — historical |
| `PRETRAIN_GAPS.md` | Known gaps in the pretraining/serving path (W4: sentinel on ONNX) | yes — partially closed |
| `LATENT_AND_ONLINE_LEARNING.md` | Judgement on latent reasoning, agent-to-agent channels, online weight updates | yes — decisions, not status |

## Reading order by question

- *"What do I build next?"* → `PLAN.md` §4
- *"How does this message look on the wire?"* → `PROTOCOL_NOTES.md`
- *"Why is it designed this way?"* → `ARCH_REASSESSMENT.md`, then `PROJECT_LOG.md`
- *"How does the model train and serve?"* → `PIPELINE.md` → `AI_ANALYSIS.md`
- *"Why did we reject X?"* → `PROJECT_LOG.md` (decisions) and
  `LATENT_AND_ONLINE_LEARNING.md` (judged proposals)

## Rules for these files

1. **One source of truth per question.** Status lives only in `PLAN.md`; other
   files may be stale on status and must not be trusted for it.
2. **Numbers are measured or labelled.** Anything unmeasured says so.
3. **A decision records its falsifier** — the observation that would overturn it.
4. **Capture before coding.** A protocol note is written from traffic, never from
   reading the minified bundle.
